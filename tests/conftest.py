from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crate.config import Settings
from crate.library.scanner import LibraryScanner
from crate.persistence.database import open_database
from crate.persistence.sample_repository import SampleRepository

# Qt tests run without a display. Set before PySide6 is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeClock:
    """Deterministic clock so timestamp assertions are not flaky."""

    def __init__(self, start: datetime | None = None, step_seconds: float = 1.0) -> None:
        self.now = start or datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        self._step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        current = self.now
        self.now += self._step
        return current


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = open_database(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture
def repository(connection: sqlite3.Connection) -> SampleRepository:
    return SampleRepository(connection)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # Small batch size so batching/flush paths are exercised by tiny fixtures.
    return Settings(data_dir=tmp_path / "data", scan_batch_size=3)


@pytest.fixture
def scanner(repository: SampleRepository, settings: Settings, clock: FakeClock) -> LibraryScanner:
    return LibraryScanner(repository, settings, clock=clock)


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A small, deliberately messy sample library."""
    root = tmp_path / "library"
    layout = {
        "drums/kicks/Kick_Final_2.wav": b"kick",
        "drums/kicks/DRM_094.WAV": b"kick2",
        "drums/snares/snare one.aiff": b"snare",
        "bass/808_new.flac": b"eight-oh-eight",
        "vocals/Audio_338.mp3": b"vox",
        "notes.txt": b"not audio",
        "artwork/cover.png": b"not audio",
        "packs/.git/objects/blob.wav": b"should be ignored",
        "unicode/kick_éü你好.wav": b"unicode name",
        "empty/silence.wav": b"",
    }
    for relative, payload in layout.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return root


AUDIO_FILE_COUNT = 7
"""Supported audio files in the `library` fixture, excluding the .git one."""
