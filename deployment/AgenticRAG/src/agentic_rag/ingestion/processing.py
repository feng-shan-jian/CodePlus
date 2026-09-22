"""Actual captured-input parser/chunker/archive path. No source-path rereads."""

import hashlib
from uuid import uuid5

from ..domain import DocumentVersion, ErrorCode, RagError
from ..storage import processing as processing_store
from .capture import read_input
from .chunking import chunk_document, CHUNKER_IMPLEMENTATION, CHUNKER_VERSION
from .parsing import parse_document, PARSER, PARSER_FINGERPRINT


def process_inputs(catalog, owner, tokenizer, *, skip_unchanged=False, cancelled=None):
    token = owner.token
    with catalog._owned(owner, token):
        pass
    batch = catalog.get_batch(token.batch_id)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    config = snapshot.resolved_config
    if (config.processing.parser != PARSER or config.processing.chunker.implementation != CHUNKER_IMPLEMENTATION or
            config.processing.chunker.version != CHUNKER_VERSION or config.embedding.identity != tokenizer.profile.identity):
        raise RagError(ErrorCode.IDENTITY_MISMATCH, 'original processing components/configuration unavailable', stage='process')
    inputs = catalog.get_input_items(token.batch_id)
    if any(i.stage == 'pending' for i in inputs):
        raise RagError(ErrorCode.CHECKPOINT_INVALID, 'finish original input capture before processing', stage='process')
    with catalog._owned(owner, token) as connection:
        connection.execute("UPDATE mutation_batches SET state='PROCESSING' WHERE batch_id=? AND state='SNAPSHOTTING'", (str(token.batch_id),))
    output = []
    for raw_item in inputs:
        if cancelled is not None and cancelled():
            raise RagError(ErrorCode.CANCELLED, 'processing cancelled', stage='process')
        if raw_item.stage != 'captured':
            continue  # R07 failures remain authoritative raw failures.
        if skip_unchanged and raw_item.change in ('unchanged', 'index_changed'):
            continue
        existing = processing_store.read(catalog, token.batch_id, raw_item.entry.item_id)
        if existing is not None:
            output.append(existing[0])
            continue
        with catalog._owned(owner, token):
            pass
        try:
            raw = read_input(catalog, token.batch_id, raw_item.entry.item_id)
            version_id = uuid5(raw_item.entry.item_id, snapshot.document_encoding_fingerprint)
            parsed = parse_document(raw, raw_item.raw.metadata.media_type, version_id)
            result = chunk_document(parsed, config.processing.chunker, tokenizer)
            version = DocumentVersion(document_version_id=version_id, document_id=raw_item.document_id,
                raw_hash=raw_item.raw.sha256, parsed_hash=parsed.source_map.canonical_hash,
                source_map_hash=hashlib.sha256(parsed.source_map.model_dump_json().encode()).hexdigest(),
                parser_fingerprint=PARSER_FINGERPRINT, source_uri=raw_item.raw.source_uri,
                captured_at=raw_item.raw.captured_at, source_metadata=raw_item.raw.metadata.model_copy(update={'title': parsed.title or None}))
        except RagError as exc:
            if exc.error.code in (ErrorCode.CANCELLED, ErrorCode.STORAGE_FAILURE, ErrorCode.LIBRARY_BUSY):
                raise
            output.append(processing_store.persist(catalog, owner, raw_item, snapshot, error=exc.error, produced_by=token))
            continue
        # Acceptance failures propagate; never overwrite uncertain committed
        # success with a synthetic file failure or hide loss of ownership.
        output.append(processing_store.persist(catalog, owner, raw_item, snapshot, version=version,
                                              result=result, produced_by=token))
    return tuple(output)


def read_processed(catalog, batch_id, item_id):
    return processing_store.read(catalog, batch_id, item_id)
