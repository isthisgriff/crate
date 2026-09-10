"""Incremental library scanning.

The scanner is the bridge between :mod:`crate.library.discovery` (filesystem)
and :mod:`crate.persistence` (SQLite). It owns exactly one decision:

    given what is on disk and what is in the database, what changed?

It is deliberately synchronous and single-threaded. Phase 2 will run it on a
worker thread and drive the UI from the ``progress`` callback; that requires no
change here, which is the point of keeping it free of UI and async concerns.

Change detection
----------------
A file is considered *unchanged* when its path, size and modification time in
nanoseconds all match the stored record. This is the cheap fingerprint: it
needs no file reads, so a rescan of an unchanged 100k-file library is a
directory walk and nothing else.

It is not a content hash, and it can be fooled (a file rewritten with identical
size within the filesystem's timestamp resolution). Content hashing is added in
Phase 3 for the files that actually need it -- the ones queued for analysis --
rather than for every file at scan time.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from crate.config import Settings
from crate.domain import (
    LibraryRoot,
    SampleStatus,
    ScanErrorRecord,
    ScanProgress,
    ScanReport,
)
from crate.library.discovery import DiscoveredFile, DiscoveryError, walk_audio_files
from crate.persistence.sample_repository import IndexedFile, SampleRepository

ProgressCallback = Callable[[ScanProgress], None]
CancelCheck = Callable[[], bool]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Batch:
    """Pending writes, flushed together to bound memory and transaction size."""

    inserts: list[tuple[object, ...]] = field(default_factory=list)
    updates: list[tuple[object, ...]] = field(default_factory=list)
    touches: list[int] = field(default_factory=list)
    errors: list[ScanErrorRecord] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.inserts) + len(self.updates) + len(self.touches) + len(self.errors)

    def clear(self) -> None:
        self.inserts.clear()
        self.updates.clear()
        self.touches.clear()
        self.errors.clear()


class LibraryScanner:
    """Brings the inventory for a root directory up to date."""

    def __init__(
        self,
        repository: SampleRepository,
        settings: Settings,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._repo = repository
        self._settings = settings
        self._clock = clock

    def scan(
        self,
        root_path: Path | str,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
        progress_every: int = 200,
    ) -> ScanReport:
        """Scan ``root_path`` and reconcile the database with what is on disk.

        Args:
            root_path: Directory to index. Resolved to an absolute path so the
                same folder reached two ways produces one root, not two.
            progress: Called roughly every ``progress_every`` files with a
                :class:`ScanProgress` snapshot. Must be cheap and must not
                block; it runs inside the scan loop.
            cancel: Polled at batch boundaries. Returning True stops the scan
                cleanly, keeping everything written so far.
            progress_every: Files between progress callbacks.

        Returns:
            A :class:`ScanReport`. Errors are recorded against the scan run in
            the database rather than raised.
        """
        root_path = Path(root_path).expanduser()
        # Unresolvable paths (a dead network share) still get a scan run, so the
        # failure shows up in the report instead of vanishing.
        with contextlib.suppress(OSError):
            root_path = root_path.resolve(strict=False)

        started_at = self._clock()
        root = self._repo.get_or_create_root(root_path, started_at)
        run_id = self._repo.start_scan_run(root.id, started_at)

        known: dict[str, IndexedFile] = self._repo.load_index(root.id)
        seen_ids: set[int] = set()
        unreadable_dirs: list[str] = []

        counters = dict(files_seen=0, added=0, updated=0, unchanged=0, errors=0)
        batch = _Batch()
        recorded_errors = 0
        last_progress_at = 0
        cancelled = False

        def emit(current: Path | None) -> None:
            if progress is not None:
                progress(
                    ScanProgress(
                        files_seen=counters["files_seen"],
                        added=counters["added"],
                        updated=counters["updated"],
                        unchanged=counters["unchanged"],
                        errors=counters["errors"],
                        current_path=current,
                    )
                )

        def flush() -> None:
            nonlocal recorded_errors
            now = self._clock()
            self._repo.insert_samples(batch.inserts)
            self._repo.update_samples(batch.updates)
            self._repo.touch_samples(batch.touches, now)
            if batch.errors:
                room = self._settings.max_scan_errors_recorded - recorded_errors
                if room > 0:
                    keep = batch.errors[:room]
                    self._repo.record_scan_errors(run_id, keep, now)
                    recorded_errors += len(keep)
            batch.clear()

        events = walk_audio_files(
            root_path,
            supported_extensions=self._settings.audio_extensions,
            ignored_dir_names=self._settings.ignored_dir_names,
            follow_symlinks=self._settings.follow_symlinks,
        )

        for event in events:
            if isinstance(event, DiscoveryError):
                counters["errors"] += 1
                if event.is_directory:
                    unreadable_dirs.append(str(event.path))
                batch.errors.append(
                    ScanErrorRecord(
                        path=event.path,
                        error_type=event.error_type,
                        message=event.message,
                    )
                )
            else:
                counters["files_seen"] += 1
                self._classify(event, root.id, known, seen_ids, batch, counters)

            if len(batch) >= self._settings.scan_batch_size:
                flush()
                if cancel is not None and cancel():
                    cancelled = True
                    break

            if counters["files_seen"] - last_progress_at >= progress_every:
                last_progress_at = counters["files_seen"]
                emit(event.path)

        flush()

        marked_missing = 0
        if not cancelled:
            missing_ids = self._missing_ids(known, seen_ids, unreadable_dirs)
            marked_missing = self._repo.mark_missing(missing_ids, self._clock())

        finished_at = self._clock()
        self._repo.finish_scan_run(
            run_id,
            finished_at=finished_at,
            status="cancelled" if cancelled else "completed",
            files_seen=counters["files_seen"],
            added=counters["added"],
            updated=counters["updated"],
            unchanged=counters["unchanged"],
            marked_missing=marked_missing,
            errors=counters["errors"],
        )
        if not cancelled:
            self._repo.mark_root_scanned(root.id, finished_at)

        emit(None)
        return ScanReport(
            run_id=run_id,
            root=self._refresh_root(root),
            started_at=started_at,
            finished_at=finished_at,
            files_seen=counters["files_seen"],
            added=counters["added"],
            updated=counters["updated"],
            unchanged=counters["unchanged"],
            marked_missing=marked_missing,
            errors=counters["errors"],
            cancelled=cancelled,
        )

    # ------------------------------------------------------------- internals

    def _classify(
        self,
        found: DiscoveredFile,
        root_id: int,
        known: dict[str, IndexedFile],
        seen_ids: set[int],
        batch: _Batch,
        counters: dict[str, int],
    ) -> None:
        """Decide whether a discovered file is new, changed or unchanged."""
        key = str(found.path)
        existing = known.get(key)
        now = self._clock()

        if existing is None:
            batch.inserts.append(
                (
                    root_id,
                    key,
                    found.filename,
                    found.extension,
                    found.size_bytes,
                    found.mtime_ns,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                )
            )
            counters["added"] += 1
            return

        seen_ids.add(existing.id)

        changed = (
            existing.size_bytes != found.size_bytes
            or existing.mtime_ns != found.mtime_ns
            or existing.status is not SampleStatus.PRESENT
        )
        if changed:
            batch.updates.append(
                (
                    found.size_bytes,
                    found.mtime_ns,
                    now.isoformat(),
                    now.isoformat(),
                    existing.id,
                )
            )
            counters["updated"] += 1
        else:
            batch.touches.append(existing.id)
            counters["unchanged"] += 1

    @staticmethod
    def _missing_ids(
        known: dict[str, IndexedFile],
        seen_ids: set[int],
        unreadable_dirs: list[str],
    ) -> list[int]:
        """Rows whose files were not found *and* whose absence we can trust.

        Anything under a directory that failed to list was never looked at, so
        it is unknown, not missing. Reporting it as deleted would be a lie the
        user pays for later when their metadata is orphaned.
        """
        prefixes = tuple(_as_prefix(directory) for directory in unreadable_dirs)
        missing: list[int] = []
        for path, record in known.items():
            if record.id in seen_ids:
                continue
            if prefixes and path.startswith(prefixes):
                continue
            if record.status is SampleStatus.MISSING:
                continue
            missing.append(record.id)
        return missing

    def _refresh_root(self, root: LibraryRoot) -> LibraryRoot:
        for candidate in self._repo.list_roots():
            if candidate.id == root.id:
                return candidate
        return root


def _as_prefix(directory: str) -> str:
    """Normalize a directory path into a `startswith` prefix.

    Trailing separator included so that ``/library/kicks`` does not swallow
    ``/library/kicks_old``.
    """
    return directory.rstrip("/\\") + os.sep
