"""Deterministic, lossless CodePlus adapter for a pinned MultiHop-RAG release."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED = "codeplus-multihop-tiers-v1"
PINNED_HASHES = {
    "upstream/MultiHopRAG.json": "03cfb4926461f868684903aadc8024447bdda5bb3f6804741424cce338515bff",
    "upstream/corpus.json": "20b61b5ab84de84a927420c5d265b7ec8d859ae49980699958a787ade9e4d28f",
    "upstream/retrieval_evaluate.py": "6734516f0d5f9385076f76bf4e08d54968541fdb05f519f1cf687c0b65ae0f90",
    "upstream/qa_evaluate.py": "a518bc37d94d2e84cc9012b41a758567e308dceea92aba14cda44a1364626ae7",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_upstream(root=ROOT):
    lock = read(root / "source-lock.json")
    require(lock["dataset_revision"] == "71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82", "Dataset revision changed")
    require(lock["evaluator_revision"] == "c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8", "Evaluator revision changed")
    require(set(lock["files"]) == set(PINNED_HASHES), "Upstream file inventory changed")
    for name, digest in PINNED_HASHES.items():
        require(lock["files"][name]["sha256"] == digest, f"Source lock changed: {name}")
        require(sha256((root / name).read_bytes()).hexdigest() == digest, f"Upstream checksum mismatch: {name}")
    return lock


def make_tiers(questions):
    counts = Counter(q["track"] for q in questions)
    ranked = {kind: sorted((q["id"] for q in questions if q["track"] == kind),
                          key=lambda qid: (sha256(f"{SEED}:{qid}".encode()).hexdigest(), qid))
              for kind in sorted(counts)}
    tiers = {}
    previous = set()
    for name, size in (("lite", 50), ("medium", 200), ("full", len(questions))):
        quotas = {kind: size * count // len(questions) for kind, count in counts.items()}
        remainder_order = sorted(counts, key=lambda kind: (-(size * counts[kind] % len(questions)), kind))
        for kind in remainder_order[:size - sum(quotas.values())]:
            quotas[kind] += 1
        selected = {qid for kind in counts for qid in ranked[kind][:quotas[kind]]}
        require(previous <= selected, "Tier selection is not nested")
        previous = selected
        tiers[name] = {"questions": size, "question_types": dict(sorted(quotas.items())),
                       "question_ids": [q["id"] for q in questions if q["id"] in selected]}
    return {"version": 1, "seed": SEED, "sampling": "largest-remainder quotas by official question_type; SHA256 rank per type",
            "corpus": "All 609 documents for every tier", "split": "Local nested samples, not an official train/test split",
            "tiers": tiers}


def build(root=ROOT):
    lock = verify_upstream(root)
    corpus = read(root / "upstream/corpus.json")
    raw_questions = read(root / "upstream/MultiHopRAG.json")
    require(len(corpus) == 609 and len(raw_questions) == 2556, "Unexpected upstream release size")
    require(len({row["url"] for row in corpus}) == len(corpus), "Duplicate corpus URL")
    require(len({q["query"] for q in raw_questions}) == len(raw_questions), "Duplicate query")
    artifacts, documents, by_url = {}, [], {}
    for index, article in enumerate(corpus):
        doc_id = f"D{index + 1:04d}"
        prefix = (f"# {article['title']}\n\nSource: {article['source']}\n"
                  f"Published: {article['published_at']}\nURL: {article['url']}\n\n")
        content = (prefix + article["body"] + "\n").encode("utf-8")
        path = f"corpus/{doc_id}.md"
        digest = sha256(content).hexdigest()
        artifacts[path] = content
        documents.append({"id": doc_id, "file": f"{doc_id}.md", "path": path, "sha256": digest,
                          "text_path": path, "text_sha256": digest, "upstream_index": index,
                          "provenance": {key: value for key, value in article.items() if key != "body"}})
        by_url[article["url"]] = (doc_id, article, len(prefix))
    questions = []
    for index, original in enumerate(raw_questions):
        qid = f"MH-{index + 1:04d}"
        gold = []
        for fact_index, evidence in enumerate(original["evidence_list"]):
            doc_id, article, prefix_length = by_url[evidence["url"]]
            fact = evidence["fact"]
            start = article["body"].find(fact)
            require(bool(fact.strip()) and start >= 0, f"Evidence is not an exact source substring: {qid}")
            start += prefix_length
            gold.append({"id": f"{qid}-E{fact_index + 1}", "source_id": doc_id,
                         "char_start": start, "char_end": start + len(fact), "quote": fact})
        answerable = original["question_type"] != "null_query"
        require(answerable == bool(gold), f"Answerability mismatch: {qid}")
        questions.append({"id": qid, "upstream_index": index, "query": original["query"],
                          "reference_answer": original["answer"], "track": original["question_type"],
                          "answerable": answerable, "gold": gold,
                          "no_answer_reason": "" if answerable else "Official null_query: insufficient information in the corpus."})
    manifest = {"id": "multihop-rag-official", "version": "1.0.0", "language": "en",
                "upstream": {key: lock[key] for key in ("dataset", "dataset_revision", "evaluator_revision")},
                "protocol": {"top_k": 10, "scoring": "source-content-v1", "scope": "corpus_retrieval",
                             "answer_model": "not_called", "official_scoring": "score.py"},
                "documents": documents}
    artifacts.update({"dataset.json": json_bytes(manifest), "questions.json": json_bytes({"questions": questions}),
                      "tiers.json": json_bytes(make_tiers(questions))})
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify generated bytes without rewriting")
    args = parser.parse_args()
    artifacts = build()
    require({p.name for p in (ROOT / "corpus").glob("*")} <= {Path(p).name for p in artifacts if p.startswith("corpus/")},
            "Unexpected corpus files; inspect before regenerating")
    for name, content in artifacts.items():
        path = ROOT / name
        if args.check:
            require(path.read_bytes() == content, f"Adapter differs from official release: {name}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print(json.dumps({"status": "ok", "generated_files": len(artifacts), "questions": 2556, "documents": 609}))


if __name__ == "__main__":
    main()
