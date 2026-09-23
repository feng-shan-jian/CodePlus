"""Genuine schema8 seed and installed schema9 migration/history/resume child."""

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
from agentic_rag.storage import Catalog, publication

H = runpy.run_path(str(Path(__file__).with_name('r14_upgrade_process.py')))
SOURCE = runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def main(args):
    root = Path(args.root)
    settings = json.loads((root/'settings.json').read_text(encoding='utf-8'))
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
                if name != 'lifetimes.sql': return value
                class Fault:
                    def read_text(self, **kw): return value.read_text(**kw)+'\nSELECT * FROM r15_absent_migration_table;\n'
                return Fault()
        database.files = lambda package: Resources(original(package))
        try: Catalog(root/'rollback-data')
        except Exception as exc: error = repr(exc)
        else: raise AssertionError('migration fault did not roll back')
        assert 'r15_absent_migration_table' in error
        db = apsw.Connection(str(root/'rollback-data/catalog.sqlite'))
        data = H['rows'](db)
        assert db.pragma('user_version') == 8 and 'index_readers' not in data
        print(json.dumps({'error':error, 'schema':8, 'migrations':data.pop('schema_migrations'),
            'row_hashes':{k:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest() for k,v in data.items()},
            'integrity':list(db.execute('PRAGMA integrity_check')), 'foreign_keys':list(db.execute('PRAGMA foreign_key_check'))}))
        return
    catalog = Catalog(root/'data')
    if args.mode == 'seed':
        from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
        from agentic_rag.indexes.milvus import MilvusRevisionIndex
        from agentic_rag.models import LocalModelClient, FrozenTokenizer
        from agentic_rag.sources import SourceSession
        from agentic_rag.retrieval.dense import DenseSearch
        from agentic_rag.citations import CitationRegistry
        from agentic_rag.evidence import DeliveryGateway
        with catalog._db.transaction() as db: assert db.pragma('user_version') == 8
        kb = catalog.create_library('genuine committed R14 schema8')
        base, uncited, extra = root/'baseline.md', root/'uncited.md', root/'pending.md'
        base.write_text('# History\nOriginal telescope certificate remains oldblue.\n', encoding='utf-8')
        uncited.write_text('# Uncited\nAn unreferenced old archive is violet.\n', encoding='utf-8')
        provider = LocalModelClient(worker); backend = MilvusRevisionIndex(config.storage, catalog)
        tokenizer = FrozenTokenizer(config.embedding, args.model_cache)
        def change(paths):
            return begin_changes(catalog, kb.kb_id, ProcessingSnapshot.capture(uuid4(), config),
                tuple(InputSelection(path=str(path)) for path in paths))
        try:
            with change((base, uncited)) as owner: published = build_changes(catalog, owner, provider, backend, tokenizer)
            lease = catalog.start_run(kb.kb_id, resolve_run(config, 'qa'))
            session = SourceSession(catalog, lease, SOURCE['ControlledMeter'](),
                                    dense=DenseSearch(catalog, lease.run.run_id, provider, backend))
            found = session.search('telescope certificate')
            selected = next(item for item in found.payload['items'] if item['file_name'] == 'baseline.md')
            opened = session.open(selected['source_ref'])
            gateway = DeliveryGateway(session); permit, _, _ = SOURCE['prepared'](gateway, opened)
            evidence, = gateway.settle(permit, 'confirmed')
            item = opened.payload['items'][0]
            citation = CitationRegistry(session).save(evidence, (Span.model_validate(item['returned_spans'][0]),), (item['text'],))
            with catalog._db.transaction() as db:
                versions = [{'document_id':doc, 'version_id':version,
                             'text':catalog.archives.read(catalog.get_version(UUID(version)).parsed_hash).decode()}
                    for doc, version in db.execute('SELECT document_id,document_version_id FROM revision_members WHERE revision_id=?', (published['receipt']['revision_id'],))]
            base.write_text('# History\nPending telescope certificate now newred.\n', encoding='utf-8')
            extra.write_text('# Pending\nComplete encoded ocean checkpoint.\n', encoding='utf-8')
            pending = change((base, extra))
            class Pause(Exception): pass
            def pause(stage, value):
                if stage == 'validated': raise Pause()
            try:
                with pending: build_changes(catalog, pending, provider, backend, tokenizer, observer=pause)
            except Pause: pass
            else: raise AssertionError('pending batch unexpectedly published')
            ids = {'kb_id':str(kb.kb_id), 'published_revision':published['receipt']['revision_id'],
                   'pending_batch':str(pending.token.batch_id), 'run_id':str(lease.run.run_id),
                   'pin_nonce':str(lease.pin.owner_nonce), 'citation_id':citation['citation']['citation_id'],
                   'versions':versions, 'original_paths':list(map(str, (base, uncited, extra)))}
            (root/'ids.json').write_text(json.dumps(ids), encoding='utf-8')
            for path in (base, uncited, extra): path.unlink()
        finally:
            provider.close(); backend.close()
        result = H['snapshot'](catalog, ids)
        result['seed_worker'] = provider.metadata
        print(json.dumps(result)); return
    ids = json.loads((root/'ids.json').read_text(encoding='utf-8'))
    if args.mode == 'read':
        from agentic_rag.source_archive import read_version
        parser_calls = []
        def trace(frame, event, arg):
            if event == 'call' and frame.f_code.co_name == 'parse_document':
                parser_calls.append(frame.f_code.co_filename)
        sys.setprofile(trace)
        result = H['snapshot'](catalog, ids)
        assert all(not Path(path).exists() for path in ids['original_paths'])
        for item in ids['versions']:
            source = read_version(catalog, UUID(ids['kb_id']), UUID(ids['published_revision']),
                                  UUID(item['document_id']), UUID(item['version_id']))
            assert source.text == item['text']
        with catalog._db.transaction() as db:
            assert db.execute('SELECT * FROM run_lifetimes').fetchall() == []
            assert db.execute('SELECT * FROM index_readers').fetchall() == []
        assert catalog.get_pin(UUID(ids['run_id'])).state == 'active'
        try: catalog.release_crashed_run(UUID(ids['run_id']), UUID(ids['pin_nonce']))
        except Exception as exc: result['legacy_pin_preserved'] = repr(exc)
        else: raise AssertionError('missing old run lifetime was fabricated')
        assert not any(name == 'torch' or name.startswith('torch.') or name == 'pymilvus' or name.startswith('pymilvus.') for name in sys.modules)
        sys.setprofile(None); assert not parser_calls
        result['parser_calls'] = parser_calls
        result['no_external_imports'] = True
        print(json.dumps(result)); return
    from agentic_rag.ingestion import inspect_recovery, continue_recovery
    from agentic_rag.indexes.milvus import MilvusRevisionIndex
    from agentic_rag.models import LocalModelClient, FrozenTokenizer
    from agentic_rag.storage import OwnerToken
    plan = inspect_recovery(catalog, UUID(ids['pending_batch']))
    assert all(item['checkpoint'] == 'encoded' for item in plan['items'])
    owner = OwnerToken(**{key:UUID(value) if key != 'owner_epoch' else value for key,value in plan['expected'].items()})
    provider = LocalModelClient(worker); backend = MilvusRevisionIndex(config.storage, catalog)
    tokenizer = FrozenTokenizer(config.embedding, args.model_cache)
    try:
        old = publication.artifact(catalog, UUID(ids['published_revision']), published=True)
        assert backend.has_ownership(old)
        old_query = backend.search(old, 'telescope', field='sparse'); assert old_query
        result = continue_recovery(catalog, owner, runtime_factory=lambda frozen: (tokenizer, provider, backend))
        assert not provider.handles, 'complete old encoded checkpoints unexpectedly re-encoded'
        assert catalog.get_pin(UUID(ids['run_id'])).state == 'active'
        print(json.dumps({'status':'PASS', 'plan':plan, 'result':result, 'old_query':old_query,
                          'model_requests':len(provider.handles), 'after':H['snapshot'](catalog, ids)}))
    finally:
        provider.close(); backend.close(); assert backend.wait_closed(30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('root', 'mode', 'model-cache'): parser.add_argument('--'+key, required=True)
    main(parser.parse_args())
