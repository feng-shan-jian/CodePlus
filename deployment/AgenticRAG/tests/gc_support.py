"""Explicit synthetic metadata/SDK transport for GC protocol tests, not IO proof."""

import io
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import uuid4

from agentic_rag._schema import canonical_json, fingerprint
from agentic_rag.config import ProcessingSnapshot
from agentic_rag.domain import IndexState, KnowledgeRevision
from agentic_rag.indexes.manifest import collection_name, schema_spec
from agentic_rag.storage import Catalog, publication

H = runpy.run_path(str(Path(__file__).with_name('publication_support.py')))


class Client:
    def __init__(self):
        self.collections, self.dropped, self.calls = {}, [], []
        self.search_hook = None
        self.drop_hook = None

    def get_server_version(self, **kwargs): return '3.0.1'
    def close(self): pass
    def has_collection(self, name, **kwargs): return name in self.collections
    def describe_collection(self, name, **kwargs): return dict(self.collections[name])

    def drop_collection(self, name, **kwargs):
        if self.drop_hook:
            self.drop_hook(name)
        self.dropped.append(name)
        self.collections.pop(name)

    def search(self, name, **kwargs):
        self.calls.append(name)
        if self.search_hook:
            return self.search_hook(name)
        return [[]]


def setup(root, *, automatic=False):
    catalog = Catalog(root / 'data')
    automatic_callback = catalog._maintain_indexes
    if not automatic:
        catalog._maintain_indexes = lambda: []
    config = H['configuration'](root / 'data')
    client = Client()
    backend = H['constructed_backend'](catalog, config, client)
    return SimpleNamespace(catalog=catalog, config=config, client=client, backend=backend,
                           kb=catalog.create_library('synthetic GC metadata').kb_id,
                           automatic_callback=automatic_callback)


def published(s, *, physical=True, config=None, kb=None):
    """Synthetic READY row/receipt. No claim that these empty vectors were built."""
    config, kb = config or s.config, kb or s.kb
    snapshot = ProcessingSnapshot.capture(uuid4(), config)
    owner = s.catalog.begin_mutation(kb, snapshot, 'a' * 64)
    batch = s.catalog.get_batch(owner.token.batch_id)
    rid, aid = uuid4(), uuid4()
    revision = KnowledgeRevision(revision_id=rid, kb_id=kb, base_revision_id=batch.base_revision_id,
        manifest_hash='a' * 64, processing_snapshot_id=snapshot.snapshot_id, index_state=IndexState.PREPARING)
    s.catalog.add_candidate(owner, revision, ())
    name = collection_name(config.storage.namespace, s.catalog.store_id, kb, rid, owner.token.owner_epoch)
    spec = schema_spec(snapshot)
    obj = s.catalog.archives.put(io.BytesIO(b'{"rows":[]}'))
    marker = 'unit-owned:' + str(aid)
    with s.catalog._db.transaction(write=True) as db:
        db.execute('INSERT INTO archive_objects VALUES(?,?) ON CONFLICT DO NOTHING', (obj.sha256, obj.size_bytes))
        db.execute('INSERT INTO index_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (str(aid), str(kb), str(rid), str(batch.batch_id), name, fingerprint('milvus-schema', spec),
                    owner.token.owner_epoch, canonical_json(spec), obj.sha256, '{}', 'READY'))
        db.execute("UPDATE revisions SET index_state='READY' WHERE revision_id=?", (str(rid),))
        db.execute('INSERT INTO current_candidates VALUES(?,?,?)', (str(batch.batch_id), str(kb), str(aid)))
        db.execute('INSERT INTO publications VALUES(?,?,?,?,?,?,?,?)',
                   (str(batch.batch_id), str(kb), str(rid), str(aid), str(owner.token.owner_nonce),
                    owner.token.owner_epoch, 'a' * 64, 'synthetic-test'))
        db.execute("UPDATE mutation_batches SET state='PUBLISHED',published_revision_id=? WHERE batch_id=?",
                   (str(rid), str(batch.batch_id)))
        db.execute('UPDATE libraries SET current_revision_id=?,pending_mutation_id=NULL WHERE kb_id=?', (str(rid), str(kb)))
        db.execute('DELETE FROM revision_dependencies WHERE batch_id=?', (str(batch.batch_id),))
        db.execute('INSERT INTO artifact_creation_intents VALUES(?,?,?,?)', (str(aid), config.storage.milvus_uri, 'default', marker))
        if physical:
            db.execute('INSERT INTO artifact_ownership_proofs VALUES(?,?,?,?,?)', (str(aid), str(aid.int), '123', marker, 'synthetic-test'))
    owner.close()
    if physical:
        s.client.collections[name] = {'collection_id': str(aid.int), 'created_timestamp': '123', 'description': marker}
    return publication.artifact(s.catalog, rid)
