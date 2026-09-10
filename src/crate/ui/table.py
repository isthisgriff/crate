"""The sample list view.

Exists as a subclass for one reason: dragging a row out of Crate must hand the
receiving application a *file*, not a row index.

That is deliberately in scope even though full DAW integration is a later
concern. Without it the workflow ends at "I found the sample" and the producer
still has to go find it again in Explorer, which is the thing Crate is supposed
to replace. Qt gives it to us for the cost of one method.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QMimeData, QModelIndex, QPersistentModelIndex, Qt, QUrl
from PySide6.QtWidgets import QAbstractItemView, QTableView, QWidget

from crate.ui.models import SampleTableModel


class SampleTableView(QTableView):
    """A table whose rows drag out as file URLs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def mimeData(self, indexes: Sequence[QModelIndex | QPersistentModelIndex]) -> QMimeData | None:
        model = self.model()
        if not isinstance(model, SampleTableModel):
            return self._default_mime_data(indexes)

        urls: list[QUrl] = []
        seen: set[int] = set()
        for index in indexes:
            row = index.row()
            if row in seen:
                continue
            seen.add(row)
            sample = model.sample_at(row)
            if sample is not None:
                urls.append(QUrl.fromLocalFile(str(sample.path)))

        if not urls:
            return self._default_mime_data(indexes)

        data = QMimeData()
        data.setUrls(urls)
        # Some hosts read text/plain rather than the URL list.
        data.setText("\n".join(url.toLocalFile() for url in urls))
        return data

    def _default_mime_data(
        self, indexes: Sequence[QModelIndex | QPersistentModelIndex]
    ) -> QMimeData | None:
        result: QMimeData | None = super().mimeData(indexes)  # type: ignore[misc]
        return result
