from __future__ import annotations

from pathlib import Path

import pytest

from conftest import AUDIO_FILE_COUNT
from crate.cli import main


def test_scan_then_stats(tmp_path: Path, library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"

    assert main(["--data-dir", str(data_dir), "scan", str(library), "--quiet"]) == 0
    scan_output = capsys.readouterr().out
    assert f"files found     {AUDIO_FILE_COUNT}" in scan_output

    assert main(["--data-dir", str(data_dir), "stats"]) == 0
    stats_output = capsys.readouterr().out
    assert f"samples    {AUDIO_FILE_COUNT}" in stats_output
    assert ".wav" in stats_output


def test_roots_lists_scanned_directory(
    tmp_path: Path, library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    main(["--data-dir", str(data_dir), "scan", str(library), "--quiet"])
    capsys.readouterr()

    assert main(["--data-dir", str(data_dir), "roots"]) == 0
    output = capsys.readouterr().out
    assert str(library.resolve()) in output
    assert "last scanned" in output


def test_roots_before_any_scan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--data-dir", str(tmp_path / "data"), "roots"]) == 0
    assert "No roots yet" in capsys.readouterr().out


def test_scan_of_bad_path_reports_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--data-dir", str(tmp_path / "data"), "scan", str(tmp_path / "nope"), "--quiet"])
    assert code == 1
    assert "NotADirectoryError" in capsys.readouterr().out


def test_database_is_created_under_the_data_dir(tmp_path: Path, library: Path) -> None:
    data_dir = tmp_path / "data"
    main(["--data-dir", str(data_dir), "scan", str(library), "--quiet"])
    assert (data_dir / "crate.db").is_file()
