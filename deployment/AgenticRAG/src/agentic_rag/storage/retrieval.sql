-- Migration 11. Diagnostics are not source candidates or delivered evidence.
CREATE TABLE retrieval_traces (
 call_id TEXT PRIMARY KEY REFERENCES source_calls(call_id),
 payload TEXT NOT NULL CHECK(json_valid(payload))
);
