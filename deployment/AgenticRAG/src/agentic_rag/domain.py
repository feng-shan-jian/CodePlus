"""Immutable identities and records; persistence and state transitions belong to R06+."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, get_args
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ._schema import NonNegativeInt, PositiveInt, Record, Sha256, Text
from .config import ProcessingSnapshot, RunConfiguration


class ErrorCode(StrEnum):
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    CUDA_OUT_OF_MEMORY = "CUDA_OUT_OF_MEMORY"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    CANCELLED = "CANCELLED"
    WORKER_BUSY = "WORKER_BUSY"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"
    WORKER_PROTOCOL = "WORKER_PROTOCOL"
    LIBRARY_BUSY = "LIBRARY_BUSY"
    NOT_READY = "NOT_READY"
    STORAGE_FAILURE = "STORAGE_FAILURE"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    CHECKPOINT_INVALID = "CHECKPOINT_INVALID"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    INVALID_CURSOR = "INVALID_CURSOR"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CITATION_INVALID = "CITATION_INVALID"


class ErrorInfo(Record):
    code: ErrorCode
    stage: Text
    message: Text
    retryable: bool = False
    request_id: UUID | None = None
    call_id: Text | None = None


class RagError(Exception):
    """A diagnosable failure, never an empty successful result or fallback."""

    def __init__(self, code: ErrorCode, message: str, *, stage: str, request_id: UUID | None = None,
                 call_id: str | None = None):
        self.error = ErrorInfo(code=code, stage=stage, message=message, request_id=request_id, call_id=call_id)
        super().__init__(f"{code.value}: {message}")


class IndexState(StrEnum):
    PREPARING = "PREPARING"
    READY = "READY"
    RECLAIMING = "RECLAIMING"
    RECLAIMED = "RECLAIMED"
    FAILED = "FAILED"


class BatchState(StrEnum):
    SNAPSHOTTING = "SNAPSHOTTING"
    PROCESSING = "PROCESSING"
    INDEXING = "INDEXING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    WAITING_RECOVERY = "WAITING_RECOVERY"
    ABANDONED = "ABANDONED"
    COMPLETED_NO_CHANGE = "COMPLETED_NO_CHANGE"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


BudgetStopReason = Literal[
    "token_budget", "time_budget", "search_limit", "open_limit", "iteration_limit", "context_limit",
]

StopReason = Literal[
    "finished", BudgetStopReason,
    "no_evidence", "no_hits", "citation_invalid", "budget", "provider_truncated", "explicit_error",
    "user_cancelled", "consumer_closed", "report_save_failed",
]


class Span(Record):
    """Half-open Unicode codepoint offsets into a version's canonical parsed text."""

    start: NonNegativeInt
    end: PositiveInt

    @model_validator(mode="after")
    def nonempty(self):
        if self.end <= self.start:
            raise ValueError("span requires 0 <= start < end")
        return self


Spans = Annotated[tuple[Span, ...], Field(min_length=1)]


def _ordered_spans(spans: tuple[Span, ...]) -> None:
    if any(left.end > right.start for left, right in zip(spans, spans[1:])):
        raise ValueError("spans must be ordered and non-overlapping")


class KnowledgeBase(Record):
    kb_id: UUID
    name: Text
    current_revision_id: UUID | None = None
    pending_mutation_id: UUID | None = None


class Document(Record):
    document_id: UUID
    kb_id: UUID
    source_key: Text
    source_key_version: Literal[1] = 1
    original_name: Text


class SourceMetadata(Record):
    original_name: Text
    title: str | None = None
    media_type: Literal["text/markdown", "text/plain"]


class DocumentVersion(Record):
    document_version_id: UUID
    document_id: UUID
    raw_hash: Sha256
    parsed_hash: Sha256
    source_map_hash: Sha256
    parser_fingerprint: Sha256
    source_uri: Text
    captured_at: datetime
    source_metadata: SourceMetadata

    @field_validator("captured_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at requires a timezone")
        return value


class Section(Record):
    section_id: UUID
    document_version_id: UUID
    heading_path: tuple[Text, ...] = ()
    span: Span


class Chunk(Record):
    chunk_id: UUID
    document_version_id: UUID
    section_id: UUID
    spans: Spans
    text_hash: Sha256
    chunker_fingerprint: Sha256

    @model_validator(mode="after")
    def valid_spans(self):
        _ordered_spans(self.spans)
        return self


class KnowledgeRevision(Record):
    revision_id: UUID
    kb_id: UUID
    base_revision_id: UUID | None = None
    manifest_hash: Sha256
    processing_snapshot_id: UUID
    index_state: IndexState

    @model_validator(mode="after")
    def distinct_base(self):
        if self.base_revision_id == self.revision_id:
            raise ValueError("revision cannot be its own base")
        return self


class RevisionMember(Record):
    revision_id: UUID
    document_id: UUID
    document_version_id: UUID
    chunk_set_hash: Sha256


class IndexArtifact(Record):
    artifact_id: UUID
    revision_id: UUID
    collection_name: Annotated[str, Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,254}$")]
    schema_hash: Sha256
    owner_epoch: PositiveInt
    state: IndexState


class ImportBatch(Record):
    batch_id: UUID
    kb_id: UUID
    base_revision_id: UUID | None = None
    input_manifest_hash: Sha256
    processing_snapshot_id: UUID
    owner_epoch: PositiveInt
    state: BatchState
    published_revision_id: UUID | None = None
    recovery_stage: Literal["SNAPSHOTTING", "PROCESSING", "INDEXING", "VALIDATING", "READY"] | None = None

    @model_validator(mode="after")
    def coherent_state(self):
        if (self.state == BatchState.PUBLISHED) != (self.published_revision_id is not None):
            raise ValueError("only PUBLISHED batches have a published revision")
        if (self.state == BatchState.WAITING_RECOVERY) != (self.recovery_stage is not None):
            raise ValueError("only WAITING_RECOVERY batches require a recovery stage")
        return self


class CheckpointArtifact(Record):
    kind: Literal["raw", "parsed", "source_map", "chunks", "vectors"]
    sha256: Sha256


class ImportItem(Record):
    batch_id: UUID
    document_id: UUID
    captured_hash: Sha256 | None = None
    stage: Literal["pending", "captured", "parsed", "chunked", "encoded", "failed"]
    output_hashes: tuple[CheckpointArtifact, ...] = ()
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def coherent_checkpoint(self):
        if (self.stage == "failed") != (self.error is not None):
            raise ValueError("failed import items require an error; successful items cannot have one")
        if self.stage not in ("pending", "failed") and self.captured_hash is None:
            raise ValueError("processed items require the captured original hash")
        kinds = [artifact.kind for artifact in self.output_hashes]
        if len(kinds) != len(set(kinds)):
            raise ValueError("checkpoint artifact kinds must be unique")
        return self


class RunUsage(Record):
    searches: NonNegativeInt = 0
    opens: NonNegativeInt = 0
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    cached_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None
    elapsed_ms: NonNegativeInt = 0


class Run(Record):
    run_id: UUID
    parent_run_id: UUID | None = None
    kb_id: UUID
    revision_id: UUID
    resolved_config: RunConfiguration
    resolved_config_hash: Sha256
    usage: RunUsage = Field(default_factory=RunUsage)
    status: RunStatus = RunStatus.RUNNING
    stop_reason: StopReason | None = None

    @model_validator(mode="after")
    def coherent_run(self):
        if self.parent_run_id == self.run_id:
            raise ValueError("run cannot be its own parent")
        if self.resolved_config_hash != self.resolved_config.identity:
            raise ValueError("resolved run config fingerprint mismatch")
        allowed = {
            RunStatus.RUNNING: {None}, RunStatus.COMPLETED: {"finished"},
            RunStatus.PARTIAL: set(get_args(BudgetStopReason)),
            RunStatus.INCOMPLETE: {"no_evidence", "no_hits", "citation_invalid", "budget", "provider_truncated", "report_save_failed"}
                                 | set(get_args(BudgetStopReason)),
            RunStatus.FAILED: {"explicit_error"}, RunStatus.CANCELLED: {"user_cancelled", "consumer_closed"},
        }
        if self.stop_reason not in allowed[self.status]:
            raise ValueError("run status and stop_reason disagree")
        return self


class RunPin(Record):
    run_id: UUID
    revision_id: UUID
    owner_nonce: UUID
    state: Literal["active", "released"] = "active"


class SourceRef(Record):
    kb_id: UUID
    revision_id: UUID
    document_id: UUID
    document_version_id: UUID
    section_id: UUID


class Evidence(Record):
    """Delivered-body record; only the future trusted receipt path may create it."""

    evidence_id: UUID
    run_id: UUID
    delivery_id: UUID
    source_ref: SourceRef
    spans: Spans
    text_hash: Sha256

    @model_validator(mode="after")
    def valid_spans(self):
        _ordered_spans(self.spans)
        return self


class Citation(Record):
    citation_id: UUID
    run_id: UUID
    evidence_id: UUID
    spans: Spans
    quote_hash: Sha256

    @model_validator(mode="after")
    def valid_spans(self):
        _ordered_spans(self.spans)
        return self
