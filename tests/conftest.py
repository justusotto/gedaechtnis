"""Suite-wide guard: the tests must not touch the machine's real vault.

Ten test modules open by promising they never read or write the real vault. Nothing enforced it,
and on 2026-09-14 one of them did: `test_cleanup.py`'s scan-vs-apply test repointed
GEDAECHTNIS_VAULT and reloaded `cleanup`, but `config.VAULT` had already been resolved and cached
in `sys.modules` by an earlier test in the same process — so `propose()` ran against the real
`~/Atlas` and came back with 52 proposals about the owner's own memory. It failed on the assertion
that followed, before `apply()` could write anything, which is luck rather than design: that module
rewrites memory files and commits them.

So the promise moves out of ten docstrings and into one fixture. A rule a script can decide with no
judgment is enforced, not remembered.

**What this can and cannot see.** It fingerprints the real vault ONCE before the suite and once
after: HEAD, the porcelain status, and the top-level entry names. It therefore catches a write, not
a read, and it names the suite rather than the test. That is the honest trade for two git calls
instead of two per test — and a write is the half that does damage. A read that reaches the real
vault still produces a wrong test result, which is what the per-module discipline is for.
"""
from __future__ import annotations
import os, subprocess
from pathlib import Path
import pytest

# Captured at COLLECTION time, before any test has had a chance to move the environment.
REAL_VAULT = Path(os.path.expanduser(os.environ.get("GEDAECHTNIS_VAULT") or "~/Atlas"))


def fingerprint(vault: Path) -> str | None:
    """What the real vault looks like right now, or None when there is no real vault here."""
    if not vault.is_dir():
        return None
    parts = [",".join(sorted(p.name for p in vault.iterdir()))]
    for args in (["rev-parse", "HEAD"], ["status", "--porcelain"]):
        p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        # A non-zero rc is recorded AS ITSELF rather than as an empty string: "git failed" and
        # "the vault is clean" are opposite facts, and folding them together is the exact defect
        # class this package exists to end.
        parts.append(p.stdout if p.returncode == 0 else f"RC{p.returncode}")
    return "\x00".join(parts)


@pytest.fixture(scope="session", autouse=True)
def the_suite_leaves_the_real_vault_alone():
    before = fingerprint(REAL_VAULT)
    yield
    after = fingerprint(REAL_VAULT)
    assert after == before, (
        f"THE SUITE WROTE TO THE REAL VAULT AT {REAL_VAULT}. A test pointed a module at it instead "
        f"of its fixture — check for an in-process import of a module whose VAULT was already "
        f"resolved, and drive that code through a subprocess with the fixture's own environment.")
