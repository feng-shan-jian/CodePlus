"""Opt-in two clean distribution installs and genuine schema-5 upgrade."""
import argparse
from datetime import datetime, timezone
import hashlib
from email.parser import BytesParser
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib
import zipfile


def main(args):
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    repo=Path(args.repo).resolve();rag=repo/'deployment/AgenticRAG'
    report={'status':'RUNNING','started_at':datetime.now(timezone.utc).isoformat(),'commands':[]}
    report['source_sha256']={str(p.relative_to(repo)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
        for base in (repo/'codeplus',rag/'src') for p in sorted(base.rglob('*')) if p.suffix in ('.py','.sql','.json')}
    locks={name:tomllib.loads((project/'uv.lock').read_text(encoding='utf-8')) for name,project in [('host',repo),('rag',rag)]}
    versions={name:{p['name']:p['version'] for p in lock['package']} for name,lock in locks.items()}
    report['original_lock_version_conflicts']={name:{'host':versions['host'][name],'rag':versions['rag'][name]}
        for name in sorted(versions['host'].keys() & versions['rag'].keys()) if versions['host'][name]!=versions['rag'][name]}
    def command(argv, *, cwd=root):
        result=subprocess.run([str(v) for v in argv],cwd=cwd,env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'},capture_output=True)
        report['commands'].append({'argv':[str(v) for v in argv],'cwd':str(cwd),'exit_code':result.returncode,
            'stdout':result.stdout.decode('utf-8','replace'),'stderr':result.stderr.decode('utf-8','replace')})
        result.check_returncode();return result.stdout.decode('utf-8').strip()
    try:
        for name,project in [('host',repo),('rag',rag)]:
            target=root/'direct'/name
            command(['uv','build','--project',project,'--wheel','--out-dir',target])
            command(['uv','build','--project',project,'--sdist','--out-dir',root/'sdist'/name])
            archive=next((root/'sdist'/name).glob('*.tar.gz'))
            extracted=root/'expanded'/name;extracted.mkdir(parents=True)
            with tarfile.open(archive) as source:source.extractall(extracted,filter='data')
            command(['uv','build','--project',next(extracted.iterdir()),'--wheel','--out-dir',root/'rebuilt'/name])
        command(['uv','export','--project',repo,'--locked','--no-dev','--no-emit-project','--output-file',root/'host-requirements.txt'])
        command(['uv','export','--project',rag,'--locked','--no-dev','--extra','milvus','--no-emit-project','--output-file',root/'rag-requirements.txt'])
        frozen=command(['uv','pip','freeze','--python',sys.executable])
        (root/'validated-host-constraints.txt').write_text('\n'.join(line for line in frozen.splitlines() if '==' in line)+'\n',encoding='utf-8')
        requirements=[];report['wheel_requires_dist']={}
        for name in ('host','rag'):
            with zipfile.ZipFile(next((root/'direct'/name).glob('*.whl'))) as wheel:
                metadata=BytesParser().parsebytes(wheel.read(next(v for v in wheel.namelist() if v.endswith('.dist-info/METADATA'))))
                report['wheel_requires_dist'][name]=metadata.get_all('Requires-Dist')
                for dependency in metadata.get_all('Requires-Dist'):
                    if 'extra ==' not in dependency:requirements.append(dependency)
                    elif 'milvus' in dependency:requirements.append(dependency.split(';')[0].strip())
        (root/'integration.in').write_text('\n'.join(requirements)+'\n',encoding='utf-8')
        command(['uv','pip','compile',root/'integration.in','--python-version','3.14','--generate-hashes',
                 '--constraint',root/'validated-host-constraints.txt','--output-file',root/'integration.lock'])
        report['integration_lock']={'path':str(root/'integration.lock'),'sha256':hashlib.sha256((root/'integration.lock').read_bytes()).hexdigest(),
            'constraints':(root/'validated-host-constraints.txt').read_text(encoding='utf-8'),
            'basis':'Satisfy both local wheel declarations using versions already verified in the R12 host; no change to either project lock'}
        smoke = '''import hashlib,importlib.metadata as m,json,pathlib,sys
import codeplus,agentic_rag
from agentic_rag.adapters.codeplus.meter import DeepSeekTextMeter
from agentic_rag.storage import Catalog
meter=DeepSeekTextMeter(sys.argv[1],model='deepseek-chat',protocol='openai-compat',base_url='https://api.deepseek.com')
c=Catalog(pathlib.Path(sys.argv[2]));lib=c.create_library('clean installed package')
result={'codeplus_file':codeplus.__file__,'rag_file':agentic_rag.__file__,'meter':meter.frozen_identity(),'library':str(lib.kb_id),'torch_imported':'torch' in sys.modules}
assert '/site-packages/' in str(codeplus.__file__).replace('\\\\','/') and '/site-packages/' in str(agentic_rag.__file__).replace('\\\\','/')
assert not result['torch_imported']
print(json.dumps(result))'''
        report['installs']={}
        for label,folder in [('wheel','direct'),('sdist','rebuilt')]:
            env=root/('env-'+label)
            command(['uv','venv','--python',sys.executable,env])
            py=env/'Scripts/python.exe'
            command(['uv','pip','install','--python',py,'--require-hashes','-r',root/'integration.lock'])
            wheels=[next((root/folder/name).glob('*.whl')) for name in ('host','rag')]
            command(['uv','pip','install','--python',py,'--no-deps',*wheels])
            command(['uv','pip','check','--python',py])
            value=command([py,'-I','-B','-X','utf8','-c',smoke,args.tokenizer,root/('data-'+label)])
            report['installs'][label]=json.loads(value)
            report['installs'][label]['python']=str(py)
        report['artifacts']=[]
        for archive in sorted(root.rglob('*.whl')):
            with zipfile.ZipFile(archive) as z:
                files=z.namelist()
                if archive.name.startswith('codeplus_agentic'):
                    vendor='agentic_rag/adapters/codeplus/_vendor/deepseek_v41.py'
                    digest=hashlib.sha256(z.read(vendor)).hexdigest()
                    assert digest=='502bdaec8a3fd88ebc24c4721a7038fbe42f2063c664638127056107920035c1'
                    assert 'agentic_rag/storage/host_runs.sql' in files
                else:
                    assert 'codeplus/run_policy.py' in files
                    assert not any(p.startswith('agentic_rag/') or p.startswith('deployment/') for p in files)
                report['artifacts'].append({'path':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':files})
        # Exact committed pre-R12 source, not a down-versioned current database.
        baseline=root/'baseline.tar'
        command(['git','archive','--format=tar','--output',baseline,args.baseline,'deployment/AgenticRAG'],cwd=repo)
        baseline_root=root/'baseline';baseline_root.mkdir()
        with tarfile.open(baseline) as source:source.extractall(baseline_root,filter='data')
        command(['uv','build','--project',baseline_root/'deployment/AgenticRAG','--wheel','--out-dir',root/'baseline-wheel'])
        upgrade=root/'env-upgrade';command(['uv','venv','--python',sys.executable,upgrade])
        py=upgrade/'Scripts/python.exe'
        command(['uv','pip','install','--python',py,'--require-hashes','-r',root/'rag-requirements.txt'])
        command(['uv','pip','install','--python',py,'--no-deps',next((root/'baseline-wheel').glob('*.whl'))])
        schema='''import json,pathlib,sys
from agentic_rag.storage import Catalog
c=Catalog(pathlib.Path(sys.argv[1]))
if sys.argv[2]=='create':c.create_library('schema-five-kept')
with c._db.transaction() as db:
 print(json.dumps({'version':db.pragma('user_version'),'migrations':list(db.execute('SELECT version,sha256 FROM schema_migrations')),'libraries':list(db.execute('SELECT kb_id,name FROM libraries'))}))'''
        before=json.loads(command([py,'-I','-B','-c',schema,root/'data-upgrade','create']))
        assert before['version']==5
        command(['uv','pip','install','--python',py,'--no-deps','--reinstall',next((root/'direct/rag').glob('*.whl'))])
        after=json.loads(command([py,'-I','-B','-c',schema,root/'data-upgrade','read']))
        assert after['version']==6 and before['migrations']==after['migrations'][:5] and before['libraries']==after['libraries']
        report['upgrade']={'baseline':args.baseline,'before':before,'after':after}
        report['status']='PASS'
    except BaseException as error:
        report.update(status='FAIL',error_type=type(error).__name__,error=str(error));raise
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','repo','tokenizer','report','baseline'):p.add_argument('--'+name,required=True)
    main(p.parse_args())
