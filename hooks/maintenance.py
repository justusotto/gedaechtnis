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

`<state>/maintenance.json` — the triggers' own bookkeeping, machine-written and outside the boot
chain — AND, when a memory file has grown past the bound that decides whether the search can read
it, the compaction of that file into its archive segments.

That second half is deliberate and was not here first. A hook that computed a trigger and touched
nothing was the right shape while compaction lived elsewhere; once it lives here, "writes nothing"
would be a docstring asserting the opposite of the code. **The compacted paths are COMMITTED**, by
handing them to `commit.py`'s `auto_commit` seam rather than writing any git law here: a hook's own
file writes are invisible to the touched set (`commit.py`: "a script the session ran — is not in
the touched set and is not committed"), so compaction that was not explicitly committed would leave
the vault permanently dirty, one growing set of untracked archive segments at a time, and dirt of
that kind quietly stops other machinery rather than announcing itself.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import limits
import session_start
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import archive
import bootfile
import recall
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
    """(rc, stdout). A non-zero rc is LOGGED, because of what the callers do with it.

    Every git-backed arm returns 0 or [] when its command fails, and 0 is also what it returns when
    the vault genuinely had no activity. Those are opposite facts with identical output: a missing
    git, a VAULT that is not a repository, a corrupted `.git` or a timeout all render as a
    perfectly healthy quiet vault. The arms cannot distinguish them — but they can refuse to be
    silent about it, and `checked` below carries the distinction into the state file so a reader
    is never shown a zero that was never actually counted."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=timeout)
        if p.returncode != 0:
            log("maintenance", f"git check FAILED rc={p.returncode}: {' '.join(args[:6])} "
                               f"— the arm using it reports UNCHECKED, not zero")
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError) as e:
        log("maintenance", f"git check FAILED ({e.__class__.__name__}): {' '.join(args[:6])} "
                           f"— the arm using it reports UNCHECKED, not zero")
        return 1, ""


def substantive_commits(since: str) -> tuple[int, int, bool]:
    """(substantive, total, checked) vault commits since `since`. `checked` is False when the git
    command itself failed — the counts are then meaningless and must never be read as zero. The bare date would undercount — a
    `--since=YYYY-MM-DD` with no time component is read as that date's current clock time — so the
    00:00:00 is explicit, exactly as the vault learned to write it."""
    rc, out = sh(["git", "-C", str(VAULT), "log", f"--since={since} 00:00:00", "--format=%s"])
    if rc != 0:
        return 0, 0, False
    subjects = [l for l in out.splitlines() if l.strip()]
    return (sum(1 for s in subjects if not s.startswith(BOOKKEEPING_SUBJECTS)), len(subjects), True)


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


def new_region_entries(since: str) -> tuple[int, bool]:
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
        return 0, True                      # no region files is a real, checked answer of zero
    rc, out = sh(["git", "-C", str(VAULT), "log", f"--since={since} 00:00:00", "--format=", "-p",
                  "--", *files], timeout=40)
    if rc != 0:
        return 0, False
    return sum(1 for line in out.splitlines() if line.startswith("+## ")), True


def topology_events(since: str) -> tuple[list[str], bool]:
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
    if rc0 != 0:
        return [], False
    if not prior_any.strip():
        return [], True                     # checked, and correctly nothing: the vault is young
    rc, out = sh(["git", "-C", str(VAULT), "log", f"--since={since} 00:00:00", "--diff-filter=A",
                  "--name-only", "--format="], timeout=40)
    if rc != 0:
        return [], False
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
    return events, True


# ------------------------------------------------------------------- size ----
def oversize_role_files() -> list[dict]:
    """Role files over their per-role LINE limit. A stem with no entry in the table has no line
    limit and is never flagged by this arm — that is the table's meaning, not an omission.

    ★ THE POPULATION IS `memory_files()`, not a bare `rglob`. This arm and `unsearchable_files()`
    each walked the vault themselves, so none of the directory rules applied to either — and row
    R2's cleanup bundle mirrors the vault's own relative paths, which means the copy of a removed
    entry lands at `Cleanup/<date>/removed/<region>/Canon.md`, with the SAME STEM as the file it
    came out of. A reviewer reproduced the result: a 50-line bundle receipt was reported as an
    oversize `Canon`, indistinguishable from the live file it mirrors, on the owner-facing status
    surface this arm feeds. Every stem in the limits table will eventually acquire such a receipt,
    and the bundle only ever grows."""
    table = limits.get("role_soft_limits_lines")
    if not isinstance(table, dict) or not table:
        return []
    out = []
    for md in memory_files():
        try:
            rel = md.relative_to(VAULT)
        except ValueError:
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


def unsearchable_files() -> list[dict]:
    """Memory files the search will not look inside, because they are over its own ceiling.

    ★ This arm exists because the failure is SILENT and the symptom is REASSURING. `recall.md_files`
    skips an oversize file, `search()` returns nothing, and the caller reads "the vault does not
    know that" — which is indistinguishable from the answer never having been written down. Row S0
    measured what that costs: at year 3 under heavy use, every held-out answer was unreachable this
    way and no surface anywhere said so.

    It is a MEASUREMENT of a fact, not a threshold anyone tuned: the ceiling belongs to `recall.py`
    and is read from the limits file rather than repeated here. R3 makes the files small enough that
    this should stay empty — and this arm is what will say so if it ever stops being true, which is
    the half of the fix that survives a future change to the compactor."""
    ceiling = int(limits.get("max_searchable_file_bytes"))
    out = []
    for md in memory_files():                    # the same population, for the same reason
        try:
            rel = md.relative_to(VAULT)
        except ValueError:
            continue
        try:
            size = md.stat().st_size
        except OSError:
            continue
        if size > ceiling:
            out.append({"path": str(rel), "bytes": size, "ceiling": ceiling})
    out.sort(key=lambda r: -r["bytes"])
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

    substantive, total, vol_checked = substantive_commits(str(state.get("last_cleanup", day)))
    oversize = oversize_role_files()
    boot_bytes, boot_files, biggest = boot_chain_now(cwd)

    d_days = limits.get("cleanup_trigger_days")
    d_commits = limits.get("cleanup_trigger_commits")
    budget = limits.get("boot_budget_bytes")

    cleanup_arms = {
        "time": {"fired": cleanup_days is not None and cleanup_days >= d_days,
                 "days": cleanup_days, "threshold": d_days},
        "volume": {"fired": vol_checked and substantive >= d_commits, "commits": substantive,
                   "total_commits": total, "threshold": d_commits, "checked": vol_checked},
        "size_lines": {"fired": bool(oversize), "files": oversize[:EVIDENCE_CAP],
                       "n_files": len(oversize)},
        "boot_bytes": {"fired": boot_bytes > budget, "bytes": boot_bytes,
                       "n_files": boot_files, "threshold": budget, "largest": biggest},
    }

    entries, ent_checked = new_region_entries(str(state.get("last_synthesis", day)))
    events, ev_checked = topology_events(str(state.get("last_synthesis", day)))
    s_days = limits.get("synthesis_trigger_days")
    s_entries = limits.get("synthesis_trigger_entries")
    synthesis_arms = {
        "time": {"fired": synth_days is not None and synth_days >= s_days,
                 "days": synth_days, "threshold": s_days},
        "entries": {"fired": ent_checked and entries >= s_entries, "entries": entries,
                    "threshold": s_entries, "checked": ent_checked},
        "events": {"fired": bool(events), "events": events[:EVIDENCE_CAP], "checked": ev_checked},
    }

    blind = unsearchable_files()
    # The Boot-file arms. NOT cleanup arms: a region that has outgrown its boot payload is not
    # untidy, and a stale Boot file is not a threshold being approached — it is a summary that
    # reads as current while contradicting the bodies it was distilled from.
    stale, boot_unchecked = bootfile.stale_boot_files(VAULT)
    boot = {"graduation": bootfile.graduation_candidates(VAULT)[:EVIDENCE_CAP],
            "oversize": bootfile.oversize_boot_files(VAULT)[:EVIDENCE_CAP],
            "stale": stale[:EVIDENCE_CAP],
            "unchecked": boot_unchecked[:EVIDENCE_CAP]}

    return {
        "boot_file": boot,
        "computed": day,
        # NOT a cleanup arm: it is not a matter of tidiness and no threshold is being approached.
        # A file here is one the search has already stopped reading, and the only thing that must
        # never happen is that it goes unmentioned.
        "unsearchable": {"files": blind[:EVIDENCE_CAP], "n_files": len(blind)},
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
    blind = (doc.get("unsearchable") or {})
    if blind.get("n_files"):
        names = ", ".join(f"{f['path']} ({f['bytes']:,} B)" for f in blind.get("files") or [])
        more = blind["n_files"] - len(blind.get("files") or [])
        out.append(f"- Maintenance: {blind['n_files']} memory file(s) are larger than the "
                   f"{(blind.get('files') or [{}])[0].get('ceiling', 0):,} B search ceiling and are "
                   f"NOT searched — an answer inside one is unreachable, which is not the same as "
                   f"absent: {names}" + (f", and {more} more" if more > 0 else "") + ".")
    boot = doc.get("boot_file") or {}
    for g in boot.get("graduation") or []:
        largest = ", ".join(f"{m['path']} ({m['bytes']:,} B)" for m in g.get("largest") or [])
        out.append(f"- Boot file: {g['region']} carries {g['bytes']:,} B of memory with no Boot "
                   f"file, over a {g['threshold']:,} B budget. Largest: {largest}. A Boot file "
                   f"would replace these in what a session loads; writing one is a judgment about "
                   f"what matters, so nothing has been written.")
    for f in boot.get("oversize") or []:
        out.append(f"- Boot file: {f['path']} is {f['bytes']:,} B — {f['state']} "
                   f"(warn {f['warn']:,} B, budget {f['threshold']:,} B).")
    for s in boot.get("stale") or []:
        out.append(f"- Boot file: {s['path']} is STALE — {', '.join(s['moved'])} moved after it "
                   f"did, so it summarises a state the region has left. It is still loaded every "
                   f"session and nothing else contradicts it.")
    for u in boot.get("unchecked") or []:
        out.append(f"- Boot file: freshness could NOT be checked, so read it as UNCHECKED, never "
                   f"as fresh: {u}.")
    unchecked = []
    for label, group in (("cleanup", cleanup), ("synthesis", synth)):
        for name, arm in (group.get("arms") or {}).items():
            if isinstance(arm, dict) and arm.get("checked") is False:
                unchecked.append(f"{label}/{name}")
    if unchecked:
        # NOT a quiet arm. A measurement that could not be taken is reported as UNMEASURED rather
        # than as a zero — the zero is indistinguishable from a healthy vault, which is the whole
        # failure class this hook was ported to end.
        out.append("- Maintenance: " + ", ".join(unchecked) + " could NOT be measured this session "
                   "(the vault git command failed); read them as UNCHECKED, never as zero.")
    if out:
        out.append(f"- Maintenance state: {state_path()} (computed at the last session end).")
    return out


# -------------------------------------------------------------- compaction ----
def memory_files() -> list[Path]:
    """Every `.md` a search would read — which is the population the size bound is about, and the
    only population this hook may rewrite.

    ★ THE EXCLUSIONS ARE `recall.py`'S OWN, imported rather than restated. The bound exists because
    a file too large for `recall.md_files()` stops being searched, so "which files does that apply
    to" has exactly one correct answer and it lives there. A second list here would drift, and the
    direction it would drift in is the dangerous one: compaction REWRITES the files it touches, so
    a set that is too WIDE does not merely waste work — it rewrites a work queue or a lane notice
    outbox, which are operational surfaces that other machinery parses and no one asked this hook
    to edit. Queues (`Pharos/`, `Channels/`), generated mirrors (`.gedaechtnis/`) and git internals
    are all out, for the same reasons the search leaves them out.


    ★ IT REUSES recall's DIRECTORY RULES AND NOT ITS SIZE CEILING, and the difference is the whole
    point of the row. `recall.md_files()` drops any file over `MAX_FILE_BYTES` — correctly, for its
    own purpose: it will not read one. Delegating to it wholesale made compaction inherit that
    filter, so a file that had ALREADY crossed the ceiling became invisible to the thing whose job
    is to bring it back under — the files that most need compacting were the only ones it could not
    see, and such a vault never self-heals. A reviewer reproduced it with a 2,494,898 B file that
    two Stop hooks in a row left untouched, unlogged and unmentioned.
    """
    try:
        import recall
    except Exception:
        return []
    skip_dirs = (set(recall.SKIP_DIRS) | set(recall.NON_MEMORY_DIRS)
                 | set(recall.GENERATED_DIRS) | set(recall.REMOVED_DIRS))
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(VAULT):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in skip_dirs and not d.startswith("."))
            for name in sorted(filenames):
                if not name.endswith(".md"):
                    continue
                q = Path(dirpath) / name
                if q.is_file() and not q.is_symlink():
                    out.append(q)
    except OSError:
        return []
    return out


def compact_vault() -> list[dict]:
    """Keep every memory file under the bound that decides whether the search can read it.

    ★ THIS IS THE HALF THAT MAKES THE REST OF ROW R3 REAL. The seam (`archive.py`) and the year-3
    measurement that justified it both existed before this function did, and the row's acceptance
    evidence was produced by the SIMULATOR calling `archive.compact_file` directly — so for an
    actual installed vault nothing compacted anything and the files went on growing exactly as
    before. A fix that no hook calls is a fix the product does not have; the row's own review
    caught it, which is the same failure class the row exists to hunt, arriving at the level of the
    row's delivery rather than its code.

    It is a NO-OP on a healthy vault: `compact_file` returns 0 for any file under the bound, so a
    vault that never grows one is never written to. A sealed segment is never itself compacted —
    that would move bytes twice and break the links into it.

    ★ SCOPE IS EVERY MEMORY FILE, not every file inside a region. The first version walked
    `regions()` — directories carrying `Map.md`, excluding `Global/` — and a reviewer reproduced
    what that misses: a file at the vault root, or in a directory whose `Map.md` is absent, grew
    past the bound across repeated session ends and was never touched. That is the year-3 failure
    exactly, relocated to whichever files happen to sit outside a region. `Global/` is excluded from
    the SYNTHESIS scan for a real reason — it is where a pass promotes TO — but that reason says
    nothing about whether its files may grow until the search stops reading them.
    """
    moved = []
    for path in sorted(memory_files()):
        if archive.is_sidecar(path.stem):
            continue
        if True:
            try:
                n = archive.compact_file(path)
            except (OSError, ValueError) as e:
                log("maintenance", f"compaction FAILED for {path}: {e.__class__.__name__}: {e}")
                continue
            if n:
                # The live file AND every segment of it. The segments are NEW FILES, and a commit
                # that carried only the shrunken live file would commit the removal of entries
                # while leaving the file that now holds them untracked — the worst possible half
                # of this operation to land on its own. Caught by
                # `test_compaction_is_COMMITTED_not_left_as_vault_dirt`, which saw exactly that.
                touched = [path] + archive.segments(path)
                moved.append({"path": str(path.relative_to(VAULT)), "entries": n,
                              "paths": [str(q.relative_to(VAULT)) for q in touched]})
                log("maintenance", f"compacted {n} entry(ies) out of {path.relative_to(VAULT)} "
                                   f"into {len(touched) - 1} segment(s)")
    return moved


def commit_compaction(inp: dict, compacted: list[dict]) -> None:
    """Commit what compaction just wrote, through `commit.py`'s seam and no git law of our own.

    ★ WITHOUT THIS THE WHOLE ORGAN IS INVISIBLE TO GIT. `commit.py` commits the session's TOUCHED
    SET — the paths recorded by `chore.py` on every Edit/Write tool call — and its own docstring
    says what that means for us: "a shell command, a script the session ran — is not in the touched
    set and is not committed". Compaction is exactly that. Left alone, every compaction would leave
    a modified live file and a fistful of untracked archive segments behind FOREVER, because no
    later session's touched set will ever contain them either. Dirt of that kind does not announce
    itself; it quietly makes other machinery defer.

    Everything about HOW to commit stays in `auto_commit`: the off switch, the lane fail-safe, the
    partition filter, the dirty intersection, the explicit `git add --`, the pathspec read back from
    the index, the identity. This function contributes a selector and a subject line. A second
    implementation of the vault's git law here would be a second thing to keep correct.

    A path outside the lane's declared partition is dropped by `auto_commit`, not by us — which is
    the right place for it: compaction may legitimately touch a file this lane does not own, and
    the answer to that is to leave it for the lane that does, exactly as for any other write."""
    if not compacted:
        return
    paths = []
    for c in compacted:
        for q in c.get("paths") or [c["path"]]:
            if q not in paths:
                paths.append(q)
    try:
        import commit as commit_mod
        commit_mod.auto_commit(
            inp,
            lambda sid, _prefixes: paths,
            lambda lane: f"Compaction ({lane}): {len(paths)} memory file(s) kept under the search bound",
            tag="maintenance-compaction")
    except Exception as e:                       # a commit bug must not cost the session its hook
        log("maintenance", f"compaction commit FAILED ({e.__class__.__name__}: {e}) — "
                           f"{len(paths)} path(s) left UNCOMMITTED: {', '.join(paths[:5])}")


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

    # Compact BEFORE measuring, so the state file and the facts line describe the vault as it is
    # after this session end rather than as it was before — a measurement taken before the act it
    # is meant to reflect reports a problem that has already been fixed.
    compacted = compact_vault()
    # The Boot file's own window, against the BOOT budget rather than the memory-file bound. Its
    # paths join the same commit: a compaction that is not committed leaves the vault permanently
    # dirty, and dirt of that kind makes other machinery defer rather than announce itself.
    rolled = bootfile.roll_window(VAULT)
    for r in rolled:
        compacted.append({"path": r["path"], "entries": r["entries"],
                          "paths": [r["path"]] + r["segments"]})
        log("maintenance", f"Boot file {r['path']} rolled: {r['entries']} entry(ies) moved, "
                           f"{r['bytes_before']} -> {r['bytes_after']} B")
    commit_compaction(inp, compacted)
    doc = dict(state)
    doc.update(compute(state, cwd, day))
    doc["compacted"] = compacted
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
