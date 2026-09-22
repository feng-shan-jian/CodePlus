"""Dense candidates and canonical source spans bound to one immutable run revision."""

import hashlib
import json
import math
import time
from uuid import UUID, uuid4

from ..capabilities import ModelInput, RequestContext, validate_response
from ..config import document_encoding_identity
from ..domain import RunStatus
from ..indexes.manifest import index_error, SCALAR_FIELDS
from ..storage import publication


class DenseSearch:
    def __init__(self, catalog, run_id, provider, backend):
        self.catalog, self.run_id, self.provider, self.backend = catalog, run_id, provider, backend
        self.run = catalog.get_run(run_id)
        self.artifact = publication.artifact(catalog, self.run.revision_id, published=True)
        batch = catalog.get_batch(UUID(self.artifact['batch_id']))
        self.snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
        if (self.artifact['kb_id'] != str(self.run.kb_id) or backend.catalog is not catalog or
                backend.storage != self.snapshot.resolved_config.storage or
                document_encoding_identity(self.run.resolved_config.knowledge) != self.snapshot.document_encoding_fingerprint):
            raise index_error('run/index/endpoint/encoding binding differs', 'dense')
        self.expected = {r['chunk_id']:r for r in json.loads(catalog.archives.read(self.artifact['expected_hash']))['rows']}

    def search(self, query, *, limit=10, deadline_monotonic_ns=None):
        if not isinstance(query, str) or not query.strip() or type(limit) is not int or not 1 <= limit <= 16384:
            raise ValueError('nonempty query and positive bounded candidate limit required')
        run, pin = self.catalog.get_run(self.run_id), self.catalog.get_pin(self.run_id)
        if ((run.run_id,run.kb_id,run.revision_id,run.resolved_config_hash) !=
                (self.run.run_id,self.run.kb_id,self.run.revision_id,self.run.resolved_config_hash) or
                pin.state != 'active' or run.status != RunStatus.RUNNING):
            raise index_error('run binding no longer active', 'dense')
        start = time.perf_counter()
        item = ModelInput(item_id=uuid4(), text=query)
        context = RequestContext(request_id=uuid4(), owner_id=self.provider.owner_id, purpose=run.resolved_config.task_kind,
            deadline_monotonic_ns=deadline_monotonic_ns or time.monotonic_ns()+120_000_000_000)
        response = self.provider.embed_query(item, self.snapshot.resolved_config.embedding, context)
        validate_response(response, (item,), self.snapshot.resolved_config.embedding, context)
        hits = self.backend.search(self.artifact, response.results[0].vector, limit=limit,
                                   nprobe=run.resolved_config.knowledge.retrieval.nprobe)
        output = []
        for hit in hits:
            identity, row = str(hit['id']), hit['entity']
            expected = self.expected.get(identity)
            if expected is None or {k:row[k] for k in SCALAR_FIELDS} != expected or not math.isfinite(float(hit['distance'])):
                raise index_error('returned candidate differs from published manifest', 'dense')
            version = self.catalog.get_version(UUID(row['document_version_id']))
            doc = self.catalog.get_document(version.document_id)
            text = self.catalog.archives.read(version.parsed_hash).decode('utf-8')
            body = text[row['span_start']:row['span_end']]
            if (version.document_id != UUID(row['document_id']) or doc.kb_id != run.kb_id or
                    version.raw_hash != row['raw_hash'] or version.parsed_hash != row['parsed_hash'] or
                    hashlib.sha256(body.encode()).hexdigest() != row['body_hash']):
                raise index_error('canonical source differs from fixed revision candidate', 'source')
            output.append({'chunk_id':identity, 'kb_id':row['kb_id'], 'revision_id':row['revision_id'],
                'document_id':row['document_id'], 'document_version_id':row['document_version_id'],
                'section_id':row['section_id'], 'source_name':version.source_metadata.original_name, 'source_uri':version.source_uri,
                'raw_hash':version.raw_hash, 'parsed_hash':version.parsed_hash, 'text_hash':row['body_hash'],
                'text':body, 'source_spans':[{'char_start':row['span_start'], 'char_end':row['span_end']}],
                'score':float(hit['distance']), 'branch':'dense'})
        return {'run_id':str(run.run_id), 'revision_id':str(run.revision_id), 'request_id':str(context.request_id),
                'profile_fingerprint':response.profile_fingerprint, 'hits':output,
                'elapsed_ms':(time.perf_counter()-start)*1000, 'model_timings':response.timings.model_dump(mode='json')}
