"""Offline stage attribution for a complete SciFact retrieval-only run."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import statistics

import scifact_suite as suite
from runtime_inputs import INPUT_HASHES


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def window_coverage(records, qrels, k):
    """Cut the original chunk window BEFORE mapping to unique documents."""
    found, fully, hit, recall, returned, duplicate_slots = 0, 0, 0, 0.0, 0, 0
    per_query = {}
    for row in records:
        hits = row["hits"][:k]
        docs = {h["doc_id"] for h in hits}
        relevant = set(qrels[row["id"]])
        covered = docs & relevant
        found += len(covered)
        fully += covered == relevant
        hit += bool(covered)
        recall += len(covered) / len(relevant)
        returned += len(hits)
        duplicate_slots += len(hits) - len(docs)
        per_query[row["id"]] = {"covered": sorted(covered), "required": len(relevant)}
    return {"questions": len(records), "hit": hit/len(records), "macro_recall": recall/len(records),
            "covered_qrels": found, "total_qrels": sum(map(len, qrels.values())),
            "micro_recall": found/sum(map(len, qrels.values())), "fully_covered_questions": fully,
            "returned_chunks": returned, "duplicate_document_slots": duplicate_slots}, per_query


def project(question, candidates, chunk_docs, ok, chunk_spans=None):
    return {**question, "status": "ok" if ok else "error",
        "hits": [{"doc_id": chunk_docs[c["chunk_id"]], "score": float(len(candidates)-i),
                  "chunk_id": c["chunk_id"], "original_score": c.get("score"),
                  "source_spans": c.get("source_spans", (chunk_spans or {}).get(c["chunk_id"], {}).get("source_spans", []))}
                 for i, c in enumerate(candidates)] if ok else []}


def summarize(source, run_name):
    suite.check()
    protocol = suite.read_json(source / "protocol.json")
    completion = suite.read_json(source / "completion.json")
    suite.require(completion["status"] in {"complete", "complete_with_errors"}, "Incomplete run cannot be summarized as complete")
    suite.require(completion.get("source_unchanged") is True, "Runtime source identity changed")
    suite.require(completion.get("production_control_matched") is True, "Direct production control did not pass")
    split = protocol["split"]
    suite.require(protocol["input_sha256"] == INPUT_HASHES[split], "Runtime input differs")
    runtime = suite.read_json(suite.ROOT / f"runtime/{split}.json")
    chunk_docs = suite.read_json(source / "chunk-documents.json")
    chunk_spans = suite.read_json(source / "chunk-spans.json")
    retrieval, rerank = (read_jsonl(source / name) for name in ("retrieval.jsonl", "rerank.jsonl"))
    suite.require(len(retrieval) == len(rerank) == len(runtime["questions"]), "Missing query outcomes")
    context = read_jsonl(source / "context.jsonl")
    suite.require(len(context) == len(runtime["questions"]), "Missing source selection outcomes")
    stages = {name: [] for name in ("dense50", "bm2550", "union100", "rrf24", "rerank10", "context")}
    for question, raw, final in zip(runtime["questions"], retrieval, rerank):
        suite.require(all(row[k] == question[k] for row in (raw, final) for k in ("id", "query")), "Query identity mismatch")
        trace = raw.get("trace") or raw.get("error", {}).get("trace") or {}
        branches = trace.get("branches", {})
        for branch, name in (("dense", "dense50"), ("bm25", "bm2550")):
            detail = branches.get(branch, {})
            stages[name].append(project(question, detail.get("candidates", []), chunk_docs, detail.get("status") in {"ok", "empty"}, chunk_spans))
        union = list({c["chunk_id"]: c for branch in ("dense", "bm25") for c in branches.get(branch, {}).get("candidates", [])}.values())
        stages["union100"].append(project(question, union, chunk_docs, raw["status"] == "ok", chunk_spans))
        stages["rrf24"].append(project(question, trace.get("fusion", [])[:24], chunk_docs, raw["status"] == "ok", chunk_spans))
        stages["rerank10"].append(project(question, final["hits"], chunk_docs, final["status"] == "ok"))
    for question, row in zip(runtime["questions"], context, strict=True):
        suite.require(all(row[k] == question[k] for k in ("id", "query")), "Source selection query identity mismatch")
        stages["context"].append(project(question, row["hits"], chunk_docs, row["status"] == "ok"))
    output = suite.ROOT / "runs" / split / run_name
    suite.require(not output.exists() and output.resolve() == output, "Output exists or is redirected")
    output.mkdir(parents=True)
    _, _, all_qrels = suite.load_source()
    qrels = all_qrels[split]
    summary = {"dataset_id": runtime["dataset_id"], "split": split, "questions": len(retrieval),
        "dataset_fingerprint": runtime["dataset_fingerprint"], "corpus_fingerprint": runtime["corpus_fingerprint"],
        "namespace": runtime["namespace"], "metric_library": {n: importlib.metadata.version(n)
            for n in ("ir-measures", "pytrec-eval-terrier")},
        "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "documents": len(runtime["documents"]), "indexed_chunks": len(chunk_docs),
        "answer_model_executed": False, "completion": completion,
        "source": str(source), "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.iterdir() if p.is_file()}, "stages": {}}
    details = {}
    for name, records in stages.items():
        envelope = {k: runtime[k] for k in ("dataset_id", "dataset_fingerprint", "namespace", "corpus_fingerprint", "split")}
        envelope.update(protocol={"unit": "chunk", "top_k": {"dense50": 50, "bm2550": 50, "union100": 100, "rrf24": 24, "rerank10": 10, "context": 10}[name]}, records=records)
        ranked, errors, duplicates = suite.validate_report(envelope, runtime)
        metrics, per_query = suite.measure(qrels, ranked)
        windows = {}
        for k in (5, 10, envelope["protocol"]["top_k"]):
            windows[str(k)], details[f"{name}@{k}"] = window_coverage(records, qrels, k)
        # Candidate union has no meaningful ranking; report only its full coverage.
        summary["stages"][name] = {"errors": errors, "duplicate_chunk_hits": duplicates,
            "document_metrics": metrics if name != "union100" else None,
            "original_chunk_windows": windows if name != "union100" else {"100": windows["100"]}}
        (output / f"{name}.json").write_bytes(suite.encode(envelope))
        (output / f"{name}-metrics.json").write_bytes(suite.encode({"metrics": metrics if name != "union100" else None,
            "per_query": per_query if name != "union100" else None, "windows": summary["stages"][name]["original_chunk_windows"]}))
    for k in (5, 10):
        before, after = details[f"rrf24@{k}"], details[f"rerank10@{k}"]
        gained = {qid: sorted(set(after[qid]["covered"])-set(before[qid]["covered"])) for qid in before}
        lost = {qid: sorted(set(before[qid]["covered"])-set(after[qid]["covered"])) for qid in before}
        summary[f"rerank_change_at_{k}"] = {"gained_qrels": sum(map(len, gained.values())), "lost_qrels": sum(map(len, lost.values())),
            "improved_queries": sum(len(after[q]["covered"]) > len(before[q]["covered"]) for q in before),
            "regressed_queries": sum(len(after[q]["covered"]) < len(before[q]["covered"]) for q in before)}
        details[f"rerank_changes@{k}"] = {"gained": gained, "lost": lost}
    timings = [a.get("elapsed_ms", 0)+b.get("elapsed_ms", 0) for a,b in zip(retrieval,rerank) if a["status"]==b["status"]=="ok"]
    summary["two_pass_query_ms"] = {"median": statistics.median(timings) if timings else None,
        "p95": sorted(timings)[min(len(timings)-1, int(len(timings)*0.95))] if timings else None,
        "note": "sum of retrieval and rerank in separate passes; excludes build; not Agent answer latency"}
    (output / "summary.json").write_bytes(suite.encode(summary))
    (output / "details.json").write_bytes(suite.encode(details))
    lines = ["# SciFact 检索评测", "", f"{len(retrieval)} 道 {split} 查询；5,183 文档；{len(chunk_docs)} 块。未生成最终回答。", "",
        "下表先截取原始前 5/10 个块，再映射到原文档计分，不从后续块补位。Recall 为逐题平均。", "",
        "| 阶段 | Hit@5 | Recall@5 | Hit@10 | Recall@10 |", "| --- | ---: | ---: | ---: | ---: |"]
    for name in ("dense50", "bm2550", "rrf24", "rerank10", "context"):
        w = summary["stages"][name]["original_chunk_windows"]
        lines.append(f"| {name} | {w['5']['hit']:.2%} | {w['5']['macro_recall']:.2%} | {w['10']['hit']:.2%} | {w['10']['macro_recall']:.2%} |")
    lines += ["", "候选证据保留量（每个查询和相关文档组成一条标注；下表为全体标注的覆盖率）：", "",
        "| 阶段 | 覆盖标注数 | 覆盖率 |", "| --- | ---: | ---: |"]
    for name, depth in (("union100", "100"), ("rrf24", "24"), ("rerank10", "10")):
        window = summary["stages"][name]["original_chunk_windows"][depth]
        lines.append(f"| {name} | {window['covered_qrels']}/{window['total_qrels']} | {window['micro_recall']:.2%} |")
    lines += ["", "固定相同返回数量时，精排相对 RRF 的变化：", ""]
    for k in (5, 10):
        before = summary["stages"]["rrf24"]["original_chunk_windows"][str(k)]
        after = summary["stages"]["rerank10"]["original_chunk_windows"][str(k)]
        delta = summary[f"rerank_change_at_{k}"]
        lines.append(f"- Top{k}：逐题平均 Recall 变化 {(after['macro_recall']-before['macro_recall'])*100:+.2f} 个百分点；"
            f"新增覆盖 {delta['gained_qrels']} 条标注，失去 {delta['lost_qrels']} 条；"
            f"{delta['improved_queries']} 题覆盖增加，{delta['regressed_queries']} 题覆盖减少。")
    lines += ["", f"召回失败 {completion['retrieval_errors']} 题；精排失败 {completion['rerank_errors']} 题。"
        "首题的两遍执行结果已与生产 search 开启 rerank 的直接调用对齐；运行前后源码身份一致。",
        "相关性标注是文档级的：命中相关文档的块不代表该块包含完整证据，更不代表答案正确。",
        "", "完整的文档去重指标、候选覆盖、逐题得失、错误、源码指纹和运行时见 summary.json / details.json。",
        "本结果是当前冻结代码的内部检索评测，不是 CLI/Agent 回答质量验收，也未使用 test 选择参数。"]
    (output / "report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    args = parser.parse_args()
    import re
    suite.require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.run_name), "Unsafe run name")
    print(summarize(args.source.resolve(), args.run_name))
