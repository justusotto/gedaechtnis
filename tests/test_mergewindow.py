"""MERGEWINDOW-1 — one session moves `main` at a time. Every door has its positive control (the
merge without the window is refused) and its negative controls (the holder merges; a repository
without `.merge-window`, a feature branch, and `--abort` are never touched). The hooks run as
subprocesses against a scratch repository, with the state and config redirected into tmp_path.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import mergewindow  # noqa: E402


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def w(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / ".merge-window").write_text("one session moves main at a time\n")
    git(repo, "add", "--", ".merge-window"); git(repo, "commit", "-q", "-m", "root")
    git(repo, "branch", "feature")
    vault = tmp_path / "Vault"; vault.mkdir()
    cfg = tmp_path / "config.json"; cfg.write_text("{}")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_CONFIG=str(cfg), GEDAECHTNIS_USER_MEMORY=str(tmp_path / "none.md"),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "none-roster.md"))
    return dict(repo=repo, env=env)


def hook(w, script, which, cmd, sid="me", response=None):
    payload = {"cwd": str(w["repo"]), "tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": sid}
    if response is not None:
        payload["tool_response"] = response
    p = subprocess.run([sys.executable, str(HOOKS / script), which], input=json.dumps(payload),
                       capture_output=True, text=True, env=w["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def decision(res):
    return (res or {}).get("hookSpecificOutput", {}).get("permissionDecision")


def test_a_merge_onto_main_WITHOUT_the_window_is_refused(w):
    out = hook(w, "gate.py", "bash", "git merge --ff-only feature")
    assert decision(out) == "deny" and "grant me" in json.dumps(out), out


def test_the_HOLDER_merges(w):
    assert mergewindow.grant("me", w["repo"])[0] == 0
    assert hook(w, "gate.py", "bash", "git merge --ff-only feature") is None


def test_another_sessions_window_refuses_and_names_the_holder(w):
    mergewindow.grant("builder-28", w["repo"])
    out = hook(w, "gate.py", "bash", "git rebase feature")
    assert decision(out) == "deny" and "held by session builder-28" in json.dumps(out), out


def test_an_EXPIRED_window_is_no_window(w):
    mergewindow.grant("me", w["repo"], now=time.time() - (mergewindow.TTL_MIN * 60 + 5))
    assert decision(hook(w, "gate.py", "bash", "git pull")) == "deny"


def test_a_repository_without_the_marker_is_never_gated(w):
    git(w["repo"], "rm", "-q", "--", ".merge-window"); git(w["repo"], "commit", "-q", "-m", "off")
    assert hook(w, "gate.py", "bash", "git merge --ff-only feature") is None


def test_a_merge_on_a_FEATURE_branch_is_not_gated(w):
    git(w["repo"], "checkout", "-q", "feature")
    assert hook(w, "gate.py", "bash", "git merge main") is None


def test_finishing_an_operation_is_never_refused(w):
    for cmd in ("git merge --abort", "git rebase --continue", "git rebase --skip", "git merge --quit"):
        assert hook(w, "gate.py", "bash", cmd) is None, cmd


def test_a_prefixed_merge_is_gated_too(w):
    assert decision(hook(w, "gate.py", "bash", "env X=1 git merge --ff-only feature")) == "deny"


def test_the_door_reads_the_repo_behind_dash_C(w, tmp_path):
    other = tmp_path / "elsewhere"; other.mkdir()
    out = hook(w, "gate.py", "bash", f"cd {other} && git -C {w['repo']} merge --ff-only feature")
    assert decision(out) == "deny", out


# ---- the release, after `verify-merge` said "on main" ----

def verified(sha, where):
    return {"stdout": f"{sha} — {where} (repo /x)", "stderr": "", "interrupted": False}


def test_verify_merge_ON_MAIN_releases_the_holders_window(w):
    mergewindow.grant("me", w["repo"])
    hook(w, "chore.py", "bash", "python3 gedaechtnis/hooks/mergesha.py verify-merge abc1234", response=verified("abc1234", "on main"))
    assert mergewindow.read(w["repo"]) is None


def test_verify_merge_NOT_on_main_keeps_the_window(w):
    mergewindow.grant("me", w["repo"])
    hook(w, "chore.py", "bash", "python3 gedaechtnis/hooks/mergesha.py verify-merge abc1234", response=verified("abc1234", "NOT on main"))
    assert mergewindow.read(w["repo"])["holder"] == "me"


def test_verify_merge_never_releases_ANOTHER_sessions_window(w):
    mergewindow.grant("builder-28", w["repo"])
    hook(w, "chore.py", "bash", "python3 gedaechtnis/hooks/mergesha.py verify-merge abc1234", response=verified("abc1234", "on main"))
    assert mergewindow.read(w["repo"])["holder"] == "builder-28"


# ---- the CLI ----

def test_grant_refuses_while_another_holds_and_release_refuses_a_foreign_window(w):
    assert mergewindow.main(["grant", "a", str(w["repo"])]) == 0
    assert mergewindow.main(["grant", "b", str(w["repo"])]) == 3
    assert mergewindow.main(["release", "b", str(w["repo"])]) == 5
    assert mergewindow.main(["release", "a", str(w["repo"])]) == 0
    assert mergewindow.main(["grant", "b", str(w["repo"])]) == 0


def test_the_window_is_shared_by_every_worktree_of_the_repo(w, tmp_path):
    wt = tmp_path / "wt"
    git(w["repo"], "worktree", "add", "-q", str(wt), "feature")
    mergewindow.grant("me", wt)
    assert mergewindow.read(w["repo"])["holder"] == "me"


# ---- MERGEWINDOW-2: the release reads the command the way the doors do ----
# Measured 2026-09-24 ~02:10: builder 28's `verify-merge 37cce85a; echo rc=$?` (rc 0) left its window
# held until expiry — a regex took `37cce85a;` as the sha and `echo` as the repository. These drive
# the REAL `mergesha.py` against the scratch repo and hand its real stdout to the chore.

MERGESHA = HOOKS / "mergesha.py"


def real_verify(w, sha, tail=""):
    p = subprocess.run([sys.executable, str(MERGESHA), "verify-merge", sha], cwd=w["repo"],
                       capture_output=True, text=True, env=w["env"], timeout=30)
    return {"stdout": p.stdout + tail, "stderr": "", "interrupted": False}, p.returncode


def status(w, capsys):
    mergewindow.main(["status", str(w["repo"])])
    return capsys.readouterr().out.strip()


def merged_sha(w):
    git(w["repo"], "checkout", "-q", "feature")
    (w["repo"] / "f.txt").write_text("f\n")
    git(w["repo"], "add", "--", "f.txt"); git(w["repo"], "commit", "-q", "-m", "f")
    sha = git(w["repo"], "rev-parse", "--short=8", "HEAD")
    git(w["repo"], "checkout", "-q", "main"); git(w["repo"], "merge", "-q", "--ff-only", "feature")
    return sha


@pytest.mark.parametrize("shape", [
    "python3 {m} verify-merge {sha}; echo rc=$?; git log --format=%h -1",       # builder 28's
    "python3 {m} verify-merge {sha} 2>&1 | tail -5",                             # the seat's
    "cd {repo} && python3 {m} verify-merge {sha} > /dev/null; echo done",
    "python3 {m} verify-merge {sha} {repo}; echo rc=$?",
])
def test_MW2_POSITIVE_grant_then_verify_merge_of_a_merged_sha_leaves_the_window_free(w, capsys, shape):
    sha = merged_sha(w)
    assert mergewindow.grant("me", w["repo"])[0] == 0
    resp, rc = real_verify(w, sha, tail="rc=0\n")
    assert rc == 0 and f"{sha} — on main" in resp["stdout"]
    hook(w, "chore.py", "bash", shape.format(m=MERGESHA, sha=sha, repo=w["repo"]), response=resp)
    assert status(w, capsys) == "free"


def test_MW2_NEGATIVE_verify_merge_of_an_UNMERGED_sha_leaves_it_held(w, capsys):
    git(w["repo"], "checkout", "-q", "feature")
    (w["repo"] / "g.txt").write_text("g\n")
    git(w["repo"], "add", "--", "g.txt"); git(w["repo"], "commit", "-q", "-m", "g")
    sha = git(w["repo"], "rev-parse", "--short=8", "HEAD")
    git(w["repo"], "checkout", "-q", "main")
    mergewindow.grant("me", w["repo"])
    resp, rc = real_verify(w, sha, tail="rc=1\n")
    assert rc == 1
    hook(w, "chore.py", "bash", f"python3 {MERGESHA} verify-merge {sha}; echo rc=$?", response=resp)
    assert status(w, capsys).startswith("held by session me")


def test_MW2_NEGATIVE_on_main_for_ANOTHER_sha_than_the_one_verified_leaves_it_held(w, capsys):
    sha = merged_sha(w)
    mergewindow.grant("me", w["repo"])
    resp, _ = real_verify(w, sha)
    hook(w, "chore.py", "bash", f"python3 {MERGESHA} verify-merge deadbee1; cat /x", response=resp)
    assert status(w, capsys).startswith("held by session me")


def test_MW2_W7_a_FOREIGN_holder_is_never_released_and_draws_no_notice(w, capsys):
    """HANDOFF-27-3's W7: `release()` refuses a foreign window by itself, so dropping the holder
    check changed no state and the mutant stayed green. Its only effect is a notice — asserted."""
    sha = merged_sha(w)
    mergewindow.grant("builder-28", w["repo"])
    resp, _ = real_verify(w, sha)
    out = hook(w, "chore.py", "bash", f"python3 {MERGESHA} verify-merge {sha}; echo rc=$?", response=resp)
    assert status(w, capsys).startswith("held by session builder-28")
    assert "merge window" not in json.dumps(out or {})


# ---- MERGEWINDOW-2: commands that move main without checking it out ----

@pytest.mark.parametrize("cmd", ["git push . feature:main", "git push . +feature:refs/heads/main",
                                 "git fetch . feature:main", "git branch -f main feature",
                                 "git branch --force main feature", "git branch -M feature main",
                                 "git update-ref refs/heads/main feature", "git branch -f 'main' feature",
                                 "git checkout -B main feature", "git switch -C main feature"])
def test_MW2_a_direct_move_of_main_WITHOUT_the_window_is_refused_whatever_is_checked_out(w, cmd):
    git(w["repo"], "checkout", "-q", "feature")
    out = hook(w, "gate.py", "bash", cmd)
    assert decision(out) == "deny" and "grant me" in json.dumps(out), cmd


@pytest.mark.parametrize("cmd", ["git push . feature:main", "git branch -f main feature",
                                 "git update-ref refs/heads/main feature"])
def test_MW2_the_HOLDER_moves_main_directly(w, cmd):
    git(w["repo"], "checkout", "-q", "feature")
    mergewindow.grant("me", w["repo"])
    assert hook(w, "gate.py", "bash", cmd) is None, cmd


@pytest.mark.parametrize("cmd", ["git push origin main", "git push . main:feature",
                                 "git fetch . main:feature", "git branch -f other main",
                                 "git branch main-2", "git update-ref refs/heads/other main",
                                 "git branch -d feature", "git branch -M main other",
                                 "git checkout -b other main", "git switch -c other main",
                                 "git checkout main"])
def test_MW2_commands_that_do_not_move_main_are_not_gated(w, cmd):
    git(w["repo"], "checkout", "-q", "feature")
    assert decision(hook(w, "gate.py", "bash", cmd)) != "deny", cmd
