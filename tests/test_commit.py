"""Tests for the Stop-time auto-commit — the TOUCHED-SET rule.

Every case runs against a git repository created in tmp_path and pointed at by
GEDAECHTNIS_VAULT, with the state directory redirected too: the suite never reads or writes a
real vault (a suite that writes the application's real state makes its own verdict depend on the
machine — and here that state is git history, which is not undoable).

The touch is recorded the way it is in production, by running `chore.py write`, so these tests
also prove the two halves fit: a hook that recorded touches nobody read, or a commit hook reading
a key nobody wrote, would pass a test that stubbed the record in.

Each rule gets both controls: a touched declared path IS committed, an untouched or undeclared
one is NOT; a marker that resolves DOES commit, an absent or malformed one commits nothing and
says so in the log.
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


def hook(w, script, argv, payload, env=None, timeout=60):
    p = subprocess.run([sys.executable, str(HOOKS / script)] + ([argv] if argv else []),
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=env or w["env"], timeout=timeout)
    assert p.returncode == 0, p.stderr
    return p


def write(w, rel: str, text: str, sid="s1", cwd=None):
    """Write a vault file AS A SESSION DOES: the Write tool, then the PostToolUse chore that
    records the touch. Nothing here reaches into the state file by hand."""
    path = w["vault"] / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    hook(w, "chore.py", "write", {"cwd": str(cwd or w["repo"]), "session_id": sid,
                                  "tool_name": "Write",
                                  "tool_input": {"file_path": str(path), "content": text}})
    return path


def stop(w, cwd=None, env=None, sid="s1"):
    p = hook(w, "commit.py", None, {"cwd": str(cwd or w["repo"]), "session_id": sid}, env)
    assert p.stdout.strip() == "", "a Stop hook must not talk over the session's last message"
    return p


def head_subject(vault: Path) -> str:
    return git(vault, "log", "-1", "--format=%s").strip()


def head_files(vault: Path) -> list[str]:
    return [l for l in git(vault, "show", "--name-only", "--format=", "HEAD").splitlines() if l]


def commit_log(w) -> str:
    f = w["state"] / "commit.log"
    return f.read_text(encoding="utf-8") if f.is_file() else ""


def touched(w, sid="s1") -> list[str]:
    doc = json.loads((w["state"] / f"session-start-{sid}.json").read_text())
    return doc["touched"]


# ------------------------------------------------------------------ the positive control ----
def test_a_touched_declared_path_is_committed(world):
    v = world["vault"]
    write(world, "MyProject/Position.md", "shipped the thing\n")
    write(world, "MyProject/Canon.md", "## decided\n")                 # untracked, also ours
    assert touched(world) == ["MyProject/Position.md", "MyProject/Canon.md"]
    stop(world)
    assert head_files(v) == ["MyProject/Canon.md", "MyProject/Position.md"]
    assert head_subject(v).startswith("session-end auto-commit: [MY-PROJECT] ")
    assert "committed=2" in commit_log(world)


def test_the_commit_identity_is_the_machine_identity(world):
    write(world, "MyProject/Position.md", "changed\n")
    stop(world)
    assert git(world["vault"], "log", "-1", "--format=%an <%ae>").strip() == "Gedächtnis <gedaechtnis@local>"


def test_a_first_write_into_an_untracked_region_is_committed(world):
    """git reports a wholly untracked directory as ONE `?? dir/` record and never names the
    files in it, so a naive dirty-set intersection would drop a new region's first entry."""
    (world["repo"] / ".atlas-lane").write_text(
        "lane: MY-PROJECT\npath: MyProject/\npath: NewRegion/\n")
    write(world, "NewRegion/Canon.md", "## the first decision\n")
    stop(world)
    assert head_files(world["vault"]) == ["NewRegion/Canon.md"]


def test_a_touched_path_that_is_now_deleted_is_committed_as_a_deletion(world):
    v = world["vault"]
    write(world, "MyProject/Position.md", "about to go\n")
    (v / "MyProject" / "Position.md").unlink()
    stop(world)
    assert head_files(v) == ["MyProject/Position.md"]
    assert git(v, "ls-files", "MyProject/Position.md").strip() == ""


# ------------------------------------------------------------------ the negative controls ----
def test_a_dirty_UNdeclared_path_is_left_untouched(world):
    """The partition: another lane's region is not this lane's to commit, even when this session
    wrote it (the write itself is recorded in that region's Inbox by a different hook)."""
    v = world["vault"]
    write(world, "MyProject/Position.md", "ours\n")
    (v / "Other" / "Position.md").write_text("theirs, mid-edit\n")
    (v / "Other" / "Notes.md").write_text("theirs, untracked\n")
    stop(world)
    assert head_files(v) == ["MyProject/Position.md"]
    porcelain = git(v, "status", "--porcelain")
    assert " M Other/Position.md" in porcelain and "?? Other/Notes.md" in porcelain


def test_a_dirty_file_this_session_never_touched_is_NOT_committed(world):
    """★ The collision this rule exists for. Session A is half-way through an Errata entry and
    idles; session B, which never opened that file, stops. B's commit must not carry A's
    unfinished sentence — same lane, same declared prefix, and only the touched set can tell
    them apart."""
    v = world["vault"]
    write(world, "MyProject/Errata.md", "## A half-written entry\n\nThe mechanism is that", sid="A")
    write(world, "MyProject/Position.md", "B's own work\n", sid="B")
    stop(world, sid="B")
    assert head_files(v) == ["MyProject/Position.md"]
    assert "Errata.md" not in git(v, "show", "--name-only", "--format=", "HEAD")
    assert " M MyProject/Errata.md" in git(v, "status", "--porcelain")   # still A's to finish


def test_two_sessions_with_disjoint_touched_sets_make_two_clean_commits(world):
    v = world["vault"]
    write(world, "MyProject/Position.md", "A's work\n", sid="A")
    write(world, "MyProject/Canon.md", "## B's decision\n", sid="B")
    stop(world, sid="A")
    assert head_files(v) == ["MyProject/Position.md"]
    stop(world, sid="B")
    assert head_files(v) == ["MyProject/Canon.md"]
    assert git(v, "status", "--porcelain").strip() == ""
    assert len(git(v, "log", "--format=%h").splitlines()) == 3          # root + one each


def test_the_second_stopper_finds_nothing_left_and_says_so(world):
    """Both sessions edited the same file: the first to stop carries both edits (git cannot
    split them), and the second logs one line rather than committing an empty change."""
    v = world["vault"]
    write(world, "MyProject/Position.md", "A's line\n", sid="A")
    write(world, "MyProject/Position.md", "A's line\nB's line\n", sid="B")
    stop(world, sid="B")
    assert head_files(v) == ["MyProject/Position.md"]
    before = git(v, "rev-parse", "HEAD").strip()
    stop(world, sid="A")
    assert git(v, "rev-parse", "HEAD").strip() == before
    assert "nothing-left-to-commit" in commit_log(world)


def test_no_marker_commits_nothing(world):
    v = world["vault"]
    bare = world["tmp"] / "unmarked"
    bare.mkdir()
    write(world, "MyProject/Position.md", "changed\n", cwd=bare)
    before = head_subject(v)
    stop(world, cwd=bare)
    assert head_subject(v) == before
    assert "lane=UNKNOWN" in commit_log(world) and "action=staged-nothing" in commit_log(world)
    assert " M MyProject/Position.md" in git(v, "status", "--porcelain")


def test_a_dotdot_path_in_the_marker_rejects_the_WHOLE_marker(world):
    """One bad `path:` line invalidates the marker entire — including its good lines — because a
    marker that can name a directory outside the vault cannot be trusted about any of them."""
    v = world["vault"]
    write(world, "MyProject/Position.md", "changed\n")
    (world["repo"] / ".atlas-lane").write_text(
        "lane: MY-PROJECT\npath: MyProject/\npath: ../elsewhere\n")
    before = head_subject(v)
    stop(world)
    assert head_subject(v) == before
    assert "lane=UNKNOWN" in commit_log(world)
    assert " M MyProject/Position.md" in git(v, "status", "--porcelain")


def test_an_absolute_path_in_the_marker_rejects_the_WHOLE_marker(world):
    v = world["vault"]
    write(world, "MyProject/Position.md", "changed\n")
    (world["repo"] / ".atlas-lane").write_text("lane: MY-PROJECT\npath: /etc\n")
    before = head_subject(v)
    stop(world)
    assert head_subject(v) == before and "lane=UNKNOWN" in commit_log(world)


def test_a_session_that_wrote_nothing_produces_no_commit_and_no_noise(world):
    v = world["vault"]
    before = head_subject(v)
    stop(world)
    assert head_subject(v) == before
    assert commit_log(world) == "", "a session that wrote nothing says nothing at all"


def test_auto_commit_can_be_switched_off(world):
    v = world["vault"]
    write(world, "MyProject/Position.md", "changed\n")
    before = head_subject(v)
    stop(world, env=dict(world["env"], GEDAECHTNIS_AUTO_COMMIT="0"))
    assert head_subject(v) == before and "auto_commit=off" in commit_log(world)


# ------------------------------------------------------- what the hook is forbidden to do ----
def test_the_hook_never_sweeps_and_never_amends(world):
    """Proven two ways: from git itself (a second commit is a NEW commit, and a file outside the
    partition that was dirty before the run is still dirty after it), and from the source, which
    may not contain the forms the vault's git law forbids."""
    v = world["vault"]
    write(world, "MyProject/Position.md", "one\n")
    stop(world)
    first = git(v, "rev-parse", "HEAD").strip()
    write(world, "MyProject/Position.md", "two\n")
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
    """A sibling session can have something staged in the shared index, INSIDE this lane's own
    declared prefix. Our commit names its own paths, so that file is neither committed nor
    unstaged: we simply leave it alone."""
    v = world["vault"]
    (v / "MyProject" / "Errata.md").write_text("staged by the session next door\n")
    git(v, "add", "--", "MyProject/Errata.md")
    write(world, "MyProject/Position.md", "ours\n")
    stop(world)
    assert head_files(v) == ["MyProject/Position.md"]
    assert "M  MyProject/Errata.md" in git(v, "status", "--porcelain")  # still staged, uncommitted
