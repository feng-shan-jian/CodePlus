"""Milvus 3.0.1 / PyMilvus 3.0.2 revision adapter; no implicit fallback."""

import importlib.metadata
import json
import math
import threading
import time
from contextlib import contextmanager
from functools import partial, wraps
from uuid import UUID

from .manifest import (UUID_FIELDS, HASH_FIELDS, SCALAR_FIELDS, TEXT_MAX_BYTES, LAYOUT,
                       collection_name, vector_hash, index_error)
from .._schema import fingerprint
from ..storage.locks import ProcessLock
from ..storage import recovery as recovery_store
from ..storage import index_versions
from ..storage.readers import Reader, sdk_read
from ..storage.paths import failure


def publication_validator(catalog, storage, backend):
    """Bind the official proof algorithm before any source validation IO."""
    if (type(backend) is not MilvusRevisionIndex or backend.catalog is not catalog or
            backend.storage != storage):
        raise failure('validation requires the explicitly bound production Milvus adapter')
    # Dispatch the one production algorithm, never a caller/subclass pass flag.
    return partial(MilvusRevisionIndex.validate, backend)


def _tracked_read(method):
    @wraps(method)
    def invoke(self, artifact, *args, **kwargs):
        # This covers direct adapter callers as well as run-bound DenseSearch,
        # validation and base-vector reuse. Admission precedes every SDK read.
        with self._admission_guard:
            if self._closing:
                raise index_error('index adapter is closing; new readers are not admitted')
            reader = Reader(self.catalog, artifact, 'operation')
        try:
            return method(self, artifact, *args, **kwargs)
        finally:
            reader.finish()
    return invoke


class MilvusRevisionIndex:
    def __init__(self, storage, catalog, *, token=None, timeout=30):
        # Optional dependency is lazy so core installs never import database SDKs.
        from pymilvus import MilvusClient
        if importlib.metadata.version('pymilvus') != '3.0.2':
            raise index_error('supported PyMilvus version is 3.0.2', 'dependencies')
        if storage.credential_ref and token is None:
            raise index_error('Milvus credential reference has not been resolved', 'configuration')
        if not 0 < timeout <= 300:
            raise ValueError('Milvus timeout must be 0..300 seconds')
        self.storage, self.store_id, self.timeout = storage, catalog.store_id, timeout
        self.catalog = catalog
        self._closing = False
        self._closed = threading.Event()
        self._admission_guard = threading.Lock()
        self.database_name = 'default'  # Existing StorageConfig always used this database.
        self.client = MilvusClient(uri=storage.milvus_uri, token=token or '', db_name=self.database_name, timeout=timeout)
        self.server_version = self.client.get_server_version(timeout=timeout)
        if self.server_version.lstrip('v') != '3.0.1':
            self.client.close()
            raise index_error('unsupported Milvus server version: ' + self.server_version, 'dependencies')
        catalog._index_backends.add(self)
        catalog._maintain_indexes()

    def close(self):
        with self._admission_guard:
            if self._closing:
                return
            self._closing = True
        try:
            self.catalog._retire_backend(self)
        except Exception:
            self._close_transport()

    def wait_closed(self, timeout=None):
        return self._closed.wait(timeout)

    def _close_transport(self):
        # Serialize against the actual collector, then make closure observable.
        with self.catalog._maintenance_lock:
            self.catalog._index_backends.discard(self)
            try:
                self.client.close()
            finally:
                self._closed.set()
        self.catalog._maintenance_wake.set()

    def _name(self, artifact):
        expected = collection_name(self.storage.namespace, self.store_id, UUID(artifact['kb_id']),
                                   UUID(artifact['revision_id']), artifact['owner_epoch'])
        if artifact['collection_name'] != expected or artifact['spec']['layout'] != LAYOUT:
            raise index_error('artifact is outside this installation/library/revision ownership')
        if artifact['schema_hash'] != fingerprint('milvus-schema', artifact['spec']):
            raise index_error('artifact schema fingerprint differs')
        physical = index_versions.physical(self.catalog, artifact)
        if physical['collection_name'] != collection_name(self.storage.namespace, self.store_id,
                UUID(physical['kb_id']), UUID(physical['revision_id']), physical['owner_epoch']):
            raise index_error('shared collection is outside this installation ownership')
        return physical['collection_name']

    def _writable(self, artifact, owner):
        if self._closing:
            raise index_error('index adapter is closing; new writes are not admitted')
        from ..storage.publication import owned_artifact
        stored = owned_artifact(self.catalog, owner, UUID(artifact['revision_id']))
        if any(stored[k] != artifact[k] for k in ('artifact_id','collection_name','schema_hash','spec','kb_id','batch_id','owner_epoch')) or stored['state'] != 'PREPARING':
            raise index_error('artifact no longer belongs to this preparing owner')
        return self._name(stored)

    def _lifecycle(self, artifact):
        physical = index_versions.physical(self.catalog, artifact)
        return ProcessLock(self.catalog._directory.path('locks','artifact-' + str(UUID(physical['artifact_id'])) + '.lock'))

    def _ownership(self, artifact):
        physical = index_versions.physical(self.catalog, artifact)
        with self.catalog._db.transaction() as db:
            row = db.execute('SELECT i.endpoint,i.database_name,i.marker,p.collection_id,p.created_timestamp,p.description '
                'FROM artifact_creation_intents i LEFT JOIN artifact_ownership_proofs p ON p.artifact_id=i.artifact_id WHERE i.artifact_id=?',
                (physical['artifact_id'],)).fetchone()
        if row is not None and row[:2] != (self.storage.milvus_uri,getattr(self,'database_name','default')):
            raise index_error('physical ownership endpoint/database differs')
        return row

    def has_ownership(self, artifact):
        row = self._ownership(artifact)
        return row is not None and row[3] is not None

    def _read_call(self, artifact, method, *args, **kwargs):
        with sdk_read(self.catalog, artifact, method.__name__):
            return method(*args, **kwargs)

    def _verify_physical(self, artifact, *, required=True, reading=False):
        row = self._ownership(artifact)
        if row is None or row[3] is None:
            if required:
                raise index_error('no persisted create/describe physical ownership; preserve uncertain collection')
            return None  # Genuine old published indexes remain readable.
        observed = (self._read_call(artifact, self.client.describe_collection, self._name(artifact), timeout=self.timeout)
                    if reading else self.client.describe_collection(self._name(artifact),timeout=self.timeout))
        actual = tuple(str(observed.get(k,'')) for k in ('collection_id','created_timestamp','description'))
        if actual != row[3:] or row[5] != row[2]:
            raise index_error('physical collection ID/timestamp/description changed; preserve replacement')
        return observed

    @contextmanager
    def _io(self, artifact, owner, kind, *, creating=False):
        # All logical revisions serialize writes/reclamation on the physical
        # collection lifetime lock. Per-artifact IO receipts retain uncertain work.
        with self._lifecycle(artifact):
            name = self._writable(artifact,owner)
            if not creating:
                self._verify_physical(artifact)
            identity = recovery_store.start_io(self.catalog,owner,'milvus_' + kind,artifact_id=artifact['artifact_id'])
            yield name
            recovery_store.observe_io(self.catalog,identity,finished=True)

    def drop_owned(self, artifact, *, _claim=None):
        """Drop only an idle artifact claimed by the durable GC coordinator.

        PyMilvus drop has no expected collection-ID condition. We never reuse a
        name and serialize our own lifecycle. An external administrator replacing
        it between describe/drop (or SDK retry) remains an explicit limitation.
        """
        from ..storage.gc import _Claim
        from ..storage.publication import artifact as stored_artifact
        if type(_claim) is not _Claim or _claim.catalog is not self.catalog:
            raise index_error('physical drop requires the actual durable GC owner')
        stored = stored_artifact(self.catalog,UUID(artifact['revision_id']))
        if any(stored[k]!=artifact[k] for k in ('artifact_id','kb_id','revision_id','batch_id','collection_name','schema_hash','owner_epoch','spec')):
            raise index_error('drop target differs from the registered immutable identity')
        with self.catalog._db.transaction() as db:
            _claim.require(db, artifact)
        if not self.has_ownership(artifact):
            raise index_error('cannot delete collection without physical ownership proof')
        if not self.client.has_collection(self._name(artifact),timeout=self.timeout):
            return
        self._verify_physical(artifact)
        with self.catalog._db.transaction() as db:
            versions = index_versions.reclamation(db, artifact)
        if versions is None:
            self.client.drop_collection(self._name(artifact),timeout=self.timeout)
        elif versions:
            # Version IDs, rather than document IDs, cannot erase a replacement.
            self.client.delete(self._name(artifact),
                filter='document_version_id in ' + json.dumps(versions), timeout=self.timeout)

    def create(self, artifact, owner):
        from pymilvus import DataType, Function, FunctionType
        name = self._writable(artifact, owner)
        if artifact['state'] != 'PREPARING':
            raise index_error('published or failed artifacts are immutable')
        if index_versions.physical(self.catalog, artifact)['artifact_id'] != artifact['artifact_id']:
            with self._io(artifact, owner, 'attach'):
                pass
            return
        c = self.client
        intent = self._ownership(artifact)
        if intent is None or intent[3] is not None:
            raise index_error('creation requires a fresh physical intent without a prior receipt')
        schema = c.create_schema(auto_id=False, enable_dynamic_field=False, description=intent[2])
        for field in UUID_FIELDS:
            schema.add_field(field, DataType.VARCHAR, max_length=36, is_primary=field == 'chunk_id')
        for field in HASH_FIELDS:
            schema.add_field(field, DataType.VARCHAR, max_length=64)
        config = artifact['spec']['index']
        schema.add_field('text', DataType.VARCHAR, max_length=TEXT_MAX_BYTES, enable_analyzer=True,
                         analyzer_params={'tokenizer': config['tokenizer'], 'filter': config['filters']})
        for field in ('span_start', 'span_end'):
            schema.add_field(field, DataType.INT64)
        schema.add_field('dense', DataType.FLOAT_VECTOR, dim=1024)
        schema.add_field('sparse', DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(Function(name='text_bm25', input_field_names=['text'],
                                     output_field_names=['sparse'], function_type=FunctionType.BM25))
        with self._io(artifact,owner,'create',creating=True):
            if c.has_collection(name, timeout=self.timeout):
                raise index_error('candidate collection already exists; do not overwrite uncertain resources')
            c.create_collection(name, schema=schema, consistency_level='Strong', timeout=self.timeout)
            observed = c.describe_collection(name,timeout=self.timeout)
            if (observed.get('description') != intent[2] or not observed.get('collection_id') or
                    not observed.get('created_timestamp')):
                raise index_error('create/describe did not establish physical identity; preserve resource')
            with self.catalog._owned(owner) as db:
                db.execute('INSERT INTO artifact_ownership_proofs VALUES(?,?,?,?,?)',
                           (artifact['artifact_id'],str(observed['collection_id']),str(observed['created_timestamp']),
                            observed['description'],recovery_store.now()))

    def insert(self, artifact, rows, owner):
        name = self._writable(artifact, owner)
        if artifact['state'] != 'PREPARING':
            raise index_error('only preparing artifacts accept rows')
        for row in rows:
            if len(row['text'].encode('utf-8')) > TEXT_MAX_BYTES or vector_hash(row['dense']) != row['vector_hash']:
                raise index_error('index row size/vector hash differs')
        producers = index_versions.origins(self.catalog, artifact)
        incremental = index_versions.physical(self.catalog, artifact)['artifact_id'] != artifact['artifact_id']
        if incremental:
            with self.catalog._owned(owner) as db:
                inherited = {r[0] for r in db.execute('SELECT m.document_version_id FROM revision_members m '
                    'JOIN revisions r ON r.base_revision_id=m.revision_id WHERE r.revision_id=?',
                    (artifact['revision_id'],))}
                for row in rows:
                    identity = db.execute('SELECT kb_id,document_version_id FROM chunks WHERE chunk_id=?',
                                          (row['chunk_id'],)).fetchone()
                    if row['document_version_id'] in inherited or identity != (artifact['kb_id'],row['document_version_id']):
                        raise index_error('incremental writes must use new document versions and their own chunk IDs')
        physical_rows = []
        for row in rows:
            producer = producers.get(row['document_version_id'])
            if producer is None or row['revision_id'] != artifact['revision_id']:
                raise index_error('insert row is outside the candidate revision')
            physical_rows.append({**row, 'revision_id':producer})
        with self._io(artifact,owner,'insert'):
            # A recovered document has identical chunk IDs. Upsert makes replay
            # idempotent without deleting any previously published version.
            if not incremental:
                result = self.client.insert(name, physical_rows, timeout=self.timeout)
                count = result['insert_count']
            else:
                result = self.client.upsert(name, physical_rows, timeout=self.timeout)
                count = result['upsert_count']
        if count != len(rows):
            raise index_error('Milvus did not acknowledge the full insert batch')
        return result

    def finalize(self, artifact, count, owner, *, index_timeout=180):
        with self._io(artifact,owner,'finalize'):
            return self._finalize(artifact,count,owner,index_timeout=index_timeout)

    def _finalize(self, artifact, count, owner, *, index_timeout=180):
        name = self._writable(artifact, owner)
        if artifact['state'] != 'PREPARING':
            raise index_error('only preparing artifacts may be finalized')
        c, timings = self.client, {}
        start = time.perf_counter()
        c.flush(name, timeout=self.timeout)
        timings['flush_seconds'] = time.perf_counter() - start
        if index_versions.physical(self.catalog, artifact)['artifact_id'] != artifact['artifact_id']:
            # Existing indexes accept new segments; do not release a collection
            # that older runs are actively querying. Strong reads validate the
            # complete new document set before the metadata pointer can change.
            return timings
        config = artifact['spec']['index']
        indexes = c.prepare_index_params()
        indexes.add_index(field_name='dense', index_name='dense_cosine', index_type='IVF_FLAT',
                          metric_type='COSINE', params={'nlist': config['nlist']})
        indexes.add_index(field_name='sparse', index_name='sparse_bm25', index_type='SPARSE_INVERTED_INDEX',
                          metric_type='BM25', params={'inverted_index_algo': 'DAAT_MAXSCORE',
                          'bm25_k1': config['bm25_k1'], 'bm25_b': config['bm25_b']})
        start = time.perf_counter()
        c.create_index(name, index_params=indexes, timeout=self.timeout)
        deadline = time.monotonic() + index_timeout
        while True:
            observed = self._indexes(name)
            if all(i['state'] == 'Finished' and i['total_rows'] == count and
                   i['indexed_rows'] == count and i['pending_index_rows'] == 0 for i in observed.values()):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f'actual Milvus index rows incomplete: {observed}')
            time.sleep(min(1, max(0, deadline-time.monotonic())))
        timings['index_seconds'] = time.perf_counter() - start
        start = time.perf_counter()
        c.load_collection(name, timeout=self.timeout)
        # Sealed-index reload is deliberately real, not just an empty-index state.
        c.release_collection(name, timeout=self.timeout)
        c.load_collection(name, timeout=self.timeout)
        timings['load_sealed_reload_seconds'] = time.perf_counter() - start
        return timings

    def _indexes(self, name, artifact=None):
        call = self.client.describe_index if artifact is None else partial(self._read_call, artifact, self.client.describe_index)
        return {index: call(name, index, timeout=self.timeout)
                for index in ('dense_cosine', 'sparse_bm25')}

    @_tracked_read
    def inspect(self, artifact, count):
        name = self._name(artifact)
        shared = index_versions.shared(self.catalog, artifact)
        self._verify_physical(artifact,required=False,reading=True)
        c = self.client
        schema = self._read_call(artifact, c.describe_collection, name, timeout=self.timeout)
        fields = {f['name']: f for f in schema['fields']}
        if (schema['auto_id'] or schema['enable_dynamic_field'] or schema['aliases'] or
                schema['consistency_level_name'] != 'Strong' or
                set(fields) != set(SCALAR_FIELDS) | {'dense','sparse'}):
            raise index_error('actual Milvus schema scope/dynamic/consistency/fields differs')
        for field in UUID_FIELDS + HASH_FIELDS:
            if fields[field]['type'] != 21 or int(fields[field]['params']['max_length']) != (36 if field in UUID_FIELDS else 64):
                raise index_error('actual Milvus identity field differs')
        if not fields['chunk_id'].get('is_primary') or fields['dense']['type'] != 101 or int(fields['dense']['params']['dim']) != 1024 or fields['sparse']['type'] != 104:
            raise index_error('actual Milvus vector/primary schema differs')
        config = artifact['spec']['index']
        text = fields['text']
        analyzer = text['params'].get('analyzer_params')
        analyzer = json.loads(analyzer) if isinstance(analyzer, str) else analyzer
        functions = schema['functions']
        if (text['type'] != 21 or int(text['params']['max_length']) != TEXT_MAX_BYTES or
                str(text['params'].get('enable_analyzer')).lower() != 'true' or
                analyzer != {'tokenizer': config['tokenizer'], 'filter': config['filters']} or
                any(fields[f]['type'] != 5 for f in ('span_start','span_end')) or len(functions) != 1 or
                functions[0]['type'] != 1 or list(functions[0]['input_field_names']) != ['text'] or
                list(functions[0]['output_field_names']) != ['sparse']):
            raise index_error('actual Milvus analyzer/BM25 function differs')
        indexes = self._indexes(name, artifact)
        dense, sparse = indexes['dense_cosine'], indexes['sparse_bm25']
        if (dense['index_type'] != 'IVF_FLAT' or dense['metric_type'] != 'COSINE' or int(dense['nlist']) != config['nlist'] or
                sparse['index_type'] != 'SPARSE_INVERTED_INDEX' or sparse['metric_type'] != 'BM25' or
                sparse['inverted_index_algo'] != 'DAAT_MAXSCORE' or float(sparse['bm25_k1']) != config['bm25_k1'] or float(sparse['bm25_b']) != config['bm25_b'] or
                (not shared and any(i['state'] != 'Finished' or i['total_rows'] != count or i['indexed_rows'] != count or i['pending_index_rows'] != 0 for i in indexes.values()))):
            raise index_error('actual Milvus index parameters or complete row counts differ')
        state = str(self._read_call(artifact, c.get_load_state, name, timeout=self.timeout)['state'])
        if state != 'Loaded':
            raise index_error('actual Milvus index is not loaded')
        segments = [dict(segment_id=s.segment_id, num_rows=s.num_rows, state=s.state_name,
                         index_name=s.index_name, index_id=s.index_id, mem_size=s.mem_size)
                    for s in self._read_call(artifact, c.list_loaded_segments, name, timeout=self.timeout)]
        unique = {s['segment_id']:s for s in segments}
        if not shared and ((count > 0 and not unique) or sum(s['num_rows'] for s in unique.values()) != count or
                any(s['state'] != 'Sealed' or s['index_name'] not in ('IVF_FLAT','SPARSE_INVERTED_INDEX') or s['index_id'] <= 0 for s in segments)):
            raise index_error('loaded sealed segments do not cover all indexed rows')
        return {'schema': schema, 'indexes': indexes, 'load_state': state, 'segments': segments}

    @_tracked_read
    def search(self, artifact, value, *, field='dense', limit=10, nprobe=1, filter=''):
        name = self._name(artifact)
        if field not in ('dense', 'sparse') or not 1 <= limit <= 16384:
            raise ValueError('invalid search field/limit')
        if field == 'dense':
            vector_hash(value)
            if not 1 <= nprobe <= artifact['spec']['index']['nlist']:
                raise ValueError('nprobe exceeds frozen index')
            # ModelResult vectors are immutable tuples; PyMilvus 3.0.2's public
            # search validator accepts list/ndarray vectors on its wire boundary.
            value = list(value)
        producers = index_versions.origins(self.catalog, artifact)
        selected = index_versions.version_filter(self.catalog, artifact, filter)
        hits = self._read_call(artifact, self.client.search, name, data=[value], anns_field=field, limit=limit, filter=selected,
            output_fields=list(SCALAR_FIELDS), search_params={'metric_type': 'COSINE' if field == 'dense' else 'BM25',
            'params': {'nprobe': nprobe} if field == 'dense' else {}}, consistency_level='Strong', timeout=self.timeout)[0]
        result = []
        for hit in hits:
            row = dict(hit['entity'])
            if str(hit['chunk_id']) != row['chunk_id']:
                raise index_error('Milvus outer primary key differs from entity chunk identity')
            row = index_versions.logical_row(artifact, row, producers)
            result.append({'id':str(hit['chunk_id']), 'distance':float(hit['distance']), 'entity':row})
        return result

    @_tracked_read
    def read_vectors(self, artifact, expected):
        """Authenticate every source scalar and float32 value before reuse."""
        self.inspect(artifact,len(expected))
        by_id = {r['chunk_id']:r for r in expected}
        if len(by_id) != len(expected):
            raise index_error('base encoded manifest has duplicate IDs')
        vectors = {}
        producers = index_versions.origins(self.catalog, artifact)
        iterator = self._read_call(artifact, self.client.query_iterator, self._name(artifact),filter=index_versions.version_filter(self.catalog, artifact),output_fields=[*SCALAR_FIELDS,'dense'],
            batch_size=256,consistency_level='Strong',timeout=self.timeout)
        try:
            while batch := self._read_call(artifact, iterator.next):
                for row in batch:
                    row = index_versions.logical_row(artifact, row, producers)
                    identity = row['chunk_id']
                    if (identity in vectors or identity not in by_id or
                            {k:row[k] for k in SCALAR_FIELDS} != by_id[identity] or
                            vector_hash(row['dense']) != by_id[identity]['vector_hash']):
                        raise index_error('base vector reuse identity/source/config/float32 digest differs')
                    vectors[identity] = {'chunk_id':identity,'dense':list(row['dense']),'vector_hash':row['vector_hash']}
        finally:
            self._read_call(artifact, iterator.close)
        if set(vectors) != set(by_id):
            raise index_error('base vector reuse member set is incomplete')
        return vectors

    @_tracked_read
    def validate(self, artifact, expected):
        name = self._name(artifact)
        proof = self.inspect(artifact, len(expected))
        by_id = {r['chunk_id']: r for r in expected}
        seen, first, max_norm_error = set(), None, 0.0
        producers = index_versions.origins(self.catalog, artifact)
        iterator = self._read_call(artifact, self.client.query_iterator, name, filter=index_versions.version_filter(self.catalog, artifact), output_fields=[*SCALAR_FIELDS, 'dense'],
                    batch_size=256, consistency_level='Strong', timeout=self.timeout)
        try:
            while batch := self._read_call(artifact, iterator.next):
                for row in batch:
                    row = index_versions.logical_row(artifact, row, producers)
                    identity = row['chunk_id']
                    if identity in seen or identity not in by_id:
                        raise index_error('Milvus full iterator found duplicate/foreign chunk')
                    expected_row = by_id[identity]
                    if {k: row[k] for k in SCALAR_FIELDS} != expected_row or vector_hash(row['dense']) != expected_row['vector_hash']:
                        raise index_error('Milvus full row/source/config/text/vector hash differs: ' + identity)
                    seen.add(identity)
                    max_norm_error = max(max_norm_error, abs(math.sqrt(sum(v*v for v in row['dense']))-1))
                    first = first or row
        finally:
            self._read_call(artifact, iterator.close)
        if seen != set(by_id):
            raise index_error('Milvus full iterator ID set incomplete')
        # Empty published revisions are valid after explicit last-document
        # deletion. Exercise both query paths without inserting a dummy document.
        if first is None:
            dense = self.search(artifact,[1.0]+[0.0]*1023,limit=1,nprobe=artifact['spec']['index']['nlist'])
            sparse = self.search(artifact,'empty revision verification',field='sparse',limit=1)
            if dense or sparse:
                raise index_error('empty revision returned foreign rows')
            proof.update(rows_checked=0,id_set_hash=fingerprint('index-ids',{'ids':[]}),
                row_manifest_hash=fingerprint('index-rows',{'rows':expected}),
                vector_validation='empty set; no document vectors',max_norm_error=0.0,
                dense_smoke=[],bm25_smoke=[],bm25_analyzer_token_count=0,bm25_empty_is_valid=True,
                empty_revision=True,server_version=self.server_version,client_version='3.0.2')
            return json.loads(json.dumps(proof,default=list))
        dense = self.search(artifact, first['dense'], limit=min(10, len(expected)), nprobe=artifact['spec']['index']['nlist'])
        # Query text is an existing complete index input, never a fabricated constant.
        config = artifact['spec']['index']
        analysis = self._read_call(artifact, self.client.run_analyzer, texts=[first['text']],
            analyzer_params={'tokenizer':config['tokenizer'],'filter':config['filters']},timeout=self.timeout)
        tokens = list(analysis[0].tokens)
        sparse = self.search(artifact, first['text'], field='sparse', limit=min(10, len(expected)))
        if (not dense or (bool(tokens) != bool(sparse)) or
                any(str(h['id']) not in seen or not math.isfinite(float(h['distance'])) for h in dense+sparse)):
            raise index_error('Dense/BM25 actual query validation failed')
        proof.update(rows_checked=len(seen), id_set_hash=fingerprint('index-ids', {'ids': sorted(seen)}),
            row_manifest_hash=fingerprint('index-rows', {'rows': expected}), vector_validation='exact little-endian float32 SHA256; L2 abs_tol=1e-4',
            max_norm_error=max_norm_error, dense_smoke=[{'id': h['id'], 'score':float(h['distance'])} for h in dense],
            bm25_analyzer_token_count=len(tokens), bm25_empty_is_valid=not tokens,
            bm25_smoke=[{'id':h['id'], 'score':float(h['distance'])} for h in sparse],
            server_version=self.server_version, client_version='3.0.2')
        # Protobuf repeated-field objects in describe_collection are lists on wire.
        return json.loads(json.dumps(proof, default=list))
