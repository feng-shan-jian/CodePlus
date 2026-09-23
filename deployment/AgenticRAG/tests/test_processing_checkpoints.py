"""Real parse/archive/SQL boundary, original source isolation and recovery checks."""
from contextlib import contextmanager
from pathlib import Path
import runpy
from uuid import uuid4

import pytest
from agentic_rag.domain import RagError, ErrorCode
from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, process_inputs, read_processed
from agentic_rag.ingestion import processing
from agentic_rag.storage import Catalog
from agentic_rag.storage import processing as store

HELPER = runpy.run_path(str(Path(__file__).with_name('parsing_support.py')))


@pytest.fixture(scope='module')
def tok(): return HELPER['tokenizer']()


def begin(tmp_path, raw=b'\xef\xbb\xbf# Title\r\n\r\nSame paragraph.\r\n\r\nSame paragraph.\r\n'):
    source=tmp_path/'source.md';source.write_bytes(raw)
    catalog=Catalog(tmp_path/'data');library=catalog.create_library('processing')
    snapshot=HELPER['snapshot']()
    owner=catalog.begin_import(library.kb_id,snapshot,select_inputs((InputSelection(path=str(source)),)))
    item,=capture_inputs(catalog,owner)
    return source,catalog,library,snapshot,owner,item


def test_deleted_source_real_archives_complete_checkpoint_and_reopen(tmp_path,tok):
    source,catalog,kb,snapshot,owner,raw=begin(tmp_path)
    try:
        source.unlink()
        item,=process_inputs(catalog,owner,tok)
        assert item.stage=='chunked'
        assert {a.kind for a in item.output_hashes}=={'raw','parsed','source_map','chunks'}
        read=read_processed(catalog,raw.batch_id,raw.entry.item_id)
        assert read[0]==item and read[1].source_uri==raw.raw.source_uri
        assert catalog.get_input_items(raw.batch_id)==(raw,)
        assert process_inputs(catalog,owner,tok)==(item,)
        assert catalog.get_batch(raw.batch_id).state.value=='PROCESSING'
    finally: owner.close()
    reopened=Catalog(catalog._directory.root)
    assert read_processed(reopened,raw.batch_id,raw.entry.item_id)==read
    assert reopened.get_snapshot(snapshot.snapshot_id)==snapshot
    token=reopened.identify_interrupted(kb.kb_id)
    with reopened.resume_mutation(token) as resumed:
        assert process_inputs(reopened,resumed,tok)==(item,)
        with pytest.raises(RagError,match='late result'):
            store.persist(reopened,resumed,raw,snapshot,version=read[1],result=read[2],produced_by=owner.token)
        resumed.abandon()


@pytest.mark.parametrize('raw',[b'',b' \r\n',b'# Heading only\n',b'\xff',b'abc\0'])
def test_no_body_or_explicit_file_error_persisted(tmp_path,tok,raw):
    source,catalog,kb,snapshot,owner,input_item=begin(tmp_path,raw)
    with owner:
        item,=process_inputs(catalog,owner,tok)
        result=read_processed(catalog,input_item.batch_id,input_item.entry.item_id)
        if raw in (b'\xff',b'abc\0'):
            assert item.stage=='failed' and result[1:] == (None,None)
        else:
            assert item.stage=='chunked' and result[2].inputs==()
        owner.abandon()


def test_sql_failure_rolls_back_version_structure_and_checkpoint(tmp_path,tok):
    _,catalog,_,_,owner,raw=begin(tmp_path)
    with owner:
        with catalog._db.transaction(write=True) as connection:
            connection.execute("CREATE TRIGGER injected_checkpoint_failure BEFORE INSERT ON processing_items BEGIN SELECT RAISE(ABORT,'injected boundary failure'); END")
        with pytest.raises(RagError,match='injected boundary failure'):
            process_inputs(catalog,owner,tok)
        with catalog._db.transaction() as connection:
            for table in ('document_versions','sections','chunks','processing_items'):
                assert connection.execute(f'SELECT count(*) FROM {table}').fetchone()==(0,)
        assert read_processed(catalog,raw.batch_id,raw.entry.item_id) is None
        with catalog._db.transaction(write=True) as connection:
            connection.execute('DROP TRIGGER injected_checkpoint_failure')
        assert process_inputs(catalog,owner,tok)[0].stage=='chunked'
        owner.abandon()


def test_filesystem_and_tokenizer_work_happen_outside_sql_transactions(tmp_path,tok,monkeypatch):
    _,catalog,_,_,owner,raw=begin(tmp_path)
    active=[];transaction=catalog._db.transaction
    @contextmanager
    def tracked(*args,**kwargs):
        with transaction(*args,**kwargs) as connection:
            active.append(True)
            try: yield connection
            finally: active.pop()
    monkeypatch.setattr(catalog._db,'transaction',tracked)
    for object_,name in ((catalog.archives,'put'),(catalog.archives,'read'),(tok,'encode'),(tok,'offsets')):
        original=getattr(object_,name)
        def guarded(*args,_original=original,**kwargs):
            assert not active
            return _original(*args,**kwargs)
        monkeypatch.setattr(object_,name,guarded)
    with owner:
        assert process_inputs(catalog,owner,tok)[0].stage=='chunked'
        assert read_processed(catalog,raw.batch_id,raw.entry.item_id)[2].inputs
        owner.abandon()


@pytest.mark.parametrize('kind',['raw','parsed','source_map','chunks'])
def test_checkpoint_hash_damage_is_explicit_no_source_repair(tmp_path,tok,kind):
    source,catalog,_,_,owner,raw=begin(tmp_path)
    with owner:
        item,=process_inputs(catalog,owner,tok)
        source.write_bytes(b'latest content must not repair')
        digest=next(a.sha256 for a in item.output_hashes if a.kind==kind)
        catalog.archives._path(digest).write_bytes(b'corrupt')
        with pytest.raises(RagError) as exc:
            read_processed(catalog,raw.batch_id,raw.entry.item_id)
        assert exc.value.error.code==ErrorCode.CHECKPOINT_INVALID
        with pytest.raises(RagError): process_inputs(catalog,owner,tok)
        owner.abandon()


@pytest.mark.parametrize('damage',['version','map','chunker','snapshot'])
def test_inconsistent_produced_result_cannot_be_registered(tmp_path,tok,monkeypatch,damage):
    _,catalog,_,_,owner,raw=begin(tmp_path)
    original=store.persist
    def corrupt(catalog_,owner_,raw_,snapshot_,**kwargs):
        if damage=='version': kwargs['version']=kwargs['version'].model_copy(update={'document_version_id':uuid4()})
        if damage=='map':
            result=kwargs['result'];parsed=result.parsed
            kwargs['result']=result.model_copy(update={'parsed':parsed.model_copy(update={'text':parsed.text+'x'})})
        if damage=='chunker':
            result=kwargs['result'];item=result.inputs[0]
            item=item.model_copy(update={'chunk':item.chunk.model_copy(update={'chunker_fingerprint':'f'*64})})
            kwargs['result']=result.model_copy(update={'inputs':(item,)+result.inputs[1:]})
        if damage=='snapshot': snapshot_=HELPER['snapshot'](128,0).model_copy(update={'snapshot_id':snapshot_.snapshot_id})
        return original(catalog_,owner_,raw_,snapshot_,**kwargs)
    monkeypatch.setattr(store,'persist',corrupt)
    with owner:
        with pytest.raises((ValueError,RagError)):
            process_inputs(catalog,owner,tok)
        with catalog._db.transaction() as connection:
            assert connection.execute('SELECT count(*) FROM document_versions').fetchone()==(0,)
        owner.abandon()


@pytest.mark.parametrize('damage',[False,True])
def test_actual_schema2_to3_upgrade_preserves_authenticated_history(tmp_path,damage):
    from datetime import datetime, timezone
    import hashlib
    from importlib.resources import files
    import apsw
    fixture=runpy.run_path(str(Path(__file__).with_name('test_input_migration.py')))['schema1_fixture']
    path=tmp_path/'old-schema2';old=fixture(path)
    migration=files('agentic_rag.storage').joinpath('inputs.sql').read_text(encoding='utf-8')
    digest=hashlib.sha256(migration.encode()).hexdigest()
    connection=apsw.Connection(str(path/'catalog.sqlite'))
    with connection:
        connection.execute(migration)
        connection.execute('INSERT INTO schema_migrations VALUES(?,?,?)',(2,'f'*64 if damage else digest,datetime.now(timezone.utc).isoformat()))
        connection.pragma('user_version',2)
    connection.close()
    if damage:
        with pytest.raises(RagError,match='migration fingerprint'):Catalog(path)
        connection=apsw.Connection(str(path/'catalog.sqlite'))
        assert connection.pragma('user_version')==2
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='processing_items'").fetchone() is None
        connection.close()
    else:
        catalog=Catalog(path)
        assert catalog.get_version(old['version']).raw_hash==old['raw']
        assert catalog.archives.read(old['raw'])==b'real archived original in schema1'
        with catalog._db.transaction() as connection:
            assert connection.pragma('user_version')==9
            assert connection.execute('SELECT sha256 FROM schema_migrations WHERE version=2').fetchone()==(digest,)
            assert connection.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_schema2_upgrade_retains_actual_r07_captured_input_and_archive(tmp_path):
    import apsw
    from agentic_rag.ingestion import read_input
    source,catalog,kb,snapshot,owner,raw=begin(tmp_path)
    owner.close()
    manifest=catalog.get_input_manifest(raw.batch_id)
    original=read_input(catalog,raw.batch_id,raw.entry.item_id)
    source.unlink()
    # Raw capture above is the actual R07 path. There are zero processed versions.
    # Remove only empty v3-only objects to construct the exact shipped v2 schema;
    # no raw row/archive or original migration resource/hash is synthesized.
    connection=apsw.Connection(str(catalog._directory.root/'catalog.sqlite'))
    with connection:
        for table in ('maintenance_cursors','index_gc_attempts','index_gc_claims','index_readers','run_lifetimes'):
            connection.execute('DROP TABLE '+table)
        connection.execute('DELETE FROM schema_migrations WHERE version=9')
        for table in ('mutation_completions','mutation_item_results','ordinary_mutations'):
            assert connection.execute('SELECT count(*) FROM '+table).fetchone()==(0,)
            connection.execute('DROP TABLE '+table)
        for name in ('candidate_cleanup_attempts','mutation_abandonments','recovery_plans','mutation_io','mutation_executions',
                     'artifact_ownership_proofs','artifact_creation_intents','current_candidates'):
            connection.execute('DROP TABLE '+name)
        connection.execute('DELETE FROM schema_migrations WHERE version=8')
        connection.execute('DELETE FROM schema_migrations WHERE version=7')
        for table in ('host_tool_calls','model_requests','host_runs'):
            assert connection.execute('SELECT count(*) FROM '+table).fetchone()==(0,)
            connection.execute('DROP TABLE '+table)
        connection.execute('DELETE FROM schema_migrations WHERE version=6')
        for table in ('source_handles','source_usage','source_calls','source_candidates','delivery_receipts',
                      'delivered_evidence','evidence_windows','saved_citations'):
            assert connection.execute('SELECT count(*) FROM '+table).fetchone()==(0,)
            connection.execute('DROP TABLE '+table)
        connection.execute('DELETE FROM schema_migrations WHERE version=5')
        for trigger in ('immutable_publication_update','immutable_publication_delete','immutable_artifact_identity','sealed_artifact_content'):
            connection.execute(f'DROP TRIGGER {trigger}')
        connection.execute('DROP TABLE publications')
        connection.execute('DROP TABLE index_artifacts')
        connection.execute('DELETE FROM schema_migrations WHERE version=4')
        assert connection.execute('SELECT count(*) FROM processing_items').fetchone()==(0,)
        for trigger in ('immutable_processing_items_update','immutable_processing_items_delete',
                        'completed_sections_insert','completed_chunks_insert'):
            connection.execute(f'DROP TRIGGER {trigger}')
        connection.execute('DROP TABLE processing_items')
        connection.execute('DELETE FROM schema_migrations WHERE version=3')
        connection.pragma('user_version',2)
    connection.close()
    upgraded=Catalog(catalog._directory.root)
    assert upgraded.get_input_items(raw.batch_id)==(raw,)
    assert upgraded.get_input_manifest(raw.batch_id)==manifest
    assert upgraded.get_snapshot(snapshot.snapshot_id)==snapshot
    assert read_input(upgraded,raw.batch_id,raw.entry.item_id)==original
    with upgraded._db.transaction() as connection:
        assert connection.pragma('user_version')==9
        assert connection.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert connection.execute('PRAGMA foreign_key_check').fetchall()==[]
