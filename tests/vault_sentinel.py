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


def _git(vault: Path, *args) -> str:
    try:
        r = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=20)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def head_of(vault: Path) -> str:
    return _git(vault, "rev-parse", "HEAD").strip()


def attribute(moved: list, roots: dict, head_before: str) -> tuple:
    """Split the moved paths into (the suite's, a concurrent committer's).

    ## Why this exists, and why it is not a loosening

    A vault like this one has writers that are not the suite: a background session-end commit,
    another lane, a person in an editor. Their writes are indistinguishable from a stray test's by
    content, and the first version said so honestly and failed on both. Measured over two full runs
    on 2026-09-15: zero false positives in a quiet five minutes, then FIVE in a run made while the
    session hosting it was committing. A guard that cries wolf during ordinary work is a guard
    people learn to tail past — which is precisely how the incident it was built for went unread
    for hours.

    The discriminator is what happens to the file AFTERWARDS. A concurrent session-end commit
    LEAVES THE FILE COMMITTED: the vault's HEAD moves and the path is clean against it. A stray
    test writes and walks away: the path is DIRTY, because no test is going to commit the user's
    vault. So a moved path that is (a) clean against the working tree and (b) part of the range
    `head_before..HEAD` is attributed to the other writer; everything else is the suite's and
    still fails.

    The conservative direction is deliberate. Anything the check cannot attribute — no git, no HEAD
    movement, a dirty file, a path outside the repo, a git call that failed — counts as the
    SUITE's. Being unable to tell must never resolve in the suite's favour."""
    vault = roots["trees"][0] if roots["trees"] else None
    if vault is None or not head_before:
        return moved, []
    head_now = head_of(vault)
    if not head_now or head_now == head_before:
        return moved, []          # nobody committed; every change is unattributed, i.e. ours
    committed = set()
    names = _git(vault, "diff", "--name-only", f"{head_before}..{head_now}")
    for rel in names.splitlines():
        if rel.strip():
            committed.add(str((vault / rel.strip()).resolve()))
    dirty = _git(vault, "status", "--porcelain")
    dirty_paths = set()
    for line in dirty.splitlines():
        rel = line[3:].strip().strip('"')
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        if rel:
            dirty_paths.add(str((vault / rel).resolve()))
    ours, theirs = [], []
    for m in moved:
        rm = str(Path(m).resolve())
        (theirs if (rm in committed and rm not in dirty_paths) else ours).append(m)
    return ours, theirs


def report(moved: list, scope: str, roots: dict, theirs: list = ()) -> str:
    head = (f"THE USER'S REAL FILES CHANGED DURING {scope}. {len(moved)} path(s) moved:\n  "
            + "\n  ".join(moved[:12])
            + (f"\n  ... and {len(moved) - 12} more" if len(moved) > 12 else ""))
    if theirs:
        head += (f"\n\n(A further {len(theirs)} path(s) moved and WERE attributed to a concurrent "
                 f"committer — they are committed and clean. Those are not counted above.)")
    return head + (
        f"\n\nWatched: {', '.join(str(t) for t in roots['trees'])}"
        "\n\nThese paths are NOT attributable to another writer: they are dirty in the working tree, "
        "or the vault's HEAD did not move. A concurrent session-end commit leaves its files "
        "committed; a stray test writes and walks away. So this is the suite."
        "\n\nThe usual cause is an in-process import of a module whose path constants were already "
        "resolved under a different environment. Resolve per call, or drive the code through a "
        "subprocess carrying the fixture's environment.")


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


def _baseline(fingerprint):
    r = roots()
    vault = r["trees"][0] if r["trees"] else None
    return r, fingerprint(r), (head_of(vault) if vault else "")


def _check(r, before, head_before, fingerprint, scope):
    moved = changed(before, fingerprint(r))
    if not moved:
        return
    ours, theirs = attribute(moved, r, head_before)
    if ours:
        raise AssertionError(report(ours, scope, r, theirs))
    # Everything was attributable to another writer. Say so on stdout — silence here would hide the
    # fact that the watched tree is moving under the run, which a reader needs in order to judge the
    # next failure.
    print(f"\n[vault sentinel] {len(theirs)} path(s) moved during {scope} and were attributed to a "
          f"concurrent committer (committed and clean). Not the suite.")


@pytest.fixture(scope="session", autouse=True)
def real_files_unchanged_session():
    """Content hash across the whole run. The backstop: it sees a change no narrower scope framed,
    including one made during collection or by a session-scoped fixture's own teardown."""
    r, before, head = _baseline(content_fingerprint)
    yield
    _check(r, before, head, content_fingerprint, "THE SUITE")


@pytest.fixture(scope="module", autouse=True)
def real_files_unchanged_module(request):
    """Content hash around each test module — the scope that names a FILE, and the only one that
    can see a rewrite restoring the original size."""
    r, before, head = _baseline(content_fingerprint)
    yield
    _check(r, before, head, content_fingerprint, f"MODULE {request.node.name}")


@pytest.fixture(autouse=True)
def real_files_unchanged_function(request):
    """`stat` fingerprint around each test — the scope that names the TEST. Cheap on purpose
    (~24 ms over a 3,000-path tree): the per-test question is *which one*, and size-plus-mtime
    answers it for every write that is not a deliberate forgery."""
    r, before, head = _baseline(stat_fingerprint)
    yield
    _check(r, before, head, stat_fingerprint, f"TEST {request.node.nodeid}")
