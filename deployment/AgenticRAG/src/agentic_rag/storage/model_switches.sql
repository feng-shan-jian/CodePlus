-- Migration 10. A model choice links to the existing mutation lifecycle.
CREATE TABLE model_switches (
 proposal_id TEXT PRIMARY KEY,
 kb_id TEXT NOT NULL REFERENCES libraries(kb_id),
 base_revision_id TEXT NOT NULL,
 target_fingerprint TEXT NOT NULL,
 target_snapshot TEXT NOT NULL CHECK(json_valid(target_snapshot)),
 decision TEXT NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','approved','kept')),
 batch_id TEXT UNIQUE,
 errors TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(errors)),
 created_at TEXT NOT NULL,
 FOREIGN KEY(kb_id,base_revision_id) REFERENCES revisions(kb_id,revision_id),
 FOREIGN KEY(kb_id,batch_id) REFERENCES mutation_batches(kb_id,batch_id),
 CHECK(decision!='approved' OR batch_id IS NOT NULL)
);
CREATE INDEX model_switch_target ON model_switches(kb_id,target_fingerprint);
