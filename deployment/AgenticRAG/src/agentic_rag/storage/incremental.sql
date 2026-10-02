-- Migration 12. Logical revisions share physical collections without rewriting history.
CREATE TABLE artifact_collections (
 artifact_id TEXT PRIMARY KEY REFERENCES index_artifacts(artifact_id),
 collection_artifact_id TEXT NOT NULL REFERENCES index_artifacts(artifact_id)
);
CREATE INDEX collection_revisions ON artifact_collections(collection_artifact_id);
CREATE TRIGGER immutable_collection_binding_update BEFORE UPDATE ON artifact_collections BEGIN SELECT RAISE(ABORT,'immutable collection binding'); END;
CREATE TRIGGER immutable_collection_binding_delete BEFORE DELETE ON artifact_collections BEGIN SELECT RAISE(ABORT,'historical collection binding'); END;
CREATE TABLE indexed_document_versions (
 collection_artifact_id TEXT NOT NULL REFERENCES index_artifacts(artifact_id),
 document_version_id TEXT NOT NULL REFERENCES document_versions(document_version_id),
 producer_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
 PRIMARY KEY(collection_artifact_id,document_version_id)
);
CREATE TRIGGER immutable_indexed_version_update BEFORE UPDATE ON indexed_document_versions BEGIN SELECT RAISE(ABORT,'immutable indexed version'); END;
CREATE TRIGGER immutable_indexed_version_delete BEFORE DELETE ON indexed_document_versions BEGIN SELECT RAISE(ABORT,'historical indexed version'); END;
CREATE TABLE watched_sources (
 kb_id TEXT NOT NULL REFERENCES libraries(kb_id), path TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 last_result TEXT CHECK(last_result IS NULL OR json_valid(last_result)),
 PRIMARY KEY(kb_id,path)
);
