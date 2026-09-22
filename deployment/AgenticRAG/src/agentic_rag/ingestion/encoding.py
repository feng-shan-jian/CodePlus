"""One complete-document checkpoint shared by strict and ordinary imports."""

import io
import json
import threading
import time
from uuid import UUID, uuid4

from .._schema import canonical_json
from ..capabilities import RequestContext, validate_response
from ..domain import ErrorCode, RagError
from ..indexes.manifest import rows_for_document, vector_hash
from ..storage import recovery as recovery_store
from ..storage.paths import failure
from .capture import read_input
from .processing import read_processed


def outcomes(catalog, batch_id):
    with catalog._db.transaction() as db:
        rows = db.execute('SELECT item_id,state,encoded_hash,error_json FROM mutation_item_results WHERE batch_id=?',(str(batch_id),)).fetchall()
    return {r[0]:{'state':r[1],'encoded_hash':r[2],'error':json.loads(r[3]) if r[3] else None} for r in rows}


def binding(catalog, batch_id, raw, checked):
    batch = catalog.get_batch(batch_id)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    item, version, result = checked
    rows, _, _ = rows_for_document(batch.kb_id,UUID(int=0),snapshot,version,result)
    return {'store_id':str(catalog.store_id),'batch_id':str(batch_id),'item_id':str(raw.entry.item_id),
            'manifest_hash':batch.input_manifest_hash,'config_fingerprint':snapshot.config_fingerprint,
            'encoding_fingerprint':snapshot.document_encoding_fingerprint,
            'raw_hash':raw.raw.sha256,'raw_size':raw.raw.size_bytes,
            'version':version.model_dump(mode='json'),
            'artifacts':[a.model_dump(mode='json') for a in item.output_hashes],
            'ordered_rows':list(rows)}


def read_encoded(catalog, batch_id, raw, state=None):
    """Authenticate original, config, parsed structure, exact order and float32."""
    state = state or outcomes(catalog,batch_id).get(str(raw.entry.item_id))
    if state is None or state['state'] != 'encoded':
        return None
    try:
        read_input(catalog,batch_id,raw.entry.item_id)
        checked = read_processed(catalog,batch_id,raw.entry.item_id)
        if checked is None or checked[0].stage != 'chunked':
            raise ValueError('no complete parsed document')
        expected = binding(catalog,batch_id,raw,checked)
        payload = json.loads(catalog.archives.read(state['encoded_hash']))
        # Genuine v7 encoded archives have only vectors. Their immutable SQL
        # item/config/version chain is still authenticated above; never rewrite
        # these historical checkpoints into a newly invented provenance record.
        if set(payload) == {'vectors'}:
            pass
        elif set(payload) != {'format','binding','vectors'} or payload['format'] != 2 or payload['binding'] != expected:
            raise ValueError('encoding checkpoint input/config/parsed binding differs')
        vectors = payload['vectors']
        if ([v['chunk_id'] for v in vectors] != [r['chunk_id'] for r in expected['ordered_rows']] or
                any(set(v) != {'chunk_id','vector_hash','dense'} or vector_hash(v['dense']) != v['vector_hash'] for v in vectors)):
            raise ValueError('complete ordered Chunk set or float32 digest differs')
        return vectors
    except (OSError, ValueError, KeyError, TypeError, RagError) as exc:
        raise RagError(ErrorCode.CHECKPOINT_INVALID,f'complete document encoding checkpoint invalid: {exc}',stage='encoding_checkpoint') from exc


def record(catalog, owner, raw, state, *, vectors=None, error=None, produced_by=None):
    obj = None
    if vectors is not None:
        checked = read_processed(catalog,owner.token.batch_id,raw.entry.item_id)
        payload = {'format':2,'binding':binding(catalog,owner.token.batch_id,raw,checked),'vectors':vectors}
        obj = catalog.archives.put(io.BytesIO(canonical_json(payload).encode()))
    with catalog._owned(owner,produced_by or owner.token) as db:
        if obj:
            db.execute('INSERT INTO archive_objects VALUES(?,?) ON CONFLICT DO NOTHING',(obj.sha256,obj.size_bytes))
            if db.execute('SELECT size_bytes FROM archive_objects WHERE sha256=?',(obj.sha256,)).fetchone() != (obj.size_bytes,):
                raise failure('encoded archive size differs')
        db.execute('INSERT INTO mutation_item_results VALUES(?,?,?,?,?,?)',
                   (str(raw.entry.item_id),str(owner.token.kb_id),str(owner.token.batch_id),state,
                    obj.sha256 if obj else None,error.model_dump_json() if error else None))


def cancelled(check):
    if check is not None and check():
        raise RagError(ErrorCode.CANCELLED,'batch cancelled; no partial publication',stage='mutation')


def model_request(catalog, owner, provider, items, profile, context):
    """Keep an actual unfinished RequestHandle as a durable cleanup dependency."""
    tracked = hasattr(provider,'submit_documents')
    identity = recovery_store.start_io(catalog,owner,'model' if tracked else 'model_sync',
                                      detail={'request_id':str(context.request_id)})
    if not tracked:
        try:
            response = provider.embed_documents(items,profile,context)
        except RagError as exc:
            if exc.error.code in (ErrorCode.INVALID_INPUT,ErrorCode.INPUT_TOO_LONG):
                recovery_store.observe_io(catalog,identity,finished=True)
            raise
        recovery_store.observe_io(catalog,identity,finished=True)
        return response
    handle = provider.submit_documents(items,profile,context)
    recovery_store.observe_io(catalog,identity,worker=provider.metadata)
    try:
        return handle.result()
    finally:
        def settle():
            if handle.wait_finished():
                recovery_store.observe_io(catalog,identity,finished=True)
        if handle.wait_finished(0):
            settle()
        else:
            threading.Thread(target=settle,name='rag-encoding-completion',daemon=True).start()


def encode_documents(catalog, owner, provider, *, ordinary=False, cancelled_check=None,
                     request_seconds=120, observer=None):
    batch = catalog.get_batch(owner.token.batch_id)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    profile, token = snapshot.resolved_config.embedding, owner.token
    previous = outcomes(catalog,batch.batch_id)
    calls = []
    for raw in catalog.get_input_items(batch.batch_id):
        cancelled(cancelled_check)
        state = previous.get(str(raw.entry.item_id))
        if state:
            if state['state'] == 'encoded':
                read_encoded(catalog,batch.batch_id,raw,state)
            elif state['state'] == 'unchanged':
                if not ordinary or raw.change not in ('unchanged','index_changed'):
                    raise failure('invalid unchanged document checkpoint')
                read_input(catalog,batch.batch_id,raw.entry.item_id)
            if not ordinary and state['state'] != 'encoded':
                raise RagError(ErrorCode.CHECKPOINT_INVALID,'strict first build requires every document to succeed',stage='encoding')
            continue
        if raw.stage == 'failed':
            if not ordinary:
                raise RagError(ErrorCode.CHECKPOINT_INVALID,'strict first build has a failed original snapshot',stage='encoding')
            record(catalog,owner,raw,'failed',error=raw.error,produced_by=token)
        elif ordinary and raw.change in ('unchanged','index_changed'):
            read_input(catalog,batch.batch_id,raw.entry.item_id)
            record(catalog,owner,raw,'unchanged',produced_by=token)
        else:
            checked = read_processed(catalog,batch.batch_id,raw.entry.item_id)
            if checked is None:
                raise failure('captured file has no terminal processing result')
            item, version, result = checked
            if item.stage == 'failed':
                if not ordinary:
                    raise RagError(ErrorCode.CHECKPOINT_INVALID,'strict first build has a failed parsed document',stage='encoding')
                record(catalog,owner,raw,'failed',error=item.error,produced_by=token)
                continue
            encoded, offset = [], 0
            try:
                rows, inputs, counts = rows_for_document(batch.kb_id,UUID(int=0),snapshot,version,result)
                while offset < len(rows):
                    cancelled(cancelled_check)
                    stop = min(len(rows),offset+profile.limits.max_batch_size)
                    while stop > offset and (stop-offset)*max(counts[offset:stop]) > profile.limits.max_padded_tokens:
                        stop -= 1
                    if stop == offset:
                        raise RagError(ErrorCode.INPUT_TOO_LONG,'document chunk exceeds frozen model batch capacity',stage='encoding')
                    values = inputs[offset:stop]
                    context = RequestContext(request_id=uuid4(),owner_id=provider.owner_id,purpose='import',
                                             deadline_monotonic_ns=time.monotonic_ns()+int(request_seconds*1e9))
                    request_start = time.perf_counter()
                    response = model_request(catalog,owner,provider,values,profile,context)
                    request_elapsed = time.perf_counter()-request_start
                    validate_response(response,values,profile,context)
                    cancelled(cancelled_check)
                    with catalog._owned(owner,token):
                        pass
                    for row, vector in zip(rows[offset:stop],response.results,strict=True):
                        encoded.append({'chunk_id':row['chunk_id'],'vector_hash':vector_hash(vector.vector),'dense':list(vector.vector)})
                    calls.append({'request_id':str(context.request_id),'rows':stop-offset,
                                  'wall_seconds':request_elapsed,
                                  'tokens':sum(r.input_tokens for r in response.results),**response.timings.model_dump(mode='json')})
                    offset = stop
                    if observer:
                        observer('encoding_response',{'item_id':str(raw.entry.item_id),'complete':offset,'total':len(rows)})
                record(catalog,owner,raw,'encoded',vectors=encoded,produced_by=token)
            except RagError as exc:
                if not ordinary or exc.error.code not in (ErrorCode.INVALID_INPUT,ErrorCode.INPUT_TOO_LONG):
                    raise
                record(catalog,owner,raw,'failed',error=exc.error,produced_by=token)
        if observer:
            observer('file_terminal',{'item_id':str(raw.entry.item_id),'document_id':str(raw.document_id)})
    cancelled(cancelled_check)
    return calls
