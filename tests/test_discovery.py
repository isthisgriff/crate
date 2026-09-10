from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from crate.config import DEFAULT_AUDIO_EXTENSIONS, DEFAULT_IGNORED_DIR_NAMES
from crate.library.discovery import DiscoveredFile, DiscoveryError, walk_audio_files


def walk(root: Path, **kwargs: object) -> list[object]:
    return list(
        walk_audio_files(
            root,
            supported_extensions=DEFAULT_AUDIO_EXTENSIONS,
            ignored_dir_names=DEFAULT_IGNORED_DIR_NAMES,
            **kwargs,  # type: ignore[arg-type]
        )
    )


def test_finds_audio_recursively_and_skips_other_files(library: Path) -> None:
    events = walk(library)
    files = [event for event in events if isinstance(event, DiscoveredFile)]
    names = {file.filename for file in files}

    assert names == {
        "Kick_Final_2.wav",
        "DRM_094.WAV",
        "snare one.aiff",
        "808_new.flac",
        "Audio_338.mp3",
        "kick_éü你好.wav",
        "silence.wav",
    }
    assert "notes.txt" not in names
    assert "cover.png" not in names


def test_ignored_directories_are_not_descended(library: Path) -> None:
    files = [event for event in walk(library) if isinstance(event, DiscoveredFile)]
    assert all(".git" not in file.path.parts for file in files)


def test_filesystem_metadata_is_captured(library: Path) -> None:
    files = {file.filename: file for file in walk(library) if isinstance(file, DiscoveredFile)}
    kick = files["Kick_Final_2.wav"]
    on_disk = (library / "drums" / "kicks" / "Kick_Final_2.wav").stat()

    assert kick.size_bytes == on_disk.st_size == 4
    assert kick.mtime_ns == on_disk.st_mtime_ns
    assert kick.extension == ".wav"


def test_zero_byte_file_is_still_inventoried(library: Path) -> None:
    files = {file.filename: file for file in walk(library) if isinstance(file, DiscoveredFile)}
    # A 0-byte file is a real thing in real libraries. Phase 1 inventories it;
    # Phase 3 analysis is where it gets flagged as undecodable.
    assert files["silence.wav"].size_bytes == 0


def test_missing_root_yields_error_not_exception(tmp_path: Path) -> None:
    events = walk(tmp_path / "does-not-exist")
    assert len(events) == 1
    error = events[0]
    assert isinstance(error, DiscoveryError)
    assert error.is_directory is True


def test_file_as_root_yields_error(tmp_path: Path) -> None:
    target = tmp_path / "kick.wav"
    target.write_bytes(b"x")
    events = walk(target)
    assert [type(event) for event in events] == [DiscoveryError]


def test_empty_directory_yields_nothing(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert walk(tmp_path / "empty") == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
def test_unreadable_directory_is_reported_and_scan_continues(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    (root / "ok").mkdir(parents=True)
    (root / "ok" / "kick.wav").write_bytes(b"k")
    locked = root / "locked"
    locked.mkdir()
    (locked / "hidden.wav").write_bytes(b"h")
    locked.chmod(0o000)
    try:
        events = walk(root)
    finally:
        locked.chmod(0o755)

    files = [event for event in events if isinstance(event, DiscoveredFile)]
    errors = [event for event in events if isinstance(event, DiscoveryError)]
    assert [file.filename for file in files] == ["kick.wav"]
    assert len(errors) == 1
    assert errors[0].is_directory is True


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_symlink_loop_terminates(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    (root / "kick.wav").write_bytes(b"k")
    (root / "loop").symlink_to(root, target_is_directory=True)

    events = walk(root, follow_symlinks=True)
    files = [event for event in events if isinstance(event, DiscoveredFile)]
    assert len(files) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_symlinks_not_followed_by_default(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "kick.wav").write_bytes(b"k")
    root = tmp_path / "lib"
    root.mkdir()
    (root / "linked").symlink_to(real, target_is_directory=True)

    files = [event for event in walk(root) if isinstance(event, DiscoveredFile)]
    assert files == []


def test_deep_tree_does_not_blow_the_stack(tmp_path: Path) -> None:
    current = tmp_path / "deep"
    current.mkdir()
    for level in range(200):
        current = current / f"level{level}"
        current.mkdir()
    (current / "kick.wav").write_bytes(b"k")

    files = [event for event in walk(tmp_path / "deep") if isinstance(event, DiscoveredFile)]
    assert len(files) == 1
