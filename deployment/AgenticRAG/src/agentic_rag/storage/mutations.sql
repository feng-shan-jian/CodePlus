-- Migration 7. Ordinary mutation requests and terminal per-file encoding results.
-- Artifact owner_epoch remains the generation that wrote it, not a recovery owner.
CREATE TABLE ordinary_mutations (
 batch_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 FOREIGN KEY(kb_id,batch_id) REFERENCES mutation_batches(kb_id,batch_id)
);
CREATE TABLE mutation_item_results (
 item_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, batch_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('unchanged','encoded','failed')),
 encoded_hash TEXT REFERENCES archive_objects(sha256),
 error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json)),
 CHECK((state='encoded')=(encoded_hash IS NOT NULL)),
 CHECK((state='failed')=(error_json IS NOT NULL)),
 FOREIGN KEY(kb_id,batch_id,item_id) REFERENCES input_items(kb_id,batch_id,item_id)
);
CREATE TABLE mutation_completions (
 batch_id TEXT PRIMARY KEY, kb_id TEXT NOT NULL,
 summary_json TEXT NOT NULL CHECK(json_valid(summary_json)),
 FOREIGN KEY(kb_id,batch_id) REFERENCES mutation_batches(kb_id,batch_id)
);
CREATE TRIGGER immutable_ordinary_mutations_update BEFORE UPDATE ON ordinary_mutations BEGIN SELECT RAISE(ABORT,'immutable mutation request'); END;
CREATE TRIGGER immutable_ordinary_mutations_delete BEFORE DELETE ON ordinary_mutations BEGIN SELECT RAISE(ABORT,'historical mutation request'); END;
CREATE TRIGGER immutable_mutation_item_results_update BEFORE UPDATE ON mutation_item_results BEGIN SELECT RAISE(ABORT,'immutable file result'); END;
CREATE TRIGGER immutable_mutation_item_results_delete BEFORE DELETE ON mutation_item_results BEGIN SELECT RAISE(ABORT,'historical file result'); END;
CREATE TRIGGER immutable_mutation_completions_update BEFORE UPDATE ON mutation_completions BEGIN SELECT RAISE(ABORT,'immutable batch result'); END;
CREATE TRIGGER immutable_mutation_completions_delete BEFORE DELETE ON mutation_completions BEGIN SELECT RAISE(ABORT,'historical batch result'); END;
