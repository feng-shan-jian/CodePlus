"""Internal R10 runtime runner. Query/corpus only; scoring is a separate process.

This is not a product CLI or Agent. The runner calls the installed production
capture/process/build/DenseSearch path. Failures retain an empty-hit record.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import sys
import time
import traceback
from uuid import UUID, uuid4

from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import RunStatus
from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, process_inputs
from agentic_rag.ingestion.build import build_first_revision
from agentic_rag.ingestion.parsing import PARSER
from agentic_rag.ingestion.chunking import chunker_config
from agentic_rag.indexes.manifest import LAYOUT
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import FrozenTokenizer, LocalModelClient
from agentic_rag.retrieval import DenseSearch
from agentic_rag.storage import Catalog, publication


def experiment_config(data_dir, endpoint):
    # Exact IVF traversal is an explicit development baseline, not an ANN optimum.
    return KnowledgeConfig.model_validate_json(json.dumps({
        'storage':{'data_dir':str(data_dir), 'milvus_uri':endpoint, 'namespace':'r10_acceptance'},
        'processing':{'parser':PARSER.model_dump(mode='json'), 'chunker':chunker_config().model_dump(mode='json'),
            'index':{'schema_version_name':LAYOUT, 'nlist':64, 'bm25_k1':1.2, 'bm25_b':0.75}},
        'models':{'embedding':'embed', 'reranker':'rank'},
        'model_profiles':[{'name':'embed','capability':'embedding'}, {'name':'rank','capability':'rerank'}],
        'retrieval':{'mode':'fixed','route':'dense','rerank':False,'dense_candidates':50,'bm25_candidates':50,
            'rerank_candidates':50,'rrf_k':60,'nprobe':64,'context_chunks':8,'context_tokens':8000},
        'budgets':{'qa':{'searches':4,'opens':4,'total_tokens':16000,'duration_ms':180000,'finish_reserve_tokens':3000,'finish_reserve_ms':20000},
                   'report':{'searches':12,'opens':12,'total_tokens':48000,'duration_ms':600000,'finish_reserve_tokens':6000,'finish_reserve_ms':40000}}}))


def protect_runtime_reads():
    suite = Path(__file__).resolve().parents[3]/'eval/RAG-eval'
    denied=[]
    def audit(event, args):
        if event != 'open' or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        path=Path(os.fsdecode(args[0])).absolute()
        if path.is_relative_to(suite) and not path.is_relative_to(suite/'corpus'):
            denied.append(str(path))
            raise PermissionError('runtime cannot read evaluation scoring inputs: '+str(path))
    sys.addaudithook(audit)
    return denied


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=list)+'\n',encoding='utf-8')


def directory_bytes(path):
    return sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file() and not p.is_symlink())


def error_record(exc):
    return {'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc(),
            'detail':exc.error.model_dump(mode='json') if hasattr(exc,'error') else None}


def observed_version(name):
    try:return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:return None


def run(args):
    denied=protect_runtime_reads()
    boundary=runpy.run_path(str(Path(__file__).with_name('runtime_inputs.py')))
    selected=json.loads(Path(args.ids).read_text(encoding='utf-8')) if args.ids else None
    runtime=boundary['load_runtime_inputs'](question_ids=selected)
    root=Path(args.root).resolve(); root.mkdir(exist_ok=True,parents=True)
    data=root/'data'
    config=experiment_config(data,args.endpoint)
    worker=WorkerExecutionConfig(executable=args.cuda_python, model_cache=args.model_cache,
        runtime_dir=str(root/'worker'), idle_timeout_ms=1000)
    catalog=backend=provider=None
    report={'action':args.action,'pid':os.getpid(),'python':sys.executable,'cwd':str(Path.cwd()),
        'argv':sys.argv,'runtime_input_sha256':boundary['INPUT_SHA256'],
        'config':config.model_dump(mode='json'),'worker_config':worker.model_dump(mode='json'),
        'dependencies':{n:observed_version(n) for n in ('codeplus-agentic-rag','pydantic','apsw','tokenizers','pymilvus')},
        'forbidden_runtime_reads':denied,'result':'RUNNING'}
    if args.action=='dense':
        report.update(dataset_sha256=args.dataset_hash,protocol={'top_k':10,'route':'dense','rerank':False,
            'product_context_chunks':8,'evaluation_uses_candidate_top_k':True},
            records=[{**q,'status':'error','hits':[],'elapsed_ms':0.0,
                'error':{'phase':'not_started','type':'NotStarted','message':'request did not start'}} for q in runtime['questions']])
    started=time.perf_counter()
    try:
        catalog=Catalog(data)
        backend=MilvusRevisionIndex(config.storage,catalog)
        provider=LocalModelClient(worker)
        if args.action=='build':
            if (root/'state.json').exists(): raise ValueError('build state already exists')
            kb=catalog.create_library('R10 frozen corpus')
            snapshot=ProcessingSnapshot.capture(uuid4(),config)
            t=time.perf_counter(); manifest=select_inputs(tuple(InputSelection(path=p) for p in runtime['corpus_paths']))
            report['selection_seconds']=time.perf_counter()-t
            with catalog.begin_import(kb.kb_id,snapshot,manifest) as owner:
                report['batch_id']=str(owner.token.batch_id)
                t=time.perf_counter(); captured=capture_inputs(catalog,owner); report['capture_seconds']=time.perf_counter()-t
                t=time.perf_counter(); processed=process_inputs(catalog,owner,FrozenTokenizer(config.embedding,Path(args.model_cache)))
                report['process_seconds']=time.perf_counter()-t
                report['captured']=len(captured);report['processed']=len(processed)
                report['file_failures']=[r.model_dump(mode='json') for r in captured+processed if r.stage=='failed']
                def observe(stage,value):
                    if stage!='encoded' or value['complete']%256==0 or value['complete']==value['total']:
                        print(json.dumps({'stage':stage,**value},default=list),flush=True)
                report['build']=build_first_revision(catalog,owner,provider,backend,observer=observe)
                write(root/'state.json',{'kb_id':str(kb.kb_id),'revision_id':report['build']['receipt']['revision_id'],
                    'batch_id':str(owner.token.batch_id),'snapshot_id':str(snapshot.snapshot_id)})
                report['documents']=[{'document_id':str(r.document_id),'raw_hash':r.raw.sha256,
                    'source_name':r.raw.metadata.original_name,'size_bytes':r.raw.size_bytes} for r in captured]
        else:
            state=json.loads((root/'state.json').read_text(encoding='utf-8'))
            kb_id=UUID(state['kb_id'])
            with catalog.start_run(kb_id,resolve_run(config,'qa')) as lease:
                search=DenseSearch(catalog,lease.run.run_id,provider,backend)
                report.update(run_id=str(lease.run.run_id),
                    revision_id=str(lease.run.revision_id),artifact=publication.artifact(catalog,lease.run.revision_id,published=True))
                sources={Path(p).name:Path(p).stem for p in runtime['corpus_paths']}
                for number,q in enumerate(runtime['questions']):
                    begin=time.perf_counter()
                    try:
                        result=search.search(q['query'],limit=10)
                        for h in result['hits']: h['source_id']=sources[h['source_name']]
                        record={**q,**result,'status':'ok'}
                    except Exception as exc:
                        record={**q,'status':'error','hits':[],'elapsed_ms':(time.perf_counter()-begin)*1000,
                            'error':error_record(exc)}
                    report['records'][number]=record
                    if number%25==0: print(json.dumps({'query':number+1,'total':len(runtime['questions']),'status':record['status']}),flush=True)
                first=runtime['questions'][0]
                try:
                    report['warm_repeat']=search.search(first['query'],limit=10)
                except Exception as exc:
                    report['warm_repeat']={'status':'error','error':error_record(exc)}
                report['errors']=sum(r['status']=='error' for r in report['records'])
                lease.finish(RunStatus.FAILED if report['errors'] else RunStatus.COMPLETED,'explicit_error' if report['errors'] else 'finished')
        report['worker_status']=provider.status()
        report['worker_identity']={k:v for k,v in provider.metadata.items() if k not in ('auth_token','token')}
        report['result']=('COMPLETE_WITH_REQUEST_ERRORS' if report.get('errors') else
                          'COMPLETE_WITH_AUXILIARY_ERROR' if report.get('warm_repeat',{}).get('status')=='error' else 'PASS')
    except BaseException as exc:
        failure=error_record(exc)
        report.update(result='FAIL',error=failure)
        for record in report.get('records',[]):
            if record.get('error',{}).get('phase')=='not_started':
                record['error']={**failure,'phase':'not_started_due_to_run_failure'}
        raise
    finally:
        if args.action=='dense':report['errors']=sum(r['status']=='error' for r in report['records'])
        report['elapsed_seconds']=time.perf_counter()-started
        report['storage_bytes']={'data_including_sqlite_wal_metadata':directory_bytes(data),
            'archives':directory_bytes(data/'archives'),'task_root_including_data_runtime':directory_bytes(root)}
        report['cleanup_errors']=[]
        for resource in (provider,backend):
            if resource is not None:
                try:resource.close()
                except Exception as exc:report['cleanup_errors'].append(error_record(exc))
        if report['cleanup_errors'] and report['result']=='PASS':report['result']='COMPLETE_WITH_CLEANUP_ERRORS'
        write(args.report,report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['build','dense'])
    for name in ('root','endpoint','cuda-python','model-cache','report'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--ids');parser.add_argument('--dataset-hash')
    sys.exit(0 if run(parser.parse_args())['result']=='PASS' else 1)
