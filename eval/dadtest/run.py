#!/usr/bin/env python3
"""dadtest/run.py — ROW-A1, Part A: the deterministic half of the "dad test".

    python3 eval/dadtest/run.py --json /tmp/dadtest-out.json

**What this is.** ROW-A1 asks whether a non-technical stranger would get through 90 days with this
package. Two of its four pass criteria never need a model — they are properties of the plugin
acting on a vault, not of anything a session says. This script drives a single sandboxed vault
through 90 simulated days, running the REAL `hooks/session_start.py`, `hooks/maintenance.py` and
`cleanup.py` as subprocesses at the right point in each simulated day, and reads every number back
out of what those scripts themselves wrote to disk. See
this package's own design note for why the harness is split this
way; Part B (the model half, criterion 1 — "at most 3 questions to the user") is a separate script
and is not built here.

**Reuse, not reimplementation.** The sandbox, the entry author, the wikilink checkers and the
growth-load table all come from `eval/simulator/run.py` — the harness this row's own S0/R3 work
already built and hardened (see that file's own scar tissue in its docstring and comments). This
script is loaded under a private module name (`_ged_dadtest_simulator`), never as `run`: both files
are literally named `run.py`, and a bare `import run` after the other one is already registered in
`sys.modules` silently returns the WRONG module — the exact class of bug this package has been
bitten by more than once (see `hooks/common.py`'s PEP 562 note on `from X import Y` binding once).

**The three things this script measures, and why they are the right three for a hook harness:**

  boot bytes         `hooks/session_start.py`, run as a subprocess every single simulated day (not
                     just at sampled snapshots) — this is what criterion 2 ("no day boots over
                     `boot_budget_bytes`") needs, and a budget that is only checked on 10 sampled
                     days out of 90 could hide the one day it was crossed. The number is read back
                     from the hook's own `session-start.json`, via `Sandbox.hook_boot_bytes` —
                     never recomputed a second way.
  cleanup fires      `hooks/maintenance.py`, run as a subprocess at every simulated day's end. Its
  and applies        own `maintenance.json` state says whether a cleanup pass is DUE; when it is,
                     the real `cleanup.py` is run (also a subprocess) and its own JSON receipt says
                     what it proposed or moved. Nothing here decides "due" or "applied" itself — both are read
                     off the artifacts the real scripts already produce.
  wikilink health    `wikilink_health()` / `wikilink_literal()` from the simulator, called at day 0
                     (right after day 1's growth, before any compaction has ever run) and at day
                     90. The AGGREGATE ratios are recorded at both days per the row's brief, and a
                     stricter per-TARGET check (`resolved_targets`, below) names any specific
                     `(stem, heading)` that resolved at day 0 and stopped resolving by day 90 — the
                     genuine regression this criterion is hunting, which an aggregate ratio alone
                     can hide behind new links added in between.

**The load chosen, and why — and a correction this file's first draft needed.** The default is
`--load low --curve linear`. `low` is the rate projected from MEASURED per-session rates
(~857 B/day across role files, ~214 B/day into the boot file); `medium` is MEASURED too, but from
**one active engineering project's own development** (6,294 / 2,147 B/day, day 7→30). A person who
keeps notes about a hobby is the first, not the second, and the row this serves is explicitly about
that person.

The first draft defaulted to `medium` and justified it by saying `low` *"never got either cleanup arm
to fire in 90 days"*. **That is false, and the sweep that was supposed to have established it is what
refutes it:** at `low` the cleanup became due and applied on day 89 — late, but inside the horizon,
and criterion 3 passes. The justification was the load-choosing mistake this row was most exposed to
(pick the load that makes the interesting thing happen, then write the reason afterwards), so it is
recorded here rather than quietly corrected.

**Criterion 3 since propose-first (2026-09-23, SIMROW-1).** The day-89 PASS above was measured
before cleanup became propose-first; on the same load today a due pass PROPOSES and moves nothing,
and the old criterion read all 76 proposing sessions as "never had anything to apply". Criterion 3
is now: proposed something, every proposing pass left the vault byte-identical, applied after the
simulated person switched `cleanup_apply` on, within `max_files_per_pass`, with an undo line in the
commit. At `low` it passes (due day 15, first proposal day 89, applied day 90).

`medium` remains available and is run as a **named stress arm**, because what it exposes is real —
see the note below. `linear` (not `bursty` or `exponential`) because the criterion is about whether
the pass eventually catches up, not about a particular growth shape.

**★ WHAT THE STRESS ARM FOUND, verified three ways.** At `medium`, 80 of 90 days boot OVER the
20,000 B budget, peaking at 191,029 B — and the mechanism is not slow compaction, it is a closed
loop that cannot close:

- `maintenance.compute()`'s `boot_bytes` arm fires when the @-import chain exceeds the budget, so a
  cleanup is due, every session, for ever;
- `cleanup.propose()` then SKIPS any file `bootfile.is_protected` names — *"a file a session LOADS is
  not tidied by an unattended pass"* — and for a fresh, ungraduated region the boot chain IS
  `Position.md`, the only file that grew;
- the one thing that does compact a boot file, `bootfile.roll_window`, needs a graduated `Kernel.md`
  carrying a declared `<!-- gedaechtnis:window -->` marker. A stranger's install has neither.

So the trigger names the boot budget and the remedy is forbidden from touching the file that
breached it. Reproduced directly: a 40-day `low`-rate sandbox grew `Position.md` to 88,205 B / 878
lines, and `is_protected` returns *"it is @-imported by the session's own boot chain"* on exactly
that file. This is NOT the author-declared-window decision (that one is about an existing vault's
markers, and is ruled); it is that an install with no graduated region has no path to one at all.
Pinned by `test_dadtest.py::test_the_boot_chain_file_is_the_one_cleanup_will_not_touch`.

**Every threshold this script reads comes through `hooks/limits.py`**, which is `rules/limits.json`'s
one reader — no number here is a literal repeat of one that lives there.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]          # .../gedaechtnis
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "eval"))

import limits  # noqa: E402  the ONE reader of rules/limits.json; no threshold is inlined below


def _load_module(path: Path, name: str):
    """Load a module by file path under a name that will never collide with the bare `run` a
    sibling test suite already uses for `eval/simulator/run.py` (`tests/test_simulator.py`). Both
    files are named `run.py`; `import run` a second time under the same name returns whichever
    module got there first, silently."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SIMULATOR = _load_module(PLUGIN / "eval" / "simulator" / "run.py", "_ged_dadtest_simulator")

Sandbox = SIMULATOR.Sandbox
Author = SIMULATOR.Author
wikilink_health = SIMULATOR.wikilink_health
wikilink_literal = SIMULATOR.wikilink_literal
curve_factor = SIMULATOR.curve_factor
LOADS = SIMULATOR.LOADS
CURVES = SIMULATOR.CURVES
BOOT_STEM = SIMULATOR.BOOT_STEM
OTHER_STEMS = SIMULATOR.OTHER_STEMS
ENTRY_B = SIMULATOR.ENTRY_B
_run_proc = SIMULATOR.run              # (rc, stdout, stderr) subprocess helper
_chain_text = SIMULATOR._chain_text    # the @-import closure's TEXT, not just its byte count
WIKILINK_RE = SIMULATOR.WIKILINK_RE
HEADING_RE = SIMULATOR.HEADING_RE
archive = SIMULATOR.archive

# Days Part B will sample. Named here, not there, because Part A is what produces the artefact
# Part B reads at each one — see `--json`'s `sampled_days`.
SAMPLE_DAYS = (1, 2, 7, 14, 21, 30, 45, 60, 75, 90)


# ------------------------------------------------------------ real-hook plumbing ----
def run_hook(sb, script: Path, payload: dict | None, cwd: Path | None = None,
             extra_args: list | None = None, timeout: int = 90):
    """One subprocess call into a real plugin script, through the sandbox's own env seam
    (`sb.env` — HOME / GEDAECHTNIS_VAULT / GEDAECHTNIS_STATE_DIR / GEDAECHTNIS_CONFIG all
    redirected). Returns (rc, stdout, stderr) and never raises on a non-zero rc: the caller decides
    what that means, and it must mean UNCHECKED, never a silent zero.

    `cwd` here is the subprocess's OS-level working directory, which every one of these scripts
    resolves its own file locations independently of (via `__file__` and env vars) — mirrors
    `Sandbox.hook_boot_bytes`'s own idiom of running from `PLUGIN` while passing the SANDBOXED
    repo path inside the JSON payload's `cwd` field, which is what the hooks actually key lane
    resolution off of."""
    args = [sys.executable, "-B", str(script)] + (extra_args or [])
    stdin = json.dumps(payload) if payload is not None else None
    return _run_proc(args, cwd=cwd or PLUGIN, env=sb.env, stdin=stdin, timeout=timeout)


def read_json_state(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def session_boot(sb, day: int) -> dict:
    """One simulated session's start: the REAL `session_start.py`, boot bytes read back from its
    own state file via `Sandbox.hook_boot_bytes` (which ALSO asserts the in-process `boot_chain()`
    number agrees — the anchor `eval/simulator/run.py`'s own `snapshot()` uses). Never a second
    computation of the byte count."""
    try:
        boot_bytes = sb.hook_boot_bytes(day)
        return {"day": day, "ok": True, "boot_bytes": boot_bytes}
    except (RuntimeError, AssertionError, OSError) as e:
        return {"day": day, "ok": False, "error": f"{e.__class__.__name__}: {e}"}


def session_boot_with_payload(sb, day: int) -> tuple[dict, str]:
    """Like `session_boot`, but ALSO runs the hook once more to capture its stdout — the
    `additionalContext` block a real session is actually handed at boot — and returns the
    assembled payload text alongside the result. Only called on sampled days: it costs a second
    hook invocation, and 90 of them would double the run's subprocess count for no criterion this
    row measures."""
    result = session_boot(sb, day)
    payload_text = ""
    rc, out, err = run_hook(sb, PLUGIN / "hooks" / "session_start.py",
                            {"session_id": f"sim-day-{day}", "cwd": str(sb.repo),
                             "source": "startup"})
    ctx = ""
    if rc == 0 and out.strip():
        try:
            ctx = json.loads(out).get("hookSpecificOutput", {}).get("additionalContext", "")
        except (ValueError, AttributeError, TypeError):
            ctx = ""
    chain_text = "".join(_chain_text(e) for e in sb.boot_entrypoints)
    payload_text = (
        f"=== @-import chain, {len(sb.boot_entrypoints)} entrypoint(s), day {day} ===\n\n"
        + chain_text
        + "\n\n=== hooks/session_start.py additionalContext, day {} ===\n\n".format(day)
        + (ctx or "(none — hook rc={}, stdout empty or unparsable)".format(rc))
    )
    return result, payload_text


def vault_digest(sb) -> dict:
    """{vault-relative path: bytes} for every file in the sandboxed vault outside `.git` — what a
    proposing pass must leave exactly as it found it."""
    return {str(f.relative_to(sb.vault)): f.read_bytes() for f in sorted(sb.vault.rglob("*"))
            if f.is_file() and ".git" not in f.relative_to(sb.vault).parts}


def revert_line(sb) -> str | None:
    """The `Undo:` line of the vault's newest cleanup commit, or None when there is no such commit
    or it names no undo — read from git, where a person would look, never from the receipt."""
    rc, body, _err = _run_proc(["git", "-C", str(sb.vault), "log", "-1", "--format=%B",
                                "--grep=^gedaechtnis-pass: "], cwd=sb.vault, env=sb.env)
    if rc != 0:
        return None
    for line in body.splitlines():
        if line.startswith("Undo: git ") and " revert " in line:
            return line
    return None


def switch_cleanup_on(sb) -> None:
    """The person's one step after reading a proposal: `cleanup_apply: true` in the `limits`
    object of the sandbox's own config.json, exactly where the proposal line says to put it."""
    cfg = sb.home / ".claude" / "gedaechtnis" / "config.json"
    try:
        doc = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    doc.setdefault("limits", {})["cleanup_apply"] = True
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")


def session_end(sb, day: int, sid: str) -> dict:
    """One simulated session's end: the REAL `hooks/maintenance.py`, then — only when ITS OWN
    state says a cleanup pass is due — the REAL `cleanup.py`. Neither "due" nor "applied" is
    decided here; both are read from the artifacts the real scripts wrote."""
    out = {"day": day, "maintenance_ok": False, "cleanup_due": None,
           "cleanup_ran": False, "cleanup_applied": False}
    rc, mout, merr = run_hook(sb, PLUGIN / "hooks" / "maintenance.py",
                              {"cwd": str(sb.repo), "session_id": sid})
    if rc != 0:
        out["maintenance_error"] = (merr or mout or "").strip()[:2000]
        return out
    out["maintenance_ok"] = True
    state = read_json_state(sb.state / "maintenance.json")
    out["maintenance_compactions"] = len((state or {}).get("compacted") or [])
    due = bool(state and (state.get("cleanup") or {}).get("due"))
    out["cleanup_due"] = due
    out["cleanup_due_arms"] = (
        {k: v.get("fired") for k, v in (state.get("cleanup", {}).get("arms") or {}).items()}
        if state else None)
    if not due:
        return out
    before = vault_digest(sb)
    rc2, cout, cerr = run_hook(sb, PLUGIN / "cleanup.py", None, cwd=sb.repo,
                               extra_args=["--json", "--session-id", sid])
    out["cleanup_ran"] = True
    out["cleanup_vault_unchanged"] = vault_digest(sb) == before
    if rc2 != 0:
        out["cleanup_error"] = (cerr or cout or "").strip()[:2000]
        return out
    try:
        receipt = json.loads(cout)
    except ValueError:
        out["cleanup_error"] = f"non-JSON receipt: {cout[:500]!r}"
        return out
    if receipt.get("proposing_only"):
        # PROPOSE FIRST (destructive.py rule 3): until the vault sets `cleanup_apply`, a due pass
        # records what it would change and moves nothing. This receipt carries `proposed`, never
        # `moved`/`folded` — reading only those two keys is how a proposing pass came to read as
        # "never had anything to apply".
        proposed = receipt.get("proposed") or []
        out["cleanup_proposing_only"] = True
        out["cleanup_proposed"] = [{"path": i["path"], "kind": i.get("kind")} for i in proposed]
        return out
    moved = receipt.get("moved") or []
    folded = receipt.get("folded") or []
    out["cleanup_applied"] = bool(moved or folded)
    if out["cleanup_applied"]:
        out["cleanup_files_touched"] = len({m["path"] for m in moved} | {f["path"] for f in folded})
        out["cleanup_revert_line"] = revert_line(sb)
    out["cleanup_receipt"] = {
        "moved": [{"path": m["path"], "heading": m.get("heading")} for m in moved],
        "folded": [{"path": f["path"], "entries": f.get("entries"), "limit": f.get("limit")}
                   for f in folded],
        "reported_stale": len(receipt.get("reported") or []),
        "failed": receipt.get("failed") or [],
    }
    return out


# --------------------------------------------------------------- wikilink identity ----
def wikilink_snapshot(sb) -> dict:
    """`{health, literal, targets}` or `{error: ...}` when a memory file could not be read.

    Every measurement here reads files inside the sandboxed vault, and an unreadable one (a
    permission bit flipped, a mid-write truncation, a corrupted mode) must produce UNCHECKED for
    the criterion that depends on it — never let the whole 90-day run crash on day 47 because one
    file could not be opened, and never let the missing measurement silently read as "no links"."""
    try:
        return {"health": wikilink_health(sb), "literal": wikilink_literal(sb),
                "targets": resolved_targets(sb)}
    except OSError as e:
        return {"error": f"{e.__class__.__name__}: {e}"}


def resolved_targets(sb) -> set[tuple[str, str]]:
    """{(stem, heading)} for every distinct wikilink TARGET that currently resolves in the
    sandbox's region — the family-credit `headings` construction is `wikilink_health`'s own
    (restated here, not re-derived: same regexes, same `archive.is_sidecar` /
    `archive.live_stem_of` credit for a segmented target), just returned as a set of identities
    instead of a resolved/total count.

    Keyed on the TARGET, deliberately, not on the source occurrence: `Author.entry` writes a
    wikilink once and the text never moves independently of the entry that contains it — if that
    entry itself gets archived, the link text moves WITH it and still exists somewhere. What this
    criterion is watching for is a target heading that stops being reachable BY THAT NAME, which is
    exactly what `wikilink_health`'s aggregate measures — this just lets a day-0-vs-day-90 diff
    name which target broke instead of only reporting a count that moved."""
    region = sb.vault / sb.region
    headings: dict[str, set[str]] = {}
    for path in sorted(region.glob("*.md")):
        found = set(HEADING_RE.findall(path.read_text(encoding="utf-8")))
        headings.setdefault(path.stem, set()).update(found)
        if archive.is_sidecar(path.stem):
            headings.setdefault(archive.live_stem_of(path.stem), set()).update(found)
    ids: set[tuple[str, str]] = set()
    for path in sorted(region.glob("*.md")):
        for stem, heading in WIKILINK_RE.findall(path.read_text(encoding="utf-8")):
            s, h = stem.strip(), heading.strip()
            if h in headings.get(s, ()):
                ids.add((s, h))
    return ids


# ------------------------------------------------------------------------- main run ----
# How a fixture written FOR a test TALKS ABOUT ITSELF. A corpus doing any of this has already
# failed its purpose: a reader can tell, and so can the session under measurement — which is how
# the first Part B run came to be answering the fixture instead of the package.
#
# ★ PHRASES, NOT WORDS, and the first version of this list was words. It fired on a gardener
# writing "haven't changed anything to test it" and "keep meaning to get it tested properly" —
# ordinary English about soil, in exactly the register the fixture is supposed to have. A checker
# that fires on a non-claim is repaired in the EXTRACTOR, never silenced with an exemption, and the
# repair is to match the thing actually forbidden: a note describing its own artificiality.
CORPUS_TELLS = (
    r"test (data|fixture|vault|corpus|entr\w+|case)",
    r"(sample|placeholder|synthetic|generated|dummy|mock|fake) (data|text|content|entr\w+|note)",
    r"for (testing|test) purposes",
    r"lorem ipsum",
    r"this (is|was) (a|an) (test|example|fixture|sample)",
)
CORPUS_STEMS = {"Position", "Canon", "Patterns", "Errata", "Aporia"}


def corpus_problems(entries) -> list:
    """Everything that disqualifies a corpus as Part B's fixture, checked BEFORE any session runs.

    ★ These are deterministic and they are a GATE, not a report. A fixture flaw costs exactly what
    a good fixture costs — ten real sessions — and produces a number about itself. The first run
    of Part B spent that and measured the filler.

    What is checked is what the row committed to: enough entries, a spread across the horizon
    rather than a clump, every temptation actually present, no load-generated filler, no owner
    name, and no word that tells a reader this was written for a test. What is NOT checked is
    whether the prose is any good — that is a judgment, and it belongs to the person who reads it."""
    problems = []
    if not isinstance(entries, list):
        return [f"the corpus is a {type(entries).__name__}, not a list of entries"]
    if len(entries) < 50:
        problems.append(f"{len(entries)} entries; the row committed to ~60 and at least 50 — "
                        f"a thinner vault is a different measurement")
    days, stems, bodies, headings = [], set(), [], []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not {"day", "stem", "heading", "body"} <= set(e):
            problems.append(f"entry {i} is missing one of day/stem/heading/body")
            continue
        try:
            days.append(int(e["day"]))
        except (TypeError, ValueError):
            problems.append(f"entry {i} has a non-numeric day {e['day']!r}")
        stems.add(str(e["stem"]))
        bodies.append(str(e["body"]))
        headings.append(str(e.get("heading", "")))
        if str(e["body"]).lstrip().startswith("#") or "\n#" in str(e["body"]):
            problems.append(f"entry {i} contains a markdown heading inside its body")
    bad_stems = stems - CORPUS_STEMS
    if bad_stems:
        problems.append(f"unknown role stems {sorted(bad_stems)}: the package ships "
                        f"{sorted(CORPUS_STEMS)}")
    if days:
        if min(days) > 5 or max(days) < 85:
            problems.append(f"days run {min(days)}..{max(days)}; the fixture must cover the "
                            f"horizon, not a window inside it")
        if len(set(days)) < 45:
            problems.append(f"{len(set(days))} distinct days across {len(days)} entries — "
                            f"a clump, not ninety days of notes")
    # HEADINGS COUNT. The blob was bodies only, and a person puts the sharpest thing they have to
    # say in the heading — this corpus states both of its explicit reversals there ("changed my
    # mind about the asparagus bed"). Scanning half the text made the gate report ONE reversal on a
    # corpus that carries three, which would have rejected a good fixture and invited the wrong
    # repair: lowering the bar. A check over part of a document is a claim about that part.
    blob = "\n".join(bodies + headings).lower()
    for tell in CORPUS_TELLS:
        m = re.search(tell, blob, re.I)
        if m:
            problems.append(f"the corpus describes itself as artificial ({m.group(0)!r}) — a "
                            f"fixture a reader can identify as a fixture is one the session under "
                            f"measurement can identify too")
    # `re.I`, because `blob` is lowercased two lines up and this pattern anchors on a CAPITAL K.
    # Without it the check could never match anything and had been inert since it was written —
    # found by the negative control that points the gate at the filler it exists to reject, which
    # is the whole reason that control is worth more than another positive one.
    if re.search(r"\bK\d{5}\b", blob, re.I):
        problems.append("load-generated filler tokens (K00001) are present; this corpus exists to "
                        "replace exactly those")
    # The temptations. Each is what makes a question POSSIBLE; a fixture with nothing to ask about
    # measures the same nothing the filler did.
    temptations = {
        "a contradiction of an earlier entry": r"\b(actually|in fact|turns out|opposite|wrong about|"
                                               r"contrary to|earlier I|I said)\b",
        # ★ A REVERSAL IS NOT ONE PHRASE. The first version matched only the explicit spellings
        # and counted 1 on a corpus that contains at least three — *"instead of the leave-it-be
        # approach I was doing earlier in the year"*, *"moving them to the bottom bed from next
        # spring instead"*. A person reverses a decision by describing the new practice against
        # the old one, not by announcing a reversal. Under-coverage in a GATE is the dangerous
        # direction: it rejects a fixture that is fine, and the obvious next move is to weaken the
        # bar rather than the regex — which is how a criterion comes to be written to fit its data.
        "a decision reversed": r"(chang\w+ my mind|decided against|gone back on|no longer|"
                               r"given up on|abandon\w+|instead of [^.]{0,60}?I (was|used to|"
                               r"had been|'?d been)|from next (spring|year|season) instead)",
        "near-duplicate notes": r"\bslug\w*\b",
        "a note too vague to act on": r"\b(not sure|can'?t remember|something about|must look|"
                                      r"come back to|meant to|todo|vague)\b",
    }
    for name, rx in temptations.items():
        n = len(re.findall(rx, blob, re.I))
        if n < 2:
            problems.append(f"the corpus carries {name} {n} time(s); the row committed to at "
                            f"least two of each, spread out")
    return problems


class PersonaAuthor:
    """The simulator's `Author`, with the CONTENT replaced by a real person's notes.

    ★ WHY THIS EXISTS. Part B measures one thing — whether the shipped PROSE makes a session ask
    the user questions — and its first run measured the FIXTURE instead. The vault it read was the
    growth simulator's load-generated filler (`The threshold for the upload is K00001`, the same
    paragraph three times per entry) while the persona talked about an allotment, and three of ten
    replies said so outright; day 90's opened *"this isn't really 'tidy or not' … it's synthetic
    filler."* A session asking what the vault is FOR is answering the fixture.

    So the entries come from a corpus a person's notes could plausibly be, and the corpus carries
    the same temptations the persona messages do — a contradiction of an earlier note, a decision
    reversed, near-duplicates that look like they want deleting, a note too vague to act on. **A
    fixture with nothing to ask about would measure the same nothing the filler did.**

    It is used ONLY when `--entries` is passed. Part A's own path is untouched and its measured
    numbers stand: a byte-identical control asserts that in `test_dadtest.py`.

    The corpus is finite, which the filler was not. When it runs out this stops writing and SAYS
    so — `exhausted_on_day` reaches the result — rather than padding with the filler it exists to
    replace or looping forever on a zero-byte return."""

    def __init__(self, sb, seed: int, entries: list):
        self.sb = sb
        self.rng = random.Random(seed)
        self.n = 0
        self.day = 0
        self.questions: list[dict] = []
        self.headings: list[tuple] = []
        self._entries = list(entries)
        self._i = 0
        self.exhausted_on_day: int | None = None

    def entry(self, stem: str, target_bytes: int) -> int:
        """Write the next persona entry whose day has arrived; `stem` is the corpus's, not the
        caller's, because which role file a note belongs in is part of what the fixture asserts.

        Returns `target_bytes` when nothing is written, so the caller's byte-carry drains and the
        day ends instead of spinning on a zero."""
        if self._i >= len(self._entries):
            if self.exhausted_on_day is None:
                self.exhausted_on_day = self.day
            return target_bytes
        e = self._entries[self._i]
        if int(e.get("day", 1)) > self.day:
            return target_bytes          # not yet: this note is written on a later day
        self._i += 1
        self.n += 1
        heading = str(e["heading"]).strip()
        body = str(e["body"]).strip()
        text = f"\n## {heading}\n\n{body}\n"
        path = self.sb.role(str(e["stem"]))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
        self.headings.append((str(e["stem"]), heading))
        return len(text.encode("utf-8"))


# ★ WHAT THE FIXTURE VAULT SAYS IT IS. Part B's finding was that a person's notes were being kept
# in a vault whose role files were born software-project-shaped, and day 90 said so in both arms:
# "a garden log wearing a project's file structure". `--entries` IS the statement that this run's
# vault is somebody's own notes rather than a software project, so it carries the shape with it —
# a runner that has to remember a second flag is a runner that will one day forget it and quietly
# measure the old defect again. Part A (no `--entries`) keeps `project` in both, byte for byte.
ENTRIES_SHAPE = "notes"
DEFAULT_SHAPE = "project"
DEFAULT_REGION = "project"


def fixture_shape(entries, shape: str | None = None) -> str:
    """What `init.py` is told this fixture vault is for. A function, not an inline conditional, so
    the rule can be exercised without a ninety-day run."""
    return shape or (ENTRIES_SHAPE if entries else DEFAULT_SHAPE)


def run(load: str, curve: str, horizon: int, seed: int, payload_dir: Path,
        sample_days: tuple = SAMPLE_DAYS, entries: list | None = None,
        region: str | None = None, shape: str | None = None,
        start_day: str | None = None) -> dict:
    if load not in LOADS:
        raise ValueError(f"unknown load {load!r}: expected one of {sorted(LOADS)}")
    if curve not in CURVES:
        raise ValueError(f"unknown curve {curve!r}: expected one of {', '.join(CURVES)}")

    # The SAME leak, one level up: `limits` is read IN THIS PROCESS, so an ambient
    # GEDAECHTNIS_LIMITS would set the budget criterion 2 is judged against even after the sandbox's
    # subprocesses were cleaned. Cleared here for the same reason and named in the result, so a
    # number that came from the machine can never be mistaken for one that came from the package.
    #
    # ★ Popping the env is NOT enough since the per-vault limits seam shipped. `limits` now also
    # reads `config.json`'s `limits` object, and `config.config_path()` falls back to the REAL
    # `~/.claude/gedaechtnis/config.json` when GEDAECHTNIS_CONFIG is absent — so removing that
    # variable does not isolate this process, it points it at the machine. The seam is therefore
    # REDIRECTED to a path that cannot exist rather than cleared, which is what the sandbox already
    # does for its subprocesses. Clearing a seam whose default is a real file is the same mistake in
    # the opposite direction.
    for _seam in [k for k in os.environ if k.startswith("GEDAECHTNIS_")]:
        os.environ.pop(_seam, None)
    # The redirect must point at a path that DOES NOT EXIST, and saying so is not the same as
    # being so: a fixed name in the shared temp dir is one stray write or one crashed prior harness
    # away from being a real file, and then this process reads limits from the machine while the
    # comment above promises it cannot. A unique directory makes the sentence true.
    _iso = Path(tempfile.mkdtemp(prefix="ged-dadtest-iso-"))
    _no_config = _iso / "no-such-config.json"
    assert not _no_config.exists(), f"the isolation path must not exist: {_no_config}"
    os.environ["GEDAECHTNIS_CONFIG"] = str(_no_config)
    importlib.reload(limits)
    budget = int(limits.get("boot_budget_bytes"))
    payload_dir = Path(payload_dir)
    payload_dir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="ged-dadtest-"))
    region = region or DEFAULT_REGION
    shape = fixture_shape(entries, shape)
    # ★ THE SIMULATED CALENDAR, ON BY DEFAULT. Every one of these ninety days used to fall on the
    # same real date, so `days_between` was 0 throughout and the cleanup and synthesis time
    # triggers (14 days each) were structurally unreachable — Part A's "cleanup never became due in
    # 90 days" was a fact about this harness, not about how much a person writes. It is on by
    # default and not behind a flag for the reason TEMPLATESHAPE-1's shape is carried by
    # `--entries`: a harness that has to remember a flag is one that will one day forget it and
    # quietly re-measure the old defect.
    start_day = SIMULATOR.calendar_start(horizon, start_day)
    sb = Sandbox(root, region=region, shape=shape, start_day=start_day)
    sb.advance(1)                      # install() runs on day 1, not on the machine's today
    sb.install()
    author = PersonaAuthor(sb, seed, entries) if entries else Author(sb, seed)
    rr, rb, provenance = LOADS[load]
    carry_boot = carry_other = 0.0

    days: list[dict] = []
    sampled_days: dict[int, dict] = {}
    day0_snapshot: dict | None = None
    switched_on_day: int | None = None
    baseline_snapshot: dict | None = None

    for day in range(1, horizon + 1):
        sb.advance(day)
        author.day = day
        sid = f"dadtest-day-{day}"

        # -- session start: boot bytes, every day (this is criterion 2's population) --
        sampled = day in sample_days
        if sampled:
            boot, payload_text = session_boot_with_payload(sb, day)
            payload_path = payload_dir / f"boot-day-{day:03d}.txt"
            payload_path.write_text(payload_text, encoding="utf-8")
        else:
            boot = session_boot(sb, day)
            payload_path = None

        # -- the session's own work: today's growth, at the declared load/curve --
        f = curve_factor(curve, day)
        carry_boot += rb * f
        carry_other += rr * f
        while carry_boot >= ENTRY_B:
            carry_boot -= author.entry(BOOT_STEM, ENTRY_B)
        while carry_other >= ENTRY_B:
            carry_other -= author.entry(author.rng.choice(OTHER_STEMS), ENTRY_B)

        # Day-0 baseline: right after day 1's growth, before ANY session end has ever run —
        # i.e. before compaction has ever touched the vault.
        if day0_snapshot is None:
            day0_snapshot = {"day": day, **wikilink_snapshot(sb)}

        # -- session end: the real maintenance pass, and the real cleanup if it says so --
        end = session_end(sb, day, sid)

        row = {"day": day, **boot, **end}
        days.append(row)
        # The person reads the first proposal that names something and switches cleanup on. The
        # links that resolve at that moment, before any pass has moved anything, are criterion 4's
        # baseline: day 1 has no links yet, so a day-0 baseline could only ever pass.
        if switched_on_day is None and end.get("cleanup_proposed"):
            baseline_snapshot = {"day": day, **wikilink_snapshot(sb)}
            switch_cleanup_on(sb)
            switched_on_day = day
            row["cleanup_switched_on"] = True
        if sampled:
            snap = wikilink_snapshot(sb)
            row["boot_payload_path"] = str(payload_path)
            sampled_days[day] = {**{k: v for k, v in snap.items() if k != "targets"},
                                 "boot_payload_path": str(payload_path)}

    day90_snapshot = {"day": horizon, **wikilink_snapshot(sb)}

    def fmt_snap(snap: dict) -> dict:
        if "error" in snap:
            return {"day": snap.get("day"), "error": snap["error"]}
        return {"day": snap["day"], "wikilink_health": list(snap["health"]),
                "wikilink_literal": list(snap["literal"])}

    result = {
        "load": load, "curve": curve, "horizon": horizon, "seed": seed,
        "provenance": provenance, "boot_budget_bytes": budget,
        "sandbox_root": str(root), "region": region, "shape": shape,
        "start_day": start_day, "last_day": sb.day_iso, "days": days,
        "day0": fmt_snap(day0_snapshot),
        "baseline": fmt_snap(baseline_snapshot or day0_snapshot),
        "switched_on_day": switched_on_day,
        "max_files_per_pass": int(limits.get("max_files_per_pass")),
        "day90": fmt_snap(day90_snapshot),
        "sampled_days": sampled_days,
        "vault_final_bytes": sum(p.stat().st_size for p in sb.vault.rglob("*.md")),
        "entries_written": author.n,
    }
    result["criteria"] = evaluate_criteria(result, (baseline_snapshot or day0_snapshot).get("targets"),
                                           day90_snapshot.get("targets"))
    return result


def wikilink_criterion(day0_targets, day90_targets, day0_doc: dict, day90_doc: dict) -> dict:
    """PASS/FAIL/UNCHECKED for "links that resolved at day 0 still resolve at day 90", as its own
    function so a test can drive it directly with synthetic before/after sets rather than a whole
    90-day run — a `None` target set (an unreadable memory file at that snapshot) is UNCHECKED,
    never read as an empty vault."""
    if day0_targets is None or day90_targets is None:
        bad = ("day 0" if day0_targets is None else "") + \
              (" and " if day0_targets is None and day90_targets is None else "") + \
              ("day 90" if day90_targets is None else "")
        return {"status": "UNCHECKED",
                "detail": f"a memory file could not be read while checking wikilinks at {bad}: "
                          f"{day0_doc.get('error') or day90_doc.get('error')}",
                "day0": day0_doc, "day90": day90_doc}
    broken = sorted(day0_targets - day90_targets)
    if broken:
        c4 = {"status": "FAIL",
              "detail": f"{len(broken)} of {len(day0_targets)} link target(s) that resolved "
                        f"at the baseline (day {day0_doc.get('day')}) no longer resolve at day 90",
              "broken_targets": [f"{s}#{h}" for s, h in broken[:20]]}
    elif not day0_targets:
        # VACUOUS, not PASS: with nothing to regress the check cannot fail, and a green that
        # cannot be red is a fact about the fixture (A1's day-0 baseline read this way in every run).
        c4 = {"status": "VACUOUS", "detail": "no wikilink resolved at the baseline, so there was "
              "nothing to regress — this criterion measured nothing"}
    else:
        c4 = {"status": "PASS", "detail": f"all {len(day0_targets)} link target(s) resolvable "
              f"at the baseline (day {day0_doc.get('day')}) are still resolvable at day 90"}
    c4["day0"] = day0_doc
    c4["day90"] = day90_doc
    return c4


# ------------------------------------------------------------------------ verdicts ----
def evaluate_criteria(result: dict, day0_targets: set, day90_targets: set) -> dict:
    """The four ROW-A1 criteria, as PASS / FAIL / UNCHECKED — never a fourth spelling of zero.

    Criterion 1 (<=3 questions to the user) is Part B's — a model-in-the-loop measurement this
    script makes no API call to take, so it is UNCHECKED here by construction, and the OVERALL
    verdict below is never PASS while it is: an UNCHECKED criterion is a criterion that has not
    been decided, not one that has passed by default."""
    days = result["days"]
    budget = result["boot_budget_bytes"]

    # -- criterion 2: boot bytes never exceed the budget -----------------------------
    measured = [d for d in days if d.get("ok")]
    unmeasured_boot = [d["day"] for d in days if not d.get("ok")]
    over = [d for d in measured if d["boot_bytes"] > budget]
    if not measured:
        c2 = {"status": "UNCHECKED", "detail": "the session_start.py hook never produced a "
              "readable boot-bytes number on any of the 90 simulated days",
              "unmeasured_days": unmeasured_boot}
    elif over:
        c2 = {"status": "FAIL", "detail": f"{len(over)} of {len(days)} day(s) booted over the "
              f"{budget:,} B budget", "over_budget_days": [d["day"] for d in over[:20]],
              "max_boot_bytes": max(d["boot_bytes"] for d in measured),
              "unmeasured_days": unmeasured_boot}
    elif unmeasured_boot:
        c2 = {"status": "UNCHECKED", "detail": f"no measured day exceeded {budget:,} B, but "
              f"{len(unmeasured_boot)} day(s) could not be measured at all and are not covered "
              "by that finding", "unmeasured_days": unmeasured_boot,
              "max_boot_bytes": max(d["boot_bytes"] for d in measured)}
    else:
        c2 = {"status": "PASS", "detail": f"all {len(days)} simulated days booted under the "
              f"{budget:,} B budget", "max_boot_bytes": max(d["boot_bytes"] for d in measured)}

    # -- criterion 3: a cleanup becomes due, proposes, and applies once switched on --------
    # PROPOSE FIRST (MAINTCAL-1 / CLEANUPGATE-1, destructive.py rule 3): a fresh install's cleanup
    # records what it would change and moves nothing until the person sets `cleanup_apply`. So the
    # criterion is four facts, each read off what the real scripts wrote: (a) a due pass PROPOSED
    # something; (b) every proposing pass left the vault byte-identical; (c) after the switch a
    # pass APPLIED, touching at most `max_files_per_pass` files; (d) its commit names the command
    # that undoes it. The old criterion ("fires and applies, unprompted") cannot pass on a
    # propose-first package by design, and a FAIL that only restates the design measures nothing.
    maint_ok_days = [d for d in days if d.get("maintenance_ok")]
    due_days = [d for d in days if d.get("cleanup_due")]
    proposing = [d for d in days if d.get("cleanup_proposing_only")]
    proposed = [d for d in proposing if d.get("cleanup_proposed")]
    leaked = [d["day"] for d in proposing if d.get("cleanup_vault_unchanged") is False]
    applied_days = [d for d in days if d.get("cleanup_applied")]
    max_files = result.get("max_files_per_pass")
    had_errors = any(not d.get("maintenance_ok") for d in days) or \
        any(d.get("cleanup_ran") and "cleanup_error" in d for d in days)
    base = {"first_due_day": due_days[0]["day"] if due_days else None,
            "due_sessions": len(due_days), "proposing_sessions": len(proposing),
            "sessions_that_proposed_something": len(proposed),
            "first_proposal_day": proposed[0]["day"] if proposed else None,
            "switched_on_day": result.get("switched_on_day"),
            "applied_sessions": len(applied_days)}
    if not maint_ok_days:
        c3 = {"status": "UNCHECKED", "detail": "hooks/maintenance.py never ran successfully on "
              "any of the 90 simulated days"}
    elif leaked:
        c3 = {"status": "FAIL", "detail": f"a proposing pass changed the vault on day(s) "
              f"{leaked[:10]} — proposing must move nothing", **base}
    elif applied_days:
        first = applied_days[0]
        over = [d["day"] for d in applied_days
                if max_files is not None and d.get("cleanup_files_touched", 0) > max_files]
        no_undo = [d["day"] for d in applied_days if not d.get("cleanup_revert_line")]
        if over:
            c3 = {"status": "FAIL", "detail": f"an applying pass touched more than "
                  f"{max_files} files (max_files_per_pass) on day(s) {over[:10]}", **base}
        elif no_undo:
            c3 = {"status": "FAIL", "detail": f"an applying pass on day(s) {no_undo[:10]} left no "
                  "commit naming the command that undoes it", **base}
        else:
            c3 = {"status": "PASS",
                  "detail": f"cleanup became due on day {base['first_due_day']}, first proposed "
                            f"something on day {base['first_proposal_day']} with the vault left "
                            f"unchanged ({len(proposing)} proposing session(s), none changed a "
                            f"file), was switched on that day, and first applied on day "
                            f"{first['day']}: {first.get('cleanup_files_touched')} file(s), within "
                            f"{max_files}, commit names its undo",
                  **base, "first_applied_day": first["day"],
                  "receipt": first.get("cleanup_receipt"),
                  "revert_line": first.get("cleanup_revert_line")}
    elif had_errors:
        c3 = {"status": "UNCHECKED", "detail": "no cleanup was observed to apply, but "
              "maintenance.py or cleanup.py failed on at least one day — a real cleanup could "
              "have been hidden behind one of those failures",
              "error_days": [d["day"] for d in days
                             if not d.get("maintenance_ok") or "cleanup_error" in d]}
    elif not due_days:
        c3 = {"status": "FAIL", "detail": f"cleanup never became due in {result['horizon']} days",
              **base}
    elif not proposed:
        c3 = {"status": "FAIL", "detail": f"cleanup became due on day {due_days[0]['day']} but "
              f"never proposed anything ({len(due_days)} due session(s), 0 proposals)", **base}
    else:
        c3 = {"status": "FAIL", "detail": f"cleanup proposed on day {proposed[0]['day']} and was "
              f"switched on, but never applied by day {result['horizon']}", **base}

    # -- criterion 4: wikilinks that resolved at the baseline still resolve at day 90 --
    # The baseline is the day cleanup was switched on (before any pass moved anything), or day 1
    # when it never was; `day0_targets` keeps its name for the tests that drive it directly.
    c4 = wikilink_criterion(day0_targets, day90_targets, result.get("baseline", result["day0"]),
                            result["day90"])

    c1 = {"status": "UNCHECKED",
          "detail": "criterion 1 (<=3 questions to the user) is Part B's — it needs a model "
                    "reading the assembled boot payload, which this script does not call. "
                    "See `sampled_days` for the payload files Part B reads."}

    statuses = {"1_questions_to_user": c1, "2_boot_within_budget": c2,
                "3_cleanup_proposes_then_applies": c3, "4_wikilinks_resolve_day90": c4}
    part_a_statuses = [c2["status"], c3["status"], c4["status"]]
    if "UNCHECKED" in part_a_statuses or "VACUOUS" in part_a_statuses:
        part_a = "UNCHECKED"
    elif "FAIL" in part_a_statuses:
        part_a = "FAIL"
    else:
        part_a = "PASS"
    return {**statuses, "part_a_verdict": part_a,
            "overall_verdict": "UNCHECKED",
            "overall_verdict_note": "criterion 1 is not measured by this script (Part B); the "
                                     "overall verdict cannot be PASS until it is."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--horizon", type=int, default=90)
    ap.add_argument("--load", default="low", choices=sorted(LOADS),
                    help="low = the person this row is about; medium = the named stress arm")
    ap.add_argument("--curve", default="linear", choices=list(CURVES))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--payload-dir", help="where sampled-day boot payloads are saved "
                                          "(default: a fresh temp dir, left on disk)")
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--entries", help="a persona corpus (JSON array of day/stem/heading/body) to "
                                      "write INSTEAD of generated filler — Part B's fixture. "
                                      "Without it Part A behaves exactly as before.")
    ap.add_argument("--start-day", default=None,
                    help="ISO date simulated day 1 falls on (default: today minus "
                         "horizon-1, so the last simulated day is today)")
    ap.add_argument("--region", default=None,
                    help=f"the fixture region's folder name (default: {DEFAULT_REGION})")
    ap.add_argument("--shape", default=None,
                    help="what the fixture vault is for, passed to init.py (default: "
                         f"{ENTRIES_SHAPE} with --entries, {DEFAULT_SHAPE} without)")
    a = ap.parse_args(argv)

    payload_dir = Path(a.payload_dir) if a.payload_dir else Path(tempfile.mkdtemp(
        prefix="ged-dadtest-payloads-"))
    payload_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    corpus = None
    if a.entries:
        corpus = json.loads(Path(a.entries).read_text(encoding="utf-8"))
        problems = corpus_problems(corpus)
        if problems:
            # REFUSED BEFORE SPENDING. Ten sessions over a bad fixture cost the same as ten over a
            # good one and measure the fixture.
            print("dad test: the entry corpus is not usable as a fixture:", file=sys.stderr)
            for pr in problems:
                print(f"  {pr}", file=sys.stderr)
            return 2
    result = run(a.load, a.curve, a.horizon, a.seed, payload_dir, entries=corpus,
                 region=a.region, shape=a.shape, start_day=a.start_day)
    result["wall_seconds"] = round(time.time() - t0, 1)
    result["payload_dir"] = str(payload_dir)

    c = result["criteria"]
    print(f"dad test, Part A — load={a.load} curve={a.curve} horizon={a.horizon}d "
          f"({result['wall_seconds']}s)")
    print(f"  sandbox: {result['sandbox_root']}")
    print(f"  payloads: {payload_dir}")
    for key in ("2_boot_within_budget", "3_cleanup_proposes_then_applies", "4_wikilinks_resolve_day90"):
        entry = c[key]
        print(f"  [{entry['status']:>9}] {key}: {entry['detail']}")
    print(f"  [{c['1_questions_to_user']['status']:>9}] 1_questions_to_user: "
          f"{c['1_questions_to_user']['detail']}")
    print(f"\nPart A verdict: {c['part_a_verdict']} — overall: {c['overall_verdict']} "
          f"({c['overall_verdict_note']})")

    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
        print(f"\njson -> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
