"""First complete import build, using the same durable mutation state machine."""

import time
from ..indexes.manifest import index_error
from ..storage import publication
from ..storage.inputs import _InputRead
from ..storage.recovery import terminal
from .encoding import encode_documents, outcomes, _read_encoded, cancelled as _check_cancelled


def build_first_revision(catalog, owner, provider, backend, *, request_seconds=120, index_timeout=180, observer=None):
    """Requires completed capture/process. No cleanup of uncertain/published resources.

    observer receives stage/progress measurements; it grants no validation rights.
    Strict all-file success is retained. Document checkpoints share the ordinary
    encoder; no partial success is admitted to this publication path.
    """
    batch = catalog.get_batch(owner.token.batch_id)
    old = terminal(catalog,batch.batch_id)
    if old is not None:
        return old
    if batch.base_revision_id is not None:
        raise index_error('first-import coordinator requires a new library; updates are not exposed', 'manifest')
    return _build_complete_revision(catalog, owner, provider, backend, request_seconds=request_seconds,
                                    index_timeout=index_timeout, observer=observer)


def _build_complete_revision(catalog, owner, provider, backend, *, request_seconds=120, index_timeout=180, observer=None, cancelled=None):
    """Shared strict all-member build; an existing library requires approval."""
    batch = catalog.get_batch(owner.token.batch_id)
    old = terminal(catalog, batch.batch_id)
    if old is not None:
        return old
    if batch.base_revision_id is not None:
        from ..model_switch import require_rebuild
        require_rebuild(catalog, batch)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    if backend.catalog is not catalog or backend.storage != snapshot.resolved_config.storage:
        raise index_error('build backend differs from frozen catalog/endpoint')
    started = time.perf_counter()
    _check_cancelled(cancelled)
    calls = encode_documents(catalog,owner,provider,request_seconds=request_seconds,observer=observer,cancelled_check=cancelled)
    checkpoint_encode_seconds = time.perf_counter()-started
    prepared_start = time.perf_counter()
    _check_cancelled(cancelled)
    candidate, artifact = publication.register(catalog, owner)
    metrics = {'prepare_seconds': time.perf_counter()-prepared_start, 'documents': len(candidate.members),
               'chunks':len(candidate.rows), 'revision_id':str(candidate.revision_id),
               'manifest_hash':candidate.manifest_hash, 'schema_hash':candidate.schema_hash,
               'model_calls':calls, 'encode_seconds':sum(call['wall_seconds'] for call in calls), 'insert_seconds':0.0}
    def event(stage, **value):
        if observer:
            observer(stage, value)
    event('prepared', **metrics)
    t = time.perf_counter()
    backend.create(artifact, owner)
    event('created',revision_id=str(candidate.revision_id),collection_name=artifact['collection_name'])
    metrics['create_seconds'] = time.perf_counter()-t
    expected = []
    checkpoint_read_start = time.perf_counter()
    states = outcomes(catalog,batch.batch_id)
    input_read = _InputRead(catalog, batch.batch_id)
    vectors = {v['chunk_id']:v for raw in input_read.items
               for v in _read_encoded(catalog,batch.batch_id,raw,states[str(raw.entry.item_id)],_inputs=input_read)}
    metrics['checkpoint_seconds'] = (checkpoint_encode_seconds-metrics['encode_seconds']+
                                     time.perf_counter()-checkpoint_read_start)
    for offset in range(0,len(candidate.rows),256):
        _check_cancelled(cancelled)
        rows = []
        for original in candidate.rows[offset:offset+256]:
            vector = vectors[original['chunk_id']]
            row = {**original, 'vector_hash':vector['vector_hash']}
            expected.append(row)
            rows.append({**row, 'dense':vector['dense']})
        t = time.perf_counter()
        backend.insert(artifact, rows, owner)
        metrics['insert_seconds'] += time.perf_counter()-t
        event('encoded', complete=len(expected), total=len(candidate.rows))
    metrics['encode_and_insert_seconds'] = metrics['encode_seconds']+metrics['insert_seconds']
    publication.record_encoded(catalog, owner, candidate.revision_id, expected)
    event('inserted',revision_id=str(candidate.revision_id),rows=len(expected))
    _check_cancelled(cancelled)
    metrics.update(backend.finalize(artifact, len(expected), owner, index_timeout=index_timeout))
    event('indexed', **{k:v for k,v in metrics.items() if k != 'model_calls'})
    _check_cancelled(cancelled)
    t = time.perf_counter()
    proof = publication.validate(catalog, owner, candidate.revision_id, backend)
    metrics['validation_seconds'] = time.perf_counter()-t
    event('validated', rows_checked=proof['rows_checked'])
    _check_cancelled(cancelled)
    t = time.perf_counter()
    receipt = publication.publish(catalog, owner, candidate.revision_id)
    metrics['publish_seconds'] = time.perf_counter()-t
    metrics['build_seconds'] = time.perf_counter()-started
    return {'receipt':receipt, 'metrics':metrics, 'validation':proof}
