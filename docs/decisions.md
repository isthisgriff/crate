# Design decisions and known debt

Short records of choices worth remembering, and debt taken on deliberately.

---

## D1 — Extension-based format detection

**Decision.** Phase 1 identifies audio files by file extension, not by reading
headers.

**Why.** Header sniffing means opening every file. On a 100k-file library that
turns a directory walk into 100k reads for a payoff that is small in practice:
sample packs ship with correct extensions.

**Cost.** A `.wav` that is really an MP3 gets indexed with the wrong format; a
correct audio file named `kick.bin` is skipped entirely.

**When to revisit.** Phase 3, when a decoder exists. Analysis will read the
file anyway, so the true format can be recorded then at no extra cost.

---

## D2 — `(path, size, mtime_ns)` as the change fingerprint

**Decision.** A file counts as unchanged when path, size and modification time
in nanoseconds all match.

**Why.** It needs zero file reads, so an unchanged rescan is a directory walk.
Content hashing every file on every scan would make rescans as expensive as
first scans, which defeats the point of incremental indexing.

**Cost.** A file rewritten to the same byte length within the filesystem's
timestamp resolution reads as unchanged. Some tools also preserve mtime when
copying.

**When to revisit.** Phase 3. Hash the files queued for analysis, store it in
the `content_hash` column that already exists, and use it for duplicate
detection at the same time.

---

## D3 — Missing files are flagged, never deleted

**Decision.** A file that disappears gets `status = 'missing'`. No row is ever
removed by a scan.

**Why.** The most common reason a file "disappears" is an unplugged drive or an
unmounted network share, not a deletion. Once samples carry favourites, tags
and embeddings, deleting on absence means losing that work to a USB cable.

**Cost.** The database accumulates rows for files genuinely gone. Needs a
"clean up missing samples" action in the UI eventually.

---

## D4 — Files under an unreadable directory are not marked missing

**Decision.** When listing a directory fails, everything beneath it is excluded
from the missing-file reconciliation.

**Why.** Not discovered is not the same as not there. Without this, a single
permissions hiccup on a top folder marks a whole library missing.

**Cost.** A directory that fails to list *and* whose contents were genuinely
deleted stays stale until a successful scan. Correct direction to be wrong in.

---

## D5 — No move detection *(resolved in Phase 2)*

**Decision.** Renaming a folder shows up as N missing files plus N new files.

**Why.** Doing it properly wants a content hash. Doing it cheaply — matching a
new file to a missing one by `(size, mtime, filename)` — is heuristic, and in
Phase 1 there is no metadata attached to a sample to lose, so nothing is
actually at stake yet.

**Cost.** Rescanning after a big reorganisation churns the inventory.

**Resolved.** Favourites made row identity valuable, so Phase 2 implemented
it. See D10.

---

## D6 — Whole-root index loaded into memory during a scan

**Decision.** `load_index()` pulls every row for the root into a dict keyed by
path.

**Why.** One dict lookup per file instead of one SQL round trip per file. About
150 bytes per file, so ~15 MB for a 100k-file root — irrelevant on a machine
that runs a DAW.

**When to revisit.** If a root ever exceeds roughly a million files, this
becomes a cursor-based merge join against a sorted walk. The scanner does not
need to know; the change is local to the repository.

---

## D7 — CLI first, GUI in Phase 2

**Decision.** Phase 1 ships a command line, not a window.

**Why.** The Phase 1 milestone is "a reliable inventory of my real library". A
CLI demonstrates that on a real 100k-file drive today, keeps the core testable
without a UI framework, and avoids committing to PySide6 before there is
anything to render.

---

## D8 — Zero runtime dependencies in Phase 1 *(superseded in Phase 2)*

**Decision.** Phase 1 used only the standard library.

**Why.** Walking a filesystem into SQLite needs nothing else.

**Superseded by D11.** Phase 2 adds numpy, soundfile and sounddevice as core
dependencies, and PySide6 as the `ui` extra. `crate scan` still runs without
Qt installed.

---

## D9 — Timestamps as ISO-8601 UTC text

**Decision.** Store datetimes as ISO text in UTC.

**Why.** SQLite has no datetime type. Text sorts chronologically, is readable
in any database browser, and round-trips through `datetime.fromisoformat`.
UTC avoids the DST ambiguity that would otherwise show up in `last_seen_at`
comparisons twice a year.

**Cost.** Slightly larger than an integer epoch, and no arithmetic in SQL
without `julianday()`. Neither matters at this scale.

---

## D10 — Move detection as a post-walk pass

**Decision.** After the walk, a file that vanished from one path and appeared
at another with the same size, mtime and filename is treated as the same file:
the original row is re-pointed and the newly inserted row discarded.

**Why post-walk.** Mid-walk it is impossible to tell a move from a copy —
the original path may simply not have been visited yet. The pass also has to
run before missing-marking, or a rename would flag the file as deleted.

**Why the old row survives, not the new one.** Row identity is what favourites
(and later analysis and embeddings) hang off. Re-pointing the old row keeps
`id`, `first_seen_at` and every derived artefact; merging onto the new row
would not.

**Cost.** Conservative on purpose. Same name and bytes but a touched
timestamp reads as delete-plus-add. A pack with many identical copies pairs
them arbitrarily, which is harmless since the rows are interchangeable.

**Implementation note.** New rows are found with
`first_seen_at >= scan_started_at` rather than by buffering every new path
during the walk, so a first scan of 100k files costs no extra memory.

---

## D11 — soundfile + sounddevice, not Qt Multimedia

**Decision.** Decode with libsndfile (`soundfile`) into numpy, play through
PortAudio (`sounddevice`). Qt is used only for the interface.

**Why.** The waveform needs decoded samples and so does playback. Decoding
once and using the same buffer for both is what makes preview instant: on
this library the median decode is 11.5 ms, and starting playback afterwards
is a pointer assignment. `QMediaPlayer` would decode separately, internally,
with its own start latency and no access to the samples.

It also front-runs Phase 3: analysis will decode through exactly this path,
so a file is never opened twice for two different reasons.

**Cost.** Three dependencies instead of zero, and PortAudio is a native
library. libsndfile 1.2 covers every format in the library (WAV, FLAC, MP3,
AIFF), verified.

---

## D12 — Decoded audio is normalised to stereo float32, capped at 5 minutes

**Decision.** Every decode returns `(frames, 2)` float32. Mono is duplicated,
surround is folded down, anything past 5 minutes is truncated for preview.

**Why stereo always.** The output stream can then stay open across auditions
and only reopen when the sample *rate* changes. Reopening a PortAudio stream
costs tens of milliseconds and is audible as hesitation between samples.

**Why the cap.** A sample browser must not allocate 600 MB because there is a
two-hour DJ set in the folder. `DecodedAudio.duration_seconds` still reports
the real file length; `decoded_seconds` reports the excerpt.

---

## D13 — Filtering and sorting happen in SQL, not in a proxy model

**Decision.** `SampleTableModel` pages results in via `canFetchMore`/
`fetchMore`; `QSortFilterProxyModel` is not used, and `setSortingEnabled` is
off with the header indicator driven by hand.

**Why.** A proxy model filters and sorts rows it already holds, so it needs
all of them. Showing 40 rows out of 35,000 has to cost 40 rows.

**Cost.** Sorting logic lives in two places (the SQL and the header handler),
and the header no longer manages its own indicator — which was in fact a bug
until it was written explicitly.

---

## D14 — macOS AppleDouble sidecars are skipped

**Decision.** Files named `._*` and the `__MACOSX` directory are never
indexed.

**Why.** Unzipping a Mac-authored sample pack on Windows scatters ~200-byte
resource-fork files carrying a real `.wav` extension. They were **756 files,
2.1% of this library**, and they cannot be decoded — so they were noise in
every search result and would be noise in every similarity result later.

**Cost.** A real file legitimately named `._something.wav` would be skipped.
Nothing else creates such names.

---

## Known debt

| Item | Impact | Trigger to address |
| --- | --- | --- |
| Deep paging is O(offset) — **measured 174 ms at row 35,000** vs 20 ms at row 0 | Perceptible hitch when scrolling an *unfiltered* 35k list | Keyset pagination, if unfiltered deep scrolling turns out to be a real habit. Searching first makes it moot. |
| Search is `LIKE %term%` — **measured 40 ms** on 35k rows | Fine behind the 140 ms debounce; grows linearly | FTS5, when a library makes 40 ms into 200 ms |
| No content hash (D2) | Rare missed modifications; no dedupe | Phase 3 |
| Waveform peaks recomputed on every resize | A 5-minute file recomputes ~13 M samples on drag | Cache per width, if dragging the window ever feels sticky |
| Symlink and POSIX-permission tests skip on Windows | Reduced coverage on the primary dev machine | Add CI on Linux |
| No CI pipeline | Nothing enforces the suite | Before Phase 3 |
| Scan is single-threaded | First scan of a huge drive is I/O bound | Measure before optimising |
| No structured logging | Debugging a scan means reading `scan_errors` | Phase 3, with background jobs |
| Drag-out to DAW is untested automatically | Only the MIME payload is covered; the drop side is manual | If a host rejects the payload |
