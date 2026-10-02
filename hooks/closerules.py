#!/usr/bin/env python3
"""closerules.py — what a session's own transcript says about whether it is FINISHED (SESSCLOSE-2).

The owner, 2026-09-27: "we need the not anymore running ones to get closed automatically while not
closing them to early just because they pasue or wait for tasks or answers."

The first close sweep (TERMOVERLOAD-1) judged 16 live sessions and kept all 16, for two reasons that
were about the MACHINE, not the session: unsaved work was counted for the whole shared checkout
(~100 standing dirty paths, none of them the session's), and "finished" could only be a handoff file
the launch ledger named. This module reads the SESSION instead, from its transcript alone:

  * `scan()` walks one transcript once and returns its facts at every turn end — the words the turn
    ended on, the background tasks and scheduled wake-ups still open, the files it wrote, whether
    the owner typed into it — plus the timestamps of everything that happened after.
  * `verdict()` is the transcript half of the close rules, PURE, so each rule has a test and the
    same function serves the live sweep (`fleet.classify`) and the replay below.
  * `replay()` is the pilot: over the last days of real transcripts it finds the moment the rules
    would first have closed each session and whether anything happened AFTER that moment — a
    message, a resume, a reply, a write. Each such case is a would-be WRONG close.

What always KEEPS, however long a session sits (the owner's "pause or wait"):
  - last words that ask or wait (`ASK_TEXT`, `WAIT_TEXT`);
  - a turn that has not ended (a tool call without its result — a permission prompt looks like this);
  - a background task, Monitor or agent it started that has not reported back, a scheduled wake-up
    not yet due, a recurring job not deleted;
  - an owner conversation — a session the owner has typed into since its first prompt is his.
Silence is never a reason on its own: every rule reads a fact from the transcript.

No writes anywhere; reads transcripts only.
"""
from __future__ import annotations
import json, os, re, time
from pathlib import Path

# The fleet's wording for a turn that ended while WAITING (TERMOVERLOAD-1 reviewer rev. 1).
WAIT_TEXT = re.compile(
    r"\bHOLDING\b|\bwait(?:ing|s)? (?:on|for|until)\b|\bblocked\b|\bpaused\b|\bstand(?:ing)? by\b|"
    r"\bthe owner'?s to answer\b|\bawait(?:ing|s)?\b|\bonce (?:you|the seat|he|it|they)\b|"
    r"\bwhen (?:you|the seat|he) (?:reply|replies|answer|answers|say|says|confirm)|"
    r"\buntil (?:you|the seat|he|the suite|it)\b|\bwill (?:continue|resume|merge|proceed|report) "
    r"(?:when|once|after|as soon)\b|\bI'?ll (?:wait|check back|report back|be notified)\b|"
    r"\bstill running\b|\b(?:is|are) running\b|\bin the background\b|\bnot yet\b|\bpending\b", re.I)
# A turn that ended on a question or a request for someone's word.
ASK_TEXT = re.compile(
    r"\?(?:[\s*_`)\]\"'»]|$)|\bshall I\b|\bshould I\b|\bwant me to\b|\bwould you like\b|"
    r"\blet me know\b|\byour (?:call|decision|go|word|answer|approval|pick)\b|\bplease (?:confirm|"
    r"approve|decide|choose|reply|answer|advise)\b|\bY/N\b|\b(?:needs?|awaits?) (?:your|a|the "
    r"owner'?s|the seat'?s) (?:decision|call|answer|approval|go|word|ruling)\b|\bover to you\b|"
    r"\bif you (?:want|approve|agree|prefer)\b|\bwhich (?:one|option) do you\b|\bOK to\b", re.I)
# "Needs a decision: none" and similar debrief lines are not requests.
NONE_ASK = re.compile(r"\b(?:needs? (?:a|your) (?:decision|call)|open questions?)\s*[:—-]\s*"
                      r"(?:none|nothing|—|-)\b\.?", re.I)
# A turn that ended on a statement that the work is over.
FINAL_TEXT = re.compile(
    r"\bSESSION COMPLETE\b|\bmerged\b|\blanded\b|\b(?:all |work |row |task |job )?(?:done|complete"
    r"|completed|finished)\b|\bhandoff (?:is )?(?:written|committed|on disk)\b|\bshipped\b|"
    r"\bnothing (?:left|more|else) (?:to do|for me)\b|\bclosing out\b|\bsigning off\b|"
    r"\bwrapped up\b|\bfinal report\b", re.I)
# A delivered report / handoff, by the file name the session wrote.
REPORT_NAME = re.compile(r"(?i)(?:^|[/_.-])(?:handoff|report|review|findings|verdict|summary|"
                         r"results?|debrief|disposition)[^/]*\.(?:md|json|html|txt|tsv)$")
HANDOFF_NAME = re.compile(r"(?i)(?:^|/)handoff[^/]*\.md$")

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
BG_TOOLS = ("Agent", "Task", "Monitor", "Workflow")
_BG_ID = re.compile(r"with ID: (\w+)|agentId: (\w+)|Monitor started \(task (\w+)|\btask[_ ]id[\"':\s]+(\w+)")
_TN_ID = re.compile(r"<task-id>\s*(\w+)\s*</task-id>")
_TN_END = re.compile(r"<status>\s*(completed|failed|killed|stopped|cancelled|expired)\s*</status>")
END_PAIR_S = 60.0                    # two `end_turn` records this close are one message's parts
TAIL_TEXT = 700                      # the "last words" are this many characters of the final text


def _reported(body, open_bg: dict, queued: bool = False) -> bool:
    """Whether `body` is a task notification; one with a final status takes its task off `open_bg`.
    `queued`: the text of a queue or attachment record, which must BEGIN with the notification —
    a prompt or a peer's message that quotes one is somebody's words, not a report."""
    if not isinstance(body, str) or "<task-notification>" not in body[:200]:
        return False
    if queued and not body.lstrip().startswith("<task-notification>"):
        return False
    for block in body.split("<task-notification>")[1:]:     # id and status are paired per block
        tid, end = _TN_ID.search(block), _TN_END.search(block)
        if tid and end:
            open_bg.pop(tid.group(1), None)
    return True


def _ts(s) -> float | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        from datetime import datetime                          # noqa: PLC0415
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "tool_result":
                c = b.get("content")
                out.append(c if isinstance(c, str) else _text_of(c))
    return " ".join(out)


def last_words(text: str) -> str:
    t = " ".join((text or "").split())
    return t[-TAIL_TEXT:]


def asks_or_waits(text: str) -> str | None:
    """'asks' / 'waits' when the last words ask or wait, else None."""
    w = NONE_ASK.sub(" ", last_words(text))
    if WAIT_TEXT.search(w):
        return "waits"
    if ASK_TEXT.search(w):
        return "asks"
    return None


def says_final(text: str) -> bool:
    return bool(FINAL_TEXT.search(last_words(text)))


def scan(path: Path, lines=None) -> dict:
    """One pass over a transcript. Returns {name, sid, cwd, ends, activity, pending, last}.

    ends: one snapshot per main-chain turn that ENDED (assistant end_turn, no tool call left
          without its result): {ts, text, open_bg, open_bg_ts (id -> when it was started), began
          (when the turn began: the last prompt, message or notification), sent (the messages the
          turn sent with SendMessage, [{to, ts}]), wake_until, cron, writes, reports, humans}.
    activity: timestamps of every record that means the session was USED — a user record of any
          origin (typed, a peer's message, a task notification), a queued prompt, an assistant record.
    last: the snapshot of the final end if the transcript's last conversation record IS that end,
          else None (the session is mid-turn: working, or at a prompt).
    """
    if lines is None:
        try:
            lines = path.read_bytes().decode("utf-8", "replace").splitlines()
        except OSError:
            lines = []
    name = sid = cwd = None
    uses: dict[str, tuple[str, dict]] = {}
    open_calls: set[str] = set()
    open_bg: dict[str, float | None] = {}              # task id -> when it was started (CLOSETUNE-1)
    sent: list[dict] = []                              # messages it sent in the current turn
    began = None                                       # when the current turn began
    writes: dict[str, float] = {}
    reports: list[str] = []
    wake_until = 0.0
    cron = 0
    humans = 0
    turn_text = ""
    ends: list[dict] = []
    activity: list[float] = []
    last_is_end = False
    end_at = end_mid = None                            # the last end record's time and message id
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        typ = rec.get("type")
        if typ in ("custom-title", "agent-name"):
            name = rec.get("customTitle") or rec.get("agentName") or name
            continue
        if rec.get("isSidechain"):
            continue
        ts = _ts(rec.get("timestamp"))
        sid = sid or rec.get("sessionId")
        cwd = cwd or rec.get("cwd")
        if typ == "queue-operation":
            if rec.get("operation") == "enqueue":
                if ts:
                    activity.append(ts)
                # CLOSETUNE-2 (2026-10-02): a task that reports while the session is WORKING is
                # queued and handed over as an attachment, never as a user record. Not reading
                # these left every such task "never reported back" (15 finished sessions kept).
                _reported(rec.get("content"), open_bg, queued=True)
            continue
        if typ == "attachment":
            att = rec.get("attachment")
            if isinstance(att, dict) and att.get("type") == "queued_command":
                _reported(att.get("prompt"), open_bg, queued=True)
            continue
        if typ not in ("user", "assistant"):
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        blocks = content if isinstance(content, list) else []
        if ts:
            activity.append(ts)
        if typ == "user":
            last_is_end = False
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            for b in results:
                uid = b.get("tool_use_id")
                open_calls.discard(uid)
                tool, inp = uses.get(uid, ("", {}))
                if tool in BG_TOOLS or inp.get("run_in_background"):
                    body = _text_of([b])
                    for m in _BG_ID.finditer(body[:2000]):
                        open_bg.setdefault(next(g for g in m.groups() if g), ts)   # None: unknown
                elif tool == "SendMessage" and not b.get("is_error"):
                    to = inp.get("to") or inp.get("recipient")
                    if isinstance(to, str) and to:
                        sent.append({"to": to, "ts": ts or 0.0})
            if not results:
                body = _text_of(content)
                if _reported(body, open_bg):
                    pass
                elif not rec.get("isMeta") and (rec.get("origin") or {}).get("kind") in (None, "human"):
                    humans += 1
                turn_text = ""
                began, sent = ts, []
            continue
        # assistant
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                uid, tool, inp = b.get("id"), b.get("name") or "", b.get("input") or {}
                uses[uid] = (tool, inp if isinstance(inp, dict) else {})
                open_calls.add(uid)
                turn_text = ""                                  # last words come AFTER the last tool
                if tool in WRITE_TOOLS:
                    fp = inp.get("file_path") or inp.get("notebook_path")
                    if isinstance(fp, str) and fp:
                        writes[fp] = ts or 0.0
                        if REPORT_NAME.search(fp):
                            reports.append(fp)
                elif tool == "ScheduleWakeup" and not inp.get("stop"):
                    try:
                        wake_until = max(wake_until, (ts or 0) + float(inp.get("delaySeconds") or 0))
                    except (TypeError, ValueError):
                        pass
                elif tool == "ScheduleWakeup" and inp.get("stop"):
                    wake_until = 0.0
                elif tool == "CronCreate":
                    cron += 1
                elif tool == "CronDelete":
                    cron = max(0, cron - 1)
            elif b.get("type") == "text" and (b.get("text") or "").strip():
                turn_text = (turn_text + " " + b["text"]).strip()
        # `not open_calls` is defence in depth: the API ends a turn that has a tool call open with
        # stop_reason "tool_use", never "end_turn" (reviewer, 2026-09-27) — so today it never fires.
        # Keep it: a turn with a call awaiting its result or a prompt is the one thing never closed.
        ended = msg.get("stop_reason") == "end_turn" and not open_calls
        # One final message can arrive as two records (its thinking, then its text), each marked
        # `end_turn`: that is ONE turn end, and the later record replaces the earlier snapshot
        # (CLOSETUNE-2: the replay read the second record as a turn the session did on its own).
        # Only records of the SAME message (its id, or END_PAIR_S apart: measured 6 ms and 1 s) —
        # a reply hours later with nothing recorded in between is a turn of its own.
        mid = msg.get("id")
        if ended and last_is_end and ends and (
                (mid and mid == end_mid) or
                (not (mid and end_mid) and ts is not None and end_at is not None
                 and 0 <= ts - end_at <= END_PAIR_S)):
            ends.pop()
        last_is_end = ended
        if ended:
            end_at, end_mid = ts, mid
            ends.append({"ts": ts, "text": turn_text, "open_bg": sorted(open_bg),
                         "open_bg_ts": dict(open_bg), "began": began, "sent": list(sent),
                         "wake_until": wake_until, "cron": cron, "writes": dict(writes),
                         "reports": list(reports), "humans": humans})
    return {"name": name, "sid": sid, "cwd": cwd, "ends": ends, "activity": activity,
            "last": ends[-1] if (ends and last_is_end) else None, "path": str(path)}


def verdict(end: dict | None, now: float, idle_min: float, handoff: bool = False) -> list[str]:
    """The transcript half of the close rules for a session whose last turn is `end`. [] = the
    transcript says finished. Pure. `handoff`: a handoff file the launch ledger named exists.

    Finished = a handoff file, a report/handoff the session itself wrote, or final words saying so;
    then `idle_min` of quiet. ONE threshold for all three: the replay of 2026-09-24..27 found
    finished sessions (handoff written, "Idle.") messaged with new work by their orchestrator up to
    139 minutes later — chaining work in a warm session is house practice — so a short idle for a
    handoff would close exactly the sessions that are about to be re-used."""
    if end is None:
        return ["idle: its last turn has not ended (working, or a tool call waiting on a prompt)"]
    keep = []
    how = asks_or_waits(end.get("text") or "")
    if how:
        keep.append(f"not-waiting: its last words {how} ({last_words(end.get('text') or '')[-90:]!r})")
    if end.get("open_bg"):
        keep.append(f"background: {len(end['open_bg'])} task(s) it started have not reported back "
                    f"(e.g. {end['open_bg'][0]})")
    if (end.get("wake_until") or 0) > now:
        keep.append("background: it scheduled a wake-up that is not due yet")
    if end.get("cron"):
        keep.append("background: it created a recurring job it has not deleted")
    if (end.get("humans") or 0) > 1:
        keep.append("conversation: the owner has typed into it since its first prompt — his to close")
    evidence = bool(handoff or end.get("reports"))
    final = says_final(end.get("text") or "")
    if not (evidence or final):
        keep.append("finished: no handoff file, no report written and no final statement")
    ts = end.get("ts")
    if ts is None or now - ts < idle_min * 60:
        keep.append(f"idle: its turn ended less than {idle_min:g} minutes ago")
    return keep


UNKNOWN_FACTOR = 4          # an UNKNOWN keeps only while the session is younger than idle × this
REPORT_IDLE_MIN = 30        # minutes of quiet after a done handoff or a report to its seat


def finished(end: dict | None, now: float, idle_min: float, handoff_done: float | None = None,
             seat: str | None = None, slack_s: float = 300, nothing_runs: bool = False) -> dict:
    """CLOSETUNE-1: whether a session is FINISHED, from facts about time, never from its words. Pure.

    Finished when its last turn ENDED (no tool call without its result at the tail) and ONE of:
      (time)    its last assistant turn is at least `idle_min` minutes old;
      (handoff) `handoff_done` — the author time of the last commit of its handoff whose State
                says DONE / LANDED / STOPPED — falls INSIDE the final turn (from when it began to
                `slack_s` after it ended): the session committed it as its last act — and the
                turn ended at least REPORT_IDLE_MIN minutes ago (CLOSETUNE-2: the replay of
                2026-09-29..10-02 found three such sessions messaged by their seat 2, 13 and 16
                minutes after the handoff; none after 30);
      (report)  the final turn sent a message to `seat` (SendMessage `to`, no error) and ended at
                least REPORT_IDLE_MIN minutes ago.
    Returns {how, age_s, hard, soft, notes}: `how` = which criterion held (None = not finished);
    `hard` keeps whatever the age; `soft` keeps only while age < idle × UNKNOWN_FACTOR (the caller
    adds its own soft facts: git unknown or dirt, an unreadable screen); `notes` never keep — the
    last words that ask or wait, and the old evidence rule, are printed, not obeyed.
    `nothing_runs` (CLOSETUNE-2): the caller READ that nothing of this session's runs — its registry
    status is idle and it has no live process of its own. An OLD task that never reported is then
    a note, not a doubt. A young one keeps either way."""
    out = {"how": None, "age_s": None, "hard": [], "soft": [], "notes": []}
    if end is None:
        out["hard"].append("idle: its last turn has not ended (working, or a tool call waiting on a prompt)")
        return out
    ts = end.get("ts")
    age = (now - ts) if ts is not None else None
    out["age_s"] = age
    if age is not None and age >= idle_min * 60:
        out["how"] = f"its last turn ended {age / 3600:.1f} h ago (≥ {idle_min / 60:g} h)"
    elif (handoff_done is not None and end.get("began") is not None and ts is not None
          and end["began"] <= handoff_done <= ts + slack_s and age >= REPORT_IDLE_MIN * 60):
        out["how"] = (f"its handoff's State line says done/landed/stopped, no turn began after its "
                      f"commit, and it has been quiet {age / 60:.0f} min (≥ {REPORT_IDLE_MIN})")
    elif (seat and age is not None and age >= REPORT_IDLE_MIN * 60
          and any(s.get("to") == seat for s in end.get("sent") or [])):
        out["how"] = (f"it sent its report to its seat {seat} and has been quiet "
                      f"{age / 60:.0f} min (≥ {REPORT_IDLE_MIN})")
    if out["how"] is None:
        out["hard"].append(
            f"finished: its last turn ended {'at an unknown time' if age is None else f'{max(age, 0) / 60:.0f} min ago'}"
            f" (< {idle_min:g}), and no done/landed/stopped handoff or report to its seat says otherwise")
    how = asks_or_waits(end.get("text") or "")
    if how:
        out["notes"].append(f"its last words {how} ({last_words(end.get('text') or '')[-90:]!r})")
    limit = now - idle_min * 60
    # A task whose start time was not recorded is YOUNG (a hard keep); a snapshot from before
    # CLOSETUNE-1 carries no start times at all, and its tasks count as old.
    bg_ts = end.get("open_bg_ts")
    if bg_ts is None:
        bg_ts = {t: float("-inf") for t in end.get("open_bg") or []}
    young = sorted(t for t, st in bg_ts.items() if st is None or st > limit)
    old = sorted(t for t in bg_ts if t not in young)
    if young:
        out["hard"].append(f"background: {len(young)} task(s) it started less than {idle_min:g} min "
                           f"ago have not reported back (e.g. {young[0]})")
    if old and nothing_runs:
        out["notes"].append(f"{len(old)} older task(s) it started never reported back (e.g. {old[0]}); "
                            f"its registry status is idle and no process of its own is running")
    elif old:
        out["soft"].append(f"background: {len(old)} older task(s) it started never reported back, and "
                           f"whether they still run cannot be told (e.g. {old[0]})")
    if (end.get("wake_until") or 0) > now:
        out["hard"].append("background: it scheduled a wake-up that is not due yet")
    if end.get("cron"):
        out["hard"].append("background: it created a recurring job it has not deleted")
    if (end.get("humans") or 0) > 1:
        out["soft"].append("conversation: the owner has typed into it since its first prompt")
    return out


def close_moment(end: dict, idle_min: float, handoff: bool = False) -> float | None:
    """The earliest time `verdict()` would pass for this end, or None if it never would."""
    if end.get("ts") is None:
        return None
    t = max(end["ts"] + idle_min * 60, (end.get("wake_until") or 0) + 1)
    return t if not verdict(end, t, idle_min, handoff) else None


def live_moment(end: dict, idle_min: float, handoff_done: float | None = None,
                seat: str | None = None, nothing_runs: bool = False) -> float | None:
    """The earliest time the LIVE rule (`finished`, as `fleet.classify` applies it: no hard keep,
    and a soft keep only up to idle × UNKNOWN_FACTOR) would close a session whose last turn is
    `end`, or None if it never would. Pure: `finished` asked at each moment its answer can change."""
    ts = end.get("ts")
    if ts is None:
        return None
    line = idle_min * 60 * UNKNOWN_FACTOR
    starts = [st for st in (end.get("open_bg_ts") or {}).values() if st]
    for t in sorted({ts, ts + REPORT_IDLE_MIN * 60, ts + idle_min * 60, ts + line,
                     (end.get("wake_until") or 0) + 1, *(st + idle_min * 60 for st in starts)}):
        if t < ts:
            continue
        v = finished(end, t, idle_min, handoff_done, seat, nothing_runs=nothing_runs)
        if v["how"] and not v["hard"] and (not v["soft"] or t - ts >= line):
            return t
    return None


# ------------------------------------------------------------------------------------ replay ----

def transcripts(root: Path, since: float) -> list[Path]:
    """Main-session transcripts touched since `since` (subagent transcripts live one level deeper).

    `./*/*.jsonl`, never `*/*.jsonl`: the project folders start with `-`, and a glob handed to a
    command that expands to `-Users-…` is read as options (Global Errata, the leading-`-` glob)."""
    out = []
    for f in root.glob("./*/*.jsonl"):
        try:
            if f.stat().st_mtime >= since:
                out.append(f)
        except OSError:
            continue
    return sorted(out)


# What may follow a close without the close having been WRONG: somebody else's act. Everything
# else that follows a close — a task of its own reporting, a reply nobody prompted, a record this
# list does not know — is counted as the session's own turn (review MF3: the allow-list is this
# side, so an unrecognised record can never hide a wrong close).
SOMEONE = ("the owner typed", "a message from another session", "a prompt was queued")
OWN_TURN = ("a background task reported", "it wrote a reply")
OTHER_TRANSCRIPT = "another transcript under its name began (a new launch or a resume)"


def replay(files: list[Path], now: float, since: float, idle_min: float, facts_fn=None,
           nothing_runs: bool = False) -> list[dict]:
    """Per session (transcripts grouped by session name; an unnamed one stands alone): the first
    moment the LIVE rule (`live_moment`) would have closed it at or after `since`, and what happened
    after. `facts_fn(scan, end)` -> (handoff_done, seat) supplies what the transcript does not hold.

    Row: {name, files, closed_at, end_text, after, after_kind, wrong, reused, reason}. Activity
    AFTER `closed_at`, in the same transcript or a later one under the same name, is one of two
    things. `reused`: someone messaged or resumed it (SOMEONE, or another transcript beginning with
    anything but the session's own turn) — the close costs the one `claude -r` line the close log
    prints. `wrong`: anything else — the session did another turn ON ITS OWN, and the close would
    have cut work off. A reused close is not the end of the walk: the session lived on, so its
    later turn ends are judged too, and one wrong close among them makes the row wrong (review
    MF1). The live-only facts (screen, unsaved work, the registry status) are NOT replayed; they
    can only keep a session. `nothing_runs`: the replay cannot read yesterday's process table.
    False (the default) is what the live rule does when it cannot read it: an old unreported task
    stays a doubt for 12 h. True drops every such doubt — the upper bound, and the gap the live
    read of the registry and the process table has to cover."""
    groups: dict[str, list[dict]] = {}
    for f in files:
        s = scan(f)
        if not s["ends"] and not s["activity"]:
            continue
        key = s["name"] or f"(unnamed) {f.stem[:8]}"
        groups.setdefault(key, []).append(s)
    rows = []
    for key, scans in sorted(groups.items()):
        acts = sorted(t for s in scans for t in s["activity"])
        ends = sorted((e for s in scans for e in s["ends"] if e.get("ts")), key=lambda e: e["ts"])
        row = {"name": key, "files": len(scans), "closed_at": None, "end_text": "", "after": None,
               "after_kind": "", "wrong": False, "reused": False, "reason": "", "ends": len(ends)}
        owner = {id(e): s for s in scans for e in s["ends"]}
        for e in ends:
            done, seat = facts_fn(owner[id(e)], e) if facts_fn else (None, None)
            t = live_moment(e, idle_min, done, seat, nothing_runs)
            if t is None or t < since:
                continue
            nxt = next((a for a in acts if a > e["ts"] + 0.5), None)
            if nxt is not None and nxt <= t:
                continue                                        # used again before the rules fired
            if t > now:
                if row["closed_at"] is None:
                    row["reason"] = "rules would fire later (still inside the idle window)"
                break
            this = {"closed_at": t, "end_text": last_words(e.get("text") or "")[-160:], "after": nxt,
                    "after_kind": "", "wrong": False, "reused": False}
            if nxt is not None:
                kind = _kind_after(scans, nxt)
                elsewhere = nxt not in owner[id(e)]["activity"]
                this["reused"] = kind in SOMEONE or (elsewhere and kind not in OWN_TURN)
                this["wrong"] = not this["reused"]
                this["after_kind"] = OTHER_TRANSCRIPT if elsewhere and this["reused"] else kind
                if elsewhere:                 # its OWN transcript went on as well: judge that too
                    own = next((a for a in sorted(owner[id(e)]["activity"]) if a > e["ts"] + 0.5), None)
                    k2 = _kind_after([owner[id(e)]], own) if own is not None else None
                    if k2 is not None and k2 not in SOMEONE:      # (fix review MF2)
                        this.update(after=own, after_kind=k2, wrong=True, reused=False)
            if row["closed_at"] is None or this["wrong"]:
                row.update(this)
            if this["wrong"] or nxt is None:
                break
        if row["closed_at"] is None and not row["reason"]:
            v = finished(ends[-1], now, idle_min, nothing_runs=nothing_runs) if ends else None
            why = (v["hard"] + v["soft"]) if v else ["no turn ever ended"]
            row["reason"] = "kept: " + (why[0] if why else "used again before the rule fired")
        rows.append(row)
    return rows


def reuse_gaps(files: list[Path], since: float, min_gap_s: float = 600) -> list[dict]:
    """Every turn end whose TRANSCRIPT rules (idle aside) say finished and that was used again later:
    {name, ended, gap_min, text}, gap ≥ `min_gap_s`, longest first. This is what the idle threshold
    has to exceed — the evidence behind `session_close_idle_minutes`."""
    out = []
    for f in files:
        s = scan(f)
        acts = sorted(s["activity"])
        for e in s["ends"]:
            if not e.get("ts") or e["ts"] < since:
                continue
            if [k for k in verdict(e, e["ts"] + 10**7, 0) if not k.startswith("idle")]:
                continue
            nxt = next((a for a in acts if a > e["ts"] + 0.5), None)
            if nxt is not None and nxt - e["ts"] >= min_gap_s:
                out.append({"name": s["name"] or f.stem[:8], "ended": e["ts"],
                             "gap_min": (nxt - e["ts"]) / 60, "text": last_words(e["text"])[-100:]})
    return sorted(out, key=lambda r: -r["gap_min"])


def _kind_after(scans: list[dict], t: float) -> str:
    """What the first record after `t` was, in words (for the replay table)."""
    for s in scans:
        try:
            lines = Path(s["path"]).read_bytes().decode("utf-8", "replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("isSidechain") or _ts(rec.get("timestamp")) != t:
                continue
            typ = rec.get("type")
            if typ not in ("user", "assistant", "queue-operation") or (
                    typ == "queue-operation" and rec.get("operation") != "enqueue"):
                continue                      # not a record `scan` counts as activity (fix review MF1)
            origin = (rec.get("origin") or {}).get("kind")
            if typ == "queue-operation":
                text = rec.get("content")
                if origin == "task-notification" or "<task-notification>" in str(text):
                    return "a background task reported"
                return ("a prompt was queued" if isinstance(text, str) and text.strip()
                        else "a queue record without text")
            if typ == "assistant":
                return "it wrote a reply"
            body = _text_of((rec.get("message") or {}).get("content"))
            if origin == "task-notification" or "<task-notification>" in body[:200]:
                return "a background task reported"
            if origin == "peer" or "Another Claude" in body[:80] or "Cross-session" in body[:40]:
                return "a message from another session"
            return "the owner typed" if origin in (None, "human") and not rec.get("isMeta") else \
                "a system/meta message"
    return "activity"


def fmt_time(t: float | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(t)) if t else "—"
