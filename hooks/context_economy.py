"""context_economy.py — two non-blocking PreToolUse notices that make the token cost of READING
visible, instead of asking a session to remember it.

WHY (measured 2026-09-10, Speculum/Kernel "Council cost"): on Fable, cache READS cost 0.025× input,
so a session's bill is dominated by cache WRITES — the new bytes each turn adds, mostly tool
output. Reading less is the cheapest lever there is: an exact section instead of a whole file, no
re-reading a file that has not changed, a shorter output. On Opus the reads dominate instead and
the lever is boot size — this module helps both, but it was built for Fable's bill.

Both notices ALWAYS ALLOW. Nothing here is a gate: the text rides `hookSpecificOutput`'s
`additionalContext` on an explicit "allow" (common.allow), so the model sees it on its next turn
and nothing is ever refused. A hook bug in here must never cost a turn — every public function
degrades to "no notice" on any I/O problem, and the one path that can still raise (a genuinely
malformed tool_input) is caught by the caller's `guarded()` wrapper the same way every other rule
in this plugin is: log one line, print nothing, exit 0.

  RE-READ  — the session already has this file's exact content in context from an earlier read in
             THIS session (same sha256), so reading it again spends tokens on nothing new.
  BIG-READ — the session is about to read a file whole (no offset/limit) that is bigger than
             `config.big_read_kb()` KB; a targeted Grep-then-offset read would cost less.

Both fire for the `Read` tool and for a handful of Bash read verbs (`cat`, `head`, `tail`,
`sed -n`) on a single file — the same act done from the shell. RE-READ is checked against the
whole file's hash regardless of which slice a partial read asked for (a `head -5` of unchanged
content is still unchanged content); BIG-READ never fires for a partial read (`offset`/`limit`,
`head`/`tail`/`sed -n`) or for a file this session itself wrote this session — there is nothing to
warn about re-reading your own fresh work.

State lives under `config.STATE`, one JSON file per session id (`context-economy-<sid>.json`), the
same shape and locking discipline as `common.py`'s session-state helpers: `reads` (path → sha256 +
timestamp + size), `written` (paths this session wrote), and the two notice counters `status.py`
reports. PreToolUse only ever READS this file (the read has not happened yet, so it cannot record
one — see chore.py); PostToolUse chores record it once the tool call is known to have succeeded.
"""
from __future__ import annotations
import hashlib, json, re, shlex, time
from pathlib import Path
import config
from common import STATE, expand, log

BYTES_PER_TOKEN = 4
HASH_SIZE_LIMIT = 2 * 1024 * 1024   # hashing above this is skipped with no notice — keep the hook fast
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".heic",
                   ".pdf", ".mp3", ".mp4", ".mov", ".wav", ".m4a", ".zip", ".gz", ".tar", ".7z",
                   ".woff", ".woff2", ".ttf", ".otf", ".eot", ".db", ".sqlite", ".sqlite3"}


# ---------------------------------------------------------------------- per-session state ----

def _state_path(sid: str) -> Path:
    return STATE / f"context-economy-{sid or '-'}.json"


def _load(sid: str) -> dict:
    try:
        doc = json.loads(_state_path(sid).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(sid: str, mutate) -> dict:
    """Read-modify-write the session's context-economy record under an exclusive lock — the same
    discipline as `common.update_session_state`, in a file of its own so this module never has to
    know the shape of the session-state record that hook owns."""
    import fcntl
    path = _state_path(sid)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / f"context-economy-{(sid or '-')}.lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict):
                    doc = {}
            except (OSError, ValueError):
                doc = {}
            mutate(doc)
            path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            fcntl.flock(lk, fcntl.LOCK_UN)
            return doc
    except OSError as e:
        log("hook-errors", f"context-economy-state\t{sid}\t{e}")
        return {}


def _bump(sid: str, key: str) -> None:
    def mut(doc: dict) -> None:
        doc[key] = int(doc.get(key, 0) or 0) + 1
    _update(sid, mut)


def counts(sid: str) -> dict:
    """{'reread_notices': N, 'bigread_notices': M} for this session — what actually fired, never
    what was "avoided" (there is no way to know that)."""
    doc = _load(sid)
    return {"reread_notices": int(doc.get("reread_notices", 0) or 0),
           "bigread_notices": int(doc.get("bigread_notices", 0) or 0)}


# ---------------------------------------------------------------------------- file evidence ----

def _eligible(p: Path) -> bool:
    try:
        return p.is_file() and p.suffix.lower() not in BINARY_SUFFIXES
    except OSError:
        return False


def _hash(p: Path) -> tuple[str, int] | None:
    """(sha256, size) or None — absent, unreadable, or over HASH_SIZE_LIMIT all read as "no
    evidence", never as an error the caller has to handle."""
    try:
        size = p.stat().st_size
    except OSError:
        return None
    if size > HASH_SIZE_LIMIT:
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest(), size


def _kb(n: int) -> str:
    return f"{n / 1024:.1f}"


def _tokens(n: int) -> str:
    return f"{n // BYTES_PER_TOKEN:,}"


def _ago(prior_ts) -> str:
    try:
        mins = max(0.0, (time.time() - float(prior_ts)) / 60.0)
    except (TypeError, ValueError):
        mins = 0.0
    if mins < 1:
        return "less than a minute ago"
    n = round(mins)
    return f"{n} minute{'s' if n != 1 else ''} ago"


def _reread_text(prior_ts, size: int) -> str:
    return (f"Unchanged since you read it {_ago(prior_ts)} ({_kb(size)} KB, ~{_tokens(size)} tokens "
            "at 4 B/token). Re-reading writes those tokens to context again; Grep for the section "
            "you need, or trust your earlier read.")


def _bigread_text(size: int) -> str:
    return (f"This file is {_kb(size)} KB (~{_tokens(size)} tokens). Read the section you need: "
            "Grep for the heading, then Read with offset/limit.")


def _was_written_by_session(sid: str, path: Path) -> bool:
    w = _load(sid).get("written")
    return isinstance(w, list) and str(path) in w


# --------------------------------------------------------------------------- the two checks ----

def check_read(sid: str, path: Path, partial: bool = False) -> str | None:
    """The notice text for one read of `path` by session `sid`, or None. `partial` is True when
    the caller already knows this is not a whole-file read (Read with offset/limit; head/tail/
    sed -n from the shell) — BIG-READ never fires then, RE-READ still can."""
    if not config.flag("context_economy", True):
        return None
    if not _eligible(path):
        return None
    got = _hash(path)
    if got is None:
        return None
    sha, size = got
    reads = _load(sid).get("reads")
    prior = reads.get(str(path)) if isinstance(reads, dict) else None
    if prior and prior.get("sha256") == sha:
        _bump(sid, "reread_notices")
        return _reread_text(prior.get("ts"), size)
    if partial:
        return None
    if _was_written_by_session(sid, path):
        return None
    if size > config.big_read_kb() * 1024:
        _bump(sid, "bigread_notices")
        return _bigread_text(size)
    return None


def record_read(sid: str, path: Path) -> None:
    """PostToolUse: the read actually happened — remember what it saw, so the NEXT read of this
    path can be compared against it. A PreToolUse alone cannot call this: it does not yet know the
    tool call is going to succeed."""
    if not _eligible(path):
        return
    got = _hash(path)
    if got is None:
        return
    sha, size = got

    def mut(doc: dict) -> None:
        r = doc.get("reads")
        if not isinstance(r, dict):
            r = {}
        r[str(path)] = {"sha256": sha, "ts": time.time(), "size": size}
        doc["reads"] = r
    _update(sid, mut)


def record_write(sid: str, path: Path) -> None:
    """PostToolUse: this session wrote `path` (Edit/Write/MultiEdit, any file — not vault-scoped).
    Consulted only by the BIG-READ exemption: a file this session just wrote needs no warning
    about reading it back whole."""
    def mut(doc: dict) -> None:
        w = doc.get("written")
        if not isinstance(w, list):
            w = []
        s = str(path)
        if s not in w:
            w.append(s)
        doc["written"] = w
    _update(sid, mut)


# ------------------------------------------------------------------- Bash: cat/head/tail/sed -n ----
# The same act, done from the shell. `gate.segments` already knows how to split a compound Bash
# command on && || ; | and newlines while respecting quotes/heredocs/$( ) — imported lazily so
# loading this module never has to load gate.py first (gate.py loads this module at import time).

_SINGLE_FLAG = re.compile(r"^-[a-zA-Z]$")


def bash_read_targets(cmd: str, cwd: str | None) -> list[tuple[Path, bool]]:
    """[(path, whole_file)] for every `cat`/`head`/`tail`/`sed -n` of exactly one file across the
    command's segments. Heuristic and best-effort: this module never denies, so an under-detection
    only costs a missed notice, never a wrong refusal — unlike gate.py's write-target parsing,
    which has to be conservative in the other direction."""
    try:
        from gate import segments as _segments
    except ImportError as e:                          # pragma: no cover - ships with gate.py
        log("hook-errors", f"context-economy-bash-targets\timport failed: {e}")
        return []
    out: list[tuple[Path, bool]] = []
    for seg in _segments(cmd):
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        if not toks:
            continue
        verb, rest = toks[0], toks[1:]
        if verb == "cat":
            whole = True
        elif verb in ("head", "tail"):
            whole = False
        elif verb == "sed" and "-n" in rest:
            whole = False
        else:
            continue
        files: list[str] = []
        i = 0
        while i < len(rest):
            t = rest[i]
            if t.startswith("-"):
                if _SINGLE_FLAG.match(t) and i + 1 < len(rest) and not rest[i + 1].startswith("-"):
                    i += 2                              # a flag that takes a value: `-n 50`, `-c 100`
                    continue
                i += 1
                continue
            files.append(t)
            i += 1
        if verb == "sed" and files:
            files = files[1:]                          # drop the sed script/address itself
        if len(files) == 1:
            out.append((expand(files[0], cwd), whole))
    return out


def bash_notice(inp: dict) -> str | None:
    """The notice for a PreToolUse Bash call, or None — checked against every single-file
    cat/head/tail/sed-n target in the command; the first notice found wins."""
    if not config.flag("context_economy", True):
        return None
    cmd = (inp.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return None
    sid = inp.get("session_id", "-")
    cwd = inp.get("cwd")
    for path, whole in bash_read_targets(cmd, cwd):
        note = check_read(sid, path, partial=not whole)
        if note:
            return note
    return None


def record_bash_reads(sid: str, cmd: str, cwd: str | None) -> None:
    """PostToolUse: the Bash command ran — record every file it read, mirroring `record_read`."""
    if not cmd:
        return
    for path, _whole in bash_read_targets(cmd, cwd):
        record_read(sid, path)
