"""rootguard.py — the last line before a write lands: is this path ours to touch?

## The rule

This package writes in exactly four places, and a write anywhere else is a bug rather than a
feature:

  * the resolved **vault** — the user's memory;
  * the resolved **state directory** — logs, cursors, locks, the mode file;
  * the resolved **config file** — one file, written only by the installer and `--decline`;
  * the resolved **worktrees directory** — copy-on-write clones.

Anything else is refused. `permit()` raises `OutsideRoot`; it never returns False and never
`assert`s, because an assertion disappears under `python -O` and a guard that can be optimised
out of a shipped plugin is not a guard.

## Why a runtime guard and not a rule in the docs

On 2026-09-15 this package compacted 263 files of a real memory vault during a test run. Nothing
in the code was wrong about *what* to write — the bug was entirely about *where*, and every layer
that could have noticed was reasoning about paths it had been handed. The docstrings all said the
right thing. A rule a machine can decide with no judgment does not belong in prose.

## The pytest clause, and why it is stricter rather than looser

Under pytest the ordinary roots are NOT enough. A test that has correctly pointed
`GEDAECHTNIS_VAULT` at its own tmp dir is inside its root and passes; a test that failed to — the
incident — is pointed at the user's real vault, which is ALSO "inside the resolved root", and the
ordinary rule waves it through. So when `PYTEST_CURRENT_TEST` is set, the resolved root must
itself lie under the run's temp root, and a root that does not is refused by name. That is the
one place this module knows anything about tests, and it exists because the test environment is
where the resolution is wrong.

The temp root comes from `tempfile.gettempdir()` and from `PYTEST_DEBUG_TEMPROOT`; a suite that
deliberately drives the real vault (there are none, and there should be none) has no hatch here.

## What this cannot do

It guards the paths that go through it. A module that opens a file without calling `permit()` is
not covered — and that gap is not hypothetical: the first version of this file shipped with ZERO
production call sites while this very paragraph claimed a coverage test existed. It did not. Two
independent reviews found it the same day. A docstring that asserts a check nobody wrote is worse
than no docstring, because it stops the next person from looking.

So the claim now names a file that exists: **`tests/test_mutation_coverage.py`** walks the AST of
every product module and fails on any filesystem-mutating call whose enclosing function neither
calls `permit()` nor appears in that file's `EXEMPT` table with a written reason. It has a positive
control (plant an unguarded write, watch the scan name it), a negative control (the guarded shape
must not trip it), a check that the scan is reading the package at all, and a check that no
exemption has gone stale — a stale exemption silently pre-approves whatever is written there next.

What that test does NOT prove: that the permit is on the right path, or that it runs before the
write. It proves somebody considered the question at each site and recorded the answer. The
behavioural half is `tests/test_rootguard.py`; neither substitutes for the other.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402


class OutsideRoot(RuntimeError):
    """A write whose target is not this package's to touch. Never caught to continue past."""


def _norm(p) -> Path:
    """Absolute and symlink-free, WITHOUT requiring the path to exist.

    `resolve()` on a non-existent path is fine on 3.6+, and the parent is resolved too, so a
    symlink pointing out of a root cannot be used to walk past this check."""
    q = Path(os.path.expanduser(str(p)))
    try:
        return Path(os.path.realpath(str(q)))
    except OSError:
        return q if q.is_absolute() else Path(os.path.abspath(str(q)))


def roots(scratch=None) -> list:
    """The directories this package may write in, resolved NOW.

    Resolved per call, never cached at import: the constant that was resolved once per process is
    the whole defect this module is here for, and a guard sharing that defect would bless the very
    write it exists to refuse.

    `scratch` is an explicit extra root a caller declares for a run — a report directory, an export
    target the user named on the command line. It is an argument rather than an environment
    variable so it cannot be set once and forgotten."""
    out = [config.vault(), config.state(), config.config_path(), config.worktrees()]
    if scratch is not None:
        out.append(_norm(scratch))
    return [_norm(p) for p in out]


def _temp_root() -> Path:
    return _norm(os.environ.get("PYTEST_DEBUG_TEMPROOT") or tempfile.gettempdir())


def under(path, root) -> bool:
    p, r = _norm(path), _norm(root)
    return p == r or r in p.parents


def permit(path, why: str = "", scratch=None) -> Path:
    """Return the normalised path, or raise `OutsideRoot`. Call immediately before mutating.

    Two questions, in order. First: is the target inside one of this package's roots? Second, and
    only under pytest: is the ROOT it is inside actually a temporary one? The second is what the
    incident needed — its write was inside the resolved vault, and the resolved vault was the
    user's."""
    p = _norm(path)
    rs = roots(scratch)
    home = _norm(os.path.expanduser("~"))

    if os.environ.get("PYTEST_CURRENT_TEST"):
        tmp = _temp_root()
        # Under pytest, the run's temp root is ALWAYS permitted, whether or not it is one of the
        # configured roots. This is what makes the guard a drop-in for a test-only door: the great
        # majority of this suite drives the package at a `tmp_path` fixture WITHOUT pointing
        # GEDAECHTNIS_VAULT at it, which is correct — the code under test takes the path as an
        # argument. Without this clause a straight swap refuses every one of them (measured: it
        # does), and the refusal would be about the fixture rather than about anything real.
        #
        # It is not a hole. It widens what is allowed only while PYTEST_CURRENT_TEST is set, and
        # only to a directory the test framework owns and destroys. The clause below is the one
        # doing the work in that state: being inside a temp path is permitted, being inside a REAL
        # root is not.
        if under(p, tmp):
            return p
        stray = [r for r in rs if not under(r, tmp)]
        if stray and any(under(p, r) for r in stray):
            raise OutsideRoot(
                f"REFUSED under pytest: {p}\n"
                f"  It is inside {stray[0]}, which is NOT under the test temp root {tmp}.\n"
                f"  That means this process resolved a REAL root — the user's own files — while a "
                f"test was running. The usual cause is a path constant resolved once at import, "
                f"before the test set its environment. Resolve per call, or drive this code in a "
                f"subprocess carrying the test's environment.\n"
                f"  Purpose given: {why or '(none)'}")

    if any(under(p, r) for r in rs):
        return p

    raise OutsideRoot(
        f"REFUSED: {p} is outside every root this package may write.\n"
        f"  Roots: " + ", ".join(str(r) for r in rs) + "\n"
        f"  Purpose given: {why or '(none)'}\n"
        + ("  It is under the user's home directory, which this package never writes wholesale.\n"
           if under(p, home) else "")
        + "  If this write is legitimate, it needs an explicit scratch root declared by its caller, "
          "not a widened rule here.")
