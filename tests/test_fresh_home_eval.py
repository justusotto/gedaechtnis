"""Tests for `eval/fresh_home/run.py` — q:CU-2026-09-10-FRESHHOME-1: the stranger's install path.

The DRY-RUN half (no model, no network) runs every time: it builds a fresh `HOME` + a scratch git
repo, runs the real `init.py` against them with nothing overridden but `HOME`, and asserts every
artifact `init.py`'s own docstring promises (vault region files, `Global/Kernel.md` +
`fleet-roster.md`, `.atlas-lane`, `CLAUDE.md`, `~/.claude/gedaechtnis/config.json`, and — the one
this build exists to prove — the `~/.claude/skills/gedaechtnis` symlink Claude Code actually loads
the plugin through).

The LIVE half additionally launches ONE real `claude -p` from that fresh HOME with
`--setting-sources user,project` and asserts `session.log` gains a row. It is gated behind
`GEDAECHTNIS_FRESHHOME_LIVE=1` (unset by default, the same convention `tests/test_anthropic_watch.py`
uses for `ANTHROPIC_WATCH_LIVE`) — this suite never spends money on its own. **The core assertion —
`session_log_gained_a_row` — does NOT require authentication to pass**: a probe run against this
machine found the fresh HOME cannot authenticate (no Keychain fallback once `HOME` is overridden;
the CLI returns `"Not logged in · Please run /login"`, `total_cost_usd: 0`), but Claude Code's local
SessionStart/UserPromptSubmit hooks still run and `session.log` still gains its row BEFORE the API
auth check fails — see `eval/fresh_home/run.py`'s module docstring, "THE AUTH FINDING". So this test
asserts the row unconditionally and only treats `auth_status` as informational.

Every subprocess here runs `run.py` (and, transitively, `init.py`) exactly the way
`test_eval_skeletons.py` insists on: never a direct in-process `import` of a script that does a
bare `import config` after inserting `hooks/` onto `sys.path` — that pattern shares one
`sys.modules["config"]` process-wide, and importing it here would risk exactly the cross-test
pollution `Global/Errata.md` warns about.
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
RUN = PLUGIN / "eval" / "fresh_home" / "run.py"
LIVE_ENV_FLAG = "GEDAECHTNIS_FRESHHOME_LIVE"
CORE_SIX = ("Map", "Position", "Canon", "Patterns", "Errata", "Aporia")


def _env() -> dict:
    """The env the TEST process passes to `run.py`. `run.py` builds its own fully-isolated fixture
    env internally (`install_env`, HOME overridden, nothing else) — this only strips any
    `GEDAECHTNIS_*` the pytest process itself happens to carry, the same caution every other test
    in this suite takes even where it is not strictly load-bearing."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}


def run_eval(tmp_path: Path, *extra_args: str, timeout: int = 180) -> tuple[subprocess.CompletedProcess, Path, Path]:
    out = tmp_path / "out"
    base = tmp_path / "base"
    p = subprocess.run([sys.executable, str(RUN), "--out", str(out), "--base", str(base), *extra_args],
                       capture_output=True, text=True, env=_env(), stdin=subprocess.DEVNULL, timeout=timeout)
    return p, out, base


# ------------------------------------------------------------------ dry-run ----
def test_dry_run_creates_every_install_artifact(tmp_path):
    p, out, base = run_eval(tmp_path, "--dry-run")
    assert p.returncode == 0, p.stderr
    report = json.loads((out / "fresh-home-dry-run.json").read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert report["artifacts_all_present"] is True, report["artifacts"]
    for stem in CORE_SIX:
        assert report["artifacts"][f"vault/{report['region']}/{stem}.md"] is True
    assert report["artifacts"]["vault/Global/Kernel.md"] is True
    assert report["artifacts"]["vault/Global/fleet-roster.md"] is True
    assert report["artifacts"]["repo/.atlas-lane"] is True
    assert report["artifacts"]["repo/CLAUDE.md"] is True
    assert report["artifacts"]["config.json"] is True
    assert report["artifacts"]["config.json names the fixture vault"] is True
    assert report["artifacts"]["~/.claude/skills/gedaechtnis symlink"] is True
    assert report["artifacts"]["symlink points at this plugin checkout"] is True
    assert report["session_log_lines_before_any_call"] == 0


def test_dry_run_makes_no_model_call(tmp_path):
    """Negative control: nothing in --dry-run touches a network, and the report never carries a
    'call' key (only the --live report does)."""
    p, out, _base = run_eval(tmp_path, "--dry-run")
    assert p.returncode == 0, p.stderr
    report = json.loads((out / "fresh-home-dry-run.json").read_text(encoding="utf-8"))
    assert "call" not in report
    assert "cost" not in report
    assert not (out / "fresh-home-live.json").exists()


def test_dry_run_symlink_targets_this_plugin_checkout(tmp_path):
    """The install artifact this build exists to prove: `~/.claude/skills/gedaechtnis` (under the
    FRESH home, never the real one) resolves to THIS plugin checkout, byte-for-byte — the mechanism
    `--setting-sources user` later loads the plugin through."""
    p, out, base = run_eval(tmp_path, "--dry-run")
    assert p.returncode == 0, p.stderr
    report = json.loads((out / "fresh-home-dry-run.json").read_text(encoding="utf-8"))
    link = Path(report["home"]) / ".claude" / "skills" / "gedaechtnis"
    assert link.is_symlink()
    assert Path(os.path.realpath(link)) == PLUGIN.resolve()


def test_dry_run_state_dir_is_derived_under_the_fresh_home_only(tmp_path):
    """`state_dir` must fall inside the fresh HOME this run built — never the real
    `~/.claude/gedaechtnis` — which is the whole isolation guarantee `install_env()` exists for
    (HOME overridden, nothing else set)."""
    p, out, base = run_eval(tmp_path, "--dry-run")
    assert p.returncode == 0, p.stderr
    report = json.loads((out / "fresh-home-dry-run.json").read_text(encoding="utf-8"))
    state_dir = Path(report["state_dir"])
    home = Path(report["home"])
    state_dir.relative_to(home)                       # raises ValueError (-> test failure) otherwise
    real_state_dir = Path(os.path.expanduser("~/.claude/gedaechtnis"))
    assert state_dir != real_state_dir


def test_dry_run_is_idempotent(tmp_path):
    """A second `--dry-run` over the same `--base` changes nothing — `init.py`'s own idempotency
    guarantee, exercised through this harness."""
    p1, out, base = run_eval(tmp_path, "--dry-run")
    assert p1.returncode == 0, p1.stderr
    report1 = json.loads((out / "fresh-home-dry-run.json").read_text(encoding="utf-8"))
    p2, out2, _base2 = run_eval(tmp_path, "--dry-run", "--base", str(base))
    assert p2.returncode == 0, p2.stderr
    report2 = json.loads((out2 / "fresh-home-dry-run.json").read_text(encoding="utf-8"))
    assert report2["artifacts_all_present"] is True
    assert "kept" in report2["init_stdout"]
    assert "created" not in report2["init_stdout"]
    assert report1["artifacts"] == report2["artifacts"]


def test_dry_run_refuses_when_both_modes_given(tmp_path):
    p, _out, _base = run_eval(tmp_path, "--dry-run", "--live")
    assert p.returncode != 0
    assert "mutually exclusive" in p.stderr


def test_refuses_when_neither_mode_given(tmp_path):
    p, _out, _base = run_eval(tmp_path)
    assert p.returncode != 0
    assert "--dry-run" in p.stderr and "--live" in p.stderr


# -------------------------------------------------------------------- live ----
live = pytest.mark.skipif(os.environ.get(LIVE_ENV_FLAG) != "1",
                          reason=f"set {LIVE_ENV_FLAG}=1 to run the real `claude -p` probe "
                                 "(gedaechtnis/eval/fresh_home/run.py --live)")


@live
def test_live_session_log_gains_a_row(tmp_path):
    """The build's actual deliverable: after ONE pinned `claude -p` run from a completely fresh
    HOME, `session.log` under that same fresh HOME has a new row naming this session's id.

    This assertion does NOT depend on authentication succeeding — see the module docstring's "THE
    AUTH FINDING": the local hook pipeline runs, and `session.log` gains its row, before the API
    auth check can fail. `auth_status` is recorded for the report but is not what this test is
    named for."""
    claude = shutil.which("claude")
    if not claude:
        pytest.skip("no `claude` binary on PATH")
    p, out, _base = run_eval(tmp_path, "--live", "--model", "claude-sonnet-5", "--effort", "low",
                             "--ceiling-usd", "1", timeout=180)
    assert p.returncode == 0, f"stdout={p.stdout[-4000:]}\nstderr={p.stderr[-2000:]}"
    report = json.loads((out / "fresh-home-live.json").read_text(encoding="utf-8"))
    assert report["artifacts_all_present"] is True, report["artifacts"]
    assert report["session_log_gained_a_row"] is True, report
    assert report["session_log_new_row_matches_session_id"] is True, report
    assert report["auth_status"] in ("authenticated", "unauthenticated", "unknown")
    # A value checkpoint, never a silent pass: the run must not have spent more than it declared.
    assert report["cost"]["usd_total"] <= report["ceiling_usd"], report["cost"]
    if report["auth_status"] == "authenticated":
        answer = str(report["call"]["result"].get("result", "")).strip().lower()
        assert "ok" in answer, report["call"]["result"]
        assert report["cost"]["priced"] is True, report["cost"]


@live
def test_live_is_the_install_path_not_the_staged_hooks_path(tmp_path):
    """Distinguishes this harness from `memory_eval`'s `gedaechtnis` arm: nothing here ever writes
    a `.claude/settings.json` INSIDE the scratch repo staging the plugin's own hook table — the
    only settings file involved is the one `init.py` writes at the fresh HOME's user level (the
    outage-check hook), and the plugin itself is reachable only through the
    `~/.claude/skills/gedaechtnis` symlink loaded via `--setting-sources user`."""
    claude = shutil.which("claude")
    if not claude:
        pytest.skip("no `claude` binary on PATH")
    p, out, base = run_eval(tmp_path, "--live", "--model", "claude-sonnet-5", "--effort", "low",
                            "--ceiling-usd", "1", timeout=180)
    assert p.returncode == 0, f"stdout={p.stdout[-4000:]}\nstderr={p.stderr[-2000:]}"
    report = json.loads((out / "fresh-home-live.json").read_text(encoding="utf-8"))
    repo_settings = Path(report["repo"]) / ".claude" / "settings.json"
    assert not repo_settings.exists(), (
        "a repo-level .claude/settings.json would mean this test staged the plugin's hooks "
        "directly (memory_eval's path), not the install path")
    assert "user,project" in report["call"]["argv"]
    argv_idx = report["call"]["argv"].index("--setting-sources")
    assert report["call"]["argv"][argv_idx + 1] == "user,project"
