"""Identity, immutable serialization and invalid persisted-state boundaries."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agentic_rag.domain import (
    BatchState, Chunk, Citation, Document, DocumentVersion, Evidence, ImportBatch,
    ImportItem, IndexArtifact, IndexState, KnowledgeBase, KnowledgeRevision,
    RevisionMember, RunPin, Section, SourceMetadata, SourceRef, Span,
)


def test_document_identity_is_independent_of_content_and_names():
    kb = KnowledgeBase(kb_id=uuid4(), name="same name")
    first = Document(document_id=uuid4(), kb_id=kb.kb_id, source_key="C:/one.md", original_name="one.md")
    second = Document(document_id=uuid4(), kb_id=kb.kb_id, source_key="C:/two.md", original_name="two.md")
    common = dict(raw_hash="a" * 64, parsed_hash="b" * 64, source_map_hash="c" * 64,
                  parser_fingerprint="d" * 64, source_uri="file:///C:/one.md", captured_at=datetime.now(timezone.utc),
                  source_metadata=SourceMetadata(original_name="one.md", media_type="text/markdown"))
    v1 = DocumentVersion(document_version_id=uuid4(), document_id=first.document_id, **common)
    v2 = DocumentVersion(document_version_id=uuid4(), document_id=second.document_id, **common)
    assert v1.raw_hash == v2.raw_hash and v1.document_id != v2.document_id
    assert DocumentVersion.model_validate_json(v1.model_dump_json()) == v1
    with pytest.raises(ValidationError):
        v1.model_copy(update={"captured_at": datetime.now()})
    with pytest.raises(ValidationError):
        first.model_copy(update={"document_id": "a" * 64})


def test_typed_records_roundtrip_without_mutable_collections():
    kb, doc, ver, sec, chunk, rev, batch, run, evidence = [uuid4() for _ in range(9)]
    source = SourceRef(kb_id=kb, revision_id=rev, document_id=doc, document_version_id=ver, section_id=sec)
    span = Span(start=0, end=5)
    objects = [
        Section(section_id=sec, document_version_id=ver, span=span),
        Chunk(chunk_id=chunk, document_version_id=ver, section_id=sec, spans=(span,), text_hash="a" * 64, chunker_fingerprint="b" * 64),
        KnowledgeRevision(revision_id=rev, kb_id=kb, manifest_hash="c" * 64, processing_snapshot_id=uuid4(), index_state=IndexState.PREPARING),
        RevisionMember(revision_id=rev, document_id=doc, document_version_id=ver, chunk_set_hash="d" * 64),
        IndexArtifact(artifact_id=uuid4(), revision_id=rev, collection_name="rag_test", schema_hash="e" * 64, owner_epoch=1, state=IndexState.READY),
        ImportBatch(batch_id=batch, kb_id=kb, input_manifest_hash="f" * 64, processing_snapshot_id=uuid4(), owner_epoch=1, state=BatchState.SNAPSHOTTING),
        ImportItem(batch_id=batch, document_id=doc, stage="pending"),
        RunPin(run_id=run, revision_id=rev, owner_nonce=uuid4()),
        Evidence(evidence_id=evidence, run_id=run, delivery_id=uuid4(), source_ref=source, spans=(span,), text_hash="1" * 64),
        Citation(citation_id=uuid4(), run_id=run, evidence_id=evidence, spans=(span,), quote_hash="2" * 64),
    ]
    for obj in objects:
        assert type(obj).model_validate_json(obj.model_dump_json()) == obj
        with pytest.raises(ValidationError):
            obj.schema_version = 2


@pytest.mark.parametrize("start,end", [(-1, 5), (0, 0), (5, 4), (True, 5), ("0", 5)])
def test_invalid_source_intervals_rejected(start, end):
    with pytest.raises(ValidationError):
        Span(start=start, end=end)


def test_invalid_fields_versions_overlaps_and_states_rejected():
    with pytest.raises(ValidationError):
        Span(start=0, end=1, schema_version=True)
    with pytest.raises(ValidationError):
        Span(start=0, end=1, extra="ignored?")
    with pytest.raises(ValidationError):
        Chunk(chunk_id=uuid4(), document_version_id=uuid4(), section_id=uuid4(),
              spans=(Span(start=3, end=9), Span(start=8, end=10)), text_hash="a" * 64, chunker_fingerprint="b" * 64)
    batch = ImportBatch(batch_id=uuid4(), kb_id=uuid4(), input_manifest_hash="a" * 64,
                        processing_snapshot_id=uuid4(), owner_epoch=1, state=BatchState.READY)
    for change in ({"state": BatchState.PUBLISHED}, {"published_revision_id": uuid4()},
                   {"state": BatchState.WAITING_RECOVERY}, {"owner_epoch": 0}):
        with pytest.raises(ValidationError):
            batch.model_copy(update=change)
    recovered = batch.model_copy(update={"state": BatchState.WAITING_RECOVERY, "recovery_stage": "READY"})
    assert recovered.recovery_stage == "READY"
    with pytest.raises(ValidationError):
        ImportItem(batch_id=uuid4(), document_id=uuid4(), stage="encoded")
