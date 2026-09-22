"""Physical path policy and stable source handles; no database or parsing."""

from contextlib import contextmanager
import ctypes
import hashlib
import os
from pathlib import Path
import stat

from ..domain import ErrorCode, RagError
from .records import FileStamp


def input_error(message, code=ErrorCode.INVALID_INPUT):
    return RagError(code, message, stage='input_snapshot')


def _kernel():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                  ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    kernel.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
    return kernel


@contextmanager
def _windows_handle(path, *, read=False):
    kernel = _kernel()
    handle = kernel.CreateFileW(str(path), 0x80000000 if read else 0x80,
                                1 if read else 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield kernel, handle
    finally:
        kernel.CloseHandle(handle)


def _final_path(kernel, handle):
    size = kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size + 1)
    count = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not count or count >= len(buffer):
        raise input_error('source path changed while resolving')
    value = buffer.value
    if not value.startswith('\\\\?\\') or value.startswith('\\\\?\\UNC\\'):
        raise input_error('only local DOS-volume input paths are supported')
    return Path(value[4:])


def normalize_source(value: str | Path) -> Path:
    """No Unicode folding or file-ID matching; symlinks/reparse are rejected."""
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise input_error('input requires an absolute path without parent traversal')
    if os.name == 'nt':
        if str(path).startswith('\\\\') or ':' in str(path)[2:]:
            raise input_error('UNC, device paths and alternate streams are unsupported')
        drive_type = _kernel().GetDriveTypeW
        drive_type.argtypes = [ctypes.c_wchar_p]
        if drive_type(path.anchor) != 3:
            raise input_error('input requires a local fixed drive')
        if any(part.endswith((' ', '.')) for part in path.parts[1:]):
            raise input_error('ambiguous trailing characters are unsupported')
    for component in [*reversed(path.parents), path]:
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise input_error('symbolic links and reparse points are unsupported input boundaries')
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise input_error('special files are unsupported inputs')
        if os.name == 'nt' and stat.S_ISDIR(info.st_mode):
            with _windows_handle(component) as (kernel, handle):
                flags = ctypes.c_uint32()
                if not kernel.GetFileInformationByHandleEx(handle, 23, ctypes.byref(flags), ctypes.sizeof(flags)):
                    raise input_error('cannot verify directory case-sensitivity policy')
                # Both modes are supported: GetFinalPathNameByHandleW returns
                # the actual spelling; URI strings are compared exactly.
    if os.name == 'nt':
        with _windows_handle(path) as (kernel, handle):
            return _final_path(kernel, handle)
    return path


def file_stamp(stream) -> FileStamp:
    info = os.fstat(stream.fileno())
    changed = info.st_ctime_ns
    modified = info.st_mtime_ns
    if os.name == 'nt':
        import msvcrt
        class BasicInfo(ctypes.Structure):
            _fields_ = [('creation', ctypes.c_int64), ('access', ctypes.c_int64),
                        ('write', ctypes.c_int64), ('change', ctypes.c_int64), ('attributes', ctypes.c_uint32)]
        basic = BasicInfo()
        kernel = _kernel()
        if not kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(stream.fileno()), 0,
                                                  ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ctypes.WinError(ctypes.get_last_error())
        changed, modified = basic.change * 100, basic.write * 100
    if not stat.S_ISREG(info.st_mode):
        raise input_error('source is no longer an ordinary file', ErrorCode.SOURCE_CHANGED)
    return FileStamp(device=info.st_dev, inode=info.st_ino, size_bytes=info.st_size,
                     modified_ns=modified, changed_ns=changed)


@contextmanager
def open_source(path):
    if os.name == 'nt':
        import msvcrt
        # Duplicate the handle for fd ownership; keep the original protected
        # handle until the caller has accepted the durable checkpoint.
        with _windows_handle(path, read=True) as (_, handle):
            kernel = _kernel()
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            kernel.DuplicateHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
                                               ctypes.c_int, ctypes.c_uint32]
            duplicate = ctypes.c_void_p()
            process = kernel.GetCurrentProcess()
            if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                fd = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle(duplicate)
                raise
            with os.fdopen(fd, 'rb', buffering=0) as stream:
                yield stream
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb', buffering=0) as stream:
            yield stream


class VerifiedReader:
    """EOF is accepted only after length/state and a second full hash agree."""
    def __init__(self, stream, before, cancel):
        self.stream, self.before, self.cancel = stream, before, cancel
        self.digest, self.count, self.complete = hashlib.sha256(), 0, False

    def read(self, size):
        if self.cancel is not None and self.cancel():
            raise input_error('input capture cancelled', ErrorCode.CANCELLED)
        if self.complete:
            return b''
        block = self.stream.read(size)
        if block:
            self.count += len(block)
            self.digest.update(block)
            if self.count > self.before.size_bytes:
                raise input_error('source grew during capture', ErrorCode.SOURCE_CHANGED)
            return block
        if self.count != self.before.size_bytes or file_stamp(self.stream) != self.before:
            raise input_error('source state or complete length changed during capture', ErrorCode.SOURCE_CHANGED)
        self.stream.seek(0)
        second = hashlib.sha256()
        second_count = 0
        while block := self.stream.read(size):
            if self.cancel is not None and self.cancel():
                raise input_error('input verification cancelled', ErrorCode.CANCELLED)
            second.update(block)
            second_count += len(block)
            if second_count > self.before.size_bytes:
                raise input_error('source grew during verification', ErrorCode.SOURCE_CHANGED)
        if second_count != self.count or second.digest() != self.digest.digest() or file_stamp(self.stream) != self.before:
            raise input_error('source changed during verification', ErrorCode.SOURCE_CHANGED)
        self.complete = True
        return b''
