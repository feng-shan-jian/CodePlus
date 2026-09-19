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

    def delete_document(self, name, doc_id):
        self.rows = {key: r for key, r in self.rows.items() if r["doc_id"] != doc_id}

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
    hit = service.search(kb, "中文").hits[0]
    original = saved.read_bytes().decode("utf-8-sig")
    for span in hit.source_spans:
        fragment = original[span.char_start:span.char_end]
        assert fragment in hit.text
        assert fragment in "".join(original.splitlines(keepends=True)[span.line_start-1:span.line_end])
    rows_b = {key: row for key, row in service.store.rows.items() if row["doc_id"] == duplicate_bytes["id"]}
    source.write_text("Changed", encoding="utf-8")
    updated = service.import_document(kb, source)
    assert updated["id"] == doc["id"] and updated["generation_id"] != doc["generation_id"]
    assert service.status(kb)["revision"] == 3 and service.status(kb)["collection_name"] == before["collection_name"]
    assert all(h.generation_id == updated["generation_id"] for h in service.search(kb, "test").hits if h.doc_id == doc["id"])
    assert {key: row for key, row in service.store.rows.items() if row["doc_id"] == duplicate_bytes["id"]} == rows_b
    assert Path(service.source(kb, hit.chunk_id)["original_path"]).read_bytes() == data == saved.read_bytes()
    assert service.remove(kb, doc["id"])["removed"]
    state = service.status(kb)
    assert service.remove(kb, doc["id"])["unchanged"] and service.status(kb) == state
    assert all(h.doc_id != doc["id"] for h in service.search(kb, "test").hits)
    assert service.source(kb, hit.chunk_id)["original_path"] == str(saved)
    source.write_bytes(data)  # Restore a historical generation: stable IDs, retained original mapping.
    restored = service.import_document(kb, source)
    assert restored["generation_id"] == doc["generation_id"] and restored["original_path"] == str(saved)
    assert not restored["removed"] and service.status(kb)["revision"] == 5
    assert service.retry(kb)["unchanged"] and service.status(kb)["revision"] == 5
    # A concurrent replacement wins while the first import prepares its vectors.
    source.write_text("First candidate", encoding="utf-8")
    encode = service.embedding.encode_documents
    def concurrent_update(texts):
        other_writer = KnowledgeService(service.config)
        other_writer.embedding, other_writer._store = TinyEmbedding(), service.store
        source.write_text("Concurrent winner", encoding="utf-8")
        other_writer.import_document(kb, source)
        return encode(texts)
    with monkeypatch.context() as patch:
        patch.setattr(service.embedding, "encode_documents", concurrent_update)
        with pytest.raises(ValueError, match="changed during preparation"):
            service.import_document(kb, source)
    assert Path(service.metadata.document(doc["id"])["original_path"]).read_text() == "Concurrent winner"
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
    monkeypatch.undo()
    if failure == "metadata":
        with service.metadata.connect() as db:
            db.execute("DROP TRIGGER fail_second")
    reopened._store = service.store
    reopened.embedding = TinyEmbedding()
    assert reopened.retry(kb)["chunk_count"] == len(pending["chunks"])
    assert reopened.status(kb)["state"] == "READY" and reopened.status(kb)["revision"] == 1
    assert reopened.retry(kb)["unchanged"]
    assert reopened.search(kb, "test").hits


def test_schema_constraints_and_version(service, tmp_path, monkeypatch):
    metadata = service.metadata
    with pytest.raises(sqlite3.IntegrityError):
        with metadata.connect() as db:
            db.execute("INSERT INTO chunks VALUES ('c','unknown','g',0,'text','[]','original.md')")
    kb = service.create("S2")["id"]
    source = tmp_path / "s2.md"
    source.write_text("S2 existing document", encoding="utf-8")
    document = service.import_document(kb, source)
    hit = service.search(kb, "test").hits[0]
    with monkeypatch.context() as patch:
        def unavailable(*args, **kwargs):
            raise RuntimeError("Milvus unavailable at creation")
        patch.setattr(service.store, "ensure_collection", unavailable)
        with pytest.raises(RuntimeError, match="Milvus unavailable"):
            service.create("unfinished S2 creation")
    with metadata.connect() as db:
        failed = db.execute("SELECT id FROM knowledge_bases WHERE name='unfinished S2 creation'").fetchone()[0]
        # Reconstruct the actual v1 column layout; lifecycle state comes from service operations.
        db.execute("ALTER TABLE chunks DROP COLUMN original_path")
        db.execute("ALTER TABLE documents DROP COLUMN removed")
        db.execute("ALTER TABLE knowledge_bases DROP COLUMN pending_operation")
        db.execute("PRAGMA user_version = 1")
    Metadata(service.root)
    assert service.source(kb, hit.chunk_id)["original_path"] == document["original_path"]
    assert service.search(kb, "test").hits[0] == hit
    assert service.import_document(kb, source)["unchanged"]
    assert service.status(failed)["pending_operation"] == "create"
    assert service.retry(failed)["state"] == "READY"
    with metadata.connect() as db:
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"knowledge_bases", "documents", "chunks"}
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="schema version: 99"):
        Metadata(service.root)


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


def write_pdf(path, pages, *, encrypted=False):
    """Small real PDF fixture, without adding a second PDF library to test dependencies."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(300, 200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    writer.write(path)


def test_pdf_docx_locations_and_format_errors(service, tmp_path):
    from docx import Document
    from codeplus.knowledge.documents import parse_document

    kb = service.create("formats")["id"]
    pdf = tmp_path / "two pages.pdf"
    write_pdf(pdf, ["Amber license expires in April.", "Cobalt delivery arrives in November."])
    doc = service.import_document(kb, pdf)
    hits = service.search(kb, "Cobalt", 50).hits
    assert {span.page for hit in hits for span in hit.source_spans} == {1, 2}
    text, _ = parse_document(Path(doc["original_path"]))
    assert any("Cobalt" in text[s.char_start:s.char_end] and s.page == 2 for h in hits for s in h.source_spans)
    word = tmp_path / "body order.docx"
    source = Document()
    source.add_heading("Dispatch", 1)
    source.add_paragraph("Before table")
    table = source.add_table(rows=2, cols=2)
    for cell, value in zip((c for r in table.rows for c in r.cells), ("Route", "Deadline", "Cobalt", "November")):
        cell.text = value
    source.add_paragraph("After table")
    source.save(word)
    document = service.import_document(kb, word)
    text, blocks = parse_document(Path(document["original_path"]))
    assert text.index("Before table") < text.index("November") < text.index("After table")
    assert next(b.source for b in blocks if "After table" in b.text).paragraph == 3
    hits = [h for h in service.search(kb, "November", 50).hits if h.doc_id == document["id"]]
    spans = [s for h in hits for s in h.source_spans]
    assert all(s.page is None and s.line_start is None and s.heading_path == ["Dispatch"] for s in spans)
    assert any((s.table, s.row, s.column) == (1, 2, 2) and "November" in text[s.char_start:s.char_end] for s in spans)
    assert all(text[s.char_start:s.char_end] in h.text for h in hits for s in h.source_spans)
    before = service.status(kb)
    invalid = tmp_path / "invalid.pdf"
    write_pdf(invalid, [""])
    with pytest.raises(ValueError, match="empty or scanned"):
        service.import_document(kb, invalid)
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
    writer = PdfWriter()
    page = writer.add_blank_page(300, 200)
    image = DecodedStreamObject()
    image.set_data(b"\x00")
    image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(1), NameObject("/Height"): NumberObject(1),
                  NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8)})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(image)})})
    stream = DecodedStreamObject()
    stream.set_data(b"q 100 0 0 100 0 0 cm /Im1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(invalid)
    with pytest.raises(ValueError, match="empty or scanned"):
        service.import_document(kb, invalid)
    write_pdf(invalid, ["Secret"], encrypted=True)
    with pytest.raises(ValueError, match="Encrypted PDF"):
        service.import_document(kb, invalid)
    invalid.write_bytes(b"not a PDF")
    with pytest.raises(ValueError, match="Cannot parse PDF"):
        service.import_document(kb, invalid)
    with pytest.raises(ValueError, match="convert legacy .doc"):
        service.import_document(kb, tmp_path / "old.doc")
    assert service.status(kb) == before


@pytest.mark.skipif(not os.getenv("CODEPLUS_TEST_MILVUS_URI"), reason="real Milvus not requested")
def test_real_store_document_isolation_and_binding(service, tmp_path, monkeypatch):
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
        # The collection really exists without a dense index when creation fails.
        with monkeypatch.context() as patch:
            def fail_index(*args, **kwargs):
                raise RuntimeError("before index creation")
            patch.setattr(service.store.client, "create_index", fail_index)
            with pytest.raises(RuntimeError, match="before index creation"):
                service.create("real SDK")
        with service.metadata.connect() as db:
            kb = dict(db.execute("SELECT * FROM knowledge_bases WHERE name='real SDK'").fetchone())
        assert service.store.client.has_collection(kb["collection_name"])
        assert service.store.client.list_indexes(kb["collection_name"]) == []
        assert service.retry(kb["id"])["state"] == "READY"
        assert service.store.client.list_indexes(kb["collection_name"], field_name="dense")
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
        child = tmp_path / "crash.py"
        child.write_text('''
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_knowledge_service import TinyEmbedding
from codeplus.config import KnowledgeConfig
from codeplus.knowledge.service import KnowledgeService
service = KnowledgeService(KnowledgeConfig(**json.loads(sys.argv[1])))
service.embedding = TinyEmbedding()
kb, path, doc, phase = sys.argv[2:]
if phase in ('deleted', 'remove'):
    erase = service.store.delete_document
    def stop(*args):
        erase(*args)
        os._exit(73)
    service.store.delete_document = stop
elif phase == 'partial':
    write = service.store.upsert_chunks
    def stop(name, rows):
        assert len(rows) > 1
        write(name, rows[:len(rows)//2])
        os._exit(73)
    service.store.upsert_chunks = stop
else:
    service.metadata.finish_import = lambda *args, **kwargs: os._exit(73)
if phase == 'remove':
    service.remove(kb, doc)
else:
    service.import_document(kb, path)
''', encoding="utf-8")
        source = tmp_path / "first.md"
        for phase in ("deleted", "partial", "written", "remove"):
            before = service.status(kb["id"])
            old = service.metadata.document(docs[0]["id"])
            source.write_text(f"# {phase}\n\n" + "New content with distinct chunks. " * 8, encoding="utf-8")
            process = subprocess.run([sys.executable, str(child), json.dumps(asdict(service.config)), kb["id"],
                                      str(source), docs[0]["id"], phase], capture_output=True, text=True, timeout=90)
            assert process.returncode == 73, process.stderr
            with KnowledgeService(service.config).metadata.connect() as db:
                assert db.execute("SELECT generation_id FROM documents WHERE id=?", (old["id"],)).fetchone()[0] == old["generation_id"]
            reopened = KnowledgeService(service.config)
            reopened._store = service.store
            reopened.embedding = TinyEmbedding()
            assert reopened.status(kb["id"])["state"] == "UPDATING"
            with pytest.raises(ValueError, match="UPDATING"):
                reopened.search(kb["id"], "test")
            pending_doc = reopened.metadata.document(old["id"])
            expected = [] if phase == "remove" else json.loads(Path(pending_doc["pending_path"]).read_text(encoding="utf-8"))["rows"]
            recovered = reopened.retry(kb["id"])
            state = reopened.status(kb["id"])
            assert state["revision"] == before["revision"] + 1 and state["state"] == "READY"
            assert state["collection_name"] == kb["collection_name"]
            service.store.verify_document(kb["collection_name"], old["id"], expected)
            actual_b = service.store.client.query(kb["collection_name"], filter="doc_id == {doc_id}",
                                                  filter_params={"doc_id": docs[1]["id"]}, output_fields=["*"])
            assert list(actual_b) == [r for r in rows if r["doc_id"] == docs[1]["id"]]
            assert recovered["removed"] == (phase == "remove")
            assert reopened.retry(kb["id"])["unchanged"]
            print(f"real process exit at {phase}: blocked then READY revision={state['revision']}; B unchanged")
    finally:
        if kb:
            service.store.client.drop_collection(kb["collection_name"])
            assert not service.store.client.has_collection(kb["collection_name"])
