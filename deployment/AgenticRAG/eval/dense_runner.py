"""Internal retrieval runtime runner. Query/corpus only; scoring is separate.

This is not a product CLI or Agent. The runner calls the installed production
capture/process/build/RetrievalSearch path. Failures retain an empty-hit record.
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
from agentic_rag.retrieval import RetrievalSearch
from agentic_rag.storage import Catalog, publication


def experiment_config(data_dir, endpoint, route='dense', *, rerank=False):
    # Exact IVF traversal is an explicit development baseline, not an ANN optimum.
    return KnowledgeConfig.model_validate_json(json.dumps({
        'storage':{'data_dir':str(data_dir), 'milvus_uri':endpoint, 'namespace':'r10_acceptance'},
        'processing':{'parser':PARSER.model_dump(mode='json'), 'chunker':chunker_config().model_dump(mode='json'),
            'index':{'schema_version_name':LAYOUT, 'nlist':64, 'bm25_k1':1.2, 'bm25_b':0.75}},
        'models':{'embedding':'embed', 'reranker':'rank'},
        'model_profiles':[{'name':'embed','capability':'embedding'}, {'name':'rank','capability':'rerank'}],
        'retrieval':{'mode':'fixed','route':route,'rerank':rerank,'dense_candidates':50,'bm25_candidates':50,
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
            'detail':exc.error.model_dump(mode='json') if hasattr(exc,'error') else None,
            'retrieval_trace':getattr(exc,'retrieval_trace',None)}


def observed_version(name):
    try:return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:return None


def completion_status(report):
    if report.get('errors'):
        return 'COMPLETE_WITH_REQUEST_ERRORS'
    if report.get('context_errors'):
        return 'COMPLETE_WITH_CONTEXT_ERRORS'
    warm=report.get('warm_repeat',{})
    if warm.get('status')=='error' or warm.get('context',{}).get('status')=='error':
        return 'COMPLETE_WITH_AUXILIARY_ERROR'
    return 'PASS'


def query_context(catalog, kb_id, revision_id, config, provider, backend, meter, query):
    """One real retrieval, plus production selection and exact unsent request.

    This query-only measurement never runs an answer model or confirms delivery.
    The empty request reserves the same fixed wrapper before source selection.
    """
    from agentic_rag.adapters.codeplus.policy import SYSTEM
    from agentic_rag.domain import Span
    from agentic_rag.evidence import DeliveryGateway, MappedSpan
    from agentic_rag.sources import SourceSession
    observed = {}
    class RecordedSearch(RetrievalSearch):
        def search(self, *args, **kwargs):
            result = super().search(*args, **kwargs)
            observed.update(result)
            return result
    started = time.perf_counter()
    with catalog.start_run(kb_id, resolve_run(config,'qa')) as lease:
        if str(lease.run.revision_id) != revision_id:
            raise ValueError('context run publication differs')
        search=RecordedSearch(catalog,lease.run.run_id,provider,backend)
        session=SourceSession(catalog,lease,meter,dense=search)
        gateway=DeliveryGateway(session)
        call='measurement-search'
        body={'model':meter.model,'messages':[{'role':'system','content':SYSTEM},
            {'role':'user','content':query}, {'role':'assistant','content':None,
             'tool_calls':[{'id':call,'type':'function','function':{'name':'knowledge_search','arguments':json.dumps({'query':query})}}]},
            {'role':'tool','tool_call_id':call,'content':''}],
            'max_tokens':1000,'stream':True,'stream_options':{'include_usage':True}}
        raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
        initial=gateway.prepare(raw,(),purpose='finalize',protocol='compat')
        gateway.retain_prepared_window(initial);gateway.settle(initial,'not_sent')
        context={'status':'running','delivery':'not_sent','answer_model_executed':False,'meter_identity':meter.identity,
                 'fixed_request_bytes':len(raw),'selected':[],'prepared':[]}
        try:
            result=session.search(query)
            context.update(selected=result.payload['items'],selection=session.retrieval_trace(result.payload['call_id'])['context'],
                tool_text=result.text,tool_upper=meter.count(result.text))
            gateway.bind_tool_result(result,call)
            body['messages'][-1]['content']=result.text
            raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
            mappings=tuple(MappedSpan(m.candidate_id,m.source_span,('messages',3,'content'),m.body_span,
                ('messages',3,'tool_call_id')) for m in result.body_mappings)
            permit=gateway.prepare(raw,mappings,purpose='finalize',protocol='compat')
            try:
                context.update(delivery='prepared',request_body=body,request_sha256=hashlib.sha256(raw).hexdigest(),
                    request_utf8_upper=meter.count(raw.decode()),host_input_upper=meter.input_upper_bound(raw,output_cap=1000))
                gateway.retain_prepared_window(permit)
                context.update(status='ok',prepared=result.payload['items'])
            finally:
                gateway.settle(permit,'not_sent')
            with catalog._db.transaction() as db:
                context['confirmed_evidence']=db.execute('SELECT count(*) FROM delivered_evidence WHERE run_id=?',
                    (str(lease.run.run_id),)).fetchone()[0]
        except Exception as exc:
            context.update(status='error',error=error_record(exc))
            if not observed:raise
        context['elapsed_ms']=(time.perf_counter()-started)*1000
        observed['hits']=observed['hits'][:10]
        observed['context']=context
        lease.finish(RunStatus.FAILED if context['status']=='error' else RunStatus.COMPLETED,
            'explicit_error' if context['status']=='error' else 'finished')
    return observed


def run(args):
    denied=protect_runtime_reads()
    boundary=runpy.run_path(str(Path(__file__).with_name('runtime_inputs.py')))
    selected=json.loads(Path(args.ids).read_text(encoding='utf-8')) if args.ids else None
    runtime=boundary['load_runtime_inputs'](question_ids=selected)
    root=Path(args.root).resolve(); root.mkdir(exist_ok=True,parents=True)
    data=root/'data'
    config=experiment_config(data,args.endpoint,'dense' if args.action=='build' else args.action,rerank=getattr(args,'rerank',False))
    catalog=backend=provider=None
    report={'action':args.action,'pid':os.getpid(),'python':sys.executable,'cwd':str(Path.cwd()),
        'argv':sys.argv,'runtime_input_sha256':boundary['INPUT_SHA256'],
        'config':config.model_dump(mode='json'),'worker_config':None,
        'dependencies':{n:observed_version(n) for n in ('codeplus-agentic-rag','pydantic','apsw','tokenizers','pymilvus')},
        'forbidden_runtime_reads':denied,'result':'RUNNING'}
    if args.action!='build':
        report.update(dataset_sha256=args.dataset_hash,protocol={'top_k':10,'route':args.action,'rerank':config.retrieval.rerank,
            'product_context_chunks':8,'evaluation_uses_candidate_top_k':True,'context_measurement':bool(getattr(args,'answer_tokenizer',None))},
            records=[{**q,'status':'error','hits':[],'elapsed_ms':0.0,
                'error':{'phase':'not_started','type':'NotStarted','message':'request did not start'}} for q in runtime['questions']])
    started=time.perf_counter()
    try:
        catalog=Catalog(data)
        backend=MilvusRevisionIndex(config.storage,catalog)
        if args.action!='bm25' or config.retrieval.rerank:
            worker=WorkerExecutionConfig(executable=args.cuda_python, model_cache=args.model_cache,
                runtime_dir=str(root/'worker'), idle_timeout_ms=1000)
            report['worker_config']=worker.model_dump(mode='json')
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
            meter=None
            if getattr(args,'answer_tokenizer',None):
                from agentic_rag.adapters.codeplus.meter import DeepSeekTextMeter
                meter=DeepSeekTextMeter(args.answer_tokenizer,model='deepseek-chat',protocol='openai-compat',base_url='https://api.deepseek.com')
            with catalog.start_run(kb_id,resolve_run(config,'qa')) as lease:
                if str(lease.run.revision_id)!=state['revision_id']:
                    raise ValueError('current revision differs from the frozen build state')
                search=RetrievalSearch(catalog,lease.run.run_id,provider,backend)
                report.update(run_id=str(lease.run.run_id),
                    revision_id=str(lease.run.revision_id),artifact=publication.artifact(catalog,lease.run.revision_id,published=True))
                sources={Path(p).name:Path(p).stem for p in runtime['corpus_paths']}
                for number,q in enumerate(runtime['questions']):
                    begin=time.perf_counter()
                    try:
                        result=(query_context(catalog,kb_id,state['revision_id'],config,provider,backend,meter,q['query'])
                                if meter else search.search(q['query'],limit=10))
                        for h in result['hits']: h['source_id']=sources[h['source_name']]
                        record={**q,**result,'status':'ok'}
                    except Exception as exc:
                        record={**q,'status':'error','hits':[],'elapsed_ms':(time.perf_counter()-begin)*1000,
                            'error':error_record(exc)}
                    report['records'][number]=record
                    if number%25==0: print(json.dumps({'query':number+1,'total':len(runtime['questions']),'status':record['status']}),flush=True)
                first=runtime['questions'][0]
                try:
                    report['warm_repeat']=(query_context(catalog,kb_id,state['revision_id'],config,provider,backend,meter,first['query'])
                                           if meter else search.search(first['query'],limit=10))
                except Exception as exc:
                    report['warm_repeat']={'status':'error','error':error_record(exc)}
                report['errors']=sum(r['status']=='error' for r in report['records'])
                report['context_errors']=sum(r.get('context',{}).get('status')=='error' for r in report['records'])
                lease.finish(RunStatus.FAILED if report['errors'] else RunStatus.COMPLETED,'explicit_error' if report['errors'] else 'finished')
        if provider is not None:
            report['worker_status']=provider.status()
            report['worker_identity']={k:v for k,v in provider.metadata.items() if k not in ('auth_token','token')}
        else:
            report['worker_status']={'used':False,'reason':'bm25_has_no_query_embedding'}
        report['result']=completion_status(report)
    except BaseException as exc:
        failure=error_record(exc)
        report.update(result='FAIL',error=failure)
        for record in report.get('records',[]):
            if record.get('error',{}).get('phase')=='not_started':
                record['error']={**failure,'phase':'not_started_due_to_run_failure'}
        raise
    finally:
        if args.action!='build':report['errors']=sum(r['status']=='error' for r in report['records'])
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
    parser.add_argument('action',choices=['build','dense','bm25','hybrid'])
    for name in ('root','endpoint','report'):parser.add_argument('--'+name,required=True)
    for name in ('cuda-python','model-cache'):parser.add_argument('--'+name)
    parser.add_argument('--ids');parser.add_argument('--dataset-hash')
    parser.add_argument('--rerank',action='store_true',help='enable the frozen reranker; all other retrieval settings stay fixed')
    parser.add_argument('--answer-tokenizer',help='measure selected/prepared unsent Context using the pinned host upper-bound meter')
    sys.exit(0 if run(parser.parse_args())['result']=='PASS' else 1)
