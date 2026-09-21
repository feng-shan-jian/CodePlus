"""Load the prepared query-only input, without opening any scoring-side files.

This internal boundary is not an Agent or a retrieval runner. Preparing the
manifest and verifying gold belong to the separate evaluation/scoring process.
"""
from hashlib import sha256
import json
from pathlib import Path
import re


INPUT_PATH = Path(__file__).with_name("runtime-inputs.json")
INPUT_SHA256 = "8db278a920b290ea9d6f63d5bea473960fb20c26fcb41c5078dececa421d4d0d"
SUITE_ROOT = Path(__file__).resolve().parents[3] / "eval" / "RAG-eval"


def validate_runtime_inputs(value):
    """Reject extra fields and nested payloads instead of silently stripping them."""
    if not isinstance(value, dict) or set(value) != {"questions", "corpus_paths"}:
        raise ValueError("Runtime input must contain only questions and corpus_paths")
    questions, paths = value["questions"], value["corpus_paths"]
    if not isinstance(questions, list) or not questions:
        raise ValueError("Runtime questions must be a nonempty list")
    ids = []
    for question in questions:
        if not isinstance(question, dict) or set(question) != {"id", "query"}:
            raise ValueError("Runtime question must contain only id and query")
        if any(not isinstance(question[key], str) or not question[key].strip() for key in ("id", "query")):
            raise ValueError("Runtime id and query must be nonempty strings")
        ids.append(question["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate runtime question IDs")
    if not isinstance(paths, list) or not paths:
        raise ValueError("Runtime corpus_paths must be a nonempty list")
    if any(not isinstance(path, str) or re.fullmatch(r"corpus/D[0-9]{4}\.md", path) is None for path in paths):
        raise ValueError("Runtime import paths must name only corpus/D0000.md files")
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate runtime corpus paths")


def load_runtime_inputs(suite_root=SUITE_ROOT, question_ids=None):
    """Return only IDs/queries and resolved corpus paths, preserving official order.

    ``question_ids`` is an optional ID list selected by the experiment harness.
    Neither the runtime manifest nor this loader reads answer/gold/tier metadata.
    """
    raw = INPUT_PATH.read_bytes()
    value = json.loads(raw)
    validate_runtime_inputs(value)
    if sha256(raw).hexdigest() != INPUT_SHA256:
        raise ValueError("Frozen runtime input checksum mismatch")
    questions = value["questions"]
    if question_ids is not None:
        if (not isinstance(question_ids, (list, tuple)) or not question_ids
                or any(not isinstance(item, str) for item in question_ids)
                or len(question_ids) != len(set(question_ids))):
            raise ValueError("Question selection must contain unique, nonempty IDs")
        selected = set(question_ids)
        if selected - {question["id"] for question in questions}:
            raise ValueError("Unknown runtime question IDs")
        questions = [question for question in questions if question["id"] in selected]
    root = Path(suite_root).resolve()
    corpus = root / "corpus"
    # Do not accept a corpus directory or file symlink into the scoring inputs.
    if corpus.resolve() != corpus:
        raise ValueError("Runtime corpus directory must not redirect outside its location")
    paths = []
    for relative in value["corpus_paths"]:
        path = root / relative
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"Runtime corpus path is missing or redirected: {relative}")
        paths.append(str(path))
    return {"questions": questions, "corpus_paths": paths}
