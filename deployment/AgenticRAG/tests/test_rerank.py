"""Real archives and frozen tokenizer, controlled model/index transports."""
import json
import os
from pathlib import Path
import runpy
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agentic_rag.capabilities import RerankResponse, RerankScore, ModelTimings
from agentic_rag.config import KnowledgeConfig, resolve_run
from agentic_rag.domain import ErrorCode, RagError, RunStatus, Span
from agentic_rag.evidence import DeliveryGateway
from agentic_rag.citations import CitationRegistry
from agentic_rag.models import FrozenTokenizer
from agentic_rag.models.identity import process_birth
from agentic_rag.retrieval import RetrievalSearch
from agentic_rag.sources import SourceSession
from agentic_rag.storage import readers, runs

R=runpy.run_path(str(Path(__file__).with_name('test_retrieval.py')))
S=R['S']
CACHE=R['H']['HELPER']['cache']()


def configured(config, route='hybrid', *, rerank=True, batch_size=1, **limits):
    data=config.model_dump(mode='json')
    data['retrieval'].update(route=route,rerank=rerank,rerank_candidates=3,context_chunks=3,context_tokens=20000)
    data['budgets']['qa'].update(searches=10,total_tokens=200000)
    for p in data['model_profiles']:
        if p['capability']=='rerank':p['limits'].update(max_batch_size=batch_size,**limits)
    return KnowledgeConfig.model_validate_json(json.dumps(data))


class Provider(R['Provider']):
    def __init__(self):
        super().__init__();self.rank_calls=[];self.config=SimpleNamespace(model_cache=CACHE);self.defect=None
    def rerank(self,query,items,profile,context):
        self.rank_calls.append((query,items,profile,context))
        if self.defect=='failure' and len(self.rank_calls)%3==2:
            raise RagError(ErrorCode.WORKER_UNAVAILABLE,'second batch failed',stage='test_worker')
        tokenizer=FrozenTokenizer(profile,CACHE)
        values=[RerankScore(item_id=i.item_id,score=.9 if 'Two' in (i.title or '') else .5,
            input_tokens=tokenizer.rerank(query,i.text,i.title or '').token_count) for i in items]
        values=sorted(values,key=lambda v:(-v.score,str(v.item_id)))
        if self.defect=='missing':values=values[:-1]
        if self.defect=='duplicate':values[-1]=values[0]
        if self.defect=='foreign':values[-1]=values[-1].model_copy(update={'item_id':uuid4()})
        if self.defect=='order':values=list(reversed(values))
        if self.defect=='tokens':values[0]=values[0].model_copy(update={'input_tokens':values[0].input_tokens+1})
        # model_construct intentionally represents a malformed wire response.
        return RerankResponse.model_construct(request_id=context.request_id,profile_fingerprint=profile.identity,
            results=tuple(values),timings=ModelTimings(queue_ms=0,load_ms=0,inference_ms=1))


@pytest.mark.parametrize('route',['dense','bm25','hybrid'])
@pytest.mark.parametrize('enabled',[False,True])
def test_routes_rerank_original_chunks_and_complete_stable_reassembly(tmp_path,route,enabled):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    model=Provider();calls=[];backend.search=R['transport'](rows,calls)
    config=configured(base,route,rerank=enabled)
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        found=RetrievalSearch(catalog,lease.run.run_id,model,backend).search('certificate',limit=2)
        assert len(model.calls)==(route!='bm25')
        assert len(model.rank_calls)==(3 if enabled else 0)
        assert len(found['hits'])==2 and found['trace']['parameters']['rerank']==enabled
        if not enabled:return
        inputs=[item for _,items,_,_ in model.rank_calls for item in items]
        assert {str(i.item_id) for i in inputs}=={r['chunk_id'] for r in rows}
        assert {i.title for i in inputs}=={'One','Two','Three'}
        assert {i.text for i in inputs}=={'# One\nTelescope ocean.\n','# Two\nCertificate blue.\n','# Three\nArchive bronze.\n'}
        assert found['hits'][0]['text']=='# Two\nCertificate blue.\n'
        rank=found['trace']['rerank']['ranking']
        assert rank[1]['chunk_id']<rank[2]['chunk_id']
        assert all(b['padded_tokens']==sum(b['input_tokens']) for b in found['trace']['rerank']['batches'])
        assert all('complete_input_sha256' in i for i in found['trace']['rerank']['inputs'])


def test_unique_input_cap_precedes_rerank_and_response_limit(tmp_path):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport']([rows[0],rows[0],*rows[1:]],[])
    data=configured(base).model_dump(mode='json');data['retrieval'].update(rerank_candidates=2,context_chunks=2)
    config=KnowledgeConfig.model_validate_json(json.dumps(data));model=Provider()
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        found=RetrievalSearch(catalog,lease.run.run_id,model,backend).search('certificate',limit=1)
        assert len(model.rank_calls)==2 and len(found['hits'])==1
        assert len({str(items[0].item_id) for _,items,_,_ in model.rank_calls})==2


class ThresholdProvider(Provider):
    def rerank(self,query,items,profile,context):
        response=super().rerank(query,items,profile,context)
        scores={item.item_id:{'One':0.0009,'Two':0.001,'Three':0.9}[item.title] for item in items}
        values=tuple(sorted((value.model_copy(update={'score':scores[value.item_id]})
                             for value in response.results), key=lambda v:(-v.score,str(v.item_id))))
        return response.model_copy(update={'results':values})


@pytest.mark.parametrize('route',['dense','bm25','hybrid'])
@pytest.mark.parametrize('threshold,expected',[(None,3),(0.0,3),(0.001,2),(1.0,0)])
def test_min_score_filters_complete_reranking_inclusively_without_backfill(tmp_path,route,threshold,expected):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport'](rows,[]);model=ThresholdProvider()
    base=configured(base,route,batch_size=2)
    config=base.model_copy(update={'retrieval':base.retrieval.model_copy(update={'min_score':threshold})})
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        search=RetrievalSearch(catalog,lease.run.run_id,model,backend)
        found=search.search('certificate',limit=3)
        assert len(found['hits'])==expected
        assert len(model.rank_calls)==2 and sum(len(items) for _,items,_,_ in model.rank_calls)==3
        assert [h['score'] for h in found['hits']]==[0.9,0.001,0.0009][:expected]
        trace=found['trace']
        assert len(trace['rerank']['ranking'])==3
        assert trace['status']==('ok' if expected else 'empty')
        if threshold is not None:
            removed=trace['rerank']['filter']['removed_ids']
            assert len(removed)==3-expected and trace['rerank']['filter']['min_score']==threshold
            assert set(removed).isdisjoint(trace['returned_ids'])
        else:
            assert 'filter' not in trace['rerank']
        assert len(search.search('certificate',limit=1)['hits'])==min(1,expected)


@pytest.mark.parametrize('route',['dense','bm25','hybrid'])
def test_auto_override_without_rerank_does_not_threshold_raw_scores(tmp_path,route):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport'](rows,[]);model=Provider()
    base=configured(base)
    config=base.model_copy(update={'retrieval':base.retrieval.model_copy(update={'mode':'auto','min_score':1.0})})
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        result=RetrievalSearch(catalog,lease.run.run_id,model,backend).search('q',strategy=route,rerank=False)
        assert len(result['hits'])==3 and model.rank_calls==[]
        assert 'filter' not in result['trace']['rerank']
        assert catalog.get_run(lease.run.run_id).resolved_config.retrieval.min_score==1.0


def test_all_filtered_source_search_creates_no_deliverable_candidates(tmp_path):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport'](rows,[]);model=ThresholdProvider()
    base=configured(base)
    config=base.model_copy(update={'retrieval':base.retrieval.model_copy(update={'min_score':1.0})})
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        session=SourceSession(catalog,lease,S['ControlledMeter'](),dense=RetrievalSearch(catalog,lease.run.run_id,model,backend))
        result=session.search('certificate')
        assert result.payload['items']==[]
        with catalog._db.transaction() as db:
            assert db.execute('SELECT count(*) FROM source_candidates').fetchone()[0]==0


def test_actual_padded_token_bound_can_make_smaller_batches_than_item_limit(tmp_path):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport'](rows,[]);model=Provider()
    config=configured(base,'bm25',batch_size=4,max_input_tokens=200,max_padded_tokens=200)
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        result=RetrievalSearch(catalog,lease.run.run_id,model,backend).search('certificate')
        assert [len(b['chunk_ids']) for b in result['trace']['rerank']['batches']]==[2,1]
        assert all(b['padded_tokens']<=200 for b in result['trace']['rerank']['batches'])


@pytest.mark.parametrize('defect',['missing','duplicate','foreign','order','tokens'])
def test_invalid_complete_response_ids_order_and_counts_fail(tmp_path,defect):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport'](rows,[]);model=Provider();model.defect=defect
    with catalog.start_run(kb,resolve_run(configured(base,batch_size=3),'qa')) as lease:
        search=RetrievalSearch(catalog,lease.run.run_id,model,backend)
        with pytest.raises(RagError) as caught:search.search('q')
        assert caught.value.error.code==ErrorCode.INVALID_RESPONSE
        trace=caught.value.retrieval_trace
        assert trace['returned_ids']==[] and trace['rerank']['ranking']==[]
        assert trace['rerank']['batches'][0]['status']=='error'


def test_actual_query_template_title_and_special_tokens_limit_before_dispatch(tmp_path):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport'](rows,[]);model=Provider()
    config=configured(base,'bm25')
    tokenizer=FrozenTokenizer(config.reranker,CACHE)
    assert tokenizer.rerank('q', 'Certificate blue.', 'Two').token_count > len(tokenizer.encode('Certificate blue.'))
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        with pytest.raises(RagError) as caught:
            RetrievalSearch(catalog,lease.run.run_id,model,backend).search('q '*2200)
        assert caught.value.error.code==ErrorCode.INPUT_TOO_LONG
        assert model.calls==model.rank_calls==[]
        assert caught.value.retrieval_trace['rerank']['status']=='error'


def test_later_batch_failure_keeps_old_confirmed_evidence_and_creates_no_candidates(tmp_path):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport'](rows,[]);model=Provider()
    with catalog.start_run(kb,resolve_run(configured(base),'qa')) as lease:
        session=SourceSession(catalog,lease,S['ControlledMeter'](),dense=RetrievalSearch(catalog,lease.run.run_id,model,backend))
        first=session.search('certificate');gateway=DeliveryGateway(session)
        permit,_,_=S['prepared'](gateway,first);evidence,=gateway.settle(permit,'confirmed')
        with catalog._db.transaction() as db:before=db.execute('SELECT count(*) FROM source_candidates').fetchone()
        model.defect='failure'
        with pytest.raises(RagError) as caught:session.search('second query')
        trace=session.retrieval_trace(caught.value.error.call_id)
        assert [b['status'] for b in trace['rerank']['batches']]==['ok','error']
        assert trace['returned_ids']==[] and trace['rerank']['ranking']==[]
        with catalog._db.transaction() as db:assert db.execute('SELECT count(*) FROM source_candidates').fetchone()==before
        item=first.payload['items'][0]
        assert CitationRegistry(session).save(evidence,(Span.model_validate(item['returned_spans'][0]),),(item['text'],))


@pytest.mark.parametrize('error',[ErrorCode.CANCELLED,ErrorCode.WORKER_UNAVAILABLE])
def test_result_error_or_client_close_needs_real_finished_receipt_before_reader_releases(tmp_path,error):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    backend.search=R['transport'](rows,[]);finished=threading.Event()
    class Handle:
        worker_identity={'pid':os.getpid(),'process_birth':process_birth(os.getpid()),'instance_id':str(uuid4()),'session_id':str(uuid4())}
        completion_source='worker_finished'
        def result(self):raise RagError(error,'result returned before execution finished',stage='controlled_transport')
        def wait_finished(self,timeout=None):return finished.wait(timeout)
    class Tracked(Provider):
        def submit_rerank(self,*args):return Handle()
        def close(self):pass
    model=Tracked();lease=catalog.start_run(kb,resolve_run(configured(base,'bm25'),'qa'))
    try:
        with pytest.raises(RagError):RetrievalSearch(catalog,lease.run.run_id,model,backend).search('q')
        model.close()
        with catalog._db.transaction() as db:
            assert db.execute("SELECT count(*) FROM index_readers WHERE state='pending' AND kind='model'").fetchone()==(1,)
        lease.finish(RunStatus.FAILED,'explicit_error')
        assert catalog.get_pin(lease.run.run_id).state=='active'
        finished.set()
        readers.flush_finished(catalog)
        # Production registered backends call this from bounded maintenance.
        # The transport-only fixture has no independently running collector.
        runs.settle(catalog._db, lease.run.run_id)
        with catalog._db.transaction() as db:
            assert db.execute("SELECT count(*) FROM index_readers WHERE state='pending'").fetchone()==(0,)
        assert catalog.get_pin(lease.run.run_id).state=='released'
    finally:finished.set();lease.close()
