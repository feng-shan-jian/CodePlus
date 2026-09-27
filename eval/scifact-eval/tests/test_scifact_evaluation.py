"""Permanent regression tests for dataset isolation and scoring semantics."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import runtime_inputs as runtime
import scifact_suite as suite


def sample():
    inputs = {"dataset_id": suite.DATASET_ID, "dataset_fingerprint": "frozen",
        "namespace": suite.NAMESPACE, "corpus_fingerprint": "corpus", "split": "train",
        "documents": [{"doc_id": "10"}, {"doc_id": "20"}],
        "questions": [{"id": "1", "query": "claim one"}, {"id": "2", "query": "claim two"}]}
    report = {k: v for k, v in inputs.items() if k not in {"documents", "questions"}}
    report.update(protocol={"unit": "document", "top_k": 10}, records=[
        {**inputs["questions"][0], "status": "ok", "hits": [{"doc_id": "10", "score": 0.9}]},
        {**inputs["questions"][1], "status": "error", "hits": []}])
    return inputs, report


def test_official_data_integrity_and_splits():
    checked = suite.check()
    assert checked["documents"] == 5183
    assert checked["splits"] == {"train": {"queries": 809, "qrels": 919}, "test": {"queries": 300, "qrels": 339},
                                  "development": {"queries": 807, "qrels": 917}}
    train, test = (runtime.load_runtime_inputs(s) for s in ("train", "test"))
    assert not {q["id"] for q in train["questions"]} & {q["id"] for q in test["questions"]}
    assert train["documents"] == test["documents"]
    assert all(set(q) == {"id", "query"} for s in (train, test) for q in s["questions"])
    development = runtime.load_runtime_inputs("development")
    assert {q["id"] for q in train["questions"]} - {q["id"] for q in development["questions"]} == {"871", "1291"}
    assert not ({suite.normalized_query(q["query"]) for q in development["questions"]}
                & {suite.normalized_query(q["query"]) for q in test["questions"]})


@pytest.mark.parametrize("key,value", [
    ("dataset_id", "multihop"), ("dataset_fingerprint", "old"),
    ("namespace", "r10_acceptance"), ("corpus_fingerprint", "mixed"), ("split", "test")])
def test_cross_suite_or_split_result_is_rejected(key, value):
    inputs, report = sample()
    report[key] = value
    with pytest.raises(ValueError, match="identity"):
        suite.validate_report(report, inputs)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "changed_query", "foreign_doc", "nan", "out_of_order", "error_hits"])
def test_incomplete_or_invalid_results_do_not_get_scored(mutation):
    inputs, report = sample()
    if mutation == "missing":
        report["records"].pop()
    elif mutation == "duplicate":
        report["records"][1] = deepcopy(report["records"][0])
    elif mutation == "changed_query":
        report["records"][0]["query"] = "another query"
    elif mutation == "foreign_doc":
        report["records"][0]["hits"][0]["doc_id"] = "D0001"
    elif mutation == "nan":
        report["records"][0]["hits"][0]["score"] = float("nan")
    elif mutation == "out_of_order":
        report["records"][0]["hits"].append({"doc_id": "20", "score": 1})
    else:
        report["records"][0]["status"] = "error"
    with pytest.raises(ValueError):
        suite.validate_report(report, inputs)


def test_multiple_chunks_cannot_inflate_document_recall():
    inputs, report = sample()
    report["protocol"]["unit"] = "chunk"
    report["records"][0]["hits"] = [
        {"doc_id": "10", "score": 0.9}, {"doc_id": "10", "score": 0.8}, {"doc_id": "20", "score": 0.7}]
    ranked, errors, duplicates = suite.validate_report(report, inputs)
    assert list(ranked["1"]) == ["10", "20"]
    assert duplicates == errors == 1


def test_failed_queries_remain_in_denominator_and_precision_is_not_hit_rate():
    metrics, per_query = suite.measure({"1": {"10": 1}, "2": {"20": 1}}, {"1": {"10": 1.0}, "2": {}})
    assert metrics["Success@5"] == metrics["R@5"] == metrics["RR@10"] == metrics["nDCG@10"] == 0.5
    assert metrics["P@5"] == pytest.approx(0.1)
    assert all(v == 0 for v in per_query["2"].values())


def test_all_failed_queries_have_zero_metrics():
    aggregate, _ = suite.measure({"1": {"10": 1}}, {"1": {}})
    assert all(value == 0 for value in aggregate.values())


def test_storage_cannot_reuse_multihop_or_unbound_data(tmp_path):
    inputs = dict(runtime.BINDING)
    with pytest.raises(ValueError, match="namespace"):
        runtime.bind_storage(inputs, "baseline", namespace="r10_acceptance", root=tmp_path)
    assert not (tmp_path / ".state").exists()
    data = runtime.bind_storage(inputs, "baseline", root=tmp_path)
    assert Path(data["data_dir"]).is_relative_to(tmp_path / ".state")
    assert runtime.bind_storage(inputs, "baseline", root=tmp_path) == data
    inputs["corpus_fingerprint"] = "other corpus"
    with pytest.raises(ValueError, match="not bound"):
        runtime.bind_storage(inputs, "baseline", root=tmp_path)
    inputs = dict(runtime.BINDING)
    foreign = tmp_path / ".state/foreign"
    foreign.mkdir()
    with pytest.raises(ValueError, match="not bound"):
        runtime.bind_storage(inputs, "foreign", root=tmp_path)


def test_runtime_rejects_a_foreign_corpus_file(tmp_path):
    (tmp_path / "runtime/corpus").mkdir(parents=True)
    (tmp_path / "runtime/train.json").write_bytes((suite.ROOT / "runtime/train.json").read_bytes())
    (tmp_path / "runtime/corpus/D0001.md").write_text("foreign", encoding="utf-8")
    with pytest.raises(ValueError, match="Mixed"):
        runtime.load_runtime_inputs("train", tmp_path)


def test_read_guard_blocks_qrels_other_split_and_multihop():
    code = """
from pathlib import Path
from runtime_inputs import ROOT, protect_runtime_reads
protect_runtime_reads('train')
for path in [ROOT/'upstream/qrels/train.tsv', ROOT/'runtime/test.json', ROOT.parent/'RAG-eval/questions.json']:
    try:
        path.read_bytes()
    except PermissionError:
        continue
    raise AssertionError('forbidden read was allowed: ' + str(path))
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=suite.ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_cli_scores_full_failed_split_and_preserves_existing_run(tmp_path, monkeypatch, capsys):
    inputs = suite.read_json(suite.ROOT / "runtime/test.json")
    report = {k: inputs[k] for k in (*runtime.BINDING, "split")}
    report.update(protocol={"unit": "document", "top_k": 10},
        records=[{**q, "status": "error", "hits": []} for q in inputs["questions"]])
    input_file = tmp_path / "retrieval.json"
    input_file.write_bytes(suite.encode(report))
    # score() retains its real dataset root; only the CLI output is redirected.
    monkeypatch.setattr(suite, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["scifact_suite.py", "score", "--input", str(input_file), "--run-name", "failed"])
    assert suite.main() == 1
    scored = suite.read_json(tmp_path / "runs/test/failed/metrics.json")
    assert scored["questions"] == scored["request_errors"] == 300
    assert all(value == 0 for value in scored["metrics"].values())
    assert scored["input_sha256"] == suite.digest(input_file.read_bytes())
    with pytest.raises(ValueError, match="already exists"):
        suite.main()
    assert suite.read_json(tmp_path / "runs/test/failed/metrics.json") == scored
    capsys.readouterr()
