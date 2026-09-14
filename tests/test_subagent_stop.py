"""Tests for the SubagentStop auto-commit — the ATTRIBUTED subset of the touched set.

Same world as `test_commit.py`: a git repository in tmp_path pointed at by GEDAECHTNIS_VAULT,
the state directory redirected, so the suite never reads or writes a real vault.

The touches are recorded the way production records them — by running `chore.py write` with the
payload shape the harness actually sends, `agent_id` present for a subagent's tool call and
absent for the parent's own (measured live 2026-09-14 from a raw hook dump). Nothing is stubbed
into the state file by hand: a hook reading a key nobody writes would pass a test that did.

Both controls, and the negative one is the load-bearing half. A SubagentStop commit must FIRE on
the subagent's own paths and STAY QUIET on everything else — the parent's untouched-by-the-
subagent files above all, since committing those is precisely the sweep `commit.py` exists to
prevent, aimed at the parent instead of a sibling.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def git(vault: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=True)
    return p.stdout


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "vault"
    (vault / "MyProject").mkdir(parents=True)
    (vault / "Other").mkdir()
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    (vault / "MyProject" / "Position.md").write_text("start\n")
    (vault / "MyProject" / "Errata.md").write_text("start\n")
    (vault / "Other" / "Position.md").write_text("start\n")
    git(vault, "add", "-A")                      # the fixture's own setup, not the hook's doing
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: MY-PROJECT\npath: MyProject/\npath: Global/\n")
    state = tmp_path / "state"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-such-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, state=state, env=env, tmp=tmp_path)


def hook(w, script, argv, payload, timeout=60):
    p = subprocess.run([sys.executable, str(HOOKS / script)] + ([argv] if argv else []),
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=w["env"], timeout=timeout)
    assert p.returncode == 0, p.stderr
    return p


def write(w, rel: str, text: str, sid="s1", agent: str | None = None, cwd=None):
    """Write a vault file as the harness reports it: the Write tool, then the PostToolUse chore.

    `agent` is the subagent's id when the write came from a subagent's tool call, and None when
    the parent wrote it with its own hand — the exact discriminator the live payloads carry."""
    path = w["vault"] / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    payload = {"cwd": str(cwd or w["repo"]), "session_id": sid, "tool_name": "Write",
               "tool_input": {"file_path": str(path), "content": text}}
    if agent:
        payload["agent_id"] = agent
        payload["agent_type"] = "general-purpose"
    hook(w, "chore.py", "write", payload)
    return path


def subagent_stop(w, agent: str | None = "ag1", sid="s1", cwd=None):
    payload = {"cwd": str(cwd or w["repo"]), "session_id": sid,
               "hook_event_name": "SubagentStop", "stop_hook_active": False}
    if agent:
        payload["agent_id"] = agent
        payload["agent_type"] = "general-purpose"
    p = hook(w, "subagent_stop.py", None, payload)
    assert p.stdout.strip() == "", "a SubagentStop hook must not talk over the session"
    return p


def parent_stop(w, sid="s1", cwd=None):
    p = hook(w, "commit.py", None, {"cwd": str(cwd or w["repo"]), "session_id": sid})
    assert p.stdout.strip() == ""
    return p


def head_subject(vault: Path) -> str:
    return git(vault, "log", "-1", "--format=%s").strip()


def head_files(vault: Path) -> list[str]:
    return [l for l in git(vault, "show", "--name-only", "--format=", "HEAD").splitlines() if l]


def commit_log(w) -> str:
    f = w["state"] / "commit.log"
    return f.read_text(encoding="utf-8") if f.is_file() else ""


def porcelain(w) -> str:
    return git(w["vault"], "status", "--porcelain")


# ------------------------------------------------------------------ the positive control ----
def test_a_subagents_write_is_committed_when_that_subagent_ends(world):
    """★ The whole point of the hook: the subagent's file is in history before the parent stops."""
    v = world["vault"]
    write(world, "MyProject/Canon.md", "## the builder's decision\n", agent="ag1")
    subagent_stop(world)
    assert head_files(v) == ["MyProject/Canon.md"]
    assert head_subject(v).startswith("subagent auto-commit: [MY-PROJECT] general-purpose ")
    assert git(v, "log", "-1", "--format=%an <%ae>").strip() == "Gedächtnis <gedaechtnis@local>"
    assert "event=subagent-stop agent=ag1" in commit_log(world) and "committed=1" in commit_log(world)


def test_the_parents_own_earlier_work_still_lands_at_the_parents_stop(world):
    """The hook is about LATENCY, not ownership: nothing it declines to carry is lost — the
    parent's Stop takes its own set afterwards, exactly as before."""
    v = world["vault"]
    write(world, "MyProject/Position.md", "the parent, mid-turn\n")
    write(world, "MyProject/Canon.md", "## the builder's decision\n", agent="ag1")
    subagent_stop(world)
    assert head_files(v) == ["MyProject/Canon.md"]
    parent_stop(world)
    assert head_files(v) == ["MyProject/Position.md"]
    assert porcelain(world).strip() == ""


def test_each_subagent_carries_only_its_own_paths(world):
    v = world["vault"]
    write(world, "MyProject/Canon.md", "## first builder\n", agent="ag1")
    write(world, "MyProject/Errata.md", "## second builder\n", agent="ag2")
    subagent_stop(world, agent="ag1")
    assert head_files(v) == ["MyProject/Canon.md"]
    subagent_stop(world, agent="ag2")
    assert head_files(v) == ["MyProject/Errata.md"]


# ------------------------------------------------------------------ the negative controls ----
def test_a_parents_in_flight_file_the_subagent_never_touched_is_NOT_committed(world):
    """★ THE control this hook is designed around. The parent is half-way through an Errata
    entry when a subagent it launched ends. The naive implementation — point SubagentStop at
    `commit.py` unchanged — commits the parent's touched set and carries that unfinished
    sentence, which is the sibling-sweep collision aimed at the parent instead."""
    v = world["vault"]
    write(world, "MyProject/Errata.md", "## half-written\n\nThe mechanism is that")   # parent
    write(world, "MyProject/Canon.md", "## the builder's decision\n", agent="ag1")
    subagent_stop(world)
    assert head_files(v) == ["MyProject/Canon.md"]
    assert "Errata.md" not in git(v, "show", "--name-only", "--format=", "HEAD")
    assert " M MyProject/Errata.md" in porcelain(world)          # still the parent's to finish


def test_a_file_BOTH_wrote_is_held_for_the_parents_stop(world):
    """The background case: the parent kept editing while the subagent ran, so the file holds
    both edits and git cannot split them. Held, logged, and left for the parent's Stop — late,
    which is the defect being reduced, rather than a committed half-edit, which is not undoable."""
    v = world["vault"]
    write(world, "MyProject/Position.md", "builder's paragraph\n", agent="ag1")
    write(world, "MyProject/Position.md", "builder's paragraph\nthe parent, mid-sentence")
    before = git(v, "rev-parse", "HEAD").strip()
    subagent_stop(world)
    assert git(v, "rev-parse", "HEAD").strip() == before, "nothing should have been committed"
    assert "held-for-parent-stop" in commit_log(world)
    assert " M MyProject/Position.md" in porcelain(world)
    parent_stop(world)                                           # and the parent still gets it
    assert head_files(v) == ["MyProject/Position.md"]


def test_a_subagent_that_wrote_nothing_to_the_vault_commits_nothing(world):
    v = world["vault"]
    write(world, "MyProject/Position.md", "the parent's own work\n")
    before = git(v, "rev-parse", "HEAD").strip()
    subagent_stop(world, agent="ag-idle")
    assert git(v, "rev-parse", "HEAD").strip() == before
    assert " M MyProject/Position.md" in porcelain(world)


def test_a_payload_with_no_agent_id_commits_nothing(world):
    """Attribution missing = the hook does not know whose writes it holds. It must NOT fall back
    to the parent's touched set — that fallback IS the naive implementation."""
    v = world["vault"]
    write(world, "MyProject/Canon.md", "## the builder's decision\n", agent="ag1")
    write(world, "MyProject/Position.md", "the parent, mid-turn\n")
    before = git(v, "rev-parse", "HEAD").strip()
    subagent_stop(world, agent=None)
    assert git(v, "rev-parse", "HEAD").strip() == before
    assert "agent=UNKNOWN" in commit_log(world) and "action=staged-nothing" in commit_log(world)


def test_a_subagents_write_OUTSIDE_the_partition_is_left_untouched(world):
    v = world["vault"]
    write(world, "Other/Position.md", "another lane's region\n", agent="ag1")
    before = git(v, "rev-parse", "HEAD").strip()
    subagent_stop(world)
    assert git(v, "rev-parse", "HEAD").strip() == before
    assert " M Other/Position.md" in porcelain(world)


def test_no_marker_commits_nothing(world):
    """The lane fail-safe is `commit.py`'s and is inherited whole, not re-implemented."""
    v = world["vault"]
    bare = world["tmp"] / "unmarked"
    bare.mkdir()
    write(world, "MyProject/Canon.md", "## builder\n", agent="ag1", cwd=bare)
    before = git(v, "rev-parse", "HEAD").strip()
    subagent_stop(world, cwd=bare)
    assert git(v, "rev-parse", "HEAD").strip() == before
    assert "lane=UNKNOWN" in commit_log(world)
    assert "?? MyProject/Canon.md" in porcelain(world)


def test_a_second_stop_for_the_same_subagent_is_a_no_op(world):
    v = world["vault"]
    write(world, "MyProject/Canon.md", "## the builder's decision\n", agent="ag1")
    subagent_stop(world)
    after_first = git(v, "rev-parse", "HEAD").strip()
    subagent_stop(world)
    assert git(v, "rev-parse", "HEAD").strip() == after_first
    assert "nothing-left-to-commit" in commit_log(world)
