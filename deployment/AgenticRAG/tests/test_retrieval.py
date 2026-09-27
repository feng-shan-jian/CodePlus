"""Real run/publication/source state; model and index transports are controlled."""
import json
from pathlib import Path
import runpy
import time
from uuid import uuid4

import pytest

from agentic_rag.capabilities import EmbeddingResponse, EmbeddingResult, ModelTimings
from agentic_rag.config import KnowledgeConfig, resolve_run
from agentic_rag.domain import RagError
from agentic_rag.evidence import DeliveryGateway
from agentic_rag.citations import open_citation
from agentic_rag.retrieval import DenseSearch, RetrievalSearch, reciprocal_rank_fusion
from agentic_rag.sources import SourceSession
from agentic_rag.storage import Catalog, publication

H=runpy.run_path(str(Path(__file__).with_name('publication_support.py')))
S=runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def configured(config, route='hybrid', **kwargs):
    data=config.model_dump(mode='json')
    data['retrieval'].update(route=route,rerank=False,**kwargs)
    return KnowledgeConfig.model_validate_json(json.dumps(data))


class Provider:
    owner_id=uuid4()
    def __init__(self):self.calls=[]
    def embed_query(self,item,profile,context):
        self.calls.append((item.text,profile.identity))
        return EmbeddingResponse(request_id=context.request_id,profile_fingerprint=profile.identity,
            results=(EmbeddingResult(item_id=item.item_id,vector=tuple(H['VECTOR']),input_tokens=5),),
            timings=ModelTimings(queue_ms=0,load_ms=0,inference_ms=0))


def published(tmp_path):
    catalog=Catalog(tmp_path/'data')
    owner,config=H['processed'](catalog,tmp_path/'doc.md',text='# One\nTelescope ocean.\n# Two\nCertificate blue.\n# Three\nArchive bronze.\n')
    prepared,artifact,rows,backend,_=H['unit_ready'](catalog,owner,config)
    publication.publish(catalog,owner,prepared.revision_id);owner.close()
    assert len(rows)==3
    return catalog,owner.token.kb_id,config,rows,backend


def transport(rows, calls, *, empty=(), fail=None, after_dense=None):
    def search(artifact,value,*,field='dense',limit=10,**kwargs):
        calls.append({'artifact':artifact,'field':field,'value':value,'limit':limit,**kwargs})
        if field==fail:raise RuntimeError('injected '+field+' failure')
        if field in empty:return []
        ordered=rows if field=='dense' else list(reversed(rows))
        if field=='dense' and after_dense:after_dense()
        return [{'id':r['chunk_id'],'distance':(.9-i*.1 if field=='dense' else 80-i),'entity':r}
                for i,r in enumerate(ordered[:limit])]
    return search


def test_rrf_recomputes_original_ranks_duplicates_missing_and_ties():
    def hits(*ids):return [{'chunk_id':i,'score':999} for i in ids]
    result=reciprocal_rank_fusion({'dense':hits('b','a','b','c'),'bm25':hits('a','d')},60)
    by_id={r['chunk_id']:r for r in result}
    assert by_id['a']['score']==1/62+1/61
    assert by_id['b']['score']==1/61
    assert by_id['c']['score']==1/64
    assert by_id['c']['branch_ranks']=={'dense':4}
    assert [r['chunk_id'] for r in result]==['a','b','d','c']
    assert [r['chunk_id'] for r in reciprocal_rank_fusion({'dense':hits('b'),'bm25':hits('a')},1)]==['a','b']
    assert reciprocal_rank_fusion({'dense':[],'bm25':[]},60)==[]


@pytest.mark.parametrize('route',['dense','bm25','hybrid'])
def test_three_routes_one_binding_caps_scores_and_canonical_sources(tmp_path,route):
    catalog,kb,config,rows,backend=published(tmp_path)
    config=configured(config,route,dense_candidates=2,bm25_candidates=3,context_chunks=1)
    model=Provider();calls=[];backend.search=transport(rows,calls)
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        found=RetrievalSearch(catalog,lease.run.run_id,None if route=='bm25' else model,backend).search('same query',limit=1)
        trace=found['trace']
        assert len(found['hits'])==1 and trace['parameters']['result_limit']==1
        assert trace['query']=='same query' and trace['filter']==''
        assert all(h['revision_id']==str(lease.run.revision_id) for h in found['hits'])
        assert [c['limit'] for c in calls]==({'dense':[2],'bm25':[3],'hybrid':[2,3]}[route])
        assert len(model.calls)==(0 if route=='bm25' else 1)
        assert found['hits'][0]['score_type']=={'dense':'cosine_similarity','bm25':'bm25','hybrid':'rrf'}[route]
        assert 'heading_path' not in found['hits'][0]['text']
        assert all(c['artifact']['revision_id']==trace['revision_id'] and c['filter']=='' for c in calls)
        if route=='hybrid':
            assert calls[1]['value']==model.calls[0][0]
            assert len(trace['fusion'])==3
            for item in trace['fusion']:
                assert item['score']==sum(1/(60+rank) for rank in item['branch_ranks'].values())
        if route=='bm25':assert found['profile_fingerprint'] is found['model_timings'] is None


def test_dense_compatibility_keeps_explicit_limit(tmp_path):
    catalog,kb,config,rows,backend=published(tmp_path);calls=[]
    backend.search=transport(rows,calls)
    with catalog.start_run(kb,resolve_run(config,'qa')) as lease:
        assert len(DenseSearch(catalog,lease.run.run_id,Provider(),backend).search('q',limit=2)['hits'])==2
    assert [c['limit'] for c in calls]==[2]


@pytest.mark.parametrize('empty',[('dense',),('sparse',),('dense','sparse')])
def test_legitimate_empty_branch_fuses_without_fabricated_rank(tmp_path,empty):
    catalog,kb,config,rows,backend=published(tmp_path)
    backend.search=transport(rows,[],empty=empty)
    with catalog.start_run(kb,resolve_run(configured(config),'qa')) as lease:
        result=RetrievalSearch(catalog,lease.run.run_id,Provider(),backend).search('q')
        assert len(result['hits'])==(0 if len(empty)==2 else 3)
        for item in result['trace']['fusion']:
            assert len(item['branch_ranks'])==1


@pytest.mark.parametrize('fail',['dense','sparse'])
def test_partial_branch_failure_persists_diagnostics_without_new_evidence(tmp_path,fail):
    catalog,kb,config,rows,backend=published(tmp_path)
    backend.search=transport(rows,[])
    with catalog.start_run(kb,resolve_run(configured(config),'qa')) as lease:
        session=SourceSession(catalog,lease,S['ControlledMeter'](),dense=RetrievalSearch(catalog,lease.run.run_id,Provider(),backend))
        first=session.search('telescope')
        assert not any(word in first.text for word in ('branch_ranks','cosine_similarity','rrf_k'))
        trace=session.retrieval_trace(first.payload['call_id'])
        assert trace['status']=='ok' and len(trace['fusion'])==3
        gateway=DeliveryGateway(session);permit,_,_=S['prepared'](gateway,first)
        evidence,=gateway.settle(permit,'confirmed')
        with catalog._db.transaction() as db:
            previous=[db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('source_candidates','delivered_evidence')]
        backend.search=transport(rows,[],fail=fail)
        with pytest.raises(RagError) as caught:session.search('failed query')
        call_id=caught.value.error.call_id
        failed=session.retrieval_trace(call_id)
        assert failed['status']=='error' and failed['returned_ids']==[] and failed['fusion']==[]
        assert failed['branches']['dense']['status']==('error' if fail=='dense' else 'ok')
        assert failed['branches']['bm25']['status']==('not_started' if fail=='dense' else 'error')
        with catalog._db.transaction() as db:
            assert [db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('source_candidates','delivered_evidence')]==previous
            assert db.execute('SELECT status FROM source_calls WHERE call_id=?',(call_id,)).fetchone()==('error',)
        item=first.payload['items'][0]
        assert open_citation(catalog,evidence)['quotes']==[item['text']]


def test_both_branches_hold_old_pin_when_current_publishes_between_them(tmp_path):
    catalog,kb,config,rows,backend=published(tmp_path);calls=[]
    def publish_new():
        owner,next_config=H['processed'](catalog,tmp_path/'next.md',kb_id=kb,text='# New\nChanged publication.\n')
        prepared,*_=H['unit_ready'](catalog,owner,next_config)
        publication.publish(catalog,owner,prepared.revision_id);owner.close()
    backend.search=transport(rows,calls,after_dense=publish_new)
    with catalog.start_run(kb,resolve_run(configured(config),'qa')) as lease:
        found=RetrievalSearch(catalog,lease.run.run_id,Provider(),backend).search('telescope')
        assert catalog.get_library(kb).current_revision_id!=lease.run.revision_id
        assert {c['artifact']['revision_id'] for c in calls}=={str(lease.run.revision_id)}
        assert {h['revision_id'] for h in found['hits']}=={str(lease.run.revision_id)}


def test_deadline_rejects_bm25_without_io(tmp_path):
    catalog,kb,config,rows,backend=published(tmp_path);calls=[]
    backend.search=transport(rows,calls)
    with catalog.start_run(kb,resolve_run(configured(config,'bm25'),'qa')) as lease:
        with pytest.raises(RagError) as caught:
            RetrievalSearch(catalog,lease.run.run_id,None,backend).search('q',deadline_monotonic_ns=time.monotonic_ns()-1)
        assert caught.value.error.code=='DEADLINE_EXCEEDED'
        assert calls==[]
