"""MERGESHA-1 — a claimed merge sha is checked, not believed.

Every answer here comes from a REAL git repository built in tmp_path: a `main` with one commit, a
branch with one commit that was never merged, and a vault repository beside it. Nothing about git
is mocked, because the thing under test is whether the right git question is asked and its exit
code read the right way round.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
sys.path.insert(0, str(HOOKS))

ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env={**os.environ, **ENV}).stdout.strip()


def make_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    for name, body in files.items():
        (path / name).write_text(body, encoding="utf-8")
    git(path, "add", "--", *files)
    git(path, "commit", "-q", "-m", "first")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    merged = make_repo(repo, {"a.txt": "a"})
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    git(repo, "add", "--", "b.txt")
    git(repo, "commit", "-q", "-m", "unmerged")
    unmerged = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    vault = tmp_path / "vault"
    vsha = make_repo(vault, {"Position.md": "p", "Canon.md": "c"})
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(vault))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "config.json"))
    import importlib
    import config, common, mergesha                                      # noqa: PLC0415
    importlib.reload(config); importlib.reload(common); importlib.reload(mergesha)
    assert Path(common.VAULT) == vault, "the sandbox vault is not bound"
    return {"repo": repo, "vault": vault, "merged": merged, "unmerged": unmerged, "vsha": vsha,
            "mod": mergesha}


# ------------------------------------------------------------------ the three answers

def test_an_ancestor_of_main_is_on_main(world):
    r = world["mod"].verify(world["merged"][:8], world["repo"])
    assert r["state"] == world["mod"].ON


def test_a_commit_on_an_unmerged_branch_is_NOT_on_main(world):
    r = world["mod"].verify(world["unmerged"][:8], world["repo"])
    assert r["state"] == world["mod"].NOT_ON


def test_a_sha_git_does_not_have_is_unknown(world):
    assert world["mod"].verify("deadbeef", world["repo"])["state"] == world["mod"].UNKNOWN


def test_a_string_that_is_not_a_sha_is_unknown_and_never_reaches_git(world, monkeypatch):
    """`HEAD`, `main~1` and `--all` are all things git would happily resolve. A claim is a sha or
    it is nothing — and a leading dash must never become a git option."""
    calls = []
    monkeypatch.setattr(world["mod"], "_git", lambda *a: calls.append(a))
    for bad in ("HEAD", "main~1", "--all", "abc12", ""):
        assert world["mod"].verify(bad, world["repo"])["state"] == world["mod"].UNKNOWN
    assert calls == []


def test_a_ref_that_does_not_exist_is_unknown_not_NOT_on(world):
    """`merge-base --is-ancestor` exits 128 for a missing ref. Reading every non-zero exit as
    "not on main" would report a healthy merge as missing in a repo whose trunk is not `main`."""
    r = world["mod"].verify(world["merged"][:8], world["repo"], ref="trunk")
    assert r["state"] == world["mod"].UNKNOWN


# ------------------------------------------------------------------ vault commits name their files

def test_a_vault_commit_lists_the_files_it_touched(world):
    r = world["mod"].verify(world["vsha"][:8], world["vault"])
    assert r["state"] == world["mod"].ON
    assert sorted(r["files"]) == ["Canon.md", "Position.md"]
    assert "it touched: " in world["mod"].line(r)


def test_a_repo_commit_does_not(world):
    assert world["mod"].verify(world["merged"][:8], world["repo"])["files"] is None


def test_a_sha_the_repo_does_not_know_is_looked_up_in_the_vault(world):
    r = world["mod"].resolve(world["vsha"][:8], str(world["repo"] / "a.txt"))
    assert r["state"] == world["mod"].ON and Path(r["repo"]) == world["vault"]
    r = world["mod"].resolve(world["unmerged"][:8], str(world["repo"]))
    assert r["state"] == world["mod"].NOT_ON and Path(r["repo"]) == world["repo"]


def test_the_vault_is_not_asked_when_the_repo_already_answered(world, monkeypatch):
    """Repo first, and only on UNKNOWN the vault: a short sha can name a commit in both, and the
    reader is in the repo."""
    real, asked = world["mod"].verify, []
    monkeypatch.setattr(world["mod"], "verify", lambda sha, repo, ref="main": asked.append(Path(repo)) or real(sha, repo, ref))
    world["mod"].resolve(world["unmerged"][:8], str(world["repo"]))
    assert asked == [world["repo"]]


def test_a_blob_or_tree_sha_is_not_a_merge(world):
    """A sha of a file or directory object is not a commit; git refuses the ancestor question."""
    blob = git(world["repo"], "rev-parse", "HEAD:a.txt")
    assert world["mod"].verify(blob[:10], world["repo"])["state"] == world["mod"].UNKNOWN


# ------------------------------------------------------------------ reading claims out of a message

@pytest.mark.parametrize("text,want", [
    ("MERGED 1a2b3c4d on main", ["1a2b3c4d"]),
    ("merged `abcdef1`, row mark `1234567`", ["abcdef1"]),
    ("Merged: 0123456789abcdef", ["0123456789abcdef"]),
    ("merged abcdef1 and merged abcdef1 again", ["abcdef1"]),
])
def test_claims_are_read_in_the_forms_reports_use(world, text, want):
    assert world["mod"].claims(text) == want


@pytest.mark.parametrize("text", [
    "unmerged abcdef1", "not-merged abcdef1", "merged abc12", "merged-by abcdef1",
    "merged abcdef1xyz", "the row mark is 1234567",
])
def test_non_claims_are_not_read(world, text):
    assert world["mod"].claims(text) == []


def test_only_a_message_from_another_session_is_audited(world):
    body = f"MERGED {world['unmerged'][:8]}"
    person = {"prompt": body, "cwd": str(world["repo"])}
    assert world["mod"].from_prompt(person) is None
    peer = {"prompt": "Another Claude session sent a message:\n<cross-session-message "
                      f'from-name="seat">{body}</cross-session-message>', "cwd": str(world["repo"])}
    out = world["mod"].from_prompt(peer)
    assert out and "NOT on main" in out and world["unmerged"][:8] in out


# ------------------------------------------------------------------ the two doors, as processes

def run(argv, cwd, stdin=None, env_extra=None):
    env = {**os.environ, **ENV, **(env_extra or {})}
    return subprocess.run([sys.executable, "-B", *argv], cwd=cwd, input=stdin, capture_output=True,
                          text=True, env=env, timeout=30)


def test_the_cli_answers_in_one_line_with_an_exit_code_per_answer(world):
    cli = str(HOOKS / "mergesha.py")
    for sha, rc, word in ((world["merged"][:8], 0, "on main"),
                          (world["unmerged"][:8], 1, "NOT on main"),
                          ("deadbeef", 2, "unknown object")):
        p = run([cli, "verify-merge", sha, str(world["repo"])], world["repo"])
        assert p.returncode == rc, p.stderr
        assert p.stdout.count("\n") == 1 and f"— {word} " in p.stdout


def test_the_prompt_chore_adds_context_and_never_blocks(world):
    chore = str(HOOKS / "chore.py")
    msg = ("Another Claude session sent a message:\n<cross-session-message from-name=\"b\">"
           f"MERGED {world['unmerged'][:8]}</cross-session-message>")
    p = run([chore, "mergesha"], world["repo"],
            stdin=json.dumps({"prompt": msg, "cwd": str(world["repo"]), "session_id": "s"}))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    assert "NOT on main" in out["additionalContext"]
    assert "decision" not in p.stdout and "block" not in p.stdout
    quiet = run([chore, "mergesha"], world["repo"],
                stdin=json.dumps({"prompt": "hello", "cwd": str(world["repo"]), "session_id": "s"}))
    assert quiet.returncode == 0 and quiet.stdout.strip() == ""
