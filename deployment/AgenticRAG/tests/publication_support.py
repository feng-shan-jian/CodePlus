"""Formal test helpers. Synthetic 1024D vectors are explicitly not quality evidence."""

import json
from pathlib import Path
import runpy
from unittest.mock import patch
from uuid import uuid4

from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot
from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, process_inputs
from agentic_rag.indexes.manifest import vector_hash, LAYOUT
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.storage import publication

HELPER = runpy.run_path(str(Path(__file__).with_name('parsing_support.py')))
VECTOR = [1.0] + [0.0]*1023


def configuration(data_dir, endpoint='http://127.0.0.1:19532', nlist=1):
    data = HELPER['configuration']().model_dump(mode='json')
    data['storage'] = {'data_dir':str(data_dir), 'milvus_uri':endpoint, 'namespace':'r10_acceptance'}
    data['processing']['index'].update(schema_version_name=LAYOUT, nlist=nlist)
    data['retrieval'].update(mode='fixed', nprobe=nlist, dense_candidates=50, bm25_candidates=50,
                             rerank_candidates=50, rrf_k=60, context_chunks=8, context_tokens=8000)
    return KnowledgeConfig.model_validate_json(json.dumps(data))


def processed(catalog, source, *, kb_id=None, text='# Heading\nImmutable telescope ocean source.\n'):
    source.write_text(text, encoding='utf-8')
    kb_id = kb_id or catalog.create_library('publication fixture').kb_id
    config = configuration(catalog._directory.root)
    owner = catalog.begin_import(kb_id, ProcessingSnapshot.capture(uuid4(), config),
        select_inputs((InputSelection(path=str(source)),)))
    capture_inputs(catalog, owner)
    process_inputs(catalog, owner, HELPER['tokenizer']())
    return owner, config


def synthetic_encoded(catalog, owner):
    prepared, artifact = publication.register(catalog, owner)
    expected = [{**r, 'vector_hash':vector_hash(VECTOR)} for r in prepared.rows]
    publication.record_encoded(catalog, owner, prepared.revision_id, expected)
    return prepared, artifact, expected


def constructed_backend(catalog, config, client):
    """Run the real adapter constructor with an explicitly controlled SDK."""
    with patch('pymilvus.MilvusClient', return_value=client) as constructor:
        backend = MilvusRevisionIndex(config.storage, catalog, timeout=1)
    constructor.assert_called_once_with(uri=config.storage.milvus_uri, token='', db_name='default', timeout=1)
    return backend


def unit_backend(catalog, config, expected):
    """Production iterator validator with explicitly stubbed transport/index IO."""
    class Iterator:
        def __init__(self): self.offset = 0
        def next(self):
            batch = [{**r, 'dense':VECTOR} for r in expected[self.offset:self.offset+256]]
            self.offset += len(batch)
            return batch
        def close(self): pass
    class Client:
        def get_server_version(self, **kwargs): return '3.0.1'
        def query_iterator(self, *args, **kwargs): return Iterator()
        def run_analyzer(self, *args, **kwargs):
            from types import SimpleNamespace
            return [SimpleNamespace(tokens=['synthetic'])]
    backend = constructed_backend(catalog, config, Client())
    backend.inspect = lambda artifact, count: {'indexes':{'synthetic_transport':True}, 'load_state':'Loaded'}
    backend.search = lambda artifact, value, **kwargs: [{'id':expected[0]['chunk_id'], 'distance':1.0, 'entity':expected[0]}]
    return backend


def unit_ready(catalog, owner, config):
    prepared, artifact, expected = synthetic_encoded(catalog, owner)
    backend = unit_backend(catalog, config, expected)
    proof = publication.validate(catalog, owner, prepared.revision_id, backend)
    return prepared, artifact, expected, backend, proof
