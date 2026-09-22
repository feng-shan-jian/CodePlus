"""Genuine installed committed schema-6 application -> current wheel upgrade.

Creates archived/parsed input with the old wheel, removes the external source,
then authenticates all old rows and checkpoints with the new installed wheel.
"""
import argparse
from datetime import datetime,timezone
import hashlib,json,os,subprocess,sys,tarfile
from pathlib import Path


SMOKE=r'''
import hashlib,json,pathlib,sys
from uuid import UUID,uuid4
import agentic_rag
from agentic_rag.storage import Catalog
from agentic_rag.config import KnowledgeConfig,ProcessingSnapshot
from agentic_rag.ingestion import InputSelection,select_inputs,capture_inputs,process_inputs,read_processed
from agentic_rag.models import FrozenTokenizer
root=pathlib.Path(sys.argv[1]);config=KnowledgeConfig.model_validate_json(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))
catalog=Catalog(root/'data')
if sys.argv[4]=='seed':
 source=root/'old source 中文.md';source.write_text('# Old installed source\nThe archived telescope requires immutable approval.\n',encoding='utf-8')
 kb=catalog.create_library('installed v6 keeps data')
 with catalog.begin_import(kb.kb_id,ProcessingSnapshot.capture(uuid4(),config),select_inputs((InputSelection(path=str(source)),))) as owner:
  item,=capture_inputs(catalog,owner);process_inputs(catalog,owner,FrozenTokenizer(config.embedding,sys.argv[3]))
  (root/'ids.json').write_text(json.dumps({'batch':str(item.batch_id),'item':str(item.entry.item_id)}),encoding='utf-8')
  owner.abandon()
 source.unlink()
ids=json.loads((root/'ids.json').read_text());item,version,result=read_processed(catalog,UUID(ids['batch']),UUID(ids['item']))
with catalog._db.transaction() as db:
 rows={name:list(db.execute('SELECT * FROM '+name+' ORDER BY 1')) for name in
 ('libraries','documents','document_versions','sections','chunks','mutation_batches','processing_snapshots','input_manifests','input_items','input_results','processing_items','archive_objects')}
 output={'schema':db.pragma('user_version'),'migrations':list(db.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version')),
         'rows_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),'integrity':list(db.execute('PRAGMA integrity_check')),
         'foreign_keys':list(db.execute('PRAGMA foreign_key_check')),'package':agentic_rag.__file__,
         'checkpoint':item.model_dump(mode='json'),'version':version.model_dump(mode='json'),
         'parsed':result.parsed.text,'source_exists':(root/'old source 中文.md').exists()}
 print(json.dumps(output))
'''


def main(args):
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    repo=Path(args.repo).resolve();report={'status':'RUNNING','commands':[],'baseline':args.baseline,'started_at':datetime.now(timezone.utc).isoformat()}
    def command(argv,cwd=root):
        result=subprocess.run([str(v) for v in argv],cwd=cwd,env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'},capture_output=True,text=True,encoding='utf-8')
        report['commands'].append({'argv':[str(v) for v in argv],'cwd':str(cwd),'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        result.check_returncode();return result.stdout
    try:
        command(['git','archive','--format=tar','--output',root/'baseline.tar',args.baseline,'deployment/AgenticRAG'],cwd=repo)
        with tarfile.open(root/'baseline.tar') as archive:archive.extractall(root/'baseline',filter='data')
        command(['uv','build',root/'baseline/deployment/AgenticRAG','--wheel','--out-dir',root/'old-wheel','--python',sys.executable,'--no-build-isolation','--no-create-gitignore'])
        command(['uv','export','--project',repo/'deployment/AgenticRAG','--locked','--no-dev','--no-emit-project','--output-file',root/'requirements.txt'])
        command(['uv','venv',root/'env','--python',sys.executable])
        py=root/'env/Scripts/python.exe'
        command(['uv','pip','install','--python',py,'--require-hashes','-r',root/'requirements.txt'])
        command(['uv','pip','install','--python',py,'--no-deps',next((root/'old-wheel').glob('*.whl'))])
        smoke=root/'upgrade_smoke.py';smoke.write_text(SMOKE,encoding='utf-8')
        before=json.loads(command([py,'-I','-B',smoke,root,args.config,args.model_cache,'seed']))
        assert before['schema']==6 and before['source_exists'] is False
        command(['uv','pip','install','--python',py,'--no-deps','--reinstall',args.wheel])
        after=json.loads(command([py,'-I','-B',smoke,root,args.config,args.model_cache,'read']))
        assert after['schema']==7 and before['migrations']==after['migrations'][:6]
        for key in ('rows_sha256','checkpoint','version','parsed','source_exists'):assert before[key]==after[key],key
        assert after['integrity']==[['ok']] and after['foreign_keys']==[]
        report.update(before=before,after=after,status='PASS',wheel={'path':args.wheel,'sha256':hashlib.sha256(Path(args.wheel).read_bytes()).hexdigest()})
    except BaseException as exc:
        report.update(status='FAIL',error=str(exc));raise
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','repo','baseline','config','model-cache','wheel','report'):p.add_argument('--'+name,required=True)
    main(p.parse_args())
