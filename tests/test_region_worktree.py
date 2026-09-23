"""REGIONWT-1 — a write's REGION comes from the main checkout, like its LANE always did.

`chore._inbox_target` asks two questions about one write: which LANE owns it (`lane_of_repo`,
which resolves through `main_checkout_of`) and which REGION it belongs to (`region_of_repo`, which
did not). The two could disagree about the same file, and the INBOXWT-1 reviewer DEMONSTRATED both
consequences on real worktrees rather than suspecting them:

  * a worktree on a branch whose `CLAUDE.md` names a different region filed the row under the
    WRONG region;
  * a worktree whose `CLAUDE.md` has no resolvable @-import at all made `_inbox_target` return
    `(None, None)` — the row VANISHED, and nothing errored.

Real git worktrees, not a fixture imitating one: the defect lives in the difference between a
`.git` directory and a `.git` FILE, which only real git produces. The helpers come from
`test_inbox_worktree.py`, which established that discipline for the lane half.
"""
from __future__ import annotations
import sys
import pytest
from pathlib import Path

from test_gate import world, run                                # noqa: F401 — the shared sandbox
from test_inbox_worktree import git, make_repo, worktree_of, declare_lane, inbox_call  # noqa: F401

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))


@pytest.fixture(autouse=True)
def restore_env():
    """★ `common_for` below sets GEDAECHTNIS_* in this PROCESS, and pytest runs every module in
    one. Without this, the last test here would leave `config` pointing at a deleted tmp_path and
    the next module would fail somewhere unrelated — a test suite whose verdict depends on its own
    ordering. Snapshot and restore, and reload the two modules onto the restored values."""
    import os, importlib                                        # noqa: PLC0415
    keys = [k for k in os.environ if k.startswith("GEDAECHTNIS_")]
    before = {k: os.environ[k] for k in keys}
    yield
    for k in [k for k in os.environ if k.startswith("GEDAECHTNIS_")]:
        if k not in before:
            os.environ.pop(k, None)
    os.environ.update(before)
    import config, common                                       # noqa: PLC0415
    importlib.reload(config)
    importlib.reload(common)


def common_for(world):
    """`common` bound to the WORLD's sandbox vault, not the machine's.

    ★ `config.vault()` is resolved through `config`, and reloading `common` alone leaves `config`
    pointing wherever the first test in the process pointed it — the failure mode this suite hit
    on its first run, and it failed in the REASSURING direction: `region_of_repo` returned None
    for a perfectly ordinary checkout and the test read that as the code being wrong."""
    import os, importlib                                        # noqa: PLC0415
    for k, v in world["env"].items():
        if k.startswith("GEDAECHTNIS_"):
            os.environ[k] = v
    import config, common                                       # noqa: PLC0415
    importlib.reload(config)
    importlib.reload(common)
    assert str(common.config.vault()) == str(world["vault"]), "the sandbox vault is not bound"
    return common


def rewrite_claude_md(wt: Path, text: str) -> None:
    """Change the WORKTREE's CLAUDE.md and commit it on the worktree's own branch, so the two
    checkouts genuinely disagree. Writing the file without committing would leave a dirty tree and
    prove nothing about a branch."""
    (wt / "CLAUDE.md").write_text(text, encoding="utf-8")
    git("-C", str(wt), "add", "--", "CLAUDE.md")
    git("-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-q", "-m", "branch names a different region", "--", "CLAUDE.md")


# ------------------------------------------------------------------ the resolution ----

def test_a_worktree_whose_branch_names_another_region_resolves_to_the_MAIN_checkouts(world, tmp_path):
    """POSITIVE. The demonstrated misfiling: the branch says Vocab, the repository is Cards."""
    common = common_for(world)
    repo = make_repo(tmp_path / "cards", world["vault"], "Studio/Cards", "CARD")
    (world["vault"] / "Studio" / "Vocab").mkdir(parents=True, exist_ok=True)
    wt = worktree_of(repo, "cards-wt-DIVERGENT")
    rewrite_claude_md(wt, f"@{world['vault']}/Studio/Vocab/Kernel.md\n")

    assert common._region_in_claude_md(wt) == "Studio/Vocab", "fixture broken: the branch does not diverge"
    assert common._region_in_claude_md(repo) == "Studio/Cards"
    assert common.region_of_repo(wt) == "Studio/Cards", "the worktree's own branch decided the region"


def test_a_worktree_with_no_resolvable_import_falls_back_and_does_not_vanish(world, tmp_path):
    """POSITIVE for the swallow class. With no @-import on the branch, the old code returned None
    and `_inbox_target` dropped the row entirely — the failure that produces NO signal at all."""
    common = common_for(world)
    repo = make_repo(tmp_path / "cards", world["vault"], "Studio/Cards", "CARD")
    wt = worktree_of(repo, "cards-wt-BARE")
    rewrite_claude_md(wt, "# No imports on this branch at all.\n")

    assert common._region_in_claude_md(wt) is None, "fixture broken: the branch still resolves"
    assert common.region_of_repo(wt) == "Studio/Cards"


def test_the_worktree_is_the_FALLBACK_when_the_main_checkout_has_none(world, tmp_path):
    """NEGATIVE for 'main checkout always wins'. A branch may legitimately ADD an import the main
    checkout has not got; what it may not do is silently MOVE the repo. Returning None here would
    re-open the swallow class from the other side, so the fallback has to be real — and a test
    that only ever drove the main-checkout-wins direction could not tell the two apart."""
    common = common_for(world)
    repo = make_repo(tmp_path / "fresh", world["vault"], "Studio/Cards", "CARD")
    (repo / "CLAUDE.md").write_text("# Nothing imported yet.\n", encoding="utf-8")
    git("-C", str(repo), "add", "--", "CLAUDE.md")
    git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-q", "-m", "drop the import", "--", "CLAUDE.md")
    wt = worktree_of(repo, "fresh-wt-ADDS")
    rewrite_claude_md(wt, f"@{world['vault']}/Studio/Cards/Kernel.md\n")

    assert common._region_in_claude_md(repo) is None, "fixture broken: the main checkout resolves"
    assert common.region_of_repo(wt) == "Studio/Cards", "the fallback to the worktree did not happen"


def test_an_ordinary_checkout_is_unchanged(world, tmp_path):
    """NEGATIVE. The overwhelmingly common case must not move: a repo that is its own main
    checkout answers exactly as before, and reads its OWN CLAUDE.md."""
    common = common_for(world)
    repo = make_repo(tmp_path / "plain", world["vault"], "Studio/Cards", "CARD")
    assert common.region_of_repo(repo) == "Studio/Cards"
    assert common._region_sources(repo) == [repo], "an ordinary checkout consulted a second source"


def test_a_region_segment_that_SYMLINKS_out_of_the_vault_is_refused(world, tmp_path):
    """★ THE CONTAINMENT CHECK'S ONLY CONTROL, ANYWHERE IN THIS SUITE — and it was written after a
    mutation showed the traversal test below could not reach it.

    `region_of_repo` has two guards against a region escaping the vault: a cheap segment rule
    (no segment may be `.` or `..`) and `region_is_contained`, which compares realpaths. A `..`
    fixture is caught by the CHEAP one — `top.startswith(".")` fires first — so deleting
    `region_is_contained` left every test green and the guard read as redundant. It is not: a
    segment that is a SYMLINK contains no dot at all, and realpath is the only thing that sees
    through it.

    ★ CORRECTION, from the REGIONWT-1 reviewer, because the first telling of this was too strong.
    What had no control was the CALL SITE, not the function. Neutering `region_is_contained`
    itself also reddens `test_a_dot_dot_that_stays_INSIDE_the_vault_is_still_refused`, which calls
    it directly — so the function was never wholly unmeasured. What nothing reached was the
    `if not region_is_contained(rel)` branch INSIDE `region_of_repo`: deleting that one line left
    all 14 tests green before this test existed, and is RED with it and GREEN again when only
    this test is deselected. That pair is the actual proof, and it is a narrower claim than
    "untested for seven days" — a distinction worth keeping, because the loose version would have
    had someone re-deriving a control that already existed."""
    common = common_for(world)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Kernel.md").write_text("# not in the vault\n", encoding="utf-8")
    (world["vault"] / "Escape").symlink_to(outside, target_is_directory=True)

    repo = make_repo(tmp_path / "linky", world["vault"], "Studio/Cards", "CARD")
    (repo / "CLAUDE.md").write_text(f"@{world['vault']}/Escape/Kernel.md\n", encoding="utf-8")
    git("-C", str(repo), "add", "--", "CLAUDE.md")
    git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-q", "-m", "symlinked region", "--", "CLAUDE.md")

    # The fixture REACHES the guard: no segment carries a dot, so the cheap rule cannot fire and
    # only `region_is_contained` can refuse this. Asserted, because a fixture that quietly failed
    # to reach the guard would leave this test as decorative as the one it replaces.
    assert "." not in "Escape", "the cheap segment rule would fire instead"
    assert common.region_is_contained("Escape") is False, "the containment check did not refuse"
    assert common.region_of_repo(repo) is None


def test_a_dot_dot_that_stays_INSIDE_the_vault_is_still_refused(world, tmp_path):
    """The segment rule inside `region_is_contained` is not redundant with the realpath check,
    and a mutation is how that was settled rather than by reading.

    `Studio/../Cards` resolves to a real region inside the vault, so realpath containment ACCEPTS
    it; only the segment rule refuses. It matters because the returned string is joined onto the
    vault by three callers and handed around as a region NAME — two names for one region means an
    Inbox row filed where the owning lane will not look for it."""
    common = common_for(world)
    (world["vault"] / "Studio" / "Cards").mkdir(parents=True, exist_ok=True)
    import os                                                   # noqa: PLC0415
    inside = os.path.realpath(str(world["vault"] / "Studio" / ".." / "Cards"))
    assert inside.startswith(os.path.realpath(str(world["vault"]))), \
        "fixture broken: this path does not stay inside the vault, so realpath would refuse it"
    assert common.region_is_contained("Studio/../Cards") is False


def test_the_traversal_guard_still_holds_on_the_main_checkout_path(world, tmp_path):
    """The `..` half, which the CHEAP segment rule catches — not `region_is_contained`; the test
    above is that guard's control. Kept because both sources must refuse it after the refactor."""
    common = common_for(world)
    repo = make_repo(tmp_path / "eviltree", world["vault"], "Studio/Cards", "CARD")
    (repo / "CLAUDE.md").write_text(f"@{world['vault']}/../Desktop/Kernel.md\n", encoding="utf-8")
    git("-C", str(repo), "add", "--", "CLAUDE.md")
    git("-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-q", "-m", "traversal", "--", "CLAUDE.md")
    wt = worktree_of(repo, "eviltree-wt")
    assert common.region_of_repo(repo) is None
    assert common.region_of_repo(wt) is None


# ------------------------------------------------------------------ end to end ----

def test_a_foreign_write_from_a_DIVERGENT_worktree_files_under_the_right_region(world, tmp_path):
    """The whole point, driven through the real hook: a foreign lane's worktree on a branch that
    names another region must still file its row under the repository's OWN region."""
    other = make_repo(tmp_path / "vocab", world["vault"], "Studio/Vocab", "VOCAB")
    (world["vault"] / "Studio" / "Cards").mkdir(parents=True, exist_ok=True)
    wt = worktree_of(other, "vocab-wt-DIVERGENT")
    rewrite_claude_md(wt, f"@{world['vault']}/Studio/Cards/Kernel.md\n")
    declare_lane(world, "CARD")
    f = wt / "report.md"; f.write_text("x\n", encoding="utf-8")

    inbox_call(world, cwd=world["vault"], target=f)
    assert (world["vault"] / "Studio" / "Vocab" / "Inbox.md").exists(), \
        "the row did not land under the repository's own region"
    assert not (world["vault"] / "Studio" / "Cards" / "Inbox.md").exists(), \
        "the row landed under the region the worktree's BRANCH named"
