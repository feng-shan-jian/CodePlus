"""Independent BEIR SciFact data adapter and document-level retrieval scoring.

Only prepare/check/score read qrels. Retrieval consumes runtime/<split>.json
and the exact runtime/corpus files; no questions or judgments enter the index.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parent
DATASET_ID = "beir-scifact-v1"
NAMESPACE = "beir_scifact_v1"
PINNED = {
    "corpus.jsonl": "dec31c8182f3d744c7d2c09423756fd1d17cbef75808db13ba01cc0aab4d1ac6",
    "queries.jsonl": "8ff84a7c903f722981cd8d595c022660140c51867b27608a6d4910db86080313",
    "qrels/train.tsv": "a53f2114831916c096b6c37d9e54da68cef4efdcdbd5ed46533601af972acf1d",
    "qrels/test.tsv": "0864bb985e0ca2367ba217977e72004d549054b2b06666ed9d4825ac7c21284c",
}
SPLITS = ("development", "train", "test")


def normalized_query(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def digest(content):
    return hashlib.sha256(content).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def regular_file(path):
    require(path.is_file() and path.resolve() == path.absolute(), f"Missing or redirected file: {path}")
    return path.read_bytes()


def load_source(root=ROOT):
    root = Path(root).resolve()
    lock = read_json(root / "source-lock.json")
    require(lock["dataset_id"] == DATASET_ID and lock["files"] == PINNED, "Wrong dataset source lock")
    for name, expected in PINNED.items():
        require(digest(regular_file(root / "upstream" / name)) == expected, f"Source changed: {name}")
    corpus = [json.loads(line) for line in (root / "upstream/corpus.jsonl").read_text(encoding="utf-8").splitlines()]
    queries = [json.loads(line) for line in (root / "upstream/queries.jsonl").read_text(encoding="utf-8").splitlines()]
    docs = {row["_id"]: row for row in corpus}
    query_map = {row["_id"]: row["text"] for row in queries}
    require(len(docs) == len(corpus) == 5183, "Wrong corpus inventory")
    require(len(query_map) == len(queries) == 1109, "Wrong query inventory")
    qrels = {}
    for split in ("train", "test"):
        with (root / f"upstream/qrels/{split}.tsv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        judgments = {}
        for row in rows:
            qid, doc_id, relevance = row["query-id"], row["corpus-id"], int(row["score"])
            require(qid in query_map and doc_id in docs and relevance > 0, "Invalid qrel")
            require(doc_id not in judgments.get(qid, {}), "Duplicate qrel")
            judgments.setdefault(qid, {})[doc_id] = relevance
        require(len(judgments) == {"train": 809, "test": 300}[split], f"Wrong {split} question count")
        qrels[split] = judgments
    require(not set(qrels["train"]) & set(qrels["test"]), "Train/test query overlap")
    test_text = {normalized_query(query_map[qid]) for qid in qrels["test"]}
    qrels["development"] = {qid: rel for qid, rel in qrels["train"].items()
                            if normalized_query(query_map[qid]) not in test_text}
    return docs, query_map, qrels


def artifacts(root=ROOT):
    docs, queries, qrels = load_source(root)
    fingerprint = digest(encode({"dataset_id": DATASET_ID, "files": PINNED}))
    outputs, documents = {}, []
    for doc_id, doc in docs.items():
        require(re.fullmatch(r"[0-9]+", doc_id), "Unsafe document ID")
        path = f"runtime/corpus/SF-{doc_id}.md"
        content = (f"# {doc['title']}\n\n{doc['text']}\n").encode("utf-8")
        outputs[path] = content
        documents.append({"doc_id": doc_id, "path": path, "sha256": digest(content)})
    common = {"dataset_id": DATASET_ID, "dataset_fingerprint": fingerprint,
              "namespace": NAMESPACE, "corpus_fingerprint": digest(encode(documents))}
    outputs["dataset.json"] = encode({**common, "documents": len(docs), "queries": len(queries),
        "splits": {s: {"queries": len(q), "qrels": sum(map(len, q.values()))} for s, q in qrels.items()},
        "development_policy": {"source": "official train minus normalized text overlap with test",
            "normalization": "NFKC, casefold, collapse whitespace",
            "excluded_train_query_ids": sorted(set(qrels["train"]) - set(qrels["development"]))},
        "relevance_unit": "document", "answer_evaluation": "not_applicable",
        "source_lock": "source-lock.json", "state_directory": ".state", "results_directory": "runs"})
    for split, judgments in qrels.items():
        selected = [{"id": qid, "query": text} for qid, text in queries.items() if qid in judgments]
        outputs[f"runtime/{split}.json"] = encode({**common, "split": split, "documents": documents, "questions": selected})
    return outputs


def prepare(root=ROOT):
    root = Path(root).resolve()
    outputs = artifacts(root)
    # Never absorb an existing index or foreign files into the import directory.
    corpus = root / "runtime/corpus"
    require(corpus.resolve() == corpus, "Corpus directory is redirected")
    if corpus.exists():
        existing = {p.relative_to(root).as_posix() for p in corpus.rglob("*") if p.is_file()}
        require(existing <= outputs.keys(), "Unexpected files in SciFact corpus; preserve and inspect them")
    for name, content in outputs.items():
        path = root / name
        if path.exists():
            require(regular_file(path) == content, f"Refuse to overwrite changed artifact: {name}")
        else:
            require(path.resolve() == path, f"Redirected output: {name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return check(root)


def check(root=ROOT):
    root = Path(root).resolve()
    outputs = artifacts(root)
    for name, content in outputs.items():
        require(regular_file(root / name) == content, f"Prepared data differs: {name}")
    actual = {p.relative_to(root).as_posix() for p in (root / "runtime/corpus").rglob("*") if p.is_file()}
    require(actual == {name for name in outputs if name.startswith("runtime/corpus/")}, "Mixed corpus files")
    return {"status": "ok", **read_json(root / "dataset.json"),
            "source_files_verified": len(PINNED), "retrieval_evaluation": "not_executed"}


def validate_report(report, runtime):
    for key in ("dataset_id", "dataset_fingerprint", "namespace", "corpus_fingerprint", "split"):
        require(report.get(key) == runtime[key], f"Wrong result identity: {key}")
    protocol = report.get("protocol", {})
    require(protocol.get("unit") in {"document", "chunk"}, "Declare document or chunk results")
    require(type(protocol.get("top_k")) is int and protocol["top_k"] >= 10, "Result depth must be >= 10")
    records = report.get("records")
    require(isinstance(records, list) and len(records) == len(runtime["questions"]), "Missing or extra query records")
    doc_ids = {doc["doc_id"] for doc in runtime["documents"]}
    ranked, errors, duplicate_count = {}, 0, 0
    for expected, record in zip(runtime["questions"], records):
        require(all(record.get(k) == expected[k] for k in ("id", "query")), "Changed, reordered or duplicate query")
        require(record.get("status") in {"ok", "error"}, "Unknown query status")
        hits = record.get("hits")
        require(isinstance(hits, list) and len(hits) <= protocol["top_k"], "Invalid hit count")
        if record["status"] == "error":
            require(not hits, "Failed query must have empty hits")
            errors += 1
        documents, previous = {}, math.inf
        for hit in hits:
            doc_id, score = hit.get("doc_id"), hit.get("score")
            require(doc_id in doc_ids, f"Foreign document ID: {doc_id}")
            require(type(score) in (int, float) and math.isfinite(score), "Score must be finite")
            require(score <= previous, "Hits must be in descending score order")
            previous = score
            if doc_id in documents:
                require(protocol["unit"] == "chunk", "Duplicate document hit")
                duplicate_count += 1
                continue
            # Unique scores preserve the submitted order, including score ties.
            documents[doc_id] = float(protocol["top_k"] - len(documents))
        ranked[expected["id"]] = documents
    return ranked, errors, duplicate_count


def measure(qrels, ranked):
    import ir_measures as ir
    measures = [ir.Success@5, ir.Success@10, ir.R@5, ir.R@10, ir.RR@10, ir.nDCG@10, ir.P@5, ir.P@10]
    provider = ir.providers.registry["pytrec_eval"]
    # This provider supports unbounded RR only; passing RR@10 directly silently
    # ignores the cutoff. Score RR separately on the first ten ranked documents.
    evaluator = provider.evaluator([m for m in measures if m != ir.RR@10], qrels)
    # Keep the full qrels denominator, including queries that failed or returned nothing.
    per_query = {qid: {str(m): 0.0 for m in measures} for qid in qrels}
    for value in evaluator.iter_calc(ranked):
        per_query[value.query_id][str(value.measure)] = value.value
    top10 = {qid: dict(sorted(documents.items(), key=lambda item: item[1], reverse=True)[:10])
             for qid, documents in ranked.items()}
    for value in provider.evaluator([ir.RR], qrels).iter_calc(top10):
        per_query[value.query_id]["RR@10"] = value.value
    aggregate = {str(m): sum(row[str(m)] for row in per_query.values()) / len(per_query) for m in measures}
    return aggregate, per_query


def score(report, root=ROOT):
    check(root)
    split = report.get("split")
    require(split in SPLITS, "Unknown split")
    runtime = read_json(Path(root) / f"runtime/{split}.json")
    ranked, errors, duplicates = validate_report(report, runtime)
    _, _, qrels = load_source(root)
    aggregate, per_query = measure(qrels[split], ranked)
    return {"dataset_id": DATASET_ID, "dataset_fingerprint": runtime["dataset_fingerprint"],
            "split": split, "namespace": NAMESPACE, "questions": len(per_query), "request_errors": errors,
            "status": "complete_with_errors" if errors else "complete", "metrics": aggregate,
            "per_query": per_query, "metric_library": {name: importlib.metadata.version(name)
                for name in ("ir-measures", "pytrec-eval-terrier")},
            "document_deduplication": "first occurrence in submitted score order",
            "duplicate_chunk_hits_removed": duplicates, "answer_evaluation": "not_evaluated"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "score"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--run-name", default="baseline")
    args = parser.parse_args()
    if args.action in {"prepare", "check"}:
        result = prepare() if args.action == "prepare" else check()
    else:
        require(args.input is not None, "--input is required")
        require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.run_name), "Unsafe run name")
        raw = args.input.read_bytes()
        report = json.loads(raw)
        result = score(report)
        output = ROOT / "runs" / result["split"] / args.run_name
        require(output.resolve() == output and not output.exists(), "Run output already exists or is redirected")
        output.mkdir(parents=True)
        (output / "retrieval.json").write_bytes(raw)
        result["input_sha256"] = digest(raw)
        (output / "metrics.json").write_bytes(encode(result))
        result = {k: v for k, v in result.items() if k != "per_query"}
        result["output"] = str(output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("request_errors", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
