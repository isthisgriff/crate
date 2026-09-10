"""Waveform display with click-to-seek.

Holds the decoded audio rather than a fixed set of peaks, so resizing the
window recomputes the envelope at the new width instead of stretching a
low-resolution picture.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from crate.audio import DecodedAudio, compute_peaks


class WaveformView(QWidget):
    """Draws the envelope of the loaded sample and reports seek clicks."""

    seek_requested = Signal(float)
    """Emitted with a 0..1 position when the user clicks or drags."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.IBeamCursor)

        self._audio: DecodedAudio | None = None
        self._peaks: np.ndarray | None = None
        self._playhead = 0.0
        self._message = "Select a sample"

        self._background = QColor("#141518")
        self._wave = QColor("#5ac8a8")
        self._wave_played = QColor("#2f7d68")
        self._axis = QColor("#2a2c31")
        self._cursor = QColor("#f0f0f0")
        self._text = QColor("#6d7078")

    # ----------------------------------------------------------------- API

    def set_audio(self, audio: DecodedAudio | None, message: str = "Select a sample") -> None:
        self._audio = audio
        self._message = message
        self._playhead = 0.0
        self._recompute()
        self.update()

    def set_playhead(self, fraction: float) -> None:
        clamped = max(0.0, min(1.0, fraction))
        # Repaint only when the change is visible at this width; the transport
        # timer fires many times per second and full repaints add up.
        if abs(clamped - self._playhead) * max(1, self.width()) < 1.0:
            return
        self._playhead = clamped
        self.update()

    # -------------------------------------------------------------- events

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._recompute()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._emit_seek(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._emit_seek(event)

    def _emit_seek(self, event: QMouseEvent) -> None:
        if self._audio is None or self.width() <= 0:
            return
        self.seek_requested.emit(max(0.0, min(1.0, event.position().x() / self.width())))

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        painter.fillRect(rect, self._background)

        middle = rect.height() / 2
        painter.setPen(self._axis)
        painter.drawLine(0, int(middle), rect.width(), int(middle))

        if self._peaks is None or self._audio is None:
            painter.setPen(self._text)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._message)
            painter.end()
            return

        played_until = self._playhead * rect.width()
        scale = middle - 4
        for x in range(min(self._peaks.shape[0], rect.width())):
            low, high = self._peaks[x]
            top = middle - float(high) * scale
            bottom = middle - float(low) * scale
            if bottom - top < 1.0:
                bottom = top + 1.0
            painter.fillRect(
                QRectF(QPointF(x, top), QPointF(x + 1, bottom)),
                self._wave_played if x < played_until else self._wave,
            )

        if self._playhead > 0.0:
            painter.setPen(self._cursor)
            position = int(played_until)
            painter.drawLine(position, 0, position, rect.height())

        painter.end()

    # ----------------------------------------------------------- internals

    def _recompute(self) -> None:
        if self._audio is None or self._audio.frames == 0:
            self._peaks = None
            return
        self._peaks = compute_peaks(self._audio.samples, max(1, self.width()))
