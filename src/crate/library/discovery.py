"""Recursive filesystem discovery of audio files.

This module is pure traversal: it yields what it finds and what went wrong,
and it never touches the database. Keeping it separate means the messy
real-world edge cases (permission errors, symlink loops, files that vanish
mid-walk, network shares that time out) are testable without SQLite.

Design notes
------------
* ``os.scandir`` over ``os.walk``/``Path.rglob``: it exposes ``DirEntry``,
  which caches ``stat`` results from the directory read on Windows. That is
  the single biggest win when walking a large library.
* Errors are *yielded*, not raised. A library with one unreadable folder
  should still index everything else.
* Traversal is iterative, not recursive, so a pathologically deep tree cannot
  blow the Python stack.
"""

from __future__ import annotations

import os
from collections.abc import Container, Iterator
from dataclasses import dataclass
from pathlib import Path

from crate.library.formats import is_supported_audio, normalized_extension

APPLEDOUBLE_PREFIX = "._"
"""macOS resource-fork sidecars.

Unzipping a Mac-authored sample pack on Windows scatters these everywhere: a
200-byte `._Kick.wav` beside every real `Kick.wav`. They carry the right
extension and are not audio. On this machine they were 2.1% of the library --
enough to be a nuisance in every search result and every "find similar" list
later on."""


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """A supported audio file found on disk, with its filesystem metadata."""

    path: Path
    size_bytes: int
    mtime_ns: int

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def extension(self) -> str:
        return normalized_extension(self.path)


@dataclass(frozen=True, slots=True)
class DiscoveryError:
    """A path that could not be read. Recorded and skipped, never fatal."""

    path: Path
    error_type: str
    message: str
    is_directory: bool = False
    """True when a *directory* could not be listed.

    The scanner needs this: files beneath an unreadable directory were never
    discovered, and must not be mistaken for deleted files."""

    @classmethod
    def from_exception(
        cls, path: Path, exc: BaseException, *, is_directory: bool = False
    ) -> DiscoveryError:
        return cls(
            path=path,
            error_type=type(exc).__name__,
            message=str(exc) or repr(exc),
            is_directory=is_directory,
        )


DiscoveryEvent = DiscoveredFile | DiscoveryError


def walk_audio_files(
    root: Path,
    *,
    supported_extensions: Container[str],
    ignored_dir_names: Container[str] = (),
    follow_symlinks: bool = False,
) -> Iterator[DiscoveryEvent]:
    """Walk ``root`` depth-first, yielding audio files and per-path failures.

    Args:
        root: Directory to walk. Yields a single :class:`DiscoveryError` if it
            is not a readable directory.
        supported_extensions: Extensions (``".wav"`` form) Crate indexes.
        ignored_dir_names: Directory *names* skipped anywhere in the tree.
        follow_symlinks: Off by default. Sample libraries with symlinked packs
            would otherwise be indexed twice; when enabled, ``(device, inode)``
            bookkeeping prevents infinite loops.
    """
    root = Path(root)
    if not root.is_dir():
        yield DiscoveryError(
            path=root,
            error_type="NotADirectoryError",
            message=f"{root} is not a directory or is not reachable",
            is_directory=True,
        )
        return

    visited: set[tuple[int, int]] = set()
    stack: list[Path] = [root]

    while stack:
        directory = stack.pop()

        if follow_symlinks:
            key = _identity(directory)
            if key is not None:
                if key in visited:
                    continue
                visited.add(key)

        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            yield DiscoveryError.from_exception(directory, exc, is_directory=True)
            continue

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=follow_symlinks):
                    if entry.name not in ignored_dir_names:
                        stack.append(Path(entry.path))
                    continue

                if not entry.is_file(follow_symlinks=follow_symlinks):
                    continue

                if entry.name.startswith(APPLEDOUBLE_PREFIX):
                    continue

                if not is_supported_audio(entry.path, supported_extensions):
                    continue

                info = entry.stat(follow_symlinks=follow_symlinks)
                yield DiscoveredFile(
                    path=Path(entry.path),
                    size_bytes=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                )
            except OSError as exc:
                # Deleted mid-walk, permission denied, broken symlink, name too
                # long, network share dropped: all normal, none fatal.
                yield DiscoveryError.from_exception(Path(entry.path), exc)
            except ValueError as exc:
                # Windows raises this for some malformed paths before the OS
                # is ever consulted.
                yield DiscoveryError.from_exception(Path(entry.path), exc)


def _identity(directory: Path) -> tuple[int, int] | None:
    """Return ``(device, inode)`` for symlink-loop detection, or None."""
    try:
        info = directory.stat()
    except OSError:
        return None
    if not info.st_ino:
        return None
    return (info.st_dev, info.st_ino)
