"""Score selected official questions with pinned upstream scoring functions."""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import runpy

from check import check
from prepare import ROOT, read, require, verify_upstream


def scorer(task, root=ROOT):
    verify_upstream(root)
    name = "retrieval_evaluate.py" if task == "retrieval" else "qa_evaluate.py"
    return runpy.run_path(str(root / "upstream" / name))


def score_retrieval(report, questions, metric_fn, expected_hash):
    require(report["dataset_sha256"] == expected_hash, "Report dataset/tier fingerprint mismatch")
    require(report["protocol"]["top_k"] == 10, "Official wrapper requires Top-K=10")
    records = report["records"]
    require([r["id"] for r in records] == [q["id"] for q in questions], "Missing, extra, reordered or duplicate retrieval records")
    groups = defaultdict(list)
    errors = 0
    for record, question in zip(records, questions):
        require(record["query"] == question["query"], f"Query changed: {question['id']}")
        require(record["status"] in {"ok", "error"}, "Unknown request status")
        require(len(record["hits"]) <= 10, "Report contains more than 10 hits")
        require(all(isinstance(hit["text"], str) for hit in record["hits"]), "Hit text must be a string")
        if record["status"] == "error":
            errors += 1
        if question["track"] != "null_query":
            # Failed requests count as zero; never drop them from the denominator.
            hits = [] if record["status"] == "error" else [hit["text"] for hit in record["hits"]]
            groups[question["track"]].append((hits, [gold["quote"] for gold in question["gold"]]))
    def metrics(pairs):
        return {"questions": len(pairs), "metrics": metric_fn([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else None}
    combined = [pair for pairs in groups.values() for pair in pairs]
    return {"selected_questions": len(questions), "scored_questions": len(combined),
            "excluded_null_queries": len(questions) - len(combined), "request_errors": errors,
            "overall": metrics(combined), "by_question_type": {kind: metrics(pairs) for kind, pairs in sorted(groups.items())},
            "answer_correctness": "not_evaluated", "refusal_correctness": "not_evaluated"}


def score_answers(records, questions, metric_fn, extract_answer):
    require(isinstance(records, list), "Answers must be a JSON list")
    by_query = {record["query"]: record for record in records}
    require(len(by_query) == len(records), "Duplicate answer query")
    require(set(by_query) == {q["query"] for q in questions}, "Missing or extra answer queries")
    groups = defaultdict(list)
    errors = 0
    for question in questions:
        record = by_query[question["query"]]
        require(record.get("id", question["id"]) == question["id"], "Answer ID/query mismatch")
        status = record.get("status", "ok")
        require(status in {"ok", "error"}, "Unknown answer status")
        require(isinstance(record.get("model_answer"), str), "model_answer must be a string, including failures")
        # Gold answers and question types always come from the verified official adapter.
        prediction = "" if status == "error" else extract_answer(record["model_answer"])
        errors += status == "error"
        groups[question["track"]].append((prediction, question["reference_answer"]))
    def metrics(pairs):
        value = metric_fn([p[0] for p in pairs], [p[1] for p in pairs])[3]
        return {"questions": len(pairs), "upstream_word_overlap_accuracy": value}
    combined = [pair for pairs in groups.values() for pair in pairs]
    return {"selected_questions": len(questions), "scored_questions": len(combined), "request_errors": errors,
            "overall": metrics(combined), "by_question_type": {kind: metrics(pairs) for kind, pairs in sorted(groups.items())},
            "scoring_limit": "Upstream scoring accepts any shared lowercased whitespace-token; it is not semantic correctness.",
            "citation_correctness": "not_evaluated"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("retrieval", "answers"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tier", choices=("lite", "medium", "full"), default="full")
    parser.add_argument("--output", type=Path, help="Optional score file; must not exist")
    args = parser.parse_args()
    integrity = check(args.tier)
    selected = set(read(ROOT / "tiers.json")["tiers"][args.tier]["question_ids"])
    questions = [q for q in read(ROOT / "questions.json")["questions"] if q["id"] in selected]
    functions = scorer(args.task)
    records = read(args.input)
    if args.task == "retrieval":
        result = score_retrieval(records, questions, functions["calculate_metrics"], integrity["dataset_sha256"])
    else:
        result = score_answers(records, questions, functions["calculate_metrics"], functions["extract_answer"])
    result.update(task=args.task, tier=args.tier, dataset_sha256=integrity["dataset_sha256"],
                  evaluator_revision=read(ROOT / "source-lock.json")["evaluator_revision"],
                  input_sha256=sha256(args.input.read_bytes()).hexdigest(), model_called=False)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(output)
    print(output, end="")


if __name__ == "__main__":
    main()
