"""Prepared Context measurements cannot turn source/window failures into PASS."""
import json
from pathlib import Path
import runpy

from agentic_rag.config import KnowledgeConfig

R=runpy.run_path(str(Path(__file__).with_name('test_retrieval.py')))
E=runpy.run_path(str(Path(__file__).parents[1]/'eval/dense_runner.py'))


def test_query_only_runner_keeps_ranking_and_explicit_context_failure(tmp_path):
    catalog,kb,base,rows,backend=R['published'](tmp_path)
    calls=[];backend.search=R['transport'](rows,calls)
    data=R['configured'](base,'bm25').model_dump(mode='json')
    data['retrieval']['context_tokens']=3000
    config=KnowledgeConfig.model_validate_json(json.dumps(data))
    class Meter(R['S']['ControlledMeter']):
        model='deepseek-chat'
        def input_upper_bound(self,raw_body,*,output_cap):
            raise ValueError('controlled final request meter failure')
    result=E['query_context'](catalog,kb,str(catalog.get_library(kb).current_revision_id),config,None,backend,Meter(),'query')
    assert len(calls)==1 and len(result['hits'])==3
    assert result['context']['status']=='error' and result['context']['prepared']==[]
    assert result['context']['answer_model_executed'] is False
    assert E['completion_status']({'errors':0,'context_errors':1})=='COMPLETE_WITH_CONTEXT_ERRORS'
    assert E['completion_status']({'warm_repeat':result})=='COMPLETE_WITH_AUXILIARY_ERROR'
    with catalog._db.transaction() as db:
        assert db.execute('SELECT count(*) FROM delivered_evidence').fetchone()==(0,)
        assert db.execute("SELECT count(*) FROM delivery_receipts WHERE status='prepared'").fetchone()==(0,)


def test_context_score_keeps_failed_preparation_in_body_coverage_denominator(monkeypatch):
    suite=Path(__file__).resolve().parents[3]/'eval/RAG-eval'
    monkeypatch.syspath_prepend(str(suite))
    native=runpy.run_path(str(suite/'replay.py'))
    scorer=runpy.run_path(str(Path(__file__).parents[1]/'eval/score_context.py'))
    gold={'source_id':'document','char_start':0,'char_end':4,'quote':'fact'}
    item={'file_name':'document.md','document_version_id':'version',
        'returned_spans':[{'start':0,'end':4}],'text':'fact'}
    questions=[{'id':str(i),'query':str(i),'gold':[gold] if i<2 else []} for i in range(3)]
    report={'records':[{'id':str(i),'query':str(i),'status':'ok',
        'context':{'status':'error' if i==1 else 'ok','selected':[item],
            'prepared':[] if i==1 else [item],'answer_model_executed':False,'confirmed_evidence':0}}
        for i in range(3)]}
    result=scorer['score_context'](report,questions,native['evidence_recall'],native['latency'])
    assert (result['selected_questions'],result['scored_questions'],result['context_errors'])==(3,2,1)
    assert result['retrieval_errors']==0
    assert (result['selected']['covered_ranges'],result['selected']['evidence_ranges'])==(2,2)
    assert (result['prepared']['covered_ranges'],result['prepared']['evidence_ranges'])==(1,2)
    assert result['prepared']['fully_covered']==1 and result['prepared']['evidence_recall']==0.5
    assert result['per_question'][1]['prepared']['fragments']==0
    assert result['confirmed_evidence']==0 and result['answer_correctness']=='not_evaluated'


def test_context_score_preserves_failed_retrieval_time_and_attempted_rerank_batches(monkeypatch):
    suite=Path(__file__).resolve().parents[3]/'eval/RAG-eval'
    monkeypatch.syspath_prepend(str(suite))
    native=runpy.run_path(str(suite/'replay.py'))
    scorer=runpy.run_path(str(Path(__file__).parents[1]/'eval/score_context.py'))
    questions=[{'id':'failed','query':'failed','gold':[]}]
    report={'records':[{'id':'failed','query':'failed','status':'error','elapsed_ms':1250.5,
        'error':{'retrieval_trace':{'rerank':{'batches':[
            {'status':'ok','total_tokens':80},{'status':'error','total_tokens':90}]}}}}]}
    result=scorer['score_context'](report,questions,native['evidence_recall'],native['latency'])
    row=result['per_question'][0]
    assert row['context_elapsed_ms']==1250.5 and row['elapsed_source']=='retrieval_request'
    assert (row['rerank_batches'],row['rerank_input_tokens'])==(2,170)
    assert result['context_latency_all_attempts']=={'samples':1,'p50_ms':1250.5,'p95_ms':1250.5}
    assert result['request_utf8_upper']=={'samples':0,'unknown':1,'total':None,'mean':None,'minimum':None,'maximum':None}
    assert row['prepared']['fragments']==0
