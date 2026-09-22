-- Migration 8. Run with foreign_keys OFF outside BEGIN, then check all FKs
-- before COMMIT. Rebuild only artifacts, preserving its name and every v7 row.
-- Inbound publication FKs and immutable publication triggers remain untouched.
CREATE TABLE r14_index_artifacts (
 artifact_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, revision_id TEXT NOT NULL UNIQUE,
 batch_id TEXT NOT NULL, collection_name TEXT NOT NULL UNIQUE,
 schema_hash TEXT NOT NULL, owner_epoch INTEGER NOT NULL CHECK(owner_epoch>0),
 spec_json TEXT NOT NULL CHECK(json_valid(spec_json)),
 expected_hash TEXT REFERENCES archive_objects(sha256),
 validation_json TEXT CHECK(validation_json IS NULL OR json_valid(validation_json)),
 state TEXT NOT NULL CHECK(state IN ('PREPARING','READY','RECLAIMING','RECLAIMED','FAILED')),
 UNIQUE(batch_id,owner_epoch), UNIQUE(kb_id,batch_id,artifact_id),
 FOREIGN KEY(kb_id,revision_id) REFERENCES revisions(kb_id,revision_id),
 FOREIGN KEY(kb_id,batch_id) REFERENCES mutation_batches(kb_id,batch_id),
 CHECK(state<>'READY' OR (expected_hash IS NOT NULL AND validation_json IS NOT NULL))
);
INSERT INTO r14_index_artifacts SELECT * FROM index_artifacts;
DROP TABLE index_artifacts;
ALTER TABLE r14_index_artifacts RENAME TO index_artifacts;
CREATE TRIGGER immutable_artifact_identity BEFORE UPDATE OF artifact_id,kb_id,revision_id,batch_id,collection_name,schema_hash,owner_epoch,spec_json ON index_artifacts BEGIN SELECT RAISE(ABORT,'immutable index identity'); END;
CREATE TRIGGER sealed_artifact_content BEFORE UPDATE OF expected_hash,validation_json ON index_artifacts WHEN OLD.state<>'PREPARING' BEGIN SELECT RAISE(ABORT,'sealed index validation'); END;
CREATE TRIGGER historical_artifact_delete BEFORE DELETE ON index_artifacts BEGIN SELECT RAISE(ABORT,'historical index identity'); END;
CREATE TABLE current_candidates (
 batch_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, artifact_id TEXT NOT NULL UNIQUE,
 FOREIGN KEY(kb_id,batch_id,artifact_id) REFERENCES index_artifacts(kb_id,batch_id,artifact_id)
);
INSERT INTO current_candidates SELECT batch_id,kb_id,artifact_id FROM index_artifacts;
CREATE TABLE artifact_creation_intents (
 artifact_id TEXT PRIMARY KEY REFERENCES index_artifacts(artifact_id),
 endpoint TEXT NOT NULL, database_name TEXT NOT NULL, marker TEXT NOT NULL UNIQUE
);
CREATE TABLE artifact_ownership_proofs (
 artifact_id TEXT PRIMARY KEY REFERENCES artifact_creation_intents(artifact_id),
 collection_id TEXT NOT NULL, created_timestamp TEXT NOT NULL, description TEXT NOT NULL,
 observed_at TEXT NOT NULL
);
CREATE TRIGGER immutable_creation_intent_update BEFORE UPDATE ON artifact_creation_intents BEGIN SELECT RAISE(ABORT,'immutable creation intent'); END;
CREATE TRIGGER immutable_creation_intent_delete BEFORE DELETE ON artifact_creation_intents BEGIN SELECT RAISE(ABORT,'historical creation intent'); END;
CREATE TRIGGER immutable_ownership_proof_update BEFORE UPDATE ON artifact_ownership_proofs BEGIN SELECT RAISE(ABORT,'immutable physical ownership'); END;
CREATE TRIGGER immutable_ownership_proof_delete BEFORE DELETE ON artifact_ownership_proofs BEGIN SELECT RAISE(ABORT,'historical physical ownership'); END;
CREATE TABLE mutation_executions (
 batch_id TEXT NOT NULL REFERENCES mutation_batches(batch_id), owner_epoch INTEGER NOT NULL,
 owner_nonce TEXT NOT NULL, pid INTEGER NOT NULL, process_birth TEXT NOT NULL,
 started_at TEXT NOT NULL, PRIMARY KEY(batch_id,owner_epoch)
);
CREATE TRIGGER immutable_execution_update BEFORE UPDATE ON mutation_executions BEGIN SELECT RAISE(ABORT,'immutable execution identity'); END;
CREATE TRIGGER immutable_execution_delete BEFORE DELETE ON mutation_executions BEGIN SELECT RAISE(ABORT,'historical execution identity'); END;
CREATE TABLE mutation_io (
 io_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES mutation_batches(batch_id),
 artifact_id TEXT REFERENCES index_artifacts(artifact_id), owner_epoch INTEGER NOT NULL,
 owner_nonce TEXT NOT NULL, kind TEXT NOT NULL, pid INTEGER NOT NULL, process_birth TEXT NOT NULL,
 worker_pid INTEGER, worker_birth TEXT, state TEXT NOT NULL CHECK(state IN ('pending','finished')),
 detail TEXT NOT NULL CHECK(json_valid(detail)),
 CHECK((worker_pid IS NULL)=(worker_birth IS NULL))
);
CREATE TRIGGER immutable_io_identity BEFORE UPDATE OF io_id,batch_id,artifact_id,owner_epoch,owner_nonce,kind,pid,process_birth ON mutation_io BEGIN SELECT RAISE(ABORT,'immutable IO identity'); END;
CREATE TRIGGER sealed_io BEFORE UPDATE ON mutation_io WHEN OLD.state='finished' BEGIN SELECT RAISE(ABORT,'finished IO receipt'); END;
CREATE TRIGGER historical_io_delete BEFORE DELETE ON mutation_io BEGIN SELECT RAISE(ABORT,'historical IO receipt'); END;
CREATE TABLE recovery_plans (
 plan_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES mutation_batches(batch_id),
 owner_epoch INTEGER NOT NULL, plan_json TEXT NOT NULL CHECK(json_valid(plan_json)), created_at TEXT NOT NULL
);
CREATE TABLE mutation_abandonments (
 batch_id TEXT PRIMARY KEY REFERENCES mutation_batches(batch_id),
 result_json TEXT NOT NULL CHECK(json_valid(result_json))
);
CREATE TRIGGER immutable_abandonment_update BEFORE UPDATE ON mutation_abandonments BEGIN SELECT RAISE(ABORT,'immutable abandonment'); END;
CREATE TRIGGER immutable_abandonment_delete BEFORE DELETE ON mutation_abandonments BEGIN SELECT RAISE(ABORT,'historical abandonment'); END;
CREATE TABLE candidate_cleanup_attempts (
 attempt_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES index_artifacts(artifact_id),
 result_json TEXT NOT NULL CHECK(json_valid(result_json)), attempted_at TEXT NOT NULL
);
