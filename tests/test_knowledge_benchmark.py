"""Dataset integrity and honest offline scoring; no model or Milvus dependency."""

import argparse
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeplus.config import KnowledgeConfig
from codeplus.knowledge import __main__ as cli, benchmark
from codeplus.knowledge.benchmark import load_dataset, run, score
from codeplus.knowledge.models import fingerprint
from codeplus.knowledge.service import KnowledgeService
from test_knowledge_service import TinyEmbedding, service  # Reuse the SQLite/MemoryStore fixture.


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


@pytest.fixture
def live_benchmark(service, tmp_path, monkeypatch, request):
    dataset(tmp_path)
    if getattr(request, "param", False):
        service.profile.pop("indexing")
        service.profile_hash = fingerprint(service.profile)
    kb_id = service.create("benchmark")["id"]
    service.import_document(kb_id, tmp_path / "source.md")
    store = service.store
    store.search_results["bm25"] = [{"chunk_id": key, "distance": 12.0} for key in store.rows]
    opened = []

    def open_service(config):
        live = KnowledgeService(config)
        live.embedding = TinyEmbedding()
        live._store = store
        opened.append(live)
        return live

    monkeypatch.setattr(benchmark, "KnowledgeService", open_service)
    monkeypatch.setattr(benchmark, "version", lambda _: "test")
    return service, kb_id, opened


def test_cli_three_strategies_keep_dataset_and_config_unchanged(live_benchmark, tmp_path, monkeypatch, capsys):
    service, kb_id, opened = live_benchmark
    config = replace(service.config, enabled=False, top_k=1, retrieval_mode="dense", retrieval_candidates=17, rrf_k=9)
    original_config = asdict(config)
    monkeypatch.setattr(cli, "load_config", lambda _: SimpleNamespace(knowledge=config))
    dataset_hash = load_dataset(tmp_path)[2]
    saved_profile = json.loads((service.root / kb_id / "profile.json").read_text(encoding="utf-8"))
    reports = []
    for mode in ("dense", "bm25", "hybrid"):
        assert cli.main(["benchmark", "--dataset", str(tmp_path), "--kb-id", kb_id,
                         "--mode", mode, "--candidates", "2", "--rrf-k", "20.5"]) == 0
        result = json.loads(capsys.readouterr().out)
        reports.append(json.loads(Path(result["report"]).read_text(encoding="utf-8")))
        report = reports[-1]
        markdown = Path(result["report"]).with_suffix(".md").read_text(encoding="utf-8")
        assert f"请求检索：{mode}；候选数 N=2；RRF=20.5；Top-K=5" in markdown
        assert f"| Q01 | {mode} | 5 | {'20.5' if mode == 'hybrid' else '—'} |" in markdown
        assert report["retrieval_request"] == {"mode": mode, "candidates": 2, "rrf_k": 20.5, "top_k": 5}
        assert report["profile"] == saved_profile
        assert report["dataset_sha256"] == dataset_hash
        assert report["summary"]["single_turn"]["fully_covered"] == 1
        assert report["answer_model_called"] is False
        for record in report["records"]:
            actual = record["retrieval"]
            assert actual["requested_mode"] == actual["mode"] == mode
            assert actual["top_k"] == actual["candidates"] == 5
            assert actual["rrf_k"] == (20.5 if mode == "hybrid" else None)
            assert actual["profile_hash"] == fingerprint(saved_profile)
            assert actual["lane_counts"] == {lane: 1 if mode in (lane, "hybrid") else None for lane in ("dense", "bm25")}
            assert record["hits"][0]["score_type"] == {"dense": "cosine_similarity", "bm25": "bm25", "hybrid": "rrf"}[mode]
        assert asdict(config) == original_config
        assert opened[-1].config.enabled and opened[-1]._closed
    assert len(list((tmp_path / "runs").iterdir())) == 3
    assert len({report["kb_revision"] for report in reports}) == 1
    assert len({report["implementation_sha256"] for report in reports}) == 1
    assert load_dataset(tmp_path)[2] == dataset_hash


@pytest.mark.parametrize("live_benchmark", [False, True], indirect=True, ids=["hybrid-base", "legacy-base"])
def test_omitted_overrides_use_config_and_saved_profile(live_benchmark, tmp_path):
    service, kb_id, opened = live_benchmark
    config = replace(service.config, retrieval_candidates=8, rrf_k=7, top_k=1)
    # Old programmatic callers need not add the new Namespace attributes.
    args = argparse.Namespace(dataset=tmp_path, check=False, replay=None, kb_id=kb_id, managed_local=False)
    result = run(config, args)
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    legacy = "indexing" not in service.profile
    assert report["profile"] == service.profile
    assert report["retrieval_request"] == {"mode": "auto", "candidates": 8, "rrf_k": 7, "top_k": 5}
    assert opened[-1].config == config
    for record in report["records"]:
        assert record["retrieval"]["requested_mode"] == "auto"
        assert record["retrieval"]["mode"] == ("dense" if legacy else "hybrid")
        assert record["retrieval"]["candidates"] == (5 if legacy else 8)
        assert record["retrieval"]["rrf_k"] == (None if legacy else 7)


@pytest.mark.parametrize("option,field,value", [("mode", "retrieval_mode", "bm25"),
                                                ("candidates", "retrieval_candidates", 12), ("rrf_k", "rrf_k", 3.5)])
def test_single_override_preserves_other_config_fields(live_benchmark, tmp_path, option, field, value):
    service, kb_id, opened = live_benchmark
    config = replace(service.config, retrieval_mode="dense", retrieval_candidates=8, rrf_k=7)
    original = asdict(config)
    args = argparse.Namespace(dataset=tmp_path, check=False, replay=None, kb_id=kb_id, managed_local=False, **{option: value})
    run(config, args)
    assert asdict(opened[-1].config) == {**original, field: value}
    assert asdict(config) == original


@pytest.mark.parametrize("flag,value", [("--candidates", "0"), ("--candidates", "16385"),
                                       ("--rrf-k", "0"), ("--rrf-k", "nan"), ("--rrf-k", "inf")])
def test_cli_invalid_overrides_use_config_validation(tmp_path, monkeypatch, capsys, flag, value):
    dataset(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _: SimpleNamespace(knowledge=KnowledgeConfig()))
    monkeypatch.setattr(benchmark, "KnowledgeService", lambda *_: pytest.fail("Invalid config opened the service"))
    assert cli.main(["benchmark", "--dataset", str(tmp_path), "--kb-id", "test", flag, value]) == 1
    assert "knowledge." in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("offline", ["--check", "--replay"])
@pytest.mark.parametrize("override", [[], ["--mode", "dense"], ["--candidates", "50"], ["--rrf-k", "60"]])
def test_cli_offline_modes_never_load_config_or_service(tmp_path, monkeypatch, capsys, offline, override):
    questions = dataset(tmp_path)
    source = tmp_path / "old-report.json"
    old_report = {"dataset_sha256": load_dataset(tmp_path)[2],
                  "records": [{"id": q["id"], "query": q["query"], "status": "ok", "elapsed_ms": 1, "hits": []}
                              for q in questions]}
    source.write_text(json.dumps(old_report), encoding="utf-8")
    original = source.read_bytes()
    monkeypatch.setattr(cli, "load_config", lambda *_: pytest.fail("Offline mode loaded user configuration"))
    monkeypatch.setattr(benchmark, "KnowledgeService", lambda *_: pytest.fail("Offline mode opened the service"))
    args = ["benchmark", "--dataset", str(tmp_path), offline]
    if offline == "--replay":
        args.append(str(source))
    assert cli.main(args + override) == (1 if override else 0)
    captured = capsys.readouterr()
    if override:
        assert "require --kb-id" in captured.err
        assert not (tmp_path / "runs").exists()
    elif offline == "--replay":
        replay_path = Path(json.loads(captured.out)["report"])
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        assert "retrieval_request" not in replay
        assert all("retrieval" not in record for record in replay["records"])
        assert "| Q01 | 未记录 | 未记录 | 未记录 |" in replay_path.with_suffix(".md").read_text(encoding="utf-8")
    assert source.read_bytes() == original


def test_query_failure_is_scored_and_replay_keeps_retrieval_metadata(live_benchmark, tmp_path, monkeypatch):
    service, kb_id, _ = live_benchmark
    search = service.store.search_bm25

    def fail_once(*args):
        monkeypatch.setattr(service.store, "search_bm25", search)
        raise RuntimeError("BM25 unavailable")

    monkeypatch.setattr(service.store, "search_bm25", fail_once)
    args = argparse.Namespace(dataset=tmp_path, check=False, replay=None, kb_id=kb_id, managed_local=False, mode="hybrid")
    result = run(service.config, args)
    report_path = Path(result["report"])
    original = report_path.read_bytes()
    report = json.loads(original)
    assert result["status"] == "query_failures"
    assert report["summary"]["single_turn"]["request_errors"] == 1
    assert report["summary"]["single_turn"]["evidence_recall"] == 0
    assert report["records"][0]["error"] == "bm25 retrieval failed: BM25 unavailable"
    assert report["records"][0]["hits"] == []
    assert "retrieval" not in report["records"][0]
    monkeypatch.setattr(benchmark, "KnowledgeService", lambda *_: pytest.fail("Replay connected"))
    replay_result = run(None, argparse.Namespace(dataset=tmp_path, check=False, replay=report_path))
    replay = json.loads(Path(replay_result["report"]).read_text(encoding="utf-8"))
    for field in ("records", "retrieval_request", "profile", "implementation_sha256", "summary"):
        assert replay[field] == report[field]
    assert report_path.read_bytes() == original


def test_revision_change_discards_report(live_benchmark, tmp_path, monkeypatch):
    service, kb_id, opened = live_benchmark
    search = service.store.search_bm25

    def change_revision(*args):
        with service.metadata.connect() as db:
            db.execute("UPDATE knowledge_bases SET revision=revision+1 WHERE id=?", (kb_id,))
        return search(*args)

    monkeypatch.setattr(service.store, "search_bm25", change_revision)
    args = argparse.Namespace(dataset=tmp_path, check=False, replay=None, kb_id=kb_id, managed_local=False)
    with pytest.raises(ValueError, match="changed during the benchmark"):
        run(service.config, args)
    assert opened[-1]._closed
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("module", ["knowledge/retrieval.py", "knowledge/models.py", "knowledge/__main__.py",
                                    "config.py", "validator.py"])
def test_implementation_fingerprint_includes_retrieval_configuration(live_benchmark, tmp_path, monkeypatch, module):
    service, kb_id, _ = live_benchmark
    args = argparse.Namespace(dataset=tmp_path, check=False, replay=None, kb_id=kb_id, managed_local=False)
    before = run(service.config, args)
    target = Path(benchmark.__file__).parents[1] / module
    read_bytes = Path.read_bytes

    def changed_bytes(path):
        data = read_bytes(path)
        return data + b"\n# changed\n" if path.resolve() == target.resolve() else data

    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    after = run(service.config, args)
    reports = [json.loads(Path(result["report"]).read_text(encoding="utf-8")) for result in (before, after)]
    assert reports[0]["implementation_sha256"] != reports[1]["implementation_sha256"]
    assert reports[0]["dataset_sha256"] == reports[1]["dataset_sha256"]
