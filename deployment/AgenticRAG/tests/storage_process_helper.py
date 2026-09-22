"""Formal subprocess protocol and synthetic metadata fixtures; no Milvus claims."""

import argparse
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import runpy
import time
from uuid import uuid4

from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, resolve_run
from agentic_rag.domain import IndexState, KnowledgeRevision, RagError
from agentic_rag.storage import Catalog

HASH = "a" * 64


def config():
    values = runpy.run_path(str(Path(__file__).with_name("test_configuration.py")))["example_config"]()
    return KnowledgeConfig.model_validate_json(json.dumps(values))


def snapshot():
    return ProcessingSnapshot.capture(uuid4(), config())


def seed_ready(catalog, kb_id):
    """Synthetic metadata only; deliberately NOT a production publication API."""
    snap = snapshot()
    with catalog.begin_mutation(kb_id, snap, HASH) as owner:
        revision = KnowledgeRevision(revision_id=uuid4(), kb_id=kb_id, manifest_hash=HASH,
                                     base_revision_id=catalog.get_batch(owner.token.batch_id).base_revision_id,
                                     processing_snapshot_id=snap.snapshot_id, index_state=IndexState.PREPARING)
        catalog.add_candidate(owner, revision, ())
        with catalog._db.transaction(write=True) as connection:
            connection.execute("UPDATE revisions SET index_state='READY' WHERE revision_id=?", (str(revision.revision_id),))
            connection.execute("UPDATE libraries SET current_revision_id=? WHERE kb_id=?", (str(revision.revision_id), str(kb_id)))
        owner.abandon()
    return revision.revision_id


def main():
    from uuid import UUID
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("mutation", "run", "probe", "archive"))
    parser.add_argument("directory")
    parser.add_argument("kb")
    parser.add_argument("event")
    parser.add_argument("control")
    args = parser.parse_args()
    start = time.monotonic()
    catalog = Catalog(args.directory)
    lease = None
    evidence = {"pid": os.getpid(), "mode": args.mode, "started_monotonic": start}
    try:
        if args.mode == "mutation":
            lease = catalog.begin_mutation(UUID(args.kb), snapshot(), HASH)
            evidence.update(result="acquired", token={key: str(value) if not isinstance(value, int) else value for key, value in asdict(lease.token).items()})
        elif args.mode == "run":
            lease = catalog.start_run(UUID(args.kb), resolve_run(config(), "qa"))
            evidence.update(result="pinned", run_id=str(lease.run.run_id), nonce=str(lease.pin.owner_nonce), revision_id=str(lease.run.revision_id))
        elif args.mode == "archive":
            Path(args.event + ".ready").write_text(str(os.getpid()), encoding="utf-8")
            deadline = time.monotonic() + 15
            while not Path(args.control).exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("archive start barrier timed out")
                time.sleep(0.01)
            obj = catalog.archives.put(io.BytesIO(b"concurrent archive" * 100000))
            evidence.update(result="archived", sha256=obj.sha256, size_bytes=obj.size_bytes)
        else:
            try:
                with catalog.begin_mutation(UUID(args.kb), snapshot(), HASH) as owner:
                    owner.abandon()
                evidence["same_library"] = "UNEXPECTED_ACQUIRED"
            except RagError as exc:
                evidence["same_library"] = exc.error.code.value
            other = catalog.create_library("other process library")
            with catalog.begin_mutation(other.kb_id, snapshot(), HASH) as owner:
                owner.abandon()
            evidence["other_library"] = "completed"
            evidence["read_library"] = str(catalog.get_library(UUID(args.kb)).kb_id)
            with catalog.start_run(UUID(args.kb), resolve_run(config(), "qa")) as run:
                evidence["pin"] = str(run.run.revision_id)
            evidence["result"] = "probe_completed"
        evidence["elapsed_ms"] = (time.monotonic() - start) * 1000
        Path(args.event).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        if lease is not None:
            deadline = time.monotonic() + 30
            while not Path(args.control).exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("parent did not release helper within 30 seconds")
                time.sleep(0.02)
            lease.close()
    finally:
        if lease is not None:
            lease.close()


if __name__ == "__main__":
    main()
