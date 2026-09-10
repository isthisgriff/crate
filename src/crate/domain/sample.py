"""Core domain entities.

Phase 1 covers only the *identity* branch of the Sample model described in the
design doc. Audio properties, musical properties, analysis features, AI
metadata and user organization are added in later phases as separate tables
so that re-analysis never rewrites the inventory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class SampleStatus(StrEnum):
    """Whether the file backing a sample record was present at the last scan."""

    PRESENT = "present"
    MISSING = "missing"


class ScanOutcome(StrEnum):
    """What a scan decided about a single discovered file."""

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class LibraryRoot:
    """A directory the user asked Crate to index."""

    id: int
    path: Path
    added_at: datetime
    last_scanned_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Sample:
    """One audio file in the library inventory."""

    id: int
    root_id: int
    path: Path
    filename: str
    extension: str
    size_bytes: int
    mtime_ns: int
    status: SampleStatus
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime
    content_hash: str | None = None

    @property
    def stem(self) -> str:
        return Path(self.filename).stem


@dataclass(frozen=True, slots=True)
class ScanErrorRecord:
    """A per-file or per-directory failure that did not abort the scan."""

    path: Path
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class ScanProgress:
    """Snapshot handed to progress callbacks while a scan is running.

    Deliberately a value object with no Qt/UI types: the Phase 2 worker thread
    will marshal these across to the UI thread unchanged.
    """

    files_seen: int
    added: int
    updated: int
    unchanged: int
    errors: int
    current_path: Path | None = None

    @property
    def indexed(self) -> int:
        return self.added + self.updated + self.unchanged


@dataclass(frozen=True, slots=True)
class ScanRunSummary:
    """A persisted scan run, as read back from the database."""

    id: int
    root_id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    files_seen: int
    added: int
    updated: int
    unchanged: int
    marked_missing: int
    errors: int


@dataclass(frozen=True, slots=True)
class ScanReport:
    """Result of one completed (or cancelled) scan of one root."""

    run_id: int
    root: LibraryRoot
    started_at: datetime
    finished_at: datetime
    files_seen: int
    added: int
    updated: int
    unchanged: int
    marked_missing: int
    errors: int
    cancelled: bool = False

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()
