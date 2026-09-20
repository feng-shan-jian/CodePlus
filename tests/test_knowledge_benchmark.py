"""Dataset integrity and honest offline scoring; no model or Milvus dependency."""

import argparse
from hashlib import sha256
import json

import pytest

from codeplus.knowledge.benchmark import load_dataset, run, score


def dataset(tmp_path):
    text = "\r\n依据\r\n"
    (tmp_path / "source.md").write_bytes(text.encode())
    digest = sha256(text.encode()).hexdigest()
    manifest = {"id": "test", "version": "1.0.0", "protocol": {"top_k": 5},
                "documents": [{"id": "D01", "file": "source.md", "path": "source.md", "sha256": digest,
                               "text_path": "source.md", "text_sha256": digest}]}
    q = {"id": "Q01", "query": "依据是什么", "track": "single_turn", "answerable": True,
         "gold": [{"source_id": "D01", "char_start": 2, "char_end": 4, "quote": "依据"}]}
    questions = [q, {**q, "id": "Q02", "track": "followup"},
                 {**q, "id": "Q03", "track": "no_answer", "answerable": False, "gold": [], "no_answer_reason": "资料没有答案"}]
    for name, value in (("dataset.json", manifest), ("questions.json", {"questions": questions})):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    return questions


def test_integrity_checks_source_and_quote(tmp_path):
    dataset(tmp_path)
    first = load_dataset(tmp_path)[2]
    assert first == load_dataset(tmp_path)[2]
    (tmp_path / "source.md").write_text("资料被修改", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        load_dataset(tmp_path)
    questions = dataset(tmp_path)
    questions[0]["gold"][0]["quote"] = "错误"
    (tmp_path / "questions.json").write_text(json.dumps({"questions": questions}), encoding="utf-8")
    with pytest.raises(ValueError, match="quote"):
        load_dataset(tmp_path)


def test_replay_separates_tracks_and_never_connects(tmp_path, monkeypatch):
    questions = dataset(tmp_path)
    hit = {"source_id": "D01", "file": "same-name.md", "source_spans": [{"char_start": 2, "char_end": 4}]}
    records = [{"id": q["id"], "query": q["query"], "status": "ok", "elapsed_ms": 1, "hits": [hit]} for q in questions]
    records[1].update(status="error", hits=[])
    summary = score(questions, records, 5)
    assert summary["single_turn"]["fully_covered"] == 1
    assert summary["followup"]["evidence_recall"] == 0
    assert summary["followup"]["request_errors"] == 1
    assert summary["no_answer"]["fully_covered"] is None
    assert summary["no_answer"]["refusal_correctness"] == "not_evaluated"
    records[0]["hits"] = [{**hit, "source_id": "D02"}]
    assert score(questions, records, 5)["single_turn"]["fully_covered"] == 0
    source = tmp_path / "saved.json"
    report = {"dataset_sha256": load_dataset(tmp_path)[2], "records": records}
    source.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr("codeplus.knowledge.benchmark.KnowledgeService", lambda *_: pytest.fail("Offline replay connected"))
    args = argparse.Namespace(dataset=tmp_path, check=False, replay=source)
    result = run(None, args)
    assert result["status"] == "query_failures"
    report["dataset_sha256"] = "different version"
    source.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="different dataset"):
        run(None, args)


def test_live_run_rejects_a_different_corpus_before_preparing(tmp_path, monkeypatch):
    from codeplus.config import KnowledgeConfig

    dataset(tmp_path)

    class OtherCorpus:
        def __init__(self, config):
            pass

        def status(self, kb_id):
            return {"state": "READY", "documents": [{"state": "READY", "removed": False, "content_hash": "changed"}]}

        def prepare(self):
            pytest.fail("Prepared a mismatched corpus")

        def close(self):
            pass

    monkeypatch.setattr("codeplus.knowledge.benchmark.KnowledgeService", OtherCorpus)
    args = argparse.Namespace(dataset=tmp_path, check=False, replay=None, kb_id="test", managed_local=False)
    with pytest.raises(ValueError, match="exactly the dataset"):
        run(KnowledgeConfig(), args)
