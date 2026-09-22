"""Child run under genuine installed v7 and v8 packages for R14 migration."""

import argparse,hashlib,json,sys
from pathlib import Path
import runpy
from uuid import UUID,uuid4

import agentic_rag
import apsw
from agentic_rag.config import KnowledgeConfig,ProcessingSnapshot,WorkerExecutionConfig,resolve_run
from agentic_rag.domain import Span
from agentic_rag.ingestion import InputSelection,begin_changes,build_changes
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import LocalModelClient,FrozenTokenizer
from agentic_rag.models.identity import process_birth
from agentic_rag.storage import Catalog,publication
from agentic_rag.citations import CitationRegistry,open_citation
from agentic_rag.evidence import DeliveryGateway
from agentic_rag.sources import SourceSession
from agentic_rag.retrieval.dense import DenseSearch

H=runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def rows(db):
    tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {table:list(db.execute('SELECT * FROM '+table+' ORDER BY 1,2')) for table in tables}


def snapshot(catalog,ids):
    with catalog._db.transaction() as db:
        data=rows(db)
        value={'schema':db.pragma('user_version'),'migrations':data.pop('schema_migrations'),
            'row_hashes':{k:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest() for k,v in data.items()},
            'row_counts':{k:len(v) for k,v in data.items()},
            'triggers':dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")),
            'publication_fk':list(db.execute('PRAGMA foreign_key_list(publications)')),
            'integrity':list(db.execute('PRAGMA integrity_check')),'foreign_keys':list(db.execute('PRAGMA foreign_key_check'))}
    value['citation']=open_citation(catalog,UUID(ids['citation_id']))
    value['typed_run']=catalog.get_run(UUID(ids['run_id'])).model_dump(mode='json')
    value['archives_verified']={}
    for digest,size in data['archive_objects']:
        archived=catalog.archives.read(digest)
        assert len(archived)==size and hashlib.sha256(archived).hexdigest()==digest
        value['archives_verified'][digest]=size
    value['package']=agentic_rag.__file__;value['python']=sys.executable
    return value


def main(args):
    root=Path(args.root);settings=json.loads((root/'settings.json').read_text(encoding='utf-8'))
    config=KnowledgeConfig.model_validate_json(json.dumps(settings['config']));worker=WorkerExecutionConfig.model_validate_json(json.dumps(settings['worker']))
    if args.mode=='fault':
        from agentic_rag.storage import database
        real_files=database.files
        class Resource:
            def __init__(self,value):self.value=value
            def joinpath(self,name):
                value=self.value.joinpath(name)
                if name!='recovery.sql':return value
                class Fault:
                    def read_text(self,**kw):return value.read_text(**kw)+'\nSELECT * FROM r14_injected_absent_table;\n'
                return Fault()
        database.files=lambda package:Resource(real_files(package))
        error=None
        try:Catalog(root/'rollback-data')
        except Exception as exc:error=repr(exc)
        assert error and 'r14_injected_absent_table' in error
        db=apsw.Connection(str(root/'rollback-data/catalog.sqlite'));data=rows(db)
        result={'failure':error,'schema':db.pragma('user_version'),'row_hashes':{k:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest() for k,v in data.items() if k!='schema_migrations'},
                'integrity':list(db.execute('PRAGMA integrity_check')),'foreign_keys':list(db.execute('PRAGMA foreign_key_check'))}
        assert result['schema']==7 and 'current_candidates' not in data
        print(json.dumps(result));return
    catalog=Catalog(root/'data')
    if args.mode=='seed':
        with catalog._db.transaction() as db:assert db.pragma('user_version')==7
        kb=catalog.create_library('genuine installed R13 publication and pending')
        base=root/'baseline.md';base.write_text('# History\nThe archived original telescope approval remains blue.\n',encoding='utf-8')
        provider=LocalModelClient(worker);backend=MilvusRevisionIndex(config.storage,catalog);tokenizer=FrozenTokenizer(config.embedding,args.model_cache)
        def change(paths):return begin_changes(catalog,kb.kb_id,ProcessingSnapshot.capture(uuid4(),config),tuple(InputSelection(path=str(p)) for p in paths))
        try:
            with change([base]) as owner:published=build_changes(catalog,owner,provider,backend,tokenizer)
            lease=catalog.start_run(kb.kb_id,resolve_run(config,'qa'))
            session=SourceSession(catalog,lease,H['ControlledMeter'](),dense=DenseSearch(catalog,lease.run.run_id,provider,backend))
            found=session.search('archived telescope approval')
            opened=session.open(found.payload['items'][0]['source_ref'])
            gateway=DeliveryGateway(session);permit,wire,mappings=H['prepared'](gateway,opened)
            evidence,=gateway.settle(permit,'confirmed')
            item=opened.payload['items'][0];span=Span.model_validate(item['returned_spans'][0])
            saved=CitationRegistry(session).save(evidence,(span,),(item['text'],))
            base.write_text('# History\nUpdated pending telescope approval remains original to the batch.\n',encoding='utf-8')
            extra=root/'new-pending.md';extra.write_text('# Pending\nA meaningful complete encoded ocean checkpoint.\n',encoding='utf-8')
            pending=change([base,extra])
            class StopAfterValidation(Exception):pass
            def pause(stage,value):
                if stage=='validated':raise StopAfterValidation()
            try:
                with pending:build_changes(catalog,pending,provider,backend,tokenizer,observer=pause)
            except StopAfterValidation:pass
            else:raise AssertionError('pending seed unexpectedly published')
            ids={'kb_id':str(kb.kb_id),'published_batch':published['receipt']['batch_id'],'published_revision':published['receipt']['revision_id'],
                 'pending_batch':str(pending.token.batch_id),'run_id':str(lease.run.run_id),'pin_nonce':str(lease.pin.owner_nonce),
                 'citation_id':saved['citation']['citation_id'],'delivery':'controlled core settlement; no LLM network claim'}
            (root/'ids.json').write_text(json.dumps(ids),encoding='utf-8')
            base.unlink();extra.unlink()
        finally:provider.close();backend.close()
    ids=json.loads((root/'ids.json').read_text())
    if args.mode in ('seed','read'):
        result=snapshot(catalog,ids)
        if args.mode=='seed':result['seed_worker']={k:v for k,v in provider.metadata.items() if k not in ('token','auth_token')}
        print(json.dumps(result));return
    from agentic_rag.ingestion import inspect_recovery,continue_recovery
    from agentic_rag.storage import OwnerToken
    plan=inspect_recovery(catalog,UUID(ids['pending_batch']))
    assert all(item['checkpoint']=='encoded' for item in plan['items'])
    owner=OwnerToken(**{k:UUID(v) if k!='owner_epoch' else v for k,v in plan['expected'].items()})
    provider=LocalModelClient(worker);backend=MilvusRevisionIndex(config.storage,catalog);tokenizer=FrozenTokenizer(config.embedding,args.model_cache)
    try:
        old=publication.artifact(catalog,UUID(ids['published_revision']),published=True)
        assert not backend.has_ownership(old),'migration fabricated old physical ownership'
        old_query=backend.search(old,'telescope',field='sparse');assert old_query
        result=continue_recovery(catalog,owner,runtime_factory=lambda frozen:(tokenizer,provider,backend))
        assert not provider.handles,'genuine v7 complete documents were re-encoded'
        with catalog._db.transaction() as db:
            artifacts=list(db.execute('SELECT revision_id,owner_epoch FROM index_artifacts WHERE batch_id=? ORDER BY owner_epoch',(ids['pending_batch'],)))
            assert len(artifacts)==2
        assert open_citation(catalog,UUID(ids['citation_id']))==snapshot(catalog,ids)['citation']
        print(json.dumps({'status':'PASS','plan':plan,'result':result,'old_query':old_query,'old_physical_proof':False,
                          'model_request_count':len(provider.handles),'artifacts':artifacts,'after':snapshot(catalog,ids)}))
    finally:provider.close();backend.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('root','mode','model-cache'):p.add_argument('--'+key,required=True)
    main(p.parse_args())
