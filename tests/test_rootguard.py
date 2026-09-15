"""The runtime root guard: a write outside this package's roots is REFUSED, and a write inside is
not. Both halves, because a guard proven only to fire is a guard nobody can afford to keep.

The third test is the one the incident needed and the ordinary rule could not give: under pytest,
being inside the resolved vault is not enough, because *the resolved vault was the user's*.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
import rootguard  # noqa: E402


@pytest.fixture
def sandboxed(tmp_path, monkeypatch):
    """A vault and a state dir inside the run's temp root — what a well-behaved test sets up."""
    v = tmp_path / "vault"; v.mkdir()
    s = tmp_path / "state"; s.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(v))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(s))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("GEDAECHTNIS_WORKTREES", str(tmp_path / "wt"))
    return v, s


# ---------------------------------------------------------------- negative control (stays quiet)

def test_permits_a_write_inside_the_vault(sandboxed):
    v, _ = sandboxed
    assert rootguard.permit(v / "Region" / "Position.md", "compaction") == rootguard._norm(v / "Region" / "Position.md")


def test_permits_the_state_dir_and_the_config_file(sandboxed):
    _, s = sandboxed
    rootguard.permit(s / "chore.log", "hook log")
    rootguard.permit(Path(os.environ["GEDAECHTNIS_CONFIG"]), "installer")


def test_permits_a_declared_scratch_root(sandboxed, tmp_path):
    """A report directory a caller names is legitimate — but it is an ARGUMENT, so it cannot be set
    once and forgotten the way an environment variable can."""
    out = tmp_path / "reports"
    rootguard.permit(out / "run.json", "shadow report", scratch=out)
    with pytest.raises(rootguard.OutsideRoot):
        rootguard.permit(out / "run.json", "shadow report")      # same path, undeclared


# ---------------------------------------------------------------- positive control (bites)

def test_refuses_a_path_outside_every_root(sandboxed, tmp_path):
    outside = tmp_path.parent / "somewhere-else" / "notes.md"
    with pytest.raises(rootguard.OutsideRoot, match="outside every root"):
        rootguard.permit(outside, "a bug")


def test_refuses_a_write_into_the_users_home(sandboxed):
    with pytest.raises(rootguard.OutsideRoot, match="home directory"):
        rootguard.permit(Path(os.path.expanduser("~")) / "Documents" / "taxes.pdf", "a worse bug")


def test_a_symlink_cannot_walk_out_of_a_root(sandboxed, tmp_path):
    """Containment is asserted on the REAL path. A symlink inside the vault pointing out of it is
    the oldest way past a prefix check, and `relative_to` on the unresolved path agrees with the
    wrong answer."""
    v, _ = sandboxed
    escape = tmp_path.parent / "escape-target"
    escape.mkdir(exist_ok=True)
    link = v / "door"
    link.symlink_to(escape, target_is_directory=True)
    with pytest.raises(rootguard.OutsideRoot):
        rootguard.permit(link / "stolen.md", "via a symlink")


# ---------------------------------------------------------------- the pytest clause

def test_under_pytest_a_REAL_root_is_refused_even_though_it_is_the_resolved_root(tmp_path, monkeypatch):
    """★ The incident, exactly. The write was INSIDE the resolved vault — the ordinary rule waves it
    through — and the resolved vault was the user's own, because the constant had been frozen before
    the test set its environment. So under pytest the ROOT itself must be under the temp root."""
    real_ish = tmp_path.parent.parent / "not-a-temp-root-vault"
    real_ish.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(real_ish))
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path))
    assert os.environ.get("PYTEST_CURRENT_TEST"), "this test's premise is that pytest marks the run"
    with pytest.raises(rootguard.OutsideRoot, match="NOT under the test temp root"):
        rootguard.permit(real_ish / "Region" / "Position.md", "compaction under a test bound")


def test_outside_pytest_the_same_write_is_permitted(tmp_path, monkeypatch):
    """The discriminating half: the pytest clause must be the ONLY thing that made the case above
    fail. In production that identical write is this package doing its job."""
    real_ish = tmp_path.parent.parent / "not-a-temp-root-vault"
    real_ish.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(real_ish))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    rootguard.permit(real_ish / "Region" / "Position.md", "compaction, for real")


# ---------------------------------------------------------------- the guard's own shape

def test_the_guard_raises_and_never_asserts():
    """`assert` disappears under `python -O`. A guard that can be optimised out of a shipped plugin
    is not a guard, and this package ships."""
    src = (PLUGIN / "hooks" / "rootguard.py").read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    asserts = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    assert not asserts, f"rootguard uses `assert` at line(s) {asserts} — use `raise`"


def test_the_guard_resolves_its_roots_per_call(sandboxed, tmp_path, monkeypatch):
    """A guard that cached its own roots at import would bless exactly the write it exists to
    refuse — the defect, one layer up."""
    first = rootguard.roots()
    moved = tmp_path / "moved-vault"; moved.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(moved))
    assert rootguard.roots() != first
    assert rootguard._norm(moved) in rootguard.roots()
