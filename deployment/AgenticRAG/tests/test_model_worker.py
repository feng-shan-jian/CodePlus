"""Formal deterministic protocol/lifecycle tests. Fake engine is NOT GPU evidence."""

import asyncio
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from agentic_rag._schema import fingerprint
from agentic_rag.capabilities import EmbeddingResponse, EmbeddingResult, ModelInput, ModelTimings, RequestContext
from agentic_rag.config import ProcessingSnapshot, RunConfiguration, WorkerExecutionConfig, assemble_configuration
from agentic_rag.domain import ErrorCode, RagError
from agentic_rag.models.client import LocalModelClient, RequestHandle
from agentic_rag.models.engine import stopped
from agentic_rag.models.identity import clock_domain, private_directory, verify_private
from agentic_rag.models.protocol import Hello, Submit, decode, encode, parse, read_frame, write_frame
from agentic_rag.models.worker import Job, Session, Worker
from agentic_rag.profiles import EmbeddingProfile


def config(tmp_path, **overrides):
    import sys
    return WorkerExecutionConfig(executable=sys.executable, model_cache=str(tmp_path / 'models'),
                                 runtime_dir=str(tmp_path / 'runtime'), **overrides)


def context(owner, duration=30):
    return RequestContext(request_id=uuid4(), owner_id=owner, purpose='qa',
                          deadline_monotonic_ns=time.monotonic_ns() + int(duration * 1e9))


def request(owner, duration=30):
    profile = EmbeddingProfile(name='test')
    return Submit(type='submit', operation='documents', profile=profile, profile_fingerprint=profile.identity,
        items=(ModelInput(item_id=uuid4(), text='protocol fixture'),), context=context(owner, duration))


@pytest.mark.parametrize('data', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'[]', b'\xff'])
def test_strict_json(data):
    with pytest.raises(RagError):
        decode(data)


def test_frame_rejects_length_before_reading_body():
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack('!I', 1048577))  # no body and no EOF
        with pytest.raises(RagError):
            await read_frame(reader, timeout=.1)
        empty = asyncio.StreamReader()
        with pytest.raises(TimeoutError):
            await read_frame(empty, timeout=.01)
    asyncio.run(run())


def test_closed_schema_profiles_ids_and_no_code_or_paths():
    original = request(uuid4()).model_dump(mode='json')
    for key, value in [('code', 'print(1)'), ('path', 'C:/secret'), ('unexpected', True)]:
        with pytest.raises(RagError):
            parse(Submit, {**original, key: value})
    original['items'].append(original['items'][0])
    with pytest.raises(RagError):
        parse(Submit, original)
    hello = {'type': 'hello', 'protocol': True, 'token': 'a'*64, 'owner_id': str(uuid4()),
             'instance_id': str(uuid4()), 'implementation_digest': 'b'*64,
             'runtime_fingerprint': 'c'*64, 'clock_domain': 'd'*64}
    # Explicit boolean protocol version is rejected by transport validation.
    with pytest.raises(RagError):
        parse(Hello, hello)
    hello['protocol']=1;hello['token']='中'*64
    with pytest.raises(RagError): parse(Hello,hello)


def test_prechange_r08_snapshot_and_run_identity_survive_worker_configuration():
    path = Path(__file__).parent / 'fixtures/legacy-worker-snapshot.json'
    before = json.loads(path.read_text(encoding='utf-8'))
    assert before['source_head'] == '97224b0414c9b67bc99ed59799fa2f2286ab5460'
    snapshot = ProcessingSnapshot.model_validate_json(json.dumps(before['snapshot']))
    run = RunConfiguration.model_validate_json(json.dumps(before['run_configuration']))
    assert snapshot.config_fingerprint == before['snapshot']['config_fingerprint']
    assert snapshot.model_dump(mode='json') == before['snapshot']
    assert run.model_dump(mode='json') == before['run_configuration']
    result = assemble_configuration(defaults=before['snapshot']['resolved_config'], explicit={
        'worker': {'executable': 'C:/python/python.exe', 'model_cache': 'C:/models', 'runtime_dir': 'C:/runtime'}})
    assert result.knowledge == snapshot.resolved_config
    assert fingerprint('knowledge-config', result.knowledge) == snapshot.config_fingerprint
    assert result.worker.runtime_dir == 'C:/runtime'
    assert next(row for row in result.origins if row.path == 'worker.runtime_dir').source == 'explicit'
    assert run.identity == fingerprint('knowledge-run-config', before['run_configuration'])


def test_private_directory_rechecks_existing_permissions(tmp_path):
    path = private_directory(tmp_path / 'private')
    assert verify_private(path)['owner_current_user']
    assert private_directory(path) == path
    if __import__('os').name == 'nt':
        # tempfile parent uses inherited broad ACL; do not rewrite its ACL.
        with pytest.raises((RagError, PermissionError)):
            private_directory(tmp_path)
    else:
        path.chmod(0o755)
        with pytest.raises((RagError, PermissionError)):
            private_directory(path)
    assert len(clock_domain()) == 64


def test_absolute_deadline_covers_shared_client_and_write_lock_wait(tmp_path):
    client = LocalModelClient(config(tmp_path))
    acquired, release = threading.Event(), threading.Event()
    def hold(lock):
        with lock:
            acquired.set()
            release.wait(2)
    for lock, action in [(client.lock, lambda: client.submit_documents(
            (ModelInput(item_id=uuid4(), text='bounded'),), EmbeddingProfile(name='e'), context(client.owner_id, .05))),
                         (client.write_lock, lambda: client._send({'type': 'status'}, time.monotonic_ns()+50000000))]:
        acquired.clear(); release.clear()
        thread = threading.Thread(target=hold, args=(lock,)); thread.start(); acquired.wait(1)
        start = time.monotonic()
        with pytest.raises(RagError) as caught:
            action()
        assert caught.value.error.code == ErrorCode.DEADLINE_EXCEEDED
        assert time.monotonic() - start < .3
        release.set(); thread.join(1)


def test_deadline_result_never_waits_for_cancel_io(tmp_path):
    client = LocalModelClient(config(tmp_path))
    handle = RequestHandle(client, request(client.owner_id, .01))
    acquired, release = threading.Event(), threading.Event()
    def block():
        with client.lock:
            acquired.set(); release.wait(2)
    thread = threading.Thread(target=block); thread.start(); acquired.wait(1)
    try:
        start = time.monotonic()
        with pytest.raises(RagError) as caught:
            handle.result()
        assert caught.value.error.code == ErrorCode.DEADLINE_EXCEEDED
        assert time.monotonic() - start < .2
        assert not handle.execution_finished
    finally:
        release.set(); thread.join(1)


def test_expiry_detaches_all_jobs_before_disconnect_yields(tmp_path):
    async def scenario():
        executor = ThreadPoolExecutor(max_workers=1)
        worker = Worker(config(tmp_path), {}, {}, executor, ControlledEngine())
        session = Session(None, uuid4())
        first, second = Job(request(session.owner, .001), session, 10), Job(request(session.owner, .001), session, 20)
        worker.queue, worker.queue_bytes = [first, second], 30
        finished = []
        async def disconnect_during_finish(job, **kwargs):
            worker.cancel_session(session)
            await asyncio.sleep(0)
            finished.append(job)
        worker.finish = disconnect_during_finish
        await asyncio.sleep(.005)
        task = asyncio.create_task(worker.expire_queued())
        await asyncio.sleep(.04)
        assert not task.done() and finished == [first, second]
        assert worker.queue_bytes == 0
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
        executor.shutdown()
    asyncio.run(scenario())


def test_unload_always_synchronizes_and_sync_failure_terminates_real_child(tmp_path):
    from agentic_rag.models.engine import CudaEngine, UnsafeCudaCompletion, synchronize_or_stop
    from types import SimpleNamespace
    calls=[]
    def cache_error():
        calls.append('empty_cache'); raise RuntimeError('controlled empty_cache failure')
    cuda=SimpleNamespace(empty_cache=cache_error,synchronize=lambda:calls.append('synchronize'))
    engine=CudaEngine.__new__(CudaEngine);engine.model=None;engine.torch=SimpleNamespace(cuda=cuda)
    with pytest.raises(RuntimeError): engine.unload()
    assert calls==['empty_cache','synchronize']
    def sync_error(): raise RuntimeError('controlled sync failure')
    cuda.synchronize=sync_error
    with pytest.raises(UnsafeCudaCompletion): synchronize_or_stop(engine.torch)
    helper=Path(__file__).with_name('worker_fault_helper.py')
    result=subprocess.run([sys.executable,'-I','-B',str(helper),'fatal',str(tmp_path)],
                          cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=15)
    assert result.returncode!=0 and 'UnsafeCudaCompletion' in result.stderr
    assert 'UNSAFE_FINISHED' not in result.stdout


def test_busy_device_with_malformed_metadata_never_spawns(tmp_path,monkeypatch):
    from agentic_rag.models import lifecycle
    from agentic_rag.models.identity import atomic_private_json
    from agentic_rag.storage.locks import ProcessLock
    directory=private_directory(tmp_path/'coordination')
    atomic_private_json(directory/'active.json',{'pid':'not-a-pid','unexpected':True})
    monkeypatch.setattr(lifecycle,'coordination_directory',lambda:directory)
    monkeypatch.setattr(lifecycle,'target_identity',lambda *_:({},{}))
    monkeypatch.setattr(lifecycle.subprocess,'Popen',lambda *_args,**_kwargs:pytest.fail('must not spawn while lifetime is held'))
    with ProcessLock(directory/'lifetime.lock'):
        with pytest.raises(RagError) as caught:
            lifecycle.discover(config(tmp_path),time.monotonic_ns()+1000000000)
    assert caught.value.error.code==ErrorCode.IDENTITY_MISMATCH
    assert (directory/'active.json').exists()


def test_stale_delete_retains_lifetime_against_actual_competing_process(tmp_path,monkeypatch):
    from agentic_rag.models import lifecycle,identity
    directory=private_directory(tmp_path/'coordination')
    identity.atomic_private_json(directory/'active.json',{'stale':True})
    helper=Path(__file__).with_name('worker_startup_race_helper.py')
    child=subprocess.Popen([sys.executable,'-I','-B',str(helper),str(directory)],
        cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    original_verify=identity.verify_private
    def verify_with_competitor(path):
        result=original_verify(path)
        if Path(path)==directory/'active.json':
            (directory/'attempt-gate').write_text('go')
            end=time.monotonic()+5
            while not (directory/'attempt-result').exists():
                assert time.monotonic()<end
                time.sleep(.005)
            assert (directory/'attempt-result').read_text()=='blocked_by_lifetime'
        return result
    class ProbeFinished(Exception):pass
    def intercepted_spawn(*_,**__):
        stdout,stderr=child.communicate(timeout=10)
        assert child.returncode==0,stderr
        assert json.loads(stdout)['published_after_lifetime']
        raise ProbeFinished()
    monkeypatch.setattr(identity,'verify_private',verify_with_competitor)
    monkeypatch.setattr(lifecycle,'coordination_directory',lambda:directory)
    monkeypatch.setattr(lifecycle,'target_identity',lambda *_:({},{}))
    monkeypatch.setattr(lifecycle.subprocess,'Popen',intercepted_spawn)
    try:
        with pytest.raises(ProbeFinished):lifecycle.discover(config(tmp_path),time.monotonic_ns()+10000000000)
        assert identity.private_json(directory/'active.json')=={'race_owner':True}
    finally:
        if child.poll() is None:
            (directory/'attempt-gate').touch()
            child.wait(10)


@pytest.mark.parametrize('mutation',['foreign_request','foreign_profile','unknown_field'])
def test_client_rejects_mismatched_or_unknown_response_without_false_completion(tmp_path,mutation):
    client=LocalModelClient(config(tmp_path));req=request(client.owner_id);handle=RequestHandle(client,req)
    client.handles[req.context.request_id]=handle
    response=EmbeddingResponse(request_id=req.context.request_id,profile_fingerprint=req.profile.identity,
        results=(EmbeddingResult(item_id=req.items[0].item_id,vector=(1.0,)+(0.0,)*1023,input_tokens=3),),
        timings=ModelTimings(queue_ms=0,load_ms=0,inference_ms=0)).model_dump(mode='json')
    message={'type':'finished','request_id':str(req.context.request_id),'execution_finished':True,
             'response':response,'error':None,'model_status':{}}
    if mutation=='foreign_request':response['request_id']=str(uuid4())
    elif mutation=='foreign_profile':response['profile_fingerprint']='0'*64
    else:message['surprise']=True
    client._receive=lambda:message
    client._read_loop()
    with pytest.raises(RagError):handle.result()
    assert client.broken and not handle.execution_finished


class ControlledEngine:
    """Explicit injection: scheduling and protocol evidence only."""
    def __init__(self):
        self.release, self.started = threading.Event(), threading.Event()
        self.snapshot, self.device_identity = {'test_double': True}, {'test_double': True}

    def execute(self, req, cancelled, observer, admitted):
        observer('inference_started')
        self.started.set()
        assert self.release.wait(5), 'test controller did not release fake engine'
        stopped(req, cancelled, 'completed_batch')
        return EmbeddingResponse(request_id=req.context.request_id, profile_fingerprint=req.profile.identity,
            results=tuple(EmbeddingResult(item_id=item.item_id, vector=(1.0,)+(0.0,)*1023, input_tokens=3) for item in req.items),
            timings=ModelTimings(queue_ms=0, load_ms=0, inference_ms=0))


def test_unauthenticated_or_mismatched_handshakes_cannot_read_status(tmp_path):
    async def scenario():
        executor=ThreadPoolExecutor(1)
        identity={'implementation_digest':'a'*64,'runtime_fingerprint':'b'*64,'clock_domain':clock_domain()}
        bootstrap={'instance_id':str(uuid4()),'token':'c'*64}
        worker=Worker(config(tmp_path,handshake_timeout_ms=100),bootstrap,identity,executor,ControlledEngine())
        server=await asyncio.start_server(worker.accept,'127.0.0.1',0)
        hello={'type':'hello','protocol':1,'owner_id':str(uuid4()),**bootstrap,**identity}
        bad=[{'type':'status'},{**hello,'token':'d'*64},{**hello,'token':'中'*64},
             {**hello,'protocol':True},{**hello,'clock_domain':'0'*64},{**hello,'run_code':'ignored?'}]
        try:
            for message in bad:
                reader,writer=await asyncio.open_connection('127.0.0.1',server.sockets[0].getsockname()[1])
                await write_frame(writer,message)
                assert await asyncio.wait_for(reader.read(),1)==b''
                writer.close();await writer.wait_closed()
            assert not worker.sessions and not worker.queue
        finally:
            server.close();await server.wait_closed();executor.shutdown()
    asyncio.run(scenario())


def test_real_socket_bounded_queue_owner_cancel_deadline_and_disconnect(tmp_path):
    async def scenario():
        engine = ControlledEngine()
        executor = ThreadPoolExecutor(max_workers=1)
        identity = {'implementation_digest': 'a'*64, 'runtime_fingerprint': 'b'*64, 'clock_domain': clock_domain()}
        bootstrap = {'instance_id': str(uuid4()), 'token': 'c'*64}
        worker = Worker(config(tmp_path, max_queue_items=1, idle_timeout_ms=100), bootstrap, identity, executor, engine)
        server = await asyncio.start_server(worker.accept, '127.0.0.1', 0)
        scheduler, expiry = asyncio.create_task(worker.scheduler()), asyncio.create_task(worker.expire_queued())
        sockets = []
        async def connect():
            owner = uuid4()
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            sockets.append(writer)
            await write_frame(writer, {'type': 'hello', 'protocol': 1, 'owner_id': str(owner), **bootstrap, **identity})
            ack, _ = await read_frame(reader)
            assert ack['type'] == 'hello_ack'
            return owner, reader, writer
        async def until(reader, kind):
            while True:
                value, _ = await read_frame(reader)
                if value['type'] == kind:
                    return value
        try:
            a, ar, aw = await connect(); b, br, bw = await connect(); c, cr, cw = await connect()
            running = request(a)
            await write_frame(aw, running.model_dump(mode='json'))
            assert (await until(ar, 'phase'))['phase'] == 'queued'
            assert (await until(ar, 'phase'))['phase'] == 'inference_started'
            queued = request(b)
            await write_frame(bw, queued.model_dump(mode='json')); await until(br, 'phase')
            full = request(c)
            await write_frame(cw, full.model_dump(mode='json'))
            assert (await until(cr, 'finished'))['error']['code'] == 'WORKER_BUSY'
            await write_frame(aw, {'type': 'cancel', 'request_id': str(queued.context.request_id)})
            assert (await until(ar, 'cancel_ack'))['accepted'] is False
            await write_frame(bw, {'type': 'cancel', 'request_id': str(queued.context.request_id)})
            assert (await until(br, 'cancel_ack'))['accepted'] is True
            assert (await until(br, 'finished'))['error']['code'] == 'CANCELLED'
            expires = request(b, .05)
            await write_frame(bw, expires.model_dump(mode='json'))
            assert (await until(br, 'finished'))['error']['code'] == 'DEADLINE_EXCEEDED'
            assert worker.running is not None and not engine.release.is_set()
            await write_frame(aw, {'type': 'cancel', 'request_id': str(running.context.request_id)})
            assert (await until(ar, 'cancel_ack'))['accepted'] is True
            assert worker.running is not None  # acknowledgment is not completion
            engine.release.set()
            assert (await until(ar, 'finished'))['error']['code'] == 'CANCELLED'
            # Duplicate accepted request ID closes this owner's connection.
            await write_frame(aw, running.model_dump(mode='json'))
            assert await asyncio.wait_for(ar.read(), 2) == b''
            successor = request(b)
            await write_frame(bw, successor.model_dump(mode='json'))
            done = await until(br, 'finished')
            assert done['response']['request_id'] == str(successor.context.request_id)
            assert done['execution_finished'] is True
        finally:
            engine.release.set()
            for writer in sockets:
                writer.close(); await writer.wait_closed()
            server.close(); await server.wait_closed()
            await asyncio.wait_for(scheduler, 3)
            expiry.cancel(); await asyncio.gather(expiry, return_exceptions=True)
            executor.shutdown()
    asyncio.run(scenario())
