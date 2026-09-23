#!/usr/bin/env python3
"""procs.py — the two process questions this package asks: which Claude am I under, and is it alive.

Both answers were already in `claim.py`, which needed them to decide who owns a region lock. The
worktree sweep needs the same two answers for a different reason — a worktree that is a LIVE
session's working directory must never be removed, however clean it looks — and a second
implementation of a parent-chain walk is exactly the duplicated fact this package refuses
elsewhere. So the implementation moved here and `claim.py` imports it; its behaviour is unchanged
and `test_claim.py` is what says so.

No module-level state, no config, no vault: this module answers questions about the machine, and
is safe to import from any hook.
"""
from __future__ import annotations
import os, subprocess


def ps_info(pid: int) -> tuple[int, str] | None:
    """(ppid, full command) for one pid, or None if it cannot be read."""
    try:
        p = subprocess.run(["ps", "-o", "ppid=,command=", "-p", str(pid)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    line = (p.stdout or "").strip()
    if p.returncode != 0 or not line:
        return None
    head, _, rest = line.partition(" ")
    try:
        return int(head), rest.strip()
    except ValueError:
        return None


def is_claude(cmd: str) -> bool:
    """True when a command line RUNS claude, not merely when it MENTIONS it.

    Every process in the hook's parent chain mentions it: the hook's own command line contains the
    plugin's path under a `.claude` directory, and the shell that ran it inherits that string. A
    substring test would therefore match the transient shell — the one process whose pid must never
    end up in a lock file. So the test is on each argument's BASENAME: `.claude` is a directory
    component, never a basename, while `claude`, `/usr/local/bin/claude` and `bin/claude` all
    reduce to the basename `claude`."""
    for tok in cmd.split():
        if os.path.basename(tok.rstrip("/")) == "claude":
            return True
    return False


def claude_pid(max_hops: int = 6) -> int | None:
    """The Claude Code process this hook is running under, found by walking the parent chain.

    Returns None rather than a guess. There is no fallback to the hook's own pid or to a shell's:
    a lock owned by a process that exits in milliseconds is worse than no lock, because it reads
    as a live holder to `status` and as a reapable wedge to everyone else."""
    me = os.getpid()
    cur = me
    for _ in range(max_hops):
        info = ps_info(cur)
        if not info:
            return None
        ppid = info[0]
        if ppid <= 1:
            return None
        parent = ps_info(ppid)
        if not parent:
            return None
        if is_claude(parent[1]):
            return ppid if ppid != me else None
        cur = ppid
    return None


def pid_alive(pid: int | None) -> bool:
    """Whether a pid names a live process on THIS host.

    ★ The return value is used to decide whether a directory may be DELETED, so the two error
    directions are not symmetric and this function is deliberately biased. `os.kill(pid, 0)` sends
    no signal; it asks the kernel to resolve the pid and report permission. Three outcomes:

      * no exception       — the process exists and is ours: ALIVE.
      * `PermissionError`  — the process exists and belongs to another user: ALIVE. A pid we may
                             not signal is still a pid that is running, and reading this as dead
                             would be reading "I am not allowed to look" as "nothing is there".
      * `ProcessLookupError` — no such process: DEAD. The only outcome that permits a removal.

    A `pid` of None, 0 or negative is NOT alive but also NOT a licence: callers treat an ABSENT
    pid as "cannot tell" and keep the worktree. `os.kill(0, 0)` addresses the whole process GROUP
    and `os.kill(-1, 0)` every process the user owns — both would answer a question nobody asked,
    so they are refused here rather than at each call site."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True                      # an unexpected errno is not evidence of death
    return True


def session_name(pid: int | None) -> str | None:
    """The `--name` a Claude session was launched with, or None.

    This is how a session finds out what it is CALLED, which nothing in the hook payload carries:
    the payload has a session id (a uuid, meaningless to a person) and the transcript records the
    conversation, not the launch. The name is the only handle a fleet gives a session that another
    session can address, so a door that reports one session's state to another has to read it off
    the process.

    Both spellings, because both are in use: `--name X` and `--name=X`. A value is returned
    verbatim apart from surrounding quotes; nothing here interprets it — `match_name` does that,
    against a pattern the vault owns.

    Never raises, and returns None rather than a guess. A missing name is the SHIPPED state of an
    ordinary interactive session, so None is the common answer and must cost nothing."""
    if pid is None or pid <= 0:
        return None
    info = ps_info(pid)
    if not info:
        return None
    toks = info[1].split()
    for i, tok in enumerate(toks):
        if tok == "--name":
            if i + 1 < len(toks):
                return toks[i + 1].strip("'\"") or None
            return None
        if tok.startswith("--name="):
            return tok[len("--name="):].strip("'\"") or None
    return None
