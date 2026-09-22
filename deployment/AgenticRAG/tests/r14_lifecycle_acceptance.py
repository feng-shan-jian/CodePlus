"""Real Milvus physical ownership/cleanup and two-interpreter recovery races."""

import argparse,json,os,subprocess,sys,time
from pathlib import Path
import runpy
from uuid import UUID,uuid4

from agentic_rag.config import ProcessingSnapshot
from agentic_rag.domain import RagError,ErrorCode
from agentic_rag.ingestion import InputSelection,begin_changes,process_changes,capture_inputs,inspect_recovery,continue_recovery,abandon_recovery
from agentic_rag.storage import Catalog,publication,recovery

H=runpy.run_path(str(Path(__file__).with_name('r14_real_acceptance.py')))
Suite,write,state=H['Suite'],H['write'],H['state']


def physical(suite,name):
    root=suite.root/name;root.mkdir();config=suite.config(root);catalog=Catalog(root/'data');kb=catalog.create_library(name).kb_id
    source=root/'physical.md';source.write_text('# Physical ownership\nA real GPU vector and isolated physical candidate.\n',encoding='utf-8')
    case={'fault':name}
    with suite.runtime(catalog,config) as (tok,provider,backend):
        owner=begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(source)),))
        process_changes(catalog,owner,tok,provider);prepared,artifact=publication.register(catalog,owner);collection=artifact['collection_name']
        if name=='collision':
            backend.client.create_collection(collection,dimension=2,description='R14 explicitly test-owned foreign collision',timeout=30)
            foreign=backend.client.describe_collection(collection);case['foreign_before']=foreign
            try:backend.create(artifact,owner)
            except RagError as exc:case['creation_error']=exc.error.model_dump(mode='json')
            else:raise AssertionError('same-name preexisting collection was overwritten')
        elif name=='create_response_unknown':
            create=backend.client.create_collection
            def response_lost(*a,**kw):
                result=create(*a,**kw);raise ConnectionError('controlled loss after genuine successful create')
            backend.client.create_collection=response_lost
            try:backend.create(artifact,owner)
            except ConnectionError as exc:case['creation_error']=str(exc)
            else:raise AssertionError('uncertain response unexpectedly accepted')
            backend.client.create_collection=create
            foreign=backend.client.describe_collection(collection);case['actual_created']=foreign
            assert not backend.has_ownership(artifact)
        else:
            backend.create(artifact,owner);created=backend.client.describe_collection(collection);case['owned_before']=created
            assert backend.has_ownership(artifact)
            try:backend.drop_owned(artifact)
            except RagError as exc:case['unclaimed_drop_rejected']=exc.error.model_dump(mode='json')
            else:raise AssertionError('unclaimed candidate dropped')
            if name=='physical_replacement':
                # Emulates an external administrator only inside the dedicated
                # project: copy the marker but recreate a different physical ID.
                backend.client.drop_collection(collection)
                backend.client.create_collection(collection,dimension=2,description=created['description'])
                foreign=backend.client.describe_collection(collection);case['replacement']=foreign
                assert foreign['collection_id']!=created['collection_id']
        batch=owner.token.batch_id;owner.close();catalog.identify_interrupted(kb)
        abandon_recovery(catalog,H['token'](catalog,batch))
        if name=='service_cleanup_retry':
            def compose(arguments):
                argv=['docker','compose','-f',suite.args.compose,'-p',suite.args.project,*arguments]
                p=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8');case.setdefault('service_commands',[]).append({'argv':argv,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr});p.check_returncode()
            compose(['stop','standalone']);backend.timeout=.25
            try:
                repeated=abandon_recovery(catalog,owner.token,backend=backend)
                replay=continue_recovery(catalog,owner.token,runtime_factory=lambda _:(_ for _ in ()).throw(AssertionError('terminal factory called')))
                assert repeated['summary']==replay['summary'] and repeated['state']=='ABANDONED'
                case['abandon_and_continue_replayed_while_service_stopped']=repeated
                failure=recovery.cleanup_candidates(catalog,batch,backend)
                assert failure[0]['state']=='retained';case['service_failure']=failure
            finally:compose(['up','-d','--wait','--wait-timeout','180','standalone'])
            backend.timeout=30
            result=recovery.cleanup_candidates(catalog,batch,backend)
            assert result[0]['state']=='reclaimed' and not backend.client.has_collection(collection)
        else:
            result=recovery.cleanup_candidates(catalog,batch,backend)
            assert result[0]['state']=='retained' and backend.client.has_collection(collection)
            observed=backend.client.describe_collection(collection)
            assert observed['collection_id']==foreign['collection_id'];case['retained_actual']=observed
        case.update(status='PASS',cleanup=result,after=state(catalog,kb))
    suite.report['cases'][name]=case;suite.save()


def compete(suite,actions):
    name='race-'+'-'.join(actions);root=suite.root/name;root.mkdir();config=suite.config(root);catalog=Catalog(root/'data');kb=catalog.create_library(name).kb_id
    source=root/'race.md';source.write_text('# Race\nOnly one recovery generation owns this document.\n',encoding='utf-8')
    owner=begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(source)),));capture_inputs(catalog,owner)
    batch=owner.token.batch_id;owner.close();plan=inspect_recovery(catalog,batch)
    write(root/'settings.json',{'config':config.model_dump(mode='json'),'worker':suite.worker.model_dump(mode='json'),'expected':plan['expected']})
    processes=[];logs=[];case={'actions':actions,'before':state(catalog,kb)}
    try:
        for index,action in enumerate(actions):
            argv=[sys.executable,'-I','-B',str(Path(__file__).with_name('r14_compete_process.py')),str(root),str(index),action]
            log=(root/(str(index)+'.log')).open('w',encoding='utf-8');logs.append(log)
            child=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT);processes.append(child)
            suite.report['processes'].append({'argv':argv,'launcher_pid':child.pid})
        ready=[H['wait_file'](root/(str(i)+'-ready.json'),p) for i,p in enumerate(processes)];write(root/'start.json',{})
        end=time.monotonic()+60;winner=None
        while winner is None:
            for index in range(2):
                if (root/(str(index)+'-acquired.json')).exists():winner=index;break
            if time.monotonic()>end:raise TimeoutError('no race winner')
            time.sleep(.02)
        loser=1-winner;rejected=H['wait_file'](root/(str(loser)+'-result.json'),processes[loser])
        assert rejected['status']=='rejected' and rejected['error']['code']==ErrorCode.LIBRARY_BUSY.value
        assert not (root/(str(loser)+'-acquired.json')).exists()
        write(root/'release.json',{})
        for p in processes:p.wait(timeout=180);assert p.returncode==0
        results=[json.loads((root/(str(i)+'-result.json')).read_text()) for i in range(2)]
        assert results[winner]['status']=='completed'
        assert catalog.get_library(kb).pending_mutation_id is None
        with catalog._db.transaction() as db:
            count=db.execute('SELECT count(*) FROM mutation_executions WHERE batch_id=?',(str(batch),)).fetchone()[0]
        assert count==2  # original owner and exactly one recovered owner
        case.update(status='PASS',ready=ready,winner=winner,results=results,execution_count=count,after=state(catalog,kb))
    finally:
        for log in logs:log.close()
    suite.report['cases'][name]=case;suite.save()


def main(args):
    suite=Suite(args)
    try:
        for name in ('collision','create_response_unknown','physical_replacement','service_cleanup_retry'):physical(suite,name)
        compete(suite,['continue','continue']);compete(suite,['continue','abandon'])
        suite.report['status']='PASS'
    except BaseException as exc:suite.report.update(status='FAIL',error=repr(exc));raise
    finally:suite.report['finished']=time.time();suite.save()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('root','cuda-python','model-cache','endpoint','report','compose','project'):p.add_argument('--'+key,required=True)
    main(p.parse_args())
