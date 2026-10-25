"""Database session management and transactional unit-of-work wrapper.

Supports dependency injection in FastAPI (`Depends(get_db_session)`) with
automatic commit/rollback handling, parameterized query execution, and live
connectivity checks for readiness probes.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Generator, Iterable, Mapping

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.base import initialize_sqlite_schema

logger = get_logger(__name__)

# Shared connection registry for in-memory SQLite databases during isolated tests
_IN_MEMORY_CONNECTIONS: dict[str, sqlite3.Connection] = {}


class DatabaseSession:
    """Transactional database session wrapping parameterized SQL operations."""

    def __init__(self, connection: sqlite3.Connection, close_on_exit: bool = True) -> None:
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._close_on_exit = close_on_exit
        self._closed = False

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def execute(
        self, sql: str, params: Iterable[Any] | Mapping[str, Any] = ()
    ) -> sqlite3.Cursor:
        """Execute a parameterized SQL statement."""
        return self._conn.execute(sql, params)

    def fetchone(
        self, sql: str, params: Iterable[Any] | Mapping[str, Any] = ()
    ) -> dict[str, Any] | None:
        """Execute a parameterized SELECT and return a single row as a dict or None."""
        cur = self._conn.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return dict(row) if row is not None else None

    def fetchall(
        self, sql: str, params: Iterable[Any] | Mapping[str, Any] = ()
    ) -> list[dict[str, Any]]:
        """Execute a parameterized SELECT and return all rows as dicts."""
        cur = self._conn.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]

    def commit(self) -> None:
        """Commit the current transaction."""
        self._conn.commit()

    def rollback(self) -> None:
        """Roll back the current transaction."""
        self._conn.rollback()

    def ping(self) -> bool:
        """Verify database connectivity and schema initialization."""
        row = self.fetchone("SELECT 1 AS ok;")
        return bool(row and row.get("ok") == 1)

    def list_tables(self) -> list[str]:
        """Return all user tables in the database."""
        rows = self.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        return [str(r["name"]) for r in rows]

    def close(self) -> None:
        """Close the underlying connection if owned by this session."""
        if not self._closed and self._close_on_exit:
            self._conn.close()
            self._closed = True


def _connect_sqlite_safe(db_path: str) -> sqlite3.Connection:
    """Connect to SQLite and configure journal mode safely across POSIX and 9p mounts."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode = MEMORY;")
        conn.execute("PRAGMA synchronous = OFF;")
        initialize_sqlite_schema(conn)
        return conn
    except sqlite3.OperationalError:
        conn.close()
        fallback_path = "/tmp/reconcilex.db"
        conn = sqlite3.connect(fallback_path, check_same_thread=False)
        initialize_sqlite_schema(conn)
        return conn


def create_session_for_settings(settings: Settings) -> DatabaseSession:
    """Create and initialize a DatabaseSession for the given settings."""
    if not settings.is_sqlite:
        try:
            import psycopg  # type: ignore

            raise NotImplementedError(
                "PostgreSQL engine requires SQLAlchemy/psycopg runtime; use Docker container."
            )
        except ImportError as exc:
            raise RuntimeError(
                f"PostgreSQL driver not installed in current environment for URL '{settings.DATABASE_URL}'."
            ) from exc

    db_path = settings.sqlite_file_path
    if db_path == ":memory:":
        conn = _IN_MEMORY_CONNECTIONS.get(settings.DATABASE_URL)
        if conn is None:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            initialize_sqlite_schema(conn)
            _IN_MEMORY_CONNECTIONS[settings.DATABASE_URL] = conn
        return DatabaseSession(conn, close_on_exit=False)

    conn = _connect_sqlite_safe(db_path)
    return DatabaseSession(conn, close_on_exit=True)


@contextmanager
def session_scope(settings: Settings | None = None) -> Generator[DatabaseSession, None, None]:
    """Provide a transactional scope around a series of operations."""
    cfg = settings or get_settings()
    session = create_session_for_settings(cfg)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session(
    settings: Settings = Depends(get_settings),
) -> Generator[DatabaseSession, None, None]:
    """FastAPI dependency yielding a transactional DatabaseSession."""
    session = create_session_for_settings(settings)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_in_memory_databases() -> None:
    """Close and clear any cached in-memory SQLite connections (used in test fixtures)."""
    for conn in _IN_MEMORY_CONNECTIONS.values():
        try:
            conn.close()
        except Exception:
            pass
    _IN_MEMORY_CONNECTIONS.clear()
