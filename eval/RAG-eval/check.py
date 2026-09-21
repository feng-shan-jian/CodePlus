"""Offline integrity and split checks for the sole maintained RAG quality suite."""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
from codeplus.knowledge.benchmark import load_dataset


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_file(spec):
    path = (ROOT / spec["path"]).resolve()
    require(path.is_relative_to(ROOT), "Fixture escapes suite directory")
    require(sha256(path.read_bytes()).hexdigest() == spec["sha256"], f"Fixture changed: {spec['path']}")
    return path


def check():
    suite = read(ROOT / "suite.json")
    questions = []
    family_splits = defaultdict(set)
    source_splits = defaultdict(set)
    details = []
    for entry in suite["datasets"]:
        directory = ROOT / entry["path"]
        manifest, rows, fingerprint = load_dataset(directory)
        require(len(rows) == entry["questions"], f"Question count changed: {entry['name']}")
        require(len(manifest["documents"]) == entry["documents"], f"Document count changed: {entry['name']}")
        require(len({d["id"] for d in manifest["documents"]}) == len(manifest["documents"]), "Duplicate document IDs")
        docs = {d["id"]: d for d in manifest["documents"]}
        rubrics = read(directory / "rubrics.json")["rubrics"]
        require([r["question_id"] for r in rubrics] == [q["id"] for q in rows], "Rubric/question mismatch")
        for q, rubric in zip(rows, rubrics):
            require(q["split"] in ("development", "holdout"), "Unknown split")
            require(q["reference_answer"].strip(), f"Missing answer: {q['id']}")
            require(rubric["required_answer"] == q["reference_answer"], f"Stale rubric answer: {q['id']}")
            require(rubric["required_evidence"] == [g["id"] for g in q["gold"]], f"Broken rubric evidence: {q['id']}")
            require(len({g["id"] for g in q["gold"]}) == len(q["gold"]), "Duplicate evidence IDs")
            family_splits[q["family_id"]].add(q["split"])
            for gold in q["gold"]:
                source_splits[docs[gold["source_id"]]["sha256"]].add(q["split"])
            if entry["protocol"] == "provided_context_only":
                require(q.get("context_sets"), "Context case missing assigned sources")
                for context in q["context_sets"].values():
                    require(context and set(context) <= docs.keys(), "Unknown context document")
                    require({g["source_id"] for g in q["gold"]} <= set(context), "Variant drops required positive evidence")
            questions.append(q)
        details.append({"name": entry["name"], "questions": len(rows), "documents": len(docs), "dataset_sha256": fingerprint})
    require(len(questions) == suite["single_questions"] == 300, "Expected 300 unique static questions")
    require(len({q["id"] for q in questions}) == 300, "Duplicate question IDs across datasets")
    require(len({q["query"] for q in questions}) == 300, "Exact duplicate queries")
    splits = Counter(q["split"] for q in questions)
    require(splits == {"development": 60, "holdout": 240}, "Static split count changed")
    require(all(len(v) == 1 for v in family_splits.values()), "Question family crosses development/holdout")
    require(all(len(v) == 1 for v in source_splits.values()), "Exact gold source reused across splits")

    sessions = read(ROOT / "sessions.json")["sessions"]
    require(len(sessions) == 20 and len({s["id"] for s in sessions}) == 20, "Expected 20 sessions")
    require(Counter(s["split"] for s in sessions) == {"development": 4, "holdout": 16}, "Session split changed")
    for case in sessions:
        require(len(case["turns"]) == 3, "Session must have three actual turns")
        sources = {d["id"]: check_file(d).read_text(encoding="utf-8") for d in case["documents"]}
        require(case["reset_session_before_case"] is True, "Session reset required")
        for turn in case["turns"]:
            require(turn["query"] and turn["reference_answer"], "Incomplete session turn")
            for evidence in turn["evidence"]:
                text = sources[evidence["source_id"]]
                require(text[evidence["char_start"]:evidence["char_end"]] == evidence["quote"], "Session evidence changed")
    workflows = read(ROOT / "workflows.json")["workflows"]
    require(len(workflows) == 20 and len({w["id"] for w in workflows}) == 20, "Expected 20 workflows")
    require(Counter(w["split"] for w in workflows) == {"development": 4, "holdout": 16}, "Workflow split changed")
    require(Counter(w["category"] for w in workflows) == read(ROOT / "workflows.json")["expected_counts"], "Workflow coverage changed")
    for case in workflows:
        for fixture in case["fixtures"].values():
            check_file(fixture)
        require(case["setup"] and case["steps"] and case["teardown"], "Incomplete workflow")
        for step in case["steps"]:
            if "fixture" in step:
                require(step["fixture"] in case["fixtures"], "Unknown workflow fixture")
    _, smoke, _ = load_dataset(ROOT / "smoke")
    require(len(smoke) == 32, "Smoke count changed")
    by_id = {q["id"]: q for q in questions}
    for q in smoke:
        full = by_id["O-" + q["id"]]
        require(full["split"] == "development" and full["query"] == q["query"] and full["reference_answer"] == q["reference_answer"], "Smoke is not a development subset")
    # Include evaluator-side rules and sequences, which the existing retrieval
    # fingerprint intentionally does not cover, in the full suite identity.
    artifacts = ["suite.json", "source-lock.json", "quality-review.json", "sessions.json", "workflows.json", "check.py", "run.ps1"]
    artifacts += [entry["path"] + "/rubrics.json" for entry in suite["datasets"]]
    artifact_hashes = {path: sha256((ROOT / path).read_bytes()).hexdigest() for path in artifacts}
    suite_identity = {"datasets": details, "artifacts": artifact_hashes}
    suite_sha256 = sha256(json.dumps(suite_identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    retrieval_rows = [q for q in questions if not q.get("context_sets")]
    require(len(retrieval_rows) == 250, "Retrieval/context protocol counts changed")
    require(sum(bool(q["gold"]) for q in retrieval_rows) == 247, "Retrieval evidence denominator changed")
    return {"status": "passed", "scope": "offline_dataset_integrity_only", "questions": 300,
            "suite_sha256": suite_sha256, "artifact_sha256": artifact_hashes,
            "retrieval_questions": 250, "retrieval_questions_with_gold": 247,
            "provided_context_questions": 50,
            "source_mix": suite["source_mix"], "splits": dict(splits), "scenarios": dict(Counter(q["track"] for q in questions)),
            "sessions": 20, "session_turns": 60, "workflows": 20, "datasets": details,
            "model_called": False, "database_called": False, "independent_human_review": "pending",
            "system_evaluation": suite["execution_status"]}


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=2))
