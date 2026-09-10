"""Decoding audio files into memory.

Everything downstream — the waveform, the player, and Phase 3's analysis —
consumes :class:`DecodedAudio`, so the awkward parts of real-world audio are
dealt with exactly once, here:

* Channel layouts are normalised to stereo. Mono is duplicated, and anything
  wider than stereo is folded down. The player can then hold one output stream
  open across auditions instead of reopening it per file, which is most of
  what makes preview feel instant.
* Long files are truncated. A sample browser must not allocate 600 MB because
  someone left a two-hour DJ set in their samples folder.
* Failures raise one exception type. Callers do not need to know that
  libsndfile exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

DEFAULT_MAX_SECONDS = 300.0
"""Preview decode cap. Long enough for any loop or one-shot, short enough that
a stray podcast episode costs ~100 MB rather than gigabytes."""

HEAD_SECONDS = 3.0
"""How much to decode before starting playback.

Measured on a 35k-sample library: a full decode costs 7 ms for a one-shot but
550 ms for a 20 MB+ file, while decoding the first three seconds is flat at
~1 ms whatever the size. Auditioning the head first makes time-to-sound
constant, which is the difference between arrowing through loops feeling
broken and feeling like a sampler."""


class AudioDecodeError(Exception):
    """A file could not be decoded. Carries the path and the underlying cause."""

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


@dataclass(frozen=True, slots=True)
class DecodedAudio:
    """PCM audio in memory, always float32 and always stereo."""

    path: Path
    samples: np.ndarray
    """Shape ``(frames, 2)``, float32, nominally in [-1, 1]."""

    sample_rate: int
    source_channels: int
    """Channel count of the file itself, before normalisation, for display."""

    source_frames: int
    """Frame count of the file itself, before truncation."""

    truncated: bool = False

    @property
    def frames(self) -> int:
        return int(self.samples.shape[0])

    @property
    def duration_seconds(self) -> float:
        """Duration of the *file*, not of the decoded excerpt."""
        return self.source_frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def decoded_seconds(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def preview_seconds(self) -> float:
        """How much of this file the browser can ever play.

        Stable across the head/full decode swap, so the transport does not
        show 0:03 for a moment and then jump to 1:04.
        """
        return min(self.duration_seconds, DEFAULT_MAX_SECONDS)


def decode(path: Path | str, *, max_seconds: float | None = DEFAULT_MAX_SECONDS) -> DecodedAudio:
    """Decode ``path`` to stereo float32.

    Raises:
        AudioDecodeError: for anything the decoder cannot handle — unsupported
            codec, truncated file, zero bytes, missing file, permission error.
            One exception type, because at the call site there is one response.
    """
    target = Path(path)
    try:
        with sf.SoundFile(str(target)) as handle:
            sample_rate = int(handle.samplerate)
            source_channels = int(handle.channels)
            source_frames = int(handle.frames)

            wanted = source_frames
            if max_seconds is not None and sample_rate > 0:
                wanted = min(source_frames, int(max_seconds * sample_rate))

            raw = handle.read(frames=wanted, dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError, OSError) as exc:
        raise AudioDecodeError(target, str(exc) or type(exc).__name__) from exc

    if sample_rate <= 0:
        raise AudioDecodeError(target, "file reports a sample rate of zero")

    return DecodedAudio(
        path=target,
        samples=to_stereo(raw),
        sample_rate=sample_rate,
        source_channels=source_channels,
        source_frames=source_frames,
        truncated=wanted < source_frames,
    )


def to_stereo(raw: np.ndarray) -> np.ndarray:
    """Normalise any channel layout to a contiguous ``(frames, 2)`` float32 array.

    Contiguity matters: PortAudio reads the buffer directly in its callback, and
    a non-contiguous view would force a copy on the audio thread.
    """
    if raw.ndim == 1:
        raw = raw.reshape(-1, 1)
    channels = raw.shape[1]

    if channels == 0:
        stereo = np.zeros((raw.shape[0], 2), dtype=np.float32)
    elif channels == 1:
        stereo = np.repeat(raw, 2, axis=1)
    elif channels == 2:
        stereo = raw
    else:
        # Surround and multitrack stems: fold down rather than refuse. Averaging
        # the odd/even channels keeps a rough left/right image.
        left = raw[:, 0::2].mean(axis=1)
        right = raw[:, 1::2].mean(axis=1)
        stereo = np.stack([left, right], axis=1)

    return np.ascontiguousarray(stereo, dtype=np.float32)
