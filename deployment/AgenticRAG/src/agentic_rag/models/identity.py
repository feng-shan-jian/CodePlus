"""Current user/device coordination, private runtime files and frozen code identity."""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
from uuid import UUID, uuid4

from .._schema import fingerprint
from ..domain import ErrorCode, RagError
from ..profiles import LocalRuntime


def fail(message, code=ErrorCode.IDENTITY_MISMATCH, stage='worker_identity'):
    return RagError(code, message, stage=stage)


def no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            value = part.lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value, 'st_file_attributes', 0) & 0x400:
                raise fail('runtime paths must not traverse symbolic links or reparse points')
    return path


def private_directory(path):
    path = no_links(path)
    if not path.parent.exists():
        private_directory(path.parent)
    try:
        if os.name == 'nt':
            from ._windows import private_mkdir
            private_mkdir(path)
        else:
            path.mkdir(mode=0o700, exist_ok=True)
        verify_private(path)
    except OSError as exc:
        raise fail('private runtime directory creation/ownership verification failed') from exc
    return path


def verify_private(path):
    path = no_links(path)
    if os.name == 'nt':
        from ._windows import verify_private as native_verify
        return native_verify(path)
    current = path.stat()
    if current.st_uid != os.getuid() or current.st_mode & 0o077:
        raise PermissionError('runtime mode/owner admits other users')
    return {'owner_current_user': True, 'mode': oct(stat.S_IMODE(current.st_mode))}


def coordination_directory():
    # Deliberately not configurable by data/cache/runtime directories or env.
    if os.name == 'nt':
        from ._windows import local_appdata
        # The native profile's fixed Temp directory supports the ephemeral IPC
        # metadata rename even when LocalAppData inherits Windows EFS. Never use
        # caller-controlled TEMP/TMP or the configured runtime_dir as lock scope.
        base = local_appdata() / 'Temp'
    else:
        import pwd
        base = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return private_directory(base / '.codeplus-agenticrag-workers' / 'cuda-0')


def atomic_private_json(path, value):
    path = no_links(path)
    verify_private(path.parent)
    temporary = path.with_name(path.name + '.' + str(uuid4()) + '.new')
    no_links(temporary)
    # Only callers holding the coordination/startup lock write shared filenames.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, allow_nan=False, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        verify_private(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def private_json(path):
    path = no_links(path)
    verify_private(path)
    if path.stat().st_size > 65536:
        raise fail('runtime metadata exceeds limit')
    from .protocol import decode
    return decode(path.read_bytes())


def checked_identity(value):
    expected = {'executable', 'module_path', 'python', 'packages', 'device', 'clock_domain',
                'device_environment', 'implementation_digest', 'runtime_fingerprint'}
    if not isinstance(value, dict) or set(value) != expected:
        raise fail('invalid worker runtime identity metadata')
    if any(not isinstance(value[key], str) or not re.fullmatch('[0-9a-f]{64}', value[key])
           for key in ('clock_domain', 'implementation_digest', 'runtime_fingerprint')):
        raise fail('invalid worker runtime fingerprints')
    if (value['device'] != 'cuda:0' or not isinstance(value['packages'], dict)
            or set(value['packages']) != {'torch', 'transformers', 'tokenizers'}
            or not isinstance(value['device_environment'], dict)
            or set(value['device_environment']) != {'CUDA_VISIBLE_DEVICES', 'NVIDIA_VISIBLE_DEVICES', 'CUDA_DEVICE_ORDER'}):
        raise fail('invalid worker device/dependency metadata')
    if any(not isinstance(value[key], str) or not value[key] for key in ('executable', 'module_path', 'python')):
        raise fail('invalid worker interpreter metadata')
    actual = {key: item for key, item in value.items() if key != 'runtime_fingerprint'}
    if fingerprint('local-worker-runtime', actual) != value['runtime_fingerprint']:
        raise fail('worker runtime metadata fingerprint differs')
    return value


def checked_bootstrap(value):
    from ..config import WorkerExecutionConfig
    from .protocol import parse
    try:
        if set(value) != {'config', 'token', 'instance_id', 'expected_identity', 'startup_deadline'}:
            raise ValueError()
        UUID(value['instance_id'])
        if not isinstance(value['token'], str) or not re.fullmatch('[0-9a-f]{64}', value['token']):
            raise ValueError()
        if type(value['startup_deadline']) is not int or value['startup_deadline'] <= 0:
            raise ValueError()
        parse(WorkerExecutionConfig, value['config'])
        checked_identity(value['expected_identity'])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise fail('invalid private worker startup metadata') from exc
    return value


def checked_active(value):
    from ..config import WorkerExecutionConfig
    from .protocol import parse
    try:
        if set(value) != {'instance_id', 'pid', 'process_birth', 'port', 'runtime_dir', 'identity', 'actual_device', 'config'}:
            raise ValueError()
        UUID(value['instance_id'])
        if (type(value['pid']) is not int or value['pid'] <= 0 or type(value['port']) is not int or
                not 1 <= value['port'] <= 65535 or not isinstance(value['process_birth'], str) or
                not value['process_birth'].isdigit()):
            raise ValueError()
        config = parse(WorkerExecutionConfig, value['config'])
        if value['runtime_dir'] != config.runtime_dir:
            raise ValueError()
        checked_identity(value['identity'])
        device = value['actual_device']
        if (not isinstance(device, dict) or set(device) != {'name', 'index', 'uuid', 'total_memory_bytes'} or
                type(device['index']) is not int or device['index'] != 0 or
                not isinstance(device['name'], str) or type(device['total_memory_bytes']) is not int or device['total_memory_bytes'] <= 0):
            raise ValueError()
        UUID(device['uuid'])
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise fail('invalid private active worker metadata') from exc
    return value


def process_birth(pid):
    if type(pid) is not int or pid <= 0:
        raise fail('invalid process identity')
    if os.name == 'nt':
        from ._windows import process_birth as native_birth
        return native_birth(pid)
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except FileNotFoundError:
        return None


def clock_domain():
    if os.name == 'nt':
        from ._windows import boot_id
        boot = boot_id()
    else:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return fingerprint('local-monotonic-clock', {'machine': platform.node(), 'boot': boot})


def implementation_digest():
    root = Path(__file__).resolve().parents[1]
    names = ['_schema.py', 'config.py', 'profiles.py', 'domain.py', 'capabilities.py', 'storage/locks.py', 'storage/paths.py']
    names += [str(p.relative_to(root)).replace('\\', '/') for p in (root / 'models').rglob('*') if p.suffix in ('.py', '.json')]
    return fingerprint('local-worker-implementation', {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                                     for name in sorted(names)})


def describe():
    # Executed in the target interpreter; does not import Torch or initialize GPU.
    runtime = LocalRuntime()
    packages = {}
    for name, expected in [('torch', runtime.torch_version), ('transformers', runtime.transformers_version),
                           ('tokenizers', runtime.tokenizers_version)]:
        if importlib.util.find_spec(name) is None:
            raise fail(f'local-models dependency missing: {name}', ErrorCode.DEPENDENCY_UNAVAILABLE, 'dependencies')
        packages[name] = importlib.metadata.version(name)
        if packages[name] != expected:
            raise fail(f'local-models dependency version differs: {name}', stage='dependencies')
    identity = {'executable': str(Path(sys.executable).absolute()), 'module_path': str(Path(__file__).resolve()),
                'python': platform.python_version(),
                'packages': packages, 'device': runtime.device, 'clock_domain': clock_domain(),
                'device_environment': {key: os.environ.get(key) for key in
                    ('CUDA_VISIBLE_DEVICES', 'NVIDIA_VISIBLE_DEVICES', 'CUDA_DEVICE_ORDER')},
                'implementation_digest': implementation_digest()}
    return {**identity, 'runtime_fingerprint': fingerprint('local-worker-runtime', identity)}
