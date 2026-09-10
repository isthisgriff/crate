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

## D5 — No move detection yet

**Decision.** Renaming a folder shows up as N missing files plus N new files.

**Why.** Doing it properly wants a content hash. Doing it cheaply — matching a
new file to a missing one by `(size, mtime, filename)` — is heuristic, and in
Phase 1 there is no metadata attached to a sample to lose, so nothing is
actually at stake yet.

**Cost.** Rescanning after a big reorganisation churns the inventory.

**When to revisit.** Before Phase 2 ships favourites. The
`(size_bytes, mtime_ns)` index needed for the lookup already exists.

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

## D8 — Zero runtime dependencies in Phase 1

**Decision.** Only the standard library. `sqlite3`, `os.scandir`, `tomllib`,
`argparse`, `pathlib`.

**Why.** Nothing needed is missing from it, and the first thing this project
does — walk a filesystem into SQLite — should not require a dependency
resolver. Real dependencies arrive in Phase 3 with actual audio decoding.

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

## Known debt

| Item | Impact | Trigger to address |
| --- | --- | --- |
| No move detection (D5) | Reorganising churns the inventory | Before favourites ship |
| No content hash (D2) | Rare missed modifications; no dedupe | Phase 3 |
| No `crate forget` / prune command | Missing rows accumulate | When it becomes annoying |
| Symlink and POSIX-permission tests skip on Windows | Reduced coverage on the primary dev machine | Add CI on Linux |
| No CI pipeline | Nothing enforces the suite | Before Phase 2 |
| Scan is single-threaded | First scan of a huge drive is I/O bound | Measure before optimising |
| No structured logging | Debugging a scan means reading `scan_errors` | Phase 3, with background jobs |
