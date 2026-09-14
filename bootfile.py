#!/usr/bin/env python3
"""bootfile.py — the Boot file: when a region needs one, how it stays small, and when it has gone stale.

A region that has grown past a few files costs a session real context at every boot. The answer is a
**Boot file** (`Kernel.md`): a short, always-loaded index that REPLACES the bodies in the import
chain, with the bodies read on demand. Three mechanisms keep that honest, and each is useless
without the other two.

**Graduation.** A region whose boot payload has outgrown the budget and has no Boot file is
PROPOSED one. Never written for it: distilling a region into an index is a judgment about what
matters, and a machine that did it would put a confident summary nobody wrote into every session.

**The rolling window, and the shape of Boot file it may touch.** A Boot file is a fixed-size
window, not an append log. Left alone it becomes the thing it was created to replace — the vault
this grew from watched three of its own drift 50-70% over budget while a status table printed OVER
for weeks and nothing acted.

★ But a Boot file is NOT a chronological log, and the first version of this module treated it as
one. It compacted the file's OLDEST `## ` sections into an archive — and a real Boot file's sections
are named and structural (*At a glance*, *Resume point*, *Standing constraints*, *Canon headlines*,
*Pointer map*), not ordered by age. The row's reviewer reproduced the consequence on a realistic
fixture: **a `## Standing constraints` section carrying a NEVER rule was moved wholesale into an
archive file nothing reads at boot.** The rule that would have forbidden that lives in the kind of
file the mechanism had just emptied.

So the window is now DECLARED, never inferred. A Boot file is compacted only below a marker its
author put there:

    <!-- gedaechtnis:window -->
    ... the entries that may roll ...
    <!-- gedaechtnis:/window -->

Everything outside those two lines is untouchable, whatever the file grows to. Between them, the
oldest entries roll into the archive sidecar. **Both markers are required**, and the second is not
symmetry: with an open-ended window the same repro that showed the standing constraints surviving
also showed `## Canon headlines` and `## Pointer map` being carried off, because they sat below it.
A heading is not a fence, and nothing can infer where a window stops. **A Boot file that does not
declare a closed window is REPORTED and never written**, and the facts line says how to opt in. That is the safe default in the only direction
that matters: the failure mode of not compacting is a large file, and the failure mode of compacting
the wrong thing is a binding rule that silently stops being loaded.

**Freshness.** A Boot file distilled from bodies that have since moved is worse than no Boot file:
it is a stale summary that reads as current, in every session's context, and nothing contradicts it.
It is STALE when a watched body in its own region has a commit the Boot file's own last commit
cannot reach — **ancestry, not timestamps**, because this package's own hook commits several files
inside one second and a same-second comparison silently reports fresh.

## What is deliberately absent

No automatic distillation, and no automatic rewriting of an import line. The arms REPORT; a session
or a person acts. The one thing this module writes is the window compaction, which moves whole
entries into the file's own archive sidecar and deletes nothing.
"""
from __future__ import annotations
import subprocess
from pathlib import Path

BOOT_FILE = "Kernel.md"
# The bodies a Boot file is distilled FROM. A change in one of these is what can make the index
# wrong; a change in an Errata or a Patterns file adds to the region without contradicting its
# summary, which is why they are not here.
WATCHED_BODIES = ("Position.md", "Course.md", "Canon.md", "Nomos.md", "Aporia.md")


def _limits():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
    import limits
    return limits


def _archive():
    """`archive.py`, lazily. Its `is_sidecar` is the package's ONE definition of a sidecar name; the
    substring test that stood here disagreed with it in two directions — it excluded a live file
    merely containing `-archive` mid-name, and it did not recognise a numbered `-fixed-2`."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import archive
    return archive


def budget() -> int:
    return int(_limits().get("boot_file_budget_bytes"))


def warn_bytes() -> int:
    return int(_limits().get("boot_file_warn_bytes"))


def _git(vault: Path, *args: str, ok_codes=(0,)):
    """(rc, stdout). A failure is returned, never swallowed — every caller here must be able to
    tell "no" from "I could not look"."""
    try:
        p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return 128, ""
    return p.returncode, p.stdout.strip()


def regions(vault: Path) -> list[Path]:
    """The regions, from `maintenance.regions()` — the package's ONE definition.

    ★ This was a second implementation and it had already drifted. `maintenance.regions()` excludes
    `Global/`; this one did not, so the window would have rewritten a `Global/Kernel.md` — a file
    that in the vault this came from is shared across every lane and runs its own hand-designed
    window with different, deliberate semantics. The row's reviewer reproduced that rewrite. The
    import is lazy because `maintenance` imports this module.

    The `vault` argument is kept so the call sites read the same, and asserted against the module's
    own VAULT rather than silently ignored: a function that takes a path and uses a different one is
    the shape of the last three defects this arc has fixed."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
    import maintenance
    from common import VAULT as _V
    if Path(vault).resolve() != Path(_V).resolve():
        # A caller pointing somewhere else is a test or a mistake; either way, answer about the
        # path it ASKED about rather than about the environment's vault.
        return [d for d in sorted(p for p in Path(vault).rglob("*") if p.is_dir())
                if (d / "Map.md").is_file()
                and not any(part.startswith(".") for part in d.relative_to(vault).parts)
                and d.relative_to(vault).parts[0] not in maintenance.EXCLUDED_REGION_DIRS]
    return maintenance.regions()


def region_payload(region: Path) -> tuple[int, list[dict]]:
    """(bytes, the largest members) of what a region would put in front of a session.

    ★ The population is the region's OWN markdown, not its @-import chain, and the difference
    matters. A region with no Boot file typically has no import line either — its files reach a
    session because a person pasted them, or because the region is small enough that everything is
    read. Measuring the chain would report 0 for exactly the regions that need a Boot file most.
    Sidecars are excluded: an archive is what a Boot file's window moves INTO, so counting it would
    make a compaction look like growth."""
    total, members = 0, []
    try:
        names = sorted(p for p in region.glob("*.md") if p.is_file())
    except OSError:
        return 0, []
    for p in names:
        if p.name == BOOT_FILE:
            continue
        if _archive().is_sidecar(p.stem):
            continue
        try:
            n = p.stat().st_size
        except OSError:
            continue
        total += n
        members.append({"path": p.name, "bytes": n})
    members.sort(key=lambda m: -m["bytes"])
    return total, members[:5]


def graduation_candidates(vault: Path) -> list[dict]:
    """Regions whose payload has outgrown the budget and that have no Boot file yet."""
    out = []
    for region in regions(vault):
        if (region / BOOT_FILE).is_file():
            continue
        total, members = region_payload(region)
        if total > budget():
            out.append({"region": _rel(vault, region), "bytes": total,
                        "threshold": budget(), "largest": members})
    out.sort(key=lambda r: -r["bytes"])
    return out


def oversize_boot_files(vault: Path) -> list[dict]:
    """Boot files at or past the budget, and the ones approaching it.

    A door that first speaks at the moment it refuses has never given anyone a chance to act, which
    is why the WARN band exists and is reported separately rather than as a smaller kind of alarm."""
    out = []
    for region in regions(vault):
        f = region / BOOT_FILE
        if not f.is_file():
            continue
        try:
            n = f.stat().st_size
        except OSError:
            continue
        if n > budget():
            state = "OVER"
        elif n >= warn_bytes():
            state = "WARN"
        else:
            continue
        out.append({"path": _rel(vault, f), "bytes": n, "state": state,
                    "threshold": budget(), "warn": warn_bytes()})
    out.sort(key=lambda r: -r["bytes"])
    return out


def _last_commit(vault: Path, rel: str) -> str | None:
    rc, out = _git(vault, "log", "-1", "--format=%H", "--", rel)
    if rc != 0:
        return None
    return out or None


def _reachable(vault: Path, a: str, b: str) -> bool | None:
    """Is `a` reachable from `b`? None when git could not answer — which is not False."""
    rc, _ = _git(vault, "merge-base", "--is-ancestor", a, b)
    if rc in (0, 1):
        return rc == 0
    return None


def stale_boot_files(vault: Path) -> tuple[list[dict], list[str]]:
    """(stale, unchecked). A Boot file whose bodies moved after it did.

    ★ ANCESTRY, NOT TIMESTAMPS. This package's own Stop hook commits several files inside one
    second, so a body committed after its Boot file in the same second compares EQUAL and the
    staleness goes unreported. `%ct` also moves with the author's clock and with a rebase.
    Reachability is exact and immune to both."""
    if not (vault / ".git").exists():
        return [], ["the vault is not a git repository, so no Boot file's freshness can be checked"]
    stale, unchecked = [], []
    for region in regions(vault):
        f = region / BOOT_FILE
        if not f.is_file():
            continue
        rel_boot = _rel(vault, f)
        own = _last_commit(vault, rel_boot)
        if own is None:
            unchecked.append(f"{rel_boot} (never committed, or git could not be read)")
            continue
        moved = []
        for name in WATCHED_BODIES:
            body = region / name
            if not body.is_file():
                continue
            rel = _rel(vault, body)
            when = _last_commit(vault, rel)
            if when is None or when == own:
                continue
            reach = _reachable(vault, when, own)
            if reach is None:
                unchecked.append(f"{rel} (git could not decide reachability)")
            elif not reach:
                moved.append(rel)
        if moved:
            stale.append({"path": rel_boot, "moved": moved})
    return stale, unchecked


def _rel(vault: Path, path: Path) -> str:
    """`path` relative to `vault`, tolerating two spellings of the same directory.

    `regions()` may return paths spelled as the module's own VAULT while the caller passed an
    equal-but-differently-spelled path — `/tmp` against `/private/tmp` on macOS is the everyday
    case — and `Path.relative_to` raises on that. The reviewer reproduced the crash; nothing in
    production reaches it today, which is exactly the kind of latent break that surfaces under a
    symlinked worktree at the worst moment."""
    import os
    try:
        return str(path.relative_to(vault))
    except ValueError:
        return os.path.relpath(str(path), str(vault))


def split_entries(text: str) -> tuple[str, list[str]]:
    """(head, entries) splitting on a `## ` heading, but NEVER inside a fenced code block.

    ★ A fence is content, not structure. The reviewer built a window entry containing a code fence
    with a `## ` line inside it and watched the fence torn across the live file and the archive —
    the opener carried off, the orphaned closing fence promoted to a spurious live heading.
    Byte-preserving and structurally corrupting, which is the same spirit as the defect this row
    was rejected for twice.

    ★ It SLICES the original string rather than reassembling one. The first version joined lines
    back together with `"\n"` and lost a byte per entry — caught by the round-trip property test
    rather than by review, which is the only reason it is not in the archive of somebody's memory.
    `head + "".join(entries) == text` is an identity here, and that is what makes the compaction's
    conservation check an equality instead of an approximation.

    An entry keeps the newline that precedes its heading, because that is the unit `archive.py`
    splits on (`\n## `) and a mismatch there would break its retry-deduplication."""
    fence, offsets, pos = False, [], 0
    for line in text.splitlines(keepends=True):
        if not fence and line.startswith("## "):
            offsets.append(pos - 1 if pos and text[pos - 1] == "\n" else pos)
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = not fence
        pos += len(line)
    if not offsets:
        return text, []
    bounds = offsets + [len(text)]
    return text[:offsets[0]], [text[a:b] for a, b in zip(bounds, bounds[1:])]


WINDOW_OPEN = "<!-- gedaechtnis:window -->"
WINDOW_CLOSE = "<!-- gedaechtnis:/window -->"


def split_window(text: str) -> tuple[str, str, str] | None:
    """(before, window, after), or None when the file does not declare a CLOSED window.

    ★ BOTH markers are required, and the second one is not symmetry — it is the second half of the
    same defect. With an open-ended window (everything after the marker rolls), a realistic Boot
    file loses the sections that sit BELOW its resume point: the repro that proved the standing
    constraints above the marker were safe also showed `## Canon headlines` and `## Pointer map`
    being carried into the archive, because they came after it. There is no way to infer where a
    window stops — a heading is not a fence — so the file says, or nothing is written.

    Both marker LINES stay outside the window, so a compaction can never remove the thing that
    says where compaction may happen."""
    i = text.find(WINDOW_OPEN)
    if i < 0:
        return None
    j = text.find(WINDOW_CLOSE, i + len(WINDOW_OPEN))
    if j < 0:
        return None
    open_nl = text.find("\n", i + len(WINDOW_OPEN))
    start = len(text) if open_nl < 0 else open_nl + 1
    if start > j:
        return None                       # both markers on one line: no window between them
    return text[:start], text[start:j], text[j:]


def roll_window(vault: Path) -> list[dict]:
    """Compact every over-budget Boot file against the BOOT budget. Returns what moved.

    R3's `compact_file` with a different limit — not a second compactor. Its floor share applies
    here for the same reason it applies there: a Boot file compacted to nothing is a region with no
    index, which is worse than an oversize one.

    ★ ONLY BETWEEN THE DECLARED MARKERS, and only in a file that declares both. Everything outside
    them is untouchable whatever the file grows to; a Boot file that does not declare a closed
    window is reported and never written. The first version compacted the whole file by age, and a
    reviewer reproduced what that does to a real Boot file: the `## Standing constraints` section —
    binding NEVER rules — moved into an archive nothing reads at boot. The asymmetry decides the
    default: not compacting costs a large file, compacting the wrong thing costs a rule that
    silently stops being loaded.

    Returns one row per Boot file it looked at — including the ones it could not compact and WHY,
    because a file left OVER budget with no explanation is how the drift this mechanism exists to
    stop happens with the mechanism installed."""
    archive = _archive()
    moved = []
    floor = float(_limits().get("compaction_floor_share"))
    for f in oversize_boot_files(vault):
        if f["state"] != "OVER":
            continue
        live = vault / f["path"]
        before = f["bytes"]
        try:
            text = live.read_text(encoding="utf-8")
        except OSError as e:
            moved.append({"path": f["path"], "entries": 0, "bytes_before": before,
                          "bytes_after": before, "segments": [],
                          "skipped": f"could not be read ({e.__class__.__name__})"})
            continue
        parts = split_window(text)
        if parts is None:
            moved.append({"path": f["path"], "entries": 0, "bytes_before": before,
                          "bytes_after": before, "segments": [],
                          "skipped": f"declares no window — put {WINDOW_OPEN} and "
                                     f"{WINDOW_CLOSE} around the entries that may roll, and "
                                     f"nothing outside them will ever be moved"})
            continue
        fixed, window, tail = parts
        head, entries_ = split_entries(window)
        if not entries_:
            moved.append({"path": f["path"], "entries": 0, "bytes_before": before,
                          "bytes_after": before, "segments": [],
                          "skipped": "its window holds no `## ` entries, so there is nothing that "
                                     "can be moved without cutting a section in half"})
            continue
        target = budget() * floor
        size = len(text.encode("utf-8"))
        n, removed = 0, 0
        # NEVER the last entry: a window emptied to nothing is the same loss by a slower route, and
        # `compact_file`'s floor does not bind when a file has few large entries — measured by the
        # reviewer, who watched a single 39,000 B entry compact a file to 36 bytes.
        while n < len(entries_) - 1 and size - removed > target:
            removed += len(entries_[n].encode("utf-8"))
            n += 1
        if not n:
            moved.append({"path": f["path"], "entries": 0, "bytes_before": before,
                          "bytes_after": before, "segments": [],
                          "skipped": "its window's entries are too large to move even one without "
                                     "emptying it"})
            continue
        outgoing = entries_[:n]
        try:
            archive.append_entries(live, "".join(outgoing))
            kept = "".join(entries_[n:]) if entries_[n:] else "\n"
            archive.atomic_write(live, fixed + head + kept + tail)
        except (OSError, ValueError) as e:
            moved.append({"path": f["path"], "entries": 0, "bytes_before": before,
                          "bytes_after": before, "segments": [],
                          "skipped": f"compaction FAILED ({e.__class__.__name__}: {e})"})
            continue
        try:
            after = live.stat().st_size
        except OSError:
            after = None
        moved.append({"path": f["path"], "entries": n, "bytes_before": before,
                      "bytes_after": after,
                      "segments": [str(s.relative_to(vault)) for s in archive.segments(live)]})
    return moved
