"""PLUGDIR-1 — the doors a stranger could meet, and the one permission shortcut, closed.

Two changes, each with its controls:

  1. NO HOOK EVER EMITS `permissionDecision: "allow"`. A note is `additionalContext` alone. An
     explicit allow skips the person's own permission prompt, which is exactly what a plugin must
     not do quietly (the delete door already said so; the notices did it anyway until 2026-09-26).

  2. THE EVERYWHERE-DOORS ARE MARKER-SCOPED. The worktree-placement door, the inherited-tag push
     door, the two fan-out rules, the resume gate and the operating-rules injection act in full only
     inside the configured vault or a repo carrying `.atlas-lane` (or when the session was LAUNCHED
     in one — `CLAUDE_PROJECT_DIR`). Elsewhere the same text arrives as a note and the tool runs.
     The fan-out and resume negatives live beside their positives, in test_fanout.py and
     test_resume_gate.py; the rest are here.

Plus init.py: from an installed plugin it neither symlinks itself into ~/.claude/skills nor writes
~/.claude/settings.json, and a default run writes no settings anywhere.

Every subprocess gets an env WITHOUT `CLAUDE_PROJECT_DIR` unless a test sets it: a suite run from
inside a Claude Code hook would otherwise carry a marked launch directory into every negative
control and turn it into a positive one.
"""
from __future__ import annotations
import json, os, re, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(PLUGIN))


def env_for(tmp: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GEDAECHTNIS_") and k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
    env.update(GEDAECHTNIS_VAULT=str(tmp / "Vault"), GEDAECHTNIS_STATE_DIR=str(tmp / "state"),
               GEDAECHTNIS_CONFIG=str(tmp / "config.json"),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp / "no-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp / "no-user-memory.md"))
    env.update(extra)
    return env


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def gate(tmp: Path, which: str, payload: dict, **extra):
    p = subprocess.run([sys.executable, str(HOOKS / "gate.py"), which], input=json.dumps(payload),
                       capture_output=True, text=True, env=env_for(tmp, **extra), timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def hso(res) -> dict:
    return (res or {}).get("hookSpecificOutput") or {}


def bash(tmp, cmd, cwd, **extra):
    return gate(tmp, "bash", {"cwd": str(cwd), "tool_name": "Bash", "session_id": "t",
                              "tool_input": {"command": cmd}}, **extra)


@pytest.fixture
def repo(tmp_path):
    """An UNMARKED git repo with one commit — a stranger's project."""
    r = tmp_path / "proj"
    r.mkdir()
    git("init", "-q", "-b", "main", str(r))
    (r / "a.txt").write_text("a\n")
    git("add", "--", "a.txt", cwd=r)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root", "--", "a.txt", cwd=r)
    (tmp_path / "config.json").write_text("{}")
    return r


def mark(r: Path) -> Path:
    (r / ".atlas-lane").write_text("lane: TEST\npath: Test/\n")
    return r


# ------------------------------------------------------- 1. no hook emits "allow" ----

ALLOW_RX = re.compile(r"""permissionDecision["']?\s*[:=,]\s*["']allow["']""")


def _plugin_sources():
    for p in PLUGIN.rglob("*.py"):
        rel = p.relative_to(PLUGIN).parts
        if rel[0] in ("tests", "eval") or "__pycache__" in rel:
            continue
        yield p


def test_no_hook_source_emits_permission_decision_allow():
    """Every Python file the plugin ships (tests and evals aside) — no `permissionDecision` of
    "allow" anywhere, and the helper that used to emit one is gone."""
    offenders = [str(p.relative_to(PLUGIN)) for p in _plugin_sources() if ALLOW_RX.search(p.read_text(encoding="utf-8"))]
    assert offenders == [], offenders
    import common
    assert not hasattr(common, "allow"), "common.allow is back — a note must be common.context"


def test_the_allow_scan_would_see_one():
    """The scan's own positive control: the shape the old helper had is matched."""
    old = '''print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "allow",'''
    assert ALLOW_RX.search(old)
    assert not ALLOW_RX.search('"permissionDecision": "deny"')
    assert len(list(_plugin_sources())) > 20, "the scan walked almost nothing"


def test_a_notice_carries_no_permission_decision(tmp_path, repo):
    """BEHAVIOUR, not source: the fan-out 8-of-8 warning — a note from a hook that used to allow —
    arrives as additionalContext with no decision at all."""
    mark(repo)
    state = tmp_path / "state"
    state.mkdir()
    import time
    sid = "s-allow"
    (state / f"session-start-{sid}.json").write_text(json.dumps({"agent_open": [time.time()] * 7}))
    res = gate(tmp_path, "agent", {"session_id": sid, "cwd": str(repo),
                                   "tool_input": {"subagent_type": "x", "model": "sonnet", "prompt": "go"}})
    assert "permissionDecision" not in hso(res), res
    assert "8 of 8" in hso(res).get("additionalContext", "")


# ------------------------------------------------ 2a. the worktree-placement door ----

def wt_cmd(r: Path) -> str:
    return f"git -C {r} worktree add {r.parent / 'beside'} -b wt-x main"


def test_worktree_door_positive_a_marked_repo_is_refused(tmp_path, repo):
    res = bash(tmp_path, wt_cmd(mark(repo)), repo)
    assert hso(res).get("permissionDecision") == "deny", res
    assert ".claude/worktrees" in hso(res)["permissionDecisionReason"]


def test_worktree_door_negative_an_unmarked_repo_gets_a_note_and_runs(tmp_path, repo):
    res = bash(tmp_path, wt_cmd(repo), repo)
    h = hso(res)
    assert "permissionDecision" not in h, res
    assert "not enforced here" in h["additionalContext"] and ".claude/worktrees" in h["additionalContext"]


def test_worktree_door_the_launch_directory_counts(tmp_path, repo):
    (tmp_path / "launched").mkdir()
    launched = mark(tmp_path / "launched")
    res = bash(tmp_path, wt_cmd(repo), repo, CLAUDE_PROJECT_DIR=str(launched))
    assert hso(res).get("permissionDecision") == "deny", res


def test_worktree_door_inside_the_vault_is_refused_without_a_marker(tmp_path):
    """The vault carries no marker by law; being inside it is scope enough."""
    v = tmp_path / "Vault"
    v.mkdir()
    git("init", "-q", "-b", "main", str(v))
    (v / "a.md").write_text("a\n")
    git("add", "--", "a.md", cwd=v)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "r", "--", "a.md", cwd=v)
    (tmp_path / "config.json").write_text("{}")
    res = bash(tmp_path, wt_cmd(v), v)
    assert hso(res).get("permissionDecision") == "deny", res


# ------------------------------------------------ 2b. the inherited-tag push door ----

@pytest.fixture
def clone(tmp_path, repo):
    git("tag", "private-start", cwd=repo)
    dst = tmp_path / "clone"
    git("clone", "-q", str(repo), str(dst))
    return dst


def test_tag_door_positive_a_marked_clone_is_refused(tmp_path, clone):
    res = bash(tmp_path, "git push --tags", mark(clone))
    assert hso(res).get("permissionDecision") == "deny", res


def test_tag_door_negative_an_unmarked_clone_gets_a_note_and_runs(tmp_path, clone):
    res = bash(tmp_path, "git push --tags", clone)
    h = hso(res)
    assert "permissionDecision" not in h, res
    assert "not enforced here" in h["additionalContext"] and "private-start" in h["additionalContext"]


# ------------------------------------------------ 2c. the operating-rules injection ----

def boot(tmp: Path, cwd: Path) -> str:
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "session_start.py")],
                       input=json.dumps({"session_id": "s1", "cwd": str(cwd), "source": "startup"}),
                       capture_output=True, text=True, env=env_for(tmp), timeout=120)
    assert p.returncode == 0, p.stderr
    return hso(json.loads(p.stdout)).get("additionalContext", "")


RULES_HEAD = "The operating rules for this vault"


def test_rules_positive_a_marked_repo_gets_them(tmp_path, repo):
    (tmp_path / "Vault").mkdir()
    assert RULES_HEAD in boot(tmp_path, mark(repo))


def test_rules_negative_an_unmarked_repo_does_not(tmp_path, repo):
    (tmp_path / "Vault").mkdir()
    assert RULES_HEAD not in boot(tmp_path, repo)


# ------------------------------------------------ 3. init.py from an installed plugin ----

def test_from_installed_plugin_reads_the_root_and_the_cache(tmp_path, monkeypatch):
    import init
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    cached = tmp_path / ".claude" / "plugins" / "cache" / "m" / "gedaechtnis" / "0.2.0"
    cached.mkdir(parents=True)
    elsewhere = tmp_path / "src" / "gedaechtnis"
    elsewhere.mkdir(parents=True)
    assert init.from_installed_plugin(cached) is True
    assert init.from_installed_plugin(elsewhere) is False
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(elsewhere))
    assert init.from_installed_plugin(elsewhere) is True


def _run_init(tmp: Path, repo: Path, *args, **extra):
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = env_for(tmp, HOME=str(home), GEDAECHTNIS_VAULT=str(home / "Gedaechtnis"),
                  GEDAECHTNIS_CONFIG=str(home / ".claude" / "gedaechtnis" / "config.json"),
                  GEDAECHTNIS_STATE_DIR=str(home / ".claude" / "gedaechtnis"), **extra)
    p = subprocess.run([sys.executable, str(PLUGIN / "init.py"), "--repo", str(repo), "--yes", *args],
                       cwd=str(repo), capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=180)
    assert p.returncode == 0, p.stderr + p.stdout
    return home, p.stdout


def test_init_from_an_installed_plugin_writes_no_symlink_and_no_settings(tmp_path, repo):
    home, out = _run_init(tmp_path, repo, "--outage-check", CLAUDE_PLUGIN_ROOT=str(PLUGIN))
    assert not (home / ".claude" / "skills" / "gedaechtnis").exists(), out
    assert not (home / ".claude" / "settings.json").exists(), out
    assert (repo / ".atlas-lane").is_file(), "the install itself must still happen"


def test_init_from_a_checkout_symlinks_and_writes_settings_only_on_the_flag(tmp_path, repo):
    """The negative control for the one above: a hand install still symlinks; settings.json only
    with `--outage-check`."""
    home, _ = _run_init(tmp_path, repo)
    assert (home / ".claude" / "skills" / "gedaechtnis").is_symlink()
    assert not (home / ".claude" / "settings.json").exists(), "a default run wrote Claude's settings"
    home, _ = _run_init(tmp_path, repo, "--outage-check")
    assert "outage_check.py" in (home / ".claude" / "settings.json").read_text()


# ------------------------------------ the launch directory counts ONLY when IT is marked ----
# `CLAUDE_PROJECT_DIR` is a PLACE in_scope() checks, exactly like the cwd — never a scope by
# itself. A stranger's launch directory is their unmarked repo, so it must leave every door a note.

def test_an_unmarked_launch_directory_leaves_every_door_a_note(tmp_path, repo, clone):
    launched = {"CLAUDE_PROJECT_DIR": str(repo)}                     # the stranger's own, unmarked
    h = hso(bash(tmp_path, wt_cmd(repo), repo, **launched))
    assert "permissionDecision" not in h and "not enforced here" in h["additionalContext"], h
    h = hso(bash(tmp_path, "git push --tags", clone, **launched))
    assert "permissionDecision" not in h and "not enforced here" in h["additionalContext"], h
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    import time
    (state / "session-start-s-launch.json").write_text(json.dumps({"agent_open": [time.time()] * 8}))
    h = hso(gate(tmp_path, "agent", {"session_id": "s-launch", "cwd": str(repo),
                                     "tool_input": {"subagent_type": "x", "model": "sonnet", "prompt": "go"}},
                 **launched))
    assert "permissionDecision" not in h and "agent_max_concurrent" in h["additionalContext"], h
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "session_start.py")],
                       input=json.dumps({"session_id": "s1", "cwd": str(repo), "source": "startup"}),
                       capture_output=True, text=True, env=env_for(tmp_path, **launched), timeout=120)
    assert RULES_HEAD not in p.stdout


# ------------------------------------------------ 2d. the artifact-not-file door ----
# Scoped after the PLUGDIR-1 review: it matched ANY page carrying an artifact-store call, so a
# stranger opening their own such page was refused. The positive stays in test_gate.py
# (`test_open_answer_store_page_denied_plain_html_allowed`, a marked session).

def _store_page(d: Path) -> Path:
    p = d / "judge.html"
    p.write_text('<title>x</title><script>const db=await claude.use("db")</script>')
    return p


def test_artifact_door_positive_a_marked_repo_is_refused(tmp_path, repo):
    page = _store_page(mark(repo))
    assert hso(bash(tmp_path, f"open {page}", repo)).get("permissionDecision") == "deny"


def test_artifact_door_negative_an_unmarked_repo_gets_a_note_and_runs(tmp_path, repo):
    page = _store_page(repo)
    h = hso(bash(tmp_path, f"open {page}", repo))
    assert "permissionDecision" not in h and "ARTIFACT's own store" in h["additionalContext"], h


def test_the_scope_log_names_the_door_that_would_have_refused(tmp_path, repo, clone):
    """The review's must-fix: the row sliced inside the fixed prefix and said nothing about WHICH
    door. Two doors, two rows, each naming its own reason."""
    bash(tmp_path, wt_cmd(repo), repo)
    bash(tmp_path, "git push --tags", clone)
    rows = (tmp_path / "state" / "scope.log").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2, rows
    assert "A git worktree goes under" in rows[0] and "`git push --tags` sends" in rows[1], rows
    assert not any("not enforced here" in r for r in rows), rows
