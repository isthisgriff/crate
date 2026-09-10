"""UI tests.

Scope is deliberate. These check the parts of the UI that hold *logic* — the
table model's paging and the window's wiring — not that pixels look right.
Every test here runs headless (`QT_QPA_PLATFORM=offscreen`, set in
`conftest.py`), so they need no display and no audio device.

Playback is stubbed: the point is that selecting a row asks for the right
file, not that a speaker made a noise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from collections.abc import Iterator

import numpy as np
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from crate.app import CrateApp
from crate.audio import DecodedAudio, PlaybackState
from crate.domain import SampleQuery, SampleSort
from crate.library.scanner import LibraryScanner
from crate.persistence.sample_repository import SampleRepository
from crate.ui.main_window import MainWindow
from crate.ui.models import PAGE_SIZE, Column, SampleTableModel, human_size

pytestmark = pytest.mark.gui


@pytest.fixture
def indexed(scanner: LibraryScanner, library: Path) -> Path:
    scanner.scan(library)
    return library


@pytest.fixture
def model(qapp: QApplication, repository: SampleRepository, indexed: Path) -> SampleTableModel:
    table_model = SampleTableModel(repository)
    table_model.reload()
    return table_model


# ---------------------------------------------------------------- the model


def test_model_exposes_rows_and_columns(model: SampleTableModel) -> None:
    assert model.rowCount() == 7
    assert model.columnCount() == Column.COUNT


def test_model_renders_each_column(model: SampleTableModel) -> None:
    row = next(
        index
        for index in range(model.rowCount())
        if model.sample_at(index).filename == "808_new.flac"  # type: ignore[union-attr]
    )

    def cell(column: int) -> str:
        value: str = model.data(model.index(row, column), Qt.ItemDataRole.DisplayRole)
        return value

    assert cell(Column.NAME) == "808_new.flac"
    assert cell(Column.FOLDER) == "bass"
    assert cell(Column.FORMAT) == "FLAC"
    assert cell(Column.FAVORITE) == ""
    assert cell(Column.SIZE).endswith("B")


def test_tooltip_is_the_full_path(model: SampleTableModel, indexed: Path) -> None:
    tip = model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
    assert str(indexed) in tip


def test_setting_a_query_filters(model: SampleTableModel) -> None:
    model.set_query(SampleQuery(text="kick"))
    assert model.rowCount() == 3
    assert model.total == 3


def test_setting_the_same_query_does_not_reload(model: SampleTableModel) -> None:
    resets: list[int] = []
    model.modelAboutToBeReset.connect(lambda: resets.append(1))

    model.set_query(SampleQuery(text="kick"))
    model.set_query(SampleQuery(text="kick"))

    # The search box fires on every keystroke, including ones that change
    # nothing; re-querying then would drop the user's selection.
    assert len(resets) == 1


def test_sorting_reorders(model: SampleTableModel) -> None:
    model.sort_by_column(Column.SIZE, descending=True)
    sizes = [model.sample_at(row).size_bytes for row in range(model.rowCount())]  # type: ignore[union-attr]
    assert sizes == sorted(sizes, reverse=True)
    assert model.query.sort is SampleSort.SIZE


def test_paging_loads_lazily(
    qapp: QApplication,
    repository: SampleRepository,
    scanner: LibraryScanner,
    tmp_path: Path,
) -> None:
    """The whole point of the model: a big library costs one page to show."""
    root = tmp_path / "big"
    root.mkdir()
    for index in range(PAGE_SIZE * 2 + 25):
        (root / f"hit_{index:04d}.wav").write_bytes(b"x")
    scanner.scan(root)

    model = SampleTableModel(repository)
    model.reload()

    assert model.total == PAGE_SIZE * 2 + 25
    assert model.rowCount() == PAGE_SIZE
    assert model.canFetchMore() is True

    model.fetchMore()
    assert model.rowCount() == PAGE_SIZE * 2

    model.fetchMore()
    assert model.rowCount() == model.total
    assert model.canFetchMore() is False


def test_paged_rows_are_unique(
    qapp: QApplication,
    repository: SampleRepository,
    scanner: LibraryScanner,
    tmp_path: Path,
) -> None:
    root = tmp_path / "big"
    root.mkdir()
    for index in range(PAGE_SIZE + 40):
        (root / f"hit_{index:04d}.wav").write_bytes(b"x")
    scanner.scan(root)

    model = SampleTableModel(repository)
    model.reload()
    while model.canFetchMore():
        model.fetchMore()

    ids = [model.sample_at(row).id for row in range(model.rowCount())]  # type: ignore[union-attr]
    assert len(set(ids)) == len(ids) == model.total


def test_replace_row_updates_in_place(
    model: SampleTableModel, repository: SampleRepository
) -> None:
    from datetime import UTC, datetime

    sample = model.sample_at(0)
    assert sample is not None
    changed: list[int] = []
    model.dataChanged.connect(lambda *_: changed.append(1))

    repository.set_favorite(sample.id, True, datetime(2026, 1, 1, tzinfo=UTC))
    updated = repository.get_sample(sample.id)
    assert updated is not None
    model.replace_row(0, updated)

    assert changed
    assert model.data(model.index(0, Column.FAVORITE), Qt.ItemDataRole.DisplayRole) == "★"
    # No reset means the selection and scroll position survive.
    assert model.rowCount() == 7


def test_out_of_range_access_is_safe(model: SampleTableModel) -> None:
    assert model.sample_at(9999) is None
    assert model.data(QModelIndex()) is None
    model.replace_row(9999, model.sample_at(0))  # type: ignore[arg-type]


def test_human_size() -> None:
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"


# --------------------------------------------------------------- the window


@pytest.fixture
def window(
    qtbot: QtBot, tmp_path: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MainWindow]:
    from crate.audio.player import AudioPlayer

    # Never touch a real audio device in tests.
    monkeypatch.setattr(AudioPlayer, "_ensure_stream", lambda self, rate: None)

    app = CrateApp.open(tmp_path / "data")
    app.scanner().scan(library)

    main = MainWindow(app)
    qtbot.addWidget(main)
    yield main

    main.close()
    app.close()


def test_window_opens_with_the_library_listed(window: MainWindow) -> None:
    assert window._model.total == 7
    assert "7 shown" in window._status.text()


def test_typing_in_the_search_box_filters(window: MainWindow, qtbot: QtBot) -> None:
    window._search.setText("kick")
    qtbot.waitUntil(lambda: window._model.total == 3, timeout=2000)


def test_format_filter_is_populated_from_the_library(window: MainWindow) -> None:
    formats = {window._format.itemText(index) for index in range(window._format.count())}
    assert "All formats" in formats
    assert "WAV" in formats
    assert "FLAC" in formats


def test_selecting_a_row_requests_a_decode(window: MainWindow, qtbot: QtBot) -> None:
    requested: list[tuple[int, str]] = []
    window.decode_requested.connect(lambda token, path: requested.append((token, path)))

    window._table.selectRow(0)

    assert requested
    token, path = requested[-1]
    assert Path(path).exists()
    assert token == window._decode_token


def test_stale_decode_results_are_ignored(window: MainWindow) -> None:
    audio = DecodedAudio(
        path=Path("stale.wav"),
        samples=np.zeros((10, 2), dtype=np.float32),
        sample_rate=44100,
        source_channels=2,
        source_frames=10,
    )
    window._on_decoded(window._decode_token - 5, audio, True)
    assert window._audio is None


def test_toggling_a_favourite_persists_and_updates_the_row(window: MainWindow) -> None:
    window._table.selectRow(0)
    sample = window._model.sample_at(0)
    assert sample is not None

    window._toggle_favorite()

    stored = window._app.samples.get_sample(sample.id)
    assert stored is not None
    assert stored.favorite is True

    in_model = window._model.sample_at(0)
    assert in_model is not None
    assert in_model.favorite is True


def test_undecodable_file_shows_a_message_and_does_not_crash(window: MainWindow) -> None:
    window._on_decode_failed(window._decode_token, "malformed header")
    assert window._audio is None


def test_rows_are_draggable(window: MainWindow) -> None:
    """Without ItemIsDragEnabled on the model, Qt never starts a drag at all.

    This is the flag whose absence made drag-into-DAW silently do nothing.
    """
    index = window._model.index(0, 0)
    assert window._model.flags(index) & Qt.ItemFlag.ItemIsDragEnabled
    assert window._table.dragEnabled()


def test_drag_payload_comes_from_the_model(window: MainWindow) -> None:
    """Qt asks the *model* for MIME data, never the view.

    An earlier version put `mimeData` on the QTableView subclass, where Qt
    never calls it -- and the test passed because it called that method
    directly. Go through the model, the way `startDrag` does.
    """
    model = window._model
    assert "text/uri-list" in model.mimeTypes()

    # A row selection hands Qt one index per column; they must collapse to one file.
    indexes = [model.index(0, column) for column in range(model.columnCount())]
    data = model.mimeData(indexes)

    assert data is not None
    urls = data.urls()
    assert len(urls) == 1
    assert urls[0].isLocalFile()
    assert Path(urls[0].toLocalFile()).exists()
    assert data.text() == str(model.sample_at(0).path)  # type: ignore[union-attr]


def test_header_click_sorts_and_toggles_direction(window: MainWindow) -> None:
    """Qt cannot drive this itself: sorting happens in SQL, so the window owns
    the indicator and the toggle."""
    header = window._table.horizontalHeader()

    window._on_header_clicked(Column.SIZE)
    first = window._model.query
    assert first.sort is SampleSort.SIZE
    assert first.descending is False
    assert header.sortIndicatorSection() == Column.SIZE
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder

    window._on_header_clicked(Column.SIZE)
    second = window._model.query
    assert second.descending is True
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

    # A different column starts ascending again rather than inheriting.
    window._on_header_clicked(Column.NAME)
    third = window._model.query
    assert third.sort is SampleSort.FILENAME
    assert third.descending is False


def test_clicking_an_unsortable_column_is_ignored(window: MainWindow) -> None:
    before = window._model.query
    window._on_header_clicked(Column.FAVORITE)
    assert window._model.query == before


def test_duration_is_shown_before_playback_starts(window: MainWindow) -> None:
    window._autoplay = False
    window._on_decoded(
        window._decode_token,
        DecodedAudio(
            path=Path("loop.wav"),
            samples=np.zeros((44100 * 3, 2), dtype=np.float32),
            sample_rate=44100,
            source_channels=2,
            source_frames=44100 * 3,
        ),
        True,
    )
    window._tick_transport()
    assert window._time_label.text() == "0:00 / 0:03"


def test_closing_mid_scan_cancels_rather_than_blocking(window: MainWindow) -> None:
    """`QThread.quit()` waits for the running slot to return, so a scan in
    flight has to be cancelled or closing the window stalls."""
    window._scanner._cancel.clear()
    window.close()
    assert window._scanner._cancel.is_set()
    assert window._scan_thread.isFinished()
    assert window._decode_thread.isFinished()


# ------------------------------------------------------ two-stage decoding


def test_short_file_decodes_in_one_stage(qapp: QApplication, tmp_path: Path) -> None:
    """Most of a sample library is one-shots. They must not pay for two decodes."""
    import soundfile as sf

    from crate.ui.workers import DecodeWorker

    path = tmp_path / "kick.wav"
    sf.write(str(path), np.zeros((4410, 2), dtype=np.float32), 44100)

    worker = DecodeWorker()
    results: list[tuple[DecodedAudio, bool]] = []
    worker.decoded.connect(lambda _t, audio, final: results.append((audio, final)))
    worker.decode_file(1, str(path))

    assert len(results) == 1
    assert results[0][1] is True


def test_long_file_emits_a_head_then_the_whole_thing(qapp: QApplication, tmp_path: Path) -> None:
    """The fix for audition latency: sound starts from a short head, so
    time-to-sound stops scaling with file size."""
    import soundfile as sf

    from crate.audio.decoder import HEAD_SECONDS
    from crate.ui.workers import DecodeWorker

    path = tmp_path / "loop.wav"
    seconds = HEAD_SECONDS * 3
    sf.write(str(path), np.zeros((int(44100 * seconds), 2), dtype=np.float32), 44100)

    worker = DecodeWorker()
    results: list[tuple[DecodedAudio, bool]] = []
    worker.decoded.connect(lambda _t, audio, final: results.append((audio, final)))
    worker.decode_file(7, str(path))

    assert [is_final for _, is_final in results] == [False, True]
    head, full = results[0][0], results[1][0]
    assert head.decoded_seconds == pytest.approx(HEAD_SECONDS, abs=0.05)
    assert full.decoded_seconds == pytest.approx(seconds, abs=0.05)
    # The head is a strict prefix, which is what makes the swap seamless.
    assert np.array_equal(head.samples, full.samples[: head.frames])


def test_window_plays_the_head_then_extends(window: MainWindow) -> None:
    def clip(frames: int, total: int) -> DecodedAudio:
        return DecodedAudio(
            path=Path("loop.wav"),
            samples=np.zeros((frames, 2), dtype=np.float32),
            sample_rate=44100,
            source_channels=2,
            source_frames=total,
            truncated=frames < total,
        )

    token = window._decode_token
    window._on_decoded(token, clip(44100 * 3, 44100 * 30), False)

    # Playing already, but the waveform waits rather than drawing 3s of a 30s file.
    assert window._player.state is PlaybackState.PLAYING
    assert window._player.position().total_frames == 44100 * 3
    assert window._waveform._peaks is None

    window._on_decoded(token, clip(44100 * 30, 44100 * 30), True)

    assert window._player.position().total_frames == 44100 * 30
    assert window._player.state is PlaybackState.PLAYING
    assert window._waveform._peaks is not None


def test_transport_shows_the_whole_file_while_only_the_head_is_loaded(
    window: MainWindow,
) -> None:
    window._on_decoded(
        window._decode_token,
        DecodedAudio(
            path=Path("loop.wav"),
            samples=np.zeros((44100 * 3, 2), dtype=np.float32),
            sample_rate=44100,
            source_channels=2,
            source_frames=44100 * 30,
            truncated=True,
        ),
        False,
    )
    window._tick_transport()
    # 0:30, not 0:03 — the label must not jump when the full decode lands.
    assert window._time_label.text().endswith("/ 0:30")
