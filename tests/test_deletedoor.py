"""DELETEPOLICY-1 — the delete door: `rm` only in the scratchpad, a worktree, or `__pycache__`.

Driven through `gate.py bash` as a subprocess, the way Claude Code calls it, so what is asserted is
the JSON the harness would act on. The session's temp tree is seamed with
GEDAECHTNIS_SESSION_TMP_ROOT so no test writes under the machine's real `/private/tmp/claude-*`.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SID = "sid-delete-1"


@pytest.fixture
def w(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    tmproot = tmp_path / "claude-tmp"
    scratch = tmproot / "-proj" / SID / "scratchpad"
    scratch.mkdir(parents=True)
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / ".git").mkdir()                          # a repository, for the worktrees allowance
    (repo / ".claude" / "worktrees" / "ROW-1").mkdir(parents=True)
    (repo / "pkg" / "__pycache__").mkdir(parents=True)
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(state), GEDAECHTNIS_VAULT=str(tmp_path / "vault"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user-memory.md"),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-roster.md"),
               GEDAECHTNIS_SESSION_TMP_ROOT=str(tmproot), GEDAECHTNIS_DELETE_DOOR="1")
    return dict(tmp=tmp_path, state=state, scratch=scratch.resolve(), repo=repo.resolve(), env=env)


def pin(w, mode):
    (w["state"] / "delete.mode").write_text(mode + "\n")


def bash(w, cmd, cwd=None, env=None):
    p = subprocess.run([sys.executable, str(HOOKS / "gate.py"), "bash"],
                       input=json.dumps({"cwd": str(cwd or w["repo"]), "tool_name": "Bash",
                                         "session_id": SID, "tool_input": {"command": cmd}}),
                       capture_output=True, text=True, env=env or w["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out)["hookSpecificOutput"] if out else {}


def denied(res):
    return res.get("permissionDecision") == "deny"


# ------------------------------------------------------------------ the row's three controls ----

def test_POSITIVE_rm_in_this_sessions_scratchpad_is_allowed(w):
    pin(w, "deny")
    (w["scratch"] / "tmp.txt").write_text("x")
    assert bash(w, f"rm {w['scratch']}/tmp.txt") == {}
    assert bash(w, f"rm -rf {w['scratch']}/build") == {}


def test_NEGATIVE_rm_under_data_is_refused_and_names_the_trash_command(w):
    pin(w, "deny")
    (w["repo"] / "data" / "cache.json").write_text("x")
    res = bash(w, "rm data/cache.json")
    assert denied(res)
    assert f"/usr/bin/trash {w['repo']}/data/cache.json" in res["permissionDecisionReason"]
    assert "moved to Trash" in res["permissionDecisionReason"]


def test_SYMLINK_rm_r_over_a_dir_containing_a_link_is_refused_even_in_the_scratchpad(w):
    pin(w, "deny")
    d = w["scratch"] / "tree"
    d.mkdir()
    target = w["repo"] / "data"
    (d / "link").symlink_to(target)
    res = bash(w, f"rm -r {d}")
    assert denied(res)
    assert "symbolic link" in res["permissionDecisionReason"]
    assert target.exists()


def test_the_same_tree_WITHOUT_a_link_is_allowed(w):
    """The symlink control's negative: the refusal above is the link, not the recursion."""
    pin(w, "deny")
    d = w["scratch"] / "tree"
    d.mkdir()
    (d / "plain.txt").write_text("x")
    assert bash(w, f"rm -r {d}") == {}


# ---------------------------------------------------------------------------- the allow-list ----

def test_a_worktree_under_dot_claude_worktrees_is_allowed(w):
    pin(w, "deny")
    assert bash(w, "rm -rf .claude/worktrees/ROW-1/build") == {}


def test_the_worktrees_directory_ITSELF_is_not_allowed(w):
    pin(w, "deny")
    assert denied(bash(w, "rm -rf .claude/worktrees"))


def test_pycache_and_pyc_are_allowed(w):
    pin(w, "deny")
    assert bash(w, "rm -rf pkg/__pycache__") == {}
    assert bash(w, "rm pkg/mod.pyc") == {}


def test_the_session_tree_is_found_among_SEVERAL_project_dirs(w):
    """The harness keeps one directory per project; this session's tree is in only one of them."""
    pin(w, "deny")
    root = w["tmp"] / "claude-tmp"
    for name in ("-a", "-b", "-c", "-d", "-e"):
        (root / name / "not-this-session").mkdir(parents=True)
    moved = root / "-zz" / SID
    moved.parent.mkdir()
    (root / "-proj" / SID).rename(moved)
    assert bash(w, f"rm {moved.resolve()}/scratchpad/f.txt") == {}


def test_ANOTHER_sessions_scratchpad_is_refused(w):
    pin(w, "deny")
    other = w["tmp"] / "claude-tmp" / "-proj" / "some-other-session" / "scratchpad"
    other.mkdir(parents=True)
    assert denied(bash(w, f"rm {other}/f.txt"))


# ------------------------------------------------------------------ reading the command right ----

def test_cd_is_followed_across_segments(w):
    pin(w, "deny")
    assert bash(w, f"cd {w['scratch']} && rm f.txt") == {}
    assert denied(bash(w, f"cd {w['repo']}/data && rm f.txt", cwd=w["scratch"]))


def test_a_variable_target_is_refused_as_unseeable(w):
    pin(w, "deny")
    res = bash(w, 'rm -rf "$WORK"')
    assert denied(res)
    assert "cannot see" in res["permissionDecisionReason"]


def test_HOME_is_expanded_not_treated_as_unseeable(w):
    pin(w, "deny")
    res = bash(w, "rm $HOME/some-file")
    assert denied(res)
    assert "not in this session's scratchpad" in res["permissionDecisionReason"]


def test_xargs_rm_is_refused(w):
    pin(w, "deny")
    assert denied(bash(w, f"ls {w['scratch']} | xargs rm"))


def test_find_delete_is_judged_by_its_start_directory(w):
    pin(w, "deny")
    assert bash(w, f"find {w['scratch']} -name '*.tmp' -delete") == {}
    assert denied(bash(w, "find data -name '*.json' -delete"))


def test_a_glob_is_expanded_against_the_cwd(w):
    pin(w, "deny")
    (w["scratch"] / "a.txt").write_text("x")
    (w["repo"] / "data" / "b.json").write_text("x")
    assert bash(w, "rm *.txt", cwd=w["scratch"]) == {}
    assert denied(bash(w, "rm data/*"))


@pytest.mark.parametrize("cmd", ["env rm data/x", "env FOO=1 BAR=2 rm data/x", "sudo rm data/x",
                                 "command rm data/x", "/bin/rm data/x", "bash -c 'rm data/x'",
                                 "true; rm data/x", "echo ok && rm -f data/x"])
def test_a_WRAPPED_rm_is_still_seen(w, cmd):
    """Reviewer's bypass: `env rm` passed while bare `rm` was refused. Every wrapper the door
    claims to see is listed here, so dropping one from the prefix set reddens its own case."""
    pin(w, "deny")
    assert denied(bash(w, cmd)), cmd


def test_git_rm_and_non_rm_commands_are_not_this_doors(w):
    pin(w, "deny")
    assert bash(w, "git rm --cached -- data/x") == {}
    assert bash(w, "echo rm data/x") == {}
    assert bash(w, "ls data") == {}


# -------------------------------------------------------------------------- WARN, then DENY ----

def test_WARN_lets_the_command_run_tells_the_model_and_logs(w):
    pin(w, "warn")
    res = bash(w, "rm data/x")
    assert "permissionDecision" not in res, "WARN must never make a permission decision"
    assert res["additionalContext"].startswith("WARN")
    assert "/usr/bin/trash" in res["additionalContext"]
    assert "warn\trm data/x" in (w["state"] / "delete.log").read_text()


def test_no_mode_file_starts_a_DATED_warn_day(w):
    res = bash(w, "rm data/x")
    assert res["additionalContext"].startswith("WARN")
    since = float((w["state"] / "delete.warn-since").read_text())
    assert abs(since - time.time()) < 60


def test_the_warn_day_ends_and_DENY_begins_after_24h(w):
    (w["state"] / "delete.warn-since").write_text(f"{time.time() - 24 * 3600 - 5:.0f}\n")
    assert denied(bash(w, "rm data/x"))


def test_one_hour_into_the_warn_day_is_still_WARN(w):
    (w["state"] / "delete.warn-since").write_text(f"{time.time() - 3600:.0f}\n")
    assert not denied(bash(w, "rm data/x"))


def test_twenty_three_hours_into_the_warn_day_is_still_WARN(w):
    """The window is 24 h, not "about a day": 23 h in must still run the command."""
    (w["state"] / "delete.warn-since").write_text(f"{time.time() - 23 * 3600:.0f}\n")
    assert not denied(bash(w, "rm data/x"))


def test_a_pinned_WARN_outlasts_the_warn_day(w):
    (w["state"] / "delete.warn-since").write_text(f"{time.time() - 48 * 3600:.0f}\n")
    pin(w, "warn")
    assert not denied(bash(w, "rm data/x"))


def test_the_door_is_OFF_without_its_config_flag(w):
    env = dict(w["env"])
    env.pop("GEDAECHTNIS_DELETE_DOOR")
    pin(w, "deny")
    assert bash(w, "rm data/x", env=env) == {}


def test_the_facts_line_names_the_mode_and_the_flip_time(w, monkeypatch):
    for k, v in w["env"].items():
        monkeypatch.setenv(k, v)
    sys.path.insert(0, str(HOOKS))
    import importlib, config, common, deletedoor                 # noqa: PLC0415
    for m in (config, common, deletedoor):
        importlib.reload(m)
    assert "WARN until" in deletedoor.facts_line()
    pin(w, "deny")
    assert deletedoor.facts_line().startswith("- Delete door: DENY")
