"""Markdown originals and tokenizer chunks, with offsets into the saved source."""

from bisect import bisect_right
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path

from .models import Chunk, ParsedBlock, SourceSpan, fingerprint


def read_source(path: Path) -> tuple[bytes, str]:
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("Only Markdown is supported in S2")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        data = stream.read()
        after = os.fstat(stream.fileno())
    # On Windows Python 3.14, stat/fstat expose different ctime semantics.
    signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if signature(before) != signature(after) or signature(after) != signature(path.stat()):
        raise ValueError("Source changed while copying; import again")
    return data, sha256(data).hexdigest()


def save_original(directory: Path, data: bytes) -> Path:
    temporary = directory / "original.tmp"
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    saved = directory / "original.md"
    temporary.replace(saved)
    saved.chmod(0o444)
    return saved


def parse_markdown(text: str) -> list[ParsedBlock]:
    from markdown_it import MarkdownIt

    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    headings = []
    blocks = []
    tokens = MarkdownIt("commonmark").parse(text)
    kinds = {"heading_open": "heading", "paragraph_open": "paragraph",
             "fence": "code", "code_block": "code", "html_block": "html", "hr": "separator"}
    for index, token in enumerate(tokens):
        if token.type not in kinds or token.map is None:
            continue
        first, last = token.map
        if token.type == "heading_open":
            level = int(token.tag[1:])
            headings = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, tokens[index + 1].content))
        span = SourceSpan(kinds[token.type], [title for _, title in headings],
                          first + 1, last, starts[first], starts[last])
        blocks.append(ParsedBlock(text[span.char_start:span.char_end], span))
    if not blocks:
        raise ValueError("Markdown has no content")
    return blocks


def chunk_markdown(text: str, blocks: list[ParsedBlock], tokenizer, doc_id: str,
                   generation_id: str, max_tokens: int, overlap: int) -> list[Chunk]:
    """Merge adjacent structures; overlap only when splitting an oversized structure.

    Use character offsets, not decoded token slices: a Chinese character may span
    multiple tokens. Every emitted substring is re-tokenized with special tokens.
    """
    def count(value):
        return len(tokenizer(value, add_special_tokens=True)["input_ids"])

    groups = []
    for block in blocks:
        start, end = block.source.char_start, block.source.char_end
        if (groups and block.source.kind != "heading"
                and count(text[groups[-1][0]:end]) <= max_tokens):
            groups[-1] = (groups[-1][0], end)
        else:
            groups.append((start, end))
    line_starts = [0] + [i + 1 for i, char in enumerate(text) if char == "\n"]
    chunks = []
    for start, stop in groups:
        while start < stop:
            remaining = text[start:stop]
            offsets = tokenizer(remaining, add_special_tokens=False,
                                return_offsets_mapping=True)["offset_mapping"]
            end = stop
            if count(remaining) > max_tokens:
                boundaries = sorted({a for a, _ in offsets[:max_tokens + 1] if a > 0})
                while boundaries:
                    end = start + boundaries.pop()
                    if count(text[start:end]) <= max_tokens:
                        break
                else:
                    raise ValueError("chunk_tokens cannot fit one source character")
            value = text[start:end]
            spans = []
            for block in blocks:
                left, right = max(start, block.source.char_start), min(end, block.source.char_end)
                if left < right:
                    spans.append(replace(block.source, char_start=left, char_end=right,
                                         line_start=bisect_right(line_starts, left),
                                         line_end=bisect_right(line_starts, right - 1)))
            ordinal = len(chunks)
            chunks.append(Chunk(fingerprint([doc_id, generation_id, ordinal]), doc_id,
                                generation_id, ordinal, value, spans))
            if end == stop:
                break
            emitted = tokenizer(value, add_special_tokens=False,
                                return_offsets_mapping=True)["offset_mapping"]
            # Do not repeat a partial multi-token character beyond the overlap budget.
            next_start = end
            if overlap and len(emitted) > overlap:
                boundary = emitted[-overlap][0]
                next_start = start + next((a for a, _ in emitted[-overlap:] if a > boundary), len(value))
                if boundary > 0 and all(a < boundary for a, _ in emitted[:-overlap]):
                    next_start = start + boundary
            start = next_start
    return chunks
