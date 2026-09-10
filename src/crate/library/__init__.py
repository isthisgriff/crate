"""Library management: discovering files on disk and keeping the inventory current."""

from crate.library.discovery import DiscoveredFile, DiscoveryError, walk_audio_files
from crate.library.formats import is_supported_audio, normalized_extension
from crate.library.scanner import LibraryScanner

__all__ = [
    "DiscoveredFile",
    "DiscoveryError",
    "LibraryScanner",
    "is_supported_audio",
    "normalized_extension",
    "walk_audio_files",
]
