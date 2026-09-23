"""Installed schema9 seed and schema10 migration/read child; genuine GPU data."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
from uuid import UUID, uuid4

import agentic_rag
import apsw
from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import Span
from agentic_rag.storage import Catalog

H = runpy.run_path(str(Path(__file__).with_name('r14_upgrade_process.py')))
SOURCE = runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def rows_state(db):
    rows = H['rows'](db)
    return {'schema':db.pragma('user_version'), 'migrations':rows.pop('schema_migrations'),
        'row_hashes':{k:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest() for k,v in rows.items()},
        'triggers':dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")),
        'integrity':list(db.execute('PRAGMA integrity_check')), 'foreign_keys':list(db.execute('PRAGMA foreign_key_check'))}


def main(args):
    root = Path(args.root)
    settings = json.loads((root/'settings.json').read_text())
    config = KnowledgeConfig.model_validate_json(json.dumps(settings['config']))
    worker = WorkerExecutionConfig.model_validate_json(json.dumps(settings['worker']))
    assert 'site-packages' in Path(agentic_rag.__file__).parts and sys.flags.isolated
    if args.mode == 'fault':
        from agentic_rag.storage import database
        original = database.files
        class Resources:
            def __init__(self, value): self.value = value
            def joinpath(self, name):
                value = self.value.joinpath(name)
                if name != 'model_switches.sql': return value
                class Fault:
                    def read_text(self, **kwargs): return value.read_text(**kwargs)+'\nSELECT * FROM r16_missing_table;\n'
                return Fault()
        database.files = lambda package: Resources(original(package))
        try: Catalog(root/'rollback-data')
        except Exception as exc: error = repr(exc)
        else: raise AssertionError('migration fault committed')
        assert 'r16_missing_table' in error
        db = apsw.Connection(str(root/'rollback-data/catalog.sqlite'))
        result = rows_state(db)
        assert result['schema'] == 9 and 'model_switches' not in result['row_hashes']
        result['injected_error'] = error
        print(json.dumps(result)); return
    if args.mode == 'read':
        # Compare migration alone, before automatic lifecycle maintenance.
        from agentic_rag.storage.database import Database
        from agentic_rag.storage.paths import DataDirectory
        Database(DataDirectory(root/'data'))
        db = apsw.Connection(str(root/'data/catalog.sqlite'))
        result = rows_state(db); db.close()
        catalog = Catalog(root/'data')
        ids = json.loads((root/'ids.json').read_text())
        history = H['snapshot'](catalog, ids)
        result.update(citation=history['citation'], typed_run=history['typed_run'], archives_verified=history['archives_verified'])
        assert all(not Path(path).exists() for path in ids['original_paths'])
        from agentic_rag.indexes.milvus import MilvusRevisionIndex
        from agentic_rag.models import LocalModelClient
        from agentic_rag.retrieval.dense import DenseSearch
        from agentic_rag.model_switch import inspect_model_switch
        provider = LocalModelClient(worker); backend = MilvusRevisionIndex(config.storage, catalog)
        try:
            with catalog.start_current_run(UUID(ids['kb_id']), config, 'qa') as lease:
                query = DenseSearch(catalog, lease.run.run_id, provider, backend).search('archived certificate')
                assert query['hits'] and query['profile_fingerprint'] == config.embedding.identity
                result['query_after_upgrade'] = query
            assert inspect_model_switch(catalog, UUID(ids['kb_id']), config)['state'] == 'CURRENT'
        finally:
            provider.close(); backend.close()
        print(json.dumps(result)); return
    catalog = Catalog(root/'data')
    with catalog._db.transaction() as db: assert db.pragma('user_version') == 9
    from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
    from agentic_rag.indexes.milvus import MilvusRevisionIndex
    from agentic_rag.models import LocalModelClient, FrozenTokenizer
    from agentic_rag.retrieval.dense import DenseSearch
    from agentic_rag.sources import SourceSession
    from agentic_rag.evidence import DeliveryGateway
    from agentic_rag.citations import CitationRegistry
    kb = catalog.create_library('genuine R15 schema9 data').kb_id
    source = root/'source.md'
    source.write_text('# Certificate\nThe archived telescope certificate remains blue.\n', encoding='utf-8')
    provider = LocalModelClient(worker); backend = MilvusRevisionIndex(config.storage, catalog)
    try:
        with begin_changes(catalog, kb, ProcessingSnapshot.capture(uuid4(), config),
                           (InputSelection(path=str(source)),)) as owner:
            published = build_changes(catalog, owner, provider, backend, FrozenTokenizer(config.embedding, args.model_cache))
        with catalog.start_run(kb, resolve_run(config, 'qa')) as lease:
            session = SourceSession(catalog, lease, SOURCE['ControlledMeter'](),
                                    dense=DenseSearch(catalog, lease.run.run_id, provider, backend))
            found = session.search('telescope certificate')
            opened = session.open(found.payload['items'][0]['source_ref'])
            gateway = DeliveryGateway(session); permit, _, _ = SOURCE['prepared'](gateway, opened)
            evidence, = gateway.settle(permit, 'confirmed')
            item = opened.payload['items'][0]
            saved = CitationRegistry(session).save(evidence, (Span.model_validate(item['returned_spans'][0]),), (item['text'],))
            ids = {'kb_id':str(kb), 'published_revision':published['receipt']['revision_id'],
                   'run_id':str(lease.run.run_id), 'citation_id':saved['citation']['citation_id'], 'original_paths':[str(source)]}
        source.unlink()
        (root/'ids.json').write_text(json.dumps(ids))
    finally:
        provider.close(); backend.close()
        assert backend._closed.wait(30)
    result = H['snapshot'](catalog, ids)
    result['seed_worker'] = provider.metadata
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root','model-cache','mode'): parser.add_argument('--'+name, required=True)
    main(parser.parse_args())
