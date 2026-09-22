"""Installed local worker entry point; authenticated IPC remains responsive to GPU work."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hmac
import json
import os
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4

from ..config import WorkerExecutionConfig
from ..domain import ErrorCode, RagError
from ..storage.locks import ProcessLock
from .identity import (atomic_private_json, coordination_directory, describe, private_json,
                       process_birth, checked_bootstrap)
from .protocol import (Cancel, Goodbye, Hello, Status, Submit, parse, protocol_error,
                       read_frame, write_frame)


@dataclass(eq=False)
class Session:
    writer: object
    owner: UUID
    session_id: str = field(default_factory=lambda: str(uuid4()))
    ids: set = field(default_factory=set)
    writes: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False


@dataclass(eq=False)
class Job:
    request: Submit
    session: Session
    size: int
    admitted: int = field(default_factory=time.monotonic_ns)
    cancelled: threading.Event = field(default_factory=threading.Event)
    phase: str = 'queued'


class Worker:
    def __init__(self, config, bootstrap, identity, executor, engine):
        self.config, self.bootstrap, self.identity = config, bootstrap, identity
        self.executor, self.engine = executor, engine
        self.sessions, self.connections, self.queue, self.running = set(), 0, [], None
        self.queue_bytes, self.front_streak = 0, 0
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self.last_activity = time.monotonic()

    async def send(self, session, value):
        if session.closed:
            return
        try:
            async with session.writes:
                await write_frame(session.writer, value, limit=self.config.max_frame_bytes,
                                  timeout=self.config.io_timeout_ms / 1000)
        except (OSError, TimeoutError, ConnectionError):
            session.closed = True
            session.writer.close()
            self.cancel_session(session)

    async def finish(self, job, *, response=None, error=None):
        # Called only when removed from queue or the executor has really returned.
        await self.send(job.session, {'type': 'finished', 'request_id': str(job.request.context.request_id),
            'execution_finished': True, 'response': response.model_dump(mode='json') if response else None,
            'error': error.error.model_dump(mode='json') if error else None,
            'model_status': dict(self.engine.snapshot)})

    def cancel_session(self, session):
        for job in list(self.queue):
            if job.session is session:
                job.cancelled.set()
                self.queue.remove(job)
                self.queue_bytes -= job.size
        if self.running and self.running.session is session:
            self.running.cancelled.set()
        self.wake.set()

    async def accept(self, reader, writer):
        self.connections += 1
        session = None
        try:
            if self.connections > self.config.max_connections:
                return
            raw, _ = await read_frame(reader, limit=4096, timeout=self.config.handshake_timeout_ms / 1000)
            hello = parse(Hello, raw)
            identity = self.identity
            if (not hmac.compare_digest(hello.token, self.bootstrap['token']) or
                    str(hello.instance_id) != self.bootstrap['instance_id'] or
                    hello.implementation_digest != identity['implementation_digest'] or
                    hello.runtime_fingerprint != identity['runtime_fingerprint'] or
                    hello.clock_domain != identity['clock_domain']):
                raise protocol_error('authentication or runtime handshake mismatch')
            # owner identity is bound to exactly one authenticated session.
            if any(item.owner == hello.owner_id for item in self.sessions):
                raise protocol_error('owner already belongs to an active session')
            session = Session(writer, hello.owner_id)
            self.sessions.add(session)
            await self.send(session, {'type': 'hello_ack', 'protocol': 1, 'session_id': session.session_id,
                'instance_id': self.bootstrap['instance_id'], 'pid': os.getpid(),
                'process_birth': process_birth(os.getpid()), 'identity': self.identity,
                'actual_device': self.engine.device_identity})
            while not session.closed:
                # A frame must complete promptly after its first byte. Idle
                # authenticated clients are allowed while they own a live session.
                first = await reader.readexactly(1)
                async with asyncio.timeout(self.config.io_timeout_ms / 1000):
                    import struct
                    length, = struct.unpack('!I', first + await reader.readexactly(3))
                    if not 0 < length <= self.config.max_frame_bytes:
                        raise protocol_error('frame length exceeds limit')
                    from .protocol import decode
                    raw = decode(await reader.readexactly(length))
                kind = raw.get('type')
                if kind == 'submit':
                    await self.submit(session, parse(Submit, raw), length)
                elif kind == 'cancel':
                    await self.cancel(session, parse(Cancel, raw))
                elif kind == 'status':
                    parse(Status, raw)
                    await self.send(session, {'type': 'status', 'model_status': dict(self.engine.snapshot),
                        'queued': len(self.queue), 'queue_bytes': self.queue_bytes,
                        'running': str(self.running.request.context.request_id) if self.running else None,
                        'clients': len(self.sessions), 'instance_id': self.bootstrap['instance_id']})
                elif kind == 'goodbye':
                    parse(Goodbye, raw)
                    break
                else:
                    raise protocol_error('unknown message type')
        except (RagError, ValueError, OSError, asyncio.IncompleteReadError, TimeoutError):
            pass  # No unauthenticated details, secrets or document text in logs.
        finally:
            if session:
                session.closed = True
                self.cancel_session(session)
                self.sessions.discard(session)
            self.connections -= 1
            self.last_activity = time.monotonic()
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), self.config.io_timeout_ms / 1000)
            except (OSError, TimeoutError):
                pass
            self.wake.set()

    async def submit(self, session, request, size):
        rid = request.context.request_id
        if request.context.owner_id != session.owner or rid in session.ids:
            raise protocol_error('owner/request identity mismatch or duplicate request ID')
        if len(session.ids) >= self.config.max_session_requests:
            raise protocol_error('session request limit reached; open a new owner session')
        session.ids.add(rid)
        job = Job(request, session, size)
        if time.monotonic_ns() >= request.context.deadline_monotonic_ns:
            await self.finish(job, error=RagError(ErrorCode.DEADLINE_EXCEEDED, 'deadline exceeded before queue', stage='queue'))
        elif len(self.queue) >= self.config.max_queue_items or self.queue_bytes + size > self.config.max_queue_bytes:
            await self.finish(job, error=RagError(ErrorCode.WORKER_BUSY, 'bounded worker queue is full', stage='queue'))
        else:
            self.queue.append(job)
            self.queue_bytes += size
            await self.send(session, {'type': 'phase', 'request_id': str(rid), 'phase': 'queued'})
            self.wake.set()

    async def cancel(self, session, command):
        jobs = list(self.queue) + ([self.running] if self.running else [])
        own = next((job for job in jobs if job.session is session and job.request.context.request_id == command.request_id), None)
        if command.request_id not in session.ids:
            await self.send(session, {'type': 'cancel_ack', 'request_id': str(command.request_id), 'accepted': False})
            return
        if own:
            own.cancelled.set()
        await self.send(session, {'type': 'cancel_ack', 'request_id': str(command.request_id), 'accepted': True})
        if own in self.queue:
            self.queue.remove(own)
            self.queue_bytes -= own.size
            await self.finish(own, error=RagError(ErrorCode.CANCELLED, 'queued request cancelled', stage='queue'))
        self.wake.set()

    def observe(self, job, phase):
        def publish():
            job.phase = phase
            asyncio.create_task(self.send(job.session, {'type': 'phase',
                'request_id': str(job.request.context.request_id), 'phase': phase}))
        self.loop.call_soon_threadsafe(publish)

    async def scheduler(self):
        while True:
            if not self.queue:
                if not self.sessions and not self.connections and time.monotonic() - self.last_activity >= self.config.idle_timeout_ms / 1000:
                    return
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 0.02)
                except TimeoutError:
                    pass
                continue
            front = [job for job in self.queue if job.request.context.purpose in ('qa', 'report')]
            back = [job for job in self.queue if job.request.context.purpose in ('import', 'rebuild')]
            job = back[0] if back and (not front or self.front_streak >= 4) else (front or back)[0]
            self.front_streak = self.front_streak + 1 if job in front else 0
            self.queue.remove(job)
            self.queue_bytes -= job.size
            self.running = job
            response, error = None, None
            try:
                response = await self.loop.run_in_executor(self.executor, self.engine.execute, job.request,
                    job.cancelled, lambda phase: self.observe(job, phase), job.admitted)
                # Recheck in the IPC loop too: cancellation may race thread return.
                from .engine import stopped
                stopped(job.request, job.cancelled, 'delivery')
            except RagError as exc:
                info = exc.error
                error = RagError(info.code, info.message, stage=info.stage, request_id=info.request_id)
                # Never retain a Torch traceback/cause through idle scheduling.
                exc.__traceback__ = exc.__cause__ = exc.__context__ = None
                if info.code == ErrorCode.CUDA_OUT_OF_MEMORY:
                    await self.loop.run_in_executor(self.executor, self.engine.unload)
            except Exception:
                error = RagError(ErrorCode.WORKER_UNAVAILABLE, 'local model execution failed', stage=job.phase)
            await self.finish(job, response=response if error is None else None, error=error)
            self.running = None
            self.last_activity = time.monotonic()

    async def expire_queued(self):
        # Independent from inference await: deadlines remove waiting requests
        # even while a long model load or CUDA batch is still in progress.
        while True:
            expired = [job for job in self.queue if time.monotonic_ns() >= job.request.context.deadline_monotonic_ns]
            # Detach the complete expired set before any send can yield to a
            # disconnect/cancel handler which also removes queue entries.
            for job in expired:
                self.queue.remove(job)
                self.queue_bytes -= job.size
            for job in expired:
                await self.finish(job, error=RagError(ErrorCode.DEADLINE_EXCEEDED, 'queued request expired', stage='queue'))
            await asyncio.sleep(0.01)


async def serve(path, frozen_identity):
    bootstrap = checked_bootstrap(private_json(path))
    config = parse(WorkerExecutionConfig, bootstrap['config'])
    if frozen_identity != bootstrap['expected_identity']:
        raise protocol_error('installed runtime changed between description and startup')
    coordination = coordination_directory()
    # No GPU imports/context until the fixed OS-user/device lifetime lock is held.
    with ProcessLock(coordination / 'lifetime.lock'):
        from .lifecycle import remaining
        remaining(bootstrap['startup_deadline'])
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='rag-cuda')
        engine = None
        try:
            from .engine import CudaEngine
            loop = asyncio.get_running_loop()
            engine = await loop.run_in_executor(executor, CudaEngine, config.model_cache)
            worker = Worker(config, bootstrap, frozen_identity, executor, engine)
            server = await asyncio.start_server(worker.accept, '127.0.0.1', 0, limit=config.max_frame_bytes + 4,
                                                backlog=config.max_connections)
            port = server.sockets[0].getsockname()[1]
            metadata = {'instance_id': bootstrap['instance_id'], 'pid': os.getpid(),
                        'process_birth': process_birth(os.getpid()), 'port': port,
                        'runtime_dir': config.runtime_dir, 'identity': frozen_identity,
                        'actual_device': engine.device_identity,
                        'config': config.model_dump(mode='json')}
            atomic_private_json(coordination / 'active.json', metadata)
            expiry = asyncio.create_task(worker.expire_queued())
            try:
                async with server:
                    await worker.scheduler()
            finally:
                expiry.cancel()
                await asyncio.gather(expiry, return_exceptions=True)
        except RagError as exc:
            atomic_private_json(Path(path).with_suffix('.error.json'), {
                'instance_id': bootstrap['instance_id'], 'error': exc.error.model_dump(mode='json')})
            raise
        finally:
            if engine is not None:
                await asyncio.get_running_loop().run_in_executor(executor, engine.unload)
            executor.shutdown(wait=True)
            # Never unlink either lock file. Only own exact instance metadata.
            try:
                with ProcessLock(coordination / 'startup.lock').acquire(timeout_ms=3000):
                    active = coordination / 'active.json'
                    if active.exists() and private_json(active).get('instance_id') == bootstrap['instance_id']:
                        active.unlink()
                    if Path(path).exists() and private_json(path).get('instance_id') == bootstrap['instance_id']:
                        Path(path).unlink()
            except (OSError, RagError):
                pass  # Stale metadata is harmless; next startup verifies OS lock.


def main():
    parser = argparse.ArgumentParser(description='Private local inference worker')
    parser.add_argument('--describe', action='store_true')
    parser.add_argument('--bootstrap')
    args = parser.parse_args()
    try:
        identity = describe()  # one frozen digest for this process lifetime
        if args.describe:
            print(json.dumps({'ok': True, 'identity': identity}))
        elif args.bootstrap and Path(args.bootstrap).exists():
            asyncio.run(serve(args.bootstrap, identity))
        else:
            parser.error('bootstrap is required')
    except RagError as exc:
        # Error records contain only fixed diagnostic strings, never input/token.
        if args.describe:
            print(json.dumps({'ok': False, 'error': exc.error.model_dump(mode='json')}))
        elif args.bootstrap and Path(args.bootstrap).exists():
            bootstrap = private_json(args.bootstrap)
            atomic_private_json(Path(args.bootstrap).with_suffix('.error.json'), {
                'instance_id': bootstrap['instance_id'], 'error': exc.error.model_dump(mode='json')})
        raise SystemExit(2)


if __name__ == '__main__':
    main()
