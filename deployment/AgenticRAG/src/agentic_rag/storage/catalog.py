"""Metadata operations needed by capture/parse, kept separate from publication."""

from contextlib import contextmanager
import json
from pathlib import Path
from uuid import UUID, uuid4

from ..config import KnowledgeConfig, ProcessingSnapshot
from ..domain import (Chunk, Document, DocumentVersion, IndexState, KnowledgeBase, KnowledgeRevision,
                      RevisionMember, Section)
from .archives import ArchiveStore
from .database import Database
from . import ownership
from .ownership import Mutation, OwnerToken
from .paths import DataDirectory, failure


class Catalog:
    def __init__(self, data_dir: str | Path, *, busy_timeout_ms: int = 1500):
        self._directory = DataDirectory(data_dir)
        self._db = Database(self._directory, busy_timeout_ms=busy_timeout_ms)
        self.archives = ArchiveStore(self._directory)

    @property
    def store_id(self) -> UUID:
        return self._directory.store_id

    def create_library(self, name: str, *, kb_id: UUID | None = None) -> KnowledgeBase:
        library = KnowledgeBase(kb_id=kb_id or uuid4(), name=name)
        with self._db.transaction(write=True) as connection:
            connection.execute("INSERT INTO libraries(kb_id,name) VALUES(?,?)", (str(library.kb_id), library.name))
        return library

    def get_library(self, kb_id: UUID) -> KnowledgeBase:
        with self._db.transaction() as connection:
            row = connection.execute("SELECT kb_id,name,current_revision_id,pending_mutation_id FROM libraries WHERE kb_id=?", (str(kb_id),)).fetchone()
        if row is None:
            raise failure("knowledge library not found")
        return KnowledgeBase(kb_id=UUID(row[0]), name=row[1], current_revision_id=UUID(row[2]) if row[2] else None,
                             pending_mutation_id=UUID(row[3]) if row[3] else None)

    def begin_mutation(self, kb_id: UUID, snapshot: ProcessingSnapshot, manifest_hash: str, *, batch_id: UUID | None = None) -> Mutation:
        return ownership.begin(self._db, kb_id, batch_id or uuid4(), snapshot, manifest_hash)

    def identify_interrupted(self, kb_id: UUID) -> OwnerToken | None:
        return ownership.identify_interrupted(self._db, kb_id)

    def resume_mutation(self, expected: OwnerToken) -> Mutation:
        return ownership.resume(self._db, expected)

    def get_batch(self, batch_id: UUID):
        with self._db.transaction() as connection:
            return ownership.read_batch(connection, batch_id)

    def get_snapshot(self, snapshot_id: UUID) -> ProcessingSnapshot:
        with self._db.transaction() as connection:
            row = connection.execute("SELECT config_fingerprint,document_encoding_fingerprint,index_fingerprint,resolved_config FROM processing_snapshots WHERE snapshot_id=?", (str(snapshot_id),)).fetchone()
        if row is None:
            raise failure("snapshot not found")
        return ProcessingSnapshot(snapshot_id=snapshot_id, config_fingerprint=row[0], document_encoding_fingerprint=row[1], index_fingerprint=row[2], resolved_config=KnowledgeConfig.model_validate_json(row[3]))

    @contextmanager
    def _owned(self, owner: Mutation, produced_by: OwnerToken | None = None):
        with self._db.transaction(write=True) as connection:
            owner.require(self._db, connection, produced_by)
            yield connection

    def add_document(self, owner: Mutation, document: Document, *, produced_by: OwnerToken | None = None) -> None:
        if document.kb_id != owner.token.kb_id:
            raise failure("document belongs to another library")
        with self._owned(owner, produced_by) as connection:
            connection.execute("INSERT INTO documents VALUES(?,?,?,?,?)", (str(document.document_id), str(document.kb_id), document.source_key, document.source_key_version, document.original_name))

    def get_document(self, document_id: UUID) -> Document:
        with self._db.transaction() as connection:
            row = connection.execute("SELECT document_id,kb_id,source_key,source_key_version,original_name FROM documents WHERE document_id=?", (str(document_id),)).fetchone()
        if row is None:
            raise failure("document not found")
        return Document(document_id=UUID(row[0]), kb_id=UUID(row[1]), source_key=row[2], source_key_version=row[3], original_name=row[4])

    def add_version(self, owner: Mutation, version: DocumentVersion, *, produced_by: OwnerToken | None = None) -> None:
        # Potentially large filesystem reads occur before the short transaction.
        objects = [self.archives.verify(digest) for digest in {version.raw_hash, version.parsed_hash, version.source_map_hash}]
        with self._owned(owner, produced_by) as connection:
            for obj in objects:
                connection.execute("INSERT INTO archive_objects VALUES(?,?) ON CONFLICT(sha256) DO NOTHING", (obj.sha256, obj.size_bytes))
                if connection.execute("SELECT size_bytes FROM archive_objects WHERE sha256=?", (obj.sha256,)).fetchone() != (obj.size_bytes,):
                    raise failure("archive metadata size mismatch")
            connection.execute("INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?,?,?)", (str(version.document_version_id), str(owner.token.kb_id), str(version.document_id), version.raw_hash, version.parsed_hash, version.source_map_hash, version.parser_fingerprint, version.source_uri, version.captured_at.isoformat(), version.source_metadata.model_dump_json()))

    def get_version(self, version_id: UUID) -> DocumentVersion:
        with self._db.transaction() as connection:
            row = connection.execute("SELECT document_version_id,document_id,raw_hash,parsed_hash,source_map_hash,parser_fingerprint,source_uri,captured_at,source_metadata FROM document_versions WHERE document_version_id=?", (str(version_id),)).fetchone()
        if row is None:
            raise failure("document version not found")
        fields = dict(zip(("document_version_id", "document_id", "raw_hash", "parsed_hash", "source_map_hash", "parser_fingerprint", "source_uri", "captured_at", "source_metadata"), row))
        fields["source_metadata"] = json.loads(fields["source_metadata"])
        return DocumentVersion.model_validate_json(json.dumps(fields))

    def add_structure(self, owner: Mutation, sections: tuple[Section, ...], chunks: tuple[Chunk, ...], *, produced_by: OwnerToken | None = None) -> None:
        with self._owned(owner, produced_by) as connection:
            for section in sections:
                connection.execute("INSERT INTO sections VALUES(?,?,?,?,?,?)", (str(section.section_id), str(owner.token.kb_id), str(section.document_version_id), json.dumps(section.heading_path), section.span.start, section.span.end))
            for chunk in chunks:
                connection.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?)", (str(chunk.chunk_id), str(owner.token.kb_id), str(chunk.document_version_id), str(chunk.section_id), json.dumps([span.model_dump() for span in chunk.spans]), chunk.text_hash, chunk.chunker_fingerprint))

    def add_candidate(self, owner: Mutation, revision: KnowledgeRevision, members: tuple[RevisionMember, ...], *, produced_by: OwnerToken | None = None) -> None:
        if revision.kb_id != owner.token.kb_id or revision.index_state != IndexState.PREPARING:
            raise failure("only PREPARING candidates owned by this library may be registered")
        if any(member.revision_id != revision.revision_id for member in members):
            raise failure("candidate member refers to a different revision")
        with self._owned(owner, produced_by) as connection:
            batch = ownership.read_batch(connection, owner.token.batch_id)
            if revision.base_revision_id != batch.base_revision_id or revision.processing_snapshot_id != batch.processing_snapshot_id:
                raise failure("candidate must use its batch base and frozen processing snapshot")
            connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?)", (str(revision.revision_id), str(revision.kb_id), str(revision.base_revision_id) if revision.base_revision_id else None, revision.manifest_hash, str(revision.processing_snapshot_id), revision.index_state.value))
            for member in members:
                connection.execute("INSERT INTO revision_members VALUES(?,?,?,?,?)", (str(revision.kb_id), str(member.revision_id), str(member.document_id), str(member.document_version_id), member.chunk_set_hash))
            connection.execute("INSERT INTO revision_dependencies VALUES(?,?,?,'candidate')", (str(revision.kb_id), str(batch.batch_id), str(revision.revision_id)))

    def retain_revision(self, owner: Mutation, revision_id: UUID, purpose: str, *, produced_by: OwnerToken | None = None) -> None:
        if purpose not in {"recovery", "vector_reuse"}:
            raise ValueError("explicit recovery or vector_reuse purpose required")
        with self._owned(owner, produced_by) as connection:
            state = connection.execute("SELECT index_state FROM revisions WHERE kb_id=? AND revision_id=?", (str(owner.token.kb_id), str(revision_id))).fetchone()
            if state is None or state[0] in {"RECLAIMING", "RECLAIMED"}:
                raise failure("revision unavailable for a new dependency")
            connection.execute("INSERT INTO revision_dependencies VALUES(?,?,?,?) ON CONFLICT DO NOTHING", (str(owner.token.kb_id), str(owner.token.batch_id), str(revision_id), purpose))

    def start_run(self, kb_id: UUID, config, *, run_id: UUID | None = None, parent_run_id: UUID | None = None):
        from .runs import start
        return start(self._db, kb_id, config, run_id=run_id or uuid4(), parent_run_id=parent_run_id)

    def release_crashed_run(self, run_id: UUID, expected_nonce: UUID) -> None:
        from .runs import release_crashed
        release_crashed(self._db, run_id, expected_nonce)

    def get_run(self, run_id: UUID):
        from .runs import read_run
        with self._db.transaction() as connection:
            return read_run(connection, run_id)

    def get_pin(self, run_id: UUID):
        from .runs import read_pin
        with self._db.transaction() as connection:
            return read_pin(connection, run_id)
