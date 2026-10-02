"""resume_gate.py — refuse a message that would wake an idle session at a cost a fresh start avoids.

WHY. Waking an idle session re-reads its whole context. When the session is large, or its prompt
cache has expired, that costs more than a fresh session that boots from the handoff the stopped one
already wrote (boot ~100-150k). Specimen 2026-09-23: three builders were woken the morning after
writing their handoffs at 340k, 494k and 303k tokens of context; one stopped on its first resumed
tool result because it was already past the context warn line.

WHAT IS REFUSED. A `SendMessage` whose target is a named, live, IDLE session on this machine when
any of these holds (owner rulings 2026-09-23 11:15, 11:35, 11:50):
  * COLD AND LARGE — its last record is older than its own cache lifetime AND its context is at or
    above `resume_cold_cap` (200,000). The lifetime is read from the target's last usage record
    that created cache: `ephemeral_1h_input_tokens` > 0 means one hour, otherwise five minutes (the
    Claude Code default). Only when no record says is `resume_cold_s` used.
  * COLD WITH TOO LITTLE HEADROOM — cold, and its context is at or above `resume_window`
    (420,000) minus `resume_min_headroom` (70,000): less than that fits no row.
  * WARM AND FULL (MSGGATE-1) — warm, and at or above `resume_window`; or warm, the message longer
    than `resume_short_chars` (1,500), and context + the message's token estimate +
    `context_reach_margin_tokens` (20,000) not under `resume_window`. A warm session is still
    working, so a short message to it under its window is always delivered (owner 2026-09-30,
    "fix that": messages to a warm orchestrator at 353-385k were parked by the headroom rule).
  * FABLE AND COLD — its model is `claude-fable-5-1` and its last record is older than its own
    cache lifetime, at any size, unless it is the SENDER'S IGNITER (below). A Fable session is
    started only on the owner's word, and waking a cold one is starting it again. A WARM Fable
    session passes with its price line (RESUMEGATE-2): the specimen was builder 25's report to the
    seat that had launched it — warm, 301,782 tokens, 118k of headroom, ~$0.08 — refused by a rule
    written against accidental wakes.
THE SENDER'S IGNITER. A session launched by another one carries that fact in its own opener ("ignited
... by the seat cursus-atlas"). When the sender's first prompt names the target in a sentence with
"ignite", the message is the reply the target is waiting for, and the Fable rule does not refuse
it. It is read from the SENDER's opener because nothing on the target's side records whom it
launched: the session-start record holds claims and working directories, and the target's own
opener was written before its builders existed. Cold-and-large and the headroom bound still apply
to an igniter.
Every decision prints what the wake would cost at the target's own model: a warm wake re-reads the
context at the cache-read rate, a cold one writes it again at the cache-write rate (`RATES` below;
where the price page lists no write rate it is ASSUMED and the line says so). The refusal names the
numbers against their bounds and says to start the work FRESH from its handoff. A message whose
FIRST LINE is `RESUME-OVERRIDE: <reason>` passes, and the override is logged to deny.log with its
reason.

WHAT PASSES, and why each case is not a refused wake:
  * a BUSY target — the message waits for its next tool round; nothing is re-read;
  * a target that is not a named live session here and not a sub-agent id ("main", a teammate
    name, a remote session) — there is no transcript on this machine to measure;
  * a warm target under `resume_window` that the message fits (above) — the case the resume
    exists for; it decides itself whether the next task fits. A session stopped by the usage limit
    inside its cache lifetime is this case and must still get its "continue";
  * a cold but small target — re-reading under `resume_cold_cap` costs less than a fresh boot plus
    reading the handoff.
Every decision leads with one line — the class used (live session or sub-agent, warm or cold),
the context, the window and DELIVERED / PARKED / REFUSED — then the size, headroom and wake price. A target that IS a live idle
session but whose transcript cannot be read is allowed WITH a note saying it was not measured —
never a silent pass, never a guessed 0.

SUB-AGENTS (SUBAGENTGATE-1). An in-process sub-agent is not in the session registry, so a message
to its id (`a` + hex, as the Agent tool returns it) is measured from its own transcript,
`<projects>/<project>/<parent session id>/subagents/agent-<id>.jsonl` (the task's `output_file`
is a link to it), and the same bounds apply. There is no registry status for a sub-agent, so it is
never skipped as busy; there is no handoff, so a refusal says to start a fresh agent from the
brief. An id with no transcript PASSES with an "UNMEASURED — no transcript" note and a log line:
a missing measurement is never a reason to refuse.

ONE MEASURER. The context figure is `context_cap.current_context`, the same function the context
warn line uses; this module adds only what that function does not read — the last record's age,
the model, and the cache lifetime.
"""
from __future__ import annotations
import json, os, re, time
from datetime import datetime
from pathlib import Path

import common
import context_cap
import limits
import procs
from common import log

OVERRIDE = "RESUME-OVERRIDE:"
_REF = re.compile(r"^(.*?)\s*\[([^\]]+)\]\s*$")        # "name [ref]" as ListAgents prints it


def sessions_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("CLAUDE_SESSIONS_DIR")
                                   or str(Path.home() / ".claude" / "sessions")))


def _registry() -> list[dict]:
    out = []
    try:
        files = sorted(sessions_dir().glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            doc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


def target_session(to: str) -> dict | None:
    """The registry record of the live session `to` names, or None when it names none (or two)."""
    to = (to or "").strip()
    if not to:
        return None
    ref = None
    m = _REF.match(to)
    if m:
        to, ref = m.group(1).strip(), m.group(2).strip()
    hits = [d for d in _registry() if d.get("name") == to]
    if ref and len(hits) > 1:
        hits = [d for d in hits if str(d.get("sessionId", "")).startswith(ref)
                or str(d.get("pid", "")) == ref]
    if len(hits) != 1:
        return None
    try:
        pid = int(hits[0].get("pid"))
    except (TypeError, ValueError):
        return None
    return hits[0] if procs.pid_alive(pid) else None


_AGENT_ID = re.compile(r"^a[0-9a-f]{8,32}$")


def subagent_transcript(agent_id: str) -> Path | None:
    """The sub-agent's own transcript, under whichever session spawned it (~30 ms over every
    project here, so there is no sender-first shortcut to keep in step)."""
    name = f"agent-{agent_id}.jsonl"
    root = Path(os.path.expanduser(os.environ.get("CLAUDE_PROJECTS_DIR")
                                   or str(Path.home() / ".claude" / "projects")))
    try:
        hits = sorted(root.glob(f"*/*/subagents/{name}"))
    except OSError:
        return None
    return hits[0] if hits else None


def last_record_age(transcript: Path, now: float) -> float | None:
    """Seconds since the transcript's last timestamped record; the file's mtime if none has one."""
    try:
        lines = context_cap._read_tail(transcript, context_cap.TAIL_BYTES)
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            ts = json.loads(line).get("timestamp")
        except (ValueError, AttributeError):
            continue
        if isinstance(ts, str):
            try:
                return now - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
    try:
        return now - transcript.stat().st_mtime
    except OSError:
        return None


# $ per million tokens: (cache read, cache write). Read rates are from the price page; the write
# rate is ASSUMED at 1.25x the input price where the page lists none (RESUMEGATE-1, seat 11:50).
RATES = {
    "claude-opus-5-5": (0.20, 5.00),
    "claude-opus-5": (0.50, 6.25),
    "claude-sonnet-5": (0.20, 2.50),
    "claude-fable-5-1": (0.25, 12.50),
}
FABLE = "claude-fable-5-1"
OPENER_BYTES = 262_144                                 # the first prompt sits in the first records


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def opener_text(transcript: Path | None) -> str:
    """The session's first prompt: its first main-chain, non-meta user record with text, or "".

    Only the first OPENER_BYTES are read; a first prompt longer than that is cut mid-record, does
    not parse, and reads as "" — no igniter, so the Fable rule refuses as before (fails safe)."""
    if transcript is None:
        return ""
    try:
        lines = context_cap._read_head(transcript, OPENER_BYTES)
    except OSError:
        return ""
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("type") != "user" or rec.get("isMeta"):
            continue
        text = _text_of((rec.get("message") or {}).get("content")).strip()
        if text:
            return text
    return ""


def names_igniter(opener: str, name: str) -> bool:
    """True when `opener` names `name` in the same sentence as a form of "ignite"."""
    if not opener or not name:
        return False
    tok = r"(?<![\w-])" + re.escape(name) + r"(?![\w-])"
    for sentence in re.split(r"(?<=[.!?])\s+|\n", opener):
        if re.search(r"\bignit", sentence, re.I) and re.search(tok, sentence):
            return True
    return False


def tail_facts(transcript: Path, sidechain: bool = False) -> dict:
    """{model, ttl_s} from the LAST main-chain records: the model of the last assistant record,
    and the cache lifetime of the last record that created cache (1 h if it wrote 1-hour cache,
    else 5 min). A key is absent when the tail does not say."""
    try:
        lines = context_cap._read_tail(transcript, context_cap.TAIL_BYTES)
    except OSError:
        return {}
    got = {}
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant" or bool(rec.get("isSidechain")) != sidechain:
            continue
        msg = rec.get("message") or {}
        if msg.get("model") == context_cap.SYNTHETIC:
            continue
        if "model" not in got and msg.get("model"):
            got["model"] = msg["model"]
        cc = (msg.get("usage") or {}).get("cache_creation")
        if "ttl_s" not in got and isinstance(cc, dict):
            h1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
            m5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
            if h1 > 0:
                got["ttl_s"] = 3600
            elif m5 > 0:
                got["ttl_s"] = 300
        if "model" in got and "ttl_s" in got:
            break
    return got


def price(model: str | None, total: int, warm: bool) -> str:
    rates = RATES.get(model or "")
    if not rates:
        return f"wake price unknown (no rate for model {model or '?'})"
    read, write = rates
    if warm:
        return f"a warm wake re-reads it for ~${total * read / 1e6:.2f} ({model}, cache read ${read}/M)"
    return (f"a cold wake writes it again for ~${total * write / 1e6:.2f} ({model}, cache write "
            f"${write}/M, assumed)")


def _override_reason(message) -> str | None:
    first = (message if isinstance(message, str) else "").lstrip().split("\n", 1)[0]
    if first.startswith(OVERRIDE):
        reason = first[len(OVERRIDE):].strip()
        return reason or None
    return None


def decide(inp: dict, now: float | None = None) -> tuple[str, str] | None:
    """("deny", reason) / ("note", text) / None (say nothing)."""
    ti = inp.get("tool_input") or {}
    to = ti.get("to") or ""
    sid = inp.get("session_id", "-")
    target = target_session(to)
    sub = target is None and bool(_AGENT_ID.match(to.strip()))
    if target is None and not sub:
        return None
    now = time.time() if now is None else now
    if sub:
        tsid = to.strip()
        target = {"name": tsid}
        transcript = subagent_transcript(tsid)
        if transcript is None:
            log("resume_gate", f"unmeasured\tsubagent\tno-transcript\tfrom={sid}\tto={to}")
            return ("note", f"Resume check: UNMEASURED — no transcript for sub-agent `{to}`, so "
                            f"this message is not checked against the resume bounds.")
    else:
        tsid = str(target.get("sessionId") or "")
        if target.get("status") == "busy":
            log("resume_gate", f"pass\tbusy\tfrom={sid}\tto={to}\ttarget={tsid}")
            return None
        transcript = context_cap.locate_transcript(tsid, None)
    total, compacted = context_cap.reading(transcript, sub) if transcript else (None, False)
    age = last_record_age(transcript, now) if transcript else None
    if total is not None and age is not None and compacted:
        return _compacted(inp, to, sid, tsid, target, transcript, age, now)
    if total is None or age is None:
        log("resume_gate", f"unmeasured\tfrom={sid}\tto={to}\ttarget={tsid}")
        return ("note", f"Resume check: could NOT measure `{to}` (no readable transcript for "
                        f"session {tsid or '?'}), so this message is not checked against the resume "
                        f"bounds.")
    facts = tail_facts(transcript, sub)
    model = facts.get("model")
    ttl = facts.get("ttl_s") or int(limits.get("resume_cold_s", 300))
    ttl_src = "its own cache" if "ttl_s" in facts else "resume_cold_s, no cache record"
    cap = int(limits.get("resume_cold_cap", 200000))
    window = int(limits.get("resume_window", 420000))
    min_head = int(limits.get("resume_min_headroom", 70000))
    warm = age <= ttl
    cost = price(model, total, warm)
    kind = "sub-agent" if sub else "live session"
    state = (f"last record {int(age) // 60} min old against a cache lifetime of {ttl:,} s "
             f"({ttl_src}) — {'warm' if warm else 'cold'}; context {total:,} tokens, "
             f"{window - total:,} of headroom before resume_window {window:,}")
    why = []                                   # in the docstring's order
    passed = ""                                # the warm rule that let it through, for the line
    if warm:
        passed, refused = _warm_rule(ti.get("message"), total, window)
        if refused:
            why.append(refused)
    else:
        if total >= cap:
            why.append(f"it is cold and its context is at or above resume_cold_cap {cap:,}")
        if total >= window - min_head:
            why.append(f"it has {window - total:,} tokens of headroom, under resume_min_headroom "
                       f"{min_head:,}")
    igniter = False
    if model == FABLE and not warm:
        sender = context_cap.locate_transcript(sid, inp.get("transcript_path"))
        igniter = names_igniter(opener_text(sender), str(target.get("name") or ""))
        if not igniter:
            why.append(f"it runs {FABLE} and is cold, and a cold Fable session is started again "
                       f"only on the owner's word")

    def head(decision: str) -> str:
        return (f"Resume check for `{to}`: {kind}, {'warm' if warm else 'cold'} — context "
                f"{total:,}, window {window:,} — {decision}.")

    if not why:
        log("resume_gate", f"pass\t{'warm' if warm else 'cold-small'}\tfrom={sid}\tto={to}\t"
                           f"total={total}\tage={int(age)}\tttl={ttl}\tmodel={model}"
                           + (f"\trule={passed.split(':', 1)[0]}" if passed else "")
                           + ("\tigniter" if igniter else ""))
        return ("note", head("DELIVERED" + (f" ({passed})" if passed else ""))
                        + f" {state[0].upper()}{state[1:]}; {cost}."
                        + (" It ignited this session, so this is the reply it is waiting for."
                           if igniter else "")
                        + " Continue if the next task fits in the headroom.")
    reason = _override_reason(ti.get("message"))
    if reason:
        log("deny", f"resume-override\tfrom={sid}\tto={to}\ttarget={tsid}\ttotal={total}\t"
                    f"age={int(age)}\tttl={ttl}\tmodel={model}\treason={reason}\t{cost}")
        return ("note", head("DELIVERED (override)")
                        + f" Resume check overridden for `{to}` ({state}; {cost}): {reason}")
    handoffs = [] if sub else common.handoff_paths(tsid)
    log("deny", f"resume{'-subagent' if sub else ''}\tfrom={sid}\tto={to}\ttarget={tsid}\t"
                f"total={total}\tage={int(age)}\tttl={ttl}\tmodel={model}\tcap={cap}\twindow={window}\theadroom={min_head}\t{cost}")
    parked = None if sub else _park(str(target.get("name") or to), "; ".join(why), ti.get("message"),
                                    sid)
    text = (head("PARKED" if parked else "REFUSED") + " "
            f"Not sent: `{to}` is idle, and waking it is refused — " + "; ".join(why) + f". "
            f"({state}; {cost}.) "
            + ("Start a fresh agent from the brief instead. " if sub else
               "Start the work FRESH from its handoff instead"
               + (f" ({handoffs[-1]})" if handoffs else "") + ". ")
            + (f"The message is parked in {parked}, and is shown to it at its next prompt or "
               f"session start. " if parked else "")
            + f"If waking it is still right, send again with the first line "
            f"`{OVERRIDE} <reason>`; the override is logged.")
    return ("deny", text)


def _message_chars(message) -> int:
    return len(message if isinstance(message, str) else json.dumps(message))


def _token_estimate(chars: int) -> int:
    """Tokens a message of `chars` characters adds, over-estimated on purpose: 3 characters a
    token, rounded up (English runs ~4; Cyrillic and code nearer 3)."""
    return -(-chars // 3)


def _warm_rule(message, total: int, window: int) -> tuple[str, str | None]:
    """(why it passes, None) or ("", why it is refused) for a message to a WARM target (MSGGATE-1).

    A warm session is still working: a message re-reads its cache for cents, and the headroom
    rule (`resume_min_headroom`) exists for the next ROW, which a message does not bring. So:
      * at or above `resume_window` — refused, whatever the message;
      * at most `resume_short_chars` characters — delivered (0 turns this pass off);
      * longer — delivered when context + its token estimate + `context_reach_margin_tokens` is
        under `resume_window`, else refused with those numbers.
    The window is `resume_window` for every target: nothing on this machine tells the gate a
    builder from a seat. The cold rules never look at this function."""
    if total >= window:
        return "", f"its context is at or above resume_window {window:,}"
    n = _message_chars(message)
    chars = int(limits.get("resume_short_chars", 0) or 0)
    if chars > 0 and n <= chars:
        return f"short: {n:,} characters, at most resume_short_chars {chars:,}", None
    est = _token_estimate(n)
    margin = int(limits.get("context_reach_margin_tokens", 20000) or 0)
    need = total + est + margin
    sums = (f"context {total:,} + message ~{est:,} tokens ({n:,} characters) + "
            f"context_reach_margin_tokens {margin:,} = {need:,}")
    if need >= window:
        return "", f"{sums}, not under resume_window {window:,}"
    return f"fits: {sums}, under resume_window {window:,}", None


def _compacted(inp: dict, to: str, sid: str, tsid: str, target: dict, transcript: Path,
               age: float, now: float) -> tuple[str, str] | None:
    """The target compacted after its last measured call: its figure is the OLD window's, and
    nothing on disk gives the new one until its next call (CONTEXTMSG-1). The size rules are not
    applied to a figure known to be stale; the Fable-cold rule, which does not depend on size, is."""
    facts = tail_facts(transcript)
    model = facts.get("model")
    ttl = facts.get("ttl_s") or int(limits.get("resume_cold_s", 300))
    warm = age <= ttl
    if model == FABLE and not warm:
        sender = context_cap.locate_transcript(sid, inp.get("transcript_path"))
        if not names_igniter(opener_text(sender), str(target.get("name") or "")):
            reason = _override_reason((inp.get("tool_input") or {}).get("message"))
            if reason:
                log("deny", f"resume-override\tfrom={sid}\tto={to}\ttarget={tsid}\tcompacted\t"
                            f"age={int(age)}\tmodel={model}\treason={reason}")
                return ("note", f"Resume check overridden for `{to}` (compacted, not yet "
                                f"measured): {reason}")
            log("deny", f"resume\tfrom={sid}\tto={to}\ttarget={tsid}\tcompacted\tage={int(age)}\t"
                        f"ttl={ttl}\tmodel={model}")
            why = (f"it runs {FABLE} and is cold, and a cold Fable session is started again only "
                   f"on the owner's word")
            parked = _park(str(target.get("name") or to), why,
                           (inp.get("tool_input") or {}).get("message"), sid)
            return ("deny", f"Not sent: `{to}` is idle, and waking it is refused — {why}. (It "
                            f"compacted after its last measured call, so its size is not yet "
                            f"measured.) "
                            + (f"The message is parked in {parked}, and is shown to it at its "
                               f"next prompt. " if parked else "")
                            + f"If waking it is still right, send again with the first line "
                            f"`{OVERRIDE} <reason>`; the override is logged.")
    log("resume_gate", f"pass\tcompacted\tfrom={sid}\tto={to}\ttarget={tsid}\tage={int(age)}\t"
                       f"model={model}")
    return ("note", f"Resume check for `{to}`: compacted, not yet measured — it compacted after its "
                    f"last measured call, so the last figure is from before the compaction and the "
                    f"size bounds are not applied to it. Continue if the next task fits.")


def _park(target_name: str, reason: str, message, sender_sid: str | None = None) -> str | None:
    """Keep the refused message where the recipient will be shown it (TERMOVERLOAD-1; delivered by
    `deliver.py`, CONTEXTMSG-1). A refused wake used to leave no trace the recipient's side could
    ever read; the sender alone knew. The sender is named by its session id through the registry,
    as the target is, so a resumed sender is not "?". Never raises: a parking failure must not turn
    a refusal into a crash."""
    try:
        import fleet                                          # noqa: PLC0415 — deny path only
        sender = fleet.session_name_of(sender_sid)
        path = fleet.park(target_name, sender, reason,
                          message if isinstance(message, str) else json.dumps(message))
        return str(path) if path else None
    except Exception as exc:                                  # noqa: BLE001
        log("resume_gate", f"park-failed\t{target_name}\t{exc}")
        return None
