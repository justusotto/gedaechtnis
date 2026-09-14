#!/usr/bin/env python3
"""maintenance.py — at session end, decide whether the vault is due a cleanup or a synthesis pass.

Both passes are periodic, both are OR-triggered, and both have the same shape: read a date, measure
what has happened since it, say so. They therefore live in ONE hook over ONE state file, which is
also what lets the synthesis agent (a later row) be built against state this hook already computes
rather than a second, parallel trigger of its own.

## The defect this is a port of, named

In the vault this grew from, the equivalent blocks read their "last pass" date out of a `last_lustrum:`
line in the frontmatter of one particular memory file. A vault that has no such file, or has one with
no such line, took `sys.exit(0)` — quietly. The triggers had therefore never fired for anyone except
the one vault whose file happened to carry the key. The state moves here, to `<state>/maintenance.json`,
for two reasons: it works on a vault nobody has hand-edited, and a trigger's bookkeeping is not
something a person wrote and not something a model should ever read as memory.

## Four rules this hook follows

1. **State lives in the state directory, never in a memory file.** Machine-written, machine-read,
   absent from the boot chain.
2. **A missing state file is FIRST RUN — not an error, and not a fired trigger.** It is created with
   today's date and nothing fires. The failure to avoid is the opposite of the vault's: a fresh
   install that opens by telling its user their empty vault is untidy has lied to them.
3. **The facts line states the trigger, never the advice.** Same discipline as the boot-cost fact:
   it reports what is true. What a session does about it is a separate decision.
4. **Every threshold is read from `rules/limits.json`** through `limits.py`. No number here is a
   literal, and a test asserts the hook and the config agree.

## The arms

**Cleanup** (OR): days since the last pass ≥ `cleanup_trigger_days` · substantive vault commits since
it ≥ `cleanup_trigger_commits` (the hook's own bookkeeping subjects excluded, so the count is of real
content) · any role file over its per-role LINE limit · the session's @-import chain over
`boot_budget_bytes`.

The byte arm is the chain TOTAL, not a per-file constant, and that is a deliberate change from the
vault's version. What the budget means is what a session pays to boot; a per-file threshold both
misses ten files at 90% of it and alarms on one big file nobody imports. Measuring the chain makes
membership intrinsic: a file outside the chain cannot move the number, which is the same repair the
vault's byte arm eventually needed and got by bolting membership onto a stem test.

**Synthesis** (OR): days since the last pass ≥ `synthesis_trigger_days` · new `## ` entries across
the regions' `Errata.md`/`Patterns.md` since it ≥ `synthesis_trigger_entries` · a topology event — a
new top-level vault surface since the date.

A region is a vault directory carrying `Map.md`, which is `init.py`'s own definition of one. There is
no hardcoded list of region names anywhere in this file.

## What it writes

`<state>/maintenance.json`, and nothing else — no vault file, no commit, no git write. The hook that
computes a trigger has no business touching the surface the trigger is about.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import limits
import session_start
from common import read_input, log, guarded, VAULT, STATE

STATE_FILE = "maintenance.json"
SCHEMA_VERSION = 1

# The hook's own commit subjects. A bookkeeping commit is not content, and counting it would make
# the volume arm measure the hook rather than the vault. Prefix-matched, as the vault's is.
BOOKKEEPING_SUBJECTS = ("session-end auto-commit", "subagent auto-commit", "Maintenance status:")

# Evidence is capped: this file is machine-read, but its lines reach a session through the facts
# line, and an unbounded detector grows the very boot payload the byte arm exists to protect.
EVIDENCE_CAP = 8

ROLE_FILE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*\.md$")


# ------------------------------------------------------------------ state ----
def state_path() -> Path:
    return STATE / STATE_FILE


def today() -> str:
    return time.strftime("%Y-%m-%d")


def read_state() -> dict | None:
    """The state, or None when there is none (or it cannot be read as an object)."""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def first_run_state(day: str) -> dict:
    return {"version": SCHEMA_VERSION, "created": day, "last_cleanup": day, "last_synthesis": day}


# ------------------------------------------------------------------- git -----
def sh(args, timeout=20) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def substantive_commits(since: str) -> tuple[int, int]:
    """(substantive, total) vault commits since `since`. The bare date would undercount — a
    `--since=YYYY-MM-DD` with no time component is read as that date's current clock time — so the
    00:00:00 is explicit, exactly as the vault learned to write it."""
    rc, out = sh(["git", "-C", str(VAULT), "log", f"--since={since} 00:00:00", "--format=%s"])
    if rc != 0:
        return 0, 0
    subjects = [l for l in out.splitlines() if l.strip()]
    return sum(1 for s in subjects if not s.startswith(BOOKKEEPING_SUBJECTS)), len(subjects)


# ---------------------------------------------------------------- regions ----
def regions() -> list[Path]:
    """Every vault directory carrying `Map.md` — init.py's own definition of a region, so a vault
    laid out flat and one laid out in umbrellas are both read correctly and neither is named here.
    `Global/` is excluded: it is where a synthesis pass PROMOTES to, never a source it reads."""
    found = []
    try:
        for mapfile in VAULT.rglob("Map.md"):
            rel = mapfile.relative_to(VAULT)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if rel.parts[0] == "Global":
                continue
            found.append(mapfile.parent)
    except OSError:
        return []
    return sorted(found)


def new_region_entries(since: str) -> int:
    """`## ` headings added to any region's Errata.md / Patterns.md since `since`.

    A unified diff's file header is `+++ b/…`, which cannot match the four characters `+## `, so
    counting that prefix counts heading additions and nothing else."""
    files = []
    for region in regions():
        for name in ("Errata.md", "Patterns.md"):
            p = region / name
            if p.is_file():
                files.append(str(p.relative_to(VAULT)))
    if not files:
        return 0
    rc, out = sh(["git", "-C", str(VAULT), "log", f"--since={since} 00:00:00", "--format=", "-p",
                  "--", *files], timeout=40)
    if rc != 0:
        return 0
    return sum(1 for line in out.splitlines() if line.startswith("+## "))


def topology_events(since: str) -> list[str]:
    """New top-level vault surfaces since `since`. A surface is NEW when it has no commit before
    the date — a directory that merely gained a file today is not a new surface, and the negative
    control for this arm is exactly that case."""
    # NEWNESS IS UNDEFINED ON A VAULT WITH NO HISTORY BEFORE THE WINDOW. A vault younger than the
    # window has every surface "appearing" inside it, so the arm would fire on a fresh install's
    # own region — the exact false alarm rule 2 forbids, arriving one day later than the first-run
    # guard covers. With nothing before `since` there is nothing for a surface to be new RELATIVE
    # TO, and the arm reports nothing rather than everything.
    rc0, prior_any = sh(["git", "-C", str(VAULT), "log", "-1", "--format=%H",
                         f"--before={since} 00:00:00"])
    if rc0 != 0 or not prior_any.strip():
        return []
    rc, out = sh(["git", "-C", str(VAULT), "log", f"--since={since} 00:00:00", "--diff-filter=A",
                  "--name-only", "--format="], timeout=40)
    if rc != 0:
        return []
    tops = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("."):
            continue
        parts = line.split("/")
        if len(parts) > 1:
            tops.add(parts[0])
    events = []
    for top in sorted(tops):
        rc2, prior = sh(["git", "-C", str(VAULT), "log", "-1", "--format=%H",
                         f"--before={since} 00:00:00", "--", top + "/"])
        if rc2 == 0 and not prior.strip():
            events.append(f"new top-level surface `{top}/`")
    return events


# ------------------------------------------------------------------- size ----
def oversize_role_files() -> list[dict]:
    """Role files over their per-role LINE limit. A stem with no entry in the table has no line
    limit and is never flagged by this arm — that is the table's meaning, not an omission."""
    table = limits.get("role_soft_limits_lines")
    if not isinstance(table, dict) or not table:
        return []
    out = []
    try:
        candidates = list(VAULT.rglob("*.md"))
    except OSError:
        return []
    for md in candidates:
        try:
            rel = md.relative_to(VAULT)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        limit = table.get(md.stem)
        if not isinstance(limit, int):
            continue
        try:
            n_lines = sum(1 for _ in md.open(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if n_lines > limit:
            out.append({"path": str(rel), "lines": n_lines, "limit": limit})
    out.sort(key=lambda r: r["lines"] - r["limit"], reverse=True)
    return out


def boot_chain_now(cwd: str) -> tuple[int, int, list[dict]]:
    """(bytes, files, the largest members) for the chain this session actually booted with.

    The walk is `session_start.boot_chain_files` — the same one that produces the boot-cost fact,
    not a second implementation of @-import resolution. Two answers to "what does a session load"
    inside one package is the duplicated-fact failure, and this arm exists to watch that number."""
    members = session_start.boot_chain_files([config.USER_MEMORY, Path(cwd) / "CLAUDE.md"])
    total = sum(size for _, size in members)
    biggest = [{"path": str(p), "bytes": size}
               for p, size in sorted(members, key=lambda m: -m[1])[:EVIDENCE_CAP]]
    return total, len(members), biggest


# --------------------------------------------------------------- compute -----
def days_between(earlier: str, later: str) -> int | None:
    import datetime
    try:
        return (datetime.date.fromisoformat(later) - datetime.date.fromisoformat(earlier)).days
    except ValueError:
        return None


def compute(state: dict, cwd: str, day: str) -> dict:
    """Both trigger sets, as data. Pure enough to test: everything it reads is the vault, the
    chain and the limits file; everything it returns is JSON."""
    cleanup_days = days_between(str(state.get("last_cleanup", day)), day)
    synth_days = days_between(str(state.get("last_synthesis", day)), day)

    substantive, total = substantive_commits(str(state.get("last_cleanup", day)))
    oversize = oversize_role_files()
    boot_bytes, boot_files, biggest = boot_chain_now(cwd)

    d_days = limits.get("cleanup_trigger_days")
    d_commits = limits.get("cleanup_trigger_commits")
    budget = limits.get("boot_budget_bytes")

    cleanup_arms = {
        "time": {"fired": cleanup_days is not None and cleanup_days >= d_days,
                 "days": cleanup_days, "threshold": d_days},
        "volume": {"fired": substantive >= d_commits, "commits": substantive,
                   "total_commits": total, "threshold": d_commits},
        "size_lines": {"fired": bool(oversize), "files": oversize[:EVIDENCE_CAP],
                       "n_files": len(oversize)},
        "boot_bytes": {"fired": boot_bytes > budget, "bytes": boot_bytes,
                       "n_files": boot_files, "threshold": budget, "largest": biggest},
    }

    entries = new_region_entries(str(state.get("last_synthesis", day)))
    events = topology_events(str(state.get("last_synthesis", day)))
    s_days = limits.get("synthesis_trigger_days")
    s_entries = limits.get("synthesis_trigger_entries")
    synthesis_arms = {
        "time": {"fired": synth_days is not None and synth_days >= s_days,
                 "days": synth_days, "threshold": s_days},
        "entries": {"fired": entries >= s_entries, "entries": entries, "threshold": s_entries},
        "events": {"fired": bool(events), "events": events[:EVIDENCE_CAP]},
    }

    return {
        "computed": day,
        "cleanup": {"due": any(a["fired"] for a in cleanup_arms.values()), "arms": cleanup_arms},
        "synthesis": {"due": any(a["fired"] for a in synthesis_arms.values()),
                      "arms": synthesis_arms},
    }


# ------------------------------------------------------------------ facts ----
def facts_lines(doc: dict) -> list[str]:
    """What a session is told at its next start — only about an arm that actually fired.

    Nothing fired means NOTHING IS SAID. A maintenance line printed every session is a line every
    session learns to skip, and the one time it matters it reads exactly like the other hundred."""
    out = []
    cleanup = doc.get("cleanup") or {}
    synth = doc.get("synthesis") or {}
    if cleanup.get("due"):
        arms = cleanup.get("arms") or {}
        why = []
        t, v = arms.get("time") or {}, arms.get("volume") or {}
        sz, bb = arms.get("size_lines") or {}, arms.get("boot_bytes") or {}
        if t.get("fired"):
            why.append(f"{t.get('days')} days since the last pass (threshold {t.get('threshold')})")
        if v.get("fired"):
            why.append(f"{v.get('commits')} substantive vault commits since it "
                       f"(threshold {v.get('threshold')})")
        if sz.get("fired"):
            why.append(f"{sz.get('n_files')} file(s) over their role line limit")
        if bb.get("fired"):
            why.append(f"the @-import chain is {bb.get('bytes'):,} B over a "
                       f"{bb.get('threshold'):,} B budget")
        out.append("- Maintenance: a cleanup pass is due — " + "; ".join(why) + ".")
    if synth.get("due"):
        arms = synth.get("arms") or {}
        why = []
        t, e, ev = arms.get("time") or {}, arms.get("entries") or {}, arms.get("events") or {}
        if t.get("fired"):
            why.append(f"{t.get('days')} days since the last pass (threshold {t.get('threshold')})")
        if e.get("fired"):
            why.append(f"{e.get('entries')} new region Errata/Patterns entries since it "
                       f"(threshold {e.get('threshold')})")
        if ev.get("fired"):
            why.append("; ".join(ev.get("events") or []))
        out.append("- Maintenance: a synthesis pass is due — " + "; ".join(why) + ".")
    if out:
        out.append(f"- Maintenance state: {state_path()} (computed at the last session end).")
    return out


# ------------------------------------------------------------------- main ----
def main() -> None:
    inp = read_input()
    cwd = inp.get("cwd") or os.getcwd()
    day = today()
    if not VAULT.is_dir():
        log("maintenance", f"no vault at {VAULT}; nothing computed")
        return

    STATE.mkdir(parents=True, exist_ok=True)
    state = read_state()
    if state is None:
        # FIRST RUN. The dates start today, so nothing has "been due since" a date the user never
        # had. Written, and then the hook stops: no arm is evaluated against a state born this second.
        fresh = first_run_state(day)
        state_path().write_text(json.dumps(fresh, indent=1) + "\n", encoding="utf-8")
        log("maintenance", f"first run: state created at {state_path()}, nothing evaluated")
        return

    doc = dict(state)
    doc.update(compute(state, cwd, day))
    doc["version"] = SCHEMA_VERSION
    new = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    old = None
    try:
        old = state_path().read_text(encoding="utf-8")
    except OSError:
        pass
    # Idempotent on the numbers, not on the clock: `computed` is the only key that moves by itself,
    # so it is excluded from the comparison. A file rewritten every session would make its own mtime
    # meaningless as a signal of when anything actually changed.
    def sans_stamp(text):
        try:
            d = json.loads(text)
            d.pop("computed", None)
            return json.dumps(d, indent=1, sort_keys=True)
        except (ValueError, AttributeError):
            return text
    if old is None or sans_stamp(old) != sans_stamp(new):
        state_path().write_text(new, encoding="utf-8")
    log("maintenance", f"cleanup_due={doc['cleanup']['due']} synthesis_due={doc['synthesis']['due']}")


if __name__ == "__main__":
    guarded(main)
