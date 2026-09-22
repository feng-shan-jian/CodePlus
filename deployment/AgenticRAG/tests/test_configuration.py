"""Configuration identity, isolation and capability boundaries (no GPU calls)."""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from agentic_rag.capabilities import (
    EmbeddingResponse, EmbeddingResult, ModelInput, ModelTimings, RequestContext,
    RerankResponse, RerankScore, require_provider, validate_input_batch, validate_response,
)
from agentic_rag.config import (
    KnowledgeConfig, ProcessingSnapshot, RunOverride, assemble_configuration,
    document_encoding_identity, index_identity, resolve_run,
)
from agentic_rag.domain import ErrorCode, RagError, Run, RunStatus
from agentic_rag.profiles import EmbeddingProfile, InputLimits, RerankProfile


def example_config():
    # Deliberately explicit *test* values, not product quality defaults.
    return {
        "storage": {"data_dir": "C:/r05-example/data", "milvus_uri": "http://127.0.0.1:19530", "namespace": "r05_test"},
        "processing": {
            "parser": {"implementation": "markdown_txt", "version": "test-1", "normalization_version": "test-1"},
            "chunker": {"implementation": "structure_sentence_token", "version": "test-1", "max_tokens": 400, "overlap_tokens": 40},
            "index": {"schema_version_name": "test-1", "nlist": 1, "bm25_k1": 1.2, "bm25_b": 0.75},
        },
        "models": {"embedding": "embed", "reranker": "rank"},
        "model_profiles": [
            {"name": "embed", "capability": "embedding"}, {"name": "rank", "capability": "rerank"},
        ],
        "retrieval": {"route": "dense", "rerank": False, "dense_candidates": 20, "bm25_candidates": 20,
                      "rerank_candidates": 20, "rrf_k": 40, "nprobe": 1, "context_chunks": 5, "context_tokens": 3000},
        "budgets": {
            "qa": {"searches": 3, "opens": 3, "total_tokens": 12000, "duration_ms": 120000,
                   "finish_reserve_tokens": 3000, "finish_reserve_ms": 20000},
            "report": {"searches": 7, "opens": 9, "total_tokens": 36000, "duration_ms": 360000,
                       "finish_reserve_tokens": 6000, "finish_reserve_ms": 40000},
        },
    }


def parse(data):
    return KnowledgeConfig.model_validate_json(json.dumps(data))


def test_precedence_copy_and_run_override_are_feature_scoped():
    raw = example_config()
    configured = {"retrieval": {"mode": "fixed", "context_tokens": 2000}}
    explicit = {"retrieval": {"mode": "auto"}}
    resolved = assemble_configuration(defaults=raw, configured=configured, explicit=explicit)
    origins = {item.path: item.source for item in resolved.origins}
    assert origins["retrieval.mode"] == "explicit"
    assert origins["retrieval.context_tokens"] == "configured"
    assert origins["budgets.qa.searches"] == "defaults"
    config = resolved.knowledge
    before = config.model_dump_json()
    qa = resolve_run(config, "qa", RunOverride(mode="fixed"))
    report = resolve_run(config, "report")
    assert qa.retrieval.mode == "fixed" and report.retrieval.mode == "auto"
    assert qa.budget.searches > 1 and report.budget.searches > 1
    assert qa.budget != report.budget
    raw["model_profiles"][0]["name"] = "changed"
    configured["retrieval"]["context_tokens"] = 1
    assert config.model_dump_json() == before
    with pytest.raises(ValidationError):
        RunOverride(mode="auto", permission_mode="full")
    with pytest.raises(ValidationError):
        RunOverride(top_k=2)
    with pytest.raises(ValueError):
        resolve_run(config, "chat")


def test_origin_scalar_to_mapping_and_schema_default_distinction():
    raw = example_config()
    storage = raw.pop("storage")
    raw["storage"] = None
    result = assemble_configuration(defaults=raw, configured={"storage": storage})
    origins = {row.path: row.source for row in result.origins}
    assert "storage" not in origins
    assert origins["storage.data_dir"] == "configured"
    assert "retrieval.mode" not in origins  # schema default, documented separately
    assert result.knowledge.retrieval.mode == "auto"


def test_arrays_replace_atomically_and_unknown_profile_rejected():
    with pytest.raises(ValidationError, match="unknown model profile"):
        assemble_configuration(defaults=example_config(), explicit={"model_profiles": [{"name": "embed", "capability": "embedding"}]})


@pytest.mark.parametrize("path,value", [
    (("schema_version",), 2), (("schema_version",), True),
    (("mode",), "auto"), (("api_key",), "secret"),
    (("storage", "data_dir"), "relative/data"), (("storage", "data_dir"), "//server/share"),
    (("storage", "milvus_uri"), "https://user:secret@example.test"),
    (("storage", "milvus_uri"), "https://example.test?token=secret"),
    (("storage", "credential_ref"), "Bearer secret"),
    (("models", "embedding"), "rank"), (("models", "reranker"), "embed"),
    (("models", "embedding"), "missing"), (("retrieval", "mode"), "unrestricted"),
    (("retrieval", "dense_candidates"), "20"), (("retrieval", "dense_candidates"), True),
    (("retrieval", "context_chunks"), 21), (("retrieval", "nprobe"), 2),
    (("budgets", "qa", "finish_reserve_tokens"), 12000),
    (("processing", "chunker", "max_tokens"), 2049),
    (("processing", "chunker", "overlap_tokens"), 400),
    (("processing", "index", "bm25_b"), 1.1),
])
def test_invalid_configuration_fails(path, value):
    data = example_config()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        parse(data)


@pytest.mark.parametrize("patch", [
    {"backend": "arbitrary_url"}, {"runtime": {"device": "cpu"}},
    {"runtime": {"dtype": "float32"}}, {"revision": "main"},
    {"revision": "0" * 40}, {"dimension": 768}, {"dimension": 1024.0},
    {"limits": {"max_batch_size": 5}}, {"limits": {"max_input_tokens": 2049}},
    {"limits": {"max_padded_tokens": 100}}, {"runtime": {"trust_remote_code": True}},
    {"tokenizer": {"model": "wrong", "revision": "0" * 40, "tokenizer_sha256": "0" * 64}},
    {"document_with_title": "{title}{text}"}, {"api_key": "secret"},
])
def test_unverified_model_combinations_rejected(patch):
    with pytest.raises(ValidationError):
        EmbeddingProfile.model_validate_json(json.dumps({"name": "embed", **patch}))


def test_locked_profiles_match_r03_assets_and_templates():
    package = Path(__file__).resolve().parents[1]
    lock = json.loads((package / "probes/models/models.lock.json").read_text(encoding="utf-8-sig"))
    for capability, profile in (("embedding", EmbeddingProfile(name="e")), ("reranker", RerankProfile(name="r"))):
        model = lock["models"][capability]
        assert (profile.model, profile.revision) == (model["model_id"], model["revision"])
        for attribute, filename in (("tokenizer_sha256", "tokenizer.json"), ("config_sha256", "tokenizer_config.json"),
                                    ("vocab_sha256", "vocab.json"), ("merges_sha256", "merges.txt")):
            assert getattr(profile.tokenizer, attribute) == model["files"][filename]["sha256"]
    # AST reads the probe's literal templates without importing its GPU code.
    import ast
    tree = ast.parse((package / "probes/models/probe.py").read_text(encoding="utf-8-sig"))
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id in ("INSTRUCTION", "PREFIX", "SUFFIX")}
    rank = RerankProfile(name="r")
    assert (rank.instruction, rank.prefix, rank.suffix) == (constants["INSTRUCTION"], constants["PREFIX"], constants["SUFFIX"])


def test_snapshot_roundtrip_deep_immutability_and_tamper_detection():
    raw = example_config()
    config = parse(raw)
    snapshot = ProcessingSnapshot.capture(uuid4(), config)
    frozen = snapshot.model_dump_json()
    raw["processing"]["index"]["bm25_k1"] = 2.0
    raw["model_profiles"].append({"name": "new", "capability": "embedding"})
    assert snapshot.model_dump_json() == frozen
    assert ProcessingSnapshot.model_validate_json(frozen) == snapshot
    with pytest.raises(ValidationError):
        snapshot.resolved_config.model_profiles[0].runtime.device = "cpu"
    with pytest.raises(ValidationError):
        snapshot.model_copy(update={"config_fingerprint": "0" * 64})
    tampered = json.loads(frozen)
    tampered["resolved_config"]["budgets"]["qa"]["searches"] += 1
    with pytest.raises(ValidationError, match="fingerprint mismatch"):
        ProcessingSnapshot.model_validate_json(json.dumps(tampered))
    assert "api_key" not in frozen and "password" not in frozen


@pytest.mark.parametrize("path,value,encoding_changed,index_changed", [
    (("budgets", "qa", "searches"), 4, False, False),
    (("budgets", "report", "opens"), 10, False, False),
    (("retrieval", "mode"), "fixed", False, False),
    (("retrieval", "rerank"), True, False, False),
    (("model_profiles", 1, "instruction"), "Rank only exact answers", False, False),
    (("processing", "parser", "version"), "test-2", True, True),
    (("processing", "chunker", "max_tokens"), 401, True, True),
    (("model_profiles", 0, "instruction"), "Find exact answers", True, True),
    (("processing", "index", "bm25_k1"), 1.5, False, True),
])
def test_encoding_and_index_identity_boundaries(path, value, encoding_changed, index_changed):
    raw = example_config()
    before = parse(raw)
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    after = parse(raw)
    assert (document_encoding_identity(before) != document_encoding_identity(after)) == encoding_changed
    assert (index_identity(before) != index_identity(after)) == index_changed
    assert ProcessingSnapshot.capture(uuid4(), before).config_fingerprint != ProcessingSnapshot.capture(uuid4(), after).config_fingerprint


def test_profile_name_is_not_model_identity_and_order_canonicalization():
    first = example_config()
    second = copy.deepcopy(first)
    second["model_profiles"][0]["name"] = "renamed"
    second["models"]["embedding"] = "renamed"
    assert document_encoding_identity(parse(first)) == document_encoding_identity(parse(second))
    reordered = dict(reversed(list(first.items())))
    assert index_identity(parse(first)) == index_identity(parse(reordered))


def test_run_record_validates_frozen_config_and_terminal_state():
    config = resolve_run(parse(example_config()), "qa")
    run = Run(run_id=uuid4(), kb_id=uuid4(), revision_id=uuid4(), resolved_config=config, resolved_config_hash=config.identity)
    assert Run.model_validate_json(run.model_dump_json()) == run
    with pytest.raises(ValidationError):
        run.model_copy(update={"status": RunStatus.COMPLETED})
    with pytest.raises(ValidationError):
        run.model_copy(update={"resolved_config_hash": "0" * 64})
    assert run.model_copy(update={"status": RunStatus.COMPLETED, "stop_reason": "finished"}).status == RunStatus.COMPLETED
    for reason in ("token_budget", "time_budget", "search_limit", "open_limit", "iteration_limit"):
        assert run.model_copy(update={"status": RunStatus.INCOMPLETE, "stop_reason": reason}).stop_reason == reason


def test_monotonic_request_deadline_and_diagnostic_stage():
    deadline = time.monotonic_ns() + 1000000000
    request = RequestContext(request_id=uuid4(), owner_id=uuid4(), purpose="report", deadline_monotonic_ns=deadline)
    assert request.deadline_at is None
    assert RequestContext.model_validate_json(request.model_dump_json()).deadline_monotonic_ns == deadline
    with pytest.raises(ValidationError):
        request.model_copy(update={"deadline_monotonic_ns": 0})
    with pytest.raises(ValidationError):
        request.model_copy(update={"deadline_at": datetime.now()})
    exc = RagError(ErrorCode.DEADLINE_EXCEEDED, "request expired", stage="model_queue",
                   request_id=request.request_id, call_id="tool-call-1")
    assert exc.error.stage == "model_queue"
    assert exc.error.request_id == request.request_id and exc.error.call_id == "tool-call-1"


def test_duplicate_profiles_and_missing_selected_reranker_fail():
    raw = example_config()
    raw["model_profiles"].append(dict(raw["model_profiles"][0]))
    with pytest.raises(ValidationError, match="unique"):
        parse(raw)
    raw = example_config()
    raw["models"]["reranker"] = None
    raw["retrieval"]["rerank"] = True
    with pytest.raises(ValidationError, match="without a selected"):
        parse(raw)


@pytest.mark.parametrize("counts,expected", [
    ((2048, 2048), None), ((1024, 1024, 1024, 1024), None),
    ((2049,), ErrorCode.INPUT_TOO_LONG), ((2048, 2048, 1), ErrorCode.BATCH_TOO_LARGE),
    ((1, 1, 1, 1, 1), ErrorCode.BATCH_TOO_LARGE), ((True,), ErrorCode.INVALID_INPUT),
    ((0,), ErrorCode.INVALID_INPUT), ((), ErrorCode.INVALID_INPUT),
])
def test_complete_input_and_padded_batch_boundaries(counts, expected):
    items = tuple(ModelInput(item_id=uuid4(), text="body") for _ in counts)
    if expected is None:
        validate_input_batch(items, counts, InputLimits())
    else:
        with pytest.raises(RagError) as exc:
            validate_input_batch(items, counts, InputLimits())
        assert exc.value.error.code == expected


def test_capability_response_id_profile_and_order_checks():
    context = RequestContext(request_id=uuid4(), owner_id=uuid4(), purpose="qa",
                             deadline_monotonic_ns=time.monotonic_ns() + 1000000000, deadline_at=datetime.now(timezone.utc))
    items = (ModelInput(item_id=UUID(int=1), text="a"), ModelInput(item_id=UUID(int=2), text="b"))
    profile = EmbeddingProfile(name="embed")
    timing = ModelTimings(queue_ms=0, load_ms=0, inference_ms=1)
    vector = (1.0,) + (0.0,) * 1023
    results = tuple(EmbeddingResult(item_id=item.item_id, vector=vector, input_tokens=5) for item in items)
    response = EmbeddingResponse(request_id=context.request_id, profile_fingerprint=profile.identity, results=results, timings=timing)
    validate_response(response, items, profile, context)
    for changed in (response.model_copy(update={"request_id": uuid4()}),
                    response.model_copy(update={"profile_fingerprint": "0" * 64}),
                    response.model_copy(update={"results": tuple(reversed(results))}),
                    response.model_copy(update={"results": (results[0], results[0])})):
        with pytest.raises(RagError):
            validate_response(changed, items, profile, context)
    rank = RerankProfile(name="rank")
    scores = tuple(RerankScore(item_id=item.item_id, score=0.5, input_tokens=10) for item in items)
    ranked = RerankResponse(request_id=context.request_id, profile_fingerprint=rank.identity, results=scores, timings=timing)
    validate_response(ranked, items, rank, context)
    with pytest.raises(RagError):
        validate_response(ranked.model_copy(update={"results": tuple(reversed(scores))}), items, rank, context)
    with pytest.raises(ValidationError):
        EmbeddingResult(item_id=uuid4(), vector=(float("nan"),) + vector[1:], input_tokens=1)
    with pytest.raises(ValidationError):
        EmbeddingResult(item_id=uuid4(), vector=(0.0,) * 1024, input_tokens=1)
    with pytest.raises(RagError) as exc:
        require_provider(None, "embedding")
    assert exc.value.error.code == ErrorCode.CAPABILITY_UNAVAILABLE
