from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crate.domain import SampleStatus, ScanErrorRecord
from crate.persistence.database import connect, open_database, transaction
from crate.persistence.migrations import (
    MIGRATIONS,
    SCHEMA_VERSION,
    apply_migrations,
    current_version,
    split_statements,
)
from crate.persistence.sample_repository import SampleRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MUSIC = Path("/music")
KICK = MUSIC / "kick.wav"


def _key(path: Path) -> str:
    """Paths are stored exactly as `str(Path)` renders them on this platform."""
    return str(path)


def test_fresh_database_is_migrated_to_head(tmp_path: Path) -> None:
    conn = open_database(tmp_path / "crate.db")
    assert current_version(conn) == SCHEMA_VERSION
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"roots", "samples", "scan_runs", "scan_errors"} <= tables
    conn.close()


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "crate.db"
    open_database(path).close()
    conn = open_database(path)
    assert current_version(conn) == SCHEMA_VERSION
    applied = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert applied == len(MIGRATIONS)
    conn.close()


def test_migration_versions_are_unique_and_ordered() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_split_statements_drops_comments() -> None:
    statements = split_statements("-- a comment\nCREATE TABLE a(x);\nCREATE TABLE b(y);\n")
    assert statements == ["CREATE TABLE a(x)", "CREATE TABLE b(y)"]


def test_migration_is_atomic_when_a_statement_fails(tmp_path: Path) -> None:
    conn = connect(tmp_path / "crate.db")
    apply_migrations(conn)
    # A migration that half-succeeds must leave nothing behind.
    from crate.persistence.migrations import Migration

    broken = Migration(999, "broken", "CREATE TABLE good(x); CREATE TABLE bad(;")
    with pytest.raises(sqlite3.OperationalError), transaction(conn):
        for statement in split_statements(broken.sql):
            conn.execute(statement)
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "good" not in tables
    conn.close()


def test_get_or_create_root_is_stable(repository: SampleRepository) -> None:
    first = repository.get_or_create_root(MUSIC / "samples", NOW)
    second = repository.get_or_create_root(MUSIC / "samples", NOW)
    assert first.id == second.id
    assert len(repository.list_roots()) == 1


def test_insert_and_read_back(repository: SampleRepository) -> None:
    root = repository.get_or_create_root(MUSIC, NOW)
    stamp = NOW.isoformat()
    repository.insert_samples(
        [(root.id, _key(KICK), "kick.wav", ".wav", 1024, 111, stamp, stamp, stamp)]
    )
    sample = repository.get_sample_by_path(KICK)
    assert sample is not None
    assert sample.filename == "kick.wav"
    assert sample.extension == ".wav"
    assert sample.size_bytes == 1024
    assert sample.status is SampleStatus.PRESENT
    assert sample.stem == "kick"


def test_insert_is_upsert_on_path(repository: SampleRepository) -> None:
    root = repository.get_or_create_root(MUSIC, NOW)
    stamp = NOW.isoformat()
    record = (root.id, _key(KICK), "kick.wav", ".wav", 10, 1, stamp, stamp, stamp)
    repository.insert_samples([record])
    repository.insert_samples([(*record[:4], 99, 2, stamp, stamp, stamp)])
    assert repository.count_samples() == 1
    sample = repository.get_sample_by_path(KICK)
    assert sample is not None
    assert sample.size_bytes == 99


def test_mark_missing_does_not_delete(repository: SampleRepository) -> None:
    root = repository.get_or_create_root(MUSIC, NOW)
    stamp = NOW.isoformat()
    repository.insert_samples(
        [(root.id, _key(KICK), "kick.wav", ".wav", 10, 1, stamp, stamp, stamp)]
    )
    sample = repository.get_sample_by_path(KICK)
    assert sample is not None

    repository.mark_missing([sample.id], NOW)
    reloaded = repository.get_sample_by_path(KICK)
    assert reloaded is not None
    assert reloaded.status is SampleStatus.MISSING
    assert repository.count_samples() == 1


def test_update_clears_content_hash(repository: SampleRepository) -> None:
    root = repository.get_or_create_root(MUSIC, NOW)
    stamp = NOW.isoformat()
    repository.insert_samples(
        [(root.id, _key(KICK), "kick.wav", ".wav", 10, 1, stamp, stamp, stamp)]
    )
    sample = repository.get_sample_by_path(KICK)
    assert sample is not None
    repository._conn.execute("UPDATE samples SET content_hash = 'abc' WHERE id = ?", (sample.id,))

    repository.update_samples([(20, 2, stamp, stamp, sample.id)])
    reloaded = repository.get_sample_by_path(KICK)
    assert reloaded is not None
    assert reloaded.content_hash is None
    assert reloaded.size_bytes == 20


def test_scan_run_lifecycle_and_errors(repository: SampleRepository) -> None:
    root = repository.get_or_create_root(MUSIC, NOW)
    run_id = repository.start_scan_run(root.id, NOW)
    repository.record_scan_errors(
        run_id,
        [ScanErrorRecord(Path("/music/bad.wav"), "PermissionError", "denied")],
        NOW,
    )
    repository.finish_scan_run(
        run_id,
        finished_at=NOW,
        status="completed",
        files_seen=1,
        added=0,
        updated=0,
        unchanged=0,
        marked_missing=0,
        errors=1,
    )
    errors = repository.list_scan_errors(run_id)
    assert len(errors) == 1
    assert errors[0].error_type == "PermissionError"

    latest = repository.latest_scan_run(root.id)
    assert latest is not None
    assert latest.status == "completed"


def test_deleting_a_root_cascades(repository: SampleRepository) -> None:
    root = repository.get_or_create_root(MUSIC, NOW)
    stamp = NOW.isoformat()
    repository.insert_samples(
        [(root.id, _key(KICK), "kick.wav", ".wav", 10, 1, stamp, stamp, stamp)]
    )
    with transaction(repository._conn):
        repository._conn.execute("DELETE FROM roots WHERE id = ?", (root.id,))
    assert repository.count_samples() == 0


def test_stats(repository: SampleRepository) -> None:
    root = repository.get_or_create_root(MUSIC, NOW)
    stamp = NOW.isoformat()
    repository.insert_samples(
        [
            (root.id, _key(MUSIC / "a.wav"), "a.wav", ".wav", 100, 1, stamp, stamp, stamp),
            (root.id, _key(MUSIC / "b.wav"), "b.wav", ".wav", 200, 1, stamp, stamp, stamp),
            (root.id, _key(MUSIC / "c.mp3"), "c.mp3", ".mp3", 300, 1, stamp, stamp, stamp),
        ]
    )
    stats = repository.stats()
    assert stats.total_samples == 3
    assert stats.present_samples == 3
    assert stats.total_bytes == 600
    assert stats.by_extension == {".wav": 2, ".mp3": 1}


def test_transaction_rolls_back_on_error(connection: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), transaction(connection):
        connection.execute(
            "INSERT INTO roots (path, added_at) VALUES ('/x', '2026-01-01T00:00:00+00:00')"
        )
        raise RuntimeError("boom")
    remaining = connection.execute("SELECT COUNT(*) AS n FROM roots").fetchone()["n"]
    assert remaining == 0
