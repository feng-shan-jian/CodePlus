"""Canonical UTF-8 text, exact byte mapping and Markdown/TXT structure.

Canonical text retains Markdown syntax. Only an initial BOM and newline spelling
change. Neither Unicode NFC nor tokenizer decoding participates in this mapping.
"""

from bisect import bisect_right
import hashlib
from importlib.metadata import version
from typing import Literal
from uuid import UUID, uuid5

from pydantic import model_validator

from .._schema import NonNegativeInt, Record, Sha256, Text, fingerprint
from ..config import ParserConfig
from ..domain import ErrorCode, RagError, Section, Span

PARSER = ParserConfig(implementation='markdown_it_txt', version='markdown-it-py-4.0.0+source-v1',
                      normalization_version='utf8-bom-newlines-v1')
PARSER_FINGERPRINT = fingerprint('parser', PARSER)


class SourceMap(Record):
    raw_hash: Sha256
    raw_size: NonNegativeInt
    canonical_hash: Sha256
    # len(text)+1 boundary values. A normalized LF owns its original CRLF bytes.
    byte_boundaries: tuple[NonNegativeInt, ...]
    line_starts: tuple[NonNegativeInt, ...]
    bom_bytes: Literal[0, 3]

    @model_validator(mode='after')
    def valid(self):
        if (not self.byte_boundaries or self.byte_boundaries[0] != self.bom_bytes or
                self.byte_boundaries[-1] != self.raw_size or
                any(a >= b for a, b in zip(self.byte_boundaries, self.byte_boundaries[1:]))):
            raise ValueError('invalid canonical to byte boundaries')
        length = len(self.byte_boundaries) - 1
        if (not self.line_starts or self.line_starts[0] != 0 or self.line_starts[-1] > length or
                any(a >= b for a, b in zip(self.line_starts, self.line_starts[1:]))):
            raise ValueError('invalid line starts')
        return self

    def raw_span(self, span: Span) -> Span:
        if span.end >= len(self.byte_boundaries):
            raise ValueError('canonical span out of range')
        return Span(start=self.byte_boundaries[span.start], end=self.byte_boundaries[span.end])

    def lines(self, span: Span) -> tuple[int, int]:
        self.raw_span(span)
        return bisect_right(self.line_starts, span.start), bisect_right(self.line_starts, span.end - 1)


class Block(Record):
    kind: Text
    span: Span
    level: NonNegativeInt = 0
    info: str = ''


class ParsedDocument(Record):
    text: str
    source_map: SourceMap
    sections: tuple[Section, ...]
    blocks: tuple[Block, ...]
    headings: tuple['Heading', ...] = ()
    eligible_spans: tuple[Span, ...] = ()
    title: str = ''


class Heading(Record):
    section_id: UUID
    parent_section_id: UUID | None
    level: int
    title: Text
    # Includes descendants; Section.span itself is a disjoint own-content range.
    subtree_span: Span


def canonicalize(raw: bytes) -> tuple[str, SourceMap]:
    bom = 3 if raw.startswith(b'\xef\xbb\xbf') else 0
    try:
        decoded = raw[bom:].decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise RagError(ErrorCode.INVALID_INPUT, f'unsupported encoding: UTF-8 required at byte {bom + exc.start}', stage='parse') from exc
    if '\x00' in decoded:
        raise RagError(ErrorCode.INVALID_INPUT, 'NUL is unsupported in text inputs', stage='parse')
    pieces, boundaries, cursor, index = [], [bom], bom, 0
    while index < len(decoded):
        char = decoded[index]
        if char == '\r':
            width = 2 if decoded[index:index + 2] == '\r\n' else 1
            pieces.append('\n')
            cursor += width
            index += width
        else:
            pieces.append(char)
            cursor += len(char.encode('utf-8'))
            index += 1
        boundaries.append(cursor)
    text = ''.join(pieces)
    line_starts = (0,) + tuple(i + 1 for i, char in enumerate(text) if char == '\n')
    return text, SourceMap(raw_hash=hashlib.sha256(raw).hexdigest(), raw_size=len(raw),
        canonical_hash=hashlib.sha256(text.encode('utf-8')).hexdigest(), byte_boundaries=tuple(boundaries),
        line_starts=line_starts, bom_bytes=bom)


def parse_document(raw: bytes, media_type: str, version_id: UUID) -> ParsedDocument:
    if media_type not in ('text/markdown', 'text/plain'):
        raise RagError(ErrorCode.INVALID_INPUT, 'only Markdown/TXT inputs are supported', stage='parse')
    text, mapping = canonicalize(raw)
    if not text:
        return ParsedDocument(text=text, source_map=mapping, sections=(), blocks=())
    blocks, headings = [], []
    if media_type == 'text/markdown':
        from markdown_it import MarkdownIt
        if version('markdown-it-py') != '4.0.0':
            raise RagError(ErrorCode.IDENTITY_MISMATCH, 'requires markdown-it-py 4.0.0', stage='parse')
        tokens = MarkdownIt('commonmark').enable('table').parse(text)
        def offset(line):
            return mapping.line_starts[line] if line < len(mapping.line_starts) else len(text)
        for index, token in enumerate(tokens):
            if token.map is None or token.nesting == -1 or token.type == 'inline':
                continue
            start, end = map(offset, token.map)
            if end <= start:
                continue
            kind = token.type.removesuffix('_open')
            blocks.append(Block(kind=kind, span=Span(start=start, end=end), level=token.level, info=token.info))
            if token.type == 'heading_open':
                # Display path only; evidence always uses original canonical span.
                title = tokens[index + 1].content.strip()
                if title:
                    headings.append((start, int(token.tag[1:]), title))
    else:
        # Paragraph boundaries derive directly from line spans, not repeated-text searches.
        start = end = 0
        for line in text.splitlines(keepends=True):
            end += len(line)
            if not line.strip():
                blocks.append(Block(kind='paragraph', span=Span(start=start, end=end)))
                start = end
        if start < end:
            blocks.append(Block(kind='paragraph', span=Span(start=start, end=end)))
    sections, path, boundaries = [], [], []
    if not headings or headings[0][0] != 0:
        boundaries.append((0, ()))
    for start, level, title in headings:
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, title))
        boundaries.append((start, tuple(t for _, t in path)))
    for i, (start, heading_path) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        if end > start:
            sections.append(Section(section_id=uuid5(version_id, f'section:{start}:{end}'),
                document_version_id=version_id, heading_path=heading_path, span=Span(start=start, end=end)))
    nodes, parents = [], []
    for index, (start, level, title) in enumerate(headings):
        section = next(s for s in sections if s.span.start == start)
        end = next((pos for pos, lev, _ in headings[index + 1:] if lev <= level), len(text))
        while parents and parents[-1][0] >= level:
            parents.pop()
        nodes.append(Heading(section_id=section.section_id, parent_section_id=parents[-1][1] if parents else None,
                             level=level, title=title, subtree_span=Span(start=start, end=end)))
        parents.append((level, section.section_id))
    body_kinds = {'paragraph', 'fence', 'code_block', 'html_block', 'table'}
    eligible = tuple(s.span for s in sections if (
        bool(text[s.span.start:s.span.end].strip()) if media_type == 'text/plain' else
        any(b.kind in body_kinds and s.span.start <= b.span.start < s.span.end for b in blocks)))
    return ParsedDocument(text=text, source_map=mapping, sections=tuple(sections), blocks=tuple(blocks),
                          headings=tuple(nodes), eligible_spans=eligible, title=headings[0][2] if headings else '')
