"""WTSWEEP-1 — the Stop-time git-worktree sweep, and the placement deny.

Real `git worktree add` trees throughout, for the reason `test_region_worktree.py` established:
the behaviour under test lives in the difference between a `.git` DIRECTORY and a `.git` FILE, and
only real git produces the second. A fixture that imitated one would be testing the fixture.

The two conditions that are NOT in the row's original text are the ones most worth reading here:

  * a worktree that is a LIVE session's working directory is never removed, however clean;
  * a detached worktree orphaned by a rebase gets its own reason and is never removed.

Both were found on the fleet's own worktrees rather than reasoned about, and both fail in the
direction that destroys work, so each has a positive AND a negative control.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))


def git(*args: str, cwd: Path | None = None) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, cwd=str(cwd) if cwd else None)
    assert p.returncode == 0, f"git {' '.join(args)}\n{p.stdout}\n{p.stderr}"
    return p.stdout.strip()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A real repo on `main`, a real state dir, and `wtsweep` bound to both.

    `GEDAECHTNIS_STATE` is the seam the session records are read through; without pointing it at a
    temp dir this suite would read the MACHINE's live sessions and its verdicts would depend on who
    else is working right now."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(state))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "vault"))
    # CLONESWEEP-1: the sweep also lists the clones folder; unset, that is the machine's own.
    monkeypatch.setenv("GEDAECHTNIS_WORKTREES", str(tmp_path / "clones"))
    # The config file too (REDS32-1, RECHECK-1 item 11): unset, `config` read the machine's own
    # ~/.claude/gedaechtnis/config.json, whose `limits.worktree_sweep: true` outranks the limits
    # FILE a test points at — so the key test was red alone and green only when an earlier test in
    # the same process had left GEDAECHTNIS_CONFIG pointing at a sandbox.
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))

    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.name", "t", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    git("add", "--", "f.txt", cwd=repo)
    git("commit", "-q", "-m", "one", "--", "f.txt", cwd=repo)

    import importlib
    import config, common, wtsweep                              # noqa: PLC0415
    importlib.reload(config)
    importlib.reload(common)
    importlib.reload(wtsweep)
    assert str(config.state()) == str(state), "the sandbox state dir is not bound"
    return {"repo": repo, "state": state, "wtsweep": wtsweep, "tmp": tmp_path}


def add_worktree(repo: Path, name: str, branch: str | None = None, detach_at: str | None = None) -> Path:
    wt = repo / ".claude" / "worktrees" / name
    wt.parent.mkdir(parents=True, exist_ok=True)
    if detach_at:
        git("worktree", "add", "-q", "--detach", str(wt), detach_at, cwd=repo)
    else:
        git("worktree", "add", "-q", "-b", branch or f"wt-{name}", str(wt), "main", cwd=repo)
    return wt


def write_session(state: Path, sid: str, cwd: str, pid: int | None, with_pid_field: bool = True,
                  cwd_last: str | None = None) -> None:
    doc = {"session_id": sid, "cwd": cwd}
    if with_pid_field:
        doc["pid"] = pid
    if cwd_last:
        doc["cwd_last"] = cwd_last
    (state / f"session-start-{sid}.json").write_text(json.dumps(doc), encoding="utf-8")


def merge_into_main(repo: Path, branch: str) -> None:
    git("merge", "--ff-only", branch, cwd=repo)


# ----------------------------------------------------------------- the removable case ----

def test_a_clean_merged_unoccupied_worktree_is_REMOVED(sandbox):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "DONE")
    (wt / "g.txt").write_text("two\n", encoding="utf-8")
    git("add", "--", "g.txt", cwd=wt)
    git("commit", "-q", "-m", "two", "--", "g.txt", cwd=wt)
    merge_into_main(repo, "wt-DONE")

    result = ws.sweep(repo)
    assert [Path(p).name for p in result["removed"]] == ["DONE"]
    assert result["kept"] == []
    assert not wt.exists(), "the directory is still on disk"


def test_the_MAIN_worktree_is_never_a_candidate(sandbox):
    """The main checkout satisfies every removal condition — clean, and its HEAD is main itself."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    result = ws.sweep(repo)
    assert result["removed"] == []
    assert str(repo) not in [p for p, _ in result["kept"]]
    assert (repo / "f.txt").exists()


# ----------------------------------------------------------------- the kept cases ----

def test_a_worktree_with_uncommitted_work_is_KEPT(sandbox):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "DIRTY")
    merge_into_main(repo, "wt-DIRTY")               # merged, so ONLY dirtiness can hold it
    (wt / "scratch.txt").write_text("unsaved\n", encoding="utf-8")

    result = ws.sweep(repo)
    assert result["removed"] == []
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("DIRTY", ws.KEPT_DIRTY)]
    assert wt.exists()


def test_a_worktree_with_unmerged_commits_is_KEPT(sandbox):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "AHEAD")
    (wt / "h.txt").write_text("three\n", encoding="utf-8")
    git("add", "--", "h.txt", cwd=wt)
    git("commit", "-q", "-m", "three", "--", "h.txt", cwd=wt)

    result = ws.sweep(repo)
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("AHEAD", ws.KEPT_UNMERGED)]
    assert wt.exists()


# ------------------------------------------------- ★ amendment 1: a live session's cwd ----

def test_a_clean_merged_worktree_a_LIVE_session_occupies_is_KEPT(sandbox):
    """★ The condition the row's first draft did not have.

    This worktree satisfies every git-side condition for removal: clean status, HEAD an ancestor of
    main. A reviewer that has restored all of its mutations looks EXACTLY like this while it is
    still running. The only thing that separates the two is whether a process is alive."""
    repo, ws, state = sandbox["repo"], sandbox["wtsweep"], sandbox["state"]
    wt = add_worktree(repo, "BUSY")
    merge_into_main(repo, "wt-BUSY")
    write_session(state, "live", cwd=str(repo), pid=os.getpid(), cwd_last=str(wt))

    result = ws.sweep(repo)
    assert result["removed"] == []
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("BUSY", ws.KEPT_LIVE)]
    assert wt.exists(), "a live session's working directory was deleted"


def test_a_session_whose_cwd_is_BELOW_the_worktree_root_still_occupies_it(sandbox):
    """An agent sitting in `<wt>/gedaechtnis/tests` is as much in that worktree as one at its root."""
    repo, ws, state = sandbox["repo"], sandbox["wtsweep"], sandbox["state"]
    wt = add_worktree(repo, "DEEP")
    merge_into_main(repo, "wt-DEEP")
    deep = wt / "a" / "b"
    deep.mkdir(parents=True)
    write_session(state, "live", cwd=str(repo), pid=os.getpid(), cwd_last=str(deep))

    result = ws.sweep(repo)
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("DEEP", ws.KEPT_LIVE)]


def test_a_DEAD_session_does_not_protect_a_worktree(sandbox):
    """★ THE NEGATIVE CONTROL FOR THE ABOVE, and without it the occupancy rule would be untestable
    from its green: a guard that protects everything protects nothing, and a suite that only ever
    showed worktrees being KEPT could not tell a working rule from `return False`.

    The pid is a real one that has exited, not a fabricated number — a pid that never existed and a
    pid that has died are the same to `os.kill`, but only the second is the case this models."""
    repo, ws, state = sandbox["repo"], sandbox["wtsweep"], sandbox["state"]
    wt = add_worktree(repo, "STALE")
    merge_into_main(repo, "wt-STALE")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    write_session(state, "gone", cwd=str(repo), pid=dead.pid, cwd_last=str(wt))

    result = ws.sweep(repo)
    assert [Path(p).name for p in result["removed"]] == ["STALE"]
    assert not wt.exists()


def test_a_session_record_with_NO_pid_field_protects_the_worktree(sandbox):
    """CANNOT-TELL resolves toward keeping. A record written before the `pid` field existed names a
    directory and says nothing about whether anyone is in it; the sweep may not read that silence
    as permission to delete."""
    repo, ws, state = sandbox["repo"], sandbox["wtsweep"], sandbox["state"]
    wt = add_worktree(repo, "OLDREC")
    merge_into_main(repo, "wt-OLDREC")
    write_session(state, "ancient", cwd=str(repo), pid=None, with_pid_field=False, cwd_last=str(wt))

    result = ws.sweep(repo)
    assert result["removed"] == []
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("OLDREC", ws.KEPT_UNKNOWN_SESSION)]


def test_a_live_pid_outranks_a_stale_record_for_the_SAME_worktree(sandbox):
    """Two records name one directory: one pidless, one live. The answer must be LIVE, not
    CANNOT-TELL — the loop may not stop at the first record it happens to read, and dict ordering
    on disk is not something this may depend on."""
    repo, ws, state = sandbox["repo"], sandbox["wtsweep"], sandbox["state"]
    wt = add_worktree(repo, "BOTH")
    merge_into_main(repo, "wt-BOTH")
    write_session(state, "aaa-old", cwd=str(wt), pid=None, with_pid_field=False)
    write_session(state, "zzz-live", cwd=str(wt), pid=os.getpid())

    result = ws.sweep(repo)
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("BOTH", ws.KEPT_LIVE)]


# --------------------------------------- ★ amendment 2: orphaned by a rebase ----

def test_a_detached_worktree_orphaned_by_a_REBASE_is_kept_with_its_OWN_reason(sandbox):
    """★ Its sha is an ancestor of nothing, forever, so `merge-base --is-ancestor` can never clear
    it. Reported as plain "unmerged" it would print an identical line at every SessionStart for the
    life of the repo, which is how a report teaches people to skim it."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "REVIEW")
    (wt / "i.txt").write_text("four\n", encoding="utf-8")
    git("add", "--", "i.txt", cwd=wt)
    git("commit", "-q", "-m", "four", "--", "i.txt", cwd=wt)
    orphan = git("rev-parse", "HEAD", cwd=wt)
    git("checkout", "-q", "--detach", orphan, cwd=wt)
    git("branch", "-q", "-D", "wt-REVIEW", cwd=repo)          # the rebase dropped the old branch

    result = ws.sweep(repo)
    assert result["removed"] == []
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("REVIEW", ws.KEPT_ORPHANED)]
    assert wt.exists(), "a directory holding the only copy of a commit was deleted"


def test_a_detached_worktree_still_REACHABLE_from_a_branch_is_ordinary_unmerged_work(sandbox):
    """The negative control for the reason above: detached is not by itself orphaned. Somebody's
    branch still points at this commit, so it is ordinary unmerged work and must not borrow the
    rebase-leftover wording."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "HELD")
    (wt / "j.txt").write_text("five\n", encoding="utf-8")
    git("add", "--", "j.txt", cwd=wt)
    git("commit", "-q", "-m", "five", "--", "j.txt", cwd=wt)
    sha = git("rev-parse", "HEAD", cwd=wt)
    git("checkout", "-q", "--detach", sha, cwd=wt)
    # wt-HELD still exists and still contains this sha.

    result = ws.sweep(repo)
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("HELD", ws.KEPT_UNMERGED)]


# ----------------------------------------------------------------- reporting ----

def test_only_KEPT_worktrees_reach_the_facts_line(sandbox):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "DIRTY2")
    merge_into_main(repo, "wt-DIRTY2")
    (wt / "x.txt").write_text("u\n", encoding="utf-8")
    done = add_worktree(repo, "GONE2")
    merge_into_main(repo, "wt-GONE2")

    result = ws.sweep(repo)
    ws.record(repo, result)
    line = ws.facts_line()
    assert "DIRTY2" in line and ws.KEPT_DIRTY in line
    assert "GONE2" not in line, "a successful removal is not news"
    assert not done.exists()


def test_a_sweep_that_kept_nothing_says_nothing(sandbox):
    """Silence is the correct output, and it is not the same as never having run: `record` still
    writes the file, so a reader can tell 'swept, nothing outstanding' from 'never swept'."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    result = ws.sweep(repo)
    ws.record(repo, result)
    assert ws.facts_line() is None
    assert ws.state_file().exists()


def test_apply_False_classifies_and_removes_nothing(sandbox):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "DRY")
    merge_into_main(repo, "wt-DRY")

    result = ws.sweep(repo, apply=False)
    # DESTRUCTIVEGATE-1: what WOULD be removed is `proposed`; `removed` means removed.
    assert [Path(p).name for p in result["proposed"]] == ["DRY"]
    assert result["removed"] == []
    assert wt.exists(), "apply=False removed a directory"


# ----------------------------------------------------------------- the placement deny ----

@pytest.fixture
def gate_mod(sandbox, monkeypatch):
    import importlib, gate                                       # noqa: PLC0415
    importlib.reload(gate)
    # The placement door refuses only in a MARKED repo (PLUGDIR-1); these are its in-scope cases.
    # The unmarked case (a note, never a refusal) is test_scope_doors.py's negative control.
    (sandbox["repo"] / ".atlas-lane").write_text("lane: T\npath: T/\n", encoding="utf-8")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return gate


def test_a_worktree_add_OUTSIDE_the_repo_is_refused(gate_mod, sandbox):
    repo = sandbox["repo"]
    r = gate_mod.rule_worktree_placement(
        f"git worktree add {sandbox['tmp']}/beside-the-repo -b wt-X main", str(repo))
    assert r and ".claude/worktrees/" in r


def test_a_worktree_add_INSIDE_the_repo_is_allowed(gate_mod, sandbox):
    repo = sandbox["repo"]
    assert gate_mod.rule_worktree_placement(
        f"git worktree add {repo}/.claude/worktrees/X -b wt-X main", str(repo)) is None


def test_the_PATH_is_found_past_a_branch_flag(gate_mod, sandbox):
    """★ `git worktree add -b wt-ROW <path> main` is the spelling this fleet actually uses, and a
    naive "first token that is not a flag" reading finds `wt-ROW` — a BRANCH NAME — and judges the
    placement of a directory that was never mentioned. Both directions are driven, because reading
    the branch name as the path would make the allowed form look denied AND vice versa."""
    repo = sandbox["repo"]
    assert gate_mod.rule_worktree_placement(
        f"git worktree add -b wt-ROW {repo}/.claude/worktrees/ROW main", str(repo)) is None
    denied = gate_mod.rule_worktree_placement(
        f"git worktree add -b wt-ROW {sandbox['tmp']}/ROW main", str(repo))
    assert denied, "the path past -b was not read as the path"


def test_a_relative_path_is_resolved_against_the_session_cwd(gate_mod, sandbox):
    repo = sandbox["repo"]
    assert gate_mod.rule_worktree_placement(
        "git worktree add .claude/worktrees/REL -b wt-REL main", str(repo)) is None
    assert gate_mod.rule_worktree_placement(
        "git worktree add ../outside -b wt-OUT main", str(repo))


def test_other_git_worktree_subcommands_are_not_touched(gate_mod, sandbox):
    """The deny is about CREATION. `list`, `remove` and `prune` name paths too, and a rule that
    fired on those would refuse the sweep's own cleanup."""
    repo = sandbox["repo"]
    for cmd in ("git worktree list --porcelain",
                f"git worktree remove {sandbox['tmp']}/anywhere",
                "git worktree prune",
                "git status --porcelain"):
        assert gate_mod.rule_worktree_placement(cmd, str(repo)) is None, cmd


# ----------------------------------------------------------------- the limits seam ----

def test_the_sweep_is_disabled_by_its_config_key(sandbox, monkeypatch, tmp_path):
    """`worktree_sweep: false` must make the sweep report-only at the DOOR, not merely change a
    label. Driven through `chore.do_wtsweep`, because that is where the key is read."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "OFF")
    merge_into_main(repo, "wt-OFF")

    lf = tmp_path / "limits.json"
    lf.write_text(json.dumps({"worktree_sweep": False}), encoding="utf-8")
    monkeypatch.setenv("GEDAECHTNIS_LIMITS", str(lf))
    import importlib, limits, chore                                # noqa: PLC0415
    importlib.reload(limits)
    importlib.reload(ws)
    importlib.reload(chore)
    assert ws.enabled() is False

    chore.do_wtsweep({"cwd": str(repo)})
    assert wt.exists(), "the sweep ran with its key off"


def test_git_own_REFUSAL_is_the_second_guard_when_classify_is_wrong(sandbox, monkeypatch):
    """★ Written because a mutation said it was missing: swapping `worktree remove` for
    `worktree remove --force` left all 20 tests GREEN.

    Nothing reached that line with a dirty tree, because `classify` refuses dirtiness first — so
    the suite could not tell "we never force" from "we always force". The two agree on every input
    the other tests produce, and disagree only when the FIRST guard is wrong, which is the one case
    that matters and the one no test was creating.

    So this test makes the first guard wrong on purpose. `classify` is forced to call a worktree
    with uncommitted work removable; git's own refusal is then the only thing standing between that
    work and deletion. With `--force` it would not stand."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "SAVED")
    merge_into_main(repo, "wt-SAVED")
    (wt / "only-copy.txt").write_text("not committed anywhere\n", encoding="utf-8")

    monkeypatch.setattr(ws, "classify", lambda *a, **k: (True, ""))
    result = ws.sweep(repo)

    assert result["removed"] == [], "a dirty worktree was removed"
    assert wt.exists() and (wt / "only-copy.txt").exists(), "uncommitted work was destroyed"
    assert len(result["kept"]) == 1 and "git refused" in result["kept"][0][1]


# ------------------------------- record_cwd and session_locations, driven directly ----
# ★ THE REVIEWER'S MUST-FIX. Every test above seeds session records by writing the JSON itself, so
# `record_cwd` — the one mechanism that makes the occupancy check see a session working INSIDE a
# worktree — was exercised by nothing. A mutation that stopped it updating at all left all 21
# green. These drive it.

def _common_for(sandbox):
    import importlib, common                                     # noqa: PLC0415
    importlib.reload(common)
    return common


def test_record_cwd_puts_a_new_location_where_the_sweep_can_see_it(sandbox):
    c = _common_for(sandbox)
    repo, state = sandbox["repo"], sandbox["state"]
    (state / "session-start-s1.json").write_text(
        json.dumps({"session_id": "s1", "cwd": str(repo), "pid": os.getpid()}), encoding="utf-8")

    wt = repo / ".claude" / "worktrees" / "SEEN"
    c.record_cwd("s1", str(wt))

    places = [p for p, _pid, _has in c.session_locations()]
    assert str(wt) in places, "the new location never reached session_locations"
    assert str(repo) in places, "the SessionStart cwd was dropped"


def test_a_SUBAGENT_and_its_parent_share_one_record_and_BOTH_places_are_kept(sandbox):
    """★ The defect this row's first draft shipped, found by the reviewer asking whether a subagent
    shares its parent's session id. It does — measured 2026-09-14, and `common.py`'s attribution
    note is built on it.

    So one record has two writers in different directories. With a single latest-wins `cwd_last`,
    the parent's next Bash call overwrites the reviewer subagent's location and the worktree that
    subagent is standing in loses its protection WHILE IT IS STILL RUNNING — the exact deletion the
    occupancy condition exists to prevent. The parent writes LAST here, because that is the
    ordering that breaks a latest-wins implementation."""
    c = _common_for(sandbox)
    repo, ws, state = sandbox["repo"], sandbox["wtsweep"], sandbox["state"]
    wt = add_worktree(repo, "SUBAGENT")
    merge_into_main(repo, "wt-SUBAGENT")
    (state / "session-start-s2.json").write_text(
        json.dumps({"session_id": "s2", "cwd": str(repo), "pid": os.getpid()}), encoding="utf-8")

    c.record_cwd("s2", str(wt))          # the subagent, working in the worktree
    c.record_cwd("s2", str(repo))        # its parent, one Bash call later, in the repo root

    result = ws.sweep(repo)
    assert result["removed"] == [], "the subagent's worktree was deleted under it"
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("SUBAGENT", ws.KEPT_LIVE)]


def test_the_location_set_is_BOUNDED_and_drops_the_oldest(sandbox):
    """Unbounded, a long session's record would grow without limit and keep protecting directories
    it left hours ago — protection that never expires is the same as no rule."""
    c = _common_for(sandbox)
    repo, state = sandbox["repo"], sandbox["state"]
    (state / "session-start-s3.json").write_text(
        json.dumps({"session_id": "s3", "cwd": str(repo), "pid": os.getpid()}), encoding="utf-8")

    for i in range(c.CWD_SEEN_MAX + 3):
        c.record_cwd("s3", f"/tmp/place-{i}")

    doc = json.loads((state / "session-start-s3.json").read_text(encoding="utf-8"))
    assert len(doc["cwd_seen"]) == c.CWD_SEEN_MAX
    assert "/tmp/place-0" not in doc["cwd_seen"], "the oldest location was not dropped"
    assert doc["cwd_seen"][-1] == f"/tmp/place-{c.CWD_SEEN_MAX + 2}"


def test_repeating_the_SAME_cwd_does_not_rewrite_the_record(sandbox):
    """The short-circuit is cost-only — `record_cwd` runs on EVERY Bash PostToolUse — but a
    short-circuit that skipped a needed write would be a correctness bug, so both halves are
    driven: same cwd twice writes once, a different cwd always writes."""
    c = _common_for(sandbox)
    repo, state = sandbox["repo"], sandbox["state"]
    rec = state / "session-start-s4.json"
    rec.write_text(json.dumps({"session_id": "s4", "cwd": str(repo), "pid": os.getpid()}),
                   encoding="utf-8")

    c.record_cwd("s4", "/tmp/one")
    first = json.loads(rec.read_text(encoding="utf-8"))["cwd_last_ts"]
    c.record_cwd("s4", "/tmp/one")
    assert json.loads(rec.read_text(encoding="utf-8"))["cwd_last_ts"] == first, "rewrote for nothing"

    c.record_cwd("s4", "/tmp/two")
    doc = json.loads(rec.read_text(encoding="utf-8"))
    assert doc["cwd_last"] == "/tmp/two", "a real change was skipped"
    assert doc["cwd_seen"] == ["/tmp/one", "/tmp/two"]


def test_record_cwd_ignores_an_empty_cwd(sandbox):
    c = _common_for(sandbox)
    state = sandbox["state"]
    rec = state / "session-start-s5.json"
    rec.write_text(json.dumps({"session_id": "s5", "cwd": "/x", "pid": 1}), encoding="utf-8")
    c.record_cwd("s5", None)
    c.record_cwd("s5", "")
    assert "cwd_seen" not in json.loads(rec.read_text(encoding="utf-8"))


# ------------------------------------------- the reviewer's three logged gaps ----
# Each was found by a mutation that stayed GREEN. None is destructive — git's own refusal covers
# the locked case, and the other two only choose wording — but "not destructive" is not "tested",
# and an untested branch is one nobody will notice breaking.

def test_a_LOCKED_worktree_is_kept_with_its_own_reason(sandbox):
    """Deleting the `locked` branch left all 21 tests green. git would still refuse the removal, so
    the cost of the gap was a wrong REASON, not a deletion — but a reason is the entire output of
    this feature for everything it does not remove."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "LOCKED")
    merge_into_main(repo, "wt-LOCKED")
    git("worktree", "lock", "--reason", "held for an owner check", str(wt), cwd=repo)

    result = ws.sweep(repo)
    assert result["removed"] == []
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("LOCKED", ws.KEPT_LOCKED)]
    assert wt.exists()


def test_a_worktree_record_with_a_lock_REASON_still_parses(sandbox):
    """`git worktree lock --reason` makes the porcelain line `locked <reason>` rather than a bare
    `locked`, and the parser splits on the first space. A reason containing the word `worktree`
    would start a new record if this were pattern-matched instead of parsed."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "REASONED")
    git("worktree", "lock", "--reason", "worktree HEAD branch detached", str(wt), cwd=repo)

    wts = ws.list_worktrees(repo)
    assert len(wts) == 2, [w["path"] for w in wts]
    assert wts[1]["locked"] is True and wts[1]["head"]


def test_the_facts_line_TRUNCATES_a_long_list_and_says_how_many_it_dropped(sandbox):
    """Widening `kept[:6]` to `kept[:600]` left everything green: no fixture ever made more than a
    handful. A report that silently drops the tail is worse than one that is long."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    for i in range(8):
        wt = add_worktree(repo, f"MANY{i}")
        (wt / "d.txt").write_text("x\n", encoding="utf-8")       # dirty -> kept
    ws.record(repo, ws.sweep(repo))
    line = ws.facts_line()
    assert "and 2 more" in line, line
    # Count the WORKTREES named, not the semicolons: the line's closing sentence contains one of
    # its own, which is how this assertion first failed on a correct implementation.
    named = [i for i in range(8) if f"MANY{i} (" in line]
    assert len(named) == 6, f"{len(named)} named, expected 6: {line}"


# ------------------------------------------- WTPROC-1: a live PROCESS occupies a worktree ----
# Specimen: 2026-09-22 22:01:45 the sweep removed BASE-22 mid-pytest. The suite was a background
# process; no session record named the tree, because the session's own cwd had moved on.

def _sleeper(cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(["sleep", "60"], cwd=str(cwd), stdin=subprocess.DEVNULL)


def test_a_background_PROCESS_with_its_cwd_in_the_worktree_KEEPS_it(sandbox):
    """Positive control: no session record, only a process standing in the tree → KEPT."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "SUITE")
    merge_into_main(repo, "wt-SUITE")
    deep = wt / "tests"
    deep.mkdir()
    proc = _sleeper(deep)
    try:
        result = ws.sweep(repo)
    finally:
        proc.kill()
        proc.wait()
    assert result["removed"] == []
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("SUITE", ws.KEPT_PROCESS)]
    assert wt.exists(), "a tree a live process was working in was deleted"


def test_once_the_process_EXITS_the_same_worktree_is_removed(sandbox):
    """Negative control: the same tree, the process gone, no record → removable, as before."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "DONE")
    merge_into_main(repo, "wt-DONE")
    proc = _sleeper(wt)
    proc.kill()
    proc.wait()
    result = ws.sweep(repo)
    assert [Path(p).name for p in result["removed"]] == ["DONE"]
    assert not wt.exists()


def test_process_cwds_sees_a_KNOWN_live_process(tmp_path):
    """The probe's own positive control: a process we started, in a directory we made, is found.
    Without it a parser that returned [] for every line would pass the negative control above."""
    import wtsweep as ws                                          # noqa: PLC0415
    proc = _sleeper(tmp_path)
    try:
        cwds = ws.process_cwds()
    finally:
        proc.kill()
        proc.wait()
    assert cwds is not None
    assert (str(tmp_path.resolve()), proc.pid) in [(str(Path(c).resolve()), p) for c, p in cwds]


def test_UNREADABLE_process_cwds_keep_every_worktree(sandbox, monkeypatch):
    """lsof missing or failing is 'cannot tell', never 'nobody there': nothing is removed."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "BLIND")
    merge_into_main(repo, "wt-BLIND")
    monkeypatch.setattr(ws, "process_cwds", lambda: None)
    result = ws.sweep(repo)
    assert [(Path(p).name, r) for p, r in result["kept"]] == [("BLIND", ws.KEPT_PROCS_UNREAD)]
    assert wt.exists()


def test_process_cwds_returns_None_when_lsof_cannot_run(monkeypatch):
    import wtsweep as ws                                          # noqa: PLC0415

    def boom(*a, **k):
        raise FileNotFoundError("lsof")
    monkeypatch.setattr(ws.subprocess, "run", boom)
    assert ws.process_cwds() is None


def test_a_process_in_a_SIBLING_whose_name_shares_a_prefix_does_not_occupy(sandbox):
    """`/wt/AB` must not occupy `/wt/A` — containment is by path parents, never string prefix."""
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = add_worktree(repo, "A")
    merge_into_main(repo, "wt-A")
    sib = wt.parent / "AB-not-a-worktree"
    sib.mkdir()
    proc = _sleeper(sib)
    try:
        result = ws.sweep(repo)
    finally:
        proc.kill()
        proc.wait()
    assert [Path(p).name for p in result["removed"]] == ["A"]


# ------------------------------------------- CLONESWEEP-1: the copy-on-write clones ----

@pytest.fixture
def clones(sandbox, monkeypatch, tmp_path):
    """HOME in the temp dir (the To-delete folder is under it) and no minimum age, set through
    the vault's own `limits` object."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "config.json").write_text(
        json.dumps({"limits": {"worktree_clone_min_age_seconds": 0}}), encoding="utf-8")
    import limits                                                  # noqa: PLC0415
    limits._reset_for_tests()
    yield sandbox
    limits._reset_for_tests()


def make_clone(sandbox, name: str = "agent-1", dirt_at_birth: str | None = None) -> tuple[Path, float]:
    """What `worktree.py create` leaves: a full copy, the marker and the birth manifest."""
    import shutil, worktree                                        # noqa: PLC0415
    repo = sandbox["repo"]
    if dirt_at_birth:
        (repo / dirt_at_birth).write_text("born with it\n", encoding="utf-8")
    dst = sandbox["tmp"] / "clones" / f"repo--{name}"
    shutil.copytree(repo, dst, symlinks=True)
    (dst / ".gedaechtnis-clone-manifest.json").write_text(json.dumps(worktree.dirt_manifest(dst)))
    (dst / ".gedaechtnis-clone-of").write_text(str(repo) + "\n", encoding="utf-8")
    time.sleep(0.05)                    # whatever a test writes next is later than the marker
    return dst, (dst / ".gedaechtnis-clone-of").stat().st_mtime


def write_after_birth(path: Path, born: float, text: str = "x\n") -> None:
    path.write_text(text, encoding="utf-8")
    assert path.stat().st_mtime > born


def test_a_finished_clone_is_PROPOSED_then_MOVED_to_the_to_delete_folder(clones):
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, born = make_clone(clones, dirt_at_birth="scratch.txt")
    write_after_birth(clone / "run.log", born)                     # generated: not work
    assert ws.sweep(repo, apply=False)["proposed"] == [str(clone.resolve())]
    assert clone.exists(), "a proposing pass moved the clone"

    result = ws.sweep(repo, apply=True)
    dest = ws.to_delete_dir() / clone.name
    assert result["removed"] == [str(clone.resolve())] and result["kept"] == []
    assert not clone.exists() and (dest / "f.txt").read_text() == "one\n"
    assert str(clones["tmp"] / "home" / "Downloads" / "To delete ") in str(dest)
    assert str(dest) in (dest.parent / "ledger.tsv").read_text()
    assert (dest.parent / "README-what-went-where.html").read_text().startswith(
        '<!doctype html>\n<meta charset="utf-8">\n')
    assert result["undo"] == [ws.undo_mv(dest, clone.resolve())]


def test_a_clone_YOUNGER_than_the_minimum_age_is_kept(clones, monkeypatch):
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, _ = make_clone(clones)
    (clones["tmp"] / "config.json").write_text("{}", encoding="utf-8")   # the shipped day
    import limits                                                  # noqa: PLC0415
    limits._reset_for_tests()
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_YOUNG)]
    assert clone.exists()


def test_a_clone_with_its_OWN_uncommitted_file_is_kept(clones):
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, born = make_clone(clones, dirt_at_birth="scratch.txt")
    write_after_birth(clone / "scratch.txt", born, "edited in the clone\n")
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_DIRTY)]
    assert clone.exists()


def test_a_clone_commit_is_kept_until_main_has_it_or_its_PATCH(clones):
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, born = make_clone(clones)
    (clone / "g.txt").write_text("two\n", encoding="utf-8")
    git("add", "--", "g.txt", cwd=clone)
    git("commit", "-q", "-m", "two", "--", "g.txt", cwd=clone)
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_UNMERGED)]
    # the same change lands on main under another sha (a rebase, a cherry-pick): patch-equivalent
    (repo / "pad.txt").write_text("pad\n", encoding="utf-8")
    git("add", "--", "pad.txt", cwd=repo)
    git("commit", "-q", "-m", "pad", "--", "pad.txt", cwd=repo)
    (repo / "g.txt").write_text("two\n", encoding="utf-8")
    git("add", "--", "g.txt", cwd=repo)
    git("commit", "-q", "-m", "two again", "--", "g.txt", cwd=repo)
    assert ws.sweep(repo, apply=False)["proposed"] == [str(clone.resolve())]


def test_a_clone_with_a_stash_of_its_own_or_a_live_session_is_kept(clones):
    repo, ws, state = clones["repo"], clones["wtsweep"], clones["state"]
    clone, born = make_clone(clones)
    write_session(state, "live", cwd=str(repo), pid=os.getpid(), cwd_last=str(clone / "sub"))
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_LIVE)]
    (state / "session-start-live.json").unlink()
    (clone / "f.txt").write_text("stashed\n", encoding="utf-8")
    subprocess.run(["git", "stash", "push", "-q"], cwd=str(clone), check=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": f"@{int(born) + 60} +0000"})
    git("checkout", "-q", "--", ".", cwd=clone)
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_CLONE_STASH)]
    assert clone.exists()


def test_a_clone_of_ANOTHER_repo_and_an_unmarked_folder_are_never_listed(clones):
    repo, ws = clones["repo"], clones["wtsweep"]
    other, _ = make_clone(clones, name="other")
    (other / ".gedaechtnis-clone-of").write_text(str(clones["tmp"] / "elsewhere") + "\n")
    (clones["tmp"] / "clones" / "plain-folder").mkdir()
    assert ws.clones_of(repo) == []
    assert ws.sweep(repo, apply=True) == {"removed": [], "kept": [], "proposed": [], "undo": []}


# ------------------------------- CLONESWEEP-1: a pidless session record expires ----

def test_a_pidless_session_record_stops_protecting_once_its_file_is_OLD(sandbox):
    """Negative control: `test_a_session_record_with_NO_pid_field_protects_the_worktree` above —
    the same record, fresh, still keeps the worktree."""
    repo, ws, state = sandbox["repo"], sandbox["wtsweep"], sandbox["state"]
    wt = add_worktree(repo, "EXPIRED")
    merge_into_main(repo, "wt-EXPIRED")
    write_session(state, "ancient", cwd=str(repo), pid=None, with_pid_field=False, cwd_last=str(wt))
    old = time.time() - 2 * 86400
    os.utime(state / "session-start-ancient.json", (old, old))
    assert [Path(p).name for p in ws.sweep(repo)["removed"]] == ["EXPIRED"]


# ---- the review's holes (TOOLS-C review, 2026-10-02): each was PROPOSED before the fix ----

def test_a_born_dirty_file_with_a_QUOTED_name_edited_in_the_clone_is_kept(clones):
    """git prints `café.txt` as an escaped, quoted string unless asked for -z; read that way the
    manifest recorded it as "gone" and the edit read as "born deleted"."""
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, born = make_clone(clones, dirt_at_birth="café \"q\".txt")
    assert ws.sweep(repo, apply=False)["proposed"] == [str(clone.resolve())]   # untouched: fine
    write_after_birth(clone / "café \"q\".txt", born, "edited\n")
    assert ws.sweep(repo, apply=False)["kept"] == [(str(clone.resolve()), ws.KEPT_DIRTY)]


def test_a_born_dirty_file_REPLACED_with_an_old_timestamp_is_kept(clones):
    """`cp -p` and `mv` keep an old modification time; the inode change time still moves."""
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, born = make_clone(clones, dirt_at_birth="scratch.txt")
    (clone / "scratch.txt").write_text("replaced\n", encoding="utf-8")
    os.utime(clone / "scratch.txt", (born - 3600, born - 3600))
    assert ws.sweep(repo, apply=False)["kept"] == [(str(clone.resolve()), ws.KEPT_DIRTY)]


@pytest.mark.parametrize("how", ["detached", "tag", "other-ref", "merge"])
def test_a_commit_only_the_clone_holds_keeps_it_WHEREVER_it_hangs(clones, how):
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, _ = make_clone(clones)
    if how == "merge":                       # the clone merges by hand; the source merges the same
        for r in (repo, clone):              # branch itself, so every NON-merge commit is on main
            git("checkout", "-q", "-b", "side", cwd=r)
            (r / "s.txt").write_text("side\n", encoding="utf-8")
            git("add", "--", "s.txt", cwd=r)
            subprocess.run(["git", "commit", "-q", "-m", "side", "--", "s.txt"], cwd=str(r), check=True,
                           env={**os.environ, "GIT_AUTHOR_DATE": "@1700000000 +0000",
                                "GIT_COMMITTER_DATE": "@1700000000 +0000"})
            git("checkout", "-q", "main", cwd=r)
            git("merge", "-q", "--no-ff", "-m", f"merge in {r.name}", "side", cwd=r)
    else:
        (clone / "g.txt").write_text("two\n", encoding="utf-8")
        git("add", "--", "g.txt", cwd=clone)
        git("commit", "-q", "-m", "two", "--", "g.txt", cwd=clone)
        if how == "tag":
            git("tag", "keep-me", cwd=clone)
        if how == "other-ref":
            git("update-ref", "refs/wip/x", "HEAD", cwd=clone)
        if how != "detached":
            git("reset", "-q", "--hard", "HEAD~1", cwd=clone)       # no branch holds it any more
        else:
            git("checkout", "-q", "--detach", cwd=clone)
            git("branch", "-f", "main", "HEAD~1", cwd=clone)
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_UNMERGED)]
    assert clone.exists()


def test_a_FAILING_git_cherry_never_reads_as_merged(clones, monkeypatch):
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, _ = make_clone(clones)
    (clone / "g.txt").write_text("two\n", encoding="utf-8")
    git("add", "--", "g.txt", cwd=clone)
    git("commit", "-q", "-m", "two", "--", "g.txt", cwd=clone)
    real = ws._git

    def broken(args, **k):
        if args[:1] == ["cherry"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal")
        return real(args, **k)
    monkeypatch.setattr(ws, "_git", broken)
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_UNMERGED)]


def test_a_broken_marker_or_config_never_escapes_the_sweep_and_the_worktree_half_is_reported(clones):
    repo, ws = clones["repo"], clones["wtsweep"]
    wt = add_worktree(repo, "DONE")
    merge_into_main(repo, "wt-DONE")
    clone, _ = make_clone(clones)
    bad = clones["tmp"] / "clones" / "not-utf8"
    bad.mkdir()
    (bad / ".gedaechtnis-clone-of").write_bytes(b"\xff\xfe\n")
    (clones["tmp"] / "config.json").write_text(json.dumps(
        {"limits": {"worktree_clone_min_age_seconds": 0, "worktree_generated": [1, "*.log"]}}))
    import limits                                                  # noqa: PLC0415
    limits._reset_for_tests()
    (clone / "new.txt").write_text("work\n", encoding="utf-8")
    result = ws.sweep(repo, apply=True)
    assert [Path(p).name for p in result["removed"]] == ["DONE"] and not wt.exists()
    assert result["kept"] == [(str(clone.resolve()), ws.KEPT_DIRTY)]


def test_a_session_working_in_ONE_directory_keeps_its_record_young(sandbox):
    """The expiry reads the record file's age; `record_cwd` skips the write when the directory has
    not changed, so without the hourly touch a live pidless session would age out in place."""
    import common                                                  # noqa: PLC0415
    state = sandbox["state"]
    common.record_cwd("stay", "/somewhere")
    f = common.session_state_path("stay")
    old = time.time() - 2 * 86400
    os.utime(f, (old, old))
    common.record_cwd("stay", "/somewhere")
    assert time.time() - f.stat().st_mtime < 60
    assert ("/somewhere", None, False) in common.session_locations(stale_no_pid=86400)


# ---- fix-only review acf80012: N1 (a linked worktree of the clone's own), N2 (reflog-only commits),
# ---- N3 (content, not the clock), and four guards that had no test (its mutants M1, M3, M4, M5)

def test_a_clone_in_which_a_git_worktree_was_ADDED_is_kept_and_an_inherited_one_is_not(clones):
    """N1: `for-each-ref` does not list a linked worktree's HEAD and no status reads its files, so
    a clone with a hand-written file in its own linked worktree was PROPOSED. Control first: the
    `.git/worktrees/` entry a clone is BORN with (the source's worktree) does not keep it."""
    repo, ws = clones["repo"], clones["wtsweep"]
    git("worktree", "add", "-q", "-b", "src-wt", str(clones["tmp"] / "src-linked"), "main", cwd=repo)
    clone, _ = make_clone(clones)
    assert (clone / ".git" / "worktrees" / "src-linked").is_dir()
    assert ws.sweep(repo, apply=False)["proposed"] == [str(clone.resolve())]
    lw = clones["tmp"] / "clone-linked"
    git("worktree", "add", "-q", "--detach", str(lw), cwd=clone)
    (lw / "notes.txt").write_text("hand-written, uncommitted, in no status the sweep reads\n")
    result = ws.sweep(repo, apply=True)
    assert (str(clone.resolve()), ws.KEPT_CLONE_LINKED) in result["kept"] and result["removed"] == []
    assert clone.exists()


def test_a_commit_on_a_linked_worktrees_HEAD_counts_as_a_tip_of_the_clone(clones, monkeypatch):
    """N1, second guard: with the "added after birth" check out of the way (as after a touched
    marker), the linked worktree's detached commit must still read as held only by the clone."""
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, _ = make_clone(clones)
    lw = clones["tmp"] / "clone-linked"
    git("worktree", "add", "-q", "--detach", str(lw), cwd=clone)
    monkeypatch.setattr(ws, "clone_worktree_added", lambda wt, born: False)
    assert ws.sweep(repo, apply=False)["proposed"] == [str(clone.resolve())]   # control: no commit yet
    (lw / "g.txt").write_text("two\n", encoding="utf-8")
    git("add", "--", "g.txt", cwd=lw)
    git("commit", "-q", "-m", "in the linked worktree", "--", "g.txt", cwd=lw)
    assert ws.sweep(repo, apply=False)["kept"] == [(str(clone.resolve()), ws.KEPT_UNMERGED)]


def test_a_commit_only_the_clones_REFLOG_holds_keeps_it(clones):
    """N2: `commit; reset --hard HEAD~1` leaves a commit no ref names. Control: the same history
    made in the SOURCE before the clone was born is the source's own and keeps nothing."""
    repo, ws = clones["repo"], clones["wtsweep"]

    def commit_and_drop(r):
        (r / "g.txt").write_text(f"dropped in {r.name}\n", encoding="utf-8")
        git("add", "--", "g.txt", cwd=r)
        git("commit", "-q", "-m", "dropped", "--", "g.txt", cwd=r)
        git("reset", "-q", "--hard", "HEAD~1", cwd=r)
    commit_and_drop(repo)
    clone, _ = make_clone(clones)
    assert ws.sweep(repo, apply=False)["proposed"] == [str(clone.resolve())]
    commit_and_drop(clone)
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_UNMERGED)]


def test_more_unknown_tips_than_the_cap_is_kept_unjudged(clones, monkeypatch):
    """Control: one tip the source lacks, patch-equivalent to a commit on main, is held."""
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, born = make_clone(clones)
    for r, msg in ((clone, "two"), (repo, "the same patch under another sha")):
        (r / "g.txt").write_text("two\n", encoding="utf-8")
        git("add", "--", "g.txt", cwd=r)
        git("commit", "-q", "-m", msg, "--", "g.txt", cwd=r)
    assert ws.clone_commits_kept_elsewhere(repo, clone, born) is True
    monkeypatch.setattr(ws, "MAX_CLONE_TIPS", 0)
    assert ws.clone_commits_kept_elsewhere(repo, clone, born) is False


def test_a_born_dirty_file_is_judged_by_its_CONTENT_whatever_the_clocks_say(clones):
    """N3: an edit followed by a touch of the marker moved "born" past the edit and the clone was
    PROPOSED. Control: the same file rewritten with the SAME content after birth is not work."""
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, born = make_clone(clones, dirt_at_birth="scratch.txt")
    write_after_birth(clone / "scratch.txt", born, "born with it\n")            # same bytes, new times
    assert ws.sweep(repo, apply=False)["proposed"] == [str(clone.resolve())]
    (clone / "scratch.txt").write_text("edited\n", encoding="utf-8")
    time.sleep(0.05)
    os.utime(clone / ".gedaechtnis-clone-of")                                    # born moves past the edit
    assert ws.sweep(repo, apply=False)["kept"] == [(str(clone.resolve()), ws.KEPT_DIRTY)]


def test_porcelain_z_skips_the_ORIGINAL_path_of_a_rename(sandbox):
    common = _common_for(sandbox)
    assert common.porcelain_z("R  f2.txt\0f.txt\0?? café \"q\".txt\0 M a b\0") == [
        ("R ", "f2.txt"), ("??", "café \"q\".txt"), (" M", "a b")]
    assert common.porcelain_z("") == [] and common.porcelain_z(None) == []


def test_a_staged_rename_at_birth_edited_in_the_clone_is_kept(clones):
    repo, ws = clones["repo"], clones["wtsweep"]
    git("mv", "f.txt", "f2.txt", cwd=repo)
    clone, born = make_clone(clones)
    assert ws.sweep(repo, apply=False)["proposed"] == [str(clone.resolve())]   # untouched: fine
    write_after_birth(clone / "f2.txt", born, "edited after the rename\n")
    assert ws.sweep(repo, apply=False)["kept"] == [(str(clone.resolve()), ws.KEPT_DIRTY)]


def test_a_dirty_DIRECTORY_a_nested_repository_always_keeps_the_clone(clones):
    """Its inside is never read, so nothing can show it unchanged."""
    repo, ws = clones["repo"], clones["wtsweep"]
    nested = repo / "vendor" / "lib"
    nested.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=nested)
    (nested / "x.txt").write_text("x\n", encoding="utf-8")
    clone, _ = make_clone(clones)
    assert ws.sweep(repo, apply=False)["kept"] == [(str(clone.resolve()), ws.KEPT_DIRTY)]


def test_an_exception_in_the_clone_half_still_reports_what_the_worktree_half_removed(clones, monkeypatch):
    repo, ws = clones["repo"], clones["wtsweep"]
    wt = add_worktree(repo, "DONE")
    merge_into_main(repo, "wt-DONE")
    make_clone(clones)

    def boom(*a, **k):
        raise RuntimeError("classify fell over")
    monkeypatch.setattr(ws, "classify_clone", boom)
    result = ws.sweep(repo, apply=True)
    assert [Path(p).name for p in result["removed"]] == ["DONE"] and not wt.exists()
    assert len(result["kept"]) == 1 and "the clone sweep stopped" in result["kept"][0][1]


def test_a_FAILING_rev_list_never_reads_as_merged(clones, monkeypatch):
    repo, ws = clones["repo"], clones["wtsweep"]
    clone, _ = make_clone(clones)
    (clone / "g.txt").write_text("two\n", encoding="utf-8")
    git("add", "--", "g.txt", cwd=clone)
    git("commit", "-q", "-m", "two", "--", "g.txt", cwd=clone)
    real = ws._git

    def broken(args, **k):
        if args[:1] == ["rev-list"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal")
        return real(args, **k)
    monkeypatch.setattr(ws, "_git", broken)
    assert ws.sweep(repo, apply=True)["kept"] == [(str(clone.resolve()), ws.KEPT_CLONE_GIT)]
