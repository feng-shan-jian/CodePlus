"""Evaluation-only dataset validation and stable fingerprints; standard library only."""
from hashlib import sha256
import json
from pathlib import Path


def fingerprint(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


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
