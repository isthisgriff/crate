"""Application composition root.

The one place that wires settings, database and repositories together. The CLI
uses it; the Phase 2 UI will use the same object, which is why it exposes
services rather than being a service itself.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from crate.config import Settings, load_settings
from crate.library.scanner import LibraryScanner
from crate.persistence.database import open_database
from crate.persistence.sample_repository import SampleRepository


@dataclass
class CrateApp:
    """Owns the database connection and hands out configured services."""

    settings: Settings
    connection: sqlite3.Connection

    @classmethod
    def open(cls, data_dir: Path | None = None) -> CrateApp:
        settings = load_settings(data_dir)
        connection = open_database(settings.database_path)
        return cls(settings=settings, connection=connection)

    @property
    def samples(self) -> SampleRepository:
        return SampleRepository(self.connection)

    def scanner(self) -> LibraryScanner:
        return LibraryScanner(self.samples, self.settings)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> CrateApp:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
