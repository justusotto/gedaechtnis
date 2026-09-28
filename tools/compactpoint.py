#!/usr/bin/env python3
"""compactpoint.py — write a COMPACT POINT into this session's state-of-record file.

    python3 tools/compactpoint.py write <state-file> --session-id <id> [--name <name>] [--transcript <path>]

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
import argparse, os, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import common        # noqa: E402
import compact_door  # noqa: E402

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
    compact_door.record_file(sid, state_file)
    return block


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("state_file")
    w.add_argument("--session-id", default=os.environ.get("CLAUDE_CODE_SESSION_ID") or "-")
    w.add_argument("--name")
    w.add_argument("--transcript")
    a = ap.parse_args(argv)
    if not a.session_id or a.session_id == "-":
        # recorded under no session, the point is one the door can never find — and the person would
        # hear "READY" followed by a refusal. Refused here instead, in words.
        print("compactpoint: no session id — pass --session-id <id> (the boot facts block's \"Session id\" "
              "line); CLAUDE_CODE_SESSION_ID is not set in this shell. Nothing was written.", file=sys.stderr)
        return 2
    p = Path(a.state_file).expanduser().resolve()
    block = write(p, a.session_id, a.name, os.getcwd(), a.transcript)
    print(block, end="")
    print(f"\nWritten to {p}. Now replace the three `{PLACEHOLDER}` lines in that file with: the orders in "
          "force (what the owner has ruled that still binds), what is LIVE and why (each with its sha or "
          "path), and the numbered RESUME ORDER. Then say: READY — type /compact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
