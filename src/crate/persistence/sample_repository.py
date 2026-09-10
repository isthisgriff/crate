"""Data access for the sample inventory.

Everything above this layer works with domain objects and never sees SQL.
Everything below is SQLite detail that can be swapped without touching the
scanner or (later) the UI.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from crate.domain import (
    LibraryRoot,
    Sample,
    SampleQuery,
    SampleSort,
    SampleStatus,
    ScanErrorRecord,
    ScanRunSummary,
)
from crate.persistence.database import transaction


@dataclass(frozen=True, slots=True)
class IndexedFile:
    """The minimum needed to decide whether a known file changed.

    Loaded in bulk at the start of a scan so the hot loop does one dict lookup
    per file instead of one SQL round trip.
    """

    id: int
    size_bytes: int
    mtime_ns: int
    status: SampleStatus


@dataclass(frozen=True, slots=True)
class LibraryStats:
    total_samples: int
    present_samples: int
    missing_samples: int
    total_bytes: int
    by_extension: dict[str, int]


LIKE_ESCAPE = "\\"
"""Escape character for LIKE patterns, applied to search terms by `_escape_like`."""


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _require(value: str | None) -> datetime:
    parsed = _parse(value)
    if parsed is None:
        raise ValueError("expected a timestamp, found NULL")
    return parsed


class SampleRepository:
    """Reads and writes the inventory tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ roots

    def get_or_create_root(self, path: Path, now: datetime) -> LibraryRoot:
        """Return the root for ``path``, creating it if this is the first scan."""
        key = str(path)
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR IGNORE INTO roots (path, added_at) VALUES (?, ?)",
                (key, _iso(now)),
            )
        row = self._conn.execute("SELECT * FROM roots WHERE path = ?", (key,)).fetchone()
        return self._row_to_root(row)

    def list_roots(self) -> list[LibraryRoot]:
        rows = self._conn.execute("SELECT * FROM roots ORDER BY path").fetchall()
        return [self._row_to_root(row) for row in rows]

    def mark_root_scanned(self, root_id: int, when: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE roots SET last_scanned_at = ? WHERE id = ?", (_iso(when), root_id)
            )

    @staticmethod
    def _row_to_root(row: sqlite3.Row) -> LibraryRoot:
        return LibraryRoot(
            id=int(row["id"]),
            path=Path(row["path"]),
            added_at=_require(row["added_at"]),
            last_scanned_at=_parse(row["last_scanned_at"]),
        )

    # ---------------------------------------------------------------- samples

    def load_index(self, root_id: int) -> dict[str, IndexedFile]:
        """Load the current inventory for a root, keyed by path.

        Trades memory for speed: roughly 150 bytes per file, so ~15 MB for a
        100k-file root. If that ever matters, this becomes a cursor-based
        merge join against a sorted walk -- the scanner does not need to know.
        """
        rows = self._conn.execute(
            "SELECT id, path, size_bytes, mtime_ns, status FROM samples WHERE root_id = ?",
            (root_id,),
        ).fetchall()
        return {
            str(row["path"]): IndexedFile(
                id=int(row["id"]),
                size_bytes=int(row["size_bytes"]),
                mtime_ns=int(row["mtime_ns"]),
                status=SampleStatus(row["status"]),
            )
            for row in rows
        }

    def insert_samples(self, records: Sequence[tuple[object, ...]]) -> None:
        """Bulk-insert new samples.

        Each record is ``(root_id, path, filename, extension, size_bytes,
        mtime_ns, first_seen_at, last_seen_at, updated_at)``. The upsert clause
        covers the case where a path already indexed under one root is later
        scanned as part of another (nested or overlapping roots).
        """
        if not records:
            return
        with transaction(self._conn):
            self._conn.executemany(
                """
                INSERT INTO samples (
                    root_id, path, filename, extension, size_bytes, mtime_ns,
                    status, first_seen_at, last_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'present', ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    root_id      = excluded.root_id,
                    size_bytes   = excluded.size_bytes,
                    mtime_ns     = excluded.mtime_ns,
                    status       = 'present',
                    last_seen_at = excluded.last_seen_at,
                    updated_at   = excluded.updated_at
                """,
                records,
            )

    def update_samples(self, records: Sequence[tuple[object, ...]]) -> None:
        """Bulk-update changed samples: ``(size, mtime, last_seen, updated, id)``.

        Clearing ``content_hash`` is the contract that keeps later phases
        honest: when the bytes change, every derived artefact is stale.
        """
        if not records:
            return
        with transaction(self._conn):
            self._conn.executemany(
                """
                UPDATE samples
                   SET size_bytes   = ?,
                       mtime_ns     = ?,
                       status       = 'present',
                       last_seen_at = ?,
                       updated_at   = ?,
                       content_hash = NULL
                 WHERE id = ?
                """,
                records,
            )

    def touch_samples(self, sample_ids: Sequence[int], when: datetime) -> None:
        """Record that unchanged samples were seen, without bumping updated_at."""
        if not sample_ids:
            return
        stamp = _iso(when)
        with transaction(self._conn):
            self._conn.executemany(
                "UPDATE samples SET last_seen_at = ?, status = 'present' WHERE id = ?",
                [(stamp, sample_id) for sample_id in sample_ids],
            )

    def mark_missing(self, sample_ids: Iterable[int], when: datetime) -> int:
        """Flag records whose files were not found. Never deletes.

        Deletion is the user's decision, not the scanner's: a disconnected
        external drive must not wipe an inventory.
        """
        ids = list(sample_ids)
        if not ids:
            return 0
        stamp = _iso(when)
        with transaction(self._conn):
            self._conn.executemany(
                "UPDATE samples SET status = 'missing', updated_at = ? "
                "WHERE id = ? AND status != 'missing'",
                [(stamp, sample_id) for sample_id in ids],
            )
        return len(ids)

    def delete_missing(self) -> int:
        """Permanently remove rows whose files are gone.

        The scanner never does this (see decision D3): only the user can say
        that an absent file is really gone rather than on an unplugged drive.
        """
        with transaction(self._conn):
            cursor = self._conn.execute("DELETE FROM samples WHERE status = 'missing'")
        return cursor.rowcount

    def count_samples(
        self, *, root_id: int | None = None, status: SampleStatus | None = None
    ) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if root_id is not None:
            clauses.append("root_id = ?")
            params.append(root_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._conn.execute(f"SELECT COUNT(*) AS count FROM samples{where}", params).fetchone()
        return int(row["count"])

    def get_sample_by_path(self, path: Path) -> Sample | None:
        row = self._conn.execute("SELECT * FROM samples WHERE path = ?", (str(path),)).fetchone()
        return self._row_to_sample(row) if row else None

    def iter_samples(
        self,
        *,
        root_id: int | None = None,
        status: SampleStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Sample]:
        clauses: list[str] = []
        params: list[object] = []
        if root_id is not None:
            clauses.append("root_id = ?")
            params.append(root_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM samples{where} ORDER BY path LIMIT ? OFFSET ?"
        params.extend([-1 if limit is None else limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_sample(row) for row in rows]

    @staticmethod
    def _row_to_sample(row: sqlite3.Row) -> Sample:
        return Sample(
            id=int(row["id"]),
            root_id=int(row["root_id"]),
            path=Path(row["path"]),
            filename=str(row["filename"]),
            extension=str(row["extension"]),
            size_bytes=int(row["size_bytes"]),
            mtime_ns=int(row["mtime_ns"]),
            status=SampleStatus(row["status"]),
            first_seen_at=_require(row["first_seen_at"]),
            last_seen_at=_require(row["last_seen_at"]),
            updated_at=_require(row["updated_at"]),
            content_hash=row["content_hash"],
            favorite=bool(row["favorite"]),
            favorited_at=_parse(row["favorited_at"]),
        )

    # ---------------------------------------------------------------- browse

    def search(self, query: SampleQuery, *, limit: int, offset: int = 0) -> list[Sample]:
        """Page through the samples matching ``query``.

        Always paginated: the browser must never load 35k rows to draw 40.
        """
        where, params = _build_filter(query)
        rows = self._conn.execute(
            f"SELECT * FROM samples WHERE {where} ORDER BY {_order_by(query)} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [self._row_to_sample(row) for row in rows]

    def count_matching(self, query: SampleQuery) -> int:
        where, params = _build_filter(query)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS count FROM samples WHERE {where}", params
        ).fetchone()
        return int(row["count"])

    def get_sample(self, sample_id: int) -> Sample | None:
        row = self._conn.execute("SELECT * FROM samples WHERE id = ?", (sample_id,)).fetchone()
        return self._row_to_sample(row) if row else None

    def set_favorite(self, sample_id: int, favorite: bool, when: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE samples SET favorite = ?, favorited_at = ? WHERE id = ?",
                (1 if favorite else 0, _iso(when) if favorite else None, sample_id),
            )

    def extensions_in_use(self) -> list[str]:
        return [
            str(row["extension"])
            for row in self._conn.execute(
                "SELECT extension, COUNT(*) AS n FROM samples GROUP BY extension ORDER BY n DESC"
            )
        ]

    # ----------------------------------------------------------------- moves

    def rows_added_since(self, root_id: int, since: datetime) -> list[tuple[int, str, int, int]]:
        """Rows this scan inserted: ``(id, path, size_bytes, mtime_ns)``.

        Identified by ``first_seen_at``, which only an insert ever sets. Avoids
        having to buffer every new path in memory during the walk.
        """
        return [
            (int(row["id"]), str(row["path"]), int(row["size_bytes"]), int(row["mtime_ns"]))
            for row in self._conn.execute(
                "SELECT id, path, size_bytes, mtime_ns FROM samples "
                "WHERE root_id = ? AND first_seen_at >= ?",
                (root_id, _iso(since)),
            )
        ]

    def apply_moves(self, moves: Sequence[tuple[int, int]], when: datetime) -> int:
        """Re-point old rows at new paths, discarding the freshly inserted rows.

        Each move is ``(old_id, new_id)``. The *old* row survives, so its id,
        favourites and (in later phases) analysis and embeddings survive with
        it. The new row is deleted first to free the unique path.
        """
        if not moves:
            return 0
        stamp = _iso(when)
        with transaction(self._conn):
            for old_id, new_id in moves:
                row = self._conn.execute(
                    "SELECT path, filename, root_id FROM samples WHERE id = ?", (new_id,)
                ).fetchone()
                if row is None:
                    continue
                self._conn.execute("DELETE FROM samples WHERE id = ?", (new_id,))
                self._conn.execute(
                    "UPDATE samples SET path = ?, filename = ?, root_id = ?, "
                    "status = 'present', last_seen_at = ?, updated_at = ? WHERE id = ?",
                    (row["path"], row["filename"], row["root_id"], stamp, stamp, old_id),
                )
        return len(moves)

    # ------------------------------------------------------------- scan runs

    def start_scan_run(self, root_id: int, started_at: datetime) -> int:
        with transaction(self._conn):
            cursor = self._conn.execute(
                "INSERT INTO scan_runs (root_id, started_at, status) VALUES (?, ?, 'running')",
                (root_id, _iso(started_at)),
            )
        run_id = cursor.lastrowid
        if run_id is None:
            raise RuntimeError("failed to create scan run")
        return int(run_id)

    def finish_scan_run(
        self,
        run_id: int,
        *,
        finished_at: datetime,
        status: str,
        files_seen: int,
        added: int,
        updated: int,
        unchanged: int,
        marked_missing: int,
        errors: int,
    ) -> None:
        with transaction(self._conn):
            self._conn.execute(
                """
                UPDATE scan_runs
                   SET finished_at = ?, status = ?, files_seen = ?, added = ?,
                       updated = ?, unchanged = ?, marked_missing = ?, errors = ?
                 WHERE id = ?
                """,
                (
                    _iso(finished_at),
                    status,
                    files_seen,
                    added,
                    updated,
                    unchanged,
                    marked_missing,
                    errors,
                    run_id,
                ),
            )

    def latest_scan_run(self, root_id: int) -> ScanRunSummary | None:
        row = self._conn.execute(
            "SELECT * FROM scan_runs WHERE root_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
            (root_id,),
        ).fetchone()
        if row is None:
            return None
        return ScanRunSummary(
            id=int(row["id"]),
            root_id=int(row["root_id"]),
            started_at=_require(row["started_at"]),
            finished_at=_parse(row["finished_at"]),
            status=str(row["status"]),
            files_seen=int(row["files_seen"]),
            added=int(row["added"]),
            updated=int(row["updated"]),
            unchanged=int(row["unchanged"]),
            marked_missing=int(row["marked_missing"]),
            errors=int(row["errors"]),
        )

    def record_scan_errors(
        self, run_id: int, errors: Sequence[ScanErrorRecord], when: datetime
    ) -> None:
        if not errors:
            return
        stamp = _iso(when)
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT INTO scan_errors (scan_run_id, path, error_type, message, occurred_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (run_id, str(error.path), error.error_type, error.message, stamp)
                    for error in errors
                ],
            )

    def list_scan_errors(self, run_id: int, limit: int = 50) -> list[ScanErrorRecord]:
        rows = self._conn.execute(
            "SELECT path, error_type, message FROM scan_errors WHERE scan_run_id = ? "
            "ORDER BY id LIMIT ?",
            (run_id, limit),
        ).fetchall()
        return [
            ScanErrorRecord(
                path=Path(row["path"]),
                error_type=str(row["error_type"]),
                message=str(row["message"]),
            )
            for row in rows
        ]

    # ----------------------------------------------------------------- stats

    def stats(self) -> LibraryStats:
        totals = self._conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(status = 'present'), 0) AS present,
                   COALESCE(SUM(status = 'missing'), 0) AS missing,
                   COALESCE(SUM(size_bytes), 0)         AS bytes
              FROM samples
            """
        ).fetchone()
        by_extension = {
            str(row["extension"]): int(row["count"])
            for row in self._conn.execute(
                "SELECT extension, COUNT(*) AS count FROM samples "
                "GROUP BY extension ORDER BY count DESC"
            ).fetchall()
        }
        return LibraryStats(
            total_samples=int(totals["total"]),
            present_samples=int(totals["present"]),
            missing_samples=int(totals["missing"]),
            total_bytes=int(totals["bytes"]),
            by_extension=by_extension,
        )


_NUMERIC_SORTS = frozenset({SampleSort.SIZE, SampleSort.MODIFIED})


def _order_by(query: SampleQuery) -> str:
    """Build the ORDER BY clause for a browser query.

    Two details that are easy to get wrong and obvious once broken:

    * Text columns sort case-insensitively, or `Kick.wav` lands nowhere near
      `kick.wav` — which is exactly how sample libraries are named.
    * There is always a unique tiebreaker. Thousands of samples share a size,
      and every one-shot in a pack shares an mtime; without it, LIMIT/OFFSET
      paging can show a row twice and skip another.
    """
    direction = "DESC" if query.descending else "ASC"
    column = query.sort.value
    key = (
        f"{column} {direction}"
        if query.sort in _NUMERIC_SORTS
        else (f"{column} COLLATE NOCASE {direction}")
    )
    return f"{key}, filename COLLATE NOCASE ASC, id ASC"


def _build_filter(query: SampleQuery) -> tuple[str, list[object]]:
    """Translate a :class:`SampleQuery` into a WHERE clause and parameters.

    Text matching is `LIKE %term%` against the full path, so folder names count
    -- producers rely on them at least as much as on filenames. Every term must
    match, which makes "808 dark" behave the way a search box should.

    This cannot use an index, and does not need to: a leading-wildcard LIKE
    over 35k rows measures in single-digit milliseconds. FTS5 is the answer
    when a library is large enough to make that false, and it is a change
    confined to this function plus a migration.
    """
    clauses: list[str] = []
    params: list[object] = []

    if not query.include_missing:
        clauses.append("status = ?")
        params.append(SampleStatus.PRESENT.value)
    if query.favorites_only:
        clauses.append("favorite = 1")
    if query.root_id is not None:
        clauses.append("root_id = ?")
        params.append(query.root_id)
    if query.extensions:
        placeholders = ", ".join("?" for _ in query.extensions)
        clauses.append(f"extension IN ({placeholders})")
        params.extend(sorted(query.extensions))
    for term in query.terms:
        clauses.append(f"path LIKE ? ESCAPE '{LIKE_ESCAPE}'")
        params.append(f"%{_escape_like(term)}%")

    return (" AND ".join(clauses) if clauses else "1 = 1"), params


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so a literal `%` or `_` in a query stays literal.

    `_` matters in practice: sample filenames are full of it, and without this
    a search for `808_new` would also match `808-new`.
    """
    for char in (LIKE_ESCAPE, "%", "_"):
        term = term.replace(char, LIKE_ESCAPE + char)
    return term
