# Architecture

## Layers

Dependencies point strictly downward. Nothing below a line knows anything
about what is above it.

```
                     ui/           PySide6: window, table model, waveform
                     |             (the only package that imports Qt)
                    app.py         composition root: wires everything
                     |
        +------------+------------+-------------------+
        |            |            |                   |
     library/     audio/      persistence/     (future: analysis/,
     discovery,   decode,     SQLite,           ml/, search/)
     scanner      playback,   migrations,
                  peaks       repositories
        |            |            |
        +------------+------------+
                     |
                  domain/          plain dataclasses, zero imports
```

| Package | Responsibility | Must not |
| --- | --- | --- |
| `crate.domain` | Entities and value objects | Import anything from Crate, or do I/O |
| `crate.persistence` | Schema, migrations, repositories | Know about the filesystem or the UI |
| `crate.library` | Find files, decide what changed | Contain SQL, or know about the UI |
| `crate.audio` | Decode, compute peaks, play | Import Qt, or touch the database |
| `crate.ui` | Present, and only present | Contain business logic |
| `crate.config` | Resolve settings once, explicitly | Be read from anywhere but the composition root |
| `crate.app` | Wire the object graph | Contain business logic |
| `crate.cli` | Present results to a terminal | Contain business logic |

Two rules do most of the work:

1. **No global state.** Every collaborator is passed in. `LibraryScanner` takes
   a repository, settings and a clock. That is why the tests need no mocking
   framework and why timestamps are deterministic.
2. **The layer that knows about SQL is the only one that knows about SQL.**
   `SampleRepository` returns domain objects, never `sqlite3.Row`.

## Why this shape

The roadmap ends with neural embeddings, a vector index and semantic search.
None of that exists yet, and guessing at it now would be wrong. What these
boundaries buy is the ability to add it later without a rewrite:

- Audio analysis becomes `crate.analysis`, sitting beside `crate.library` and
  writing to its own tables. The inventory does not change.
- The Phase 2 UI calls `LibraryScanner.scan()` on a worker thread and renders
  the `ScanProgress` snapshots. The scanner needs no change to support this —
  it is already synchronous, cancellable and free of UI types.
- If walking a 500k-file library ever becomes the bottleneck,
  `crate.library.discovery` is ~120 lines with one entry point and no
  dependencies. It is the one module in the system that could sensibly be
  rewritten in Rust, and nothing else would notice.

## Data model (schema v2)

Phases 1–2 implement the *identity* and *user organization* branches of the
sample model. Audio properties, musical properties, analysis features and AI
metadata get their own tables in later phases, so that re-running analysis
never rewrites the inventory and losing an embedding model never costs you
your library.

```
roots                       directories the user asked Crate to index
  id, path (unique), added_at, last_scanned_at

samples                     the inventory
  id, root_id -> roots
  path (unique), filename, extension
  size_bytes, mtime_ns      the change-detection fingerprint
  content_hash              NULL until Phase 3 needs it
  status                    present | missing
  favorite, favorited_at    user organization; scans never write these
  first_seen_at, last_seen_at, updated_at

scan_runs                   one row per scan, for progress and history
  id, root_id -> roots, started_at, finished_at
  status                    running | completed | cancelled | failed
  files_seen, added, updated, unchanged, marked_missing, errors

scan_errors                 per-path failures, attached to the run that saw them
  id, scan_run_id -> scan_runs, path, error_type, message, occurred_at

schema_migrations           applied migration versions
```

Indexes: `root_id`, `extension`, `status`, `filename`, `size_bytes`,
`mtime_ns`, a partial index on `favorite = 1`, and `(size_bytes, mtime_ns)`
for move detection.

Timestamps are ISO-8601 UTC text. SQLite has no datetime type; text sorts
correctly, is human-readable in a database browser, and round-trips through
`datetime.fromisoformat`.

### Migrations

Append-only. Never edit a shipped migration — add a new one. Each runs inside
a single transaction, so a failure leaves the database exactly as it was.

`Connection.executescript` commits any open transaction before it runs, which
would silently break that guarantee, so migrations execute statement by
statement via `split_statements`.

## Change detection

```
for each file found on disk:
    known = index[path]                # loaded once, in bulk
    if known is None:                  -> ADDED
    elif (size, mtime_ns) differ:      -> UPDATED  (and content_hash cleared)
    else:                              -> UNCHANGED (last_seen_at only)

after the walk:
    rows not seen, matched to a new row by
        (size, mtime, filename)                    -> MOVED  (old row re-pointed)
    remaining rows not seen, not already missing,
    and not under a directory that failed to list  -> MISSING
```

`(path, size, mtime_ns)` is the fingerprint. It needs no file reads, so
rescanning an unchanged library costs one directory walk. It can be fooled by a
file rewritten to the same byte length within the filesystem's timestamp
resolution; content hashing is deferred to Phase 3, where it will be computed
for the files queued for analysis rather than for all of them at scan time.

### Safety properties the scanner guarantees

These are load-bearing once samples carry favourites, tags and embeddings.

- **Nothing is ever deleted.** A vanished file becomes `missing`. Unplugging an
  external drive must not wipe an inventory.
- **Unreadable is not deleted.** Files under a directory that failed to list
  were never examined, so they are left alone. Reporting them as missing would
  orphan their metadata.
- **Rescans do not churn.** Unchanged rows keep their `id`, `first_seen_at` and
  `updated_at`. Row identity is what later phases hang their data off.
- **Interruption is safe.** Writes are flushed in batches, so a killed scan
  leaves a smaller inventory, never a corrupt one. Re-running finishes the job.
- **Overlapping roots do not duplicate.** `samples.path` is unique; scanning a
  folder and then its parent reconciles rather than duplicates.
- **Reorganising does not lose work.** A renamed folder is recognised as a
  move, so the row keeps its id and its favourite.

## The browser (Phase 2)

Four execution contexts, and a rule for each:

| Thread | Does | Never does |
| --- | --- | --- |
| UI | Widgets, model, paging queries | Decodes, walks the filesystem |
| Decode worker | `crate.audio.decode` | Touches widgets |
| Scan worker | `LibraryScanner`, on **its own** SQLite connection | Shares a connection |
| PortAudio callback | Copies from an in-memory buffer | Allocates, blocks, logs |

WAL mode is what lets the scan worker write while the UI reads.

**Paging.** `SampleTableModel` holds one page (200 rows) and fetches more via
`canFetchMore`/`fetchMore`. Filtering and sorting are SQL, not a proxy model
(D13) — a proxy sorts rows it already has, and the model deliberately does not
have them.

**Audition latency.** Selecting a row bumps a token and posts a decode request;
results arriving with a stale token are dropped. That is how arrow-keying down
a list stays responsive without cancelling decodes mid-flight.

**Measured on the 35,089-sample library:**

| | |
| --- | --- |
| Search (count + first page) | ~40 ms, behind a 140 ms debounce |
| Sort (first page) | ~22 ms |
| First page, no filter | 20 ms |
| Page at row 35,000 | 174 ms — see debt |
| Decode to playable buffer | median 11.5 ms, p95 128 ms |
| Decode failures, 300 random samples | 0 |

## Testing

- `tests/test_discovery.py` — traversal, filtering, and the messy cases
  (permissions, symlink loops, deep trees, unicode, zero-byte files).
- `tests/test_persistence.py` — migrations, atomicity, repository behaviour.
- `tests/test_scanner.py` — the incremental-indexing guarantees above.
- `tests/test_search.py` — filter combinations, ordering, stable paging.
- `tests/test_audio.py` — decode edge cases, peak extraction, and the player
  state machine, driven by calling the PortAudio callback directly with a fake
  output buffer. No device required.
- `tests/test_ui.py` — model paging and window wiring, headless via
  `QT_QPA_PLATFORM=offscreen` with playback stubbed out.
- `tests/test_cli.py` — end-to-end through the command line.

Determinism comes from a `FakeClock` fixture, not from sleeping or from
tolerance windows.

Five tests are skipped on Windows because they need POSIX permission or symlink
semantics. The most important of them — the unreadable-directory safety
property — also has a platform-independent version that fakes `os.scandir`
failure, so that behaviour is covered everywhere.
