#!/usr/bin/env python3
"""autocontinue.py — after the usage window resets, type "continue" into every session it cut
(CONTINUITY-1).

WHY. A session stopped by the account's usage limit ends its turn with the harness's text "You've
hit your session limit · resets 5:10pm (Europe/Berlin)" and then sits idle: nothing re-prompts it
when the window resets. Measured 2026-09-19 → 10-03: six window cuts stopped ~35 sessions, and each
waited for someone to notice (`.orchestration/review/handoffcost-2026-10-03/REPORT-2.md`).

WHAT IS A CUT SESSION. One this package launched (`sessions.py launch`: a row in the launch ledger,
the same pid AND start time still live) whose LAST main-chain user/assistant record is the
harness's `<synthetic>` rate-limit record. Anything after it — a prompt, a tool result, a reply —
means the session moved on and is not touched. The reset moment is read from the record's own
text, in the timezone it names, counted from the record's timestamp; a limit text with no reset
time (a per-model limit that needs credits) is reported and never woken.

WHAT IS SENT, AND HOW. One line, `MESSAGE`, typed into the session's own screen/tmux window — the
launcher's multiplexer is the one route every launched session has, and a script has no
`SendMessage`. The window is pinned to the PROCESS (`window_of`: the screen the claude process's own
`STY` names; the tmux pane whose pid is the claude pid), then READ: a dialog in its last lines
(`fleet.PROMPT_TEXT`) or an unreadable window means nothing is typed. The text and Enter go as two
keystroke batches. Each limit record is answered at most once (`usage-continue.json`), so a
keystroke that did not land is never repeated blindly; one pass runs at a time. KNOWN LIMITS: a
half-typed draft at the prompt would be sent with the line; a keystroke the window accepted is not
re-read to confirm the turn started.

THE RESUME CHECK STILL DECIDES. Before typing, the message is put to `resume_gate.decide` with the
bounds a `SendMessage` from a seat meets — cold and large, cold with too little headroom, cold
Fable: REFUSED, reported in `usage_continue.log`, the session left for a fresh start from its
handoff. The check names its target by registry name, so a session it cannot name uniquely (no
name, two live records, the name of another session) is UNCHECKED and nothing is typed. Nothing is
parked: "continue" read hours later at an unrelated prompt would be wrong.

WHERE IT RUNS. The Stop hook (`chore.py usagecontinue`), rate-limited machine-wide by
`usage_continue_every_s`, and `sessions.py continue [--dry-run]`. No timer of its own. A usage
limit cuts EVERY session of the account at once, so after the reset the Stop arm fires only when a
session ends a turn — the first prompt anyone types; a login item that runs `sessions.py continue`
every few minutes is what makes it unattended (not installed by this package). OFF unless
`usage_continue` is true (on under `profile: atlas`).

Never raises into a hook: every failure is one log line.
"""
from __future__ import annotations
import json, re, subprocess, time
from datetime import datetime, timedelta
from pathlib import Path

import common
import config
import context_cap
import limits
from common import log

LOG = "usage_continue"
MESSAGE = "continue — the usage window has reset (auto-continue). Carry on where you stopped."
LIMIT_RE = re.compile(r"(?:hit|reached) your (?:[\w.-]+ )*limit\s*[·∙•|-]\s*resets\s+(?P<when>.+?)\s*\((?P<tz>[^)]+)\)")
LIMIT_ANY = re.compile(r"(?:hit|reached) your .{0,40}limit", re.I)
CLOCK = re.compile(r"^(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)$", re.I)
DATED = re.compile(r"^(?P<mon>[A-Za-z]{3})\w*\s+(?P<d>\d{1,2})\s+at\s+(?P<clock>.+)$")
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                      "sep", "oct", "nov", "dec"), 1)}
GRACE_S = 60                     # the harness's reset is a minute, not a second
STAMP_SLACK_S = 120              # a record written a little after the minute it names still means today
SCREEN_TAIL_LINES = 15           # a dialog is read from the window's last lines only
PASS_BUDGET_S = 30.0             # the Stop entry has 60 s


def enabled() -> bool:
    try:
        return limits.get("usage_continue", False) is True
    except Exception:                                        # noqa: BLE001
        return False


def _epoch(ts) -> float | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _clock(text: str) -> tuple[int, int] | None:
    m = CLOCK.match(text.strip())
    if not m:
        return None
    h, mi = int(m.group("h")) % 12, int(m.group("m") or 0)
    if m.group("ap").lower() == "pm":
        h += 12
    return h, mi


def _latest(local: datetime) -> float:
    """The LATER of a wall-clock time's two readings. A repeated hour (autumn) has two instants and
    a skipped one (spring) maps either side of the gap; which `fold` is later differs between the
    two, so both are read and the later taken — a wake is late at worst, never early."""
    return max(local.replace(fold=0).timestamp(), local.replace(fold=1).timestamp())


def reset_at(when: str, tz: str, at: float) -> float | None:
    """The reset moment the limit text names, as an epoch, or None when it cannot be read.

    "5:10pm" / "1am" = the first such clock time after `at` in `tz`; "Oct 1 at 4am" = that date in
    `at`'s year, or the next year when that is already past."""
    try:
        from zoneinfo import ZoneInfo                        # noqa: PLC0415
        zone = ZoneInfo(tz.strip())
    except Exception:                                        # noqa: BLE001
        return None
    base = datetime.fromtimestamp(at, zone)
    when = when.strip()
    d = DATED.match(when)
    if d:
        mon, hm = MONTHS.get(d.group("mon").lower()[:3]), _clock(d.group("clock"))
        if not mon or not hm:
            return None
        try:
            cand = base.replace(month=mon, day=int(d.group("d")), hour=hm[0], minute=hm[1],
                                second=0, microsecond=0)
        except ValueError:
            return None
        if _latest(cand) < at - STAMP_SLACK_S:
            cand = cand.replace(year=cand.year + 1)
        return _latest(cand)
    hm = _clock(when)
    if not hm:
        return None
    cand = base.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if _latest(cand) < at - STAMP_SLACK_S:
        day = (base + timedelta(days=1)).date()
        cand = datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=zone)
    return _latest(cand)


def last_limit(transcript: Path) -> dict | None:
    """{text, at, reset, uuid} when the transcript's last main-chain user/assistant record is the
    harness's usage-limit record; None otherwise (moved on, unreadable, or never cut)."""
    try:
        lines = context_cap._read_tail(transcript, context_cap.TAIL_BYTES)
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") not in ("user", "assistant"):
            continue
        if rec.get("isSidechain"):
            continue
        if rec.get("type") == "user":
            return None
        msg = rec.get("message") or {}
        if msg.get("model") != context_cap.SYNTHETIC or rec.get("error") != "rate_limit":
            return None
        text = " ".join(b.get("text", "") for b in (msg.get("content") or [])
                        if isinstance(b, dict)).strip()
        if not LIMIT_ANY.search(text):
            return None
        at = _epoch(rec.get("timestamp"))
        m = LIMIT_RE.search(text)
        reset = reset_at(m.group("when"), m.group("tz"), at) if (m and at) else None
        return {"text": text, "at": at, "reset": reset,
                "uuid": rec.get("uuid") or rec.get("timestamp") or text}
    return None


# --------------------------------------------------------------------------------- state ----
# One document: `sent` = the limit record each session was answered for (never twice); `logged` =
# the last (record, outcome) written to the log per session, so a session that waits for hours is
# one log line, not one per pass.

def _doc_path() -> Path:
    return config.state() / "usage-continue.json"


def _doc() -> dict:
    try:
        doc = json.loads(_doc_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sent": {}, "logged": {}}
    if not isinstance(doc, dict):
        return {"sent": {}, "logged": {}}
    for k in ("sent", "logged"):
        if not isinstance(doc.get(k), dict):
            doc[k] = {}
    return doc


def _put(section: str, sid: str, value: str) -> bool:
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with open(config.state() / "usage-continue.lock", "w") as lk:
            common.lock_file(lk)
            doc = _doc()
            doc[section][sid] = value
            _doc_path().write_text(json.dumps(doc, indent=1), encoding="utf-8")
            common.unlock_file(lk)
        return True
    except OSError as e:
        log(LOG, f"state\t{sid}\t{e}")
        return False


def due(now: float | None = None) -> bool:
    """Machine-wide rate limit for the Stop arm: True at most once per `usage_continue_every_s`."""
    now = time.time() if now is None else now
    every = int(limits.get("usage_continue_every_s", 300) or 0)
    st = config.state() / "usage-continue.last"
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with open(config.state() / "usage-continue.lock", "w") as lk:
            common.lock_file(lk)
            try:
                last = float(st.read_text().strip() or 0)
            except (OSError, ValueError):
                last = 0.0
            if every and now - last < every:
                common.unlock_file(lk)
                return False
            st.write_text(str(now))
            common.unlock_file(lk)
            return True
    except OSError:
        return False


class _PassLock:
    """One pass at a time, machine-wide: a Stop pass and a hand-run pass must not both type into the
    same session between the sent-check and the mark. Non-blocking: the second pass skips."""

    def __enter__(self):
        self.fh, self.held = None, True
        try:
            import fcntl                                     # noqa: PLC0415
        except ImportError:                                  # Windows: no multiplexer to type into
            return self
        try:
            config.state().mkdir(parents=True, exist_ok=True)
            self.fh = open(config.state() / "usage-continue.pass.lock", "w")
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.held = False
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            self.fh.close()                                  # closing releases the flock
        return False


# ------------------------------------------------------------------------ window and keys ----
# The window is found from the PROCESS, never from the name alone: a screen session counts only when
# the claude process's own `STY` names it; a tmux pane only when its `pane_pid` IS the claude pid.

def window_of(row: dict) -> "tuple[str, str] | None":
    """(mux, target) for this launch's claude process, or None when it cannot be pinned to one."""
    import fleet                                             # noqa: PLC0415
    mux, name, pid = row.get("mux"), row.get("mux_session") or row.get("name"), row.get("pid")
    try:
        if mux == "screen":
            if fleet.screen_of(pid) != name:
                return None
            pids = fleet.mux_session_pids("screen", name)
            return ("screen", f"{pids[0]}.{name}") if pids and len(pids) == 1 else None
        if mux == "tmux":
            p = subprocess.run(["tmux", "list-panes", "-s", "-t", f"={name}", "-F",
                                "#{pane_id} #{pane_pid}"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=5)
            hits = [ln.split()[0] for ln in (p.stdout or "").splitlines()
                    if len(ln.split()) == 2 and ln.split()[1] == str(pid)]
            return ("tmux", hits[0]) if p.returncode == 0 and len(hits) == 1 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return None
    return None


def read_window(mux: str, target: str) -> str | None:
    import fleet                                             # noqa: PLC0415
    if mux == "screen":
        return fleet.read_screen("screen", target.split(".", 1)[1])
    try:
        p = subprocess.run(["tmux", "capture-pane", "-p", "-t", target], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=5)
        return p.stdout if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def type_into(mux: str, target: str, text: str) -> bool:
    """The text, a short pause, then Enter on its own — a burst ending in Enter can be read as a
    paste and not submitted. True when the multiplexer accepted both."""
    if mux == "screen":
        steps = [["screen", "-S", target, "-p", "0", "-X", "stuff", text],
                 ["screen", "-S", target, "-p", "0", "-X", "stuff", "\r"]]
    elif mux == "tmux":
        steps = [["tmux", "send-keys", "-t", target, "-l", text],
                 ["tmux", "send-keys", "-t", target, "Enter"]]
    else:
        return False
    try:
        for n, argv in enumerate(steps):
            if n:
                time.sleep(0.4)
            p = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               timeout=5)
            if p.returncode != 0:
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def dialog_open(screen: str) -> bool:
    """A permission dialog in the LAST lines of the window. Scrollback is not read: an old dialog,
    or the assistant quoting one, must not keep a session asleep forever."""
    import fleet                                             # noqa: PLC0415
    lines = [ln for ln in screen.splitlines() if ln.strip()][-SCREEN_TAIL_LINES:]
    return bool(fleet.PROMPT_TEXT.search("\n".join(lines)))


def _registry_record(pid) -> dict | None:
    import resume_gate                                       # noqa: PLC0415
    try:
        f = resume_gate.sessions_dir() / f"{int(pid)}.json"
        doc = json.loads(f.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------------- pass ----

def judge(row: dict, now: float, alive, locate, read_screen, typer, apply: bool,
          deadline: float = float("inf")) -> dict | None:
    """One ledger row → an outcome dict, or None when the session is not a cut one (silent)."""
    import resume_gate                                       # noqa: PLC0415
    name = row.get("name") or "?"
    if not alive(row.get("pid"), row.get("pid_start")):
        return None
    reg = _registry_record(row.get("pid")) or {}
    sid = str(reg.get("sessionId") or "")
    transcript = context_cap.locate_transcript(sid, None) if sid else None
    if transcript is None:
        return None
    lim = last_limit(transcript)
    if lim is None:
        return None
    out = {"name": name, "sid": sid, "limit": lim["text"], "uuid": lim["uuid"]}
    if _doc()["sent"].get(sid) == lim["uuid"]:
        return {**out, "outcome": "already-sent"}
    if lim["reset"] is None:
        why = ("the limit text names no reset time" if not LIMIT_RE.search(lim["text"])
               else "its reset time could not be read")
        return {**out, "outcome": "no-reset-time", "why": why}
    if now < lim["reset"] + GRACE_S:
        return {**out, "outcome": "waiting",
                "why": "resets " + time.strftime("%Y-%m-%d %H:%M", time.localtime(lim["reset"]))}
    if reg.get("status") == "busy":
        return {**out, "outcome": "busy"}
    # The resume check names its target by registry NAME. It must name exactly THIS session — else
    # it measured another session, or nothing (and `decide` reads "nothing" as a pass).
    to = str(reg.get("name") or "")
    target = resume_gate.target_session(to) if to else None
    if (target is None or str(target.get("sessionId") or "") != sid
            or str(target.get("pid")) != str(row.get("pid"))):
        return {**out, "outcome": "unchecked",
                "why": f"the resume check cannot name this session uniquely (registry name {to!r})"}
    if context_cap.reading(transcript)[0] is None:
        # `decide` lets an UNMEASURED target through with a note; typing into a session whose size
        # nobody read is exactly what the check exists to prevent.
        return {**out, "outcome": "unchecked",
                "why": "its context could not be measured (no usage record in the transcript's tail)"}
    verdict = resume_gate.decide({"session_id": "usage-continue",
                                  "tool_input": {"to": to, "message": MESSAGE}}, now, park=False)
    if verdict and verdict[0] == "deny":
        return {**out, "outcome": "refused", "why": verdict[1]}
    if time.time() > deadline:
        return {**out, "outcome": "deferred", "why": "the pass ran out of time"}
    win = locate(row)
    if win is None:
        return {**out, "outcome": "no-window",
                "why": f"its {row.get('mux') or 'no'} window could not be pinned to its process"}
    screen = read_screen(*win)
    if screen is None:
        return {**out, "outcome": "unreadable", "why": f"the {win[0]} window could not be read"}
    if dialog_open(screen):
        return {**out, "outcome": "dialog", "why": "a dialog is open in its window"}
    if not apply:
        return {**out, "outcome": "would-send"}
    if time.time() > deadline:
        return {**out, "outcome": "deferred", "why": "the pass ran out of time"}
    if not typer(win[0], win[1], MESSAGE):
        return {**out, "outcome": "send-failed", "why": f"the {win[0]} window refused the keystrokes"}
    _put("sent", sid, lim["uuid"])
    return {**out, "outcome": "sent", "why": (verdict[1] if verdict else "")}


def run_pass(apply: bool = True, now: float | None = None, by: str = "?", alive=None,
             locate=None, read_screen=None, typer=None,
             budget_s: float = PASS_BUDGET_S) -> list[dict]:
    """Judge every current launch; type into the cut ones whose window has reset. Each outcome is
    one line in `usage_continue.log` the first time it is reached for that limit record."""
    import fleet                                             # noqa: PLC0415
    now = time.time() if now is None else now
    alive = alive or fleet.same_process
    locate = locate or window_of
    read_screen = read_screen or read_window
    typer = typer or type_into
    out, deadline = [], time.time() + budget_s
    with _PassLock() as lock:
        if not lock.held:
            log(LOG, f"skipped\tby={by}\tanother pass is running")
            return out
        for row in fleet.current_launches():
            if time.time() > deadline:
                log(LOG, f"deferred\tby={by}\tbudget={budget_s:.0f}s")
                break
            try:
                res = judge(row, now, alive, locate, read_screen, typer, apply, deadline)
            except Exception as exc:                         # noqa: BLE001
                log(LOG, f"error\t{row.get('name')}\t{exc}")
                continue
            if res is None:
                continue
            out.append(res)
            key = f"{res['uuid']}|{res['outcome']}"
            if res["outcome"] == "already-sent" or _doc()["logged"].get(res["sid"]) == key:
                continue
            _put("logged", res["sid"], key)
            why = " ".join(str(res.get("why") or "").split())
            log(LOG, f"{res['outcome']}\t{res['name']}\t{res['sid']}\tby={by}\t{why}"[:600])
    return out
