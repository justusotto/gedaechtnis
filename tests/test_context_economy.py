"""context_economy.py — two non-blocking PreToolUse notices: RE-READ (unchanged content) and
BIG-READ (a whole file over the threshold). Every rule gets a POSITIVE control (the notice fires)
and a NEGATIVE control (it does not) — a notice that only ever fires proves nothing about what it
lets through silently. Neither notice may ever deny; the last test proves that holds even for
malformed input.

All state is redirected into tmp_path: the suite never reads the real vault and never writes the
real ~/.claude/gedaechtnis (Global/Errata: a suite that writes the application's real sidecar makes
its own verdict depend on the machine's state).
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
# Deliberately NOT on sys.path and never imported: every hook here is exercised as the SUBPROCESS
# Claude Code actually runs (see tests/test_doors.py for why).


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "Atlas"
    (vault / "Global").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, state=state, env=env, tmp=tmp_path)


def run(script, which, payload, env, cwd=HOOKS):
    p = subprocess.run([sys.executable, str(cwd / script), which], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out) if out else None


def decision(res):
    return (res or {}).get("hookSpecificOutput", {}).get("permissionDecision")


def ctx(res):
    return (res or {}).get("hookSpecificOutput", {}).get("additionalContext", "")


def gate_read(w, path, sid="A", offset=None, limit=None, cwd=None):
    ti = {"file_path": str(path)}
    if offset is not None:
        ti["offset"] = offset
    if limit is not None:
        ti["limit"] = limit
    return run("gate.py", "read", {"cwd": cwd or str(w["repo"]), "tool_name": "Read",
                                   "session_id": sid, "tool_input": ti}, w["env"])


def chore_read(w, path, sid="A", offset=None, limit=None, cwd=None):
    ti = {"file_path": str(path)}
    if offset is not None:
        ti["offset"] = offset
    if limit is not None:
        ti["limit"] = limit
    return run("chore.py", "read", {"cwd": cwd or str(w["repo"]), "tool_name": "Read",
                                    "session_id": sid, "tool_input": ti}, w["env"])


def gate_bash(w, cmd, sid="A", cwd=None):
    return run("gate.py", "bash", {"cwd": cwd or str(w["repo"]), "tool_name": "Bash",
                                   "session_id": sid, "tool_input": {"command": cmd}}, w["env"])


def chore_bash(w, cmd, sid="A", cwd=None):
    return run("chore.py", "bash", {"cwd": cwd or str(w["repo"]), "tool_name": "Bash",
                                    "session_id": sid, "tool_input": {"command": cmd}}, w["env"])


def chore_write(w, path, sid="A", cwd=None):
    """Register `path` as written by `sid`, without actually needing matching content — chore.py's
    context-economy dispatch records the PATH, not the tool_input's `content`."""
    return run("chore.py", "write", {"cwd": cwd or str(w["repo"]), "tool_name": "Write", "session_id": sid,
                                     "tool_input": {"file_path": str(path), "content": "irrelevant"}}, w["env"])


SMALL = "line one\nline two\n"                       # well under the 24 KB default threshold


# ============================================================ RE-READ — Read tool ================

def test_first_read_of_a_file_gets_no_notice(world):
    """NEGATIVE control. Nothing was read before, so there is nothing to compare against."""
    f = world["repo"] / "notes.md"
    f.write_text(SMALL)
    assert gate_read(world, f) is None


def test_unchanged_reread_gets_the_notice_and_still_allows(world):
    """POSITIVE control. PROVES the notice fires on the shape it exists for, and that it is an
    ALLOW, not a wall — a session must never be blocked from reading its own memory back."""
    f = world["repo"] / "notes.md"
    f.write_text(SMALL)
    assert chore_read(world, f) is None                # PostToolUse: the first read is recorded
    res = gate_read(world, f)                           # PreToolUse: the second read, unchanged
    assert decision(res) == "allow"
    text = ctx(res)
    assert "Unchanged since you read it" in text
    assert "ago (" in text and "KB" in text and "tokens at 4 B/token" in text
    assert "Re-reading writes those tokens to context again" in text
    assert "Grep for the section you need" in text


def test_a_file_that_changed_since_the_last_read_gets_no_reread_notice(world):
    """NEGATIVE control, the discriminating case: same path, different content — the whole point
    of hashing rather than trusting the path alone."""
    f = world["repo"] / "notes.md"
    f.write_text(SMALL)
    chore_read(world, f)
    f.write_text(SMALL + "a third line, added since\n")
    assert gate_read(world, f) is None


def test_reread_notice_is_per_SESSION(world):
    """NEGATIVE control on scope: session B never read this file, so B gets no re-read notice even
    though A's read of the identical content is on file."""
    f = world["repo"] / "notes.md"
    f.write_text(SMALL)
    chore_read(world, f, sid="A")
    assert gate_read(world, f, sid="B") is None


# ========================================================= BIG-READ — Read tool ===================

BIG = ("x" * 1024 + "\n") * 30                        # ~30 KB, over the 24 KB default


def test_a_whole_big_file_gets_the_big_read_notice(world):
    """POSITIVE control. PROVES the notice fires on a first read (no prior record at all) — this
    is not a re-read rule, it is a size rule."""
    f = world["repo"] / "big.md"
    f.write_text(BIG)
    res = gate_read(world, f)
    assert decision(res) == "allow"
    text = ctx(res)
    assert "This file is" in text and "KB (~" in text and "tokens)" in text
    assert "Grep for the heading, then Read with offset/limit" in text


def test_a_small_file_never_gets_the_big_read_notice(world):
    """NEGATIVE control on size: same shape, under the threshold."""
    f = world["repo"] / "notes.md"
    f.write_text(SMALL)
    assert gate_read(world, f) is None


def test_read_with_offset_suppresses_the_big_read_notice(world):
    """NEGATIVE control on the offset/limit exemption — the session already asked for a slice, so
    the notice that tells it to ask for a slice would be nonsense."""
    f = world["repo"] / "big.md"
    f.write_text(BIG)
    assert gate_read(world, f, offset=1) is None
    assert gate_read(world, f, limit=50) is None


def test_a_file_this_session_wrote_itself_never_gets_the_big_read_notice(world):
    """NEGATIVE control on the write exemption. A discriminating pair: the SAME big file gets the
    notice for a session that never wrote it, and none for the session that did."""
    f = world["repo"] / "big.md"
    f.write_text(BIG)
    chore_write(world, f, sid="A")
    assert gate_read(world, f, sid="A") is None
    assert decision(gate_read(world, f, sid="B")) == "allow"


def test_context_economy_can_be_switched_off(world):
    """NEGATIVE control on the config flag — both notices, silenced together."""
    env = dict(world["env"], GEDAECHTNIS_CONTEXT_ECONOMY="0")
    w = dict(world, env=env)
    f = w["repo"] / "big.md"
    f.write_text(BIG)
    assert gate_read(w, f) is None                      # would be a big-read notice if enabled
    chore_read(w, f)
    f2 = w["repo"] / "notes.md"
    f2.write_text(SMALL)
    chore_read(w, f2)
    assert gate_read(w, f2) is None                      # would be a re-read notice if enabled


# ========================================================= Bash: cat / head / tail / sed -n =======

def test_bash_cat_of_an_unchanged_file_gets_the_reread_notice(world):
    """POSITIVE control, the shell half of RE-READ."""
    f = world["repo"] / "notes.md"
    f.write_text(SMALL)
    assert chore_bash(world, f"cat {f}") is None
    res = gate_bash(world, f"cat {f}")
    assert decision(res) == "allow"
    assert "Unchanged since you read it" in ctx(res)


def test_bash_cat_of_a_big_file_gets_the_bigread_notice(world):
    """POSITIVE control, the shell half of BIG-READ — `cat` is treated as a whole-file read."""
    f = world["repo"] / "big.md"
    f.write_text(BIG)
    res = gate_bash(world, f"cat {f}")
    assert decision(res) == "allow"
    assert "This file is" in ctx(res)


@pytest.mark.parametrize("cmd_fmt", ["head -5 {f}", "tail -5 {f}", "sed -n '1,5p' {f}"])
def test_bash_head_tail_sed_n_never_get_the_bigread_notice(world, cmd_fmt):
    """NEGATIVE controls: these three are partial reads by construction — the whole-file threshold
    must never fire for them, however big the file is."""
    f = world["repo"] / "big.md"
    f.write_text(BIG)
    res = gate_bash(world, cmd_fmt.format(f=f))
    assert res is None, (cmd_fmt, ctx(res))


def test_bash_command_that_is_not_a_read_gets_no_notice(world):
    """NEGATIVE control on the verb list: `wc -l` is not one of the read verbs this module knows."""
    f = world["repo"] / "notes.md"
    f.write_text(SMALL)
    assert gate_bash(world, f"wc -l {f}") is None


# ================================================================= status.py reporting ============

def test_status_reports_the_notice_counts(world):
    """PROVES the counts status.py shows are read from the same state this module writes — a report
    that computed its own version would drift the moment either changed."""
    f = world["repo"] / "notes.md"
    f.write_text(SMALL)
    chore_read(world, f, sid="S")
    gate_read(world, f, sid="S")                         # fires the re-read notice, once
    big = world["repo"] / "big.md"
    big.write_text(BIG)
    gate_read(world, big, sid="S")                        # fires the big-read notice, once
    p = subprocess.run([sys.executable, str(TOOLS / "status.py"), "--session-id", "S",
                        "--cwd", str(world["repo"])],
                       capture_output=True, text=True, env=world["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    assert "context economy: 1 re-read notice(s), 1 big-read notice(s) this session" in p.stdout


# ============================================================ fail-open on malformed input ========

def test_never_denies_even_on_malformed_input(world):
    """PROVES the fail-open contract: a `file_path` that is not a string breaks `expand()` inside
    this module, and the hook still exits 0 with NO decision printed — never a deny — and the
    traceback lands in hook-errors.log instead of taking the turn down. Exercised on both the Read
    and Bash dispatch, and on both PreToolUse and PostToolUse."""
    bad = {"cwd": str(world["repo"]), "tool_name": "Read", "session_id": "A",
           "tool_input": {"file_path": 12345}}            # not a string: expand() calls .strip() on it
    for script, which in (("gate.py", "read"), ("chore.py", "read")):
        p = subprocess.run([sys.executable, str(HOOKS / script), which], input=json.dumps(bad),
                           capture_output=True, text=True, env=world["env"], timeout=30)
        assert p.returncode == 0, (script, p.stderr)
        assert p.stdout.strip() == "", (script, "a malformed read must never print a decision")

    bad_bash = {"cwd": str(world["repo"]), "tool_name": "Bash", "session_id": "A",
               "tool_input": {"command": 12345}}          # not a string either
    for script, which in (("gate.py", "bash"), ("chore.py", "bash")):
        p = subprocess.run([sys.executable, str(HOOKS / script), which], input=json.dumps(bad_bash),
                           capture_output=True, text=True, env=world["env"], timeout=30)
        assert p.returncode == 0, (script, p.stderr)

    errlog = world["state"] / "hook-errors.log"
    assert errlog.is_file() and errlog.read_text(encoding="utf-8").strip(), \
        "a hook that swallowed an exception with nothing logged is a silent failure mode"
