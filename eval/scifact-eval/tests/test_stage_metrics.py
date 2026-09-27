"""Stage scoring must reflect the actual chunk window sent downstream."""
import pytest

from summarize_retrieval import project, window_coverage


def test_chunk_budget_is_cut_before_document_deduplication():
    records = [{"id": "q", "hits": [{"doc_id": "noise"}] * 5 + [{"doc_id": "gold"}]}]
    qrels = {"q": {"gold": 1}}
    five, _ = window_coverage(records, qrels, 5)
    ten, _ = window_coverage(records, qrels, 10)
    assert five["hit"] == five["macro_recall"] == 0
    assert five["duplicate_document_slots"] == 4
    assert ten["hit"] == ten["macro_recall"] == 1


def test_failed_question_and_multiple_relevant_documents_keep_denominators():
    records = [{"id": "one", "hits": [{"doc_id": "a"}]}, {"id": "two", "hits": []}]
    result, _ = window_coverage(records, {"one": {"a": 1, "b": 1}, "two": {"c": 1}}, 5)
    assert result["hit"] == 0.5
    assert result["macro_recall"] == 0.25
    assert result["micro_recall"] == pytest.approx(1/3)
    assert result["fully_covered_questions"] == 0


def test_stage_projection_requires_a_document_mapping():
    with pytest.raises(KeyError):
        project({"id": "q", "query": "text"}, [{"chunk_id": "foreign"}], {}, True)
    assert project({"id": "q", "query": "text"}, [], {}, False)["status"] == "error"
