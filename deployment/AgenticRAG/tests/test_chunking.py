"""Actual frozen tokenizer boundaries, complete budgets and coverage arithmetic."""
from pathlib import Path
import runpy
from uuid import uuid4

import pytest
from agentic_rag.domain import RagError
from agentic_rag.ingestion.parsing import parse_document
from agentic_rag.ingestion.chunking import chunk_document, chunker_config, coverage

HELPER = runpy.run_path(str(Path(__file__).with_name('parsing_support.py')))


@pytest.fixture(scope='module')
def tok():
    return HELPER['tokenizer']()


@pytest.mark.parametrize('body', ['e\u0301 中文😀🫨🧑🏽‍💻 ' * 600, 'a' * 25000,
    ('Same paragraph.\n\n' * 1000), '句子。第二句！第三句？第四句。' * 600,
    '```python\n' + ('print("same 😀")\n' * 900) + '```\n',
    '|A|B|\n|-|-|\n' + '|same|重复|\n' * 900,
    '<|endoftext|> <|im_start|> ' * 600], ids=['unicode','long-token','repeats','chinese','fence','table','special'])
def test_complete_tokenizer_budget_and_independent_coverage(tok, body):
    parsed = parse_document(('# Title\n'+body).encode(),'text/markdown',uuid4())
    result = chunk_document(parsed, chunker_config(128,16), tok)
    audit = coverage(result)
    assert audit['covered_codepoints'] == len(parsed.text) == audit['eligible_codepoints']
    assert audit['gap_spans'] == []
    assert len(result.inputs) > 1
    for item in result.inputs:
        span, = item.chunk.spans
        text = parsed.text[span.start:span.end]
        actual = tok.document(text,item.index_title)
        assert actual.token_count == item.complete_embedding_tokens <= 128
        assert actual.ids[-1] == 151643
        assert item.overlap_tokens <= 16
    if body.startswith(('```','|A')):
        assert any(i.split_structures for i in result.inputs)


def test_title_budget_and_empty_body(tok):
    parsed = parse_document(('# ' + 'verylong ' * 512 + '\nbody').encode(),'text/markdown',uuid4())
    with pytest.raises(RagError, match='title leaves no body'):
        chunk_document(parsed,chunker_config(),tok)
    for raw in (b'',b' \n',b'# Heading only'):
        result = chunk_document(parse_document(raw,'text/markdown',uuid4()),chunker_config(),tok)
        assert result.inputs == () and coverage(result)['status'] == 'no_body'


def test_chinese_sentences_need_no_space_and_txt_soft_line_is_not_block(tok):
    text = ('连续中文句子测试。另一个完整句子。' * 100)
    parsed = parse_document(text.encode(),'text/plain',uuid4())
    result = chunk_document(parsed,chunker_config(24,0),tok)
    assert all(parsed.text[i.chunk.spans[0].end-1] == '。' for i in result.inputs)
    parsed = parse_document(('word '*25+'soft\n'+'line ends here.\n\nnext paragraph.').encode(),'text/plain',uuid4())
    assert len(parsed.blocks)==2


def test_coverage_detects_dropped_chunk_not_self_defined_denominator(tok):
    result = chunk_document(parse_document(('body '*3000).encode(),'text/plain',uuid4()),chunker_config(128,0),tok)
    with pytest.raises(ValueError,match='coverage gaps'):
        coverage(result.model_copy(update={'inputs': result.inputs[1:]}))


def test_long_first_paragraph_does_not_emit_heading_only_chunk(tok):
    parsed = parse_document(('# Heading\n\n' + 'A complete sentence about galaxies. '*50).encode(),'text/markdown',uuid4())
    result = chunk_document(parsed,chunker_config(64,8),tok)
    assert result.inputs[0].chunk.spans[0].end > len('# Heading\n\nA complete sentence about galaxies.')
    parsed = parse_document(('# Heading\n\n'+'a'*5000).encode(),'text/markdown',uuid4())
    result = chunk_document(parsed,chunker_config(64,8),tok)
    assert result.inputs[0].chunk.spans[0].end > len('# Heading\n\n')


@pytest.mark.parametrize('text', ['body'+' '*10000, ' '*10000+'body', 'first'+' '*10000+'last',
                                'first\n'+'\n'*1000+'last'])
def test_large_whitespace_is_archived_but_never_empty_model_input(tok,text):
    result=chunk_document(parse_document(text.encode(),'text/plain',uuid4()),chunker_config(16,0),tok)
    assert result.parsed.text==text
    assert all(text[i.chunk.spans[0].start:i.chunk.spans[0].end].strip() for i in result.inputs)
    audit=coverage(result)
    assert audit['required_nonwhitespace_codepoints']==sum(not c.isspace() for c in text)
    assert audit['covered_required_codepoints']==audit['required_nonwhitespace_codepoints']
    assert audit['covered_codepoints']+audit['omitted_whitespace_codepoints']==len(text)
    for excluded in audit['excluded_spans']:
        span=excluded['span'];assert not text[span['start']:span['end']].strip()


@pytest.mark.parametrize('media_type',['text/plain','text/markdown'])
@pytest.mark.parametrize('title',['','# H\n\n'])
@pytest.mark.parametrize('gap',['\n'*2000,' '*10000],ids=['blank-lines','indentation'])
def test_markdown_and_txt_long_leading_gap_keep_short_body(tok,media_type,title,gap):
    text=title+gap+'x'
    result=chunk_document(parse_document(text.encode(),media_type,uuid4()),chunker_config(32,0),tok)
    assert result.inputs
    assert all('x' in text[i.chunk.spans[0].start:i.chunk.spans[0].end] or media_type=='text/plain'
               for i in result.inputs)
    audit=coverage(result)
    assert audit['covered_required_codepoints']>=1
    assert audit['covered_codepoints']+sum(e['span']['end']-e['span']['start'] for e in audit['excluded_spans'])==len(text)
    assert text[result.inputs[-1].chunk.spans[0].start:result.inputs[-1].chunk.spans[0].end].endswith('x')
