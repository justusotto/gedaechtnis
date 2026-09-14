#!/usr/bin/env python3
"""bootfile.py — the Boot file: when a region needs one, how it stays small, and when it has gone stale.

A region that has grown past a few files costs a session real context at every boot. The answer is a
**Boot file** (`Kernel.md`): a short, always-loaded index that REPLACES the bodies in the import
chain, with the bodies read on demand. Three mechanisms keep that honest, and each is useless
without the other two.

**Graduation.** A region whose boot payload has outgrown the budget and has no Boot file is
PROPOSED one. Never written for it: distilling a region into an index is a judgment about what
matters, and a machine that did it would put a confident summary nobody wrote into every session.

**The rolling window.** A Boot file is a fixed-size window, not an append log. Left alone it becomes
the thing it was created to replace — the vault this grew from watched three of its own drift 50-70%
over budget while a status table printed OVER for weeks and nothing acted. So it is compacted
against the BOOT budget, through R3's `archive.compact_file` with a different limit rather than a
second compactor.

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
    """A region is a directory carrying `Map.md` — `init.py`'s own definition, not a second one."""
    out = []
    try:
        for d in sorted(p for p in vault.rglob("*") if p.is_dir()):
            rel = d.relative_to(vault)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if (d / "Map.md").is_file():
                out.append(d)
    except OSError:
        return []
    return out


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
        if "-archive" in p.stem or p.stem.endswith(("-fixed", "-resolved")):
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
            out.append({"region": str(region.relative_to(vault)), "bytes": total,
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
        out.append({"path": str(f.relative_to(vault)), "bytes": n, "state": state,
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
        rel_boot = str(f.relative_to(vault))
        own = _last_commit(vault, rel_boot)
        if own is None:
            unchecked.append(f"{rel_boot} (never committed, or git could not be read)")
            continue
        moved = []
        for name in WATCHED_BODIES:
            body = region / name
            if not body.is_file():
                continue
            rel = str(body.relative_to(vault))
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


def roll_window(vault: Path) -> list[dict]:
    """Compact every over-budget Boot file against the BOOT budget. Returns what moved.

    R3's `compact_file` with a different limit — not a second compactor. Its floor share applies
    here for the same reason it applies there: a Boot file compacted to nothing is a region with no
    index, which is worse than an oversize one.

    ★ It moves the OLDEST entries, which is the mechanism's one sharp edge and belongs in the
    report rather than in a docstring nobody reads: a standing rule someone wrote at the TOP of a
    Boot file is the first thing the window moves out. The compaction names every entry it moved so
    that is visible, and moves nothing anywhere but into the file's own archive sidecar."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import archive
    moved = []
    floor = float(_limits().get("compaction_floor_share"))
    for f in oversize_boot_files(vault):
        if f["state"] != "OVER":
            continue
        live = vault / f["path"]
        before = f["bytes"]
        try:
            n = archive.compact_file(live, limit=budget(), floor_share=floor)
        except (OSError, ValueError):
            continue
        if n:
            try:
                after = live.stat().st_size
            except OSError:
                after = None
            moved.append({"path": f["path"], "entries": n, "bytes_before": before,
                          "bytes_after": after,
                          "segments": [str(s.relative_to(vault)) for s in archive.segments(live)]})
    return moved
