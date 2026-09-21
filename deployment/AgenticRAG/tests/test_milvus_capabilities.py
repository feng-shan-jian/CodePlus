"""Opt-in real integration test. A skip is NOT R02 acceptance.

Set R02_RUN_REAL=1 and R02_REPORT_PATH to run against the dedicated Compose
project. Missing/unavailable services fail once opted in; there is no mock path.
"""

from __future__ import annotations

import importlib.util
import math
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def observations(tmp_path_factory):
    if os.environ.get("R02_RUN_REAL") != "1":
        pytest.skip("R02 real Milvus not requested; set R02_RUN_REAL=1; no acceptance evidence")
    script = Path(__file__).resolve().parents[1] / "probes" / "milvus" / "probe.py"
    spec = importlib.util.spec_from_file_location("r02_milvus_probe", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = os.environ.get("R02_REPORT_PATH")
    destination = Path(report) if report else tmp_path_factory.mktemp("r02-milvus") / "observations.json"
    return module.run_probe(destination)


def same_snapshot(left, right):
    assert left["collection"] == right["collection"]
    assert left["members"] == right["members"]
    assert left["member_texts_sha256"] == right["member_texts_sha256"]
    assert left["sparse_hit_count"] == right["sparse_hit_count"]
    assert left["anchor_scores"] == pytest.approx(right["anchor_scores"], rel=1e-6, abs=1e-7)
    for field in ("sparse_top10", "dense_top4"):
        # Dense non-matches can tie. Compare rank groups by score and member sets,
        # while requiring the unique exact-vector first hit to remain first.
        if field == "dense_top4":
            assert left[field][0]["id"] == right[field][0]["id"] == 1
            assert [h["score"] for h in left[field]] == pytest.approx([h["score"] for h in right[field]], abs=1e-7)
        else:
            assert [h["id"] for h in left[field]] == [h["id"] for h in right[field]]
            assert [h["score"] for h in left[field]] == pytest.approx([h["score"] for h in right[field]], rel=1e-6, abs=1e-7)


def test_real_native_bm25_schema_and_index_loading(observations):
    data = observations
    assert data["status"] == "OBSERVATIONS_COMPLETE"
    assert data["pymilvus"] == "3.0.2"
    assert "3.0.1" in str(data["server_version"])
    assert data["analyzer_tokens"] == ["quasar", "telescopes", "ocean"]
    for version in ("v1_build", "v2_build"):
        schema = data[version]["schema"]
        assert schema["consistency_level"] == 0  # protobuf Strong
        fields = {field["name"]: field for field in schema["fields"]}
        assert int(fields["dense"]["type"]) == 101  # FLOAT_VECTOR
        assert int(fields["sparse"]["type"]) == 104  # SPARSE_FLOAT_VECTOR
        assert schema["functions"][0]["input_field_names"] == ["text"]
        assert schema["functions"][0]["output_field_names"] == ["sparse"]
        assert int(schema["functions"][0]["type"]) == 1  # BM25
        assert data[version]["indexes"]["dense_cosine"]["metric_type"] == "COSINE"
        assert data[version]["indexes"]["dense_cosine"]["index_type"] == "IVF_FLAT"
        assert data[version]["indexes"]["sparse_bm25"]["metric_type"] == "BM25"
        assert data[version]["load_state"] == "Loaded"
    for phase, minimum in (("v1_finalized", 1028), ("v2_initial_finalized", 1028), ("v2_finalized", 2052)):
        ready = data[phase]
        assert ready["load_state"] == "Loaded"
        for index in ready["indexes"].values():
            assert index["state"] == "Finished"
            assert index["total_rows"] >= minimum
            assert index["indexed_rows"] == index["total_rows"]
            assert index["pending_index_rows"] == 0
        assert sum(segment["num_rows"] for segment in ready["segments"] if segment["state"] == "Sealed") >= minimum


def test_strong_write_then_read_without_flush(observations):
    data = observations
    assert data["v1_immediate_write_read"]["members"] == [1, 2, 3, 4] + list(range(10000, 11024))
    assert data["v1_immediate_write_read"]["dense_top4"][0]["id"] == 1
    assert {h["id"] for h in data["v1_immediate_write_read"]["sparse_top10"]} == {1, 2}
    assert len(data["v2_immediate_write_read"]["members"]) == 2052
    assert 4 not in data["v2_immediate_write_read"]["members"]
    assert 5 in data["v2_immediate_write_read"]["members"]


def test_physical_versions_isolate_members_and_bm25_statistics(observations):
    data = observations
    old, candidate = data["v1_baseline"], data["v2_after_mutation"]
    assert old["collection"] != candidate["collection"]
    same_snapshot(old, data["v1_after_v2_mutation"])
    assert data["v2_before_mutation"]["members"] == old["members"]
    assert data["v2_before_mutation"]["anchor_scores"] == pytest.approx(old["anchor_scores"], rel=1e-6)
    assert len(candidate["members"]) == 2052
    assert candidate["sparse_hit_count"] == 1026
    for anchor in ("1", "2"):
        assert not math.isclose(candidate["anchor_scores"][anchor], old["anchor_scores"][anchor], rel_tol=.05)
    assert 4 in old["members"]
    assert 4 not in candidate["members"]
    assert 5 not in old["members"]
    assert 5 in candidate["members"]


def test_empty_hits_are_distinct_from_explicit_service_and_capability_errors(observations):
    assert all(result == [] for result in observations["empty_results"].values())
    failures = observations["errors"]
    assert set(failures) == {"missing_collection", "unsupported_analyzer", "unreachable_endpoint"}
    assert "collection" in failures["missing_collection"]["message"].lower()
    assert "tokenizer" in failures["unsupported_analyzer"]["message"].lower()
    assert failures["unreachable_endpoint"]["seconds"] < 15
    assert all(failure["class"] and failure["message"] for failure in failures.values())


def test_real_client_process_and_service_restart_keep_persisted_versions(observations):
    data = observations
    assert data["new_client_process"]["pid"] != data["pid"]
    assert data["after_service_restart"]["pid"] not in (data["pid"], data["new_client_process"]["pid"])
    assert data["restart"]["before_started_at"] != data["restart"]["after_started_at"]
    assert data["restart"]["health"] == "healthy"
    assert data["after_service_restart"]["readiness"]["seconds"] < 120
    assert all(error["code"] in (503, 901)
               for error in data["after_service_restart"]["readiness"]["transient_failures"])
    for phase in ("new_client_process", "after_service_restart"):
        same_snapshot(data["v1_baseline"], data[phase]["snapshots"][0])
        # Equal-score high-frequency documents may reorder across restart; compare
        # all members and stable anchors, not arbitrary order among tied top hits.
        candidate = data[phase]["snapshots"][1]
        assert candidate["members"] == data["v2_after_mutation"]["members"]
        assert candidate["member_texts_sha256"] == data["v2_after_mutation"]["member_texts_sha256"]
        assert candidate["anchor_scores"] == pytest.approx(data["v2_after_mutation"]["anchor_scores"], rel=1e-6, abs=1e-7)
        assert candidate["sparse_hit_count"] == 1026


def test_resource_ownership_and_test_collection_cleanup(observations):
    data = observations
    assert data["cleanup_errors"] == []
    assert set(data["collections_after_cleanup"]) == set(data["collections_before"])
    assert not set(data["collections_owned"]) & set(data["collections_after_cleanup"])
    unrelated_before = {k: v for k, v in data["containers_before"].items() if v["project"] != data["project"]}
    unrelated_after = {k: v for k, v in data["containers_after"].items() if v["project"] != data["project"]}
    assert unrelated_before == unrelated_after
    for phase in ("storage_before_kib", "storage_after_v1_kib", "storage_after_v2_kib", "storage_after_restart_kib"):
        assert set(data[phase]) == {"etcd", "minio", "standalone"}
        assert all(amount >= 0 for amount in data[phase].values())
