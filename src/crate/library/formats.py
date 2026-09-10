"""Audio format identification.

Phase 1 identifies formats by file extension only. That is a deliberate
limitation, not an oversight: opening 100k files to sniff headers would make
the first scan an order of magnitude slower, and the payoff (catching files
with wrong or missing extensions) is small on real sample libraries.

Phase 3 introduces a decoder. At that point ``probe`` gains a content-sniffing
implementation and mislabelled files get reclassified during analysis rather
than being silently skipped at scan time.
"""

from __future__ import annotations

from collections.abc import Container
from pathlib import Path


def normalized_extension(path: Path | str) -> str:
    """Return the lowercase extension including the dot (``".wav"``).

    Returns ``""`` for files with no extension. Handles the awkward real-world
    cases: ``Kick.WAV``, ``loop.wav ``  (trailing space, legal on some shares)
    and dotfiles such as ``.DS_Store``, which have no extension at all.
    """
    suffix = Path(path).suffix
    return suffix.strip().lower()


def is_supported_audio(path: Path | str, supported_extensions: Container[str]) -> bool:
    """True when ``path`` looks like an audio format Crate can index."""
    extension = normalized_extension(path)
    return bool(extension) and extension in supported_extensions
