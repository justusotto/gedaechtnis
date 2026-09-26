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

# Transient COORDINATION state, not memory. A lock exists to be taken and dropped; it is written
# and removed constantly by whatever else is using the vault, it is untracked (so the
# committed-and-clean attribution can never clear it), and nothing in it is a user's content. It is
# the one category that produces a standing alarm on a machine where anything else is running.
#
# The exclusion is deliberately by NAME and deliberately short. It is not "skip what looks
# temporary": a `.tmp` file beside a role file is a half-written MEMORY file and stays watched.
_TRANSIENT_NAMES = {".atlas-writer.lock"}
_TRANSIENT_DIRS = {".atlas-locks"}


def _walk(tree: Path):
    """Every file under `tree`, skipping `.git` internals and coordination locks.

    `.git` moves on its own (packing, reflogs) and a change there is never what this is looking for.
    The lock exclusions are argued above — they are the only two, and widening this set is how a
    guard stops guarding."""
    if not tree.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d != ".git" and d not in _TRANSIENT_DIRS]
        for name in filenames:
            if name in _TRANSIENT_NAMES:
                continue
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


# ---------------------------------------------------------------- what THIS process wrote
# ★ Added 2026-09-15 after the CR6-FIX session hit a false positive the attribution rule below
# could not see: the vault's own hook-edit ritual is BACKUP, EDIT, COMMIT LATER, so a perfectly
# legitimate concurrent writer sits DIRTY for minutes — and "dirty" was the rule's evidence for
# "a stray test wrote this and walked away". Three of its errors named
# `.hooks/atlas-stop-hook.sh` and a `.pre-v7.10-…` sibling: the maintenance session installing a
# hook version, mid-ritual.
#
# That session proposed keying on the `.pre-<desc>-<DATE>` sibling, which is that ritual's
# signature. DECLINED, and the reason matters more than the decision: it is one vault's editing
# convention, and teaching a SHIPPED plugin to recognise it is the hard-coded `~/Atlas` fallback
# in a new costume — right on this machine, silently inert on anyone else's.
#
# The generic fix is to stop inferring and start MEASURING. An audit hook records every path this
# process opens for writing, renames or removes. A changed path in that set was written by us, as
# a fact rather than an inference.
#
# THE LIMIT, NAMED: an audit hook sees this process only. Much of this suite drives the product
# through a subprocess, and those writes are invisible here. So the set makes "ours" CERTAIN and
# never makes "not ours" certain — which is why an unrecorded change still fails, and why the
# message below no longer claims to know which it is. Being unable to tell is reported as being
# unable to tell.

_WRITTEN: set = set()
_AUDIT_INSTALLED = False


def _audit(event: str, args):
    try:
        if event == "open":
            path, mode = args[0], args[1]
            if path and mode and any(c in str(mode) for c in "wax+"):
                _WRITTEN.add(os.path.realpath(str(path)))
        elif event in ("os.rename", "os.replace", "os.link", "os.symlink"):
            for a in args[:2]:
                if a:
                    _WRITTEN.add(os.path.realpath(str(a)))
        elif event in ("os.remove", "os.unlink", "os.rmdir", "os.mkdir", "os.truncate", "os.chmod"):
            if args and args[0]:
                _WRITTEN.add(os.path.realpath(str(args[0])))
        elif event.startswith("shutil."):
            for a in args[:2]:
                if a:
                    _WRITTEN.add(os.path.realpath(str(a)))
    except Exception:
        pass            # an audit hook must never be able to fail a program


def install_audit() -> None:
    """Start recording this process's own writes. Idempotent; an audit hook cannot be removed."""
    global _AUDIT_INSTALLED
    if not _AUDIT_INSTALLED:
        sys.addaudithook(_audit)
        _AUDIT_INSTALLED = True


def written_by_us(paths) -> list:
    return [p for p in paths if os.path.realpath(str(p)) in _WRITTEN]


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


def report(moved: list, scope: str, roots: dict, theirs: list = (), certain: list = ()) -> str:
    head = (f"THE USER'S REAL FILES CHANGED DURING {scope}. {len(moved)} path(s) moved:\n  "
            + "\n  ".join(moved[:12])
            + (f"\n  ... and {len(moved) - 12} more" if len(moved) > 12 else ""))
    if certain:
        head += ("\n\n★ THIS PROCESS WROTE " + ("IT" if len(certain) == 1 else f"{len(certain)} OF THEM")
                 + " — recorded by an audit hook at the moment of the write, so this is a fact, not "
                   "an inference:\n  " + "\n  ".join(certain[:8]))
    if theirs:
        head += (f"\n\n(A further {len(theirs)} path(s) moved and were attributed to a concurrent "
                 f"committer — committed and clean at a HEAD this run did not make. Not counted above.)")
    tail = (f"\n\nWatched: {', '.join(str(t) for t in roots['trees'])}")
    if certain:
        return head + tail + (
            "\n\nA test in this process wrote the user's files directly. Resolve paths per call, or "
            "drive the code through a subprocess carrying the fixture's environment.")
    return head + tail + (
        "\n\nTHIS PROCESS DID NOT WRITE THESE — no audit record exists for them. They were written "
        "either by a SUBPROCESS of this run (which the audit hook cannot see) or by something else "
        "on this machine. This check cannot tell those apart, and says so rather than guessing: the "
        "run fails because being unable to tell must not resolve in the suite's favour."
        "\n\nTo tell them apart: re-run the module alone. If it reproduces, it is a subprocess of "
        "the suite. If it does not, another writer was active — a session-end commit, an editor, or "
        "a hook-edit ritual that leaves a file dirty between its backup and its commit.")


# ---------------------------------------------------------------- the fixtures themselves
# They live HERE rather than in conftest.py so that the positive control can install the very same
# objects into a throwaway suite and prove they bite. A control that exercises a COPY of the guard
# proves something about the copy; conftest.py therefore re-exports these three names and adds
# nothing, and `test_vault_sentinel.py` asserts that it still does.

import pytest  # noqa: E402  (deliberately after the pure helpers above, which import nothing heavy)

install_audit()          # before any test runs

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
        certain = written_by_us(ours)
        raise AssertionError(report(ours, scope, r, theirs, certain))
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
