"""Official scorer compatibility and strict evaluation denominators, without models."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil

import pytest

SUITE = Path(__file__).resolve().parents[1] / "eval" / "RAG-eval"
pytestmark = pytest.mark.skipif(not (SUITE / "score.py").exists(), reason="Evaluation assets are excluded from source distributions")


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.syspath_prepend(str(SUITE))
    spec = importlib.util.spec_from_file_location("multihop_score", SUITE / "score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cases():
    return [
        {"id": "A", "query": "First?", "track": "inference_query", "gold": [{"quote": "alpha beta"}], "reference_answer": "red orange"},
        {"id": "B", "query": "Second?", "track": "temporal_query", "gold": [{"quote": "gamma"}], "reference_answer": "green"},
        {"id": "C", "query": "Unknown?", "track": "null_query", "gold": [], "reference_answer": "Insufficient information."},
    ]


def report():
    rows = [{"id": q["id"], "query": q["query"], "status": "ok", "hits": []} for q in cases()]
    rows[0]["hits"] = [{"text": "unrelated"}, {"text": "alpha\nbeta"}]
    rows[1].update(status="error", hits=[{"text": "gamma"}])
    return {"dataset_sha256": "expected", "protocol": {"top_k": 10}, "records": rows}


@pytest.mark.parametrize("tier,count,scored", [("lite", 50, 44), ("medium", 200, 177), ("full", 2556, 2255)])
def test_frozen_release_and_every_evidence_range(adapter, tier, count, scored):
    result = adapter.check(tier)
    assert result["questions"] == count
    assert result["documents"] == 609
    assert result["retrieval_scored_questions"] == scored
    assert result["exact_evidence_ranges"] == 6084
    assert result["model_evaluation"] == "not_executed"
    assert (result["dataset_sha256"] == result["source_dataset_sha256"]) == (tier == "full")


def test_upstream_code_is_verified_before_execution(adapter, tmp_path):
    shutil.copyfile(SUITE / "source-lock.json", tmp_path / "source-lock.json")
    shutil.copytree(SUITE / "upstream", tmp_path / "upstream")
    (tmp_path / "upstream" / "retrieval_evaluate.py").write_text("raise RuntimeError('must not execute')", encoding="utf-8")
    with pytest.raises(ValueError, match="Upstream checksum mismatch"):
        adapter.scorer("retrieval", tmp_path)


def test_upstream_retrieval_keeps_errors_and_excludes_null_only(adapter):
    metric = adapter.scorer("retrieval")["calculate_metrics"]
    result = adapter.score_retrieval(report(), cases(), metric, "expected")
    assert (result["selected_questions"], result["scored_questions"], result["excluded_null_queries"]) == (3, 2, 1)
    assert result["request_errors"] == 1
    assert result["overall"]["metrics"] == {"Hits@10": 0.5, "Hits@4": 0.5, "MAP@10": 0.25, "MRR@10": 0.25}
    assert result["by_question_type"]["temporal_query"]["metrics"]["Hits@10"] == 0
    assert result["answer_correctness"] == result["refusal_correctness"] == "not_evaluated"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reordered", "changed_query", "wrong_tier", "wrong_top_k", "too_many_hits", "unknown_status"])
def test_retrieval_identity_and_protocol_cannot_drift(adapter, mutation):
    value = report()
    if mutation == "missing":
        value["records"].pop()
    elif mutation == "duplicate":
        value["records"][1] = deepcopy(value["records"][0])
    elif mutation == "reordered":
        value["records"].reverse()
    elif mutation == "changed_query":
        value["records"][0]["query"] = "changed"
    elif mutation == "wrong_tier":
        value["dataset_sha256"] = "another tier"
    elif mutation == "wrong_top_k":
        value["protocol"]["top_k"] = 5
    elif mutation == "too_many_hits":
        value["records"][0]["hits"] *= 6
    else:
        value["records"][0]["status"] = "skipped"
    with pytest.raises(ValueError):
        adapter.score_retrieval(value, cases(), lambda *_: pytest.fail("Scored invalid input"), "expected")


def answers():
    return [{"id": q["id"], "query": q["query"], "model_answer": q["reference_answer"]} for q in cases()]


def test_qa_uses_official_overlap_not_semantic_correctness(adapter):
    functions = adapter.scorer("answers")
    records = answers()
    records[0]["model_answer"] = 'The answer to the question is "orange"'
    records[0]["gold_answer"] = "override must be ignored"
    records[0]["question_type"] = "override must be ignored"
    records[1]["status"] = "error"
    result = adapter.score_answers(records, cases(), functions["calculate_metrics"], functions["extract_answer"])
    assert result["overall"]["upstream_word_overlap_accuracy"] == pytest.approx(2 / 3)
    assert result["by_question_type"]["inference_query"]["upstream_word_overlap_accuracy"] == 1
    assert result["by_question_type"]["null_query"]["questions"] == 1
    assert result["scored_questions"] == 3
    assert result["request_errors"] == 1
    assert "not semantic correctness" in result["scoring_limit"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "changed_query", "wrong_id", "absent_answer", "unknown_status"])
def test_answers_must_cover_exactly_the_selected_questions(adapter, mutation):
    records = answers()
    if mutation == "missing":
        records.pop()
    elif mutation == "duplicate":
        records.append(deepcopy(records[0]))
    elif mutation == "extra":
        records.append({"query": "extra", "model_answer": "extra"})
    elif mutation == "changed_query":
        records[0]["query"] = "changed"
    elif mutation == "wrong_id":
        records[0]["id"] = "B"
    elif mutation == "absent_answer":
        del records[0]["model_answer"]
    else:
        records[0]["status"] = "skipped"
    with pytest.raises(ValueError):
        adapter.score_answers(records, cases(), lambda *_: pytest.fail("Scored invalid input"), lambda answer: answer)


def test_answer_order_does_not_change_score(adapter):
    functions = adapter.scorer("answers")
    records = list(reversed(answers()))
    result = adapter.score_answers(records, cases(), functions["calculate_metrics"], functions["extract_answer"])
    assert result["overall"]["upstream_word_overlap_accuracy"] == 1
