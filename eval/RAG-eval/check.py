"""Offline source identity, evidence alignment and tier checks; no model calls."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))
from prepare import build, read, require, verify_upstream
from dataset_io import load_dataset
from dataset_io import fingerprint


def check(tier="full"):
    artifacts = build(ROOT)
    for name, content in artifacts.items():
        require((ROOT / name).read_bytes() == content, f"Adapter differs from official release: {name}")
    require({p.relative_to(ROOT).as_posix() for p in (ROOT / "corpus").rglob("*") if p.is_file()}
            == {name for name in artifacts if name.startswith("corpus/")}, "Unexpected corpus files")
    manifest, questions, source_hash = load_dataset(ROOT)
    tiers = read(ROOT / "tiers.json")["tiers"]
    selected_ids = tiers[tier]["question_ids"]
    selection_hash = source_hash if tier == "full" else fingerprint({
        "source_dataset_sha256": source_hash, "total_questions": len(questions), "question_ids": selected_ids})
    selected = [q for q in questions if q["id"] in set(selected_ids)]
    require(len(questions) == 2556 and sum(len(q["gold"]) for q in questions) == 6084, "Official totals changed")
    return {"status": "ok", "dataset": manifest["id"], "tier": tier, "questions": len(selected),
            "documents": len(manifest["documents"]), "question_types": dict(sorted(Counter(q["track"] for q in selected).items())),
            "retrieval_scored_questions": sum(q["answerable"] for q in selected),
            "source_dataset_sha256": source_hash, "dataset_sha256": selection_hash,
            "all_tiers": {name: {k: v for k, v in spec.items() if k != "question_ids"} for name, spec in tiers.items()},
            "upstream_files_verified": len(verify_upstream(ROOT)["files"]), "exact_evidence_ranges": 6084,
            "model_evaluation": "not_executed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("lite", "medium", "full"), default="full")
    args = parser.parse_args()
    print(json.dumps(check(args.tier), ensure_ascii=False, indent=2))
