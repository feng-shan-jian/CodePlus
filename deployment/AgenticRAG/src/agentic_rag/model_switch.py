"""Explicit, single-library model choices over the existing import lifecycle.

Inspection records a proposal only. A choice binds its library, published base
and encoding/index target. Recovery, publication and GC retain their authority.
"""

from contextlib import ExitStack
import json
from uuid import UUID, uuid4

from .config import KnowledgeConfig, ProcessingSnapshot, document_encoding_identity, index_identity
from .domain import ErrorCode, RagError
from .ingestion.records import InputCheckpoint, InputManifest, InputSelection
from .storage import inputs, recovery
from .storage.paths import failure


def _current(db, kb_id):
    row = db.execute('SELECT l.name,l.current_revision_id,l.pending_mutation_id,s.resolved_config '
        'FROM libraries l JOIN revisions r ON r.revision_id=l.current_revision_id AND r.kb_id=l.kb_id '
        'JOIN processing_snapshots s ON s.snapshot_id=r.processing_snapshot_id '
        "WHERE l.kb_id=? AND r.index_state='READY'", (str(kb_id),)).fetchone()
    if row is None:
        raise RagError(ErrorCode.NOT_READY, 'model switching requires a published library', stage='model_switch')
    return row[:3], KnowledgeConfig.model_validate_json(row[3])


def _read(db, proposal_id, kb_id):
    row = db.execute('SELECT kb_id,base_revision_id,target_fingerprint,target_snapshot,decision,batch_id,errors '
                     'FROM model_switches WHERE proposal_id=?', (str(proposal_id),)).fetchone()
    if row is None or row[0] != str(kb_id):
        raise RagError(ErrorCode.SCOPE_MISMATCH, 'proposal belongs to a different library or does not exist', stage='model_switch')
    value = dict(zip(('kb_id','base_revision_id','target_fingerprint','target_snapshot','decision','batch_id','errors'), row))
    value['snapshot'] = ProcessingSnapshot.model_validate_json(value.pop('target_snapshot'))
    value['errors'] = json.loads(value['errors'])
    value['attempts'] = db.execute('SELECT count(*) FROM mutation_executions WHERE batch_id=?', (value['batch_id'],)).fetchone()[0]
    return value


def _encoding(config):
    return {'embedding': config.embedding.model_dump(mode='json'),
            'document_encoding_fingerprint': document_encoding_identity(config), 'index_fingerprint': index_identity(config)}


def _inspect_execution(catalog, kb_id):
    with catalog._db.transaction() as db:
        active = db.execute('SELECT 1 FROM libraries l JOIN model_switches p ON p.batch_id=l.pending_mutation_id '
            "JOIN mutation_batches b ON b.batch_id=p.batch_id WHERE l.kb_id=? AND b.state!='WAITING_RECOVERY'",
            (str(kb_id),)).fetchone()
    if active:
        try:
            catalog.identify_interrupted(kb_id)
        except RagError as exc:
            if exc.error.code != ErrorCode.LIBRARY_BUSY:
                raise


def _view(db, kb_id, desired, proposal_id=None):
    current, actual = _current(db, kb_id)
    value = {'kb_id': str(kb_id), 'library_name': current[0], 'current_revision_id': current[1],
             'pending_batch_id': current[2], 'desired': _encoding(desired), 'actual': _encoding(actual),
             'proposal_id': str(proposal_id) if proposal_id else None, 'state': 'CURRENT', 'choices': []}
    if proposal_id is None:
        return value
    proposal = _read(db, proposal_id, kb_id)
    value.update(scope=[{'kb_id': str(kb_id), 'name': current[0], 'base_revision_id': proposal['base_revision_id']}],
                 target=proposal['snapshot'].model_dump(mode='json'), batch_id=proposal['batch_id'],
                 attempts=proposal['attempts'], errors=proposal['errors'])
    same_target = index_identity(desired) == proposal['target_fingerprint'] and desired.storage == proposal['snapshot'].resolved_config.storage
    value['target_matches_desired'] = same_target
    terminal = recovery.terminal_in(db, proposal['batch_id']) if proposal['batch_id'] else None
    if terminal and terminal['state'] == 'PUBLISHED':
        value.update(state='PUBLISHED', receipt=terminal['receipt'])
    elif proposal['decision'] == 'kept' or terminal and terminal['state'] == 'ABANDONED':
        value['state'] = 'KEPT_ORIGINAL'
    elif current[1] != proposal['base_revision_id'] or not same_target:
        value['state'] = 'STALE_PROPOSAL'
        value['choices'] = ['keep_original'] if proposal['decision'] == 'approved' else []
    elif proposal['decision'] == 'pending':
        value.update(state='WAITING_CONFIRMATION', choices=['confirm', 'keep_original'])
    else:
        value.update(state='WAITING_RECOVERY', choices=['retry', 'keep_original'])
        if proposal['batch_id']:
            batch_state, = db.execute('SELECT state FROM mutation_batches WHERE batch_id=?', (proposal['batch_id'],)).fetchone()
            value['batch_state'] = batch_state
            if batch_state != 'WAITING_RECOVERY':
                value.update(state='BUILDING', choices=[])
    return value


def inspect_model_switch(catalog, kb_id, desired):
    """Persist/display an exact proposal. No mutation lease, source or model IO."""
    _inspect_execution(catalog, kb_id)
    with catalog._db.transaction(write=True) as db:
        current, actual = _current(db, kb_id)
        # Surface an unfinished approved choice even when defaults changed.
        active = db.execute("SELECT p.proposal_id FROM model_switches p LEFT JOIN mutation_batches b ON b.batch_id=p.batch_id "
            "WHERE p.kb_id=? AND p.decision='approved' AND (p.batch_id IS NULL OR b.state NOT IN ('PUBLISHED','ABANDONED')) "
            'ORDER BY p.rowid DESC LIMIT 1', (str(kb_id),)).fetchone()
        if active:
            return _view(db, kb_id, desired, active[0])
        if index_identity(desired) == index_identity(actual):
            return _view(db, kb_id, desired)
        if desired.storage != actual.storage:
            raise RagError(ErrorCode.INVALID_CONFIGURATION, 'model switch cannot relocate the published store or endpoint', stage='model_switch')
        key = index_identity(desired)
        # Keeping a target is durable even if later ordinary updates move current.
        kept = db.execute("SELECT p.proposal_id FROM model_switches p LEFT JOIN mutation_batches b ON b.batch_id=p.batch_id "
            "WHERE p.kb_id=? AND p.target_fingerprint=? AND (p.decision='kept' OR b.state='ABANDONED') "
            'ORDER BY p.rowid DESC LIMIT 1', (str(kb_id), key)).fetchone()
        if kept:
            return _view(db, kb_id, desired, kept[0])
        old = db.execute("SELECT proposal_id FROM model_switches WHERE kb_id=? AND base_revision_id=? AND target_fingerprint=? "
                         "AND decision='pending' ORDER BY rowid DESC LIMIT 1", (str(kb_id), current[1], key)).fetchone()
        proposal_id = old[0] if old else str(uuid4())
        if not old:
            snapshot = ProcessingSnapshot.capture(uuid4(), desired)
            db.execute('INSERT INTO model_switches(proposal_id,kb_id,base_revision_id,target_fingerprint,target_snapshot,created_at) '
                       'VALUES(?,?,?,?,?,?)', (proposal_id, str(kb_id), current[1], key, snapshot.model_dump_json(), recovery.now()))
        return _view(db, kb_id, desired, proposal_id)


def _archived_inputs(catalog, base_revision_id):
    with catalog._db.transaction() as db:
        rows = db.execute('SELECT m.document_id,v.raw_hash,v.source_uri,i.result_json FROM revision_members m '
            'JOIN document_versions v ON v.document_version_id=m.document_version_id '
            'LEFT JOIN processing_items p ON p.document_version_id=m.document_version_id '
            'LEFT JOIN input_results i ON i.item_id=p.item_id WHERE m.revision_id=? ORDER BY m.document_id',
            (str(base_revision_id),)).fetchall()
    selections, entries, archived = [], [], {}
    for doc_id, raw_hash, uri, record in rows:
        if record is None:
            raise failure('published member has no original input checkpoint')
        original = InputCheckpoint.model_validate_json(record)
        if original.raw is None or str(original.document_id) != doc_id or (original.raw.sha256, original.raw.source_uri) != (raw_hash, uri):
            raise failure('published member differs from its archived original')
        entry = original.entry.model_copy(update={'item_id': uuid4(), 'document_id': UUID(doc_id), 'selection_index': len(selections)})
        selections.append(InputSelection(path=entry.requested_path, document_id=entry.document_id))
        entries.append(entry)
        archived[entry.item_id] = original.raw
    return InputManifest(selections=tuple(selections), entries=tuple(entries)), archived


def require_rebuild(catalog, batch):
    """All-member approval gate shared by build, recovery and manifest validation."""
    with catalog._db.transaction() as db:
        row = db.execute("SELECT proposal_id FROM model_switches WHERE kb_id=? AND batch_id=? AND decision='approved'",
                         (str(batch.kb_id), str(batch.batch_id))).fetchone()
        if row is None:
            raise failure('existing-library strict build requires its approved model switch')
        proposal = _read(db, row[0], batch.kb_id)
        if proposal['base_revision_id'] != str(batch.base_revision_id) or proposal['snapshot'].snapshot_id != batch.processing_snapshot_id:
            raise failure('rebuild batch differs from approved base or target')
        expected = set(db.execute('SELECT document_id,document_version_id FROM revision_members WHERE revision_id=?',
                                  (str(batch.base_revision_id),)))
        actual = set(db.execute('SELECT document_id,base_version_id FROM input_items WHERE batch_id=?', (str(batch.batch_id),)))
        if actual != expected:
            raise failure('approved rebuild must cover every published member exactly')


def _begin(catalog, kb_id, proposal_id):
    with catalog._db.transaction() as db:
        proposal = _read(db, proposal_id, kb_id)
    manifest, archived = _archived_inputs(catalog, proposal['base_revision_id'])
    def register(db, batch):
        live = _read(db, proposal_id, kb_id)
        if (live['decision'] != 'pending' or live['batch_id'] is not None or
                live['base_revision_id'] != str(batch.base_revision_id)):
            raise failure('proposal or published base changed before rebuild admission')
        db.execute("UPDATE model_switches SET decision='approved',batch_id=? WHERE proposal_id=?", (str(batch.batch_id), str(proposal_id)))
    return inputs.begin_import(catalog, kb_id, proposal['snapshot'], manifest, _archived=archived, _register=register)


def apply_model_switch(catalog, kb_id, proposal_id, desired, *, choice=None, runtime_factory=None,
                       observer=None, request_seconds=120, index_timeout=180):
    """Execute only an explicit confirm/retry/keep_original choice for this scope.

    A runtime factory follows R14: frozen config -> tokenizer, provider, backend,
    optionally managed by a context manager. Empty/no choice only returns state.
    """
    _inspect_execution(catalog, kb_id)
    with catalog._db.transaction() as db:
        view = _view(db, kb_id, desired, proposal_id)
        proposal = _read(db, proposal_id, kb_id)
    if choice is None or choice == '' or view['state'] in ('PUBLISHED', 'KEPT_ORIGINAL', 'BUILDING'):
        return view
    if choice not in ('confirm', 'retry', 'keep_original'):
        raise ValueError('choose confirm, retry or keep_original explicitly')
    if choice == 'keep_original':
        if proposal['batch_id']:
            from .ingestion.recovery import abandon_recovery
            token = catalog.identify_interrupted(kb_id)
            if token is not None and str(token.batch_id) == proposal['batch_id']:
                abandon_recovery(catalog, token)
            else:
                with catalog._db.transaction() as db:
                    return _view(db, kb_id, desired, proposal_id)
        with catalog._db.transaction(write=True) as db:
            # A confirmation can have acquired the library since this view was
            # read; a pending choice must never cancel that live mutation.
            live = _read(db, proposal_id, kb_id)
            if live['batch_id'] != proposal['batch_id']:
                return _view(db, kb_id, desired, proposal_id)
            db.execute("UPDATE model_switches SET decision='kept' WHERE proposal_id=?", (str(proposal_id),))
            return _view(db, kb_id, desired, proposal_id)
    if view['state'] == 'STALE_PROPOSAL':
        return {**view, 'current_proposal': inspect_model_switch(catalog, kb_id, desired)}
    if choice == 'retry' and proposal['decision'] != 'approved':
        return view  # A retry is not initial authorization.
    if choice == 'confirm' and proposal['decision'] == 'approved':
        return view  # Repeated confirm never silently retries a failed build.
    if runtime_factory is None:
        raise ValueError('confirmed execution requires a runtime_factory')
    with catalog._db.transaction(write=True) as db:
        live = _read(db, proposal_id, kb_id)
        current, _ = _current(db, kb_id)
        if current[1] != live['base_revision_id'] or live['decision'] != proposal['decision']:
            return _view(db, kb_id, desired, proposal_id)
        if current[2] is not None and current[2] != live['batch_id']:
            raise RagError(ErrorCode.LIBRARY_BUSY, 'finish or abandon the existing library mutation first', stage='model_switch')
        other = db.execute("SELECT 1 FROM model_switches p LEFT JOIN mutation_batches b ON b.batch_id=p.batch_id "
            "WHERE p.kb_id=? AND p.proposal_id!=? AND p.decision='approved' "
            "AND (p.batch_id IS NULL OR b.state NOT IN ('PUBLISHED','ABANDONED'))",
            (str(kb_id), str(proposal_id))).fetchone()
        if other:
            raise RagError(ErrorCode.LIBRARY_BUSY, 'finish or keep the previous approved model switch first', stage='model_switch')
    try:
        if proposal['batch_id']:
            from .ingestion.recovery import continue_recovery
            token = catalog.identify_interrupted(kb_id)
            if token is None or str(token.batch_id) != proposal['batch_id']:
                raise failure('approved recovery no longer owns the pending batch')
            result = continue_recovery(catalog, token, runtime_factory=runtime_factory, observer=observer,
                                       request_seconds=request_seconds, index_timeout=index_timeout)
        else:
            from .ingestion.build import _build_complete_revision
            from .ingestion.processing import process_inputs
            with _begin(catalog, kb_id, proposal_id) as owner, ExitStack() as stack:
                runtime = runtime_factory(proposal['snapshot'].resolved_config)
                if hasattr(runtime, '__enter__'):
                    runtime = stack.enter_context(runtime)
                tokenizer, provider, backend = runtime
                process_inputs(catalog, owner, tokenizer)
                result = _build_complete_revision(catalog, owner, provider, backend, observer=observer,
                    request_seconds=request_seconds, index_timeout=index_timeout)
        with catalog._db.transaction() as db:
            return {**_view(db, kb_id, desired, proposal_id), 'build': result}
    except Exception as exc:
        error = exc.error.model_dump(mode='json') if isinstance(exc, RagError) else {'type': type(exc).__name__, 'message': str(exc)}
        with catalog._db.transaction(write=True) as db:
            errors = _read(db, proposal_id, kb_id)['errors']
            db.execute('UPDATE model_switches SET errors=? WHERE proposal_id=?',
                       (json.dumps([*errors, error]), str(proposal_id)))
            return _view(db, kb_id, desired, proposal_id)
