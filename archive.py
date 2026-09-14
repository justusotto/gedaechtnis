#!/usr/bin/env python3
"""archive.py — the archive seam: a compacted entry goes into a BOUNDED file, and a new segment
opens before the current one gets big enough to disappear from search.

## The measurement this exists to answer

Row S0 grew a vault through this package's own hooks for a simulated year and then asked it
questions. At day 365 the answers came back: **1.00 of held-out answers found under quiet use, 0.54
under normal use, 0.00 under heavy use** — identically in every boot-budget arm, because the
rolling window pours every compacted entry into ONE growing archive file, that file crosses
`recall.MAX_FILE_BYTES`, and `md_files()` then skips it with no error and no hit. The vault still
held every answer. Nothing could reach them.

## Why the obvious fix is the wrong one

`max_searchable_file_bytes` is a real ceiling with a real reason: a search that reads a 100 MB file
is not a search. **Raising it moves the cliff; it does not remove it.** Any single append-only file
crosses any constant eventually — the only open question is which month. So this module is about
the archive's SHAPE, and the ceiling is left where it is.

## The scheme, and the one place it departs from the row's sketch

`<Stem>-archive.md` is segment ONE. When appending would take it past `max_memory_file_bytes`,
`<Stem>-archive-2.md` opens and becomes the current segment; then `-3`, and so on.

The row's sketch had `<Stem>-archive.md` stay the CURRENT segment, rolling its contents out to a
numbered name when full. That is one rename per roll, and **a rename breaks every wikilink that
already pointed into the archive** — `[[Position-archive#Some heading]]` resolves today and dangles
tomorrow, silently, which is the same failure class the row exists to end. Sealing in place instead
means **no archived byte ever moves twice and no archived heading ever changes file**: a link into
an archive is valid for the life of the vault. The cost is that the familiar name holds the OLDEST
narrative rather than the newest, which is a naming inconvenience, not a correctness one. The
vault's own law points the same way: roll forward, never rewrite an existing archive.

The segment limit comes from `rules/limits.json` and sits well under `max_searchable_file_bytes`.
**The margin is the point, not the equality**: a segment that is merely at the ceiling is one
oversized append away from the cliff it was built to avoid.

## What a caller gets

    current_segment(live)     the file an append would go to, rolling if needed
    segments(live)            every segment that exists, oldest first
    append_entries(live, …)   append text, opening a new segment when this one would cross

Nothing here deletes, rewrites or moves an existing file. `append_entries` only ever appends to a
file or creates a new one.
"""
from __future__ import annotations
import os
import re
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import limits  # noqa: E402

# `Position-archive`, `Position-archive-2`, `Errata-fixed`, `Aporia-resolved-11`. The trailing
# segment number is what `recall.ARCHIVE_RE` had to learn to read: a segmented name that the search
# no longer recognises AS an archive reproduces row S0's bug in a new costume.
SIDECAR_RE = re.compile(r"-(archive|fixed|resolved)(?:-(\d+))?$", re.I)

FIRST_SEGMENT_SUFFIX = "-archive"


def atomic_write(path: Path, text: str) -> None:
    """Replace `path`'s contents, or leave the file exactly as it was. Never anything between.

    ## Why this is not a nicety

    `Path.write_text` opens with `'w'`, which TRUNCATES before it writes. A process killed in that
    window leaves the file empty or half-written, and the caller here is compaction — which rewrites
    a LIVE role file holding memory the user wrote. The moment the Stop hook began compacting a real
    vault, that stopped being a theoretical ordering concern and became a way to lose someone's
    notes. Git is not the safety net people assume: it can only restore what was already committed,
    and compaction routinely touches entries newer than the last commit.

    The recipe is the ordinary one and every step earns its place: write the replacement to a
    temporary file IN THE SAME DIRECTORY (so `os.replace` is a rename within one filesystem, which
    is atomic, rather than a copy across two, which is not); `flush` and `fsync` it so the bytes are
    on the device before anything points at them; then `os.replace`, which either fully succeeds or
    leaves the original untouched.

    **What this does and does not promise.** The threat model it is proven against is a PROCESS
    DEATH — a kill, a crash, an OOM — and the tests kill a real process at two points inside the
    write. Full power loss is a weaker guarantee: the directory fsync below makes the rename likely
    to survive one, but nothing here has been tested against pulled power, and the claim is not
    made. The temp file is cleaned up if any of that fails, so a crashed
    run does not litter the vault with debris the next search would try to read.
    """
    tmp = None
    try:
        # The original's MODE, read before anything replaces it. `mkstemp` creates 0600 and
        # `os.replace` carries the temp file's mode onto the destination — so without this a user's
        # memory file silently becomes owner-only the first time it is compacted. Measured, not
        # assumed: 0644 before, 0600 after. Small, but it is a state change with no signal, which
        # is the shape this project refuses.
        try:
            mode = os.stat(path).st_mode & 0o777
        except OSError:
            mode = None
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)            # atomic: the old inode survives until this instant
        tmp = None
        # fsync the DIRECTORY too, so the RENAME survives a power loss and not only the bytes it
        # points at. Best-effort: a filesystem that will not let a directory be opened or synced is
        # not a reason to fail a write that has already landed.
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass


def segment_limit() -> int:
    """Bytes. Read, never inlined — and asserted against the search ceiling at call time rather
    than trusted, because a config edit that lifts the segment limit past the ceiling would
    reintroduce the exact defect with no other symptom."""
    seg = int(limits.get("max_memory_file_bytes"))
    ceiling = int(limits.get("max_searchable_file_bytes"))
    if seg >= ceiling:
        raise ValueError(
            f"max_memory_file_bytes ({seg:,}) must be well under max_searchable_file_bytes "
            f"({ceiling:,}) — a segment at the ceiling is one append from being unsearchable")
    return seg


def is_sidecar(stem: str) -> bool:
    return bool(SIDECAR_RE.search(stem))


def live_stem_of(stem: str) -> str:
    """`Position-archive-3` -> `Position`. The live file a sidecar belongs to."""
    return SIDECAR_RE.sub("", stem)


def segment_index(path: Path) -> int:
    """1 for `<Stem>-archive.md`, n for `<Stem>-archive-<n>.md`. Not a sidecar -> 0."""
    m = SIDECAR_RE.search(path.stem)
    if not m:
        return 0
    return int(m.group(2)) if m.group(2) else 1


def segment_name(live: Path, index: int) -> Path:
    if index <= 1:
        return live.with_name(f"{live.stem}{FIRST_SEGMENT_SUFFIX}{live.suffix}")
    return live.with_name(f"{live.stem}{FIRST_SEGMENT_SUFFIX}-{index}{live.suffix}")


def segments(live: Path) -> list[Path]:
    """Every existing segment for this live file, oldest first. Sorted by INDEX, never by name:
    a lexical sort puts `-archive-10` before `-archive-2`, and an archive read in the wrong order
    is a history that reads as though it happened in the wrong order."""
    found = []
    for p in live.parent.glob(f"{live.stem}{FIRST_SEGMENT_SUFFIX}*{live.suffix}"):
        idx = segment_index(p)
        if idx and live_stem_of(p.stem) == live.stem:
            found.append((idx, p))
    return [p for _, p in sorted(found)]


def current_segment(live: Path, incoming_bytes: int = 0, limit: int | None = None) -> Path:
    """The segment an append of `incoming_bytes` belongs in.

    The check is on the size the file WOULD reach, not the size it has: a segment that is under
    the limit today and 40 MB after one append has been under the limit at every moment anyone
    asked, and over it for every search that follows.
    """
    limit = segment_limit() if limit is None else limit
    existing = segments(live)
    if not existing:
        return segment_name(live, 1)
    last = existing[-1]
    try:
        size = last.stat().st_size
    except OSError:
        size = 0
    if size + incoming_bytes > limit:
        return segment_name(live, segment_index(last) + 1)
    return last


def header_for(live: Path) -> str:
    return (f"# {live.stem} (archive)\n\n"
            "Narrative compacted out of the live file. Headings are byte-identical to the ones "
            "they had there, and nothing here is ever rewritten or moved: a link into an archive "
            "segment stays valid.\n")


def compact_file(live: Path, limit: int | None = None, floor_share: float = 0.4,
                 measure=None) -> int:
    """Move the OLDEST entries out of an over-large LIVE file into its archive segments, until it
    is under `floor_share` of the limit. Returns entries moved. Nothing is deleted.

    `measure` is the UNIT the limit is expressed in, and defaults to bytes. Row R2's cleanup pass
    needs the same movement against a LINE limit — the per-role soft limits are written in lines,
    because that is the unit the guidance they come from uses and because bytes-per-line varies by
    more than 2x across real role files. Passing `measure=lambda s: s.count(chr(10)) + 1` compacts
    a file against its line limit through this one implementation rather than a second copy of the
    idempotence, segment-splitting and atomic-write reasoning below, which is where a duplicate
    would drift first. The SEGMENT limit stays in bytes whatever `measure` is: a segment is bounded
    by what the search can read, which is a byte fact and has nothing to do with the unit that
    decided the live file was too long.

    ## Why this exists, measured rather than reasoned

    Row S0's rolling window compacted the BOOT file against the boot budget, and nothing at all
    watched the other role files. Simulated to year 3 at high load, that produced `Aporia.md` at
    7,001,112 B, `Canon.md` at 6,887,434, `Errata.md` at 6,807,646 and `Patterns.md` at 6,872,157 —
    every one of them past `recall.MAX_FILE_BYTES`, every one of them a LIVE file, and tool recall
    at **0.00**. Segmenting the archive does nothing for that: the archive was never the file that
    had grown. A budget-driven window is a rule about what a session PAYS TO BOOT; this is a rule
    about what the search can still READ, and a vault needs both.

    The floor is the same idea as the boot window's: compact to a share of the limit rather than
    emptying the file, because a role file compacted to nothing is a region with no live state.
    """
    byte_mode = measure is None
    limit = segment_limit() if limit is None else limit
    if byte_mode:
        measure = lambda s: len(s.encode("utf-8"))  # noqa: E731
        try:
            if live.stat().st_size <= limit:
                return 0
        except OSError:
            return 0
    try:
        text = live.read_text(encoding="utf-8")
    except OSError:
        return 0
    if not byte_mode and measure(text) <= limit:
        return 0
    parts = text.split("\n## ")
    head, entries_ = parts[0], parts[1:]
    if not entries_:
        return 0                     # a head with no entries: there is nothing to move
    target = limit * floor_share
    size = measure(text)
    moved, removed = 0, 0
    while moved < len(entries_) and size - removed > target:
        removed += measure("\n## " + entries_[moved])
        moved += 1
    if not moved:
        return 0
    # Write the archive FIRST: if the process dies between the two writes the entries exist twice,
    # and duplication is recoverable where loss is not.
    #
    # ★ BUT "recoverable" is not "handled", and the honest version of this is IDEMPOTENCE. A retry
    # after that crash re-reads the still-oversize live file, recomputes the same oldest entries and
    # appends them a SECOND time — so the naive ordering argument silently compounds the damage it
    # was defending. Before appending, the chunk already present at the tail of the newest segment
    # is skipped, which makes a retried compaction a no-op rather than a duplicator.
    # PER ENTRY, not per chunk. A first attempt compared the whole chunk against the segment tail,
    # which only catches a retry that recomputed byte-identical output — and a retry need not: the
    # live file it re-reads may have changed, so it moves a different number of entries and the
    # overlap is partial. Filtering entry by entry handles both, and an entry is identified by its
    # exact text, so a legitimately superseded entry sharing a heading is not mistaken for a copy.
    outgoing = ["\n## " + e for e in entries_[:moved]]
    existing = segments(live)
    if existing:
        try:
            # EVERY segment, not the last few. A crash mid-compaction can leave the moved entries
            # spread over several freshly-opened segments, and a window of the newest two silently
            # missed the rest — which is how this guard failed its own test on the first attempt.
            # The scan costs one read of the archive, paid only on a compaction, which fires only
            # when a file is over the bound; correctness is worth more here than the read.
            # EXACT ENTRIES, not substring containment. `e in blob` would drop a short entry whose
            # whole text happens to occur inside unrelated archived content — unlikely, and exactly
            # the kind of unlikely that silently deletes a memory. The archive is parsed into the
            # same entry units the outgoing list is made of, and membership is equality.
            archived = set()
            for s in existing:
                body = s.read_text(encoding="utf-8")
                archived.update("\n## " + part for part in body.split("\n## ")[1:])
            outgoing = [e for e in outgoing if e not in archived]
        except OSError:
            pass
    if outgoing:
        # `limit` here is the SEGMENT limit, always in bytes. Handing a line limit to the
        # segment roll would size segments in the wrong unit — a 200-"byte" segment per line
        # limit, one entry per file.
        append_entries(live, "".join(outgoing), limit=limit if byte_mode else None)
    atomic_write(live, head + ("\n## " + "\n## ".join(entries_[moved:]) if entries_[moved:] else "\n"))
    return moved


def append_entries(live: Path, text: str, limit: int | None = None) -> Path:
    """Append `text` to the right segment(s), opening new ones as the limit is reached. Returns the
    last segment written to. Creates; appends; never rewrites, moves or deletes.

    ★ THE APPEND IS SPLIT ACROSS SEGMENTS AT ENTRY BOUNDARIES, and that is not tidiness. Rolling
    only BETWEEN appends is what the first draft did, and a test caught it immediately: one
    compaction of an over-large live file hands over every moved entry in a single call, so all of
    it landed in one segment and that segment was instantly larger than the search ceiling — the
    original defect, rebuilt by the machinery meant to prevent it. A segment boundary has to be
    able to fall INSIDE one append, or the limit only binds when nothing much is happening.
    """
    if not text:
        return current_segment(live, 0, limit)
    limit = segment_limit() if limit is None else limit
    # Split on entry boundaries, never mid-entry: an entry cut in half is worse than an oversize
    # file, because both halves read as complete.
    parts = text.split("\n## ")
    chunks = ([parts[0]] if parts[0] else []) + ["\n## " + s for s in parts[1:]]
    # ★ THE ROLL DECISION USES THE PROJECTED SIZE, NOT THE ON-DISK SIZE. Batching the writes means
    # the file does not grow as the loop runs, so asking `current_segment` each time — which stats
    # the disk — put every chunk in the same segment and the split silently stopped happening. The
    # running total is what the on-disk size used to stand in for.
    target = current_segment(live, 0, limit)
    try:
        cur_size = target.stat().st_size
    except OSError:
        cur_size = len(header_for(live).encode("utf-8"))
    pending: dict = {}
    order: list = []
    for chunk in chunks:
        blob = len(chunk.encode("utf-8"))
        if cur_size + blob > limit and cur_size > len(header_for(live).encode("utf-8")):
            target = segment_name(live, segment_index(target) + 1)
            cur_size = len(header_for(live).encode("utf-8"))
        if target not in pending:
            pending[target] = []
            order.append(target)
        pending[target].append(chunk)
        cur_size += blob
    # ONE atomic write per segment, not one per chunk. Fewer read-rewrite cycles is cheaper, but
    # the reason that matters is that each cycle is a chance to get the read wrong — and getting it
    # wrong here overwrites an archive.
    for target in order:
        chunks = pending[target]
        if target.exists():
            # ★ NOT GUARDED, DELIBERATELY. This read was briefly wrapped in
            # `except OSError: existing_text = ""`, which meant that a transient read failure — a
            # permission blip, a network-mount hiccup, a momentary lock — made the code treat a
            # full segment as EMPTY and then write only the new chunk over it, destroying the
            # header and every entry already archived there. Silently: nothing raised, nothing
            # logged, the function returned normally. That is strictly worse than the crash bug
            # this row exists to fix, and it fires with no crash at all; the old append-mode code
            # never read the segment, so it could not have this failure. Letting the error
            # propagate hands it to `compact_vault`'s handler, which logs it and moves on with the
            # archive intact.
            existing_text = target.read_text(encoding="utf-8")
        else:
            existing_text = header_for(live)
        atomic_write(target, existing_text + "".join(chunks))
    return target or current_segment(live, 0, limit)
