"""SciFact retrieval-only execution through the installed production RAG core.

Build and query are separate invocations. Query uses two passes to avoid swapping
embedding/reranker weights every question. The rerank pass calls the same core
_rerank implementation with the unchanged, frozen RRF candidate inputs.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time
from uuid import UUID, uuid4

from runtime_inputs import ROOT, INPUT_HASHES, bind_storage, load_runtime_inputs, protect_runtime_reads


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def error(exc):
    return {"type": type(exc).__name__, "message": str(exc), "trace": getattr(exc, "retrieval_trace", None)}


def source_identity():
    import agentic_rag
    root = Path(agentic_rag.__file__).parent
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob("*")) if p.suffix in {".py", ".json", ".sql"}}
    return {"root": str(root), "sha256": fingerprint(files), "files": files,
            "dependencies": {n: importlib.metadata.version(n) for n in
                ("codeplus-agentic-rag", "pymilvus", "apsw", "pydantic", "tokenizers", "torch", "transformers")}}


class RotatingProvider:
    """Rotate completed owner sessions without raising the worker request limit."""
    def __init__(self, config):
        from agentic_rag.models import LocalModelClient
        self.config = config
        self.client = LocalModelClient(config)
        self.retired = []

    @property
    def owner_id(self):
        if len(self.client.handles) >= self.config.max_session_requests - 32:
            from agentic_rag.models import LocalModelClient
            for handle in tuple(self.client.handles.values()):
                if not handle.wait_finished(60):
                    raise RuntimeError("Cannot rotate an unfinished model request")
            self.retired.append({"owner_id": str(self.client.owner_id), "requests": len(self.client.handles)})
            self.client.close()
            self.client = LocalModelClient(self.config)
        return self.client.owner_id

    def __getattr__(self, name):
        return getattr(self.client, name)


def configuration(storage, endpoint):
    from agentic_rag.config import assemble_configuration
    from agentic_rag.ingestion.parsing import PARSER
    from agentic_rag.ingestion.chunking import chunker_config
    from agentic_rag.indexes.manifest import LAYOUT
    defaults = {
        "storage": {**storage, "milvus_uri": endpoint},
        "models": {"embedding": "embed", "reranker": "rank"},
        "model_profiles": [{"name": "embed", "capability": "embedding"}, {"name": "rank", "capability": "rerank"}],
        "processing": {"parser": PARSER.model_dump(mode="json"), "chunker": chunker_config().model_dump(mode="json"),
            "index": {"schema_version_name": LAYOUT, "nlist": 64, "bm25_k1": 1.2, "bm25_b": 0.75}},
        "retrieval": {"mode": "fixed", "route": "hybrid", "rerank": False,
            "dense_candidates": 50, "bm25_candidates": 50, "rerank_candidates": 24, "rrf_k": 60, "nprobe": 64,
            "context_chunks": 8, "context_tokens": 8000}}
    selected = json.loads((ROOT.parents[1] / "deployment/AgenticRAG/docs/retrieval-selected.json").read_text(encoding="utf-8"))
    # The first pass freezes candidates; the second uses the selected reranker.
    selected["retrieval"]["rerank"] = False
    return assemble_configuration(defaults=defaults, explicit=selected).knowledge



def build(inputs, base, config, worker):
    from agentic_rag.config import ProcessingSnapshot
    from agentic_rag.ingestion import InputSelection, select_inputs, capture_inputs, process_inputs
    from agentic_rag.ingestion.build import build_first_revision
    from agentic_rag.indexes.milvus import MilvusRevisionIndex
    from agentic_rag.models import FrozenTokenizer
    from agentic_rag.storage import Catalog
    if (base / "build-attempt.json").exists():
        raise ValueError("Build was already attempted; inspect its retained state before any retry")
    write(base / "build-attempt.json", {"status": "started", "source": source_identity(), "config": config.model_dump(mode="json")})
    started = time.perf_counter()
    catalog = Catalog(config.storage.data_dir)
    backend = MilvusRevisionIndex(config.storage, catalog)
    provider = RotatingProvider(worker)
    report = {"status": "running", "answer_model_executed": False, "config": config.model_dump(mode="json")}
    try:
        kb = catalog.create_library("BEIR SciFact 5183 documents")
        snapshot = ProcessingSnapshot.capture(uuid4(), config)
        manifest = select_inputs(tuple(InputSelection(path=str(ROOT / d["path"])) for d in inputs["documents"]))
        print(json.dumps({"stage": "selected", "documents": len(inputs["documents"])}), flush=True)
        with catalog.begin_import(kb.kb_id, snapshot, manifest) as owner:
            captured = capture_inputs(catalog, owner)
            print(json.dumps({"stage": "captured", "documents": len(captured)}), flush=True)
            processed = process_inputs(catalog, owner, FrozenTokenizer(config.embedding, Path(worker.model_cache)))
            report["failures"] = [r.model_dump(mode="json") for r in captured + processed if r.stage == "failed"]
            if report["failures"]:
                raise ValueError("Corpus import has failed documents; full-corpus evaluation required")
            report["document_map"] = {str(r.document_id): Path(r.raw.metadata.original_name).stem.removeprefix("SF-") for r in captured}
            print(json.dumps({"stage": "processed", "documents": len(processed)}), flush=True)
            done = 0
            def observe(stage, value):
                nonlocal done
                if stage == "file_terminal":
                    done += 1
                    if done % 100 == 0:
                        print(json.dumps({"stage": "embedding", "documents": done, "total": len(captured)}), flush=True)
                elif stage not in {"encoding_response", "encoded"}:
                    print(json.dumps({"stage": stage, **{k: v for k, v in value.items() if k != "model_calls"}}, default=str), flush=True)
            report["build"] = build_first_revision(catalog, owner, provider, backend, observer=observe)
            report["state"] = {"kb_id": str(kb.kb_id), "revision_id": report["build"]["receipt"]["revision_id"],
                "config_fingerprint": fingerprint(config.model_dump(mode="json")), "source_sha256": source_identity()["sha256"],
                "document_map": report["document_map"]}
            write(base / "state.json", report["state"])
        report["status"] = "complete"
    except BaseException as exc:
        report.update(status="failed", error=error(exc))
        raise
    finally:
        report.update(elapsed_seconds=time.perf_counter()-started, retired_sessions=provider.retired)
        provider.close()
        backend.close()
        write(base / "build.json", report)


def query(inputs, base, config, worker, run_name):
    from agentic_rag.config import resolve_run
    from agentic_rag.domain import RunStatus
    from agentic_rag.indexes.milvus import MilvusRevisionIndex
    from agentic_rag.retrieval import RetrievalSearch
    from agentic_rag.storage import Catalog
    state = json.loads((base / "state.json").read_text(encoding="utf-8"))
    identity = source_identity()
    output = base / "retrieval" / run_name
    if output.exists():
        raise ValueError("Run exists; preserve its attempts and use a new name")
    output.mkdir(parents=True)
    protocol = {"input_sha256": INPUT_HASHES[inputs["split"]], "source": identity,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "split": inputs["split"], "config": config.model_dump(mode="json"),
        "index_build": json.loads((base / "build.json").read_text(encoding="utf-8"))["config"],
        "worker": worker.model_dump(mode="json"), "state": state, "answer_model_executed": False,
        "stage_order": ["hybrid_without_rerank_all_queries", "production_rerank_on_frozen_rrf24_all_queries"]}
    write(output / "protocol.json", protocol)
    catalog = Catalog(config.storage.data_dir)
    backend = MilvusRevisionIndex(config.storage, catalog)
    provider = RotatingProvider(worker)
    records = [{**q, "status": "error", "hits": [], "error": {"type": "NotStarted"}} for q in inputs["questions"]]
    started = time.perf_counter()
    summary = {"status": "running", "answer_model_executed": False}
    first_final = None
    try:
        with catalog.start_run(UUID(state["kb_id"]), resolve_run(config, "qa")) as lease:
            if str(lease.run.revision_id) != state["revision_id"]:
                raise ValueError("Published revision changed")
            search = RetrievalSearch(catalog, lease.run.run_id, provider, backend)
            chunk_docs = {key: state["document_map"][row["document_id"]] for key, row in search.expected.items()}
            write(output / "chunk-documents.json", chunk_docs)
            write(output / "chunk-spans.json", {key: {"doc_id": chunk_docs[key],
                "source_spans": [{"char_start": row["span_start"], "char_end": row["span_end"]}]}
                for key, row in search.expected.items()})
            summary["collection_name"] = search.artifact["collection_name"]
            with (output / "retrieval.jsonl").open("x", encoding="utf-8") as stream:
                for number, question in enumerate(inputs["questions"]):
                    try:
                        result = search.search(question["query"], limit=24)
                        record = {**question, **result, "status": "ok"}
                    except Exception as exc:
                        record = {**question, "status": "error", "hits": [], "error": error(exc)}
                    records[number] = record
                    stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    stream.flush()
                    if number % 25 == 0 or number + 1 == len(records):
                        print(json.dumps({"stage": "retrieval", "completed": number+1, "total": len(records), "status": record["status"]}), flush=True)
            summary["retrieval_seconds"] = time.perf_counter()-started
            phase_start = time.perf_counter()
            with (output / "rerank.jsonl").open("x", encoding="utf-8") as stream:
                for number, record in enumerate(records):
                    trace = {"inputs": [], "batches": [], "ranking": []}
                    reranked = {"id": record["id"], "query": record["query"], "status": "error", "hits": []}
                    begin = time.perf_counter()
                    try:
                        if record["status"] != "ok":
                            raise ValueError("Retrieval failed; rerank not attempted")
                        hits = search._rerank(record["query"], record["hits"], trace, None)
                        hits = [hit for hit in hits if hit["score"] >= config.retrieval.min_score]
                        reranked.update(status="ok", hits=hits[:10], context_candidates=hits)
                    except Exception as exc:
                        reranked["error"] = error(exc)
                    reranked.update(trace=trace, elapsed_ms=(time.perf_counter()-begin)*1000)
                    if number == 0:
                        first_final = reranked
                    stream.write(json.dumps(reranked, ensure_ascii=False, default=str) + "\n")
                    stream.flush()
                    record["rerank_status"] = reranked["status"]
                    if number % 25 == 0 or number+1 == len(records):
                        print(json.dumps({"stage": "rerank", "completed": number+1, "total": len(records), "status": reranked["status"]}), flush=True)
            summary["rerank_seconds"] = time.perf_counter()-phase_start
            summary["retrieval_errors"] = sum(r["status"] != "ok" for r in records)
            summary["rerank_errors"] = sum(r.get("rerank_status") != "ok" for r in records)
            failed = summary["retrieval_errors"] or summary["rerank_errors"]
            lease.finish(RunStatus.FAILED if failed else RunStatus.COMPLETED, "explicit_error" if failed else "finished")
            summary["status"] = "complete_with_errors" if failed else "complete"
        # One diagnostic control checks that the two-pass execution returns the
        # same result as the public production search with rerank enabled.
        direct_config = config.model_copy(update={"retrieval": config.retrieval.model_copy(update={"rerank": True})})
        # Replay the complete validated ranking through production source
        # selection. This measures per-call truncation without another model call.
        from agentic_rag.sources import SourceSession
        from agentic_rag.adapters.codeplus.policy import SourceTextMeter
        class RankedSearch:
            route = config.retrieval.route
            def search(self, query, **kwargs):
                return {"hits": self.hits}
        ranked = RankedSearch()
        context_errors = 0
        with catalog.start_run(UUID(state["kb_id"]), resolve_run(direct_config, "qa")) as context_lease:
            ranked.catalog = catalog
            ranked.run_id = context_lease.run.run_id
            session = SourceSession(catalog, context_lease, SourceTextMeter(), dense=ranked)
            with (output / "context.jsonl").open("x", encoding="utf-8") as stream:
                for line in (output / "rerank.jsonl").read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    context = {"id": row["id"], "query": row["query"], "status": "error", "hits": []}
                    try:
                        if row["status"] != "ok":
                            raise ValueError("Rerank failed; source selection not attempted")
                        ranked.hits = row["context_candidates"]
                        result = session.search(row["query"])
                        context.update(status="ok", hits=[{**item, "source_spans": [
                            {"char_start": span["start"], "char_end": span["end"]}
                            for span in item["returned_spans"]]} for item in result.payload["items"]],
                            trace=session.retrieval_trace(result.payload["call_id"]))
                    except Exception as exc:
                        context["error"] = error(exc)
                        context_errors += 1
                    stream.write(json.dumps(context, ensure_ascii=False, default=str) + "\n")
            context_lease.finish(RunStatus.FAILED if context_errors else RunStatus.COMPLETED,
                                 "explicit_error" if context_errors else "finished")
        summary["context_errors"] = context_errors
        if context_errors:
            summary["status"] = "complete_with_errors"
        with catalog.start_run(UUID(state["kb_id"]), resolve_run(direct_config, "qa")) as control_lease:
            direct = RetrievalSearch(catalog, control_lease.run.run_id, provider, backend).search(inputs["questions"][0]["query"], limit=10)
            matched = (first_final is not None and first_final["status"] == "ok"
                and [h["chunk_id"] for h in direct["hits"]] == [h["chunk_id"] for h in first_final["hits"]]
                and all(abs(a["score"]-b["score"]) <= 1e-6 for a,b in zip(direct["hits"], first_final["hits"])))
            write(output / "production-control.json", {"query_id": inputs["questions"][0]["id"],
                "matched": matched, "score_tolerance": 1e-6, "direct": direct, "two_pass": first_final})
            control_lease.finish(RunStatus.COMPLETED if matched else RunStatus.FAILED,
                "finished" if matched else "explicit_error")
            summary["production_control_matched"] = matched
            if not matched:
                raise ValueError("Two-pass result differs from direct production reranked search")
    except BaseException as exc:
        summary.update(status="failed", error=error(exc))
        raise
    finally:
        summary.update(elapsed_seconds=time.perf_counter()-started, retired_sessions=provider.retired,
            source_unchanged=identity == source_identity())
        provider.close()
        backend.close()
        write(output / "completion.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "query"))
    parser.add_argument("--split", choices=tuple(INPUT_HASHES), default="development")
    parser.add_argument("--index-name", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model-cache", required=True)
    parser.add_argument("--run-name", default="baseline")
    args = parser.parse_args()
    import re
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.run_name):
        raise ValueError("Unsafe run name")
    protect_runtime_reads(args.split)
    inputs = load_runtime_inputs(args.split)
    storage = bind_storage(inputs, args.index_name)
    config = configuration(storage, args.endpoint)
    base = Path(storage["data_dir"]).parent
    from agentic_rag.config import WorkerExecutionConfig
    worker = WorkerExecutionConfig(executable=sys.executable, model_cache=args.model_cache,
        runtime_dir=str(base / "worker"), idle_timeout_ms=60000)
    if args.action == "build":
        build(inputs, base, config, worker)
        return 0
    result = query(inputs, base, config, worker, args.run_name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
