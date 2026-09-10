"""Persistence layer: SQLite schema, migrations and repositories."""

from crate.persistence.database import connect, open_database, transaction
from crate.persistence.migrations import SCHEMA_VERSION, apply_migrations, current_version
from crate.persistence.sample_repository import IndexedFile, LibraryStats, SampleRepository

__all__ = [
    "SCHEMA_VERSION",
    "IndexedFile",
    "LibraryStats",
    "SampleRepository",
    "apply_migrations",
    "connect",
    "current_version",
    "open_database",
    "transaction",
]
