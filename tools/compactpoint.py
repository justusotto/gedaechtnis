#!/usr/bin/env python3
"""compactpoint.py — write a COMPACT POINT into this session's state-of-record file.

    python3 tools/compactpoint.py write <state-file> --session-id <id> [--name <name>] [--transcript <path>]
    python3 tools/compactpoint.py check <state-file> [--session-id <id>]   # the door's own verdict
    python3 tools/compactpoint.py renew <state-file>    # restamp the newest point's heading to now

Appends, to the end of <state-file> (created if absent):

    ## ★ COMPACT POINT — YYYY-MM-DD HH:MM +ZZZZ (<name, or the first 8 of the session id>)
    ### Machine part
    - what the plugin knows, read from the same functions the hooks act on
    **ORDERS IN FORCE:** <fill in>
    **LIVE:** <fill in>
    **RESUME ORDER:** <fill in>

The three labels are left as placeholders ON PURPOSE: they are judgment, and the PreCompact door
refuses a point whose labels are still placeholders. The model fills them, then says
"READY — type /compact". The file is recorded as this session's compact-point file, so the door
finds it without being told.
"""
from __future__ import annotations
import argparse, os, shlex, stat, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import common        # noqa: E402
import compact_door  # noqa: E402
import config        # noqa: E402

PLACEHOLDER = "<fill in>"


def _safe(what: str, fn) -> list[str]:
    try:
        return fn()
    except Exception as e:                      # one missing fact never costs the whole point
        return [f"- {what}: unknown ({type(e).__name__}: {e})"]


def machine_part(sid: str, cwd: str, transcript: str | None) -> list[str]:
    out = ["### Machine part"]

    def gauge():
        import context_cap
        tp = context_cap.locate_transcript(sid, transcript)
        n = context_cap.current_context(tp) if tp else None
        return [f"- Context: {n:,} tokens" if n is not None else "- Context: unknown (no transcript found)"]

    def sessions():
        import stallbrief
        lines = stallbrief.facts_lines()
        return lines or ["- Managed sessions: none live (or no session-name pattern configured)"]

    def claims():
        import claim
        _, got = claim._read_claims(sid)
        regions = [str(c.get("region")) for c in got if c.get("region")]
        return [f"- Region claims held: {', '.join(regions) if regions else 'none'}"]

    def window():
        import mergewindow
        root = common.git_root(cwd)
        if root is None:
            return ["- Merge window: not in a git repository"]
        w = mergewindow.read(root)
        if not w:
            return [f"- Merge window ({root.name}): free"]
        holder = str(w.get("holder") or "?")
        mine = " (this session)" if holder == sid else ""
        return [f"- Merge window ({root.name}): held by {holder[:8]}{mine}"]

    def dirty():
        import commit
        lane, _, _ = common.lane_for(cwd)
        line = commit.partition_dirty_line(lane)
        return [line] if line else [f"- Uncommitted partition paths: none recorded for {lane or 'no lane'}"]

    for what, fn in (("Context", gauge), ("Managed sessions", sessions), ("Region claims", claims),
                     ("Merge window", window), ("Uncommitted partition paths", dirty)):
        out += _safe(what, fn)
    return out


def compose(sid: str, name: str | None, cwd: str, transcript: str | None, now: datetime | None = None) -> str:
    now = (now or datetime.now()).astimezone()
    who = name or (sid[:8] if sid and sid != "-" else "unnamed")
    lines = [f"{compact_door.HEADING} — {now:%Y-%m-%d %H:%M %z} ({who})"]
    lines += machine_part(sid, cwd, transcript)
    lines += [f"**{lab}:** {PLACEHOLDER}" for lab in compact_door.LABELS]
    return "\n".join(lines) + "\n"


def write(state_file: Path, sid: str, name: str | None, cwd: str, transcript: str | None) -> str:
    block = compose(sid, name, cwd, transcript)
    try:
        old = state_file.read_text(encoding="utf-8")
    except OSError:
        old = ""
    sep = "" if not old else ("\n" if old.endswith("\n") else "\n\n")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "a", encoding="utf-8") as fh:
        fh.write(sep + block)
    # The point is the LAST thing in the file: text below it is read as part of it and counts toward
    # the 8,000-character block (2026-09-27: 10,859). A writer that ran between the append and this
    # read would leave text after it — refused in words, never left for the door to find.
    after = (compact_door._read(state_file) or "")
    if not after.endswith(block):
        raise RuntimeError(f"text was added to {state_file} after the new point while it was written; the "
                           "point must be the last thing in the file — write it again")
    compact_door.record_file(sid, state_file)
    return block


def _newest_of(blocks):
    """The door's rule for equal stamps: of two points stamped the same minute, the LATER one in the
    file wins (`compact_door.newest` keeps a later block when its stamp is `>=`). `max()` would keep
    the first — the one the session has since written past."""
    best = None
    for b in blocks:
        if best is None or b[0] >= best[0]:
            best = b
    return best


def newest_in(p: Path) -> "tuple | None":
    """(stamp, block, file) of the newest point in ONE file, read as the door reads it: the last
    `## ★ COMPACT POINT` heading to the next `## ` heading or the end of the file (the later of two
    with the same stamp); failing that, the file as a handoff carrying the three labels (stamped by
    its modification time)."""
    text = compact_door._read(p)
    if text is None:
        return None
    blocks = compact_door.blocks_in(text)
    if blocks:
        stamp, block = _newest_of(blocks)
        return stamp, block, str(p)
    hb = compact_door.handoff_block(p)
    return (hb[0], hb[1], str(p)) if hb else None


def _same_file(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def check(p: Path, sid: str | None = None) -> tuple[int, str]:
    """(0, "READY until HH:MM …") when the door would let /compact through, else (1, what is missing).

    With a session id, the point judged is the one the DOOR judges — `compact_door.newest(sid)`, the
    newest point among every file recorded for this session and its handoffs — and a READY is given
    only when that point is in `p`. Judging `p` alone said READY while the door refused: an older
    complete point in `p` beside a newer incomplete one in another recorded file, or a point in a
    file never recorded for this session (xhigh review, COMPACTDOOR-2). Without a session id, `p`'s
    own newest point is judged: with the door on, a point that passes gets (1, "NOT CONFIRMED — pass
    --session-id"), never READY; with the door off, READY, saying the door is off (then no file is
    recorded, and nothing refuses /compact).
    The verdict is the same `compact_door.judge` the door calls — age included."""
    door_on = config.compact_point()
    known = bool(sid) and sid != "-" and door_on
    if known:
        got = compact_door.newest(sid)
        if got is None:
            return 1, (f"NOT READY — the door finds no compact point for session {sid}:\n- "
                       f"{compact_door.NO_POINT}\n- a point is found only in a file `compactpoint.py write "
                       f"<file> --session-id {sid}` recorded, or in a handoff this session wrote; {p} is "
                       "neither, or carries no point\n")
        if not _same_file(got[2], p):
            return 1, (f"NOT READY — the door judges {got[2]}, not {p}: this session's newest compact point "
                       f"({got[0]:%Y-%m-%d %H:%M %z}) is there. Fix that point, or write a new one in {p} "
                       f"with `write`; then check again.\n")
    else:
        got = newest_in(p)
        if got is None:
            return 1, f"NOT READY — {p} carries no compact point:\n- {compact_door.NO_POINT}\n"
    missing = compact_door.judge(*got)
    if missing:
        return 1, f"NOT READY — the newest point in {got[2]}:\n- " + "\n- ".join(missing) + "\n"
    if door_on and not known:
        # The door is on and judges this SESSION's newest point in any file it recorded; without the
        # id that point cannot be found, and a READY here was followed by a refusal (round-2 xhigh
        # review, finding C). So no READY: exit 1, and say what would confirm it.
        return 1, (f"NOT CONFIRMED — pass --session-id <id> (the boot facts block's \"Session id\" line). "
                   f"The newest point in {got[2]} passes on its own, but the door judges this session's "
                   "newest point in any file it recorded, and without the session id that cannot be read.\n")
    until = (got[0] + timedelta(minutes=config.compact_point_fresh_min())).astimezone()
    out = (f"READY until {until:%H:%M} — the newest point in {got[2]} ({got[0]:%Y-%m-%d %H:%M %z}, "
           f"{len(got[1]):,} characters) passes the door's check; after {until:%H:%M} it is too old and "
           "has to be renewed.\n")
    if not door_on:
        out += ("(The door is off here — `compact_point` is not on — so nothing refuses /compact; this "
                "judged the file alone.)\n")
    return 0, out


def renew(p: Path, now: datetime | None = None) -> tuple[int, str]:
    """Restamp the NEWEST point's heading to now — the one line an edit of an existing point leaves
    stale. Only the heading's bytes change: the file is read and written as bytes, so line endings
    and every other byte stay as they were; a symlink stays a symlink and its target is renewed; the
    file keeps its mode. A file that is not UTF-8 is refused, as is one with no point — in words,
    with nothing written."""
    try:
        target = p.resolve(strict=True)
        if target.stat().st_size > compact_door.READ_MAX:
            return 2, f"compactpoint: {p} is over {compact_door.READ_MAX:,} bytes. Nothing was changed.\n"
        raw = target.read_bytes()
        mode = stat.S_IMODE(target.stat().st_mode)
    except (OSError, RuntimeError):
        return 2, f"compactpoint: cannot read {p}. Nothing was changed.\n"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return 2, (f"compactpoint: {p} is not UTF-8 (byte {e.start} cannot be read), and rewriting it would "
                   "change bytes other than the heading. Nothing was changed.\n")
    # The door reads with universal newlines; the same offsets are kept here by standing a space in
    # for the \r of a CRLF (and \n for a lone \r), so a heading is found in a CRLF file exactly
    # where the door finds it, and the line ending itself is never part of what is replaced.
    norm = text.replace("\r\n", " \n").replace("\r", "\n")
    hits = list(compact_door.HEAD_RE.finditer(compact_door._mask_fences(norm)))
    if not hits:
        return 2, (f"compactpoint: {p} has no `{compact_door.HEADING}` heading to renew. Run `write` for a new "
                   "point. Nothing was changed.\n")
    def key(m):
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M %z")
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    m = _newest_of([(key(h), h) for h in hits])[1]
    now = (now or compact_door._now()).astimezone()
    head = f"{compact_door.HEADING} — {now:%Y-%m-%d %H:%M %z} ({m.group(3)})"
    a, b = m.start(), m.end(3) + 1                  # the heading through its closing parenthesis
    new = raw[:len(text[:a].encode("utf-8"))] + head.encode("utf-8") + raw[len(text[:b].encode("utf-8")):]
    tmp = target.with_name(target.name + ".compactpoint-tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(new)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return 2, f"compactpoint: could not write {target} ({e}). Nothing was changed.\n"
    return 0, f"Renewed: {head}" + (f" (in {target})" if target != p.absolute() else "") + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("state_file")
    w.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID") or "-")
    w.add_argument("--name")
    w.add_argument("--transcript")
    c = sub.add_parser("check")
    c.add_argument("state_file")
    c.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID") or "-")
    sub.add_parser("renew").add_argument("state_file")
    a = ap.parse_args(argv)
    if a.cmd in ("check", "renew"):
        p = Path(a.state_file).expanduser()
        rc, out = check(p, a.session_id) if a.cmd == "check" else renew(p)
        (sys.stdout if rc == 0 else sys.stderr).write(out)
        return rc
    if not a.session_id or a.session_id == "-":
        # recorded under no session, the point is one the door can never find — and the person would
        # hear "READY" followed by a refusal. Refused here instead, in words.
        print("compactpoint: no session id — pass --session-id <id> (the boot facts block's \"Session id\" "
              "line); CLAUDE_CODE_SESSION_ID is not set in this shell. Nothing was written.", file=sys.stderr)
        return 2
    p = Path(a.state_file).expanduser().resolve()
    try:
        block = write(p, a.session_id, a.name, os.getcwd(), a.transcript)
    except RuntimeError as e:
        print(f"compactpoint: {e}.", file=sys.stderr)
        return 3
    print(block, end="")
    print(f"\nWritten to {p}. Now replace the three `{PLACEHOLDER}` lines in that file with: the orders in "
          "force (what the owner has ruled that still binds), what is LIVE and why (each with its sha or "
          "path), and the numbered RESUME ORDER. Write nothing below the point: text after it counts toward "
          f"its 8,000 characters. Then run `python3 {shlex.quote(str(Path(__file__).resolve()))} check "
          f"{shlex.quote(str(p))} --session-id {shlex.quote(a.session_id)}` and, only when it "
          "prints READY, say: READY — type /compact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
