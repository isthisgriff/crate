"""The Crate browser window.

Layout: filters on top, sample list in the middle, waveform and transport at
the bottom. The whole thing is arranged around one loop —

    type a few letters -> arrow down the list -> hear each sample instantly
    -> star the good one -> drag it into the DAW

so everything on that path is one keystroke, and nothing on it blocks.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QItemSelection,
    QModelIndex,
    QPoint,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from crate.app import CrateApp
from crate.audio import AudioPlaybackError, AudioPlayer, DecodedAudio, PlaybackState
from crate.domain import Sample, ScanProgress, ScanReport
from crate.ui.models import SORT_BY_COLUMN, Column, SampleTableModel
from crate.ui.waveform_view import WaveformView
from crate.ui.workers import DecodeWorker, ScanWorker

SEARCH_DEBOUNCE_MS = 140
"""Long enough to skip a query per keystroke while typing, short enough that
the list feels like it is filtering live."""

TRANSPORT_TICK_MS = 30


class MainWindow(QMainWindow):
    """Composition of the browser: model, player, workers and widgets."""

    decode_requested = Signal(int, str)
    scan_requested = Signal(list)

    def __init__(self, app: CrateApp) -> None:
        super().__init__()
        self._app = app
        self._player = AudioPlayer()
        self._audio: DecodedAudio | None = None
        self._audio_is_final = False
        self._decode_token = 0
        self._autoplay = True

        self.setWindowTitle("Crate")
        self.resize(1180, 760)

        self._model = SampleTableModel(app.samples, self)
        self._build_ui()
        self._start_workers()
        self._install_shortcuts()

        self._model.reload()
        self._refresh_status()
        self._populate_formats()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        layout.addLayout(self._build_filter_bar())
        layout.addWidget(self._build_table(), stretch=1)
        layout.addWidget(self._build_waveform())
        layout.addLayout(self._build_transport())

        self.setCentralWidget(central)
        self._build_status_bar()

    def _build_filter_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search filenames and folders…   (Ctrl+F)")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._apply_filters)

        self._format = QComboBox()
        self._format.addItem("All formats", "")
        self._format.currentIndexChanged.connect(self._apply_filters)

        self._favorites_only = QCheckBox("★ Favourites")
        self._favorites_only.toggled.connect(self._apply_filters)

        scan_button = QPushButton("Scan folder…")
        scan_button.clicked.connect(self._choose_folder_to_scan)

        bar.addWidget(self._search, stretch=1)
        bar.addWidget(self._format)
        bar.addWidget(self._favorites_only)
        bar.addWidget(scan_button)
        return bar

    def _build_table(self) -> QTableView:
        self._table = QTableView()
        self._table.setModel(self._model)
        # Drag support proper lives on the model (flags + mimeData); the view
        # only has to be willing to start one.
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.doubleClicked.connect(lambda _index: self._replay())

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(Column.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(Column.FOLDER, QHeaderView.ResizeMode.Interactive)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(Column.NAME, Qt.SortOrder.AscendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)
        self._table.setColumnWidth(Column.FOLDER, 220)
        self._table.setColumnWidth(Column.FORMAT, 60)
        self._table.setColumnWidth(Column.SIZE, 80)
        self._table.setColumnWidth(Column.MODIFIED, 96)
        self._table.setColumnWidth(Column.FAVORITE, 34)

        vertical = self._table.verticalHeader()
        vertical.setVisible(False)
        vertical.setDefaultSectionSize(22)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.clicked.connect(self._on_cell_clicked)
        return self._table

    def _build_waveform(self) -> WaveformView:
        self._waveform = WaveformView()
        self._waveform.seek_requested.connect(self._on_seek)
        return self._waveform

    def _build_transport(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._play_button = QPushButton("▶")
        self._play_button.setFixedWidth(44)
        self._play_button.clicked.connect(self._toggle_playback)

        stop_button = QPushButton("■")
        stop_button.setFixedWidth(44)
        stop_button.clicked.connect(self._stop_playback)

        self._autoplay_box = QCheckBox("Audition on select")
        self._autoplay_box.setChecked(True)
        self._autoplay_box.toggled.connect(self._set_autoplay)

        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setMinimumWidth(96)
        self._detail_label = QLabel("")
        self._detail_label.setObjectName("detail")

        bar.addWidget(self._play_button)
        bar.addWidget(stop_button)
        bar.addWidget(self._time_label)
        bar.addWidget(self._detail_label, stretch=1)
        bar.addWidget(self._autoplay_box)

        self._transport_timer = QTimer(self)
        self._transport_timer.setInterval(TRANSPORT_TICK_MS)
        self._transport_timer.timeout.connect(self._tick_transport)
        self._transport_timer.start()
        return bar

    def _build_status_bar(self) -> None:
        self._status = QLabel("")
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(180)
        self._progress.setRange(0, 0)
        self._progress.hide()

        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.hide()
        self._cancel_button.clicked.connect(self._cancel_scan)

        self.statusBar().addWidget(self._status, 1)
        self.statusBar().addPermanentWidget(self._progress)
        self.statusBar().addPermanentWidget(self._cancel_button)

    def _install_shortcuts(self) -> None:
        for keys, handler in (
            ("Ctrl+F", self._focus_search),
            ("/", self._focus_search),
            ("Space", self._toggle_playback),
            ("Esc", self._stop_playback),
            ("F", self._toggle_favorite),
            ("Ctrl+R", self._rescan_all),
        ):
            QShortcut(QKeySequence(keys), self, handler)

    # ------------------------------------------------------------- workers

    def _start_workers(self) -> None:
        self._decode_thread = QThread(self)
        self._decoder = DecodeWorker()
        self._decoder.moveToThread(self._decode_thread)
        self.decode_requested.connect(self._decoder.decode_file)
        self._decoder.decoded.connect(self._on_decoded)
        self._decoder.failed.connect(self._on_decode_failed)
        self._decode_thread.start()

        self._scan_thread = QThread(self)
        self._scanner = ScanWorker(self._app.settings)
        self._scanner.moveToThread(self._scan_thread)
        self.scan_requested.connect(self._scanner.scan)
        self._scanner.progress.connect(self._on_scan_progress)
        self._scanner.root_finished.connect(self._on_root_scanned)
        self._scanner.all_finished.connect(self._on_scan_finished)
        self._scanner.failed.connect(self._on_scan_failed)
        self._scan_thread.start()

    # ------------------------------------------------------------ filtering

    def _on_search_changed(self, _text: str) -> None:
        self._search_timer.start()

    def _apply_filters(self) -> None:
        self._search_timer.stop()
        extension = self._format.currentData() or ""
        self._model.set_query(
            replace(
                self._model.query,
                text=self._search.text(),
                extensions=frozenset({extension}) if extension else frozenset(),
                favorites_only=self._favorites_only.isChecked(),
            )
        )
        self._refresh_status()

    def _on_header_clicked(self, column: int) -> None:
        """Sort by the clicked column, toggling direction on a repeat click.

        The indicator is driven by hand. `setSortingEnabled(True)` would make
        Qt call `QAbstractItemModel.sort()`, which sorts rows already loaded —
        and the model only ever holds one page. Sorting has to happen in SQL,
        so the header is presentation only.
        """
        if column not in SORT_BY_COLUMN:
            return

        header = self._table.horizontalHeader()
        same_column = SORT_BY_COLUMN[column] is self._model.query.sort
        descending = same_column and not self._model.query.descending

        header.setSortIndicator(
            column,
            Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder,
        )
        self._model.sort_by_column(column, descending)
        self._table.scrollToTop()
        self._refresh_status()

    def _populate_formats(self) -> None:
        current = self._format.currentData()
        self._format.blockSignals(True)
        self._format.clear()
        self._format.addItem("All formats", "")
        for extension in self._app.samples.extensions_in_use():
            self._format.addItem(extension.lstrip(".").upper(), extension)
        index = self._format.findData(current)
        self._format.setCurrentIndex(max(0, index))
        self._format.blockSignals(False)

    def _focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    # ------------------------------------------------------------- playback

    def _on_selection_changed(self, _selected: QItemSelection, _d: QItemSelection) -> None:
        sample = self._current_sample()
        if sample is None:
            return
        self._detail_label.setText(str(sample.path))
        self._decode_token += 1
        self._audio = None
        self._audio_is_final = False
        self._waveform.set_audio(None, "Loading…")
        self.decode_requested.emit(self._decode_token, str(sample.path))

    def _on_decoded(self, token: int, audio: DecodedAudio, is_final: bool) -> None:
        """Handle one stage of a decode.

        Long files arrive twice: a short head so playback can begin at once,
        then the whole file. See `HEAD_SECONDS`.
        """
        if token != self._decode_token:
            return  # superseded while the user kept moving

        already_playing_head = self._audio is not None and not self._audio_is_final
        self._audio = audio
        self._audio_is_final = is_final
        self._detail_label.setText(self._describe(audio))

        if not is_final:
            # Do not draw a 3-second envelope stretched across a 60-second
            # file; that would be a lie the playhead then races across.
            self._waveform.set_audio(None, "Loading waveform…")
            if self._autoplay:
                self._start_playback()
            return

        self._waveform.set_audio(audio)
        if already_playing_head:
            # Swap the longer buffer in underneath the callback. If it refuses
            # (the user stopped, or moved on) there is nothing to restart.
            self._player.extend(audio)
            return
        if self._autoplay:
            self._start_playback()

    def _on_decode_failed(self, token: int, message: str) -> None:
        if token != self._decode_token:
            return
        self._audio = None
        self._waveform.set_audio(None, f"Cannot decode: {message}")
        self._player.stop()

    def _describe(self, audio: DecodedAudio) -> str:
        channels = {1: "mono", 2: "stereo"}.get(audio.source_channels, f"{audio.source_channels}ch")
        parts = [
            audio.path.name,
            f"{audio.duration_seconds:.2f}s",
            f"{audio.sample_rate / 1000:g} kHz",
            channels,
        ]
        if audio.truncated:
            parts.append("(preview truncated)")
        return "   ·   ".join(parts)

    def _start_playback(self) -> None:
        if self._audio is None:
            return
        try:
            self._player.play(self._audio)
        except AudioPlaybackError as exc:
            self._autoplay = False
            self._autoplay_box.setChecked(False)
            QMessageBox.warning(self, "No audio output", str(exc))

    def _replay(self) -> None:
        self._player.stop()
        self._start_playback()

    def _toggle_playback(self) -> None:
        if self._player.state is PlaybackState.STOPPED:
            self._start_playback()
        else:
            self._player.toggle()
        self._update_play_button()

    def _stop_playback(self) -> None:
        self._player.stop()
        self._waveform.set_playhead(0.0)
        self._update_play_button()

    def _set_autoplay(self, enabled: bool) -> None:
        self._autoplay = enabled

    def _on_seek(self, fraction: float) -> None:
        self._player.seek_fraction(fraction)
        if self._player.state is not PlaybackState.PLAYING:
            self._start_playback()
            self._player.seek_fraction(fraction)

    def _tick_transport(self) -> None:
        position = self._player.position()
        self._waveform.set_playhead(position.fraction)

        # The player only knows a duration once it has opened a stream. With
        # "audition on select" off, a loaded sample would otherwise read
        # 0:00 / 0:00 until you pressed play.
        total = position.total_seconds
        if self._audio is not None:
            # The file's real playable length, not the length of whichever
            # decode stage happens to be loaded.
            total = self._audio.preview_seconds

        self._time_label.setText(f"{_clock(position.seconds)} / {_clock(total)}")
        self._update_play_button()

    def _update_play_button(self) -> None:
        playing = self._player.state is PlaybackState.PLAYING
        self._play_button.setText("❚❚" if playing else "▶")

    # ------------------------------------------------------------ favourites

    def _on_cell_clicked(self, index: QModelIndex) -> None:
        if index.column() == Column.FAVORITE:
            self._toggle_favorite()

    def _toggle_favorite(self) -> None:
        row = self._table.currentIndex().row()
        sample = self._model.sample_at(row)
        if sample is None:
            return
        from crate.library.scanner import utc_now

        self._app.samples.set_favorite(sample.id, not sample.favorite, utc_now())
        updated = self._app.samples.get_sample(sample.id)
        if updated is not None:
            self._model.replace_row(row, updated)

    # --------------------------------------------------------------- files

    def _show_context_menu(self, point: QPoint) -> None:
        sample = self._current_sample()
        if sample is None:
            return
        menu = QMenu(self)

        reveal = QAction("Show in Explorer", self)
        reveal.triggered.connect(lambda: _reveal(sample.path))
        menu.addAction(reveal)

        copy = QAction("Copy full path", self)
        copy.triggered.connect(lambda: _copy_to_clipboard(str(sample.path)))
        menu.addAction(copy)

        star = QAction("Remove from favourites" if sample.favorite else "Add to favourites", self)
        star.triggered.connect(self._toggle_favorite)
        menu.addAction(star)

        menu.exec(self._table.viewport().mapToGlobal(point))

    # -------------------------------------------------------------- scanning

    def _choose_folder_to_scan(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a sample folder")
        if folder:
            self._begin_scan([folder])

    def _rescan_all(self) -> None:
        roots = [str(root.path) for root in self._app.samples.list_roots()]
        if not roots:
            self._choose_folder_to_scan()
            return
        self._begin_scan(roots)

    def _begin_scan(self, paths: list[str]) -> None:
        self._progress.show()
        self._cancel_button.show()
        self._status.setText("Scanning…")
        self.scan_requested.emit(paths)

    def _cancel_scan(self) -> None:
        self._scanner.request_cancel()
        self._status.setText("Cancelling…")

    def _on_scan_progress(self, progress: ScanProgress) -> None:
        self._status.setText(
            f"Scanning… {progress.files_seen:,} files   "
            f"({progress.added:,} new, {progress.updated:,} changed, "
            f"{progress.errors:,} errors)"
        )

    def _on_root_scanned(self, report: ScanReport) -> None:
        verb = "Cancelled" if report.cancelled else "Scanned"
        self._status.setText(
            f"{verb} {report.root.path} — {report.added:,} new, {report.updated:,} changed, "
            f"{report.moved:,} moved, {report.marked_missing:,} missing, "
            f"{report.errors:,} errors"
        )

    def _on_scan_finished(self) -> None:
        self._progress.hide()
        self._cancel_button.hide()
        self._model.reload()
        self._populate_formats()
        QTimer.singleShot(2500, self._refresh_status)

    def _on_scan_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Scan failed", message)

    # -------------------------------------------------------------- helpers

    def _current_sample(self) -> Sample | None:
        return self._model.sample_at(self._table.currentIndex().row())

    def _refresh_status(self) -> None:
        stats = self._app.samples.stats()
        shown = self._model.total
        self._status.setText(
            f"{shown:,} shown   ·   {stats.present_samples:,} samples indexed"
            + (f"   ·   {stats.missing_samples:,} missing" if stats.missing_samples else "")
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self._transport_timer.stop()
        self._player.close()

        # Cancel before quitting: `quit()` only ends the event loop once the
        # running slot returns, so closing mid-scan would otherwise block for
        # the full timeout and leave the thread alive. The scan is resumable,
        # so cancelling costs nothing.
        self._scanner.request_cancel()
        for thread in (self._decode_thread, self._scan_thread):
            thread.quit()
            if not thread.wait(5000):
                thread.terminate()
                thread.wait(1000)
        super().closeEvent(event)


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _reveal(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def _copy_to_clipboard(text: str) -> None:
    from PySide6.QtWidgets import QApplication

    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)
