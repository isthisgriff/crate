"""Command-line interface.

Phase 1 has no GUI, so this is how the milestone gets demonstrated. It stays
useful afterwards: scanning a 200k-file drive from a terminal is a better
first experience than watching a progress bar, and it keeps the core testable
without a UI framework.

The CLI is a thin shell. All behaviour lives in the library/persistence layers.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from crate import __version__
from crate.app import CrateApp
from crate.domain import SampleStatus, ScanProgress, ScanReport
from crate.persistence.migrations import SCHEMA_VERSION


def _human_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:,.1f} TB"


class _ConsoleProgress:
    """Throttled single-line progress output on stderr."""

    def __init__(self, *, enabled: bool, min_interval: float = 0.1) -> None:
        self._enabled = enabled
        self._min_interval = min_interval
        self._last = 0.0
        self._width = 0

    def __call__(self, progress: ScanProgress) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        if now - self._last < self._min_interval:
            return
        self._last = now
        line = (
            f"  scanned {progress.files_seen:,}  "
            f"new {progress.added:,}  changed {progress.updated:,}  "
            f"errors {progress.errors:,}"
        )
        self._width = max(self._width, len(line))
        sys.stderr.write("\r" + line.ljust(self._width))
        sys.stderr.flush()

    def done(self) -> None:
        if self._enabled and self._width:
            sys.stderr.write("\r" + " " * self._width + "\r")
            sys.stderr.flush()


def _print_report(report: ScanReport) -> None:
    verb = "Cancelled" if report.cancelled else "Scanned"
    rate = report.files_seen / report.duration_seconds if report.duration_seconds > 0 else 0.0
    print(f"{verb} {report.root.path}")
    print(f"  files found     {report.files_seen:,}")
    print(f"  new             {report.added:,}")
    print(f"  changed         {report.updated:,}")
    print(f"  unchanged       {report.unchanged:,}")
    print(f"  moved           {report.moved:,}")
    print(f"  now missing     {report.marked_missing:,}")
    print(f"  errors          {report.errors:,}")
    print(f"  took            {report.duration_seconds:.2f}s ({rate:,.0f} files/s)")


def cmd_scan(args: argparse.Namespace) -> int:
    with CrateApp.open(args.data_dir) as app:
        scanner = app.scanner()
        exit_code = 0
        for raw_path in args.paths:
            reporter = _ConsoleProgress(enabled=not args.quiet and sys.stderr.isatty())
            report = scanner.scan(Path(raw_path), progress=reporter)
            reporter.done()
            _print_report(report)

            if report.errors:
                shown = app.samples.list_scan_errors(report.run_id, limit=args.show_errors)
                if shown:
                    print(f"  first {len(shown)} error(s):")
                    for error in shown:
                        print(f"    [{error.error_type}] {error.path}: {error.message}")
                if report.files_seen == 0:
                    exit_code = 1
            print()
        return exit_code


def cmd_stats(args: argparse.Namespace) -> int:
    with CrateApp.open(args.data_dir) as app:
        stats = app.samples.stats()
        print(f"database   {app.settings.database_path}")
        print(f"schema     v{SCHEMA_VERSION}")
        print(f"samples    {stats.total_samples:,}")
        print(f"  present  {stats.present_samples:,}")
        print(f"  missing  {stats.missing_samples:,}")
        print(f"size       {_human_bytes(stats.total_bytes)}")
        if stats.by_extension:
            print("by format")
            for extension, count in stats.by_extension.items():
                print(f"  {extension:<8} {count:,}")
    return 0


def cmd_roots(args: argparse.Namespace) -> int:
    with CrateApp.open(args.data_dir) as app:
        roots = app.samples.list_roots()
        if not roots:
            print("No roots yet. Run: crate scan <directory>")
            return 0
        for root in roots:
            last = (
                root.last_scanned_at.isoformat(timespec="seconds")
                if root.last_scanned_at
                else "never"
            )
            count = app.samples.count_samples(root_id=root.id)
            print(f"{root.path}\n  samples {count:,}  last scanned {last}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Delete records for files that are gone.

    Separate from scanning on purpose: a scan flags absent files, it never
    deletes them, because the usual reason a file is absent is an unplugged
    drive. Removing the records is the user's call, so it needs this command
    and an explicit --yes.
    """
    with CrateApp.open(args.data_dir) as app:
        missing = app.samples.count_samples(status=SampleStatus.MISSING)
        if not missing:
            print("Nothing to prune: no samples are marked missing.")
            return 0
        if not args.yes:
            print(f"{missing:,} sample(s) are marked missing.")
            print("Re-run with --yes to delete their records. Files on disk are untouched.")
            return 0
        removed = app.samples.delete_missing()
        print(f"Removed {removed:,} record(s). No files were deleted.")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Open the desktop browser."""
    try:
        from crate.ui import run
    except ImportError:
        print("The desktop UI needs PySide6. Install it with:", file=sys.stderr)
        print('    pip install "crate[ui]"', file=sys.stderr)
        return 1

    with CrateApp.open(args.data_dir) as app:
        return run(app)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crate",
        description="Crate - local-first sample library manager.",
    )
    parser.add_argument("--version", action="version", version=f"crate {__version__}")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the Crate data directory (database + config).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Index one or more directories.")
    scan.add_argument("paths", nargs="+", help="Directories to scan recursively.")
    scan.add_argument("--quiet", action="store_true", help="Suppress live progress.")
    scan.add_argument(
        "--show-errors",
        type=int,
        default=10,
        metavar="N",
        help="How many per-file errors to print (default: 10).",
    )
    scan.set_defaults(func=cmd_scan)

    stats = subparsers.add_parser("stats", help="Summarize the indexed library.")
    stats.set_defaults(func=cmd_stats)

    roots = subparsers.add_parser("roots", help="List indexed directories.")
    roots.set_defaults(func=cmd_roots)

    prune = subparsers.add_parser("prune", help="Delete records for files that no longer exist.")
    prune.add_argument("--yes", action="store_true", help="Actually delete (default: report).")
    prune.set_defaults(func=cmd_prune)

    ui = subparsers.add_parser("ui", help="Open the desktop sample browser.")
    ui.set_defaults(func=cmd_ui)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result: int = args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return result


if __name__ == "__main__":
    raise SystemExit(main())
