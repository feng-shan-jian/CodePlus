"""Real abrupt interpreter exit; explicit reopen and owner/epoch recovery."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from uuid import UUID
import pytest

from agentic_rag.ingestion import process_inputs, read_processed
from agentic_rag.storage import Catalog

HELPER=runpy.run_path(str(Path(__file__).with_name('parsing_support.py')))
EVENTS=[]


@pytest.mark.parametrize('mode',['orphan','precommit','committed'])
def test_actual_exit_checkpoint_acceptance_is_all_or_nothing(tmp_path,mode,monkeypatch):
    source=tmp_path/'source.md';source.write_bytes(b'# Header\n\nOriginal evidence.\n')
    catalog=Catalog(tmp_path/'data');kb=catalog.create_library('exit boundary')
    event=tmp_path/'event.json'
    args=[sys.executable,'-I','-B',str(Path(__file__).with_name('processing_process_helper.py')),
          mode,str(catalog._directory.root),str(kb.kb_id),str(source),str(event)]
    run=subprocess.run(args,cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert run.returncode==91,run.stdout+run.stderr
    info=json.loads(event.read_text());assert info['pid']!=os.getpid()
    EVENTS.append({'argv':args,'cwd':str(tmp_path),'exit_code':run.returncode,**info})
    source.unlink()
    reopened=Catalog(catalog._directory.root)
    batch,item=UUID(info['batch_id']),UUID(info['item_id'])
    existing=read_processed(reopened,batch,item)
    assert bool(existing)==(mode=='committed')
    with reopened._db.transaction() as connection:
        assert connection.execute('SELECT count(*) FROM document_versions').fetchone()==((1 if existing else 0),)
        assert connection.execute('PRAGMA integrity_check').fetchone()==('ok',)
    token=reopened.identify_interrupted(kb.kb_id)
    with reopened.resume_mutation(token) as owner:
        assert owner.token.owner_epoch==2
        if existing:
            import agentic_rag.ingestion.processing as module
            def no_reparse(*args,**kwargs):raise AssertionError('completed checkpoint must be reused')
            monkeypatch.setattr(module,'parse_document',no_reparse)
            monkeypatch.setattr(module,'chunk_document',no_reparse)
            import agentic_rag.ingestion.parsing as parser
            monkeypatch.setattr(parser,'parse_document',no_reparse)
        assert process_inputs(reopened,owner,HELPER['tokenizer']())[0].stage=='chunked'
        owner.abandon()
    if os.environ.get('R08_PROCESS_REPORT'):
        Path(os.environ['R08_PROCESS_REPORT']).write_text(json.dumps(EVENTS,indent=2)+'\n',encoding='utf-8')
