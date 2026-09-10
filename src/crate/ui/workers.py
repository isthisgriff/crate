"""Background workers.

Two rules keep the UI responsive and the database sane:

* Nothing that touches the disk runs on the UI thread. Scanning walks tens of
  thousands of files; decoding a long file takes tens of milliseconds. Either
  one on the UI thread is a visible freeze.
* A worker that writes to the database opens its own connection. SQLite in WAL
  mode handles one writer alongside readers; sharing a single connection object
  across threads does not.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from crate.audio import AudioDecodeError, decode
from crate.config import Settings
from crate.library.scanner import LibraryScanner
from crate.persistence.database import open_database
from crate.persistence.sample_repository import SampleRepository


class DecodeWorker(QObject):
    """Decodes audio off the UI thread, newest request wins.

    Each request carries a token. Arrow-keying down a list fires a decode per
    row; by the time a slow one finishes its result is stale, and the main
    window drops any result whose token is not the current one. Cheaper and
    far simpler than trying to cancel a decode in flight.
    """

    decoded = Signal(int, object)
    failed = Signal(int, str)

    @Slot(int, str)
    def decode_file(self, token: int, path: str) -> None:
        try:
            audio = decode(Path(path))
        except AudioDecodeError as exc:
            self.failed.emit(token, exc.message)
            return
        except Exception as exc:  # a decoder crash must not kill the thread
            self.failed.emit(token, f"{type(exc).__name__}: {exc}")
            return
        self.decoded.emit(token, audio)


class ScanWorker(QObject):
    """Runs library scans on a worker thread with progress and cancellation."""

    progress = Signal(object)
    root_finished = Signal(object)
    all_finished = Signal()
    failed = Signal(str)

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        """Callable from the UI thread; the scanner polls it at batch boundaries."""
        self._cancel.set()

    @Slot(list)
    def scan(self, paths: list[str]) -> None:
        self._cancel.clear()
        connection = None
        try:
            connection = open_database(self._settings.database_path)
            scanner = LibraryScanner(SampleRepository(connection), self._settings)
            for path in paths:
                report = scanner.scan(
                    Path(path),
                    progress=self.progress.emit,
                    cancel=self._cancel.is_set,
                )
                self.root_finished.emit(report)
                if report.cancelled:
                    break
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if connection is not None:
                connection.close()
            self.all_finished.emit()
