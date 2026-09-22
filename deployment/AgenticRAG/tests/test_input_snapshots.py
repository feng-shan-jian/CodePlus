"""Failures are per-file; interrupted inputs never silently refresh from source."""

import errno
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from uuid import UUID

import pytest

from agentic_rag.domain import ErrorCode, RagError
from agentic_rag.ingestion import InputSelection, capture_inputs, read_input, select_inputs, verify_inputs
from agentic_rag.ingestion.source import VerifiedReader
from agentic_rag.storage import Catalog

SUPPORT = runpy.run_path(str(Path(__file__).with_name('test_ingestion_inputs.py')))
HARNESS = runpy.run_path(str(Path(__file__).with_name('test_storage_processes.py')))
EVENTS = HARNESS['EVENTS']


@pytest.fixture(autouse=True)
def report_processes():
    yield
    if target := os.environ.get('R07_PROCESS_REPORT'):
        Path(target).write_text(json.dumps({'python':sys.executable,'platform':sys.platform,'events':EVENTS},indent=2)+'\n',encoding='utf-8')


class Child(HARNESS['Child']):
    def __init__(self, tmp_path, mode, catalog, kb_id, source):
        from uuid import uuid4
        suffix = str(uuid4())
        self.event, self.control = tmp_path/f'input-event-{suffix}.json', tmp_path/f'input-control-{suffix}'
        self.args = [sys.executable,'-I','-B',str(Path(__file__).with_name('ingestion_process_helper.py')),
                     mode,str(catalog._directory.root),str(kb_id),str(source),str(self.event),str(self.control)]
        self.process = subprocess.Popen(self.args,cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        self.actual_pid = None
        EVENTS.append({'event':'spawn','launcher_pid':self.process.pid,'argv':self.args,'cwd':str(tmp_path)})


def test_archive_only_after_real_change_delete_move_and_reopen(tmp_path):
    catalog, kb, snap = SUPPORT['new_catalog'](tmp_path)
    paths = [tmp_path/f'{name}.txt' for name in ('changed','deleted','moved')]
    for path in paths:
        path.write_bytes(path.name.encode())
    items = SUPPORT['ingest'](catalog,kb,snap,*paths)
    paths[0].write_bytes(b'changed later'); paths[1].unlink(); paths[2].rename(tmp_path/'renamed.txt')
    reopened = Catalog(catalog._directory.root)
    frozen, checked = verify_inputs(reopened,items[0].batch_id)
    assert frozen == snap and checked == items
    for item,path in zip(items,paths):
        assert read_input(reopened,item.batch_id,item.entry.item_id) == path.name.encode()


@pytest.mark.parametrize('failure', ['read','short','second_read_changed','full','fsync','rename','permission','cancel'])
def test_injected_io_change_incomplete_capacity_and_cancel_have_file_errors(tmp_path,monkeypatch,failure):
    import agentic_rag.ingestion.capture as capture
    import agentic_rag.storage.archives as archives
    catalog,kb,snap=SUPPORT['new_catalog'](tmp_path)
    path=tmp_path/'doc.txt'; path.write_bytes(b'abcdefgh')
    manifest=select_inputs((InputSelection(path=str(path)),))
    original_read=VerifiedReader.read
    if failure=='read':
        def bad_read(*_): raise OSError(errno.EIO,'injected source read error')
        monkeypatch.setattr(VerifiedReader,'read',bad_read)
    elif failure in ('short','second_read_changed'):
        from contextlib import contextmanager
        original_open=capture.open_source
        class Faulty:
            def __init__(self,stream): self.stream,self.second,self.used=stream,False,False
            def fileno(self): return self.stream.fileno()
            def seek(self,*a): self.second=True; return self.stream.seek(*a)
            def read(self,size):
                if failure=='short':
                    if self.used: return b''
                    self.used=True; return self.stream.read(4)
                value=self.stream.read(size)
                return b'XXXXXXXX' if self.second and value else value
        @contextmanager
        def faulty(path):
            with original_open(path) as stream: yield Faulty(stream)
        monkeypatch.setattr(capture,'open_source',faulty)
    elif failure=='full':
        def full(*a,**kw): raise OSError(errno.ENOSPC,'injected disk full, not real exhaustion')
        monkeypatch.setattr(catalog.archives,'put',full)
    elif failure=='fsync':
        def no_sync(*a): raise OSError(errno.EIO,'injected fsync failure')
        monkeypatch.setattr(archives.os,'fsync',no_sync)
    elif failure=='rename':
        def no_finish(*a): raise OSError(errno.EIO,'injected same-volume completion failure')
        monkeypatch.setattr(archives,'_complete',no_finish)
    elif failure=='permission':
        def no_open(*a): raise PermissionError('injected source permission denial')
        monkeypatch.setattr(capture,'open_source',no_open)
    with catalog.begin_import(kb.kb_id,snap,manifest) as owner:
        item,=capture_inputs(catalog,owner,cancelled=(lambda:True) if failure=='cancel' else None)
        assert item.stage=='failed' and item.raw is None and item.error is not None
        if failure in ('short','second_read_changed'):
            assert item.error.code==ErrorCode.SOURCE_CHANGED
        if failure=='cancel': assert item.error.code==ErrorCode.CANCELLED
        with catalog._db.transaction() as connection:
            assert connection.execute('SELECT count(*) FROM archive_objects').fetchone()==(0,)
        owner.abandon()
    assert list(catalog._directory.path('staging').iterdir())==[]


def test_real_metadata_change_with_restored_mtime_is_detected(tmp_path,monkeypatch):
    import agentic_rag.ingestion.source as source
    catalog,kb,snap=SUPPORT['new_catalog'](tmp_path)
    path=tmp_path/'changed.txt';path.write_bytes(b'data')
    manifest=select_inputs((InputSelection(path=str(path)),))
    original=source.file_stamp
    # Real metadata writes are allowed by FILE_SHARE_READ; ChangeTime must
    # detect them even when the public LastWriteTime is restored.
    called=False
    def stamp(stream):
        nonlocal called
        if not called:
            called=True
            previous=path.stat()
            os.utime(path,ns=(previous.st_atime_ns,previous.st_mtime_ns-10000000))
            os.utime(path,ns=(previous.st_atime_ns,previous.st_mtime_ns))
        return original(stream)
    monkeypatch.setattr(source,'file_stamp',stamp)
    with catalog.begin_import(kb.kb_id,snap,manifest) as owner:
        item,=capture_inputs(catalog,owner)
        assert item.stage=='failed' and item.error.code==ErrorCode.SOURCE_CHANGED
        owner.abandon()


@pytest.mark.parametrize('damage',['missing','corrupt','permission'])
def test_reopen_verification_returns_file_errors_without_source_repair(tmp_path,monkeypatch,damage):
    catalog,kb,snap=SUPPORT['new_catalog'](tmp_path)
    path=tmp_path/'good.txt';path.write_bytes(b'known original')
    item,=SUPPORT['ingest'](catalog,kb,snap,path)
    archive=catalog.archives._path(item.raw.sha256)
    if damage=='missing': archive.unlink()
    elif damage=='corrupt': archive.write_bytes(b'bad')
    else:
        original_open=Path.open
        def denied(target,*args,**kwargs):
            if target==archive: raise PermissionError('injected archive read denial')
            return original_open(target,*args,**kwargs)
        monkeypatch.setattr(Path,'open',denied)
    path.write_bytes(b'latest source cannot repair old checkpoint')
    frozen, checked=verify_inputs(catalog,item.batch_id)
    assert frozen==snap and checked[0].error.code==ErrorCode.CHECKPOINT_INVALID
    with pytest.raises(RagError,match='checkpoint'):
        read_input(catalog,item.batch_id,item.entry.item_id)
    assert catalog.get_input_items(item.batch_id)[0]==item  # immutable historical result


@pytest.mark.parametrize('mode',['partial','orphan'])
def test_actual_process_death_preserves_completed_only_and_never_recaptures(tmp_path,mode):
    catalog,kb,snap=SUPPORT['new_catalog'](tmp_path)
    sources=tmp_path/'sources';sources.mkdir()
    (sources/'a.txt').write_bytes(b'first original')
    (sources/'b.txt').write_bytes(b'second original'*100000)
    (sources/'c.txt').write_bytes(b'third original')
    child=Child(tmp_path,mode,catalog,kb.kb_id,sources)
    try:
        event=child.wait_event()
        batch_id=UUID(event['batch_id'])
        items=catalog.get_input_items(batch_id)
        assert [i.stage for i in items]==['captured','pending','pending']
        assert len(event['complete'])==1
        if mode=='partial':
            assert event['staging_files']
            probe=Child(tmp_path,'probe',catalog,kb.kb_id,sources/'b.txt')
            try:
                blocked=probe.wait_event()
                if os.name=='nt':
                    assert all(result['type']=='PermissionError' and result['errno']==13 for result in blocked['results'].values())
                    assert blocked['results']['rename']['winerror']==blocked['results']['delete']['winerror']==32
            finally:
                probe.finish()
        else:
            assert catalog.archives.verify(event['orphan_hash']).size_bytes>0
        # IO wait in another process must not hold a long metadata transaction.
        other=catalog.create_library('progress while source handle is held')
        with catalog.begin_mutation(other.kb_id,SUPPORT['HELPER']['snapshot'](),'a'*64) as owner:
            owner.abandon()
    finally:
        child.finish(kill=True)
    (sources/'a.txt').unlink()
    (sources/'b.txt').write_bytes(b'new second'); (sources/'c.txt').rename(sources/'moved.txt')
    (sources/'added.txt').write_bytes(b'new input')
    reopened=Catalog(catalog._directory.root)
    frozen,checked=verify_inputs(reopened,batch_id)
    assert checked[0].stage=='captured' and all(i.error.code==ErrorCode.CHECKPOINT_INVALID for i in checked[1:])
    assert read_input(reopened,batch_id,items[0].entry.item_id)==b'first original'
    assert frozen==reopened.get_snapshot(reopened.get_batch(batch_id).processing_snapshot_id)
    expected=reopened.identify_interrupted(kb.kb_id)
    with reopened.resume_mutation(expected) as owner:
        final=capture_inputs(reopened,owner)
        assert [i.stage for i in final]==['captured','failed','failed']
        assert all('original snapshot incomplete' in i.error.message for i in final[1:])
        owner.abandon()
    EVENTS.append({'event':'capture_restart_verified','batch_id':str(batch_id),'mode':mode,
                   'states':[i.stage for i in final],'input_hash':reopened.get_batch(batch_id).input_manifest_hash,
                   'config_hash':frozen.config_fingerprint,'first_raw_hash':items[0].raw.sha256})


@pytest.mark.parametrize('mode',['writer','mapping'])
def test_real_existing_write_handle_or_mapping_blocks_capture(tmp_path,mode):
    catalog,kb,snap=SUPPORT['new_catalog'](tmp_path)
    path=tmp_path/'busy.txt';path.write_bytes(b'original')
    manifest=select_inputs((InputSelection(path=str(path)),))
    child=Child(tmp_path,mode,catalog,kb.kb_id,path)
    try:
        child.wait_event()
        with catalog.begin_import(kb.kb_id,snap,manifest) as owner:
            item,=capture_inputs(catalog,owner)
            if os.name=='nt':
                assert item.stage=='failed' and item.error.code==ErrorCode.SOURCE_CHANGED
            owner.abandon()
    finally:
        child.finish()


def test_terminal_checkpoint_immutable_stale_owner_and_import_order(tmp_path):
    from agentic_rag.storage.inputs import record_result
    catalog,kb,snap=SUPPORT['new_catalog'](tmp_path)
    path=tmp_path/'done.txt';path.write_bytes(b'done')
    manifest=select_inputs((InputSelection(path=str(path)),))
    with catalog.begin_import(kb.kb_id,snap,manifest) as owner:
        item,=capture_inputs(catalog,owner)
        with pytest.raises(RagError): record_result(catalog,owner,item,produced_by=owner.token)
        for table in ('input_results','input_items','input_manifests','document_sources'):
            with pytest.raises(RagError,match='historical'):
                with catalog._db.transaction(write=True) as connection: connection.execute(f'DELETE FROM {table}')
        token=owner.token
    with catalog.resume_mutation(catalog.identify_interrupted(kb.kb_id)) as resumed:
        with pytest.raises(RagError,match='late result'):
            record_result(catalog,resumed,item,produced_by=token)
        resumed.abandon()
    code="from agentic_rag.storage import Catalog; from uuid import UUID; import sys; c=Catalog(sys.argv[1]); i=c.get_input_items(UUID(sys.argv[2])); assert len(i)==1 and i[0].stage=='captured'; assert c.get_input_manifest(UUID(sys.argv[2])).identity==c.get_batch(UUID(sys.argv[2])).input_manifest_hash; print('storage-first reopen PASS')"
    command=[sys.executable,'-I','-B','-c',code,str(catalog._directory.root),str(item.batch_id)]
    completed=subprocess.run(command,cwd=tmp_path,capture_output=True,text=True,timeout=15)
    EVENTS.append({'event':'fresh_storage_first_read','argv':command,'cwd':str(tmp_path),'exit_code':completed.returncode,
                   'stdout':completed.stdout,'stderr':completed.stderr})
    assert completed.returncode==0,completed.stderr
