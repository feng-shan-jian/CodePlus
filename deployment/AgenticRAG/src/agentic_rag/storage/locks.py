"""Stable OS advisory lock files: never unlink to transfer execution rights."""

import errno
import os
from pathlib import Path
import time

from ..domain import ErrorCode, RagError
from .paths import check_path, failure

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None
        self._pid = os.getpid()

    def acquire(self, *, timeout_ms: int = 0) -> "ProcessLock":
        if self._fd is not None:
            raise failure("lock handle is already held")
        if self._pid != os.getpid():
            raise failure("create a new lock handle in each process")
        check_path(self.path)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        os.set_inheritable(fd, False)
        deadline = time.monotonic() + timeout_ms / 1000
        try:
            while True:
                try:
                    if os.name == "nt":
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise RagError(ErrorCode.LIBRARY_BUSY, "OS lifetime lock is held; retry later", stage="lock") from exc
                    time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        try:
            self.check()
        except BaseException:
            self.close()
            raise
        return self

    def check(self) -> None:
        if self._fd is None or self._pid != os.getpid():
            raise failure("a live lock acquired by this process is required")
        check_path(self.path)
        if not os.path.samestat(os.fstat(self._fd), self.path.stat()):
            raise failure("lock file identity changed; ownership is uncertain")

    def close(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                if self._pid == os.getpid():
                    if os.name == "nt":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def __enter__(self):
        if self._fd is None:
            return self.acquire()
        self.check()
        return self

    def __exit__(self, *_):
        self.close()
