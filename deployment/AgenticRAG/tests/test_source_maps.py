"""Exact byte ranges independently checked against original bytes."""
import hashlib
from uuid import uuid4

import pytest
from agentic_rag.domain import Span
from agentic_rag.ingestion.parsing import canonicalize, parse_document, SourceMap


@pytest.mark.parametrize('raw', [b'', b'\xef\xbb\xbf', b'\xef\xbb\xbfa\r\nb\rc\n',
    'e\u0301 é 中文😀\r\n重复\r重复\n'.encode(), b'\n\nend', b'last\n', b'a\r\r\nb'])
def test_every_codepoint_maps_to_exact_original_bytes(raw):
    text, mapping = canonicalize(raw)
    assert text == raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
    assert mapping.canonical_hash == hashlib.sha256(text.encode()).hexdigest()
    assert SourceMap.model_validate_json(mapping.model_dump_json()) == mapping
    for index, char in enumerate(text):
        span = mapping.raw_span(Span(start=index,end=index+1))
        original = raw[span.start:span.end].decode('utf-8')
        assert original.replace('\r\n','\n').replace('\r','\n') == char
        assert mapping.lines(Span(start=index,end=index+1))[0] == text[:index].count('\n') + 1
    assert len(mapping.byte_boundaries) == len(text) + 1


def test_non_nfc_evidence_and_repeated_offsets_are_preserved():
    raw = '\ufeff# e\u0301\r\né e\u0301\r\né e\u0301'.encode()
    parsed = parse_document(raw, 'text/markdown', uuid4())
    assert parsed.text == '# e\u0301\né e\u0301\né e\u0301'
    assert parsed.source_map.byte_boundaries[:5] == (3,4,5,6,8)
    with pytest.raises(ValueError, match='out of range'):
        parsed.source_map.raw_span(Span(start=0,end=len(parsed.text)+1))
