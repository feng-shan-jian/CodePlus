"""A small, frozen Milvus retrieval experiment; no answer model or daily migration."""

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import re
from tempfile import TemporaryDirectory
from time import perf_counter, process_time
import uuid

from .milvus_store import MilvusStore
from .models import fingerprint
from .service import KnowledgeService


ANALYZER = {"tokenizer": {"type": "jieba", "mode": "search", "hmm": False}, "filter": ["lowercase"]}


def evidence_recall(gold, hits, top_k, *, ignore_whitespace=False):
    """Micro recall: one gold source interval is one unit; only full union coverage counts."""
    covered = []
    for evidence in gold:
        intervals = sorted((s["char_start"], s["char_end"])
                           for hit in hits[:top_k] if hit["file"] == evidence["file"]
                           for s in hit["source_spans"])
        required = [(evidence["char_start"], evidence["char_end"])]
        if ignore_whitespace:
            required = [(evidence["char_start"] + match.start(), evidence["char_start"] + match.end())
                        for match in re.finditer(r"\S+", evidence["quote"])]
        checks = []
        for start, end in required:
            cursor = start
            for left, right in intervals:
                if left > cursor:
                    break
                cursor = max(cursor, right)
            checks.append(cursor >= end)
        covered.append(bool(checks) and all(checks))
    return {"covered": sum(covered), "total": len(gold), "per_range": covered,
            "recall": sum(covered) / len(gold) if gold else None}


def ann_recall(reference, approximate, top_k):
    """FLAT's actual scoped result count is the denominator, including when it is < K."""
    expected = {h["chunk_id"] for h in reference[:top_k]}
    found = expected & {h["chunk_id"] for h in approximate[:top_k]}
    return {"matched": len(found), "reference_count": len(expected),
            "recall": len(found) / len(expected) if expected else None}


def rrf(dense, bm25, constant):
    """Equal weights, one-based ranks, deterministic chunk-ID tie break; preserve both scores."""
    merged = {}
    for lane, hits in (("dense", dense), ("bm25", bm25)):
        for rank, hit in enumerate(hits, 1):
            entry = merged.setdefault(hit["chunk_id"], {
                **hit, "score": 0.0, "score_type": "rrf", "dense_rank": None, "dense_score": None,
                "bm25_rank": None, "bm25_score": None,
            })
            entry[f"{lane}_rank"], entry[f"{lane}_score"] = rank, hit["score"]
            entry["score"] += 1 / (constant + rank)
    result = sorted(merged.values(), key=lambda hit: (-hit["score"], hit["chunk_id"]))
    return [{**hit, "rank": rank} for rank, hit in enumerate(result, 1)]


def fuse(dense, bm25, constant):
    start = perf_counter()
    lanes = {"dense": dense["status"], "bm25": bm25["status"]}
    if any(state == "error" for state in lanes.values()):
        return {"status": "unavailable", "lanes": lanes, "hits": [], "fusion_ms": 0.0,
                "elapsed_ms": dense["elapsed_ms"] + bm25["elapsed_ms"]}
    hits = rrf(dense["hits"], bm25["hits"], constant)
    elapsed = (perf_counter() - start) * 1000
    return {"status": "ok" if hits else "no_hits", "lanes": lanes, "hits": hits, "fusion_ms": elapsed,
            "elapsed_ms": dense["elapsed_ms"] + bm25["elapsed_ms"] + elapsed}


def latency(samples):
    """Linear interpolation at (n-1)*p; successful and failed requests are summarized separately."""
    values = sorted(samples)
    def percentile(p):
        if not values:
            return None
        at = (len(values) - 1) * p
        low, high = math.floor(at), math.ceil(at)
        return values[low] + (values[high] - values[low]) * (at - low)
    return {"samples": len(values), "p50_ms": percentile(0.5), "p95_ms": percentile(0.95)}


def summarize(questions, records, lane, top_k):
    results = [r["lanes"][lane] for r in records]
    gold_total = sum(len(q["gold"]) for q in questions)
    # Failures contribute zero retrieved evidence to the fixed denominator, and are also reported separately.
    covered = sum(r["evidence"]["covered"] for r in results)
    successful = [r for r in results if r["status"] in {"ok", "no_hits"}]
    successful_gold = sum(r["evidence"]["total"] for r in successful)
    no_gold = [r for q, r in zip(questions, results) if not q["gold"]]
    return {"k": top_k, "questions": len(questions), "gold_ranges": gold_total,
            "covered_ranges": covered, "evidence_recall": covered / gold_total if gold_total else None,
            "successful_gold_ranges": successful_gold,
            "successful_evidence_recall": covered / successful_gold if successful_gold else None,
            "request_errors": sum(r["status"] == "error" for r in results),
            "unavailable": sum(r["status"] == "unavailable" for r in results),
            "no_hits": sum(r["status"] == "no_hits" for r in results),
            "no_gold_questions": len(no_gold), "no_gold_with_hits": sum(bool(r["hits"]) for r in no_gold),
            "no_gold_no_hits": sum(r["status"] == "no_hits" for r in no_gold),
            "success_latency": latency([r["elapsed_ms"] for r in successful]),
            "failure_latency": latency([r["elapsed_ms"] for r in results if r not in successful])}


def _write(path, value):
    # PyMilvus 3.0.2 describes Function input/output names as protobuf repeated containers.
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=list), encoding="utf-8")


def _rows(store, name):
    iterator = store.client.query_iterator(name, output_fields=["chunk_id", "doc_id", "generation_id", "text", "dense"],
                                           consistency_level="Strong")
    rows = []
    try:
        while batch := iterator.next():
            rows.extend({**r, "dense": [float(x) for x in r["dense"]]} for r in batch)
    finally:
        iterator.close()
    return sorted(rows, key=lambda row: row["chunk_id"])


def _drop_owned(store, names, cleanup):
    for name in names:
        # Only exact names registered by this invocation are ever passed here.
        try:
            store.client.drop_collection(name)
            cleanup.append({"collection": name, "removed": not store.client.has_collection(name)})
        except Exception as exc:
            cleanup.append({"collection": name, "removed": False, "error": str(exc)})


def freeze(config, fixtures, cleanup):
    annotations = json.loads((fixtures / "questions.json").read_text(encoding="utf-8"))
    questions = annotations["questions"]
    documents = [{"file": p.name, "content": p.read_bytes().decode("utf-8")} for p in sorted(fixtures.glob("*.md"))]
    originals = {d["file"]: d["content"] for d in documents}
    for q in questions:
        if q.get("scope") is not None and not set(q["scope"]) <= originals.keys():
            raise ValueError(f"Unknown scoped document: {q['id']}")
        for gold in q["gold"]:
            text = originals[gold["file"]]
            if (not 0 <= gold["char_start"] < gold["char_end"] <= len(text)
                    or text[gold["char_start"]:gold["char_end"]] != gold["quote"]
                    or (q.get("scope") is not None and gold["file"] not in q["scope"])):
                raise ValueError(f"Gold original range/quote/scope mismatch: {q['id']}")
        if not q["gold"] and not q.get("no_answer_reason"):
            raise ValueError(f"No-gold question needs an explicit reason: {q['id']}")
    with TemporaryDirectory(prefix="codeplus-eval-") as temporary:
        root = Path(temporary)
        with closing(KnowledgeService(replace(config, data_dir=str(root / "library")))) as service:
            try:
                kb = service.create("frozen evaluation")
                for d in documents:
                    path = root / d["file"]
                    path.write_bytes(d["content"].encode("utf-8"))
                    imported = service.import_document(kb["id"], path)
                    d.update(sha256=imported["content_hash"], doc_id=imported["id"], generation_id=imported["generation_id"])
                rows = _rows(service.store, kb["collection_name"])
                by_doc = {d["doc_id"]: d for d in documents}
                sources = {}
                for row in rows:
                    source = service.source(kb["id"], row["chunk_id"])
                    sources[row["chunk_id"]] = {
                        "file": by_doc[row["doc_id"]]["file"],
                        **{key: source[key] for key in ("doc_id", "generation_id", "text", "source_spans")},
                    }
                vectors, timings = [], []
                for q in questions:
                    start = perf_counter()
                    vectors.append(service.embedding.encode_query(q["query"]))
                    timings.append((perf_counter() - start) * 1000)
                data = {"profile": service.profile, "documents": documents, "annotations": annotations,
                        "sources": sources, "rows": rows, "query_vectors": vectors, "query_encoding_ms": timings}
                data["hashes"] = {key: fingerprint(data[key]) for key in
                                  ("profile", "documents", "annotations", "sources", "rows", "query_vectors")}
                return {"sha256": fingerprint(data), "data": data}
            finally:
                with service.metadata.connect() as db:
                    names = [r[0] for r in db.execute("SELECT collection_name FROM knowledge_bases")]
                _drop_owned(service.store, names, cleanup)


def resources():
    observation = {"process_cpu_seconds": process_time()}
    if platform.system() == "Windows":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in
                ("peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if get_memory(process(), ctypes.byref(counters), counters.cb):
            observation.update(rss_bytes=counters.rss, peak_rss_bytes=counters.peak_rss)
    else:
        import resource
        observation["ru_maxrss"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return observation


def _retrieve(store, collection, query, vector, sources, limit, doc_ids, lane, ef=None):
    start = perf_counter()
    raw = []
    try:
        raw = (store.search_bm25(collection, query, limit, doc_ids=doc_ids) if lane == "bm25" else
               store.search_dense(collection, vector, limit, doc_ids=doc_ids, ef=ef))
        hits = []
        for rank, match in enumerate(raw, 1):
            chunk_id = match["chunk_id"]
            source = sources[chunk_id]
            if doc_ids is not None and source["doc_id"] not in doc_ids:
                raise ValueError("Milvus result escaped the document scope")
            hits.append({**source, "chunk_id": chunk_id, "rank": rank, "score": float(match["distance"]),
                         "score_type": "bm25" if lane == "bm25" else "cosine_similarity"})
        return {"status": "ok" if hits else "no_hits", "raw": raw, "hits": hits,
                "elapsed_ms": (perf_counter() - start) * 1000}
    except Exception as exc:
        return {"status": "error", "raw": raw, "hits": [], "error": {"type": type(exc).__name__, "message": str(exc)},
                "elapsed_ms": (perf_counter() - start) * 1000}


def run(config, args):
    """All mutable resources are isolated, registered before creation, and closed in this run."""
    if not config.enabled:
        raise ValueError("Knowledge is disabled in the supplied configuration")
    if not (1 <= args.top_k <= args.candidates <= 16384 and math.isfinite(args.rrf_k) and args.rrf_k > 0
            and len(set(args.ef)) >= 2 and min(args.ef) >= args.candidates
            and args.m >= 2 and args.ef_construction > 0 and args.warmup >= 1 and args.repeats >= 1):
        raise ValueError("Require 1 <= K <= candidates <= ef, two distinct ef values, positive RRF/build/repeat parameters")
    output = Path(".codeplus/knowledge/experiments") / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8])
    output = output.resolve()
    output.mkdir(parents=True)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "status": "running", "cleanup": [],
              "config": {key: getattr(args, key) for key in
                         ("mode", "top_k", "candidates", "rrf_k", "m", "ef_construction", "ef", "warmup", "repeats")},
              "resources": {"start": resources()}, "records": [], "warmup_records": []}
    owned = []
    started = perf_counter()
    store = None
    try:
        try:
            store = MilvusStore(config.milvus_uri)
            report["environment"] = {"python": platform.python_version(), "platform": platform.platform(),
                                     "pymilvus": version("pymilvus"), "milvus": store.client.get_server_version(),
                                     "torch": version("torch"), "transformers": version("transformers")}
            start = perf_counter()
            frozen = (json.loads(args.replay.read_text(encoding="utf-8")) if args.replay else
                      freeze(config, args.fixtures, report["cleanup"]))
            data = frozen["data"]
            if fingerprint(data) != frozen["sha256"]:
                raise ValueError("Frozen input checksum mismatch")
            if any(fingerprint(data[key]) != value for key, value in data["hashes"].items()):
                raise ValueError("Frozen component checksum mismatch")
            _write(output / "frozen.json", frozen)
            report.update(frozen_sha256=frozen["sha256"], hashes=data["hashes"], profile=data["profile"],
                          preparation_seconds=perf_counter() - start, replay=args.replay is not None)
            report["corpus_sha256"] = fingerprint(sorted(
                ({"file": d["file"], "sha256": d["sha256"]} for d in data["documents"]), key=lambda d: d["file"]))
            report["resources"]["prepared"] = resources()
            rows, questions = data["rows"], data["annotations"]["questions"]
            sources, vectors = data["sources"], data["query_vectors"]
            by_file = {d["file"]: d["doc_id"] for d in data["documents"]}
            flat, hnsw = (f"codeplus_eval_{uuid.uuid4().hex}_{kind}" for kind in ("flat", "hnsw"))
            report["collections"] = {}
            for name, kind in ((flat, "FLAT"), (hnsw, "HNSW")):
                owned.append(name)
                start = perf_counter()
                store.create_experiment(name, data["hashes"]["profile"], data["profile"]["dimension"], rows,
                                        index_type=kind, build_params={} if kind == "FLAT" else
                                        {"M": args.m, "efConstruction": args.ef_construction},
                                        analyzer=ANALYZER if kind == "FLAT" else None)
                info = {"index_type": kind, "create_insert_build_load_seconds": perf_counter() - start,
                        "schema": store.client.describe_collection(name),
                        "indexes": [store.client.describe_index(name, i) for i in store.client.list_indexes(name)],
                        "stored_rows": len(rows), "readback_sha256": fingerprint(_rows(store, name)),
                        "input_sha256": data["hashes"]["rows"], "query_vectors_sha256": data["hashes"]["query_vectors"]}
                info["loaded_segments"] = [
                    {"segment_id": s.segment_id, "rows": s.num_rows, "state": s.state_name,
                     "index_name": s.index_name, "index_id": s.index_id, "mem_size": s.mem_size}
                    for s in store.client.list_loaded_segments(name)]
                report["collections"][name] = info
                _write(output / "report.json", report)
                if info["readback_sha256"] != info["input_sha256"]:
                    raise ValueError("FLAT/HNSW readback differs from frozen input")
            report["resources"]["loaded"] = resources()
            probes = ["差旅报销", "APQP", "QMS-204", "SLA", "API-429", "NPK-712", "ZXQNONCE998871"]
            report["analyzer"] = {"params": ANALYZER, "probes": probes,
                                  "tokens": [r.tokens for r in store.client.run_analyzer(
                                      probes, collection_name=flat, field_name="text")],
                                  "question_tokens": [r.tokens for r in store.client.run_analyzer(
                                      [q["query"] for q in questions], collection_name=flat, field_name="text")]}
            report["measurement"] = {
                "queries_per_repeat": len(questions), "warmup_rounds": args.warmup, "measured_rounds": args.repeats,
                "order": "fixed question order; FLAT, BM25, then HNSW ef in supplied order; sequential client",
                "latency_scope": "SDK request plus frozen source mapping; excludes encoding, build and warmup. Hybrid is the two sequential lane times plus fusion.",
                "evidence_denominator": "Number of annotated original intervals across answerable questions; complete interval union coverage only. Failures count zero; no-gold excluded.",
                "ann_denominator": "FLAT actual scoped result count at K; empty/error reference is unavailable, never recall=1.",
                "index_evidence": "SDK reports configured index type and loaded segment index IDs. Finished/indexed_rows can include skipped builds. These fields do not independently prove server execution type; verify the run's server logs before claiming HNSW execution.",
                "caveat": "Tiny synthetic corpus, one process, warm caches, no throughput or production performance conclusion; ties use returned FLAT IDs.",
            }
            for iteration in range(-args.warmup, args.repeats):
                for number, q in enumerate(questions):
                    doc_ids = None if q.get("scope") is None else [by_file[f] for f in q["scope"]]
                    record = {"question_id": q["id"], "iteration": iteration, "scope_doc_ids": doc_ids, "lanes": {}}
                    lanes = record["lanes"]
                    lanes["dense"] = _retrieve(store, flat, q["query"], vectors[number], sources,
                                                args.candidates, doc_ids, "dense")
                    if args.mode in {"all", "bm25", "hybrid"}:
                        lanes["bm25"] = _retrieve(store, flat, q["query"], vectors[number], sources,
                                                   args.candidates, doc_ids, "bm25")
                    if args.mode in {"all", "hybrid"}:
                        lanes["hybrid"] = fuse(lanes["dense"], lanes["bm25"], args.rrf_k)
                    for ef in args.ef:
                        result = _retrieve(store, hnsw, q["query"], vectors[number], sources,
                                           args.candidates, doc_ids, "dense", ef)
                        lanes[f"hnsw_ef_{ef}"] = result
                        result["ann"] = (ann_recall(lanes["dense"]["hits"], result["hits"], args.top_k)
                                         if lanes["dense"]["status"] == "ok" and result["status"] in {"ok", "no_hits"}
                                         else {"matched": 0, "reference_count": 0, "recall": None})
                    for result in lanes.values():
                        result["evidence"] = evidence_recall(q["gold"], result["hits"], args.top_k)
                    report["records" if iteration >= 0 else "warmup_records"].append(record)
            # Each distinct question contributes once to evidence recall, regardless of timing repeats.
            first = [r for r in report["records"] if r["iteration"] == 0]
            report["summary"] = {lane: summarize(questions, first, lane, args.top_k) for lane in first[0]["lanes"]}
            for lane, summary in report["summary"].items():
                results = [r["lanes"][lane] for r in report["records"]]
                summary["success_latency"] = latency([r["elapsed_ms"] for r in results if r["status"] in {"ok", "no_hits"}])
                summary["failure_latency"] = latency([r["elapsed_ms"] for r in results if r["status"] in {"error", "unavailable"}])
                summary["all_repeats_failures"] = sum(r["status"] in {"error", "unavailable"} for r in results)
                if lane.startswith("hnsw"):
                    references = [r["ann"] for r in results if r["ann"]["recall"] is not None]
                    denominator = sum(r["reference_count"] for r in references)
                    summary["ann"] = {"matched": sum(r["matched"] for r in references), "reference_count": denominator,
                                      "recall": sum(r["matched"] for r in references) / denominator if denominator else None,
                                      "available_queries": len(references), "unavailable_queries": len(results) - len(references),
                                      "execution_verified": False,
                                      "interpretation": "Observed neighbor overlap for the configured HNSW collection. NOT verified as HNSW execution by SDK metadata; requires this run's server build/load logs. Small segments may skip building despite nonzero indexed_rows/index_id."}
            report["hnsw_indexes_after_queries"] = [store.client.describe_index(hnsw, i) for i in store.client.list_indexes(hnsw)]
            report["status"] = ("query_failures" if any(v["status"] in {"error", "unavailable"}
                                for r in report["records"] + report["warmup_records"] for v in r["lanes"].values()) else "ok")
        except Exception as exc:
            report["status"] = "error"
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            if store is not None:
                _drop_owned(store, owned, report["cleanup"])
            if any(not c["removed"] for c in report["cleanup"]):
                report["status"] = "cleanup_failed"
            report["elapsed_seconds"] = perf_counter() - started
            report["resources"]["end"] = resources()
            _write(output / "report.json", report)
    finally:
        if store is not None:
            store.close()
    return {"status": report["status"], "report": str(output / "report.json"),
            "frozen": str(output / "frozen.json"), "summary": report.get("summary", {}),
            "error": report.get("error")}
