"""Versioned source reads. No current filesystem path or search service access."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from .domain import ErrorCode, RagError, SourceRef, Span
from .ingestion.chunking import ChunkSet
from .ingestion.parsing import SourceMap, canonicalize


def invalid(message, stage='source'):
    code={'source_scope':ErrorCode.SCOPE_MISMATCH,'source_cursor':ErrorCode.INVALID_CURSOR,
          'source_budget':ErrorCode.BUDGET_EXHAUSTED,'source_archive':ErrorCode.CHECKPOINT_INVALID,
          'source_search':ErrorCode.CAPABILITY_UNAVAILABLE,'citation':ErrorCode.CITATION_INVALID,
          'citation_history':ErrorCode.CITATION_INVALID}.get(stage,ErrorCode.EVIDENCE_INVALID)
    return RagError(code, message, stage=stage)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def contains(outer, inner):
    """A continuous span cannot bridge a gap in delivered source."""
    cursor = inner.start
    for span in outer:
        if span.end <= cursor:
            continue
        if span.start > cursor:
            return False
        cursor = max(cursor, span.end)
        if cursor >= inner.end:
            return True
    return False


def union(spans):
    merged = []
    for span in sorted(spans, key=lambda s: (s.start, s.end)):
        if merged and span.start <= merged[-1].end:
            merged[-1] = Span(start=merged[-1].start, end=max(merged[-1].end, span.end))
        else:
            merged.append(span)
    return tuple(merged)


@dataclass(frozen=True)
class ArchivedSource:
    version: object
    chunks: ChunkSet

    @property
    def text(self):
        return self.chunks.parsed.text

    def section(self, section_id):
        result = next((s for s in self.chunks.parsed.sections if s.section_id == section_id), None)
        if result is None:
            raise invalid('section is not part of the fixed document version')
        return result


def read_version(catalog, kb_id, revision_id, document_id, version_id):
    """Authenticate the complete archived structure against immutable membership."""
    with catalog._db.transaction() as connection:
        row = connection.execute('SELECT chunk_set_hash FROM revision_members WHERE kb_id=? AND revision_id=? AND document_id=? AND document_version_id=?',
            tuple(str(v) for v in (kb_id, revision_id, document_id, version_id))).fetchone()
        sections = list(connection.execute('SELECT section_id,heading_path,start,end FROM sections WHERE kb_id=? AND document_version_id=? ORDER BY start',
            (str(kb_id), str(version_id))))
    if row is None:
        raise invalid('source is not a member of the fixed revision','source_scope')
    try:
        version = catalog.get_version(version_id)
        chunks = ChunkSet.model_validate_json(catalog.archives.read(row[0]))
        text = catalog.archives.read(version.parsed_hash).decode('utf-8')
        mapping = SourceMap.model_validate_json(catalog.archives.read(version.source_map_hash))
        canonical, raw_map = canonicalize(catalog.archives.read(version.raw_hash))
        archived_sections = [(str(s.section_id), list(s.heading_path), s.span.start, s.span.end)
                             for s in chunks.parsed.sections]
        stored_sections = [(s[0], json.loads(s[1]), s[2], s[3]) for s in sections]
        if (version.document_id != document_id or text != canonical or chunks.parsed.text != text or
                mapping != raw_map or chunks.parsed.source_map != mapping or archived_sections != stored_sections or
                any(s.document_version_id != version_id for s in chunks.parsed.sections)):
            raise ValueError('archived source identity, canonical map or structure differs')
        return ArchivedSource(version, chunks)
    except (ValueError, OSError, RagError) as exc:
        raise invalid(f'archive unavailable or corrupt: {exc}', 'source_archive') from exc


def read_ref(catalog, ref: SourceRef):
    result = read_version(catalog, ref.kb_id, ref.revision_id, ref.document_id, ref.document_version_id)
    result.section(ref.section_id)
    return result
