"""Query-only SciFact boundary for retrieval adapters; never reads qrels."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
NAMESPACE = "beir_scifact_v1"
BINDING = {
    "dataset_id": "beir-scifact-v1",
    "dataset_fingerprint": "98f3c573b8f153679f838d7fac07221519c258437124f2c5ef5b00fa87b3ca10",
    "namespace": NAMESPACE,
    "corpus_fingerprint": "b31c479f4edf81b808ce397d98a43422fde51d27510119d9dba3ded18264d48c",
}
INPUT_HASHES = {
    "development": "d19b5bd745588410cf9592149155479e095c5979ee2a430edbc32beb8d854de9",
    "train": "0d89bbb37c873e8942f0e41ba4f624988a50aec3c6b7350eacb3de3e24798655",
    "test": "6b7c0d87cb9cf9db9a1a1b78b330136a7b631893953f687db619ceeee3adff6c",
}


def load_runtime_inputs(split, root=ROOT):
    if split not in INPUT_HASHES:
        raise ValueError("Choose development, train or test explicitly")
    root = Path(root).resolve()
    path = root / f"runtime/{split}.json"
    if path.resolve() != path:
        raise ValueError("Redirected runtime manifest")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != INPUT_HASHES[split]:
        raise ValueError("Frozen runtime input checksum mismatch")
    value = json.loads(raw)
    corpus = root / "runtime/corpus"
    expected = {root / doc["path"] for doc in value["documents"]}
    actual = {p for p in corpus.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Mixed or incomplete SciFact corpus")
    for doc in value["documents"]:
        source = root / doc["path"]
        if source.resolve() != source or hashlib.sha256(source.read_bytes()).hexdigest() != doc["sha256"]:
            raise ValueError(f"Missing, changed or redirected corpus file: {source}")
    return value


def bind_storage(runtime, index_name, *, namespace=NAMESPACE, root=ROOT):
    """Allocate only a suite-local store; callers use the returned config verbatim.

    This does not create an index or connect to Milvus. A new retrieval adapter
    must bind this store before import and must import runtime['documents'] only.
    """
    if namespace != NAMESPACE or runtime["namespace"] != NAMESPACE:
        raise ValueError("SciFact requires its dedicated namespace")
    binding = {k: runtime.get(k) for k in BINDING}
    if binding != BINDING:
        raise ValueError("Runtime is not bound to the frozen SciFact corpus")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", index_name):
        raise ValueError("Invalid index name")
    directory = Path(root).resolve() / ".state" / index_name
    if directory.resolve() != directory:
        raise ValueError("Redirected state directory")
    marker = directory / "binding.json"
    if directory.exists():
        if (marker.resolve() != marker or not marker.is_file()
                or json.loads(marker.read_text(encoding="utf-8")) != binding):
            raise ValueError("Existing state is not bound to this SciFact corpus")
    else:
        directory.mkdir(parents=True)
        with marker.open("x", encoding="utf-8") as stream:
            json.dump(binding, stream, ensure_ascii=False, indent=2)
    data = directory / "data"
    if data.resolve() != data:
        raise ValueError("Redirected data directory")
    return {"data_dir": str(data), "namespace": namespace}


def protect_runtime_reads(split, root=ROOT):
    """Fail fast on accidental Python reads of labels or the other benchmark.

    This is an evaluation IO guard, not an operating-system security sandbox.
    Install before loading inputs, in the retrieval process, never in scoring.
    """
    if split not in INPUT_HASHES:
        raise ValueError("Unknown split")
    root = Path(root).resolve()
    denied_trees = (root / "upstream", root / "runs", root.parent / "RAG-eval")
    denied_files = {root / f"runtime/{other}.json" for other in INPUT_HASHES if other != split}

    def audit(event, args):
        if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        mode, flags = args[1], args[2]
        reading = ("r" in mode or "+" in mode) if isinstance(mode, str) else not flags & os.O_WRONLY
        if not reading:
            return
        path = Path(os.fsdecode(args[0])).resolve()
        if path in denied_files or any(path.is_relative_to(tree) for tree in denied_trees):
            raise PermissionError(f"SciFact retrieval cannot read scoring or foreign inputs: {path}")
    sys.addaudithook(audit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=tuple(INPUT_HASHES), required=True)
    args = parser.parse_args()
    protect_runtime_reads(args.split)
    inputs = load_runtime_inputs(args.split)
    print(json.dumps({"dataset_id": inputs["dataset_id"], "split": args.split,
        "documents": len(inputs["documents"]), "queries": len(inputs["questions"]),
        "namespace": inputs["namespace"], "status": "query-only inputs verified; retrieval not executed"}))
