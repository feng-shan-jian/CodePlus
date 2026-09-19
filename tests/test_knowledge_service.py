"""State/identity regressions use real SQLite and OS locks; SDK checks are opt-in."""

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from codeplus.config import KnowledgeConfig
from codeplus.knowledge.metadata import Metadata
from codeplus.knowledge.service import KnowledgeService


class TinyEmbedding:
    calls = 0

    @property
    def tokenizer(self):
        return lambda text, **kwargs: {"input_ids": list(range(len(text))),
                                       "offset_mapping": [(i, i + 1) for i in range(len(text))]}

    def encode_documents(self, texts):
        self.calls += 1
        return [[1.0, 0.0, 0.0] for _ in texts]

    def encode_query(self, query):
        return [1.0, 0.0, 0.0]


class MemoryStore:
    def __init__(self):
        self.rows = {}

    def ensure_collection(self, *args, **kwargs):
        pass

    def upsert_chunks(self, name, rows):
        self.rows.update((row["chunk_id"], row) for row in rows)

    def verify_document(self, name, doc_id, expected):
        assert {r["chunk_id"] for r in expected} == {key for key, r in self.rows.items() if r["doc_id"] == doc_id}

    def search_dense(self, name, vector, top_k):
        return [{"chunk_id": key, "distance": 1.0} for key in list(self.rows)[:top_k]]

    def close(self):
        pass


@pytest.fixture
def service(tmp_path):
    pytest.importorskip("filelock")
    service = KnowledgeService(KnowledgeConfig(enabled=True, data_dir=str(tmp_path / "library"),
                                               embedding_dimension=3, chunk_tokens=64, chunk_overlap=8))
    service.embedding = TinyEmbedding()
    service._store = MemoryStore()
    yield service
    service.close()


def test_import_identity_sources_and_preparation_failure(service, tmp_path, monkeypatch):
    kb = service.create("sources")["id"]
    source = tmp_path / "source with spaces.md"
    data = "# 标题\r\n\r\n需要准确定位的中文段落。\r\n\r\n```python\r\nprint('hello')\r\n```\r\n".encode()
    source.write_bytes(data)
    doc = service.import_document(kb, source)
    saved = Path(doc["original_path"])
    assert saved.read_bytes() == data and not saved.stat().st_mode & 0o200
    before = service.status(kb)
    assert service.import_document(kb, source)["unchanged"]
    assert service.status(kb) == before and service.embedding.calls == 1
    other = tmp_path / "other.md"
    other.write_bytes(data)
    duplicate_bytes = service.import_document(kb, other)
    assert duplicate_bytes["id"] != doc["id"] and duplicate_bytes["generation_id"] == doc["generation_id"]
    wrong = KnowledgeService(replace(service.config, embedding_revision="same-dimension-other-model"))
    with pytest.raises(ValueError, match="profile mismatch"):
        wrong.import_document(kb, source)
    assert wrong.embedding._model is None and wrong._store is None
    source.write_text("Changed", encoding="utf-8")
    with pytest.raises(ValueError, match="S3"):
        service.import_document(kb, source)
    assert saved.read_bytes() == data
    hit = service.search(kb, "中文").hits[0]
    original = saved.read_bytes().decode("utf-8-sig")
    for span in hit.source_spans:
        fragment = original[span.char_start:span.char_end]
        assert fragment in hit.text
        assert fragment in "".join(original.splitlines(keepends=True)[span.line_start-1:span.line_end])
    before = service.status(kb)
    fresh = tmp_path / "failure.md"
    fresh.write_text("# New\n\nFailed preparation", encoding="utf-8")
    def fail_model(texts):
        raise RuntimeError("model failed")
    monkeypatch.setattr(service.embedding, "encode_documents", fail_model)
    with pytest.raises(RuntimeError, match="model failed"):
        service.import_document(kb, fresh)
    monkeypatch.setattr(service.embedding, "encode_query", fail_model)
    with pytest.raises(RuntimeError, match="model failed"):
        service.search(kb, "query model failure")
    assert service.status(kb) == before and not list(service.root.glob("preparing-*"))
    empty = service.create("empty")["id"]
    with pytest.raises(ValueError, match="empty"):
        service.search(empty, "test")
    fresh.write_text("\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no content"):
        service.import_document(empty, fresh)
    assert service.status(empty)["revision"] == 0


@pytest.mark.parametrize("failure", ["upsert", "verify", "metadata"])
def test_failed_commit_retains_pending_and_blocks_search(service, tmp_path, monkeypatch, failure):
    kb = service.create("failure")["id"]
    source = tmp_path / "document.md"
    source.write_text("# Long\n\n" + "中文正文 " * 40, encoding="utf-8")
    def fail(*args):
        raise RuntimeError(failure)
    if failure == "upsert":
        monkeypatch.setattr(service.store, "upsert_chunks", fail)
    elif failure == "verify":
        monkeypatch.setattr(service.store, "verify_document", fail)
    else:
        with service.metadata.connect() as db:
            db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON chunks WHEN NEW.ordinal=1 "
                       "BEGIN SELECT RAISE(ABORT, 'metadata'); END")
    with pytest.raises((RuntimeError, sqlite3.IntegrityError), match=failure):
        service.import_document(kb, source)
    state = service.status(kb)
    assert state["state"] == "NEEDS_REPAIR" and state["revision"] == 0
    document, = state["documents"]
    assert document["state"] == "NEEDS_REPAIR" and document["chunk_count"] == 0
    assert document["pending_operation"] == "import"
    pending = json.loads(Path(document["pending_path"]).read_text(encoding="utf-8"))
    assert pending["document"]["content_hash"] == document["content_hash"]
    assert len(pending["chunks"]) == len(pending["rows"]) > 1
    with pytest.raises(ValueError, match="NEEDS_REPAIR"):
        service.search(kb, "test")
    reopened = KnowledgeService(service.config)
    assert reopened.status(kb) == state
    with pytest.raises(ValueError, match="NEEDS_REPAIR"):
        reopened.search(kb, "test")


def test_schema_constraints_and_version(tmp_path):
    metadata = Metadata(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with metadata.connect() as db:
            db.execute("INSERT INTO chunks VALUES ('c','unknown','g',0,'text','[]')")
    with metadata.connect() as db:
        assert db.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
        db.execute("PRAGMA user_version = 2")
    with pytest.raises(ValueError, match="schema version: 2"):
        Metadata(tmp_path)


def test_process_lock_crash_keeps_import_pending(service, tmp_path):
    kb = service.create("writer")["id"]
    other = service.create("independent")["id"]
    source = tmp_path / "process.md"
    source.write_text("# Process\n\nImported in a different process", encoding="utf-8")
    marker = tmp_path / "writing"
    command = [sys.executable, "-c", """
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_knowledge_service import TinyEmbedding, MemoryStore
from codeplus.config import KnowledgeConfig
from codeplus.knowledge.service import KnowledgeService
service = KnowledgeService(KnowledgeConfig(**json.loads(sys.argv[1])))
service.embedding = TinyEmbedding()
class PausedStore(MemoryStore):
    def upsert_chunks(self, name, rows):
        Path(sys.argv[4]).touch()
        sys.stdin.readline()
        os._exit(0)
service._store = PausedStore()
service.import_document(sys.argv[2], sys.argv[3])
""", json.dumps(asdict(service.config)), kb, str(source), str(marker)]
    writer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reader = None
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline and writer.poll() is None:
            time.sleep(0.05)
        assert marker.exists(), "writer did not reach pending SDK write"
        assert service.status(other)["state"] == "READY"
        reader = subprocess.Popen([sys.executable, "-c", """
import json, sys
from codeplus.config import KnowledgeConfig
from codeplus.knowledge.service import KnowledgeService
service = KnowledgeService(KnowledgeConfig(**json.loads(sys.argv[1])))
print('waiting', flush=True)
try:
    service.search(sys.argv[2], 'test')
except ValueError as exc:
    assert 'UPDATING' in str(exc), str(exc)
    print('blocked by pending state', flush=True)
else:
    raise AssertionError('incomplete import was searchable')
""", json.dumps(asdict(service.config)), kb], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert reader.stdout.readline().strip() == "waiting"
        time.sleep(0.2)
        assert reader.poll() is None, "search bypassed the writer's OS lock"
        writer.kill()  # A real process death: no finally/SQLite success/status repair runs.
        writer.communicate(timeout=10)
        output, error = reader.communicate(timeout=15)
        assert reader.returncode == 0 and "blocked by pending state" in output, error
        state = service.status(kb)
        assert state["state"] == "UPDATING" and state["revision"] == 0
        assert Path(state["documents"][0]["pending_path"]).exists()
    finally:
        for process in (writer, reader):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


@pytest.mark.skipif(not os.getenv("CODEPLUS_TEST_MILVUS_URI"), reason="real Milvus not requested")
def test_real_store_document_isolation_and_binding(service, tmp_path):
    from codeplus.knowledge.milvus_store import MilvusStore
    from codeplus.knowledge.models import Chunk

    chunk = Chunk("chunk", "doc", "generation", 0, "中" * 22000, [])
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        MilvusStore.rows([chunk], [[1.0, 0.0, 0.0]], 3)
    with pytest.raises(ValueError, match="dimension"):
        MilvusStore.rows([replace(chunk, text="small")], [[1.0, 0.0]], 3)
    service._store = MilvusStore(os.environ["CODEPLUS_TEST_MILVUS_URI"])
    kb = None
    try:
        kb = service.create("real SDK")
        docs = []
        for name in ("first", "second"):
            source = tmp_path / f"{name}.md"
            source.write_text(f"# {name}\n\nA passage for {name}", encoding="utf-8")
            docs.append(service.import_document(kb["id"], source))
        for document in docs:
            assert document["chunk_count"] == 1
        hits = service.search(kb["id"], "test").hits
        assert {hit.doc_id for hit in hits} == {doc["id"] for doc in docs}
        rows = list(service.store.client.query(kb["collection_name"], filter="", limit=10, output_fields=["*"]))
        service.store.upsert_chunks(kb["collection_name"], rows)
        for doc in docs:
            service.store.verify_document(kb["collection_name"], doc["id"], [r for r in rows if r["doc_id"] == doc["id"]])
        with pytest.raises(ValueError, match="profile mismatch"):
            service.store.ensure_collection(kb["collection_name"], "different", 3)
        with pytest.raises(ValueError, match="profile mismatch"):
            service.store.ensure_collection(kb["collection_name"], kb["profile_hash"], 4)
        service.store.delete_document(kb["collection_name"], docs[0]["id"])
        service.store.verify_document(kb["collection_name"], docs[1]["id"], [r for r in rows if r["doc_id"] == docs[1]["id"]])
    finally:
        if kb:
            service.store.client.drop_collection(kb["collection_name"])
            assert not service.store.client.has_collection(kb["collection_name"])
