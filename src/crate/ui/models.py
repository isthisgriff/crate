"""Qt model for the sample list.

The one hard requirement: showing 40 rows out of 35,000 must cost 40 rows.
Qt's usual answer, `QSortFilterProxyModel`, cannot do that — it filters and
sorts rows it already has, so it needs all of them in memory. So filtering and
sorting are pushed into SQL, and the model pages results in through
`canFetchMore`/`fetchMore`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QUrl,
)

from crate.domain import Sample, SampleQuery, SampleSort
from crate.persistence.sample_repository import SampleRepository

Index = QModelIndex | QPersistentModelIndex
"""Qt hands models either kind of index; the signatures must accept both."""

ROOT = QModelIndex()
"""The invalid index meaning "the top level". A table has no other parent, and
QModelIndex is a value type, so one shared instance is correct and cheap."""

PAGE_SIZE = 200
"""Rows fetched per page. Comfortably more than a screenful, so scrolling does
not stutter, and small enough that the first paint is immediate."""


class Column:
    NAME = 0
    FOLDER = 1
    FORMAT = 2
    SIZE = 3
    MODIFIED = 4
    FAVORITE = 5
    COUNT = 6


HEADERS = {
    Column.NAME: "Name",
    Column.FOLDER: "Folder",
    Column.FORMAT: "Type",
    Column.SIZE: "Size",
    Column.MODIFIED: "Modified",
    Column.FAVORITE: "★",
}

SORT_BY_COLUMN = {
    Column.NAME: SampleSort.FILENAME,
    Column.FOLDER: SampleSort.PATH,
    Column.SIZE: SampleSort.SIZE,
    Column.MODIFIED: SampleSort.MODIFIED,
}


def human_size(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class SampleTableModel(QAbstractTableModel):
    """A lazily-paged, SQL-filtered view of the sample inventory."""

    def __init__(self, repository: SampleRepository, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo = repository
        self._query = SampleQuery()
        self._rows: list[Sample] = []
        self._total = 0

    # -------------------------------------------------------------- queries

    @property
    def query(self) -> SampleQuery:
        return self._query

    @property
    def total(self) -> int:
        """Total matching rows, including those not yet fetched."""
        return self._total

    def set_query(self, query: SampleQuery) -> None:
        """Replace the query and reload from the first page.

        A no-op when the query is unchanged, so the search box can call this on
        every keystroke without re-querying for a cursor move.
        """
        if query == self._query:
            return
        self._query = query
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self._total = self._repo.count_matching(self._query)
        self._rows = self._repo.search(self._query, limit=PAGE_SIZE)
        self.endResetModel()

    def sample_at(self, row: int) -> Sample | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def row_of(self, sample_id: int) -> int | None:
        for index, sample in enumerate(self._rows):
            if sample.id == sample_id:
                return index
        return None

    def replace_row(self, row: int, sample: Sample) -> None:
        """Swap one row in place, e.g. after toggling a favourite.

        Avoids a full reload, which would lose the selection and scroll
        position — the two things a producer notices immediately.
        """
        if not 0 <= row < len(self._rows):
            return
        self._rows[row] = sample
        self.dataChanged.emit(self.index(row, 0), self.index(row, Column.COUNT - 1))

    def sort_by_column(self, column: int, descending: bool) -> None:
        sort = SORT_BY_COLUMN.get(column)
        if sort is None:
            return
        self.set_query(replace(self._query, sort=sort, descending=descending))

    # ------------------------------------------------------- Qt model API

    def rowCount(self, parent: Index = ROOT, /) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: Index = ROOT, /) -> int:
        if parent.isValid():
            return 0
        return Column.COUNT

    def canFetchMore(self, parent: Index = ROOT, /) -> bool:
        if parent.isValid():
            return False
        return len(self._rows) < self._total

    def fetchMore(self, parent: Index = ROOT, /) -> None:
        if parent.isValid():
            return
        remaining = self._total - len(self._rows)
        if remaining <= 0:
            return
        batch = self._repo.search(
            self._query, limit=min(PAGE_SIZE, remaining), offset=len(self._rows)
        )
        if not batch:
            # The library changed underneath us (a rescan removed rows). Stop
            # asking rather than looping forever.
            self._total = len(self._rows)
            return
        first = len(self._rows)
        self.beginInsertRows(ROOT, first, first + len(batch) - 1)
        self._rows.extend(batch)
        self.endInsertRows()

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return HEADERS.get(section)

    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        sample = self.sample_at(index.row())
        if sample is None:
            return None
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(sample, column)
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(sample.path)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (
            Column.SIZE,
            Column.FAVORITE,
        ):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    # ------------------------------------------------------------ dragging

    # Dragging a row into a DAW is what makes Crate a replacement for the
    # file browser rather than something you consult before opening it.
    #
    # All three pieces below live on the *model*, not the view, because that
    # is where Qt looks: `QAbstractItemView::startDrag` filters the selection
    # by `ItemIsDragEnabled` and then calls `model->mimeData()`. `QTableView`
    # has no `mimeData` of its own -- an override there is silently never
    # called, which is exactly the bug this replaced.

    def flags(self, index: Index) -> Qt.ItemFlag:
        base = super().flags(index)
        if not index.isValid():
            return base
        return base | Qt.ItemFlag.ItemIsDragEnabled

    def mimeTypes(self) -> list[str]:
        return ["text/uri-list", "text/plain"]

    def mimeData(self, indexes: Sequence[Index]) -> QMimeData:
        """Hand the drop target real files.

        Windows turns a `text/uri-list` of local files into CF_HDROP, which is
        what a DAW is listening for. `text/plain` is the fallback for hosts
        that only read text.
        """
        paths: list[str] = []
        seen: set[int] = set()
        for index in indexes:
            row = index.row()
            if row in seen:
                continue
            seen.add(row)
            sample = self.sample_at(row)
            if sample is not None:
                paths.append(str(sample.path))

        data = QMimeData()
        if paths:
            data.setUrls([QUrl.fromLocalFile(path) for path in paths])
            # Native separators here, not the URL form: a host that reads
            # text/plain wants a path it can open, not file:///C:/...
            data.setText("\n".join(paths))
        return data

    @staticmethod
    def _display(sample: Sample, column: int) -> str:
        if column == Column.NAME:
            return sample.filename
        if column == Column.FOLDER:
            return sample.path.parent.name
        if column == Column.FORMAT:
            return sample.extension.lstrip(".").upper()
        if column == Column.SIZE:
            return human_size(sample.size_bytes)
        if column == Column.MODIFIED:
            moment = datetime.fromtimestamp(sample.mtime_ns / 1_000_000_000)
            return moment.strftime("%Y-%m-%d")
        if column == Column.FAVORITE:
            return "★" if sample.favorite else ""
        return ""
