# Roadmap

Each phase ends with something usable, demonstrated against a real sample
library. A phase is not done because the tests pass; it is done because the
tool is better to use than it was before.

| Phase | Goal | Status |
| --- | --- | --- |
| 0 | Project foundation | Done |
| 1 | Sample library scanner | Done |
| 2 | Sample browser and playback | Done |
| 3 | Audio analysis | Next |
| 4 | Categorization and tagging | |
| 5 | Feature-based similarity (baseline) | |
| 6 | Neural audio embeddings | |
| 7 | Natural-language semantic search | |
| 8 | AI metadata generation | |
| 9 | Search by example | |
| 10 | Reference track matching | |

---

## Phase 0 — Foundation ✅

Repository, layout, tooling, tests, docs, domain model, config system.

**Exit criteria met:** clone, `uv pip install -e ".[dev]"`, `pytest` — green on
a fresh machine. `crate --help` runs. Strict `mypy` and `ruff` both clean.

## Phase 1 — Sample library scanner ✅

Recursive discovery, filesystem metadata, SQLite inventory, progress
reporting, per-file error capture, incremental rescans.

**Exit criteria:**

- [x] Scans a directory tree of thousands of files without crashing
- [x] Bad files, permissions and unreadable folders are recorded, not fatal
- [x] Rescanning does not recreate records
- [x] New / modified / deleted files detected on rescan
- [x] Unicode, spaces, uppercase extensions, zero-byte files handled
- [x] Automated tests for all of the above
- [x] **Demonstrated on the real library** — 35,845 samples / 53.5 GB indexed with
      zero errors; rescan of 35,217 files in 1.1 s with 0 records recreated
      (see "Verifying it on a real library" in the README)

Not in Phase 1: move detection (see decision D5), content hashing (D2),
duration or any other property requiring the file to be opened.

## Phase 2 — Sample browser and playback ✅

`crate ui` opens a dark, keyboard-driven browser over the existing inventory.

**Exit criteria:**

- [x] Browse the indexed library, paged (200 rows at a time, not 35,000)
- [x] Search filenames *and folder names*; filter by format; favourites-only
- [x] Sort by name / folder / size / date, in SQL
- [x] Instant preview on select — median 11.5 ms decode, stream held open
- [x] Play, pause, stop, seek; click or drag the waveform to scrub
- [x] Waveform display, recomputed at the window's width
- [x] Favourites, persisted, surviving rescans and folder renames
- [x] Scan from the UI on a worker thread, with progress and cancel
- [x] Move detection, so reorganising a pack does not discard favourites
- [x] Drag a row straight into a DAW as a file
- [x] Tests for the model, the audio engine and the window (headless)
- [x] **Used against the real library** — 35,089 samples

Fixed after first real use:

- Audition latency scaled with file size (up to 388 ms on big loops); now a
  head-first decode makes time-to-sound flat at ~1.5 ms (D15).
- Drag into a DAW did nothing — drag support was on the view, where Qt never
  looks (D16).

Also this phase, because the real library demanded it: macOS AppleDouble
sidecars are skipped (756 files, 2.1% of the library — D14) and `crate prune`
clears records for files that are genuinely gone.

Drag-out was pulled forward from "future features" deliberately. Without it
the workflow ends at "I found the sample" and you still go hunting in
Explorer, which is the thing Crate exists to replace. It cost one method.

**Exit criterion:** open Crate instead of Explorer when looking for a sample,
and do not regret it.

## Phase 3 — Audio analysis (next)

Duration, sample rate, channels, peak, RMS, loudness; spectral features; BPM
and key where they are meaningful, with explicit confidence and an honest
"unknown".

Forces the first real infrastructure: a job queue
(`pending → processing → completed → failed`), resumable across restarts, and
content hashing so analysis is not repeated for unchanged bytes.

Groundwork already in place from Phase 2:

- `crate.audio.decode` is the decode path analysis will use — one file, one
  open, whatever the reason.
- `samples.content_hash` exists and is cleared whenever bytes change.
- The scan-run/scan-error tables are the shape the job queue wants.

Worth doing first: duration is the single most useful missing column in the
browser, and it comes free with the decode.

**Exit criterion:** "show me samples around 140 BPM" answered from extracted
audio data.

## Phase 4 — Categorization and tagging

Filename and folder heuristics first (`kick_808_distorted.wav` → `kick`, `808`,
`distorted`). ML classification only where the heuristics visibly fail.
Categories live in data, not in an enum. Tags record source and confidence so
user edits always win.

## Phase 5 — Feature-based similarity

Normalised feature vectors from Phase 3, cosine and Euclidean distance,
weighted by feature group. Deliberately built before embeddings, to serve as
the baseline Phase 6 has to beat.

## Phase 6 — Neural audio embeddings

Evaluate pretrained audio models on retrieval quality, local CPU inference,
size and licence. Vector index sized for a personal library. Must be measurably
better than Phase 5 on the same queries, documented.

## Phase 7 — Natural-language semantic search

Requires a model with a genuine shared audio–text embedding space; arbitrary
text embeddings are not comparable with audio embeddings. Hybrid retrieval:
keyword + metadata filter + vector search.

**Exit criterion:** typing "dark aggressive distorted kick" returns useful
samples from the real library.

## Phase 8 — AI metadata generation

Tags and descriptions for samples whose filenames say nothing. Stores model,
version, confidence and timestamp. Never overwrites user metadata.

## Phase 9 — Search by example

Drag a sample in, get similar ones back. Adjustable similarity dimensions only
where the model can actually separate them.

## Phase 10 — Reference track matching

Analyse a reference track, build a query profile, recommend samples from the
user's own library by role (kick, snare, bass, ...).

---

## Explicitly deferred

DAW drag-and-drop, collections, smart playlists, duplicate detection, pack
detection, MIDI, spectrograms, batch tagging, custom taxonomies, trained
models, learned ranking, plugin and DAW integration, cloud sync.

These get built when actual use demands them, not because they are on a list.
