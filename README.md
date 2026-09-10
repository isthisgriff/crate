# Crate

A local-first sample library manager for music producers.

Producers accumulate tens of thousands of samples across packs, downloads and
drives, with filenames like `DRM_094.wav` and `Audio_338.wav`. A file browser
cannot answer *"find me a dark, aggressive, distorted kick."* Crate is being
built so that eventually it can.

Everything stays on your machine: local database, local files, local analysis.

**Status: Phase 1 — sample library inventory.** No UI, no audio analysis, no AI
yet. See [docs/roadmap.md](docs/roadmap.md).

## What works today

Point Crate at a folder and it builds a persistent, incrementally-maintained
inventory of every supported audio file underneath it.

```bash
crate scan "D:/Samples"
crate stats
crate roots
```

Formats indexed in Phase 1: `.wav`, `.wave`, `.mp3`, `.flac`, `.aif`, `.aiff`,
`.aifc`.

## Setup

Requires Python 3.11+. [`uv`](https://docs.astral.sh/uv/) is recommended but
optional.

```bash
git clone <your-remote> crate && cd crate
uv venv
uv pip install -e ".[dev]"
```

With plain pip:

```bash
python -m venv .venv && .venv/Scripts/activate   # source .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
```

Phase 1 has **zero runtime dependencies** — only the standard library. The dev
extra installs pytest, ruff and mypy.

## Usage

```bash
crate scan PATH [PATH ...]   # index directories (safe to re-run)
crate stats                  # summary of the indexed library
crate roots                  # directories Crate knows about
crate --help
```

Useful flags:

| Flag | Purpose |
| --- | --- |
| `--data-dir DIR` | Use a different database/config location (handy for testing) |
| `--quiet` | No live progress line |
| `--show-errors N` | How many per-file failures to print after a scan |

### Where data lives

| Platform | Location |
| --- | --- |
| Windows | `%LOCALAPPDATA%\Crate\` |
| macOS / Linux | `~/.local/share/crate/` (or `$XDG_DATA_HOME/crate`) |

Override with the `CRATE_HOME` environment variable or `--data-dir`. The
directory holds `crate.db` and an optional `config.toml`:

```toml
audio_extensions = ["wav", "flac", "aiff", "mp3", "ogg"]
ignored_dir_names = ["Backups", "Bounces"]
follow_symlinks = false
scan_batch_size = 500
```

## Rescanning is cheap

Re-running a scan does **not** rebuild the database. Crate compares each file's
path, size and modification time against what it already knows and reports:

```
Scanned D:\Samples
  files found     41,208
  new             12
  changed         3
  unchanged       41,193
  now missing     0
  errors          1
```

Files that disappear are marked `missing`, never deleted — an unplugged
external drive must not wipe your library. Files under a directory that could
not be read are left alone entirely, because "unreadable" is not "deleted".

## Verifying it on a real library

Phase 1 is considered done only when it works on an actual sample library, not
just on fixtures. First run on this machine:

```
crate scan "D:/Music" "C:/Users/griff/Documents/Image-Line"
```

| | |
| --- | --- |
| Samples indexed | 35,845 |
| Total audio | 53.5 GB |
| Errors | 0 |
| Database size | 20.9 MB |
| Rescan of 35,217 files | 1.1 s (~32,000 files/s) |
| Rescan result | 0 new, 0 changed, 35,217 unchanged |

The rescan number is the one that matters: re-running the scan reuses every
existing record instead of rebuilding the inventory.

## Development

```bash
pytest              # test suite
ruff check .        # lint
ruff format .       # format
mypy                # type check (strict)
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — layers, boundaries, schema
- [docs/roadmap.md](docs/roadmap.md) — phases and their exit criteria
- [docs/decisions.md](docs/decisions.md) — design decisions and known debt

## Licence

MIT
