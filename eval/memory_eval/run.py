#!/usr/bin/env python3
"""memory_eval/run.py — WP8 skeleton: three arms, cross-session tasks, no model call by default.

    python3 run.py --dry-run
    python3 run.py --claude /path/to/real/claude --model sonnet --effort low   # NOT wired yet — see below

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

**No model call, no network, ever, in this skeleton.** `--claude`/`--model` exist so a future
session can point this harness at a real `claude -p` deliberately (DESIGN.md §8, R7: WP8's live
arms are DEFERRED); `--dry-run` is the only mode this skeleton is meant to be run in today, and it
forces `--claude` to the stub regardless of what else is passed.

**The positive control.** Task 001 cannot be answered from the day-N prompt or the repo alone — see
`tasks/001-db-choice.json`. If the `none` arm ever succeeds on it, either a fact leaked into that
arm's prompt/repo by accident, or the checker is too loose; `tests/test_eval_skeletons.py` asserts
this task fails under `none` and treats a pass as a test failure, per the harness-vacuity rule in
`Global/Patterns-verification.md` ("a check whose trigger condition never occurs... is VACUOUS").
"""
from __future__ import annotations
import argparse, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))
from _stub_client import call_claude, usage_tokens  # noqa: E402

STUB = EVAL / "stub_claude.py"
DEFAULT_TASKS_DIR = Path(__file__).resolve().parent / "tasks"
ARMS = ("none", "automemory", "gedaechtnis")
BOOT_RE = re.compile(r"- boot: ([\d,]+) B across (\d+) files")


# ------------------------------------------------------------------- tasks ----
def load_tasks(tasks_dir: Path) -> list[dict]:
    tasks = []
    for p in sorted(tasks_dir.glob("*.json")):
        t = json.loads(p.read_text(encoding="utf-8"))
        for key in ("task_id", "day1_prompt", "memory_sentence", "canon_heading", "fact",
                    "day_n_prompt", "checker_file", "checker_must_contain"):
            if key not in t:
                raise ValueError(f"{p}: task is missing {key!r}")
        tasks.append(t)
    if not tasks:
        raise ValueError(f"{tasks_dir}: no task JSON files found")
    if not any(t.get("positive_control") for t in tasks):
        raise ValueError(f"{tasks_dir}: no task is flagged \"positive_control\": true — the "
                         "harness-vacuity check has nothing to assert (see the module docstring)")
    # Sanity: one task's fact must not accidentally leak into another task's day-N prompt, or a
    # cross-task collision could make the "none" arm pass by ACCIDENT rather than by memory.
    for a in tasks:
        for b in tasks:
            if a is not b and a["fact"] in b["day_n_prompt"]:
                raise ValueError(f"{a['task_id']}'s fact {a['fact']!r} appears in "
                                 f"{b['task_id']}'s day_n_prompt — this would make a 'none'-arm "
                                 "pass ambiguous. Rewrite one of the two.")
    return tasks


def build_answers(tasks: list[dict]) -> dict:
    """Substring -> answer, in the order `stub_claude.py` checks them: every task's FACT key
    first (so a prompt that genuinely carries the recalled fact matches it), then every task's
    plain day-N prompt as a fallback (so a prompt with NO recalled context — the `none` arm — gets
    an honest "I don't know" instead of accidentally matching something else)."""
    answers = {}
    for t in tasks:
        answers[t["fact"]] = f"Recorded decision: {t['fact']}."
    for t in tasks:
        answers[t["day_n_prompt"]] = "I have no record of that decision from this session alone."
    return answers


# --------------------------------------------------------------- automemory ----
def automemory_path(home: Path, repo: Path) -> Path:
    """A stand-in for Claude Code's own `~/.claude/projects/<mangled-cwd>/memory/MEMORY.md`. This
    mimics the SHAPE of that convention (the project-mangled directory under `.claude/projects/`)
    for a fair token/success comparison; it is not a byte-exact replica and nothing here asserts
    that a real Claude Code session would load it — the automemory arm is deliberately the
    cheapest, most literal reading of "give it a plain memory file and see if that alone helps"."""
    mangled = str(repo).replace(os.sep, "-")
    return home / ".claude" / "projects" / mangled / "memory" / "MEMORY.md"


def write_automemory(home: Path, repo: Path, task: dict) -> None:
    path = automemory_path(home, repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = path.read_text(encoding="utf-8") if path.is_file() else "# Memory index\n\n"
    path.write_text(prior + f"- {task['title']} — {task['memory_sentence']}\n", encoding="utf-8")


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


def write_canon_entry(vault: Path, region: str, task: dict) -> None:
    canon = vault / region / "Canon.md"
    prior = canon.read_text(encoding="utf-8") if canon.is_file() else f"# {region} — Canon\n"
    canon.write_text(prior + f"\n## {task['canon_heading']}\n\n**Decided:** eval day 1. "
                     f"{task['memory_sentence']}\n", encoding="utf-8")


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


# --------------------------------------------------------------------- run ----
def run_task_arm(arm: str, task: dict, base: Path, claude_cmd: Path, model: str,
                 answers_path: Path, effort: str | None) -> dict:
    home = base / f"{arm}__{task['task_id']}" / "home"
    repo = home / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    env = sub_env(home)

    day1_answer, day1_usage = call_claude(claude_cmd, model, task["day1_prompt"], answers_path, effort=effort, env=env)
    tokens = usage_tokens(day1_usage)

    vault, boot_bytes = None, None
    if arm == "automemory":
        write_automemory(home, repo, task)
    elif arm == "gedaechtnis":
        vault = home / "vault"
        run_init(repo, vault, env)
        write_canon_entry(vault, "Demo", task)
        boot_bytes = measure_boot_bytes(repo, env)

    if arm == "none":
        context = ""
    elif arm == "automemory":
        context = read_automemory(home, repo)
    else:
        context = call_recall(vault, task["day_n_prompt"], env)

    day_n_prompt = (context + "\n\n" if context else "") + task["day_n_prompt"]
    day_n_answer, day_n_usage = call_claude(claude_cmd, model, day_n_prompt, answers_path, effort=effort, env=env)
    tokens += usage_tokens(day_n_usage)

    out_file = repo / task["checker_file"]
    out_file.write_text(day_n_answer, encoding="utf-8")
    success = task["checker_must_contain"] in out_file.read_text(encoding="utf-8")

    return {
        "arm": arm, "task_id": task["task_id"], "positive_control": bool(task.get("positive_control", False)),
        "success": success, "tokens": tokens, "boot_bytes": boot_bytes,
        "context_bytes": len(context.encode("utf-8")),
    }


# ------------------------------------------------------------------ report ----
def render_markdown(rows: list[dict]) -> str:
    """Pure function of the rows — see recall_bench/run.py's identical discipline. A caller must
    never hand-type this table; regenerate it from the JSONL instead."""
    lines = ["| arm | task | positive_control | success | tokens | boot_bytes | context_bytes |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['arm']} | {r['task_id']} | {r['positive_control']} | {r['success']} | "
                     f"{r['tokens']} | {r['boot_bytes'] if r['boot_bytes'] is not None else '-'} | "
                     f"{r['context_bytes']} |")
    by_arm = {}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)
    lines.append("")
    for arm in ARMS:
        rs = by_arm.get(arm, [])
        if not rs:
            continue
        rate = sum(1 for r in rs if r["success"]) / len(rs)
        toks = sum(r["tokens"] for r in rs)
        lines.append(f"**{arm}:** {sum(1 for r in rs if r['success'])}/{len(rs)} tasks succeeded "
                     f"({rate:.2f}), {toks} tokens total")
    return "\n".join(lines) + "\n"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def render_from_jsonl(jsonl_path: Path) -> str:
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return render_markdown(rows)


# -------------------------------------------------------------------- main ----
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WP8 memory-eval skeleton: three arms, cross-session tasks.")
    ap.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    ap.add_argument("--out", default=None, help="output directory (default: ./results next to this script)")
    ap.add_argument("--claude", default=None, help="the claude command to drive each step (default: the stub, but ONLY with --dry-run)")
    ap.add_argument("--model", default=None, help="required — pinned on every call, no exceptions")
    ap.add_argument("--effort", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="the only mode this skeleton is meant to run in today: forces --claude to the stub")
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

    if a.dry_run:
        claude_cmd, model = STUB, a.model or "dry-run-stub"
    else:
        if not a.claude or not a.model:
            ap.error("--claude and --model are required unless --dry-run (WP8's live arms are "
                     "deferred per DESIGN.md §8 R7 — pass --dry-run to run the stub end-to-end).")
        claude_cmd, model = Path(a.claude), a.model

    tasks = load_tasks(Path(a.tasks_dir).expanduser().resolve())
    out = Path(a.out).expanduser().resolve() if a.out else Path(__file__).resolve().parent / "results"

    base = Path(tempfile.mkdtemp(prefix="gedaechtnis-eval-"))
    answers_path = base / "answers.json"
    answers_path.write_text(json.dumps(build_answers(tasks)), encoding="utf-8")

    rows = []
    for arm in ARMS:
        for task in tasks:
            rows.append(run_task_arm(arm, task, base, claude_cmd, model, answers_path, a.effort))

    write_jsonl(out / "results.jsonl", rows)
    md = render_markdown(rows)
    (out / "results.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"(throwaway homes kept for inspection under {base})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
