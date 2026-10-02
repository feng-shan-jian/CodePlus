"""Document-level writes, revision isolation, and version reclamation.

RAG_INCREMENTAL_MILVUS selects an owned live endpoint. Embedding vectors are
synthetic in both modes; these are lifecycle tests, not semantic-quality scores.
"""

import json
import os
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import UUID

import pytest

from agentic_rag.config import resolve_run
from agentic_rag.domain import RagError, RunStatus
from agentic_rag.indexes.manifest import SCALAR_FIELDS
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.storage import Catalog, publication, gc, index_versions

H = runpy.run_path(str(Path(__file__).with_name('test_mutations.py')))


@pytest.fixture(params=['controlled','milvus'])
def indexed(tmp_path, request):
    endpoint = os.environ.get('RAG_INCREMENTAL_MILVUS')
    if request.param=='milvus' and not endpoint:
        pytest.skip('set RAG_INCREMENTAL_MILVUS to an owned live Milvus endpoint')
    catalog = Catalog(tmp_path/'data')
    catalog._maintain_indexes = lambda: []  # GC assertions use the real explicit coordinator.
    config = H['HELPER']['configuration'](tmp_path/'data', endpoint=endpoint or 'http://127.0.0.1:19532')
    s = SimpleNamespace(root=tmp_path,catalog=catalog,config=config,
        kb=catalog.create_library('incremental acceptance').kb_id,
        model=H['Model'](),tokenizer=H['HELPER']['HELPER']['tokenizer'](),physical={})
    if request.param=='milvus':
        s.backend = MilvusRevisionIndex(config.storage, catalog)
    else:
        s.backend = H['backend'](catalog, config)
        backend, client = s.backend, s.backend.client
        original_create = backend.create
        def create(artifact, owner):
            original_create(artifact, owner)
            physical = index_versions.physical(catalog, artifact)
            if physical['artifact_id']==artifact['artifact_id']:
                with catalog._owned(owner) as db:
                    marker = db.execute('SELECT marker FROM artifact_creation_intents WHERE artifact_id=?',
                                        (artifact['artifact_id'],)).fetchone()[0]
                s.physical[backend._name(artifact)] = dict(collection_id=artifact['artifact_id'],created_timestamp='123',description=marker)
            else:
                MilvusRevisionIndex.create(backend, artifact, owner)
        backend.create = create
        def search(name, **kw):
            versions = json.loads(kw['filter'].split(' in ',1)[1]) if kw.get('filter') else None
            rows = [r for r in backend.collections[name] if versions is None or r['document_version_id'] in versions]
            return [[{'chunk_id':r['chunk_id'],'distance':1.0,'entity':{k:r[k] for k in SCALAR_FIELDS}}
                     for r in rows[:kw['limit']]]]
        client.search = search
        # Exercise the actual pre-filter and producer-to-logical-row projection.
        del backend.search
        client.has_collection = lambda name,**kw: name in backend.collections
        client.describe_collection = lambda name,**kw: s.physical[name]
        client.close = lambda: None
        def delete(name, **kw):
            versions = json.loads(kw['filter'].split(' in ',1)[1])
            backend.collections[name][:] = [r for r in backend.collections[name] if r['document_version_id'] not in versions]
        client.delete = delete
        client.drop_collection = lambda name,**kw: backend.collections.pop(name)
    yield s
    for lease in tuple(catalog._db._run_leases.values()):
        if lease.run.status == RunStatus.RUNNING:
            lease.finish(RunStatus.CANCELLED,'test_complete')
    with catalog._db.transaction() as db:
        names = [row[0] for row in db.execute('SELECT DISTINCT a.collection_name FROM artifact_collections c '
                    'JOIN index_artifacts a ON a.artifact_id=c.collection_artifact_id')]
    for name in names:
        if s.backend.client.has_collection(name):
            s.backend.client.drop_collection(name)
    s.backend.close()
    s.backend.wait_closed(20)


def search_ids(s, artifact, branch):
    value = H['VECTOR'] if branch=='dense' else 'telescope approval'
    return {row['entity']['document_version_id'] for row in s.backend.search(artifact,value,field=branch,limit=20)}


def test_update_is_invisible_until_publish_and_old_run_delays_delete(indexed):
    s = indexed
    a = H['source'](s,'a.md','# A\nOld telescope approval.\n')
    b = H['source'](s,'b.md','# B\nUnchanged telescope approval.\n')
    with H['begin'](s,(a,b)) as owner:
        initial = H['run'](s,owner)
    old = publication.artifact(s.catalog,UUID(initial['receipt']['revision_id']))
    old_members = set(H['members'](s).values())
    lease = s.catalog.start_run(s.kb,resolve_run(s.config,'qa'))
    a.write_text('# A\nUpdated telescope approval.\n',encoding='utf-8')
    stages = []
    def observe(stage, value):
        if stage in ('inserted','validated'):
            stages.append(stage)
            assert s.catalog.get_library(s.kb).current_revision_id==UUID(old['revision_id'])
            for branch in ('dense','sparse'):
                assert search_ids(s,old,branch)==old_members
    with H['begin'](s,(a,)) as owner:
        changed = H['run'](s,owner,observer=observe)
    current = publication.artifact(s.catalog,UUID(changed['receipt']['revision_id']))
    current_members = set(H['members'](s).values())
    assert stages==['inserted','validated']
    assert changed['metrics']['incremental_index'] and changed['metrics']['written_vectors']==1
    assert changed['metrics']['reused_vectors']==1
    assert s.backend._name(old)==s.backend._name(current)
    for branch in ('dense','sparse'):
        assert search_ids(s,current,branch)==current_members
        assert search_ids(s,old,branch)==old_members
    assert gc.collect(s.catalog,s.backend,old)['state']=='retained'
    lease.finish(RunStatus.COMPLETED,'finished')
    assert gc.collect(s.catalog,s.backend,old)['state']=='reclaimed'
    assert s.backend.client.has_collection(s.backend._name(current))
    for branch in ('dense','sparse'):
        assert search_ids(s,current,branch)==current_members
    # The obsolete version is physically deleted, not merely hidden in search.
    iterator=s.backend.client.query_iterator(s.backend._name(current),filter='',output_fields=['document_version_id'],batch_size=256,consistency_level='Strong')
    try:
        remaining=set()
        while rows:=iterator.next():
            remaining.update(r['document_version_id'] for r in rows)
    finally:
        iterator.close()
    assert remaining==current_members
    assert s.catalog.get_version(UUID(next(iter(old_members-current_members)))).raw_hash


def test_failed_staged_update_and_last_document_delete(indexed):
    s=indexed
    path=H['source'](s,'a.md','# A\nOld telescope approval.\n')
    with H['begin'](s,(path,)) as owner:
        initial=H['run'](s,owner)
    old=publication.artifact(s.catalog,UUID(initial['receipt']['revision_id']))
    old_members=set(H['members'](s).values())
    path.write_text('# A\nUnpublished telescope approval.\n',encoding='utf-8')
    def interrupt(stage,value):
        if stage=='inserted':
            raise RuntimeError('injected stop before publication')
    owner=H['begin'](s,(path,))
    try:
        with pytest.raises(RuntimeError,match='before publication'):
            H['run'](s,owner,observer=interrupt)
        with s.catalog._db.transaction() as db:
            rid=db.execute('SELECT revision_id FROM index_artifacts WHERE batch_id=?',(str(owner.token.batch_id),)).fetchone()[0]
        failed=publication.artifact(s.catalog,UUID(rid))
        for branch in ('dense','sparse'):
            assert search_ids(s,old,branch)==old_members
        owner.abandon()
    finally:
        owner.close()
    assert gc.collect(s.catalog,s.backend,failed)['state']=='reclaimed'
    assert s.catalog.get_library(s.kb).current_revision_id==UUID(old['revision_id'])
    identity=UUID(next(iter(H['members'](s))))
    with H['begin'](s,delete_document_ids=(identity,)) as owner:
        empty=H['run'](s,owner)
    assert empty['metrics']['written_vectors']==0
    assert empty['validation']['empty_revision']
    current=publication.artifact(s.catalog,UUID(empty['receipt']['revision_id']))
    for branch in ('dense','sparse'):
        assert search_ids(s,current,branch)==set()


def test_recovery_reuses_document_checkpoint_without_duplicate_rows(indexed):
    from agentic_rag.ingestion import continue_recovery
    s=indexed
    path=H['source'](s,'recover.md','# Recovery\nOriginal telescope approval.\n')
    with H['begin'](s,(path,)) as owner:
        H['run'](s,owner)
    path.write_text('# Recovery\nUpdated telescope approval.\n',encoding='utf-8')
    def stop(stage,value):
        if stage=='inserted':
            raise RuntimeError('response arrived; stop before publish')
    with H['begin'](s,(path,)) as owner:
        with pytest.raises(RuntimeError):
            H['run'](s,owner,observer=stop)
    calls=len(s.model.calls)
    expected=s.catalog.identify_interrupted(s.kb)
    resumed=continue_recovery(s.catalog,expected,runtime_factory=lambda _:(s.tokenizer,s.model,s.backend))
    assert len(s.model.calls)==calls
    current=publication.artifact(s.catalog,UUID(resumed['receipt']['revision_id']))
    members=set(H['members'](s).values())
    for branch in ('dense','sparse'):
        assert search_ids(s,current,branch)==members
    assert resumed['validation']['rows_checked']==1
    with s.catalog._db.transaction() as db:
        candidates=[UUID(r[0]) for r in db.execute('SELECT revision_id FROM index_artifacts WHERE batch_id=?',
                                                 (str(expected.batch_id),))]
    assert len(candidates)==2
    obsolete=next(r for r in candidates if str(r)!=current['revision_id'])
    assert gc.collect(s.catalog,s.backend,publication.artifact(s.catalog,obsolete))['state']=='reclaimed'
    assert search_ids(s,current,'dense')==members


def test_incremental_upsert_cannot_overwrite_published_chunk(indexed):
    s=indexed
    a=H['source'](s,'stable.md','# Stable\nPublished telescope approval.\n')
    b=H['source'](s,'new.md','# New\nNew telescope approval.\n')
    with H['begin'](s,(a,)) as owner:
        initial=H['run'](s,owner)
    old=publication.artifact(s.catalog,UUID(initial['receipt']['revision_id']))
    iterator=s.backend.client.query_iterator(s.backend._name(old),filter='',
        output_fields=list(SCALAR_FIELDS)+['dense'],batch_size=256,consistency_level='Strong')
    try:
        original=iterator.next()[0]
    finally:
        iterator.close()
    checked=[]
    with H['begin'](s,(b,)) as owner:
        def observe(stage,value):
            if stage!='created':
                return
            candidate=publication.artifact(s.catalog,UUID(value['revision_id']))
            inherited={**original,'revision_id':candidate['revision_id']}
            new_version=next(v for v in index_versions.origins(s.catalog,candidate)
                             if v!=original['document_version_id'])
            for row in (inherited,{**inherited,'document_version_id':new_version}):
                with pytest.raises(RagError,match='new document versions and their own chunk IDs'):
                    MilvusRevisionIndex.insert(s.backend,candidate,[row],owner)
                checked.append(True)
        H['run'](s,owner,observer=observe)
    assert len(checked)==2


def test_schema11_existing_collection_is_enrolled_without_copying(indexed):
    import apsw
    s=indexed
    a=H['source'](s,'legacy.md','# Legacy\nOriginal telescope approval.\n')
    b=H['source'](s,'stable.md','# Stable\nUnchanged telescope approval.\n')
    with H['begin'](s,(a,b)) as owner:
        initial=H['run'](s,owner)
    old=publication.artifact(s.catalog,UUID(initial['receipt']['revision_id']))
    # These additive tables did not exist in v11; all published artifacts,
    # original archives, ownership receipts and actual Milvus rows are preserved.
    db=apsw.Connection(str(s.catalog._directory.root/'catalog.sqlite'))
    try:
        with db:
            for table in ('watched_sources','indexed_document_versions','artifact_collections'):
                db.execute('DROP TABLE '+table)
            db.execute('DELETE FROM schema_migrations WHERE version=12')
            db.pragma('user_version',11)
    finally:
        db.close()
    upgraded=Catalog(s.catalog._directory.root)
    upgraded._maintain_indexes=lambda: []
    assert upgraded.get_library(s.kb).current_revision_id==UUID(old['revision_id'])
    a.write_text('# Legacy\nNew telescope approval.\n',encoding='utf-8')
    with H['begin'](s,(a,)) as owner:
        result=H['run'](s,owner)
    current=publication.artifact(s.catalog,UUID(result['receipt']['revision_id']))
    assert s.backend._name(current)==old['collection_name']
    assert result['metrics']['written_vectors']==1 and result['metrics']['reused_vectors']==1
    with s.catalog._db.transaction() as db:
        assert db.pragma('user_version')==12
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_legacy_collection_without_ownership_is_copied(indexed):
    s = indexed
    a = H['source'](s, 'legacy.md', '# Legacy\nOriginal telescope approval.\n')
    b = H['source'](s, 'stable.md', '# Stable\nUnchanged telescope approval.\n')
    with H['begin'](s, (a, b)) as owner:
        initial = H['run'](s, owner)
    old = publication.artifact(s.catalog, UUID(initial['receipt']['revision_id']))
    # Before ownership receipts existed, published collections were readable
    # but could not be modified or reclaimed by this installation.
    with s.catalog._db.transaction(write=True) as db:
        trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='immutable_ownership_proof_delete'").fetchone()[0]
        db.execute('DROP TRIGGER immutable_ownership_proof_delete')
        db.execute('DELETE FROM artifact_ownership_proofs WHERE artifact_id=?', (old['artifact_id'],))
        db.execute(trigger)
    original = search_ids(s, old, 'dense')
    a.write_text('# Legacy\nUpdated telescope approval.\n', encoding='utf-8')
    with H['begin'](s, (a,)) as owner:
        result = H['run'](s, owner)
    current = publication.artifact(s.catalog, UUID(result['receipt']['revision_id']))
    assert s.backend._name(current) != s.backend._name(old)
    assert not result['metrics']['incremental_index']
    assert result['metrics']['written_vectors'] == 2
    assert result['metrics']['reused_vectors'] == 1
    assert search_ids(s, old, 'dense') == original
    assert search_ids(s, current, 'dense') == set(H['members'](s).values())
