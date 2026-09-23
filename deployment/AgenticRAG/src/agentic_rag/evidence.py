"""Trusted final-payload provenance gate; it does not perform network IO.

Only an adapter holding DeliveryGateway can settle an in-memory permit. Model
JSON, UUIDs, rendering, UI history and stored request hashes cannot self-confirm.
R12 must call settle only after the real protocol terminal has been observed.
"""

from dataclasses import dataclass
import hashlib
import json
from threading import RLock
from uuid import UUID, uuid4

from .domain import Evidence, SourceRef, Span
from .source_archive import contains, digest, encode, invalid, read_ref, union
from .retrieval.context import window_removed


@dataclass(frozen=True)
class MappedSpan:
    candidate_id: UUID
    source_span: Span
    json_path: tuple[str | int, ...]
    body_span: Span
    tool_call_id_path: tuple[str | int, ...]


@dataclass(frozen=True, eq=False)
class _Prepared:
    request_id: UUID
    body_sha256: str


def _node(body, path):
    if not isinstance(path, tuple) or not path:
        raise invalid('explicit JSON node path required', 'delivery')
    current = body
    for part in path:
        if type(part) is int and isinstance(current,list) and 0 <= part < len(current):
            current=current[part]
        elif isinstance(part,str) and isinstance(current,dict) and part in current:
            current=current[part]
        else:
            raise invalid('mapped JSON path does not exist', 'delivery')
    return current


def _unique_object(pairs):
    output = {}
    for key,value in pairs:
        if key in output:
            raise invalid('ambiguous duplicate JSON key', 'delivery')
        output[key]=value
    return output


def _tool_nodes(body, protocol):
    """Independently bind text and ID to the same actual protocol tool result."""
    found={}
    if not isinstance(body,dict):
        raise invalid('request body must be a JSON object','delivery')
    if protocol=='compat':
        for i,item in enumerate(body.get('messages',[])):
            if isinstance(item,dict) and item.get('role')=='tool' and isinstance(item.get('content'),str):
                found[('messages',i,'content')]=(('messages',i,'tool_call_id'),item.get('tool_call_id'))
    elif protocol=='responses':
        for i,item in enumerate(body.get('input',[])):
            if isinstance(item,dict) and item.get('type')=='function_call_output' and isinstance(item.get('output'),str):
                found[('input',i,'output')]=(('input',i,'call_id'),item.get('call_id'))
    else:
        for i,message in enumerate(body.get('messages',[])):
            if not isinstance(message,dict) or message.get('role')!='user' or not isinstance(message.get('content'),list):
                continue
            for j,block in enumerate(message['content']):
                if not isinstance(block,dict) or block.get('type')!='tool_result' or block.get('is_error'):
                    continue
                prefix=('messages',i,'content',j)
                identity=(prefix+('tool_use_id',),block.get('tool_use_id'))
                if isinstance(block.get('content'),str):
                    found[prefix+('content',)]=identity
                elif isinstance(block.get('content'),list):
                    for k,text in enumerate(block['content']):
                        if isinstance(text,dict) and text.get('type')=='text' and isinstance(text.get('text'),str):
                            found[prefix+('content',k,'text')]=identity
    return found


def candidate(catalog, run_id, candidate_id):
    with catalog._db.transaction() as connection:
        row=connection.execute("SELECT c.payload FROM source_candidates c JOIN source_calls s ON s.call_id=c.call_id WHERE c.candidate_id=? AND c.run_id=? AND s.status='ok'",
                               (str(candidate_id),str(run_id))).fetchone()
    if row is None:
        raise invalid('unknown or unsuccessful source candidate', 'delivery')
    return json.loads(row[0])


def read_evidence(catalog, run_id, evidence_id):
    with catalog._db.transaction() as connection:
        row=connection.execute('SELECT e.payload,r.kb_id,r.revision_id FROM delivered_evidence e JOIN runs r ON r.run_id=e.run_id WHERE e.evidence_id=? AND e.run_id=?',
                               (str(evidence_id),str(run_id))).fetchone()
        receipts=list(connection.execute("SELECT request_id,status,purpose,payload FROM delivery_receipts WHERE run_id=? AND status='confirmed' AND purpose<>'compact'",
                                         (str(run_id),)))
    if row is None:
        raise invalid('evidence was not confirmed for this run', 'citation')
    data=json.loads(row[0])
    record=Evidence.model_validate_json(encode(data['evidence']))
    if (record.evidence_id!=evidence_id or record.run_id!=run_id or
            (str(record.source_ref.kb_id),str(record.source_ref.revision_id))!=(row[1],row[2]) or
            not data['deliveries'] or str(record.delivery_id)!=data['deliveries'][0]):
        raise invalid('persisted evidence identity differs from fixed run binding','citation')
    source=read_ref(catalog,record.source_ref)
    section=source.section(record.source_ref.section_id)
    if any(not contains((section.span,),span) for span in record.spans):
        raise invalid('persisted evidence exceeds its exact source section','citation')
    text=''.join(source.text[s.start:s.end] for s in record.spans)
    if record.text_hash != digest(text):
        raise invalid('confirmed evidence archive hash differs', 'citation')
    # Evidence is derived from sealed receipts, not an independently authoritative
    # JSON cache. A damaged single evidence row cannot enlarge delivered ranges.
    actual_deliveries=set();delivered=[]
    for request,status,purpose,payload in receipts:
        receipt=json.loads(payload)
        relevant=[m for m in receipt['mappings'] if m['evidence_id']==str(evidence_id)]
        if not relevant:
            continue
        if (receipt['request_id']!=request or receipt['run_id']!=str(run_id) or
                receipt['kb_id']!=row[1] or receipt['revision_id']!=row[2] or receipt['purpose']!=purpose):
            raise invalid('sealed receipt binding differs from evidence','citation')
        actual_deliveries.add(request)
        for mapped in relevant:
            span=Span.model_validate(mapped['source_span'])
            if (mapped['source_ref']!=record.source_ref.model_dump(mode='json') or
                    not contains((section.span,),span) or
                    digest(source.text[span.start:span.end])!=mapped['text_sha256']):
                raise invalid('sealed receipt source differs from evidence','citation')
            delivered.append(span)
    if (len(data['deliveries'])!=len(set(data['deliveries'])) or
            set(data['deliveries'])!=actual_deliveries or union(delivered)!=record.spans):
        raise invalid('derived evidence differs from confirmed non-compact receipt ranges','citation')
    return record, source, data


class DeliveryGateway:
    """Host-only capability. Never expose this object as a model-facing tool."""
    def __init__(self, session):
        self.session=session
        self._calls={}
        self._permits={}
        self._lock=RLock()

    def bind_tool_result(self, result, tool_call_id: str):
        """Bind host-observed successful result to its actual protocol call ID."""
        if not isinstance(tool_call_id,str) or not tool_call_id.strip():
            raise invalid('actual tool call ID required', 'delivery')
        for identity in result.candidate_ids:
            item=candidate(self.session.catalog,self.session.run.run_id,identity)
            if item['call_id'] != result.payload['call_id']:
                raise invalid('result/candidate call identity differs', 'delivery')
            previous=self._calls.get(str(identity))
            if previous is not None and previous != tool_call_id:
                raise invalid('candidate cannot be rebound to a different tool call', 'delivery')
            self._calls[str(identity)]=tool_call_id

    def prepare(self, raw_body: bytes, mappings: tuple[MappedSpan,...], *, purpose: str, protocol: str, request_id=None):
        if purpose not in {'explore','finalize','citation_repair','compact'} or protocol not in {'anthropic','responses','compat'}:
            raise invalid('unknown delivery purpose/protocol', 'delivery')
        if not isinstance(raw_body,bytes):
            raise TypeError('raw HTTP body bytes required')
        try:
            body=json.loads(raw_body.decode('utf-8'),object_pairs_hook=_unique_object)
        except (ValueError,UnicodeError) as exc:
            raise invalid('invalid final JSON request body', 'delivery') from exc
        request_id=request_id or uuid4()
        if not isinstance(request_id,UUID):
            raise TypeError('request_id must be UUID')
        checked=[]
        seen=[]
        eligible=_tool_nodes(body,protocol)
        for mapping in mappings:
            if not isinstance(mapping,MappedSpan):
                raise invalid('trusted mapped span required, not model JSON', 'delivery')
            item=candidate(self.session.catalog,self.session.run.run_id,mapping.candidate_id)
            ref=SourceRef.model_validate_json(encode(item['source_ref']))
            if (ref.kb_id,ref.revision_id)!=(self.session.run.kb_id,self.session.run.revision_id):
                raise invalid('candidate binding differs', 'delivery')
            source=read_ref(self.session.catalog,ref)
            spans=tuple(Span.model_validate(s) for s in item['spans'])
            archived_candidate=''.join(source.text[s.start:s.end] for s in spans)
            if (not item['succeeded'] or item['text']!=archived_candidate or item['text_hash']!=digest(archived_candidate) or
                    not contains(spans,mapping.source_span)):
                raise invalid('delivered span exceeds successful candidate', 'delivery')
            source_text=source.text[mapping.source_span.start:mapping.source_span.end]
            node=_node(body,mapping.json_path)
            actual_call=_node(body,mapping.tool_call_id_path)
            if eligible.get(mapping.json_path)!=(mapping.tool_call_id_path,actual_call):
                raise invalid('mapping is not text and ID from the same legal tool result','delivery')
            if actual_call != self._calls.get(str(mapping.candidate_id)) or not isinstance(actual_call,str):
                raise invalid('actual tool call ID not bound to successful result', 'delivery')
            if (not isinstance(node,str) or mapping.body_span.end>len(node) or
                    node[mapping.body_span.start:mapping.body_span.end]!=source_text or
                    len(source_text)!=mapping.body_span.end-mapping.body_span.start):
                raise invalid('mapped final JSON text differs from canonical source', 'delivery')
            # Overlapping mappings in one final text node are ambiguous, even if
            # their repeated text happens to be identical.
            if any(path==mapping.json_path and a.start<mapping.body_span.end and mapping.body_span.start<a.end for path,a in seen):
                raise invalid('overlapping final-payload provenance mappings', 'delivery')
            seen.append((mapping.json_path,mapping.body_span))
            checked.append({'candidate_id':str(mapping.candidate_id),'evidence_id':item['evidence_id'],
                'source_ref':ref.model_dump(mode='json'),'source_span':mapping.source_span.model_dump(),
                'text_sha256':digest(source_text),'json_path':list(mapping.json_path),
                'body_span':mapping.body_span.model_dump(),'tool_call_id_path':list(mapping.tool_call_id_path),
                'tool_call_id':actual_call})
        body_hash=hashlib.sha256(raw_body).hexdigest()
        previous = self.window()
        payload={'request_id':str(request_id),'run_id':str(self.session.run.run_id),
                 'kb_id':str(self.session.run.kb_id),'revision_id':str(self.session.run.revision_id),
                 'body_sha256':body_hash,'purpose':purpose,'protocol':protocol,'mappings':checked,
                 'window_transition':{'previous_request_id':previous['request_id'],
                    'removed_from_request':window_removed(previous['mappings'],checked),
                    'on_confirmed_compact_removed':checked if purpose=='compact' else []}}
        with self.session.catalog._db.transaction(write=True) as connection:
            self.session._active(connection)
            generation,=connection.execute('SELECT window_generation FROM source_usage WHERE run_id=?',(str(self.session.run.run_id),)).fetchone()
            if connection.execute('SELECT 1 FROM delivery_receipts WHERE request_id=?',(str(request_id),)).fetchone():
                raise invalid('request ID cannot be reused', 'delivery')
            connection.execute('INSERT INTO delivery_receipts VALUES(?,?,?,?,?,?,?)',
                (str(request_id),str(self.session.run.run_id),'prepared',purpose,protocol,body_hash,encode(payload)))
        permit=_Prepared(request_id,body_hash)
        self._permits[permit]={'payload':payload,'status':'prepared','evidence_ids':(),
                               'window_tokens':self.session._count(raw_body.decode('utf-8')),
                               'window_generation':generation,'retained_generation':None}
        return permit

    def retain_prepared_window(self, permit):
        """Trusted adapter synchronizes an explicitly transformed current window.

        The exact prepared JSON was checked with propagated sidecars. This only
        releases/replaces source window reservations: it never grants evidence,
        resets cumulative source-return cost or asserts HTTP delivery. Whole JSON
        (including wrappers/navigation) is metered conservatively by the host's
        answer meter. R12 owns when current messages actually change.
        """
        with self._lock:
            return self._retain_prepared_window(permit)

    def _retain_prepared_window(self, permit):
        if not isinstance(permit,_Prepared) or permit not in self._permits:
            raise invalid('unknown trusted window permit','delivery')
        entry=self._permits[permit]
        tokens=entry['window_tokens'];mappings=entry['payload']['mappings']
        if tokens>self.session.retrieval.context_tokens or len(mappings)>self.session.retrieval.context_chunks:
            raise invalid('retained source window exceeds configured limits','source_budget')
        with self.session.catalog._db.transaction(write=True) as connection:
            self.session._active(connection)
            generation,=connection.execute('SELECT window_generation FROM source_usage WHERE run_id=?',(str(self.session.run.run_id),)).fetchone()
            if entry['retained_generation'] is not None:
                if generation==entry['retained_generation']:
                    return
                raise invalid('old retained window cannot reset later source reservations','delivery')
            if generation!=entry['window_generation']:
                raise invalid('stale prepared window cannot discard newer source reservations','delivery')
            connection.execute('UPDATE source_usage SET window_tokens=?,window_fragments=?,window_generation=window_generation+1 WHERE run_id=?',
                (tokens,len(mappings),str(self.session.run.run_id)))
            connection.execute('INSERT INTO evidence_windows VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload',
                (str(self.session.run.run_id),encode({'request_id':str(permit.request_id),'mappings':mappings})))
        entry['retained_generation']=generation+1

    def settle(self, permit, status: str):
        with self._lock:
            return self._settle(permit,status)

    def _settle(self, permit, status: str):
        if not isinstance(permit,_Prepared) or permit not in self._permits:
            raise invalid('unknown trusted delivery permit', 'delivery')
        if status not in {'not_sent','rejected','unknown','confirmed'}:
            raise invalid('terminal delivery status required', 'delivery')
        entry=self._permits[permit]
        if entry['status']!='prepared':
            if entry['status']!=status:
                raise invalid('terminal delivery state cannot change', 'delivery')
            return entry['evidence_ids']
        payload=entry['payload']
        # All archive IO and hash checks precede the short atomic receipt/evidence
        # transaction. No model/network call executes inside a write transaction.
        additions={}
        if status=='confirmed' and payload['purpose']!='compact':
            for item in payload['mappings']:
                ref=SourceRef.model_validate_json(encode(item['source_ref']))
                source=read_ref(self.session.catalog,ref)
                span=Span.model_validate(item['source_span'])
                if digest(source.text[span.start:span.end])!=item['text_sha256']:
                    raise invalid('source changed between prepare and confirmation', 'delivery')
                group=additions.setdefault(item['evidence_id'],{'ref':ref,'source':source,'spans':[]})
                group['spans'].append(span)
        ids=tuple(UUID(i) for i in additions)
        with self.session.catalog._db.transaction(write=True) as connection:
            self.session._active(connection)
            row=connection.execute('SELECT status,body_sha256,payload FROM delivery_receipts WHERE request_id=? AND run_id=?',
                (str(permit.request_id),str(self.session.run.run_id))).fetchone()
            if row!=('prepared',permit.body_sha256,encode(payload)):
                raise invalid('prepared receipt binding differs', 'delivery')
            for identity,group in additions.items():
                previous=connection.execute('SELECT payload FROM delivered_evidence WHERE evidence_id=? AND run_id=?',
                    (identity,str(self.session.run.run_id))).fetchone()
                old=json.loads(previous[0]) if previous else None
                spans=list(group['spans'])
                deliveries=[]
                if old:
                    record=Evidence.model_validate_json(encode(old['evidence']))
                    if record.source_ref!=group['ref']:
                        raise invalid('evidence identity rebound to a different source', 'delivery')
                    spans.extend(record.spans)
                    deliveries=old['deliveries']
                spans=union(spans)
                record=Evidence(evidence_id=UUID(identity),run_id=self.session.run.run_id,
                    delivery_id=UUID(deliveries[0]) if deliveries else permit.request_id,source_ref=group['ref'],
                    spans=spans,text_hash=digest(''.join(group['source'].text[s.start:s.end] for s in spans)))
                value={'evidence':record.model_dump(mode='json'),'deliveries':deliveries+[str(permit.request_id)]}
                connection.execute('INSERT INTO delivered_evidence VALUES(?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET payload=excluded.payload',
                    (identity,str(self.session.run.run_id),encode(value)))
            connection.execute('UPDATE delivery_receipts SET status=? WHERE request_id=?',(status,str(permit.request_id)))
            generation,=connection.execute('SELECT window_generation FROM source_usage WHERE run_id=?',(str(self.session.run.run_id),)).fetchone()
            if status=='confirmed' and generation in (entry['window_generation'],entry['retained_generation']):
                # Window state is only what this actual request retained; historic
                # evidence remains independently available after compact/cropping.
                retained=[] if payload['purpose']=='compact' else payload['mappings']
                connection.execute('INSERT INTO evidence_windows VALUES(?,?) ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload',
                    (str(self.session.run.run_id),encode({'request_id':str(permit.request_id),'mappings':retained})))
        entry.update(status=status,evidence_ids=ids)
        return ids

    def window(self):
        with self.session.catalog._db.transaction() as connection:
            row=connection.execute('SELECT payload FROM evidence_windows WHERE run_id=?',(str(self.session.run.run_id),)).fetchone()
        return json.loads(row[0]) if row else {'request_id':None,'mappings':[]}
