#!/usr/bin/env python3
"""stallbrief.py — at session start, name the managed sessions that have stopped moving.

A seat that ignites other sessions learns that one of them stopped only when somebody reads its
transcript. A session stopped by the account limit sits idle until messaged; a session that
finished its work sits idle until noticed. Neither exits, so nothing that watches for exits sees
them. This module reads what is already on the machine — the live `claude` processes and their
transcripts — and prints one block at the seat's session start:

    - Managed sessions (2 live, 1 stalled):
      - a-builder-R1 · claude-opus-5 · up 3h05m · last active 2m ago: "Merged, row marked."
      - a-builder-R2 · claude-opus-5 · up 6h12m · last active 2h10m ago: "You've hit your session limit …" — STALLED (limit reached)
      - Worktrees still open in this repo: …

READ-ONLY. It runs `ps` once, reads the head and tail of a few transcripts, and writes nothing.

OFF unless the vault config names a `session_name_pattern` — the same key that turns on the
idle-notify door, because both answer the same question ("which sessions does this installation
manage?") and two keys for one fact would drift. With the pattern unset the block is absent and
no process or transcript is read.

A session is STALLED when its process is alive and either its last assistant record is the
account-limit message, or that record is older than `stall_minutes` (default 20). A session with
no assistant record yet is reported as such, and not called stalled: it may still be booting.
"""
from __future__ import annotations
import json, os, re, subprocess, time
from pathlib import Path

import config, idlenotify, procs

STALL_MINUTES_DEFAULT = 20
LINE_CHARS = 80
HEAD_BYTES = 64_000          # the title records are the first lines of a transcript
TAIL_BYTES = 400_000         # same bound as idlenotify.last_assistant_line
LIMIT_TEXT = re.compile(r"^You['’]ve hit your .*limit", re.IGNORECASE)


def stall_minutes() -> int:
    """`stall_minutes` from the vault config, else the default. A value that is not a positive
    integer is ignored rather than raised on: a typo in a config file must not take a session down."""
    v = config._load().get("stall_minutes")
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        return STALL_MINUTES_DEFAULT
    return v


def projects_dir() -> Path:
    return config.home() / ".claude" / "projects"


def _etime_seconds(s: str) -> int | None:
    """`ps` elapsed time — `[[dd-]hh:]mm:ss` — in seconds, or None."""
    days = 0
    if "-" in s:
        d, _, s = s.partition("-")
        try:
            days = int(d)
        except ValueError:
            return None
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    while len(nums) < 3:
        nums.insert(0, 0)
    h, m, sec = nums[-3:]
    return ((days * 24 + h) * 60 + m) * 60 + sec


def _flag_value(toks: list[str], flag: str) -> str | None:
    for i, tok in enumerate(toks):
        if tok == flag:
            if i + 1 >= len(toks):
                return None
            return toks[i + 1].strip("'\"") or None
        if tok.startswith(flag + "="):
            return tok[len(flag) + 1:].strip("'\"") or None
    return None


def _runs_claude(toks: list[str]) -> bool:
    """Whether the PROCESS IMAGE is claude — not merely whether its arguments mention it.

    `procs.is_claude` answers a different question (is any argument's basename `claude`), which is
    right for a parent-chain walk and wrong for a process table: a launcher shell running
    `zsh -c "claude --name X"` carries the same name, and would list one session twice."""
    if not toks:
        return False
    first = os.path.basename(toks[0])
    if first == "claude":
        return True
    return first.startswith("node") and len(toks) > 1 and os.path.basename(toks[1]) == "claude"


def live_sessions() -> list[dict]:
    """Every live `claude` process launched with a `--name`: pid, name, model, age in seconds.

    One `ps` call for the whole table rather than one per pid. The name is read with the same
    token rule `procs.session_name` uses (`--name X` and `--name=X`)."""
    try:
        p = subprocess.run(["ps", "-Ao", "pid=,etime=,command="], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return []
    out = []
    for line in (p.stdout or "").splitlines():
        bits = line.split(None, 2)
        if len(bits) < 3:
            continue
        toks = bits[2].split()
        if not _runs_claude(toks):
            continue
        name = _flag_value(toks, "--name")
        if not name:
            continue
        try:
            pid = int(bits[0])
        except ValueError:
            continue
        out.append({"pid": pid, "name": name, "model": _flag_value(toks, "--model"),
                    "age": _etime_seconds(bits[1])})
    return out


def find_transcript(name: str, since: float, root: Path | None = None) -> Path | None:
    """The newest transcript whose title records carry `name`, among files touched since `since`.

    Claude Code writes `{"type":"custom-title","customTitle":NAME}` and
    `{"type":"agent-name","agentName":NAME}` as the first lines of a named session's transcript.
    Only a title RECORD counts: the name appearing anywhere else — a message addressed to that
    session, an opener quoting it — is another session talking ABOUT it, and matching that would
    report the seat's own transcript as the builder's."""
    root = root or projects_dir()
    try:
        files = [f for f in root.glob("*/*.jsonl") if f.stat().st_mtime >= since]
    except OSError:
        return None
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files:
        try:
            with f.open("rb") as fh:
                head = fh.read(HEAD_BYTES).decode("utf-8", "replace")
        except OSError:
            continue
        for line in head.splitlines():
            if '"custom-title"' not in line and '"agent-name"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("customTitle") == name or rec.get("agentName") == name:
                return f
    return None


def _ts(s) -> float | None:
    if not isinstance(s, str):
        return None
    try:
        from datetime import datetime                       # noqa: PLC0415
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def last_turn(transcript: Path) -> dict | None:
    """The last main-chain assistant record: its text, its timestamp, and whether it is the
    account-limit message. None when the tail holds no assistant record."""
    try:
        size = transcript.stat().st_size
        with transcript.open("rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
            blob = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    out = None
    for line in blob.splitlines():
        if '"assistant"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        text = content if isinstance(content, str) else " ".join(
            b.get("text", "") for b in (content or []) if isinstance(b, dict) and b.get("type") == "text")
        text = " ".join(text.split())
        # The CLOCK is the last assistant record of any kind — a record that is only a tool call
        # is a session at work. The TEXT is the last one that said something, so the line a
        # person reads is never an empty quote.
        prev_text = out["text"] if out else ""
        limit = rec.get("error") == "rate_limit" or bool(LIMIT_TEXT.match(text))
        out = {"text": text or prev_text, "ts": _ts(rec.get("timestamp")), "limit": limit}
    return out


def _dur(sec: float | None) -> str:
    if sec is None:
        return "?"
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    m = sec // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h{m:02d}m"
    return f"{h // 24}d{h % 24:02d}h"


def assess(sess: dict, turn: dict | None, now: float, stall_after: int) -> str | None:
    """Why this live session is STALLED, or None. The clock rule and the limit rule, nothing else."""
    if turn is None:
        return None
    if turn.get("limit"):
        return "limit reached"
    ts = turn.get("ts")
    if ts is not None and now - ts > stall_after * 60:
        return f"silent over {stall_after}m"
    return None


def facts_lines(worktrees: str | None = None, now: float | None = None,
                sessions: list[dict] | None = None, root: Path | None = None) -> list[str]:
    """The "Managed sessions" block, or [] when the pattern is unset or no managed session lives.

    `worktrees` is `wtsweep.facts_line()`'s line, passed in so the caller can print it on its own
    when this block is absent."""
    if config.session_name_pattern() is None:
        return []
    now = time.time() if now is None else now
    if sessions is None:
        sessions = live_sessions()
    me = procs.claude_pid()
    managed = [s for s in sessions
               if s["pid"] != me and idlenotify.match_name(s["name"]) is not None]
    if not managed:
        return []
    after = stall_minutes()
    rows, stalled = [], 0
    for s in sorted(managed, key=lambda s: s["name"]):
        age = s.get("age")
        since = now - (age if age is not None else 7 * 86400) - 60
        tp = find_transcript(s["name"], since, root)
        turn = last_turn(tp) if tp else None
        parts = [s["name"], s.get("model") or "model ?", f"up {_dur(age)}"]
        if tp is None:
            parts.append("no transcript found")
        elif turn is None:
            parts.append("no reply yet")
        else:
            ago = _dur(now - turn["ts"]) + " ago" if turn.get("ts") is not None else "at an unknown time"
            said = turn["text"][:LINE_CHARS] + ("…" if len(turn["text"]) > LINE_CHARS else "")
            parts.append(f'last active {ago}' + (f': "{said}"' if said else ""))
        why = assess(s, turn, now, after)
        line = "  - " + " · ".join(parts)
        if why:
            stalled += 1
            line += f" — STALLED ({why})"
        rows.append(line)
    head = f"- Managed sessions ({len(managed)} live" + (f", {stalled} stalled" if stalled else "") + "):"
    out = [head] + rows
    if worktrees:
        out.append("  " + worktrees)
    return out


def block(worktrees: str | None, **kw) -> list[str]:
    """What session start prints here: the managed block with the worktree line folded in, or —
    when there is no block — the worktree line alone, exactly as it printed before this module."""
    return facts_lines(worktrees=worktrees, **kw) or ([worktrees] if worktrees else [])
