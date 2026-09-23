"""Every selected ID remains scorable even when service/setup/warm work fails."""
import json
from pathlib import Path
import subprocess
import sys
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
