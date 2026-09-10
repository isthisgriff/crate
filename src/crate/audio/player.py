"""Preview playback.

The producer workflow this exists to serve is: click a sample, hear it now;
click the next one, hear that instead. Latency is the whole feature.

Two decisions follow from that:

* Playback reads from an already-decoded in-memory buffer. There is no file
  I/O and no decoding on the audio path, so starting is a pointer assignment.
* The output stream is opened once and kept open across auditions. It is only
  reopened when the sample rate changes, which is rare inside one library.
  Reopening a PortAudio stream costs tens of milliseconds and is audible as
  hesitation.

Thread safety: PortAudio calls :meth:`_callback` from its own high-priority
thread. Everything it touches is guarded by a short lock, and the callback
itself does no allocation, no I/O and no logging.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any

import numpy as np
import sounddevice as sd

from crate.audio.decoder import DecodedAudio

CHANNELS = 2
"""Decoded audio is always stereo, so the stream always is too."""


class PlaybackState(StrEnum):
    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


class AudioPlaybackError(Exception):
    """No usable audio output device, or the device rejected the stream."""


@dataclass(frozen=True, slots=True)
class PlaybackPosition:
    """Where playback is, sampled at one instant."""

    state: PlaybackState
    frame: int
    total_frames: int
    sample_rate: int

    @property
    def seconds(self) -> float:
        return self.frame / self.sample_rate if self.sample_rate else 0.0

    @property
    def total_seconds(self) -> float:
        return self.total_frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def fraction(self) -> float:
        return self.frame / self.total_frames if self.total_frames else 0.0


class AudioPlayer:
    """Plays :class:`DecodedAudio` through the default output device.

    Not a singleton and not global: the UI owns one and passes it around.
    """

    def __init__(self, *, blocksize: int = 512) -> None:
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self._stream_rate: int | None = None
        self._blocksize = blocksize

        self._buffer: np.ndarray | None = None
        self._position = 0
        self._state = PlaybackState.STOPPED
        self._finished_callback: Callable[[], None] | None = None

    # ------------------------------------------------------------- controls

    def play(self, audio: DecodedAudio, *, start_frame: int = 0) -> None:
        """Start playing ``audio``, replacing whatever was playing."""
        with self._lock:
            self._buffer = audio.samples
            self._position = max(0, min(start_frame, audio.frames))
            self._state = PlaybackState.PLAYING
        self._ensure_stream(audio.sample_rate)

    def toggle(self) -> PlaybackState:
        """Pause if playing, resume if paused. Returns the resulting state."""
        with self._lock:
            if self._state is PlaybackState.PLAYING:
                self._state = PlaybackState.PAUSED
            elif self._state is PlaybackState.PAUSED:
                self._state = PlaybackState.PLAYING
            return self._state

    def pause(self) -> None:
        with self._lock:
            if self._state is PlaybackState.PLAYING:
                self._state = PlaybackState.PAUSED

    def stop(self) -> None:
        """Stop and rewind. The stream stays open, ready for the next sample."""
        with self._lock:
            self._state = PlaybackState.STOPPED
            self._position = 0

    def seek(self, frame: int) -> None:
        with self._lock:
            if self._buffer is None:
                return
            self._position = max(0, min(int(frame), self._buffer.shape[0]))

    def seek_fraction(self, fraction: float) -> None:
        with self._lock:
            if self._buffer is None:
                return
            total = self._buffer.shape[0]
            self._position = max(0, min(int(fraction * total), total))

    def set_finished_callback(self, callback: Callable[[], None] | None) -> None:
        """Called once when playback reaches the end.

        Invoked from the audio thread, so it must be cheap and thread-safe —
        in practice it posts to the UI thread and returns.
        """
        with self._lock:
            self._finished_callback = callback

    # ---------------------------------------------------------------- state

    def position(self) -> PlaybackPosition:
        with self._lock:
            total = 0 if self._buffer is None else int(self._buffer.shape[0])
            return PlaybackPosition(
                state=self._state,
                frame=self._position,
                total_frames=total,
                sample_rate=self._stream_rate or 0,
            )

    @property
    def state(self) -> PlaybackState:
        with self._lock:
            return self._state

    def close(self) -> None:
        """Release the output device and forget the audio. Idempotent."""
        self._close_stream()
        with self._lock:
            self._state = PlaybackState.STOPPED
            self._buffer = None
            self._position = 0

    def _close_stream(self) -> None:
        """Tear down the output stream only, leaving playback state alone.

        Separate from :meth:`close` because reopening at a new sample rate
        happens *during* `play()`, and must not discard the buffer that call
        just set.
        """
        with self._lock:
            stream, self._stream = self._stream, None
            self._stream_rate = None
        if stream is not None:
            # A device can vanish (interface unplugged) while we are closing it.
            with contextlib.suppress(Exception):
                stream.stop()
                stream.close()

    def __enter__(self) -> AudioPlayer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------ internals

    def _ensure_stream(self, sample_rate: int) -> None:
        """Open the output stream, reusing it when the sample rate is unchanged."""
        with self._lock:
            reusable = self._stream is not None and self._stream_rate == sample_rate
        if reusable:
            return

        self._close_stream()
        try:
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=CHANNELS,
                dtype="float32",
                blocksize=self._blocksize,
                callback=self._callback,
            )
            stream.start()
        except Exception as exc:  # sounddevice raises bare Exception subclasses
            with self._lock:
                self._state = PlaybackState.STOPPED
            raise AudioPlaybackError(str(exc) or "could not open audio output") from exc

        with self._lock:
            self._stream = stream
            self._stream_rate = sample_rate

    def _callback(self, outdata: np.ndarray, frames: int, _time: Any, _status: object) -> None:
        """PortAudio pull callback. Runs on the audio thread; keep it boring."""
        finished: Callable[[], None] | None = None
        with self._lock:
            buffer = self._buffer
            if buffer is None or self._state is not PlaybackState.PLAYING:
                outdata[:] = 0.0
                return

            start = self._position
            stop = min(start + frames, buffer.shape[0])
            available = stop - start

            if available > 0:
                outdata[:available] = buffer[start:stop]
            if available < frames:
                outdata[available:] = 0.0
                self._state = PlaybackState.STOPPED
                self._position = 0
                finished = self._finished_callback
            else:
                self._position = stop

        if finished is not None:
            finished()
