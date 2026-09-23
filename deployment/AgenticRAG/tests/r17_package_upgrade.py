"""Upgrade real data written by the authentic R16 wheel; no edited schema seed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.models.identity import process_birth


def main(args):
    root=Path(args.root).resolve();root.mkdir(parents=True)
    report={'status':'RUNNING','commands':[],'started':time.time()}
    def save():Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def command(argv):
        argv=list(map(str,argv))
        done=subprocess.run(argv,cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',
            env={**{k:v for k,v in os.environ.items() if k!='PYTHONPATH'},'UV_CACHE_DIR':str(root/'uv-cache')})
        report['commands'].append({'argv':argv,'exit_code':done.returncode,'stdout':done.stdout,'stderr':done.stderr})
        save();done.check_returncode();return done.stdout
    try:
        assert hashlib.sha256(Path(args.old_wheel).read_bytes()).hexdigest()=='5638a14be4020909e819ddf23004f0622cc3af15e314f698bdd63f93a8d4b0d5'
        shutil.copytree(Path(sys.executable).parents[1],root/'env',symlinks=False,ignore=shutil.ignore_patterns('__pycache__'))
        py=root/'env/Scripts/python.exe'
        for target in (py,args.cuda_python):command(['uv','pip','install','--python',target,'--no-deps','--reinstall',args.old_wheel])
        helper=Path(__file__).with_name('r17_upgrade_process.py')
        support=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
        config=support['configuration'](root/'data',args.endpoint)
        worker=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,runtime_dir=str(root/'worker'),idle_timeout_ms=1000)
        (root/'settings.json').write_text(json.dumps({'config':config.model_dump(mode='json'),'worker':worker.model_dump(mode='json')}))
        def child(mode):return json.loads(command([py,'-I','-B',helper,'--root',root,'--mode',mode,'--model-cache',args.model_cache]))
        before=child('seed');report['before']=before;assert before['schema']==10
        old=before['seed_worker'];end=time.monotonic()+30
        while process_birth(old['pid'])==old['process_birth']:
            if time.monotonic()>end:raise TimeoutError('schema10 worker did not exit normally')
            time.sleep(.1)
        shutil.copytree(root/'data',root/'rollback-data')
        for target in (py,args.cuda_python):command(['uv','pip','install','--python',target,'--no-deps','--reinstall',args.wheel])
        fault=child('fault');report['rollback']=fault
        assert fault['schema']==10 and fault['row_hashes']==before['row_hashes'] and fault['migrations']==before['migrations']
        after=child('read');report['after']=after
        assert after['schema']==11 and after['migrations'][:10]==before['migrations']
        assert all(after['row_hashes'][key]==value for key,value in before['row_hashes'].items())
        assert after['triggers']==before['triggers']
        for key in ('citation','typed_run','archives_verified'):assert after[key]==before[key]
        assert after['integrity']==[['ok']] and after['foreign_keys']==[]
        report.update(status='PASS',old_wheel_sha256=hashlib.sha256(Path(args.old_wheel).read_bytes()).hexdigest(),
            new_wheel_sha256=hashlib.sha256(Path(args.wheel).read_bytes()).hexdigest())
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:report['finished']=time.time();save()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','old-wheel','wheel','cuda-python','model-cache','endpoint','report'):parser.add_argument('--'+name,required=True)
    main(parser.parse_args())
