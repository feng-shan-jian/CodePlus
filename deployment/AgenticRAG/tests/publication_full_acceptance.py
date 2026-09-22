"""Opt-in full persisted-corpus verification, new clients and owned restart.

This acceptance driver uses installed production code and a previously completed
609-document build. It never loads evaluation questions, gold, or scoring code.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
import traceback
from uuid import UUID

from agentic_rag.config import WorkerExecutionConfig, resolve_run
from agentic_rag.domain import RunStatus
from agentic_rag.indexes.manifest import prepare
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import LocalModelClient
from agentic_rag.retrieval import DenseSearch
from agentic_rag.storage import Catalog, publication


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=list)+'\n', encoding='utf-8')


def execute(args):
    root=Path(args.root).resolve()
    state=json.loads((root/'state.json').read_text(encoding='utf-8'))
    catalog=Catalog(root/'data')
    revision_id=UUID(state['revision_id'])
    artifact=publication.artifact(catalog,revision_id,published=True)
    snapshot=catalog.get_snapshot(catalog.get_batch(UUID(state['batch_id'])).processing_snapshot_id)
    config=snapshot.resolved_config
    runner=runpy.run_path(str(Path(__file__).resolve().parents[1]/'eval/dense_runner.py'))
    denied=runner['protect_runtime_reads']()
    report={'action':args.action,'pid':os.getpid(),'python':sys.executable,'argv':sys.argv,
        'revision_id':str(revision_id),'collection_name':artifact['collection_name'],
        'publication_receipt':publication.receipt(catalog,UUID(state['batch_id'])),
        'forbidden_runtime_reads':denied,'result':'RUNNING'}
    start=time.perf_counter()
    try:
        if args.action=='revalidate':
            backend=MilvusRevisionIndex(config.storage,catalog)
            try:
                plan=prepare(catalog,UUID(state['batch_id']),revision_id)
                expected=json.loads(catalog.archives.read(artifact['expected_hash']))['rows']
                assert [{k:v for k,v in row.items() if k!='vector_hash'} for row in expected]==list(plan.rows)
                report['proof']=backend.validate(artifact,expected)
                report['processing_manifest_hash']=plan.manifest_hash
                report['expected_archive_hash']=artifact['expected_hash']
                assert report['proof']['rows_checked']==4041
                assert report['proof']['row_manifest_hash']==artifact['validation']['row_manifest_hash']
                assert report['proof']['id_set_hash']==artifact['validation']['id_set_hash']
            finally:backend.close()
        elif args.action=='query':
            worker=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,
                runtime_dir=str(root/'worker'),idle_timeout_ms=1000)
            boundary=runpy.run_path(str(Path(__file__).resolve().parents[1]/'eval/runtime_inputs.py'))
            question=boundary['load_runtime_inputs']()['questions'][0]
            backend=MilvusRevisionIndex(config.storage,catalog)
            provider=None
            try:
                provider=LocalModelClient(worker)
                report['query']=DenseSearch(catalog,UUID(args.run_id),provider,backend).search(question['query'],limit=10)
                report['worker_identity']={k:v for k,v in provider.metadata.items() if k not in ('auth_token','token')}
                assert report['query']['revision_id']==str(revision_id)
            finally:
                if provider is not None:provider.close()
                backend.close()
        else:
            resources=runpy.run_path(str(Path(__file__).with_name('publication_resources.py')))
            attempts=[]
            with catalog.start_run(UUID(state['kb_id']),resolve_run(config,'qa')) as lease:
                report['fixed_run_id']=str(lease.run.run_id)
                def query(label,timeout):
                    output=Path(args.report).with_name(Path(args.report).stem+'-'+label+'.json')
                    argv=[sys.executable,'-I','-B',str(Path(__file__).resolve()),'query',
                        '--root',str(root),'--report',str(output),'--run-id',str(lease.run.run_id),
                        '--cuda-python',args.cuda_python,'--model-cache',args.model_cache]
                    completed=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8',timeout=timeout)
                    value=json.loads(output.read_text(encoding='utf-8')) if output.exists() else None
                    attempts.append({'label':label,'argv':argv,'exit_code':completed.returncode,
                        'stdout':completed.stdout,'stderr':completed.stderr,'report':str(output),
                        'sha256':hashlib.sha256(output.read_bytes()).hexdigest() if output.exists() else None})
                    return value if completed.returncode==0 else None
                before=query('before',90);assert before is not None
                resource_report=Path(args.report).with_name(Path(args.report).stem+'-resources.json')
                subprocess.run([sys.executable,'-I','-B',str(Path(__file__).with_name('publication_resources.py')),
                    'restart',str(resource_report)],check=True,timeout=180)
                report['restart_resources']=json.loads(resource_report.read_text(encoding='utf-8'))
                deadline=time.monotonic()+180
                number=0
                while True:
                    number+=1
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('same endpoint data readiness did not recover within 180 seconds')
                    after=query('after-'+str(number),min(90,remaining))
                    if after is not None:break
                    time.sleep(min(2,max(0,deadline-time.monotonic())))
                left,right=before['query'],after['query']
                assert left['run_id']==right['run_id']==str(lease.run.run_id)
                assert len(left['hits'])==len(right['hits'])>0
                for old,new in zip(left['hits'],right['hits']):
                    assert {k:v for k,v in old.items() if k!='score'}=={k:v for k,v in new.items() if k!='score'}
                    assert abs(old['score']-new['score'])<=1e-6
                report.update(attempts=attempts,new_client_pids=[before['pid'],after['pid']],
                    matched_hits=len(left['hits']),source_identity_and_body_equal=True,score_abs_tolerance=1e-6,
                    data_readiness_seconds=180-(deadline-time.monotonic()))
                lease.finish(RunStatus.COMPLETED,'finished')
            report['space_after']=resources['measure']()
        assert publication.receipt(catalog,UUID(state['batch_id']))==report['publication_receipt']
        assert str(catalog.get_library(UUID(state['kb_id'])).current_revision_id)==str(revision_id)
        report['result']='PASS'
    except BaseException as exc:
        report.update(result='FAIL',error={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()})
        raise
    finally:
        report['elapsed_seconds']=time.perf_counter()-start
        write(args.report,report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['revalidate','query','restart'])
    parser.add_argument('--root',required=True);parser.add_argument('--report',required=True)
    parser.add_argument('--run-id');parser.add_argument('--cuda-python');parser.add_argument('--model-cache')
    execute(parser.parse_args())
