"""Build exact preceding Git revision, create its real DB, reopen with R09."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import UUID
import pytest

from agentic_rag.config import ProcessingSnapshot,RunConfiguration
from agentic_rag.ingestion import read_processed
from agentic_rag.storage import Catalog


@pytest.mark.parametrize('revision', ['97224b0414c9b67bc99ed59799fa2f2286ab5460', '9afe7e37982cf63c3e2199403499b289d8a520b1', '81bf1461c73d9d640144eb1ed8b9a193d0f4f953'])
def test_real_preceding_installed_checkpoint_and_run_reopen(tmp_path, revision):
    package=Path(__file__).resolve().parents[1];repo=package.parents[1]
    source=tmp_path/'r08-source';source.mkdir()
    uv=shutil.which('uv');assert uv
    report={'source_head':revision,'commands':[]}
    def command(args,cwd=tmp_path):
        result=subprocess.run([str(a) for a in args],cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
        report['commands'].append({'argv':[str(a) for a in args],'cwd':str(cwd),'exit_code':result.returncode,
                                  'stdout':result.stdout,'stderr':result.stderr})
        assert result.returncode==0,result.stdout+result.stderr
        return result.stdout
    try:
        tracked=command(['git','ls-tree','-r','--name-only',revision,'deployment/AgenticRAG'],repo).splitlines()
        files=[path for path in tracked if path.startswith('deployment/AgenticRAG/src/') or
               path in ['deployment/AgenticRAG/'+name for name in ('pyproject.toml','uv.lock','README.md','LICENSE','.gitignore')]]
        for name in files:
            data=subprocess.check_output(['git','show',revision+':'+name],cwd=repo)
            destination=source/Path(name).relative_to('deployment/AgenticRAG');destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(data)
        report['source_files']={str(p.relative_to(source)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in source.rglob('*') if p.is_file()}
        command([uv,'build',source,'--wheel','--out-dir',tmp_path/'old-wheel','--python',sys.executable,'--no-build-isolation','--no-create-gitignore'])
        wheel,=(tmp_path/'old-wheel').glob('*.whl');report['old_wheel_sha256']=hashlib.sha256(wheel.read_bytes()).hexdigest()
        command([uv,'venv',tmp_path/'old-env','--python',sys.executable])
        python=tmp_path/'old-env'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        command([uv,'pip','install','--python',python,wheel])
        fixture=package/'docs/implementation-records/R09-prechange-snapshot.json'
        cache=Path(os.environ.get('R09_MODEL_CACHE',str(Path.home()/'.cache/codeplus-agenticrag/models')))
        command([python,'-I','-B',package/'tests/worker_history_helper.py',tmp_path,fixture,cache])
        old=json.loads((tmp_path/'old-state.json').read_text(encoding='utf-8'))
        catalog=Catalog(tmp_path/'old-data')
        assert catalog.get_snapshot(UUID(old['snapshot_id'])).model_dump(mode='json')==old['snapshot']
        assert read_processed(catalog,UUID(old['batch_id']),UUID(old['item_id']))[0].model_dump(mode='json')==old['processing']
        assert RunConfiguration.model_validate_json(json.dumps(old['run_json'])).identity==old['run_identity']
        with catalog._db.transaction() as connection:
            assert connection.pragma('user_version') == 11
            assert connection.execute('SELECT count(*) FROM publications').fetchone() == (0,)
            assert connection.execute('SELECT count(*) FROM index_artifacts').fetchone() == (0,)
            assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
            for table in ('source_candidates','delivered_evidence','saved_citations','delivery_receipts'):
                assert connection.execute('SELECT count(*) FROM '+table).fetchone() == (0,)
        report['migration5_preserved_history_without_fabricated_publication_or_evidence'] = True
        report['old_state']=old;report['result']='PASS';report['source_deleted_before_reopen']=True
    finally:
        path=os.environ.get('R11_HISTORY_REPORT') or os.environ.get('R10_HISTORY_REPORT') or os.environ.get('R09_HISTORY_REPORT')
        if path and (os.environ.get('R11_HISTORY_REPORT') or os.environ.get('R10_HISTORY_REPORT')):
            p=Path(path);path=p.with_name(p.stem+'-'+revision[:7]+p.suffix)
        if path:Path(path).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
