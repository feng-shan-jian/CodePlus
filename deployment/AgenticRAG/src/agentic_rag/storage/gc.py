"""Bounded, durable physical index reclamation; historical archives never enter it."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from .._schema import canonical_json
from . import readers, recovery
from .paths import failure

TERMINAL = ('PUBLISHED', 'COMPLETED_NO_CHANGE', 'ABANDONED')


def dependency(db, artifact):
    rid, aid, bid = artifact['revision_id'], artifact['artifact_id'], artifact['batch_id']
    row = db.execute('SELECT 1 FROM libraries WHERE current_revision_id=? UNION ALL '
        "SELECT 1 FROM run_pins WHERE revision_id=? AND state='active' UNION ALL "
        'SELECT 1 FROM revision_dependencies WHERE revision_id=? UNION ALL '
        "SELECT 1 FROM mutation_batches WHERE (base_revision_id=? OR batch_id=?) "
        "AND state NOT IN ('PUBLISHED','COMPLETED_NO_CHANGE','ABANDONED') UNION ALL "
        "SELECT 1 FROM index_readers WHERE artifact_id=? AND state='pending' LIMIT 1",
        (rid, rid, rid, rid, bid, aid)).fetchone()
    if row:
        return 'current revision, active pin, reader or mutation dependency'
    # R14 write receipts can outlive abandonment and cover all generations.
    rows = db.execute("SELECT kind,worker_pid,worker_birth FROM mutation_io WHERE batch_id=? "
                      "AND (artifact_id IS NULL OR artifact_id=?) AND state='pending'", (bid, aid)).fetchall()
    if any(kind != 'model' or pid is None or not readers.dead(pid, birth) for kind, pid, birth in rows):
        return 'uncertain mutation IO'
    return None


@dataclass(frozen=True)
class _Claim:
    catalog: object
    artifact_id: str
    nonce: str
    lock: object

    def require(self, db, artifact):
        if self.artifact_id != artifact['artifact_id']:
            raise failure('GC claim belongs to a different artifact')
        row = db.execute('SELECT owner_nonce,pid,process_birth,lock_dev,lock_ino,state FROM index_gc_claims WHERE artifact_id=?',
                         (self.artifact_id,)).fetchone()
        if row != (self.nonce, *readers.process_identity(), *readers.lock_identity(self.lock), 'claimed'):
            raise failure('GC claim no longer belongs to this actual collector lifetime')
        expected_path = self.catalog._directory.path('locks', f'artifact-{UUID(self.artifact_id)}.lock')
        if self.lock.path != expected_path:
            raise failure('GC claim does not hold the artifact lifecycle lock')
        state = db.execute('SELECT a.state,r.index_state FROM index_artifacts a '
            'JOIN revisions r ON r.revision_id=a.revision_id WHERE a.artifact_id=?', (self.artifact_id,)).fetchone()
        if state != ('RECLAIMING', 'RECLAIMING') or dependency(db, artifact):
            raise failure('GC claim acquired a new or uncertain dependency')


def _claim(catalog, backend, artifact, lock):
    with catalog._db.transaction(write=True) as db:
        reason = dependency(db, artifact)
        if reason:
            return None, reason
        state = db.execute('SELECT a.state,r.index_state,b.state FROM index_artifacts a '
            'JOIN revisions r ON r.revision_id=a.revision_id JOIN mutation_batches b ON b.batch_id=a.batch_id '
            'WHERE a.artifact_id=?', (artifact['artifact_id'],)).fetchone()
        if state is None or state[0] != state[1] or state[2] not in TERMINAL:
            return None, 'artifact or terminal batch identity differs'
        if state[0] == 'RECLAIMED':
            return None, 'already reclaimed'
        old = db.execute('SELECT owner_nonce,pid,process_birth,lock_dev,lock_ino,attempt,state FROM index_gc_claims WHERE artifact_id=?',
                         (artifact['artifact_id'],)).fetchone()
        if state[0] == 'RECLAIMING' and old is None:
            return None, 'legacy reclaiming operation lacks collector completion proof'
        if old is not None and (old[3:5] != readers.lock_identity(lock) or
                (old[6] == 'claimed' and not readers.dead(old[1], old[2]))):
            return None, 'previous collector lifetime remains uncertain'
        # No network IO here; persisted create/describe proof is a prerequisite.
        proof = db.execute('SELECT i.endpoint,i.database_name,i.marker,p.description '
            'FROM artifact_creation_intents i JOIN artifact_ownership_proofs p USING(artifact_id) '
            'WHERE artifact_id=?', (artifact['artifact_id'],)).fetchone()
        if (proof is None or proof[:2] != (backend.storage.milvus_uri, backend.database_name)
                or proof[2] != proof[3]):
            return None, 'no matching persisted physical ownership proof'
        nonce = str(uuid4())
        db.execute('INSERT INTO index_gc_claims VALUES(?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(artifact_id) DO UPDATE SET owner_nonce=excluded.owner_nonce,pid=excluded.pid,'
            'process_birth=excluded.process_birth,lock_dev=excluded.lock_dev,lock_ino=excluded.lock_ino,'
            'attempt=excluded.attempt,state=excluded.state,error=NULL,updated_at=excluded.updated_at',
            (artifact['artifact_id'], nonce, *readers.process_identity(), *readers.lock_identity(lock),
             old[5] + 1 if old else 1, 'claimed', None, recovery.now()))
        db.execute("UPDATE index_artifacts SET state='RECLAIMING' WHERE artifact_id=?", (artifact['artifact_id'],))
        db.execute("UPDATE revisions SET index_state='RECLAIMING' WHERE revision_id=?", (artifact['revision_id'],))
    return _Claim(catalog, artifact['artifact_id'], nonce, lock), None


def collect(catalog, backend, artifact, *, observer=None):
    from ..indexes.milvus import MilvusRevisionIndex
    if type(backend) is not MilvusRevisionIndex or backend.catalog is not catalog:
        raise failure('GC requires the actual bound Milvus adapter')
    snapshot = catalog.get_snapshot(catalog.get_batch(UUID(artifact['batch_id'])).processing_snapshot_id)
    if backend.storage != snapshot.resolved_config.storage or backend.database_name != 'default':
        raise failure('GC backend differs from the complete frozen storage identity')
    result = {key: artifact[key] for key in ('artifact_id', 'revision_id', 'collection_name')}
    claim = None
    try:
        with backend._lifecycle(artifact) as lock:
            claim, reason = _claim(catalog, backend, artifact, lock)
            if claim is None:
                result.update(state='retained', reason=reason)
            else:
                try:
                    if observer:
                        observer('before_drop', dict(result))
                    backend.drop_owned(artifact, _claim=claim)
                    if observer:
                        observer('after_drop', dict(result))
                    with catalog._db.transaction(write=True) as db:
                        claim.require(db, artifact)
                        db.execute("UPDATE index_artifacts SET state='RECLAIMED' WHERE artifact_id=?", (artifact['artifact_id'],))
                        db.execute("UPDATE revisions SET index_state='RECLAIMED' WHERE revision_id=?", (artifact['revision_id'],))
                        db.execute("UPDATE index_gc_claims SET state='reclaimed',error=NULL,updated_at=? WHERE artifact_id=? AND owner_nonce=?",
                                   (recovery.now(), artifact['artifact_id'], claim.nonce))
                    result.update(state='reclaimed', reason='matching physical ownership; drop acknowledged or proven absent')
                    if observer:
                        observer('after_receipt', dict(result))
                except Exception as exc:
                    result.update(state='retained', reason=f'{type(exc).__name__}: {exc}')
                    with catalog._db.transaction(write=True) as db:
                        db.execute("UPDATE index_gc_claims SET state='failed',error=?,updated_at=? WHERE artifact_id=? AND owner_nonce=? AND state='claimed'",
                                   (result['reason'], recovery.now(), artifact['artifact_id'], claim.nonce))
    except Exception as exc:
        result.update(state='retained', reason=f'{type(exc).__name__}: {exc}')
    with catalog._db.transaction(write=True) as db:
        db.execute('INSERT INTO index_gc_attempts VALUES(?,?,?,?)',
                   (str(uuid4()), artifact['artifact_id'], canonical_json(result), recovery.now()))
    return result


def maintain(catalog, *, limit=2, backend=None, batch_id=None, observer=None, retry_failed=True):
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ValueError('maintenance limit must be 1..32 artifacts')
    if not catalog._maintenance_lock.acquire(blocking=False):
        return []
    try:
        from . import runs, publication
        readers.flush_finished(catalog)
        readers.reconcile(catalog)
        for lease in tuple(catalog._db._run_leases.values()):
            lease._settle()
        # No model start or dependency import. Legacy active pins with no new
        # process/lock proof are retained, including terminal legacy host pins.
        pins = readers.scan(catalog, 'pins')
        for run_id, nonce, pid, birth in pins:
            if readers.dead(pid, birth):
                try:
                    runs.release_crashed(catalog._db, UUID(run_id), UUID(nonce))
                except Exception:
                    pass
        ids = readers.scan(catalog, 'artifacts', limit=min(32, limit * 8))
        results = []
        for revision_id, in ids:
            stored = publication.artifact(catalog, UUID(revision_id))
            if batch_id is not None and stored['batch_id'] != str(batch_id):
                continue
            with catalog._db.transaction() as db:
                if dependency(db, stored):
                    continue
                last = db.execute('SELECT attempt,state,updated_at FROM index_gc_claims WHERE artifact_id=?',
                                  (stored['artifact_id'],)).fetchone()
                if last and last[1] == 'failed' and not retry_failed:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last[2])).total_seconds()
                    if age < min(60, 2 ** min(last[0], 6)):
                        continue
            snapshot = catalog.get_snapshot(catalog.get_batch(UUID(stored['batch_id'])).processing_snapshot_id)
            selected = backend or next((candidate for candidate in tuple(catalog._index_backends)
                                        if candidate.storage == snapshot.resolved_config.storage and
                                        candidate.database_name == 'default' and not candidate._closing), None)
            if selected is None:
                # Metadata establishes queued work; optional core-only/history
                # use neither imports PyMilvus nor contacts a service.
                continue
            results.append(collect(catalog, selected, stored, observer=observer))
            if len(results) >= limit:
                # Preserve the next artifact for the following maintenance edge;
                # the scan window itself was bounded and rotates retained rows.
                with catalog._db.transaction(write=True) as db:
                    position = db.execute('SELECT rowid FROM index_artifacts WHERE artifact_id=?', (stored['artifact_id'],)).fetchone()[0]
                    db.execute("UPDATE maintenance_cursors SET after_rowid=? WHERE name='artifacts'", (position,))
                break
        return results
    finally:
        catalog._maintenance_lock.release()


def retire_pass(catalog, backend, after, cutoff):
    """One finite close-time window, using the same claims and drop coordinator.

    A failure is already durable: stop this retirement and close its transport.
    Uncertain/protected objects remain eligible for later lifecycle/startup retry.
    """
    from . import publication
    readers.flush_finished(catalog)
    for lease in tuple(catalog._db._run_leases.values()):
        lease._settle()
    with catalog._maintenance_lock:
        with catalog._db.transaction() as db:
            rows = db.execute('SELECT rowid,revision_id FROM index_artifacts WHERE rowid>? AND rowid<=? '
                              'ORDER BY rowid LIMIT 16', (after, cutoff)).fetchall()
        attempts = 0
        for position, revision_id in rows:
            stored = publication.artifact(catalog, UUID(revision_id))
            after = position
            with catalog._db.transaction() as db:
                state = db.execute('SELECT state FROM mutation_batches WHERE batch_id=?', (stored['batch_id'],)).fetchone()[0]
                if stored['state'] == 'RECLAIMED' or state not in TERMINAL or dependency(db, stored):
                    continue
                if not db.execute('SELECT 1 FROM artifact_ownership_proofs WHERE artifact_id=?', (stored['artifact_id'],)).fetchone():
                    continue
                if stored['state'] == 'RECLAIMING' and not db.execute('SELECT 1 FROM index_gc_claims WHERE artifact_id=?', (stored['artifact_id'],)).fetchone():
                    continue
            snapshot = catalog.get_snapshot(catalog.get_batch(UUID(stored['batch_id'])).processing_snapshot_id)
            if backend.storage != snapshot.resolved_config.storage:
                continue
            result = collect(catalog, backend, stored)
            attempts += 1
            with catalog._db.transaction() as db:
                failed = db.execute("SELECT 1 FROM index_gc_claims WHERE artifact_id=? AND state='failed'", (stored['artifact_id'],)).fetchone()
            if failed:
                return after, True
            if attempts >= 2:
                return after, after >= cutoff
        return after, len(rows) < 16 or after >= cutoff
