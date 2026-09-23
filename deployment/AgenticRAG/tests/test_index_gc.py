"""R15 CPU protocol coverage. Real GPU/service/process evidence is separate."""

import json
import os
from pathlib import Path
import runpy
import threading
import time
from uuid import UUID, uuid4

import pytest

from agentic_rag.config import ProcessingSnapshot, resolve_run
from agentic_rag.domain import RagError, RunStatus
from agentic_rag.models.client import LocalModelClient, RequestHandle
from agentic_rag.models.identity import process_birth
from agentic_rag.storage import Catalog, publication
from agentic_rag.storage import gc, readers

H = runpy.run_path(str(Path(__file__).with_name('gc_support.py')))


@pytest.fixture
def s(tmp_path):
    value = H['setup'](tmp_path)
    yield value
    value.catalog._maintain_indexes = value.automatic_callback
    value.backend.close()
    assert value.backend.wait_closed(20)


def collect(s, artifact):
    return gc.collect(s.catalog, s.backend, artifact)


def test_current_complete_run_pin_and_mutation_base_protect_entire_revision(s):
    old = H['published'](s)
    lease = s.catalog.start_run(s.kb, resolve_run(s.config, 'qa'))
    assert collect(s, old)['state'] == 'retained'
    H['published'](s)
    assert collect(s, old)['state'] == 'retained'
    with s.catalog.begin_mutation(s.kb, ProcessingSnapshot.capture(uuid4(), s.config), 'a'*64) as owner:
        s.catalog.retain_revision(owner, UUID(old['revision_id']), 'vector_reuse')
        lease.finish(RunStatus.COMPLETED, 'finished')
        assert collect(s, old)['state'] == 'retained'
        owner.abandon()
    assert collect(s, old)['state'] == 'reclaimed'
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT count(*) FROM publications').fetchone() == (2,)
        assert db.execute('SELECT count(*) FROM archive_objects').fetchone() == (1,)
    with pytest.raises(RagError, match='published index'):
        publication.artifact(s.catalog, UUID(old['revision_id']), published=True)


def test_direct_sdk_admission_holds_terminal_pin_until_actual_call_returns(s):
    old = H['published'](s)
    lease = s.catalog.start_run(s.kb, resolve_run(s.config, 'qa'))
    entered, leave = threading.Event(), threading.Event()
    errors = []
    def blocked(name):
        entered.set()
        assert leave.wait(10)
        return [[]]
    s.client.search_hook = blocked
    def search():
        try: s.backend.search(old, 'old unseen document', field='sparse')
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=search)
    thread.start(); assert entered.wait(5)
    H['published'](s)
    lease.finish(RunStatus.CANCELLED, 'consumer_closed')
    lease.close()
    assert s.catalog.get_pin(lease.run.run_id).state == 'active'
    assert collect(s, old)['state'] == 'retained'
    leave.set(); thread.join(5); assert not thread.is_alive() and not errors
    s.catalog.maintain_indexes()
    assert s.catalog.get_pin(lease.run.run_id).state == 'released'
    assert not s.client.has_collection(old['collection_name'])


@pytest.mark.parametrize('fault', ['invalid_nprobe', 'bad_returned_row', 'timeout'])
def test_only_actual_unknown_rpc_retains_reader(s, fault):
    old = H['published'](s)
    lease = s.catalog.start_run(s.kb, resolve_run(s.config, 'qa'))
    if fault == 'bad_returned_row':
        s.client.search_hook = lambda name: [[{'entity': {'chunk_id': 'a'}, 'chunk_id': 'b', 'distance': 1}]]
    elif fault == 'timeout':
        def timeout(name): raise TimeoutError('accepted request has no completion proof')
        s.client.search_hook = timeout
    with pytest.raises((RagError, ValueError, TimeoutError)):
        s.backend.search(old, [1.0]+[0.0]*1023, nprobe=999 if fault == 'invalid_nprobe' else 1)
    assert len(s.client.calls) == (0 if fault == 'invalid_nprobe' else 1)
    H['published'](s)
    lease.finish(RunStatus.FAILED, 'explicit_error')
    with s.catalog._db.transaction() as db:
        pending = db.execute("SELECT kind FROM index_readers WHERE state='pending'").fetchall()
    assert pending == ([('milvus',)] if fault == 'timeout' else [])
    assert s.catalog.get_pin(lease.run.run_id).state == ('active' if fault == 'timeout' else 'released')
    assert collect(s, old)['state'] == ('retained' if fault == 'timeout' else 'reclaimed')


def test_claim_failure_retry_receipt_and_replaced_collection(s):
    old = H['published'](s); H['published'](s)
    original = s.client.collections[old['collection_name']]['collection_id']
    s.client.collections[old['collection_name']]['collection_id'] = 'replacement'
    result = collect(s, old)
    assert result['state'] == 'retained' and not s.client.dropped
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT state,attempt FROM index_gc_claims').fetchone() == ('failed', 1)
    s.client.collections[old['collection_name']]['collection_id'] = original
    assert collect(s, old)['state'] == 'reclaimed'
    assert s.client.dropped == [old['collection_name']]
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT state,attempt FROM index_gc_claims').fetchone() == ('reclaimed', 2)
        assert db.execute('SELECT count(*) FROM index_gc_attempts').fetchone() == (2,)


def test_actual_returned_reader_retries_local_receipt_failure(s, monkeypatch):
    from contextlib import contextmanager
    import apsw
    old = H['published'](s)
    lease = s.catalog.start_run(s.kb, resolve_run(s.config, 'qa'))
    reader = readers.Reader(s.catalog, old, 'milvus')
    original = s.catalog._db.transaction
    @contextmanager
    def busy(*, write=False):
        if write:
            raise apsw.BusyError('controlled first completion receipt write')
        with original() as db: yield db
    monkeypatch.setattr(s.catalog._db, 'transaction', busy)
    with pytest.raises(apsw.BusyError): reader.finish('call_returned')
    assert s.catalog._readers[reader.identity] is reader and reader.lock._fd is not None
    monkeypatch.setattr(s.catalog._db, 'transaction', original)
    H['published'](s)
    lease.finish(RunStatus.CANCELLED, 'consumer_closed')
    assert s.catalog.get_pin(lease.run.run_id).state == 'active'
    s.catalog.maintain_indexes()
    assert s.catalog.get_pin(lease.run.run_id).state == 'released'
    assert not s.client.has_collection(old['collection_name'])
    with original() as db:
        assert db.execute('SELECT state,completed_by FROM index_readers WHERE reader_id=?',
                          (str(reader.identity),)).fetchone() == ('finished', 'call_returned')


def test_collector_nonce_lock_and_unknown_legacy_claim_cannot_be_forged(s):
    old = H['published'](s); H['published'](s)
    with s.backend._lifecycle(old) as lock:
        claim, reason = gc._claim(s.catalog, s.backend, old, lock)
        assert reason is None
        with pytest.raises(RagError):
            s.backend.drop_owned(old, _claim=gc._Claim(s.catalog, old['artifact_id'], str(uuid4()), lock))
    assert collect(s, old)['state'] == 'retained'  # Original collector still alive.
    with s.catalog._db.transaction(write=True) as db:
        db.execute('DELETE FROM index_gc_claims')
    assert 'legacy reclaiming' in collect(s, old)['reason']
    assert not s.client.dropped


def test_legacy_run_without_lifetime_proof_remains_pinned(s):
    old = H['published'](s)
    lease = s.catalog.start_run(s.kb, resolve_run(s.config, 'qa'))
    lease._lock.close()
    with s.catalog._db.transaction(write=True) as db:
        db.execute('DROP TRIGGER immutable_run_lifetime_delete')
        db.execute('DELETE FROM run_lifetimes')
    H['published'](s)
    reopened = Catalog(s.catalog._directory.root)
    with pytest.raises(RagError, match='uncertain'):
        reopened.release_crashed_run(lease.run.run_id, lease.pin.owner_nonce)
    assert reopened.get_pin(lease.run.run_id).state == 'active'
    assert collect(s, old)['state'] == 'retained'


def test_pending_readers_scan_rotates_past_sixteen_unknown_calls(s):
    old = H['published'](s)
    for _ in range(16): readers.Reader(s.catalog, old, 'milvus').uncertain()
    reader = readers.Reader(s.catalog, old, 'model')
    # Explicit controlled birth mismatch, never used as real process evidence.
    reader.worker({'pid':os.getpid(), 'process_birth':'synthetic-not-current-birth',
                   'instance_id':str(uuid4()), 'session_id':str(uuid4())})
    reader.uncertain()
    readers.reconcile(s.catalog); readers.reconcile(s.catalog)
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT state FROM index_readers WHERE reader_id=?', (str(reader.identity),)).fetchone() == ('finished',)
        assert db.execute("SELECT count(*) FROM index_readers WHERE state='pending'").fetchone() == (16,)


def test_artifact_scan_rotates_past_unowned_history(s):
    for _ in range(20): H['published'](s, physical=False)
    eligible = H['published'](s); H['published'](s)
    for _ in range(15): s.catalog.maintain_indexes(limit=2)
    assert not s.client.has_collection(eligible['collection_name'])


def test_pin_scan_rotates_past_sixteen_live_runs(s):
    H['published'](s)
    leases = [s.catalog.start_run(s.kb, resolve_run(s.config, 'qa')) for _ in range(17)]
    last = leases[-1]
    last._lock.close()
    s.catalog._db._run_leases.pop(last.run.run_id)
    # Explicit controlled death identity. Actual process death is covered by
    # test_storage_processes and the installed R15 process acceptance driver.
    with s.catalog._db.transaction(write=True) as db:
        db.execute('DROP TRIGGER immutable_run_lifetime_update')
        db.execute('UPDATE run_lifetimes SET process_birth=? WHERE run_id=?',
                   ('synthetic-prior-birth', str(last.run.run_id)))
    s.catalog.maintain_indexes(); s.catalog.maintain_indexes()
    assert s.catalog.get_pin(last.run.run_id).state == 'released'
    assert all(s.catalog.get_pin(lease.run.run_id).state == 'active' for lease in leases[:-1])
    for lease in leases[:-1]: lease.close()


def test_automatic_backlog_runs_without_new_business_calls(s):
    old = [H['published'](s) for _ in range(17)]
    current = H['published'](s)
    s.catalog._maintain_indexes = s.automatic_callback
    s.catalog._maintain_indexes()
    end = time.monotonic()+35
    while len(s.client.dropped) != 17 and time.monotonic() < end:
        time.sleep(.05)
    assert set(s.client.dropped) == {a['collection_name'] for a in old}
    assert s.client.has_collection(current['collection_name'])


def test_matching_storage_and_multiple_adapter_close_order(s):
    old = H['published'](s); H['published'](s)
    duplicate = H['H']['constructed_backend'](s.catalog, s.config, s.client)
    modified = s.config.model_dump(mode='json'); modified['storage']['namespace'] = 'another_namespace'
    other_config = type(s.config).model_validate_json(json.dumps(modified))
    other = H['H']['constructed_backend'](s.catalog, other_config, H['Client']())
    duplicate.close()
    assert s.backend in s.catalog._index_backends and other in s.catalog._index_backends
    s.catalog.maintain_indexes()
    assert s.client.dropped == [old['collection_name']]
    other.close()


def test_only_adapter_close_drains_finite_backlog_and_closes_transport(s):
    old = [H['published'](s) for _ in range(17)]
    current = H['published'](s)
    s.catalog._maintain_indexes = s.automatic_callback
    started = time.monotonic(); s.backend.close()
    assert time.monotonic() - started < 1
    with pytest.raises(RagError, match='closing'):
        s.backend.search(current, 'new reader rejected', field='sparse')
    assert s.backend.wait_closed(35)
    assert set(s.client.dropped) == {a['collection_name'] for a in old}
    assert s.client.has_collection(current['collection_name'])


def test_adapter_registration_at_maintenance_thread_exit_restarts_dispatch(s):
    import weakref
    old = H['published'](s); H['published'](s)
    exiting, release = threading.Event(), threading.Event()
    class ExitGate(weakref.WeakSet):
        def __bool__(self):
            result = bool(len(self))
            if not result and threading.current_thread().name == 'old-index-maintenance':
                exiting.set(); assert release.wait(10)
            return result
    s.catalog._index_backends = ExitGate()
    s.catalog._maintain_indexes = s.automatic_callback
    previous = threading.Thread(target=s.catalog._maintenance_loop, name='old-index-maintenance')
    s.catalog._maintenance_thread = previous
    previous.start(); assert exiting.wait(5)
    new = H['H']['constructed_backend'](s.catalog, s.config, s.client)
    assert s.catalog._maintenance_thread is previous
    release.set(); previous.join(5); assert not previous.is_alive()
    end = time.monotonic()+5
    while not s.client.dropped and time.monotonic() < end: time.sleep(.02)
    assert s.client.dropped == [old['collection_name']]
    new.close(); assert new.wait_closed(10)
    # The original fixture adapter was explicitly removed to manufacture the
    # empty-set boundary; restore its registration for ordinary fixture close.
    s.catalog._index_backends.add(s.backend)


def test_knowledge_scope_reap_releases_pin_before_retiring_only_adapter(s, monkeypatch):
    from concurrent.futures import Future
    from types import SimpleNamespace
    from agentic_rag.adapters.codeplus.policy import KnowledgeScope
    old = H['published'](s)
    lease = s.catalog.start_run(s.kb, resolve_run(s.config, 'qa'))
    with s.catalog._db.transaction(write=True) as db:
        db.execute('INSERT INTO host_runs(run_id,frozen,started_ns) VALUES(?,?,?)', (str(lease.run.run_id), '{}', 1))
    H['published'](s)
    # Actual host-future completion can precede the durable completion writer.
    # Controlled handle completion, with a real Reader/nonce/native lock below.
    pending = readers.Reader(s.catalog, old, 'model')
    pending.worker({'pid':os.getpid(), 'process_birth':process_birth(os.getpid()),
                    'instance_id':str(uuid4()), 'session_id':str(uuid4())})
    pending.handle = SimpleNamespace(wait_finished=lambda timeout: True, completion_source='finished_frame')
    receipt_waiting, receipt_release = threading.Event(), threading.Event()
    original_finish = readers.Reader.finish
    def delayed_finish(reader, *args):
        if threading.current_thread().name == 'rag-query-completion':
            receipt_waiting.set(); assert receipt_release.wait(20)
        return original_finish(reader, *args)
    monkeypatch.setattr(readers.Reader, 'finish', delayed_finish)
    completion = threading.Thread(target=lambda: pending.finish('finished_frame'), name='rag-query-completion')
    completion.start(); assert receipt_waiting.wait(5)
    lease.finish(RunStatus.CANCELLED, 'consumer_closed', cleanup_pending=('actual-sdk-future',))
    assert s.catalog.get_pin(lease.run.run_id).state == 'active'
    future = Future()
    scope = object.__new__(KnowledgeScope)
    scope.run_id, scope.lease, scope.backend = str(lease.run.run_id), lease, s.backend
    scope.provider = SimpleNamespace(close=lambda **kw: None)
    scope.owner = SimpleNamespace(tasks=(), threads=(future,), _executor=SimpleNamespace(shutdown=lambda **kw: None))
    scope._handles_pending = lambda: []
    scope._pending_cleanup = True
    s.catalog._maintain_indexes = s.automatic_callback
    thread = threading.Thread(target=scope._reap)
    thread.start(); time.sleep(.05)
    assert s.catalog.get_pin(lease.run.run_id).state == 'active'
    future.set_result(None); thread.join(5)
    assert not thread.is_alive() and not scope._pending_cleanup
    assert s.backend.wait_closed(10)
    assert s.catalog.get_pin(lease.run.run_id).state == 'released'
    assert s.client.dropped == [old['collection_name']]
    with s.catalog._db.transaction() as db:
        assert db.execute('SELECT state,completed_by FROM index_readers WHERE reader_id=?',
                          (str(pending.identity),)).fetchone() == ('finished', 'finished_frame')
    receipt_release.set(); completion.join(5); assert not completion.is_alive()


def test_request_handle_worker_death_uses_frozen_birth_not_current_client_metadata(tmp_path):
    support = runpy.run_path(str(Path(__file__).with_name('test_model_worker.py')))
    client = LocalModelClient(support['config'](tmp_path))
    actual = {'pid':os.getpid(), 'process_birth':process_birth(os.getpid()),
              'instance_id':str(uuid4()), 'session_id':str(uuid4())}
    client._request_identity = actual
    handle = RequestHandle(client, support['request'](client.owner_id))
    client.metadata = {**actual, 'process_birth':'different'}
    actual['process_birth'] = 'mutated-after-birth'
    copy = handle.worker_identity; copy['process_birth'] = 'external-mutation'
    assert not handle.wait_finished(0)
    assert handle.worker_identity['process_birth'] == process_birth(os.getpid())
