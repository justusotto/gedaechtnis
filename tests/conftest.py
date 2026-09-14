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

**What this can and cannot see — three limits, none of them hidden.**

1. It catches a WRITE, not a read, and it names the SUITE rather than the test. That is the trade
   for one walk before and one after instead of two per test, and a write is the half that does
   damage. A read that reaches the real vault still produces a wrong test result, which is what the
   per-module discipline is for.

2. **A concurrent writer produces the same signal.** This vault is built for them: background
   session-end commits, other lanes, a person editing in an editor. A commit landing inside a test
   run's window fails the suite with a message about the suite. The failure therefore NAMES THE
   PATHS THAT MOVED, because that is what tells the two apart: fixture-shaped paths are the
   suite's doing, somebody's region file is not. It stays a failure rather than a warning — a
   warning about the one file class that must never be corrupted is a warning nobody reads.

3. **The first version was blind in a way that mattered here.** It fingerprinted git's view — HEAD,
   `status --porcelain`, top-level names — which says nothing about a GITIGNORED file being
   modified, or about a file created and removed inside the window. In this vault the gitignored
   files are exactly the hook-written status files that are @-imported into every session and whose
   absence fails silently. A reviewer reproduced the false PASS. It walks the tree instead now, so
   git's opinion of a file is irrelevant to whether a change to it is seen.
"""
from __future__ import annotations
import os, subprocess
from pathlib import Path
import pytest

# Captured at COLLECTION time, before any test has had a chance to move the environment.
REAL_VAULT = Path(os.path.expanduser(os.environ.get("GEDAECHTNIS_VAULT") or "~/Atlas"))


def fingerprint(vault: Path) -> dict[str, tuple[int, int]] | None:
    """Every file in the real vault with its size and mtime, or None when there is no vault here.

    Deliberately NOT git's view. Git cannot see a modification to an ignored file, and the ignored
    files here are the hook-written status files every session boots with. `.git/` itself is
    skipped: its internals move on their own (packing, reflogs) and a change there is never the
    thing this guard is looking for."""
    if not vault.is_dir():
        return None
    out: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            q = Path(dirpath) / name
            try:
                st = q.stat()
            except OSError:
                # An unreadable file is recorded as unreadable, never dropped: dropping it would
                # make "I could not look" identical to "it is not there".
                out[str(q)] = (-1, -1)
                continue
            out[str(q)] = (st.st_size, st.st_mtime_ns)
    return out


def changed(before, after) -> list[str]:
    if before is None or after is None:
        return []
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))


@pytest.fixture(scope="session", autouse=True)
def the_suite_leaves_the_real_vault_alone():
    before = fingerprint(REAL_VAULT)
    yield
    moved = changed(before, fingerprint(REAL_VAULT))
    assert not moved, (
        f"THE REAL VAULT AT {REAL_VAULT} CHANGED DURING THE SUITE. {len(moved)} path(s) moved:\n  "
        + "\n  ".join(moved[:10])
        + (f"\n  ... and {len(moved) - 10} more" if len(moved) > 10 else "")
        + "\n\nIf those look like a test's fixture, a test pointed a module at the real vault "
          "instead of its own — check for an in-process import of a module whose VAULT was already "
          "resolved, and drive that code through a subprocess with the fixture's environment. "
          "If they look like somebody's actual work, another writer touched the vault while the "
          "suite ran and this is a false alarm; that ambiguity is why the paths are printed.")
