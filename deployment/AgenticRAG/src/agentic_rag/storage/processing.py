"""Atomic acceptance of a complete parsed version and one authoritative ImportItem.

All content reading, hashing, serialization, and validation happens before the
owned SQL transaction. No tokenizer, parser, or filesystem work holds SQL open.
"""

import hashlib
import io
import json
from uuid import UUID, uuid5

from ..domain import CheckpointArtifact, DocumentVersion, ErrorCode, ImportItem, RagError, Section, Chunk
from .paths import failure
from . import ownership


def _validate_result(raw_item, snapshot, version, result, raw):
    from ..ingestion.parsing import canonicalize, PARSER, PARSER_FINGERPRINT
    from ..ingestion.chunking import coverage, chunker_fingerprint, CHUNKER_IMPLEMENTATION, CHUNKER_VERSION
    config = snapshot.resolved_config
    if (config.processing.parser != PARSER or config.processing.chunker.implementation != CHUNKER_IMPLEMENTATION or
            config.processing.chunker.version != CHUNKER_VERSION):
        raise ValueError('original parser/chunker unavailable')
    if len(raw) != raw_item.raw.size_bytes or hashlib.sha256(raw).hexdigest() != raw_item.raw.sha256:
        raise ValueError('captured original differs')
    version_id = uuid5(raw_item.entry.item_id, snapshot.document_encoding_fingerprint)
    canonical, mapping = canonicalize(raw)
    parsed = result.parsed
    if parsed.text != canonical or parsed.source_map != mapping:
        raise ValueError('canonical/source map differs from captured original')
    if any(s.document_version_id != version_id for s in parsed.sections):
        raise ValueError('section version differs from captured input/configuration')
    if any(b.span.end > len(canonical) for b in parsed.blocks):
        raise ValueError('structure block outside canonical text')
    body_kinds = {'paragraph', 'fence', 'code_block', 'html_block', 'table'}
    expected_eligible = tuple(s.span for s in parsed.sections if (
        bool(canonical[s.span.start:s.span.end].strip()) if raw_item.raw.metadata.media_type == 'text/plain' else
        any(b.kind in body_kinds and s.span.start <= b.span.start < s.span.end for b in parsed.blocks)))
    if parsed.eligible_spans != expected_eligible:
        raise ValueError('eligible spans differ from parsed body structure')
    if (version.document_version_id != version_id or version.document_id != raw_item.document_id or
            version.raw_hash != raw_item.raw.sha256 or version.source_uri != raw_item.raw.source_uri or
            version.captured_at != raw_item.raw.captured_at or version.parser_fingerprint != PARSER_FINGERPRINT or
            version.source_metadata != raw_item.raw.metadata.model_copy(update={'title': parsed.title or None}) or
            version.parsed_hash != parsed.source_map.canonical_hash or
            version.source_map_hash != hashlib.sha256(parsed.source_map.model_dump_json().encode()).hexdigest()):
        raise ValueError('document version differs from frozen input/configuration')
    expected = chunker_fingerprint(config.processing.chunker, config.embedding)
    sections = {s.section_id: s for s in parsed.sections}
    previous = {}
    for item in result.inputs:
        chunk = item.chunk
        section = sections.get(chunk.section_id)
        span, = chunk.spans
        frontier = previous.get(chunk.section_id, section.span.start if section else 0)
        if (section is None or chunk.document_version_id != version_id or chunk.chunker_fingerprint != expected or
                chunk.chunk_id != uuid5(version_id, f'chunk:{expected}:{span.start}:{span.end}') or
                item.index_title != ' / '.join(section.heading_path) or
                item.complete_embedding_tokens > min(config.processing.chunker.max_tokens, config.embedding.limits.max_input_tokens) or
                item.overlap_tokens > config.processing.chunker.overlap_tokens or
                item.overlap_codepoints != max(0, frontier - span.start) or span.end <= frontier):
            raise ValueError('chunk identity/template/budget/progress differs from frozen configuration')
        previous[chunk.section_id] = span.end
    coverage(result)


def persist(catalog, owner, raw_item, snapshot, *, version=None, result=None, error=None, produced_by):
    from ..ingestion.chunking import coverage
    batch = catalog.get_batch(owner.token.batch_id)
    stored_snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    if stored_snapshot != snapshot:
        raise failure('processing configuration differs from stored snapshot')
    objects = []
    if result is not None:
        raw = catalog.archives.read(raw_item.raw.sha256)
        _validate_result(raw_item, snapshot, version, result, raw)
        parsed = result.parsed
        if (version.document_id != raw_item.document_id or version.raw_hash != raw_item.raw.sha256 or
                version.source_uri != raw_item.raw.source_uri or version.captured_at != raw_item.raw.captured_at or
                version.source_metadata != raw_item.raw.metadata.model_copy(update={'title': parsed.title or None})):
            raise failure('parsed version differs from captured input')
        values = [('raw', raw),
                  ('parsed', parsed.text.encode('utf-8')),
                  ('source_map', parsed.source_map.model_dump_json().encode('utf-8')),
                  ('chunks', result.model_dump_json().encode('utf-8'))]
        for kind, data in values:
            obj = catalog.archives.put(io.BytesIO(data))
            objects.append((kind, obj))
        hashes = {kind: obj.sha256 for kind, obj in objects}
        if (hashes['parsed'] != version.parsed_hash or hashes['source_map'] != version.source_map_hash or
                hashes['raw'] != version.raw_hash):
            raise failure('parsed archive hashes differ')
        item = ImportItem(batch_id=raw_item.batch_id, document_id=raw_item.document_id,
            captured_hash=raw_item.raw.sha256, stage='chunked', output_hashes=tuple(
                CheckpointArtifact(kind=kind, sha256=obj.sha256) for kind, obj in objects))
        section_rows = [(str(s.section_id), str(owner.token.kb_id), str(s.document_version_id),
                         json.dumps(s.heading_path), s.span.start, s.span.end) for s in parsed.sections]
        chunk_rows = [(str(i.chunk.chunk_id), str(owner.token.kb_id), str(i.chunk.document_version_id),
                       str(i.chunk.section_id), json.dumps([s.model_dump() for s in i.chunk.spans]),
                       i.chunk.text_hash, i.chunk.chunker_fingerprint) for i in result.inputs]
        version_row = (str(version.document_version_id), str(owner.token.kb_id), str(version.document_id),
            version.raw_hash, version.parsed_hash, version.source_map_hash, version.parser_fingerprint,
            version.source_uri, version.captured_at.isoformat(), version.source_metadata.model_dump_json())
    else:
        if error is None:
            raise failure('processing failure requires error')
        item = ImportItem(batch_id=raw_item.batch_id, document_id=raw_item.document_id,
                          captured_hash=raw_item.raw.sha256, stage='failed', error=error)
        hashes = {}
    raw_json, item_json = raw_item.model_dump_json(), item.model_dump_json()
    snapshot_json = snapshot.resolved_config.model_dump_json()
    checkpoint_row = (str(raw_item.entry.item_id), str(owner.token.kb_id), str(raw_item.batch_id),
                      str(raw_item.document_id), str(version.document_version_id) if version else None,
                      str(snapshot.snapshot_id), hashes.get('chunks'), item_json)
    with catalog._owned(owner, produced_by) as connection:
        batch = ownership.read_batch(connection, owner.token.batch_id)
        if batch.batch_id != raw_item.batch_id or batch.processing_snapshot_id != snapshot.snapshot_id:
            raise failure('processing result belongs to another batch or snapshot')
        if connection.execute('SELECT resolved_config,config_fingerprint FROM processing_snapshots WHERE snapshot_id=?',
                              (str(snapshot.snapshot_id),)).fetchone() != (snapshot_json, snapshot.config_fingerprint):
            raise failure('stored processing configuration differs')
        current = connection.execute('SELECT result_json FROM input_results WHERE item_id=? AND batch_id=? AND kb_id=?',
            (str(raw_item.entry.item_id), str(batch.batch_id), str(batch.kb_id))).fetchone()
        if current != (raw_json,):
            raise failure('processing input differs from complete raw checkpoint')
        for _, obj in objects:
            connection.execute('INSERT INTO archive_objects VALUES(?,?) ON CONFLICT DO NOTHING', (obj.sha256, obj.size_bytes))
            if connection.execute('SELECT size_bytes FROM archive_objects WHERE sha256=?', (obj.sha256,)).fetchone() != (obj.size_bytes,):
                raise failure('archive metadata differs')
        if result is not None:
            connection.execute('INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?,?)', version_row)
            connection.executemany('INSERT INTO sections VALUES(?,?,?,?,?,?)', section_rows)
            connection.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)', chunk_rows)
        connection.execute('INSERT INTO processing_items VALUES(?,?,?,?,?,?,?,?)', checkpoint_row)
    return item


def read(catalog, batch_id, item_id, *, _inputs=None):
    """Return verified (ImportItem, DocumentVersion|None, ChunkSet|None), or None.

    A present but invalid checkpoint always fails explicitly; an orphan archive
    or partial version cannot satisfy this API merely by having matching bytes.
    """
    from ..ingestion.chunking import ChunkSet, coverage
    from ..ingestion.parsing import SourceMap, canonicalize, PARSER_FINGERPRINT
    from .inputs import _read_item
    with catalog._db.transaction() as connection:
        batch = ownership.read_batch(connection, batch_id)
        row = connection.execute('SELECT document_id,document_version_id,snapshot_id,chunk_set_hash,item_json '
                                 'FROM processing_items WHERE batch_id=? AND item_id=?', (str(batch_id), str(item_id))).fetchone()
        if row is None:
            return None
        section_rows = connection.execute('SELECT section_id,document_version_id,heading_path,start,end FROM sections WHERE document_version_id=? ORDER BY start', (row[1],)).fetchall()
        chunk_rows = connection.execute('SELECT chunk_id,document_version_id,section_id,spans,text_hash,chunker_fingerprint FROM chunks WHERE document_version_id=?', (row[1],)).fetchall()
    try:
        item = ImportItem.model_validate_json(row[4])
        raw_item = _read_item(catalog, batch_id, item_id, _inputs)
        if (raw_item is None or item.batch_id != batch_id or str(item.document_id) != row[0] or raw_item.document_id != item.document_id or
                raw_item.stage != 'captured' or item.captured_hash != raw_item.raw.sha256 or
                str(batch.processing_snapshot_id) != row[2]):
            raise ValueError('processing checkpoint identity mismatch')
        if item.stage == 'failed':
            if any(row[i] is not None for i in (1, 3)) or item.output_hashes:
                raise ValueError('failure contains complete version')
            return item, None, None
        hashes = {a.kind: a.sha256 for a in item.output_hashes}
        if item.stage != 'chunked' or set(hashes) != {'raw', 'parsed', 'source_map', 'chunks'} or hashes['chunks'] != row[3]:
            raise ValueError('processing checkpoint lacks complete artifacts')
        payload = {kind: catalog.archives.read(digest) for kind, digest in hashes.items()}
        if any(hashlib.sha256(payload[k]).hexdigest() != h for k, h in hashes.items()):
            raise ValueError('returned archive bytes differ')
        canonical, mapping = canonicalize(payload['raw'])
        source_map = SourceMap.model_validate_json(payload['source_map'])
        result = ChunkSet.model_validate_json(payload['chunks'])
        version = catalog.get_version(UUID(row[1]))
        if (canonical != payload['parsed'].decode('utf-8') or result.parsed.text != canonical or
                source_map != mapping or result.parsed.source_map != mapping or
                version.raw_hash != hashes['raw'] or version.parsed_hash != hashes['parsed'] or
                version.source_map_hash != hashes['source_map'] or version.document_id != item.document_id or
                version.parser_fingerprint != PARSER_FINGERPRINT):
            raise ValueError('version, canonical text or exact source map differs')
        sections = tuple(Section(section_id=UUID(r[0]), document_version_id=UUID(r[1]),
                                 heading_path=tuple(json.loads(r[2])), span={'start': r[3], 'end': r[4]}) for r in section_rows)
        chunks = tuple(Chunk(chunk_id=UUID(r[0]), document_version_id=UUID(r[1]), section_id=UUID(r[2]),
                             spans=tuple(json.loads(r[3])), text_hash=r[4], chunker_fingerprint=r[5]) for r in chunk_rows)
        if sections != result.parsed.sections or {c.chunk_id: c for c in chunks} != {i.chunk.chunk_id: i.chunk for i in result.inputs}:
            raise ValueError('complete structure set differs from checkpoint')
        snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
        _validate_result(raw_item, snapshot, version, result, payload['raw'])
        return item, version, result
    except (ValueError, StopIteration, OSError, RagError) as exc:
        raise RagError(ErrorCode.CHECKPOINT_INVALID, f'processing checkpoint invalid: {exc}', stage='processing_checkpoint') from exc
