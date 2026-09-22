"""Production index identity derived only from validated processing checkpoints."""

from dataclasses import dataclass
import hashlib
import math
import struct
from uuid import UUID

from .._schema import fingerprint
from ..domain import RagError, ErrorCode, RevisionMember
from ..ingestion.processing import read_processed

TEXT_MAX_BYTES = 65535
LAYOUT = 'uuid1024-bm25-utf8-v1'
TEXT_TRANSFORM = 'heading-newline-canonical-body-v1'
UUID_FIELDS = ('chunk_id', 'kb_id', 'revision_id', 'document_id', 'document_version_id', 'section_id')
HASH_FIELDS = ('text_hash', 'body_hash', 'raw_hash', 'parsed_hash', 'encoding_hash', 'vector_hash')
SCALAR_FIELDS = (*UUID_FIELDS, *HASH_FIELDS, 'text', 'span_start', 'span_end')


def index_error(message, stage='index'):
    return RagError(ErrorCode.INVALID_RESPONSE, message, stage=stage)


def text_for_index(title, body):
    value = title + '\n' + body if title else body
    size = len(value.encode('utf-8'))
    if size > TEXT_MAX_BYTES:
        raise RagError(ErrorCode.INPUT_TOO_LONG, f'index text UTF-8 bytes {size} exceeds {TEXT_MAX_BYTES}; no truncation', stage='index_text')
    return value


def vector_hash(vector):
    if len(vector) != 1024 or any(not math.isfinite(v) for v in vector):
        raise index_error('Dense vector must contain 1024 finite values')
    if not math.isclose(math.sqrt(sum(v*v for v in vector)), 1.0, abs_tol=1e-4):
        raise index_error('Dense vector must be L2 normalized')
    return hashlib.sha256(struct.pack('<1024f', *vector)).hexdigest()


def collection_name(namespace, store_id, kb_id, revision_id, epoch):
    return f'ar_{namespace}_{store_id.hex}_{kb_id.hex}_{revision_id.hex}_e{epoch}'


def schema_spec(snapshot):
    return {'layout': LAYOUT, 'text_transform': TEXT_TRANSFORM, 'text_max_bytes': TEXT_MAX_BYTES,
            'dimension': 1024, 'uuid_fields': list(UUID_FIELDS), 'hash_fields': list(HASH_FIELDS),
            'dynamic': False, 'consistency': 'Strong',
            'index': snapshot.resolved_config.processing.index.model_dump(mode='json')}


@dataclass(frozen=True)
class PreparedRevision:
    revision_id: UUID
    members: tuple
    rows: tuple
    model_inputs: tuple
    token_counts: tuple
    manifest_hash: str
    schema_hash: str
    spec: dict


def prepare(catalog, batch_id, revision_id):
    """Full first-import only. Filesystem/hash checks finish before any SQL write."""
    from ..capabilities import ModelInput
    batch = catalog.get_batch(batch_id)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    members, rows, inputs, counts = [], [], [], []
    raw_items = catalog.get_input_items(batch_id)
    if not raw_items:
        raise index_error('cannot publish an empty first import')
    for raw in raw_items:
        if raw.stage != 'captured':
            raise index_error('first import requires all original snapshots', 'manifest')
        checked = read_processed(catalog, batch_id, raw.entry.item_id)
        if checked is None or checked[0].stage != 'chunked':
            raise index_error('first import requires all verified processing checkpoints', 'manifest')
        item, version, result = checked
        chunks_hash = next(a.sha256 for a in item.output_hashes if a.kind == 'chunks')
        members.append(RevisionMember(revision_id=revision_id, document_id=version.document_id,
                                      document_version_id=version.document_version_id, chunk_set_hash=chunks_hash))
        for entry in result.inputs:
            chunk = entry.chunk
            span, = chunk.spans
            body = result.parsed.text[span.start:span.end]
            text = text_for_index(entry.index_title, body)
            row = dict(chunk_id=str(chunk.chunk_id), kb_id=str(batch.kb_id), revision_id=str(revision_id),
                       document_id=str(version.document_id), document_version_id=str(version.document_version_id),
                       section_id=str(chunk.section_id), text=text,
                       text_hash=hashlib.sha256(text.encode()).hexdigest(), body_hash=chunk.text_hash,
                       raw_hash=version.raw_hash, parsed_hash=version.parsed_hash,
                       encoding_hash=snapshot.document_encoding_fingerprint, span_start=span.start, span_end=span.end)
            rows.append(row)
            inputs.append(ModelInput(item_id=chunk.chunk_id, title=entry.index_title or None, text=body))
            counts.append(entry.complete_embedding_tokens)
    if not rows or len({r['chunk_id'] for r in rows}) != len(rows):
        raise index_error('candidate requires nonempty unique chunks')
    ordered = sorted(zip(rows, inputs, counts), key=lambda t: t[0]['chunk_id'])
    rows, inputs, counts = map(tuple, zip(*ordered))
    members = tuple(sorted(members, key=lambda m: str(m.document_id)))
    spec = schema_spec(snapshot)
    manifest = fingerprint('publication-manifest', {'batch_id': str(batch_id),
        'input_manifest_hash': batch.input_manifest_hash, 'snapshot': snapshot.config_fingerprint,
        'members': [m.model_dump(mode='json') for m in members], 'rows': list(rows), 'schema': spec})
    return PreparedRevision(revision_id, members, rows, inputs, counts, manifest,
                            fingerprint('milvus-schema', spec), spec)
