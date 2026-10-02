"""context_cap.py — tell a session, once, that it is running out of room to finish cleanly.

WHY, and the numbers this is gated on. The two thresholds below are not guesses: they come from a
census of every interactive session in one installation's transcripts over 30 days (157 sessions),
measured before this module was written. What that census found:

  * a session's BOOT FLOOR — the context it already carries at its first call, before any work —
    has a median of **141,965 tokens** across the fleet, p90 **177,692**, and varies by repo from
    ~80k to ~206k;
  * the deep session types then run to a peak of **~500k median** and **~680k-790k at p90**, with
    the deepest session in the window at **932,899**;
  * the two long-running sessions that handed off cleanly did so at **690k and 695k**, while one
    that was killed at its account limit died with ~50k of headroom and left nothing on disk.

That last line is the failure this module exists to prevent: not the spend, but a session that runs
out of room DURING the handoff and loses the work's only record. So the notice fires while there is
still room to write one.

TWO LEVELS, and why they are measured differently — they answer different questions:

  WARN  = boot floor + `context_warn_over_floor_tokens` (400,000).
          Floor-RELATIVE, because this is a statement about WORK DONE, and a session in a
          heavy-boot repo has not done more work for having booted heavier. "Finish the row, write
          the handoff, hand over."
  CAP   = `context_hard_cap_tokens` (700,000), ABSOLUTE.
          The ceiling is a property of the harness, not of how much of it your boot spent. At this
          point the only safe act left is the handoff itself.

  REACH = `resume_window` − `resume_min_headroom` − `context_reach_margin_tokens` (TERMOVERLOAD-1).
          Not a statement about work done but about REACHABILITY: at `resume_window` −
          `resume_min_headroom` (350,000 shipped) the resume gate refuses every message sent to
          this session once it is idle and COLD, so nobody can hand it more work (a WARM one still
          receives messages up to `resume_window`, MSGGATE-1). WARN sits at
          floor + 400,000 — above that bound for every boot floor — so without this level a session
          became unreachable ~200,000 tokens before anything told it (specimens 2026-09-26: the seat
          at 371k needed a RESUME-OVERRIDE; WHISPERDEEP-1 at 428k was replaced from disk). Read from
          the gate's OWN keys, so the two cannot drift. "Bring your handoff up to date."

NOT A STOP CONDITION, and never a model-judged one. Both levels print one line of `additionalContext`
on a PostToolUse chore and allow everything; nothing is refused, no turn is ever cost. What to do
about it is the session's judgment — the hook supplies the arithmetic the session cannot see.

FIRES ONCE PER LEVEL PER CONTEXT WINDOW. A notice repeated on every tool call is noise, and noise is
how a real warning gets scrolled past. The levels reached are recorded in the session's own state
file, and a compaction clears them (`new_window`, CONTEXTMSG-1): the next window reaches the same
marks again, and must hear about it again.

IF THAT STATE FILE CANNOT BE WRITTEN the notice FIRES ANYWAY, on every call, and SAYS SO IN ITS OWN
TEXT. That is a deliberate choice between two bad directions, not an oversight: going quiet when the
state dir is broken would disable the warning entirely and silently, and the failure this module
exists to prevent is a session that never hears it and dies mid-handoff. A repeated notice is
irritating and visible; a missing one is invisible and costs the work.

The admission rides in the NOTICE, not in the log, because the log lives in the same state directory
that just proved unwritable -- a log line describing a broken state dir cannot be written to it. The
reader who is guaranteed to see the repetition is the session, so that is who is told why. Both
halves are pinned by tests, so whoever prefers the other trade-off has to change a test to get it,
rather than discovering it in a broken installation.

COUNTING RULES — the same five the census had to learn, implemented here over a live file:

  * context at a call = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`;
    `input_tokens` alone is 2 in a 93,000-token session;
  * assistant records are grouped by `message.id` and the LAST one wins — a streaming message is
    written many times and `output_tokens` grows across them;
  * `isSidechain` records are a SUBAGENT's context, not this session's (`sidechain=True` reads
    exactly those instead — the resume door measuring a sub-agent's own transcript);
  * a `<synthetic>` record is the harness reporting an error and carries an all-zero usage block —
    counting it produces a boot floor of 0, which is what a false zero looks like;
  * an unreadable or usage-free transcript yields NO notice, never a 0.

COST. A transcript is an append-only JSONL that reaches tens of megabytes; this runs on PostToolUse,
so it may never read the whole file. The current figure comes from a bounded TAIL read
(`TAIL_BYTES`); the floor is read once from a bounded HEAD read and then cached in session state.
Both degrade to "no notice" rather than to a slow hook.
"""
from __future__ import annotations
import json, os
from pathlib import Path

import common
import config
import limits
from common import log

TAIL_BYTES = 512 * 1024      # enough for many complete records; the last usage record is at the end
# The first main-chain assistant record sits near the start of a transcript. MEASURED 2026-09-19
# across every transcript under ~/.claude/projects: the deepest first-record offset was 546,526
# bytes, so 2 MB is roughly a 4x margin. If a future transcript shape ever breached it, `boot_floor`
# would return None and the WARN level would stop firing -- silently, which is why the miss is
# logged (`floor-not-found`) rather than merely returned.
# Overridable so the suite can actually REACH the breach: a test that cannot make a bound bite is a
# test of the happy path with the bound's name on it. Same env-seam idiom the rest of the package
# uses for the paths it has to exercise.
HEAD_BYTES = int(os.environ.get("GEDAECHTNIS_CONTEXT_HEAD_BYTES") or 2 * 1024 * 1024)
SYNTHETIC = "<synthetic>"
# Appended when the fire could not be persisted, so the repetition explains itself to the one reader
# guaranteed to see it. The log cannot carry this: it lives in the directory that just failed.
UNRECORDED_SUFFIX = (" (This notice could not be recorded — the plugin's state directory is not "
                     "writable — so it will repeat on every tool call until that is fixed.)")


def _usage_context(usage) -> int | None:
    """None — never 0 — when the record carries no usage figures at all."""
    if not isinstance(usage, dict):
        return None
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    if not any(k in usage for k in keys):
        return None
    return sum(int(usage.get(k) or 0) for k in keys)


def _main_chain_contexts(lines, sidechain: bool = False) -> "list[tuple[str, int]]":
    """(message_id, context) for main-chain assistant records, in file order, LAST record per id.

    `sidechain=True` reads the other chain instead: a sub-agent's own transcript, where every
    record is marked `isSidechain` (SUBAGENTGATE-1).

    Returns a list rather than a dict value so the caller can take the first or the last without
    caring how many times the transcript rewrote a streaming message.
    """
    seen: dict[str, int] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue                                   # a truncated first/last line of a slice
        if rec.get("type") != "assistant" or bool(rec.get("isSidechain")) != sidechain:
            continue
        msg = rec.get("message") or {}
        if msg.get("model") == SYNTHETIC:
            continue
        mid = msg.get("id")
        if not mid:
            continue
        ctx = _usage_context(msg.get("usage"))
        if ctx is None:
            continue
        seen[mid] = ctx                                # last record for this id wins
    return list(seen.items())


def _read_tail(path: Path, nbytes: int) -> list[str]:
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - nbytes))
        data = fh.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if size > nbytes and lines:
        lines = lines[1:]                              # the first line of a mid-file slice is partial
    return lines


def _read_head(path: Path, nbytes: int) -> list[str]:
    with path.open("rb") as fh:
        data = fh.read(nbytes)
    return data.decode("utf-8", errors="replace").split("\n")


def current_context(transcript: Path, sidechain: bool = False) -> int | None:
    """The context at this session's most recent completed call, or None if unreadable."""
    return reading(transcript, sidechain)[0]


def _is_boundary(rec: dict, sidechain: bool) -> bool:
    return (rec.get("type") == "system" and rec.get("subtype") == "compact_boundary"
            and bool(rec.get("isSidechain")) == sidechain)


def reading(transcript: Path, sidechain: bool = False) -> "tuple[int | None, bool]":
    """(context at the most recent completed call, COMPACTED since that call).

    CONTEXTMSG-1. A `compact_boundary` record AFTER the last usage record means the figure is from
    the window before the compaction: until the session's next call, nothing on disk says how large
    it is now (the boundary's own `postTokens` is the summary alone, not the context the next call
    carries). The resume gate read that stale figure and refused a message to a session that had
    just compacted from 374,589 to ~119,000 (specimen 2026-09-29 12:02). A caller that acts on the
    figure checks the flag."""
    try:
        lines = _read_tail(transcript, TAIL_BYTES)
    except OSError:
        return None, False
    pairs = _main_chain_contexts(lines, sidechain)
    if not pairs:
        return None, False
    last_id, compacted = pairs[-1][0], False
    for line in lines:                                 # file order: a boundary after the last call
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if _is_boundary(rec, sidechain):
            compacted = True
        elif (rec.get("type") == "assistant"
              and ((rec.get("message") or {}).get("id")) == last_id):
            compacted = False
    return pairs[-1][1], compacted


def _read_from(path: Path, offset: int, nbytes: int) -> list[str]:
    """Lines from `offset` on. A start inside a line drops that partial line; a start exactly at a
    line's beginning (where a compaction left the file) keeps it — it is the window's first call."""
    if offset <= 0:
        return _read_head(path, nbytes)
    with path.open("rb") as fh:
        fh.seek(offset - 1)
        data = fh.read(nbytes + 1)
    if data[:1] == b"\n":
        return data[1:].decode("utf-8", errors="replace").split("\n")
    return data.decode("utf-8", errors="replace").split("\n")[1:]


def boot_floor(transcript: Path, offset: int = 0) -> int | None:
    """The context at this session's FIRST call — what it carried before doing anything — or, with
    `offset` (where the latest compaction left the transcript), the first call of that window."""
    try:
        pairs = _main_chain_contexts(_read_from(transcript, offset, HEAD_BYTES))
    except OSError:
        return None
    if not pairs:
        # Not necessarily an error -- a session with no completed call yet has no floor. But if it
        # is the HEAD_BYTES bound being breached, this line is the only trace it would leave.
        log("context_cap", f"floor-not-found\t{transcript}\thead={HEAD_BYTES}")
        return None
    return pairs[0][1]


# ---------------------------------------------------------------------- per-session state ----
# A file of this module's own, the same shape and locking discipline as context_economy.py's.
# Deliberately NOT the session-start record: that document is another hook's, and three hooks read
# it for keys they own. A cost notice has no business adding fields to it.

def _state_path(sid: str) -> Path:
    return config.state() / f"context-cap-{common.safe_sid(sid)}.json"


def _load(sid: str) -> dict:
    try:
        doc = json.loads(_state_path(sid).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(sid: str, mutate) -> bool:
    """True when the change reached disk. The caller needs to know: an unrecorded fire will repeat."""
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with open(config.state() / f"context-cap-{common.safe_sid(sid)}.lock", "w") as lk:
            common.lock_file(lk)
            doc = _load(sid)
            mutate(doc)
            _state_path(sid).write_text(json.dumps(doc, indent=1), encoding="utf-8")
            common.unlock_file(lk)
        return True
    except OSError as e:
        log("context_cap", f"state\t{sid}\t{e}")
        return False


def _floor_cached(sid: str, transcript: Path) -> int | None:
    """The floor does not change during a context window; read it once and keep it. After a
    compaction (`new_window`) it is read from the first call past where the compaction left the
    transcript."""
    doc = _load(sid)
    cached = doc.get("floor")
    if isinstance(cached, int) and cached > 0:
        return cached
    offset = doc.get("window_offset")
    floor = boot_floor(transcript, offset if isinstance(offset, int) and offset > 0 else 0)
    if floor:
        _update(sid, lambda doc: doc.__setitem__("floor", floor))
    return floor


def new_window(sid: str, transcript_path: str | None = None) -> bool:
    """SessionStart `compact` (CONTEXTMSG-1): a compaction opens a new context window, so the levels
    fired in the old one and its cached floor are cleared, and the transcript's size now is kept as
    where the new window starts. Before this, each level fired once per SESSION: a seat that
    compacted nine times after its one REACH notice was never told again."""
    transcript = locate_transcript(sid, transcript_path)
    try:
        size = transcript.stat().st_size if transcript else 0
    except OSError:
        size = 0

    def mutate(doc):
        doc.pop("fired", None)
        doc.pop("floor", None)
        doc["window_offset"] = size
    ok = _update(sid, mutate)
    log("context_cap", f"new-window\t{sid}\toffset={size}\t{'ok' if ok else 'unrecorded'}")
    return ok


def _levels_fired(sid: str) -> set:
    fired = _load(sid).get("fired")
    return set(fired) if isinstance(fired, list) else set()


def _record_fired(sid: str, level: str) -> bool:
    def mutate(doc):
        got = doc.get("fired")
        got = list(got) if isinstance(got, list) else []
        if level not in got:
            got.append(level)
        doc["fired"] = got
    return _update(sid, mutate)


def locate_transcript(sid: str, transcript_path: str | None) -> Path | None:
    """The hook payload's `transcript_path` when it is there, else the session id's own file.

    The fallback is not decoration: this module's whole input is a field of someone else's payload,
    and a field that silently stops arriving would turn the notice off with no symptom at all.
    Both paths are exercised by the suite for that reason.
    """
    if transcript_path:
        p = Path(transcript_path).expanduser()
        if p.is_file():
            return p
    safe = common.safe_sid(sid or "")
    if not safe or safe == "-":
        return None
    root = Path(os.path.expanduser(os.environ.get("CLAUDE_PROJECTS_DIR")
                                   or str(Path.home() / ".claude" / "projects")))
    try:
        hits = sorted(root.glob(f"*/{safe}.jsonl"))
    except OSError:
        return None
    return hits[0] if hits else None


def reach_bound() -> int | None:
    """The REACH level, or None when it is switched off.

    `resume_window` − `resume_min_headroom` is the exact figure `resume_gate` refuses at; the margin
    moves the notice that many tokens EARLIER, so there is room to write the handoff before the
    door closes. A margin of 0 fires at the gate's own bound; a window of 0 switches REACH off."""
    window = int(limits.get("resume_window", 420000) or 0)
    if window <= 0:
        return None
    bound = (window - int(limits.get("resume_min_headroom", 70000) or 0)
             - int(limits.get("context_reach_margin_tokens", 20000) or 0))
    return bound if bound > 0 else None


def _short_clause() -> str:
    """What still reaches a WARM session past the cold bound (MSGGATE-1): a short message up to
    `resume_window`, when `resume_short_chars` is on (0 = off); a longer one while it fits."""
    chars = int(limits.get("resume_short_chars", 0) or 0)
    window = int(limits.get("resume_window", 420000) or 0)
    if chars <= 0:
        return ""
    return (f" (while this session is warm, a message of at most {chars:,} characters still passes "
            f"up to resume_window {window:,})")


def _compact_hint(sid: str) -> str:
    """The `compactpoint.py write` line for this session, when the compact ritual is on. Printed,
    never run: a point stamped by a hook but not filled in by the session would look fresh and
    say nothing."""
    try:
        if not config.compact_point():
            return ""
        import shlex
        import compact_door
        files = compact_door.recorded_files(sid)
        target = shlex.quote(files[-1]) if files else "<your state file>"
        tool = Path(__file__).resolve().parent.parent / "tools" / "compactpoint.py"
        return (f" If you will compact, write the compact point first: python3 "
                f"{shlex.quote(str(tool))} write {target} --session-id {shlex.quote(sid)} — then "
                f"fill its three lines.")
    except Exception:                                  # a hint must never cost the notice
        return ""


def notice(sid: str, transcript_path: str | None) -> str | None:
    """One line for the session, or None. Never raises: every path degrades to no notice."""
    try:
        transcript = locate_transcript(sid, transcript_path)
        if transcript is None:
            return None
        current, compacted = reading(transcript)
        if not current or compacted:                   # unreadable, no call yet, or a stale window
            return None

        cap = int(limits.get("context_hard_cap_tokens", 700000) or 0)
        over = int(limits.get("context_warn_over_floor_tokens", 400000) or 0)
        fired = _levels_fired(sid)

        if cap and current >= cap and "cap" not in fired:
            unrecorded = not _record_fired(sid, "cap")
            log("context_cap", f"cap\t{sid}\t{current}")
            return (f"CONTEXT HARD CAP — this session is at {current:,} tokens of context "
                    f"(cap {cap:,}). There is room for a handoff and not much else: write it now, "
                    f"mark what is unfinished, and hand over. Nothing here is blocked."
                    + (UNRECORDED_SUFFIX if unrecorded else ""))

        bound = reach_bound()
        if bound and current >= bound and not fired & {"reach", "warn", "cap"}:
            unrecorded = not _record_fired(sid, "reach")
            gate_at = bound + int(limits.get("context_reach_margin_tokens", 20000) or 0)
            log("context_cap", f"reach\t{sid}\t{current}\tbound={bound}")
            return (f"CONTEXT — REACH: {current:,} tokens. From {gate_at:,} the resume gate refuses "
                    f"messages sent to this session once it is idle and its cache is cold"
                    f"{_short_clause()}, and a "
                    f"refused message waits parked until this session's next prompt or compaction. "
                    f"Bring your handoff up to date now, so a successor can start from disk; if you "
                    f"are waiting on someone, write that into the handoff.{_compact_hint(sid)} "
                    f"Nothing here is blocked."
                    + (UNRECORDED_SUFFIX if unrecorded else ""))

        if over and "warn" not in fired and "cap" not in fired:
            floor = _floor_cached(sid, transcript)
            if floor and current - floor >= over:
                unrecorded = not _record_fired(sid, "warn")
                log("context_cap", f"warn\t{sid}\t{current}\tfloor={floor}")
                return (f"CONTEXT — {current:,} tokens, {current - floor:,} of them work done since "
                        f"this session's boot floor of {floor:,} (threshold {over:,}). Finish the "
                        f"row you are on, write the handoff, and hand over rather than starting "
                        f"another. Nothing here is blocked."
                        + (UNRECORDED_SUFFIX if unrecorded else ""))
    except Exception as exc:                           # a cost notice must never cost a turn
        log("context_cap", f"error\t{exc}")
    return None


def main() -> None:
    """PostToolUse, every tool. Prints at most one line, at most once per level per session.

    Its own hook entry rather than a call inside `chore.py`: chore's arms already print their own
    `additionalContext`, and a second JSON object on the same stdout is not a second notice — it is
    malformed hook output. A separate process cannot collide with one.
    """
    inp = common.read_input()
    line = notice(inp.get("session_id", "-"), inp.get("transcript_path"))
    if line:
        common.context("PostToolUse", line)


if __name__ == "__main__":
    common.guarded(main)
