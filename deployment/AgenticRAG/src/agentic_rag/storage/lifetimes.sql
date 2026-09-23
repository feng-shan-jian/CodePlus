-- Migration 9. Add evidence; never invent lifecycle identities for old rows.
CREATE TABLE run_lifetimes (
 run_id TEXT PRIMARY KEY REFERENCES runs(run_id), owner_nonce TEXT NOT NULL,
 pid INTEGER NOT NULL, process_birth TEXT NOT NULL,
 lock_dev TEXT NOT NULL, lock_ino TEXT NOT NULL
);
CREATE TRIGGER immutable_run_lifetime_update BEFORE UPDATE ON run_lifetimes BEGIN SELECT RAISE(ABORT,'immutable run lifetime'); END;
CREATE TRIGGER immutable_run_lifetime_delete BEFORE DELETE ON run_lifetimes BEGIN SELECT RAISE(ABORT,'historical run lifetime'); END;

CREATE TABLE index_readers (
 reader_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES index_artifacts(artifact_id),
 run_id TEXT REFERENCES runs(run_id), owner_nonce TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('operation','model','model_sync','milvus')),
 pid INTEGER NOT NULL, process_birth TEXT NOT NULL, lock_dev TEXT NOT NULL, lock_ino TEXT NOT NULL,
 worker_identity TEXT CHECK(worker_identity IS NULL OR json_valid(worker_identity)),
 state TEXT NOT NULL CHECK(state IN ('pending','finished')),
 detail TEXT NOT NULL CHECK(json_valid(detail)), completed_by TEXT
);
CREATE INDEX pending_artifact_readers ON index_readers(artifact_id,state);
CREATE INDEX pending_run_readers ON index_readers(run_id,state);
CREATE TRIGGER immutable_reader_identity BEFORE UPDATE OF reader_id,artifact_id,run_id,owner_nonce,kind,pid,process_birth,lock_dev,lock_ino,detail ON index_readers BEGIN SELECT RAISE(ABORT,'immutable reader identity'); END;
CREATE TRIGGER sealed_reader BEFORE UPDATE ON index_readers WHEN OLD.state='finished' BEGIN SELECT RAISE(ABORT,'finished reader receipt'); END;
CREATE TRIGGER frozen_reader_worker BEFORE UPDATE OF worker_identity ON index_readers WHEN OLD.worker_identity IS NOT NULL BEGIN SELECT RAISE(ABORT,'immutable reader worker'); END;
CREATE TRIGGER historical_reader_delete BEFORE DELETE ON index_readers BEGIN SELECT RAISE(ABORT,'historical reader receipt'); END;

CREATE TABLE index_gc_claims (
 artifact_id TEXT PRIMARY KEY REFERENCES index_artifacts(artifact_id),
 owner_nonce TEXT NOT NULL, pid INTEGER NOT NULL, process_birth TEXT NOT NULL,
 lock_dev TEXT NOT NULL, lock_ino TEXT NOT NULL,
 attempt INTEGER NOT NULL CHECK(attempt>0),
 state TEXT NOT NULL CHECK(state IN ('claimed','failed','reclaimed')),
 error TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE index_gc_attempts (
 attempt_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES index_artifacts(artifact_id),
 result_json TEXT NOT NULL CHECK(json_valid(result_json)), attempted_at TEXT NOT NULL
);
CREATE TRIGGER immutable_gc_attempt_update BEFORE UPDATE ON index_gc_attempts BEGIN SELECT RAISE(ABORT,'immutable GC attempt'); END;
CREATE TRIGGER immutable_gc_attempt_delete BEFORE DELETE ON index_gc_attempts BEGIN SELECT RAISE(ABORT,'historical GC attempt'); END;
CREATE TABLE maintenance_cursors (
 name TEXT PRIMARY KEY CHECK(name IN ('readers','pins','artifacts')), after_rowid INTEGER NOT NULL
);
