#!/usr/bin/env python3
"""compact_door.py — the compact ritual (COMPACTDOOR-1 pilot, OFF unless `compact_point` is `on`).

    python3 compact_door.py door       # PreCompact (matcher `manual`): refuse a /compact with no fresh point
    python3 compact_door.py post       # PostCompact: log only
    python3 compact_door.py reinject   # SessionStart `compact`: the recorded block, byte for byte

Before a session compacts, it writes a COMPACT POINT into its own state-of-record file (a seat's
state file, a builder's handoff):

    ## ★ COMPACT POINT — 2026-09-26 17:40 +0200 (my-seat)
    ### Machine part
    - …what the plugin knows: context gauge, managed sessions, claims, merge window, dirty paths…
    **ORDERS IN FORCE:** …
    **LIVE:** …
    **RESUME ORDER:** …

`tools/compactpoint.py write <file>` writes the heading and the machine part; the model fills the
three labels. The door then lets `/compact` through only when the NEWEST point this session wrote
is fresh, complete and short enough to be put back whole. Why a door and not a rule: the ritual
existed as a remembered rule and had drifted (design: `compact-ritual-2026-09-26/DESIGN.md` —
3 of 8 coordinator blocks complete, 0 of 5 of another seat's).

★ WHAT THIS DOOR MUST NEVER DO
  * block an AUTO compaction — Claude Code runs one to recover from a full context, and a blocked
    recovery fails the request outright. The hook is registered on `manual` only, AND `door()`
    passes any other trigger: two walls, each tested.
  * block on its own bug — every entry point is wrapped; an exception is logged and the compaction
    goes through (FAIL OPEN).
  * refuse `/compact force …` — the owner's word outranks the ritual; it is logged, never judged.
"""
from __future__ import annotations
import json, os, re, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import config

HEADING = "## ★ COMPACT POINT"
HEAD_RE = re.compile(r"^## ★ COMPACT POINT — (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) ([+-]\d{4}) \((.+)\)[ \t]*$", re.M)
LABELS = ("ORDERS IN FORCE", "LIVE", "RESUME ORDER")
LABEL_RE = re.compile(r"^\*\*(ORDERS IN FORCE|LIVE|RESUME ORDER):\*\*", re.M)
MAX_CHARS = 8000                 # the hook output cap is 10,000; a longer block would be cut on re-inject
STUB_RE = re.compile(r"^(?:<[^<>]*>|todo|tbd|tk|fill in|\.\.\.|…|-|—|\?+|x+|n/?a)$", re.I)
FILES_MAX = 8


# ------------------------------------------------------------------ where the points live ----

def record_file(sid: str, path: Path | str) -> None:
    """Remember a file this session wrote a COMPACT POINT into (the same record as `record_handoff`).
    Only reads the file when the pilot is on and the name is Markdown; never raises."""
    try:
        if not config.compact_point():
            return
        p = Path(path)
        if p.suffix.lower() != ".md" or not p.is_file() or p.stat().st_size > 4_000_000:
            return
        if HEADING not in p.read_text(encoding="utf-8", errors="replace"):
            return
        s = str(p.resolve())
    except (OSError, ValueError, TypeError):
        return

    def put(doc: dict) -> None:
        got = [x for x in (doc.get("compact_files") or []) if isinstance(x, str) and x != s]
        got.append(s)
        doc["compact_files"] = got[-FILES_MAX:]
    common.update_session_state(sid, put)


def recorded_files(sid: str) -> list[str]:
    doc = common._session_doc(sid)
    got = doc.get("compact_files")
    return [x for x in got if isinstance(x, str)] if isinstance(got, list) else []


# --------------------------------------------------------------------- reading a block ----

FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
READ_MAX = 4_000_000


def _mask_fences(text: str) -> str:
    """`text` with every fenced code block (``` or ~~~, fence lines included) blanked to spaces —
    same length, same line breaks, so an offset found here slices the original. A heading or label
    QUOTED in a fence (documentation of the format) is not a compact point; read as one, it passed
    the door and was re-injected as the session's own judgment (guided review, COMPACTDOOR-1)."""
    out, fence = [], None
    for line in text.splitlines(keepends=True):
        m = FENCE_RE.match(line)
        if fence is None and m:
            fence = m.group(1)[0] * len(m.group(1))
            out.append(re.sub(r"[^\n]", " ", line)); continue
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
            out.append(re.sub(r"[^\n]", " ", line)); continue
        out.append(line)
    return "".join(out)


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > READ_MAX:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

def label_text(block: str, label: str) -> str | None:
    """What follows `**LABEL:**`, up to the next label or heading; None when the label is absent.
    Labels and headings inside a code fence do not count."""
    masked = _mask_fences(block)
    hits = list(LABEL_RE.finditer(masked))
    for i, m in enumerate(hits):
        if m.group(1) != label:
            continue
        end = hits[i + 1].start() if i + 1 < len(hits) else len(block)
        nxt = re.search(r"^#{2,3} ", masked[m.end():end], re.M)
        if nxt:
            end = m.end() + nxt.start()
        return block[m.end():end]
    return None


def is_stub(text: str) -> bool:
    """Empty, or nothing but a placeholder — `<fill in>`, TODO, TBD, `…`, `-`."""
    words = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", ln).strip() for ln in text.splitlines()]
    words = [w for w in words if w]
    return not words or all(STUB_RE.match(w) for w in words)


def problems(block: str) -> list[str]:
    out = []
    for lab in LABELS:
        t = label_text(block, lab)
        if t is None:
            out.append(f"the line **{lab}:** is missing")
        elif is_stub(t):
            out.append(f"**{lab}:** is empty or still a placeholder")
    if len(block) > MAX_CHARS:
        out.append(f"the block is {len(block):,} characters; the limit is {MAX_CHARS:,} (a longer block "
                   "cannot be put back whole after compaction)")
    return out


def blocks_in(text: str) -> list[tuple[datetime, str]]:
    """Every COMPACT POINT in a file: (stamp, block text from its heading to the next `## `)."""
    out = []
    masked = _mask_fences(text)
    hits = list(HEAD_RE.finditer(masked))
    for m in hits:
        rest = text[m.end():]
        nxt = re.search(r"^## ", masked[m.end():], re.M)
        body = rest[: nxt.start()] if nxt else rest
        try:
            stamp = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M %z")
        except ValueError:
            continue
        out.append((stamp, (m.group(0) + body).rstrip("\n") + "\n"))
    return out


def handoff_block(path: Path) -> tuple[datetime, str] | None:
    """A builder's handoff carrying the three labels counts as its compact point — no second
    document. Its block is the region from the first label to the end of the last one; its stamp
    is the file's modification time."""
    text = _read(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if text is None:
        return None
    masked = _mask_fences(text)
    hits = list(LABEL_RE.finditer(masked))
    if not hits or {m.group(1) for m in hits} != set(LABELS):
        return None
    last = hits[-1]
    nxt = re.search(r"^#{2,3} ", masked[last.end():], re.M)
    end = last.end() + (nxt.start() if nxt else len(text) - last.end())
    body = text[hits[0].start():end].rstrip("\n") + "\n"
    stamp = datetime.fromtimestamp(mtime, timezone.utc)
    head = f"{HEADING} — from the handoff {path} ({stamp.astimezone():%Y-%m-%d %H:%M %z})\n"
    return stamp, head + body


def newest(sid: str) -> tuple[datetime, str, str] | None:
    """(stamp, block, file) of the NEWEST point this session wrote — complete or not. An older
    complete point never stands in for a newer incomplete one: the newest is what the session
    last said, and it is the one that would be put back."""
    best = None
    for f in recorded_files(sid):
        text = _read(Path(f))
        if text is None:
            continue
        for stamp, block in blocks_in(text):
            if best is None or stamp >= best[0]:
                best = (stamp, block, f)
    for f in common.handoff_paths(sid):
        if f in recorded_files(sid):
            continue
        hb = handoff_block(Path(f))
        if hb and (best is None or hb[0] > best[0]):
            best = (hb[0], hb[1], f)
    return best


# ---------------------------------------------------------------------------- the door ----

def _now() -> datetime:
    """Now, as an aware datetime. GEDAECHTNIS_NOW (epoch seconds) is the test seam."""
    v = os.environ.get("GEDAECHTNIS_NOW")
    return datetime.fromtimestamp(float(v) if v else time.time(), timezone.utc)


def _pointer_path(sid: str) -> Path:
    return config.state() / f"compact-point-{common.safe_sid(sid)}.md"


def _record_pointer(sid: str, block: str | None, where: str | None, verdict: str, missing: list[str]) -> None:
    """What the re-inject will put back. `block` None = there is no point to put back: the verdict
    says so, and an OLDER pointer from an earlier compaction is never re-injected as if current."""
    if block is not None:
        config.state().mkdir(parents=True, exist_ok=True)
        with open(_pointer_path(sid), "w", encoding="utf-8", newline="") as fh:   # byte for byte, any OS
            fh.write(block)

    def put(doc: dict) -> None:
        doc["compact_point"] = {"file": where, "verdict": verdict if block is not None else "NONE",
                                "missing": missing, "at": _now().isoformat()}
    common.update_session_state(sid, put)


def door(inp: dict) -> tuple[int, str]:
    """(exit code, stderr). 2 refuses a manual /compact; everything else is 0."""
    sid = inp.get("session_id") or "-"
    trig = inp.get("trigger")
    instr = (inp.get("custom_instructions") or "").strip()
    if not config.compact_point():
        return 0, ""
    got = newest(sid)
    missing, where = [], None
    if got is None:
        missing.append("this session has written no compact point (`## ★ COMPACT POINT — …`) and no "
                       "handoff carrying **ORDERS IN FORCE:**, **LIVE:** and **RESUME ORDER:**")
    else:
        stamp, block, where = got
        age = (_now() - stamp).total_seconds() / 60
        fresh = config.compact_point_fresh_min()
        if age > fresh or age < -5:
            missing.append(f"the newest compact point ({stamp:%Y-%m-%d %H:%M %z}, in {where}) is "
                           f"{age:.0f} minutes old; it has to be at most {fresh}")
        missing += problems(block)
    forced = re.match(r"force\b", instr, re.I) is not None
    if trig != "manual" or forced or not missing:
        verdict = "PASS" if not missing else ("FORCED" if forced and trig == "manual" else "AUTO-NOT-BLOCKED")
        _record_pointer(sid, got[1] if got else None, got[2] if got else None, verdict, missing)
        common.log("compact", f"{sid}\t{trig}\t{verdict}\t{'; '.join(missing)}")
        return 0, ""
    common.log("compact", f"{sid}\t{trig}\tREFUSED\t{'; '.join(missing)}")
    return 2, ("Not compacting yet — the compact point is not ready:\n- " + "\n- ".join(missing) + "\n"
               "Ask the session to run /compact-ready (it writes the point and fills the three lines), "
               "then type /compact again. `/compact force` skips this check.\n")


def reinject(inp: dict) -> str | None:
    """The additionalContext for SessionStart `compact`: the recorded block, byte for byte."""
    if not config.compact_point() or inp.get("source") not in (None, "compact"):
        return None
    sid = inp.get("session_id") or "-"
    state = common._session_doc(sid).get("compact_point") or {}
    if state.get("verdict") not in ("PASS", "FORCED", "AUTO-NOT-BLOCKED"):
        return None                        # no point at this compaction: nothing stale is put back
    try:
        with open(_pointer_path(sid), encoding="utf-8", newline="") as fh:
            block = fh.read()
    except OSError:
        return None
    # ★ ONE CHECK, ONE RE-INJECT. The door runs on a typed /compact only (matcher `manual`), so an
    # AUTOMATIC compaction reaches this function with the state the LAST typed /compact left — a
    # PASS that may be hours old. The first re-inject after a check marks the state; a second one
    # with no check in between was not checked, and says so (fresh-boot review, 2026-09-26).
    # `state` is the snapshot read above; the write below does not update it, so read the mark first.
    unchecked = bool(state.get("reinjected"))
    try:
        common.update_session_state(sid, lambda doc: (doc.get("compact_point") or {}).__setitem__(
            "reinjected", _now().isoformat()))
    except Exception:                                  # unmarked = the next unchecked one reads as checked
        common.log("compact", f"{sid}\tREINJECT-MARK-FAILED\t{traceback.format_exc(limit=2)!r}")
    if state.get("verdict") != "PASS" or unchecked:
        try:
            at = datetime.fromisoformat(state.get("at"))
            mins = f"{(_now() - at).total_seconds() / 60:.0f} min"
        except (TypeError, ValueError):
            mins = "an unknown time"
        if unchecked:
            return (f"Compaction NOT CHECKED (an automatic compaction does not run the check): the compact "
                    f"point below was checked {mins} ago, at an earlier /compact, and may be out of date "
                    f"(source: {state.get('file')}).\n\n" + block)
        how = "AUTO compaction" if state.get("verdict") == "AUTO-NOT-BLOCKED" else "Compaction FORCED past the check"
        why = "; ".join(state.get("missing") or []) or "not recorded"
        return (f"{how}: the compact point below was recorded {mins} ago and did not pass the check "
                f"({why}); it may be out of date (source: {state.get('file')}).\n\n" + block)
    return block


def post(inp: dict) -> None:
    sid = inp.get("session_id") or "-"
    if not config.compact_point():
        return
    where = (common._session_doc(sid).get("compact_point") or {}).get("file") or ""
    named = bool(where) and where in (inp.get("compact_summary") or "")
    common.log("compact", f"{sid}\t{inp.get('trigger')}\tPOST\tsummary names the file: {'yes' if named else 'no'}")


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        inp = common.read_input()
        if which == "door":
            rc, err = door(inp)
            if err:
                sys.stderr.write(err)
            return rc
        if which == "reinject":
            text = reinject(inp)
            if text:
                common.context("SessionStart", text)
            return 0
        if which == "post":
            post(inp)
        return 0
    except Exception:                                   # FAIL OPEN: a broken door never blocks
        common.log("compact", f"FAIL-OPEN\t{which}\t{traceback.format_exc(limit=3)!r}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
