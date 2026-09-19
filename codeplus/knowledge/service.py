"""Library operations: prepare outside the lock, serialize reads and commits inside it.

These synchronous operations can be run with asyncio.to_thread by later UI callers.
No answer model, update, remove or recovery operation is implemented in S2.
"""

from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import uuid

from codeplus.config import KnowledgeConfig
from .documents import chunk_markdown, parse_markdown, read_source, save_original
from .embedding import LocalEmbedding
from .metadata import Metadata
from .milvus_store import MilvusStore
from .models import SearchHit, SearchResult, SourceSpan, fingerprint, profile


class KnowledgeService:
    def __init__(self, config: KnowledgeConfig):
        self.config = replace(config)
        self.embedding = LocalEmbedding(self.config)
        self.profile = profile(self.config)
        self.profile_hash = fingerprint(self.profile)
        self.root = Path(self.config.data_dir).expanduser().resolve()
        self.metadata = Metadata(self.root)
        self._store = None

    @property
    def store(self):
        if self._store is None:
            self._store = MilvusStore(self.config.milvus_uri)
        return self._store

    def close(self):
        if self._store is not None:
            self._store.close()

    @contextmanager
    def _locked(self, kb_id: str, *, operational=True):
        from filelock import FileLock

        kb = self.metadata.library(kb_id)
        with FileLock(self.root / f"{kb['id']}.lock", timeout=30):
            kb = self.metadata.library(kb_id)
            if operational:
                saved_profile = json.loads((self.root / kb_id / "profile.json").read_text(encoding="utf-8"))
                if kb["profile_hash"] != self.profile_hash or fingerprint(saved_profile) != self.profile_hash:
                    raise ValueError("Knowledge profile mismatch; use the bound configuration or create a separate base")
                if kb["state"] != "READY":
                    raise ValueError(f"Knowledge base is {kb['state']}; repair required: {kb['error'] or 'unfinished operation'}")
            yield kb

    def _collection(self, kb: dict, *, create=False):
        self.store.ensure_collection(kb["collection_name"], kb["profile_hash"],
                                     self.config.embedding_dimension, create=create)

    def create(self, name: str) -> dict:
        if not name.strip():
            raise ValueError("Knowledge base name must not be empty")
        kb_id = uuid.uuid4().hex
        directory = self.root / kb_id
        directory.mkdir()
        try:
            (directory / "profile.json").write_text(json.dumps(self.profile, ensure_ascii=False, indent=2), encoding="utf-8")
            self.metadata.register(kb_id, name, f"codeplus_kb_{kb_id}", self.profile_hash)
        except Exception:
            shutil.rmtree(directory)
            raise
        with self._locked(kb_id, operational=False) as kb:
            try:
                self._collection(kb, create=True)
                self.metadata.set_state(kb_id, "READY")
            except Exception as exc:
                self.metadata.set_state(kb_id, "NEEDS_REPAIR", str(exc))
                raise
        return self.status(kb_id)

    def status(self, kb_id: str) -> dict:
        """Local diagnostics remain available when Milvus or the configured model is unavailable."""
        with self._locked(kb_id, operational=False):
            return self.metadata.status(kb_id)

    def _existing(self, doc_id: str, content_hash: str) -> dict | None:
        document = self.metadata.document(doc_id)
        if document and document["content_hash"] != content_hash:
            raise ValueError("Source has changed; document update is not implemented until S3")
        return document

    def import_document(self, kb_id: str, source: str | Path) -> dict:
        source = Path(source).expanduser().resolve()
        source_uri = Path(os.path.normcase(str(source))).as_uri()
        data, content_hash = read_source(source)
        doc_id = fingerprint([kb_id, source_uri])
        # Reject a duplicate before loading the tokenizer/model or writing preparation files.
        with self._locked(kb_id):
            if document := self._existing(doc_id, content_hash):
                return {**document, "unchanged": True}
        generation_id = fingerprint([content_hash, {key: self.profile[key] for key in
            ("tokenizer", "parsing", "chunking", "chunk_tokens", "chunk_overlap")}])
        with TemporaryDirectory(prefix="preparing-", dir=self.root) as temporary:
            prepared = Path(temporary) / "payload"
            prepared.mkdir()
            original = save_original(prepared, data)
            text = original.read_bytes().decode("utf-8-sig")
            blocks = parse_markdown(text)
            chunks = chunk_markdown(text, blocks, self.embedding.tokenizer, doc_id, generation_id,
                                    self.config.chunk_tokens, self.config.chunk_overlap)
            vectors = self.embedding.encode_documents([chunk.text for chunk in chunks])
            rows = MilvusStore.rows(chunks, vectors, self.config.embedding_dimension)
            # A death before registering pending may leave an unreferenced folder.
            # A fresh attempt must not collide with it; identities live in metadata.
            destination = self.root / kb_id / "documents" / uuid.uuid4().hex
            document = {"id": doc_id, "kb_id": kb_id, "source_uri": source_uri, "content_hash": content_hash,
                        "generation_id": generation_id, "original_path": str(destination / "original.md")}
            # Complete inputs survive a process death after pending is committed to SQLite.
            with (prepared / "pending.json").open("x", encoding="utf-8") as stream:
                json.dump({"document": document, "profile": self.profile, "chunks": [asdict(c) for c in chunks],
                           "rows": rows}, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            with self._locked(kb_id) as kb:
                if existing := self._existing(doc_id, content_hash):
                    return {**existing, "unchanged": True}
                destination.parent.mkdir(exist_ok=True)
                prepared.replace(destination)
                try:
                    self.metadata.begin_import(document, str(destination / "pending.json"))
                except Exception:
                    # No pending record means nothing in Milvus has been touched.
                    (destination / "original.md").chmod(0o600)
                    shutil.rmtree(destination)
                    raise
                try:
                    self._collection(kb)
                    self.store.upsert_chunks(kb["collection_name"], rows)
                    self.store.verify_document(kb["collection_name"], doc_id, rows)
                    self.metadata.finish_import(kb_id, doc_id, chunks)
                except Exception as exc:
                    self.metadata.set_state(kb_id, "NEEDS_REPAIR", str(exc))
                    raise
                # READY is already durable; retry material is no longer needed.
                (destination / "pending.json").unlink()
                return {**self.metadata.document(doc_id), "unchanged": False, "chunk_count": len(chunks)}

    def search(self, kb_id: str, query: str, top_k: int | None = None) -> SearchResult:
        top_k = self.config.top_k if top_k is None else top_k
        if not 1 <= top_k <= 16384:
            raise ValueError("top_k must be between 1 and 16384")
        with self._locked(kb_id):
            with self.metadata.connect() as db:
                if db.execute("SELECT 1 FROM documents WHERE kb_id=? LIMIT 1", (kb_id,)).fetchone() is None:
                    raise ValueError("Knowledge base is empty; import a Markdown document first")
        vector = self.embedding.encode_query(query)
        with self._locked(kb_id) as kb:
            self._collection(kb)
            matches = self.store.search_dense(kb["collection_name"], vector, top_k)
            hits = []
            with self.metadata.connect() as db:
                for match in matches:
                    row = db.execute(
                        "SELECT c.*, d.source_uri, d.original_path FROM chunks c JOIN documents d ON d.id=c.doc_id "
                        "WHERE c.id=? AND d.kb_id=? AND d.state='READY' AND c.generation_id=d.generation_id",
                        (match["chunk_id"], kb_id),
                    ).fetchone()
                    if row is None:
                        raise ValueError("Milvus hit has no current source in this knowledge base")
                    hits.append(SearchHit(row["id"], row["doc_id"], row["generation_id"], row["text"],
                                          row["source_uri"], row["original_path"],
                                          [SourceSpan(**span) for span in json.loads(row["source_spans"])],
                                          float(match["distance"])))
            return SearchResult(query, kb_id, kb["revision"],
                                {"top_k": top_k, "index": "FLAT", "metric": "COSINE", "profile_hash": self.profile_hash}, hits)
