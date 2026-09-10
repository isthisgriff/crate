"""Audio engine: decoding, waveform peaks and preview playback.

Deliberately free of Qt. The UI drives this; it knows nothing about the UI.
Phase 3's analysis will decode through the same path, so a file is never
opened twice for two different reasons.
"""

from crate.audio.decoder import AudioDecodeError, DecodedAudio, decode
from crate.audio.player import (
    AudioPlaybackError,
    AudioPlayer,
    PlaybackPosition,
    PlaybackState,
)
from crate.audio.waveform import compute_peaks

__all__ = [
    "AudioDecodeError",
    "AudioPlaybackError",
    "AudioPlayer",
    "DecodedAudio",
    "PlaybackPosition",
    "PlaybackState",
    "compute_peaks",
    "decode",
]
