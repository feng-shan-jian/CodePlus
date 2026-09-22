"""Full token-ID agreement with real R03 AutoTokenizer, independent Rerank cap."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest
from agentic_rag.domain import RagError
from agentic_rag.models import FrozenTokenizer

HELPER = runpy.run_path(str(Path(__file__).with_name('parsing_support.py')))


def test_actual_tokenizers_match_r03_auto_tokenizer_ids(tmp_path):
    embed, rank = HELPER['tokenizer'](), HELPER['tokenizer']('rerank')
    samples = ['e\u0301 中文😀🫨🧑🏽‍💻', 'é e\u0301\n重复 重复', '<|endoftext|> <|im_start|>', 'a'*8000]
    cases, expected = [], []
    for sample in samples:
        for tokenizer, sequence in [(embed,embed.document(sample,'标题',check=False)),
                                    (embed,embed.query('semantic retrieval')),
                                    (rank,rank.rerank('search question', sample[:3000],'标题'))]:
            cases.append({'model':tokenizer.profile.model.split('/')[-1], 'revision':tokenizer.profile.revision,
                          'sample':sample, 'pieces':sequence.pieces, 'special':tokenizer.profile.add_special_tokens})
            expected.append({'ids':list(sequence.ids),'offsets':[list(o) for o in tokenizer.offsets(sample)]})
    path = tmp_path/'oracle-input.json'
    path.write_text(json.dumps(cases),encoding='utf-8')
    python = Path(os.environ.get('R08_ORACLE_PYTHON',str(Path.home()/'.cache/codeplus-agenticrag/venv-win-cuda/Scripts/python.exe')))
    args = [str(python),'-I','-B',str(Path(__file__).with_name('tokenizer_oracle.py')),str(HELPER['cache']()),str(path)]
    command = subprocess.run(args,cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=180)
    assert command.returncode==0,command.stderr
    actual = json.loads(command.stdout)
    assert actual['results']==expected
    offsets = embed.offsets('e\u0301 中文🫨')
    assert not any(a <= 1 < b for a,b in offsets)  # actual NFC omitted position
    assert len(set(offsets)) < len(offsets)  # actual ByteLevel overlapping emoji
    assert embed.document('body').ids[-1] == 151643
    report = {'argv':args,'cwd':str(tmp_path),'exit_code':command.returncode,
              'oracle':actual,'cases':cases,'all_full_ids_equal':True,'gpu_inference':False}
    if os.environ.get('R08_TOKENIZER_REPORT'):
        Path(os.environ['R08_TOKENIZER_REPORT']).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def test_rerank_requires_own_full_query_document_budget_and_piece_encoding():
    embed,rank = HELPER['tokenizer'](),HELPER['tokenizer']('rerank')
    assert embed.document('body').token_count < 2048
    with pytest.raises(RagError,match='rerank complete input'):
        rank.rerank(' query'*3000,'body')
    sequence = rank.rerank('query','body','title')
    assert sequence.ids == tuple(t for p in sequence.pieces for t in rank.encode(p))
    assert len(sequence.pieces)==3
    # Exact complete input limits, including appended EOS.
    body = ' x'*2047
    assert embed.document(body).token_count == 2048
    with pytest.raises(RagError,match='2049 exceeds 2048'):
        embed.document(body+' x')
    with pytest.raises(RagError,match='complete input'):
        embed.document(body,'title')


def test_wrong_assets_fail_without_downloading(tmp_path):
    tok = HELPER['tokenizer']()
    with pytest.raises(RagError,match='missing frozen'):
        FrozenTokenizer(tok.profile,tmp_path)
    root = tmp_path/tok.profile.model.split('/')[-1]/tok.profile.revision
    root.mkdir(parents=True)
    (root/'tokenizer.json').write_bytes(b'{}')
    with pytest.raises(RagError,match='asset differs'):
        FrozenTokenizer(tok.profile,tmp_path)
