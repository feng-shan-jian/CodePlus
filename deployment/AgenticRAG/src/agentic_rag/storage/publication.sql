-- Migration 4. Existing READY fixtures are not fabricated into published artifacts.
CREATE TABLE index_artifacts (
 artifact_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, revision_id TEXT NOT NULL UNIQUE,
 batch_id TEXT NOT NULL UNIQUE, collection_name TEXT NOT NULL UNIQUE,
 schema_hash TEXT NOT NULL, owner_epoch INTEGER NOT NULL CHECK(owner_epoch>0),
 spec_json TEXT NOT NULL CHECK(json_valid(spec_json)),
 expected_hash TEXT REFERENCES archive_objects(sha256),
 validation_json TEXT CHECK(validation_json IS NULL OR json_valid(validation_json)),
 state TEXT NOT NULL CHECK(state IN ('PREPARING','READY','RECLAIMING','RECLAIMED','FAILED')),
 FOREIGN KEY(kb_id,revision_id) REFERENCES revisions(kb_id,revision_id),
 FOREIGN KEY(kb_id,batch_id) REFERENCES mutation_batches(kb_id,batch_id),
 CHECK(state<>'READY' OR (expected_hash IS NOT NULL AND validation_json IS NOT NULL))
);
CREATE TABLE publications (
 batch_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, revision_id TEXT NOT NULL UNIQUE,
 artifact_id TEXT NOT NULL UNIQUE REFERENCES index_artifacts(artifact_id),
 owner_nonce TEXT NOT NULL, owner_epoch INTEGER NOT NULL,
 manifest_hash TEXT NOT NULL, published_at TEXT NOT NULL,
 FOREIGN KEY(kb_id,batch_id) REFERENCES mutation_batches(kb_id,batch_id),
 FOREIGN KEY(kb_id,revision_id) REFERENCES revisions(kb_id,revision_id)
);
CREATE TRIGGER immutable_publication_update BEFORE UPDATE ON publications BEGIN SELECT RAISE(ABORT,'immutable publication'); END;
CREATE TRIGGER immutable_publication_delete BEFORE DELETE ON publications BEGIN SELECT RAISE(ABORT,'historical publication'); END;
CREATE TRIGGER immutable_artifact_identity BEFORE UPDATE OF artifact_id,kb_id,revision_id,batch_id,collection_name,schema_hash,owner_epoch,spec_json ON index_artifacts BEGIN SELECT RAISE(ABORT,'immutable index identity'); END;
CREATE TRIGGER sealed_artifact_content BEFORE UPDATE OF expected_hash,validation_json ON index_artifacts WHEN OLD.state<>'PREPARING' BEGIN SELECT RAISE(ABORT,'sealed index validation'); END;
