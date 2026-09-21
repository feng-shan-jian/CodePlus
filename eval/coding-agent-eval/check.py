"""Check frozen SWE-bench data and agent-visible fields without running a model."""
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check():
    lock = read(ROOT / "source-lock.json")
    for relative, expected in lock["files"].items():
        path = (ROOT / relative).resolve()
        require(path.is_relative_to(ROOT), "Data path escapes evaluation directory")
        require(sha256(path.read_bytes()).hexdigest() == expected, f"Changed file: {relative}")
    rows = read(ROOT / "data/evaluator/dataset.json")
    by_id = {row["instance_id"]: row for row in rows}
    require(len(rows) == len(by_id) == 15, "Expected the 15 historically evaluated tasks")
    required = set(AGENT_FIELDS) | {"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"}
    require(all(required <= row.keys() for row in rows), "Incomplete evaluator record")
    agent_rows = [json.loads(line) for line in (ROOT / "data/agent/tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    expected_agent = [{key: row[key] for key in AGENT_FIELDS} for row in rows]
    require(agent_rows == expected_agent, "Agent input must exactly match the four-field whitelist")
    tasks = read(ROOT / "selected_tasks.json")
    ids = [task["instance_id"] for task in tasks]
    require(len(ids) == len(set(ids)) == 15, "Unexpected selected task count")
    require(list(by_id) == ids == lock["selected_instance_ids"], "Frozen task identity or order changed")
    require(all(task["repo"] == by_id[task["instance_id"]]["repo"] for task in tasks), "Task repository mismatch")
    require(not (ROOT / "candidate_tasks.json").exists(), "Retired candidate pool still present")
    require(not (ROOT / "data/evaluator/lite.parquet").exists(), "Full 300-task snapshot still present")
    return {"status": "passed", "scope": "dataset_integrity_and_agent_field_whitelist_only",
            "dataset": lock["dataset"], "revision": lock["revision"], "tasks": len(rows),
            "selected_tasks": len(tasks), "agent_fields": list(AGENT_FIELDS),
            "source_lock_sha256": sha256((ROOT / "source-lock.json").read_bytes()).hexdigest(),
            "model_evaluation": "not_executed", "official_docker_evaluation": "not_executed"}


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=2))
