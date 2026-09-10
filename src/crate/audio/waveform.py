"""Waveform peak extraction for display.

A waveform view is a few hundred pixels wide and the audio behind it can be
millions of frames. Drawing every frame is both impossible and wrong: what a
producer needs to see is the *envelope*, so each pixel column shows the range
the signal covered in that slice of time.
"""

from __future__ import annotations

import numpy as np


def compute_peaks(samples: np.ndarray, buckets: int) -> np.ndarray:
    """Reduce audio to ``(buckets, 2)`` of per-column ``(min, max)``.

    Args:
        samples: ``(frames, channels)`` or ``(frames,)`` audio.
        buckets: Number of pixel columns to fill. Values below 1 are treated
            as 1.

    Returns:
        Float32 array of shape ``(buckets, 2)``. All-zero for empty input, so
        the view has something valid to draw for a zero-byte file.
    """
    buckets = max(1, int(buckets))
    if samples.size == 0:
        return np.zeros((buckets, 2), dtype=np.float32)

    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    frames = mono.shape[0]

    if frames < buckets:
        # More columns than frames: stretch rather than leaving gaps, so a
        # 200-frame click still draws as a shape instead of a few slivers.
        index = np.linspace(0, frames - 1, buckets).astype(np.int32)
        value = mono[index].astype(np.float32)
        return np.stack([np.minimum(value, 0.0), np.maximum(value, 0.0)], axis=1)

    # Trim the ragged tail so the whole thing is one reshape, not a Python loop
    # over hundreds of buckets.
    per_bucket = frames // buckets
    usable = per_bucket * buckets
    grid = mono[:usable].reshape(buckets, per_bucket)

    peaks = np.empty((buckets, 2), dtype=np.float32)
    peaks[:, 0] = grid.min(axis=1)
    peaks[:, 1] = grid.max(axis=1)

    if usable < frames:
        tail = mono[usable:]
        peaks[-1, 0] = min(peaks[-1, 0], float(tail.min()))
        peaks[-1, 1] = max(peaks[-1, 1], float(tail.max()))

    return peaks
