"""Saved originals, format adapters and one structure/offset tokenizer chunker."""

from bisect import bisect_right
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path

from .models import Chunk, ParsedBlock, SourceSpan, fingerprint


PARSERS = {".md": "markdown-it-4/lines-v1", ".markdown": "markdown-it-4/lines-v1",
           ".pdf": "pypdf-6.19.0/pages-v1", ".docx": "python-docx-1.2.0/body-v1"}


def read_source(path: Path) -> tuple[bytes, str]:
    if path.suffix.lower() not in PARSERS:
        raise ValueError("Supported formats: Markdown, PDF, DOCX; convert legacy .doc to .docx first")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        data = stream.read()
        after = os.fstat(stream.fileno())
    # On Windows Python 3.14, stat/fstat expose different ctime semantics.
    signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if signature(before) != signature(after) or signature(after) != signature(path.stat()):
        raise ValueError("Source changed while copying; import again")
    return data, sha256(data).hexdigest()


def save_original(directory: Path, data: bytes, suffix: str = ".md") -> Path:
    temporary = directory / "original.tmp"
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    saved = directory / f"original{suffix}"
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


def parse_document(original: Path) -> tuple[str, list[ParsedBlock]]:
    """Non-Markdown offsets refer to the extracted text, with native source locations.

    PDF pages are physical, one-based pages. DOCX paragraphs count body paragraphs
    (including empty ones); tables/rows/columns are one-based and never page guesses.
    """
    suffix = original.suffix.lower()
    if suffix in {".md", ".markdown"}:
        text = original.read_bytes().decode("utf-8-sig")
        return text, parse_markdown(text)
    parts, blocks = [], []
    offset = 0

    def append(value, kind, headings=(), **location):
        nonlocal offset
        if not value.strip():
            return
        value += "\n"
        blocks.append(ParsedBlock(value, SourceSpan(kind, list(headings), None, None,
                                  offset, offset + len(value), suffix[1:], **location)))
        parts.append(value)
        offset += len(value)

    if suffix == ".pdf":
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError

        try:
            reader = PdfReader(original, strict=True)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDF is not supported; provide an unencrypted original")
            for page, content in enumerate(reader.pages, 1):
                append(content.extract_text(), "page", page=page)
        except (PyPdfError, OSError, ValueError) as exc:
            raise ValueError(f"Cannot parse PDF: {exc}") from exc
        if not blocks:
            raise ValueError("PDF has no extractable text (empty or scanned); OCR is not supported")
    elif suffix == ".docx":
        from docx import Document
        from docx.text.paragraph import Paragraph

        headings = []
        paragraph = table = 0
        for item in Document(original).iter_inner_content():
            if isinstance(item, Paragraph):
                paragraph += 1
                style = item.style.name if item.style else ""
                kind = "paragraph"
                if style.startswith("Heading ") and style[8:].isdigit():
                    level = int(style[8:])
                    headings = [(depth, title) for depth, title in headings if depth < level]
                    headings.append((level, item.text))
                    kind = "heading"
                elif style == "Title":
                    headings = [(0, item.text)]
                    kind = "heading"
                append(item.text, kind, [title for _, title in headings], paragraph=paragraph)
            else:
                table += 1
                for row, cells in enumerate(item.rows, 1):
                    for column, cell in enumerate(cells.cells, cells.grid_cols_before + 1):
                        append(cell.text, "table_cell", [title for _, title in headings],
                               table=table, row=row, column=column)
        if not blocks:
            raise ValueError("DOCX has no body text")
    else:
        raise ValueError(f"Unsupported document format: {suffix}")
    return "".join(parts), blocks


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
                    lines = ({"line_start": bisect_right(line_starts, left),
                              "line_end": bisect_right(line_starts, right - 1)}
                             if block.source.format == "markdown" else {})
                    spans.append(replace(block.source, char_start=left, char_end=right, **lines))
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
