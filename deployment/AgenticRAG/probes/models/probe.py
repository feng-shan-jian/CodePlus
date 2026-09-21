"""R03 bounded real-CUDA capability probe, not an application provider/worker.

Only synthetic inputs, immutable local model files, sequential residency, and
explicit failures are supported. There is no CPU/API/other-model fallback.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import weakref


HERE = Path(__file__).resolve().parent
DEFAULT_CACHE = Path.home() / ".cache/codeplus-agenticrag/models"
INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on '
          'the Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
          '<|im_end|>\n<|im_start|>user\n')
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
MAX_INPUT_TOKENS = 2048
MAX_PADDED_BATCH_TOKENS = 4096
MAX_BATCH_SIZE = 4
MEMORY_CAP_MIB = 2048
MIB = 1024 ** 2


class ProbeError(RuntimeError):
    def __init__(self, code, message, **details):
        super().__init__(f"{code}: {message}")
        self.code, self.details = code, details

    def record(self):
        return {"code": self.code, "message": str(self), "details": self.details}


def sha_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def dependencies():
    try:
        import torch
        from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer
        return torch, AutoModel, AutoModelForCausalLM, AutoTokenizer
    except (ImportError, OSError) as exc:
        raise ProbeError("DEPENDENCY_UNAVAILABLE", "Install the pinned R03 CUDA environment; " + str(exc)) from exc


def require_cuda(torch):
    if not torch.cuda.is_available():
        raise ProbeError("DEVICE_UNAVAILABLE", "NVIDIA CUDA is required; no fallback", torch_cuda=torch.version.cuda)
    if not torch.cuda.is_bf16_supported():
        raise ProbeError("DTYPE_UNSUPPORTED", "This frozen profile requires CUDA bfloat16")
    torch.cuda.set_device(0)
    return torch.device("cuda:0")


def gpu_measure(torch, operation):
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    result = operation()
    torch.cuda.synchronize()
    return result, {"elapsed_ms": (time.perf_counter() - start) * 1000,
                    "allocated_mib": torch.cuda.memory_allocated() / MIB,
                    "reserved_mib": torch.cuda.memory_reserved() / MIB,
                    "peak_allocated_mib": torch.cuda.max_memory_allocated() / MIB,
                    "peak_reserved_mib": torch.cuda.max_memory_reserved() / MIB}


def checked_assets(capability, cache):
    lock = json.loads((HERE / "models.lock.json").read_text(encoding="utf-8"))["models"][capability]
    root = cache / lock["relative_cache_path"]
    for name, expected in lock["files"].items():
        path = root / name
        if not path.is_file():
            raise ProbeError("MODEL_UNAVAILABLE", f"Missing pinned file {path}")
        if path.stat().st_size != expected["size"] or sha_file(path) != expected["sha256"]:
            raise ProbeError("MODEL_IDENTITY_MISMATCH", f"Pinned file differs: {path}")
    return root, lock


def document_input(item):
    text = item["text"]
    title = item.get("title", "")
    return f"Title: {title}\n{text}" if title else text


def validate_items(items):
    ids = [item["id"] for item in items]
    if len(set(ids)) != len(ids) or any(not isinstance(value, str) or not value for value in ids):
        raise ProbeError("INVALID_INPUT", "Candidate IDs must be unique nonempty strings")
    if any(not isinstance(item["text"], str) or not item["text"].strip() for item in items):
        raise ProbeError("INVALID_INPUT", "Document text must not be empty")


class LocalProbe:
    def __init__(self, capability, cache=DEFAULT_CACHE):
        prepared_at = time.perf_counter()
        self.capability, self.cache = capability, Path(cache)
        self.torch, self.AutoModel, self.AutoLM, self.AutoTokenizer = dependencies()
        self.device = require_cuda(self.torch)
        self.model = None
        root, lock = checked_assets(capability, self.cache)
        self.tokenizer = self.AutoTokenizer.from_pretrained(root, padding_side="left", local_files_only=True,
                                                           trust_remote_code=False)
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.root = root
        self.identity = {**lock, "dtype": "bfloat16", "device": "cuda:0", "attention": "sdpa",
            "use_cache": False, "max_input_tokens": MAX_INPUT_TOKENS,
            "max_position_embeddings": config["max_position_embeddings"],
            "document_template": "Title: {title}\\n{text} if title else {text}",
            "instruction": INSTRUCTION, "tokenizer_add_special_tokens": capability == "embedding",
            "padding_side": "left", "truncation": False,
            "query_template": "Instruct: {instruction}\\nQuery:{query}",
            "pooling": "last_nonpadding_token" if capability == "embedding" else None,
            "normalization": "float32_L2" if capability == "embedding" else None,
            "dimension": config["hidden_size"] if capability == "embedding" else None,
            "rerank_prefix": PREFIX if capability == "reranker" else None,
            "rerank_suffix": SUFFIX if capability == "reranker" else None,
            "rerank_body": "<Instruct>: {instruction}\\n<Query>: {query}\\n<Document>: {document}",
            "score_type": "softmax_float32([no,yes])[yes]" if capability == "reranker" else None,
            "logits_to_keep": 1 if capability == "reranker" else None,
            "torch": self.torch.__version__, "transformers": importlib.metadata.version("transformers")}
        if MAX_INPUT_TOKENS > config["max_position_embeddings"]:
            raise ProbeError("INVALID_PROFILE", "Probe input cap exceeds architecture context")
        self.identity["fingerprint"] = fingerprint(self.identity)
        self.preparation_ms = (time.perf_counter() - prepared_at) * 1000

    def load(self):
        factory = self.AutoModel if self.capability == "embedding" else self.AutoLM
        try:
            def operation():
                self.model = factory.from_pretrained(self.root, dtype=self.torch.bfloat16,
                    attn_implementation="sdpa", local_files_only=True, trust_remote_code=False).to(self.device).eval()
            _, measured = gpu_measure(self.torch, operation)
        except self.torch.cuda.OutOfMemoryError as exc:
            self.close()
            raise ProbeError("CUDA_OUT_OF_MEMORY", "Model load exceeded available GPU/allocator budget", stage="load", cuda_error=str(exc)) from exc
        devices = {str(parameter.device) for parameter in self.model.parameters()}
        dtypes = {str(parameter.dtype) for parameter in self.model.parameters()}
        if devices != {"cuda:0"} or dtypes != {"torch.bfloat16"}:
            raise ProbeError("MODEL_PLACEMENT_MISMATCH", "All model parameters must be CUDA bfloat16", devices=sorted(devices), dtypes=sorted(dtypes))
        return {**measured, "actual_devices": sorted(devices), "actual_dtypes": sorted(dtypes),
                "actual_attention": self.model.config._attn_implementation}

    def close(self):
        refs = [] if self.model is None else [weakref.ref(self.model)] + [
            weakref.ref(tensor) for tensor in (*self.model.parameters(), *self.model.buffers())]
        self.model = None
        gc.collect()
        self.torch.cuda.empty_cache()
        self.torch.cuda.synchronize()
        return {"tracked_model_and_tensor_refs": len(refs), "live_model_and_tensor_refs": sum(ref() is not None for ref in refs)}

    def inputs(self, items, query=None):
        validate_items(items)
        if query is not None and (not isinstance(query, str) or not query.strip()):
            raise ProbeError("INVALID_INPUT", "Query must not be empty")
        if self.capability == "reranker" and query is None:
            raise ProbeError("INVALID_INPUT", "Rerank requires a query")
        sequences, summaries = [], []
        for item in items:
            doc = document_input(item)
            if self.capability == "reranker":
                inner = f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {doc}"
                pieces = [PREFIX, inner, SUFFIX]
                ids = sum((self.tokenizer.encode(piece, add_special_tokens=False) for piece in pieces), [])
                complete = "".join(pieces)
            else:
                complete = doc if query is None else f"Instruct: {INSTRUCTION}\nQuery:{query}"
                ids = self.tokenizer.encode(complete, add_special_tokens=True, truncation=False)
            if len(ids) > MAX_INPUT_TOKENS:
                raise ProbeError("INPUT_TOO_LONG", "Complete model input exceeds the frozen probe profile; split upstream",
                    candidate_id=item["id"], input_tokens=len(ids), max_input_tokens=MAX_INPUT_TOKENS,
                    architecture_max=self.identity["max_position_embeddings"], capability=self.capability)
            sequences.append(ids)
            summaries.append({"id": item["id"], "input_tokens": len(ids), "complete_input_sha256": fingerprint(complete),
                              "complete_input": complete, "body_sha256": fingerprint(item["text"])})
        return sequences, summaries

    def infer(self, items, query=None, batch_size=1, full_logits=False):
        # Validate every candidate before inference: failure never returns partial success.
        sequences, summaries = self.inputs(items, query)
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ProbeError("INVALID_BATCH", "Batch size outside bounded probe range")
        for start in range(0, len(sequences), batch_size):
            batch = sequences[start:start + batch_size]
            if len(batch) * max(map(len, batch)) > MAX_PADDED_BATCH_TOKENS:
                raise ProbeError("BATCH_TOO_LARGE", "Padded batch token budget exceeded")
        if self.model is None:
            raise ProbeError("MODEL_NOT_LOADED", "Explicit load required")
        outputs, timings = [], []
        try:
            for start in range(0, len(sequences), batch_size):
                chunk = sequences[start:start + batch_size]
                def operation():
                    encoded = self.tokenizer.pad({"input_ids": chunk}, padding=True, return_tensors="pt").to(self.device)
                    with self.torch.inference_mode():
                        if self.capability == "embedding":
                            hidden = self.model(**encoded, use_cache=False).last_hidden_state
                            result = self.torch.nn.functional.normalize(hidden[:, -1].float(), p=2, dim=1)
                        else:
                            logits = self.model(**encoded, use_cache=False, logits_to_keep=0 if full_logits else 1).logits[:, -1, :]
                            label_ids = [self.tokenizer.convert_tokens_to_ids("no"), self.tokenizer.convert_tokens_to_ids("yes")]
                            if len(set(label_ids)) != 2 or any(value == self.tokenizer.unk_token_id for value in label_ids):
                                raise ProbeError("TOKENIZER_MISMATCH", "yes/no tokens missing")
                            result = self.torch.softmax(logits[:, label_ids].float(), dim=1)[:, 1]
                        if not self.torch.isfinite(result).all():
                            raise ProbeError("INVALID_OUTPUT", "Model produced non-finite values")
                        return result.cpu().tolist()
                values, measured = gpu_measure(self.torch, operation)
                timings.append({**measured, "batch_size": len(chunk), "input_tokens": list(map(len, chunk)),
                                "padded_batch_tokens": len(chunk) * max(map(len, chunk))})
                for offset, value in enumerate(values):
                    source = summaries[start + offset]
                    outputs.append({"id": source["id"], "input_tokens": source["input_tokens"],
                                    "vector" if self.capability == "embedding" else "score": value})
        except self.torch.cuda.OutOfMemoryError as exc:
            raise ProbeError("CUDA_OUT_OF_MEMORY", "Inference exceeded available GPU/allocator budget; no partial results", stage="inference", cuda_error=str(exc)) from exc
        if self.capability == "reranker":
            outputs.sort(key=lambda value: (-value["score"], value["id"]))
        return {"results": outputs, "batches": timings, "inputs": summaries}


def error_record(operation, expected):
    try:
        operation()
    except ProbeError as exc:
        if exc.code != expected:
            raise
        return exc.record()
    raise AssertionError(f"Expected {expected}; operation unexpectedly succeeded")


def failure_child(kind, cache=DEFAULT_CACHE):
    torch, *_ = dependencies()
    require_cuda(torch)
    if kind == "oom":
        cap = 32 * MIB
        torch.cuda.set_per_process_memory_fraction(cap / torch.cuda.get_device_properties(0).total_memory)
        try:
            local = LocalProbe("embedding", cache)
            local.load()  # The first large parameter exceeds the quota; real model error path.
        except ProbeError as exc:
            if exc.code != "CUDA_OUT_OF_MEMORY":
                raise
            exc.details.update(allocator_cap_mib=32, allocated_mib=torch.cuda.memory_allocated()/MIB,
                               mechanism="real model load under CUDA caching allocator quota", model_results_returned=False)
            raise
        else:
            local.close()
            raise AssertionError("Controlled model load unexpectedly succeeded")
    raise AssertionError(f"Expected failure for {kind}")


def child_case(kind, cache=DEFAULT_CACHE):
    environment = dict(os.environ)
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    command = [sys.executable, "-B"]
    if kind == "dependency":
        command.append("-S")  # Real interpreter without site-packages, no mock imports.
    if kind == "device":
        environment["CUDA_VISIBLE_DEVICES"] = "-1"
    command += [str(Path(__file__).resolve()), "--failure-child", kind, "--cache", str(cache)]
    completed = subprocess.run(command, env=environment, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    expected = {"oom": "CUDA_OUT_OF_MEMORY", "device": "DEVICE_UNAVAILABLE", "dependency": "DEPENDENCY_UNAVAILABLE"}[kind]
    result = json.loads(completed.stdout)
    if completed.returncode != 2 or result["error"]["code"] != expected:
        raise AssertionError(f"Unexpected child result: {completed.returncode} {completed.stdout} {completed.stderr}")
    return {"command": command, "returncode": completed.returncode, "child_process_exited": True,
            "stderr_utf8": completed.stderr, **result}


def near_limit_item(probe, query):
    # Synthetic text only. Search the final tokenizer length, never trim source documents.
    lo, hi = 1, MAX_INPUT_TOKENS
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = {"id": "long-boundary", "title": "Synthetic boundary", "text": "nebula " * mid}
        try:
            probe.inputs([candidate], query)
            lo = mid
        except ProbeError as exc:
            if exc.code != "INPUT_TOO_LONG":
                raise
            hi = mid - 1
    return {"id": "long-boundary", "title": "Synthetic boundary", "text": "nebula " * lo}


def compact(result):
    # Keep enough numeric evidence to reproduce; don't store thousands of vector values.
    for value in result["results"]:
        if "vector" in value:
            vector = value.pop("vector")
            value.update(dimension=len(vector), norm=math.sqrt(sum(v*v for v in vector)),
                         vector_sha256=fingerprint(vector), vector_head=vector[:8])
    return result


def run_probe(destination, cache=DEFAULT_CACHE):
    destination = Path(destination)
    report = {"schema": 1, "status": "RUNNING", "cwd": str(Path.cwd()), "command": sys.argv,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pid": os.getpid(),
        "code_sha256": {name: sha_file(HERE / name) for name in ("probe.py", "assets.py", "models.lock.json")},
        "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
        "packages": {dist.metadata["Name"]: dist.version for dist in importlib.metadata.distributions()},
        "limits": {"allocator_cap_mib": MEMORY_CAP_MIB, "max_input_tokens": MAX_INPUT_TOKENS,
                   "max_padded_batch_tokens": MAX_PADDED_BATCH_TOKENS, "max_batch_size": MAX_BATCH_SIZE},
        "co_residency": "NOT_TESTED", "linux": "NOT_TESTED", "worker": "NOT_IMPLEMENTED",
        "quality_evaluation": "NOT_EXECUTED; synthetic capability examples only", "models": {}}
    active = None
    torch = None
    try:
        torch, *_ = dependencies()
        require_cuda(torch)
        total = torch.cuda.get_device_properties(0).total_memory
        free, _ = torch.cuda.mem_get_info()
        report["gpu"] = {"name": torch.cuda.get_device_name(), "total_mib": total/MIB, "free_before_mib": free/MIB,
                         "torch_cuda": torch.version.cuda, "cudnn": torch.backends.cudnn.version(),
                         "driver": subprocess.check_output(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], text=True).strip()}
        if free < (MEMORY_CAP_MIB + 256) * MIB:
            raise ProbeError("GPU_BUDGET_UNAVAILABLE", "Need 2304 MiB free before bounded probe; leave other GPU processes untouched")
        torch.cuda.set_per_process_memory_fraction(MEMORY_CAP_MIB * MIB / total)
        # Establish an actual model-free BLAS allocator baseline, not an assumed zero.
        def initialize_blas():
            matrix = torch.ones((32, 32), dtype=torch.bfloat16, device="cuda:0")
            product = matrix @ matrix
            torch.cuda.synchronize()
            return bool(torch.isfinite(product).all().item())
        finite, baseline_timing = gpu_measure(torch, initialize_blas)
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        report["model_free_allocator_baseline"] = {"bf16_matmul_finite": finite, "measurement": baseline_timing,
            "allocated_mib": torch.cuda.memory_allocated()/MIB, "reserved_mib": torch.cuda.memory_reserved()/MIB,
            "configured_cublas_workspace_mib": torch.backends.cuda.cublas_workspace_size()/MIB}
        docs = [{"id": "chunk-beijing", "title": "Capital", "text": "The capital of China is Beijing."},
                {"id": "chunk-gravity", "title": "Physics", "text": "Gravity attracts two bodies towards each other."},
                {"id": "chunk-moon", "text": "The Moon orbits the Earth."}]
        original_hash = fingerprint(docs)
        query = "What is the capital of China?"
        report["synthetic_input_hash"] = original_hash
        for capability in ("embedding", "reranker"):
            print(f"R03 loading {capability}", file=sys.stderr, flush=True)
            active = LocalProbe(capability, cache)
            section = {"identity": active.identity, "asset_validation_tokenizer_setup_ms": active.preparation_ms}
            report["models"][capability] = section
            section["cold_load"] = active.load()  # Cold model instance, OS disk cache may be warm.
            section["total_model_startup_ms"] = active.preparation_ms + section["cold_load"]["elapsed_ms"]
            rq = query if capability == "reranker" else None
            first = active.infer(docs, rq, batch_size=2)
            if capability == "embedding":
                encoded_query = active.infer([{"id": "query", "text": query}], query)
                qv = encoded_query["results"][0]["vector"]
                section["similarities"] = [{"id": d["id"], "cosine": sum(a*b for a,b in zip(qv,d["vector"]))} for d in first["results"]]
                section["query"] = compact(encoded_query)
            else:
                oracle = active.infer(docs[:1], rq, full_logits=True)
                fast = active.infer(docs[:1], rq)
                section["last_logit_oracle"] = {"full": oracle["results"], "kept_last": fast["results"],
                    "max_score_difference": abs(oracle["results"][0]["score"] - fast["results"][0]["score"])}
                section["batch_alignment"] = {str(size): active.infer(docs, rq, batch_size=size)["results"] for size in (1,2,3)}
            section["normal"] = compact(first)
            section["benchmarks"] = []
            for size, repetitions in ((1,16),(2,64),(4,128)):
                batch = [{"id": f"synthetic-{i}", "title": "Astronomy", "text": "The nebula emits light. " * repetitions} for i in range(size)]
                trials = [active.infer(batch, rq, batch_size=size) for _ in range(3)]
                section["benchmarks"].append({"batch_size": size, "trials": [value["batches"][0] for value in trials]})
            long_item = near_limit_item(active, rq)
            section["long_boundary"] = compact(active.infer([long_item], rq))
            long_batch = [dict(long_item, id=f"long-{i}") for i in range(2)]
            section["long_batch"] = compact(active.infer(long_batch, rq, batch_size=2))
            errors = {}
            errors["long_body"] = error_record(lambda: active.inputs([{"id":"too-long", "text":"nebula " * 4000}], rq), "INPUT_TOO_LONG")
            errors["long_title"] = error_record(lambda: active.inputs([{"id":"too-long-title", "title":"nebula " * 4000, "text":"short"}], rq), "INPUT_TOO_LONG")
            errors["long_query"] = error_record(lambda: active.inputs(docs[:1], "nebula " * 4000), "INPUT_TOO_LONG")
            errors["padded_batch"] = error_record(lambda: active.infer([dict(long_item,id=str(i)) for i in range(3)], rq, batch_size=3), "BATCH_TOO_LARGE")
            errors["mixed_batch_atomic"] = error_record(lambda: active.infer(docs + [{"id":"bad", "text":"nebula " * 4000}], rq, batch_size=2), "INPUT_TOO_LONG")
            section["errors"] = errors
            released, measured = gpu_measure(torch, active.close)
            section["unload"] = {**measured, **released}
            active = None
        # Reranker -> embedding reload makes both switch directions observable.
        active = LocalProbe("embedding", cache)
        report["embedding_reload"] = active.load()
        report["embedding_reload"]["asset_validation_tokenizer_setup_ms"] = active.preparation_ms
        report["embedding_reload"]["output"] = compact(active.infer(docs[:1]))
        released, measured = gpu_measure(torch, active.close)
        report["embedding_reload"]["unload"] = {**measured, **released}
        active = None
        assert fingerprint(docs) == original_hash
        report["source_unchanged"] = True
        report["failure_children"] = {kind: child_case(kind, cache) for kind in ("dependency", "device", "oom")}
        report["status"] = "OBSERVATIONS_COMPLETE"
    except Exception as exc:
        report["status"] = "FAILED"
        report["error"] = exc.record() if isinstance(exc, ProbeError) else {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        if active is not None:
            active.close()
        if torch is not None and torch.cuda.is_available():
            gc.collect()
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            report["cleanup"] = {"allocated_mib": torch.cuda.memory_allocated()/MIB, "reserved_mib": torch.cuda.memory_reserved()/MIB}
        report["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--failure-child", choices=("dependency", "device", "oom"))
    args = parser.parse_args()
    try:
        if args.failure_child:
            failure_child(args.failure_child, args.cache)
        elif args.report:
            result = run_probe(args.report, args.cache)
            print(json.dumps({"status": result["status"], "report": str(args.report)}))
        else:
            parser.error("--report or --failure-child is required")
    except ProbeError as error:
        print(json.dumps({"error": error.record()}))
        sys.exit(2)
