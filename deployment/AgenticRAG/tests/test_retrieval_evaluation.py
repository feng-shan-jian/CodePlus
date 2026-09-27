"""Official retrieval diagnostic identity and denominator checks."""
import copy
import hashlib
import json
from pathlib import Path
import runpy
import pytest

BINDING = runpy.run_path(str(Path(__file__).parents[1]/'eval/retrieval_binding.py'))

def binding_fixture():
    questions=[{'id':str(i),'query':str(i),'gold':[] if i==2 else [{}]} for i in range(4)]
    selected=questions[:3]
    selection=BINDING['fingerprint']({'source_dataset_sha256':'source','total_questions':4,
                                     'question_ids':[q['id'] for q in selected]})
    report={'dataset_sha256':selection,'protocol':{'top_k':10},
        'records':[{'id':q['id'],'query':q['query'],'status':'error','hits':[]} for q in selected]}
    raw=json.dumps(report).encode()
    official={'task':'retrieval','input_sha256':hashlib.sha256(raw).hexdigest(),'dataset_sha256':selection,
              'selected_questions':3,'scored_questions':2,'excluded_null_queries':1}
    return questions,report,raw,official


def test_native_diagnostic_binds_exact_input_and_accepts_nonmedium_denominators():
    questions,report,raw,official=binding_fixture()
    assert BINDING['bind'](report,official,raw,questions,'source')==questions[:3]
    with pytest.raises(ValueError,match='exact input'):
        BINDING['bind'](report,official,raw+b' ',questions,'source')


@pytest.mark.parametrize('mutate',[
    lambda r:r['records'].reverse(),
    lambda r:r['records'][0].update(query='changed'),
    lambda r:r['records'].append(copy.deepcopy(r['records'][0])),
    lambda r:r['records'][0].update(hits=[{'text':'leaked on failure'}]),
    lambda r:r.update(dataset_sha256='wrong'),
])
def test_native_diagnostic_rejects_query_order_identity_or_failure_drift(mutate):
    questions,report,_,official=binding_fixture();mutate(report)
    raw=json.dumps(report).encode();official['input_sha256']=hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):BINDING['bind'](report,official,raw,questions,'source')
