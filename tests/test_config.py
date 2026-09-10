from __future__ import annotations

from pathlib import Path

from crate.config import (
    DEFAULT_AUDIO_EXTENSIONS,
    Settings,
    default_data_dir,
    load_settings,
    normalize_extension,
)


def test_crate_home_overrides_platform_default(tmp_path: Path) -> None:
    resolved = default_data_dir({"CRATE_HOME": str(tmp_path / "elsewhere")})
    assert resolved == tmp_path / "elsewhere"


def test_defaults_when_no_config_file(tmp_path: Path) -> None:
    settings = load_settings(tmp_path)
    assert settings.data_dir == tmp_path
    assert settings.database_path == tmp_path / "crate.db"
    assert settings.audio_extensions == DEFAULT_AUDIO_EXTENSIONS
    assert settings.follow_symlinks is False


def test_config_file_overrides_and_normalizes(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        """
        audio_extensions = ["WAV", ".Ogg", "flac"]
        ignored_dir_names = ["Backups"]
        follow_symlinks = true
        scan_batch_size = 42
        future_setting = "kept"
        """,
        encoding="utf-8",
    )
    settings = load_settings(tmp_path)
    assert settings.audio_extensions == frozenset({".wav", ".ogg", ".flac"})
    assert settings.ignored_dir_names == frozenset({"Backups"})
    assert settings.follow_symlinks is True
    assert settings.scan_batch_size == 42
    # Unknown keys survive so a newer config does not break an older build.
    assert settings.extra["future_setting"] == "kept"


def test_normalize_extension() -> None:
    assert normalize_extension("WAV") == ".wav"
    assert normalize_extension(".AIFF") == ".aiff"
    assert normalize_extension("  .Flac ") == ".flac"
    assert normalize_extension("") == ""


def test_settings_are_immutable(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    try:
        settings.follow_symlinks = True  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Settings should be frozen")
