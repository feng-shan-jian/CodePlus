"""Offline native report replay, preserving the legacy coverage/report contract.

The official scorer remains score.py and its pinned upstream functions. This
module contains only the native interval coverage/latency report compatibility.
No online service, model, configuration, or host imports are needed.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import uuid

from dataset_io import fingerprint, load_dataset


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


def _write(path, value):
    # PyMilvus 3.0.2 describes Function input/output names as protobuf repeated containers.
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=list), encoding="utf-8")


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


def replay(root, replay_path, question_ids=None):
    root = Path(root).resolve()
    replay_path = Path(replay_path)
    manifest, questions, dataset_hash = load_dataset(root)
    selection = None
    requested_ids = question_ids
    if requested_ids is not None:
        if not requested_ids or len(set(requested_ids)) != len(requested_ids):
            raise ValueError("Question selection must contain unique, nonempty IDs")
        requested = set(requested_ids)
        unknown = requested - {q["id"] for q in questions}
        if unknown:
            raise ValueError(f"Unknown question IDs: {', '.join(sorted(unknown))}")
        selected = [q for q in questions if q["id"] in requested]
        if len(selected) != len(questions):
            selection = {"source_dataset_sha256": dataset_hash, "total_questions": len(questions),
                         "question_ids": [q["id"] for q in selected]}
            dataset_hash = fingerprint(selection)
        questions = selected
    report = json.loads(replay_path.read_text(encoding="utf-8"))
    if report["dataset_sha256"] != dataset_hash:
        raise ValueError("Replay belongs to a different dataset version")
    report["replayed_from"] = str(replay_path.resolve())
    report["summary"] = score(questions, report["records"], manifest["protocol"]["top_k"])
    report["status"] = "query_failures" if any(r["status"] == "error" for r in report["records"]) else "ok"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    output = root / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:6])
    output.mkdir(parents=True)
    _write(output / "report.json", report)
    lines = ["# 检索评测结果", "", f"数据集：{manifest['id']} {manifest['version']}；状态：{report['status']}。",
             "", "仅检查指定原文依据覆盖，不代表模型回答正确率；无答案题不自动判拒答通过。", ""]
    if selection is not None:
        lines += [f"本次固定子集：{len(questions)}/{selection['total_questions']} 题；完整语料不变。题目集合指纹：{dataset_hash}。", ""]
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--question-ids", nargs="+", help="Frozen subset; official question order is retained")
    args = parser.parse_args()
    try:
        result = replay(args.dataset, args.replay, args.question_ids)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 1
    except Exception as exc:
        print(f"knowledge: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
