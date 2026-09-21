"""Frozen data, offline CLI compatibility and query-only runtime boundary tests."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "eval" / "RAG-eval"
INTERNAL = ROOT / "deployment" / "AgenticRAG" / "eval"
RECORDS = INTERNAL.parent / "docs" / "implementation-records"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def runtime():
    return module("r01_runtime_inputs", INTERNAL / "runtime_inputs.py")


@pytest.fixture
def blocked_env(tmp_path):
    # A real interpreter startup hook also applies to Python launched by pwsh.
    (tmp_path / "sitecustomize.py").write_text(
        "import sys\n"
        "class NoHost:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'codeplus' or fullname.startswith('codeplus.'):\n"
        "            raise ImportError('R01 legacy host import forbidden: ' + fullname)\n"
        "sys.meta_path.insert(0, NoHost())\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    probe = subprocess.run([sys.executable, "-B", "-c", "import codeplus.knowledge"],
                           cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert probe.returncode != 0 and "R01 legacy host import forbidden" in probe.stderr
    return env


def run(command, env, expected=0):
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == expected, result.stdout + result.stderr
    assert "R01 legacy host import forbidden" not in result.stderr
    return json.loads(result.stdout)


def selected(tier="lite"):
    ids = set(read(SUITE / "tiers.json")["tiers"][tier]["question_ids"])
    return [q for q in read(SUITE / "questions.json")["questions"] if q["id"] in ids]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return str(path)


def frozen_hash(tier="lite"):
    return next(row["result"]["dataset_sha256"] for row in read(RECORDS / "R00-original-checks.json") if row["result"]["tier"] == tier)


def report(questions, failed=False):
    return {"dataset_sha256": frozen_hash(), "protocol": {"top_k": 10},
            "retrieval_request": {"mode": "hybrid", "candidates": 50, "rrf_k": 60, "top_k": 10},
            "records": [{"id": q["id"], "query": q["query"], "status": "error" if failed else "ok",
                         "hits": [], "elapsed_ms": 12.5} for q in questions]}


@pytest.mark.parametrize("tier", ["lite", "medium", "full"])
def test_check_matches_r00_with_host_imports_forbidden(tier, blocked_env):
    actual = run([sys.executable, "-B", str(SUITE / "check.py"), "--tier", tier], blocked_env)
    old = next(row["result"] for row in read(RECORDS / "R00-original-checks.json") if row["result"]["tier"] == tier)
    assert actual == old


def test_frozen_files_and_order_match_r00():
    rows = read(RECORDS / "R00-input-fingerprints.json")["evaluation_inputs"]
    seams = {"eval/RAG-eval/check.py", "eval/RAG-eval/run.ps1"}
    for row in rows:
        if row["path"] not in seams:
            assert sha256((ROOT / row["path"]).read_bytes()).hexdigest() == row["sha256"], row["path"]
    # Include deleted and inherited untracked input ownership, not just corpus files.
    for row in read(RECORDS / "R00-protected-inputs.json")["entries"]:
        if row["path"].startswith("eval/RAG-eval/") and row["path"] not in seams:
            path = ROOT / row["path"]
            assert (sha256(path.read_bytes()).hexdigest() if path.exists() else None) == row["sha256"], row["path"]
    actual = {p.relative_to(ROOT).as_posix() for p in (SUITE / "corpus").rglob("*") if p.is_file()}
    expected = {row["path"] for row in rows if row["path"].startswith("eval/RAG-eval/corpus/")}
    assert actual == expected and len(actual) == 609
    questions = read(SUITE / "questions.json")["questions"]
    assert len(questions) == 2556 and sum(len(q["gold"]) for q in questions) == 6084
    assert sum(q["track"] == "null_query" for q in questions) == 301


def test_query_manifest_and_split_ids_match_official_order(runtime):
    questions = read(SUITE / "questions.json")["questions"]
    manifest = read(SUITE / "dataset.json")
    tiers = read(SUITE / "tiers.json")["tiers"]
    ids = [q["id"] for q in questions]
    development = read(INTERNAL / "development-ids.json")
    acceptance = read(INTERNAL / "acceptance-ids.json")
    assert development == tiers["medium"]["question_ids"]
    assert development == [item for item in ids if item in set(development)]
    assert acceptance == [item for item in ids if item not in set(development)]
    assert (len(development), len(acceptance)) == (200, 2356)
    assert set(development).isdisjoint(acceptance)
    assert set(tiers["lite"]["question_ids"]) < set(development) < set(ids)
    assert set(development) | set(acceptance) == set(ids)
    clean = read(INTERNAL / "runtime-inputs.json")
    runtime.validate_runtime_inputs(clean)
    assert clean == {"questions": [{"id": q["id"], "query": q["query"]} for q in questions],
                     "corpus_paths": [d["path"] for d in manifest["documents"]]}
    assert [q["id"] for q in runtime.load_runtime_inputs(question_ids=list(reversed(development)))["questions"]] == development
    exposure = read(INTERNAL / "evaluation-protocol.json")["historical_exposure"]
    assert exposure["known"] and exposure["unknown"] and exposure["unseen_test_set_claim"] is False


@pytest.mark.parametrize("field", ["answer", "reference_answer", "gold", "supporting_context", "track", "question_type", "answerable", "payload"])
@pytest.mark.parametrize("location", ["root", "question", "nested_query", "nested_path"])
def test_runtime_rejects_extra_or_nested_scoring_fields(runtime, field, location):
    clean = {"questions": [{"id": "Q1", "query": "Question?"}], "corpus_paths": ["corpus/D0001.md"]}
    if location == "root":
        clean[field] = {"nested": "secret"}
    elif location == "question":
        clean["questions"][0][field] = {"nested": "secret"}
    elif location == "nested_query":
        clean["questions"][0]["query"] = {"text": "Question?", field: "secret"}
    else:
        clean["corpus_paths"][0] = {"path": "corpus/D0001.md", field: "secret"}
    with pytest.raises(ValueError):
        runtime.validate_runtime_inputs(clean)


@pytest.mark.parametrize("path", ["questions.json", "dataset.json", "../questions.json", "corpus/../questions.json", "corpus/answers.md", "C:/gold.json"])
def test_runtime_rejects_non_corpus_import_paths(runtime, path):
    with pytest.raises(ValueError):
        runtime.validate_runtime_inputs({"questions": [{"id": "Q1", "query": "Question?"}], "corpus_paths": [path]})


def test_runtime_rejects_modified_query_manifest(runtime, tmp_path, monkeypatch):
    clean = read(INTERNAL / "runtime-inputs.json")
    clean["questions"][0]["query"] = "Tampered question"
    path = tmp_path / "runtime-inputs.json"
    write(path, clean)
    monkeypatch.setattr(runtime, "INPUT_PATH", path)
    with pytest.raises(ValueError, match="checksum"):
        runtime.load_runtime_inputs()


@pytest.mark.parametrize("ids", [[], ["unknown"], ["MH-0001", "MH-0001"], [{"gold": "secret"}]])
def test_runtime_rejects_invalid_selection(runtime, ids):
    with pytest.raises(ValueError):
        runtime.load_runtime_inputs(question_ids=ids)


def test_runtime_subprocess_cannot_read_scoring_data(blocked_env):
    # The child installs an audit hook before importing the runtime loader.
    result = run([sys.executable, "-B", str(Path(__file__)), "--runtime-isolation"], blocked_env)
    assert result == {"questions": 2556, "corpus_paths": 609, "scoring_file_reads": 0}


@pytest.mark.parametrize("task", ["retrieval", "answers"])
def test_original_score_entry_keeps_failure_denominators(task, blocked_env, tmp_path):
    questions = selected()
    value = report(questions, failed=True) if task == "retrieval" else [
        {"id": q["id"], "query": q["query"], "status": "error", "model_answer": q["reference_answer"]} for q in questions]
    path = write(tmp_path / "input.json", value)
    result = run([sys.executable, "-B", str(SUITE / "score.py"), "--task", task, "--input", path, "--tier", "lite"], blocked_env)
    assert result["selected_questions"] == result["request_errors"] == 50
    assert result["scored_questions"] == (44 if task == "retrieval" else 50)
    if task == "retrieval":
        assert result["excluded_null_queries"] == 6
        assert all(value == 0 for value in result["overall"]["metrics"].values())
    else:
        assert result["overall"]["upstream_word_overlap_accuracy"] == 0
    assert result["model_called"] is False


def test_powershell_check_and_answers_with_host_forbidden(blocked_env, tmp_path):
    base = ["pwsh", "-NoProfile", "-File", str(SUITE / "run.ps1"), "-Tier", "lite"]
    checked = run(base + ["-Check"], blocked_env)
    assert checked["dataset_sha256"] == frozen_hash()
    answers = [{"id": q["id"], "query": q["query"], "model_answer": q["reference_answer"]} for q in selected()]
    path = write(tmp_path / "answers.json", answers)
    scored = run(base + ["-Answers", path], blocked_env)
    assert scored["scored_questions"] == 50
    assert scored["overall"]["upstream_word_overlap_accuracy"] == 1


@pytest.mark.parametrize("failed", [False, True])
def test_powershell_replay_preserves_native_report_and_exit(blocked_env, tmp_path, failed):
    questions = selected()
    value = report(questions, failed=failed)
    if not failed:
        question = next(q for q in questions if q["gold"])
        record = next(row for row in value["records"] if row["id"] == question["id"])
        record["hits"] = [{"source_id": gold["source_id"], "text": gold["quote"],
                           "source_spans": [{"char_start": gold["char_start"], "char_end": gold["char_end"]}]} for gold in question["gold"]]
    source = Path(write(tmp_path / "report.json", value))
    output = run(["pwsh", "-NoProfile", "-File", str(SUITE / "run.ps1"), "-Tier", "lite", "-Replay", str(source)],
                 blocked_env, expected=1 if failed else 0)
    report_path = Path(output["report"])
    try:
        replayed = read(report_path)
        assert output["status"] == ("query_failures" if failed else "ok")
        assert replayed["replayed_from"] == str(source.resolve())
        assert replayed["retrieval_request"] == value["retrieval_request"]
        assert [row["id"] for row in replayed["records"]] == [q["id"] for q in questions]
        assert replayed["summary"] == output["summary"]
        assert sum(group["questions"] for group in output["summary"].values()) == 50
        assert sum(group["request_errors"] for group in output["summary"].values()) == (50 if failed else 0)
        if not failed:
            scored = next(row for row in replayed["records"] if row["id"] == question["id"])
            assert scored["evidence"]["recall"] == 1
        assert report_path.with_suffix(".md").is_file()
        assert Path(output["upstream_score"]).is_file()
        assert output["upstream_summary"]["scored_questions"] == 44
        assert output["upstream_summary"]["request_errors"] == (50 if failed else 0)
        assert source.read_text(encoding="utf-8") == json.dumps(value, ensure_ascii=False)
    finally:
        assert report_path.parent.resolve().parent == (SUITE / "runs").resolve()
        shutil.rmtree(report_path.parent)


def test_native_replay_rejects_other_tier_before_writing(blocked_env, tmp_path):
    value = report(selected())
    path = write(tmp_path / "report.json", value)
    result = subprocess.run([sys.executable, "-B", str(SUITE / "replay.py"), "--replay", path],
                            cwd=ROOT, env=blocked_env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 1 and "different dataset version" in result.stderr


if __name__ == "__main__":
    assert sys.argv[1:] == ["--runtime-isolation"]
    # Deny every suite-side file read (including corpus content); resolving and
    # stat'ing corpus paths is sufficient to build a runtime payload.
    def no_scoring_reads(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.is_relative_to(SUITE.resolve()):
                raise PermissionError(f"R01 runtime scoring-side file read forbidden: {path}")
    sys.addaudithook(no_scoring_reads)
    runtime = module("r01_runtime_inputs_child", INTERNAL / "runtime_inputs.py")
    payload = runtime.load_runtime_inputs()
    assert set(payload) == {"questions", "corpus_paths"}
    assert all(set(question) == {"id", "query"} for question in payload["questions"])
    print(json.dumps({"questions": len(payload["questions"]), "corpus_paths": len(payload["corpus_paths"]), "scoring_file_reads": 0}))
