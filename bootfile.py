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


def always_loaded(cwd: str | None = None) -> set[str]:
    """Every file the CURRENT @-import chain actually pulls in, as resolved paths.

    ★ MEMBERSHIP IS DERIVED, NEVER INFERRED FROM A NAME, and that distinction cost a real vault.
    This module protected a Boot file by its FILENAME, so a vault's top-level index — @-imported by
    every session, the file the whole vault hangs off, but not named like a Boot file — fell
    straight through to the generic compactor and was cut from 87 lines to 17. Every session for
    the next fifteen hours booted on the remains.

    A file is protected here because a session LOADS it, which is the property that makes
    compacting it dangerous. The walk is `session_start.boot_chain_files` — the same one the boot
    facts and the byte arm use, never a second @-import resolver.

    On any failure this returns an EMPTY set, and every caller treats empty as "protect nothing
    extra" rather than "protect everything": a guard that silently expanded to the whole vault when
    it could not read the chain would stop all compaction and look exactly like a healthy quiet
    vault. The failure is reported by the caller instead."""
    return {str(p) for p, _size in chain_members(cwd)}


def chain_members(cwd: str | None = None) -> list[tuple[Path, int]]:
    """(resolved path, bytes) for every file the current @-import chain pulls in.

    `always_loaded` is this list without the sizes, and both are one walk rather than two: the
    moment two callers resolve the chain differently, one of them is protecting a set the other
    one is measuring. Empty on any failure, for the reason `always_loaded` documents."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
    try:
        import config
        import session_start
        entry = [config.USER_MEMORY]
        if cwd:
            entry.append(Path(cwd) / "CLAUDE.md")
        return [(Path(p).resolve(), int(size))
                for p, size in session_start.boot_chain_files(entry)]
    except Exception:
        return []


def is_protected(path: Path, chain: set[str] | None = None, cwd: str | None = None) -> str | None:
    """Why this file must not be compacted by a generic pass, or None.

    Two reasons, and they are the same reason at different scales: the file is a Boot file (a
    region's always-loaded index), or the session's own @-import chain loads it. Either way its
    sections are structural rather than chronological, and moving its oldest ones moves whatever
    the author put at the top — which in an always-loaded file is where standing rules live."""
    if path.name == BOOT_FILE:
        return "it is a Boot file"
    members = always_loaded(cwd) if chain is None else chain
    try:
        if str(path.resolve()) in members:
            return "it is @-imported by the session's own boot chain"
    except OSError:
        pass
    return None


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
    import common
    # `common.VAULT` as an ATTRIBUTE, never `from common import VAULT`: since BLASTRADIUS-1's
    # per-call resolution it is a PEP 562 module getter, and a `from` import binds its value once —
    # reintroducing, in this line, precisely the freeze that compacted a real vault.
    if Path(vault).resolve() != Path(common.VAULT).resolve():
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


def chain_budget() -> int:
    """The @-import CHAIN budget — `maintenance`'s `boot_bytes` arm reads the same key. Not
    `budget()`, which is the per-Boot-file ceiling: they are different numbers about different
    things, and conflating them is how a region with a healthy Boot file would look over budget."""
    return int(_limits().get("boot_budget_bytes"))


def chain_graduation(cwd: str | None = None,
                     chain: list[tuple[Path, int]] | None = None) -> dict:
    """The regions an over-budget boot chain is carrying, and what would bring each one down.

    ★ THE LOOP THIS NAMES (row A1 §3, measured at `medium` load: 80 of 90 days over budget, peak
    191,029 B). `maintenance`'s `boot_bytes` arm fires when the @-import chain crosses the budget,
    so a cleanup is due every session for ever. `cleanup.propose()` then SKIPS the file that grew,
    because `is_protected` says a file a session LOADS is not tidied by an unattended pass. And the
    one thing that compacts a boot file, `roll_window`, needs a `Kernel.md` with a declared window —
    which a fresh, ungraduated install has neither of. **The trigger names the budget and every
    remedy is forbidden from touching what breached it.**

    Everything here is PROPOSED, never done: distilling a region into an index is a judgment about
    what matters, and a pass that did it unattended would be the 00:43 incident class — an
    automatic rewrite of a file every session loads. Nothing here rewrites an import line either.

    ★ A `Kernel.md` EXISTING IS NOT THE REMEDY — POINTING THE IMPORT AT IT IS, and the first
    version of this function got that wrong in the direction that hurts. It excluded any region
    that already had a Boot file, on the reasoning that such a region "has the remedy". The row's
    reviewer measured what that does: write the `Kernel.md` the sentence asks for, and the chain
    does not move by a single byte, because the repo's `CLAUDE.md` still @-imports the bodies —
    the arm goes on firing every session and the sentence explaining it DISAPPEARS. A silenced
    loop is worse than a loud one. So a region is named whenever the chain loads its bodies, and
    the Boot file's presence only decides WHICH HALF of the remedy is left to do.

    Returns `{over_budget, chain_bytes, budget, regions: [{region, bytes, files, has_boot_file,
    remedy}]}`. `regions` is populated ONLY when the chain is over budget: a region in the chain is
    a fact about the vault's shape, and becomes a FINDING only once the chain has crossed. Both
    callers — `cleanup.propose` and the facts line — read this one answer rather than each deciding
    for itself when the conditions are met.

    Each chain member is attributed to its NEAREST containing region, never to every region above
    it. Vaults nest — a region can hold a sub-region, and both carry a `Map.md` — and charging a
    file to both double-counts it: the region totals then add up to more than the chain they came
    from, and the remedy names an ancestor whose Boot file would not replace that import at all."""
    members = chain_members(cwd) if chain is None else chain
    total = sum(size for _path, size in members)
    budget_ = chain_budget()
    out = {"over_budget": total > budget_, "chain_bytes": total, "budget": budget_,
           "regions": []}
    if not out["over_budget"]:
        return out
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
    try:
        import common
        vault = Path(common.VAULT).resolve()
    except Exception:
        return out
    # Resolved on BOTH sides. `regions()` hands back paths spelled as the module's own VAULT, and
    # `_rel` against a resolved vault then falls through to `os.path.relpath` and emits a
    # `../../…` traversal as the region's NAME — which is what the facts line prints, beside a
    # second line naming the same region correctly. `/tmp` against `/private/tmp` on macOS is the
    # everyday case, and the row's reviewer reproduced it under a symlinked vault.
    resolved = []
    for region in regions(vault):
        try:
            resolved.append(region.resolve())
        except OSError:
            continue
    by_region: dict[str, dict] = {}
    for path, size in members:
        nearest = None
        for rdir in resolved:
            if rdir in path.parents and (nearest is None or len(rdir.parts) > len(nearest.parts)):
                nearest = rdir
        if nearest is None:
            continue
        if path.name == BOOT_FILE:
            continue        # the Boot file IS what a graduated region should be loading
        rel = _rel(vault, nearest)
        row = by_region.setdefault(
            rel, {"region": rel, "bytes": 0, "files": [],
                  "has_boot_file": (nearest / BOOT_FILE).is_file()})
        row["bytes"] += size
        row["files"].append(_rel(vault, path))
    for row in by_region.values():
        row["remedy"] = _remedy(row)
    out["regions"] = sorted(by_region.values(), key=lambda r: -r["bytes"])
    return out


def _remedy(row: dict) -> str:
    """What would actually bring this region's share of the chain down, in one sentence.

    Two states, because a Boot file that nothing imports is not a remedy — it is a second file.

    **Neither sentence is a promise that anything here does it UNPROMPTED, and since GRADPATH-2
    both name the path that does it when you ask.** Before that row there was none: the trigger
    named a budget and the remedy named work no shipped command could perform, which is the loop
    the whole graduation path was built to close. Naming `/gedaechtnis-graduate` here is not a
    convenience — a remedy sentence that does not say HOW is the reason someone reads it for months
    and does nothing."""
    if row["has_boot_file"]:
        return (f"{row['region']}/{BOOT_FILE} already exists, but the chain still loads the bodies "
                f"beside it — point the @-import in your repo's CLAUDE.md at the {BOOT_FILE} "
                f"instead. `/gedaechtnis-graduate` does that rewrite, and only when you apply it.")
    return (f"give {row['region']} a {BOOT_FILE} with a declared window and point the @-import in "
            f"your repo's CLAUDE.md at it instead of these bodies — it REPLACES them in what a "
            f"session loads. `/gedaechtnis-graduate` measures the region, drafts one for you to "
            f"read, and writes nothing until you say so: what to keep is a judgment about what "
            f"matters, and it stays yours.")


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
    """`archive.split_entries` — the package's ONE entry splitter. This module had its own for two
    review rounds; the third found the same fence-blind split still live in three other callers,
    which is what moved the definition rather than the fix."""
    return _archive().split_entries(text)


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


def roll_window(vault: Path, apply: bool = True, max_share: float | None = None,
                _before_write=None) -> list[dict]:
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
    stop happens with the mechanism installed.

    The destructive-chore gate (`hooks/destructive.py`): with `apply=False` it plans and writes
    nothing, returning rows marked `proposed`; `max_share` caps the share of the file one pass may
    move; and the file is read again just before the write and left alone if it changed."""
    archive = _archive()
    moved = []
    floor = float(_limits().get("compaction_floor_share"))
    for f in oversize_boot_files(vault):
        if f["state"] != "OVER":
            continue
        live = vault / f["path"]
        before = f["bytes"]
        try:
            raw = live.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
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
        cap = None if max_share is None else size * max_share
        while n < len(entries_) - 1 and size - removed > target:
            nxt = len(entries_[n].encode("utf-8"))
            if cap is not None and removed + nxt > cap:
                break
            removed += nxt
            n += 1
        if not n:
            why = ("its window's entries are too large to move even one without emptying it"
                   if cap is None or len(entries_) < 2
                   or len(entries_[0].encode("utf-8")) <= cap else
                   f"its oldest window entry alone is more than the {max_share:.0%} of the file "
                   f"one pass may move (max_move_share)")
            moved.append({"path": f["path"], "entries": 0, "bytes_before": before,
                          "bytes_after": before, "segments": [], "skipped": why})
            continue
        outgoing = entries_[:n]
        headings = [next((ln for ln in e.splitlines() if ln.strip()), "").lstrip("# ").strip() for e in outgoing]
        if not apply:
            moved.append({"path": f["path"], "entries": n, "bytes_before": before,
                          "bytes_after": before, "segments": [], "proposed": True,
                          "headings": headings})
            continue
        try:
            if _before_write is not None:            # test seam: a writer arriving mid-pass
                _before_write(live)
            if archive.digest(live.read_bytes()) != archive.digest(raw):
                raise archive.ChangedSinceRead(f"{live.name} changed after the pass read it; "
                                               f"nothing was written")
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
                      "bytes_after": after, "headings": headings,
                      "segments": [str(s.relative_to(vault)) for s in archive.segments(live)]})
    return moved
