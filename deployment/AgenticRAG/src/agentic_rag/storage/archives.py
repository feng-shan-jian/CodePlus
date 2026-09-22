"""Immutable content-addressed objects. Metadata is a subsequent transaction."""

from dataclasses import dataclass
import ctypes
import hashlib
import os
from pathlib import Path
import re
from typing import BinaryIO
from uuid import uuid4

from .locks import ProcessLock
from .paths import DataDirectory, failure, sync_directory


@dataclass(frozen=True)
class Archive:
    sha256: str
    size_bytes: int


class ArchiveStore:
    def __init__(self, directory: DataDirectory):
        self.directory = directory

    def _path(self, digest: str) -> Path:
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise failure("invalid archive SHA256")
        return self.directory.path("archives", digest[:2], digest)

    def verify(self, digest: str) -> Archive:
        self.directory.verify_identity()
        path = self._path(digest)
        actual = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    actual.update(block)
        except OSError as exc:
            raise failure(f"archive unavailable: {digest}") from exc
        if actual.hexdigest() != digest:
            raise failure(f"archive checksum mismatch: {digest}")
        return Archive(digest, size)

    def put(self, source: BinaryIO, *, expected_hash: str | None = None) -> Archive:
        self.directory.verify_identity()
        if expected_hash is not None:
            self._path(expected_hash)
        temp = self.directory.path("staging", f"archive-{uuid4()}.tmp")
        digest, size = hashlib.sha256(), 0
        try:
            with temp.open("xb") as stream:
                while block := source.read(1024 * 1024):
                    if not isinstance(block, bytes):
                        raise failure("archive source must yield bytes")
                    stream.write(block)
                    digest.update(block)
                    size += len(block)
                stream.flush()
                os.fsync(stream.fileno())
            actual = digest.hexdigest()
            if expected_hash is not None and actual != expected_hash:
                raise failure("captured archive does not match expected hash")
            # Read back the completed temporary object before publication.
            with temp.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != actual:
                    raise failure("archive temporary file checksum mismatch")
            target = self._path(actual)
            target.parent.mkdir(exist_ok=True)
            # A short per-object OS lock serializes the exists+rename protocol.
            # Copying is outside the object lock and opens no DB transaction;
            # the caller may retain its library mutation lease throughout.
            with ProcessLock(self.directory.path("locks", f"archive-{actual}.lock")).acquire(timeout_ms=1500):
                if target.exists():
                    return self.verify(actual)
                _complete(temp, target)
                sync_directory(target.parent)
                sync_directory(self.directory.path("archives"))
            return Archive(actual, size)
        finally:
            if temp.exists():
                temp.unlink()

    def read(self, digest: str) -> bytes:
        self.verify(digest)
        return self._path(digest).read_bytes()


def _complete(source: Path, destination: Path) -> None:
    if os.name == "nt":
        # Same-volume rename, no COPY_ALLOWED and no REPLACE_EXISTING. The OS
        # refuses an existing target even if an external writer ignores our lock.
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int
        if not move(str(source), str(destination), 0x8):  # MOVEFILE_WRITE_THROUGH
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        # The stable per-digest lock makes the existence check + rename atomic
        # among cooperating local processes; ownership excludes external writers.
        os.rename(source, destination)
