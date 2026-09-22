-- v6: optional host execution ledger; existing run/config/source DTOs unchanged.
CREATE TABLE host_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    frozen TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    cleanup_state TEXT NOT NULL DEFAULT 'active' CHECK(cleanup_state IN ('active','pending','released')),
    cleanup_handles TEXT NOT NULL DEFAULT '[]',
    artifact TEXT,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE model_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES host_runs(run_id),
    purpose TEXT NOT NULL,
    protocol TEXT NOT NULL,
    input_upper INTEGER NOT NULL CHECK(input_upper>=0),
    output_cap INTEGER NOT NULL CHECK(output_cap>0),
    charged INTEGER NOT NULL CHECK(charged>=0),
    state TEXT NOT NULL CHECK(state IN ('reserved','not_sent','rejected','unknown','confirmed')),
    body_sha256 TEXT NOT NULL,
    raw_usage TEXT,
    terminal TEXT,
    outcome TEXT,
    violation TEXT
);
CREATE INDEX model_requests_run ON model_requests(run_id);
CREATE TABLE host_tool_calls (
    run_id TEXT NOT NULL REFERENCES host_runs(run_id),
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY(run_id,tool_call_id)
);
