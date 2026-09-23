"""Durable index readers admitted in the same write boundary as GC claims.

A returned exception, expired deadline, closed client or dead SDK submitter is
not a service-completion receipt. Such rows deliberately remain pending.
"""

from contextlib import contextmanager
import json
import os
import threading
from uuid import UUID, uuid4

from .._schema import canonical_json
from ..models.identity import process_birth
from .locks import ProcessLock
from .paths import failure


def process_identity():
    birth = process_birth(os.getpid())
    if birth is None:
        raise failure('cannot establish actual reader interpreter identity')
    return os.getpid(), birth


def lock_identity(lock):
    lock.check()
    value = os.fstat(lock._fd)
    return str(value.st_dev), str(value.st_ino)


def dead(pid, birth):
    try:
        return process_birth(pid) != birth
    except (OSError, PermissionError):
        return False


def reader_lock(catalog, identity):
    return ProcessLock(catalog._directory.path('locks', f'reader-{UUID(str(identity))}.lock'))


def scan(catalog, name, *, limit=16):
    """A persisted rotating bounded window; unknown head rows cannot starve it."""
    definitions = {
        'readers': ('index_readers', "state='pending'", 'rowid',
                    'reader_id,kind,pid,process_birth,lock_dev,lock_ino,worker_identity,run_id'),
        'pins': ('run_pins p JOIN run_lifetimes l ON l.run_id=p.run_id', "p.state='active'", 'p.rowid',
                 'p.run_id,p.owner_nonce,l.pid,l.process_birth'),
        'artifacts': ('index_artifacts a JOIN mutation_batches b ON b.batch_id=a.batch_id',
                      "a.state<>'RECLAIMED' AND b.state IN ('PUBLISHED','COMPLETED_NO_CHANGE','ABANDONED')",
                      'a.rowid', 'a.revision_id'),
    }
    table, condition, rowid, columns = definitions[name]
    with catalog._db.transaction(write=True) as db:
        old = db.execute('SELECT after_rowid FROM maintenance_cursors WHERE name=?', (name,)).fetchone()
        cursor = old[0] if old else 0
        rows = db.execute(f'SELECT {rowid},{columns} FROM {table} WHERE {condition} AND {rowid}>? ORDER BY {rowid} LIMIT ?',
                          (cursor, limit)).fetchall()
        if len(rows) < limit:
            rows += db.execute(f'SELECT {rowid},{columns} FROM {table} WHERE {condition} AND {rowid}<=? ORDER BY {rowid} LIMIT ?',
                               (cursor, limit - len(rows))).fetchall()
        db.execute('INSERT INTO maintenance_cursors VALUES(?,?) ON CONFLICT(name) DO UPDATE SET after_rowid=excluded.after_rowid',
                   (name, rows[-1][0] if rows else 0))
    return [row[1:] for row in rows]


class Reader:
    def __init__(self, catalog, artifact, kind, *, run_id=None, detail=None):
        if kind not in ('operation', 'model', 'model_sync', 'milvus'):
            raise ValueError('known reader kind required')
        self.catalog, self.identity, self.nonce = catalog, uuid4(), uuid4()
        self.run_id, self.kind = run_id, kind
        self.handle = None
        self._completion_guard = threading.RLock()
        self._finished = False
        self._completion_source = None
        self.lock = reader_lock(catalog, self.identity).acquire()
        self.pid, self.birth = process_identity()
        try:
            with catalog._db.transaction(write=True) as db:
                row = db.execute('SELECT a.revision_id,a.kb_id,a.state,r.index_state FROM index_artifacts a '
                                 'JOIN revisions r ON r.revision_id=a.revision_id WHERE a.artifact_id=?',
                                 (artifact['artifact_id'],)).fetchone()
                if (row is None or row[:2] != (artifact['revision_id'], artifact['kb_id']) or
                        row[2] not in ('PREPARING', 'READY') or row[3] != row[2]):
                    raise failure('index reader cannot enter a reclaimed or unavailable artifact')
                if run_id is not None:
                    run = db.execute('SELECT r.revision_id,r.status,p.state,l.pid,l.process_birth '
                        'FROM runs r JOIN run_pins p ON p.run_id=r.run_id '
                        'LEFT JOIN run_lifetimes l ON l.run_id=r.run_id AND l.owner_nonce=p.owner_nonce '
                        'WHERE r.run_id=?', (str(run_id),)).fetchone()
                    # Direct DenseSearch has the same process-bound admission as
                    # SourceSession; a UUID copied into another process is not a lease.
                    if run != (row[0], 'running', 'active', self.pid, self.birth):
                        raise failure('reader requires the active actual run owner')
                db.execute('INSERT INTO index_readers VALUES(?,?,?,?,?,?,?,?,?,NULL,?,?,NULL)',
                           (str(self.identity), artifact['artifact_id'], str(run_id) if run_id else None,
                            str(self.nonce), kind, self.pid, self.birth, *lock_identity(self.lock),
                            'pending', canonical_json(detail or {})))
        except BaseException:
            self.lock.close()
            raise
        catalog._readers[self.identity] = self

    def worker(self, value):
        # Only immutable authenticated handle metadata, never provider.metadata.
        required = {'pid', 'process_birth', 'instance_id', 'session_id'}
        if (not isinstance(value, dict) or set(value) != required or type(value['pid']) is not int or
                value['pid'] <= 0 or not isinstance(value['process_birth'], str) or not value['process_birth']):
            raise failure('complete frozen worker request identity required')
        UUID(value['instance_id']); UUID(value['session_id'])
        encoded = canonical_json(value)
        with self.catalog._db.transaction(write=True) as db:
            self._require(db)
            old = db.execute('SELECT worker_identity FROM index_readers WHERE reader_id=?', (str(self.identity),)).fetchone()[0]
            if old is not None and old != encoded:
                raise failure('reader worker identity changed')
            if old is None:
                db.execute('UPDATE index_readers SET worker_identity=? WHERE reader_id=?', (encoded, str(self.identity)))

    def _require(self, db):
        self.lock.check()
        row = db.execute('SELECT owner_nonce,pid,process_birth,lock_dev,lock_ino FROM index_readers WHERE reader_id=?',
                         (str(self.identity),)).fetchone()
        if row != (str(self.nonce), *process_identity(), *lock_identity(self.lock)):
            raise failure('reader receipt belongs to another actual lifetime')

    def finish(self, source='call_returned'):
        with self._completion_guard:
            if self._finished:
                return
            # A service completion and its local receipt commit are distinct.
            # Keep the same native lock/evidence if SQLite is temporarily busy.
            self._completion_source = self._completion_source or source
            try:
                with self.catalog._db.transaction(write=True) as db:
                    self._require(db)
                    db.execute("UPDATE index_readers SET state='finished',completed_by=? WHERE reader_id=? AND state='pending'",
                               (self._completion_source, str(self.identity)))
                self._finished = True
            except Exception:
                if self.catalog._index_backends:
                    self.catalog._maintain_indexes()
                raise
            self.lock.close()
            self.catalog._readers.pop(self.identity, None)
        if self.run_id is not None:
            from .runs import settle
            settle(self.catalog._db, self.run_id)
        self.catalog._maintain_indexes()

    def uncertain(self):
        # Preserve the durable row while releasing this now-returned Python call.
        self.lock.close()
        self.catalog._readers.pop(self.identity, None)


@contextmanager
def sdk_read(catalog, artifact, operation):
    reader = Reader(catalog, artifact, 'milvus', detail={'operation': operation})
    try:
        yield reader
    except BaseException:
        reader.uncertain()
        raise
    else:
        reader.finish()


@contextmanager
def operation(catalog, artifact, run_id):
    reader = Reader(catalog, artifact, 'operation', run_id=run_id)
    try:
        yield reader
    finally:
        # This is the Python admission spanning model -> SDK, not either IO.
        # Each real external call has its own receipt before dispatch.
        reader.finish()


def query(catalog, artifact, run_id, provider, item, profile, context):
    return _model(catalog, artifact, provider, 'query', (item, profile, context), context)


def rerank(catalog, artifact, run_id, provider, query, items, profile, context):
    return _model(catalog, artifact, provider, 'rerank', (query, items, profile, context), context)


def _model(catalog, artifact, provider, capability, arguments, context):
    tracked = hasattr(provider, 'submit_' + capability)
    reader = Reader(catalog, artifact, 'model' if tracked else 'model_sync',
                    detail={'request_id': str(context.request_id), 'capability': capability})
    # The outer operation holds run admission even if finish wins immediately
    # before this child is registered. Associate it without reopening admission.
    # A finished outer operation cannot precede this synchronous call's return.
    if not tracked:
        try:
            result = getattr(provider, 'embed_query' if capability == 'query' else 'rerank')(*arguments)
        except BaseException:
            reader.uncertain()
            raise
        reader.finish()
        return result
    try:
        handle = getattr(provider, 'submit_' + capability)(*arguments)
        reader.worker(handle.worker_identity)
        reader.handle = handle
    except BaseException:
        reader.uncertain()
        raise
    def finish():
        if handle.wait_finished():
            reader.finish(handle.completion_source or 'worker_finished')
    try:
        return handle.result()
    finally:
        if handle.wait_finished(0):
            finish()
        else:
            threading.Thread(target=finish, name='rag-' + capability + '-completion', daemon=True).start()


def flush_finished(catalog):
    """Settle already-authenticated completions before a last transport retires.

    A background receipt writer may not have run yet when a host sees the
    finished frame. Use that same actual handle; never infer from client.close.
    """
    for reader in tuple(catalog._readers.values()):
        handle = reader.handle
        if reader._completion_source is not None:
            reader.finish(reader._completion_source)
        elif handle is not None and handle.wait_finished(0):
            reader.finish(handle.completion_source or 'worker_finished')


def reconcile(catalog, *, limit=16):
    """Reconcile only death proofs; no clock expiry and no server assumption."""
    rows = scan(catalog, 'readers', limit=limit)
    for row in rows:
        worker = json.loads(row[6]) if row[6] else None
        reason = ('worker_process_death' if row[1] == 'model' and worker and dead(worker['pid'], worker['process_birth'])
                  else 'operation_process_death' if row[1] == 'operation' and dead(row[2], row[3]) else None)
        if reason is None:
            continue
        try:
            with reader_lock(catalog, row[0]) as lock:
                if lock_identity(lock) != row[4:6]:
                    continue
                with catalog._db.transaction(write=True) as db:
                    current = db.execute("SELECT reader_id,kind,pid,process_birth,lock_dev,lock_ino,worker_identity,run_id "
                        "FROM index_readers WHERE reader_id=? AND state='pending'", (row[0],)).fetchone()
                    if current != row:
                        continue
                    db.execute("UPDATE index_readers SET state='finished',completed_by=? WHERE reader_id=?", (reason, row[0]))
        except Exception:
            continue  # Occupied/replaced/inaccessible lock is not a death proof.
