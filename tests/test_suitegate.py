"""SUITEGATE-1 — a merge is called green only after the full suite ran on the tree being shipped.

Every repository here is a real git repository in tmp_path, and the RECORDER is exercised by a
real `pytest` run in a subprocess, through a root conftest shaped like this repository's own. The
gate half is driven through the Bash door's own entry (`gate.rule_suite_gate`), so `cd` and `-C`
are resolved by the same walker every other git door uses.
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

GITENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
          "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env={**os.environ, **GITENV}).stdout.strip()


def commit(repo: Path, name: str, body: str) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(body, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    base = commit(repo, "gedaechtnis/tests/test_x.py", "def test_x():\n    assert True\n")
    git(repo, "checkout", "-q", "-b", "feature")
    tip = commit(repo, "feature.txt", "f")
    git(repo, "checkout", "-q", "main")
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    import importlib
    import config, gate, suitegate                                        # noqa: PLC0415
    importlib.reload(config); importlib.reload(suitegate)

    def on():
        cfg.write_text(json.dumps({"suite_gate": True}), encoding="utf-8")

    def record(**kw):
        f = suitegate.record_file(repo)
        f.parent.mkdir(parents=True, exist_ok=True)
        r = {"sha": tip, "tree": git(repo, "rev-parse", f"{tip}^{{tree}}"), "dirty": False,
             "partial": [], "passed": 10, "failed": 0, "errors": 0, "skipped": 0, "nodes": 10,
             "exit": 0, "finished": "2026-09-22T12:00:00", **kw}
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r) + "\n")

    def door(cmd):
        return gate.rule_suite_gate(cmd, str(repo))

    return {"repo": repo, "tip": tip, "base": base, "on": on, "record": record, "door": door,
            "mod": suitegate}


# ------------------------------------------------------------------ positive

def test_a_green_full_run_on_the_shipped_commit_lets_the_merge_through(world):
    world["on"](); world["record"]()
    assert world["door"]("git merge --ff-only feature") is None


def test_a_green_run_on_a_commit_with_the_SAME_TREE_counts(world):
    """A rebase over commits that change nothing in the tree gives a new sha and the same code."""
    world["on"](); world["record"](sha="0" * 40)
    assert world["door"]("git merge feature") is None


# ------------------------------------------------------------------ negatives

def test_no_record_is_refused_and_the_refusal_names_the_commit_it_wanted(world):
    world["on"]()
    r = world["door"]("git merge feature")
    assert r and "No full-suite run is recorded" in r and world["tip"][:10] in r


def test_a_run_with_failures_is_refused_with_its_counts(world):
    world["on"](); world["record"](failed=2, passed=8)
    r = world["door"]("git merge feature")
    assert r and "2 failed, 0 errors" in r


def test_a_run_with_only_errors_is_refused(world):
    """An error is a test that never reached its assertion — setup broke, or a guard fixture fired.
    A run of all passes and one error is not green."""
    world["on"](); world["record"](errors=1)
    r = world["door"]("git merge feature")
    assert r and "0 failed, 1 errors" in r


def test_a_record_that_names_the_commit_counts_even_without_its_tree(world):
    """The tree field is a second way in, not a requirement: a record whose tree could not be read
    still names its commit."""
    world["on"](); world["record"](tree=None)
    assert world["door"]("git merge feature") is None


def test_a_run_for_a_DIFFERENT_tree_does_not_count(world):
    world["on"]()
    world["record"](sha=world["base"], tree=git(world["repo"], "rev-parse", "main^{tree}"))
    r = world["door"]("git merge feature")
    assert r and "No full-suite run is recorded" in r and world["tip"][:10] in r


def test_a_partial_run_is_refused(world):
    world["on"](); world["record"](partial=["-k"])
    r = world["door"]("git merge feature")
    assert r and "partial (-k)" in r


def test_a_run_on_modified_files_is_refused(world):
    world["on"](); world["record"](dirty=True)
    r = world["door"]("git merge feature")
    assert r and "tracked files modified" in r


def test_a_run_that_collected_nothing_is_not_green(world):
    world["on"](); world["record"](passed=0, nodes=0)
    assert world["door"]("git merge feature")


def test_a_run_where_every_test_was_SKIPPED_is_not_green(world):
    """Reviewer's must-fix: nodes collected, nothing failed, nothing ran. A whole-suite skip (a broken
    environment gate) would otherwise open the gate with zero test bodies executed."""
    world["on"](); world["record"](passed=0, skipped=10, nodes=10)
    r = world["door"]("git merge feature")
    assert r and "0 passed of 10 collected" in r


@pytest.mark.parametrize("cmd", ['git merge -m "merge feature" feature',
                                 "git merge -F msg.txt feature",
                                 "git merge --no-ff --no-edit feature"])
def test_an_options_VALUE_is_not_read_as_a_rev(world, cmd):
    """`-m "merge feature"` must not ship a rev called `merge feature`; the rev is `feature` alone."""
    assert world["mod"].shipped_revs(cmd) == ["feature"]


def test_a_later_green_run_clears_an_earlier_red_one(world):
    world["on"](); world["record"](failed=1); world["record"]()
    assert world["door"]("git merge feature") is None


# ------------------------------------------------------------------ what the door leaves alone

def test_off_by_default(world):
    assert world["door"]("git merge feature") is None


def test_the_stated_escape_passes(world):
    world["on"]()
    assert world["door"]("SUITE_GATE_ALLOW=1 git merge feature") is None


def test_merge_abort_and_other_git_commands_are_not_gated(world):
    world["on"]()
    for cmd in ("git merge --abort", "git status", "git log --oneline -3", "git rebase main"):
        assert world["door"](cmd) is None, cmd


def test_push_ships_HEAD(world):
    world["on"]()
    git(world["repo"], "checkout", "-q", "feature")
    assert world["door"]("git push origin feature")
    world["record"]()
    assert world["door"]("git push origin feature") is None


@pytest.mark.parametrize("cmd,want", [
    ("git push origin feature:main", ["feature"]),
    ("git push -u origin +feature:main", ["feature"]),
    ("git push origin HEAD:main", ["HEAD"]),
    ("git push origin feature", ["feature"]),
    ("git push", ["HEAD"]),
    ("git push -o ci.skip origin feature:main", ["feature"]),
    ("git push origin :old-branch", None),
])
def test_push_gates_the_refspec_SOURCE(world, cmd, want):
    """Reviewer's must-fix: `feature:main` ships `feature`. Gating HEAD there let a red branch
    through while the session stood on a green one."""
    assert world["mod"].shipped_revs(cmd) == want


def test_pushing_a_red_branch_from_a_green_HEAD_is_refused(world):
    world["on"](); world["record"](sha=world["base"], tree=git(world["repo"], "rev-parse", "main^{tree}"))
    assert world["door"]("git push origin main") is None                 # HEAD = main, green
    r = world["door"]("git push origin feature:main")
    assert r and world["tip"][:10] in r


def test_a_repo_without_the_plugin_tests_is_not_gated(world, tmp_path):
    world["on"]()
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(other, "a.txt", "a")
    import gate                                                          # noqa: PLC0415
    assert gate.rule_suite_gate(f"git -C {other} merge main", str(other)) is None


def test_cd_and_dash_C_are_followed_to_the_repo(world, tmp_path):
    world["on"]()
    import gate                                                          # noqa: PLC0415
    assert gate.rule_suite_gate(f"cd {world['repo']} && git merge feature", str(tmp_path))
    assert gate.rule_suite_gate(f"git -C {world['repo']} merge feature", str(tmp_path))


# ------------------------------------------------------------------ the recorder, in a real pytest run

CONFTEST = f'''
import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location("gs", {str(HOOKS / "suitegate.py")!r})
_mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_mod)
pytest_sessionfinish = _mod.pytest_sessionfinish
'''


def run_pytest(repo: Path, *args: str):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                          cwd=repo, capture_output=True, text=True, timeout=120,
                          env={**os.environ, **GITENV})


def test_the_recorder_writes_a_full_green_run_the_gate_accepts(world):
    repo = world["repo"]
    (repo / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    git(repo, "checkout", "-q", "feature")
    sha = commit(repo, "conftest.py", CONFTEST)
    commit(repo, "pytest.ini", "[pytest]\n")
    sha = git(repo, "rev-parse", "HEAD")
    p = run_pytest(repo)
    assert p.returncode == 0, p.stdout + p.stderr
    rows = world["mod"].records(repo)
    assert rows[-1]["sha"] == sha and rows[-1]["partial"] == [] and rows[-1]["dirty"] is False
    assert (rows[-1]["passed"], rows[-1]["failed"], rows[-1]["nodes"]) == (1, 0, 1)
    git(repo, "checkout", "-q", "main")
    world["on"]()
    assert world["door"]("git merge feature") is None


@pytest.mark.parametrize("args,why", [(("gedaechtnis/tests/test_x.py",), "paths given"),
                                      (("-k", "x"), "-k"), (("-x",), "-x/--maxfail"),
                                      (("--ignore", "nothing"), "--ignore")])
def test_the_recorder_marks_a_narrowed_run_partial(world, args, why):
    repo = world["repo"]
    (repo / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    run_pytest(repo, *args)
    assert why in world["mod"].records(repo)[-1]["partial"]


def test_the_recorder_marks_uncommitted_changes_dirty(world):
    repo = world["repo"]
    (repo / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (repo / "gedaechtnis/tests/test_x.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")
    run_pytest(repo)
    assert world["mod"].records(repo)[-1]["dirty"] is True


def test_the_recorder_never_raises(world):
    class Broken:
        @property
        def config(self):
            raise RuntimeError("boom")
    assert world["mod"].pytest_sessionfinish(Broken(), 0) is None
