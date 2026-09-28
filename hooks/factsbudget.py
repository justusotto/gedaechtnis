#!/usr/bin/env python3
"""factsbudget.py — the SessionStart facts block has a budget (BOOTFACTS-1).

The facts block is a second boot file: every session reads it, and nothing budgeted it. Measured
2026-09-26 over the 20 newest real sessions (their transcripts' SessionStart attachment, the
persisted file where there was one): 10,922–12,140 B, median 11,531 B. Past a size the harness
does not inline a hook's output at all — it saves it to a file and shows the model a 2 KB
PREVIEW. Over the newest ~400 sessions the largest block shown whole was 9,835 chars and the
smallest one cut to a preview 10,127, so the limit sits at ~10,000 chars (`facts_inline_limit_chars`).
All 20 were over it: in each of them the model saw the first ~2 KB — the lane, the memory-file
lists, the delete door — and none of the boot-file alarms, maintenance triggers, managed-session
states, the ledger rows or the operating rules below them.

Two things follow, and this module does both:

  1. A CAP (`limits.facts_max_bytes`, 0 = none). Over it, the LONG-TAIL lines move behind
     `/gedaechtnis-status` (which prints them from `<state>/facts-moved-<sid>.txt`), one tier at a
     time, whole tiers only, until the block fits. The tiers, in the order they go:
        memory-file lists            a static listing of the lane's regions; recomputable any time
        other regions' Boot files    size/STALE alarms for regions outside this lane's partition
        worktree list                the open worktrees of this repo, one name each
        state-file pointers          where maintenance / boot-check / limits / topology came from
     A line that is not in a tier never moves. One pointer line takes their place, second in the
     block, so it is inside the preview even when the block is still too long.
  2. A WARNING, cap or no cap: a block still over the inline limit gets a second line saying the
     model is reading a preview, which file holds the rest, and the cap line to set.

The cap is the vault owner's to set (the `limits` object of config.json); the shipped value is 0
so a stranger's install behaves as before until it opts in.
"""
from __future__ import annotations
import re
from pathlib import Path

import common
import limits

PROPOSED_CAP = 8000
POINTER_BYTES = 250          # room for the one pointer line that replaces the moved lines
TIERS = (
    ("memory-file lists", lambda ln, own: ln.startswith("- Memory files in ")),
    ("other regions' Boot-file alarms", lambda ln, own: ln.startswith("- Boot file: ") and not own(ln)),
    ("the worktree list", lambda ln, own: ln.lstrip().startswith("- Worktrees still open")),
    ("state-file pointers", lambda ln, own: ln.startswith(("- Maintenance state:", "- Boot check state:",
                                                           "- Vault limits", "- Vault topology"))),
)
# The region is the first token after "Boot file: " in three of maintenance.py's four shapes; the
# UNCHECKED shape names its file at the END ("… never as fresh: <R>/Kernel.md (…)." or a body,
# "<R>/Position.md (…)"), so the directory of every `<path>/<File>.md` in the line counts as well
# (review must-fix: it read "freshness" as a region).
_BOOT_REGION = re.compile(r"^- Boot file: (\S+?)(?:/Kernel\.md)?\s")
_BOOT_PATH = re.compile(r"([A-Za-z0-9][\w.-]*(?:/[\w.-]+)*)/[\w.-]+\.md\b")


def cap() -> int:
    try:
        return max(0, int(limits.get("facts_max_bytes", 0) or 0))
    except (TypeError, ValueError):
        return 0


def inline_limit() -> int:
    try:
        return max(0, int(limits.get("facts_inline_limit_chars", 10000) or 0))
    except (TypeError, ValueError):
        return 10000


def _owned(prefixes: list):
    # A marker may declare `Studio/` or `Studio`; `path_in_partition` matches the bare form.
    bare = [p.rstrip("/") for p in (prefixes or []) if p.strip("/")]

    def own(line: str) -> bool:
        m = _BOOT_REGION.match(line)
        regions = ([m.group(1)] if m else []) + _BOOT_PATH.findall(line)
        return any(common.path_in_partition(r + "/Kernel.md", bare) for r in regions)
    return own


def _size(lines: list, tail: list) -> int:
    return len("\n".join(lines + tail).encode("utf-8"))


def fit(lines: list, tail: list, prefixes: list, max_bytes: int) -> tuple:
    """(kept lines, moved lines, the tier names moved). `tail` (the operating rules) counts toward
    the size and is never moved. With no cap, or under it, nothing moves."""
    if max_bytes <= 0 or _size(lines, tail) <= max_bytes:
        return list(lines), [], []
    own = _owned(prefixes)
    kept, moved, names = list(lines), [], []
    for name, test in TIERS:
        hit = [ln for ln in kept if test(ln, own)]
        if not hit:
            continue
        kept = [ln for ln in kept if not test(ln, own)]
        moved.extend(hit)
        names.append(f"{name} ({len(hit)})")
        if _size(kept, tail) + POINTER_BYTES <= max_bytes:
            break
    return kept, moved, names


def save_moved(sid: str, cwd: str, moved: list) -> Path | None:
    """Write the moved lines where `/gedaechtnis-status` reads them. None when it cannot."""
    try:
        import rootguard
        f = common.STATE / f"facts-moved-{common.safe_sid(sid)}.txt"
        f = rootguard.permit(f, why="the facts lines this session's boot block moved behind /gedaechtnis-status")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"cwd: {cwd}\n" + "".join(ln + "\n" for ln in moved), encoding="utf-8")
        return f
    except Exception:                                        # noqa: BLE001 — a fact organ never costs the block
        return None


def apply(lines: list, tail: list, prefixes: list, sid: str, cwd: str) -> list:
    """The facts block's lines after the budget: long-tail tiers moved out over the cap, and a
    preview warning when the block is still past the harness's inline limit. Never raises."""
    try:
        c = cap()
        kept, moved, names = fit(lines, tail, prefixes, c)
        head, rest = kept[:1], kept[1:]
        notes = []
        if moved or c:
            # With a cap set, every start rewrites this session's file — empty when nothing moved —
            # so a resume never shows the lines an earlier start of the same session moved.
            where = save_moved(sid, cwd, moved)
        if moved:
            size = len("\n".join(moved).encode("utf-8"))
            notes.append(f"- Facts block capped at {c:,} B (`limits.facts_max_bytes`): {len(moved)} line(s), "
                         f"{size:,} B moved behind `/gedaechtnis-status` — " + ", ".join(names) +
                         ("." if where else " (NOT saved: the state directory refused the write)."))
        total = len("\n".join(head + notes + rest + tail))
        lim = inline_limit()
        if lim and total > lim:
            notes.append(f"- Facts block is {total:,} chars, over the harness's ~{lim:,}-char inline limit: "
                         "the model sees only a 2 KB preview and the rest sits in the saved file named "
                         "below — read it. " + (f"Set `\"facts_max_bytes\": {PROPOSED_CAP}` in config.json "
                                               "`limits` to move the long tail out." if not c else
                                               "The cap is set and the block is still over it: lower it."))
        return head + notes + rest
    except Exception as e:                                   # noqa: BLE001
        common.log("session_start", f"factsbudget failed: {e}")
        return list(lines)


def moved_text(sid: str | None, cwd: str) -> str | None:
    """What `/gedaechtnis-status` prints: this session's moved lines, else the newest for this cwd."""
    cands = []
    if sid:
        cands.append(common.STATE / f"facts-moved-{common.safe_sid(sid)}.txt")
    else:
        try:
            cands = sorted(common.STATE.glob("facts-moved-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            cands = []
    for f in cands:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        first, _, body = text.partition("\n")
        if sid or first == f"cwd: {cwd}":
            return body.rstrip("\n") or None
    return None
