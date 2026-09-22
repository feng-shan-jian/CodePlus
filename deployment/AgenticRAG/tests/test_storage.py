"""Relational integrity, transaction boundaries, ownership and fixed run binding."""

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import runpy
import time
from uuid import uuid4

import apsw
import pytest

from agentic_rag.config import resolve_run
from agentic_rag.domain import (BatchState, Chunk, Document, DocumentVersion, IndexState, KnowledgeRevision,
                               RagError, RevisionMember, RunStatus, Section, SourceMetadata, Span)
from agentic_rag.storage import Catalog
from agentic_rag.storage.database import require_runtime, runtime_fingerprint
from agentic_rag.storage.locks import ProcessLock

HELPER = runpy.run_path(str(Path(__file__).with_name("storage_process_helper.py")))
config, snapshot, seed_ready = HELPER["config"], HELPER["snapshot"], HELPER["seed_ready"]
HASH = "a" * 64


@pytest.fixture
def setup(tmp_path):
    catalog = Catalog(tmp_path / "data")
    library = catalog.create_library("test")
    with catalog.begin_mutation(library.kb_id, snapshot(), HASH) as owner:
        yield catalog, library, owner


def add_version(catalog, library, owner, name="source.txt"):
    doc = Document(document_id=uuid4(), kb_id=library.kb_id, source_key=name, original_name=name)
    catalog.add_document(owner, doc)
    hashes = [catalog.archives.put(io.BytesIO(content)).sha256 for content in (b"raw", b"parsed", b"map")]
    version = DocumentVersion(document_version_id=uuid4(), document_id=doc.document_id, raw_hash=hashes[0], parsed_hash=hashes[1], source_map_hash=hashes[2], parser_fingerprint=HASH, source_uri=name, captured_at=datetime.now(timezone.utc), source_metadata=SourceMetadata(original_name=name, media_type="text/plain"))
    catalog.add_version(owner, version)
    return doc, version


def test_runtime_and_connection_requirements(setup, monkeypatch):
    catalog, _, _ = setup
    require_runtime()
    with catalog._db.transaction() as connection:
        assert connection.pragma("journal_mode") == "wal"
        assert connection.pragma("synchronous") == 2
        assert connection.pragma("foreign_keys") == 1
        assert connection.pragma("user_version") == 6
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone() == (6,)
    monkeypatch.setattr(apsw, "sqlitelibversion", lambda: "3.50.4")
    with pytest.raises(RagError, match="WAL-reset"):
        require_runtime()


def test_metadata_roundtrip_reopen_and_full_snapshot(setup):
    catalog, library, owner = setup
    doc, version = add_version(catalog, library, owner)
    section = Section(section_id=uuid4(), document_version_id=version.document_version_id, span=Span(start=0, end=6))
    chunk = Chunk(chunk_id=uuid4(), document_version_id=version.document_version_id, section_id=section.section_id, spans=(section.span,), text_hash=HASH, chunker_fingerprint=HASH)
    catalog.add_structure(owner, (section,), (chunk,))
    batch = catalog.get_batch(owner.token.batch_id)
    revision = KnowledgeRevision(revision_id=uuid4(), kb_id=library.kb_id, manifest_hash=HASH, processing_snapshot_id=batch.processing_snapshot_id, index_state=IndexState.PREPARING)
    member = RevisionMember(revision_id=revision.revision_id, document_id=doc.document_id, document_version_id=version.document_version_id, chunk_set_hash=HASH)
    catalog.add_candidate(owner, revision, (member,))
    catalog.retain_revision(owner, revision.revision_id, "vector_reuse")
    reopened = Catalog(catalog._directory.root)
    assert reopened.store_id == catalog.store_id
    assert reopened.get_document(doc.document_id) == doc
    assert reopened.get_version(version.document_version_id) == version
    assert reopened.get_snapshot(batch.processing_snapshot_id).resolved_config == config()
    with reopened._db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM revision_members").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM revision_dependencies").fetchone() == (2,)


def test_owner_release_pending_resume_epoch_late_result(setup):
    catalog, library, old = setup
    old_token = old.token
    old.close()
    doc = Document(document_id=uuid4(), kb_id=library.kb_id, source_key="a", original_name="a")
    with pytest.raises(RagError, match="live lock"):
        catalog.add_document(old, doc)
    with pytest.raises(RagError, match="unfinished batch"):
        catalog.begin_mutation(library.kb_id, snapshot(), HASH)
    expected = catalog.identify_interrupted(library.kb_id)
    assert expected == old_token
    assert catalog.get_batch(old_token.batch_id).state == BatchState.WAITING_RECOVERY
    with catalog.resume_mutation(expected) as owner:
        assert owner.token.owner_epoch == old_token.owner_epoch + 1
        assert owner.token.owner_nonce != old_token.owner_nonce
        with pytest.raises(RagError, match="late result"):
            catalog.add_document(owner, doc, produced_by=old_token)
        catalog.add_document(owner, doc, produced_by=owner.token)
        owner.abandon()
    with pytest.raises(RagError, match="changed"):
        catalog.resume_mutation(expected)
    with catalog.begin_mutation(library.kb_id, snapshot(), HASH) as next_owner:
        assert next_owner.token.owner_epoch == old_token.owner_epoch + 2
        next_owner.abandon()


def test_owner_cannot_cross_catalog_or_library(setup, tmp_path):
    catalog, library, owner = setup
    other = Catalog(tmp_path / "other")
    doc = Document(document_id=uuid4(), kb_id=library.kb_id, source_key="a", original_name="a")
    with pytest.raises(RagError, match="different catalog"):
        other.add_document(owner, doc)
    wrong = doc.model_copy(update={"kb_id": uuid4()})
    with pytest.raises(RagError, match="another library"):
        catalog.add_document(owner, wrong)
    with pytest.raises(RagError, match="lock is held"):
        catalog.identify_interrupted(library.kb_id)


def test_composite_constraints_and_transaction_rollback(setup):
    catalog, library, owner = setup
    doc, version = add_version(catalog, library, owner)
    other_doc, other_version = add_version(catalog, library, owner, "other.txt")
    section = Section(section_id=uuid4(), document_version_id=version.document_version_id, span=Span(start=0, end=4))
    wrong_chunk = Chunk(chunk_id=uuid4(), document_version_id=other_version.document_version_id, section_id=section.section_id, spans=(section.span,), text_hash=HASH, chunker_fingerprint=HASH)
    with pytest.raises(RagError, match="ConstraintError"):
        catalog.add_structure(owner, (section,), (wrong_chunk,))
    with catalog._db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM sections").fetchone() == (0,)
    revision = KnowledgeRevision(revision_id=uuid4(), kb_id=library.kb_id, manifest_hash=HASH, processing_snapshot_id=catalog.get_batch(owner.token.batch_id).processing_snapshot_id, index_state=IndexState.PREPARING)
    bad_member = RevisionMember(revision_id=revision.revision_id, document_id=doc.document_id, document_version_id=other_version.document_version_id, chunk_set_hash=HASH)
    with pytest.raises(RagError, match="ConstraintError"):
        catalog.add_candidate(owner, revision, (bad_member,))
    with catalog._db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM revisions").fetchone() == (0,)
    second_lib = catalog.create_library("second")
    with pytest.raises(RagError, match="ConstraintError"):
        with catalog._db.transaction(write=True) as connection:
            connection.execute("UPDATE documents SET kb_id=? WHERE document_id=?", (str(second_lib.kb_id), str(other_doc.document_id)))


@pytest.mark.parametrize("table,column,value", [("document_versions", "source_uri", "other"), ("processing_snapshots", "config_fingerprint", HASH)])
def test_immutable_history(setup, table, column, value):
    catalog, library, owner = setup
    add_version(catalog, library, owner)
    with pytest.raises(RagError, match="immutable"):
        with catalog._db.transaction(write=True) as connection:
            connection.execute(f"UPDATE {table} SET {column}=?", (value,))


def test_candidate_cannot_publish_or_use_another_snapshot(setup):
    catalog, library, owner = setup
    revision = KnowledgeRevision(revision_id=uuid4(), kb_id=library.kb_id, manifest_hash=HASH, processing_snapshot_id=snapshot().snapshot_id, index_state=IndexState.READY)
    with pytest.raises(RagError, match="PREPARING"):
        catalog.add_candidate(owner, revision, ())
    with pytest.raises(RagError, match="frozen"):
        catalog.add_candidate(owner, revision.model_copy(update={"index_state": IndexState.PREPARING}), ())
    assert not hasattr(catalog, "set_current_pointer")
    assert catalog.get_library(library.kb_id).current_revision_id is None


def test_bounded_busy_and_reader_can_progress(setup):
    catalog, library, owner = setup
    contender = Catalog(catalog._directory.root, busy_timeout_ms=80)
    with catalog._db.transaction(write=True):
        assert contender.get_library(library.kb_id).pending_mutation_id == owner.token.batch_id
        started = time.monotonic()
        with pytest.raises(RagError, match="busy timeout"):
            contender.create_library("blocked")
        assert 0.04 <= time.monotonic() - started < 2
    assert contender.create_library("unblocked")


@pytest.mark.parametrize("pragma,value", [("user_version", 7), ("application_id", 123)])
def test_reject_unknown_schema_before_mutation(tmp_path, pragma, value):
    data = tmp_path / "data"
    catalog = Catalog(data)
    with catalog._db.transaction(write=True) as connection:
        connection.pragma(pragma, value)
    with pytest.raises(RagError, match="unknown"):
        Catalog(data)
    with apsw.Connection(str(data / "catalog.sqlite")) as connection:
        assert connection.pragma(pragma) == value


def test_reject_database_from_different_owned_directory(tmp_path):
    import shutil
    first, second = Catalog(tmp_path / "one"), Catalog(tmp_path / "two")
    shutil.copyfile(first._directory.root / "catalog.sqlite", second._directory.root / "catalog.sqlite")
    with pytest.raises(RagError, match="identity"):
        Catalog(second._directory.root)


@pytest.mark.parametrize("path", ["relative", "../escape", "//server/share", "C:relative", "/linux-only"] if os.name == "nt" else ["relative", "../escape", "//server/share"])
def test_path_invalid_for_actual_os(path):
    with pytest.raises(RagError):
        Catalog(path)


def test_unowned_directory_and_symlink_rejected(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    user_file = existing / "user.txt"
    user_file.write_text("preserve")
    with pytest.raises(RagError, match="unowned"):
        Catalog(existing)
    assert user_file.read_text() == "preserve"
    catalog = Catalog(tmp_path / "owned")
    # Windows junction needs no symlink privilege; use OS command with exact paths.
    link = tmp_path / "junction"
    if os.name == "nt":
        import subprocess
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(catalog._directory.root)], capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(catalog._directory.root, target_is_directory=True)
    try:
        with pytest.raises(RagError, match="reparse|links"):
            Catalog(link)
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_run_pins_whole_revision_and_cannot_bind_reclaiming(tmp_path):
    catalog = Catalog(tmp_path / "data")
    library = catalog.create_library("runs")
    run_id = uuid4()
    with pytest.raises(RagError, match="queryable"):
        catalog.start_run(library.kb_id, resolve_run(config(), "qa"), run_id=run_id)
    revision_id = seed_ready(catalog, library.kb_id)
    with catalog.start_run(library.kb_id, resolve_run(config(), "qa"), run_id=run_id) as lease:
        assert catalog.get_pin(run_id).revision_id == revision_id
        with pytest.raises(RagError, match="terminal"):
            lease.finish(RunStatus.RUNNING, None)
        assert catalog.get_pin(run_id).state == "active"
        with pytest.raises(RagError, match="lock is held"):
            catalog.release_crashed_run(run_id, lease.pin.owner_nonce)
        with catalog._db.transaction(write=True) as connection:
            connection.execute("UPDATE revisions SET index_state='RECLAIMING' WHERE revision_id=?", (str(revision_id),))
        with pytest.raises(RagError, match="queryable"):
            catalog.start_run(library.kb_id, resolve_run(config(), "qa"))
        assert catalog.get_run(run_id).revision_id == revision_id
        lease.finish(RunStatus.COMPLETED, "finished")
    assert catalog.get_pin(run_id).state == "released"
    with pytest.raises(RagError, match="nonce"):
        catalog.release_crashed_run(run_id, uuid4())


def test_same_process_os_lock_and_failed_acquisition_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "lock"
    with ProcessLock(path):
        with pytest.raises(RagError, match="held"):
            ProcessLock(path).acquire()
    lock = ProcessLock(path)
    original = ProcessLock.check
    monkeypatch.setattr(lock, "check", lambda: (_ for _ in ()).throw(RuntimeError("injected identity failure")))
    with pytest.raises(RuntimeError):
        lock.acquire()
    assert lock._fd is None
    with ProcessLock(path) as replacement:
        original(replacement)


@pytest.mark.parametrize("state,allowed", [("PREPARING", True), ("READY", True), ("FAILED", True), ("RECLAIMING", False), ("RECLAIMED", False)])
def test_dependency_cannot_be_added_after_reclamation_claim(setup, state, allowed):
    catalog, library, owner = setup
    revision = KnowledgeRevision(revision_id=uuid4(), kb_id=library.kb_id, manifest_hash=HASH, processing_snapshot_id=catalog.get_batch(owner.token.batch_id).processing_snapshot_id, index_state=IndexState.PREPARING)
    catalog.add_candidate(owner, revision, ())
    with catalog._db.transaction(write=True) as connection:
        connection.execute("UPDATE revisions SET index_state=? WHERE revision_id=?", (state, str(revision.revision_id)))
    if allowed:
        catalog.retain_revision(owner, revision.revision_id, "recovery")
    else:
        with pytest.raises(RagError, match="unavailable"):
            catalog.retain_revision(owner, revision.revision_id, "recovery")


def test_historical_version_structure_is_sealed_for_new_batch(setup):
    catalog, library, owner = setup
    doc, version = add_version(catalog, library, owner)
    section = Section(section_id=uuid4(), document_version_id=version.document_version_id, span=Span(start=0, end=4))
    catalog.add_structure(owner, (section,), ())
    revision = KnowledgeRevision(revision_id=uuid4(), kb_id=library.kb_id, manifest_hash=HASH, processing_snapshot_id=catalog.get_batch(owner.token.batch_id).processing_snapshot_id, index_state=IndexState.PREPARING)
    member = RevisionMember(revision_id=revision.revision_id, document_id=doc.document_id, document_version_id=version.document_version_id, chunk_set_hash=HASH)
    catalog.add_candidate(owner, revision, (member,))
    # Synthetic historical READY metadata, not verified Milvus publication.
    with catalog._db.transaction(write=True) as connection:
        connection.execute("UPDATE revisions SET index_state='READY' WHERE revision_id=?", (str(revision.revision_id),))
    owner.abandon()
    with catalog.begin_mutation(library.kb_id, snapshot(), HASH) as next_owner:
        with pytest.raises(RagError, match="sealed"):
            catalog.add_structure(next_owner, (section.model_copy(update={"section_id": uuid4()}),), ())
        chunk = Chunk(chunk_id=uuid4(), document_version_id=version.document_version_id, section_id=section.section_id, spans=(section.span,), text_hash=HASH, chunker_fingerprint=HASH)
        with pytest.raises(RagError, match="sealed"):
            catalog.add_structure(next_owner, (), (chunk,))


def test_existing_run_keeps_full_revision_when_current_changes(tmp_path):
    catalog = Catalog(tmp_path / "data")
    library = catalog.create_library("fixed")
    before = seed_ready(catalog, library.kb_id)
    with catalog.start_run(library.kb_id, resolve_run(config(), "qa")) as first:
        after = seed_ready(catalog, library.kb_id)
        with catalog.start_run(library.kb_id, resolve_run(config(), "qa")) as second:
            assert first.run.revision_id == before != after == second.run.revision_id
            assert catalog.get_pin(first.run.run_id).revision_id == before
            assert catalog.get_pin(second.run.run_id).revision_id == after
            with catalog._db.transaction() as connection:
                assert connection.execute("SELECT count(*) FROM run_pins WHERE state='active'").fetchone() == (2,)


def test_marker_strict_version_and_changed_identity_rejected(tmp_path):
    catalog = Catalog(tmp_path / "data")
    marker = catalog._directory.path(catalog._directory.MARKER)
    original = json.loads(marker.read_text())
    marker.write_text(json.dumps({**original, "version": True}))
    with pytest.raises(RagError, match="ownership marker"):
        Catalog(catalog._directory.root)
    with pytest.raises(RagError, match="identity"):
        catalog.create_library("cannot write")
    marker.write_text(json.dumps({**original, "store_id": str(uuid4())}))
    with pytest.raises(RagError, match="identity"):
        Catalog(catalog._directory.root)


def test_cross_library_version_snapshot_and_current_pointer_constraints(setup):
    catalog, library, owner = setup
    _, version = add_version(catalog, library, owner)
    other = catalog.create_library("other")
    with catalog.begin_mutation(other.kb_id, snapshot(), HASH) as other_owner:
        with pytest.raises(RagError, match="ConstraintError"):
            catalog.add_version(other_owner, version.model_copy(update={"document_version_id": uuid4()}))
        rev = KnowledgeRevision(revision_id=uuid4(), kb_id=other.kb_id, manifest_hash=HASH, processing_snapshot_id=catalog.get_batch(other_owner.token.batch_id).processing_snapshot_id, index_state=IndexState.PREPARING)
        catalog.add_candidate(other_owner, rev, ())
        with pytest.raises(RagError, match="ConstraintError"):
            with catalog._db.transaction(write=True) as connection:
                connection.execute("UPDATE libraries SET current_revision_id=? WHERE kb_id=?", (str(rev.revision_id), str(library.kb_id)))
        with pytest.raises(RagError, match="ConstraintError"):
            with catalog._db.transaction(write=True) as connection:
                connection.execute("INSERT INTO revisions VALUES(?,?,NULL,?,?,'PREPARING')", (str(uuid4()), str(library.kb_id), HASH, str(catalog.get_batch(other_owner.token.batch_id).processing_snapshot_id)))
