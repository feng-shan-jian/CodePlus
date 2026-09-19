"""Three SQLite tables; historical chunks retain their generation's original path."""

from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3


class Metadata:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "metadata.sqlite3"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError(f"Unsupported knowledge schema version: {version}")
            if version == 0:
                for statement in (
                    """CREATE TABLE knowledge_bases (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                        collection_name TEXT NOT NULL UNIQUE, profile_hash TEXT NOT NULL,
                        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
                        state TEXT NOT NULL CHECK(state IN ('READY','UPDATING','NEEDS_REPAIR')),
                        error TEXT)""",
                    """CREATE TABLE documents (
                        id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES knowledge_bases(id),
                        source_uri TEXT NOT NULL, content_hash TEXT NOT NULL,
                        generation_id TEXT NOT NULL, original_path TEXT NOT NULL,
                        state TEXT NOT NULL CHECK(state IN ('READY','UPDATING','NEEDS_REPAIR')),
                        pending_operation TEXT, pending_path TEXT, error TEXT,
                        UNIQUE(kb_id, source_uri))""",
                    """CREATE TABLE chunks (
                        id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id),
                        generation_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                        text TEXT NOT NULL, source_spans TEXT NOT NULL,
                        UNIQUE(doc_id, generation_id, ordinal))""",
                ):
                    db.execute(statement)
            if version < 2:
                db.execute("ALTER TABLE documents ADD COLUMN removed INTEGER NOT NULL DEFAULT 0 CHECK(removed IN (0,1))")
                db.execute("ALTER TABLE knowledge_bases ADD COLUMN pending_operation TEXT")
                db.execute("ALTER TABLE chunks ADD COLUMN original_path TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE chunks SET original_path=(SELECT original_path FROM documents d WHERE d.id=chunks.doc_id)")
                # S2 registers creation before contacting Milvus. An unfinished empty
                # revision-zero base has exactly this pending target, even on SDK failure.
                db.execute("UPDATE knowledge_bases SET pending_operation='create' WHERE state!='READY' "
                           "AND revision=0 AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.kb_id=knowledge_bases.id)")
                db.execute("PRAGMA user_version = 2")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def library(self, kb_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM knowledge_bases WHERE id = ?", (kb_id,)).fetchone()
        if row is None:
            raise ValueError(f"Knowledge base not found: {kb_id}")
        return dict(row)

    def document(self, doc_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def register(self, kb_id: str, name: str, collection: str, profile_hash: str):
        with self.connect() as db:
            db.execute("INSERT INTO knowledge_bases(id,name,collection_name,profile_hash,state,pending_operation) "
                       "VALUES (?,?,?,?,'UPDATING','create')", (kb_id, name, collection, profile_hash))

    def set_state(self, kb_id: str, state: str, error: str | None = None):
        with self.connect() as db:
            db.execute("UPDATE knowledge_bases SET state=?, error=? WHERE id=?", (state, error, kb_id))
            if state == "READY":
                db.execute("UPDATE knowledge_bases SET pending_operation=NULL WHERE id=?", (kb_id,))
            if state == "NEEDS_REPAIR":
                db.execute("UPDATE documents SET state=?, error=? WHERE kb_id=? AND pending_operation IS NOT NULL",
                           (state, error, kb_id))

    def begin_import(self, document: dict, pending_path: str | None, operation="import"):
        with self.connect() as db:
            db.execute("INSERT INTO documents(id,kb_id,source_uri,content_hash,generation_id,original_path,"
                       "state,pending_operation,pending_path) VALUES "
                       "(:id,:kb_id,:source_uri,:content_hash,:generation_id,:original_path,'UPDATING',:operation,:pending_path) "
                       "ON CONFLICT(id) DO UPDATE SET state='UPDATING', pending_operation=excluded.pending_operation, "
                       "pending_path=excluded.pending_path, error=NULL",
                       {**document, "pending_path": pending_path, "operation": operation})
            db.execute("UPDATE knowledge_bases SET state='UPDATING', error=NULL WHERE id=?", (document["kb_id"],))

    def finish_import(self, kb_id: str, doc_id: str, chunks, document=None, *, removed=False):
        document = document or self.document(doc_id)
        with self.connect() as db:
            db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING", [
                (c.id, c.doc_id, c.generation_id, c.ordinal, c.text,
                 json.dumps([asdict(span) for span in c.source_spans], ensure_ascii=False),
                 document["original_path"]) for c in chunks
            ])
            if not removed:
                original = db.execute("SELECT original_path FROM chunks WHERE doc_id=? AND generation_id=? LIMIT 1",
                                      (doc_id, document["generation_id"])).fetchone()[0]
                db.execute("UPDATE documents SET content_hash=?, generation_id=?, original_path=? WHERE id=?",
                           (document["content_hash"], document["generation_id"], original, doc_id))
            db.execute("UPDATE documents SET state='READY', removed=?, pending_operation=NULL, pending_path=NULL, error=NULL "
                       "WHERE id=?", (removed, doc_id))
            db.execute("UPDATE knowledge_bases SET state='READY', revision=revision+1, error=NULL WHERE id=?", (kb_id,))

    def status(self, kb_id: str) -> dict:
        result = self.library(kb_id)
        with self.connect() as db:
            result["documents"] = [dict(row) for row in db.execute(
                "SELECT d.*, (SELECT count(*) FROM chunks c WHERE d.removed=0 AND c.doc_id=d.id AND "
                "c.generation_id=d.generation_id) AS chunk_count FROM documents d WHERE kb_id=? ORDER BY source_uri",
                (kb_id,))]
        return result
