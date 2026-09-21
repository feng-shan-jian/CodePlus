"""R02 real Milvus protocol experiment; synthetic vectors, never a quality benchmark.

Only the dedicated local Compose project may be restarted. Each invocation owns
randomly named collections and drops those exact names in finally. No aliases or
user collections are written. The service is intentionally left up for review.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import uuid

import pymilvus
from pymilvus import DataType, Function, FunctionType, MilvusClient, MilvusException


PROJECT = "codeplus-r02-probe"
URI = "http://127.0.0.1:19531"
COMPOSE = Path(__file__).with_name("compose.yaml")
ANALYZER = {"tokenizer": "standard", "filter": ["lowercase"]}
TIMEOUT = 60


def command(*args: str, timeout: int = 60) -> str:
    result = subprocess.run(args, text=True, encoding="utf-8", capture_output=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{args!r}: exit {result.returncode}: {result.stderr}")
    return result.stdout.strip()


def inventory() -> dict:
    ids = command("docker", "ps", "-aq").splitlines()
    inspected = json.loads(command("docker", "inspect", *ids)) if ids else []
    return {
        item["Id"]: {
            "name": item["Name"].lstrip("/"), "image": item["Config"]["Image"],
            "image_id": item["Image"], "started_at": item["State"]["StartedAt"],
            "status": item["State"]["Status"],
            "project": (item["Config"].get("Labels") or {}).get("com.docker.compose.project"),
            "service": (item["Config"].get("Labels") or {}).get("com.docker.compose.service"),
            "volumes": [{"name": mount.get("Name"), "destination": mount["Destination"]}
                        for mount in item["Mounts"] if mount["Type"] == "volume"],
        }
        for item in inspected
    }


def owned_services(current: dict) -> dict:
    owned = {item["service"]: {"id": key, **item}
             for key, item in current.items() if item["project"] == PROJECT}
    if set(owned) != {"etcd", "minio", "standalone"}:
        raise RuntimeError("Dedicated R02 Compose project must have exactly three services")
    expected_images = {"etcd": "quay.io/coreos/etcd:v3.5.25",
                       "minio": "quay.io/minio/minio:RELEASE.2024-12-18T13-15-44Z",
                       "standalone": "milvusdb/milvus:v3.0.1"}
    for service, item in owned.items():
        if item["image"] != expected_images[service] or item["status"] != "running":
            raise RuntimeError(f"Unexpected R02 service: {item}")
        expected_volume = {"etcd": "etcd", "minio": "minio", "standalone": "milvus"}[service]
        if [v["name"] for v in item["volumes"]] != [f"{PROJECT}_{expected_volume}"]:
            raise RuntimeError(f"Unexpected volume ownership: {item}")
    bindings = json.loads(command("docker", "inspect", owned["standalone"]["id"]))[0]["HostConfig"]["PortBindings"]
    if bindings.get("19530/tcp") != [{"HostIp": "127.0.0.1", "HostPort": "19531"}]:
        raise RuntimeError("R02 endpoint is not bound to the verified dedicated container")
    return owned


def storage_kib(owned: dict) -> dict:
    # The etcd image has no du executable. A short-lived, networkless helper
    # mounts ONLY the checked owned volumes read-only, without volume copy-up.
    args = ["docker", "run", "--rm", "--network", "none", "--label", "codeplus.r02.measurement=true"]
    services = ("etcd", "minio", "standalone")
    for service in services:
        volume = owned[service]["volumes"][0]["name"]
        args += ["--mount", f"type=volume,source={volume},target=/{service},readonly,volume-nocopy"]
    args += ["alpine:3.22", "du", "-sk", *[f"/{service}" for service in services]]
    result = command(*args)
    return {line.split()[1].lstrip("/"): int(line.split()[0]) for line in result.splitlines()}


def connect() -> MilvusClient:
    return MilvusClient(uri=URI, timeout=TIMEOUT)


def schema(client: MilvusClient, analyzer: dict = ANALYZER):
    definition = client.create_schema(auto_id=False, enable_dynamic_field=False)
    definition.add_field("id", DataType.INT64, is_primary=True)
    definition.add_field("text", DataType.VARCHAR, max_length=4096,
                         enable_analyzer=True, analyzer_params=analyzer)
    definition.add_field("dense", DataType.FLOAT_VECTOR, dim=4)
    definition.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
    definition.add_function(Function(name="text_bm25", input_field_names=["text"],
                                      output_field_names=["sparse"], function_type=FunctionType.BM25))
    return definition


def create(client: MilvusClient, name: str) -> dict:
    start = time.perf_counter()
    indexes = client.prepare_index_params()
    # One IVF partition is an exact distance oracle for these synthetic vectors;
    # it still produces a real persisted index, without testing ANN graph recall.
    indexes.add_index(field_name="dense", index_name="dense_cosine", index_type="IVF_FLAT", metric_type="COSINE",
                      params={"nlist": 1})
    indexes.add_index(field_name="sparse", index_name="sparse_bm25", index_type="SPARSE_INVERTED_INDEX",
                      metric_type="BM25", params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75})
    client.create_collection(name, schema=schema(client),
                             consistency_level="Strong", timeout=TIMEOUT)
    created = time.perf_counter()
    client.create_index(name, index_params=indexes, timeout=TIMEOUT)
    indexed = time.perf_counter()
    client.load_collection(name, timeout=TIMEOUT)
    return {"create_index_load_seconds": time.perf_counter() - start,
            "create_seconds": created - start, "create_index_seconds": indexed - created,
            "load_seconds": time.perf_counter() - indexed,
            "schema": client.describe_collection(name),
            "indexes": {field: client.describe_index(name, field) for field in ("dense_cosine", "sparse_bm25")},
            "load_state": str(client.get_load_state(name)["state"])}


def finalized_indexes(client: MilvusClient, name: str, minimum_rows: int) -> dict:
    start = time.perf_counter()
    deadline = time.monotonic() + 180
    while True:
        indexes = {index: client.describe_index(name, index, timeout=TIMEOUT)
                   for index in ("dense_cosine", "sparse_bm25")}
        if all(item["state"] == "Finished" and item["total_rows"] >= minimum_rows
               and item["indexed_rows"] == item["total_rows"] and item["pending_index_rows"] == 0
               for item in indexes.values()):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Actual data indexes were not built: {indexes}")
        time.sleep(2)
    # Force a real sealed-index reload, then capture server-reported segments.
    client.release_collection(name, timeout=TIMEOUT)
    client.load_collection(name, timeout=TIMEOUT)
    segments = [{"segment_id": item.segment_id, "num_rows": item.num_rows,
                 "state": item.state_name, "index_name": item.index_name,
                 "index_id": item.index_id, "mem_size": item.mem_size}
                for item in client.list_loaded_segments(name, timeout=TIMEOUT)]
    return {"indexes": indexes, "segments": segments,
            "load_state": str(client.get_load_state(name)["state"]),
            "wait_data_index_and_reload_seconds": time.perf_counter() - start}


def search(client: MilvusClient, name: str, query: str = "quasar", *, field: str = "sparse", filter: str = "") -> list:
    result = client.search(name, data=[query] if field == "sparse" else [[1.0, 0.0, 0.0, 0.0]],
                           anns_field=field, filter=filter, limit=3000 if field == "sparse" else 10, output_fields=["text"],
                           search_params={"metric_type": "BM25" if field == "sparse" else "COSINE",
                                          "params": {} if field == "sparse" else {"nprobe": 1}},
                           consistency_level="Strong", timeout=TIMEOUT)[0]
    return [{"id": hit["id"], "score": float(hit["distance"]), "text": hit["entity"]["text"]} for hit in result]


def snapshot(client: MilvusClient, name: str) -> dict:
    members = client.query(name, filter="id >= 0", output_fields=["id", "text"],
                            consistency_level="Strong", timeout=TIMEOUT, limit=3000)
    members = sorted(members, key=lambda row: row["id"])
    sparse = search(client, name)
    dense = search(client, name, field="dense")
    return {"collection": name, "members": [row["id"] for row in members],
            "member_texts_sha256": hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest(),
            "sparse_hit_count": len(sparse), "sparse_top10": sparse[:10],
            "anchor_scores": {str(hit["id"]): hit["score"] for hit in sparse if hit["id"] in (1, 2)},
            "dense_top4": dense[:4]}


def child_read(names: list[str], *, after_restart: bool = False) -> dict:
    # A new Python interpreter is a real process boundary, with no reused client.
    args = [sys.executable, "-B", str(Path(__file__).resolve()), "--read", *names]
    if after_restart:
        args.append("--wait-data-ready")
    raw = command(*args, timeout=180)
    return json.loads(raw)


def await_data_ready(names: list[str]) -> tuple[MilvusClient, dict]:
    """Explicit startup readiness only; never a fallback for ordinary query errors."""
    start = time.perf_counter()
    deadline = time.monotonic() + 90
    failures = []
    client = None
    while True:
        try:
            client = client or MilvusClient(uri=URI, timeout=5)
            for name in names:
                client.load_collection(name, timeout=10)
                client.query(name, filter="id >= 0", output_fields=["count(*)"],
                             consistency_level="Strong", timeout=5)
            return client, {"seconds": time.perf_counter() - start, "transient_failures": failures}
        except MilvusException as error:
            # Only concrete transient availability codes from the same endpoint.
            if error.code not in (503, 901) or time.monotonic() >= deadline:
                if client is not None:
                    client.close()
                raise
            failures.append({"class": type(error).__name__, "code": error.code, "message": str(error)})
            time.sleep(2)


def failure(callable_) -> dict:
    start = time.perf_counter()
    try:
        callable_()
    except Exception as error:
        return {"class": type(error).__name__, "code": getattr(error, "code", None),
                "message": str(error), "seconds": time.perf_counter() - start}
    raise AssertionError("An explicit dependency/capability error was required, but the call succeeded")


def restart_owned(owned: dict) -> dict:
    container_id = owned["standalone"]["id"]
    # Re-check ownership immediately before mutation; never restart by guessed name.
    checked = owned_services(inventory())
    if checked["standalone"]["id"] != container_id:
        raise RuntimeError("Dedicated container identity changed")
    start = time.perf_counter()
    command("docker", "restart", "--time", "30", container_id, timeout=120)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        current = json.loads(command("docker", "inspect", container_id))[0]
        health = current["State"].get("Health", {}).get("Status")
        if health == "healthy":
            return {"container_id": container_id, "before_started_at": owned["standalone"]["started_at"],
                    "after_started_at": current["State"]["StartedAt"], "health": health,
                    "elapsed_seconds": time.perf_counter() - start}
        if current["State"]["Status"] in ("exited", "dead"):
            raise RuntimeError(f"Dedicated Milvus exited during restart: {current['State']}")
        time.sleep(2)
    raise TimeoutError("Dedicated Milvus did not become healthy within 180 seconds")


def run_probe(report_path: Path) -> dict:
    before = inventory()
    owned = owned_services(before)
    prefix = "r02_" + uuid.uuid4().hex
    names = [prefix + "_v1", prefix + "_v2", prefix + "_bad_analyzer"]
    client = None
    report = {"schema_version": 1, "status": "RUNNING", "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "real database protocol only; synthetic 4-dimensional vectors; no semantic quality claim",
              "python": sys.version, "platform": platform.platform(), "pymilvus": pymilvus.__version__,
              "pid": os.getpid(), "endpoint": URI, "project": PROJECT,
              "collections_owned": names, "containers_before": before,
              "analyzer": ANALYZER, "consistency": "Strong", "storage_before_kib": storage_kib(owned)}
    try:
        report["image_digests"] = {
            service: json.loads(command("docker", "image", "inspect", item["image_id"]))[0]["RepoDigests"]
            for service, item in owned.items()}
        report["measurement_image_digest"] = json.loads(command("docker", "image", "inspect", "alpine:3.22"))[0]["RepoDigests"]
        client = connect()
        report["server_version"] = client.get_server_version(detail=True)
        report["collections_before"] = client.list_collections()
        report["analyzer_tokens"] = client.run_analyzer(texts="Quasar TELESCOPES, ocean.", analyzer_params=ANALYZER).tokens
        report["v1_build"] = create(client, names[0])
        baseline_rows = [
            {"id": 1, "text": "quasar telescope telescope telescope", "dense": [1., 0., 0., 0.]},
            {"id": 2, "text": "quasar quasar orbit", "dense": [.8, .6, 0., 0.]},
            {"id": 3, "text": "ocean coral reef", "dense": [0., 1., 0., 0.]},
            {"id": 4, "text": "legacyonly glacier", "dense": [0., 0., 1., 0.]},
        ]
        # Above the server's 1024-row minimum segment indexing threshold. These
        # unrelated filler rows do not contain the query term; no config override.
        baseline_rows += [{"id": 10000 + n, "text": f"ocean coral filler{n}", "dense": [0., 1., 0., 0.]}
                          for n in range(1024)]
        start = time.perf_counter()
        report["v1_insert"] = client.insert(names[0], baseline_rows, timeout=TIMEOUT)
        report["v1_immediate_write_read"] = snapshot(client, names[0])
        client.flush(names[0], timeout=TIMEOUT)
        report["v1_build"]["insert_strong_read_flush_seconds"] = time.perf_counter() - start
        report["v1_finalized"] = finalized_indexes(client, names[0], 1028)
        report["storage_after_v1_kib"] = storage_kib(owned)
        report["v1_baseline"] = snapshot(client, names[0])
        report["v2_build"] = create(client, names[1])
        client.insert(names[1], baseline_rows, timeout=TIMEOUT)
        client.flush(names[1], timeout=TIMEOUT)
        report["v2_initial_finalized"] = finalized_indexes(client, names[1], 1028)
        report["v2_before_mutation"] = snapshot(client, names[1])
        # v2 remains a candidate under construction. v1 is never changed after publication.
        start = time.perf_counter()
        client.delete(names[1], ids=[4], timeout=TIMEOUT)
        additions = [{"id": 1000 + n, "text": "quasar " * 10 + f"newcandidate{n}", "dense": [0., 0., 0., 1.]}
                     for n in range(1024)]
        additions.append({"id": 5, "text": "novelonly comet", "dense": [0., 0., 0., 1.]})
        report["v2_insert"] = client.insert(names[1], additions, timeout=TIMEOUT)
        report["v2_immediate_write_read"] = snapshot(client, names[1])
        client.flush(names[1], timeout=TIMEOUT)
        report["v2_build"]["candidate_update_strong_read_flush_seconds"] = time.perf_counter() - start
        report["v2_finalized"] = finalized_indexes(client, names[1], 2052)
        report["storage_after_v2_kib"] = storage_kib(owned)
        report["v2_after_mutation"] = snapshot(client, names[1])
        report["v1_after_v2_mutation"] = snapshot(client, names[0])
        report["empty_results"] = {
            "absent_term": search(client, names[0], "unseentermzzzz"),
            "known_term_excluded_members": search(client, names[0], filter="id < 0"),
            "dense_excluded_members": search(client, names[0], field="dense", filter="id < 0"),
        }
        report["errors"] = {
            "missing_collection": failure(lambda: search(client, prefix + "_missing")),
            "unsupported_analyzer": failure(lambda: client.create_collection(
                names[2], schema=schema(client, {"tokenizer": "r02_unsupported_tokenizer"}), timeout=TIMEOUT)),
        }
        # Reserve a local non-listening port throughout the negative connection test.
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            bad_uri = "http://127.0.0.1:" + str(reserve.getsockname()[1])
            report["errors"]["unreachable_endpoint"] = failure(lambda: MilvusClient(uri=bad_uri, timeout=2))
        client.close()
        client = None
        report["new_client_process"] = child_read(names[:2])
        report["restart"] = restart_owned(owned)
        report["after_service_restart"] = child_read(names[:2], after_restart=True)
        client = connect()
        report["storage_after_restart_kib"] = storage_kib(owned)
        report["status"] = "OBSERVATIONS_COMPLETE"
    except Exception as error:
        report["status"] = "FAILED"
        report["unexpected_error"] = {"class": type(error).__name__, "message": str(error)}
        raise
    finally:
        cleanup_errors = []
        try:
            client = client or connect()
            for name in names:
                try:
                    if client.has_collection(name, timeout=TIMEOUT):
                        client.drop_collection(name, timeout=TIMEOUT)
                except Exception as error:
                    cleanup_errors.append({"collection": name, "error": str(error)})
            report["collections_after_cleanup"] = client.list_collections()
        except Exception as error:
            cleanup_errors.append({"error": str(error)})
        finally:
            if client is not None:
                client.close()
        report["cleanup_errors"] = cleanup_errors
        report["containers_after"] = inventory()
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        # Protobuf repeated containers support list(value) through __getitem__
        # but may not expose __iter__. Never stringify structured evidence.
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=list) + "\n", encoding="utf-8")
    if cleanup_errors:
        raise RuntimeError(f"Owned collection cleanup failed: {cleanup_errors}")
    return json.loads(report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read", nargs=2, required=True)
    parser.add_argument("--wait-data-ready", action="store_true")
    args = parser.parse_args()
    if not all(name.startswith("r02_") and name.replace("_", "").isalnum() for name in args.read):
        parser.error("Only R02 collection names are accepted")
    if args.wait_data_ready:
        child_client, readiness = await_data_ready(args.read)
    else:
        child_client, readiness = connect(), None
    try:
        print(json.dumps({"pid": os.getpid(), "readiness": readiness,
                          "snapshots": [snapshot(child_client, name) for name in args.read]}))
    finally:
        child_client.close()
