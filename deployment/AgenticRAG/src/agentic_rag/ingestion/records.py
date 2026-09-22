"""Immutable input requests and raw checkpoints, separate from parsed versions."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import model_validator

from .._schema import NonNegativeInt, PositiveInt, Record, Sha256, Text, fingerprint
from ..domain import ErrorInfo, SourceMetadata


class InputSelection(Record):
    path: Text
    # Explicit identity updates are single-file requests, never a directory rule.
    document_id: UUID | None = None


class FileStamp(Record):
    device: int
    inode: int
    size_bytes: NonNegativeInt
    modified_ns: int
    changed_ns: int


class InputEntry(Record):
    item_id: UUID
    selection_index: NonNegativeInt
    requested_path: Text
    document_id: UUID | None = None
    source_key: str | None = None
    source_uri: str | None = None
    metadata: SourceMetadata | None = None
    stamp: FileStamp | None = None
    error: ErrorInfo | None = None

    @model_validator(mode='after')
    def complete_selection(self):
        if self.error is None and any(v is None for v in (self.source_key, self.source_uri, self.metadata, self.stamp)):
            raise ValueError('valid input requires path, metadata and observed file state')
        return self


class InputManifest(Record):
    path_policy: Literal['local-physical-v1'] = 'local-physical-v1'
    selections: tuple[InputSelection, ...]
    entries: tuple[InputEntry, ...]

    @model_validator(mode='after')
    def valid_entries(self):
        if len({e.item_id for e in self.entries}) != len(self.entries):
            raise ValueError('item IDs must be unique')
        if any(e.selection_index >= len(self.selections) for e in self.entries):
            raise ValueError('input selection index out of range')
        return self

    @property
    def identity(self) -> str:
        return fingerprint('input-request-manifest-v1', self)


class RawSnapshot(Record):
    sha256: Sha256
    size_bytes: NonNegativeInt
    captured_at: datetime
    source_uri: Text
    source_key: Text
    metadata: SourceMetadata
    stamp: FileStamp

    @model_validator(mode='after')
    def valid_capture(self):
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError('capture timestamp requires timezone')
        if self.size_bytes != self.stamp.size_bytes:
            raise ValueError('capture length differs from stable source length')
        return self


class InputCheckpoint(Record):
    batch_id: UUID
    entry: InputEntry
    document_id: UUID | None = None
    base_version_id: UUID | None = None
    capture_epoch: PositiveInt
    stage: Literal['pending', 'captured', 'failed'] = 'pending'
    raw: RawSnapshot | None = None
    change: Literal['new', 'content_changed', 'source_changed', 'encoding_changed', 'index_changed', 'unchanged'] | None = None
    requires_rebuild_confirmation: bool = False
    error: ErrorInfo | None = None

    @model_validator(mode='after')
    def coherent(self):
        if (self.stage == 'captured') != (self.raw is not None):
            raise ValueError('only complete captured checkpoints contain a raw snapshot')
        if (self.stage == 'failed') != (self.error is not None):
            raise ValueError('failed checkpoints require a file error')
        if (self.stage == 'captured') != (self.change is not None):
            raise ValueError('captured checkpoints require a change classification')
        if self.stage != 'failed' and self.document_id is None:
            raise ValueError('pending or captured input requires a matched document')
        return self
