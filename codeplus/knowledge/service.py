"""Library operations: prepare outside the lock, serialize reads and commits inside it.

These synchronous operations can be run with asyncio.to_thread by later UI callers.
No answer model is called here.
"""

from contextlib import contextmanager
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import RLock
import uuid

from codeplus.config import KnowledgeConfig
from .documents import PARSERS, chunk_markdown, parse_document, read_source, save_original
from .embedding import LocalEmbedding
from .metadata import Metadata
from .milvus_store import MilvusStore
from .models import Chunk, SearchHit, SearchResult, SourceSpan, fingerprint, profile


class KnowledgeService:
    def __init__(self, config: KnowledgeConfig):
        self.config = replace(config)
        self.embedding = LocalEmbedding(self.config)
        self.profile = profile(self.config)
        self.profile_hash = fingerprint(self.profile)
        self.root = Path(self.config.data_dir).expanduser().resolve()
        self.metadata = Metadata(self.root)
        self._store = None
        self._prepare_lock = RLock()
        self._runtime = None
        self._closed = False

    @property
    def store(self):
        if self._closed:
            raise RuntimeError("Knowledge service is closed")
        if self._store is None:
            self._store = MilvusStore(self.config.milvus_uri)
        return self._store

    def prepare(self, progress=lambda message: None):
        """Serialize preparation and close; a failed attempt can be retried safely."""
        from .runtime import MANAGED_URI, ManagedRuntime

        with self._prepare_lock:
            if self._closed:
                raise RuntimeError("Knowledge service is closed")
            if self.config.managed_local and self.config.milvus_uri != MANAGED_URI:
                raise ValueError("managed_local 只适用于 http://127.0.0.1:19530；外部 Milvus 请设为 false")
            try:
                progress("正在连接服务")
                if self.config.managed_local:
                    if self._runtime is None:
                        self._runtime = ManagedRuntime()
                try:
                    self._connect()
                except (ImportError, ModuleNotFoundError):
                    raise
                except Exception as exc:
                    if self._runtime is None:
                        raise RuntimeError("无法连接 Milvus；请检查已配置服务。项目受管部署需显式设置 knowledge.managed_local: true") from exc
                    self._runtime.start(progress, self._connect)
                if self._runtime is not None:
                    self._runtime.hold()
                if not self.embedding.loaded:
                    progress("正在加载模型（首次使用可能下载；需要网络和磁盘空间）")
                self.embedding.tokenizer
                progress("已就绪")
            except ImportError as exc:
                raise RuntimeError(f"知识库依赖加载失败（{exc}）；在本机环境运行 uv sync --extra knowledge，或 pip install 'codeplus[knowledge]'；然后 /knowledge prepare 重试。") from exc
            except Exception as exc:
                raise RuntimeError(f"知识库准备失败：{exc}。修复后执行 /knowledge prepare（CLI: python -m codeplus.knowledge prepare）重试。") from exc

    def _connect(self):
        if self._store is None:
            self._store = MilvusStore(self.config.milvus_uri, timeout=3)
        try:
            self._store.check_health()
        except Exception:
            self._store.close()
            self._store = None
            raise

    def close(self):
        with self._prepare_lock:
            self._closed = True
            if self._store is not None:
                self._store.close()
                self._store = None
            if self._runtime is not None:
                self._runtime.close()

    @contextmanager
    def _locked(self, kb_id: str, *, operational=True, ready=True):
        from filelock import FileLock

        kb = self.metadata.library(kb_id)
        with FileLock(self.root / f"{kb['id']}.lock", timeout=30):
            kb = self.metadata.library(kb_id)
            if operational:
                saved_profile = json.loads((self.root / kb_id / "profile.json").read_text(encoding="utf-8"))
                if kb["profile_hash"] != self.profile_hash or fingerprint(saved_profile) != self.profile_hash:
                    raise ValueError("Knowledge profile mismatch; use the bound configuration or create a separate base")
                if ready and kb["state"] != "READY":
                    raise ValueError(f"Knowledge base is {kb['state']}; retry required: {kb['error'] or 'unfinished operation'}")
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
            self._finish_create(kb)
        return self.status(kb_id)

    def _finish_create(self, kb):
        try:
            self._collection(kb, create=True)
            self.metadata.set_state(kb["id"], "READY")
        except Exception as exc:
            self.metadata.set_state(kb["id"], "NEEDS_REPAIR", str(exc))
            raise

    def status(self, kb_id: str) -> dict:
        """Local diagnostics remain available when Milvus or the configured model is unavailable."""
        with self._locked(kb_id, operational=False):
            return self.metadata.status(kb_id)

    def import_document(self, kb_id: str, source: str | Path) -> dict:
        source = Path(source).expanduser().resolve()
        source_uri = Path(os.path.normcase(str(source))).as_uri()
        data, content_hash = read_source(source)
        doc_id = fingerprint([kb_id, source_uri])
        # Reject a duplicate before loading the tokenizer/model or writing preparation files.
        with self._locked(kb_id):
            previous = self.metadata.document(doc_id)
            if previous and not previous["removed"] and previous["content_hash"] == content_hash:
                return {**previous, "unchanged": True}
        # Adding formats does not invalidate an existing Markdown profile or generation.
        parsing = {key: self.profile[key] for key in
                   ("tokenizer", "parsing", "chunking", "chunk_tokens", "chunk_overlap")}
        parsing["parsing"] = PARSERS[source.suffix.lower()]
        generation_id = fingerprint([content_hash, parsing])
        with TemporaryDirectory(prefix="preparing-", dir=self.root) as temporary:
            prepared = Path(temporary) / "payload"
            prepared.mkdir()
            original = save_original(prepared, data, source.suffix.lower())
            text, blocks = parse_document(original)
            chunks = chunk_markdown(text, blocks, self.embedding.tokenizer, doc_id, generation_id,
                                    self.config.chunk_tokens, self.config.chunk_overlap)
            vectors = self.embedding.encode_documents([chunk.text for chunk in chunks])
            rows = MilvusStore.rows(chunks, vectors, self.config.embedding_dimension)
            # A death before registering pending may leave an unreferenced folder.
            # A fresh attempt must not collide with it; identities live in metadata.
            destination = self.root / kb_id / "documents" / uuid.uuid4().hex
            document = {"id": doc_id, "kb_id": kb_id, "source_uri": source_uri, "content_hash": content_hash,
                        "generation_id": generation_id, "original_path": str(destination / original.name)}
            # Complete inputs survive a process death after pending is committed to SQLite.
            with (prepared / "pending.json").open("x", encoding="utf-8") as stream:
                json.dump({"document": document, "profile": self.profile, "chunks": [asdict(c) for c in chunks],
                           "rows": rows}, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            with self._locked(kb_id) as kb:
                existing = self.metadata.document(doc_id)
                if existing and not existing["removed"] and existing["content_hash"] == content_hash:
                    return {**existing, "unchanged": True}
                if existing != previous:
                    raise ValueError("Document changed during preparation; import again")
                destination.parent.mkdir(exist_ok=True)
                prepared.replace(destination)
                try:
                    self.metadata.begin_import(document, str(destination / "pending.json"))
                except Exception:
                    # No pending record means nothing in Milvus has been touched.
                    (destination / original.name).chmod(0o600)
                    shutil.rmtree(destination)
                    raise
                return self._commit_document(kb, self.metadata.document(doc_id))

    def _commit_document(self, kb: dict, document: dict) -> dict:
        """Import, replacement, removal and retry share this lock-held commit path."""
        pending_path = document["pending_path"]
        try:
            operation = document["pending_operation"]
            chunks, rows = [], []
            if operation == "import":
                pending = json.loads(Path(pending_path).read_text(encoding="utf-8"))
                target = pending["document"]
                if (target["id"] != document["id"] or target["kb_id"] != kb["id"]
                        or fingerprint(pending["profile"]) != kb["profile_hash"]):
                    raise ValueError("Pending import does not match the registered document/profile")
                if read_source(Path(target["original_path"]))[1] != target["content_hash"]:
                    raise ValueError("Pending original content hash differs; cannot retry")
                document = target
                chunks = [Chunk(**{**c, "source_spans": [SourceSpan(**s) for s in c["source_spans"]]})
                          for c in pending["chunks"]]
                rows = pending["rows"]
            elif operation != "remove":
                raise ValueError("No supported pending operation for this document")
            self._collection(kb)
            self.store.delete_document(kb["collection_name"], document["id"])
            if rows:
                self.store.upsert_chunks(kb["collection_name"], rows)
            self.store.verify_document(kb["collection_name"], document["id"], rows)
            self.metadata.finish_import(kb["id"], document["id"], chunks, document, removed=operation == "remove")
        except Exception as exc:
            self.metadata.set_state(kb["id"], "NEEDS_REPAIR", str(exc))
            raise
        if pending_path:
            # READY is durable. A leftover retry file after cleanup failure is inert.
            try:
                Path(pending_path).unlink(missing_ok=True)
            except OSError:
                pass
        return {**self.metadata.document(document["id"]), "unchanged": False, "chunk_count": len(chunks)}

    def remove(self, kb_id: str, doc_id: str) -> dict:
        with self._locked(kb_id) as kb:
            document = self.metadata.document(doc_id)
            if document is None or document["kb_id"] != kb_id:
                raise ValueError(f"Document not found in this knowledge base: {doc_id}")
            if document["removed"]:
                return {**document, "unchanged": True}
            self.metadata.begin_import(document, None, "remove")
            return self._commit_document(kb, self.metadata.document(doc_id))

    def retry(self, kb_id: str) -> dict:
        with self._locked(kb_id, ready=False) as kb:
            pending = [d for d in self.metadata.status(kb_id)["documents"] if d["pending_operation"]]
            if kb["state"] == "READY" and not pending and not kb["pending_operation"]:
                return {**self.metadata.status(kb_id), "unchanged": True}
            if kb["pending_operation"] == "create" and not pending:
                self._finish_create(kb)
                return {**self.metadata.status(kb_id), "unchanged": False}
            if len(pending) != 1 or kb["pending_operation"]:
                raise ValueError("Knowledge base has no single registered pending target to retry")
            return self._commit_document(kb, pending[0])

    def source(self, kb_id: str, chunk_id: str) -> dict:
        """Read a saved chunk and its exact generation's original, including removed documents."""
        with self._locked(kb_id, operational=False), self.metadata.connect() as db:
            row = db.execute(
                "SELECT c.*, d.source_uri, d.generation_id AS current_generation_id, d.removed "
                "FROM chunks c JOIN documents d ON d.id=c.doc_id "
                "WHERE c.id=? AND d.kb_id=?",
                (chunk_id, kb_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Chunk not found in this knowledge base: {chunk_id}")
            return self._source_row(row)

    @staticmethod
    def _source_row(row) -> dict:
        return {**dict(row), "source_spans": json.loads(row["source_spans"]),
                "source_status": "removed" if row["removed"] else (
                    "current" if row["generation_id"] == row["current_generation_id"] else "historical")}

    def source_context(self, kb_id: str, chunk_id: str) -> list[dict]:
        """Anchor first, then at most one neighbour on each side of its saved generation."""
        anchor = self.source(kb_id, chunk_id)
        with self._locked(kb_id, operational=False), self.metadata.connect() as db:
            rows = db.execute(
                "SELECT c.*, d.source_uri, d.generation_id AS current_generation_id, d.removed "
                "FROM chunks c JOIN documents d ON d.id=c.doc_id "
                "WHERE d.kb_id=? AND c.doc_id=? AND c.generation_id=? "
                "AND c.ordinal IN (?,?) ORDER BY c.ordinal",
                (kb_id, anchor["doc_id"], anchor["generation_id"], anchor["ordinal"] - 1, anchor["ordinal"] + 1),
            ).fetchall()
            return [anchor] + [self._source_row(row) for row in rows]

    def search(self, kb_id: str, query: str, top_k: int | None = None) -> SearchResult:
        top_k = self.config.top_k if top_k is None else top_k
        if not 1 <= top_k <= 16384:
            raise ValueError("top_k must be between 1 and 16384")
        with self._locked(kb_id):
            with self.metadata.connect() as db:
                if db.execute("SELECT 1 FROM documents WHERE kb_id=? AND removed=0 LIMIT 1", (kb_id,)).fetchone() is None:
                    raise ValueError("Knowledge base is empty; import a document first")
        vector = self.embedding.encode_query(query)
        with self._locked(kb_id) as kb:
            self._collection(kb)
            matches = self.store.search_dense(kb["collection_name"], vector, top_k)
            hits = []
            with self.metadata.connect() as db:
                for match in matches:
                    row = db.execute(
                        "SELECT c.*, d.source_uri FROM chunks c JOIN documents d ON d.id=c.doc_id "
                        "WHERE c.id=? AND d.kb_id=? AND d.state='READY' AND d.removed=0 AND c.generation_id=d.generation_id",
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
