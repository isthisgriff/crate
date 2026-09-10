"""Scanner behaviour, especially the incremental-indexing guarantees.

These are the tests that protect the Phase 1 milestone: rescanning a library
must be cheap and must not churn the database.
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import AUDIO_FILE_COUNT, FakeClock
from crate.config import Settings
from crate.domain import SampleStatus, ScanProgress
from crate.library.scanner import LibraryScanner
from crate.persistence.sample_repository import SampleRepository


def test_first_scan_indexes_every_supported_file(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    report = scanner.scan(library)

    assert report.files_seen == AUDIO_FILE_COUNT
    assert report.added == AUDIO_FILE_COUNT
    assert report.updated == 0
    assert report.unchanged == 0
    assert report.marked_missing == 0
    assert repository.count_samples() == AUDIO_FILE_COUNT


def test_rescan_of_unchanged_library_changes_nothing(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    before = {
        sample.path: (sample.id, sample.first_seen_at, sample.updated_at)
        for sample in repository.iter_samples()
    }

    report = scanner.scan(library)

    assert report.added == 0
    assert report.updated == 0
    assert report.unchanged == AUDIO_FILE_COUNT
    assert repository.count_samples() == AUDIO_FILE_COUNT

    after = {
        sample.path: (sample.id, sample.first_seen_at, sample.updated_at)
        for sample in repository.iter_samples()
    }
    # Same rows, same identities, untouched provenance: no churn.
    assert before == after


def test_rescan_creates_one_root_not_two(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    scanner.scan(library)
    scanner.scan(str(library) + os.sep)
    assert len(repository.list_roots()) == 1


def test_new_file_is_added_on_rescan(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    (library / "drums" / "clap.wav").write_bytes(b"clap")

    report = scanner.scan(library)

    assert report.added == 1
    assert report.unchanged == AUDIO_FILE_COUNT
    assert repository.count_samples() == AUDIO_FILE_COUNT + 1


def test_modified_file_is_updated_not_duplicated(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    target = library / "bass" / "808_new.flac"
    original = repository.get_sample_by_path(target)
    assert original is not None

    target.write_bytes(b"a much longer eight-oh-eight")
    os.utime(target, ns=(original.mtime_ns + 10**9, original.mtime_ns + 10**9))

    report = scanner.scan(library)

    assert report.updated == 1
    assert report.added == 0
    assert repository.count_samples() == AUDIO_FILE_COUNT

    reloaded = repository.get_sample_by_path(target)
    assert reloaded is not None
    assert reloaded.id == original.id
    assert reloaded.size_bytes == len(b"a much longer eight-oh-eight")
    assert reloaded.first_seen_at == original.first_seen_at
    assert reloaded.updated_at > original.updated_at


def test_deleted_file_is_marked_missing_and_kept(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    target = library / "vocals" / "Audio_338.mp3"
    target.unlink()

    report = scanner.scan(library)

    assert report.marked_missing == 1
    assert repository.count_samples() == AUDIO_FILE_COUNT
    sample = repository.get_sample_by_path(target)
    assert sample is not None
    assert sample.status is SampleStatus.MISSING


def test_restored_file_becomes_present_again(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    target = library / "vocals" / "Audio_338.mp3"
    payload = target.read_bytes()
    target.unlink()
    scanner.scan(library)

    target.write_bytes(payload)
    report = scanner.scan(library)

    assert report.updated == 1
    sample = repository.get_sample_by_path(target)
    assert sample is not None
    assert sample.status is SampleStatus.PRESENT


def test_missing_is_not_re_marked_on_later_scans(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    (library / "vocals" / "Audio_338.mp3").unlink()
    assert scanner.scan(library).marked_missing == 1
    # Already accounted for; a third scan must not report it again.
    assert scanner.scan(library).marked_missing == 0


def test_unreadable_directory_does_not_mark_its_files_missing(
    repository: SampleRepository, settings: Settings, clock: FakeClock, tmp_path: Path
) -> None:
    """The important safety property: a folder we could not read is *unknown*.

    Marking its contents deleted would orphan every piece of metadata attached
    to them in later phases.
    """
    root = tmp_path / "lib"
    (root / "readable").mkdir(parents=True)
    (root / "readable" / "kick.wav").write_bytes(b"k")
    locked = root / "locked"
    locked.mkdir()
    (locked / "snare.wav").write_bytes(b"s")

    scanner = LibraryScanner(repository, settings, clock=clock)
    scanner.scan(root)
    assert repository.count_samples() == 2

    if os.name == "nt":
        pytest.skip("POSIX permission semantics")

    locked.chmod(0o000)
    try:
        report = scanner.scan(root)
    finally:
        locked.chmod(0o755)

    assert report.errors == 1
    assert report.marked_missing == 0
    snare = repository.get_sample_by_path(locked / "snare.wav")
    assert snare is not None
    assert snare.status is SampleStatus.PRESENT


def test_similarly_named_sibling_directory_is_not_shielded(
    repository: SampleRepository, settings: Settings, clock: FakeClock, tmp_path: Path
) -> None:
    """`/lib/kicks` failing must not protect `/lib/kicks_old` from reconciliation."""
    root = tmp_path / "lib"
    (root / "kicks").mkdir(parents=True)
    (root / "kicks_old").mkdir()
    (root / "kicks" / "a.wav").write_bytes(b"a")
    (root / "kicks_old" / "b.wav").write_bytes(b"b")

    scanner = LibraryScanner(repository, settings, clock=clock)
    scanner.scan(root)

    if os.name == "nt":
        pytest.skip("POSIX permission semantics")

    (root / "kicks_old" / "b.wav").unlink()
    (root / "kicks").chmod(0o000)
    try:
        report = scanner.scan(root)
    finally:
        (root / "kicks").chmod(0o755)

    assert report.marked_missing == 1


def test_scan_of_nonexistent_root_records_error_without_raising(
    scanner: LibraryScanner, repository: SampleRepository, tmp_path: Path
) -> None:
    report = scanner.scan(tmp_path / "not-here")

    assert report.files_seen == 0
    assert report.errors == 1
    assert report.marked_missing == 0
    errors = repository.list_scan_errors(report.run_id)
    assert errors[0].error_type == "NotADirectoryError"


def test_scan_run_is_recorded(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    report = scanner.scan(library)
    roots = repository.list_roots()
    run = repository.latest_scan_run(roots[0].id)

    assert run is not None
    assert run.id == report.run_id
    assert run.status == "completed"
    assert run.files_seen == AUDIO_FILE_COUNT
    assert run.finished_at is not None
    assert roots[0].last_scanned_at is not None


def test_progress_is_reported(
    repository: SampleRepository, settings: Settings, clock: FakeClock, library: Path
) -> None:
    seen: list[ScanProgress] = []
    scanner = LibraryScanner(repository, settings, clock=clock)
    scanner.scan(library, progress=seen.append, progress_every=2)

    assert seen, "expected at least one progress callback"
    assert seen[-1].files_seen == AUDIO_FILE_COUNT
    assert seen[-1].indexed == AUDIO_FILE_COUNT
    assert [snapshot.files_seen for snapshot in seen] == sorted(
        snapshot.files_seen for snapshot in seen
    )


def test_cancellation_keeps_partial_work_and_marks_nothing_missing(
    repository: SampleRepository, clock: FakeClock, tmp_path: Path, library: Path
) -> None:
    settings = Settings(data_dir=tmp_path / "data", scan_batch_size=2)
    scanner = LibraryScanner(repository, settings, clock=clock)

    report = scanner.scan(library, cancel=lambda: True)

    assert report.cancelled is True
    assert report.marked_missing == 0
    # Everything flushed before the cancel point is durable.
    assert 0 < repository.count_samples() < AUDIO_FILE_COUNT

    run = repository.latest_scan_run(repository.list_roots()[0].id)
    assert run is not None
    assert run.status == "cancelled"

    # A cancelled scan leaves the library resumable, not corrupt.
    resumed = scanner.scan(library)
    assert repository.count_samples() == AUDIO_FILE_COUNT
    assert resumed.cancelled is False


def test_unicode_and_spaced_filenames_round_trip(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    unicode_sample = repository.get_sample_by_path(library / "unicode" / "kick_éü你好.wav")
    spaced = repository.get_sample_by_path(library / "drums" / "snares" / "snare one.aiff")

    assert unicode_sample is not None
    assert unicode_sample.filename == "kick_éü你好.wav"
    assert spaced is not None
    assert spaced.extension == ".aiff"


def test_uppercase_extension_is_stored_normalized(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    scanner.scan(library)
    sample = repository.get_sample_by_path(library / "drums" / "kicks" / "DRM_094.WAV")
    assert sample is not None
    assert sample.extension == ".wav"
    assert sample.filename == "DRM_094.WAV"


def test_scanning_a_subdirectory_does_not_duplicate_samples(
    scanner: LibraryScanner, repository: SampleRepository, library: Path
) -> None:
    """Overlapping roots are a normal user mistake, not a corruption event."""
    scanner.scan(library)
    scanner.scan(library / "drums")

    assert repository.count_samples() == AUDIO_FILE_COUNT
    assert len(repository.list_roots()) == 2


def test_errors_are_capped(repository: SampleRepository, clock: FakeClock, tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d", scan_batch_size=1, max_scan_errors_recorded=1)
    scanner = LibraryScanner(repository, settings, clock=clock)
    report = scanner.scan(tmp_path / "missing-one")
    assert report.errors == 1
    assert len(repository.list_scan_errors(report.run_id)) == 1


def test_unreadable_directory_safety_holds_on_every_platform(
    repository: SampleRepository,
    settings: Settings,
    clock: FakeClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same property as the POSIX chmod test, without needing POSIX.

    Simulates a directory that stops being listable (permissions revoked, a
    network share dropping, a drive spinning down) by making `os.scandir`
    fail for exactly that path.
    """
    root = tmp_path / "lib"
    (root / "readable").mkdir(parents=True)
    (root / "readable" / "kick.wav").write_bytes(b"k")
    locked = root / "locked"
    locked.mkdir()
    (locked / "snare.wav").write_bytes(b"s")

    scanner = LibraryScanner(repository, settings, clock=clock)
    scanner.scan(root)
    assert repository.count_samples() == 2

    real_scandir = os.scandir

    def failing_scandir(path):  # type: ignore[no-untyped-def]
        if Path(path) == locked.resolve():
            raise PermissionError(13, "Access is denied")
        return real_scandir(path)

    monkeypatch.setattr("crate.library.discovery.os.scandir", failing_scandir)
    report = scanner.scan(root)

    assert report.errors == 1
    assert report.marked_missing == 0
    snare = repository.get_sample_by_path(locked / "snare.wav")
    assert snare is not None
    assert snare.status is SampleStatus.PRESENT


def test_renamed_folder_is_detected_as_a_move(
    scanner: LibraryScanner, repository: SampleRepository, tmp_path: Path
) -> None:
    """Reorganising must not throw away row identity (and with it favourites)."""
    root = tmp_path / "lib"
    (root / "Pack A").mkdir(parents=True)
    (root / "Pack A" / "kick.wav").write_bytes(b"kick")
    (root / "Pack A" / "snare.wav").write_bytes(b"snare")

    scanner.scan(root)
    before = {sample.filename: sample.id for sample in repository.iter_samples()}
    favourite_id = before["kick.wav"]
    repository.set_favorite(favourite_id, True, datetime(2026, 1, 1, tzinfo=UTC))

    (root / "Pack A").rename(root / "Pack A - Drums")
    report = scanner.scan(root)

    assert report.moved == 2
    assert report.added == 0
    assert report.marked_missing == 0
    assert repository.count_samples() == 2

    moved = repository.get_sample_by_path(root / "Pack A - Drums" / "kick.wav")
    assert moved is not None
    assert moved.id == favourite_id
    assert moved.favorite is True
    assert moved.status is SampleStatus.PRESENT


def test_move_requires_name_size_and_mtime_to_agree(
    scanner: LibraryScanner, repository: SampleRepository, tmp_path: Path
) -> None:
    root = tmp_path / "lib"
    (root / "a").mkdir(parents=True)
    (root / "a" / "kick.wav").write_bytes(b"kick")

    scanner.scan(root)

    (root / "b").mkdir()
    (root / "b" / "kick.wav").write_bytes(b"a different kick entirely")
    (root / "a" / "kick.wav").unlink()

    report = scanner.scan(root)

    # Same name, different bytes: a new file plus a deletion, not a move.
    assert report.moved == 0
    assert report.added == 1
    assert report.marked_missing == 1


def test_copying_a_file_is_not_a_move(
    scanner: LibraryScanner, repository: SampleRepository, tmp_path: Path
) -> None:
    root = tmp_path / "lib"
    (root / "a").mkdir(parents=True)
    source = root / "a" / "kick.wav"
    source.write_bytes(b"kick")

    scanner.scan(root)

    (root / "b").mkdir()
    shutil.copy2(source, root / "b" / "kick.wav")
    report = scanner.scan(root)

    # The original is still there, so nothing went missing and nothing moved.
    assert report.moved == 0
    assert report.added == 1
    assert report.marked_missing == 0
    assert repository.count_samples() == 2


def test_macos_sidecar_files_are_skipped(
    scanner: LibraryScanner, repository: SampleRepository, tmp_path: Path
) -> None:
    """Unzipping a Mac-authored pack on Windows litters these everywhere.

    They carry a .wav extension, are ~200 bytes, and are not audio.
    """
    root = tmp_path / "pack"
    (root / "__MACOSX" / "pack").mkdir(parents=True)
    (root / "Kick.wav").write_bytes(b"real audio")
    (root / "._Kick.wav").write_bytes(b"\x00\x05\x16\x07resource fork")
    (root / "__MACOSX" / "pack" / "._Snare.wav").write_bytes(b"resource fork")

    report = scanner.scan(root)

    assert report.files_seen == 1
    assert repository.count_samples() == 1
    sample = repository.iter_samples()[0]
    assert sample.filename == "Kick.wav"


def test_real_files_starting_with_a_dot_are_not_confused(
    scanner: LibraryScanner, repository: SampleRepository, tmp_path: Path
) -> None:
    root = tmp_path / "pack"
    root.mkdir()
    (root / ".hidden_kick.wav").write_bytes(b"audio")
    scanner.scan(root)
    # Only the `._` sidecar prefix is junk; a plain dotfile is a real file.
    assert repository.count_samples() == 1
