"""Audio engine tests.

Playback itself is not tested against a real device — CI has no speakers, and
asserting that sound came out is not something a test can do. What *is* tested
is the state machine and the buffer arithmetic, by driving the PortAudio
callback directly with a fake output array. That is where the bugs live.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from crate.audio import AudioDecodeError, DecodedAudio, compute_peaks, decode
from crate.audio.decoder import to_stereo
from crate.audio.player import AudioPlayer, PlaybackState

RATE = 44100


def write_tone(
    path: Path, *, seconds: float = 0.25, channels: int = 1, rate: int = RATE, fmt: str = "WAV"
) -> Path:
    frames = int(seconds * rate)
    t = np.linspace(0, seconds, frames, endpoint=False, dtype=np.float32)
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    data = np.repeat(tone.reshape(-1, 1), channels, axis=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, rate, format=fmt)
    return path


# --------------------------------------------------------------------- decode


def test_decodes_mono_to_stereo(tmp_path: Path) -> None:
    audio = decode(write_tone(tmp_path / "mono.wav", channels=1))

    assert audio.samples.ndim == 2
    assert audio.samples.shape[1] == 2
    assert audio.samples.dtype == np.float32
    assert audio.source_channels == 1
    # Mono is duplicated, not silenced on one side.
    assert np.array_equal(audio.samples[:, 0], audio.samples[:, 1])


def test_decodes_stereo_unchanged(tmp_path: Path) -> None:
    audio = decode(write_tone(tmp_path / "stereo.wav", channels=2))
    assert audio.source_channels == 2
    assert audio.samples.shape[1] == 2


def test_reports_duration_and_rate(tmp_path: Path) -> None:
    audio = decode(write_tone(tmp_path / "tone.wav", seconds=0.5))
    assert audio.sample_rate == RATE
    assert audio.duration_seconds == pytest.approx(0.5, abs=0.01)
    assert audio.truncated is False


@pytest.mark.parametrize("fmt", ["WAV", "FLAC", "AIFF"])
def test_decodes_each_format_in_the_library(tmp_path: Path, fmt: str) -> None:
    suffix = {"WAV": ".wav", "FLAC": ".flac", "AIFF": ".aiff"}[fmt]
    audio = decode(write_tone(tmp_path / f"tone{suffix}", fmt=fmt))
    assert audio.frames > 0


def test_long_file_is_truncated(tmp_path: Path) -> None:
    audio = decode(write_tone(tmp_path / "long.wav", seconds=2.0), max_seconds=0.5)

    assert audio.truncated is True
    assert audio.decoded_seconds == pytest.approx(0.5, abs=0.01)
    # duration_seconds still describes the file, not the excerpt.
    assert audio.duration_seconds == pytest.approx(2.0, abs=0.01)


def test_output_buffer_is_contiguous(tmp_path: Path) -> None:
    # PortAudio reads this directly; a non-contiguous view would copy on the
    # audio thread.
    audio = decode(write_tone(tmp_path / "tone.wav", channels=1))
    assert audio.samples.flags["C_CONTIGUOUS"]


def test_zero_byte_file_raises_decode_error(tmp_path: Path) -> None:
    broken = tmp_path / "silence.wav"
    broken.write_bytes(b"")
    with pytest.raises(AudioDecodeError) as caught:
        decode(broken)
    assert caught.value.path == broken


def test_garbage_file_raises_decode_error(tmp_path: Path) -> None:
    broken = tmp_path / "not-audio.wav"
    broken.write_bytes(b"this is definitely not a wav file" * 10)
    with pytest.raises(AudioDecodeError):
        decode(broken)


def test_missing_file_raises_decode_error(tmp_path: Path) -> None:
    with pytest.raises(AudioDecodeError):
        decode(tmp_path / "nope.wav")


def test_surround_is_folded_to_stereo() -> None:
    six_channel = np.tile(np.arange(6, dtype=np.float32), (10, 1))
    stereo = to_stereo(six_channel)
    assert stereo.shape == (10, 2)
    assert stereo[0, 0] == pytest.approx((0 + 2 + 4) / 3)
    assert stereo[0, 1] == pytest.approx((1 + 3 + 5) / 3)


# ------------------------------------------------------------------- waveform


def test_peaks_have_requested_width() -> None:
    samples = np.random.default_rng(0).uniform(-1, 1, (10_000, 2)).astype(np.float32)
    peaks = compute_peaks(samples, 300)
    assert peaks.shape == (300, 2)
    assert np.all(peaks[:, 0] <= peaks[:, 1])


def test_peaks_capture_the_envelope() -> None:
    samples = np.zeros((1000, 1), dtype=np.float32)
    samples[500] = 1.0
    samples[501] = -1.0
    peaks = compute_peaks(samples, 10)
    # The transient must survive downsampling; that is the whole point.
    assert peaks[:, 1].max() == pytest.approx(1.0)
    assert peaks[:, 0].min() == pytest.approx(-1.0)


def test_peaks_of_empty_audio_are_drawable() -> None:
    peaks = compute_peaks(np.zeros((0, 2), dtype=np.float32), 64)
    assert peaks.shape == (64, 2)
    assert not np.isnan(peaks).any()


def test_peaks_when_there_are_fewer_frames_than_columns() -> None:
    samples = np.linspace(-1, 1, 20, dtype=np.float32).reshape(-1, 1)
    peaks = compute_peaks(samples, 200)
    assert peaks.shape == (200, 2)
    assert peaks[:, 1].max() == pytest.approx(1.0)


def test_ragged_tail_is_not_dropped() -> None:
    # 1001 frames into 10 buckets leaves a remainder; the loudest sample is in it.
    samples = np.zeros((1001, 1), dtype=np.float32)
    samples[1000] = 1.0
    peaks = compute_peaks(samples, 10)
    assert peaks[:, 1].max() == pytest.approx(1.0)


# --------------------------------------------------------------------- player


def make_audio(frames: int = 1000, rate: int = RATE) -> DecodedAudio:
    samples = np.ones((frames, 2), dtype=np.float32) * 0.5
    return DecodedAudio(
        path=Path("test.wav"),
        samples=samples,
        sample_rate=rate,
        source_channels=2,
        source_frames=frames,
    )


class SilentPlayer(AudioPlayer):
    """An AudioPlayer that never opens a real device.

    Lets the callback and state machine be tested on a machine with no audio
    output at all.
    """

    def _ensure_stream(self, sample_rate: int) -> None:
        self._stream_rate = sample_rate


def pull(player: AudioPlayer, frames: int) -> np.ndarray:
    out = np.zeros((frames, 2), dtype=np.float32)
    player._callback(out, frames, None, None)
    return out


def test_playback_advances_and_stops_at_the_end() -> None:
    player = SilentPlayer()
    player.play(make_audio(frames=100))

    first = pull(player, 40)
    assert np.all(first == 0.5)
    assert player.position().frame == 40

    pull(player, 40)
    tail = pull(player, 40)
    # Past the end: silence, not garbage, and not a repeat.
    assert np.all(tail[20:] == 0.0)
    assert player.state is PlaybackState.STOPPED
    assert player.position().frame == 0


def test_pause_holds_position_and_outputs_silence() -> None:
    player = SilentPlayer()
    player.play(make_audio())
    pull(player, 100)
    player.pause()

    quiet = pull(player, 100)
    assert np.all(quiet == 0.0)
    assert player.position().frame == 100
    assert player.state is PlaybackState.PAUSED

    assert player.toggle() is PlaybackState.PLAYING
    resumed = pull(player, 10)
    assert np.all(resumed == 0.5)


def test_stop_rewinds() -> None:
    player = SilentPlayer()
    player.play(make_audio())
    pull(player, 500)
    player.stop()
    assert player.position().frame == 0
    assert player.state is PlaybackState.STOPPED


def test_seek_is_clamped() -> None:
    player = SilentPlayer()
    player.play(make_audio(frames=1000))

    player.seek(-50)
    assert player.position().frame == 0
    player.seek(99_999)
    assert player.position().frame == 1000
    player.seek_fraction(0.5)
    assert player.position().frame == 500


def test_playing_a_second_sample_replaces_the_first() -> None:
    player = SilentPlayer()
    player.play(make_audio(frames=1000))
    pull(player, 500)

    quiet = DecodedAudio(
        path=Path("other.wav"),
        samples=np.zeros((200, 2), dtype=np.float32),
        sample_rate=RATE,
        source_channels=2,
        source_frames=200,
    )
    player.play(quiet)

    assert player.position().frame == 0
    assert player.position().total_frames == 200
    assert np.all(pull(player, 10) == 0.0)


def test_switching_sample_rate_keeps_the_new_buffer() -> None:
    """Reopening the stream must not discard the audio that triggered it."""
    player = SilentPlayer()
    player.play(make_audio(frames=100, rate=44100))
    pull(player, 50)

    player.play(make_audio(frames=100, rate=48000))
    assert player.position().total_frames == 100
    assert np.all(pull(player, 10) == 0.5)


def test_finished_callback_fires_once_at_the_end() -> None:
    player = SilentPlayer()
    calls: list[int] = []
    player.set_finished_callback(lambda: calls.append(1))
    player.play(make_audio(frames=50))

    pull(player, 64)
    assert calls == [1]
    pull(player, 64)
    assert calls == [1]


def test_callback_is_safe_before_anything_is_played() -> None:
    player = SilentPlayer()
    assert np.all(pull(player, 32) == 0.0)
    assert player.position().total_frames == 0


# ------------------------------------------------- head-then-full extension


def test_extend_swaps_in_a_longer_buffer_without_losing_position() -> None:
    """Auditioning starts on a 3-second head so time-to-sound does not scale
    with file length; the full decode has to land without a gap."""
    player = SilentPlayer()
    head = make_audio(frames=100)
    player.play(head)
    pull(player, 60)

    full = DecodedAudio(
        path=Path("test.wav"),
        samples=np.concatenate([head.samples, np.full((900, 2), 0.25, dtype=np.float32)]),
        sample_rate=RATE,
        source_channels=2,
        source_frames=1000,
    )
    assert player.extend(full) is True

    # Same frame, still playing, and the audio past the head is now reachable.
    assert player.position().frame == 60
    assert player.position().total_frames == 1000
    assert player.state is PlaybackState.PLAYING
    assert np.all(pull(player, 40) == 0.5)
    assert np.all(pull(player, 10) == 0.25)


def test_extend_refuses_a_different_sample_rate() -> None:
    player = SilentPlayer()
    player.play(make_audio(frames=100, rate=44100))
    assert player.extend(make_audio(frames=500, rate=48000)) is False
    assert player.position().total_frames == 100


def test_extend_refuses_a_shorter_buffer() -> None:
    player = SilentPlayer()
    player.play(make_audio(frames=500))
    assert player.extend(make_audio(frames=100)) is False
    assert player.position().total_frames == 500


def test_extend_does_nothing_when_nothing_is_loaded() -> None:
    assert SilentPlayer().extend(make_audio()) is False


def test_preview_seconds_is_stable_across_decode_stages(tmp_path: Path) -> None:
    path = write_tone(tmp_path / "loop.wav", seconds=6.0)
    head = decode(path, max_seconds=3.0)
    full = decode(path)

    assert head.truncated is True
    assert head.decoded_seconds == pytest.approx(3.0, abs=0.01)
    # The transport must not read 0:03 and then jump to 0:06.
    assert head.preview_seconds == pytest.approx(full.preview_seconds, abs=0.01)
    assert head.preview_seconds == pytest.approx(6.0, abs=0.01)


def test_position_is_reported_before_any_stream_is_opened() -> None:
    """With auditioning off, nothing opens a device -- but the transport still
    has to show an honest elapsed time."""

    class NeverOpens(AudioPlayer):
        def _ensure_stream(self, sample_rate: int) -> None:
            return None

    player = NeverOpens()
    player.play(make_audio(frames=RATE * 2))
    position = player.position()

    assert position.sample_rate == RATE
    assert position.total_seconds == pytest.approx(2.0)
