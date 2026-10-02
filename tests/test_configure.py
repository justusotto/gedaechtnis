"""HANDLINES-1 — `tools/configure.py`: a limit is changed by a tool, not a pasted `python3 -c` line.

Every run points GEDAECHTNIS_CONFIG and GEDAECHTNIS_STATE_DIR at tmp_path and scrubs the session
variables unless a test sets them: the tool never sees the machine's own config.json.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "configure.py"
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT", "GEDAECHTNIS_SEAT")


@pytest.fixture
def box(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"vault": "/somewhere", "limits": {"_note": "kept", "session_cap": 40}},
                              indent=1) + "\n")
    env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    env.update(GEDAECHTNIS_CONFIG=str(cfg), GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_VAULT=str(tmp_path / "Vault"))
    env.pop("GEDAECHTNIS_LIMITS", None)

    def run(*args, **extra):
        e = dict(env, **extra)
        return subprocess.run([sys.executable, str(TOOL), *args], env=e, capture_output=True,
                              text=True, timeout=30, cwd=str(tmp_path))
    run.cfg, run.tmp = cfg, tmp_path
    return run


def limits_of(box):
    return json.loads(box.cfg.read_text())["limits"]


def test_set_a_live_key_writes_backs_up_logs_and_keeps_the_rest(box):
    p = box("set", "context_reach_margin_tokens", "25000")
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(box.cfg.read_text())
    assert doc["vault"] == "/somewhere"
    assert doc["limits"] == {"_note": "kept", "session_cap": 40, "context_reach_margin_tokens": 25000}
    backups = list(box.tmp.glob("config.json.pre-context_reach_margin_tokens-*"))
    assert len(backups) == 1 and "context_reach" not in backups[0].read_text()
    log = (box.tmp / "state" / "config.log").read_text()
    assert "\tset\tcontext_reach_margin_tokens\t\"(shipped)\" -> 25000\tby terminal" in log


def test_a_second_set_keeps_the_first_backup(box):
    box("set", "session_cap", "30")
    first = next(box.tmp.glob("config.json.pre-session_cap-*")).read_text()
    box("set", "session_cap", "20")
    assert next(box.tmp.glob("config.json.pre-session_cap-*")).read_text() == first
    assert limits_of(box)["session_cap"] == 20


def test_setting_the_same_value_writes_nothing(box):
    before = box.cfg.read_text()
    p = box("set", "session_cap", "40")
    assert p.returncode == 0 and "nothing written" in p.stdout and box.cfg.read_text() == before


@pytest.mark.parametrize("key, value, word", [
    ("session_cap", "lots", "is a int"),
    ("session_cap", "true", "bool"),
    ("session_close_apply", "1", "is a bool"),
    ("session_close_never", "\"* host\"", "is a list"),
])
def test_a_value_of_the_wrong_type_is_refused_and_nothing_written(box, key, value, word):
    before = box.cfg.read_text()
    p = box("set", key, value)
    assert p.returncode == 2 and word in p.stdout, p.stdout
    assert box.cfg.read_text() == before


def test_a_list_value_is_json(box):
    p = box("set", "session_close_never", '["* host", "seat-*"]')
    assert p.returncode == 0, p.stdout
    assert limits_of(box)["session_close_never"] == ["* host", "seat-*"]


@pytest.mark.parametrize("key", ["no_such_limit", "_resume_window", "boot_budget"])
def test_an_unknown_or_documentation_key_is_refused(box, key):
    before = box.cfg.read_text()
    p = box("set", key, "1")
    assert p.returncode == 3 and "refused" in p.stdout and box.cfg.read_text() == before


def test_an_owner_class_key_needs_the_owner_flag(box):
    before = box.cfg.read_text()
    p = box("set", "worktree_sweep_apply", "true")
    assert p.returncode == 3 and "owner's switch (owner:irreversible)" in p.stdout
    assert box.cfg.read_text() == before
    p = box("set", "worktree_sweep_apply", "true", "--owner")
    assert p.returncode == 0, p.stdout
    assert limits_of(box)["worktree_sweep_apply"] is True
    assert "(owner)" in (box.tmp / "state" / "config.log").read_text()


@pytest.mark.parametrize("var", ["CLAUDECODE", "CLAUDE_CODE_SESSION_ID"])
def test_the_owner_flag_is_refused_inside_a_claude_session(box, var):
    before = box.cfg.read_text()
    p = box("set", "worktree_sweep_apply", "true", "--owner", **{var: "1"})
    assert p.returncode == 3 and "inside a Claude session" in p.stdout
    assert box.cfg.read_text() == before
    p = box("unset", "session_cap", "--owner", **{var: "1"})     # unset too, even of a live key
    assert p.returncode == 3


def test_a_session_sets_a_live_key_and_is_named_in_the_log(box):
    p = box("set", "resume_short_chars", "1200", CLAUDE_CODE_SESSION_ID="abc-123")
    assert p.returncode == 0, p.stdout
    assert "by abc-123" in (box.tmp / "state" / "config.log").read_text()


def test_unset_returns_a_key_to_its_shipped_value(box):
    p = box("unset", "session_cap")
    assert p.returncode == 0 and "session_cap" not in limits_of(box)
    assert box("get", "session_cap").stdout.strip() == "0"
    assert box("unset", "session_cap").returncode == 0                  # twice: a no-op


def test_get_reads_the_effective_value(box):
    assert box("get", "session_cap").stdout.strip() == "40"
    assert box("get", "resume_short_window").stdout.strip() == "400000"


def test_a_broken_config_is_refused_not_replaced(box):
    box.cfg.write_text('{"limits": {"session_cap": 40,}}')
    before = box.cfg.read_text()
    p = box("set", "session_cap", "10")
    assert p.returncode == 4 and box.cfg.read_text() == before


def test_a_missing_config_is_created(box):
    box.cfg.unlink()
    p = box("set", "session_cap", "12")
    assert p.returncode == 0, p.stdout
    assert json.loads(box.cfg.read_text()) == {"limits": {"session_cap": 12}}


def test_show_names_the_source_and_the_class(box):
    out = box("show").stdout
    assert "session_cap = 40  [config; live]" in out
    assert "compaction_apply = false  [shipped; owner:irreversible]" in out


def _limits_read(box, value):
    """What limits.py makes of `session_swap_cap_share` = value in config.json (SWAPCAP)."""
    doc = json.loads(box.cfg.read_text())
    doc["limits"]["session_swap_cap_share"] = value
    box.cfg.write_text(json.dumps(doc))
    code = ("import json,sys; sys.path.insert(0, %r); import limits, fleet; "
            "print(json.dumps([limits.get('session_swap_cap_share'), fleet.swap_cap(), "
            "[p for p in limits.problems() if 'swap' in p]]))" % str(TOOL.parents[1] / "hooks"))
    env = {k: v for k, v in os.environ.items() if k not in SESSION_VARS}
    env.update(GEDAECHTNIS_CONFIG=str(box.cfg), GEDAECHTNIS_STATE_DIR=str(box.tmp / "state"),
               GEDAECHTNIS_VAULT=str(box.tmp / "Vault"))
    env.pop("GEDAECHTNIS_LIMITS", None)
    p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       timeout=30, cwd=str(box.tmp))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_a_swap_cap_share_of_0_75_is_applied(box):
    """SWAPCAP positive control: config.json's 0.75 was refused as "a float, not a int"."""
    got, cap, probs = _limits_read(box, 0.75)
    assert (got, cap, probs) == (0.75, 0.75, [])


def test_a_swap_cap_share_above_1_is_refused(box):
    got, cap, probs = _limits_read(box, 1.5)
    assert got == 0 and cap == 0.0
    assert probs and "not a share between 0 and 1" in probs[0], probs


def test_configure_sets_a_share_and_refuses_one_out_of_range(box):
    assert box("set", "session_swap_cap_share", "0.75").returncode == 0
    assert limits_of(box)["session_swap_cap_share"] == 0.75
    p = box("set", "session_swap_cap_share", "1.5")
    assert p.returncode != 0 and limits_of(box)["session_swap_cap_share"] == 0.75, p.stdout + p.stderr


def test_an_abbreviated_owner_flag_is_not_the_owner_flag():
    """PERMDESIGN-1 review: argparse expanded `--own` to `--owner`, past a deny rule on `*--owner*`."""
    import subprocess, sys as _s                                        # noqa: PLC0415,E401
    p = subprocess.run([_s.executable, str(Path(__file__).resolve().parents[1] / "tools" / "configure.py"),
                        "set", "session_cap", "1", "--own"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "unrecognized arguments: --own" in p.stderr
