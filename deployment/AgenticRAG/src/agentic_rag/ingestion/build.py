"""First complete import build, using the same durable mutation state machine."""

import time
from uuid import uuid4

from ..capabilities import RequestContext, validate_response
from ..indexes.manifest import vector_hash, index_error
from ..storage import publication


def build_first_revision(catalog, owner, provider, backend, *, request_seconds=120, index_timeout=180, observer=None):
    """Requires completed capture/process. No cleanup of uncertain/published resources.

    observer receives stage/progress measurements; it grants no validation rights.
    Per-file partial success, recovery UI and GC remain later lifecycle tasks.
    """
    batch = catalog.get_batch(owner.token.batch_id)
    if batch.base_revision_id is not None:
        raise index_error('first-import coordinator requires a new library; updates are not exposed', 'manifest')
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    if backend.catalog is not catalog or backend.storage != snapshot.resolved_config.storage:
        raise index_error('build backend differs from frozen catalog/endpoint')
    started = time.perf_counter()
    candidate, artifact = publication.register(catalog, owner)
    metrics = {'prepare_seconds': time.perf_counter()-started, 'documents': len(candidate.members),
               'chunks':len(candidate.rows), 'revision_id':str(candidate.revision_id),
               'manifest_hash':candidate.manifest_hash, 'schema_hash':candidate.schema_hash,
               'model_calls':[], 'insert_seconds':0.0}
    def event(stage, **value):
        if observer:
            observer(stage, value)
    event('prepared', **metrics)
    t = time.perf_counter()
    backend.create(artifact, owner)
    metrics['create_seconds'] = time.perf_counter()-t
    expected, offset = [], 0
    profile = snapshot.resolved_config.embedding
    encode_start = time.perf_counter()
    while offset < len(candidate.rows):
        stop = min(len(candidate.rows), offset + profile.limits.max_batch_size)
        while (stop-offset)*max(candidate.token_counts[offset:stop]) > profile.limits.max_padded_tokens:
            stop -= 1
        if stop == offset:
            raise index_error('frozen item cannot fit model batch')
        items = candidate.model_inputs[offset:stop]
        context = RequestContext(request_id=uuid4(), owner_id=provider.owner_id, purpose='import',
                                 deadline_monotonic_ns=time.monotonic_ns()+int(request_seconds*1e9))
        response = provider.embed_documents(items, profile, context)
        validate_response(response, items, profile, context)
        with catalog._owned(owner):
            pass  # Reject late model results before any external insert.
        rows = []
        for original, result in zip(candidate.rows[offset:stop], response.results, strict=True):
            row = {**original, 'vector_hash':vector_hash(result.vector)}
            expected.append(row)
            rows.append({**row, 'dense':list(result.vector)})
        t = time.perf_counter()
        backend.insert(artifact, rows, owner)
        metrics['insert_seconds'] += time.perf_counter()-t
        metrics['model_calls'].append({'request_id':str(context.request_id), 'rows':stop-offset,
                                      'tokens':sum(r.input_tokens for r in response.results),
                                      **response.timings.model_dump(mode='json')})
        offset = stop
        event('encoded', complete=offset, total=len(candidate.rows), timings=metrics['model_calls'][-1])
    metrics['encode_and_insert_seconds'] = time.perf_counter()-encode_start
    publication.record_encoded(catalog, owner, candidate.revision_id, expected)
    metrics.update(backend.finalize(artifact, len(expected), owner, index_timeout=index_timeout))
    event('indexed', **{k:v for k,v in metrics.items() if k != 'model_calls'})
    t = time.perf_counter()
    proof = publication.validate(catalog, owner, candidate.revision_id, backend)
    metrics['validation_seconds'] = time.perf_counter()-t
    event('validated', rows_checked=proof['rows_checked'])
    t = time.perf_counter()
    receipt = publication.publish(catalog, owner, candidate.revision_id)
    metrics['publish_seconds'] = time.perf_counter()-t
    metrics['build_seconds'] = time.perf_counter()-started
    return {'receipt':receipt, 'metrics':metrics, 'validation':proof}
