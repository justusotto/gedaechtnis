#!/usr/bin/env python3
"""fanout.py — how many sub-agents may run, and who may start one.

WHY THIS EXISTS. On 2026-09-20 two research agents were started with no fan-out cap; each spawned
sub-agents of its own, ~14 sessions ran at once, and 30% of a five-hour window was gone in about
five minutes (`q:CU-2026-09-20-AGENTFANOUT-1`). The routing rule that would have prevented it —
gathering is Sonnet's work, one agent per topic, no sub-sub-agents — was PROSE, and this arc's own
measurement (LESSONYIELD-1) is that prose rules recur. So the deterministic half is a door.

TWO RULES, both decided without judgment:

  1. **A sub-agent may not start a sub-agent** unless its own brief says so. The permit is a
     `fanout: allowed` line in the sub-agent's first user prompt, or `agent_fanout_allowed: true`
     in the vault's `config.json` for a fleet that wants it everywhere. Default: DENIED. A brief
     that wants fan-out says so in one line; the cost of the default being wrong is one line, and
     the cost of no default was measured above.
  2. **A session may not exceed `agent_max_concurrent` live sub-agents.** WARN at the cap, DENY
     above it.

HOW "INSIDE A SUB-AGENT" IS KNOWN. Verified on this machine, 2026-09-20, and consistent with the
live payload dump this package already records in `common.py` and `subagent_stop.py` (2026-09-14):

  * a sub-agent's tool call carries `agent_id` (and `agent_type`) in the hook payload; the parent's
    own calls carry NEITHER. This is the primary marker.
  * `session_id` is the PARENT's in both cases — the sub-agent transcript's own records carry
    `sessionId` = the parent session and `isSidechain: true`. So session_id can never distinguish,
    and nothing here reads it for that.
  * a sub-agent's transcript lives at `<projects>/<project>/<parent-session-id>/subagents/
    agent-<agent_id>.jsonl` (verified by directory listing). That path shape is the SECONDARY
    marker, used when a payload arrives without `agent_id`: either marker alone is enough, because
    a rule that needs both fails open the moment the harness renames one field.

LIVE COUNT. The gate RESERVES a slot when it lets an `Agent` call through (`agent_open`, a
timestamp: the agent id does not exist yet). `subagent_start.py` turns the oldest reservation into
a RUNNING entry under the agent's id (`agent_running`), and `subagent_stop.py` removes that entry.
The count is reservations plus running entries. A running entry older than
`agent_open_stale_seconds` is dropped on read — a crashed sub-agent whose Stop never fired must not
wedge the door shut for the rest of the day.

AGENTCAP-1 (2026-10-01). A call can pass this door and still never start: on 2026-09-30 six
`Agent` calls were allowed here and then failed in the permission step. No sub-agent started, no
Stop fired, the six reservations stayed for six hours, and four later launches were refused with
nothing running. So a reservation that no SubagentStart has claimed within
`agent_pending_seconds` is dropped. That short expiry applies only once this session has seen a
SubagentStart (`agent_start_seen`): where the harness does not send the event (verified sent,
with the parent's `session_id` and the `agent_id`, on Claude Code 2.1.284), reservations keep the
old six-hour rule and the Stop hook releases the oldest, as before.

Nothing here raises: every failure path returns "allowed". A cost door that takes a session down
costs more than the fan-out it prevents.
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path

import common
import config as _cfg
import limits

PERMIT_RX = re.compile(r"\bfanout\s*:\s*allowed\b", re.I)
SUBAGENT_TRANSCRIPT_RX = re.compile(r"/subagents/agent-[^/]+\.jsonl$")


# ----------------------------------------------------------------- who is calling ----

def in_subagent(inp: dict) -> str:
    """The marker naming this call as a sub-agent's, or "" for a top-level session."""
    aid = inp.get("agent_id")
    if isinstance(aid, str) and aid.strip():
        return "agent_id"
    tp = inp.get("transcript_path")
    if isinstance(tp, str) and SUBAGENT_TRANSCRIPT_RX.search(tp.strip()):
        return "transcript_path"
    return ""


def projects_root() -> Path:
    return Path(os.path.expanduser(os.environ.get("CLAUDE_PROJECTS_DIR")
                                   or str(Path.home() / ".claude" / "projects")))


def subagent_transcript(inp: dict) -> Path | None:
    """This sub-agent's own transcript: the payload's path when it points at one, else the file
    `agent-<agent_id>.jsonl` under the parent session's `subagents/` directory."""
    tp = inp.get("transcript_path")
    if isinstance(tp, str) and SUBAGENT_TRANSCRIPT_RX.search(tp.strip()):
        p = Path(os.path.expanduser(tp.strip()))
        if p.is_file():
            return p
    aid = inp.get("agent_id")
    sid = common.safe_sid(inp.get("session_id") or "")
    if not isinstance(aid, str) or not aid.strip() or not sid or sid == "-":
        return None
    aid = aid.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", aid):
        return None                                  # a shaped id is never interpolated into a path
    try:
        hits = sorted(projects_root().glob(f"*/{sid}/subagents/agent-{aid}.jsonl"))
    except OSError:
        return None
    return hits[0] if hits else None


def _text_of(rec: dict) -> str:
    msg = rec.get("message")
    if isinstance(msg, str):
        return msg
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def permit_in_brief(path: Path | None) -> bool:
    """True when the sub-agent's FIRST user record carries a `fanout: allowed` line. The first
    record only: a permit must come from the brief the parent wrote, never from something the
    sub-agent later said to itself."""
    if path is None:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("type") != "user":
                    continue
                return bool(PERMIT_RX.search(_text_of(rec)))
    except OSError:
        return False
    return False


def has_permit(inp: dict) -> bool:
    if _cfg.flag("agent_fanout_allowed"):
        return True
    return permit_in_brief(subagent_transcript(inp))


# ------------------------------------------------------------------- live count ----

def _stale_seconds() -> float:
    try:
        return float(limits.get("agent_open_stale_seconds", 21600))
    except (TypeError, ValueError):
        return 21600.0


def _pending_seconds() -> float:
    try:
        return float(limits.get("agent_pending_seconds", 120))
    except (TypeError, ValueError):
        return 120.0


def _prune(doc: dict, now: float) -> list:
    """Drop what no longer counts; return the reservations (`doc["agent_running"]` is pruned too)."""
    open_ = doc.get("agent_open")
    if not isinstance(open_, list):
        open_ = []
    running = doc.get("agent_running")
    doc["agent_running"] = {k: v for k, v in (running if isinstance(running, dict) else {}).items()
                            if isinstance(v, (int, float)) and v >= now - _stale_seconds()}
    cutoff = now - (_pending_seconds() if doc.get("agent_start_seen") is True else _stale_seconds())
    kept = []
    for v in open_:
        try:
            t = float(v)
        except (TypeError, ValueError):
            continue
        if t >= cutoff:
            kept.append(t)
    doc["agent_open"] = kept
    return kept


def live_count(sid: str, now: float | None = None) -> int:
    """Live sub-agents of this session, stale entries dropped (and dropped from the record)."""
    now = time.time() if now is None else now
    out = {"n": 0}

    def mutate(doc: dict) -> None:
        out["n"] = len(_prune(doc, now)) + len(doc["agent_running"])

    common.update_session_state(sid, mutate)
    return out["n"]


def try_reserve(sid: str, cap: int, now: float | None = None) -> tuple[bool, int]:
    """Prune, CHECK the cap and TAKE the slot in ONE locked section: (reserved, live count).

    Check-then-take as two locked sections is a race, and not a theoretical one — a reviewer drove
    the real hook with three concurrent `Agent` calls one below the cap and overran it in 2 trials
    of 5, which is exactly the "several Agent calls in one message" pattern that caused the
    incident this door exists to prevent. Both halves are decided inside the same
    `update_session_state` callback, under its exclusive lock, so the count a caller acts on is the
    count it wrote."""
    now = time.time() if now is None else now
    out = {"ok": False, "n": 0}

    def mutate(doc: dict) -> None:
        kept = _prune(doc, now)
        n = len(kept) + len(doc["agent_running"])
        if n >= cap:
            out["ok"], out["n"] = False, n
            return
        kept.append(now)
        out["ok"], out["n"] = True, n + 1

    common.update_session_state(sid, mutate)
    return out["ok"], out["n"]


def record_started(sid: str, agent_id: str, now: float | None = None) -> int:
    """A sub-agent started: its reservation (the oldest) becomes a running entry under its id.
    An id that is already running (an agent that was sent a message and runs again) takes none."""
    now = time.time() if now is None else now
    out = {"n": 0}

    def mutate(doc: dict) -> None:
        kept = _prune(doc, now)
        if agent_id not in doc["agent_running"] and kept:
            kept.pop(0)
        doc["agent_running"][agent_id] = now
        doc["agent_start_seen"] = True
        out["n"] = len(kept) + len(doc["agent_running"])

    common.update_session_state(sid, mutate)
    return out["n"]


def record_closed(sid: str, agent_id: str | None = None, now: float | None = None) -> int:
    """One sub-agent ended: drop its running entry. With no id, or an id no SubagentStart recorded,
    drop the OLDEST reservation instead (the rule from before the start event was read)."""
    now = time.time() if now is None else now
    out = {"n": 0}

    def mutate(doc: dict) -> None:
        kept = _prune(doc, now)
        if agent_id and agent_id in doc["agent_running"]:
            del doc["agent_running"][agent_id]
        elif kept:
            kept.pop(0)
        out["n"] = len(kept) + len(doc["agent_running"])

    common.update_session_state(sid, mutate)
    return out["n"]


def max_concurrent() -> int:
    try:
        return int(limits.get("agent_max_concurrent", 8))
    except (TypeError, ValueError):
        return 8


# ------------------------------------------------------------------- the rules ----

def permit_refusal(inp: dict) -> tuple[str, str] | None:
    """(tag, reason) when this Agent call must be denied by the FAN-OUT PERMIT rule, else None.

    A pure read: it takes no slot and changes nothing, so it is safe to run before the model rule
    and before any reservation. The concurrency cap is NOT decided here — see `reserve_or_refuse`,
    which must check and take together."""
    marker = in_subagent(inp)
    if marker and not has_permit(inp):
        return ("fanout-permit",
                "This `Agent` call comes from INSIDE a sub-agent (marked by the payload's "
                f"`{marker}`), and its brief carries no `fanout: allowed` line. Uncapped fan-out is "
                "what burned 30% of a five-hour window in about five minutes on 2026-09-20 "
                "(`q:CU-2026-09-20-AGENTFANOUT-1`): two research agents each spawned sub-agents of "
                "their own, ~14 sessions at once. Do the work in THIS agent and return the result, "
                "or have the parent start the agents itself. If this brief genuinely needs to "
                "delegate, the parent writes `fanout: allowed` in its prompt; a fleet that wants it "
                "everywhere sets `agent_fanout_allowed: true` in config.json.")
    return None


def reserve_or_refuse(inp: dict) -> tuple[tuple[str, str] | None, str | None]:
    """Take a slot for this call, or refuse it: (refusal, warning).

    Called LAST, after every other rule has passed, so a denied call never consumes a slot — a
    refusal that counted would ratchet the door shut on every retry."""
    sid = inp.get("session_id") or "-"
    cap = max_concurrent()
    ok, live = try_reserve(sid, cap)
    if not ok:
        return (("fanout-cap",
                f"This session already has {live} live sub-agent(s) and the cap is {cap} "
                "(`agent_max_concurrent`, the p90 of peak concurrent sub-agents per session "
                "measured over 30 days of transcripts on this machine). Wait for one to finish, "
                "or raise the cap in the vault's config `limits` object with a reason. Parallel "
                "sub-agents each pay their own boot and spend the same window."), None)
    if live == cap:
        return (None, f"This starts sub-agent {cap} of {cap} (`agent_max_concurrent`). The next "
                      "one is denied until one finishes.")
    return (None, None)
