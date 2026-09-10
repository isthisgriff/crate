"""Application configuration.

Settings are resolved from three sources, later sources winning:

1. Built-in defaults (this module).
2. ``config.toml`` inside the Crate data directory.
3. The ``CRATE_HOME`` environment variable (data directory only).

Nothing else in the codebase is allowed to read environment variables or
guess platform paths; everything takes a :class:`Settings` instance.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {".wav", ".wave", ".mp3", ".flac", ".aif", ".aiff", ".aifc"}
)
"""Formats Crate indexes in Phase 1.

Extension-based detection is deliberate: it is cheap and lets a scan of
100k files stay I/O bound. Phase 3 adds header sniffing when the decoder
is introduced, at which point files that lie about their extension get
reclassified rather than skipped.
"""

DEFAULT_IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "__pycache__",
        "node_modules",
        ".Trash",
        "$RECYCLE.BIN",
        "System Volume Information",
        ".Spotlight-V100",
        ".fseventsd",
    }
)

APP_DIR_NAME = "Crate"


def default_data_dir(environ: dict[str, str] | None = None) -> Path:
    """Return the per-user directory holding the database and config."""
    env = os.environ if environ is None else environ

    override = env.get("CRATE_HOME")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        if base:
            return Path(base) / APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / APP_DIR_NAME

    xdg = env.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "crate"
    return Path.home() / ".local" / "share" / "crate"


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable, explicitly-passed application settings."""

    data_dir: Path
    audio_extensions: frozenset[str] = DEFAULT_AUDIO_EXTENSIONS
    ignored_dir_names: frozenset[str] = DEFAULT_IGNORED_DIR_NAMES
    follow_symlinks: bool = False
    scan_batch_size: int = 500
    """Rows written per transaction during a scan.

    Bounds memory, bounds how much work is lost if the process dies mid-scan,
    and gives the UI a natural progress tick. Measured, not guessed, later.
    """

    max_scan_errors_recorded: int = 10_000

    extra: dict[str, object] = field(default_factory=dict)
    """Unknown keys from config.toml, kept so a newer config does not break an
    older build (and so tests can assert we did not silently drop them)."""

    @property
    def database_path(self) -> Path:
        return self.data_dir / "crate.db"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.toml"


_KNOWN_KEYS = {
    "audio_extensions",
    "ignored_dir_names",
    "follow_symlinks",
    "scan_batch_size",
    "max_scan_errors_recorded",
}


def normalize_extension(value: str) -> str:
    """Normalize ``WAV`` / ``.WAV`` / ``wav`` to ``.wav``."""
    text = value.strip().lower()
    if not text:
        return ""
    return text if text.startswith(".") else f".{text}"


def load_settings(
    data_dir: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> Settings:
    """Build :class:`Settings`, layering ``config.toml`` over the defaults."""
    resolved_dir = Path(data_dir) if data_dir is not None else default_data_dir(environ)
    settings = Settings(data_dir=resolved_dir)

    config_file = settings.config_path
    if not config_file.is_file():
        return settings

    with config_file.open("rb") as handle:
        raw = tomllib.load(handle)

    extensions = settings.audio_extensions
    if isinstance(raw.get("audio_extensions"), list):
        extensions = frozenset(
            normalize_extension(str(item)) for item in raw["audio_extensions"]
        ) - {""}

    ignored = settings.ignored_dir_names
    if isinstance(raw.get("ignored_dir_names"), list):
        ignored = frozenset(str(item) for item in raw["ignored_dir_names"])

    return Settings(
        data_dir=resolved_dir,
        audio_extensions=extensions,
        ignored_dir_names=ignored,
        follow_symlinks=bool(raw.get("follow_symlinks", settings.follow_symlinks)),
        scan_batch_size=int(raw.get("scan_batch_size", settings.scan_batch_size)),
        max_scan_errors_recorded=int(
            raw.get("max_scan_errors_recorded", settings.max_scan_errors_recorded)
        ),
        extra={key: value for key, value in raw.items() if key not in _KNOWN_KEYS},
    )
