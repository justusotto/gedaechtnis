"""INBOXWT-1 — a write from a git WORKTREE is not another lane's work.

`chore._inbox_target` decided whose a write was by comparing the written file's repo root with the
session cwd's root. A worktree has its own root, so a lane's own build worktrees compared unequal
and were recorded as foreign: 32 of CURSUS's own row reports and handoffs landed in
`Speculum/Inbox.md` on 2026-09-20, plus 7 more tagged UNKNOWN-LANE because the session stood in the
vault, where no marker lives, while the file went into its own repo.

The fix is that lane identity comes from the MARKER the written file's repository declares. These
tests use REAL git worktrees, not a fixture that imitates one: the defect lived in the difference
between a `.git` directory and a `.git` file, which only real git produces.
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
import pytest

from test_gate import world, run                                # noqa: F401 — the shared sandbox

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def make_repo(path: Path, vault: Path, region: str, lane: str | None, track_marker: bool = True):
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", str(path))
    (path / "CLAUDE.md").write_text(f"@{vault}/{region}/Kernel.md\n", encoding="utf-8")
    if lane and track_marker:
        (path / ".atlas-lane").write_text(f"lane: {lane}\npath: {region}/\n", encoding="utf-8")
    (vault / region).mkdir(parents=True, exist_ok=True)
    (path / "seed.md").write_text("x\n", encoding="utf-8")
    git("-C", str(path), "add", "-A")
    git("-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    if lane and not track_marker:
        # An UNTRACKED marker: git does not copy it into a worktree, so the worktree carries no
        # lane of its own and the identity can only come from the main checkout. This is the case
        # that makes the common-dir resolution load-bearing — with a TRACKED marker (what this
        # fleet happens to do) a worktree carries a copy and a weaker implementation looks right.
        (path / ".atlas-lane").write_text(f"lane: {lane}\npath: {region}/\n", encoding="utf-8")
    return path


def worktree_of(repo: Path, name: str) -> Path:
    wt = repo.parent / name
    git("-C", str(repo), "worktree", "add", "-q", "-b", f"b-{name}", str(wt))
    return wt


def declare_lane(world, lane: str, sid="s1"):
    """What every real session has: the lane it declared at boot, in its own start record."""
    d = Path(world["env"]["GEDAECHTNIS_STATE_DIR"]); d.mkdir(parents=True, exist_ok=True)
    (d / f"session-start-{sid}.json").write_text(json.dumps({"session_id": sid, "lane": lane}),
                                                 encoding="utf-8")


def inbox_call(world, cwd: Path, target: Path, sid="s1"):
    return run("chore.py", "inbox", {"cwd": str(cwd), "session_id": sid, "tool_name": "Write",
                                     "tool_input": {"file_path": str(target), "content": "x\n"}},
               world["env"])


# ------------------------------------------------------------------ negative controls ----

def test_a_write_from_a_worktree_of_the_sessions_own_repo_makes_no_row(world, tmp_path):
    """The specimen. `world["repo"]` is lane CARD; its worktree is the same repository."""
    repo = make_repo(tmp_path / "cards", world["vault"], "Studio/Cards", "CARD")
    wt = worktree_of(repo, "cards-wt-BUILD")
    f = wt / "report.md"; f.write_text("x\n", encoding="utf-8")
    assert inbox_call(world, cwd=repo, target=f) is None
    assert not (world["vault"] / "Studio" / "Cards" / "Inbox.md").exists()


def test_a_session_standing_in_the_vault_writing_into_its_own_repo_makes_no_row(world, tmp_path):
    """The second misread class: the vault carries no marker, so the session had no identity and
    its own write was filed under UNKNOWN-LANE. Identity comes from the file's repo instead."""
    repo = make_repo(tmp_path / "cards", world["vault"], "Studio/Cards", "CARD")
    declare_lane(world, "CARD")
    f = repo / "report.md"; f.write_text("x\n", encoding="utf-8")
    assert inbox_call(world, cwd=world["vault"], target=f) is None
    assert not (world["vault"] / "Studio" / "Cards" / "Inbox.md").exists()


# ------------------------------------------------------------------ positive controls ----

def test_a_write_into_a_foreign_lanes_repo_still_makes_a_row(world, tmp_path):
    other = make_repo(tmp_path / "vocab", world["vault"], "Studio/Vocab", "VOCAB")
    f = other / "src.py"; f.write_text("x\n", encoding="utf-8")
    assert inbox_call(world, cwd=world["repo"], target=f) is not None
    ib = world["vault"] / "Studio" / "Vocab" / "Inbox.md"
    assert ib.is_file() and "CARD wrote `vocab/src.py`" in ib.read_text(encoding="utf-8")


def test_a_write_into_a_foreign_repos_worktree_still_makes_a_row(world, tmp_path):
    """Resolving worktrees must not swallow the case the record exists for: another lane's
    worktree is still another lane."""
    other = make_repo(tmp_path / "vocab", world["vault"], "Studio/Vocab", "VOCAB")
    wt = worktree_of(other, "vocab-wt-X")
    f = wt / "src.py"; f.write_text("x\n", encoding="utf-8")
    assert inbox_call(world, cwd=world["repo"], target=f) is not None
    assert (world["vault"] / "Studio" / "Vocab" / "Inbox.md").is_file()


# --------------------------------------------------------- the resolution itself ----

def test_main_checkout_of_agrees_with_git_on_a_real_worktree(tmp_path):
    """The shortcut is a file read, not `git rev-parse --git-common-dir`. Pinned against real git
    so the two cannot diverge quietly."""
    import common
    repo = tmp_path / "r"; repo.mkdir()
    git("init", "-q", str(repo))
    (repo / "a.txt").write_text("x\n", encoding="utf-8")
    git("-C", str(repo), "add", "-A")
    git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "s")
    wt = worktree_of(repo, "r-wt")
    common_dir = git("-C", str(wt), "rev-parse", "--path-format=absolute", "--git-common-dir")
    assert common.main_checkout_of(wt / "a.txt") == Path(common_dir).parent.resolve()
    assert common.main_checkout_of(repo / "a.txt") == repo.resolve()


def test_lane_of_repo_reads_the_main_checkouts_marker(tmp_path, world):
    import common
    repo = make_repo(tmp_path / "cards", world["vault"], "Studio/Cards", "CARD")
    wt = worktree_of(repo, "cards-wt-Y")
    assert common.lane_of_repo(wt / "seed.md") == "CARD"        # the worktree carries no marker
    assert common.lane_of_repo(repo / "seed.md") == "CARD"
    unmarked = make_repo(tmp_path / "plain", world["vault"], "Studio/Vocab", None)
    assert common.lane_of_repo(unmarked / "seed.md") is None
    # The load-bearing case: the marker is untracked, so the worktree does NOT carry a copy.
    hidden = make_repo(tmp_path / "hidden", world["vault"], "Studio/Vocab", "VOCAB", track_marker=False)
    hwt = worktree_of(hidden, "hidden-wt")
    assert not (hwt / ".atlas-lane").exists()
    assert common.lane_of_repo(hwt / "seed.md") == "VOCAB"


def test_a_vault_cwd_session_writing_into_a_FOREIGN_repo_is_still_recorded(world, tmp_path):
    """The swallow this fix must not introduce. Standing in the vault does not make another lane's
    repo yours: the session's declared lane is the one it booted with, and it differs."""
    declare_lane(world, "CARD")
    other = make_repo(tmp_path / "vocab", world["vault"], "Studio/Vocab", "VOCAB")
    f = other / "src.py"; f.write_text("x\n", encoding="utf-8")
    assert inbox_call(world, cwd=world["vault"], target=f) is not None
    assert (world["vault"] / "Studio" / "Vocab" / "Inbox.md").is_file()


def test_an_unattributable_session_records_the_row_rather_than_swallowing_it(world, tmp_path):
    """No marker above the cwd and no declared lane in the start record: the session's identity is
    genuinely unknown. A record that cannot name the writer is worth more than no record — the row
    is written, and its lane field says so."""
    other = make_repo(tmp_path / "vocab", world["vault"], "Studio/Vocab", "VOCAB")
    f = other / "src.py"; f.write_text("x\n", encoding="utf-8")
    assert inbox_call(world, cwd=world["vault"], target=f, sid="nolane") is not None
    assert "UNKNOWN-LANE wrote" in (world["vault"] / "Studio" / "Vocab" / "Inbox.md").read_text(encoding="utf-8")
