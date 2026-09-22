"""Actual filesystem operations plus explicitly labelled failure injection."""

import hashlib
import io
from pathlib import Path
import runpy

import pytest

from agentic_rag.domain import RagError
from agentic_rag.storage import Catalog
import agentic_rag.storage.archives as archives_module

HELPER = runpy.run_path(str(Path(__file__).with_name("test_storage.py")))


def test_reuse_same_bytes_for_distinct_purposes_and_history(tmp_path):
    catalog = Catalog(tmp_path / "data")
    content = b"immutable\x00original"
    expected = hashlib.sha256(content).hexdigest()
    first = catalog.archives.put(io.BytesIO(content), expected_hash=expected)
    second = catalog.archives.put(io.BytesIO(content))
    assert first == second
    assert catalog.archives.read(first.sha256) == content
    newer = catalog.archives.put(io.BytesIO(b"updated"))
    assert newer.sha256 != first.sha256
    assert catalog.archives.read(first.sha256) == content
    assert list((tmp_path / "data/staging").iterdir()) == []


def test_corruption_is_rejected_and_never_replaced(tmp_path):
    catalog = Catalog(tmp_path / "data")
    obj = catalog.archives.put(io.BytesIO(b"old"))
    path = catalog.archives._path(obj.sha256)
    path.write_bytes(b"corrupt")  # explicit external corruption injection
    with pytest.raises(RagError, match="checksum"):
        catalog.archives.put(io.BytesIO(b"old"))
    assert path.read_bytes() == b"corrupt"
    with pytest.raises(RagError, match="checksum"):
        catalog.archives.read(obj.sha256)


@pytest.mark.parametrize("stage", ["read", "fsync", "complete"])
def test_injected_io_failure_no_metadata_or_partial_final(tmp_path, monkeypatch, stage):
    catalog = Catalog(tmp_path / "data")
    content = b"new complete data"
    digest = hashlib.sha256(content).hexdigest()
    source = io.BytesIO(content)

    def fail(*_):
        raise OSError("injected IO failure, not real disk-full or power-loss")

    if stage == "read":
        source.read = fail
    elif stage == "fsync":
        monkeypatch.setattr(archives_module.os, "fsync", fail)
    else:
        monkeypatch.setattr(archives_module, "_complete", fail)
    with pytest.raises(OSError, match="injected"):
        catalog.archives.put(source)
    assert not catalog.archives._path(digest).exists()
    assert list(catalog._directory.path("staging").iterdir()) == []
    with catalog._db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM archive_objects").fetchone() == (0,)


def test_hash_mismatch_and_completed_orphan(tmp_path):
    catalog = Catalog(tmp_path / "data")
    with pytest.raises(RagError, match="expected hash"):
        catalog.archives.put(io.BytesIO(b"captured"), expected_hash="0" * 64)
    obj = catalog.archives.put(io.BytesIO(b"complete orphan"))
    reopened = Catalog(catalog._directory.root)
    assert reopened.archives.verify(obj.sha256) == obj
    with reopened._db.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM archive_objects").fetchone() == (0,)


def test_reference_requires_completed_verified_objects(tmp_path):
    catalog = Catalog(tmp_path / "data")
    library = catalog.create_library("references")
    with catalog.begin_mutation(library.kb_id, HELPER["snapshot"](), "a" * 64) as owner:
        doc, version = HELPER["add_version"](catalog, library, owner)
        missing = version.model_copy(update={"document_version_id": __import__("uuid").uuid4(), "raw_hash": "0" * 64})
        with pytest.raises(RagError, match="unavailable"):
            catalog.add_version(owner, missing)
        catalog.archives._path(version.raw_hash).write_bytes(b"bad")
        with pytest.raises(RagError, match="checksum"):
            catalog.add_version(owner, version.model_copy(update={"document_version_id": __import__("uuid").uuid4()}))
        with catalog._db.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM document_versions").fetchone() == (1,)


def test_final_object_complete_before_metadata_transaction(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path / "data")
    content = b"body" * 10000
    complete = archives_module._complete
    observed = []

    def observe(source, destination):
        assert source.read_bytes() == content
        assert not destination.exists()
        complete(source, destination)
        observed.append(destination.read_bytes() == content)

    monkeypatch.setattr(archives_module, "_complete", observe)
    obj = catalog.archives.put(io.BytesIO(content))
    assert observed == [True]
    assert catalog.archives.verify(obj.sha256).size_bytes == len(content)
