"""SQLite connection management.

One module owns PRAGMA choices so they cannot drift between the CLI, the
future UI worker threads and the tests.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MEMORY = ":memory:"


def connect(database_path: Path | str) -> sqlite3.Connection:
    """Open a connection with Crate's standard pragmas applied."""
    target = str(database_path)
    if target != MEMORY:
        Path(target).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        target,
        # We manage transactions explicitly via `transaction()`; the default
        # implicit-BEGIN behaviour makes batched writes hard to reason about.
        isolation_level=None,
        # The Phase 2 UI will hand a connection to a worker thread. Python's
        # default check would reject that; SQLite itself is serialized-safe.
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if target != MEMORY:
        # WAL lets a scan write while the browser reads. Worth it even now.
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside one transaction, rolling back on any exception."""
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def open_database(database_path: Path | str) -> sqlite3.Connection:
    """Connect and bring the schema up to date. The normal entry point."""
    from crate.persistence.migrations import apply_migrations

    conn = connect(database_path)
    apply_migrations(conn)
    return conn
