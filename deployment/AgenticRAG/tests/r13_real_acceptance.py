"""Opt-in installed GPU/Milvus mutation and two-process immutable-run acceptance.

All normal encoding uses the locked NVIDIA model. Inputs are named synthetic
documents; invalid UTF-8 is a real file-level error, not a semantic quality test.
No services, collections or directories are deleted by this driver.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from uuid import UUID,uuid4

import agentic_rag
from agentic_rag.config import KnowledgeConfig,ProcessingSnapshot,WorkerExecutionConfig,resolve_run
from agentic_rag.domain import ErrorCode,RagError,RunStatus
from agentic_rag.ingestion import InputSelection,begin_changes,build_changes,retry_failed
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import FrozenTokenizer,LocalModelClient
from agentic_rag.models.identity import process_birth
from agentic_rag.retrieval.dense import DenseSearch
from agentic_rag.sources import SourceSession
from agentic_rag.storage import Catalog,publication


def now():return datetime.now(timezone.utc).isoformat()


def write(path,value):
    path=Path(path);pending=path.with_name(path.name+'.pending')
    with pending.open('w',encoding='utf-8') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n');stream.flush();os.fsync(stream.fileno())
    os.replace(pending,path)


def identity():
    return {'pid':os.getpid(),'birth':process_birth(os.getpid()),'executable':sys.executable,
            'cwd':str(Path.cwd()),'package':agentic_rag.__file__,'time':now(),
            'argv':sys.argv,'packages':{name:importlib.metadata.version(name) for name in
                ('codeplus-agentic-rag','pydantic','apsw','tokenizers','pymilvus')}}


class Meter:
    identity='R13-utf8-byte-upper-bound-source-only'
    def count(self,text):return len(text.encode('utf-8'))


def inspect_run(catalog,lease,provider,backend):
    # Intentionally created only after the writer's published gate, never cached.
    dense=DenseSearch(catalog,lease.run.run_id,provider,backend)
    sources=SourceSession(catalog,lease,Meter(),dense=dense)
    direct=dense.search('telescope approval retiredquasar keep mountain archive',limit=50)
    search=sources.search('telescope approval retiredquasar keep mountain archive')
    opened=[]
    for item in search.payload['items']:
        if item['file_name'] in ('update.md','delete.md','failed.md'):
            opened.append(sources.open(item['source_ref']).payload)
    artifact=publication.artifact(catalog,lease.run.revision_id,published=True)
    sparse={q:backend.search(artifact,q,field='sparse',limit=50) for q in ('telescope','retiredquasar','approval')}
    return {'started_at':now(),'run':lease.run.model_dump(mode='json'),'pin':catalog.get_pin(lease.run.run_id).model_dump(mode='json'),
            'collection':artifact['collection_name'],'dense':direct,'source_search':search.payload,'opened':opened,'bm25':sparse}


def reader(args):
    root=Path(args.root);settings=json.loads((root/'settings.json').read_text(encoding='utf-8'))
    config=KnowledgeConfig.model_validate_json(json.dumps(settings['config']));worker=WorkerExecutionConfig.model_validate_json(json.dumps(settings['worker']))
    catalog=Catalog(config.storage.data_dir)
    report={'status':'RUNNING','identity':identity()}
    lease=catalog.start_run(UUID(settings['kb_id']),resolve_run(config,'qa'))
    report['pinned_before_gate']={'identity':identity(),'run':lease.run.model_dump(mode='json'),
                                 'pin':catalog.get_pin(lease.run.run_id).model_dump(mode='json'),'prior_search_calls':0,'prior_source_reads':0}
    write(root/'pinned.json',report['pinned_before_gate'])
    provider=backend=None
    try:
        deadline=time.monotonic()+240
        while not (root/'published.json').exists():
            if time.monotonic()>=deadline:raise TimeoutError('writer publication gate missing')
            time.sleep(.05)
        report['gate']=json.loads((root/'published.json').read_text(encoding='utf-8'))
        report['gate_observed_at']=now()
        provider=LocalModelClient(worker);backend=MilvusRevisionIndex(config.storage,catalog)
        report['read_after_gate']=inspect_run(catalog,lease,provider,backend)
        report['worker']={k:v for k,v in provider.metadata.items() if k not in ('token','auth_token')}
        lease.finish(RunStatus.COMPLETED,'finished')
        report['released_pin']=catalog.get_pin(lease.run.run_id).model_dump(mode='json')
        report['status']='PASS'
    except BaseException as exc:
        report.update(status='FAIL',error=str(exc));raise
    finally:
        if provider:provider.close()
        if backend:backend.close()
        report['finished_at']=now();write(root/'reader-report.json',report)


def main(args):
    root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    if (root/'settings.json').exists():raise RuntimeError('use a new root for each real acceptance attempt')
    assert 'site-packages' in Path(agentic_rag.__file__).parts,'real acceptance requires installed wheel'
    template=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))['configuration']
    data=template(root/'data',args.endpoint).model_dump(mode='json')
    data['storage']['namespace']='r13_acceptance'
    data['retrieval'].update(context_chunks=32,context_tokens=50000)
    data['budgets']['qa'].update(searches=10,opens=20,total_tokens=200000,duration_ms=300000)
    config=KnowledgeConfig.model_validate_json(json.dumps(data))
    worker=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,runtime_dir=str(root/'worker'),idle_timeout_ms=30000)
    catalog=Catalog(root/'data');kb=catalog.create_library('R13 complete immutable candidates')
    report={'status':'RUNNING','identity':identity(),'config':config.model_dump(mode='json'),'worker_config':worker.model_dump(mode='json'),
            'inputs':[],'events':[],'scope':'synthetic named inputs; real GPU and service; no semantic-quality claim'}
    provider=backend=child=None
    def material(name,body):
        path=root/name
        path.write_bytes(body.encode() if isinstance(body,str) else body)
        report['inputs'].append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size':path.stat().st_size,'body':body if isinstance(body,str) else None,'time':now()})
        return path
    def change(paths=(),deletes=()):
        return begin_changes(catalog,kb.kb_id,ProcessingSnapshot.capture(uuid4(),config),
            tuple(InputSelection(path=str(p)) for p in paths),delete_document_ids=tuple(deletes))
    def build(owner):
        return build_changes(catalog,owner,provider,backend,tokenizer,observer=lambda stage,value: report['events'].append({'time':now(),'stage':stage,**value}))
    try:
        provider=LocalModelClient(worker);backend=MilvusRevisionIndex(config.storage,catalog)
        tokenizer=FrozenTokenizer(config.embedding,args.model_cache)
        update=material('update.md','# Telescope\nTelescope approval uses the oldblue certificate.\n')
        delete=material('delete.md','# Retired\nRetiredquasar approval uses the legacygreen certificate.\n')
        keep=material('keep.md','# Mountain\nMountain approval keeps the unchangedviolet archive.\n')
        failed=material('failed.md','# Failed\nFailed update approval keeps the oldsilver report.\n')
        with change((update,delete,keep,failed)) as owner:
            report['v1']=build(owner)
        v1=UUID(report['v1']['receipt']['revision_id'])
        with catalog._db.transaction() as db:
            docs={row[0]:UUID(row[1]) for row in db.execute('SELECT original_name,document_id FROM documents WHERE kb_id=?',(str(kb.kb_id),))}
        write(root/'settings.json',{'config':config.model_dump(mode='json'),'worker':worker.model_dump(mode='json'),'kb_id':str(kb.kb_id)})
        argv=[sys.executable,'-I','-B',str(Path(__file__).resolve()),'reader','--root',str(root)]
        child=subprocess.Popen(argv,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        report['reader_launcher']={'argv':argv,'cwd':str(root),'pid':child.pid,'birth':process_birth(child.pid)}
        deadline=time.monotonic()+45
        while not (root/'pinned.json').exists():
            if child.poll() is not None:raise RuntimeError('reader ended before pin: '+child.stderr.read().decode('utf-8','replace'))
            if time.monotonic()>=deadline:raise TimeoutError('reader did not pin V1')
            time.sleep(.05)
        report['reader_pinned']=json.loads((root/'pinned.json').read_text(encoding='utf-8'))
        assert report['reader_pinned']['run']['revision_id']==str(v1)
        material('update.md','# Telescope\nTelescope approval now requires the newred certificate.\n')
        material('failed.md',b'\xffinvalid UTF-8 is an actual parser failure')
        added=material('added.md','# Island\nIsland approval introduces a newamber record.\n')
        with change((update,keep,failed,added),(docs['delete.md'],)) as owner:
            report['v2']=build(owner);failed_batch=owner.token.batch_id
        assert report['v2']['summary']['failed']==1
        assert report['v2']['summary']['published_updated']==report['v2']['summary']['published_new']==report['v2']['summary']['published_deleted']==1
        assert report['v2']['metrics']['reused_vectors']==2
        gate={'time':now(),'receipt':report['v2']['receipt']};write(root/'published.json',gate)
        report['publication_gate']=gate
        lease=catalog.start_run(kb.kb_id,resolve_run(config,'qa'))
        report['new_run']=inspect_run(catalog,lease,provider,backend)
        lease.finish(RunStatus.COMPLETED,'finished')
        stdout,stderr=child.communicate(timeout=120)
        report['reader_exit']={'code':child.returncode,'stdout':stdout.decode('utf-8','replace'),'stderr':stderr.decode('utf-8','replace')}
        if child.returncode:raise RuntimeError('reader failed')
        report['old_run']=json.loads((root/'reader-report.json').read_text(encoding='utf-8'))
        old=report['old_run']['read_after_gate'];new=report['new_run']
        assert old['run']['revision_id']==str(v1) and new['run']['revision_id']==report['v2']['receipt']['revision_id']
        assert old['collection']!=new['collection']
        old_ids={h['document_id'] for h in old['dense']['hits']};new_ids={h['document_id'] for h in new['dense']['hits']}
        assert str(docs['delete.md']) in old_ids and str(docs['delete.md']) not in new_ids
        assert 'oldblue' in json.dumps(old['opened']) and 'legacygreen' in json.dumps(old['opened'])
        assert 'newred' in json.dumps(new['opened']) and 'legacygreen' not in json.dumps(new['opened'])
        assert old['bm25']['retiredquasar'] and not new['bm25']['retiredquasar']
        assert 'oldblue' in json.dumps(old['bm25']['telescope']) and 'newred' in json.dumps(new['bm25']['telescope'])
        assert report['reader_pinned']['identity']['pid']!=os.getpid()
        report['two_process_assertions']='PASS: pin before publish; first search/open after publish; all Dense/source/BM25 scopes isolated'
        before=set(backend.client.list_collections())
        with change((keep,)) as owner:report['unchanged']=build(owner)
        with change((failed,)) as owner:report['all_failed']=build(owner)
        assert set(backend.client.list_collections())==before
        material('failed.md','# Failed\nFailed update approval now uses the repairedgold report.\n')
        with retry_failed(catalog,failed_batch,accept_input_changes=True) as owner:report['retry']=build(owner)
        assert report['retry']['summary']['published_updated']==1 and len(report['retry']['summary']['items'])==1
        with catalog._db.transaction() as db:
            visible=[UUID(r[0]) for r in db.execute('SELECT document_id FROM revision_members WHERE revision_id=?',
                       (str(catalog.get_library(kb.kb_id).current_revision_id),))]
        with change(deletes=visible) as owner:report['empty']=build(owner)
        lease=catalog.start_run(kb.kb_id,resolve_run(config,'qa'))
        report['empty_run']=inspect_run(catalog,lease,provider,backend)
        assert not report['empty_run']['dense']['hits'] and not report['empty_run']['source_search']['items']
        lease.finish(RunStatus.COMPLETED,'finished')
        empty_kb=catalog.create_library('R13 all failed initial import')
        material('all-invalid.md',b'\xff')
        with begin_changes(catalog,empty_kb.kb_id,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(root/'all-invalid.md')),)) as owner:
            report['unpublished_initial']=build(owner)
        try:catalog.start_run(empty_kb.kb_id,resolve_run(config,'qa'))
        except RagError as exc:
            report['unpublished_run_error']=exc.error.model_dump(mode='json')
            assert exc.error.code==ErrorCode.NOT_READY
        else:raise AssertionError('all-failed new library must not allow a run')
        report['worker']={k:v for k,v in provider.metadata.items() if k not in ('token','auth_token')}
        report['worker_handles']=[{'request_id':str(h.request.context.request_id),'finished':h.wait_finished(30),'phases':h.phases}
                                  for h in provider.handles.values()]
        with catalog._db.transaction() as db:
            report['catalog']={'integrity':db.execute('PRAGMA integrity_check').fetchall(),'foreign_keys':db.execute('PRAGMA foreign_key_check').fetchall(),
                'publications':db.execute('SELECT batch_id,revision_id,owner_epoch FROM publications').fetchall(),
                'pins':db.execute('SELECT run_id,revision_id,state FROM run_pins').fetchall()}
        assert report['catalog']['integrity']==[('ok',)] and report['catalog']['foreign_keys']==[]
        assert all(pin[2]=='released' for pin in report['catalog']['pins'])
        report['collections']=backend.client.list_collections()
        report['status']='PASS'
    except BaseException as exc:
        report.update(status='FAIL',error_type=type(exc).__name__,error=str(exc));raise
    finally:
        if child and child.poll() is None:
            # On failure keep the owned reader's provenance and let its bounded
            # gate deadline finish; independent cleanup owns any forced stop.
            report['reader_remaining']={'launcher_pid':child.pid,'birth':process_birth(child.pid)}
        if provider:provider.close()
        if backend:backend.close()
        report['finished_at']=now();write(args.report,report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('run','reader'));p.add_argument('--root',required=True)
    for name in ('cuda-python','model-cache','endpoint','report'):p.add_argument('--'+name)
    args=p.parse_args()
    try:
        reader(args) if args.mode=='reader' else main(args)
    except BaseException as exc:
        if args.report and not Path(args.report).exists():
            write(args.report,{'status':'FAIL','stage':'driver_setup','identity':identity(),
                               'error_type':type(exc).__name__,'error':str(exc),'finished_at':now()})
        raise
