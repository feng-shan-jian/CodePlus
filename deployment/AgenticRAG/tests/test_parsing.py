"""Actual markdown-it structure and canonical spans, including repeated text."""
from uuid import uuid4

import pytest
from agentic_rag.domain import RagError
from agentic_rag.ingestion.parsing import parse_document


def test_repeated_paragraphs_and_nested_heading_ranges():
    text = '# Root\n\nSame paragraph.\n\n## Child\n\nSame paragraph.\n\n### Deep\nbody\n\n# Other\nend'
    parsed = parse_document(text.encode(), 'text/markdown', uuid4())
    assert [s.heading_path for s in parsed.sections] == [('Root',), ('Root','Child'), ('Root','Child','Deep'), ('Other',)]
    paragraphs = [b.span for b in parsed.blocks if b.kind == 'paragraph']
    assert paragraphs[0].start == 8 and paragraphs[1].start == 35
    assert parsed.text[paragraphs[0].start:paragraphs[0].end] == parsed.text[paragraphs[1].start:paragraphs[1].end]
    assert parsed.headings[0].subtree_span.end == parsed.sections[-1].span.start
    assert parsed.headings[1].parent_section_id == parsed.sections[0].section_id
    assert parsed.headings[2].parent_section_id == parsed.sections[1].section_id


def test_nested_lists_fence_table_and_terminal_line():
    text = '# A\n- first\n  - nested\n    continuation\n\n```py\nx = "😀"\n```\n\n|A|B|\n|-|-|\n|1|2|\nlast'
    p = parse_document(text.encode(), 'text/markdown', uuid4())
    assert p.text == text
    assert {'heading','bullet_list','list_item','paragraph','fence','table'} <= {b.kind for b in p.blocks}
    assert any(b.kind == 'bullet_list' and b.level > 0 for b in p.blocks)
    assert p.sections[-1].span.end == len(text)
    for block in p.blocks:
        assert 0 <= block.span.start < block.span.end <= len(text)


@pytest.mark.parametrize('text', ['', '\n \t\n', '# Heading only\n', '# Parent\n## Child\n'])
def test_empty_or_no_body_has_no_eligible_span(text):
    p = parse_document(text.encode(), 'text/markdown', uuid4())
    assert p.eligible_spans == ()
    assert bool(p.sections) == bool(text)


@pytest.mark.parametrize('raw', [b'\xff\xfeA\0', b'\x80', b'a\0b'])
def test_unsupported_input_explicit_error(raw):
    with pytest.raises(RagError, match='UTF-8|NUL'):
        parse_document(raw, 'text/plain', uuid4())


def test_format_rejected_and_txt_soft_lines_form_paragraph():
    with pytest.raises(RagError, match='only Markdown/TXT'):
        parse_document(b'body', 'application/pdf', uuid4())
    p = parse_document(b'one soft\nline.\n\nNext\nparagraph', 'text/plain', uuid4())
    assert [p.text[b.span.start:b.span.end] for b in p.blocks] == ['one soft\nline.\n\n','Next\nparagraph']
