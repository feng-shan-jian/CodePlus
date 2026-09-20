"""Small metric oracles and opt-in frozen replay against the real database."""

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from codeplus.knowledge.evaluate import ann_recall, evidence_recall, fuse, summarize
from codeplus.knowledge.retrieval import rrf


def test_original_range_union_and_fixed_denominators():
    def hit(file, left, right):
        return {"file": file, "source_spans": [{"char_start": left, "char_end": right}]}
    gold = [{"file": "a.md", "char_start": 10, "char_end": 30}]
    partial = [hit("a.md", 10, 20), hit("a.md", 21, 35)]
    assert evidence_recall(gold, partial, 2)["recall"] == 0
    assert evidence_recall(gold, partial + [hit("b.md", 10, 30)], 3)["recall"] == 0
    assert evidence_recall(gold, partial + [hit("a.md", 18, 25)], 3)["recall"] == 1
    assert evidence_recall(gold, partial + [hit("a.md", 18, 25)], 2)["recall"] == 0
    assert evidence_recall([], [], 3)["recall"] is None
    assert evidence_recall(gold, [], 3)["recall"] == 0
    assert ann_recall([{"chunk_id": "a"}], [{"chunk_id": "a"}], 3)["recall"] == 1
    assert ann_recall([], [], 3)["recall"] is None
    questions = [{"gold": gold}, {"gold": gold}, {"gold": []}, {"gold": gold}]
    results = [
        {"status": "ok", "hits": [hit("a.md", 10, 30)], "elapsed_ms": 1},
        {"status": "error", "hits": [], "elapsed_ms": 20},
        {"status": "no_hits", "hits": [], "elapsed_ms": 1},
        {"status": "no_hits", "hits": [], "elapsed_ms": 1},
    ]
    for q, result in zip(questions, results):
        result["evidence"] = evidence_recall(q["gold"], result["hits"], 3)
    summary = summarize(questions, [{"lanes": {"dense": r}} for r in results], "dense", 3)
    assert summary["evidence_recall"] == 1 / 3
    assert summary["successful_evidence_recall"] == 1 / 2
    assert (summary["gold_ranges"], summary["request_errors"], summary["no_hits"], summary["no_gold_questions"]) == (3, 1, 2, 1)


def test_content_coverage_ignores_only_whitespace():
    gold = [{"file": "a", "char_start": 0, "char_end": 4, "quote": "甲\n\n乙"}]
    hits = [{"file": "a", "source_spans": [{"char_start": 0, "char_end": 1}, {"char_start": 3, "char_end": 4}]}]
    assert evidence_recall(gold, hits, 5)["recall"] == 0
    assert evidence_recall(gold, hits, 5, ignore_whitespace=True)["recall"] == 1
    assert evidence_recall([{**gold[0], "quote": "甲丙丁乙"}], hits, 5, ignore_whitespace=True)["recall"] == 0
    assert evidence_recall(gold, [{**hits[0], "file": "b"}], 5, ignore_whitespace=True)["recall"] == 0


def test_rrf_hand_calculation_and_empty_versus_failed_lane():
    dense = [{"chunk_id": "a", "score": 0.9}, {"chunk_id": "b", "score": 0.7}]
    bm25 = [{"chunk_id": "b", "score": 12.0}, {"chunk_id": "c", "score": 8.0}]
    result = rrf(dense, bm25, 60)
    assert [r["chunk_id"] for r in result] == ["b", "a", "c"]
    assert result[0]["score"] == pytest.approx(1 / 62 + 1 / 61)
    assert (result[0]["dense_rank"], result[0]["bm25_rank"], result[0]["dense_score"], result[0]["bm25_score"]) == (2, 1, 0.7, 12.0)
    left = {"status": "ok", "hits": dense, "elapsed_ms": 1}
    empty = {"status": "no_hits", "hits": [], "elapsed_ms": 2}
    assert fuse(left, empty, 60)["status"] == "ok"
    assert fuse(left, empty, 60)["lanes"]["bm25"] == "no_hits"
    assert fuse(empty, empty, 60)["status"] == "no_hits"
    failure = fuse(left, {**empty, "status": "error"}, 60)
    assert failure["status"] == "unavailable" and failure["hits"] == []
    assert [r["chunk_id"] for r in rrf(dense[:1], bm25[:1], 60)] == ["a", "b"]


@pytest.mark.skipif(not os.getenv("CODEPLUS_TEST_EVAL_FROZEN"), reason="real Qwen/Milvus frozen replay not requested")
def test_real_replay_preserves_daily_binding_without_loading_model(tmp_path, monkeypatch):
    from codeplus.config import KnowledgeConfig
    from codeplus.knowledge.embedding import LocalEmbedding
    from codeplus.knowledge.evaluate import _retrieve, _rows, run
    from codeplus.knowledge.models import fingerprint
    from codeplus.knowledge.service import KnowledgeService

    frozen = Path(os.environ["CODEPLUS_TEST_EVAL_FROZEN"]).resolve()
    expected = json.loads(frozen.read_text(encoding="utf-8"))["data"]
    config = KnowledgeConfig(enabled=True, data_dir=str(tmp_path / "daily"),
                             milvus_uri=os.getenv("CODEPLUS_TEST_MILVUS_URI", "http://127.0.0.1:19530"))
    daily = KnowledgeService(config)
    # This control deliberately exercises the known pre-BM25 daily schema.
    daily.profile.pop("indexing")
    daily.profile_hash = fingerprint(daily.profile)
    name = None
    try:
        kb = daily.create("daily-bound-control")
        name = kb["collection_name"]
        document = tmp_path / "daily.md"
        document.write_text("# 日常库对照\n\n日常库密钥轮换周期为九十天。\n", encoding="utf-8")
        daily.import_document(kb["id"], document)
        before = daily.status(kb["id"])
        rows_before = fingerprint(_rows(daily.store, name))
        monkeypatch.setattr(LocalEmbedding, "_load", lambda self: pytest.fail("Replay attempted model loading"))
        monkeypatch.chdir(tmp_path)
        args = argparse.Namespace(replay=frozen, fixtures=tmp_path / "does-not-exist", mode="all", top_k=3,
                                  candidates=6, rrf_k=60, m=16, ef_construction=128, ef=[16, 64],
                                  warmup=1, repeats=1)
        result = run(replace(config, embedding_model="unavailable", embedding_revision="changed", embedding_dimension=17), args)
        report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        assert result["status"] == "ok", result
        assert report["profile"] == expected["profile"]
        assert all(c["removed"] for c in report["cleanup"])
        assert daily.status(kb["id"]) == before
        assert fingerprint(_rows(daily.store, name)) == rows_before
        assert daily.store.client.describe_index(name, "dense")["index_type"] == "FLAT"
        # A daily dense schema has no sparse field. Its real SDK error must not become empty-success hybrid.
        failed = _retrieve(daily.store, name, "密钥", expected["query_vectors"][0], expected["sources"], 3, None, "bm25")
        assert failed["status"] == "error" and failed["error"]["type"]
        assert fuse({"status": "no_hits", "hits": [], "elapsed_ms": 0}, failed, 60)["status"] == "unavailable"
        assert report["analyzer"]["tokens"][0]  # Server analyzer, not a local tokenizer.
        assert all(v["readback_sha256"] == expected["hashes"]["rows"] for v in report["collections"].values())
        for record in report["records"]:
            if record["scope_doc_ids"] is not None:
                assert all(h["doc_id"] in record["scope_doc_ids"] for lane in record["lanes"].values() for h in lane["hits"])
        print(json.dumps({"status": result["status"], "daily_unchanged": daily.status(kb["id"]) == before,
                          "daily_rows": len(_rows(daily.store, name)), "frozen_sha256": report["frozen_sha256"],
                          "real_missing_sparse_status": failed["status"]}))
    finally:
        if name:
            daily.store.client.drop_collection(name)
            assert not daily.store.client.has_collection(name)
        daily.close()
