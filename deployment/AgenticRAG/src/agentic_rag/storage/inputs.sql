-- Migration 2. Request identity and capture results are separate immutable records.
CREATE TABLE input_manifests (
 batch_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 request_json TEXT NOT NULL CHECK(json_valid(request_json)), capture_epoch INTEGER NOT NULL CHECK(capture_epoch>0),
 UNIQUE(kb_id,batch_id), FOREIGN KEY(kb_id,batch_id) REFERENCES mutation_batches(kb_id,batch_id)
);
CREATE TABLE input_items (
 item_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, kb_id TEXT NOT NULL, ordinal INTEGER NOT NULL CHECK(ordinal>=0),
 document_id TEXT, base_version_id TEXT, initial_json TEXT NOT NULL CHECK(json_valid(initial_json)),
 UNIQUE(batch_id,ordinal), UNIQUE(kb_id,batch_id,item_id),
 FOREIGN KEY(kb_id,batch_id) REFERENCES input_manifests(kb_id,batch_id),
 FOREIGN KEY(kb_id,document_id) REFERENCES documents(kb_id,document_id),
 FOREIGN KEY(kb_id,document_id,base_version_id) REFERENCES document_versions(kb_id,document_id,document_version_id)
);
CREATE TABLE input_results (
 item_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('captured','failed')), raw_hash TEXT REFERENCES archive_objects(sha256),
 result_json TEXT NOT NULL CHECK(json_valid(result_json)), CHECK((stage='captured')=(raw_hash IS NOT NULL)),
 FOREIGN KEY(kb_id,batch_id,item_id) REFERENCES input_items(kb_id,batch_id,item_id)
);
CREATE TABLE document_sources (
 item_id TEXT PRIMARY KEY REFERENCES input_results(item_id), document_id TEXT NOT NULL, kb_id TEXT NOT NULL,
 previous_key TEXT, source_key TEXT NOT NULL, original_name TEXT NOT NULL,
 FOREIGN KEY(kb_id,document_id) REFERENCES documents(kb_id,document_id)
);
CREATE TRIGGER immutable_input_manifests_update BEFORE UPDATE ON input_manifests BEGIN SELECT RAISE(ABORT,'immutable input manifest'); END;
CREATE TRIGGER immutable_input_manifests_delete BEFORE DELETE ON input_manifests BEGIN SELECT RAISE(ABORT,'historical input manifest'); END;
CREATE TRIGGER immutable_input_items_update BEFORE UPDATE ON input_items BEGIN SELECT RAISE(ABORT,'immutable input item'); END;
CREATE TRIGGER immutable_input_items_delete BEFORE DELETE ON input_items BEGIN SELECT RAISE(ABORT,'historical input item'); END;
CREATE TRIGGER immutable_input_results_update BEFORE UPDATE ON input_results BEGIN SELECT RAISE(ABORT,'immutable input result'); END;
CREATE TRIGGER immutable_input_results_delete BEFORE DELETE ON input_results BEGIN SELECT RAISE(ABORT,'historical input result'); END;
CREATE TRIGGER immutable_document_sources_update BEFORE UPDATE ON document_sources BEGIN SELECT RAISE(ABORT,'immutable document source history'); END;
CREATE TRIGGER immutable_document_sources_delete BEFORE DELETE ON document_sources BEGIN SELECT RAISE(ABORT,'historical document source'); END;
