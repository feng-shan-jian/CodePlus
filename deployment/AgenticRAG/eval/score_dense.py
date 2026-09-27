"""Offline scoring-only driver for the unchanged R01 native source-span metric.

Run after official score.py validation in a separate process. This driver never
constructs a provider, database adapter, run, or query runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--official-score',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    suite=Path(__file__).resolve().parents[3]/'eval/RAG-eval'
    sys.path.insert(0,str(suite))
    from dataset_io import load_dataset
    from replay import score,latency
    report=json.loads(args.input.read_text(encoding='utf-8'))
    official=json.loads(args.official_score.read_text(encoding='utf-8'))
    _,all_questions,source_hash=load_dataset(suite)
    binding=runpy.run_path(str(Path(__file__).with_name('retrieval_binding.py')))
    questions=binding['bind'](report,official,args.input.read_bytes(),all_questions,source_hash)
    scored=sum(bool(q['gold']) for q in questions)
    summary=score(questions,report['records'],10)
    applicable=[r for r in report['records'] if r['evidence']['total']]
    total=sum(r['evidence']['total'] for r in applicable)
    covered=sum(r['evidence']['covered'] for r in applicable)
    result={'input_sha256':hashlib.sha256(args.input.read_bytes()).hexdigest(),
        'official_score_sha256':hashlib.sha256(args.official_score.read_bytes()).hexdigest(),
        'dataset_sha256':report['dataset_sha256'],'selected_questions':len(questions),'scored_questions':scored,
        'excluded_null_queries':len(questions)-scored,'request_errors':sum(r['status']=='error' for r in report['records']),
        'fully_covered':sum(r['evidence']['recall']==1 for r in applicable),
        'evidence_ranges':total,'covered_ranges':covered,'evidence_recall':covered/total if total else None,
        'latency_all_successes':latency([r['elapsed_ms'] for r in report['records'] if r['status']=='ok']),
        'cold_first_ms':report['records'][0]['elapsed_ms'],
        'warm_repeat_ms':report.get('warm_repeat',{}).get('elapsed_ms'),
        'by_question_type':summary,'per_question':[{'id':r['id'],'status':r['status'],'evidence':r['evidence']} for r in report['records']],
        'answer_correctness':'not_evaluated','refusal_correctness':'not_evaluated'}
    if args.output.exists():raise FileExistsError(args.output)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('per_question','by_question_type')},ensure_ascii=False))


if __name__=='__main__':main()
