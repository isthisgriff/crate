from __future__ import annotations

from crate.config import DEFAULT_AUDIO_EXTENSIONS
from crate.library.formats import is_supported_audio, normalized_extension


def test_extension_is_case_insensitive() -> None:
    assert normalized_extension("Kick_Final_2.WAV") == ".wav"
    assert normalized_extension("loop.AiFf") == ".aiff"


def test_files_without_extension() -> None:
    assert normalized_extension("README") == ""
    assert normalized_extension(".DS_Store") == ""
    assert is_supported_audio("README", DEFAULT_AUDIO_EXTENSIONS) is False


def test_supported_and_unsupported() -> None:
    assert is_supported_audio("808_new.flac", DEFAULT_AUDIO_EXTENSIONS) is True
    assert is_supported_audio("cover.png", DEFAULT_AUDIO_EXTENSIONS) is False
    assert is_supported_audio("project.flp", DEFAULT_AUDIO_EXTENSIONS) is False


def test_dots_in_filename_do_not_confuse_detection() -> None:
    assert normalized_extension("kick.v2.final.wav") == ".wav"
    assert is_supported_audio("kick.v2.final.wav", DEFAULT_AUDIO_EXTENSIONS) is True
