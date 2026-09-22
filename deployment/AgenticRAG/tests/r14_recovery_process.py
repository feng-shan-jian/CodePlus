"""Real installed process killed at an observed production stage by R14 driver.

Only synchronization around real calls is injected. No GPU or Milvus response
is fabricated. The controller records and terminates this interpreter PID/birth.
"""

import argparse
from contextlib import contextmanager
import json,os,sys,time
from pathlib import Path
from uuid import UUID,uuid4

import agentic_rag
from agentic_rag.config import KnowledgeConfig,ProcessingSnapshot,WorkerExecutionConfig
from agentic_rag.ingestion import (InputSelection,select_inputs,begin_changes,capture_inputs,process_inputs,
    build_changes,inspect_recovery,continue_recovery)
from agentic_rag.ingestion.build import build_first_revision
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import LocalModelClient,FrozenTokenizer
from agentic_rag.models.identity import process_birth
from agentic_rag.storage import Catalog,OwnerToken
from agentic_rag.storage import inputs as input_store,processing as processing_store


def write(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.pending')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def main(args):
    root=Path(args.root);settings=json.loads((root/'settings.json').read_text(encoding='utf-8'))
    config=KnowledgeConfig.model_validate_json(json.dumps(settings['config']))
    worker=WorkerExecutionConfig.model_validate_json(json.dumps(settings['worker']))
    catalog=Catalog(config.storage.data_dir)
    report={'pid':os.getpid(),'birth':process_birth(os.getpid()),'executable':sys.executable,
            'package':agentic_rag.__file__,'stage':args.stage,'events':[],'calls':[],'started':time.time()}
    assert 'site-packages' in Path(agentic_rag.__file__).parts
    fired=False
    def gate(stage,**values):
        nonlocal fired
        if stage!=args.stage or fired:return
        fired=True
        value={**report,'gate':stage,'time':time.time(),**values}
        write(root/(args.attempt+'-gate.json'),value)
        while True:time.sleep(.05)
    def event(stage,value):
        report['events'].append({'stage':stage,'time':time.time(),**value})
        gate(stage,progress=value)
    original_input=input_store.record_result
    def captured(*a,**kw):
        result=original_input(*a,**kw)
        gate('snapshot',batch_id=str(a[1].token.batch_id));return result
    input_store.record_result=captured
    original_processing=processing_store.persist
    def parsed(*a,**kw):
        result=original_processing(*a,**kw)
        gate('parsed',batch_id=str(a[1].token.batch_id));return result
    processing_store.persist=parsed
    batch_id=settings.get('batch_id')
    original_transaction=catalog._db.transaction
    @contextmanager
    def transaction(*,write=False):
        terminal=False
        with original_transaction(write=write) as db:
            yield db
            if write and batch_id:
                terminal=db.execute("SELECT 1 FROM mutation_batches WHERE batch_id=? AND state IN ('PUBLISHED','COMPLETED_NO_CHANGE')",(batch_id,)).fetchone() is not None
                if terminal:gate('before_commit',batch_id=batch_id)
        if terminal:gate('after_commit',batch_id=batch_id)
    catalog._db.transaction=transaction
    provider=LocalModelClient(worker);backend=MilvusRevisionIndex(config.storage,catalog)
    original_create=backend.client.create_collection
    def created_without_receipt(*a,**kw):
        value=original_create(*a,**kw)
        observed=backend.client.describe_collection(a[0])
        gate('created_without_receipt',collection_name=a[0],physical_identity={
            k:observed[k] for k in ('collection_id','created_timestamp','description')})
        return value
    backend.client.create_collection=created_without_receipt
    submit=provider.submit_documents
    def tracked(items,profile,context):
        report['calls'].append({'request_id':str(context.request_id),'item_ids':[str(i.item_id) for i in items],
                                'profile':profile.identity,'time':time.time()})
        return submit(items,profile,context)
    provider.submit_documents=tracked
    tokenizer=FrozenTokenizer(config.embedding,args.model_cache)
    try:
        if args.mode=='start':
            selections=tuple(InputSelection(path=p) for p in settings['paths'])
            snapshot=ProcessingSnapshot.capture(uuid4(),config)
            owner=(catalog.begin_import(UUID(settings['kb_id']),snapshot,select_inputs(selections)) if settings['strict'] else
                   begin_changes(catalog,UUID(settings['kb_id']),snapshot,selections))
            batch_id=str(owner.token.batch_id);settings['batch_id']=batch_id
            write(root/'settings.json',settings)
            report['batch_id']=batch_id
            with owner:
                if settings['strict']:
                    capture_inputs(catalog,owner);process_inputs(catalog,owner,tokenizer)
                    result=build_first_revision(catalog,owner,provider,backend,observer=event)
                else:result=build_changes(catalog,owner,provider,backend,tokenizer,observer=event)
        else:
            report['batch_id']=batch_id
            plan=inspect_recovery(catalog,UUID(batch_id))
            if plan['state']!='WAITING_RECOVERY':result=plan
            else:
                token=OwnerToken(**{k:UUID(v) if k!='owner_epoch' else v for k,v in plan['expected'].items()})
                result=continue_recovery(catalog,token,runtime_factory=lambda frozen:(tokenizer,provider,backend),observer=event)
        report.update(status='PASS',result=result)
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:
        report['finished']=time.time()
        if provider.metadata:report['worker']={k:v for k,v in provider.metadata.items() if k not in ('token','auth_token')}
        provider.close();backend.close();write(root/(args.attempt+'-result.json'),report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('root','mode','stage','attempt','model-cache'):p.add_argument('--'+key,required=True)
    main(p.parse_args())
