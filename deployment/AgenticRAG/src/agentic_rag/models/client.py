"""Synchronous adapters backed by tracked requests in an authenticated worker."""

import json
from contextlib import contextmanager
import select
import socket
import struct
import threading
import time
from uuid import UUID, uuid4

from ..capabilities import EmbeddingResponse, RerankResponse, validate_response
from ..domain import ErrorCode, RagError
from .identity import process_birth
from .lifecycle import discover, error_from_record, remaining
from .protocol import Submit, decode, encode, parse, protocol_error


class RequestHandle:
    """result-ready and actual execution-finished are intentionally separate.

    A lost connection produces a failure immediately and leaves completion
    unknown. wait_finished accepts only a real finished message or OS evidence
    that the authenticated actual worker process identity no longer exists.
    """

    def __init__(self, client, request):
        self.client, self.request = client, request
        self.condition = threading.Condition()
        self.phases = []
        self.response, self.error = None, None
        self.result_ready, self.execution_finished = False, False
        self.cancel_ack, self.completion_source = False, None
        self.cancel_requested = False
        self.model_status = None
        # Snapshot the authenticated hello, before this request is sent. Return
        # copies so neither later metadata mutation nor callers can change it.
        self._worker_identity = tuple((client._request_identity or {}).items())

    @property
    def worker_identity(self):
        return dict(self._worker_identity)

    def _worker_dead(self):
        identity = self.worker_identity
        if not identity:
            return False
        try:
            return process_birth(identity['pid']) != identity['process_birth']
        except OSError:
            return False

    def result(self, timeout=None):
        local_end = float('inf') if timeout is None else time.monotonic() + timeout
        with self.condition:
            while not self.result_ready:
                left = min(local_end - time.monotonic(),
                           (self.request.context.deadline_monotonic_ns - time.monotonic_ns()) / 1e9)
                if left <= 0:
                    if time.monotonic_ns() >= self.request.context.deadline_monotonic_ns:
                        self.error = RagError(ErrorCode.DEADLINE_EXCEEDED, 'request deadline expired', stage='client_wait')
                        self.result_ready = True
                        # Worker enforces this same absolute deadline. Do not
                        # extend result latency by synchronously sending cancel.
                        break
                    raise TimeoutError('local result wait expired; execution remains tracked')
                self.condition.wait(left)
            error, response = self.error, self.response
        if error:
            raise error
        return response

    def cancel(self):
        with self.condition:
            if self.execution_finished:
                return
            self.cancel_requested = True
        self.client._cancel(self)

    def wait_phase(self, phase, timeout=30):
        end = time.monotonic() + timeout
        with self.condition:
            while phase not in self.phases:
                if self.result_ready or time.monotonic() >= end:
                    raise TimeoutError(f'phase {phase} was not observed')
                self.condition.wait(end - time.monotonic())

    def wait_finished(self, timeout=None):
        end = float('inf') if timeout is None else time.monotonic() + timeout
        with self.condition:
            while not self.execution_finished:
                if self._worker_dead():
                    self.execution_finished, self.completion_source = True, 'worker_process_death'
                    break
                if time.monotonic() >= end:
                    return False
                self.condition.wait(min(0.05, end - time.monotonic()))
            return True


class LocalModelClient:
    def __init__(self, config, *, owner_id=None):
        self.config, self.owner_id = config, owner_id or uuid4()
        self.lock, self.write_lock, self.status_lock = threading.RLock(), threading.Lock(), threading.Lock()
        self.handles = {}
        self.sock, self.reader, self.metadata = None, None, None
        self.closing, self.broken = False, False
        self._status_event, self._status = threading.Event(), None
        self._request_identity = None

    @contextmanager
    def _locked(self, deadline):
        if not self.lock.acquire(timeout=remaining(deadline)):
            raise RagError(ErrorCode.DEADLINE_EXCEEDED, 'deadline exceeded waiting for client ownership lock', stage='client_lock')
        try:
            remaining(deadline)
            yield
        finally:
            self.lock.release()

    def _connect(self, deadline):
        if self.sock is not None:
            return
        metadata, token = discover(self.config, deadline)
        try:
            sock = socket.create_connection(('127.0.0.1', metadata['port']), timeout=min(remaining(deadline), self.config.handshake_timeout_ms / 1000))
        except OSError as exc:
            self.broken = True
            raise RagError(ErrorCode.WORKER_UNAVAILABLE, 'authenticated worker endpoint is unavailable', stage='ipc_connect') from exc
        sock.setblocking(False)
        self.sock, self.metadata = sock, metadata
        try:
            self._send({'type': 'hello', 'protocol': 1, 'token': token, 'owner_id': str(self.owner_id),
                        'instance_id': metadata['instance_id'], **{key: metadata['identity'][key] for key in
                            ('implementation_digest', 'runtime_fingerprint', 'clock_domain')}}, deadline)
            ack = self._receive(min(deadline, time.monotonic_ns() + self.config.handshake_timeout_ms * 1000000))
        except Exception:
            self.broken = True
            sock.close()
            raise
        expected = {'type', 'protocol', 'session_id', 'instance_id', 'pid', 'process_birth', 'identity', 'actual_device'}
        if (set(ack) != expected or ack['type'] != 'hello_ack' or type(ack['protocol']) is not int or ack['protocol'] != 1
                or any(ack[key] != metadata[key] for key in ('instance_id', 'pid', 'process_birth', 'identity', 'actual_device'))):
            sock.close()
            raise protocol_error('worker handshake response mismatch')
        UUID(ack['session_id'])
        self._request_identity = {key: ack[key] for key in ('pid','process_birth','instance_id','session_id')}
        self.reader = threading.Thread(target=self._read_loop, name='rag-worker-client', daemon=True)
        self.reader.start()

    def _send(self, value, deadline=None):
        deadline = min(deadline or (time.monotonic_ns() + self.config.io_timeout_ms * 1000000),
                       time.monotonic_ns() + self.config.io_timeout_ms * 1000000)
        data = memoryview(encode(value, self.config.max_frame_bytes))
        if not self.write_lock.acquire(timeout=remaining(deadline)):
            raise RagError(ErrorCode.DEADLINE_EXCEEDED, 'deadline exceeded waiting for worker write lock', stage='ipc_write')
        try:
            while data:
                if not select.select([], [self.sock], [], remaining(deadline))[1]:
                    raise TimeoutError('worker write timed out')
                sent = self.sock.send(data)
                if not sent:
                    raise ConnectionError('worker connection closed')
                data = data[sent:]
        finally:
            self.write_lock.release()

    def _bytes(self, count, deadline):
        blocks = bytearray()
        while len(blocks) < count:
            timeout = None if deadline is None else remaining(deadline)
            if not select.select([self.sock], [], [], timeout)[0]:
                raise TimeoutError('worker read timed out')
            data = self.sock.recv(count - len(blocks))
            if not data:
                raise ConnectionError('worker connection closed')
            blocks.extend(data)
        return bytes(blocks)

    def _receive(self, deadline=None):
        first = self._bytes(1, deadline)
        end = min(deadline or (time.monotonic_ns() + self.config.io_timeout_ms * 1000000),
                  time.monotonic_ns() + self.config.io_timeout_ms * 1000000)
        length, = struct.unpack('!I', first + self._bytes(3, end))
        if not 0 < length <= self.config.max_frame_bytes:
            raise protocol_error('worker response frame too large')
        return decode(self._bytes(length, end))

    def _read_loop(self):
        try:
            while True:
                message = self._receive()
                kind = message.get('type')
                if kind == 'status':
                    if set(message) != {'type', 'model_status', 'queued', 'queue_bytes', 'running', 'clients', 'instance_id'}:
                        raise protocol_error('unknown status fields')
                    if message['instance_id'] != self.metadata['instance_id']:
                        raise protocol_error('status instance mismatch')
                    self._status = message
                    self._status_event.set()
                    continue
                rid = UUID(message.get('request_id', ''))
                with self.lock:
                    handle = self.handles.get(rid)
                if handle is None:
                    raise protocol_error('response does not belong to a registered request')
                with handle.condition:
                    if kind == 'phase' and set(message) == {'type', 'request_id', 'phase'}:
                        if message['phase'] not in ('queued', 'validation', 'loading', 'inference_started') or handle.execution_finished:
                            raise protocol_error('invalid phase')
                        handle.phases.append(message['phase'])
                    elif kind == 'cancel_ack' and set(message) == {'type', 'request_id', 'accepted'}:
                        if type(message['accepted']) is not bool:
                            raise protocol_error('invalid cancellation acknowledgment')
                        handle.cancel_ack = message['accepted']
                    elif kind == 'finished' and set(message) == {'type', 'request_id', 'execution_finished', 'response', 'error', 'model_status'}:
                        if handle.execution_finished or message['execution_finished'] is not True:
                            raise protocol_error('duplicate or invalid completion')
                        if (message['response'] is None) == (message['error'] is None):
                            raise protocol_error('completion needs one response or error')
                        if message['error'] is not None:
                            error = error_from_record(message['error'])
                            if not handle.result_ready:
                                handle.error = error
                        else:
                            cls = RerankResponse if handle.request.operation == 'rerank' else EmbeddingResponse
                            response = parse(cls, message['response'])
                            validate_response(response, handle.request.items, handle.request.profile, handle.request.context)
                            if not handle.result_ready:
                                if handle.cancel_requested:
                                    handle.error = RagError(ErrorCode.CANCELLED, 'cancelled model result discarded', stage='delivery')
                                elif time.monotonic_ns() >= handle.request.context.deadline_monotonic_ns:
                                    handle.error = RagError(ErrorCode.DEADLINE_EXCEEDED, 'late model result discarded', stage='delivery')
                                else:
                                    handle.response = response
                        handle.result_ready, handle.execution_finished = True, True
                        handle.completion_source, handle.model_status = 'worker_finished', message['model_status']
                    else:
                        raise protocol_error('unknown response type or fields')
                    handle.condition.notify_all()
        except Exception as exc:
            self._broken(exc)

    def _broken(self, exc):
        with self.lock:
            self.broken = True
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
            for handle in self.handles.values():
                with handle.condition:
                    if not handle.result_ready:
                        handle.error = (exc if isinstance(exc, RagError) else RagError(
                            ErrorCode.WORKER_UNAVAILABLE, 'worker communication ended; execution completion unknown', stage='ipc'))
                        handle.result_ready = True
                    handle.condition.notify_all()
            self._status_event.set()

    def _worker_dead(self):
        if self.metadata is None:
            return False
        try:
            return process_birth(self.metadata['pid']) != self.metadata['process_birth']
        except OSError:
            return False

    def _submit(self, operation, items, profile, context, query=None):
        request = Submit(type='submit', operation=operation, profile=profile, profile_fingerprint=profile.identity,
                         context=context, items=items, query=query)
        with self._locked(context.deadline_monotonic_ns):
            if self.closing or self.broken:
                raise RagError(ErrorCode.WORKER_UNAVAILABLE, 'client is closed or disconnected; no automatic replay', stage='client')
            if context.owner_id != self.owner_id or context.request_id in self.handles:
                raise protocol_error('owner/request identity mismatch or reused request ID')
            if len(self.handles) >= self.config.max_session_requests:
                raise protocol_error('session request limit reached; open a new owner session')
            remaining(context.deadline_monotonic_ns)
            self._connect(context.deadline_monotonic_ns)
            handle = RequestHandle(self, request)
            self.handles[context.request_id] = handle
            try:
                self._send(request.model_dump(mode='json'), context.deadline_monotonic_ns)
            except Exception as exc:
                self._broken(exc)
            return handle

    def submit_documents(self, items, profile, context):
        return self._submit('documents', items, profile, context)

    def submit_query(self, query, profile, context):
        return self._submit('query', (query,), profile, context)

    def submit_rerank(self, query, candidates, profile, context):
        return self._submit('rerank', candidates, profile, context, query)

    def embed_documents(self, items, profile, context):
        return self.submit_documents(items, profile, context).result()

    def embed_query(self, query, profile, context):
        return self.submit_query(query, profile, context).result()

    def rerank(self, query, candidates, profile, context):
        return self.submit_rerank(query, candidates, profile, context).result()

    def _cancel(self, handle):
        with self.lock:
            if self.broken or self.sock is None:
                return
            try:
                self._send({'type': 'cancel', 'request_id': str(handle.request.context.request_id)})
            except Exception as exc:
                self._broken(exc)

    def status(self):
        with self.status_lock:
            with self.lock:
                if self.broken or self.closing or self.sock is None:
                    raise RagError(ErrorCode.WORKER_UNAVAILABLE, 'client has no active connection', stage='client')
                self._status_event.clear()
                self._send({'type': 'status'})
            if not self._status_event.wait(self.config.io_timeout_ms / 1000) or self.broken:
                raise RagError(ErrorCode.WORKER_UNAVAILABLE, 'worker status unavailable', stage='ipc')
            return self._status

    def close(self, *, drain_timeout=5):
        with self.lock:
            if self.closing:
                return
            self.closing = True
            handles = list(self.handles.values())
            for handle in handles:
                if not handle.execution_finished:
                    self._cancel(handle)
        end = time.monotonic() + drain_timeout
        for handle in handles:
            handle.wait_finished(max(0, end - time.monotonic()))
        with self.lock:
            if self.sock is not None:
                try:
                    self._send({'type': 'goodbye'})
                    self.sock.shutdown(socket.SHUT_RDWR)
                except (OSError, RagError, ValueError):
                    pass
                self.sock.close()
        if self.reader is not None and self.reader is not threading.current_thread():
            self.reader.join(self.config.io_timeout_ms / 1000)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def create_local_provider(assembled, *, owner_id=None):
    """Factory receives one assembled config, never reads implicit environment."""
    if assembled.worker is None:
        raise RagError(ErrorCode.INVALID_CONFIGURATION, 'local worker execution configuration required', stage='provider_selection')
    return LocalModelClient(assembled.worker, owner_id=owner_id)
