"""Opt-in real GPU integration tests. A skip is not R03 acceptance.

R03_RUN_REAL=1 runs the bounded probe once. R03_REPORT_PATH optionally persists
the raw evidence. No fake CUDA/models or online fallback paths exist here.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def observed(tmp_path_factory):
    if os.environ.get("R03_RUN_REAL") != "1":
        pytest.skip("R03 real GPU probe not requested; no acceptance evidence")
    path = Path(__file__).resolve().parents[1] / "probes/models/probe.py"
    spec = importlib.util.spec_from_file_location("r03_local_model_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = os.environ.get("R03_REPORT_PATH")
    destination = Path(output) if output else tmp_path_factory.mktemp("r03-models") / "observations.json"
    return module, module.run_probe(destination)


def test_real_cuda_identity_and_placement(observed):
    module, report = observed
    assert report["status"] == "OBSERVATIONS_COMPLETE"
    assert report["gpu"]["torch_cuda"] == "13.0"
    assert report["gpu"]["name"].startswith("NVIDIA")
    assert report["source_unchanged"] is True
    for capability, data in report["models"].items():
        identity = data["identity"]
        assert identity["model_id"] == "Qwen/Qwen3-" + ("Embedding" if capability == "embedding" else "Reranker") + "-0.6B"
        assert len(identity["revision"]) == 40
        assert identity["license"] == "apache-2.0"
        assert data["cold_load"]["actual_devices"] == ["cuda:0"]
        assert data["cold_load"]["actual_dtypes"] == ["torch.bfloat16"]
        assert data["cold_load"]["actual_attention"] == "sdpa"
        assert identity["use_cache"] is False
        assert identity["max_position_embeddings"] == (32768 if capability == "embedding" else 40960)
        assert identity["max_input_tokens"] == module.MAX_INPUT_TOKENS == 2048
        assert identity["truncation"] is False
        assert "model.safetensors" in identity["files"]
        assert len(identity["files"]["tokenizer.json"]["sha256"]) == 64


def test_document_and_instructed_query_vectors(observed):
    _, report = observed
    data = report["models"]["embedding"]
    assert [item["id"] for item in data["normal"]["results"]] == ["chunk-beijing", "chunk-gravity", "chunk-moon"]
    vectors = data["normal"]["results"] + data["query"]["results"]
    assert all(item["dimension"] == 1024 and item["norm"] == pytest.approx(1, abs=1e-5) for item in vectors)
    assert data["query"]["inputs"][0]["complete_input"].startswith("Instruct: ")
    assert "\nQuery:What is the capital of China?" in data["query"]["inputs"][0]["complete_input"]
    assert max(data["similarities"], key=lambda value: value["cosine"])["id"] == "chunk-beijing"


def test_full_tokenizer_inputs_and_explicit_limits(observed):
    module, report = observed
    from transformers import AutoTokenizer

    for capability, data in report["models"].items():
        root = module.DEFAULT_CACHE / data["identity"]["relative_cache_path"]
        tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, trust_remote_code=False)
        for row in data["normal"]["inputs"]:
            complete = row["complete_input"]
            if capability == "embedding":
                ids = tokenizer.encode(complete, add_special_tokens=True, truncation=False)
            else:
                assert complete.startswith(module.PREFIX) and complete.endswith(module.SUFFIX)
                inner = complete[len(module.PREFIX):-len(module.SUFFIX)]
                ids = sum((tokenizer.encode(text, add_special_tokens=False) for text in (module.PREFIX, inner, module.SUFFIX)), [])
            assert row["input_tokens"] == len(ids)
        assert "Title: Capital\nThe capital of China is Beijing." in data["normal"]["inputs"][0]["complete_input"]
        assert 2046 <= data["long_boundary"]["inputs"][0]["input_tokens"] <= 2048
        for case in ("long_body", "long_title", "long_query", "mixed_batch_atomic"):
            assert data["errors"][case]["code"] == "INPUT_TOO_LONG"
            assert data["errors"][case]["details"]["input_tokens"] > 2048
        assert data["errors"]["padded_batch"]["code"] == "BATCH_TOO_LARGE"


def test_rerank_identity_alignment_and_last_token_oracle(observed):
    _, report = observed
    data = report["models"]["reranker"]
    assert data["last_logit_oracle"]["max_score_difference"] <= 1e-6
    base = {item["id"]: item["score"] for item in data["batch_alignment"]["1"]}
    for values in data["batch_alignment"].values():
        assert len(values) == len(base) == 3
        assert values[0]["id"] == "chunk-beijing"
        assert {item["id"] for item in values} == set(base)
        assert all(0 <= item["score"] <= 1 for item in values)
        for item in values:
            assert item["score"] == pytest.approx(base[item["id"]], abs=0.02)


def test_bounded_batch_measurements_and_release(observed):
    _, report = observed
    baseline = report["model_free_allocator_baseline"]
    assert baseline["bf16_matmul_finite"] is True
    for data in report["models"].values():
        assert [case["batch_size"] for case in data["benchmarks"]] == [1, 2, 4]
        for case in data["benchmarks"]:
            assert len(case["trials"]) == 3
            for trial in case["trials"]:
                assert trial["elapsed_ms"] > 0
                assert trial["padded_batch_tokens"] <= 4096
                assert 0 < trial["peak_allocated_mib"] <= 2048
        assert data["long_batch"]["batches"][0]["batch_size"] == 2
        assert data["unload"]["allocated_mib"] == baseline["allocated_mib"]
        assert data["unload"]["reserved_mib"] == baseline["reserved_mib"]
        assert data["unload"]["tracked_model_and_tensor_refs"] > 300
        assert data["unload"]["live_model_and_tensor_refs"] == 0
    assert report["embedding_reload"]["actual_devices"] == ["cuda:0"]
    assert report["embedding_reload"]["unload"]["allocated_mib"] == baseline["allocated_mib"]
    assert report["embedding_reload"]["unload"]["live_model_and_tensor_refs"] == 0
    assert report["cleanup"] == {"allocated_mib": baseline["allocated_mib"], "reserved_mib": baseline["reserved_mib"]}
    assert report["co_residency"] == "NOT_TESTED"
    assert report["worker"] == "NOT_IMPLEMENTED"


def test_real_dependency_device_and_capped_allocator_failures(observed):
    _, report = observed
    failures = report["failure_children"]
    assert failures["dependency"]["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert failures["device"]["error"]["code"] == "DEVICE_UNAVAILABLE"
    assert failures["oom"]["error"]["code"] == "CUDA_OUT_OF_MEMORY"
    assert all(value["returncode"] == 2 for value in failures.values())
    assert failures["oom"]["error"]["details"]["allocator_cap_mib"] == 32
    assert failures["oom"]["error"]["details"]["allocated_mib"] == 0
    assert failures["oom"]["error"]["details"]["stage"] == "load"
    assert failures["oom"]["error"]["details"]["model_results_returned"] is False
    assert failures["oom"]["child_process_exited"] is True
