"""Installed R14 real process/GPU/Milvus recovery acceptance (no mock results).

Each crash is an OS termination of the observed actual interpreter identity.
Transport gates delay genuine model completion frames and genuine SDK calls;
they do not substitute vectors, service responses or publication transactions.
Resources are retained for independent review and separately authorized cleanup.
"""

import argparse
from contextlib import contextmanager
import hashlib,json,os,signal,subprocess,sys,threading,time
from pathlib import Path
import runpy
from uuid import UUID,uuid4

import agentic_rag
from agentic_rag.capabilities import RequestContext
from agentic_rag.config import KnowledgeConfig,ProcessingSnapshot,WorkerExecutionConfig,resolve_run
from agentic_rag.domain import RagError,ErrorCode,RunStatus
from agentic_rag.ingestion import (InputSelection,begin_changes,build_changes,process_changes,
    inspect_recovery,continue_recovery,abandon_recovery,capture_inputs,process_inputs)
from agentic_rag.ingestion.encoding import outcomes,read_encoded,encode_documents
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import LocalModelClient,FrozenTokenizer
from agentic_rag.models.identity import process_birth
from agentic_rag.storage import Catalog,OwnerToken,publication,recovery

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))


def write(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.pending')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,default=list);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def identity():
    return {'pid':os.getpid(),'birth':process_birth(os.getpid()),'executable':sys.executable,
            'package':agentic_rag.__file__,'argv':sys.argv,'cwd':str(Path.cwd()),'time':time.time()}


def token(catalog,batch):
    with catalog._db.transaction() as db:
        row=db.execute('SELECT kb_id,owner_nonce,owner_epoch FROM mutation_batches WHERE batch_id=?',(str(batch),)).fetchone()
    return OwnerToken(catalog.store_id,UUID(row[0]),batch,UUID(row[1]),row[2])


def wait_file(path,child,seconds=180):
    end=time.monotonic()+seconds
    while not path.exists():
        if child.poll() is not None:raise RuntimeError(f'child exited {child.returncode} before {path.name}')
        if time.monotonic()>end:raise TimeoutError(str(path))
        time.sleep(.05)
    return json.loads(path.read_text(encoding='utf-8'))


def state(catalog,kb):
    with catalog._db.transaction() as db:
        run_ids=[UUID(r[0]) for r in db.execute('SELECT run_id FROM runs')]
        value={'schema':db.pragma('user_version'),'current':str(catalog.get_library(kb).current_revision_id),
            'artifacts':list(db.execute('SELECT artifact_id,revision_id,batch_id,collection_name,owner_epoch,state FROM index_artifacts ORDER BY owner_epoch')),
            'publications':list(db.execute('SELECT * FROM publications ORDER BY owner_epoch')),
            'proofs':list(db.execute('SELECT * FROM artifact_ownership_proofs')),
            'integrity':list(db.execute('PRAGMA integrity_check')),'foreign_keys':list(db.execute('PRAGMA foreign_key_check'))}
    value['typed_runs']=[catalog.get_run(run).model_dump(mode='json') for run in run_ids]
    return value


class Suite:
    def __init__(self,args):
        self.args=args;self.root=Path(args.root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        if (self.root/'started.json').exists():raise RuntimeError('use a new real acceptance root')
        write(self.root/'started.json',identity())
        self.worker=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,
            runtime_dir=str(self.root/'worker'),idle_timeout_ms=10000)
        self.report={'status':'RUNNING','identity':identity(),'worker_config':self.worker.model_dump(mode='json'),
                     'cases':{},'processes':[],'workers':[],'scope':'real GPU/Milvus, synthetic named sources; no semantic quality claim'}
        self.save()

    def save(self):write(self.args.report,self.report)

    def config(self,root):
        value=H['configuration'](root/'data',self.args.endpoint).model_dump(mode='json')
        value['storage']['namespace']='r14_acceptance'
        return KnowledgeConfig.model_validate_json(json.dumps(value))

    @contextmanager
    def runtime(self,catalog,config):
        provider=LocalModelClient(self.worker);backend=MilvusRevisionIndex(config.storage,catalog)
        try:yield FrozenTokenizer(config.embedding,self.args.model_cache),provider,backend
        finally:
            if provider.metadata:self.report['workers'].append({k:v for k,v in provider.metadata.items() if k not in ('token','auth_token')})
            provider.close();backend.close()

    def build(self,catalog,kb,config,paths):
        with begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),tuple(InputSelection(path=str(p)) for p in paths)) as owner:
            with self.runtime(catalog,config) as (tok,provider,backend):
                return build_changes(catalog,owner,provider,backend,tok)

    def spawn(self,root,mode,stage,attempt):
        argv=[sys.executable,'-I','-B',str(Path(__file__).with_name('r14_recovery_process.py')),
              '--root',str(root),'--mode',mode,'--stage',stage,'--attempt',attempt,'--model-cache',self.args.model_cache]
        log=(root/(attempt+'.log')).open('w',encoding='utf-8')
        child=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'})
        self.report['processes'].append({'argv':argv,'launcher_pid':child.pid,'log':str(root/(attempt+'.log'))})
        return child,log

    def crash(self,root,mode,stage,attempt):
        child,log=self.spawn(root,mode,stage,attempt)
        try:
            observed=wait_file(root/(attempt+'-gate.json'),child)
            assert process_birth(observed['pid'])==observed['birth']
            assert observed['pid']!=os.getpid()
            os.kill(observed['pid'],signal.SIGTERM)
            child.wait(timeout=30)
            assert process_birth(observed['pid'])!=observed['birth']
            record={'actual':observed,'launcher_pid':child.pid,'exit_code':child.returncode,'terminated_at':time.time()}
            self.report['processes'][-1].update(record);self.save();return record
        finally:log.close()

    def finish(self,root,attempt='complete'):
        child,log=self.spawn(root,'resume','none',attempt)
        try:
            child.wait(timeout=240)
            value=json.loads((root/(attempt+'-result.json')).read_text(encoding='utf-8'))
            assert child.returncode==0 and value['status']=='PASS',value
            self.report['processes'][-1].update(actual=value,exit_code=child.returncode)
            return value
        finally:log.close()

    def crash_case(self,name,stage,*,strict=False,twice=False,no_change=False):
        root=self.root/name;root.mkdir();config=self.config(root);catalog=Catalog(root/'data')
        kb=catalog.create_library(name).kb_id
        base=root/'base.md';base.write_text('# Baseline\nThe original visible telescope is blue.\n',encoding='utf-8')
        old=None if strict else self.build(catalog,kb,config,[base])
        incoming=root/'incoming';incoming.mkdir()
        paths=[base] if no_change else [incoming]
        if not no_change:
            (incoming/'a.md').write_text('# First\nFrozen original telescope alpha.\n',encoding='utf-8')
            (incoming/'b.md').write_text('# Second\nFrozen original ocean beta.\n',encoding='utf-8')
        settings={'config':config.model_dump(mode='json'),'worker':self.worker.model_dump(mode='json'),
                  'kb_id':str(kb),'paths':list(map(str,paths)),'strict':strict}
        write(root/'settings.json',settings)
        first=self.crash(root,'start',stage,'crash-one')
        settings=json.loads((root/'settings.json').read_text());batch=UUID(settings['batch_id'])
        before=state(catalog,kb);original_token=token(catalog,batch)
        terminal=recovery.terminal(catalog,batch)
        calls_before=[c for c in first['actual']['calls']]
        case={'first':first,'before_inspect':before,'batch_id':str(batch),'old_receipt':old['receipt'] if old else None}
        if stage=='created_without_receipt':
            with catalog._db.transaction() as db:
                unknown=db.execute('SELECT artifact_id,collection_name FROM index_artifacts WHERE batch_id=?',(str(batch),)).fetchone()
                assert unknown and not db.execute('SELECT 1 FROM artifact_ownership_proofs WHERE artifact_id=?',(unknown[0],)).fetchone()
            assert unknown[1]==first['actual']['collection_name']
            case['unreceipted_physical_identity']=first['actual']['physical_identity']
        if terminal is None:
            assert catalog.get_library(kb).current_revision_id==(UUID(old['receipt']['revision_id']) if old else None)
            # Opening Catalog and reading status has performed no model calls,
            # no automatic recovery and no extra publication.
            assert Catalog(root/'data').get_batch(batch).owner_epoch==original_token.owner_epoch
            changed=config.model_dump(mode='json')
            for profile in changed['model_profiles']:
                if profile['name']==changed['models']['embedding']:
                    profile['instruction']='Later default instruction; original batch must remain frozen.'
            changed['processing']['chunker']['max_tokens']=256
            plan=inspect_recovery(catalog,batch,current_config=KnowledgeConfig.model_validate_json(json.dumps(changed)))
            assert plan['default_differences'] and plan['frozen_snapshot']['resolved_config']==settings['config']
            case['plan']=plan
            complete_ids={v['chunk_id'] for raw in catalog.get_input_items(batch)
                          for v in (read_encoded(catalog,batch,raw) or [])}
            if old:
                with MilvusContext(config,catalog) as backend:
                    lease=catalog.start_run(kb,resolve_run(config,'qa'))
                    hits=backend.search(publication.artifact(catalog,lease.run.revision_id,published=True),'telescope',field='sparse')
                    assert hits;case['old_query_during_wait']={'revision':str(lease.run.revision_id),'hits':hits}
                    lease.finish(RunStatus.COMPLETED,'finished')
            try:begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(base)),))
            except RagError as exc:assert exc.error.code==ErrorCode.LIBRARY_BUSY;case['same_library_blocked']=exc.error.model_dump(mode='json')
            else:raise AssertionError('pending batch did not block new import')
            # Move/delete/change current sources and add an extra directory file.
            if not no_change:
                for index,path in enumerate(sorted(incoming.glob('*.md'))):
                    if index==0:path.write_text('NEW DEFAULT SOURCE SHOULD NOT APPEAR',encoding='utf-8')
                    else:path.rename(root/'moved-later.md')
                (incoming/'extra.md').write_text('new directory entry must not enter old manifest',encoding='utf-8')
            if stage=='parsed':
                def unavailable(frozen):return FrozenTokenizer(frozen.embedding,str(root/'unavailable-model-cache'))
                try:continue_recovery(catalog,token(catalog,batch),runtime_factory=unavailable)
                except RagError as exc:case['original_environment_unavailable']=exc.error.model_dump(mode='json')
                else:raise AssertionError('absent original model cache accepted')
                assert catalog.get_library(kb).pending_mutation_id==batch
            if twice:
                other=catalog.create_library('other library during wait').kb_id
                case['other_library_result']=self.build(catalog,other,config,[base])
                second=self.crash(root,'resume','inserted','crash-two');case['second']=second
                assert not second['actual']['calls'],'complete document re-encoded on second generation'
            finished=self.finish(root);case['finished']=finished
            assert not (complete_ids & {i for call in finished['calls'] for i in call['item_ids']}),'valid complete document was encoded again'
            result=finished['result']
            if no_change:
                assert result['receipt'] is None and result['summary']['state']=='COMPLETED_NO_CHANGE'
                assert catalog.get_library(kb).current_revision_id==UUID(old['receipt']['revision_id'])
            else:
                assert result['receipt'] and result['receipt']['batch_id']==str(batch)
                assert len(catalog.get_input_items(batch))==2
                with catalog._db.transaction() as db:
                    published_text='\n'.join(catalog.archives.read(r[0]).decode() for r in db.execute('SELECT v.parsed_hash FROM revision_members m JOIN document_versions v ON v.document_version_id=m.document_version_id WHERE m.revision_id=?',(result['receipt']['revision_id'],)))
                assert 'NEW DEFAULT SOURCE' not in published_text and 'new directory entry' not in published_text
                case['published_archived_text']=published_text
            generations=[a for a in state(catalog,kb)['artifacts'] if a[2]==str(batch)]
            if twice:assert len(generations)==3 and len({a[3] for a in generations})==3
            case['generation_count']=len(generations)
            if stage=='created_without_receipt':
                assert len(generations)==2 and len({a[3] for a in generations})==2 and not finished['calls']
                with MilvusContext(config,catalog) as backend:
                    observed=backend.client.describe_collection(unknown[1])
                    assert all(observed[k]==v for k,v in case['unreceipted_physical_identity'].items())
                case['unknown_original_collection_retained']=True
        else:
            # Lost successful response: neither factory nor service is consulted.
            result=continue_recovery(catalog,original_token,runtime_factory=lambda _:(_ for _ in ()).throw(AssertionError('terminal runtime load')))
            assert result['receipt']==terminal['receipt'] and result['summary']==terminal['summary']
            newer=root/'newer.md';newer.write_text('# Later\nA later visible publication.\n',encoding='utf-8')
            latest=self.build(catalog,kb,config,[newer])
            repeat=continue_recovery(catalog,original_token,runtime_factory=lambda _:(_ for _ in ()).throw(AssertionError('old terminal runtime')))
            abandoned=abandon_recovery(catalog,original_token,backend=object())
            assert repeat['receipt']==result['receipt'] and abandoned['receipt']==result['receipt']
            assert catalog.get_library(kb).current_revision_id==UUID(latest['receipt']['revision_id'])
            case.update(terminal_replay=result,newer=latest,old_replay_after_newer=repeat)
        case['after']=state(catalog,kb);assert case['after']['integrity']==[('ok',)] and case['after']['foreign_keys']==[]
        case['status']='PASS';self.report['cases'][name]=case;self.save()

    def late_model(self):
        root=self.root/'late-model';root.mkdir();config=self.config(root);catalog=Catalog(root/'data');kb=catalog.create_library('late GPU').kb_id
        source=root/'long.md';source.write_text('# GPU\n'+('telescope approval '*450)+'\n',encoding='utf-8')
        owner=begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(source)),))
        with self.runtime(catalog,config) as (tok,provider,backend):
            capture_inputs(catalog,owner);process_inputs(catalog,owner,tok)
            reached,release=threading.Event(),threading.Event();original_receive=provider._receive
            frame={};failures=[]
            def delayed(*a,**kw):
                message=original_receive(*a,**kw)
                if message.get('type')=='finished' and not reached.is_set():
                    frame.update(request_id=message['request_id'],actual_execution_finished=message['execution_finished'],
                                 response_profile=message['response']['profile_fingerprint'] if message['response'] else None,time=time.time())
                    reached.set();assert release.wait(60)
                return message
            provider._receive=delayed
            def encoding():
                try:encode_documents(catalog,owner,provider)
                except BaseException as exc:failures.append(exc)
            task=threading.Thread(target=encoding);task.start();assert reached.wait(120)
            handle=next(iter(provider.handles.values()));assert not handle.execution_finished
            old_token=owner.token;owner.close();catalog.identify_interrupted(kb)
            with catalog.resume_mutation(old_token) as renewed:
                assert not handle.execution_finished
                pending=recovery.unresolved_io(catalog,old_token.batch_id);assert pending
                release.set();task.join(60);assert not task.is_alive()
                assert handle.wait_finished(30) and failures and isinstance(failures[0],RagError)
                assert outcomes(catalog,old_token.batch_id)=={}
                provider._receive=original_receive
                result=build_changes(catalog,renewed,provider,backend,tok)
            value={'status':'PASS','transport':'actual finished GPU frame delayed before RequestHandle delivery',
                'actual_frame':frame,'old_token':str(old_token),'new_epoch':renewed.token.owner_epoch,
                'pending_before_release':pending,'late_error':str(failures[0]),'handle_finished':handle.execution_finished,
                'completion_source':handle.completion_source,'result':result,'after':state(catalog,kb)}
        self.report['cases']['late_model']=value;self.save()

    def late_insert(self):
        root=self.root/'late-insert';root.mkdir();config=self.config(root);catalog=Catalog(root/'data');kb=catalog.create_library('late SDK').kb_id
        source=root/'one.md';source.write_text('# SDK\nActual delayed SDK writes old candidate only.\n',encoding='utf-8')
        owner=begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(source)),))
        with self.runtime(catalog,config) as (tok,provider,backend):
            process_changes(catalog,owner,tok,provider)
            old,old_artifact=publication.register(catalog,owner);backend.create(old_artifact,owner)
            vectors={v['chunk_id']:v for raw in catalog.get_input_items(owner.token.batch_id) for v in read_encoded(catalog,owner.token.batch_id,raw)}
            old_rows=[{**r,'vector_hash':vectors[r['chunk_id']]['vector_hash'],'dense':vectors[r['chunk_id']]['dense']} for r in old.rows]
            original_insert=backend.client.insert;reached,release=threading.Event(),threading.Event();result=[]
            def delayed(name,rows,**kw):
                if name==old_artifact['collection_name']:
                    reached.set();assert release.wait(90)
                return original_insert(name,rows,**kw)
            backend.client.insert=delayed
            task=threading.Thread(target=lambda:result.append(backend.insert(old_artifact,old_rows,owner)))
            task.start();assert reached.wait(30)
            old_token=owner.token;owner.close();catalog.identify_interrupted(kb)
            with catalog.resume_mutation(old_token) as renewed:
                fresh,artifact=publication.register(catalog,renewed);backend.create(artifact,renewed)
                rows=[{**r,'vector_hash':vectors[r['chunk_id']]['vector_hash'],'dense':vectors[r['chunk_id']]['dense']} for r in fresh.rows]
                backend.insert(artifact,rows,renewed)
                expected=[{k:v for k,v in r.items() if k!='dense'} for r in rows]
                publication.record_encoded(catalog,renewed,fresh.revision_id,expected)
                backend.finalize(artifact,len(rows),renewed);publication.validate(catalog,renewed,fresh.revision_id,backend)
                receipt=publication.publish(catalog,renewed,fresh.revision_id)
                before=backend.read_vectors(publication.artifact(catalog,fresh.revision_id,published=True),expected)
                release.set();task.join(60);assert not task.is_alive() and result[0]['insert_count']==len(old_rows)
                backend.client.flush(old_artifact['collection_name'],timeout=30)
                old_count=backend.client.get_collection_stats(old_artifact['collection_name'])
                assert int(old_count['row_count'])==len(old_rows)
                after=backend.read_vectors(publication.artifact(catalog,fresh.revision_id,published=True),expected)
                assert before==after and catalog.get_library(kb).current_revision_id==fresh.revision_id
                try:publication.publish(catalog,owner,old.revision_id)
                except RagError as exc:late_error=str(exc)
                else:raise AssertionError('old SDK owner republished')
            value={'status':'PASS','gate':'after owner/physical proof and durable IO receipt, before actual SDK insert send',
                'old_collection':old_artifact['collection_name'],'new_collection':artifact['collection_name'],
                'old_actual_insert':result[0],'old_actual_rows':old_count,'new_rows_unchanged':before==after,
                'current':str(fresh.revision_id),'receipt':receipt,'late_publish_error':late_error,'after':state(catalog,kb)}
        self.report['cases']['late_insert']=value;self.save()


class MilvusContext:
    def __init__(self,config,catalog):self.value=MilvusRevisionIndex(config.storage,catalog)
    def __enter__(self):return self.value
    def __exit__(self,*_):self.value.close()


def main(args):
    suite=Suite(args)
    suite.report['case_selection']=args.case
    try:
        assert 'site-packages' in Path(agentic_rag.__file__).parts
        if args.case=='late_insert':
            suite.late_insert();suite.report['status']='PASS';return
        for name,stage,kw in [('snapshot','snapshot',{}),('parsed','parsed',{}),('encoding_uncommitted','encoding_response',{}),
                ('encoded_document','file_terminal',{}),('indexing_twice','created',{'twice':True}),
                ('before_publish_commit','before_commit',{}),('after_publish_commit','after_commit',{}),
                ('before_no_change_commit','before_commit',{'no_change':True}),
                ('after_no_change_commit','after_commit',{'no_change':True}),('strict_first','file_terminal',{'strict':True}),
                ('unreceipted_create','created_without_receipt',{})]:
            suite.crash_case(name,stage,**kw)
        suite.late_model();suite.late_insert();suite.report['status']='PASS'
    except BaseException as exc:suite.report.update(status='FAIL',error=repr(exc));raise
    finally:suite.report['finished']=time.time();suite.save()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('root','cuda-python','model-cache','endpoint','report'):parser.add_argument('--'+key,required=True)
    parser.add_argument('--case',choices=('all','late_insert'),default='all')
    main(parser.parse_args())
