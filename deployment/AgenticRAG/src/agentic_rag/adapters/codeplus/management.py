"""Existing host commands over the core library lifecycle.

The worker owns the real mutation and its transports until it returns. Cancelling
the host waiter requests cooperative cancellation and drains that same worker.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import threading
from uuid import UUID, uuid4

from ...config import DevelopmentConfig, ProcessingSnapshot
from ...domain import ErrorCode, RagError
from ...ingestion import (InputSelection, begin_changes, build_changes, retry_failed,
    mutation_summary, inspect_recovery, continue_recovery, abandon_recovery)
from ...model_switch import inspect_model_switch, apply_model_switch
from ...storage import Catalog, OwnerToken


def load_settings(path):
    if not path:
        raise ValueError('Set knowledge_development_config to an absolute configuration file path.')
    target = Path(path)
    if not target.is_absolute():
        raise ValueError('knowledge_development_config requires an absolute path.')
    try:
        return DevelopmentConfig.model_validate_json(target.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        # Validation errors may echo input values. Product diagnostics never do.
        raise ValueError('Knowledge configuration is unavailable or invalid; check its path and fields.') from error


@contextmanager
def mutation_runtime(catalog, settings, frozen):
    from ...indexes.milvus import MilvusRevisionIndex
    from ...models import FrozenTokenizer, LocalModelClient
    provider = backend = None
    try:
        tokenizer = FrozenTokenizer(frozen.embedding, settings.worker.model_cache)
        provider = LocalModelClient(settings.worker)
        backend = MilvusRevisionIndex(frozen.storage, catalog)
        yield tokenizer, provider, backend
    finally:
        if provider is not None:
            # RequestHandle.result may finish before an expired GPU request.
            # Hold this operation's real owner until its work has actually left
            # the shared worker, then close only this client.
            with provider.lock:
                handles = tuple(provider.handles.values())
            for handle in handles:
                if not handle.execution_finished:
                    handle.cancel()
                    handle.wait_finished()
            provider.close()
        if backend is not None:
            backend.close()
            backend.wait_closed()


def _result(action, data, status='completed', reason='finished'):
    return {'action': action, 'status': status, 'stop_reason': reason, 'data': data}


def _selected_batch(catalog, kb_id, batch_id):
    batch = catalog.get_batch(UUID(batch_id))
    if batch.kb_id != kb_id:
        raise RagError(ErrorCode.SCOPE_MISMATCH, 'batch belongs to another library', stage='command')
    return batch


def _status(catalog, settings, kb_id):
    library = catalog.get_library(kb_id)
    value = library.model_dump(mode='json')
    value.update(enabled=True, default_mode=settings.knowledge.retrieval.mode,
                 desired_embedding=settings.knowledge.embedding.model_dump(mode='json'))
    if library.current_revision_id:
        value['model'] = inspect_model_switch(catalog, kb_id, settings.knowledge)
    if library.pending_mutation_id:
        batch = catalog.get_batch(library.pending_mutation_id)
        value['pending'] = {'batch': batch.model_dump(mode='json'),
                            'summary': mutation_summary(catalog, batch.batch_id)}
        try:
            value['pending']['recovery'] = inspect_recovery(catalog, batch.batch_id, current_config=settings.knowledge)
        except RagError as error:
            if error.error.code != ErrorCode.LIBRARY_BUSY:
                raise
            value['pending']['active'] = True
    return value


def run_management(settings, action, kb_id=None, *, arguments=(), options=None, cancelled=None):
    """Synchronous adapter; all transaction/CAS/build decisions remain in core."""
    options = options or {}
    catalog = Catalog(settings.knowledge.storage.data_dir)
    kb_id = UUID(kb_id) if kb_id else None
    runtime = lambda frozen: mutation_runtime(catalog, settings, frozen)
    if cancelled is not None and cancelled():
        raise RagError(ErrorCode.CANCELLED, 'command cancelled', stage='command')
    if action == 'create':
        return _result(action, catalog.create_library(arguments[0]).model_dump(mode='json'))
    if action == 'status' and kb_id is None:
        return _result(action, {'enabled': True, 'default_mode': settings.knowledge.retrieval.mode,
            'libraries': [library.model_dump(mode='json') for library in catalog.list_libraries()]})
    if kb_id is None:
        raise ValueError('Select a library with /knowledge use <id>, or pass --knowledge-library with -p.')
    library = catalog.get_library(kb_id)
    if action == 'use':
        return _result(action, library.model_dump(mode='json'))
    if action == 'status':
        return _result(action, _status(catalog, settings, kb_id))
    if action == 'sources':
        revision = UUID(options['revision']) if options.get('revision') else None
        return _result(action, catalog.list_sources(kb_id, revision_id=revision))
    if action == 'open':
        from ...citations import open_citation
        value = open_citation(catalog, UUID(arguments[0]))
        if value['source_ref']['kb_id'] != str(kb_id):
            raise RagError(ErrorCode.SCOPE_MISMATCH, 'citation belongs to another library', stage='command')
        return _result(action, value)
    if action in {'recover', 'abandon'}:
        if not arguments:
            return _result(action, _status(catalog, settings, kb_id), 'waiting_confirmation', 'select_batch')
        batch = _selected_batch(catalog, kb_id, arguments[0])
        plan = inspect_recovery(catalog, batch.batch_id, current_config=settings.knowledge)
        if plan['state'] != 'WAITING_RECOVERY':
            return _result(action, plan)
        choice = 'abandon' if action == 'abandon' else options.get('choice')
        if not choice:
            return _result(action, plan, 'waiting_confirmation', 'recovery_choice_required')
        # The exact inspected owner token is the core's CAS selection.
        expected = OwnerToken(**{key: UUID(val) if key != 'owner_epoch' else val
                                 for key, val in plan['expected'].items()})
        if choice == 'abandon':
            return _result(action, abandon_recovery(catalog, expected))
        result = continue_recovery(catalog, expected, runtime_factory=runtime, cancelled=cancelled)
    elif action == 'model':
        if not arguments:
            view = inspect_model_switch(catalog, kb_id, settings.knowledge)
        else:
            view = apply_model_switch(catalog, kb_id, UUID(arguments[0]), settings.knowledge,
                choice=options.get('choice'), runtime_factory=runtime, cancelled=cancelled)
        if view['state'] == 'BUILDING':
            return _result(action, view, 'failed', 'library_busy')
        waiting = view['state'] in {'WAITING_CONFIRMATION', 'WAITING_RECOVERY', 'STALE_PROPOSAL'}
        return _result(action, view, 'waiting_confirmation' if waiting else 'completed',
                       view['state'].lower() if waiting else 'finished')
    else:
        # Pending work takes precedence over new import/delete/rebuild commands.
        if library.pending_mutation_id:
            pending = _status(catalog, settings, kb_id)
            if pending.get('pending', {}).get('active'):
                pending['message'] = 'This library is being modified; wait for the active operation to finish.'
                return _result(action, pending, 'failed', 'library_busy')
            return _result(action, pending, 'waiting_confirmation', 'pending_recovery')
        desired = settings.knowledge
        if library.current_revision_id:
            view = inspect_model_switch(catalog, kb_id, desired)
            if view['state'] not in {'CURRENT', 'PUBLISHED', 'KEPT_ORIGINAL'}:
                return _result(action, view, 'waiting_confirmation', 'model_choice_required')
            if view['state'] == 'KEPT_ORIGINAL':
                revision = catalog.get_revision(kb_id, library.current_revision_id)
                desired = catalog.get_snapshot(revision.processing_snapshot_id).resolved_config
        if action == 'retry':
            batch = _selected_batch(catalog, kb_id, arguments[0])
            owner = retry_failed(catalog, batch.batch_id, accept_input_changes=options.get('accept_input_changes', False))
        else:
            selections, deleted = (), ()
            if action == 'remove':
                deleted = tuple(UUID(arg) for arg in arguments)
            elif action == 'reimport':
                selections = (InputSelection(document_id=UUID(arguments[0]), path=arguments[1]),)
            elif action == 'import':
                selections = tuple(InputSelection(path=arg) for arg in arguments)
            else:
                raise ValueError('Unknown knowledge management action.')
            owner = begin_changes(catalog, kb_id, ProcessingSnapshot.capture(uuid4(), desired),
                                  selections, delete_document_ids=deleted)
        # Owner acquisition, work and close all occur on this one real thread.
        with owner:
            frozen = catalog.get_snapshot(catalog.get_batch(owner.token.batch_id).processing_snapshot_id).resolved_config
            with runtime(frozen) as (tokenizer, provider, backend):
                result = build_changes(catalog, owner, provider, backend, tokenizer, cancelled=cancelled)
    summary = result.get('summary', {})
    return _result(action, result, 'partial' if summary.get('failed') else 'completed',
                   'file_failures' if summary.get('failed') else 'finished')


async def execute_management(path, action, kb_id=None, *, arguments=(), options=None):
    settings = load_settings(path)
    cancel = threading.Event()
    def work():
        try:
            return run_management(settings, action, kb_id, arguments=arguments, options=options, cancelled=cancel.is_set)
        except Exception as error:
            known = isinstance(error, RagError)
            detail = error.error.model_dump(mode='json') if known else {
                'code': 'command_error', 'message': str(error) if isinstance(error, ValueError) else type(error).__name__}
            data = {'error': detail}
            if kb_id:
                catalog = Catalog(settings.knowledge.storage.data_dir)
                try:
                    library = catalog.get_library(UUID(kb_id))
                    data['library'] = library.model_dump(mode='json')
                    if library.pending_mutation_id:
                        data['summary'] = mutation_summary(catalog, library.pending_mutation_id)
                except (RagError, ValueError):
                    pass
            return _result(action, data, 'cancelled' if known and error.error.code == ErrorCode.CANCELLED else 'failed',
                           'user_cancelled' if known and error.error.code == ErrorCode.CANCELLED else 'explicit_error')
    task = asyncio.create_task(asyncio.to_thread(work))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        # A second Ctrl-C/disconnect must not release the owner or transports
        # before the thread's provider/SDK call and mutation context have ended.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        result = task.result()
        # Publication may have committed before cancellation was observed. Keep
        # the actual receipt/summary as well as the caller's cancellation status.
        return {**result, 'operation_status': result['status'],
                'status': 'cancelled', 'stop_reason': 'user_cancelled'}
