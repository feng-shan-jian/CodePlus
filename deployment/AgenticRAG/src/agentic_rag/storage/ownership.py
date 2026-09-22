"""Library execution ownership, durable pending work and manual takeover."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from ..config import ProcessingSnapshot
from ..domain import BatchState, ErrorCode, ImportBatch, RagError
from .database import Database
from .locks import ProcessLock
from .paths import failure

ACTIVE = {"SNAPSHOTTING", "PROCESSING", "INDEXING", "VALIDATING", "READY"}


@dataclass(frozen=True)
class OwnerToken:
    store_id: UUID
    kb_id: UUID
    batch_id: UUID
    owner_nonce: UUID
    owner_epoch: int


def lock_for(database: Database, kb_id: UUID) -> ProcessLock:
    if not isinstance(kb_id, UUID):
        raise TypeError("kb_id must be UUID")
    return ProcessLock(database.directory.path("locks", f"library-{kb_id}.lock"))


def read_batch(connection, batch_id: UUID) -> ImportBatch:
    row = connection.execute("SELECT batch_id,kb_id,base_revision_id,input_manifest_hash,processing_snapshot_id,owner_epoch,state,published_revision_id,recovery_stage FROM mutation_batches WHERE batch_id=?", (str(batch_id),)).fetchone()
    if row is None:
        raise failure("batch not found")
    return ImportBatch(batch_id=UUID(row[0]), kb_id=UUID(row[1]), base_revision_id=UUID(row[2]) if row[2] else None,
                       input_manifest_hash=row[3], processing_snapshot_id=UUID(row[4]), owner_epoch=row[5],
                       state=BatchState(row[6]), published_revision_id=UUID(row[7]) if row[7] else None, recovery_stage=row[8])


class Mutation:
    def __init__(self, database: Database, lock: ProcessLock, token: OwnerToken):
        self._database, self._lock, self._token = database, lock, token

    @property
    def token(self) -> OwnerToken:
        return self._token

    def require(self, database: Database, connection, produced_by: OwnerToken | None = None) -> None:
        self._lock.check()
        if database is not self._database or self.token.store_id != database.directory.store_id:
            raise failure("mutation belongs to a different catalog")
        if self._lock.path != lock_for(database, self.token.kb_id).path:
            raise failure("mutation token does not match its held library lock")
        if produced_by is not None and produced_by != self.token:
            raise failure("late result from a previous mutation owner")
        token = self.token
        row = connection.execute("SELECT b.owner_nonce,b.owner_epoch,b.state,l.pending_mutation_id,l.owner_epoch FROM mutation_batches b JOIN libraries l ON l.kb_id=b.kb_id WHERE b.batch_id=? AND b.kb_id=?",
                                 (str(token.batch_id), str(token.kb_id))).fetchone()
        if row is None or row[0] != str(token.owner_nonce) or row[1] != token.owner_epoch or row[2] not in ACTIVE or row[3] != str(token.batch_id) or row[4] != token.owner_epoch:
            raise failure("mutation owner is stale, inactive or no longer pending")

    def abandon(self) -> None:
        with self._database.transaction(write=True) as connection:
            self.require(self._database, connection)
            connection.execute("UPDATE mutation_batches SET state='ABANDONED',recovery_stage=NULL WHERE batch_id=?", (str(self.token.batch_id),))
            connection.execute("UPDATE libraries SET pending_mutation_id=NULL WHERE kb_id=?", (str(self.token.kb_id),))
            # Dependents cease requiring indexes; archives and records remain.
            connection.execute("DELETE FROM revision_dependencies WHERE batch_id=?", (str(self.token.batch_id),))
        self._lock.close()

    def close(self) -> None:
        if self._lock._fd is None:
            return
        try:
            with self._database.transaction(write=True) as connection:
                self.require(self._database, connection)
                connection.execute("UPDATE mutation_batches SET recovery_stage=state,state='WAITING_RECOVERY' WHERE batch_id=?", (str(self.token.batch_id),))
        finally:
            self._lock.close()

    def __enter__(self):
        self._lock.check()
        return self

    def __exit__(self, *_):
        self.close()


def begin(database: Database, kb_id: UUID, batch_id: UUID, snapshot: ProcessingSnapshot, manifest_hash: str, *, _register=None) -> Mutation:
    # Validate record before creating any durable state.
    ImportBatch(batch_id=batch_id, kb_id=kb_id, input_manifest_hash=manifest_hash,
                processing_snapshot_id=snapshot.snapshot_id, owner_epoch=1, state=BatchState.SNAPSHOTTING)
    lock = lock_for(database, kb_id).acquire()
    try:
        with database.transaction(write=True) as connection:
            row = connection.execute("SELECT current_revision_id,pending_mutation_id,owner_epoch FROM libraries WHERE kb_id=?", (str(kb_id),)).fetchone()
            if row is None:
                raise failure("knowledge library not found")
            if row[1] is not None:
                raise RagError(ErrorCode.LIBRARY_BUSY, "unfinished batch requires explicit recovery or abandonment", stage="mutation")
            epoch, nonce = row[2] + 1, uuid4()
            existing = connection.execute("SELECT kb_id,resolved_config,config_fingerprint FROM processing_snapshots WHERE snapshot_id=?", (str(snapshot.snapshot_id),)).fetchone()
            if existing is None:
                connection.execute("INSERT INTO processing_snapshots VALUES(?,?,?,?,?,?)", (str(snapshot.snapshot_id), str(kb_id), snapshot.config_fingerprint, snapshot.document_encoding_fingerprint, snapshot.index_fingerprint, snapshot.resolved_config.model_dump_json()))
            elif existing != (str(kb_id), snapshot.resolved_config.model_dump_json(), snapshot.config_fingerprint):
                raise failure("processing snapshot identity conflict")
            connection.execute("INSERT INTO mutation_batches VALUES(?,?,?,?,?,?,?,'SNAPSHOTTING',NULL,NULL)", (str(batch_id), str(kb_id), row[0], manifest_hash, str(snapshot.snapshot_id), epoch, str(nonce)))
            connection.execute("UPDATE libraries SET pending_mutation_id=?,owner_epoch=? WHERE kb_id=?", (str(batch_id), epoch, str(kb_id)))
            if row[0] is not None:
                connection.execute("INSERT INTO revision_dependencies VALUES(?,?,?,'base')", (str(kb_id), str(batch_id), row[0]))
            if _register is not None:
                _register(connection, read_batch(connection, batch_id))
        return Mutation(database, lock, OwnerToken(database.directory.store_id, kb_id, batch_id, nonce, epoch))
    except BaseException:
        lock.close()
        raise


def identify_interrupted(database: Database, kb_id: UUID) -> OwnerToken | None:
    """Prove former execution has ended, leave pending work blocked, never resume."""
    with lock_for(database, kb_id):
        with database.transaction(write=True) as connection:
            row = connection.execute("SELECT b.batch_id,b.owner_nonce,b.owner_epoch,b.state FROM libraries l JOIN mutation_batches b ON b.batch_id=l.pending_mutation_id AND b.kb_id=l.kb_id WHERE l.kb_id=?", (str(kb_id),)).fetchone()
            if row is None:
                return None
            if row[3] in ACTIVE:
                connection.execute("UPDATE mutation_batches SET recovery_stage=state,state='WAITING_RECOVERY' WHERE batch_id=?", (row[0],))
            elif row[3] != "WAITING_RECOVERY":
                raise failure("pending batch has an inconsistent terminal state")
            return OwnerToken(database.directory.store_id, kb_id, UUID(row[0]), UUID(row[1]), row[2])


def resume(database: Database, expected: OwnerToken) -> Mutation:
    """Explicit manual recovery primitive; caller validates checkpoints in R14."""
    if expected.store_id != database.directory.store_id:
        raise failure("recovery token belongs to a different catalog")
    lock = lock_for(database, expected.kb_id).acquire()
    try:
        with database.transaction(write=True) as connection:
            row = connection.execute("SELECT b.owner_nonce,b.owner_epoch,b.state,b.recovery_stage,l.owner_epoch FROM mutation_batches b JOIN libraries l ON l.pending_mutation_id=b.batch_id AND l.kb_id=b.kb_id WHERE b.batch_id=? AND b.kb_id=?", (str(expected.batch_id), str(expected.kb_id))).fetchone()
            if row is None or row[:3] != (str(expected.owner_nonce), expected.owner_epoch, "WAITING_RECOVERY") or row[4] != expected.owner_epoch:
                raise failure("recovery identity or expected state changed")
            epoch, nonce = row[4] + 1, uuid4()
            connection.execute("UPDATE mutation_batches SET state=recovery_stage,recovery_stage=NULL,owner_epoch=?,owner_nonce=? WHERE batch_id=?", (epoch, str(nonce), str(expected.batch_id)))
            connection.execute("UPDATE libraries SET owner_epoch=? WHERE kb_id=?", (epoch, str(expected.kb_id)))
        return Mutation(database, lock, OwnerToken(expected.store_id, expected.kb_id, expected.batch_id, nonce, epoch))
    except BaseException:
        lock.close()
        raise
