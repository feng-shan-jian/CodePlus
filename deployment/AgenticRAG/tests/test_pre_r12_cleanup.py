"""Retired retrieval entry point and retained R01 PowerShell contracts.

All answer/retrieval inputs below are controlled offline fixtures, not model or
retrieval quality evidence. R01's original data/scorer assertions remain active.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "eval/RAG-eval"


def invoke(args, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return subprocess.run(["pwsh", "-NoProfile", "-File", *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


@pytest.mark.parametrize("mode", ["dense", "bm25", "hybrid"])
def test_retired_retrieval_rejects_before_environment_or_report_access(tmp_path, mode):
    # A standalone script has no project root interpreter/dataset. The intended
    # diagnostic must win before any attempt to import or launch the retired core.
    standalone = tmp_path / "run.ps1"
    standalone.write_bytes((SUITE / "run.ps1").read_bytes())
    result = invoke([standalone, "-KbId", "retired-library", "-Mode", mode,
                     "-ManagedLocal", "-Tier", "lite"], tmp_path)
    assert result.returncode != 0
    assert "旧检索引擎已移除，新引擎宿主/评测接入尚未完成" in result.stderr
    assert "Install the project" not in result.stderr
    assert sorted(path.name for path in tmp_path.iterdir()) == ["run.ps1"]


def test_powershell_caller_relative_answers_and_replay(tmp_path):
    selected = set(json.loads((SUITE / "tiers.json").read_text(encoding="utf-8"))["tiers"]["lite"]["question_ids"])
    questions = [q for q in json.loads((SUITE / "questions.json").read_text(encoding="utf-8"))["questions"] if q["id"] in selected]
    base = [SUITE / "run.ps1", "-Tier", "lite"]
    checked = invoke([*base, "-Check"], tmp_path)
    assert checked.returncode == 0, checked.stderr
    dataset_sha = json.loads(checked.stdout)["dataset_sha256"]
    (tmp_path / "answers.json").write_text(json.dumps([
        {"id": q["id"], "query": q["query"], "model_answer": "", "status": "error"}
        for q in questions]), encoding="utf-8")
    answers = invoke([*base, "-Answers", "answers.json"], tmp_path)
    assert answers.returncode == 0, answers.stderr
    answer_report = json.loads(answers.stdout)
    assert answer_report["selected_questions"] == answer_report["request_errors"] == 50
    fixture = {"dataset_sha256": dataset_sha, "protocol": {"top_k": 10},
               "retrieval_request": {"mode": "dense", "top_k": 10, "candidates": 50, "rrf_k": 60},
               "records": [{"id": q["id"], "query": q["query"], "status": "error", "hits": [],
                            "elapsed_ms": 0.0} for q in questions]}
    source = tmp_path / "retrieval.json"
    source.write_text(json.dumps(fixture), encoding="utf-8")
    original = source.read_bytes()
    replayed = invoke([*base, "-Replay", "retrieval.json"], tmp_path)
    assert replayed.returncode == 1 and replayed.stdout, replayed.stdout + replayed.stderr
    result = json.loads(replayed.stdout)
    report = Path(result["report"])
    try:
        assert result["status"] == "query_failures"
        assert report.is_file() and report.with_suffix(".md").is_file()
        assert Path(result["upstream_score"]).is_file()
        assert result["upstream_summary"]["request_errors"] == 50
        assert result["upstream_summary"]["scored_questions"] == 44
        assert result["upstream_summary"]["excluded_null_queries"] == 6
        assert source.read_bytes() == original
    finally:
        owned = report.parent.resolve()
        assert owned.parent == (SUITE / "runs").resolve()
        assert not owned.is_symlink()
        shutil.rmtree(owned)
