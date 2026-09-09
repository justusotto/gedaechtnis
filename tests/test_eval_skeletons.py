"""Tests for the WP6/WP8 eval SKELETONS — proven against a clean HOME, no network, no model call.

Every subprocess here runs `stub_claude.py`, never a real model; `memory_eval/run.py` builds its
own throwaway HOME/repo/vault per (arm, task) under a system temp directory regardless of what
environment invokes it, so these tests do not need the `env_for(home)` redirection `test_init.py`
uses for the plugin's own hooks — but every call still gets an explicit `HOME` pointed at
`tmp_path` anyway, out of the same caution, so a bug in that internal isolation would show up here
rather than touching anything real.

The one test that matters most is `test_positive_control_fails_under_none_arm`: if it ever passes
on a run where the checked task actually SUCCEEDS under the `none` arm, the assertion inside it
fails the test on purpose — per Global/Patterns-verification.md, a check whose trigger condition
can never fire is worthless, and a positive control that cannot fail is not a control.

**Every check runs each eval script as a SUBPROCESS, never a direct in-process `import`.**
`recall.py`, `init.py`, `session_start.py` and the two `run.py` scripts all do a plain,
unqualified `import config` after inserting `hooks/` onto `sys.path` — the pattern the whole
plugin uses so its hooks stay stdlib-only and mutually independent. That pattern shares ONE
`sys.modules["config"]` process-wide: a direct import of two of these scripts inside the same
pytest process silently reuses whichever one first resolved `config`'s environment-derived
constants, which is exactly the kind of cross-test pollution `Global/Errata.md` warns about ("a
test suite that writes the application's real sidecar makes its own verdict depend on the
machine's state") — here it would be reads, not writes, but the failure mode is the same: a test
elsewhere in the suite (e.g. `test_gate.py`, which loads `hooks/common.py` directly for its own
reasons) can start seeing a stale, wrong vault path with no error. Subprocess isolation, which is
this suite's existing convention everywhere else (`test_init.py`, `test_recall.py`), sidesteps it
entirely: each subprocess gets its own fresh interpreter and its own fresh `sys.modules`.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
EVAL = PLUGIN / "eval"
STUB = EVAL / "stub_claude.py"
RECALL_BENCH_RUN = EVAL / "recall_bench" / "run.py"
MEMORY_EVAL_RUN = EVAL / "memory_eval" / "run.py"
QUESTIONS_EXAMPLE = EVAL / "recall_bench" / "questions.example.json"


def _env(home: Path) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    e["HOME"] = str(home)
    e["GEDAECHTNIS_CONFIG"] = str(home / ".claude" / "gedaechtnis" / "config.json")
    e["GEDAECHTNIS_STATE_DIR"] = str(home / "state")
    return e


# ------------------------------------------------------------------- stub ----
def test_stub_refuses_without_model(tmp_path):
    p = subprocess.run([sys.executable, str(STUB), "hello there"], capture_output=True, text=True,
                       env=_env(tmp_path), stdin=subprocess.DEVNULL, timeout=30)
    assert p.returncode == 2
    assert "--model is required" in p.stderr
    assert p.stdout == ""


def test_stub_refuses_with_no_prompt(tmp_path):
    p = subprocess.run([sys.executable, str(STUB), "--model", "x"], capture_output=True, text=True,
                       env=_env(tmp_path), stdin=subprocess.DEVNULL, timeout=30)
    assert p.returncode == 2
    assert "no prompt given" in p.stderr


def test_stub_answers_by_substring_and_reports_usage(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"PostgreSQL": "yes, PostgreSQL"}), encoding="utf-8")
    p = subprocess.run([sys.executable, str(STUB), "--model", "x", "--answers", str(answers),
                        "what database? PostgreSQL, remember"],
                       capture_output=True, text=True, env=_env(tmp_path), stdin=subprocess.DEVNULL, timeout=30)
    assert p.returncode == 0
    lines = p.stdout.splitlines()
    assert lines[0] == "yes, PostgreSQL"
    usage = json.loads(lines[-1][len("USAGE_JSON: "):])
    assert usage["model"] == "x" and usage["input_tokens"] > 0 and usage["output_tokens"] > 0


def test_stub_default_answer_when_nothing_matches(tmp_path):
    p = subprocess.run([sys.executable, str(STUB), "--model", "x", "an unconfigured prompt"],
                       capture_output=True, text=True, env=_env(tmp_path), stdin=subprocess.DEVNULL, timeout=30)
    assert p.returncode == 0
    assert p.stdout.splitlines()[0] == "(stub: no configured answer for this prompt)"


# ------------------------------------------------------------- memory_eval ----
@pytest.fixture(scope="module")
def memory_eval_result(tmp_path_factory):
    """Run `memory_eval/run.py --dry-run` exactly once and share the result across the tests
    below — it drives real `init.py`/`recall.py`/`session_start.py` subprocesses per (arm, task)
    and is not free; nothing here mutates the output, so sharing is safe."""
    out = tmp_path_factory.mktemp("memory_eval_out")
    home = tmp_path_factory.mktemp("memory_eval_home")   # unused by run.py directly, but keeps a HOME set
    p = subprocess.run([sys.executable, str(MEMORY_EVAL_RUN), "--dry-run", "--out", str(out)],
                       capture_output=True, text=True, env=_env(home), stdin=subprocess.DEVNULL, timeout=300)
    return p, out


def test_memory_eval_dry_run_executes_all_three_arms_and_writes_jsonl(memory_eval_result):
    p, out = memory_eval_result
    assert p.returncode == 0, p.stderr
    jsonl = out / "results.jsonl"
    assert jsonl.is_file()
    rows = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    arms_seen = {r["arm"] for r in rows}
    assert arms_seen == {"none", "automemory", "gedaechtnis"}
    task_ids = {r["task_id"] for r in rows}
    assert len(task_ids) == 3                      # the three shipped task specs
    assert len(rows) == 3 * 3                       # every arm x every task
    assert (out / "results.md").is_file()
    assert "arm" in (out / "results.md").read_text(encoding="utf-8")


def test_positive_control_fails_under_none_arm(memory_eval_result):
    """The load-bearing test. Per the brief: if the positive-control task ever SUCCEEDS in the
    `none` arm, this assertion must fail — a passing harness here would mean the checker or the
    prompt design leaked the day-1 fact somewhere the `none` arm could see it with no memory
    mechanism at all, which makes every other number this harness produces meaningless."""
    p, out = memory_eval_result
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    pc_rows = [r for r in rows if r["positive_control"]]
    assert pc_rows, "no task is flagged positive_control — the fixture itself is broken"
    none_pc = [r for r in pc_rows if r["arm"] == "none"]
    assert len(none_pc) == 1
    assert none_pc[0]["success"] is False, (
        "the positive-control task SUCCEEDED under the 'none' arm — the harness is vacuous: "
        "either the day-1 fact leaked into the day-N prompt/repo, or the checker is too loose")


def test_memory_eval_automemory_and_gedaechtnis_solve_every_task(memory_eval_result):
    """Not load-bearing the way the positive control is, but a sanity check that the harness's
    memory-carrying arms are not ALSO vacuous in the other direction (e.g. a context leak that
    makes every arm pass regardless of whether memory actually reached the prompt)."""
    p, out = memory_eval_result
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for arm in ("automemory", "gedaechtnis"):
        arm_rows = [r for r in rows if r["arm"] == arm]
        assert all(r["success"] for r in arm_rows), f"{arm}: {arm_rows}"


def test_memory_eval_gedaechtnis_arm_reports_boot_bytes(memory_eval_result):
    p, out = memory_eval_result
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    ged_rows = [r for r in rows if r["arm"] == "gedaechtnis"]
    assert all(isinstance(r["boot_bytes"], int) and r["boot_bytes"] > 0 for r in ged_rows)
    assert all(r["boot_bytes"] is None for r in rows if r["arm"] in ("none", "automemory"))


def test_memory_eval_markdown_is_a_true_regeneration_of_the_jsonl(memory_eval_result, tmp_path):
    """`--render-only` re-derives results.md from ONLY the JSONL (no arms, no tasks, no claude
    call at all) — run it into a fresh directory and diff byte-for-byte against the original."""
    p, out = memory_eval_result
    on_disk_md = (out / "results.md").read_text(encoding="utf-8")
    regen_dir = tmp_path / "regen"
    regen_dir.mkdir()
    rp = subprocess.run([sys.executable, str(MEMORY_EVAL_RUN), "--render-only", str(out / "results.jsonl"),
                        "--out", str(regen_dir)],
                       capture_output=True, text=True, env=_env(tmp_path), stdin=subprocess.DEVNULL, timeout=30)
    assert rp.returncode == 0, rp.stderr
    assert (regen_dir / "results.md").read_text(encoding="utf-8") == on_disk_md


def test_memory_eval_requires_claude_and_model_without_dry_run(tmp_path):
    p = subprocess.run([sys.executable, str(MEMORY_EVAL_RUN), "--out", str(tmp_path)],
                       capture_output=True, text=True, env=_env(tmp_path), stdin=subprocess.DEVNULL, timeout=30)
    assert p.returncode != 0
    assert "--dry-run" in p.stderr


# ------------------------------------------------------------- recall_bench ----
@pytest.fixture
def demo_vault(tmp_path):
    """The three-file throwaway vault the example question set is written against — see
    `eval/recall_bench/questions.example.json`. Byte-identical headings/content to what that file
    expects, so `--arm grep` scores every question correctly and this fixture doubles as proof
    the shipped example is well-formed."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "Errata.md").write_text(
        "# Demo — Errata\n\n"
        "## Compose commit messages in a file, not inline\n\n"
        "Backticks and `$(...)` inside a double-quoted -m string get shell-substituted before git "
        "ever sees them, silently dropping words from the message. Write the message to a file "
        "first and commit with `-F`, or use single quotes.\n\n"
        "## A missing @-import is skipped, not fatal\n\n"
        "A session that @-imports a file which does not exist on disk simply skips that import; "
        "nothing fails and nothing is reported, so a dropped import is invisible unless the reader "
        "checks.\n", encoding="utf-8")
    (v / "Patterns.md").write_text(
        "# Demo — Patterns\n\n"
        "## Cache files are user data, never deleted without asking\n\n"
        "A cache represents real time and money already spent computing it. Treat it like a "
        "user's own files: move it, never delete it silently, and never rewrite it without being "
        "asked.\n\n"
        "## Measure the floor with a repeat of the control\n\n"
        "Before trusting a bar on a noisy metric, run the control arm twice with a "
        "byte-identical configuration. The difference between the two runs is the metric's noise "
        "floor; a bar smaller than that floor is not a bar.\n", encoding="utf-8")
    (v / "Canon.md").write_text(
        "# Demo — Canon\n\n"
        "## One writer per region at a time\n\n"
        "Two sessions editing the same region concurrently can lose an update if both "
        "read-modify-write the same file. A claim mechanism gives each region exactly one active "
        "writer, so the second session waits or defers instead of racing the first.\n", encoding="utf-8")
    return v


def test_recall_bench_grep_arm_runs_and_produces_the_table(demo_vault, tmp_path):
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(RECALL_BENCH_RUN), "--arm", "grep", "--vault", str(demo_vault),
                        "--questions", str(QUESTIONS_EXAMPLE), "--out", str(out)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert p.returncode == 0, p.stderr
    lines = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    q_rows = [l for l in lines if "question" in l]
    assert len(q_rows) == 5                          # the shipped example question set
    assert sum(1 for l in lines if "summary_floor" in l) == 1
    assert (out / "results.md").is_file()
    md = (out / "results.md").read_text(encoding="utf-8")
    assert "recall@limit rate" in md and "noise floor" in md
    # the demo vault is byte-identical to what the questions were written against: every
    # question should be answerable, which is itself a check that the fixture and the shipped
    # example have not drifted apart.
    assert all(r["recall_at_limit"] for r in q_rows), q_rows


def test_recall_bench_index_arm_raises_not_implemented(demo_vault):
    """Driven as a subprocess (see the module docstring on why): an uncaught NotImplementedError
    exits non-zero with the exception's own message on stderr, which is exactly what a black-box
    caller of this command line sees too."""
    p = subprocess.run([sys.executable, str(RECALL_BENCH_RUN), "--arm", "index", "--vault", str(demo_vault)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert p.returncode != 0
    assert "NotImplementedError" in p.stderr and "index" in p.stderr
    assert p.stdout.strip() == ""                     # nothing written on refusal


def test_recall_bench_walk_arm_raises_not_implemented(demo_vault):
    p = subprocess.run([sys.executable, str(RECALL_BENCH_RUN), "--arm", "walk", "--vault", str(demo_vault)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert p.returncode != 0
    assert "NotImplementedError" in p.stderr and "walk" in p.stderr
    assert p.stdout.strip() == ""


def test_recall_bench_markdown_is_a_true_regeneration_of_the_jsonl(demo_vault, tmp_path):
    """`--render-only` re-derives results.md from ONLY the JSONL — run it into a fresh directory
    and diff byte-for-byte against the original the bench itself wrote."""
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(RECALL_BENCH_RUN), "--arm", "grep", "--vault", str(demo_vault),
                        "--questions", str(QUESTIONS_EXAMPLE), "--out", str(out)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert p.returncode == 0, p.stderr
    on_disk_md = (out / "results.md").read_text(encoding="utf-8")
    regen_dir = tmp_path / "regen"
    regen_dir.mkdir()
    rp = subprocess.run([sys.executable, str(RECALL_BENCH_RUN), "--render-only", str(out / "results.jsonl"),
                        "--out", str(regen_dir)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert rp.returncode == 0, rp.stderr
    assert (regen_dir / "results.md").read_text(encoding="utf-8") == on_disk_md


def test_recall_bench_repeat_control_floor_is_zero_for_the_deterministic_arm(demo_vault, tmp_path):
    """recall.py has no randomness, so the noise floor a repeat control measures on it must be
    exactly 0 for every metric that does not itself measure wall-clock time — a non-zero floor on
    `recall_at_limit_rate`, `mean_bytes_to_answer` or `n_questions` would mean either recall.py or
    this scoring code has a hidden non-determinism (e.g. dict/set iteration order leaking into
    ranking). `mean_wall_ms`'s floor is excluded on purpose: it is a real timer reading two
    independent runs, so a small nonzero value there is the correct, expected number — the whole
    point of PREREGISTRATION.md's "a floor of 0 means the two runs agreed" note."""
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(RECALL_BENCH_RUN), "--arm", "grep", "--vault", str(demo_vault),
                        "--questions", str(QUESTIONS_EXAMPLE), "--out", str(out)],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert p.returncode == 0, p.stderr
    assert "noise floor (repeat control): {" in p.stdout
    floor_json = p.stdout.strip().splitlines()[-1].split("noise floor (repeat control): ", 1)[1]
    floor = json.loads(floor_json)
    deterministic = {k: v for k, v in floor.items() if k != "mean_wall_ms"}
    assert deterministic and all(v == 0 for v in deterministic.values()), floor
    assert floor["mean_wall_ms"] >= 0


def test_recall_bench_questions_example_is_well_formed(demo_vault):
    """The shipped example file itself, independent of any fixture: 5 rows, each with the three
    required fields, each `expected` shaped like `File.md#Heading`."""
    rows = json.loads(QUESTIONS_EXAMPLE.read_text(encoding="utf-8"))
    assert len(rows) == 5
    for r in rows:
        assert set(r) == {"question", "expected", "region"}
        assert r["expected"].count("#") == 1
        file_part, heading_part = r["expected"].split("#")
        assert file_part.endswith(".md") and heading_part
