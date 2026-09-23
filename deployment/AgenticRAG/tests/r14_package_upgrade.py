"""Build/install R13 v7, publish real data, then upgrade host and OWNED CUDA copy.

The explicitly provided CUDA environment must be a dedicated disposable copy.
It is installed with the identical old wheel for seed and new wheel for resume;
raw implementation fingerprint verification is never relaxed.
"""

import argparse,hashlib,json,os,shutil,subprocess,sys,tarfile,time
from pathlib import Path
import runpy

from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.models.identity import process_birth


def main(args):
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    repo=Path(args.repo);report={'status':'RUNNING','commands':[],'baseline':args.baseline,'started':time.time()}
    def save():Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def command(argv,cwd=root):
        completed=subprocess.run(list(map(str,argv)),cwd=cwd,env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'},capture_output=True,text=True,encoding='utf-8')
        report['commands'].append({'argv':list(map(str,argv)),'cwd':str(cwd),'exit_code':completed.returncode,'stdout':completed.stdout,'stderr':completed.stderr});save()
        completed.check_returncode();return completed.stdout
    try:
        command(['git','archive','--format=tar','--output',root/'baseline.tar',args.baseline,'deployment/AgenticRAG'],cwd=repo)
        with tarfile.open(root/'baseline.tar') as archive:archive.extractall(root/'baseline',filter='data')
        command(['uv','build',root/'baseline/deployment/AgenticRAG','--wheel','--out-dir',root/'v7-wheel','--python',sys.executable,'--no-build-isolation','--no-create-gitignore'])
        command(['uv','venv',root/'env','--python',sys.executable]);py=root/'env/Scripts/python.exe'
        command(['uv','pip','sync','--python',py,'--require-hashes',repo/'deployment/AgenticRAG/docs/implementation-records/R12-windows-integration-lock.txt'])
        old=next((root/'v7-wheel').glob('*.whl'));command(['uv','pip','install','--python',py,'--no-deps',old])
        command(['uv','pip','install','--python',args.cuda_python,'--no-deps','--reinstall',old])
        H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
        config=H['configuration'](root/'data',args.endpoint)
        worker=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,runtime_dir=str(root/'worker'),idle_timeout_ms=10000)
        (root/'settings.json').write_text(json.dumps({'config':config.model_dump(mode='json'),'worker':worker.model_dump(mode='json')}),encoding='utf-8')
        helper=Path(__file__).with_name('r14_upgrade_process.py').resolve()
        def child(mode):return json.loads(command([py,'-I','-B',helper,'--root',root,'--mode',mode,'--model-cache',args.model_cache]))
        before=child('seed');report['before']=before
        assert before['schema']==7 and before['row_counts']['publications']==1 and before['row_counts']['index_artifacts']==2
        assert before['row_counts']['mutation_item_results']>=3 and before['row_counts']['saved_citations']==1
        observed=before['seed_worker'];deadline=time.monotonic()+60
        while process_birth(observed['pid'])==observed['process_birth']:
            if time.monotonic()>deadline:raise TimeoutError('old worker did not complete normal idle exit')
            time.sleep(.1)
        report['old_worker_actual_exit']={'pid':observed['pid'],'birth':observed['process_birth'],'observed_at':time.time(),'exit':'normal configured idle timeout; no global lock manipulation'};save()
        shutil.copytree(root/'data',root/'rollback-data')
        command(['uv','pip','install','--python',py,'--no-deps','--reinstall',args.wheel])
        command(['uv','pip','install','--python',args.cuda_python,'--no-deps','--reinstall',args.wheel])
        fault=child('fault');report['rollback_fault']=fault
        assert fault['row_hashes']==before['row_hashes'] and fault['foreign_keys']==[] and fault['integrity']==[['ok']]
        after=child('read');report['after_upgrade']=after
        assert after['schema']==11 and after['migrations'][:7]==before['migrations']
        assert all(after['row_hashes'][k]==v for k,v in before['row_hashes'].items())
        assert all(after['triggers'][k]==v for k,v in before['triggers'].items())
        assert after['publication_fk']==before['publication_fk'] and after['citation']==before['citation']
        assert after['archives_verified']==before['archives_verified'] and after['typed_run']==before['typed_run']
        assert after['foreign_keys']==[] and after['integrity']==[['ok']]
        resumed=child('resume');report['resume']=resumed;assert resumed['status']=='PASS'
        report.update(status='PASS',old_wheel={'path':str(old),'sha256':hashlib.sha256(old.read_bytes()).hexdigest()},
                      new_wheel={'path':args.wheel,'sha256':hashlib.sha256(Path(args.wheel).read_bytes()).hexdigest()})
    except BaseException as exc:report.update(status='FAIL',error=repr(exc));raise
    finally:report['finished']=time.time();save()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('root','repo','baseline','wheel','cuda-python','model-cache','endpoint','report'):p.add_argument('--'+key,required=True)
    main(p.parse_args())
