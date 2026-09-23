"""Run binding and whole-revision pins share one short metadata transaction."""

import json
import threading
from uuid import UUID, uuid4

from ..config import (KnowledgeConfig, RunConfiguration, RunOverride, document_encoding_identity,
                      resolve_run, with_published_encoding)
from ..domain import ErrorCode, RagError, Run, RunPin, RunStatus, RunUsage
from .database import Database
from .locks import ProcessLock
from .paths import failure
from .readers import dead, lock_identity, process_identity


def read_run(connection, run_id: UUID) -> Run:
    row = connection.execute("SELECT run_id,parent_run_id,kb_id,revision_id,resolved_config,resolved_config_hash,usage,status,stop_reason FROM runs WHERE run_id=?", (str(run_id),)).fetchone()
    if row is None:
        raise failure("run not found")
    fields = dict(zip(("run_id", "parent_run_id", "kb_id", "revision_id", "resolved_config", "resolved_config_hash", "usage", "status", "stop_reason"), row))
    fields["resolved_config"], fields["usage"] = json.loads(fields["resolved_config"]), json.loads(fields["usage"])
    return Run.model_validate_json(json.dumps(fields))


def read_pin(connection, run_id: UUID) -> RunPin:
    row = connection.execute("SELECT revision_id,owner_nonce,state FROM run_pins WHERE run_id=?", (str(run_id),)).fetchone()
    if row is None:
        raise failure("run pin not found")
    return RunPin(run_id=run_id, revision_id=UUID(row[0]), owner_nonce=UUID(row[1]), state=row[2])


def lock_for(database: Database, run_id: UUID) -> ProcessLock:
    if not isinstance(run_id, UUID):
        raise TypeError("run_id must be UUID")
    return ProcessLock(database.directory.path("locks", f"run-{run_id}.lock"))


class RunLease:
    def __init__(self, database: Database, lock: ProcessLock, run: Run, pin: RunPin):
        self._database, self._lock, self._run, self._pin = database, lock, run, pin
        self._guard = threading.RLock()
        database._run_leases[run.run_id] = self

    @property
    def run(self) -> Run:
        return self._run

    @property
    def pin(self) -> RunPin:
        return self._pin

    def finish(self, status: RunStatus, stop_reason: str, *, usage: RunUsage | None = None,
               cleanup_pending: tuple[str, ...] = ()) -> Run:
        with self._guard:
            return self._finish(status, stop_reason, usage=usage, cleanup_pending=cleanup_pending)

    def _finish(self, status, stop_reason, *, usage=None, cleanup_pending=()):
        self._lock.check()
        if self._lock.path != lock_for(self._database, self.run.run_id).path:
            raise failure("run identity does not match its lifecycle lock")
        if not isinstance(status, RunStatus) or status == RunStatus.RUNNING:
            raise failure("finish requires a terminal run status")
        with self._database.transaction(write=True) as connection:
            pin = read_pin(connection, self.run.run_id)
            current = read_run(connection, self.run.run_id)
            if pin != self.pin or pin.state != "active" or current.status != RunStatus.RUNNING:
                raise failure("run owner is stale or already released")
            # Source calls update usage durably while the lease binding remains
            # immutable. Finishing must not restore its older in-memory usage.
            finished = current.model_copy(update={"status": status, "stop_reason": stop_reason, "usage": usage or current.usage})
            connection.execute("UPDATE runs SET status=?,stop_reason=?,usage=? WHERE run_id=?", (status.value, stop_reason, finished.usage.model_dump_json(), str(self.run.run_id)))
            if cleanup_pending:
                if not connection.execute('SELECT 1 FROM host_runs WHERE run_id=?', (str(self.run.run_id),)).fetchone():
                    raise failure('pending cleanup requires a registered host owner')
                connection.execute("UPDATE host_runs SET cleanup_state='pending',cleanup_handles=? WHERE run_id=?",
                                   (json.dumps(cleanup_pending), str(self.run.run_id)))
            elif not pending_readers(connection, self.run.run_id):
                connection.execute("UPDATE run_pins SET state='released' WHERE run_id=? AND owner_nonce=?", (str(self.run.run_id), str(self.pin.owner_nonce)))
                connection.execute("UPDATE host_runs SET cleanup_state='released',cleanup_handles='[]' WHERE run_id=?", (str(self.run.run_id),))
        self._run = finished
        self._settle()
        maintain(self._database)
        return finished

    def release_cleanup(self) -> None:
        """Owning host calls only after its actual readers have all terminated."""
        reference = getattr(self._database, '_catalog', None)
        catalog = reference() if reference else None
        if catalog is not None:
            from .readers import flush_finished
            flush_finished(catalog)
        with self._guard:
            self._lock.check()
            with self._database.transaction(write=True) as connection:
                pin, current = read_pin(connection, self.run.run_id), read_run(connection, self.run.run_id)
                row = connection.execute('SELECT cleanup_state FROM host_runs WHERE run_id=?', (str(self.run.run_id),)).fetchone()
                if pin != self.pin or current.status == RunStatus.RUNNING or row != ('pending',):
                    raise failure('cleanup ownership or lifecycle differs')
                # Host futures are one layer; actual SDK/model receipts are a
                # separate admission boundary and cannot be overridden here.
                connection.execute("UPDATE host_runs SET cleanup_state='active',cleanup_handles='[]' WHERE run_id=?", (str(self.run.run_id),))
            self._settle()
        maintain(self._database)

    def _settle(self):
        with self._guard:
            if self._lock._fd is None:
                return
            self._lock.check()
            with self._database.transaction(write=True) as db:
                pin, run = read_pin(db, self.run.run_id), read_run(db, self.run.run_id)
                if pin.owner_nonce != self.pin.owner_nonce:
                    raise failure('run owner changed during completion')
                host = db.execute('SELECT cleanup_state FROM host_runs WHERE run_id=?', (str(self.run.run_id),)).fetchone()
                if run.status == RunStatus.RUNNING or host == ('pending',) or pending_readers(db, self.run.run_id):
                    return
                db.execute("UPDATE run_pins SET state='released' WHERE run_id=?", (str(self.run.run_id),))
                db.execute("UPDATE host_runs SET cleanup_state='released',cleanup_handles='[]' WHERE run_id=?", (str(self.run.run_id),))
            self._lock.close()
            self._database._run_leases.pop(self.run.run_id, None)

    def close(self) -> None:
        if self._run.status != RunStatus.RUNNING:
            # A terminal run may still own real worker readers. Closing a Python
            # consumer is not evidence that those readers have stopped.
            return
        if self._lock._fd is not None:
            try:
                self.finish(RunStatus.CANCELLED, "consumer_closed")
            except BaseException:
                # A failed release leaves the active pin for lock-proven cleanup.
                self._lock.close()
                raise

    def __enter__(self):
        self._lock.check()
        return self

    def __exit__(self, *_):
        self.close()


def start(database: Database, kb_id: UUID, config: RunConfiguration, *, run_id: UUID, parent_run_id: UUID | None = None,
          _use_published_encoding=False) -> RunLease:
    lock = lock_for(database, run_id).acquire()
    try:
        with database.transaction(write=True) as connection:
            # GC claims eligibility in this exact BEGIN IMMEDIATE boundary.
            row = connection.execute("SELECT r.revision_id,r.index_state,s.document_encoding_fingerprint,s.resolved_config FROM libraries l JOIN revisions r ON r.kb_id=l.kb_id AND r.revision_id=l.current_revision_id JOIN processing_snapshots s ON s.snapshot_id=r.processing_snapshot_id AND s.kb_id=r.kb_id WHERE l.kb_id=?", (str(kb_id),)).fetchone()
            if row is None or row[1] != "READY":
                raise RagError(ErrorCode.NOT_READY, "library has no queryable current revision", stage="run")
            if _use_published_encoding:
                actual = KnowledgeConfig.model_validate_json(row[3])
                config = resolve_run(with_published_encoding(config.knowledge, actual), config.task_kind,
                                     RunOverride(mode=config.retrieval.mode))
            if row[2] != document_encoding_identity(config.knowledge):
                raise RagError(ErrorCode.INVALID_CONFIGURATION, "run encoding differs from its revision", stage="run")
            if parent_run_id is not None and read_run(connection, parent_run_id).kb_id != kb_id:
                raise failure("parent run belongs to another library")
            run = Run(run_id=run_id, parent_run_id=parent_run_id, kb_id=kb_id, revision_id=UUID(row[0]), resolved_config=config, resolved_config_hash=config.identity)
            pin = RunPin(run_id=run_id, revision_id=run.revision_id, owner_nonce=uuid4())
            connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", (str(run_id), str(parent_run_id) if parent_run_id else None, str(kb_id), row[0], config.model_dump_json(), config.identity, run.usage.model_dump_json(), run.status.value, None))
            connection.execute("INSERT INTO run_pins VALUES(?,?,?,'active')", (str(run_id), row[0], str(pin.owner_nonce)))
            connection.execute('INSERT INTO run_lifetimes VALUES(?,?,?,?,?,?)',
                               (str(run_id), str(pin.owner_nonce), *process_identity(), *lock_identity(lock)))
        return RunLease(database, lock, run, pin)
    except BaseException:
        lock.close()
        raise


def release_crashed(database: Database, run_id: UUID, expected_nonce: UUID) -> None:
    # Keep the acquired lifecycle lock until the release transaction has committed.
    with lock_for(database, run_id) as lock:
        with database.transaction(write=True) as connection:
            pin = read_pin(connection, run_id)
            if pin.owner_nonce != expected_nonce:
                raise failure("run pin nonce changed; ownership is uncertain")
            if pin.state == "released":
                return
            lifetime = connection.execute('SELECT owner_nonce,pid,process_birth,lock_dev,lock_ino FROM run_lifetimes WHERE run_id=?', (str(run_id),)).fetchone()
            if (lifetime is None or lifetime[0] != str(expected_nonce) or lifetime[3:] != lock_identity(lock)
                    or not dead(lifetime[1], lifetime[2]) or pending_readers(connection, run_id)):
                raise failure('run lifecycle or actual readers remain uncertain; preserve pin')
            if read_run(connection, run_id).status == RunStatus.RUNNING:
                connection.execute("UPDATE runs SET status='failed',stop_reason='explicit_error' WHERE run_id=?", (str(run_id),))
            connection.execute("UPDATE run_pins SET state='released' WHERE run_id=? AND owner_nonce=?", (str(run_id), str(expected_nonce)))
            connection.execute("UPDATE host_runs SET cleanup_state='released',cleanup_handles='[]' WHERE run_id=?", (str(run_id),))
    maintain(database)


def pending_readers(db, run_id):
    # Direct backend callers have no run argument. Conservatively retain every
    # pin of that same artifact until those actual readers also finish.
    return db.execute("SELECT 1 FROM index_readers i JOIN index_artifacts a ON a.artifact_id=i.artifact_id "
                      "JOIN runs r ON r.revision_id=a.revision_id WHERE r.run_id=? AND i.state='pending' LIMIT 1",
                      (str(run_id),)).fetchone() is not None


def settle(database, run_id):
    lease = database._run_leases.get(UUID(str(run_id)))
    if lease is not None:
        lease._settle()


def maintain(database):
    reference = getattr(database, '_catalog', None)
    catalog = reference() if reference else None
    if catalog is not None:
        catalog._maintain_indexes()
