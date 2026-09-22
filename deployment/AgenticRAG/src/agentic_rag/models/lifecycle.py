"""Bounded target-interpreter startup and same-user/device worker discovery."""

import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

from ..domain import ErrorCode, RagError
from ..storage.locks import ProcessLock
from .identity import (atomic_private_json, clock_domain, coordination_directory, fail,
                       implementation_digest, private_directory, private_json, process_birth,
                       checked_active, checked_bootstrap, checked_identity)


def remaining(deadline):
    value = (deadline - time.monotonic_ns()) / 1e9
    if value <= 0:
        raise RagError(ErrorCode.DEADLINE_EXCEEDED, 'deadline expired during local worker startup', stage='startup')
    return value


def error_from_record(record):
    from ..domain import ErrorInfo
    from .protocol import parse
    info = parse(ErrorInfo, record)
    return RagError(info.code, info.message, stage=info.stage, request_id=info.request_id)


def target_identity(config, deadline):
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    # Explicit isolated interpreter. It describes installed dependencies, not GPU.
    args = [config.executable, '-I', '-B', '-m', 'agentic_rag.models.worker', '--describe']
    try:
        result = subprocess.run(args, cwd=config.runtime_dir, env=env, capture_output=True, text=True,
                                encoding='utf-8', timeout=remaining(deadline),
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        from .protocol import decode
        record = decode(result.stdout.encode('utf-8'))
    except subprocess.TimeoutExpired as exc:
        raise RagError(ErrorCode.DEADLINE_EXCEEDED, 'target interpreter preflight exceeded deadline', stage='dependencies') from exc
    except (OSError, ValueError, RagError) as exc:
        raise RagError(ErrorCode.DEPENDENCY_UNAVAILABLE, 'target interpreter cannot describe installed local-models package', stage='dependencies') from exc
    if not record.get('ok'):
        raise error_from_record(record['error'])
    if result.returncode or set(record) != {'ok', 'identity'}:
        raise fail('invalid target interpreter identity')
    identity = checked_identity(record['identity'])
    if identity['implementation_digest'] != implementation_digest() or identity['clock_domain'] != clock_domain():
        raise fail('target installed implementation or boot clock differs')
    return identity, env


def discover(config, deadline):
    deadline = min(deadline, time.monotonic_ns() + config.startup_timeout_ms * 1000000)
    runtime = private_directory(config.runtime_dir)
    coordination = coordination_directory()
    identity, env = target_identity(config, deadline)
    lock = ProcessLock(coordination / 'startup.lock')
    try:
        lock.acquire(timeout_ms=int(remaining(deadline) * 1000))
    except RagError as exc:
        raise RagError(ErrorCode.DEADLINE_EXCEEDED, 'worker startup coordination exceeded deadline', stage='startup') from exc
    with lock:
        lifetime = ProcessLock(coordination / 'lifetime.lock')
        try:
            lifetime.acquire()
        except RagError:
            active_path = coordination / 'active.json'
            if not active_path.exists():
                raise fail('device worker lock is held without authenticated metadata', ErrorCode.WORKER_BUSY, 'startup')
            active = checked_active(private_json(active_path))
            if active['identity'] != identity or active['config'] != config.model_dump(mode='json'):
                raise fail('device already has an incompatible worker configuration', ErrorCode.IDENTITY_MISMATCH, 'startup')
            if process_birth(active['pid']) != active['process_birth']:
                raise fail('device ownership is uncertain; lock held with stale process metadata', ErrorCode.WORKER_BUSY, 'startup')
            bootstrap = checked_bootstrap(private_json(Path(active['runtime_dir']) / ('bootstrap-' + active['instance_id'] + '.json')))
            if bootstrap['instance_id'] != active['instance_id']:
                raise fail('worker instance metadata does not match authentication resource')
            return active, bootstrap['token']
        else:
            # An exclusive OS lifetime lock is the authority for stale cleanup.
            # PID/metadata alone never authorizes killing a process or replacing.
            try:
                active_path = coordination / 'active.json'
                if active_path.exists():
                    # Keep lifetime held throughout cleanup: a child left by a
                    # dead starter must not publish between proof and deletion.
                    from .identity import no_links, verify_private
                    no_links(active_path)
                    verify_private(active_path)
                    active_path.unlink()
            finally:
                lifetime.close()
        instance = str(uuid4())
        token = secrets.token_hex(32)
        bootstrap_path = runtime / ('bootstrap-' + instance + '.json')
        atomic_private_json(bootstrap_path, {'config': config.model_dump(mode='json'), 'token': token,
                            'instance_id': instance, 'expected_identity': identity, 'startup_deadline': deadline})
        error_path = bootstrap_path.with_suffix('.error.json')
        process = subprocess.Popen([config.executable, '-I', '-B', '-m', 'agentic_rag.models.worker',
                                    '--bootstrap', str(bootstrap_path)], cwd=runtime, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                   close_fds=True)
        # A Windows venv launcher is not the actual worker identity. Read only
        # the authenticated instance's actual PID and creation identity below.
        while True:
            remaining(deadline)
            if error_path.exists():
                error = private_json(error_path)
                if error['instance_id'] == instance:
                    raise error_from_record(error['error'])
            active_path = coordination / 'active.json'
            if active_path.exists():
                active = checked_active(private_json(active_path))
                if active['instance_id'] == instance:
                    if active['identity'] != identity or process_birth(active['pid']) != active['process_birth']:
                        raise fail('new worker identity could not be verified')
                    return active, token
            if process.poll() is not None:
                raise RagError(ErrorCode.WORKER_UNAVAILABLE, 'local worker exited before startup completed', stage='startup')
            time.sleep(min(0.02, remaining(deadline)))
