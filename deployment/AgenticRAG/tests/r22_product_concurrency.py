"""Opt-in real import, retrieval and existing CodePlus Agent concurrency.

Synthetic documents bound the load; GPU/Milvus/answer responses are real. No
worker or storage responses are replaced, and this is not a quality benchmark.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from uuid import UUID,uuid4

from agentic_rag.adapters.codeplus.policy import DevelopmentConfig
from agentic_rag.config import ProcessingSnapshot,resolve_run
from agentic_rag.domain import ErrorCode,RagError,RunStatus
from agentic_rag.ingestion import InputSelection,begin_changes,build_changes
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import FrozenTokenizer
from agentic_rag.models.identity import process_birth
from agentic_rag.retrieval import RetrievalSearch
from agentic_rag.storage import Catalog
from agentic_rag.storage import readers

H=runpy.run_path(str(Path(__file__).with_name('scheduling_support.py')))
R21=runpy.run_path(str(Path(__file__).with_name('r21_user_acceptance.py')))


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def services(root,label):
    settings=DevelopmentConfig.model_validate_json((root/'development.json').read_text(encoding='utf-8'))
    catalog=Catalog(root/'data')
    provider=H['ObservedClient'](settings.worker,root/(label+'-model.jsonl'))
    backend=MilvusRevisionIndex(settings.knowledge.storage,catalog)
    return settings,catalog,provider,backend


def import_child(root):
    settings,catalog,provider,backend=services(root,'import')
    config=settings.knowledge;state=load(root/'state.json')
    report={'status':'RUNNING','pid':os.getpid(),'started_ns':time.monotonic_ns()}
    try:
        selected=(InputSelection(path=str(root/'中文 source.md')),InputSelection(path=str(root/'bulk')))
        with begin_changes(catalog,UUID(state['kb_id']),ProcessingSnapshot.capture(uuid4(),config),selected) as owner:
            report['owner']={key:str(value) if isinstance(value,UUID) else value for key,value in asdict(owner.token).items()}
            H['write'](root/'import-running.json',report)
            report['build']=build_changes(catalog,owner,provider,backend,FrozenTokenizer(config.embedding,settings.worker.model_cache))
        report['model_requests']=[provider.record(h) for h in provider.handles.values()]
        report['finished_ns']=time.monotonic_ns();report['status']='PASS'
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:
        provider.close();backend.close();backend.wait_closed()
        H['write'](root/'import.json',report)
    print('IMPORT_PASS',flush=True)


def recorded_search(search,query,report,label,**options):
    start=time.monotonic_ns()
    value=search.search(query,limit=8,deadline_monotonic_ns=start+120_000_000_000,**options)
    report.setdefault('core_searches',[]).append({'label':label,'started_ns':start,'finished_ns':time.monotonic_ns(),
                                               'result':value})
    return value


def main(args):
    assert os.environ.get('R22_REAL')=='1','explicit real GPU/Milvus/answer-model opt-in required'
    root=Path(args.root)
    report={'status':'RUNNING','started':time.time(),'scope':'actual installed import/search/existing CodePlus report with new synthetic documents',
            'commands':[],'core_searches':[]}
    R21['prepare'](args)
    settings,catalog,provider,backend=services(root,'core')
    config=settings.knowledge
    kb=catalog.create_library('R22 concurrent versions').kb_id
    importer=agent=None;streams=[];oldlease=None;metadata=None
    try:
        with begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),
                (InputSelection(path=str(root/'中文 source.md')),)) as owner:
            report['initial_build']=build_changes(catalog,owner,provider,backend,FrozenTokenizer(config.embedding,args.model_cache))
        old_revision=str(catalog.get_library(kb).current_revision_id)
        H['write'](root/'state.json',{'kb_id':str(kb),'old_revision':old_revision})
        (root/'中文 source.md').write_text('# Vega certificate\nIn 2025, the Vega telescope has a red safety certificate. '
            'The red certificate is held in the mountain registry.\n',encoding='utf-8')
        bulk=root/'bulk';bulk.mkdir()
        for number in range(128):
            body='\n\n'.join(f'Entry {part} for station {number} records a routine inspection of the alloy storage shelves. '
                'The inspector measured temperature and humidity, logged the instrument serial number, '
                'and scheduled the next routine visit. No telescope safety certificate is recorded here.' for part in range(64))
            (bulk/f'notebook-{number:03d}.md').write_text(f'# Routine notebook {number}\n{body}\n',encoding='utf-8')
        report['fixture']={p.relative_to(root).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size}
            for p in [root/'中文 source.md',*sorted(bulk.glob('*.md'))]}
        oldlease=catalog.start_run(kb,resolve_run(config,'qa'))
        oldsearch=RetrievalSearch(catalog,oldlease.run.run_id,provider,backend)
        assert str(oldlease.run.revision_id)==old_revision
        argv=[sys.executable,'-I','-B',str(Path(__file__).resolve()),'import','--root',str(root)]
        out=(root/'import.stdout').open('w',encoding='utf-8');err=(root/'import.stderr').open('w',encoding='utf-8');streams += [out,err]
        importer=subprocess.Popen(argv,cwd=root,stdout=out,stderr=err)
        report['commands'].append({'label':'import','argv':argv,'pid':importer.pid,'started_ns':time.monotonic_ns()})
        # Actual first-layer notification from the import's production worker.
        end=time.monotonic()+600
        while True:
            assert importer.poll() is None,'import ended before the concurrency gate'
            path=root/'import-model.jsonl'
            complete=path.read_text(encoding='utf-8').rsplit('\n',1)[0] if path.exists() else ''
            events=[json.loads(line) for line in complete.splitlines() if line.strip()]
            if any(row.get('phase')=='inference_started' for row in events):break
            assert time.monotonic()<end,'import did not reach actual GPU inference'
            time.sleep(.02)
        report['import_gpu_gate_ns']=time.monotonic_ns()
        report['import_owner']=load(root/'import-running.json')['owner']
        assert str(catalog.get_library(kb).current_revision_id)==old_revision
        try:
            with begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),
                    (InputSelection(path=str(root/'中文 source.md')),)):
                raise AssertionError('concurrent mutation was admitted')
        except RagError as exc:
            assert exc.error.code==ErrorCode.LIBRARY_BUSY
            report['same_library_busy']=exc.error.model_dump(mode='json')
        value=R21['provider'](args);work=R21['host_config'](root,value)
        env=dict(os.environ,OPENAI_API_KEY=value.resolve_api_key(),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        env.pop('PYTHONPATH',None)
        target=work/'concurrent-report.md'
        argv=[sys.executable,'-I','-B','-X','utf8','-m','codeplus','-p',
            '请生成简短研究报告，包含结论、证据和局限。'+R21['QUESTION'],'--output-format','stream-json',
            '--knowledge-library',str(kb),'--knowledge-mode','fixed','--knowledge-report',str(target),'--mode','acceptEdits']
        out=(root/'agent.stdout').open('w',encoding='utf-8');err=(root/'agent.stderr').open('w',encoding='utf-8');streams += [out,err]
        agent=subprocess.Popen(argv,cwd=work,env=env,stdout=out,stderr=err)
        report['commands'].append({'label':'agent','argv':argv,'pid':agent.pid,'started_ns':time.monotonic_ns()})
        for index in range(8):
            assert importer.poll() is None,'long import did not overlap the bounded foreground interval'
            found=recorded_search(oldsearch,'What color is the Vega telescope safety certificate and where is it held?',report,'during-import-'+str(index))
            assert found['revision_id']==old_revision and found['hits']
            assert any('blue safety certificate' in hit['text'] for hit in found['hits'])
            with catalog._db.transaction() as db:
                report.setdefault('background_progress',[]).append({'observed_ns':time.monotonic_ns(),
                    'encoded_documents':db.execute("SELECT count(*) FROM mutation_item_results WHERE batch_id=? AND state='encoded'",
                        (report['import_owner']['batch_id'],)).fetchone()[0],
                    'active_pin':catalog.get_pin(oldlease.run.run_id).state,
                    'pending_mutation_id':str(catalog.get_library(kb).pending_mutation_id),
                    'agent_source_calls':db.execute('SELECT c.run_id,c.kind,c.status FROM source_calls c JOIN host_runs h ON h.run_id=c.run_id').fetchall()})
            H['write'](root/'product.json',report)
        assert report['background_progress'][-1]['encoded_documents'] > report['background_progress'][0]['encoded_documents']
        assert any(any(call[1] in {'search','open'} and call[2]=='ok' for call in row['agent_source_calls'])
                   and row['pending_mutation_id']==report['import_owner']['batch_id']
                   for row in report['background_progress'])
        agent.wait(660)
        assert importer.wait(1200)==0,'import failed'
        for stream in streams:stream.flush()
        imported=load(root/'import.json');assert imported['status']=='PASS'
        report['import_result']=imported
        first_inference=min(event['observed_ns'] for request in imported['model_requests']
            for event in request['events'] if event.get('phase')=='inference_started')
        last_finished=max(event['observed_ns'] for request in imported['model_requests']
            for event in request['events'] if event['type']=='finished')
        assert any(first_inference<row['observed_ns']<last_finished and
            row['pending_mutation_id']==report['import_owner']['batch_id'] and
            any(call[1] in {'search','open'} and call[2]=='ok' for call in row['agent_source_calls'])
            for row in report['background_progress'])
        events=[json.loads(line) for line in (root/'agent.stdout').read_text(encoding='utf-8').splitlines() if line.startswith('{')]
        final=next(e for e in reversed(events) if e['type']=='result')
        report['agent_result']=final
        assert agent.returncode==0 and final['status']=='completed' and final['stop_reason']=='finished', \
            'concurrency may be observed, but a partial/failed report is not a normal report-chain pass'
        saved=final.get('save')
        assert saved and saved['status']=='saved' and target.is_file()
        assert sha(target)==saved['sha256'] and target.stat().st_size==saved['size_bytes']
        agent_run=catalog.get_run(UUID(final['run_id']))
        report['agent_run']=agent_run.model_dump(mode='json')
        assert str(agent_run.revision_id)==old_revision
        assert agent_run.usage.searches > 0
        assert catalog.get_pin(agent_run.run_id).state=='released'
        report['report_artifact']={'path':str(target),'sha256':sha(target),'bytes':target.stat().st_size}
        current=str(catalog.get_library(kb).current_revision_id)
        assert current != old_revision
        after_old=recorded_search(oldsearch,'What color is the Vega telescope safety certificate?',report,'old-run-after-publish')
        assert after_old['revision_id']==old_revision
        assert any('blue safety certificate' in hit['text'] for hit in after_old['hits'])
        with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
            new=recorded_search(RetrievalSearch(catalog,lease.run.run_id,provider,backend),
                'What color is the Vega telescope safety certificate?',report,'new-run-after-publish')
            assert new['revision_id']==current
            assert any('red safety certificate' in hit['text'] for hit in new['hits'])
            report['new_run_id']=str(lease.run.run_id)
        report['old_run_id']=str(oldlease.run.run_id);report['old_revision']=old_revision;report['new_revision']=current
        # Real model cancellation while a reader is active. Pin release must
        # wait for a finished notice; returning to Python is not that proof.
        lease=catalog.start_run(kb,resolve_run(config,'qa'))
        search=RetrievalSearch(catalog,lease.run.run_id,provider,backend)
        previous=set(provider.handles)
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(search.search,'word '*1800,limit=1,strategy='dense',rerank=False,
                               deadline_monotonic_ns=time.monotonic_ns()+120_000_000_000)
            end=time.monotonic()+60
            while not (set(provider.handles)-previous):
                assert time.monotonic()<end;time.sleep(.001)
            handle=provider.handles[next(iter(set(provider.handles)-previous))]
            handle.wait_phase('inference_started',60)
            lease.finish(RunStatus.CANCELLED,'user_cancelled')
            handle.cancel()
            before={'pin':catalog.get_pin(lease.run.run_id).state,'execution_finished':handle.execution_finished,
                    'observed_ns':time.monotonic_ns()}
            assert before=={**before,'pin':'active','execution_finished':False}
            try:future.result(60)
            except RagError as exc:assert exc.error.code==ErrorCode.CANCELLED
            else:raise AssertionError('cancelled reader returned success')
            assert handle.wait_finished(60)
            readers.flush_finished(catalog)
            assert catalog.get_pin(lease.run.run_id).state=='released'
            report['cancelled_reader']={'run_id':str(lease.run.run_id),'before_completion':before,'request':provider.record(handle),
                                        'final_pin':catalog.get_pin(lease.run.run_id).state}
        oldlease.close();oldlease=None
        report['core_model_requests']=[provider.record(h) for h in provider.handles.values()]
        with catalog._db.transaction() as db:
            report['actual_reader_workers']=db.execute("SELECT pid,worker_identity,state,completed_by FROM index_readers WHERE kind='model'").fetchall()
            report['persisted_state']={'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],
                'active_pins':db.execute("SELECT count(*) FROM run_pins WHERE state='active'").fetchone()[0],
                'pending_readers':db.execute("SELECT count(*) FROM index_readers WHERE state='pending'").fetchone()[0],
                'pending_mutation_id':str(catalog.get_library(kb).pending_mutation_id)}
        assert report['persisted_state']['integrity']=='ok'
        assert report['persisted_state']['active_pins']==report['persisted_state']['pending_readers']==0
        worker_ids={json.loads(row[1])['instance_id'] for row in report['actual_reader_workers']}
        worker_ids.update(row['worker']['instance_id'] for row in imported['model_requests'])
        assert len(worker_ids)==1
        assert len({row[0] for row in report['actual_reader_workers']})>=2
        report['status']='PASS'
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:
        if oldlease is not None:oldlease.close()
        # Do not infer process death or remove another owner's work on failure.
        for child in (agent,importer):
            if child is not None and child.poll() is None:child.wait(1200)
        report['exit_codes']={'agent':agent.returncode if agent else None,'import':importer.returncode if importer else None}
        for stream in streams:stream.close()
        metadata=provider.metadata
        provider.close();backend.close();backend.wait_closed()
        if metadata:
            end=time.monotonic()+30
            while process_birth(metadata['pid'])==metadata['process_birth'] and time.monotonic()<end:time.sleep(.03)
            assert process_birth(metadata['pid'])!=metadata['process_birth']
            report['worker_idle_exit']={k:metadata[k] for k in ('pid','process_birth','instance_id')}
        report['finished']=time.time();H['write'](root/'product.json',report)
    print('PRODUCT_PASS',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','import']);p.add_argument('--root',required=True)
    for option in ('endpoint','cuda-python','model-cache','answer-tokenizer','provider-config'):p.add_argument('--'+option)
    args=p.parse_args()
    if args.action=='import':import_child(Path(args.root))
    else:main(args)
