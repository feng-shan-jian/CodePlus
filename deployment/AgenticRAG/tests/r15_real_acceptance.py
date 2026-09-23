"""R15 installed GPU/Milvus, physical drop crashes and offline history acceptance."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import agentic_rag
from agentic_rag.config import KnowledgeConfig, ProcessingSnapshot, WorkerExecutionConfig, resolve_run
from agentic_rag.domain import RunStatus, Span
from agentic_rag.ingestion import InputSelection, begin_changes, build_changes
from agentic_rag.indexes.milvus import MilvusRevisionIndex
from agentic_rag.models import FrozenTokenizer, LocalModelClient
from agentic_rag.models.identity import process_birth
from agentic_rag.retrieval.dense import DenseSearch
from agentic_rag.sources import SourceSession
from agentic_rag.citations import CitationRegistry
from agentic_rag.evidence import DeliveryGateway
from agentic_rag.storage import Catalog, publication

H = runpy.run_path(str(Path(__file__).with_name('r14_real_acceptance.py')))
SOURCE = runpy.run_path(str(Path(__file__).with_name('source_support.py')))
write, identity, wait_file = H['write'], H['identity'], H['wait_file']
HELPER = Path(__file__).with_name('r15_gc_process.py')


def wait_until(check, message, seconds=30):
    end = time.monotonic()+seconds
    while not check():
        if time.monotonic() > end: raise TimeoutError(message)
        time.sleep(.03)


class Suite:
    def __init__(self, args):
        self.args = args; self.root = Path(args.root).resolve(); self.root.mkdir(parents=True)
        assert 'site-packages' in Path(agentic_rag.__file__).parts
        self.worker = WorkerExecutionConfig(executable=args.cuda_python, model_cache=args.model_cache,
            runtime_dir=str(self.root/'worker'), idle_timeout_ms=10000)
        self.provider = LocalModelClient(self.worker)
        self.report = {'status':'RUNNING', 'identity':identity(), 'worker_config':self.worker.model_dump(mode='json'),
                       'cases':{}, 'processes':[], 'scope':'real GPU/Milvus; named synthetic documents; no answer-quality claim'}
        self.save()

    def save(self): write(self.args.report, self.report)

    def case(self, name, *, automatic=True):
        root = self.root/name; root.mkdir()
        data = H['H']['configuration'](root/'data', self.args.endpoint).model_dump(mode='json')
        data['storage']['namespace'] = 'r15_acceptance'
        data['retrieval'].update(context_chunks=32, context_tokens=50000)
        data['budgets']['qa'].update(searches=20, opens=20, total_tokens=200000, duration_ms=300000)
        config = KnowledgeConfig.model_validate_json(json.dumps(data))
        catalog = Catalog(root/'data')
        if not automatic: catalog._maintain_indexes = lambda: []
        backend = MilvusRevisionIndex(config.storage, catalog, timeout=5)
        kb = catalog.create_library(name).kb_id
        return root, config, catalog, backend, kb

    def build(self, config, catalog, backend, kb, paths=(), deletes=()):
        with begin_changes(catalog, kb, ProcessingSnapshot.capture(uuid4(), config),
                tuple(InputSelection(path=str(path)) for path in paths), delete_document_ids=tuple(deletes)) as owner:
            return build_changes(catalog, owner, self.provider, backend, FrozenTokenizer(config.embedding, self.args.model_cache))

    def spawn(self, root, name, mode, **options):
        argv = [sys.executable, '-I', '-B', str(HELPER), mode, '--root', str(root), '--name', name]
        for key, value in options.items(): argv += ['--'+key, str(value)]
        log = (root/(name+'.log')).open('w', encoding='utf-8')
        child = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        log.close()
        self.report['processes'].append({'argv':argv, 'launcher_pid':child.pid, 'launcher_birth':process_birth(child.pid)})
        self.save()
        return child

    def finish(self, child, root, name):
        child.wait(90)
        result = json.loads((root/(name+'-result.json')).read_text(encoding='utf-8'))
        assert child.returncode == 0 and result['status'] == 'PASS', result
        return result

    def kill(self, child, evidence):
        actual = evidence['identity']; pid = actual['pid']
        assert process_birth(pid) == actual['birth']
        if os.name == 'nt':
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            class FileTime(ctypes.Structure):
                _fields_ = [('low', ctypes.c_uint32), ('high', ctypes.c_uint32)]
            kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FileTime)]*4
            handle = kernel.OpenProcess(0x101001, False, pid); assert handle
            try:
                created, exited, kern, user = FileTime(), FileTime(), FileTime(), FileTime()
                assert kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kern), ctypes.byref(user))
                stable_birth = str((created.high << 32) | created.low)
                assert stable_birth == actual['birth'], (stable_birth, actual['birth'])
                assert kernel.TerminateProcess(handle, 91)
                assert kernel.WaitForSingleObject(handle, 10000) == 0
            finally: kernel.CloseHandle(handle)
        else:
            import signal
            handle = os.pidfd_open(pid)
            try:
                assert process_birth(pid) == actual['birth']
                stable_birth = actual['birth']
                signal.pidfd_send_signal(handle, signal.SIGKILL)
            finally: os.close(handle)
        child.wait(15)
        assert process_birth(pid) != actual['birth']
        return {'actual_pid':pid, 'birth':actual['birth'], 'stable_handle_birth':stable_birth,
                'exit_code':child.returncode, 'observed_dead':True}

    def history_and_readers(self):
        root, config, catalog, backend, kb = self.case('history-readers')
        update, delete, untouched = root/'update.md', root/'delete.md', root/'untouched.md'
        for path, text in ((update,'# Telescope\nThe telescope certificate was oldblue.\n'),
                           (delete,'# Retired\nUnseen retiredquasar approval was legacygreen.\n'),
                           (untouched,'# Archive\nUncited mountain archive remains violet.\n')):
            path.write_text(text, encoding='utf-8')
        first = self.build(config, catalog, backend, kb, (update, delete, untouched))
        old = publication.artifact(catalog, UUID(first['receipt']['revision_id']), published=True)
        lease = catalog.start_run(kb, resolve_run(config, 'qa'))
        sdk_lease = catalog.start_run(kb, resolve_run(config, 'qa'))
        source = SourceSession(catalog, lease, SOURCE['ControlledMeter'](), dense=DenseSearch(catalog, lease.run.run_id, self.provider, backend))
        found = source.search('old telescope certificate')
        selected = next(item for item in found.payload['items'] if item['file_name'] == 'update.md')
        opened = source.open(selected['source_ref'])
        gateway = DeliveryGateway(source); permit, _, _ = SOURCE['prepared'](gateway, opened)
        evidence, = gateway.settle(permit, 'confirmed')
        item = opened.payload['items'][0]
        citation = CitationRegistry(source).save(evidence, (Span.model_validate(item['returned_spans'][0]),), (item['text'],))
        with catalog._db.transaction() as db:
            members = list(db.execute('SELECT document_id,document_version_id FROM revision_members WHERE revision_id=?', (old['revision_id'],)))
        versions = []
        for document, version in members:
            record = catalog.get_version(UUID(version))
            versions.append({'kb_id':str(kb), 'revision_id':old['revision_id'], 'document_id':document,
                             'version_id':version, 'text':catalog.archives.read(record.parsed_hash).decode()})
        settings = {'config':config.model_dump(mode='json'), 'worker':self.worker.model_dump(mode='json'), 'kb_id':str(kb),
                    'old_revision':old['revision_id'], 'original_paths':list(map(str, (update, delete, untouched))),
                    'history':{'citation_id':citation['citation']['citation_id'], 'citation':citation, 'versions':versions}}
        write(root/'settings.json', settings)
        # Existing real reader helper pins now and does its FIRST Dense/source/
        # BM25 read only after the V2 publication file appears.
        child_argv = [sys.executable, '-I', '-B', str(Path(__file__).with_name('r13_real_acceptance.py')), 'reader', '--root', str(root)]
        log = (root/'unseen-reader.log').open('w', encoding='utf-8')
        child = subprocess.Popen(child_argv, stdout=log, stderr=subprocess.STDOUT); log.close()
        pinned = wait_file(root/'pinned.json', child)
        delete_id = next(UUID(doc) for doc, version in members if catalog.get_document(UUID(doc)).original_name == 'delete.md')
        update.write_text('# Telescope\nThe telescope certificate now requires newred.\n', encoding='utf-8')
        second = self.build(config, catalog, backend, kb, (update,), (delete_id,))
        write(root/'published.json', {'receipt':second['receipt'], 'time':time.time()})
        child.wait(90); assert child.returncode == 0
        old_view = json.loads((root/'reader-report.json').read_text(encoding='utf-8'))
        assert pinned['prior_search_calls'] == pinned['prior_source_reads'] == 0
        assert 'legacygreen' in json.dumps(old_view['read_after_gate']) and 'oldblue' in json.dumps(old_view['read_after_gate'])
        with catalog.start_run(kb, resolve_run(config, 'qa')) as new_run:
            fresh = DenseSearch(catalog, new_run.run.run_id, self.provider, backend).search('telescope retiredquasar archive', limit=50)
        assert 'newred' in json.dumps(fresh) and 'legacygreen' not in json.dumps(fresh)
        # Keep the old full version pinned while a real SDK response is held at
        # the transport boundary. The underlying call actually executes once.
        entered, release = threading.Event(), threading.Event()
        original = backend.client.search; sdk_results = []; sdk_errors = []
        def held(*values, **kwargs):
            result = original(*values, **kwargs)
            entered.set(); assert release.wait(20)
            return result
        backend.client.search = held
        def sdk():
            try: sdk_results.append(backend.search(old, 'retiredquasar', field='sparse'))
            except BaseException as exc: sdk_errors.append(repr(exc))
        thread = threading.Thread(target=sdk); thread.start(); assert entered.wait(10)
        sdk_lease.finish(RunStatus.CANCELLED, 'consumer_closed')
        assert catalog.get_pin(sdk_lease.run.run_id).state == 'active'
        assert backend.client.has_collection(old['collection_name'])
        release.set(); thread.join(10); assert not thread.is_alive() and not sdk_errors and sdk_results[0]
        backend.client.search = original
        # Observe the real first CUDA layer through an independent authenticated
        # control connection. Delay the completion frame, never the computation.
        from agentic_rag.adapters.codeplus.policy import KnowledgeScope
        from agentic_rag.storage import readers
        from pymilvus import MilvusClient
        control = LocalModelClient(self.worker); control._connect(time.monotonic_ns()+30_000_000_000)
        query_provider = LocalModelClient(self.worker); query_provider._connect(time.monotonic_ns()+30_000_000_000)
        phase, deliver = threading.Event(), threading.Event()
        receipt_waiting, receipt_release = threading.Event(), threading.Event()
        receive = query_provider._receive; gpu_errors = []
        original_finish = readers.Reader.finish
        def delayed_receipt(reader, *values):
            if threading.current_thread().name == 'rag-query-completion':
                receipt_waiting.set(); assert receipt_release.wait(60)
            return original_finish(reader, *values)
        readers.Reader.finish = delayed_receipt
        def held_phase(*values, **kwargs):
            message = receive(*values, **kwargs)
            if message.get('type') == 'phase' and message.get('phase') == 'inference_started':
                phase.set(); assert deliver.wait(20)
            return message
        query_provider._receive = held_phase
        def gpu():
            try:
                DenseSearch(catalog, lease.run.run_id, query_provider, backend).search(
                    'telescope approval certificate '+(' archive'*380), limit=50,
                    deadline_monotonic_ns=time.monotonic_ns()+3_000_000_000)
            except BaseException as exc: gpu_errors.append(repr(exc))
        executor = ThreadPoolExecutor(max_workers=1)
        compute = executor.submit(gpu); assert phase.wait(30)
        during = control.status()
        with catalog._db.transaction(write=True) as db:
            db.execute('INSERT INTO host_runs(run_id,frozen,started_ns) VALUES(?,?,?)', (str(lease.run.run_id), '{}', time.monotonic_ns()))
        lease.finish(RunStatus.CANCELLED, 'consumer_closed', cleanup_pending=('real-late-gpu',))
        after_finish = control.status()
        assert catalog.get_pin(lease.run.run_id).state == 'active'
        assert backend.client.has_collection(old['collection_name'])
        assert during['running'] is not None, 'actual CUDA request already ended before lifecycle observation'
        # The request deadline returns to its consumer while a completion frame
        # is held. This alone must not release the actual reader.
        compute.result(timeout=15); assert gpu_errors
        assert any('deadline' in value.lower() for value in gpu_errors), gpu_errors
        assert catalog.get_pin(lease.run.run_id).state == 'active'
        deliver.set(); assert receipt_waiting.wait(30)
        scope = object.__new__(KnowledgeScope)
        scope.run_id, scope.lease, scope.backend = str(lease.run.run_id), lease, backend
        scope.provider = query_provider
        scope.owner = SimpleNamespace(tasks=(), threads=(compute,), _executor=executor)
        scope._pending_cleanup = True
        reap = threading.Thread(target=scope._reap); reap.start(); reap.join(30)
        assert not reap.is_alive() and not scope._pending_cleanup
        assert backend.wait_closed(30), 'unique Scope transport did not complete finite retirement'
        wait_until(lambda: publication.artifact(catalog, UUID(old['revision_id']))['state'] == 'RECLAIMED', 'old real collection not automatically reclaimed')
        assert not receipt_release.is_set(), 'receipt barrier must still be closed'
        probe = MilvusClient(uri=config.storage.milvus_uri)
        try: assert not probe.has_collection(old['collection_name'])
        finally: probe.close()
        receipt_release.set(); readers.Reader.finish = original_finish
        query_provider._receive = receive; control.close()
        for path in (update, delete, untouched): path.unlink()
        history_child = self.spawn(root, 'history', 'history')
        history = self.finish(history_child, root, 'history')
        with catalog._db.transaction() as db:
            proof = {'readers':list(db.execute('SELECT kind,state,completed_by FROM index_readers')),
                     'pins':list(db.execute('SELECT run_id,state FROM run_pins')),
                     'integrity':list(db.execute('PRAGMA integrity_check')), 'foreign_keys':list(db.execute('PRAGMA foreign_key_check'))}
        self.report['cases']['history-readers'] = {'status':'PASS', 'first':first, 'second':second, 'unseen_old_process':old_view,
            'new_run':fresh, 'sdk_results':sdk_results, 'gpu_during':during, 'gpu_after_finish':after_finish,
            'late_gpu_consumer':gpu_errors, 'scope_closed_before_background_receipt':True,
            'history':history, 'proof':proof, 'actual_collection_absent':True}
        backend.close(); assert backend.wait_closed(30); self.save()

    def prepared_gc(self, name):
        root, config, catalog, backend, kb = self.case(name, automatic=False)
        path = root/'source.md'; path.write_text('# First\nThe first physical generation is oldblue.\n', encoding='utf-8')
        first = self.build(config, catalog, backend, kb, (path,))
        old = publication.artifact(catalog, UUID(first['receipt']['revision_id']), published=True)
        path.write_text('# Second\nThe next physical generation is newred.\n', encoding='utf-8')
        second = self.build(config, catalog, backend, kb, (path,))
        settings = {'config':config.model_dump(mode='json'), 'kb_id':str(kb), 'old_revision':old['revision_id']}
        write(root/'settings.json', settings)
        backend._close_transport()  # Fault fixture intentionally leaves READY work for a separate collector process.
        return root, config, catalog, old, second

    def crash_drop(self, stage):
        root, config, catalog, old, second = self.prepared_gc('crash-'+stage)
        child = self.spawn(root, 'crash', 'collect', stage=stage)
        gate = wait_file(root/'crash-gate.json', child)
        killed = self.kill(child, gate)
        state = publication.artifact(catalog, UUID(old['revision_id']))['state']
        assert state == ('RECLAIMED' if stage == 'after_receipt' else 'RECLAIMING')
        restarted = self.spawn(root, 'restart', 'startup')
        result = self.finish(restarted, root, 'restart')
        self.report['cases']['crash-'+stage] = {'status':'PASS', 'gate':gate, 'killed':killed, 'state_after_death':state, 'restarted':result}
        self.save()

    def collectors(self):
        root, config, catalog, old, second = self.prepared_gc('two-collectors')
        first = self.spawn(root, 'first', 'collect', stage='before_drop')
        gate = wait_file(root/'first-gate.json', first)
        second_process = self.spawn(root, 'second', 'collect')
        loser = self.finish(second_process, root, 'second')
        assert not loser['drop_calls'] and all(r['state'] == 'retained' for r in loser['results'])
        (root/'first-release').touch(); winner = self.finish(first, root, 'first')
        assert len(winner['drop_calls']) == 1 and winner['results'][0]['state'] == 'reclaimed'
        self.report['cases']['two-collectors'] = {'status':'PASS', 'first_claim':gate, 'winner':winner, 'loser':loser}; self.save()

    def binding_races(self):
        root, config, catalog, backend, kb = self.case('bind-first', automatic=False)
        path = root/'source.md'; path.write_text('# First\nBinding sees oldblue.\n', encoding='utf-8')
        first = self.build(config, catalog, backend, kb, (path,))
        old = publication.artifact(catalog, UUID(first['receipt']['revision_id']))
        write(root/'settings.json', {'config':config.model_dump(mode='json'), 'kb_id':str(kb), 'old_revision':old['revision_id']})
        # Prepare both actual processes before holding SQLite's write boundary.
        collector = self.spawn(root, 'blocked-collector', 'collect', barrier='collect-go')
        wait_file(root/'blocked-collector-ready.json', collector)
        binder = self.spawn(root, 'binder', 'pin', stage='before_pin_commit')
        gate = wait_file(root/'binder-gate.json', binder)
        (root/'collect-go').touch()
        blocked = self.finish(collector, root, 'blocked-collector')
        assert blocked.get('coordination_error') and not blocked['drop_calls']
        (root/'binder-release').touch()
        pinned = wait_file(root/'binder-pinned.json', binder)
        assert pinned['run']['revision_id'] == old['revision_id']
        path.write_text('# Second\nBinding now sees newred.\n', encoding='utf-8')
        second = self.build(config, catalog, backend, kb, (path,))
        retained = catalog.maintain_indexes(backend=backend)
        assert retained == [] and backend.client.has_collection(old['collection_name'])
        (root/'binder-finish').touch(); completed = self.finish(binder, root, 'binder')
        reclaimed = catalog.maintain_indexes(backend=backend)
        assert reclaimed[0]['state'] == 'reclaimed'
        backend._close_transport()
        self.report['cases']['bind-first'] = {'status':'PASS', 'gate':gate, 'blocked':blocked,
            'pinned':pinned, 'second':second, 'retained':retained, 'completed':completed, 'reclaimed':reclaimed}; self.save()

        root, config, catalog, old, second = self.prepared_gc('claim-first')
        collector = self.spawn(root, 'collector', 'collect', stage='before_drop')
        claimed = wait_file(root/'collector-gate.json', collector)
        binder = self.spawn(root, 'binder', 'pin')
        pinned = wait_file(root/'binder-pinned.json', binder)
        assert pinned['run']['revision_id'] == second['receipt']['revision_id']
        from agentic_rag.storage.readers import Reader
        try: Reader(catalog, old, 'milvus')
        except Exception as exc: refused = repr(exc)
        else: raise AssertionError('RECLAIMING old artifact admitted a new actual reader')
        (root/'binder-finish').touch(); completed = self.finish(binder, root, 'binder')
        (root/'collector-release').touch(); dropped = self.finish(collector, root, 'collector')
        assert len(dropped['drop_calls']) == 1
        self.report['cases']['claim-first'] = {'status':'PASS', 'claim':claimed, 'pinned':pinned,
            'refused_old_reader':refused, 'completed':completed, 'dropped':dropped}; self.save()

    def replacement_and_service(self):
        root, config, catalog, old, second = self.prepared_gc('replacement-and-service')
        backend = MilvusRevisionIndex(config.storage, catalog, timeout=1)
        created = backend.client.describe_collection(old['collection_name'])
        backend.client.drop_collection(old['collection_name'])
        backend.client.create_collection(old['collection_name'], dimension=2, description=created['description'])
        replacement = backend.client.describe_collection(old['collection_name'])
        assert replacement['collection_id'] != created['collection_id']
        retained = catalog.maintain_indexes(backend=backend)
        assert retained[0]['state'] == 'retained'
        assert backend.client.describe_collection(old['collection_name'])['collection_id'] == replacement['collection_id']
        backend._close_transport()
        self.report['cases']['physical-replacement'] = {'status':'PASS', 'created':created, 'replacement':replacement, 'result':retained}
        root, config, catalog, old, second = self.prepared_gc('service-retry')
        backend = MilvusRevisionIndex(config.storage, catalog, timeout=.5)
        commands = []
        def compose(*values):
            argv = ['docker','compose','-f',self.args.compose,'-p',self.args.project,*values]
            run = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8')
            commands.append({'argv':argv,'returncode':run.returncode,'stdout':run.stdout,'stderr':run.stderr}); run.check_returncode()
        compose('stop','standalone')
        try:
            failed = catalog.maintain_indexes(backend=backend)
            assert failed[0]['state'] == 'retained'
        finally: compose('up','-d','--wait','--wait-timeout','180','standalone')
        # The short timeout forces the unavailable-service branch only. Return
        # to the adapter's normal bounded timeout after service health recovers.
        backend.timeout = 5
        recovered = catalog.maintain_indexes(backend=backend)
        self.report['cases']['service-retry'] = {'status':'RUNNING','failed':failed,'recovered':recovered,'commands':commands}; self.save()
        assert recovered[0]['state'] == 'reclaimed'
        assert not backend.client.has_collection(old['collection_name'])
        backend._close_transport()
        self.report['cases']['service-retry'] = {'status':'PASS','failed':failed,'recovered':recovered,'commands':commands}; self.save()


def main(args):
    suite = Suite(args)
    try:
        if args.case in ('all','history'): suite.history_and_readers()
        if args.case in ('all','binding'): suite.binding_races()
        if args.case in ('all','crash'):
            for phase in ('before_drop','after_drop','after_receipt'): suite.crash_drop(phase)
        if args.case in ('all','collectors'): suite.collectors()
        if args.case in ('all','service'): suite.replacement_and_service()
        suite.report['status'] = 'PASS'
    except BaseException as exc:
        suite.report.update(status='FAIL', error=repr(exc)); raise
    finally:
        if suite.provider.metadata: suite.report['worker'] = suite.provider.metadata
        suite.provider.close()
        suite.report['finished'] = time.time(); suite.save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('root','cuda-python','model-cache','endpoint','report','compose','project'):
        parser.add_argument('--'+key, required=True)
    parser.add_argument('--case', choices=('all','history','binding','crash','collectors','service'), default='all')
    main(parser.parse_args())
