"""Terminal authority, execution receipts and conservative candidate cleanup.

No model or service is contacted to reconcile a terminal business result.
An uncertain external request is a retained dependency, not proof of failure.
"""

from datetime import datetime, timezone
import json
import os
from uuid import UUID, uuid4

from .._schema import canonical_json
from ..models.identity import process_birth
from .paths import failure


def now():
    return datetime.now(timezone.utc).isoformat()


def execution(connection, batch_id, epoch, nonce):
    birth = process_birth(os.getpid())
    if birth is None:
        raise failure('cannot establish actual mutation interpreter identity')
    connection.execute('INSERT INTO mutation_executions VALUES(?,?,?,?,?,?)',
                       (str(batch_id), epoch, str(nonce), os.getpid(), birth, now()))


def terminal(catalog, batch_id):
    """Read SQL completion before model, archives, old indexes, or new leases."""
    with catalog._db.transaction() as db:
        return terminal_in(db, batch_id)


def terminal_in(db, batch_id):
    batch = db.execute('SELECT kb_id,state,published_revision_id,owner_nonce,owner_epoch FROM mutation_batches WHERE batch_id=?',
                       (str(batch_id),)).fetchone()
    if batch is None:
        raise failure('batch not found')
    published = db.execute('SELECT batch_id,kb_id,revision_id,artifact_id,owner_nonce,owner_epoch,manifest_hash,published_at '
                          'FROM publications WHERE batch_id=?', (str(batch_id),)).fetchone()
    completion = db.execute('SELECT summary_json FROM mutation_completions WHERE batch_id=?', (str(batch_id),)).fetchone()
    ordinary = db.execute('SELECT 1 FROM ordinary_mutations WHERE batch_id=?', (str(batch_id),)).fetchone()
    result = None
    if published:
        if batch != (published[1], 'PUBLISHED', published[2], published[4], published[5]):
            raise failure('batch and immutable SQL publication disagree')
        if ordinary and (not completion or json.loads(completion[0]).get('published_revision_id') != published[2]):
            raise failure('published ordinary batch lacks matching immutable completion')
        receipt = dict(zip(('batch_id','kb_id','revision_id','artifact_id','owner_nonce','owner_epoch','manifest_hash','published_at'),published))
        result = {'state':'PUBLISHED','receipt':receipt,'summary':json.loads(completion[0]) if completion else None}
    elif batch[1] == 'COMPLETED_NO_CHANGE':
        if not ordinary or not completion:
            raise failure('no-change batch lacks immutable SQL completion')
        value = json.loads(completion[0])
        if value.get('state') != 'COMPLETED_NO_CHANGE' or value.get('batch_id') != str(batch_id) or value.get('published_revision_id') is not None:
            raise failure('no-change completion identity differs')
        result = {'state':'COMPLETED_NO_CHANGE','receipt':None,'summary':value}
    elif batch[1] == 'ABANDONED':
        row = db.execute('SELECT result_json FROM mutation_abandonments WHERE batch_id=?',(str(batch_id),)).fetchone()
        result = json.loads(row[0]) if row else {'state':'ABANDONED','receipt':None,'summary':{'batch_id':str(batch_id),'state':'ABANDONED'}}
    elif batch[1] == 'PUBLISHED' or completion:
        raise failure('batch terminal authority is inconsistent')
    if result is not None:
        return {**result,'metrics':{'terminal_replay':True}}
    return None


def abandon_owned(owner):
    with owner._database.transaction(write=True) as db:
        result = terminal_in(db, owner.token.batch_id)
        if result is None:
            owner.require(owner._database,db)
            result = {'state':'ABANDONED','receipt':None,
                      'summary':{'batch_id':str(owner.token.batch_id),'kb_id':str(owner.token.kb_id),
                                 'state':'ABANDONED','owner_epoch':owner.token.owner_epoch,'abandoned_at':now()}}
            db.execute('INSERT INTO mutation_abandonments VALUES(?,?)',(str(owner.token.batch_id),canonical_json(result)))
            db.execute("UPDATE mutation_batches SET state='ABANDONED',recovery_stage=NULL WHERE batch_id=?",(str(owner.token.batch_id),))
            db.execute('UPDATE libraries SET pending_mutation_id=NULL WHERE kb_id=? AND pending_mutation_id=?',
                       (str(owner.token.kb_id),str(owner.token.batch_id)))
            db.execute('DELETE FROM revision_dependencies WHERE batch_id=?',(str(owner.token.batch_id),))
    owner._lock.close()
    return result


def start_io(catalog, owner, kind, *, artifact_id=None, detail=None):
    identity = uuid4()
    birth = process_birth(os.getpid())
    if birth is None:
        raise failure('cannot identify actual IO interpreter')
    with catalog._owned(owner,owner.token) as db:
        db.execute('INSERT INTO mutation_io VALUES(?,?,?,?,?,?,?,?,NULL,NULL,?,?)',
                   (str(identity),str(owner.token.batch_id),str(artifact_id) if artifact_id else None,
                    owner.token.owner_epoch,str(owner.token.owner_nonce),kind,os.getpid(),birth,
                    'pending',canonical_json(detail or {})))
    return identity


def observe_io(catalog, identity, *, finished=False, worker=None):
    """Only the actual submitting process can acknowledge its late completion."""
    with catalog._db.transaction(write=True) as db:
        row = db.execute('SELECT pid,process_birth,state,worker_pid,worker_birth FROM mutation_io WHERE io_id=?',(str(identity),)).fetchone()
        if row is None or row[:2] != (os.getpid(),process_birth(os.getpid())):
            raise failure('IO receipt belongs to another actual interpreter')
        if row[2] == 'finished':
            return
        if worker is not None:
            pair = (worker['pid'],worker['process_birth'])
            if row[3] is not None and row[3:] != pair:
                raise failure('worker request identity changed')
            db.execute('UPDATE mutation_io SET worker_pid=?,worker_birth=? WHERE io_id=?',(*pair,str(identity)))
        if finished:
            db.execute("UPDATE mutation_io SET state='finished' WHERE io_id=?",(str(identity),))


def unresolved_io(catalog, batch_id):
    with catalog._db.transaction() as db:
        rows = db.execute("SELECT io_id,artifact_id,kind,pid,process_birth,worker_pid,worker_birth FROM mutation_io WHERE batch_id=? AND state='pending'",
                          (str(batch_id),)).fetchall()
    pending = []
    for row in rows:
        # Worker death proves compute finished, but an SDK request can outlive
        # its submitting process. Never infer service completion from PID death.
        if row[2] == 'model' and row[5] is not None:
            try:
                if process_birth(row[5]) != row[6]:
                    continue
            except (OSError, PermissionError):
                pass
        pending.append(dict(zip(('io_id','artifact_id','kind','pid','process_birth','worker_pid','worker_birth'),row)))
    return pending


def cleanup_candidates(catalog, batch_id, backend):
    """Explicit retry for abandoned candidates only; general history GC is R15."""
    from .ownership import lock_for
    from .publication import artifact
    batch = catalog.get_batch(batch_id)
    results = []
    with lock_for(catalog._db,batch.kb_id):
        result = terminal(catalog,batch_id)
        if result is None or result['state'] != 'ABANDONED':
            return []  # Published/no-change replay cannot delete old history.
        snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
        from ..indexes.milvus import MilvusRevisionIndex
        if type(backend) is not MilvusRevisionIndex or backend.catalog is not catalog or backend.storage != snapshot.resolved_config.storage:
            raise failure('candidate cleanup backend differs from frozen catalog/endpoint')
        with catalog._db.transaction() as db:
            ids = [UUID(r[0]) for r in db.execute('SELECT revision_id FROM index_artifacts WHERE batch_id=? ORDER BY owner_epoch',(str(batch_id),))]
        pending = unresolved_io(catalog,batch_id)
        for revision_id in ids:
            stored = artifact(catalog,revision_id)
            value = {'artifact_id':stored['artifact_id'],'revision_id':str(revision_id),'collection_name':stored['collection_name']}
            affected = [io for io in pending if io['artifact_id'] in (None,stored['artifact_id'])]
            try:
                with catalog._db.transaction(write=True) as db:
                    dependent = db.execute('SELECT 1 FROM revision_dependencies WHERE revision_id=? UNION ALL '
                        "SELECT 1 FROM run_pins WHERE revision_id=? AND state='active' UNION ALL "
                        'SELECT 1 FROM libraries WHERE current_revision_id=? UNION ALL SELECT 1 FROM publications WHERE revision_id=?',
                        (str(revision_id),)*4).fetchone()
                    if dependent or affected:
                        value.update(state='retained',reason='active or uncertain dependency',pending_io=affected)
                    elif stored['state'] == 'RECLAIMED':
                        value.update(state='reclaimed',reason='prior successful cleanup')
                    elif not backend.has_ownership(stored):
                        value.update(state='retained',reason='no reliable persisted physical ownership')
                    else:
                        db.execute("UPDATE index_artifacts SET state='RECLAIMING' WHERE artifact_id=?",(stored['artifact_id'],))
                        db.execute("UPDATE revisions SET index_state='RECLAIMING' WHERE revision_id=?",(str(revision_id),))
                if 'state' not in value:
                    backend.drop_owned(stored)
                    with catalog._db.transaction(write=True) as db:
                        db.execute("UPDATE index_artifacts SET state='RECLAIMED' WHERE artifact_id=?",(stored['artifact_id'],))
                        db.execute("UPDATE revisions SET index_state='RECLAIMED' WHERE revision_id=?",(str(revision_id),))
                    value.update(state='reclaimed',reason='matching physical ownership; drop acknowledged')
            except Exception as exc:
                value.update(state='retained',reason=f'{type(exc).__name__}: {exc}')
            with catalog._db.transaction(write=True) as db:
                db.execute('INSERT INTO candidate_cleanup_attempts VALUES(?,?,?,?)',
                           (str(uuid4()),stored['artifact_id'],canonical_json(value),now()))
            results.append(value)
    return results
