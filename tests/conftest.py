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


def compaction_signature(before, after) -> list[str]:
    """The paths that changed in the shape a COMPACTION makes: a memory file that shrank, in a
    session where `-archive` files also appeared.

    ★ Why this is narrower than "anything changed". The plain version false-failed twice in one
    afternoon on other lanes writing the vault while the suite ran, and a hard failure that cries
    wolf is exactly how the real incident got skimmed past: it fired, printed `1 error`, and was
    read as a neighbouring test's problem. A guard is worth what its failures mean.

    The enforcement now lives in `archive.atomic_write`, which REFUSES a non-temp target under
    pytest and cannot false-positive at all. This is the second net, so it is tuned to the one
    signature the door exists for: a file got smaller and archives appeared beside it. A lane
    editing prose in another region does not look like that."""
    if before is None or after is None:
        return []
    new_archives = [p for p in after
                    if p not in before and "-archive" in Path(p).stem and p.endswith(".md")]
    shrunk = [p for p, v in after.items()
              if p in before and p.endswith(".md") and v[0] < before[p][0]]
    return sorted(shrunk) if (new_archives and shrunk) else []


@pytest.fixture(scope="session", autouse=True)
def the_suite_leaves_the_real_vault_alone():
    before = fingerprint(REAL_VAULT)
    yield
    after = fingerprint(REAL_VAULT)
    moved = changed(before, after)
    compacted = compaction_signature(before, after)
    if moved and not compacted:
        # Informational, NOT a failure: with concurrent writers this fires constantly, and a
        # constant alarm is one nobody reads. The door is what enforces.
        print(f"\n[vault watch] {len(moved)} path(s) under {REAL_VAULT} changed while the suite "
              f"ran — most likely another session, since none of it looks like a compaction: "
              + ", ".join(moved[:5]) + (" ..." if len(moved) > 5 else ""))
    assert not compacted, (
        f"A TEST COMPACTED THE REAL VAULT AT {REAL_VAULT}. {len(compacted)} file(s) shrank while "
        f"archive files appeared beside them:\n  "
        + "\n  ".join(compacted[:10])
        + (f"\n  ... and {len(compacted) - 10} more" if len(compacted) > 10 else "")
        + "\n\nThis is the signature of the 2026-09-15 incident: a module's VAULT was already "
          "resolved when a test set GEDAECHTNIS_VAULT, so an in-process call reached the real "
          "vault with a test-sized limit. Drive that code through a subprocess with "
          "GEDAECHTNIS_VAULT set. Every byte is recoverable from git and from the archive files "
          "beside each shrunken one — restore before doing anything else.")
