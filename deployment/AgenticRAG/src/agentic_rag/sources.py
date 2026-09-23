"""Run-bound search/open surfaces and durable pending source provenance.

The host supplies a verified answer-model token meter. This is a source-return
budget, not the complete HTTP/LLM budget gate implemented by the host adapter.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import secrets
import time
from typing import Protocol, get_args
from uuid import UUID, uuid4

from .domain import BudgetStopReason, ErrorCode, RagError, RunStatus, SourceRef, Span
from .source_archive import contains, digest, encode, invalid, read_ref, read_version, union
from .storage.runs import RunLease, read_pin, read_run


class SourceBudgetExceeded(RagError):
    """A known source allowance ended; diagnostics are not a reason protocol."""

    def __init__(self, reason: BudgetStopReason, message: str):
        if reason not in get_args(BudgetStopReason):
            raise ValueError('unknown source budget reason')
        self.reason = reason
        super().__init__(ErrorCode.BUDGET_EXHAUSTED, message, stage='source_budget')


class AnswerTokenMeter(Protocol):
    """Trusted host capability: exact tokenizer or documented safe upper bound."""
    identity: str

    def count(self, text: str) -> int: ...


@dataclass(frozen=True)
class RenderedBody:
    candidate_id: UUID
    source_span: Span
    body_span: Span


def render_payload(payload):
    """Construct CP-preserving tool text and offsets, never search it afterwards."""
    metadata={key:value for key,value in payload.items() if key!='items'}
    metadata['items']=[{key:value for key,value in item.items() if key!='text'} for item in payload.get('items',())]
    text=encode(metadata)
    mappings=[]
    for item in payload.get('items',()):
        text+='\n\n<source candidate="'+item['candidate_id']+'">\n'
        start=len(text)
        text+=item['text']
        span=Span.model_validate(item['returned_spans'][0])
        mappings.append(RenderedBody(UUID(item['candidate_id']),span,Span(start=start,end=len(text))))
        text+='\n</source>'
    return text,tuple(mappings)


@dataclass(frozen=True)
class ToolSourceResult:
    """Public payload plus trusted candidate IDs, never activated by rendering."""
    payload: dict
    candidate_ids: tuple[UUID, ...]

    @property
    def text(self):
        return render_payload(self.payload)[0]

    @property
    def body_mappings(self):
        return render_payload(self.payload)[1]


class SourceSession:
    def __init__(self, catalog, lease: RunLease, meter: AnswerTokenMeter, *, dense=None):
        if not isinstance(lease, RunLease) or lease._database is not catalog._db:
            raise invalid('source session requires the actual owning RunLease')
        if not isinstance(meter.identity, str) or not meter.identity.strip():
            raise invalid('an explicit answer token meter identity is required', 'source_budget')
        self.catalog, self.lease, self.meter, self.dense = catalog, lease, meter, dense
        self.run = lease.run
        self.budget = self.run.resolved_config.budget
        self.retrieval = self.run.resolved_config.retrieval
        if dense is not None and (dense.catalog is not catalog or dense.run_id != self.run.run_id):
            raise invalid('search service belongs to another run')
        with catalog._db.transaction(write=True) as connection:
            self._active(connection)
            connection.execute('INSERT INTO source_usage(run_id,started_ns,meter_identity) VALUES(?,?,?) ON CONFLICT DO NOTHING',
                               (str(self.run.run_id), time.monotonic_ns(), meter.identity))
            if connection.execute('SELECT meter_identity FROM source_usage WHERE run_id=?', (str(self.run.run_id),)).fetchone() != (meter.identity,):
                raise invalid('answer token meter cannot change during a run', 'source_budget')

    def _active(self, connection):
        current, pin = read_run(connection, self.run.run_id), read_pin(connection, self.run.run_id)
        if ((current.kb_id, current.revision_id, current.resolved_config_hash) !=
                (self.run.kb_id, self.run.revision_id, self.run.resolved_config_hash) or
                current.status != RunStatus.RUNNING or pin != self.lease.pin or pin.state != 'active'):
            raise invalid('run binding is terminal, expired or different','source_scope')
        self.lease._lock.check()
        return current

    def _count(self, text):
        value = self.meter.count(text)
        if type(value) is not int or value < 0 or (text and value == 0):
            raise invalid('answer token meter returned an invalid count', 'source_budget')
        return value

    def usage(self):
        with self.catalog._db.transaction() as connection:
            row = connection.execute('SELECT searches,opens,rejected,returned_tokens,returned_fragments,window_tokens,window_fragments FROM source_usage WHERE run_id=?',
                                     (str(self.run.run_id),)).fetchone()
        return dict(zip(('searches','opens','rejected','returned_tokens','returned_fragments','window_tokens','window_fragments'), row))

    @contextmanager
    def _call(self, kind):
        call_id = uuid4()
        denied = None
        with self.catalog._db.transaction(write=True) as connection:
            current = self._active(connection)
            searches, opens, tokens, started = connection.execute('SELECT searches,opens,returned_tokens,started_ns FROM source_usage WHERE run_id=?',
                (str(self.run.run_id),)).fetchone()
            count = searches if kind == 'search' else opens
            maximum = self.budget.searches if kind == 'search' else self.budget.opens
            if count >= maximum:
                denied = SourceBudgetExceeded(kind + '_limit', kind + ' call limit exhausted')
            elif (time.monotonic_ns() - started) // 1_000_000 >= self.budget.duration_ms - self.budget.finish_reserve_ms:
                denied = SourceBudgetExceeded('time_budget', 'source exploration deadline exhausted')
            elif tokens >= self.budget.total_tokens - self.budget.finish_reserve_tokens:
                denied = SourceBudgetExceeded('token_budget', 'source exploration token budget exhausted')
            if denied:
                connection.execute('UPDATE source_usage SET rejected=rejected+1 WHERE run_id=?', (str(self.run.run_id),))
            else:
                column = 'searches' if kind == 'search' else 'opens'
                connection.execute(f'UPDATE source_usage SET {column}={column}+1 WHERE run_id=?', (str(self.run.run_id),))
                usage = current.usage.model_copy(update={column:getattr(current.usage, column)+1})
                connection.execute('UPDATE runs SET usage=? WHERE run_id=?', (usage.model_dump_json(), str(self.run.run_id)))
            connection.execute('INSERT INTO source_calls(call_id,run_id,kind,status,error) VALUES(?,?,?,?,?)',
                (str(call_id), str(self.run.run_id), kind, 'rejected' if denied else 'admitted',
                 denied.error.message if denied else None))
        if denied:
            denied.error=denied.error.model_copy(update={'call_id':str(call_id)})
            raise denied
        try:
            yield call_id
        except Exception as exc:
            error=exc
            if isinstance(error,RagError):
                error.error=error.error.model_copy(update={'call_id':str(call_id)})
            elif isinstance(error,(ValueError,TypeError)):
                error=RagError(ErrorCode.INVALID_INPUT,str(error),stage='source_input',call_id=str(call_id))
            elif isinstance(error,(ConnectionError,TimeoutError)):
                error=RagError(ErrorCode.DEPENDENCY_UNAVAILABLE,str(error),stage='source_search',call_id=str(call_id))
            with self.catalog._db.transaction(write=True) as connection:
                connection.execute("UPDATE source_calls SET status='error',error=? WHERE call_id=? AND status='admitted'", (str(error),str(call_id)))
            if error is exc:
                raise
            raise error from exc

    def _token_allowance(self):
        usage=self.usage()
        return min((self.retrieval.context_tokens-usage['window_tokens'], 'context_limit'),
                   (self.budget.total_tokens - self.budget.finish_reserve_tokens - usage['returned_tokens'], 'token_budget'),
                   key=lambda allowance: allowance[0])

    def _handle(self, connection, kind, payload):
        token = ('src_' if kind == 'source' else 'cur_') + secrets.token_urlsafe(32)
        connection.execute('INSERT INTO source_handles VALUES(?,?,?,?)',
                           (token, str(self.run.run_id), kind, encode(payload)))
        return token

    def _resolve(self, token, kind):
        if not isinstance(token, str) or len(token) > 100:
            raise invalid('invalid opaque source capability', 'source_cursor' if kind=='cursor' else 'source_scope')
        with self.catalog._db.transaction() as connection:
            self._active(connection)
            row = connection.execute('SELECT payload FROM source_handles WHERE token=? AND kind=? AND run_id=?',
                (token,kind,str(self.run.run_id))).fetchone()
        if row is None:
                raise invalid('unknown, forged or cross-run source capability', 'source_cursor' if kind=='cursor' else 'source_scope')
        return json.loads(row[0])

    def issue_source(self, ref: SourceRef, *, anchor_span: Span | None = None):
        """Host-only issuance from an authenticated search/navigation result."""
        return self._issue_verified_source(ref, self._read_source(ref), anchor_span=anchor_span)

    def _read_source(self, ref):
        if (ref.kb_id, ref.revision_id) != (self.run.kb_id, self.run.revision_id):
            raise invalid('source scope differs from this run','source_scope')
        return read_ref(self.catalog, ref)

    def _issue_verified_source(self, ref, source, *, anchor_span=None):
        section = source.section(ref.section_id)
        if anchor_span is not None and not contains((section.span,),anchor_span):
            raise invalid('search anchor is outside the matched section','source_scope')
        with self.catalog._db.transaction(write=True) as connection:
            self._active(connection)
            return self._handle(connection, 'source', {'ref':ref.model_dump(mode='json'),
                                                       'anchor_span':anchor_span.model_dump() if anchor_span else None})

    def directory(self, document_id: UUID, version_id: UUID):
        """Trusted host navigation. Empty documents have no artificial section."""
        with self.catalog._db.transaction() as connection:
            self._active(connection)
        source = read_version(self.catalog, self.run.kb_id, self.run.revision_id, document_id, version_id)
        return self._navigation(source)

    @staticmethod
    def _navigation(source):
        headings = {h.section_id:h for h in source.chunks.parsed.headings}
        eligible = source.chunks.parsed.eligible_spans
        return [{'section_id':str(s.section_id), 'section_path':list(s.heading_path),
                 'span':s.span.model_dump(),
                 'parent_section_id':str(headings[s.section_id].parent_section_id) if s.section_id in headings and headings[s.section_id].parent_section_id else None,
                 'subtree_span':(headings[s.section_id].subtree_span if s.section_id in headings else s.span).model_dump(),
                 'has_body':s.span in eligible} for s in source.chunks.parsed.sections]

    def _candidate(self, call_id, ref, span, source, candidate_id=None, evidence_id=None):
        body = source.text[span.start:span.end]
        return {'candidate_id':str(candidate_id or uuid4()),'evidence_id':str(evidence_id or uuid4()),
                'run_id':str(self.run.run_id),'call_id':str(call_id),
                'source_ref':ref.model_dump(mode='json'),'spans':[span.model_dump()],
                'text':body,'text_hash':digest(body),'succeeded':True}

    def _body_item(self, candidate, token, source, *, chunk_id=None):
        ref = SourceRef.model_validate_json(encode(candidate['source_ref']))
        span = Span.model_validate(candidate['spans'][0])
        section = source.section(ref.section_id)
        return {'candidate_id':candidate['candidate_id'],'evidence_id':candidate['evidence_id'],
                'source_ref':token,'document_id':str(ref.document_id),'document_version_id':str(ref.document_version_id),
                'file_name':source.version.source_metadata.original_name,'section_id':str(ref.section_id),
                'section_path':list(section.heading_path),'chunk_id':chunk_id,
                'returned_spans':[span.model_dump()], 'lines':list(source.chunks.parsed.source_map.lines(span)),
                'text':candidate['text'],'citation_marker':'[^'+candidate['evidence_id']+']',
                'delivery_confirmed':False}

    def _commit_result(self, call_id, payload, candidates):
        tokens = self._count(render_payload(payload)[0])
        with self.catalog._db.transaction(write=True) as connection:
            self._active(connection)
            spent,window,fragments,started = connection.execute('SELECT returned_tokens,window_tokens,window_fragments,started_ns FROM source_usage WHERE run_id=?', (str(self.run.run_id),)).fetchone()
            if (time.monotonic_ns()-started)//1_000_000 >= self.budget.duration_ms-self.budget.finish_reserve_ms:
                raise SourceBudgetExceeded('time_budget', 'source exploration deadline exhausted')
            if tokens+window > self.retrieval.context_tokens:
                raise SourceBudgetExceeded('context_limit', 'shared source token budget exhausted')
            if spent + tokens > self.budget.total_tokens - self.budget.finish_reserve_tokens:
                raise SourceBudgetExceeded('token_budget', 'shared source token budget exhausted')
            if len(candidates)+fragments > self.retrieval.context_chunks:
                raise SourceBudgetExceeded('context_limit', 'source fragment count exhausted')
            for candidate in candidates:
                connection.execute('INSERT INTO source_candidates VALUES(?,?,?,?,?)',
                    (candidate['candidate_id'],str(self.run.run_id),str(call_id),candidate['evidence_id'],encode(candidate)))
            connection.execute("UPDATE source_calls SET status='ok',tokens=?,fragments=? WHERE call_id=? AND status='admitted'",
                               (tokens,len(candidates),str(call_id)))
            connection.execute('UPDATE source_usage SET returned_tokens=returned_tokens+?,returned_fragments=returned_fragments+?,window_tokens=window_tokens+?,window_fragments=window_fragments+?,window_generation=window_generation+1 WHERE run_id=?',
                               (tokens,len(candidates),tokens,len(candidates),str(self.run.run_id)))
        return ToolSourceResult(payload,tuple(UUID(c['candidate_id']) for c in candidates))

    def _unreturned(self, ref, section, additional):
        # This is tool-return coverage, not confirmed delivery or model reading.
        # Already confirmed authority is maintained independently by receipts.
        with self.catalog._db.transaction() as connection:
            rows=connection.execute("SELECT payload FROM source_candidates WHERE run_id=? AND json_extract(payload,'$.source_ref.document_version_id')=? AND json_extract(payload,'$.source_ref.section_id')=?",
                (str(self.run.run_id),str(ref.document_version_id),str(ref.section_id))).fetchall()
        spans=[additional]
        for row in rows:
            item=json.loads(row[0])
            if item['source_ref']==ref.model_dump(mode='json'):
                spans.extend(Span.model_validate(s) for s in item['spans'])
        unseen=[];position=section.span.start
        for span in union(spans):
            if position<span.start:
                unseen.append(Span(start=position,end=span.start))
            position=max(position,span.end)
        if position<section.span.end:
            unseen.append(Span(start=position,end=section.span.end))
        return [{'start':span.start,'end':span.end} for span in unseen]

    def open(self, source_ref, *, section_id=None, cursor=None):
        with self._call('open') as call_id:
            if section_id is not None and cursor is not None:
                raise invalid('section_id and cursor are mutually exclusive', 'source_cursor')
            handle=self._resolve(source_ref,'source')
            ref = SourceRef.model_validate_json(encode(handle['ref']))
            source = read_ref(self.catalog,ref)
            if section_id is not None:
                ref = ref.model_copy(update={'section_id':UUID(str(section_id))})
            section = source.section(ref.section_id)
            anchor=Span.model_validate(handle['anchor_span']) if handle['anchor_span'] else None
            start = anchor.start if anchor and section_id is None and cursor is None else section.span.start
            range_end=section.span.end
            if cursor is not None:
                state = self._resolve(cursor,'cursor')
                if state['source_token'] != source_ref:
                    raise invalid('cursor belongs to another source capability', 'source_cursor')
                target = SourceRef.model_validate_json(encode(state['ref']))
                # A cursor may continue an explicitly selected other section.
                if (target.kb_id,target.revision_id,target.document_id,target.document_version_id) != (ref.kb_id,ref.revision_id,ref.document_id,ref.document_version_id):
                    raise invalid('cursor source binding differs', 'source_cursor')
                ref = target
                section = source.section(ref.section_id)
                start = state['next_cp']
                range_end=state.get('range_end')
                if (type(start) is not int or type(range_end) is not int or
                        not section.span.start <= start < range_end <= section.span.end):
                    raise invalid('cursor is out of bounds or already at EOF', 'source_cursor')
            # A full table of contents grows without bound and can prevent even
            # a one-character chapter from fitting. Expose the selected node and
            # constant-size previous/next section links instead. Each link opens
            # that node with its own links, so all own-spans remain traversable.
            directory = self._navigation(source)
            position = next(i for i,node in enumerate(directory) if node['section_id']==str(ref.section_id))
            navigation = [directory[position]]
            navigation_status = {'position':position,'total_sections':len(directory),
                'previous_section_id':directory[position-1]['section_id'] if position else None,
                'next_section_id':directory[position+1]['section_id'] if position+1<len(directory) else None,
                'unlisted_before':position,'unlisted_after':len(directory)-position-1}
            base = {'schema_version':1,'call_id':str(call_id),'status':'ok','run_id':str(self.run.run_id),
                    'revision_id':str(self.run.revision_id),'section_id':str(ref.section_id),
                    'navigation':navigation,'navigation_status':navigation_status,'items':[],'next_cursor':None,'previous_cursor':None,'has_more':False,
                    'unread_spans':[],'has_unread':False,'unread_means':'not_returned_by_source_tools','returned_spans':[]}
            if section.span not in source.chunks.parsed.eligible_spans:
                base['status']='navigation_only'
                return self._commit_result(call_id,base,())
            # Token counts need not be monotone. Every accepted result is measured
            # in full; shrinking is conservative, with explicit no-progress failure.
            end = range_end
            candidate_id, evidence_id = uuid4(), uuid4()
            next_token = 'cur_' + secrets.token_urlsafe(32)
            previous_token = 'cur_' + secrets.token_urlsafe(32)
            cap, limit_reason = self._token_allowance()
            while end > start:
                span = Span(start=start,end=end)
                candidate = self._candidate(call_id,ref,span,source,candidate_id,evidence_id)
                unread=self._unreturned(ref,section,span)
                previous_start=next((s['start'] for s in unread if s['start']<start),None)
                base.update(items=[self._body_item(candidate,source_ref,source)],returned_spans=[span.model_dump()],
                            has_more=end<range_end or previous_start is not None,next_cursor=next_token if end<range_end else None,
                            previous_cursor=previous_token if previous_start is not None else None,
                            unread_spans=unread,has_unread=bool(unread))
                if self._count(render_payload(base)[0]) <= cap:
                    break
                end = start + (end-start)//2
            if end <= start:
                raise SourceBudgetExceeded(limit_reason, 'source metadata and one codepoint do not fit token budget')
            if base['next_cursor'] or base['previous_cursor']:
                with self.catalog._db.transaction(write=True) as connection:
                    self._active(connection)
                    for token,position,stop in ((next_token,end,range_end),(previous_token,previous_start,start)):
                        if (token==next_token and not base['next_cursor']) or (token==previous_token and previous_start is None):
                            continue
                        connection.execute('INSERT INTO source_handles VALUES(?,?,?,?)',
                            (token,str(self.run.run_id),'cursor',encode({'source_token':source_ref,'ref':ref.model_dump(mode='json'),'next_cp':position,'range_end':stop})))
            return self._commit_result(call_id,base,(candidate,))

    def _save_retrieval_trace(self, call_id, trace):
        if trace is not None:
            with self.catalog._db.transaction(write=True) as connection:
                connection.execute('INSERT INTO retrieval_traces VALUES(?,?)', (str(call_id), encode(trace)))

    def retrieval_trace(self, call_id):
        """Host diagnostics only, including failed searches; never a source tool."""
        with self.catalog._db.transaction() as connection:
            row = connection.execute('SELECT t.payload FROM retrieval_traces t JOIN source_calls c ON c.call_id=t.call_id '
                'WHERE t.call_id=? AND c.run_id=?', (str(call_id), str(self.run.run_id))).fetchone()
        return json.loads(row[0]) if row else None

    def search(self, query):
        with self._call('search') as call_id:
            if self.dense is None:
                raise invalid('retrieval capability unavailable', 'source_search')
            if self.retrieval.rerank:
                raise invalid('model rerank is not implemented at this stage', 'source_search')
            if getattr(self.dense, 'route', 'dense') != self.retrieval.route:
                raise invalid('search service route differs from the frozen run', 'source_search')
            with self.catalog._db.transaction() as connection:
                started, = connection.execute('SELECT started_ns FROM source_usage WHERE run_id=?',(str(self.run.run_id),)).fetchone()
            limit = {'dense':self.retrieval.dense_candidates, 'bm25':self.retrieval.bm25_candidates,
                     'hybrid':self.retrieval.dense_candidates+self.retrieval.bm25_candidates}[self.retrieval.route]
            try:
                result = self.dense.search(query,limit=limit,
                    deadline_monotonic_ns=started+(self.budget.duration_ms-self.budget.finish_reserve_ms)*1_000_000)
            except Exception as exc:
                self._save_retrieval_trace(call_id, getattr(exc, 'retrieval_trace', None))
                if not isinstance(exc, (RagError, ValueError, TypeError, ConnectionError, TimeoutError)):
                    raise RagError(ErrorCode.DEPENDENCY_UNAVAILABLE, str(exc), stage='source_search') from exc
                raise
            self._save_retrieval_trace(call_id, result.get('trace'))
            payload = {'schema_version':1,'call_id':str(call_id),'status':'empty','run_id':str(self.run.run_id),
                       'revision_id':str(self.run.revision_id),'route':{'strategy':self.retrieval.route,'rerank':False},
                       'items':[],'limited':{'by_count':False,'by_tokens':False}}
            candidates, seen, sources = [], set(), {}
            cap, _ = self._token_allowance()
            for hit in result['hits']:
                if hit['chunk_id'] in seen:
                    continue
                seen.add(hit['chunk_id'])
                if len(candidates)+self.usage()['window_fragments'] >= self.retrieval.context_chunks:
                    payload['limited']['by_count']=True
                    break
                ref = SourceRef.model_validate_json(encode({key:hit[key] for key in SourceRef.model_fields if key!='schema_version'}))
                identity = (ref.kb_id,ref.revision_id,ref.document_id,ref.document_version_id)
                if identity not in sources:
                    sources[identity] = self._read_source(ref)
                source = sources[identity]
                source.section(ref.section_id)
                archived = next((c.chunk for c in source.chunks.inputs if str(c.chunk.chunk_id)==hit['chunk_id']),None)
                if archived is None or archived.section_id != ref.section_id or len(archived.spans)!=1:
                    raise invalid('search candidate not in archived chunk set')
                span = archived.spans[0]
                token = self._issue_verified_source(ref,source,anchor_span=span)
                if hit['text'] != source.text[span.start:span.end] or digest(hit['text']) != archived.text_hash:
                    raise invalid('search body differs from canonical chunk')
                candidate = self._candidate(call_id,ref,span,source)
                item = self._body_item(candidate,token,source,chunk_id=hit['chunk_id'])
                payload['status']='ok'
                payload['items'].append(item)
                if self._count(render_payload(payload)[0]) > cap:
                    payload['items'].pop()
                    payload['limited']['by_tokens']=True
                    break
                candidates.append(candidate)
            if not candidates:
                payload['status']='empty'
            return self._commit_result(call_id,payload,candidates)
