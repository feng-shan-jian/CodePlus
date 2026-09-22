-- Migration 5. Durable source capabilities, delivery provenance and citations.
CREATE TABLE source_handles (
 token TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 kind TEXT NOT NULL CHECK(kind IN ('source','cursor')), payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE source_usage (
 run_id TEXT PRIMARY KEY REFERENCES runs(run_id), started_ns INTEGER NOT NULL,
 searches INTEGER NOT NULL DEFAULT 0, opens INTEGER NOT NULL DEFAULT 0,
 rejected INTEGER NOT NULL DEFAULT 0, returned_tokens INTEGER NOT NULL DEFAULT 0,
 returned_fragments INTEGER NOT NULL DEFAULT 0, meter_identity TEXT NOT NULL,
 window_tokens INTEGER NOT NULL DEFAULT 0, window_fragments INTEGER NOT NULL DEFAULT 0,
 window_generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE source_calls (
 call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 kind TEXT NOT NULL CHECK(kind IN ('search','open')), status TEXT NOT NULL,
 tokens INTEGER NOT NULL DEFAULT 0, fragments INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE TABLE source_candidates (
 candidate_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 call_id TEXT NOT NULL REFERENCES source_calls(call_id), evidence_id TEXT NOT NULL UNIQUE,
 payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE delivery_receipts (
 request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 status TEXT NOT NULL CHECK(status IN ('prepared','not_sent','rejected','unknown','confirmed')),
 purpose TEXT NOT NULL, protocol TEXT NOT NULL, body_sha256 TEXT NOT NULL,
 payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE delivered_evidence (
 evidence_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE evidence_windows (
 run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE saved_citations (
 citation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 evidence_id TEXT NOT NULL REFERENCES delivered_evidence(evidence_id),
 payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TRIGGER immutable_source_handle BEFORE UPDATE ON source_handles BEGIN SELECT RAISE(ABORT,'immutable source capability'); END;
CREATE TRIGGER immutable_source_candidate BEFORE UPDATE ON source_candidates BEGIN SELECT RAISE(ABORT,'immutable source candidate'); END;
CREATE TRIGGER immutable_saved_citation BEFORE UPDATE ON saved_citations BEGIN SELECT RAISE(ABORT,'immutable historical citation'); END;
CREATE TRIGGER retain_saved_citation BEFORE DELETE ON saved_citations BEGIN SELECT RAISE(ABORT,'historical citation'); END;
CREATE TRIGGER sealed_delivery_receipt BEFORE UPDATE ON delivery_receipts WHEN OLD.status<>'prepared' BEGIN SELECT RAISE(ABORT,'terminal delivery receipt'); END;
