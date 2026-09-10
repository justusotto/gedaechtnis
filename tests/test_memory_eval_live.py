"""Tests for `memory_eval/run.py --live` — the real-`claude -p` path, proven against the STUB.

No test here launches a real model or touches the network: `--claude` always points at
`eval/stub_claude.py`, which now speaks the `--output-format json` result shape (`result`,
`usage`, `modelUsage`) that the live path parses and prices. What is being proven is the LAUNCH
CONTRACT and the MONEY GUARDS, not any model's answers:

  * `--model` and `--effort` are pinned into the child's argv, and a run missing either REFUSES
  * every call gets `stdin=DEVNULL` (`claude -p` reads inherited stdin and appends it to the prompt)
  * each call's whole result JSON lands in `<out>/<arm>-<task>-<day>.usage.json`
  * `--ceiling-usd` refuses to START another call once the priced sum reaches it
  * `--dry-run` is untouched by all of the above
  * an unknown model refuses BEFORE the first call — an unpriceable run does not start

**Every check has a positive AND a negative control.** A refusal test that never sees the
non-refusing case cannot tell "the guard fired" from "the harness was broken anyway", and an
assertion like `stdin_extra == ""` is vacuous until the same field is shown carrying a real leak.
Each test therefore exercises both sides, most of them by pairing a refusing run with a completing
one over the same fixture.

Everything runs as a SUBPROCESS, never an in-process import — the same reason
`test_eval_skeletons.py` gives: these scripts do unqualified `import config` after putting
`hooks/` on `sys.path`, and two of them in one interpreter share `sys.modules["config"]`.
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
EVAL = PLUGIN / "eval"
STUB = EVAL / "stub_claude.py"
MEMORY_EVAL_RUN = EVAL / "memory_eval" / "run.py"
TASKS = EVAL / "memory_eval" / "tasks"
# The fleet's price table lives outside the plugin (it is not the plugin's to carry) — the live
# path imports it. Where it is absent, as in a published copy of this plugin alone, the live tests
# that need a price skip and the "refuses without a pricing module" test still runs.
PRICING = PLUGIN.parent / "scripts" / "concilium.py"
MODEL = "claude-sonnet-5"
EFFORT = "low"
LEAK = "LEAKED-STDIN-MARKER-DO-NOT-APPEND-ME"

pricing_required = pytest.mark.skipif(
    not PRICING.is_file(), reason=f"no pricing module at {PRICING} — live pricing cannot be proven")


def _env(home: Path) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    e["HOME"] = str(home)
    e["GEDAECHTNIS_CONFIG"] = str(home / ".claude" / "gedaechtnis" / "config.json")
    e["GEDAECHTNIS_STATE_DIR"] = str(home / "state")
    return e


def one_task_dir(root: Path) -> Path:
    """Just the positive-control task: three arms x two days = six calls, which is enough to prove
    every contract below and keeps the suite fast. `load_tasks` REQUIRES a positive-control task,
    so this is also the smallest legal task set."""
    d = root / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(TASKS / "001-db-choice.json", d / "001-db-choice.json")
    return d


def run_eval(tmp: Path, *args, stdin_text: str | None = None, timeout: int = 300):
    """Run the harness in its own throwaway world and return (proc, out_dir, base_dir)."""
    out = tmp / "out"
    base = tmp / "base"
    home = tmp / "home"
    home.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(MEMORY_EVAL_RUN), "--out", str(out), "--base", str(base),
           "--tasks-dir", str(one_task_dir(tmp)), *args]
    kw = {"input": stdin_text} if stdin_text is not None else {"stdin": subprocess.DEVNULL}
    p = subprocess.run(cmd, capture_output=True, text=True, env=_env(home), timeout=timeout, **kw)
    return p, out, base


def live_args(model: str = MODEL, effort: str | None = EFFORT, ceiling: str | None = None,
              pricing: Path | None = None) -> list[str]:
    args = ["--live", "--claude", str(STUB), "--model", model,
            "--pricing", str(pricing if pricing is not None else PRICING)]
    if effort is not None:
        args += ["--effort", effort]
    if ceiling is not None:
        args += ["--ceiling-usd", ceiling]
    return args


def usage_files(out: Path) -> list[Path]:
    return sorted(out.glob("*.usage.json"))


def records(out: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in usage_files(out)]


def flag_value(argv: list[str], flag: str) -> str | None:
    """The token immediately after `flag` — the only reading that proves a flag was PINNED rather
    than merely present somewhere in the command line."""
    return argv[argv.index(flag) + 1] if flag in argv else None


# ------------------------------------------------- one shared completing live run ----
@pytest.fixture(scope="module")
def live_run(tmp_path_factory):
    """The positive control every refusal test is measured against: a complete live-shaped run
    that succeeds, over the stub."""
    tmp = tmp_path_factory.mktemp("live_ok")
    p, out, base = run_eval(tmp, *live_args(ceiling="10"), stdin_text=LEAK)
    return p, out, base


# ------------------------------------------------------- model / effort pinning ----
@pricing_required
def test_model_and_effort_are_pinned_in_every_child_argv(live_run):
    p, out, _base = live_run
    assert p.returncode == 0, p.stderr
    recs = records(out)
    assert len(recs) == 6, [r["day"] for r in recs]          # 3 arms x (day1 + dayN)
    for r in recs:
        argv = r["argv"]
        assert flag_value(argv, "--model") == MODEL, argv
        assert flag_value(argv, "--effort") == EFFORT, argv
        assert flag_value(argv, "--output-format") == "json", argv
        assert flag_value(argv, "--setting-sources") == "project", argv
        assert "--resume" not in argv, "a resumed `claude -p` does not amortize its prefix"
        # the prompt is the LAST token and sits immediately after the boolean -p, so no variadic
        # flag (--allowedTools/--add-dir/--tools) can swallow it
        assert argv[-2] == "-p", argv[-3:]
        # and the child CONFIRMS what reached it, which argv alone cannot
        assert r["result"]["stub"]["model"] == MODEL
        assert r["result"]["stub"]["effort"] == EFFORT


@pricing_required
def test_live_without_effort_refuses_and_makes_no_call(tmp_path):
    """Negative control for the test above: the same run minus --effort must not start."""
    p, out, _base = run_eval(tmp_path, *live_args(effort=None, ceiling="10"))
    assert p.returncode == 2, p.stdout
    assert "--effort" in p.stderr
    assert usage_files(out) == [], "a refused run must not have launched anything"


def test_live_without_claude_refuses(tmp_path):
    p, out, _base = run_eval(tmp_path, "--live", "--model", MODEL, "--effort", EFFORT,
                             "--pricing", str(PRICING))
    assert p.returncode == 2, p.stdout
    assert "--claude" in p.stderr
    assert usage_files(out) == []


# ------------------------------------------------------------------- stdin ----
@pricing_required
def test_child_stdin_is_devnull(live_run):
    """The harness's OWN stdin carried a marker (`stdin_text=LEAK`); no child may have seen it."""
    p, out, _base = live_run
    assert p.returncode == 0, p.stderr
    recs = records(out)
    assert recs
    for r in recs:
        assert r["stdin"] == "DEVNULL"
        assert r["result"]["stub"]["stdin_extra"] == "", (
            "the child read something off stdin — `claude -p` appends inherited stdin to the prompt")


@pricing_required
def test_stdin_extra_can_actually_carry_a_leak(live_run, tmp_path):
    """Negative control for the test above: replay one recorded argv with an INHERITED pipe instead
    of DEVNULL and the very same field comes back carrying the marker. Without this, `stdin_extra
    == ""` could just mean the stub never reports anything."""
    _p, out, _base = live_run
    argv = records(out)[0]["argv"]
    leaked = subprocess.run(argv, capture_output=True, text=True, input=LEAK,
                            env=_env(tmp_path), timeout=60)
    assert leaked.returncode == 0, leaked.stderr
    assert json.loads(leaked.stdout)["stub"]["stdin_extra"] == LEAK


# ------------------------------------------------------------- usage records ----
@pricing_required
def test_every_call_writes_a_priced_usage_file(live_run):
    p, out, _base = live_run
    names = {f.name for f in usage_files(out)}
    assert "none-001-db-choice-day1.usage.json" in names
    assert "none-001-db-choice-day3.usage.json" in names
    assert "gedaechtnis-001-db-choice-day3.usage.json" in names
    for r in records(out):
        assert r["schema"] == "gedaechtnis-memory-eval-usage/1"
        assert r["result"]["modelUsage"], "the raw result JSON is kept, not just our summary"
        assert r["cost"]["usd_total"] >= 0.0
        assert r["cost"]["prices_as_of"], "a cost figure without its price date is not an answer"
        assert r["cost"]["models"] == [MODEL]
    summary = json.loads((out / "live-summary.json").read_text(encoding="utf-8"))
    assert summary["calls_made"] == 6 and summary["stopped_early"] is None
    assert summary["usd_total"] == pytest.approx(sum(r["cost"]["usd_total"] for r in records(out)))


def test_dry_run_writes_no_usage_files(tmp_path):
    """Negative control for the test above: the same harness, no live flag, no records at all."""
    p, out, _base = run_eval(tmp_path, "--dry-run")
    assert p.returncode == 0, p.stderr
    assert usage_files(out) == []
    assert not (out / "live-summary.json").exists()


# ---------------------------------------------------------------- the ceiling ----
@pricing_required
def test_ceiling_refuses_to_start_another_call(tmp_path):
    """A ceiling below one call's price lets the FIRST call run (nothing was spent yet) and refuses
    the second — the guard is on STARTING a call, which is the only point where money is still
    unspent."""
    p, out, _base = run_eval(tmp_path, *live_args(ceiling="0.00001"))
    assert p.returncode == 0, p.stderr
    assert "CEILING REACHED" in p.stdout
    recs = records(out)
    assert len(recs) == 1, [r["day"] for r in recs]
    summary = json.loads((out / "live-summary.json").read_text(encoding="utf-8"))
    assert summary["calls_made"] == 1
    assert summary["calls_planned"] == 3          # 3 arms x 1 task, cells not calls
    assert summary["stopped_early"] and "ceiling" in summary["stopped_early"].lower()
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows == [], "the interrupted cell is not reported as a completed row"


@pricing_required
def test_a_generous_ceiling_completes_the_whole_run(live_run):
    """Negative control for the test above — same code path, ceiling far above the run's cost."""
    p, out, _base = live_run
    assert p.returncode == 0, p.stderr
    assert "CEILING REACHED" not in p.stdout
    summary = json.loads((out / "live-summary.json").read_text(encoding="utf-8"))
    assert summary["calls_made"] == 6 and summary["stopped_early"] is None
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 3


# ------------------------------------------------------------------ dry run ----
DRY_RUN_ROW_KEYS = {"arm", "task_id", "positive_control", "success", "tokens", "boot_bytes",
                    "context_bytes"}


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dry")
    return run_eval(tmp, "--dry-run")


def test_dry_run_rows_and_table_are_unchanged_by_the_live_path(dry_run):
    """The live path added row keys and stdout lines. None may appear in a dry run: its JSONL rows
    carry exactly the skeleton's seven keys, and its table is still a pure regeneration of them."""
    p, out, _base = dry_run
    assert p.returncode == 0, p.stderr
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 3                                    # three arms x this fixture's one task
    for r in rows:
        assert set(r) == DRY_RUN_ROW_KEYS, sorted(set(r) - DRY_RUN_ROW_KEYS)
    assert "cost  " not in p.stdout and "live:" not in p.stdout and "# LIVE" not in p.stdout
    assert p.stdout.endswith(")\n") and "throwaway homes kept for inspection" in p.stdout


@pricing_required
def test_the_dry_run_assertion_is_not_vacuous(live_run):
    """Negative control for the test above: a LIVE run's rows DO carry the extra keys and its
    stdout DOES carry the cost lines — so the dry-run check is asserting a real difference."""
    p, out, _base = live_run
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert all({"usd", "usage_files"} <= set(r) for r in rows)
    assert "cost  " in p.stdout and "# LIVE" in p.stdout


def test_dry_run_and_live_are_mutually_exclusive(tmp_path):
    p, out, _base = run_eval(tmp_path, "--dry-run", *live_args(ceiling="10"))
    assert p.returncode != 0
    assert "mutually exclusive" in p.stderr
    assert usage_files(out) == []


# ------------------------------------------------------------ unknown model ----
@pricing_required
def test_unknown_model_refuses_before_any_call(tmp_path):
    p, out, _base = run_eval(tmp_path, *live_args(model="claude-not-a-real-model-9", ceiling="10"))
    assert p.returncode == 2, p.stdout
    assert "claude-not-a-real-model-9" in p.stderr
    assert MODEL in p.stderr, "the refusal names the models it WOULD price"
    assert usage_files(out) == [], "an unpriceable run must not spend anything first"


@pricing_required
def test_a_priced_model_is_accepted(live_run):
    """Negative control for the test above: the identical path with a model that IS in the table."""
    p, out, _base = live_run
    assert p.returncode == 0, p.stderr
    assert len(usage_files(out)) == 6


# --------------------------------------------------------------- the pricing ----
def test_missing_pricing_module_refuses(tmp_path):
    p, out, _base = run_eval(tmp_path, *live_args(pricing=tmp_path / "no-such-concilium.py",
                                                  ceiling="10"))
    assert p.returncode == 2, p.stdout
    assert "pricing" in p.stderr.lower()
    assert usage_files(out) == []


@pricing_required
def test_the_price_table_is_imported_not_copied(live_run):
    """The recorded price date must be the PRICING MODULE's own — proof the table was imported
    rather than duplicated into the harness, where it would drift on the next price change."""
    _p, out, _base = live_run
    src = MEMORY_EVAL_RUN.read_text(encoding="utf-8")
    asof = None
    for line in PRICING.read_text(encoding="utf-8").splitlines():
        if line.startswith("PRICE_ASOF"):
            asof = line.split("=", 1)[1].strip().strip('"\'')
            break
    assert asof, "the pricing module no longer declares PRICE_ASOF"
    assert asof not in src, "run.py carries a copy of the price date — import it instead"
    summary = json.loads((out / "live-summary.json").read_text(encoding="utf-8"))
    assert summary["prices_as_of"] == asof


# ------------------------------------------------- the gedaechtnis arm's plugin ----
@pricing_required
def test_gedaechtnis_arm_stages_the_plugin_via_project_settings(live_run):
    """`--setting-sources project` drops user-level plugins, so the arm's hooks must reach the
    session through a settings file written INTO the fixture — never through the user's ~/.claude."""
    _p, _out, base = live_run
    settings = base / "gedaechtnis__001-db-choice" / "home" / "repo" / ".claude" / "settings.json"
    assert settings.is_file()
    text = settings.read_text(encoding="utf-8")
    assert "${CLAUDE_PLUGIN_ROOT}" not in text, "an unexpanded placeholder loads nothing"
    doc = json.loads(text)
    shipped = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert set(doc["hooks"]) == set(shipped["hooks"]), "the staged hook set is the shipped one"
    assert str(PLUGIN) in doc["hooks"]["SessionStart"][0]["hooks"][0]["command"]


@pricing_required
def test_the_other_arms_get_no_plugin(live_run):
    """Negative control for the test above: an arm that is not `gedaechtnis` must have no settings
    file at all, or the arms would not be distinguishable."""
    _p, _out, base = live_run
    for arm in ("none", "automemory"):
        assert not (base / f"{arm}__001-db-choice" / "home" / "repo" / ".claude" / "settings.json").exists()
