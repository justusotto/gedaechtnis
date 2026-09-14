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
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import limits  # noqa: E402

# `Position-archive`, `Position-archive-2`, `Errata-fixed`, `Aporia-resolved-11`. The trailing
# segment number is what `recall.ARCHIVE_RE` had to learn to read: a segmented name that the search
# no longer recognises AS an archive reproduces row S0's bug in a new costume.
SIDECAR_RE = re.compile(r"-(archive|fixed|resolved)(?:-(\d+))?$", re.I)

FIRST_SEGMENT_SUFFIX = "-archive"


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


def compact_file(live: Path, limit: int | None = None, floor_share: float = 0.4) -> int:
    """Move the OLDEST entries out of an over-large LIVE file into its archive segments, until it
    is under `floor_share` of the limit. Returns entries moved. Nothing is deleted.

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
    limit = segment_limit() if limit is None else limit
    try:
        if live.stat().st_size <= limit:
            return 0
    except OSError:
        return 0
    text = live.read_text(encoding="utf-8")
    parts = text.split("\n## ")
    head, entries_ = parts[0], parts[1:]
    if not entries_:
        return 0                     # a head with no entries: there is nothing to move
    target = limit * floor_share
    size = len(text.encode("utf-8"))
    moved, removed = 0, 0
    while moved < len(entries_) and size - removed > target:
        removed += len(("\n## " + entries_[moved]).encode("utf-8"))
        moved += 1
    if not moved:
        return 0
    # Write the archive FIRST. If the process dies between the two writes, the entries exist twice
    # — which a reader can see and resolve. The other order loses them outright.
    append_entries(live, "".join("\n## " + e for e in entries_[:moved]), limit=limit)
    live.write_text(head + ("\n## " + "\n## ".join(entries_[moved:]) if entries_[moved:] else "\n"),
                    encoding="utf-8")
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
    target = None
    for chunk in chunks:
        blob = len(chunk.encode("utf-8"))
        target = current_segment(live, blob, limit)
        if not target.exists():
            target.write_text(header_for(live), encoding="utf-8")
        with target.open("a", encoding="utf-8") as fh:
            fh.write(chunk)
    return target or current_segment(live, 0, limit)
