"""Conservative local, application-owned directory boundary (no shared storage)."""

import ctypes
import json
import os
from pathlib import Path
import stat
from uuid import UUID, uuid4

from ..domain import ErrorCode, RagError


def failure(message: str) -> RagError:
    return RagError(ErrorCode.STORAGE_FAILURE, message, stage="storage")


def check_path(path: Path) -> None:
    """Reject links/reparse points in every existing component, including files.

    The containing user directory must not be concurrently modified by another
    application. This is an accidental-misconfiguration guard, not a sandbox.
    """
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise failure(f"links and reparse points are unsupported: {part}")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise failure(f"unsupported filesystem object: {part}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise failure(f"hard-linked storage files are unsupported: {part}")


def local_directory(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or str(value).startswith(("\\\\", "//")) or ".." in path.parts:
        raise failure("data_dir must be an absolute local path for this OS")
    if os.name == "nt":
        # GetDriveType also rejects mapped network drives and inaccessible roots.
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) != 3:
            raise failure("data_dir requires a local fixed drive")
        if any(":" in component for component in path.parts[1:]):
            raise failure("alternate streams are not storage paths")
    elif os.name == "posix":
        # Linux only: use the longest matching mount. An unknown filesystem is
        # rejected instead of guessing whether its locking/durability is local.
        mounts = Path("/proc/self/mountinfo")
        if not mounts.is_file():
            raise failure("local filesystem identification requires Linux mountinfo")
        best = (0, "")
        for line in mounts.read_text().splitlines():
            before, after = line.split(" - ", 1)
            mount = before.split()[4].replace("\\040", " ").replace("\\134", "\\")
            if path.is_relative_to(mount) and len(mount) > best[0]:
                best = (len(mount), after.split()[0])
        if best[1] not in {"ext4", "ext3", "ext2", "xfs", "btrfs", "f2fs", "zfs"}:
            raise failure(f"unsupported or nonlocal filesystem: {best[1]}")
    else:
        raise failure("unsupported storage platform")
    check_path(path)
    return path


def sync_directory(path: Path) -> None:
    # Windows Python provides no portable directory fsync. File data is flushed;
    # rename metadata durability across hardware loss is a documented limitation.
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class DataDirectory:
    MARKER = ".agentic-rag.json"

    def __init__(self, value: str | Path):
        self.root = local_directory(value)
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / self.MARKER
        check_path(marker)
        if not marker.exists():
            if any(self.root.iterdir()):
                raise failure("refusing to claim a nonempty unowned data_dir")
            identity = {"format": "codeplus-agentic-rag", "version": 1, "store_id": str(uuid4())}
            try:
                with marker.open("x", encoding="utf-8") as stream:
                    json.dump(identity, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                sync_directory(self.root)
            except FileExistsError:
                pass  # concurrent first opener: parse complete marker or fail closed
        try:
            identity = json.loads(marker.read_text(encoding="utf-8"))
            if set(identity) != {"format", "version", "store_id"} or identity["format"] != "codeplus-agentic-rag" or type(identity["version"]) is not int or identity["version"] != 1:
                raise ValueError("unknown marker format")
            self.store_id = UUID(identity["store_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise failure("invalid or incomplete data_dir ownership marker") from exc
        for name in ("archives", "staging", "locks"):
            self.path(name).mkdir(exist_ok=True)

    def path(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        if not path.is_relative_to(self.root) or any(part in (".", "..") for part in path.parts):
            raise failure("path escapes the owned data directory")
        check_path(path)
        return path

    def verify_identity(self) -> None:
        marker = self.path(self.MARKER)
        try:
            identity = json.loads(marker.read_text(encoding="utf-8"))
            if type(identity.get("version")) is not int or identity != {"format": "codeplus-agentic-rag", "version": 1, "store_id": str(self.store_id)}:
                raise ValueError("identity changed")
        except (OSError, ValueError) as exc:
            raise failure("data directory identity changed") from exc
