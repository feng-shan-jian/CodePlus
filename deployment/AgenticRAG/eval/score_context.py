"""Offline Context coverage/cost from the exact selected and prepared body ranges.

Uses the existing source-content-v1 scorer. No retrieval, answer model or judge.
Prepared snapshots are explicitly unsent and never treated as confirmed evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys


def overlap(items):
    groups={}
    for item in items:
        groups.setdefault(item['document_version_id'],[]).extend((s['start'],s['end']) for s in item['returned_spans'])
    total=unique=0
    for spans in groups.values():
        end=-1
        for left,right in sorted(spans):
            total+=right-left;unique+=max(0,right-max(left,end));end=max(end,right)
    return total-unique


def summary(values):
    measured=[v for v in values if v is not None]
    return {'samples':len(measured),'unknown':len(values)-len(measured),
            'total':sum(measured) if measured else None,'mean':statistics.mean(measured) if measured else None,
            'minimum':min(measured) if measured else None,'maximum':max(measured) if measured else None}


def score_context(report, questions, evidence_recall, latency):
    assert [(q['id'],q['query']) for q in questions]==[(r['id'],r['query']) for r in report['records']]
    records=[]
    for question,row in zip(questions,report['records'],strict=True):
        context=row.get('context',{})
        record={'id':row['id'],'retrieval_status':row['status'],'context_status':context.get('status','not_started')}
        gold=[{**g,'file':g['source_id']} for g in question['gold']]
        for stage in ('selected','prepared'):
            items=context.get(stage,[])
            hits=[{'file':Path(i['file_name']).stem,'source_spans':[
                {'char_start':s['start'],'char_end':s['end']} for s in i['returned_spans']]} for i in items]
            record[stage]={'coverage':evidence_recall(gold,hits,len(hits),ignore_whitespace=True),
                'fragments':len(items),'body_utf8_upper':sum(len(i['text'].encode()) for i in items),
                'repeated_codepoints':overlap(items)}
        trace=row.get('trace') or row.get('error',{}).get('retrieval_trace') or {}
        batches=trace.get('rerank',{}).get('batches',[])
        record.update(tool_upper=context.get('tool_upper'),request_utf8_upper=context.get('request_utf8_upper'),
            host_input_upper=context.get('host_input_upper'),context_elapsed_ms=context.get('elapsed_ms',row.get('elapsed_ms')),
            elapsed_source='context' if 'elapsed_ms' in context else 'retrieval_request',
            rerank_batches=len(batches),rerank_input_tokens=sum(b.get('total_tokens',0) for b in batches))
        assert context.get('confirmed_evidence',0)==0 and not context.get('answer_model_executed',False)
        records.append(record)
    result={'selected_questions':len(records),'scored_questions':sum(bool(q['gold']) for q in questions),
        'retrieval_errors':sum(r['retrieval_status']=='error' for r in records),
        'context_errors':sum(r['context_status']!='ok' for r in records),
        'delivery':'prepared_only_then_not_sent','confirmed_evidence':0,'answer_correctness':'not_evaluated',
        'meter':'pinned host UTF-8/template upper bound; rerank_input_tokens are actual frozen tokenizer counts',
        'context_latency_all_attempts':latency([r['context_elapsed_ms'] for r in records if r['context_elapsed_ms'] is not None]),
        'context_latency_unknown':sum(r['context_elapsed_ms'] is None for r in records),'per_question':records}
    for stage in ('selected','prepared'):
        covered=sum(r[stage]['coverage']['covered'] for r in records)
        total=sum(r[stage]['coverage']['total'] for r in records)
        result[stage]={'covered_ranges':covered,'evidence_ranges':total,'evidence_recall':covered/total if total else None,
            'fully_covered':sum(r[stage]['coverage']['recall']==1 for r in records),
            **{key:summary([r[stage][key] for r in records]) for key in ('fragments','body_utf8_upper','repeated_codepoints')}}
    for key in ('tool_upper','request_utf8_upper','host_input_upper','rerank_batches','rerank_input_tokens'):
        result[key]=summary([r[key] for r in records])
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('input','official-score','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    suite=Path(__file__).resolve().parents[3]/'eval/RAG-eval'
    sys.path.insert(0,str(suite))
    from dataset_io import load_dataset
    from replay import evidence_recall,latency
    report=json.loads(args.input.read_text(encoding='utf-8'))
    official=json.loads(args.official_score.read_text(encoding='utf-8'))
    assert report['dataset_sha256']==official['dataset_sha256']
    assert official['input_sha256']==hashlib.sha256(args.input.read_bytes()).hexdigest()
    _,questions,_=load_dataset(suite);ids={r['id'] for r in report['records']}
    questions=[q for q in questions if q['id'] in ids]
    assert len(questions)==official['selected_questions']==200
    assert sum(bool(q['gold']) for q in questions)==official['scored_questions']==177
    result=score_context(report,questions,evidence_recall,latency)
    result.update(input_sha256=official['input_sha256'],dataset_sha256=report['dataset_sha256'])
    with args.output.open('x',encoding='utf-8') as stream:stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='per_question'},ensure_ascii=False))
