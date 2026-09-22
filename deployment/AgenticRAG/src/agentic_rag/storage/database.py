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
SCHEMA_VERSION = 8


def runtime_fingerprint() -> dict:
    return {"apsw": apsw.apswversion(), "sqlite": apsw.sqlitelibversion(),
            "source_id": apsw.sqlite3_sourceid(), "compile_options": list(apsw.compile_options)}


def require_runtime() -> None:
    version = tuple(int(part) for part in apsw.sqlitelibversion().split("."))
    if version < (3, 51, 3):
        raise failure("SQLite >=3.51.3 is required for the WAL-reset fix")


_ADDITIVE_MIGRATIONS = (
    (2, 'inputs.sql'),
    (3, 'processing.sql'),
    (4, 'publication.sql'),
    (5, 'evidence.sql'),
    (6, 'host_runs.sql'),
    (7, 'mutations.sql'),
)


def _apply_migration(connection, version, sql, digest):
    """Commit one additive upgrade; bootstrap and FK rebuilds stay explicit."""
    connection.execute('BEGIN IMMEDIATE')
    try:
        connection.execute(sql)
        connection.execute('INSERT INTO schema_migrations VALUES(?,?,?)',
                           (version, digest, datetime.now(timezone.utc).isoformat()))
        connection.pragma('user_version', version)
        connection.execute('COMMIT')
    except BaseException:
        if not connection.get_autocommit():
            connection.execute('ROLLBACK')
        raise


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
                if version not in range(SCHEMA_VERSION + 1) or application not in (0, APPLICATION_ID):
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
                        connection.pragma("user_version", 1)
                        connection.execute("COMMIT")
                    except BaseException:
                        if not connection.get_autocommit():
                            connection.execute("ROLLBACK")
                        raise
                if connection.execute("SELECT sha256 FROM schema_migrations WHERE version=1").fetchone() != (digest,):
                    raise failure("schema migration fingerprint mismatch")
                # Authenticate the old store before any upgrade; v1 identity
                # and bytes are never replaced by the new schema resource.
                if (connection.pragma('application_id') != APPLICATION_ID or
                        connection.execute('SELECT store_id FROM store_identity WHERE singleton=1').fetchone() != (str(directory.store_id),)):
                    raise failure('catalog identity changed before migration')
                for target, resource in _ADDITIVE_MIGRATIONS:
                    migration = files(__package__).joinpath(resource).read_text(encoding='utf-8')
                    migration_hash = hashlib.sha256(migration.encode()).hexdigest()
                    if connection.pragma('user_version') == target - 1:
                        _apply_migration(connection, target, migration, migration_hash)
                    if connection.execute('SELECT sha256 FROM schema_migrations WHERE version=?',
                                          (target,)).fetchone() != (migration_hash,):
                        raise failure('schema migration fingerprint mismatch')
                recovery = files(__package__).joinpath('recovery.sql').read_text(encoding='utf-8')
                recovery_hash = hashlib.sha256(recovery.encode()).hexdigest()
                if connection.pragma('user_version') == 7:
                    # SQLite's table-rebuild protocol: disable only this
                    # connection's FK enforcement before BEGIN. Inbound FKs
                    # keep the unchanged index_artifacts table name. Validate
                    # the complete graph before committing the atomic upgrade.
                    connection.pragma('foreign_keys', False)
                    try:
                        connection.execute('BEGIN IMMEDIATE')
                        connection.execute(recovery)
                        if connection.execute('PRAGMA foreign_key_check').fetchone():
                            raise failure('recovery migration foreign key check failed')
                        connection.execute('INSERT INTO schema_migrations VALUES(?,?,?)',
                                           (8, recovery_hash, datetime.now(timezone.utc).isoformat()))
                        connection.pragma('user_version', 8)
                        connection.execute('COMMIT')
                    except BaseException:
                        if not connection.get_autocommit():
                            connection.execute('ROLLBACK')
                        raise
                    finally:
                        connection.pragma('foreign_keys', True)
                if connection.execute('SELECT sha256 FROM schema_migrations WHERE version=8').fetchone() != (recovery_hash,):
                    raise failure('schema migration fingerprint mismatch')
                self._verify(connection)
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
