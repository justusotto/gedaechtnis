"""vault_sentinel.py — the suite's tripwire on the user's real files. Imported by conftest.

## What it is for

On 2026-09-15 a test called this package's compactor in-process. `config.VAULT` is resolved ONCE
per process at import, so the test's `GEDAECHTNIS_VAULT` override was ignored, and the compactor
ran against the machine's real memory vault under a 1,500-byte test bound: 263 files rewritten,
1,749 archive segments opened. The suite's existing session-scoped guard DID fire. It fired at the
END of the run, in one assertion about "the suite", long after the write — and it was misread as
a concurrent writer, which is the one other thing it can mean.

So this module exists to change three things about that signal.

1. **It fires at the module boundary, not at the end of the session.** The question a failure has
   to answer is *which test did this*, and a session-scoped check cannot answer it. A per-function
   `stat` fingerprint (measured: 6 ms over ~1,000 files) narrows it to the test; a per-module
   content hash (105 ms) catches the rewrite a `stat` cannot see, because a file restored to its
   original size with a touched mtime is still a corrupted file.

2. **It watches more than the vault.** The vault is the loudest target, not the only one: the
   install's config file, the user-level memory file and the editor's settings are all reachable
   by the same defect, and a test that rewrites the config file redirects every later session on
   the machine. Those are watched by name. The state directory's *logs* are not — the live session
   running the suite writes them itself, and a guard with a standing false positive is a guard
   people learn to ignore.

3. **It names nothing.** The previous version fell back to a literal `~/Atlas`, which is one
   machine's layout compiled into a plugin other people install; on any of their machines the
   fallback watched a directory that does not exist and the guard silently watched nothing. The
   roots come from the package's own resolver, run in a SUBPROCESS with the environment as it was
   before any test touched it — a subprocess because the whole defect being guarded is that an
   in-process resolution is already cached and will not be re-read.

## What it still cannot see

A **concurrent writer** produces the same signal: this kind of vault is written by background
session-end commits and by the person using it. That is why every failure PRINTS THE PATHS — a
fixture-shaped path is the suite's doing, somebody's own memory file is not. It stays a failure
and never a warning: a warning about the one file class that must never be corrupted is a warning
nobody reads.

A **read** that reaches the user's files is invisible here. It produces a wrong test result rather
than damage, and catching it costs a different instrument.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent

# ---------------------------------------------------------------- the roots to watch

# Snapshotted at IMPORT time, which is collection time: before any test has had the chance to move
# the environment out from under the question "what would a real session use?".
_PRISTINE_ENV = dict(os.environ)

_RESOLVER = (
    "import sys; sys.path.insert(0, %r); import config; "
    "print(config.VAULT); print(config.STATE); print(config.CONFIG_PATH); print(config.USER_MEMORY)"
)


def resolved_roots() -> dict:
    """Ask the package where a REAL session's files are, in a subprocess with the pristine env.

    A subprocess and not an import, for the reason the whole module exists: this process may
    already hold a `config` whose module-level paths were resolved under some test's environment,
    and asking it would return the fake vault and watch nothing. The subprocess has no such
    history. A failure to run it is not silently tolerated — it would leave the suite unguarded,
    which is indistinguishable from a pass."""
    code = _RESOLVER % str(PLUGIN / "hooks")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=_PRISTINE_ENV, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError(
            "vault sentinel could not resolve the real roots, so the suite would run UNGUARDED "
            "over the user's own files. Refusing rather than proceeding blind.\n" + p.stderr[-2000:])
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    if len(lines) != 4:
        raise RuntimeError(f"vault sentinel expected 4 resolved paths, got {len(lines)}: {lines!r}")
    vault, state, config_path, user_memory = (Path(l) for l in lines)
    home = Path(os.path.expanduser("~"))
    return {
        # Walked whole: everything under it is the user's memory.
        "trees": [vault],
        # Watched by name. The state directory's logs are deliberately absent — the live session
        # running this suite writes them, and that would be a standing false positive.
        "files": [config_path, user_memory,
                  home / ".claude" / "settings.json",
                  home / ".claude" / "settings.local.json",
                  state / "config.json"],
    }


# ---------------------------------------------------------------- the two instruments

def _walk(tree: Path):
    """Every file under `tree`, skipping `.git` internals — they move on their own (packing,
    reflogs) and a change there is never what this is looking for."""
    if not tree.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            yield Path(dirpath) / name


def stat_fingerprint(roots: dict) -> dict:
    """path -> (size, mtime_ns). ~6 ms over a 1,000-file vault, so it can run per TEST.

    An unreadable file is recorded as unreadable rather than dropped: dropping it would make
    "I could not look" indistinguishable from "it is not there", and a guard whose blind spot
    reads as a pass is the failure mode this package keeps meeting."""
    out = {}
    for tree in roots["trees"]:
        for q in _walk(tree):
            try:
                st = q.stat()
                out[str(q)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                out[str(q)] = (-1, -1)
    for f in roots["files"]:
        try:
            st = f.stat()
            out[str(f)] = (st.st_size, st.st_mtime_ns)
        except OSError:
            out[str(f)] = None          # absent is a state, and its APPEARANCE is a change
    return out


def content_fingerprint(roots: dict) -> dict:
    """path -> sha256 of the bytes. ~105 ms over a 1,000-file vault, so it runs per MODULE.

    This is the half that catches a rewrite `stat` agrees with: same size, and a mtime nobody
    compares against a clock."""
    out = {}
    def digest(q: Path):
        try:
            h = hashlib.sha256()
            with open(q, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return "UNREADABLE"
    for tree in roots["trees"]:
        for q in _walk(tree):
            out[str(q)] = digest(q)
    for f in roots["files"]:
        out[str(f)] = digest(f) if f.exists() else None
    return out


def changed(before: dict, after: dict) -> list:
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k, "\0ABSENT") != after.get(k, "\0ABSENT"))


def report(moved: list, scope: str, roots: dict) -> str:
    head = (f"THE USER'S REAL FILES CHANGED DURING {scope}. {len(moved)} path(s) moved:\n  "
            + "\n  ".join(moved[:12])
            + (f"\n  ... and {len(moved) - 12} more" if len(moved) > 12 else ""))
    return head + (
        f"\n\nWatched: {', '.join(str(t) for t in roots['trees'])}"
        "\n\nIf those paths look like a test's own fixture, a test drove this package at the REAL "
        "root instead of its own — the usual cause is an in-process import of a module whose "
        "path constants were already resolved under a different environment. Resolve per call, or "
        "drive the code through a subprocess carrying the fixture's environment."
        "\n\nIf they look like somebody's actual work, another writer touched them while the suite "
        "ran and this is a false alarm. That ambiguity is exactly why the paths are printed.")


# ---------------------------------------------------------------- the fixtures themselves
# They live HERE rather than in conftest.py so that the positive control can install the very same
# objects into a throwaway suite and prove they bite. A control that exercises a COPY of the guard
# proves something about the copy; conftest.py therefore re-exports these three names and adds
# nothing, and `test_vault_sentinel.py` asserts that it still does.

import pytest  # noqa: E402  (deliberately after the pure helpers above, which import nothing heavy)

_ROOTS = None


def roots():
    """The resolved roots, computed once per process."""
    global _ROOTS
    if _ROOTS is None:
        _ROOTS = resolved_roots()
    return _ROOTS


@pytest.fixture(scope="session", autouse=True)
def real_files_unchanged_session():
    """Content hash across the whole run. The backstop: it sees a change no narrower scope framed,
    including one made during collection or by a session-scoped fixture's own teardown."""
    r = roots()
    before = content_fingerprint(r)
    yield
    moved = changed(before, content_fingerprint(r))
    if moved:
        raise AssertionError(report(moved, "THE SUITE", r))


@pytest.fixture(scope="module", autouse=True)
def real_files_unchanged_module(request):
    """Content hash around each test module — the scope that names a FILE, and the only one that
    can see a rewrite restoring the original size."""
    r = roots()
    before = content_fingerprint(r)
    yield
    moved = changed(before, content_fingerprint(r))
    if moved:
        raise AssertionError(report(moved, f"MODULE {request.node.name}", r))


@pytest.fixture(autouse=True)
def real_files_unchanged_function(request):
    """`stat` fingerprint around each test — the scope that names the TEST. Cheap on purpose
    (~24 ms over a 3,000-path tree): the per-test question is *which one*, and size-plus-mtime
    answers it for every write that is not a deliberate forgery."""
    r = roots()
    before = stat_fingerprint(r)
    yield
    moved = changed(before, stat_fingerprint(r))
    if moved:
        raise AssertionError(report(moved, f"TEST {request.node.nodeid}", r))
