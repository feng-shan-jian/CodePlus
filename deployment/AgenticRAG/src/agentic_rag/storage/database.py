"""Private short transaction boundary and versioned SQLite migration."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import os

import apsw

from ..domain import ErrorCode, RagError
from .locks import ProcessLock
from .paths import DataDirectory, failure

APPLICATION_ID = 0x41524147
SCHEMA_VERSION = 1


def runtime_fingerprint() -> dict:
    return {"apsw": apsw.apswversion(), "sqlite": apsw.sqlitelibversion(),
            "source_id": apsw.sqlite3_sourceid(), "compile_options": list(apsw.compile_options)}


def require_runtime() -> None:
    version = tuple(int(part) for part in apsw.sqlitelibversion().split("."))
    if version < (3, 51, 3):
        raise failure("SQLite >=3.51.3 is required for the WAL-reset fix")


class Database:
    def __init__(self, directory: DataDirectory, *, busy_timeout_ms: int = 1500):
        require_runtime()
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 10000:
            raise ValueError("busy_timeout_ms must be 1..10000")
        self.directory, self.busy_timeout_ms = directory, busy_timeout_ms
        self._pid = os.getpid()
        with ProcessLock(directory.path("locks", "schema.lock")).acquire(timeout_ms=busy_timeout_ms):
            connection = self._connect(create=True)
            try:
                version = connection.pragma("user_version")
                application = connection.pragma("application_id")
                if version not in (0, SCHEMA_VERSION) or application not in (0, APPLICATION_ID):
                    raise failure("unknown catalog application or schema version")
                schema = files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")
                digest = hashlib.sha256(schema.encode()).hexdigest()
                if version == 0:
                    if application != 0 or connection.execute("SELECT name FROM sqlite_master").fetchone():
                        raise failure("refusing to migrate an unrecognized database")
                    connection.pragma("journal_mode", "wal")
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute(schema)
                        connection.execute("INSERT INTO store_identity VALUES(1,?)", (str(directory.store_id),))
                        connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (1, digest, datetime.now(timezone.utc).isoformat()))
                        connection.pragma("application_id", APPLICATION_ID)
                        connection.pragma("user_version", SCHEMA_VERSION)
                        connection.execute("COMMIT")
                    except BaseException:
                        if not connection.get_autocommit():
                            connection.execute("ROLLBACK")
                        raise
                self._verify(connection)
                if connection.execute("SELECT sha256 FROM schema_migrations WHERE version=1").fetchone() != (digest,):
                    raise failure("schema migration fingerprint mismatch")
            finally:
                connection.close()

    def _connect(self, *, create: bool = False):
        if self._pid != os.getpid():
            raise failure("open a new Catalog in each process")
        self.directory.verify_identity()
        for name in ("catalog.sqlite", "catalog.sqlite-wal", "catalog.sqlite-shm"):
            self.directory.path(name)
        flags = apsw.SQLITE_OPEN_READWRITE | (apsw.SQLITE_OPEN_CREATE if create else 0)
        connection = apsw.Connection(str(self.directory.path("catalog.sqlite")), flags=flags)
        try:
            connection.set_busy_timeout(self.busy_timeout_ms)
            connection.pragma("foreign_keys", True)
            connection.pragma("synchronous", "FULL")
            if not connection.pragma("foreign_keys") or connection.pragma("synchronous") != 2:
                raise failure("required SQLite connection settings unavailable")
            return connection
        except BaseException:
            connection.close()
            raise

    def _verify(self, connection) -> None:
        if (connection.pragma("user_version") != SCHEMA_VERSION or
                connection.pragma("application_id") != APPLICATION_ID or
                connection.execute("SELECT store_id FROM store_identity WHERE singleton=1").fetchone() != (str(self.directory.store_id),)):
            raise failure("catalog identity or schema changed")
        if connection.pragma("journal_mode") != "wal":
            raise failure("catalog requires WAL mode")

    @contextmanager
    def transaction(self, *, write: bool = False):
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._verify(connection)
            yield connection
            connection.execute("COMMIT")
        except apsw.BusyError as exc:
            raise RagError(ErrorCode.LIBRARY_BUSY, "SQLite busy timeout expired; retry later", stage="metadata") from exc
        except apsw.Error as exc:
            raise failure(f"SQLite operation failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if connection is not None:
                try:
                    if not connection.get_autocommit():
                        connection.execute("ROLLBACK")
                finally:
                    connection.close()
