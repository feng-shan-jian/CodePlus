"""Dense, native BM25 and core RRF over one immutable run and collection."""

import hashlib
import json
import math
import time
from uuid import UUID, uuid4

from ..capabilities import ModelInput, RequestContext, validate_response
from ..config import document_encoding_identity
from ..domain import ErrorCode, RagError, RunStatus
from ..indexes.manifest import index_error, SCALAR_FIELDS
from ..storage import publication, readers
from .rrf import reciprocal_rank_fusion


class RetrievalSearch:
    """Execute the run's frozen route; a BM25 run accepts provider=None.

    Diagnostics are separate from canonical hits and have no evidence authority.
    SourceSession persists them against the normal source call, including errors.
    """
    def __init__(self, catalog, run_id, provider, backend):
        self.catalog, self.run_id, self.provider, self.backend = catalog, run_id, provider, backend
        self.run = catalog.get_run(run_id)
        self.route = self.run.resolved_config.retrieval.route
        self.artifact = publication.artifact(catalog, self.run.revision_id, published=True)
        batch = catalog.get_batch(UUID(self.artifact['batch_id']))
        self.snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
        if (self.artifact['kb_id'] != str(self.run.kb_id) or backend.catalog is not catalog or
                backend.storage != self.snapshot.resolved_config.storage or
                document_encoding_identity(self.run.resolved_config.knowledge) != self.snapshot.document_encoding_fingerprint):
            raise index_error('run/index/endpoint/encoding binding differs', 'retrieval')
        self.expected = {r['chunk_id']:r for r in json.loads(catalog.archives.read(self.artifact['expected_hash']))['rows']}

    def _limits(self, limit):
        config = self.run.resolved_config.retrieval
        return {branch: getattr(config, branch + '_candidates')
                for branch in (('dense', 'bm25') if self.route == 'hybrid' else (self.route,))}

    def _active(self):
        run, pin = self.catalog.get_run(self.run_id), self.catalog.get_pin(self.run_id)
        if ((run.run_id,run.kb_id,run.revision_id,run.resolved_config_hash) !=
                (self.run.run_id,self.run.kb_id,self.run.revision_id,self.run.resolved_config_hash) or
                pin.state != 'active' or run.status != RunStatus.RUNNING):
            raise index_error('run binding no longer active', 'retrieval')

    def search(self, query, *, limit=10, deadline_monotonic_ns=None):
        if not isinstance(query, str) or not query.strip() or type(limit) is not int or not 1 <= limit <= 32768:
            raise ValueError('nonempty query and positive bounded candidate limit required')
        limits = self._limits(limit)
        if any(not 1 <= value <= 16384 for value in limits.values()):
            raise ValueError('branch candidate limits must be 1..16384')
        config = self.run.resolved_config.retrieval
        start = time.perf_counter()
        request_id = uuid4()
        trace = {'schema_version':1, 'request_id':str(request_id), 'run_id':str(self.run_id),
            'kb_id':str(self.run.kb_id), 'revision_id':str(self.run.revision_id),
            'resolved_config_hash':self.run.resolved_config_hash, 'query':query, 'route':self.route,
            'collection_name':self.artifact['collection_name'], 'filter':'',
            'index':self.artifact['spec']['index'], 'index_schema_hash':self.artifact['schema_hash'],
            'parameters':{'branch_limits':limits, 'result_limit':limit, 'rrf_k':config.rrf_k,
                          'nprobe':config.nprobe, 'rerank':False},
            'branches':{name:{'status':'not_started', 'candidates':[]} for name in limits},
            'fusion':[], 'returned_ids':[], 'status':'running'}
        profile, model_timings = None, None
        stage = 'binding'
        try:
            # One reader protects both branches. No query resolves current again.
            with readers.operation(self.catalog, self.artifact, self.run_id):
                self._active()
                candidates = {}
                for branch, bound in limits.items():
                    stage = branch
                    detail = trace['branches'][branch]
                    begin = time.perf_counter()
                    try:
                        self._deadline(deadline_monotonic_ns)
                        value = query
                        if branch == 'dense':
                            value, profile, model_timings = self._encode(query, request_id, deadline_monotonic_ns)
                            detail.update(profile_fingerprint=profile, model_timings=model_timings)
                        raw = self.backend.search(self.artifact, value, field='dense' if branch=='dense' else 'sparse',
                            limit=bound, nprobe=config.nprobe, filter='')
                        self._deadline(deadline_monotonic_ns)
                        if len(raw) > bound:
                            raise index_error('backend exceeded branch candidate limit', branch)
                        candidates[branch] = []
                        for rank, hit in enumerate(raw, 1):
                            candidate = self._source(hit, branch)
                            candidates[branch].append(candidate)
                            detail['candidates'].append({'chunk_id':candidate['chunk_id'], 'rank':rank,
                                'score':candidate['score'], 'score_type':candidate['score_type']})
                        detail['status'] = 'ok' if raw else 'empty'
                    except Exception as exc:
                        detail.update(status='error', error=self._error(exc))
                        raise
                    finally:
                        detail['elapsed_ms'] = (time.perf_counter()-begin)*1000
                stage = 'fusion'
                if self.route == 'hybrid':
                    trace['fusion'] = reciprocal_rank_fusion(candidates, config.rrf_k)
                    by_id = {hit['chunk_id']:hit for hits in candidates.values() for hit in hits}
                    output = [{**by_id[item['chunk_id']], 'branch':'hybrid', 'score':item['score'],
                               'score_type':'rrf'} for item in trace['fusion'][:limit]]
                else:
                    # Preserve the backend's order for single-route retrieval.
                    seen = set(); output = []
                    for hit in candidates[self.route]:
                        if hit['chunk_id'] not in seen:
                            seen.add(hit['chunk_id']); output.append(hit)
                    output = output[:limit]
                trace.update(status='ok' if output else 'empty', returned_ids=[h['chunk_id'] for h in output])
        except Exception as exc:
            trace.update(status='error', error={**self._error(exc), 'failed_stage':stage})
            exc.retrieval_trace = trace
            raise
        finally:
            trace['elapsed_ms'] = (time.perf_counter()-start)*1000
        return {'run_id':str(self.run_id), 'revision_id':str(self.run.revision_id), 'request_id':str(request_id),
                'profile_fingerprint':profile, 'hits':output, 'elapsed_ms':trace['elapsed_ms'],
                'model_timings':model_timings, 'trace':trace}

    @staticmethod
    def _error(exc):
        return {'type':type(exc).__name__, 'message':str(exc),
                'detail':exc.error.model_dump(mode='json') if isinstance(exc, RagError) else None}

    @staticmethod
    def _deadline(deadline):
        if deadline is not None and time.monotonic_ns() >= deadline:
            raise RagError(ErrorCode.DEADLINE_EXCEEDED, 'retrieval deadline expired', stage='retrieval')

    def _encode(self, query, request_id, deadline):
        if self.provider is None:
            raise RagError(ErrorCode.CAPABILITY_UNAVAILABLE, 'query Embedding provider unavailable', stage='dense')
        item = ModelInput(item_id=uuid4(), text=query)
        context = RequestContext(request_id=request_id, owner_id=self.provider.owner_id,
            purpose=self.run.resolved_config.task_kind, deadline_monotonic_ns=deadline or time.monotonic_ns()+120_000_000_000)
        response = readers.query(self.catalog, self.artifact, self.run_id, self.provider,
                                 item, self.snapshot.resolved_config.embedding, context)
        validate_response(response, (item,), self.snapshot.resolved_config.embedding, context)
        return response.results[0].vector, response.profile_fingerprint, response.timings.model_dump(mode='json')

    def _source(self, hit, branch):
        identity, row = str(hit['id']), hit['entity']
        expected = self.expected.get(identity)
        if expected is None or {k:row[k] for k in SCALAR_FIELDS} != expected or not math.isfinite(float(hit['distance'])):
            raise index_error('returned candidate differs from published manifest', branch)
        version = self.catalog.get_version(UUID(row['document_version_id']))
        doc = self.catalog.get_document(version.document_id)
        text = self.catalog.archives.read(version.parsed_hash).decode('utf-8')
        body = text[row['span_start']:row['span_end']]
        if (version.document_id != UUID(row['document_id']) or doc.kb_id != self.run.kb_id or
                version.raw_hash != row['raw_hash'] or version.parsed_hash != row['parsed_hash'] or
                hashlib.sha256(body.encode()).hexdigest() != row['body_hash']):
            raise index_error('canonical source differs from fixed revision candidate', 'source')
        return {'chunk_id':identity, 'kb_id':row['kb_id'], 'revision_id':row['revision_id'],
            'document_id':row['document_id'], 'document_version_id':row['document_version_id'],
            'section_id':row['section_id'], 'source_name':version.source_metadata.original_name, 'source_uri':version.source_uri,
            'raw_hash':version.raw_hash, 'parsed_hash':version.parsed_hash, 'text_hash':row['body_hash'],
            'text':body, 'source_spans':[{'char_start':row['span_start'], 'char_end':row['span_end']}],
            'score':float(hit['distance']), 'score_type':'cosine_similarity' if branch=='dense' else 'bm25', 'branch':branch}
