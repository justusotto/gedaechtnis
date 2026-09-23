#!/usr/bin/env python3
"""idlenotify.py — a session that was ignited by another session tells it when it finishes or stalls.

THE SPECIMEN. Two audit sessions were ignited by a seat, finished their work, and sat idle for
about twenty-five minutes. The person who noticed was the owner, reading a screen; the seat that
had started them — the one piece of software whose job is to know — noticed nothing, because
nothing told it. The work was done and the only thing missing was a sentence.

A DOOR, NOT A HABIT. Every condition below is a fact on disk: the session's launch name, a handoff
file that exists, a context level that fired. Nothing here asks a model whether it is finished, and
nothing here is a stop condition — this runs at Stop, after the session has already stopped, and it
prints nothing to the session it runs in. A seat hearing about a finished session one turn late is
a small cost; a session that decides for itself that it is done is a large one.

OFF BY DEFAULT, AND OFF MEANS NOTHING IS READ. With no `session_name_pattern` in the vault config,
`match_name` answers None for every session and `notify` returns before it looks at a transcript, a
touched set or a mailbox. An installation that does not launch named sessions cannot be affected by
this module, and that is the shipped state.

WHO IS TOLD. The igniting seat, in this order:

  1. a `seat:` line in the session's OPENER — the launcher wrote it, so it is the one statement of
     intent that exists before the session ran, and it survives a name that was typed by hand;
  2. the `seat` group of the configured pattern, if it has one;
  3. nothing. There is no fallback to "the newest live session" or to a default seat: sending a
     handoff to the wrong reader is worse than sending it to nobody, because the wrong reader may
     act on it.

WHAT IS SENT. One message, once per session: the session's name, the handoff path, and the last
thing the session actually said. The last assistant line is there because it is the only part a
reader can judge without opening a file — "row 4 merged, moving to row 5" and "I am blocked on a
permission prompt" are the two cases the seat has to tell apart, and both are in that line.

DELIVERY IS A SEAM, AND THE MAILBOX IS THE RECORD. How two live sessions pass a message is a
property of the harness and of a fleet's own tooling, not of a memory plugin, so the transport is a
configured command (`notify_command`). The message is appended to the seat's mailbox FIRST and the
command runs afterwards — a transport that is absent, wedged or slow then costs a late delivery and
never a lost one, and the mailbox is what the next boot of that seat reads.
"""
from __future__ import annotations
import json
import re
import subprocess
import time
from pathlib import Path

import common
import config
import procs

# The mailbox row's schema version. Written on every row so a later reader can tell a row it
# understands from one it does not, rather than guessing from which keys happen to be present.
SCHEMA = 1

# How much of the session's last assistant line travels in the message. Long enough for the two
# cases a seat must tell apart (finished / blocked), short enough that a mailbox row stays one line
# a person can read in a terminal.
LAST_LINE_CHARS = 240

# How long the configured transport is given. A Stop hook that waited on a wedged transport would
# hold up the session's exit for the one reason this module exists to remove.
DELIVER_TIMEOUT = 10

_SEAT_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SEAT_LINE = re.compile(r"^\s*seat:\s*(\S+)", re.MULTILINE)


def enabled() -> bool:
    """Whether this installation manages named sessions at all."""
    return config.session_name_pattern() is not None


def pattern_problem() -> str | None:
    """The reason the configured pattern is not in force, or None when it is (or when none is set).

    Reported rather than raised, and reported rather than swallowed: a regex with an unbalanced
    bracket and no regex at all produce the same silence at every other surface in this module, and
    the difference between them is a fleet that opted in and is getting nothing."""
    pat = config.session_name_pattern()
    if pat is None:
        return None
    try:
        re.compile(pat)
    except re.error as e:
        return (f"`session_name_pattern` in config.json is not a valid regular expression ({e}) — "
                f"no session is treated as managed and nothing is reported to any seat")
    return None


def match_name(name: str | None) -> dict | None:
    """The pattern's named groups for `name`, or None when this is not a managed session.

    `{}` — a pattern that matches but has no named groups — is a MATCH and is returned as such.
    Truth-testing the result would make that case indistinguishable from no match, and a fleet
    whose pattern is a plain `^audit-.*$` is a perfectly ordinary opt-in."""
    pat = config.session_name_pattern()
    if pat is None or not name:
        return None
    try:
        m = re.match(pat, name)
    except re.error:
        return None
    return dict(m.groupdict()) if m else None


def seat_from_opener(text: str | None) -> str | None:
    """The `seat:` line an opener carries, or None.

    The FIRST such line wins. An opener is a document a person or a launcher wrote, and a second
    `seat:` further down is far more likely to be a quotation of some other session's opener than a
    correction of this one's.

    ★ The NAME is the first token after the colon, and the rest of the line is ignored on purpose.
    This fleet's openers write `seat: <name> [<sha>]`, so a pattern anchored at end-of-line matched
    none of them — measured against a real opener, which is the only reason the anchor came out."""
    if not text:
        return None
    m = _SEAT_LINE.search(text)
    if not m:
        return None
    seat = m.group(1).strip("'\"`")
    return seat if _SEAT_OK.match(seat) else None


def igniting_seat(name: str | None, groups: dict | None, opener: str | None) -> str | None:
    """Who to tell, or None when nobody can be named. Order and its reasoning: module docstring."""
    from_opener = seat_from_opener(opener)
    if from_opener:
        return from_opener
    if groups:
        seat = (groups.get("seat") or "").strip()
        if seat and _SEAT_OK.match(seat):
            return seat
    return None


def at_cap(sid: str) -> bool:
    """Whether this session hit its ABSOLUTE context cap.

    Read from `context_cap`'s own record of which levels fired, not recomputed here, so there is
    exactly one definition of the cap in the package and a session cannot be "at the cap" for this
    door and below it for the notice the session itself was shown.

    WARN does not count. A session that warned and then finished its row cleanly is the normal
    healthy path through a long build, and a seat told about every warn would learn to skip the
    line — which is the failure the notice itself is written to avoid."""
    try:
        import context_cap                                  # noqa: PLC0415 — Stop-only cost
        return "cap" in context_cap._levels_fired(sid)
    except Exception:                                       # noqa: BLE001 — a chore never crashes
        return False


def last_assistant_line(transcript: Path | None) -> str | None:
    """The last thing the session said, in one line, or None.

    Read from the TAIL, because a transcript reaches tens of megabytes and this runs inside a Stop
    hook's timeout. A tail that lands mid-record yields an unparseable first line, which is skipped
    like any other; a tail that contains no assistant text at all yields None rather than a
    fabricated summary."""
    if not transcript:
        return None
    try:
        size = transcript.stat().st_size
        with transcript.open("rb") as fh:
            if size > 400_000:
                fh.seek(size - 400_000)
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
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
        text = " ".join(text.split())
        if text:
            out = text
    return out[:LAST_LINE_CHARS] if out else None


def mailbox(seat: str) -> Path:
    """The seat's mailbox file. `safe_sid` because a seat name reaches this from a config pattern
    and a process command line, and neither is this package's to trust with a path segment."""
    return config.state() / "notify" / f"{common.safe_sid(seat)}.jsonl"


def message(name: str | None, handoff: str | None, last: str | None, reason: str) -> str:
    """The one line a seat reads. Plain, and it says what it knows and no more."""
    who = name or "an unnamed session"
    head = {"handoff": f"{who} wrote a handoff and stopped",
            "cap": f"{who} stopped at its context cap"}.get(reason, f"{who} stopped")
    parts = [head + "."]
    if handoff:
        parts.append(f"Handoff: {handoff}")
    if last:
        parts.append(f"Its last line: {last}")
    else:
        parts.append("It said nothing this session that could be read back.")
    return " ".join(parts)


def deliver(seat: str, row: dict) -> bool:
    """Append the row to the seat's mailbox, then hand it to the transport. True if it was RECORDED.

    The return value is about the MAILBOX, deliberately: a transport that refuses, times out or is
    not configured has not lost the message, and reporting that as a failure would invite a caller
    to retry and post it twice. A mailbox that cannot be written HAS lost it, and that is what
    False means."""
    box = mailbox(seat)
    try:
        box.parent.mkdir(parents=True, exist_ok=True)
        with box.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as e:
        common.log("hook-errors", f"idlenotify\tmailbox\t{seat}\t{e}")
        return False
    cmd = config.notify_command()
    if cmd:
        try:
            subprocess.run([config.python(), str(cmd), seat],
                           input=json.dumps(row), capture_output=True, text=True,
                           timeout=DELIVER_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError) as e:
            common.log("idlenotify", f"transport\t{seat}\tfailed\t{e}")
    return True


def already_sent(sid: str) -> bool:
    doc = common._session_doc(sid)
    return bool(doc.get("idlenotify_sent"))


def mark_sent(sid: str, seat: str) -> None:
    def put(doc: dict) -> None:
        doc["idlenotify_sent"] = {"seat": seat, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    common.update_session_state(sid, put)


def notify(sid: str, cwd: str | None, transcript_path: str | None, opener=None,
           name: str | None = None) -> str | None:
    """The whole door. Returns the message sent, or None when nothing was sent.

    ORDER IS THE SAFETY. The pattern is consulted first and returns before anything is read, so an
    installation that has not opted in never reads a transcript, never stats a mailbox and never
    runs a transport.

    ★ AND `opener` MAY BE A CALLABLE, WHICH IS WHY. The door's only caller passed
    `opener=session_start.opener_text(inp)` — a transcript open and up to 200 JSON parses — and
    Python evaluates a keyword argument BEFORE the function it is passed to runs, so that read
    happened on every Stop of every turn of every session on every machine, opted in or not. The
    guard below was three lines too late to prevent the thing its own docstring promised. A caller
    now passes a zero-argument callable and it is invoked HERE, after the pattern check, so the
    invariant is a property of this module rather than of every caller remembering it. Then the session must be one this installation manages, then it must have
    finished or hit the cap, then a seat must be nameable — and only then is anything written.

    IDEMPOTENT PER SESSION. A Stop hook runs at the end of every turn, not once per session, so
    without the sent-mark a seat would get the same handoff line on every subsequent turn. The mark
    is written AFTER a successful record, so a mailbox that could not be written is retried on the
    next Stop rather than silently dropped."""
    if config.session_name_pattern() is None:
        return None
    if name is None:
        name = procs.session_name(procs.claude_pid())
    groups = match_name(name)
    if groups is None:
        return None
    if already_sent(sid):
        return None
    handoffs = common.handoff_paths(sid)
    reason = "handoff" if handoffs else ("cap" if at_cap(sid) else None)
    if reason is None:
        return None
    if callable(opener):
        try:
            opener = opener()
        except Exception:                                   # noqa: BLE001 — a chore never crashes
            opener = None
    seat = igniting_seat(name, groups, opener)
    if not seat:
        common.log("idlenotify", f"no-seat\t{name}\treason={reason}")
        return None
    transcript = None
    try:
        import context_cap                                  # noqa: PLC0415 — Stop-only cost
        transcript = context_cap.locate_transcript(sid, transcript_path)
    except Exception:                                       # noqa: BLE001
        transcript = None
    last = last_assistant_line(transcript)
    handoff = handoffs[-1] if handoffs else None
    text = message(name, handoff, last, reason)
    row = {"schema": SCHEMA, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "seat": seat,
           "from_name": name, "session_id": sid, "reason": reason, "cwd": cwd,
           "handoff": handoff, "last_line": last, "text": text}
    if not deliver(seat, row):
        return None
    mark_sent(sid, seat)
    common.log("idlenotify", f"sent\t{seat}\t{name}\t{reason}")
    return text


def unread(seat: str, since: str | None = None) -> list[dict]:
    """The seat's mailbox rows, oldest first; `since` is an ISO timestamp rows must be newer than.

    Unparseable rows are skipped rather than raising: a mailbox is appended to by one process while
    another reads it, and a torn last line is an ordinary state, not a corruption."""
    out = []
    try:
        text = mailbox(seat).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if since and str(row.get("ts") or "") <= since:
            continue
        out.append(row)
    return out


def facts_line(name: str | None = None) -> str | None:
    """The SessionStart line for a seat that has mail, or None.

    A seat is identified by its own launch NAME here, not by a pattern group: the mailbox is
    addressed to a name, and a session reading its own mail is asking "is any of this for me".
    Silent when the pattern is unset, when this session has no name, and when there is no mail —
    three different nothings that all correctly print the same nothing."""
    if config.session_name_pattern() is None:
        return None
    if name is None:
        name = procs.session_name(procs.claude_pid())
    if not name:
        return None
    rows = unread(name)
    if not rows:
        return None
    recent = rows[-5:]
    parts = [f"{r.get('from_name') or 'a session'} ({r.get('reason') or 'stopped'})" for r in recent]
    more = f", and {len(rows) - 5} earlier" if len(rows) > 5 else ""
    return ("- Sessions you ignited have reported in: " + "; ".join(parts) + more
            + f". The messages are in {mailbox(name)}.")


def main() -> int:
    """`idlenotify.py exit --name NAME` — the launcher's trailing notify.

    THE HOOK IS NOT THE ONLY EXIT. A session killed by its account limit, by a crash, or by a
    person closing the pane never runs its Stop hook, and those are precisely the exits a seat most
    needs to hear about. So a launcher ends with this call: the process has exited, the shell is
    still there, and it reports what the hook did not.

    It reads the session record the hooks already wrote — the same handoff list, the same session
    id — so the two paths cannot disagree about what happened. Finding that record BY NAME is why
    `session_start.py` records the name in the first place."""
    import argparse                                         # noqa: PLC0415 — CLI-only cost
    ap = argparse.ArgumentParser(prog="idlenotify")
    ap.add_argument("action", choices=["exit"])
    ap.add_argument("--name", required=True)
    args = ap.parse_args()
    rec = record_for_name(args.name)
    if not rec:
        print(f"idlenotify: no session record named {args.name}")
        return 0
    sent = notify(rec.get("session_id") or "-", rec.get("cwd"), None,
                  opener=None, name=args.name)
    print(f"idlenotify: {'sent' if sent else 'nothing to send'} for {args.name}")
    return 0


def record_for_name(name: str) -> dict | None:
    """The newest session record launched under `name`, or None."""
    import glob                                             # noqa: PLC0415
    best, best_ts = None, ""
    for f in glob.glob(str(config.state() / "session-start-*.json")):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or d.get("name") != name:
            continue
        ts = str(d.get("ts") or "")
        if ts >= best_ts:
            best, best_ts = d, ts
    return best


if __name__ == "__main__":                                  # pragma: no cover - CLI
    raise SystemExit(main())
