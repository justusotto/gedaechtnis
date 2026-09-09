"""Tests for the Stop-time auto-commit.

Every case runs against a git repository created in tmp_path and pointed at by
GEDAECHTNIS_VAULT, with the state directory redirected too: the suite never reads or writes a
real vault (a suite that writes the application's real state makes its own verdict depend on the
machine — and here that state is git history, which is not undoable).

Each rule gets both controls: a declared path IS committed, an undeclared one is NOT; a marker
that resolves DOES commit, an absent or malformed one commits nothing and says so in the log.
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
    (vault / "Global").mkdir()
    (vault / "Other").mkdir()
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    (vault / "MyProject" / "Position.md").write_text("start\n")
    (vault / "Other" / "Position.md").write_text("start\n")
    git(vault, "add", "-A")                      # the fixture's own setup, not the hook's doing
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: MY-PROJECT\npath: MyProject/\npath: Global/\n")
    state = tmp_path / "state"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, state=state, env=env, tmp=tmp_path)


def stop(w, cwd=None, env=None):
    p = subprocess.run([sys.executable, str(HOOKS / "commit.py")],
                       input=json.dumps({"cwd": str(cwd or w["repo"]), "session_id": "s1"}),
                       capture_output=True, text=True, env=env or w["env"], timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "", "a Stop hook must not talk over the session's last message"
    return p


def head_subject(vault: Path) -> str:
    return git(vault, "log", "-1", "--format=%s").strip()


def head_files(vault: Path) -> list[str]:
    return [l for l in git(vault, "show", "--name-only", "--format=", "HEAD").splitlines() if l]


def commit_log(w) -> str:
    f = w["state"] / "commit.log"
    return f.read_text(encoding="utf-8") if f.is_file() else ""


# ------------------------------------------------------------------ the positive control ----
def test_a_dirty_declared_path_is_committed(world):
    v = world["vault"]
    (v / "MyProject" / "Position.md").write_text("shipped the thing\n")
    (v / "MyProject" / "Canon.md").write_text("## decided\n")          # untracked, also ours
    stop(world)
    assert head_files(v) == ["MyProject/Canon.md", "MyProject/Position.md"]
    assert head_subject(v).startswith("session-end auto-commit: [MY-PROJECT] ")
    assert "committed=2" in commit_log(world)


def test_the_commit_identity_is_the_machine_identity(world):
    v = world["vault"]
    (v / "MyProject" / "Position.md").write_text("changed\n")
    stop(world)
    assert git(v, "log", "-1", "--format=%an <%ae>").strip() == "Gedächtnis <gedaechtnis@local>"


def test_a_deletion_inside_the_partition_is_committed(world):
    v = world["vault"]
    (v / "MyProject" / "Position.md").unlink()
    stop(world)
    assert head_files(v) == ["MyProject/Position.md"]
    assert git(v, "ls-files", "MyProject/Position.md").strip() == ""


# ------------------------------------------------------------------ the negative controls ----
def test_a_dirty_UNdeclared_path_is_left_untouched(world):
    """The whole point of the partition: another lane's region is not this lane's to commit,
    even when it is dirty in the same working tree at the same moment."""
    v = world["vault"]
    (v / "MyProject" / "Position.md").write_text("ours\n")
    (v / "Other" / "Position.md").write_text("theirs, mid-edit\n")
    (v / "Other" / "Notes.md").write_text("theirs, untracked\n")
    stop(world)
    assert head_files(v) == ["MyProject/Position.md"]
    porcelain = git(v, "status", "--porcelain")
    assert " M Other/Position.md" in porcelain and "?? Other/Notes.md" in porcelain


def test_no_marker_commits_nothing(world):
    v = world["vault"]
    (v / "MyProject" / "Position.md").write_text("changed\n")
    bare = world["tmp"] / "unmarked"
    bare.mkdir()
    before = head_subject(v)
    stop(world, cwd=bare)
    assert head_subject(v) == before
    assert "lane=UNKNOWN" in commit_log(world) and "action=staged-nothing" in commit_log(world)
    assert " M MyProject/Position.md" in git(v, "status", "--porcelain")


def test_a_dotdot_path_in_the_marker_rejects_the_WHOLE_marker(world):
    """One bad `path:` line invalidates the marker entire — including its good lines — because a
    marker that can name a directory outside the vault cannot be trusted about any of them."""
    v = world["vault"]
    (world["repo"] / ".atlas-lane").write_text(
        "lane: MY-PROJECT\npath: MyProject/\npath: ../elsewhere\n")
    (v / "MyProject" / "Position.md").write_text("changed\n")
    before = head_subject(v)
    stop(world)
    assert head_subject(v) == before
    assert "lane=UNKNOWN" in commit_log(world)
    assert " M MyProject/Position.md" in git(v, "status", "--porcelain")


def test_an_absolute_path_in_the_marker_rejects_the_WHOLE_marker(world):
    v = world["vault"]
    (world["repo"] / ".atlas-lane").write_text("lane: MY-PROJECT\npath: /etc\n")
    (v / "MyProject" / "Position.md").write_text("changed\n")
    before = head_subject(v)
    stop(world)
    assert head_subject(v) == before and "lane=UNKNOWN" in commit_log(world)


def test_a_clean_vault_produces_no_commit_and_no_noise(world):
    v = world["vault"]
    before = head_subject(v)
    stop(world)
    assert head_subject(v) == before
    assert commit_log(world) == "", "an ordinary no-op session says nothing at all"


def test_auto_commit_can_be_switched_off(world):
    v = world["vault"]
    (v / "MyProject" / "Position.md").write_text("changed\n")
    before = head_subject(v)
    stop(world, env=dict(world["env"], GEDAECHTNIS_AUTO_COMMIT="0"))
    assert head_subject(v) == before and "auto_commit=off" in commit_log(world)


# ------------------------------------------------------- what the hook is forbidden to do ----
def test_the_hook_never_sweeps_and_never_amends(world):
    """Proven two ways: from git itself (a second commit is a NEW commit, and a file outside the
    partition that was dirty before the run is still dirty after it), and from the source, which
    may not contain the forms the vault's git law forbids."""
    v = world["vault"]
    (v / "MyProject" / "Position.md").write_text("one\n")
    stop(world)
    first = git(v, "rev-parse", "HEAD").strip()
    (v / "MyProject" / "Position.md").write_text("two\n")
    (v / "Other" / "Position.md").write_text("still theirs\n")
    stop(world)
    second = git(v, "rev-parse", "HEAD").strip()
    assert first != second
    assert git(v, "rev-parse", "HEAD~1").strip() == first, "the first commit was not rewritten"
    assert " M Other/Position.md" in git(v, "status", "--porcelain")

    src = (HOOKS / "commit.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    body = code.split('"""', 2)[-1]                       # past the module docstring
    for forbidden in ('"add", "-A"', '"add", "-a"', '"add", "."', '"--amend"', '"commit", "-a"'):
        assert forbidden not in body, forbidden


def test_the_commit_pathspec_leaves_a_foreign_staged_file_staged(world):
    """A sibling session can have something staged in the shared index. Our commit's pathspec
    means it is neither committed nor unstaged: we simply leave it alone."""
    v = world["vault"]
    (v / "Other" / "Position.md").write_text("staged by someone else\n")
    git(v, "add", "--", "Other/Position.md")
    (v / "MyProject" / "Position.md").write_text("ours\n")
    stop(world)
    assert head_files(v) == ["MyProject/Position.md"]
    assert "M  Other/Position.md" in git(v, "status", "--porcelain")   # still staged, uncommitted
