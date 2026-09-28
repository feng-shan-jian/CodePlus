"""Every selected ID remains scorable even when service/setup/warm work fails."""
import json
from pathlib import Path
import subprocess
import sys
import runpy
import threading
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('mode,errors',[('service-init',200),('missing-extra',200),('run-init',200),('search-init',200),('question',1),('warm',0),('close',0)])
@pytest.mark.parametrize('route',['dense','bm25','hybrid'])
def test_runner_preserves_all_200_records_and_failure_denominator(tmp_path,mode,errors,route):
    helper=Path(__file__).with_name('runner_fault_helper.py')
    result=subprocess.run([sys.executable,'-I','-B',str(helper),mode,str(tmp_path),route],capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stderr
    report=json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))
    ids=json.loads((helper.parents[1]/'eval/development-ids.json').read_text(encoding='utf-8'))
    assert [r['id'] for r in report['records']]==ids
    assert len(report['records'])==200 and report['errors']==errors
    assert report['protocol']['top_k']==10
    assert report['protocol']['route']==route
    if route=='bm25':assert report['worker_config'] is None
    assert report['dataset_sha256']=='f80fc4033be6625b19da2af9529cf925d147e9ad62c95b943df2c3d08ec2e898'
    assert all(r['hits']==[] and r.get('error') for r in report['records'] if r['status']=='error')
    assert report['forbidden_runtime_reads']==[]
    if mode in ('question','warm','close'):
        finished=json.loads((tmp_path/'finish.json').read_text(encoding='utf-8'))
        assert finished=={'status':'failed' if errors else 'completed','reason':'explicit_error' if errors else 'finished'}
    assert report['result']!='PASS'


def test_finite_owner_rotation_happens_only_after_real_work_finishes(monkeypatch):
    module=runpy.run_path(str(Path(__file__).parents[1]/'eval/dense_runner.py'))
    events=[]
    class Handle:
        execution_finished=False
        def cancel(self):events.append('cancel')
        def wait_finished(self,timeout):events.append('finished');self.execution_finished=True;return True
    handles={i:Handle() for i in range(6)}
    class Client:
        lock=threading.RLock();owner_id='old';metadata={'pid':10,'auth_token':'PRIVATE','token':'PRIVATE'}
        def __init__(self):self.handles=handles
        def status(self):return {'load_count':1}
        def close(self):
            assert all(h.execution_finished for h in self.handles.values());events.append('goodbye')
    worker=SimpleNamespace(max_session_requests=8,startup_timeout_ms=1000)
    config=SimpleNamespace(retrieval=SimpleNamespace(route='hybrid',rerank=True,rerank_candidates=2))
    created=object()
    def create(conf):events.append('new_owner');return created
    function=module['client_at_query_boundary'];monkeypatch.setitem(function.__globals__,'LocalModelClient',create)
    report={};old=Client()
    assert function(old,worker,config,report) is created
    assert events[-2:]==['goodbye','new_owner']
    assert report['retired_worker_sessions'][0]['request_count']==6
    assert 'PRIVATE' not in json.dumps(report)
    old=Client();old.handles={0:Handle()};events.clear()
    assert function(old,worker,config,{}) is old and events==[]


def test_unfinished_owner_is_never_replaced_and_default_candidate_bound_is_preserved(monkeypatch,tmp_path):
    module=runpy.run_path(str(Path(__file__).parents[1]/'eval/dense_runner.py'))
    class Handle:
        execution_finished=False
        def cancel(self):pass
        def wait_finished(self,timeout):return False
    old=SimpleNamespace(lock=threading.RLock(),handles={1:Handle()},close=lambda:pytest.fail('premature close'))
    worker=SimpleNamespace(max_session_requests=2,startup_timeout_ms=1000)
    config=SimpleNamespace(retrieval=SimpleNamespace(route='hybrid',rerank=True,rerank_candidates=1))
    function=module['client_at_query_boundary']
    monkeypatch.setitem(function.__globals__,'LocalModelClient',lambda _:pytest.fail('premature replacement'))
    with pytest.raises(RuntimeError,match='prior work'):function(old,worker,config,{})
    default=module['experiment_config'](tmp_path,'http://127.0.0.1:19556','hybrid',rerank=True)
    current=module['experiment_config'](tmp_path,'http://127.0.0.1:19556','hybrid',rerank=True,rerank_candidates=24)
    assert default.retrieval.rerank_candidates==50 and current.retrieval.rerank_candidates==24
