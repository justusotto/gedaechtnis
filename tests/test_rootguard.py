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
    """A vault and a state dir inside the run's temp root — what a well-behaved test sets up.

    `PYTEST_DEBUG_TEMPROOT` is pinned to THIS test's `tmp_path` rather than left at the framework
    default. Without that pin the temp root is the whole of `/private/var/folders/.../T`, so a
    probe built as `tmp_path.parent / "elsewhere"` is still inside it and is permitted by the
    temp clause — which silently turned three refusal tests below into tests of nothing the moment
    that clause was added. They FAILED rather than passing vacuously only because the clause
    arrived after they did; a fixture written in the other order would have shipped hollow. Hence
    the premise assertions on every refusal test in this file."""
    v = tmp_path / "vault"; v.mkdir()
    s = tmp_path / "state"; s.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(v))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(s))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("GEDAECHTNIS_WORKTREES", str(tmp_path / "wt"))
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path))
    return v, s


def assert_outside(p):
    """The premise every refusal test rests on, stated once: `p` is outside every configured root
    AND outside the run's temp root, so a refusal is about the rule and not about the fixture."""
    inside = [str(r) for r in rootguard.roots() if rootguard.under(p, r)]
    assert not inside, f"probe {p} has drifted INSIDE {inside} — this test would prove nothing"
    assert not rootguard.under(p, rootguard._temp_root()), \
        f"probe {p} is under the temp root {rootguard._temp_root()}, where writes are permitted"


# ---------------------------------------------------------------- negative control (stays quiet)

def test_permits_a_write_inside_the_vault(sandboxed):
    v, _ = sandboxed
    assert rootguard.permit(v / "Region" / "Position.md", "compaction") == rootguard._norm(v / "Region" / "Position.md")


def test_permits_the_state_dir_and_the_config_file(sandboxed):
    _, s = sandboxed
    rootguard.permit(s / "chore.log", "hook log")
    rootguard.permit(Path(os.environ["GEDAECHTNIS_CONFIG"]), "installer")


def test_permits_a_declared_scratch_root(sandboxed, tmp_path, monkeypatch):
    """A report directory a caller names is legitimate — but it is an ARGUMENT, so it cannot be set
    once and forgotten the way an environment variable can.

    ★ Run with PYTEST_CURRENT_TEST UNSET, deliberately. The scratch mechanism is a PRODUCTION rule,
    and under pytest it is unreachable in both directions: a scratch inside the temp root is
    permitted by the temp clause whether or not it was declared (so the discriminating half cannot
    fire), and a scratch outside the temp root is refused by the stray-root clause even when it was
    declared (so the permitting half cannot fire). Testing it inside a pytest run would therefore
    be testing the clauses, not the mechanism. That the two clauses shadow it completely is itself
    worth knowing — it is why `retention.py`'s scratch is exercised by its own tests through a
    tmp_path review root rather than here."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    out = tmp_path.parent / "reports-outside-the-sandbox"
    assert not any(rootguard.under(out / "run.json", r) for r in rootguard.roots()), \
        "the probe is inside a configured root; the scratch argument would not be what permits it"
    assert rootguard.permit(out / "run.json", "shadow report", scratch=out)
    with pytest.raises(rootguard.OutsideRoot):
        rootguard.permit(out / "run.json", "shadow report")      # same path, undeclared


# ---------------------------------------------------------------- positive control (bites)

def test_refuses_a_path_outside_every_root(sandboxed, tmp_path):
    outside = tmp_path.parent / "somewhere-else" / "notes.md"
    assert_outside(outside)
    with pytest.raises(rootguard.OutsideRoot, match="outside every root"):
        rootguard.permit(outside, "a bug")


def test_refuses_a_write_into_the_users_home(sandboxed):
    victim = Path(os.path.expanduser("~")) / "Documents" / "taxes.pdf"
    assert_outside(victim)
    with pytest.raises(rootguard.OutsideRoot, match="home directory"):
        rootguard.permit(victim, "a worse bug")


def test_a_symlink_cannot_walk_out_of_a_root(sandboxed, tmp_path):
    """Containment is asserted on the REAL path. A symlink inside the vault pointing out of it is
    the oldest way past a prefix check, and `relative_to` on the unresolved path agrees with the
    wrong answer."""
    v, _ = sandboxed
    escape = tmp_path.parent / "escape-target"
    escape.mkdir(exist_ok=True)
    link = v / "door"
    link.symlink_to(escape, target_is_directory=True)
    # The premise is about the REAL destination: the link itself is inside the vault, which is the
    # whole trap. If the target had drifted inside a root this would pass without proving anything.
    assert_outside(escape / "stolen.md")
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


# ---------------------------------------------------------------- premises, asserted
# ★ Adopted from the CR6-FIX session's correction, 2026-09-15. It found that a probe path I had
# suggested (`Path(tempfile.gettempdir()).resolve().parent`) was PERMITTED by its door — so the
# test built on it would have proven nothing while reading as a refusal test. The two doors did not
# even agree: this guard refuses that same path. Two implementations of one rule disagreeing about
# one path is the whole argument for having one.
#
# The durable fix is not a better constant. It is that a refusal test must ASSERT ITS OWN PREMISE —
# that the victim really is outside the allowed set — so that widening the roots later fails the
# test instead of quietly hollowing it out.

def test_the_refusal_probe_asserts_its_own_premise(sandboxed, tmp_path, monkeypatch):
    """A refusal test whose victim has drifted inside the allowed set passes forever, proving
    nothing. So the premise is checked first, and separately from the refusal."""
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path))
    probe = Path("/gedaechtnis-refusal-probe/never-written.md")
    # PREMISE: outside every configured root, and outside the run's temp root.
    assert not any(rootguard.under(probe, r) for r in rootguard.roots()), \
        f"the probe has drifted INSIDE a root — this test would pass without proving anything: {probe}"
    assert not rootguard.under(probe, rootguard._temp_root()), \
        f"the probe is under the temp root, where writes are permitted: {probe}"
    # ...and only then, the claim.
    with pytest.raises(rootguard.OutsideRoot):
        rootguard.permit(probe, "the refusal probe")
    assert not probe.exists(), "the probe path must never be created by asserting on it"


def test_a_tmp_path_fixture_is_permitted_under_pytest(tmp_path, monkeypatch):
    """★ The drop-in clause, and the reason it exists. Most of this suite drives the package at a
    `tmp_path` WITHOUT pointing GEDAECHTNIS_VAULT there — correctly, because the code under test
    takes the path as an argument. Measured before this clause existed: a straight swap of
    permit() into `archive.atomic_write` refused every one of them."""
    monkeypatch.delenv("GEDAECHTNIS_VAULT", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "a test is running")
    victim = tmp_path / "Region" / "Position.md"
    victim.parent.mkdir(parents=True)
    # PREMISE: this really is outside the configured roots, so the pass below is the clause working
    # and not the ordinary rule letting it through.
    assert not any(rootguard.under(victim, r) for r in rootguard.roots()), \
        "the fixture is inside a configured root; this test is not exercising the temp clause"
    assert rootguard.permit(victim, "an ordinary archive test fixture") == rootguard._norm(victim)


def test_the_temp_clause_does_not_leak_into_production(tmp_path, monkeypatch):
    """The discriminating half: the same permitted-under-pytest path is REFUSED with
    PYTEST_CURRENT_TEST unset. A clause that survived into production would be a real hole."""
    monkeypatch.delenv("GEDAECHTNIS_VAULT", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    victim = tmp_path / "Region" / "Position.md"
    victim.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(rootguard.OutsideRoot):
        rootguard.permit(victim, "the same path, outside a test run")
