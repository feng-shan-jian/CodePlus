-- Migration 1. Scalar identities and lifecycle fields are relational authority.
CREATE TABLE store_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), store_id TEXT NOT NULL);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE libraries (
 kb_id TEXT PRIMARY KEY, name TEXT NOT NULL CHECK(length(name)>0),
 current_revision_id TEXT, pending_mutation_id TEXT, owner_epoch INTEGER NOT NULL DEFAULT 0 CHECK(owner_epoch>=0),
 FOREIGN KEY(kb_id,current_revision_id) REFERENCES revisions(kb_id,revision_id),
 FOREIGN KEY(kb_id,pending_mutation_id) REFERENCES mutation_batches(kb_id,batch_id)
);
CREATE TABLE processing_snapshots (
 snapshot_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES libraries(kb_id),
 config_fingerprint TEXT NOT NULL, document_encoding_fingerprint TEXT NOT NULL, index_fingerprint TEXT NOT NULL,
 resolved_config TEXT NOT NULL CHECK(json_valid(resolved_config)), UNIQUE(kb_id,snapshot_id)
);
CREATE TABLE archive_objects (sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64), size_bytes INTEGER NOT NULL CHECK(size_bytes>=0));
CREATE TABLE documents (
 document_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES libraries(kb_id), source_key TEXT NOT NULL,
 source_key_version INTEGER NOT NULL CHECK(source_key_version=1), original_name TEXT NOT NULL,
 UNIQUE(kb_id,document_id), UNIQUE(kb_id,source_key)
);
CREATE TABLE document_versions (
 document_version_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, document_id TEXT NOT NULL,
 raw_hash TEXT NOT NULL REFERENCES archive_objects(sha256), parsed_hash TEXT NOT NULL REFERENCES archive_objects(sha256),
 source_map_hash TEXT NOT NULL REFERENCES archive_objects(sha256), parser_fingerprint TEXT NOT NULL,
 source_uri TEXT NOT NULL, captured_at TEXT NOT NULL, source_metadata TEXT NOT NULL CHECK(json_valid(source_metadata)),
 UNIQUE(kb_id,document_id,document_version_id), UNIQUE(kb_id,document_version_id),
 FOREIGN KEY(kb_id,document_id) REFERENCES documents(kb_id,document_id)
);
CREATE TABLE sections (
 section_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, document_version_id TEXT NOT NULL,
 heading_path TEXT NOT NULL CHECK(json_valid(heading_path)), start INTEGER NOT NULL CHECK(start>=0), end INTEGER NOT NULL CHECK(end>start),
 UNIQUE(kb_id,document_version_id,section_id),
 FOREIGN KEY(kb_id,document_version_id) REFERENCES document_versions(kb_id,document_version_id)
);
CREATE TABLE chunks (
 chunk_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, document_version_id TEXT NOT NULL, section_id TEXT NOT NULL,
 spans TEXT NOT NULL CHECK(json_valid(spans)), text_hash TEXT NOT NULL, chunker_fingerprint TEXT NOT NULL,
 FOREIGN KEY(kb_id,document_version_id,section_id) REFERENCES sections(kb_id,document_version_id,section_id)
);
CREATE TABLE revisions (
 revision_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES libraries(kb_id), base_revision_id TEXT,
 manifest_hash TEXT NOT NULL, processing_snapshot_id TEXT NOT NULL,
 index_state TEXT NOT NULL CHECK(index_state IN ('PREPARING','READY','RECLAIMING','RECLAIMED','FAILED')),
 UNIQUE(kb_id,revision_id), CHECK(base_revision_id IS NULL OR base_revision_id<>revision_id),
 FOREIGN KEY(kb_id,base_revision_id) REFERENCES revisions(kb_id,revision_id),
 FOREIGN KEY(kb_id,processing_snapshot_id) REFERENCES processing_snapshots(kb_id,snapshot_id)
);
CREATE TABLE revision_members (
 kb_id TEXT NOT NULL, revision_id TEXT NOT NULL, document_id TEXT NOT NULL, document_version_id TEXT NOT NULL, chunk_set_hash TEXT NOT NULL,
 PRIMARY KEY(revision_id,document_id),
 FOREIGN KEY(kb_id,revision_id) REFERENCES revisions(kb_id,revision_id),
 FOREIGN KEY(kb_id,document_id,document_version_id) REFERENCES document_versions(kb_id,document_id,document_version_id)
);
CREATE TABLE mutation_batches (
 batch_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL REFERENCES libraries(kb_id), base_revision_id TEXT,
 input_manifest_hash TEXT NOT NULL, processing_snapshot_id TEXT NOT NULL,
 owner_epoch INTEGER NOT NULL CHECK(owner_epoch>0), owner_nonce TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('SNAPSHOTTING','PROCESSING','INDEXING','VALIDATING','READY','PUBLISHED','WAITING_RECOVERY','ABANDONED','COMPLETED_NO_CHANGE')),
 published_revision_id TEXT, recovery_stage TEXT,
 UNIQUE(kb_id,batch_id),
 FOREIGN KEY(kb_id,base_revision_id) REFERENCES revisions(kb_id,revision_id),
 FOREIGN KEY(kb_id,published_revision_id) REFERENCES revisions(kb_id,revision_id),
 FOREIGN KEY(kb_id,processing_snapshot_id) REFERENCES processing_snapshots(kb_id,snapshot_id),
 CHECK((state='PUBLISHED')=(published_revision_id IS NOT NULL)),
 CHECK((state='WAITING_RECOVERY')=(recovery_stage IS NOT NULL)),
 CHECK(recovery_stage IS NULL OR recovery_stage IN ('SNAPSHOTTING','PROCESSING','INDEXING','VALIDATING','READY'))
);
CREATE TABLE revision_dependencies (
 kb_id TEXT NOT NULL, batch_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 purpose TEXT NOT NULL CHECK(purpose IN ('base','candidate','recovery','vector_reuse')),
 PRIMARY KEY(batch_id,revision_id,purpose),
 FOREIGN KEY(kb_id,batch_id) REFERENCES mutation_batches(kb_id,batch_id),
 FOREIGN KEY(kb_id,revision_id) REFERENCES revisions(kb_id,revision_id)
);
CREATE TABLE runs (
 run_id TEXT PRIMARY KEY, parent_run_id TEXT REFERENCES runs(run_id), kb_id TEXT NOT NULL, revision_id TEXT NOT NULL,
 resolved_config TEXT NOT NULL CHECK(json_valid(resolved_config)), resolved_config_hash TEXT NOT NULL,
 usage TEXT NOT NULL CHECK(json_valid(usage)),
 status TEXT NOT NULL CHECK(status IN ('running','completed','partial','incomplete','failed','cancelled')), stop_reason TEXT,
 CHECK(parent_run_id IS NULL OR parent_run_id<>run_id),
 CHECK((status='running')=(stop_reason IS NULL)), UNIQUE(run_id,revision_id),
 FOREIGN KEY(kb_id,revision_id) REFERENCES revisions(kb_id,revision_id)
);
CREATE TABLE run_pins (
 run_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, owner_nonce TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','released')),
 FOREIGN KEY(run_id,revision_id) REFERENCES runs(run_id,revision_id)
);
CREATE INDEX active_pins ON run_pins(revision_id,state);
CREATE INDEX dependencies_by_revision ON revision_dependencies(revision_id);
CREATE TRIGGER immutable_versions_update BEFORE UPDATE ON document_versions BEGIN SELECT RAISE(ABORT,'immutable document version'); END;
CREATE TRIGGER immutable_versions_delete BEFORE DELETE ON document_versions BEGIN SELECT RAISE(ABORT,'historical document version'); END;
CREATE TRIGGER immutable_snapshots_update BEFORE UPDATE ON processing_snapshots BEGIN SELECT RAISE(ABORT,'immutable processing snapshot'); END;
CREATE TRIGGER immutable_snapshots_delete BEFORE DELETE ON processing_snapshots BEGIN SELECT RAISE(ABORT,'historical processing snapshot'); END;
CREATE TRIGGER immutable_sections_update BEFORE UPDATE ON sections BEGIN SELECT RAISE(ABORT,'immutable section'); END;
CREATE TRIGGER immutable_sections_delete BEFORE DELETE ON sections BEGIN SELECT RAISE(ABORT,'historical section'); END;
CREATE TRIGGER immutable_chunks_update BEFORE UPDATE ON chunks BEGIN SELECT RAISE(ABORT,'immutable chunk'); END;
CREATE TRIGGER immutable_chunks_delete BEFORE DELETE ON chunks BEGIN SELECT RAISE(ABORT,'historical chunk'); END;
CREATE TRIGGER immutable_members_update BEFORE UPDATE ON revision_members BEGIN SELECT RAISE(ABORT,'immutable revision member'); END;
CREATE TRIGGER immutable_members_delete BEFORE DELETE ON revision_members BEGIN SELECT RAISE(ABORT,'historical revision member'); END;
CREATE TRIGGER sealed_members_insert BEFORE INSERT ON revision_members WHEN (SELECT index_state FROM revisions WHERE revision_id=NEW.revision_id)<>'PREPARING' BEGIN SELECT RAISE(ABORT,'sealed revision members'); END;
CREATE TRIGGER immutable_revision_identity BEFORE UPDATE OF revision_id,kb_id,base_revision_id,manifest_hash,processing_snapshot_id ON revisions BEGIN SELECT RAISE(ABORT,'immutable revision identity'); END;
CREATE TRIGGER immutable_run_binding BEFORE UPDATE OF run_id,kb_id,revision_id,resolved_config,resolved_config_hash ON runs BEGIN SELECT RAISE(ABORT,'immutable run binding'); END;
CREATE TRIGGER sealed_sections_insert BEFORE INSERT ON sections WHEN EXISTS(SELECT 1 FROM revision_members WHERE document_version_id=NEW.document_version_id) BEGIN SELECT RAISE(ABORT,'sealed document structure'); END;
CREATE TRIGGER sealed_chunks_insert BEFORE INSERT ON chunks WHEN EXISTS(SELECT 1 FROM revision_members WHERE document_version_id=NEW.document_version_id) BEGIN SELECT RAISE(ABORT,'sealed document structure'); END;
