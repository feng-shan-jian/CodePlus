"""Actual installed CUDA worker and actual host processes. Explicit opt-in only."""
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from agentic_rag.capabilities import ModelInput, RequestContext
from agentic_rag.config import WorkerExecutionConfig
from agentic_rag.domain import ErrorCode, RagError
from agentic_rag.models import FrozenTokenizer, LocalModelClient
from agentic_rag.models.identity import coordination_directory, private_json, process_birth, verify_private
from agentic_rag.profiles import EmbeddingProfile, LocalRuntime, RerankProfile

pytestmark = pytest.mark.skipif(os.environ.get('R09_REAL') != '1', reason='explicit installed CUDA acceptance required')


def ctx(client, seconds=60, purpose='qa'):
    return RequestContext(request_id=uuid4(), owner_id=client.owner_id, purpose=purpose,
                          deadline_monotonic_ns=time.monotonic_ns() + int(seconds * 1e9))


def item(text='Immutable versions preserve concurrent queries.', title=None):
    return ModelInput(item_id=uuid4(), text=text, title=title)


def expect_error(handle, code):
    with pytest.raises(RagError) as caught:
        handle.result()
    assert caught.value.error.code == code
    assert handle.wait_finished(30)
    return {'error': caught.value.error.model_dump(mode='json'), 'phases': handle.phases,
            'execution_finished': handle.execution_finished, 'completion_source': handle.completion_source,
            'cancel_ack': handle.cancel_ack}


def wait_dead(metadata, timeout=30):
    end = time.monotonic() + timeout
    while process_birth(metadata['pid']) == metadata['process_birth'] and time.monotonic() < end:
        time.sleep(.03)
    assert process_birth(metadata['pid']) != metadata['process_birth']


@pytest.fixture(scope='module')
def real(tmp_path_factory):
    root = tmp_path_factory.mktemp('actual-installed-worker')
    cache = Path(os.environ.get('R09_MODEL_CACHE', str(Path.home()/'.cache/codeplus-agenticrag/models')))
    cuda = os.environ.get('R09_CUDA_PYTHON', str(Path.home()/'.cache/codeplus-agenticrag/venv-win-cuda/Scripts/python.exe'))
    config = WorkerExecutionConfig(executable=cuda, model_cache=str(cache), runtime_dir=str(root/'private'),
                                   max_queue_items=1, idle_timeout_ms=500)
    report = {'scope': 'actual Windows installed CUDA worker; GPU cases run serially',
              'host_python': sys.executable, 'cuda_python': cuda, 'cwd': str(Path.cwd()),
              'environment': {name: importlib.metadata.version(name) for name in ('pydantic', 'tokenizers')},
              'nvidia_smi': subprocess.check_output(['nvidia-smi','--query-gpu=name,driver_version,memory.total,memory.used,memory.free',
                                                    '--format=csv,noheader'], text=True).strip(), 'cases': {}}
    yield config, root, report
    path = os.environ.get('R09_GPU_REPORT')
    if path:
        Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def test_two_real_hosts_race_start_share_model_and_independent_exit(real):
    config, root, report = real
    cfg = root / 'host-config.json'; cfg.write_text(config.model_dump_json(), encoding='utf-8')
    helper = Path(__file__).with_name('worker_process_helper.py')
    host_python = os.environ.get('R09_HOST_PYTHON', sys.executable)
    processes = [subprocess.Popen([host_python, '-I', '-B', str(helper), str(cfg)], cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8') for _ in range(2)]
    def read(proc):
        with ThreadPoolExecutor(1) as pool:
            line = pool.submit(proc.stdout.readline).result(90)
        if not line:
            proc.wait(10)
            raise AssertionError(proc.stderr.read())
        return json.loads(line)
    def send(proc, value):
        proc.stdin.write(json.dumps(value)+'\n'); proc.stdin.flush()
    try:
        ready = [read(proc) for proc in processes]
        assert ready[0]['host_pid'] != ready[1]['host_pid']
        for index, proc in enumerate(processes):
            send(proc, {'op':'query','name':f'different-display-{index}'})
        output = [read(proc) for proc in processes]
        assert output[0]['worker_pid'] == output[1]['worker_pid']
        assert output[0]['instance_id'] == output[1]['instance_id']
        assert output[0]['model_status']['model_instance_id'] == output[1]['model_status']['model_instance_id']
        assert output[0]['model_status']['load_count'] == output[1]['model_status']['load_count'] == 1
        for row in output:
            assert row['dimension'] == 1024 and abs(row['norm']-1) < 1e-4
        send(processes[0], {'op':'close'}); assert read(processes[0])['closed']; assert processes[0].wait(10) == 0
        send(processes[1], {'op':'query'}); surviving = read(processes[1])
        assert surviving['model_status']['model_instance_id'] == output[1]['model_status']['model_instance_id']
        send(processes[1], {'op':'close'}); assert read(processes[1])['closed']; assert processes[1].wait(10) == 0
        metadata = {'pid':output[0]['worker_pid'], 'process_birth':output[0]['worker_birth']}
        wait_dead(metadata)
        assert not (coordination_directory()/'active.json').exists()
        report['cases']['two_real_hosts'] = {'ready':ready,'initial':output,'after_one_exit':surviving,
                                            'last_client_idle_exit':True,'exit_codes':[p.returncode for p in processes]}
    finally:
        for proc in processes:
            if proc.poll() is None:
                try: send(proc, {'op':'close'})
                except OSError: pass
                proc.wait(30)


def test_actual_embedding_rerank_switching_identity_and_limits(real):
    config, root, report = real
    client = LocalModelClient(config)
    try:
        emb, rank = EmbeddingProfile(name='same-name'), RerankProfile(name='rank')
        docs = (item(title='Versioning'), item('GPU inference is scheduled in bounded batches.'),
                item('Stable IDs align outputs.'), item('Input templates have explicit token limits.'))
        handle = client.submit_documents(docs, emb, ctx(client)); response = handle.result()
        assert [row.item_id for row in response.results] == [row.item_id for row in docs]
        first = handle.model_status
        for row in response.results:
            assert len(row.vector) == 1024 and all(math.isfinite(value) for value in row.vector)
            assert abs(math.sqrt(sum(value*value for value in row.vector))-1) < 1e-4
        renamed = client.submit_query(item('How do versioned queries work?'), emb.model_copy(update={'name':'new-label'}), ctx(client))
        renamed.result(); assert renamed.model_status['model_instance_id'] == first['model_instance_id']
        rr = client.submit_rerank('How do versions protect concurrent queries?', docs, rank, ctx(client))
        reranked = rr.result()
        assert set(row.item_id for row in reranked.results) == set(row.item_id for row in docs)
        assert list(reranked.results) == sorted(reranked.results,key=lambda row:(-row.score,str(row.item_id)))
        assert rr.model_status['load_count'] == 2 and rr.model_status['live_unloaded_refs'] == 0
        changed = emb.model_copy(update={'instruction':'Retrieve passages about version isolation.'})
        isolated = client.submit_query(item(), changed, ctx(client)); isolated.result()
        assert isolated.model_status['load_count'] == 3 and isolated.model_status['profile_fingerprint'] != first['profile_fingerprint']
        restored = client.submit_documents(docs, emb, ctx(client)); restored.result()
        assert restored.model_status['load_count'] == 4
        too_long = client.submit_documents((item('word '*2500),), emb, ctx(client))
        long_error = expect_error(too_long, ErrorCode.INPUT_TOO_LONG)
        assert too_long.model_status['load_count'] == 4
        # Two complete 2048-token documents are accepted; no truncation.
        tokenizer = FrozenTokenizer(emb, config.model_cache)
        text = 'x '*2046+'x'
        assert tokenizer.document(text).token_count == 2048
        longest = client.submit_documents((item(text), item(text)), emb, ctx(client)); longest.result()
        assert longest.model_status['last_input_tokens'] == [2048,2048]
        report['cases']['inference_and_switch'] = {'embedding':response.model_dump(mode='json', exclude={'results'}),
            'item_ids':[str(row.item_id) for row in docs], 'first':first, 'renamed':renamed.model_status,
            'rerank':reranked.model_dump(mode='json'), 'rerank_model':rr.model_status, 'different_identity':isolated.model_status,
            'restored':restored.model_status,'input_error':long_error,'complete_boundary':longest.model_status,
            'actual_device':client.metadata['actual_device'], 'installed_identity':client.metadata['identity'],
            'private_permissions':verify_private(Path(config.runtime_dir))}
        assert 'site-packages' in Path(client.metadata['identity']['module_path']).parts
    finally:
        metadata = client.metadata; client.close()
        if metadata: wait_dead(metadata)


def test_actual_running_cancel_deadline_queue_oom_and_recovery(real):
    config, root, report = real
    client, other = LocalModelClient(config), LocalModelClient(config)
    emb = EmbeddingProfile(name='e')
    try:
        client.embed_query(item(),emb,ctx(client)); other.embed_query(item(),emb,ctx(other))
        long_docs = (item('x '*2046+'x'),item('x '*2046+'x'))
        active = client.submit_documents(long_docs,emb,ctx(client)); active.wait_phase('inference_started')
        active.cancel()
        cancelled = expect_error(active,ErrorCode.CANCELLED)
        assert 'inference_started' in active.phases
        expires = client.submit_documents(long_docs,emb,ctx(client,.08)); expires.wait_phase('inference_started')
        expired = expect_error(expires,ErrorCode.DEADLINE_EXCEEDED)
        # Real loading stage permits deterministic queue saturation/cancellation.
        switching = emb.model_copy(update={'instruction':'Different actual input identity for queue experiment.'})
        load = client.submit_documents(long_docs,switching,ctx(client)); load.wait_phase('loading')
        queued = other.submit_query(item(),emb,ctx(other)); queued.wait_phase('queued')
        full = other.submit_query(item(),emb,ctx(other))
        busy = expect_error(full,ErrorCode.WORKER_BUSY)
        queued.cancel(); qcancel = expect_error(queued,ErrorCode.CANCELLED)
        load.result()
        # Only allocator cap is reduced; it does not exhaust the shared card.
        oom_profile = emb.model_copy(update={'runtime':LocalRuntime(allocator_cap_mib=32)})
        oom = client.submit_query(item(),oom_profile,ctx(client))
        oom_error = expect_error(oom,ErrorCode.CUDA_OUT_OF_MEMORY)
        assert oom_error['error']['stage'] == 'load' and not oom.model_status['loaded']
        after = other.submit_query(item(),emb,ctx(other)); after.result()
        assert after.model_status['loaded'] and after.model_status['live_unloaded_refs'] == 0
        report['cases']['cancel_deadline_queue_oom'] = {'running_cancel':cancelled,'running_deadline':expired,
            'queue_full':busy,'queue_cancel':qcancel,'oom':oom_error,'oom_model_status':oom.model_status,'recovered':after.model_status}
    finally:
        metadata = client.metadata; client.close(); other.close()
        if metadata: wait_dead(metadata)


def test_actual_disconnect_unknown_worker_death_and_stale_restart(real):
    config, root, report = real
    client, survivor = LocalModelClient(config), LocalModelClient(config)
    emb = EmbeddingProfile(name='e')
    try:
        client.embed_query(item(),emb,ctx(client)); survivor.embed_query(item(),emb,ctx(survivor))
        req = client.submit_documents((item('x '*2046+'x'),item('x '*2046+'x')),emb,ctx(client))
        req.wait_phase('inference_started')
        # Owned connection fault, actual worker continues for another client.
        client.sock.shutdown(socket.SHUT_RDWR)
        with pytest.raises(RagError): req.result()
        assert req.execution_finished is False and req.wait_finished(.01) is False
        okay = survivor.submit_query(item(),emb,ctx(survivor)); okay.result()
        assert not req.execution_finished  # lost completion never inferred from other request
        metadata = survivor.metadata
        active = survivor.submit_documents((item('x '*2046+'x'),item('x '*2046+'x')),emb,ctx(survivor))
        active.wait_phase('inference_started')
        assert process_birth(metadata['pid']) == metadata['process_birth']
        assert private_json(coordination_directory()/'active.json')['instance_id'] == metadata['instance_id']
        # Only authenticated worker under this test's runtime is terminated.
        assert Path(metadata['runtime_dir']).is_relative_to(root)
        if os.name == 'nt':
            from agentic_rag.models import _windows
            handle = _windows.k.OpenProcess(1 | 0x1000, False, metadata['pid'])
            assert handle
            try:
                import ctypes
                from ctypes import wintypes
                _windows.k.TerminateProcess.argtypes = [wintypes.HANDLE,wintypes.UINT]
                assert _windows.k.TerminateProcess(handle,91)
            finally: _windows.k.CloseHandle(handle)
        else:
            import signal
            os.kill(metadata['pid'],signal.SIGKILL)
        death = expect_error(active,ErrorCode.WORKER_UNAVAILABLE)
        assert death['completion_source'] == 'worker_process_death'
        assert req.wait_finished(5) and req.completion_source == 'worker_process_death'
        client.close(); survivor.close()
        replacement = LocalModelClient(config)
        try:
            replacement.embed_query(item(),emb,ctx(replacement))
            assert replacement.metadata['instance_id'] != metadata['instance_id']
            report['cases']['death_and_disconnect'] = {'worker_pid':metadata['pid'], 'process_birth':metadata['process_birth'],
                'instance_id':metadata['instance_id'],'running_death':death,'disconnect_failed_while_completion_unknown':True,
                'disconnected_request_completed_only_after_death':True,'replacement_instance':replacement.metadata['instance_id']}
        finally:
            fresh = replacement.metadata; replacement.close(); wait_dead(fresh)
    finally:
        client.close(); survivor.close()


def test_actual_dependency_device_and_different_runtime_refusals(real,monkeypatch):
    config,root,report=real
    core = config.model_copy(update={'executable':sys.executable,'runtime_dir':str(root/'missing-deps')})
    missing = LocalModelClient(core)
    with pytest.raises(RagError) as caught:
        missing.embed_query(item(),EmbeddingProfile(name='e'),ctx(missing))
    assert caught.value.error.code == ErrorCode.DEPENDENCY_UNAVAILABLE
    report['cases']['missing_dependency'] = caught.value.error.model_dump(mode='json')
    owner=LocalModelClient(config)
    try:
        owner.embed_query(item(),EmbeddingProfile(name='e'),ctx(owner))
        incompatible=LocalModelClient(config.model_copy(update={'runtime_dir':str(root/'different-runtime')}))
        with pytest.raises(RagError) as caught:
            incompatible.embed_query(item(),EmbeddingProfile(name='e'),ctx(incompatible))
        assert caught.value.error.code == ErrorCode.IDENTITY_MISMATCH
        report['cases']['different_runtime_refused']=caught.value.error.model_dump(mode='json')
        monkeypatch.setenv('CUDA_VISIBLE_DEVICES','')
        mapping=LocalModelClient(config)
        with pytest.raises(RagError) as caught:
            mapping.embed_query(item(),EmbeddingProfile(name='e'),ctx(mapping))
        assert caught.value.error.code == ErrorCode.IDENTITY_MISMATCH
        report['cases']['different_device_mapping_refused']=caught.value.error.model_dump(mode='json')
    finally:
        metadata=owner.metadata; owner.close(); wait_dead(metadata)
    unavailable=LocalModelClient(config)
    with pytest.raises(RagError) as caught:
        unavailable.embed_query(item(),EmbeddingProfile(name='e'),ctx(unavailable))
    assert caught.value.error.code == ErrorCode.DEVICE_UNAVAILABLE
    report['cases']['actual_no_visible_device']=caught.value.error.model_dump(mode='json')


def test_controlled_faults_after_real_gpu_layer_and_actual_fatal_exit(real):
    config,root,report=real
    helper=Path(__file__).with_name('worker_gpu_fault_helper.py')
    evidence=[]
    for mode in ('forward','fatal'):
        result=subprocess.run([config.executable,'-I','-B',str(helper),mode,config.model_cache],cwd=root,
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
        record=json.loads(result.stdout.strip())
        assert record['real_layer_executed']
        if mode=='forward':
            assert result.returncode==0 and record['cuda_event_complete_before_error_return']
        else:
            assert result.returncode!=0 and 'UnsafeCudaCompletion' in result.stderr
        assert process_birth(record['pid']) is None
        evidence.append({**record,'exit_code':result.returncode,'actual_interpreter_exited':True})
    report['cases']['controlled_gpu_error_completion']=evidence
