"""Explicit recovery API. Opening Catalog never starts work or loads models."""

from contextlib import ExitStack
from dataclasses import asdict
import io
import hashlib
from uuid import uuid4

from .._schema import canonical_json
from ..domain import ErrorCode, RagError
from ..storage.paths import failure
from ..storage import recovery as store
from .capture import capture_inputs, read_input
from .encoding import outcomes, read_encoded
from .processing import process_inputs, read_processed


def _token(catalog, expected):
    batch = catalog.get_batch(expected.batch_id)
    if expected.store_id != catalog.store_id or expected.kb_id != batch.kb_id:
        raise failure('recovery selection belongs to another catalog or library')
    return batch


def _differences(original, current, prefix=''):
    output = []
    for key in sorted(set(original) | set(current)):
        before, after = original.get(key), current.get(key)
        path = prefix + key
        if isinstance(before,dict) and isinstance(after,dict):
            output.extend(_differences(before,after,path+'.'))
        elif before != after:
            output.append({'field':path,'frozen':before,'current_default':after})
    return output


def inspect_recovery(catalog, batch_id, *, current_config=None):
    """Persist a check/plan, return a CAS token; execute nothing without choice."""
    old = store.terminal(catalog,batch_id)
    if old is not None:
        return old
    batch = catalog.get_batch(batch_id)
    expected = catalog.identify_interrupted(batch.kb_id)
    if expected is None or expected.batch_id != batch_id:
        # Another process may have completed between the initial read and lock.
        old = store.terminal(catalog,batch_id)
        if old is not None:
            return old
        raise failure('selected batch is no longer the pending operation')
    batch = catalog.get_batch(batch_id)
    snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
    from .mutations import request
    strict = request(catalog,batch_id) is None
    value = {'state':'WAITING_RECOVERY','batch_id':str(batch_id),'kb_id':str(batch.kb_id),
             'base_revision_id':str(batch.base_revision_id) if batch.base_revision_id else None,
             'expected':{k:str(v) if k!='owner_epoch' else v for k,v in asdict(expected).items()},
             'frozen_snapshot':snapshot.model_dump(mode='json'),
             'default_differences':_differences(snapshot.resolved_config.model_dump(mode='json'),current_config.model_dump(mode='json')) if current_config else [],
             'items':[],'can_continue':True,'strict_all_files':strict,
             'execution_environment':'not_checked; validated only when runtime_factory opens frozen components',
             'actions':['continue','abandon'],'errors':[]}
    try:
        manifest = catalog.get_input_manifest(batch_id)
        value['input_manifest'] = manifest.model_dump(mode='json')
        states = outcomes(catalog,batch_id)
        for raw in catalog.get_input_items(batch_id):
            item = {'item_id':str(raw.entry.item_id),'path':raw.entry.requested_path,'raw_state':raw.stage,
                    'raw_hash':raw.raw.sha256 if raw.raw else None,'checkpoint':'uncommitted','reusable':False,'reason':None}
            try:
                if raw.stage == 'pending':
                    item.update(checkpoint='snapshot_failure',reason='original snapshot never committed; current source will not be read')
                elif raw.stage == 'failed':
                    item.update(checkpoint='file_failure',reason=raw.error.message)
                else:
                    read_input(catalog,batch_id,raw.entry.item_id)
                    checked = read_processed(catalog,batch_id,raw.entry.item_id)
                    state = states.get(str(raw.entry.item_id))
                    if state and state['state']=='encoded':
                        vectors = read_encoded(catalog,batch_id,raw,state)
                        item.update(checkpoint='encoded',reusable=True,chunks=len(vectors),reason='complete input/config/parsed/ordered Chunk/float32 proof verified')
                    elif state and state['state']=='unchanged':
                        item.update(checkpoint='unchanged',reusable=True,reason='original verified; complete base is rechecked before reuse')
                    elif state and state['state']=='failed' or checked and checked[0].stage=='failed':
                        item.update(checkpoint='file_failure',reason='immutable file failure remains terminal')
                    elif checked:
                        item.update(checkpoint='parsed',reusable=True,reason='complete processing verified; document encoding will resume from original')
                    else:
                        item.update(checkpoint='raw',reusable=True,reason='original verified; uncommitted parse/encoding recomputed')
            except (OSError,RagError,ValueError) as exc:
                item.update(checkpoint='invalid',reason=str(exc))
                value['can_continue'] = False
            if strict and item['checkpoint'] in ('snapshot_failure','file_failure'):
                value['can_continue'] = False
                item['reason'] += '; strict first build requires all original files to succeed'
            value['items'].append(item)
    except (OSError,RagError,ValueError) as exc:
        value['can_continue'] = False
        value['errors'].append(str(exc))
    value['pending_io'] = store.unresolved_io(catalog,batch_id)
    value['plan_id'] = str(uuid4())
    with catalog._db.transaction(write=True) as db:
        db.execute('INSERT INTO recovery_plans VALUES(?,?,?,?,?)',
                   (value['plan_id'],str(batch_id),expected.owner_epoch,canonical_json(value),store.now()))
    return value


def continue_recovery(catalog, expected, *, runtime_factory, observer=None, request_seconds=120, index_timeout=180):
    """Factory receives the frozen config, only after terminal/CAS validation.

    Return (tokenizer, provider, backend) or a context manager yielding that
    tuple. Callers own plain returned objects; context-managed resources close
    even on failure. No default config is overwritten and no retry_failed call
    creates a replacement batch.
    """
    batch = _token(catalog,expected)
    old = store.terminal(catalog,batch.batch_id)
    if old is not None:
        return old
    with catalog.resume_mutation(expected) as owner:
        # Validate all committed immutable checkpoints before loading the model.
        catalog.get_input_manifest(batch.batch_id)
        from .mutations import request, build_changes
        strict = request(catalog,batch.batch_id) is None
        capture_inputs(catalog,owner,abort_cancelled=True)
        for raw in catalog.get_input_items(batch.batch_id):
            if raw.stage == 'captured':
                read_input(catalog,batch.batch_id,raw.entry.item_id)
                checked = read_processed(catalog,batch.batch_id,raw.entry.item_id)
                if strict and checked and checked[0].stage == 'failed':
                    raise RagError(ErrorCode.CHECKPOINT_INVALID,'strict first build has an immutable processing failure',stage='recovery')
                read_encoded(catalog,batch.batch_id,raw)
            elif strict:
                raise RagError(ErrorCode.CHECKPOINT_INVALID,'strict first build lacks a complete original snapshot',stage='recovery')
        snapshot = catalog.get_snapshot(batch.processing_snapshot_id)
        with ExitStack() as stack:
            runtime = runtime_factory(snapshot.resolved_config)
            if hasattr(runtime,'__enter__'):
                runtime = stack.enter_context(runtime)
            tokenizer, provider, backend = runtime
            if not strict:
                return build_changes(catalog,owner,provider,backend,tokenizer,observer=observer,
                                     request_seconds=request_seconds,index_timeout=index_timeout)
            from .build import build_first_revision
            capture_inputs(catalog,owner,abort_cancelled=True)
            process_inputs(catalog,owner,tokenizer)
            return build_first_revision(catalog,owner,provider,backend,observer=observer,
                                        request_seconds=request_seconds,index_timeout=index_timeout)


def abandon_recovery(catalog, expected, *, backend=None):
    _token(catalog,expected)
    result = store.terminal(catalog,expected.batch_id)
    if result is not None:
        return result
    with catalog.resume_mutation(expected) as owner:
        result = owner.abandon()
    if result['state']=='ABANDONED' and backend is not None:
        store.cleanup_candidates(catalog,expected.batch_id,backend)
    return result


def repair_missing_original(catalog, batch_id, item_id, original_bytes):
    """Explicit exact-byte repair; corrupt existing immutable files still fail."""
    item = next((v for v in catalog.get_input_items(batch_id) if v.entry.item_id==item_id),None)
    if item is None or item.raw is None:
        raise RagError(ErrorCode.CHECKPOINT_INVALID,'no recorded original identity to repair',stage='recovery')
    if not isinstance(original_bytes,bytes) or len(original_bytes)!=item.raw.size_bytes or hashlib.sha256(original_bytes).hexdigest()!=item.raw.sha256:
        raise RagError(ErrorCode.CHECKPOINT_INVALID,'repair bytes differ from recorded original',stage='recovery')
    obj = catalog.archives.put(io.BytesIO(original_bytes))
    return obj.sha256
