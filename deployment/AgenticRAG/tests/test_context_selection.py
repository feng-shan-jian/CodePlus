"""Context positions and window transitions on actual source/delivery state."""
import json
from pathlib import Path
import runpy

import pytest

from agentic_rag.citations import CitationRegistry
from agentic_rag.domain import RagError, Span
from agentic_rag.evidence import DeliveryGateway
from agentic_rag.retrieval.context import complementary_order

S=runpy.run_path(str(Path(__file__).with_name('source_support.py')))


def fixture(tmp_path, *, tokens=20000, text=None):
    args={} if text is None else {'raw':text.encode()}
    result=S['fixture'](tmp_path,context_tokens=tokens,**args)
    catalog,lease,session,_,version,chunks,ref=result
    S['dense_fixture'](session,chunks,version,ref)
    return result


def test_relevance_bands_complement_sources_without_quotas_or_cross_query_scores():
    hits=[{'document_id':d,'chunk_id':str(i),'score':score} for i,(d,score) in enumerate(
        [('A',1),('A',.99),('B',.985),('C',.979),('A',.5),('B',.49)])]
    assert [h['chunk_id'] for h in complementary_order(hits)]==['0','2','1','3','4','5']
    # Relevant same-document complementary/conflicting facts remain eligible.
    assert len(list(complementary_order(hits)))==6
    changed=[{**h,'score':h['score']*10} for h in hits]
    assert [h['chunk_id'] for h in complementary_order(changed)]==['0','2','1','3','4','5']
    # A second query starts with no document preference from the previous call.
    assert next(complementary_order([hits[1],hits[2]]))==hits[1]


def test_pending_unsent_history_does_not_suppress_body_and_reservations_accumulate(tmp_path):
    catalog,lease,session,*_=fixture(tmp_path)
    try:
        first=session.search('q');second=session.search('q')
        assert first.payload['items'][0]['text']==second.payload['items'][0]['text']
        assert first.candidate_ids!=second.candidate_ids
        assert session.usage()['window_fragments']==2
        assert session.usage()['returned_tokens']==len(first.text.encode())+len(second.text.encode())
        gateway=DeliveryGateway(session);permit,_,_=S['prepared'](gateway,first)
        gateway.settle(permit,'not_sent')
        assert not gateway.window()['mappings']
        third=session.search('q')
        assert third.payload['items'][0]['text']==first.payload['items'][0]['text']
        with pytest.raises(RagError):
            CitationRegistry(session).save(first.payload['items'][0]['evidence_id'],(),())
    finally:lease.close()


def test_overlap_is_exact_and_same_document_disjoint_conflicts_remain(tmp_path):
    text='# Rule\n'+('Telescopes allow blue only. '*180)+'\n# Exception\nTelescopes reject blue in storms.\n'
    catalog,lease,session,_,version,chunks,ref=fixture(tmp_path,tokens=50000,text=text)
    try:
        assert len(chunks.inputs)>2
        result=session.search('blue rules')
        spans=[Span.model_validate(i['returned_spans'][0]) for i in result.payload['items']]
        for i,a in enumerate(spans):
            assert all(a.end<=b.start or b.end<=a.start for b in spans[i+1:])
        bodies=''.join(i['text'] for i in result.payload['items'])
        assert 'allow blue only' in bodies and 'reject blue in storms' in bodies
        assert len({i['document_id'] for i in result.payload['items']})==1
        trace=session.retrieval_trace(result.payload['call_id'])['context']
        assert trace['policy']['parameter_status']=='experiment'
        assert any(d['original_span']!=d['returned_spans'][0] for d in trace['decisions'] if d['returned_spans'])
    finally:lease.close()


def test_budget_crop_then_delivery_prefix_only_and_removed_body_reacquisition(tmp_path):
    catalog,lease,session,_,_,chunks,_=fixture(tmp_path,tokens=1900,text='# Heading\n'+'telescope registry '*160)
    try:
        first=session.search('registry')
        assert first.payload['items'] and first.payload['limited']['by_tokens']
        assert len(first.text.encode())<=1900
        item=first.payload['items'][0];returned=Span.model_validate(item['returned_spans'][0])
        assert returned.end<chunks.inputs[0].chunk.spans[0].end
        gateway=DeliveryGateway(session)
        partial=Span(start=returned.start,end=min(returned.start+10,returned.end))
        permit,_,_=S['prepared'](gateway,first,(partial,))
        gateway.retain_prepared_window(permit)
        evidence,=gateway.settle(permit,'confirmed')
        assert CitationRegistry(session).save(evidence,(partial,),(item['text'][:10],))
        second=session.search('same range')
        assert second.payload['items'][0]['returned_spans'][0]['start']==partial.end
        assert second.payload['items'][0]['text']==chunks.parsed.text[partial.end:second.payload['items'][0]['returned_spans'][0]['end']]
        S['discard_window'](session)
        third=session.search('same range again')
        assert third.payload['items'][0]['returned_spans'][0]['start']==returned.start
        assert session.usage()['returned_tokens']>session.usage()['window_tokens']
        with catalog._db.transaction() as db:
            receipts=[json.loads(r[0]) for r in db.execute('SELECT payload FROM delivery_receipts')]
        removal=receipts[-1]['window_transition']['removed_from_request']
        assert removal[0]['source_span']==partial.model_dump()
        assert 'text_sha256' not in removal[0] and 'body_span' not in removal[0]
    finally:lease.close()


@pytest.mark.parametrize('status',['confirmed','unknown','not_sent'])
def test_compact_trace_records_current_request_ranges_as_conditional_removals(tmp_path,status):
    catalog,lease,session,*_=fixture(tmp_path)
    try:
        first=session.search('q');gateway=DeliveryGateway(session)
        permit,_,_=S['prepared'](gateway,first)
        gateway.retain_prepared_window(permit);gateway.settle(permit,'confirmed')
        span=Span.model_validate(first.payload['items'][0]['returned_spans'][0])
        short=Span(start=span.start,end=span.start+3)
        compact,_,_=S['prepared'](gateway,first,(short,),purpose='compact')
        gateway.retain_prepared_window(compact)
        gateway.settle(compact,status)
        with catalog._db.transaction() as db:
            receipt=json.loads(db.execute('SELECT payload FROM delivery_receipts WHERE request_id=?',(str(compact.request_id),)).fetchone()[0])
        transition=receipt['window_transition']
        assert Span.model_validate(transition['removed_from_request'][0]['source_span'])==Span(start=short.end,end=span.end)
        assert transition['on_confirmed_compact_removed'][0]['source_span']==short.model_dump()
        assert len(gateway.window()['mappings'])==(0 if status=='confirmed' else 1)
        if status=='confirmed':
            S['discard_window'](session)
            recovered=session.search('q')
            assert recovered.payload['items'][0]['text']==first.payload['items'][0]['text']
    finally:lease.close()
