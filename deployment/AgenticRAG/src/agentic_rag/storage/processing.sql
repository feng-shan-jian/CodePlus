-- Migration 3. The ImportItem is the sole processing-stage authority.
CREATE TABLE processing_items (
 item_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, batch_id TEXT NOT NULL, document_id TEXT NOT NULL,
 document_version_id TEXT, snapshot_id TEXT NOT NULL, chunk_set_hash TEXT REFERENCES archive_objects(sha256),
 item_json TEXT NOT NULL CHECK(json_valid(item_json)),
 FOREIGN KEY(kb_id,batch_id,item_id) REFERENCES input_items(kb_id,batch_id,item_id),
 FOREIGN KEY(kb_id,document_id,document_version_id) REFERENCES document_versions(kb_id,document_id,document_version_id),
 FOREIGN KEY(kb_id,snapshot_id) REFERENCES processing_snapshots(kb_id,snapshot_id),
 CHECK((document_version_id IS NULL)=(chunk_set_hash IS NULL))
);
CREATE TRIGGER immutable_processing_items_update BEFORE UPDATE ON processing_items BEGIN SELECT RAISE(ABORT,'immutable processing checkpoint'); END;
CREATE TRIGGER immutable_processing_items_delete BEFORE DELETE ON processing_items BEGIN SELECT RAISE(ABORT,'historical processing checkpoint'); END;
CREATE TRIGGER completed_sections_insert BEFORE INSERT ON sections WHEN EXISTS(SELECT 1 FROM processing_items WHERE document_version_id=NEW.document_version_id) BEGIN SELECT RAISE(ABORT,'complete processing structure'); END;
CREATE TRIGGER completed_chunks_insert BEFORE INSERT ON chunks WHEN EXISTS(SELECT 1 FROM processing_items WHERE document_version_id=NEW.document_version_id) BEGIN SELECT RAISE(ABORT,'complete processing structure'); END;
