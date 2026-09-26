#!/usr/bin/env python3
"""deletedoor.py — `rm` only where it is safe; everything else goes to the Trash (DELETEPOLICY-1).

The ruling (`owner-ruling-delete-policy-rm-regenerable-trash-cli-everything-else-2026-09-22`):

    rm is allowed ONLY on   this session's own temp tree (its scratchpad, its task outputs)
                            a git worktree under `<repo>/.claude/worktrees/`
                            `__pycache__` / `.pyc`
    everything else         `/usr/bin/trash <path>` — Put Back works, the Trash is never emptied

and one more refusal wherever the target is: a RECURSIVE removal of a tree that is, or contains, a
symbolic link. `/usr/bin/trash <symlink>` removes the link and leaves its target alone (measured
2026-09-22); the night before, a Finder "move to Trash" resolved a link to its target and trashed
469 cache files. A tree with a link in it goes to the Trash, where that cannot happen.

What the door reads. A Bash command is split into segments the way `gate.py` splits them, `cd` is
followed across segments, and each `rm` / `rmdir` / `unlink` has its targets resolved against the
cwd it will run in. `find … -delete` and `find … -exec rm` are judged by the directories `find`
starts from. A target the door cannot see — a `$VARIABLE`, a command substitution, `xargs rm` fed
from a pipe — is refused: it could be anything, and "could be anything" is the case the ruling is
about. Globs are expanded against the cwd, as the shell would.

WARN, THEN DENY. The ruling ships the door in WARN for one day, then DENY. `<state>/delete.mode`
holding `warn` or `deny` decides it. When that file is absent the door dates its own WARN day: the
first evaluation writes `<state>/delete.warn-since` (epoch seconds) and DENY starts 24 hours later.
The partition door's WARN→DENY flip was left to be done by hand and has stayed undone for eight
days; a flip with a date on it does not depend on anyone remembering. In WARN the command runs and
the model is told, in its next turn, what the door would have said (a note, never a permission
decision: an explicit allow would skip the person's own prompt for an `rm`). Both modes log one line
to `<state>/delete.log`.

Off unless the config says `delete_door: true`: this is one owner's ruling, and a stranger's install
must not start refusing `rm` on first use.
"""
from __future__ import annotations
import glob as _glob
import os
import re
import shlex
import time
from pathlib import Path

import common
import config
import rootguard
import shellread

RULING = "owner-ruling-delete-policy-rm-regenerable-trash-cli-everything-else-2026-09-22"
WARN_WINDOW_S = 24 * 3600
WALK_LIMIT = 20000
_RM = {"rm", "rmdir", "unlink"}
_PREFIX_WORDS = {"sudo", "command", "nohup", "time", "exec", "builtin", "env"}
_OPAQUE = re.compile(r"[$`]")
# Interpreters the door does not parse: a one-liner that names a removal is refused as unseeable
_OTHER_INTERPRETERS = {"perl", "ruby", "node"}
_OTHER_REMOVALS = re.compile(r"\b(?:unlink|rmtree|rm_rf|rm_r|rmSync|unlinkSync|rmdirSync|rmdir|remove_tree|FileUtils\.rm)\b")


def enabled() -> bool:
    return config.flag("delete_door", False)


def mode() -> str:
    """`warn` or `deny`. The file wins; without it, the dated one-day WARN window."""
    pin = pinned()
    if pin:
        return pin
    since = warn_since()
    if since is None:
        return "warn"
    return "deny" if time.time() >= since + WARN_WINDOW_S else "warn"


def warn_since() -> float | None:
    """When the WARN day began, stamping it now if it has not begun. None only when unwritable."""
    f = common.STATE / "delete.warn-since"
    try:
        return float(f.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    try:
        f = rootguard.permit(f, why="the delete door's WARN-day stamp in the state directory")
        f.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        f.write_text(f"{now:.0f}\n", encoding="utf-8")
        return now
    except (OSError, rootguard.OutsideRoot):
        return None


def pinned() -> str | None:
    try:
        v = (common.STATE / "delete.mode").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    return v if v in ("warn", "deny") else None


def facts_line() -> str:
    """One SessionStart line: which mode, and until when, so a WARN day nobody watches is seen."""
    m, pin = mode(), pinned()
    if m == "deny":
        head = "DENY (an `rm` outside this session's scratchpad, `.claude/worktrees/` or `__pycache__` is refused)"
    elif pin == "warn":
        head = "WARN, pinned by ~/.claude/gedaechtnis/delete.mode (would-be refusals go to delete.log; the command runs)"
    else:
        since = warn_since()
        until = time.strftime("%Y-%m-%d %H:%M", time.localtime(since + WARN_WINDOW_S)) if since else "?"
        head = f"WARN until {until}, then DENY (would-be refusals go to delete.log; the command runs)"
    return f"- Delete door: {head}. Everything else goes to the Trash with `/usr/bin/trash <path>`."


# ------------------------------------------------------------------------ where rm may go ----

def session_tmp(sid: str) -> Path | None:
    """This session's own temp tree: `/private/tmp/claude-<uid>/<project>/<session id>/`.

    The scratchpad and the task output files live under it. Found by pattern rather than built,
    because the project segment is the harness's encoding of the cwd and is not in the payload."""
    if not sid or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", sid):
        return None
    base = Path(os.environ.get("GEDAECHTNIS_SESSION_TMP_ROOT") or f"/private/tmp/claude-{os.getuid()}")
    try:
        for d in base.iterdir():
            cand = d / sid
            if cand.is_dir():
                return cand.resolve()
    except OSError:
        return None
    return None


def _in_worktrees(p: Path) -> bool:
    """Strictly BELOW a `.claude/worktrees/` directory whose repository is a git repository."""
    parts = p.parts
    for i in range(len(parts) - 2):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees" and len(parts) > i + 2:
            repo = Path(*parts[:i])
            if (repo / ".git").exists():
                return True
    return False


def _is_pycache(p: Path) -> bool:
    return "__pycache__" in p.parts or p.suffix == ".pyc"


def allowed_place(p: Path, sid_tmp: Path | None) -> bool:
    if sid_tmp is not None and (p == sid_tmp or sid_tmp in p.parents):
        return True
    return _in_worktrees(p) or _is_pycache(p)


def symlink_in(raw: Path) -> Path | None:
    """The first symbolic link at or below `raw` (not following links), or None.

    More than WALK_LIMIT entries is answered with `raw` itself: a tree too big to look through is a
    tree the door cannot vouch for."""
    if raw.is_symlink():
        return raw
    if not raw.is_dir():
        return None
    n = 0
    for root, dirs, files in os.walk(raw, followlinks=False):
        for name in dirs + files:
            n += 1
            if n > WALK_LIMIT:
                return raw
            q = Path(root) / name
            if q.is_symlink():
                return q
    return None


# ------------------------------------------------------------------------- reading commands ----

def _words(seg: str) -> list[str] | None:
    try:
        w = shlex.split(seg, comments=False, posix=True)
    except ValueError:
        return None
    while w and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]) or w[0] in _PREFIX_WORDS):
        w = w[1:]
    return w


def _abs(tok: str, cwd: Path) -> Path:
    s = os.path.expanduser(tok)
    p = Path(s)
    return p if p.is_absolute() else cwd / p


def _home(s: str) -> str:
    """`$HOME` and `${HOME}` are written out: the one variable whose value the door knows."""
    h = str(config.home())
    return s.replace("${HOME}", h).replace("$HOME", h)


def _python(src: str) -> list[tuple[str, list[str], bool]]:
    """The removals in a Python source (heredoc body or `-c` string), read by `shellread`: a string
    that is a removal COMMAND comes back as ("sub", [command]) for `check` to read as a shell line."""
    got = shellread.python_removals(src)
    if got is None:                               # does not tokenize: a removal word in it is unseeable
        if shellread._PY_DESTROY.search(src) or re.search(r"(?:^|[\s'\"\[(])(?:rm|rmdir|unlink)\s", src):
            return [("python", ["<unseeable: Python that does not tokenize>"], True)]
        return []
    return [("sub", ts, False) if v == "sh" and not ts[0].startswith("<unseeable") else (v, ts, r)
            for v, ts, r in got]


def removals(cmd: str, cwd: str | None, segments) -> list[tuple[str, list[str], bool]]:
    """(verb, raw targets, recursive) for every removal in `cmd`, plus ("cd", [dir], False) so the
    caller can follow the cwd, and ("sub", [command line], False) for text the shell runs as its own
    command line (a shell heredoc body, an `rm` string in Python) — `check` reads that in place. A
    target the door cannot see is returned as `<unseeable: …>`.

    DELETEDOOR-3: the heredoc and Python readings are `shellread`'s, the same ones the vault door
    uses — a shell heredoc body is commands, a Python body or `python -c` string is code whose
    comments and other strings are prose, a `cat > f <<EOF` body is written and never run."""
    out = []
    for seg in segments(cmd):
        reader = shellread._heredoc_reader(seg)
        if reader == "shell":
            body = shellread._heredoc_body(seg)
            out.append(("sub", ["\n".join(x for x in body.split("\n") if not x.lstrip().startswith("#"))], False))
            continue
        if reader == "python":
            out.extend(_python(_home(shellread._heredoc_body(seg))))
            continue
        # any other heredoc (`cat > f <<EOF`, `sqlite3 <<EOF`) is judged by its head word, never its body
        raw = _home(seg.strip())
        if raw[:1] in ("(", "{"):                 # `(rm x)` / `{ rm x; }`: the group runs its commands
            raw = re.sub(r"\s*[)}]\s*$", "", raw[1:]).strip()
        if _OPAQUE.search(raw) and re.search(r"(?:^|[\s;|&(])(?:/bin/)?(?:rm|rmdir|unlink)\s", raw):
            # a variable or substitution anywhere in an rm segment: the target is not on the page
            words = _words(raw) or []
            if words and os.path.basename(words[0]) in _RM:
                out.append((os.path.basename(words[0]), ["<unseeable: " + raw[:80] + ">"], True))
                continue
        words = _words(raw)
        if not words:
            continue
        verb = os.path.basename(words[0])
        if shellread._PYTHON.match(verb) and "-c" in words[1:-1]:
            out.extend(_python(words[words.index("-c") + 1]))
            continue
        if verb in _OTHER_INTERPRETERS and any(x in ("-e", "-E") for x in words[1:]) \
                and _OTHER_REMOVALS.search(raw):
            out.append((verb, [f"<unseeable: a removal inside {verb} -e>"], True))
            continue
        if verb == "cd":
            out.append(("cd", words[1:2], False))
            continue
        if verb == "xargs" and any(os.path.basename(x) in _RM for x in words[1:]):
            out.append(("xargs", ["<unseeable: xargs feeds rm from a pipe>"], True))
            continue
        if verb == "find" and ("-delete" in words or any(
                words[i] == "-exec" and i + 1 < len(words) and os.path.basename(words[i + 1]) in _RM
                for i in range(len(words)))):
            roots = []
            for x in words[1:]:
                if x.startswith(("-", "(", "!")):
                    break
                roots.append(x)
            out.append(("find", roots or ["."], True))
            continue
        if verb not in _RM:
            continue
        rec, targets, opts = False, [], True
        for x in words[1:]:
            if opts and x == "--":
                opts = False
                continue
            if opts and x.startswith("-") and x != "-":
                if x == "--recursive" or (not x.startswith("--") and re.search(r"[rR]", x)):
                    rec = True
                continue
            targets.append(x)
        out.append((verb, targets, rec))
    return out


def check(cmd: str, cwd: str | None, sid: str, segments) -> str | None:
    """The refusal text for `cmd`, or None when every removal in it is allowed."""
    cur = Path(cwd) if cwd else common.HOME
    sid_tmp = session_tmp(sid)
    for verb, targets, rec in removals(cmd, cwd, segments):
        if verb == "sub":                         # a command line run in place, from the current cwd
            r = check(targets[0], str(cur), sid, segments)
            if r:
                return r
            continue
        if verb == "cd":
            if targets and not _OPAQUE.search(targets[0]):
                cur = _abs(targets[0], cur)
            continue
        for t in targets:
            if t.startswith("<unseeable"):
                return _msg(t[len("<unseeable: "):-1], "the target is not written out, so the door cannot see what it is")
            if _OPAQUE.search(t):
                return _msg(t, "the target is not written out, so the door cannot see what it is")
            paths = [Path(x) for x in sorted(_glob.glob(str(_abs(t, cur))))] if re.search(r"[*?\[]", t) \
                else [_abs(t, cur)]
            for raw in paths:
                if rec:
                    link = symlink_in(raw)
                    if link is not None:
                        return _msg(str(raw), f"it is, or contains, a symbolic link ({link}); a recursive "
                                              "removal over a link is where targets get lost")
                try:
                    real = raw.resolve()
                except OSError:
                    real = raw
                if not allowed_place(real, sid_tmp):
                    return _msg(str(raw), "it is not in this session's scratchpad, a worktree under "
                                          "`.claude/worktrees/`, or `__pycache__`")
    return None


def _msg(target: str, why: str) -> str:
    return (f"The delete door refuses this removal of `{target}`: {why}. Move it to the Trash instead: "
            f"`/usr/bin/trash {shlex.quote(target)}` (Put Back restores it; the Trash is never emptied), "
            "and report it as \"moved to Trash\". `rm` is for this session's scratchpad, "
            "`<repo>/.claude/worktrees/` and `__pycache__`/`.pyc` only, each reported as "
            f"\"removed X (regenerable by Y)\". ({RULING})")
