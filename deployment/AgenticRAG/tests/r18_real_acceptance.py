"""Opt-in installed GPU/Milvus Rerank, no-truncation and no-fallback proof.

The second-batch fault closes the actual authenticated client during execution.
No scores, service response, model error or finish receipt are fabricated.
"""
import argparse
import json
from pathlib import Path
import runpy
import sys
import time
from uuid import UUID, uuid4

import agentic_rag
from agentic_rag.citations import CitationRegistry
from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import RagError, ErrorCode, Span, RunStatus
from agentic_rag.evidence import DeliveryGateway
from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import LocalModelClient, FrozenTokenizer
from agentic_rag.retrieval import RetrievalSearch
from agentic_rag.sources import SourceSession
from agentic_rag.storage import Catalog, readers

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
S=runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def main(args):
    root=Path(args.root).resolve();root.mkdir(parents=True)
    assert 'site-packages' in Path(agentic_rag.__file__).parts and sys.flags.isolated
    report={'status':'RUNNING','started':time.time(),'argv':sys.argv,'python':sys.executable,'package':agentic_rag.__file__}
    data=H['configuration'](root/'data',args.endpoint).model_dump(mode='json')
    data['retrieval'].update(route='bm25',rerank=True,rerank_candidates=3,context_chunks=3,context_tokens=20000)
    data['budgets']['qa'].update(searches=10,total_tokens=200000,duration_ms=600000,finish_reserve_tokens=1000)
    for profile in data['model_profiles']:
        if profile['capability']=='rerank':profile['limits']['max_batch_size']=1
    config=KnowledgeConfig.model_validate_json(json.dumps(data))
    worker=WorkerExecutionConfig(executable=args.cuda_python,model_cache=args.model_cache,runtime_dir=str(root/'worker'),idle_timeout_ms=1000)
    class ObservedClient(LocalModelClient):
        def __init__(self,*a):super().__init__(*a);self.calls=[];self.fail_at=None;self.fault_handle=None
        def submit_query(self,*a):self.calls.append('query');return super().submit_query(*a)
        def submit_rerank(self,*a):
            self.calls.append('rerank')
            handle=super().submit_rerank(*a)
            if self.calls.count('rerank')==self.fail_at:
                self.fault_handle=handle
                handle.wait_phase('inference_started')
                self.close(drain_timeout=0)
            return handle
    catalog=Catalog(root/'data');provider=ObservedClient(worker);backend=MilvusRevisionIndex(config.storage,catalog)
    kb=catalog.create_library('R18 real rerank').kb_id
    source=root/'source.md'
    source.write_text('# One\nThe blue telescope certificate is archived in the ocean registry.\n'
        '# Two\nThe red telescope certificate is archived in the hill registry.\n'
        '# Three\nThe amber telescope certificate is archived in the river registry.\n',encoding='utf-8')
    try:
        with begin_changes(catalog,kb,ProcessingSnapshot.capture(uuid4(),config),(InputSelection(path=str(source)),)) as owner:
            report['build']=build_changes(catalog,owner,provider,backend,FrozenTokenizer(config.embedding,args.model_cache))
        lease=catalog.start_run(kb,resolve_run(config,'qa'))
        try:
            session=SourceSession(catalog,lease,S['ControlledMeter'](),dense=RetrievalSearch(catalog,lease.run.run_id,provider,backend))
            first=session.search('Which telescope certificate is archived in the ocean registry?')
            report['success']=session.retrieval_trace(first.payload['call_id'])
            assert report['success']['rerank']['status']=='ok' and len(report['success']['rerank']['batches'])==3
            assert 'query' not in provider.calls and provider.calls==['rerank']*3
            assert first.payload['items'][0]['section_path']==['One']
            gateway=DeliveryGateway(session);permit,_,_=S['prepared'](gateway,first)
            evidence,=gateway.settle(permit,'confirmed')
            report['controlled_delivery']='gateway confirmation fixture only; not answer-model quality'
            with catalog._db.transaction() as db:before=db.execute('SELECT count(*) FROM source_candidates').fetchone()[0]
            calls=len(provider.calls)
            try:session.search('telescope '*2500)
            except RagError as exc:
                assert exc.error.code==ErrorCode.INPUT_TOO_LONG
                report['too_long']=session.retrieval_trace(exc.error.call_id)
            else:raise AssertionError('overlong complete input was accepted')
            assert len(provider.calls)==calls
            provider.fail_at=5
            try:session.search('Which telescope certificate is in the hill registry?')
            except RagError as exc:report['batch_failure']=session.retrieval_trace(exc.error.call_id)
            else:raise AssertionError('closed actual second-batch transport returned success')
            trace=report['batch_failure']
            assert trace['returned_ids']==[] and trace['rerank']['ranking']==[]
            assert [b['status'] for b in trace['rerank']['batches']]==['ok','error']
            with catalog._db.transaction() as db:assert db.execute('SELECT count(*) FROM source_candidates').fetchone()[0]==before
            item=first.payload['items'][0]
            report['old_citation']=CitationRegistry(session).save(evidence,(Span.model_validate(item['returned_spans'][0]),),(item['text'],))
            report['worker_identity']=provider.fault_handle.worker_identity
            report['finished_at_error']=provider.fault_handle.wait_finished(0)
            lease.finish(RunStatus.FAILED,'explicit_error')
            report['pin_at_error']=catalog.get_pin(lease.run.run_id).state
            assert provider.fault_handle.wait_finished(60)
            readers.flush_finished(catalog)
            end=time.monotonic()+5
            while catalog.get_pin(lease.run.run_id).state!='released' and time.monotonic()<end:
                catalog.maintain_indexes();time.sleep(.05)
            assert catalog.get_pin(lease.run.run_id).state=='released'
            report.update(completion_source=provider.fault_handle.completion_source,pin_after_completion='released',status='PASS')
        finally:lease.close()
    except BaseException as exc:
        report.update(status='FAIL',error=repr(exc));raise
    finally:
        provider.close();backend.close();report['finished']=time.time()
        Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','endpoint','cuda-python','model-cache','report'):parser.add_argument('--'+name,required=True)
    main(parser.parse_args())
