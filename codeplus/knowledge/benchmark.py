"""Versioned local corpus regression checks through the normal knowledge service."""

from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
from time import perf_counter
import uuid

from codeplus.validator import validate_knowledge
from .evaluate import _write, evidence_recall, latency
from .models import fingerprint
from .service import KnowledgeService


def load_dataset(path):
    root = Path(path).resolve()
    manifest = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
    questions = json.loads((root / "questions.json").read_text(encoding="utf-8"))["questions"]
    texts = {}
    for doc in manifest["documents"]:
        for file_key, hash_key in (("path", "sha256"), ("text_path", "text_sha256")):
            if sha256((root / doc[file_key]).read_bytes()).hexdigest() != doc[hash_key]:
                raise ValueError(f"Corpus checksum mismatch: {doc['id']} {file_key}")
        texts[doc["id"]] = (root / doc["text_path"]).read_bytes().decode("utf-8")
    if len({q["id"] for q in questions}) != len(questions):
        raise ValueError("Duplicate question IDs")
    for q in questions:
        if q["answerable"] != bool(q["gold"]) or (not q["answerable"] and not q["no_answer_reason"]):
            raise ValueError(f"Answer/evidence mismatch: {q['id']}")
        for g in q["gold"] + q.get("supporting_context", []):
            text = texts[g["source_id"]]
            if (not 0 <= g["char_start"] < g["char_end"] <= len(text)
                    or text[g["char_start"]:g["char_end"]] != g["quote"] or not g["quote"].strip()):
                raise ValueError(f"Evidence quote mismatch: {q['id']}")
    return manifest, questions, fingerprint({"manifest": manifest, "questions": questions})


def score(questions, records, top_k):
    if [q["id"] for q in questions] != [r["id"] for r in records]:
        raise ValueError("Run questions do not match the dataset")
    groups = {}
    for q, record in zip(questions, records):
        if record["query"] != q["query"]:
            raise ValueError(f"Run query changed: {q['id']}")
        gold = [{**g, "file": g["source_id"]} for g in q["gold"]]
        hits = [{**h, "file": h["source_id"]} for h in record["hits"]]
        record["evidence"] = evidence_recall(gold, hits, top_k, ignore_whitespace=True)
        groups.setdefault(q["track"], []).append(record)
    summary = {}
    for track, rows in groups.items():
        evidence = [r["evidence"] for r in rows]
        total, covered = sum(e["total"] for e in evidence), sum(e["covered"] for e in evidence)
        summary[track] = {
            "questions": len(rows), "fully_covered": sum(e["recall"] == 1 for e in evidence) if total else None,
            "evidence_ranges": total, "covered_ranges": covered, "evidence_recall": covered / total if total else None,
            "request_errors": sum(r["status"] == "error" for r in rows),
            "latency": latency([r["elapsed_ms"] for r in rows if r["status"] != "error"]),
            "answer_correctness": "not_evaluated", "refusal_correctness": "not_evaluated",
        }
    return summary


def run(config, args):
    overrides = {field: getattr(args, option) for option, field in (
        ("mode", "retrieval_mode"), ("candidates", "retrieval_candidates"), ("rrf_k", "rrf_k")
    ) if getattr(args, option, None) is not None}
    if overrides and (args.check or args.replay):
        raise ValueError("--mode, --candidates and --rrf-k require --kb-id; --replay/--check do not run retrieval")
    root = args.dataset.resolve()
    manifest, questions, dataset_hash = load_dataset(root)
    if args.check:
        return {"status": "ok", "dataset_sha256": dataset_hash, "documents": len(manifest["documents"]),
                "questions": len(questions)}
    if args.replay:
        report = json.loads(args.replay.read_text(encoding="utf-8"))
        if report["dataset_sha256"] != dataset_hash:
            raise ValueError("Replay belongs to a different dataset version")
        report["replayed_from"] = str(args.replay.resolve())
    else:
        config = replace(config, enabled=True, managed_local=config.managed_local or args.managed_local, **overrides)
        validate_knowledge(asdict(config))
        report = {"dataset_id": manifest["id"], "dataset_version": manifest["version"],
                  "dataset_sha256": dataset_hash, "started_at": datetime.now(timezone.utc).isoformat(),
                  "protocol": manifest["protocol"], "records": [], "answer_model_called": False,
                  "retrieval_request": {"mode": config.retrieval_mode, "candidates": config.retrieval_candidates,
                                        "rrf_k": config.rrf_k, "top_k": manifest["protocol"]["top_k"]}}
        report["implementation_sha256"] = fingerprint({name: (Path(__file__).parent / name).read_bytes().hex()
            for name in ("benchmark.py", "evaluate.py", "service.py", "embedding.py", "milvus_store.py", "documents.py",
                         "retrieval.py", "models.py", "__init__.py", "__main__.py", "../config.py", "../validator.py")})
        with closing(KnowledgeService(config)) as service:
            before = service.status(args.kb_id)
            documents = [d for d in before["documents"] if not d["removed"]]
            by_hash = {d["sha256"]: d for d in manifest["documents"]}
            if (before["state"] != "READY" or any(d["state"] != "READY" for d in documents)
                    or len(documents) != len(by_hash) or {d["content_hash"] for d in documents} != by_hash.keys()):
                raise ValueError("Knowledge base must contain exactly the dataset's ready source versions")
            by_doc = {d["id"]: by_hash[d["content_hash"]] for d in documents}
            service.prepare()
            saved_profile = json.loads((service.root / before["id"] / "profile.json").read_text(encoding="utf-8"))
            report.update(kb_id=before["id"], kb_revision=before["revision"], profile=saved_profile,
                          milvus_version=service.store.check_health(),
                          dependencies={name: version(name) for name in ("pymilvus", "torch", "transformers", "pypdf", "python-docx", "llama-index-core")})
            for q in questions:
                start = perf_counter()
                record = {"id": q["id"], "query": q["query"], "status": "ok", "hits": []}
                try:
                    result = service.search(before["id"], q["query"], manifest["protocol"]["top_k"])
                    record["retrieval"] = result.retrieval
                    for hit in result.hits:
                        record["hits"].append({**asdict(hit), "source_id": by_doc[hit.doc_id]["id"],
                                               "file": by_doc[hit.doc_id]["file"]})
                except Exception as exc:
                    record.update(status="error", hits=[], error=str(exc))
                record["elapsed_ms"] = (perf_counter() - start) * 1000
                report["records"].append(record)
            if service.status(args.kb_id)["revision"] != before["revision"]:
                raise ValueError("Knowledge base changed during the benchmark; discard this run")
    report["summary"] = score(questions, report["records"], manifest["protocol"]["top_k"])
    report["status"] = "query_failures" if any(r["status"] == "error" for r in report["records"]) else "ok"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    output = root / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    _write(output / "report.json", report)
    lines = ["# 检索评测结果", "", f"数据集：{manifest['id']} {manifest['version']}；状态：{report['status']}。",
             "", "仅检查指定原文依据覆盖，不代表模型回答正确率。追问只输入最后一句；无答案题不自动判拒答通过。", ""]
    request = report.get("retrieval_request")
    if request:
        lines += [f"请求检索：{request['mode']}；候选数 N={request['candidates']}；RRF={request['rrf_k']}；Top-K={request['top_k']}。", ""]
    lines += ["| 分组 | 题数 | 找齐依据 | 请求失败 |", "|---|---:|---:|---:|"]
    for name, group in report["summary"].items():
        lines.append(f"| {name} | {group['questions']} | {group['fully_covered'] if group['fully_covered'] is not None else '不自动评分'} | {group['request_errors']} |")
    lines += ["", "| 题号 | 实际策略 | 候选数 | RRF | 覆盖依据 | 首条来源 |", "|---|---|---:|---:|---|---|"]
    for r in report["records"]:
        e = r["evidence"]
        retrieval = r.get("retrieval", {})
        constant = retrieval.get("rrf_k", "未记录")
        lines.append(f"| {r['id']} | {retrieval.get('mode', '未记录')} | {retrieval.get('candidates', '未记录')} | "
                     f"{constant if constant is not None else '—'} | {e['covered']}/{e['total']} | "
                     f"{r['hits'][0]['source_id'] if r['hits'] else r['status']} |")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": report["status"], "report": str(output / "report.json"), "summary": report["summary"]}
