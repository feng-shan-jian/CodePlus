"""Opt-in installed GPU/Milvus route, evidence, fault and old-pin acceptance.

The transport hook changes actual service load state; it does not fabricate
results or raise a synthetic branch error. All resources belong to --root.
"""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
import time
from uuid import UUID, uuid4

import agentic_rag
from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import RagError, Span
from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
from agentic_rag.models import LocalModelClient, FrozenTokenizer
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.retrieval import RetrievalSearch
from agentic_rag.sources import SourceSession
from agentic_rag.storage import Catalog
from agentic_rag.evidence import DeliveryGateway
from agentic_rag.citations import CitationRegistry, open_citation

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
S=runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def route(config,name):
    data=config.model_dump(mode='json');data['retrieval'].update(route=name,rerank=False)
    data['budgets']['qa'].update(searches=20,opens=20,total_tokens=200000,duration_ms=600000,finish_reserve_tokens=1000)
    return KnowledgeConfig.model_validate_json(json.dumps(data))


def main(args):
    root=Path(args.root).resolve();root.mkdir(parents=True)
    assert 'site-packages' in Path(agentic_rag.__file__).parts and sys.flags.isolated
    report={'status':'RUNNING','started':time.time(),'python':sys.executable,'package':agentic_rag.__file__,
        'argv':sys.argv,'root':str(root),'fault':'actual release_collection between successful Dense and native BM25'}
    catalog=Catalog(root/'data')
    config=route(H['configuration'](root/'data',args.endpoint),'hybrid')
    worker=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,runtime_dir=str(root/'worker'),idle_timeout_ms=1000)
    provider=LocalModelClient(worker);backend=MilvusRevisionIndex(config.storage,catalog)
    source=root/'source.md';source.write_text('# Certificate\nThe blue telescope certificate is archived in the ocean registry.\n',encoding='utf-8')
    kb=catalog.create_library('R17 real routes').kb_id
    def build():
        with begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(source)),)) as owner:
            return build_changes(catalog,owner,provider,backend,FrozenTokenizer(config.embedding,args.model_cache))
    def counts():
        with catalog._db.transaction() as db:
            return {table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('source_candidates','delivered_evidence','saved_citations')}
    try:
        report['publication']=build()
        with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
            search=RetrievalSearch(catalog,lease.run.run_id,provider,backend)
            session=SourceSession(catalog,lease,S['ControlledMeter'](),dense=search)
            first=session.search('blue telescope certificate')
            assert first.payload['items']
            report['hybrid_success']=session.retrieval_trace(first.payload['call_id'])
            gateway=DeliveryGateway(session);permit,_,_=S['prepared'](gateway,first)
            evidence,=gateway.settle(permit,'confirmed')
            original_counts=counts()
            real_search=backend.search
            def release_after_dense(artifact,value,**kwargs):
                result=real_search(artifact,value,**kwargs)
                if kwargs.get('field','dense')=='dense':
                    backend.client.release_collection(artifact['collection_name'])
                return result
            backend.search=release_after_dense
            try:
                try:session.search('blue telescope certificate')
                except RagError as exc:report['branch_failure']=session.retrieval_trace(exc.error.call_id)
                else:raise AssertionError('unloaded sparse branch unexpectedly succeeded')
            finally:
                backend.search=real_search
                backend.client.load_collection(search.artifact['collection_name'])
            assert report['branch_failure']['branches']['dense']['status']=='ok'
            assert report['branch_failure']['branches']['bm25']['status']=='error'
            assert report['branch_failure']['status']=='error' and report['branch_failure']['returned_ids']==[]
            assert counts()==original_counts
            report['failure_evidence_counts']=counts()
            item=first.payload['items'][0]
            saved=CitationRegistry(session).save(evidence,(Span.model_validate(item['returned_spans'][0]),),(item['text'],))
            report['old_evidence_valid_after_failure']=saved
            before=search.search('blue certificate')
            source.write_text('# Replacement\nThe amber launch certificate belongs to the new observatory.\n',encoding='utf-8')
            changed=[]
            def publish_after_dense(artifact,value,**kwargs):
                result=real_search(artifact,value,**kwargs)
                if kwargs.get('field','dense')=='dense' and not changed:
                    changed.append(True)
                    backend.search=real_search
                    try:report['new_publication']=build()
                    finally:backend.search=publish_after_dense
                return result
            backend.search=publish_after_dense
            try:after=search.search('blue certificate')
            finally:backend.search=real_search
            assert catalog.get_library(kb).current_revision_id!=lease.run.revision_id
            assert before['hits']==after['hits']
            assert before['trace']['branches']['bm25']['candidates']==after['trace']['branches']['bm25']['candidates']
            report['old_pin_across_publication']={'before':before,'after':after,'current':str(catalog.get_library(kb).current_revision_id)}
        assert open_citation(catalog,UUID(saved['citation']['citation_id']))==saved
        bad=WorkerExecutionConfig(executable=str(root/'missing-python.exe'),model_cache=str(root/'missing-models'),
            runtime_dir=str(root/'unavailable-worker'),startup_timeout_ms=1000,idle_timeout_ms=1000)
        unavailable=LocalModelClient(bad)
        try:
            with catalog.start_run(kb,resolve_run(route(config,'bm25'),'qa')) as lease:
                search=RetrievalSearch(catalog,lease.run.run_id,unavailable,backend)
                found=search.search('amber certificate')
                assert found['hits'] and found['model_timings'] is None
                assert unavailable.sock is None and not unavailable.handles and unavailable.metadata is None
                report['bm25_without_model']={'result':found,'worker_connected':False,'handles':0,'executable_exists':Path(bad.executable).exists()}
                empty=search.search('!!! ??? ...')
                assert empty['hits']==[] and empty['trace']['status']=='empty'
                report['native_empty']=empty
            with catalog.start_run(kb,resolve_run(route(config,'dense'),'qa')) as lease:
                try:RetrievalSearch(catalog,lease.run.run_id,unavailable,backend).search('amber certificate')
                except Exception as exc:report['actual_model_unavailable']={'type':type(exc).__name__,'message':str(exc),'trace':getattr(exc,'retrieval_trace',None)}
                else:raise AssertionError('nonexistent worker interpreter accepted')
        finally:unavailable.close()
        with catalog._db.transaction() as db:
            report['source_calls']=list(db.execute('SELECT call_id,kind,status,error FROM source_calls'))
            report['integrity']=list(db.execute('PRAGMA integrity_check'))
            report['foreign_keys']=list(db.execute('PRAGMA foreign_key_check'))
        report.update(status='PASS',config=config.model_dump(mode='json'),kb_id=str(kb),
            worker={k:v for k,v in provider.metadata.items() if k not in ('token','auth_token')})
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:
        provider.close();backend.close()
        report['finished']=time.time()
        report['helper_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','cuda-python','model-cache','endpoint','report'):parser.add_argument('--'+name,required=True)
    main(parser.parse_args())
