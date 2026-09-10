"""Forward-only schema migrations.

Rules:
  * Migrations are append-only. Never edit a shipped migration; add a new one.
  * Each migration is a version number, a name and a SQL script.
  * The applied version lives in ``schema_migrations`` so an old database on a
    user's machine can be upgraded without a rescan.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from crate.persistence.database import transaction


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


_MIGRATION_0001 = """
CREATE TABLE roots (
    id              INTEGER PRIMARY KEY,
    path            TEXT    NOT NULL UNIQUE,
    added_at        TEXT    NOT NULL,
    last_scanned_at TEXT
);

-- The Phase 1 inventory. Identity only: everything a filesystem can tell us
-- without opening the file. Audio/musical/AI attributes get their own tables
-- in later phases so re-analysis never rewrites this one.
CREATE TABLE samples (
    id            INTEGER PRIMARY KEY,
    root_id       INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    path          TEXT    NOT NULL UNIQUE,
    filename      TEXT    NOT NULL,
    extension     TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL,
    mtime_ns      INTEGER NOT NULL,
    content_hash  TEXT,
    status        TEXT    NOT NULL DEFAULT 'present',
    first_seen_at TEXT    NOT NULL,
    last_seen_at  TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE INDEX idx_samples_root      ON samples(root_id);
CREATE INDEX idx_samples_extension ON samples(extension);
CREATE INDEX idx_samples_status    ON samples(status);
CREATE INDEX idx_samples_filename  ON samples(filename);
-- Supports the move-detection query planned for Phase 2: a "new" file whose
-- (size, mtime) matches a row currently marked missing is almost certainly
-- the same file at a new path.
CREATE INDEX idx_samples_fingerprint ON samples(size_bytes, mtime_ns);

CREATE TABLE scan_runs (
    id             INTEGER PRIMARY KEY,
    root_id        INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    status         TEXT    NOT NULL,   -- running | completed | cancelled | failed
    files_seen     INTEGER NOT NULL DEFAULT 0,
    added          INTEGER NOT NULL DEFAULT 0,
    updated        INTEGER NOT NULL DEFAULT 0,
    unchanged      INTEGER NOT NULL DEFAULT 0,
    marked_missing INTEGER NOT NULL DEFAULT 0,
    errors         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_scan_runs_root ON scan_runs(root_id, started_at DESC);

-- A messy library is the normal case. Failures are data, not exceptions:
-- they are attached to the run that saw them so the user can inspect them.
CREATE TABLE scan_errors (
    id          INTEGER PRIMARY KEY,
    scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    error_type  TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    occurred_at TEXT    NOT NULL
);

CREATE INDEX idx_scan_errors_run ON scan_errors(scan_run_id);
"""

_MIGRATION_0002 = """
-- User organization. Kept on `samples` rather than in a side table because it
-- is per-file, tiny, and needs to be sortable and filterable in the same query
-- that drives the browser list. Scans never write these columns.
ALTER TABLE samples ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0;
ALTER TABLE samples ADD COLUMN favorited_at TEXT;

-- Partial index: the browser filters on favorite = 1, and in a real library
-- that is a few hundred rows out of tens of thousands.
CREATE INDEX idx_samples_favorite ON samples(favorite) WHERE favorite = 1;

-- Sort keys for the browser. Without these, ordering 35k rows by size or
-- recency is a full scan on every keystroke.
CREATE INDEX idx_samples_size  ON samples(size_bytes);
CREATE INDEX idx_samples_mtime ON samples(mtime_ns);
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial_inventory", _MIGRATION_0001),
    Migration(2, "user_organization", _MIGRATION_0002),
)

SCHEMA_VERSION = MIGRATIONS[-1].version


def current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version (0 for a fresh database)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    return int(row["version"] or 0)


def split_statements(script: str) -> list[str]:
    """Split a migration script into individual statements.

    ``Connection.executescript`` commits any open transaction before running,
    which would break migration atomicity, so we execute statements ourselves
    inside one transaction. This splitter only has to handle *our* DDL: no
    semicolons inside string literals, no triggers. Anything fancier belongs
    in its own migration written as explicit statements.
    """
    without_comments = "\n".join(
        line for line in script.splitlines() if not line.strip().startswith("--")
    )
    return [statement.strip() for statement in without_comments.split(";") if statement.strip()]


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply every pending migration. Returns the resulting schema version."""
    version = current_version(conn)
    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        with transaction(conn):
            for statement in split_statements(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) "
                "VALUES (?, ?, datetime('now'))",
                (migration.version, migration.name),
            )
        version = migration.version
    return version
