#!/usr/bin/env python3
"""memory_eval/run.py — WP8: three arms, cross-session tasks. Stub by default; real `claude -p` on `--live`.

    python3 run.py --dry-run                                      # the default: no model call, no network
    python3 run.py --live --claude /path/to/claude \
                   --model claude-sonnet-5 --effort low --ceiling-usd 10

Three arms per DESIGN.md §7.2.1: **none** (no memory mechanism at all), **automemory** (a
stand-in for Claude Code's own `~/.claude/projects/<mangled-cwd>/memory/MEMORY.md`), and
**gedaechtnis** (this plugin, installed fresh via `init.py` into a throwaway HOME/repo). Each task
is two steps — a day-1 setup and a later-day task — run through a `claude` COMMAND (default: this
folder's `stub_claude.py`) with `--model` always pinned, scored by a checker that reads the
throwaway repo's own filesystem state afterwards.

**Every step goes through `claude`; nothing here reads a task's `fact` field to answer it for a
model.** What differs between arms is entirely what CONTEXT reaches the day-N prompt: `none` gets
none; `automemory` gets whatever landed in its fake `MEMORY.md`; `gedaechtnis` gets whatever the
real, unmodified `recall.py` finds in a vault that `init.py` actually created. The day-1 "decision"
itself is written into each arm's memory substrate directly by this harness (not derived from the
stub's day-1 answer, which is never parsed for content) — that step stands in for what an agent
following `rules/operating-rules.md` would do after deciding something, and is the one place a real
model would eventually replace a scripted action.

**The positive control.** Task 001 cannot be answered from the day-N prompt or the repo alone — see
`tasks/001-db-choice.json`. If the `none` arm ever succeeds on it, either a fact leaked into that
arm's prompt/repo by accident, or the checker is too loose; `tests/test_eval_skeletons.py` asserts
this task fails under `none` and treats a pass as a test failure, per the harness-vacuity rule in
`Global/Patterns-verification.md` ("a check whose trigger condition never occurs... is VACUOUS").

--------------------------------------------------------------------------------------------------
LIVE MODE (`--live`) — a real `claude -p` per step, spending real money
--------------------------------------------------------------------------------------------------

`--dry-run` is still the default mode and its behaviour is byte-identical to the skeleton's: same
arms, same ordering, same row keys, same `results.md`. `--live` is a deliberate, separate path.

*The process shape* is copied from `scripts/concilium.py convene`, whose launch facts were
measured, not inferred (CLI 2.1.263): the prompt goes immediately after the boolean `-p` so no
VARIADIC flag (`--allowedTools`, `--add-dir`, `--tools`) can swallow it; `stdin=DEVNULL` always
(`claude -p` reads inherited stdin and appends it to the prompt); `--setting-sources project`, so
the launching user's `~/.claude/CLAUDE.md` chain and user-level plugins are NOT loaded and each
step's context is the fixture's alone; `--output-format json`; `--permission-prompts none`;
`--max-turns` and optionally `--max-budget-usd` pinned; and NEVER `--resume` (a resumed `claude -p`
does not amortize the prefix — every step here is a fresh process).

*Model and effort are pinned from the arguments and refused if absent* — a bare launch is never a
routing decision. The model is also checked against the price table BEFORE the first call: an
unknown model refuses rather than producing an unpriced run.

*Cost* is computed by IMPORTING the price table and pricing functions from `scripts/concilium.py`
(`--pricing PATH`, or `$GEDAECHTNIS_PRICING_PY`, else `<plugin>/../scripts/concilium.py`). Nothing
here carries a copy of that table: a generator that carries a copy of its authority document
diverges silently. Every call's whole result JSON is written to
`<out>/<arm>-<task>-<day>.usage.json` alongside the argv it was launched with, and `--ceiling-usd`
REFUSES to start another call once the priced sum reaches it, printing what was completed.

**What a live run CANNOT isolate — state this in any report built from its numbers:**

1. **The `automemory` arm does not exercise Claude Code's own memory loader.** A live session's
   auto-memory directory is decided by its CWD (`~/.claude/projects/<mangled-cwd>/memory/`) under
   whatever HOME the process has, and there is no supported way to make the CLI load a memory file
   from a throwaway location. This harness therefore keeps the SKELETON'S CONTRACT in live mode
   too: it writes the Claude-Code-SHAPED `MEMORY.md` into the fixture and feeds its text to the
   day-N prompt. The arm measures "a plain prior-decisions file in the prompt" — a fair,
   deliberately cheap baseline — and NOT "Claude Code's built-in auto-memory as the product ships
   it". No number from this arm supports a claim about that product feature.
2. **HOME.** A real `claude` authenticates out of the launching user's HOME, so `--claude-home
   real` (the default) leaves HOME alone and isolates through `--setting-sources project` plus the
   fixture's own `GEDAECHTNIS_*` environment instead. That means a live run does NOT prove the
   session was blind to everything under the real HOME — only that no user-level settings, plugin
   or CLAUDE.md chain was loaded by the documented mechanism. `--claude-home fixture` gives full
   HOME isolation and will only work if the CLI can authenticate without the user's HOME.
   `init.py`, `recall.py` and the session-start hook ALWAYS run against the fixture HOME.
3. **One sample per (arm, task).** Nothing here repeats a step or varies a seed, so a live run
   measures a sample of one per cell — enough to decide "does the plumbing carry the fact", never
   enough to rank two arms whose success rates differ by one task.
4. **The `gedaechtnis` arm loads the plugin from a project settings file** written into the
   fixture repo (`.claude/settings.json`, `${CLAUDE_PLUGIN_ROOT}` expanded to this plugin's real
   path), because `--setting-sources project` deliberately drops user-level plugins. That is the
   same hook set the plugin ships, but it is not proof that a normally-installed plugin behaves
   identically. Staging happens before the day-N step only — the day-1 step of every arm runs
   identically, which is what makes the arms comparable at day N. Installing the plugin the
   ordinary way into a FRESH HOME is a different, still-open test (FRESHHOME-1): a fresh HOME has
   no credentials, so the CLI cannot authenticate there, and nothing in this harness works around
   that. No number from this file speaks to the install path.

--------------------------------------------------------------------------------------------------
THE SECOND EVAL — four arms, samples, byte-matching, and a checker that bites
--------------------------------------------------------------------------------------------------

(No date is written into this file on purpose: `tests/test_memory_eval_live.py` asserts that the
pricing module's `PRICE_ASOF` string appears nowhere in this source, which is how "the price table
is imported, never copied" is checked. A date in a comment here would satisfy that grep for the
wrong reason. The dated reports live under `.orchestration/review/memoryeval-*/`.)

The first live run (its `RESULT.md` in that directory) had three weaknesses its own report named.
Each has a mechanism here:

* **A checker satisfied by a MENTION.** `checker_must_contain` alone passed a `none`-arm answer
  that was a *question* listing "no bare excepts" as one example of a convention it did not have.
  It also FAILED a correct answer that wrote "bare \\`except\\`" with markdown backticks. So a
  check now runs over a NORMALISED answer (backticks and emphasis asterisks removed, whitespace
  collapsed) and has three parts: `checker_must_contain` (case-insensitive substring — the fact is
  present), `checker_must_match` (every regex must match — the fact is ASSERTED as the project's
  rule/value, not merely uttered), and `checker_must_not_match` plus a shared `HEDGE_PATTERNS`
  list (no answer that disclaims having a record, asks the user what the convention should be, or
  offers parenthesised examples can pass, whatever words it contains).
* **A confound: the memory arms did not carry the same number of bytes.** `automemory` reached
  day N with 76–149 B against gedaechtnis's 2,468–3,082 B, so "more relevant context helps" was
  inside the result. `--byte-match` computes each task's gedaechtnis context size in a no-cost
  pre-pass (init + a real `recall.py` call, no model), then pads the prior-decisions file with
  plausible unrelated prior decisions to that exact byte count. Both numbers are on every row.
* **One sample per cell.** `--samples N` repeats the DAY-N step N times per (arm, task) — the
  day-1 step is not repeated, because nothing reads its answer: the memory substrate is written by
  this harness, so a second day-1 call would measure nothing and cost the same as one that does.

A fourth arm, `swapped`, is new: the gedaechtnis mechanism carrying a memory whose fact is the
plausible OPPOSITE of the truth (each task's `swap` block). It passes only when the day-N answer
FOLLOWS the swap — the true fact is added to the swapped checker's `must_not_match` by
construction. A gedaechtnis pass rate that is high while the swapped arm keeps returning the true
answer would mean the model is guessing well, not reading the vault; this arm is what tells those
apart.
"""
from __future__ import annotations
import argparse, datetime, importlib.util, json, os, re, subprocess, sys, tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))
from _stub_client import call_claude, usage_tokens  # noqa: E402

STUB = EVAL / "stub_claude.py"
DEFAULT_TASKS_DIR = Path(__file__).resolve().parent / "tasks"
ARMS = ("none", "automemory", "gedaechtnis", "swapped")
# The arms that carry a SWAPPED memory (the fact replaced by its plausible opposite). Kept as a
# set, not a name test, so the checker/substrate code never has to string-compare an arm id twice.
SWAPPED_ARMS = frozenset({"swapped"})
# The arms whose substrate is the plugin itself (init.py + a real recall.py call + staged hooks).
VAULT_ARMS = frozenset({"gedaechtnis", "swapped"})
BOOT_RE = re.compile(r"- boot: ([\d,]+) B across (\d+) files")

# ------------------------------------------------------------------ checking ----
# Markdown a model wraps a token in, and which must not decide a pass. The first live run FAILED a
# correct answer because it wrote "bare `except`" with backticks.
_STRIP_CHARS = "`*"
_WS = re.compile(r"\s+")

# Shared disqualifiers, applied to EVERY task's answer on top of its own patterns. An answer that
# disclaims having a record, asks the questioner what the convention should be, or lists
# parenthesised examples is not an answer — however many of the right words it contains. Every
# pattern below fires on the exact `none`-arm reply that wrongly passed task 003 in the first live
# run (kept verbatim as a regression fixture in tests/test_memory_eval_checker.py).
HEDGE_PATTERNS = (
    r"(?i)\bno (?:memory|record|records|entry|entries|context) (?:exists?|is available|has been)\b",
    r"(?i)\bno (?:recorded|prior|stored|previous|earlier) "
    r"(?:records?|recorded|memory|decision|decisions|rule|rules|convention|information)\b",
    r"(?i)\b(?:i|we)\s+(?:don't|do not|doesn't|have no|haven't)\b[^.\n]{0,60}"
    r"\b(?:records?|recorded|memory|information|context|note of)\b",
    r"(?i)\bcould you (?:tell|let|clarify|specify|confirm|remind)\b",
    r"(?i)\bwhat (?:convention|rule|value|name|standard|setting) (?:do|would|should|did)\s+you\b",
    r"(?i)\(\s*e\.g\.,?\s",
    r"(?i)\bthere (?:is|are) no (?:record|prior|stored|saved)\b",
)


def normalise_answer(text: str) -> str:
    """What the checker actually reads: the answer with markdown emphasis stripped and whitespace
    collapsed. NOT case-folded — the regexes carry their own `(?i)` where they want it; the
    substring test folds case itself."""
    out = "".join(ch for ch in (text or "") if ch not in _STRIP_CHARS)
    return _WS.sub(" ", out).strip()


def checker_spec(task: dict, swapped: bool) -> dict:
    """The three checker fields for one polarity of a task.

    On the SWAPPED side the true fact is appended to `must_not_match` by construction: an answer
    that names the truth cannot pass a cell whose memory said the opposite, and no author has to
    remember to write that clause."""
    src = task["swap"] if swapped else task
    must_not = list(src.get("checker_must_not_match") or [])
    if swapped:
        must_not.append(r"(?i)" + re.escape(task["fact"]))
    return {"must_contain": src["checker_must_contain"],
            "must_match": list(src.get("checker_must_match") or []),
            "must_not_match": must_not}


def check_answer(task: dict, answer: str, swapped: bool = False) -> dict:
    """Score one day-N answer. Returns the verdict AND why, so a report never has to guess which
    clause decided a cell."""
    spec = checker_spec(task, swapped)
    text = normalise_answer(answer)
    contains = spec["must_contain"].casefold() in text.casefold()
    missing = [p for p in spec["must_match"] if not re.search(p, text)]
    hedged = [p for p in (list(HEDGE_PATTERNS) + spec["must_not_match"]) if re.search(p, text)]
    return {"success": bool(contains and not missing and not hedged),
            "contains": contains, "missing_patterns": missing, "disqualified_by": hedged}

USAGE_SCHEMA = "gedaechtnis-memory-eval-usage/1"
LIVE_DEFAULT_MAX_TURNS = 8
# The steps are question-answering, not building: read-only tools, and this harness (never the
# model) writes the answer file and the memory substrate. A step that needs Write is a different
# experiment.
LIVE_TOOLS = ("Read", "Grep", "Glob")


class LiveRefusal(Exception):
    """A live run that must not start, or must not continue, saying exactly why."""


class CeilingReached(Exception):
    """--ceiling-usd hit: the NEXT call is refused. Not an error — the run stops and reports."""


# ------------------------------------------------------------------- tasks ----
TASK_KEYS = ("task_id", "day1_prompt", "memory_sentence", "canon_heading", "fact",
             "day_n_prompt", "checker_file", "checker_must_contain", "checker_must_match", "swap")
SWAP_KEYS = ("day1_prompt", "memory_sentence", "canon_heading", "fact",
             "checker_must_contain", "checker_must_match")


def task_side(task: dict, swapped: bool) -> dict:
    """The task's own fields, or its `swap` block's — one accessor, so no caller re-implements the
    polarity rule and gets one field from the wrong side."""
    return task["swap"] if swapped else task


def validate_task(task: dict, where: str) -> None:
    """Refuse a task that cannot MEAN anything, before a run spends money on it.

    Two classes are checked. (a) *self-consistency*: the recorded sentence must itself satisfy the
    checker for its own side — if the memory the harness writes would not pass, no arm can pass
    honestly and a failure would be the task's, not the arm's. (b) *polarity separation*: the true
    fact must be absent from the swap's text and vice versa, or a swapped cell's verdict is
    ambiguous."""
    for key in TASK_KEYS:
        if key not in task:
            raise ValueError(f"{where}: task is missing {key!r}")
    if not isinstance(task["swap"], dict):
        raise ValueError(f"{where}: 'swap' must be an object")
    for key in SWAP_KEYS:
        if key not in task["swap"]:
            raise ValueError(f"{where}: task 'swap' block is missing {key!r}")
    # Polarity separation is checked FIRST, because a leak also breaks the self-consistency check
    # below (the swapped checker refuses the true fact by construction) and a reader who sees only
    # "your memory_sentence fails its own checker" would go and edit the checker.
    true_text = " ".join(str(task[k]) for k in ("day1_prompt", "memory_sentence", "canon_heading",
                                                "fact", "day_n_prompt"))
    swap_text = " ".join(str(task["swap"][k]) for k in SWAP_KEYS)
    if task["fact"].casefold() in swap_text.casefold():
        raise ValueError(f"{where}: the true fact {task['fact']!r} appears in the swap block — a "
                         "swapped cell could then pass while naming the truth")
    if task["swap"]["fact"].casefold() in true_text.casefold():
        raise ValueError(f"{where}: the swap fact {task['swap']['fact']!r} appears in the true "
                         "task's text — the true cell would be answerable from the prompt")
    for swapped in (False, True):
        side, label = task_side(task, swapped), "swap" if swapped else "task"
        if not side["checker_must_contain"]:
            raise ValueError(f"{where}: the {label}'s checker_must_contain is empty — that check "
                             "would pass on any string at all")
        verdict = check_answer(task, side["memory_sentence"], swapped=swapped)
        if not verdict["success"]:
            raise ValueError(
                f"{where}: the {label}'s own memory_sentence does not pass its own checker "
                f"({verdict}) — the substrate this harness writes would score FAIL, so no arm "
                "could pass the cell honestly. Fix the sentence or the checker.")


def load_tasks(tasks_dir: Path) -> list[dict]:
    tasks = []
    for p in sorted(tasks_dir.glob("*.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        validate_task(t, str(p))
        tasks.append(t)
    if not tasks:
        raise ValueError(f"{tasks_dir}: no task JSON files found")
    if not any(t.get("positive_control") for t in tasks):
        raise ValueError(f"{tasks_dir}: no task is flagged \"positive_control\": true — the "
                         "harness-vacuity check has nothing to assert (see the module docstring)")
    ids = [t["task_id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{tasks_dir}: duplicate task_id(s) in {sorted(ids)}")
    files = [t["checker_file"] for t in tasks]
    if len(set(files)) != len(files):
        raise ValueError(f"{tasks_dir}: two tasks share a checker_file — one would overwrite the "
                         f"other's answer: {sorted(files)}")
    # Sanity: one task's fact must not accidentally leak into another task's day-N prompt, or a
    # cross-task collision could make the "none" arm pass by ACCIDENT rather than by memory. Both
    # polarities are checked: a swap fact in a neighbour's prompt is the same hazard.
    for a in tasks:
        for b in tasks:
            if a is b:
                continue
            for label, fact in (("fact", a["fact"]), ("swap fact", a["swap"]["fact"])):
                if fact.casefold() in b["day_n_prompt"].casefold():
                    raise ValueError(f"{a['task_id']}'s {label} {fact!r} appears in "
                                     f"{b['task_id']}'s day_n_prompt — this would make a "
                                     "'none'-arm pass ambiguous. Rewrite one of the two.")
    return tasks


def build_answers(tasks: list[dict]) -> dict:
    """Substring -> answer, in the order `stub_claude.py` checks them: every task's FACT key
    first (so a prompt that genuinely carries the recalled fact matches it), then every task's
    plain day-N prompt as a fallback (so a prompt with NO recalled context — the `none` arm — gets
    an honest "I don't know" instead of accidentally matching something else).

    Both polarities get a key, the SWAP first: the swapped arm's prompt carries only the swapped
    fact, so ordering cannot cross the wires, and `validate_task` has already refused any task
    where one polarity's fact appears in the other's text.

    The answer text is the side's own `memory_sentence` — not a phrase this file invents. That is
    the point of the checker self-consistency rule in `validate_task`: a dry run then exercises the
    real `check_answer`, patterns and all, instead of a string this module made pass by
    construction."""
    answers = {}
    for t in tasks:
        answers[t["swap"]["fact"]] = t["swap"]["memory_sentence"]
    for t in tasks:
        answers[t["fact"]] = t["memory_sentence"]
    for t in tasks:
        answers[t["day_n_prompt"]] = ("No memory exists yet, so I have no record of that decision "
                                      "from this session alone.")
    return answers


# --------------------------------------------------------------- automemory ----
def automemory_path(home: Path, repo: Path) -> Path:
    """A stand-in for Claude Code's own `~/.claude/projects/<mangled-cwd>/memory/MEMORY.md`. This
    mimics the SHAPE of that convention (the project-mangled directory under `.claude/projects/`)
    for a fair token/success comparison; it is not a byte-exact replica and nothing here asserts
    that a real Claude Code session would load it — the automemory arm is deliberately the
    cheapest, most literal reading of "give it a plain memory file and see if that alone helps".
    Live mode keeps exactly this contract; see the module docstring, limitation 1."""
    mangled = str(repo).replace(os.sep, "-")
    return home / ".claude" / "projects" / mangled / "memory" / "MEMORY.md"


# Plausible unrelated prior decisions, used ONLY to pad the prior-decisions file up to the
# gedaechtnis arm's context size. They must be believable (a real memory file is full of other
# projects' decisions) and must not answer, hint at, or collide with any task: nothing here names a
# filename, identifier or rule that a task asks about. `validate_task`'s cross-task leak rule does
# not cover these, so they are kept deliberately generic.
FILLER_DECISIONS = (
    "Release notes are written at tag time, not at merge time.",
    "The staging environment is rebuilt from scratch every Monday morning.",
    "Long-running migrations run behind a feature flag before they run in production.",
    "Code review is required from one person outside the authoring team.",
    "The changelog is generated from commit subjects, then edited by hand.",
    "Dependency bumps land as their own commits, never inside a feature commit.",
    "Load tests run against a copy of last quarter's traffic profile.",
    "On-call handover happens at 09:00 local time with a written summary.",
    "Documentation lives beside the code it documents, in the same pull request.",
    "Third-party outages are recorded in the incident log even when nothing broke.",
    "Feature branches older than two weeks are rebased or closed.",
    "The build is considered broken if it takes longer than twelve minutes.",
)
_PAD_PREFIX = "- (padding, so this file byte-matches the other memory arm) "


def automemory_text(task: dict, target_bytes: int | None = None) -> str:
    """The prior-decisions file's contents: the task's own recorded sentence, plus — when a target
    is given — enough unrelated prior decisions to reach EXACTLY that many bytes.

    Byte-matching is what removes the first run's largest confound. `automemory` reached day N with
    76–149 B against gedaechtnis's 2,468–3,082 B, so "more relevant context helps" sat inside the
    comparison. Padding to the same size does not make the arms equal in RELEVANCE — that
    difference is the plugin's actual mechanism — but it does mean the byte count is no longer one
    of the differences."""
    body = "# Memory index\n\n" + f"- {task['title']} — {task['memory_sentence']}\n"
    if not target_bytes:
        return body
    i = 0
    while True:
        nxt = f"- {FILLER_DECISIONS[i % len(FILLER_DECISIONS)]}\n"
        if len(body.encode("utf-8")) + len(nxt.encode("utf-8")) > target_bytes:
            break
        body += nxt
        i += 1
    # Land on the target exactly. A whole filler line rarely divides the remainder, so the last
    # line is a declared padding line, trimmed to the byte; both numbers go on the result row, so
    # nothing about this is hidden from the report.
    short = target_bytes - len(body.encode("utf-8"))
    if short >= len(_PAD_PREFIX) + 1:
        body += _PAD_PREFIX + "." * (short - len(_PAD_PREFIX) - 1) + "\n"
    elif short > 0:
        body += "." * short
    return body


def write_automemory(home: Path, repo: Path, task: dict, target_bytes: int | None = None) -> int:
    path = automemory_path(home, repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = automemory_text(task, target_bytes)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def read_automemory(home: Path, repo: Path) -> str:
    path = automemory_path(home, repo)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


# --------------------------------------------------------------- gedaechtnis ----
def sub_env(home: Path) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    e["HOME"] = str(home)
    e["GEDAECHTNIS_CONFIG"] = str(home / ".claude" / "gedaechtnis" / "config.json")
    e["GEDAECHTNIS_STATE_DIR"] = str(home / "state")
    return e


def run_init(repo: Path, vault: Path, env: dict, region: str = "Demo", lane: str = "EVAL") -> None:
    p = subprocess.run([sys.executable, str(PLUGIN / "init.py"), "--repo", str(repo), "--vault", str(vault),
                        "--region", region, "--lane", lane, "--yes"],
                       cwd=str(repo), capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"init.py failed: {p.stderr.strip() or p.stdout.strip()}")


def stage_project_settings(repo: Path) -> Path:
    """Make the plugin's OWN hooks reachable to a live step without touching the user's `~/.claude`.

    `--setting-sources project` drops user-level plugins (concilium's measured fact 2), so the hook
    set goes into the fixture repo's project settings instead, with `${CLAUDE_PLUGIN_ROOT}`
    expanded to this plugin's real path. The hook table is READ from `hooks/hooks.json`, never
    copied into this file — a generator carrying a copy of its authority document diverges."""
    raw = (PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8")
    doc = json.loads(raw.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN)))
    if "hooks" not in doc:
        raise LiveRefusal(f"{PLUGIN / 'hooks' / 'hooks.json'} has no 'hooks' key — refusing to "
                          "stage a settings file that would silently load nothing")
    path = repo / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": doc["hooks"]}, indent=2) + "\n", encoding="utf-8")
    return path


def write_canon_entry(vault: Path, region: str, task: dict, swapped: bool = False) -> None:
    side = task_side(task, swapped)
    canon = vault / region / "Canon.md"
    prior = canon.read_text(encoding="utf-8") if canon.is_file() else f"# {region} — Canon\n"
    canon.write_text(prior + f"\n## {side['canon_heading']}\n\n**Decided:** eval day 1. "
                     f"{side['memory_sentence']}\n", encoding="utf-8")


def build_vault_fixture(home: Path, repo: Path, task: dict, swapped: bool) -> tuple[Path, dict]:
    """The plugin arms' substrate: a real `init.py` vault with the day-1 decision recorded in it.
    Shared by `gedaechtnis` and `swapped` so the two differ in the RECORDED SENTENCE and nothing
    else — same init, same region, same recall call, same staged hooks."""
    env = sub_env(home)
    vault = home / "vault"
    run_init(repo, vault, env)
    write_canon_entry(vault, "Demo", task, swapped=swapped)
    return vault, env


def byte_match_targets(tasks: list[dict], base: Path) -> dict:
    """Per task, the byte size of the context the gedaechtnis arm will reach day N with.

    Computed in a PRE-PASS, before any model call, because it costs nothing: `init.py` and
    `recall.py` are local processes. The prior-decisions arm is then padded to this number, so the
    two memory arms differ in what their bytes SAY and not in how many there are. Doing it any
    later would mean either running the arms out of order or guessing the size."""
    targets = {}
    for task in tasks:
        # The scratch directory name is exactly as long as the real arm's (`gedaechtnis__`, 13
        # characters), because `recall.py`'s output quotes the vault's own FILE PATHS: a shorter
        # fixture path makes the measured context a couple of bytes smaller than the one the arm
        # will really carry. Nothing depends on this holding — every row records the ACTUAL
        # context bytes of both arms, so a drift shows up in the report instead of hiding.
        scratch = base / f"_bytematched_{task['task_id']}"
        repo = scratch / "home" / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        vault, env = build_vault_fixture(scratch / "home", repo, task, swapped=False)
        targets[task["task_id"]] = len(call_recall(vault, task["day_n_prompt"], env).encode("utf-8"))
    return targets


def call_recall(vault: Path, query: str, env: dict) -> str:
    p = subprocess.run([sys.executable, str(PLUGIN / "recall.py"), "--vault", str(vault), query],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=30)
    out = p.stdout.strip()
    if p.returncode != 0 or out.startswith("No vault entry matches") or out.startswith("recall:"):
        return ""
    return out


def measure_boot_bytes(repo: Path, env: dict) -> int | None:
    p = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "session_start.py")],
                       input=json.dumps({"session_id": "eval", "cwd": str(repo), "source": "startup"}),
                       capture_output=True, text=True, env=env, timeout=30)
    if p.returncode != 0:
        return None
    try:
        ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    except (json.JSONDecodeError, KeyError):
        return None
    m = BOOT_RE.search(ctx)
    return int(m.group(1).replace(",", "")) if m else None


# ------------------------------------------------------------------ pricing ----
DEFAULT_PRICING = PLUGIN.parent / "scripts" / "concilium.py"
PRICING_ATTRS = ("PRICE", "PRICE_ASOF", "price_usage", "resolve_price_key", "usage_from_model_usage")


def load_pricing(explicit: str | None = None):
    """Import the fleet's price table and pricing functions from `scripts/concilium.py`.

    IMPORTED, never copied: the table carries its own `PRICE_ASOF` date and refuses an unknown
    model, and a second copy of it here would drift the first time a price moved. Resolution
    order: `--pricing PATH`, `$GEDAECHTNIS_PRICING_PY`, then `<plugin>/../scripts/concilium.py`."""
    raw = explicit or os.environ.get("GEDAECHTNIS_PRICING_PY") or str(DEFAULT_PRICING)
    path = Path(raw).expanduser()
    if not path.is_file():
        raise LiveRefusal(
            f"no pricing module at {path} — a live run must price every call from the fleet's own "
            "table, not from a guess. Pass --pricing PATH (or set GEDAECHTNIS_PRICING_PY) to the "
            "concilium.py that carries PRICE/PRICE_ASOF.")
    spec = importlib.util.spec_from_file_location("_memory_eval_pricing", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    missing = [a for a in PRICING_ATTRS if not hasattr(mod, a)]
    if missing:
        raise LiveRefusal(f"{path} is not a usable pricing module: missing {', '.join(missing)}")
    return mod


def price_result(pricing, result: dict) -> dict:
    """USD for one `claude -p --output-format json` result, by the same instrument a council is
    priced with. An unknown model REFUSES: a priced-by-guess figure is a fabrication with a
    dollar sign."""
    sess = result.get("usage") or {}
    split = sess.get("cache_creation") or {}
    cw_1h = split.get("ephemeral_1h_input_tokens", 0) or 0
    cw_5m = split.get("ephemeral_5m_input_tokens", 0) or 0
    total_cw = cw_1h + cw_5m
    frac_1h = (cw_1h / total_cw) if total_cw else 0.0

    model_usage = result.get("modelUsage") or {}
    if not model_usage:
        raise LiveRefusal("the result JSON carries no `modelUsage` block, so the call cannot be "
                          "priced — refusing to report an unpriced live run as if it were free")
    usd = {"input": 0.0, "cache_write": 0.0, "cache_read": 0.0, "output": 0.0}
    toks = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    models = []
    for name, mu in model_usage.items():
        key = pricing.resolve_price_key(name, mu.get("canonicalModel"))
        if key not in pricing.PRICE:
            raise LiveRefusal(
                f"unpriced model {name!r} (resolved {key!r}) in the result — priced models as of "
                f"{pricing.PRICE_ASOF} are: {', '.join(sorted(pricing.PRICE))}")
        u = pricing.usage_from_model_usage(mu, frac_1h)
        priced = pricing.price_usage(key, u)
        for k, field in (("input", "input_tokens"), ("cache_write", "cache_creation_input_tokens"),
                         ("cache_read", "cache_read_input_tokens"), ("output", "output_tokens")):
            toks[k] += u[field]
            usd[k] += priced[k]
        models.append(key)
    return {"models": sorted(set(models)), "tokens": toks, "usd": usd,
            "usd_total": sum(usd.values()), "usd_cli": result.get("total_cost_usd"),
            "prices_as_of": pricing.PRICE_ASOF}


# ------------------------------------------------------------------ callers ----
def claude_argv0(claude_cmd) -> list[str]:
    """A `.py` command is run through this interpreter (that is how the stub is driven); anything
    else is executed as-is. The same rule `_stub_client.call_claude` uses."""
    return [sys.executable, str(claude_cmd)] if str(claude_cmd).endswith(".py") else [str(claude_cmd)]


def build_live_cmd(claude_cmd, model: str, effort: str, prompt: str, repo: Path,
                   max_turns: int = LIVE_DEFAULT_MAX_TURNS,
                   max_budget_usd: float | None = None) -> list[str]:
    """The exact argv, shaped like `concilium convene`'s: VARIADIC flags first, the prompt LAST and
    immediately after the boolean `-p` so nothing variadic can swallow it. NEVER `--resume`."""
    if not model or not effort:
        raise LiveRefusal("a live call needs BOTH --model and --effort pinned — a settings-file "
                          "default is silent routing authority, and a bare launch is never a "
                          "routing decision")
    cmd = claude_argv0(claude_cmd) + [
        "--add-dir", str(Path(repo).resolve()),
        "--allowedTools", *LIVE_TOOLS,
        "--tools", *LIVE_TOOLS,
        "--model", model,
        "--effort", effort,
        "--setting-sources", "project",
        "--permission-prompts", "none",
        "--output-format", "json",
        "--max-turns", str(max_turns),
    ]
    if max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(max_budget_usd)]
    cmd += ["-p", prompt]
    return cmd


class StubCaller:
    """The skeleton's own path, unchanged: `claude --model M [--effort E] PROMPT`, answer and
    approximate tokens parsed from the trailing `USAGE_JSON:` line."""
    live = False

    def __init__(self, claude_cmd: Path, model: str, effort: str | None, answers_path: Path):
        self.claude_cmd, self.model, self.effort = claude_cmd, model, effort
        self.answers_path = answers_path

    def __call__(self, arm: str, task: dict, day: str, prompt: str, repo: Path, env: dict):
        answer, usage = call_claude(self.claude_cmd, self.model, prompt, self.answers_path,
                                    effort=self.effort, env=env)
        return answer, usage_tokens(usage), None

    @property
    def calls(self):
        return []


class LiveCaller:
    """One real `claude -p` process per step, priced, recorded, and ceiling-bounded."""
    live = True

    def __init__(self, claude_cmd, model, effort, *, pricing, out_dir: Path,
                 max_turns: int = LIVE_DEFAULT_MAX_TURNS, max_budget_usd: float | None = None,
                 ceiling_usd: float | None = None, answers_path: Path | None = None,
                 timeout: int = 900, claude_home: str = "real"):
        if not claude_cmd:
            raise LiveRefusal("--live needs --claude PATH: the command to launch. There is no "
                              "default real binary, deliberately — a live arm is a chosen act.")
        if not model or not effort:
            raise LiveRefusal("--live needs BOTH --model and --effort: every launch in this fleet "
                              "pins them explicitly, scripted or manual.")
        key = pricing.resolve_price_key(model)
        if key not in pricing.PRICE:
            raise LiveRefusal(
                f"unknown model {model!r}: it is not in the price table as of {pricing.PRICE_ASOF} "
                f"({', '.join(sorted(pricing.PRICE))}). A live run that cannot be priced does not "
                "start — pass the full model id.")
        # Every call runs with cwd=<the arm's fixture repo>, so a RELATIVE --claude would resolve
        # against a directory that does not contain it. Resolve it once, here, and record the
        # resolved form in every argv; a bare command name on PATH is left alone.
        given = Path(str(claude_cmd)).expanduser()
        self.claude_cmd = str(given.resolve()) if given.exists() else str(claude_cmd)
        self.model, self.effort = model, effort
        self.pricing, self.out_dir = pricing, Path(out_dir)
        self.max_turns, self.max_budget_usd = max_turns, max_budget_usd
        self.ceiling_usd, self.answers_path = ceiling_usd, answers_path
        self.timeout, self.claude_home = timeout, claude_home
        self.calls: list[dict] = []
        self.spent = 0.0
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # -- environment ------------------------------------------------------
    def child_env(self, env: dict) -> dict:
        """The fixture's `GEDAECHTNIS_*` environment, plus the HOME policy (docstring, limitation
        2). `GEDAECHTNIS_STUB_ANSWERS` is passed through the same way `_stub_client` does — a real
        `claude` ignores an environment variable it has never heard of."""
        e = dict(env)
        if self.claude_home == "real":
            fixture_home = Path(env["HOME"])
            real = os.environ.get("HOME")
            if real:
                e["HOME"] = real
            # Keep the plugin's own state inside the fixture even when HOME is the real one.
            e.setdefault("GEDAECHTNIS_USER_MEMORY", str(fixture_home / ".claude" / "CLAUDE.md"))
            e.setdefault("GEDAECHTNIS_WORKTREES", str(fixture_home / ".claude" / "worktrees"))
        if self.answers_path:
            e["GEDAECHTNIS_STUB_ANSWERS"] = str(self.answers_path)
        return e

    # -- one call ---------------------------------------------------------
    def __call__(self, arm: str, task: dict, day: str, prompt: str, repo: Path, env: dict):
        if self.ceiling_usd is not None and self.spent >= self.ceiling_usd:
            raise CeilingReached(
                f"priced spend ${self.spent:.4f} has reached the --ceiling-usd "
                f"${self.ceiling_usd:.4f}; refusing to start {arm}/{task['task_id']}/{day}")
        cmd = build_live_cmd(self.claude_cmd, self.model, self.effort, prompt, repo,
                             max_turns=self.max_turns, max_budget_usd=self.max_budget_usd)
        started = datetime.datetime.now().isoformat(timespec="seconds")
        proc = subprocess.run(cmd, cwd=str(repo), env=self.child_env(env),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=self.timeout)
        ended = datetime.datetime.now().isoformat(timespec="seconds")

        usage_path = self.out_dir / f"{arm}-{task['task_id']}-{day}.usage.json"
        if usage_path.exists():
            raise LiveRefusal(f"{usage_path} already exists — two calls would share one record "
                              "and the first would be lost. The day label must be unique per call.")
        try:
            result = json.loads(proc.stdout)
        except ValueError:
            result = {"PARSE_FAILED": True, "stdout": proc.stdout[:20000], "stderr": proc.stderr[:4000]}
        record = {
            "schema": USAGE_SCHEMA, "arm": arm, "task_id": task["task_id"], "day": day,
            "model": self.model, "effort": self.effort, "max_turns": self.max_turns,
            "max_budget_usd": self.max_budget_usd, "ceiling_usd": self.ceiling_usd,
            "cwd": str(repo), "home_policy": self.claude_home, "stdin": "DEVNULL",
            "argv": cmd, "started": started, "ended": ended, "returncode": proc.returncode,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "result": result,
        }
        if result.get("PARSE_FAILED"):
            usage_path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
            raise LiveRefusal(f"{self.claude_cmd} did not emit parseable --output-format json for "
                              f"{arm}/{task['task_id']}/{day} (exit {proc.returncode}); the raw "
                              f"stdout/stderr are in {usage_path}")
        cost = price_result(self.pricing, result)
        record["cost"] = cost
        record["spent_before"] = round(self.spent, 6)
        record["spent_after"] = round(self.spent + cost["usd_total"], 6)
        usage_path.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
        self.spent += cost["usd_total"]
        self.calls.append({"arm": arm, "task_id": task["task_id"], "day": day,
                           "usd": round(cost["usd_total"], 6), "tokens": cost["tokens"],
                           "usd_cli": cost["usd_cli"], "usage_file": str(usage_path),
                           "returncode": proc.returncode})
        print(f"cost  {arm:<11} {task['task_id']:<18} {day:<5} "
              f"${cost['usd_total']:.4f}  in={cost['tokens']['input']} "
              f"out={cost['tokens']['output']} cw={cost['tokens']['cache_write']} "
              f"cr={cost['tokens']['cache_read']}  running=${self.spent:.4f}"
              + (f"/{self.ceiling_usd:.2f}" if self.ceiling_usd is not None else ""), flush=True)
        if proc.returncode != 0:
            raise LiveRefusal(f"claude exited {proc.returncode} on {arm}/{task['task_id']}/{day} — "
                              f"record kept at {usage_path}; stderr: {proc.stderr.strip()[:800]}")
        sess = result.get("usage") or {}
        tokens = int(sess.get("input_tokens") or 0) + int(sess.get("output_tokens") or 0)
        return (result.get("result") or ""), tokens, usage_path


# --------------------------------------------------------------------- run ----
def run_task_arm(arm: str, task: dict, base: Path, caller, samples: int = 1,
                 byte_match: dict | None = None) -> list[dict]:
    """One (arm, task) cell: ONE day-1 call, then `samples` day-N calls, one row each.

    **Why day 1 is not repeated.** Nothing reads its answer — the memory substrate is written by
    this harness, deliberately (see the module docstring), so a second day-1 call would cost the
    same as the first and measure nothing. The quantity being sampled is the day-N answer, and that
    is what repeats. Each day-N call is a fresh `claude -p` process with no shared state, so the
    samples are independent in the only sense available here."""
    swapped = arm in SWAPPED_ARMS
    home = base / f"{arm}__{task['task_id']}" / "home"
    repo = home / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    env = sub_env(home)
    live = getattr(caller, "live", False)
    day_n_label = f"day{task.get('day_n', 'N')}"
    usage_files = []

    _day1_answer, day1_tokens, u1 = caller(arm, task, "day1", task_side(task, swapped)["day1_prompt"],
                                           repo, env)
    if u1:
        usage_files.append(str(u1))

    boot_bytes, target, automemory_bytes = None, None, None
    if arm == "automemory":
        target = (byte_match or {}).get(task["task_id"])
        automemory_bytes = write_automemory(home, repo, task, target)
        context = read_automemory(home, repo)
    elif arm in VAULT_ARMS:
        vault, env = build_vault_fixture(home, repo, task, swapped)
        boot_bytes = measure_boot_bytes(repo, env)
        if live:
            stage_project_settings(repo)
        context = call_recall(vault, task["day_n_prompt"], env)
    else:
        context = ""

    day_n_prompt = (context + "\n\n" if context else "") + task["day_n_prompt"]
    rows = []
    for sample in range(1, samples + 1):
        label = day_n_label if samples == 1 else f"{day_n_label}-s{sample}"
        answer, tokens, u2 = caller(arm, task, label, day_n_prompt, repo, env)
        if u2:
            usage_files.append(str(u2))
        suffix = "" if samples == 1 else f".s{sample}"
        out_file = repo / (task["checker_file"] + suffix)
        out_file.write_text(answer, encoding="utf-8")
        verdict = check_answer(task, out_file.read_text(encoding="utf-8"), swapped=swapped)
        row = {
            "arm": arm, "task_id": task["task_id"], "sample": sample,
            "positive_control": bool(task.get("positive_control", False)),
            "success": verdict["success"], "tokens": tokens, "day1_tokens": day1_tokens,
            "boot_bytes": boot_bytes, "context_bytes": len(context.encode("utf-8")),
            "byte_match_target": target, "memory_bytes": automemory_bytes,
            "verdict": verdict,
        }
        if live:
            # Live-only keys: the day-1 call's price is attributed to the FIRST sample only, so
            # summing `usd` over rows equals the run's priced total instead of triple-counting it.
            own = [c for c in caller.calls if c["arm"] == arm and c["task_id"] == task["task_id"]
                   and (c["day"] == label or (sample == 1 and c["day"] == "day1"))]
            row["usd"] = round(sum(c["usd"] for c in own), 6)
            row["usage_files"] = [c["usage_file"] for c in own]
        rows.append(row)
    return rows


# ------------------------------------------------------------------ report ----
def render_markdown(rows: list[dict]) -> str:
    """Pure function of the rows — see recall_bench/run.py's identical discipline. A caller must
    never hand-type this table; regenerate it from the JSONL instead.

    Two tables, because samples give a cell a NUMERATOR: a task x arm matrix of passes-out-of-
    samples (which is what a claim rule is read off), then the per-call detail. A cell's spread
    across its samples IS the noise floor — a claim that two arms differ by fewer tasks than the
    cells wobble on their own is not readable, so the spread is printed beside the totals rather
    than left for a reader to compute."""
    arms = [a for a in ARMS if any(r["arm"] == a for r in rows)]
    arms += sorted({r["arm"] for r in rows} - set(arms))
    tasks, seen = [], set()
    for r in rows:
        if r["task_id"] not in seen:
            seen.add(r["task_id"])
            tasks.append(r["task_id"])

    def cell(arm, task):
        rs = [r for r in rows if r["arm"] == arm and r["task_id"] == task]
        return (sum(1 for r in rs if r["success"]), len(rs)) if rs else (0, 0)

    lines = ["| task | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for t in tasks:
        cells = []
        for a in arms:
            k, n = cell(a, t)
            cells.append(f"{k}/{n}" if n else "-")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")

    lines.append("")
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        per_task = [cell(arm, t) for t in tasks if cell(arm, t)[1]]
        split = sum(1 for k, n in per_task if 0 < k < n)
        passed = sum(1 for r in rs if r["success"])
        lines.append(
            f"**{arm}:** {sum(1 for k, n in per_task if k == n)}/{len(per_task)} tasks passed on "
            f"EVERY sample, {passed}/{len(rs)} cell-samples passed, {split} task(s) split across "
            f"their samples, {sum(r['tokens'] for r in rs)} day-N tokens")

    lines += ["", "| arm | task | sample | success | tokens | boot_bytes | context_bytes | "
              "byte_match_target |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['arm']} | {r['task_id']} | {r.get('sample', 1)} | {r['success']} | "
                     f"{r['tokens']} | {r['boot_bytes'] if r['boot_bytes'] is not None else '-'} | "
                     f"{r['context_bytes']} | "
                     f"{r['byte_match_target'] if r.get('byte_match_target') is not None else '-'} |")
    return "\n".join(lines) + "\n"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def render_from_jsonl(jsonl_path: Path) -> str:
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return render_markdown(rows)


def write_live_summary(path: Path, caller: LiveCaller, planned: int, stopped: str | None,
                       arms: tuple = ARMS, samples: int = 1, byte_match: dict | None = None) -> None:
    summary = {
        "schema": "gedaechtnis-memory-eval-live-summary/1",
        "arms": list(arms), "samples": samples, "byte_match_targets": byte_match or {},
        "model": caller.model, "effort": caller.effort,
        "claude": str(caller.claude_cmd), "home_policy": caller.claude_home,
        "max_turns": caller.max_turns, "max_budget_usd": caller.max_budget_usd,
        "ceiling_usd": caller.ceiling_usd, "prices_as_of": caller.pricing.PRICE_ASOF,
        "calls_planned": planned, "calls_made": len(caller.calls),
        "usd_total": round(caller.spent, 6),
        "usd_cli_total": round(sum(c["usd_cli"] or 0.0 for c in caller.calls), 6),
        "stopped_early": stopped, "calls": caller.calls,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")


# -------------------------------------------------------------------- main ----
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WP8 memory eval: three arms, cross-session tasks.")
    ap.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    ap.add_argument("--out", default=None, help="output directory (default: ./results next to this script)")
    ap.add_argument("--base", default=None, help="where the throwaway fixtures go (default: a fresh mkdtemp)")
    ap.add_argument("--claude", default=None,
                    help="the claude command to drive each step (default: the stub, but ONLY with --dry-run)")
    ap.add_argument("--model", default=None, help="required — pinned on every call, no exceptions")
    ap.add_argument("--effort", default=None, help="required with --live — pinned on every call")
    ap.add_argument("--dry-run", action="store_true",
                    help="the default mode: forces --claude to the stub; no model call, no network")
    ap.add_argument("--live", action="store_true",
                    help="run every step as a real `claude -p` process (spends money). Needs "
                         "--claude, --model and --effort; see the module docstring for what a live "
                         "run can and cannot isolate.")
    ap.add_argument("--max-turns", type=int, default=LIVE_DEFAULT_MAX_TURNS, help="--live only")
    ap.add_argument("--max-budget-usd", type=float, default=None,
                    help="--live only: the CLI's own per-call budget cap, passed through")
    ap.add_argument("--ceiling-usd", type=float, default=None,
                    help="--live only: TOTAL priced spend across the whole run. Once the sum of "
                         "priced calls reaches it, the next call is refused and the run reports "
                         "what it completed.")
    ap.add_argument("--pricing", default=None,
                    help="--live only: path to the module carrying PRICE/PRICE_ASOF (default: "
                         "$GEDAECHTNIS_PRICING_PY, else concilium.py in the sibling scripts/ dir)")
    ap.add_argument("--claude-home", choices=("real", "fixture"), default="real",
                    help="--live only: HOME for the claude process. `real` (default) keeps the "
                         "user's credentials reachable and isolates via --setting-sources project; "
                         "`fixture` is fully isolated and only works if the CLI can authenticate "
                         "without the user's HOME.")
    ap.add_argument("--timeout", type=int, default=900, help="--live only: seconds per call")
    ap.add_argument("--samples", type=int, default=1,
                    help="day-N calls per (arm, task). The day-1 call is NOT repeated: nothing "
                         "reads its answer, so a second one would cost the same and measure "
                         "nothing. >1 is what gives a cell a noise floor.")
    ap.add_argument("--arms", default=None,
                    help="comma-separated subset of " + ",".join(ARMS) + " (default: all four). "
                         "Running `none` alone first is the pre-flight: a task the no-memory arm "
                         "answers is guessable and must be dropped before the other arms pay for "
                         "it.")
    ap.add_argument("--byte-match", action="store_true",
                    help="pad the prior-decisions arm's memory file to the exact byte size of the "
                         "gedaechtnis arm's day-N context for that task, measured in a no-cost "
                         "pre-pass. Removes the first run's largest confound.")
    ap.add_argument("--render-only", default=None, metavar="JSONL",
                    help="skip every arm/task entirely: regenerate results.md from an existing JSONL "
                         "and exit — the same 'derived, never typed' exercise as recall_bench/run.py's flag")
    a = ap.parse_args(argv)

    if a.render_only:
        out = Path(a.out).expanduser().resolve() if a.out else Path(a.render_only).expanduser().resolve().parent
        md = render_from_jsonl(Path(a.render_only).expanduser().resolve())
        (out / "results.md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    if a.dry_run and a.live:
        ap.error("--dry-run and --live are mutually exclusive: one runs the stub with no network, "
                 "the other spends money.")

    if not a.dry_run and not a.live and (not a.claude or not a.model):
        ap.error("--claude and --model are required unless --dry-run (a live run also needs "
                 "--live and --effort — pass --dry-run to run the stub end-to-end).")

    if a.samples < 1:
        ap.error("--samples must be at least 1")
    arms = ARMS
    if a.arms:
        arms = tuple(s.strip() for s in a.arms.split(",") if s.strip())
        unknown = [s for s in arms if s not in ARMS]
        if unknown:
            ap.error(f"unknown arm(s) {unknown}: known arms are {', '.join(ARMS)}")

    tasks = load_tasks(Path(a.tasks_dir).expanduser().resolve())
    out = Path(a.out).expanduser().resolve() if a.out else Path(__file__).resolve().parent / "results"
    base = Path(a.base).expanduser().resolve() if a.base else Path(tempfile.mkdtemp(prefix="gedaechtnis-eval-"))
    base.mkdir(parents=True, exist_ok=True)
    answers_path = base / "answers.json"
    answers_path.write_text(json.dumps(build_answers(tasks)), encoding="utf-8")

    if a.live:
        try:
            pricing = load_pricing(a.pricing)
            caller = LiveCaller(a.claude, a.model, a.effort, pricing=pricing, out_dir=out,
                                max_turns=a.max_turns, max_budget_usd=a.max_budget_usd,
                                ceiling_usd=a.ceiling_usd, answers_path=answers_path,
                                timeout=a.timeout, claude_home=a.claude_home)
        except LiveRefusal as e:
            sys.stderr.write(f"memory_eval refuses: {e}\n")
            return 2
        print(f"# LIVE: {a.claude} --model {a.model} --effort {a.effort} "
              f"(max-turns {a.max_turns}, ceiling "
              f"{('$%.2f' % a.ceiling_usd) if a.ceiling_usd is not None else 'NONE'}, "
              f"prices as of {pricing.PRICE_ASOF})", flush=True)
    elif a.dry_run:
        caller = StubCaller(STUB, a.model or "dry-run-stub", a.effort, answers_path)
    else:
        caller = StubCaller(Path(a.claude), a.model, a.effort, answers_path)

    targets = {}
    if a.byte_match:
        targets = byte_match_targets(tasks, base)
        print("# byte-match targets (gedaechtnis day-N context bytes, measured with no model call):")
        for tid, n in targets.items():
            print(f"    {tid:<28} {n} B")

    planned = len(arms) * len(tasks) * a.samples
    rows, stopped = [], None
    try:
        for arm in arms:
            for task in tasks:
                rows.extend(run_task_arm(arm, task, base, caller, samples=a.samples,
                                         byte_match=targets))
    except CeilingReached as e:
        stopped = str(e)
    except LiveRefusal as e:
        sys.stderr.write(f"memory_eval refuses: {e}\n")
        if a.live:
            write_live_summary(out / "live-summary.json", caller, planned, f"refused: {e}",
                               arms=arms, samples=a.samples, byte_match=targets)
        return 2

    write_jsonl(out / "results.jsonl", rows)
    md = render_markdown(rows)
    (out / "results.md").write_text(md, encoding="utf-8")
    print(md)
    if a.live:
        write_live_summary(out / "live-summary.json", caller, planned, stopped,
                           arms=arms, samples=a.samples, byte_match=targets)
        if stopped:
            print("*** CEILING REACHED — the run stopped before it was complete.")
            print(f"    {stopped}")
        print(f"live: {len(caller.calls)} call(s) made, ${caller.spent:.4f} priced "
              f"(prices as of {caller.pricing.PRICE_ASOF}); "
              f"{len(rows)}/{planned} (arm x task) cells completed")
        for c in caller.calls:
            print(f"      {c['arm']}/{c['task_id']}/{c['day']} ${c['usd']:.4f}")
        print(f"live summary   : {out / 'live-summary.json'}")
    print(f"(throwaway homes kept for inspection under {base})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
