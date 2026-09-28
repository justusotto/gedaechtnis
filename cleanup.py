#!/usr/bin/env python3
"""cleanup.py — the periodic tidy of a memory vault: no question per item, one switch per vault.

`maintenance.py` already decides WHEN a vault is due a cleanup and already keeps every memory file
under the size the search can read. This is the other half: what a cleanup pass actually DOES to the
rest of the proposal, and it does it without asking.

## Why it never asks

The vault this grew from ran its cleanup as a read-only agent that returned a list of `[Y / N]`
items for a person to approve one by one. Asked about that shape, the owner of the vault said:
*"i think clean up is a hard decision for many non tech users. so better just automate and don't let
people decide."* He is right, and not only about non-technical users: a list of twenty tidying
decisions is a list nobody answers, so the pass that needs approval is the pass that never runs.

So there is no approval step anywhere in this file, and no question is ever printed. What replaces
the approval is REVERSIBILITY, and that is a hard constraint rather than a nicety:

- **Nothing is deleted. There is no `rm`, no `unlink`, and no truncating write in this module.**
  Every byte removed from a live file is byte-identical present afterwards, either in that file's
  archive sidecar or in the dated Cleanup bundle.
- **The bundle is visible.** `<vault>/Cleanup/<YYYY-MM-DD>/` with a `README-what-went-where.html`
  naming, for each entry moved: where it came FROM, WHY it moved, and where the surviving copy is.
  Deleting the bundle is the user's act, through their file manager's Trash, never this code's.
- **The receipt says what happened**, in the past tense. Users see what happened, not a decision.

## One switch, not twenty questions (CLEANUPGATE-1, 2026-09-23)

This pass follows the same five rules as every other chore that moves a user's file
(`hooks/destructive.py`): allow-list, bounded loss, propose by default, hash check, revert line. It
had followed none of them, while a guardian session auto-fires it. So a vault that has not set
`cleanup_apply: true` gets a PROPOSAL — `proposals.json` and one SessionStart line — and nothing
moves; `--propose` keeps any run that way, which is the form an automatic run uses. That does not
bring back the list of questions above: the switch is flipped once for the vault, after one
proposal has been read, and every pass after that applies without asking.

## The three detectors, and why they are the ones a machine may act on

1. **`duplicate`** — two `## ` entries in ONE file with the same heading AND byte-identical bodies.
   The first survives; each later copy moves to the bundle. *Within one file only.* Which of two
   FILES a cross-file duplicate belongs in is a placement judgment — Canon or Patterns, region or
   Global — and an automated pass that picks wrong relocates a memory silently. That half belongs
   to the `memory-cleaner` agent, which proposes and applies nothing.

2. **`oversize`** — a role file over its per-role LINE limit in `rules/limits.json`. The oldest
   entries fold into the file's archive sidecar through `archive.compact_file`, which is the same
   movement `maintenance.py` performs against the byte bound, in the unit the soft limits are
   written in. A stem with no entry in that table has no line limit and is never flagged: that is
   the table's meaning, not an omission.

3. **`stale`** — an entry whose `Last revisited:` date is older than `stale_entry_days`.
   **REPORTED, NEVER MOVED**, and that asymmetry is the point. An unanswered question is not
   untidiness. A pass that filed it into an archive would remove from the live file the one item on
   the list the reader actually has to do something about, and it would do it silently, which is the
   failure mode this whole package exists to stop.

A fourth thing that is NOT a detector: a date that will not parse. It is counted and reported as
UNCHECKED, never as fresh — a measurement that could not be taken and a measurement that came back
clean are opposite facts, and only one of them means nothing is wrong.
"""
from __future__ import annotations
import argparse, html, json, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import config                                                             # noqa: E402
import common                                                             # noqa: E402  (the one clock)
import limits                                                             # noqa: E402
import archive                                                            # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import maintenance                                                        # noqa: E402
import rootguard                                                          # noqa: E402
import destructive                                                        # noqa: E402

# ★ Resolved PER CALL, never at import. The constants these replace were evaluated once when
# this module was first imported, and on 2026-09-15 that freeze rewrote 263 files of a real
# memory vault: a test set GEDAECHTNIS_VAULT after the module was already in `sys.modules`, so
# the override was read by nobody. PEP 562 `__getattr__` below keeps the old spelling working
# while making every read a fresh resolution — but `from <this module> import VAULT` binds ONCE
# and brings the bug straight back, which is why the importers use attribute access and
# `tests/test_percall_resolution.py` fails if a module-level binding returns.

_ACCESSORS = {"VAULT": lambda: config.vault()}


def __getattr__(name: str):
    fn = _ACCESSORS.get(name)
    if fn is not None:
        return fn()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_ACCESSORS))
BUNDLE_DIR = "Cleanup"
README_NAME = "README-what-went-where.html"
# Proposal kinds this module REPORTS and never acts on. A named set rather than a
# convention, because it is read in three places that must agree — `apply` (which must not
# try to move one), `main`'s dry run (which must not count one as a change that would be
# applied) and the receipt. A convention is a hint to a reader; this is a branch in the code.
REPORT_ONLY_KINDS = ("stale", "graduate")
def _bootfile():
    """`bootfile.py`, lazily — it owns the one definition of "a session loads this file"."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import bootfile
    return bootfile

# `**Last revisited:** 2026-08-01`, `Last revisited: 2026-08-01`, and the spellings in between.
LAST_REVISITED_RE = re.compile(r"Last revisited[:*\s]+([0-9]{4}-[0-9]{2}-[0-9]{2})?", re.I)


def today() -> str:
    """The one clock (`common.today()`). Under a frozen one no entry can ever BE 56 days old,
    so the stale-entry report was unreachable by any simulation."""
    return common.today()


def lines_of(text: str) -> int:
    """The unit the per-role soft limits are written in. `wc -l` counts terminators; a file whose
    last line has no newline still occupies that line on screen, so it is counted."""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def split_entries(text: str) -> tuple[str, list[str]]:
    """`archive.split_entries` — the package's ONE entry splitter.

    This was a second copy, and it split naively on `\\n## `. Row R6's third reviewer reproduced
    what that does here: a duplicate entry containing a fenced code block with a `## ` line inside
    it was "deduplicated" by this applier, and the fence was torn — the closer orphaned, a
    fence-interior line promoted to a live heading. In a tool that applies straight away and never
    asks. Re-joining head + entries still reproduces the file byte for byte, which is what lets the
    conservation check be an equality."""
    return archive.split_entries(text)


def heading_of(entry: str) -> str:
    return entry[len("\n## "):].split("\n", 1)[0].strip()


# ------------------------------------------------------------- detectors ----
def detect_duplicates(rel: str, text: str) -> list[dict]:
    """Later copies of an entry already present, byte for byte, earlier in the same file.

    Equality is over the WHOLE entry, heading and body together. Two entries under one heading
    whose bodies differ are not duplicates and must never be treated as such: superseding an entry
    in place under its original heading is how a vault records that a rule changed, and a pass that
    collapsed those to one would delete the correction or the thing it corrected, at random."""
    _head, entries = split_entries(text)
    seen: dict[str, int] = {}
    out = []
    for i, e in enumerate(entries):
        key = e.strip()
        if key in seen:
            out.append({
                "kind": "duplicate", "action": "move", "path": rel,
                "heading": heading_of(e), "index": i, "first_index": seen[key], "text": e,
                "why": f"byte-identical to an earlier entry under the same heading in {rel}",
                "surviving_copy": f"{rel}, entry {seen[key] + 1}, unchanged",
            })
        else:
            seen[key] = i
    return out


def detect_oversize(rel: str, stem: str, text: str) -> list[dict]:
    """A role file past its per-role LINE limit."""
    table = limits.get("role_soft_limits_lines")
    if not isinstance(table, dict):
        return []
    limit = table.get(stem)
    if not isinstance(limit, int):
        return []
    n = lines_of(text)
    if n <= limit:
        return []
    return [{
        "kind": "oversize", "action": "fold", "path": rel, "lines": n, "limit": limit,
        "why": f"{n} lines against a {limit}-line soft limit for {stem}",
        "surviving_copy": f"the oldest entries move to {stem}-archive*.md beside it",
    }]


def detect_stale(rel: str, text: str, day: str) -> tuple[list[dict], int]:
    """(proposals, n_unparsable). Entries not revisited in `stale_entry_days`."""
    import datetime
    horizon = int(limits.get("stale_entry_days"))
    try:
        now = datetime.date.fromisoformat(day)
    except ValueError:
        return [], 0
    _head, entries = split_entries(text)
    out, unparsable = [], 0
    for e in entries:
        m = LAST_REVISITED_RE.search(e)
        if not m:
            continue
        if not m.group(1):
            unparsable += 1
            continue
        try:
            when = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            unparsable += 1
            continue
        age = (now - when).days
        if age >= horizon:
            out.append({
                "kind": "stale", "action": "report", "path": rel, "heading": heading_of(e),
                "last_revisited": m.group(1), "days": age, "threshold": horizon,
                "why": f"last revisited {m.group(1)}, {age} days ago (threshold {horizon})",
                "surviving_copy": f"{rel}, untouched — an open question is never filed away",
            })
    return out, unparsable


def _graduation_for(path: Path, chain_graduation: dict, vault: Path) -> dict | None:
    """The boot-chain proposal for a protected file the pass had to skip, or None.

    ★ THIS IS THE ONLY THING THE PASS CAN SAY ABOUT AN OVER-BUDGET BOOT CHAIN, and saying nothing
    was the defect (row A1 §3). The pass skips the file that grew — correctly, because a session
    LOADS it — and until now the skip was silent, so the arm fired every session for ever against a
    remedy that was forbidden to run. This names the one thing that would work.

    It PROPOSES and never applies: `kind` is in `REPORT_ONLY_KINDS`, so no path in `apply()` can
    reach it. A pass that distilled a region into a Boot file unattended would be rewriting a file
    every session loads on a machine's judgment about what matters — the incident class this
    package was rebuilt around.

    The conditions and the wording both come from `bootfile.chain_graduation`, decided there once.
    They were ALSO re-checked here until the mutation sweep showed what that cost: with two gates on
    one condition, neutering either left the other standing, and the negative control for the budget
    stayed GREEN through a mutation that removed the budget check. A redundant guard is not extra
    safety; it is a rule no test can reach.

    The region matched is the NEAREST one containing the file — the same attribution
    `chain_graduation` used to build the rows, or a nested vault gets told to graduate an ancestor
    whose Boot file would not replace this import."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    best = None
    for row in chain_graduation.get("regions") or []:
        region_dir = (vault / row["region"]).resolve()
        if region_dir not in resolved.parents:
            continue
        if best is None or len(region_dir.parts) > len(Path(vault / best["region"]).resolve().parts):
            best = row
    if best is None:
        return None
    return {
        "kind": "graduate", "action": "report", "region": best["region"],
        "bytes": best["bytes"], "files": best["files"],
        "has_boot_file": best["has_boot_file"],
        "chain_bytes": chain_graduation["chain_bytes"],
        "threshold": chain_graduation["budget"],
        "why": (f"the @-import chain is {chain_graduation['chain_bytes']:,} B against a "
                f"{chain_graduation['budget']:,} B budget, and the files that grew are ones a "
                f"session LOADS — an unattended pass never tidies those, so nothing here can "
                f"bring the chain down"),
        "remedy": best["remedy"],
        "surviving_copy": f"{best['region']}, untouched — nothing a session loads is moved by "
                          f"this pass",
    }


# --------------------------------------------------------------- propose ----
def propose(day: str | None = None) -> dict:
    """Every finding, as data. Reads the vault; writes nothing.

    There is deliberately NO `vault` parameter. `maintenance.memory_files()` — the one definition
    of which files are memory — reads the module-level VAULT that `config.py` resolves from the
    environment, so a second vault passed in here would be honoured by the path arithmetic and
    ignored by the file walk: half the function would scan one vault and describe another. The
    tests point GEDAECHTNIS_VAULT and run this as the product does."""
    vault = config.vault()
    day = day or today()
    _members = _bootfile().chain_members(os.getcwd())
    _chain = {str(q) for q, _size in _members}
    # ONE answer to "is the chain over budget, and which regions have no way down", read from
    # `bootfile` rather than decided here — the facts line reads the same function, and two
    # independent judgements of the same pair of conditions is how a proposal and the sentence
    # describing it come to disagree.
    _chain_graduation = _bootfile().chain_graduation(os.getcwd(), chain=_members)
    proposals, unparsable, unreadable = [], 0, []
    graduated: set[str] = set()
    for path in maintenance.memory_files():
        # A sidecar is APPEND-ONLY by contract ("nothing here is ever rewritten or moved: a link
        # into an archive segment stays valid"). Rewriting one to drop a duplicate would break
        # exactly the promise the archive exists to make.
        if archive.is_sidecar(path.stem):
            continue
        if _bootfile().is_protected(path, chain=_chain):
            grad = _graduation_for(path, _chain_graduation, vault)
            if grad is not None and grad["region"] not in graduated:
                graduated.add(grad["region"])
                proposals.append(grad)
            # The same rule `maintenance.compact_vault` follows, for the same reason and found in
            # the same review: a file a session LOADS is not tidied by an unattended pass. Keyed on
            # the filename until 2026-09-15, when a vault's top-level index fell through elsewhere
            # for exactly that — a name is not a property. Not enough that `role_soft_limits_lines` happens to
            # carry no `Kernel` key today: nothing declared that it must not.
            continue
        try:
            rel = str(path.relative_to(vault))
        except ValueError:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            unreadable.append(f"{rel} ({e.__class__.__name__})")
            continue
        sha = archive.digest(text.encode("utf-8"))
        for q in detect_duplicates(rel, text) + detect_oversize(rel, path.stem, text):
            q["sha"] = sha                  # the HASH CHECK's baseline (destructive.py rule 4)
            proposals.append(q)
        stale, n_bad = detect_stale(rel, text, day)
        proposals += stale
        unparsable += n_bad
    return {"day": day, "proposals": proposals, "unparsable_dates": unparsable,
            "unreadable": unreadable}


# ----------------------------------------------------------------- apply ----
def bundle_path(vault: Path, day: str) -> Path:
    return vault / BUNDLE_DIR / day


def _bundle_copy(bundle: Path, rel: str, entry: str, why: str) -> str:
    """Append one removed entry to its mirror inside the bundle. Returns the bundle-relative path.

    The bundle MIRRORS the vault's own layout, so the answer to "where did this come from" is the
    path itself, and a restore is a copy back along the same relative path rather than an exercise
    in reading the README."""
    dest = bundle / "removed" / rel
    rootguard.permit(dest, "cleanup bundle copy")
    dest.parent.mkdir(parents=True, exist_ok=True)
    header = ""
    if not dest.exists():
        header = (f"# Removed from {rel}\n\n"
                  "Entries a cleanup pass took out of the live file. Nothing here was deleted; this\n"
                  "IS the copy. To restore one, paste it back into the file named above.\n")
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(header + f"\n<!-- why: {why} -->" + entry.rstrip("\n") + "\n")
    return str(dest.relative_to(bundle))


def _readme(bundle: Path, day: str, moved: list[dict], folded: list[dict],
            reported: list[dict], unparsable: int, graduation: list[dict] | None = None) -> None:
    """The receipt, as a page a person can open from their file manager.

    `<!doctype html>` and the charset are the first two lines because this file is opened over
    `file://`, where a late or missing charset declaration is read as latin-1 and every non-ASCII
    heading in it becomes mojibake."""
    def rows(items, cols):
        out = []
        for it in items:
            cells = "".join(f"<td>{html.escape(str(it.get(c, '')))}</td>" for c in cols)
            out.append(f"<tr>{cells}</tr>")
        return "\n".join(out) or '<tr><td colspan="9" class="q">none</td></tr>'

    # ★ CONDITIONAL, because an unconditional paragraph is an unconditional CLAIM. It said "the
    # @-import chain a session boots with is over its budget" on every receipt, including one
    # produced against a 573 B chain under a 20,000 B budget — the row's reviewer opened that page.
    # A section with nothing in it does not get to assert the condition that would have filled it.
    boot_section = ""
    if graduation:
        boot_section = f"""<h2>The boot chain, and what this pass could not do about it ({len(graduation)})</h2>
<p class="q">The @-import chain a session boots with is over its budget, and the files that grew are
ones a session LOADS — so this pass left them alone, as it leaves every file a session loads alone.
Nothing here has been written for you: distilling a region into a Boot file is a judgment about what
matters, and nothing in this package rewrites an import line.</p>
<table><tr><th>region</th><th>in the chain</th><th>why this pass cannot help</th><th>what would</th></tr>
{rows(graduation, ["region", "bytes", "why", "remedy"])}</table>
"""

    doc = f"""<!doctype html>
<meta charset="utf-8">
<title>Cleanup {html.escape(day)} — what went where</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
        margin: 2rem auto; max-width: 60rem; padding: 0 1rem; }}
 h1 {{ font-size: 1.4rem; margin-bottom: .2rem; }}
 h2 {{ font-size: 1.05rem; margin-top: 2rem; }}
 p.lede {{ margin-top: 0; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
 th, td {{ text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #8884;
           vertical-align: top; }}
 th {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; opacity: .7; }}
 code, td.p {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85em; }}
 .q {{ opacity: .6; }}
</style>
<h1>Cleanup {html.escape(day)}</h1>
<p class="lede">What the cleanup pass did, and where everything it touched now lives.
<strong>Nothing was deleted.</strong> Every entry listed below still exists — in this bundle, or in
the archive file named beside it. If you want a line back, copy it from
<code>removed/</code> into the file it came from. If you want this bundle gone, move the whole
folder to the Trash yourself; nothing here does that for you.</p>

<h2>Moved into this bundle ({len(moved)})</h2>
<table><tr><th>from</th><th>entry</th><th>why</th><th>surviving copy</th><th>copy in this bundle</th></tr>
{rows(moved, ["path", "heading", "why", "surviving_copy", "bundle_copy"])}</table>

<h2>Folded into an archive file ({len(folded)})</h2>
<table><tr><th>file</th><th>why</th><th>entries moved</th><th>surviving copy</th></tr>
{rows(folded, ["path", "why", "entries", "surviving_copy"])}</table>

<h2>Reviewed, not moved ({len(reported)})</h2>
<p class="q">Open questions that have not been revisited in a while. They are left exactly where
they are on purpose: filing an unanswered question away is how it stops being answered.</p>
<table><tr><th>file</th><th>entry</th><th>last revisited</th><th>why</th></tr>
{rows(reported, ["path", "heading", "last_revisited", "why"])}</table>
{boot_section}
{f'<p class="q">{unparsable} entry(ies) carry a “Last revisited” marker whose date could not be read. They are UNCHECKED, never assumed fresh.</p>' if unparsable else ''}
"""
    rootguard.permit(bundle, "cleanup bundle receipt")
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / README_NAME).write_text(doc, encoding="utf-8")


def apply(found: dict) -> dict:
    """Execute the movable proposals. Returns the receipt.

    Order matters: duplicates are removed BEFORE a file is folded, so an entry that is both a
    duplicate and old is not archived as a second copy first."""
    vault = config.vault()
    day = found["day"]
    proposals = found["proposals"]
    if not proposals:
        return {"day": day, "bundle": None, "moved": [], "folded": [], "reported": [],
                "graduation": [],
                "unparsable_dates": found.get("unparsable_dates", 0),
                "unreadable": found.get("unreadable", [])}
    bundle = bundle_path(vault, day)
    moved, folded, failed, deferred = [], [], [], []
    # BOUNDED LOSS (destructive.py rule 2): at most `max_files_per_pass` files per pass, and at
    # most `max_move_share` of any one file. What either bound declines is DEFERRED to the next
    # pass and said so — never dropped.
    share, n_max = destructive.max_share(), destructive.max_files()
    files_done: list[str] = []

    def _room(rel: str) -> bool:
        if rel in files_done:
            return True
        if len(files_done) >= n_max:
            deferred.append(f"{rel}: this pass already touched {n_max} file(s) "
                            f"(max_files_per_pass); left for the next pass")
            return False
        files_done.append(rel)
        return True

    def _unchanged(rel: str, want: str | None) -> bool:
        # HASH CHECK (rule 4): the whole file, against what the scan read.
        try:
            now = archive.digest((vault / rel).read_bytes())
        except OSError:
            return False
        return want is None or now == want

    by_file: dict[str, list[dict]] = {}
    for p in proposals:
        if p["kind"] == "duplicate":
            by_file.setdefault(p["path"], []).append(p)
    for rel, dupes in sorted(by_file.items()):
        live = vault / rel
        try:
            text = live.read_text(encoding="utf-8")
        except OSError as e:
            failed.append(f"{rel}: {e.__class__.__name__}")
            continue
        if not _unchanged(rel, dupes[0].get("sha")):
            failed.append(f"{rel}: changed since the scan; left untouched")
            continue
        if not _room(rel):
            continue
        head, entries = split_entries(text)
        budget, kept_dupes = share * len(text), []
        for d in dupes:
            if budget - len(d["text"]) < 0:
                deferred.append(f"{rel}: duplicate “{d['heading']}” would take the pass past "
                                f"{share:.0%} of the file (max_move_share); left for the next pass")
                continue
            budget -= len(d["text"])
            kept_dupes.append(d)
        dupes = kept_dupes
        if not dupes:
            continue
        drop = {d["index"] for d in dupes}
        # The entry text is re-read from disk and compared against what the detector saw. A file
        # edited between the scan and the apply would otherwise have a DIFFERENT entry at that
        # index, and the pass would remove the wrong one — the one destructive mistake this module
        # is able to make.
        stale_scan = any(i >= len(entries) or entries[i] != d["text"]
                         for d, i in ((d, d["index"]) for d in dupes))
        if stale_scan:
            failed.append(f"{rel}: changed since the scan; left untouched")
            continue
        for d in dupes:
            d["bundle_copy"] = _bundle_copy(bundle, rel, d["text"], d["why"])
            moved.append(d)
        kept = [e for i, e in enumerate(entries) if i not in drop]
        archive.atomic_write(live, head + "".join(kept))

    for p in proposals:
        if p["kind"] != "oversize":
            continue
        live = vault / p["path"]
        if p["path"] not in files_done and not _unchanged(p["path"], p.get("sha")):
            failed.append(f"{p['path']}: changed since the scan; left untouched")
            continue
        if not _room(p["path"]):
            continue
        try:
            n = archive.compact_file(live, limit=p["limit"],
                                     floor_share=float(limits.get("compaction_floor_share")),
                                     measure=lines_of, max_share=share, dry_run=False)
        except (OSError, ValueError) as e:
            failed.append(f"{p['path']}: {e.__class__.__name__}: {e}")
            continue
        if n:
            folded.append(dict(p, entries=n,
                               surviving_copy=", ".join(str(s.relative_to(vault))
                                                        for s in archive.segments(live))
                               or p["surviving_copy"]))

    reported = [p for p in proposals if p["kind"] == "stale"]
    graduation = [p for p in proposals if p["kind"] == "graduate"]
    _readme(bundle, day, moved, folded, reported, found.get("unparsable_dates", 0), graduation)
    return {"day": day, "bundle": str(bundle.relative_to(vault)), "moved": moved,
            "folded": folded, "reported": reported, "graduation": graduation, "failed": failed,
            "deferred": deferred,
            "unparsable_dates": found.get("unparsable_dates", 0),
            "unreadable": found.get("unreadable", [])}


def touched_paths(receipt: dict) -> list[str]:
    """Every vault-relative path the apply wrote, for the commit."""
    vault = config.vault()
    out = []
    bundle = receipt.get("bundle")
    for m in receipt.get("moved") or []:
        for q in (m["path"], f"{bundle}/{m['bundle_copy']}"):
            if q not in out:
                out.append(q)
    for f in receipt.get("folded") or []:
        live = vault / f["path"]
        for q in [f["path"]] + [str(s.relative_to(vault)) for s in archive.segments(live)]:
            if q not in out:
                out.append(q)
    if bundle and (receipt.get("moved") or receipt.get("folded") or receipt.get("reported")
                   or receipt.get("graduation")):
        q = f"{bundle}/{README_NAME}"
        if q not in out:
            out.append(q)
    return out


def commit(receipt: dict, session_id: str = "-", cwd: str | None = None) -> None:
    """Commit what the pass wrote, through `commit.py`'s seam and no git law of our own.

    Same reasoning as `maintenance.commit_compaction`: this module's writes are not in any
    session's touched set, so left alone they would sit in the vault as permanent dirt — and dirt
    makes other machinery defer rather than announce itself."""
    paths = touched_paths(receipt)
    if not paths:
        return
    # REVERT LINE (rule 5): what moved, then the one command that undoes the commit.
    items: dict[str, dict] = {}
    for m in receipt.get("moved") or []:
        it = items.setdefault(m["path"], {"path": m["path"], "entries": 0, "headings": []})
        it["entries"] += 1
        it["headings"].append(m["heading"])
    for f in receipt.get("folded") or []:
        it = items.setdefault(f["path"], {"path": f["path"], "entries": 0, "headings": []})
        it["entries"] += f.get("entries", 0)
    body = destructive.revert_body(destructive.pass_id(), list(items.values()), config.vault())
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
        import commit as commit_mod
        commit_mod.auto_commit({"cwd": cwd or os.getcwd(), "session_id": session_id},
                               lambda _sid, _prefixes: paths,
                               lambda lane: f"Cleanup ({lane}): {len(paths)} path(s) tidied, "
                                            f"nothing deleted",
                               tag="cleanup", body=body)
    except Exception as e:                       # a commit bug must not cost the user the cleanup
        print(f"note: the cleanup landed but could not be committed "
              f"({e.__class__.__name__}: {e}); {len(paths)} path(s) are uncommitted in the vault.")


# ---------------------------------------------------------------- report ----
def render(receipt: dict) -> str:
    """The receipt, in the past tense, with no question in it."""
    out = []
    n = len(receipt.get("moved") or []) + len(receipt.get("folded") or [])
    if not n and not receipt.get("reported") and not receipt.get("graduation"):
        return ("Cleanup: nothing to do — no duplicate entries, no file over its line limit, "
                "no entry past its review horizon.")
    out.append(f"Cleanup {receipt['day']}: {n} change(s) applied. Nothing was deleted.")
    for m in receipt.get("moved") or []:
        out.append(f"  - moved a duplicate of “{m['heading']}” out of {m['path']} "
                   f"→ {receipt['bundle']}/{m['bundle_copy']}")
    for f in receipt.get("folded") or []:
        out.append(f"  - folded {f['entries']} oldest entry(ies) out of {f['path']} "
                   f"({f['lines']} lines, limit {f['limit']}) → {f['surviving_copy']}")
    for r in receipt.get("reported") or []:
        out.append(f"  - not moved: “{r['heading']}” in {r['path']} — {r['why']}")
    for g in receipt.get("graduation") or []:
        out.append(f"  - not tidied, and this pass cannot tidy it: {g['why']}. "
                   f"What would: {g['remedy']}")
    if receipt.get("unparsable_dates"):
        out.append(f"  - {receipt['unparsable_dates']} “Last revisited” date(s) could not be read; "
                   f"read them as UNCHECKED, never as fresh.")
    for u in receipt.get("unreadable") or []:
        out.append(f"  - could NOT be read, so it was NOT checked: {u}")
    for f in receipt.get("failed") or []:
        out.append(f"  - left untouched: {f}")
    for f in receipt.get("deferred") or []:
        out.append(f"  - not yet: {f}")
    if receipt.get("bundle"):
        out.append(f"  Receipt: {config.vault() / receipt['bundle'] / README_NAME}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tidy the memory vault. Never asks; applies once the "
                                             "vault has set cleanup_apply, proposes until then.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would change and write nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable receipt")
    ap.add_argument("--session-id", default="-")
    ap.add_argument("--propose", action="store_true",
                    help="record what it would change and change nothing, whatever the vault's "
                         "switch says — the form an automatic (guardian) run uses")
    args = ap.parse_args(argv)
    if not config.vault().is_dir():
        print(f"No vault at {config.vault()}; nothing to clean.")
        return 0
    found = propose()
    if args.dry_run:
        receipt = {"day": found["day"], "bundle": None, "moved": [], "folded": [],
                   "reported": [p for p in found["proposals"] if p["kind"] == "stale"],
                   "graduation": [p for p in found["proposals"] if p["kind"] == "graduate"],
                   "would_change": [p for p in found["proposals"]
                                    if p["kind"] not in REPORT_ONLY_KINDS],
                   "unparsable_dates": found["unparsable_dates"],
                   "unreadable": found["unreadable"]}
        print(json.dumps(receipt, indent=1) if args.json
              else f"Cleanup (dry run): {len(receipt['would_change'])} change(s) would be applied, "
                   f"{len(receipt['reported'])} entry(ies) would be reported and left alone.")
        return 0
    # PROPOSE BY DEFAULT (destructive.py rule 3). Until this vault sets `cleanup_apply: true`, a
    # pass records what it WOULD change in proposals.json, the next session is told, and nothing
    # in the vault moves. `--propose` keeps it that way whatever the switch says: an automatic run
    # reads a proposal and never applies one. The window stays open, as for a dry run: a pass that
    # only looked has not tidied anything.
    if args.propose or not destructive.applies("cleanup"):
        items = [{"path": p["path"], "kind": p["kind"], "why": p.get("why", ""),
                  **({"heading": p["heading"]} if p.get("heading") else {})}
                 for p in found["proposals"] if p["kind"] not in REPORT_ONLY_KINDS]
        destructive.record_proposal("cleanup", items)
        receipt = {"day": found["day"], "proposing_only": True, "proposed": items,
                   "switch": destructive.REGISTRY["cleanup"][0],
                   "reported": [p for p in found["proposals"] if p["kind"] == "stale"],
                   "graduation": [p for p in found["proposals"] if p["kind"] == "graduate"],
                   "unparsable_dates": found["unparsable_dates"],
                   "unreadable": found["unreadable"]}
        if args.json:
            print(json.dumps(receipt, indent=1))
        else:
            why = ("this run was asked only to propose" if args.propose else
                   "this vault has not switched cleanup on yet")
            print(f"Cleanup (proposing only — {why}): {len(items)} change(s) would be applied, "
                  f"nothing was changed. The list is in {destructive.proposals_path()}."
                  + ("" if args.propose else " To let it act, set `cleanup_apply: true` in the "
                     "`limits` object of config.json."))
        return 0
    receipt = apply(found)
    # The pass RAN, so its window closes here — whether or not it had anything to move. A pass that
    # looked and found nothing is exactly what a time arm is asking for; leaving the stamp for
    # "passes that changed something" would keep the arm firing forever in the tidiest vault.
    # One writer, in `maintenance.record_pass`: this module holds no copy of the key it stamps.
    try:
        receipt["recorded"] = maintenance.record_pass("cleanup", found["day"])
    except (OSError, ValueError) as e:                      # never fail a pass over its bookkeeping
        receipt["recorded"] = None
        receipt["record_error"] = f"{e.__class__.__name__}: {e}"
    commit(receipt, session_id=args.session_id)
    print(json.dumps(receipt, indent=1) if args.json else render(receipt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
