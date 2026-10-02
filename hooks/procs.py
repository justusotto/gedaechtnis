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
    end up in a lock file. So the test is on the EXECUTABLE (`_exe_index`): a token whose basename
    is `claude` or `claude.exe` with no flag before it.

    PROCSHOST-1: this was "any argument's basename is `claude`", which missed `claude.exe` (every
    Project thread: its walk ran past its own process to the host) and matched processes that
    only carry the word as an argument (`login -pflq user /bin/bash -c … claude remote-control`,
    `/usr/bin/caffeinate -s claude remote-control`: the two parents of every host here)."""
    return _exe_index(cmd.split()) is not None


def _exe_index(toks: list[str]) -> int | None:
    """Index of the token that EXECUTES claude (`claude` or `claude.exe`), or None.

    Stricter than `is_claude`, because a thread's marks (`--sdk-url`, `remote-control`) are plain
    text that a shell's own command line can carry: the shell that ran this very fix had the thread
    command in its `-c` string and was taken for a thread. So no token BEFORE the executable may be
    a flag: `/bin/zsh -c …claude.exe --sdk-url…` is a shell, while `…/bin/claude.exe --print …`,
    `/bin/sh …/bin/claude.exe …` (a script run by its interpreter) and a path with a space in it
    (`…/Application Support/…/claude`) are not."""
    for i, tok in enumerate(toks):
        if tok.startswith("-"):
            return None
        if os.path.basename(tok.rstrip("/")) in ("claude", "claude.exe"):
            return i
    return None


def is_thread(cmd: str) -> bool:
    """True for a session a Remote Control host (or the cloud) started: a `claude`/`claude.exe`
    process carrying `--sdk-url`. On this Mac a Project thread runs as
    `…/claude-code/bin/claude.exe --print --sdk-url https://…/sessions/cse_… --session-id cse_…`,
    a child of the host. Its basename is `claude.exe`, which `is_claude` does not match — that is
    why every thread's walk used to run past its own process and land on the host."""
    toks = cmd.split()
    i = _exe_index(toks)
    return i is not None and any(t == "--sdk-url" or t.startswith("--sdk-url=") for t in toks[i + 1:])


# Flags of the claude command line that take a value as their NEXT token (CLAIMHOST-2). The value of
# one of these is never the subcommand: `claude --name remote-control` is a plain session called
# "remote-control". A flag not listed here MAY take a value; `_subcommand` says how that is read.
_VALUED_FLAGS = frozenset((
    "--permission-mode", "--name", "-n", "--model", "--fallback-model", "--effort", "--debug-file",
    "--session-id", "--resume", "-r", "--settings", "--setting-sources", "--add-dir", "--plugin-dir",
    "--mcp-config", "--agent", "--agents", "--system-prompt", "--append-system-prompt",
    "--allowed-tools", "--allowedTools", "--disallowed-tools", "--disallowedTools", "--tools",
    "--output-format", "--input-format", "--max-turns", "--max-budget-usd", "--json-schema",
    "--permission-prompt-tool", "--betas", "--sdk-url", "--spawn", "--capacity",
))


# The subset whose value can contain SPACES (a name, a path, a list, a JSON text). `ps` prints a
# command line without its quotes, so `--name "demo host"` arrives as three tokens and the value's
# end cannot be seen; `_subcommand` says how that is read. Kept a subset of `_VALUED_FLAGS` (tested).
_SPACED_VALUE_FLAGS = frozenset((
    "--name", "-n", "--settings", "--debug-file", "--add-dir", "--plugin-dir", "--mcp-config",
    "--system-prompt", "--append-system-prompt", "--allowed-tools", "--allowedTools",
    "--disallowed-tools", "--disallowedTools", "--tools", "--betas", "--agents", "--json-schema",
))


def _subcommand(rest: list[str]) -> str | None:
    """The first argument that is neither a flag nor a flag's value, or None.

    `--flag=value` carries its value; a flag in `_VALUED_FLAGS` takes the next token. A flag this
    module does not know may or may not take one, so the token after it is skipped ONCE as a
    possible value, unless that token is `remote-control`: `claude --debug remote-control` stays a
    host. That bias is deliberate (see `is_host`: a false host only costs a claim).

    A flag in `_SPACED_VALUE_FLAGS` (either spelling, `--name v` or `--name=v`) has a value whose
    END cannot be seen. Its first token is always the value (`claude --name remote-control` is a
    session of that name); every further token up to the next flag is taken for more of the value,
    except `remote-control`, which is read as the subcommand: `claude --name demo host
    remote-control` and `claude --add-dir /a /b remote-control` are hosts (they were read as plain
    sessions, and a host then became a lock holder). The cost, on the safe side: a plain session
    started as `claude --name x fix the remote-control bug` reads as a host and does not claim.
    An unknown flag keeps the one-token skip: a real session's prompt follows its flags every day,
    and a tail skip there would turn each such prompt that mentions the word into a host.

    Hosts this still reads as plain sessions (none is a shape started here; all were so before):
    an unknown flag, or a known one-token flag (`--agent My Agent`, `--resume my session`), with
    a value of two words; a spaced value that itself holds a dash-led word followed by two more
    words, or a bare `--` (`--allowedTools Bash(git commit -m *) Edit Read remote-control`); a
    `-p` inside any value (read as the print flag). And one more false host on the safe side: a
    spaced value of several words, then an unknown flag, then a prompt whose second word is
    `remote-control` (`claude --add-dir /a /b --verbose explain remote-control`)."""
    i, maybe_value, tail = 0, False, False
    while i < len(rest):
        t = rest[i]
        if t == "--":
            return rest[i + 1] if i + 1 < len(rest) else None
        if t.startswith("-") and len(t) > 1:
            name, eq, _ = t.partition("=")
            maybe_value = tail = False
            if eq:
                tail = name in _SPACED_VALUE_FLAGS
            elif t in _SPACED_VALUE_FLAGS:
                i += 1                   # the first token after it is the value, whatever it says
                tail = True
            elif t in _VALUED_FLAGS:
                i += 1
            else:
                maybe_value = True
            i += 1
            continue
        if t == "remote-control":
            return t
        if tail:
            i += 1
            continue
        if maybe_value:
            maybe_value = False
            i += 1
            continue
        return t
    return None


def _is_print_flag(t: str) -> bool:
    return t in ("-p", "--print") or t.startswith(("--print=", "-p="))


def is_host(cmd: str) -> bool:
    """True for a Remote Control host, `claude remote-control …`: a claude executable whose
    SUBCOMMAND is `remote-control`, and no `-p`/`--print`/`--print=…` (a thread and a one-shot run
    both carry one; a host never does). The subcommand is the first argument that is not a flag
    and not a flag's value (`_subcommand`), so `claude --permission-mode auto remote-control` is a
    host, while a plain session that only MENTIONS the word is not: `claude fix the remote-control
    bug`, `claude --name remote-control` (CLAIMHOST-2: both were read as hosts by the earlier
    "anywhere among its arguments" test, and such a session then never claimed).
    A host outlives every thread it starts and its own first session never leaves, so it must
    never be anyone's lock holder; a false "host" only costs a claim (under-claim is the safe
    side). What stays a false host, because `ps` drops the quotes: a flag whose value can contain
    spaces (`_SPACED_VALUE_FLAGS`), followed before the next flag by the word `remote-control`,
    in the value itself or in a prompt after it."""
    toks = cmd.split()
    i = _exe_index(toks)
    if i is None:
        return False
    rest = toks[i + 1:]
    return _subcommand(rest) == "remote-control" and not any(_is_print_flag(t) for t in rest)


def _walk(kind_of, max_hops: int) -> tuple[int | None, str]:
    """Walk up from this process; the first parent `kind_of` names ends the walk.
    Returns (pid or None, kind), kind "none" when nothing was found."""
    me = os.getpid()
    cur = me
    for _ in range(max_hops):
        info = ps_info(cur)
        if not info:
            return None, "none"
        ppid = info[0]
        if ppid <= 1:
            return None, "none"
        parent = ps_info(ppid)
        if not parent:
            return None, "none"
        kind = kind_of(parent[1])
        if kind:
            return (ppid if ppid != me else None), kind
        cur = ppid
    return None, "none"


def claude_pid(max_hops: int = 6) -> int | None:
    """The NEAREST Claude Code process this hook is running under, found by walking the parent
    chain: a thread's own `claude.exe`, a plain session's `claude`, or, for a hook right under a
    Remote Control host, the HOST. A caller that must never get a host asks `own_pid`.

    Returns None rather than a guess. There is no fallback to the hook's own pid or to a shell's:
    a lock owned by a process that exits in milliseconds is worse than no lock, because it reads
    as a live holder to `status` and as a reapable wedge to everyone else."""
    return _walk(lambda cmd: "claude" if is_claude(cmd) else None, max_hops)[0]


def session_process(max_hops: int = 6) -> tuple[int | None, str]:
    """(pid, kind) of the SESSION this hook runs for, telling a host's threads apart.

    kind is "thread" (a `--sdk-url` child of a host: its own pid, one per thread), "claude" (an
    ordinary session, exactly what `claude_pid` answers), "host" (the walk reached
    `claude remote-control` first: pid None, a host is never a holder) or "none".
    PROJECTCLAIM-1: under a host, `claude_pid` answered the HOST's pid for every thread, so all
    threads of one folder held a region as one holder that never died."""
    def kind_of(cmd: str) -> str | None:
        if is_host(cmd):                 # first, and not gated on `is_claude`: a `claude.exe` host
            return "host"                # is no `claude` by basename and was walked past
        if is_thread(cmd):
            return "thread"
        if is_claude(cmd):
            return "claude"
        return None
    pid, kind = _walk(kind_of, max_hops)
    return (None if kind == "host" else pid), kind


def own_pid(max_hops: int = 6) -> int | None:
    """The pid of the session this hook runs for, and never a host's: `session_process`'s pid.

    PROCSHOST-1, decided per caller: the session-start record, the idle notice, the mail line, the
    session-name fallback, the close pass's "is this me" and the stall block's "not myself" all
    mean THIS session. A host's pid there names a process that outlives every thread, and its
    `--name` is the host's, shared by all of them. Under a host directly: None, "cannot tell"."""
    return session_process(max_hops)[0]


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
    import common                        # PLATFORM-1; lazy, so this module stays config-free at import
    if not common.pid_probe_safe():
        return True                      # Windows: `os.kill` TERMINATES. "Cannot tell" = alive = keep.
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
