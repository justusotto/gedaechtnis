"""STALLBRIEF-1 — a seat's session start names the managed sessions that stopped moving.

The transcripts here are built in the harness's own JSONL shape, measured against real ones: the
title records (`custom-title`, `agent-name`) are the first lines, and the account-limit message is
an assistant record carrying `"error": "rate_limit"` and `isApiErrorMessage`. The one thing the
tests hand in rather than read is the process table, because a suite that read the live `ps`
would date its own verdict by whoever else is running.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

PATTERN = r"^(?P<seat>[a-z0-9-]+?)-(?P<lane>builder|audit)-(?P<qid>[A-Za-z0-9-]+)$"
NOW = 1_800_000_000.0
LIMIT = "You've hit your session limit · resets 8pm (Europe/Berlin)"


@pytest.fixture
def sb(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    import importlib
    import config, idlenotify, procs, stallbrief                         # noqa: PLC0415
    for m in (config, procs, idlenotify, stallbrief):
        importlib.reload(m)
    monkeypatch.setattr(procs, "claude_pid", lambda *a, **k: None)
    root = tmp_path / "projects"
    (root / "-some-repo").mkdir(parents=True)

    def configure(**kw):
        cfg.write_text(json.dumps(kw), encoding="utf-8")
        return stallbrief

    return {"mod": stallbrief, "procs": procs, "root": root, "configure": configure}


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def say(text, t, **extra):
    return {"type": "assistant", "timestamp": iso(t),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}, **extra}


def tool_only(t):
    return {"type": "assistant", "timestamp": iso(t),
            "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash",
                                                          "input": {}}]}}


def write_transcript(root: Path, name: str | None, records: list[dict], fname="t.jsonl") -> Path:
    head = []
    if name:
        head = [{"type": "custom-title", "customTitle": name, "sessionId": "x"},
                {"type": "agent-name", "agentName": name, "sessionId": "x"}]
    p = root / "-some-repo" / fname
    p.write_text("\n".join(json.dumps(r) for r in head + records) + "\n", encoding="utf-8")
    os.utime(p, (NOW, NOW))
    return p


def sess(name, pid=4242, age=3600, model="claude-opus-5"):
    return {"pid": pid, "name": name, "model": model, "age": age}


def block(sb, sessions, worktrees=None):
    return sb["mod"].facts_lines(worktrees=worktrees, now=NOW, sessions=sessions, root=sb["root"])


# ------------------------------------------------------------------ positive: the two stall rules

def test_a_session_whose_last_word_is_the_limit_message_is_STALLED(sb):
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [
        say("Working on it.", NOW - 120),
        say(LIMIT, NOW - 60, error="rate_limit", isApiErrorMessage=True)])
    out = block(sb, [sess("s-builder-R1")])
    assert out[0] == "- Managed sessions (1 live, 1 stalled):"
    assert "STALLED (limit reached)" in out[1], out


def test_the_limit_TEXT_alone_is_enough_when_the_error_field_is_absent(sb):
    """Two carriers of one fact; the error field is the harness's, the text is what a person sees.
    Either one alone must be read, or a harness that drops one field silences the rule."""
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [say(LIMIT, NOW - 60)])
    assert "STALLED (limit reached)" in block(sb, [sess("s-builder-R1")])[1]


def test_the_error_FIELD_alone_is_enough_when_the_wording_changes(sb):
    """The other carrier: a harness release that rewords the message must not silence the rule."""
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [
        say("Usage cap reached until 8pm.", NOW - 60, error="rate_limit", isApiErrorMessage=True)])
    assert "STALLED (limit reached)" in block(sb, [sess("s-builder-R1")])[1]


def test_a_session_silent_past_stall_minutes_is_STALLED(sb):
    sb["configure"](session_name_pattern=PATTERN, stall_minutes=20)
    write_transcript(sb["root"], "s-builder-R1", [say("Done; handoff written.", NOW - 21 * 60)])
    out = block(sb, [sess("s-builder-R1")])
    assert "STALLED (silent over 20m)" in out[1], out
    assert 'last active 21m ago: "Done; handoff written."' in out[1]


# ------------------------------------------------------------------ negatives

def test_a_recent_reply_is_not_flagged(sb):
    sb["configure"](session_name_pattern=PATTERN, stall_minutes=20)
    write_transcript(sb["root"], "s-builder-R1", [say("Still going.", NOW - 19 * 60)])
    out = block(sb, [sess("s-builder-R1")])
    assert out[0] == "- Managed sessions (1 live):"
    assert "STALLED" not in out[1]


def test_a_tool_call_keeps_the_clock_moving_and_the_quote_is_the_last_thing_said(sb):
    """A session running tools is at work. The clock reads the tool-call record; the quote reads the
    last text, so a person never sees an empty quote."""
    sb["configure"](session_name_pattern=PATTERN, stall_minutes=20)
    write_transcript(sb["root"], "s-builder-R1", [say("Running the suite.", NOW - 3600),
                                                 tool_only(NOW - 60)])
    line = block(sb, [sess("s-builder-R1")])[1]
    assert "STALLED" not in line
    assert 'last active 1m ago: "Running the suite."' in line


def test_a_subagent_talking_does_not_hide_a_stalled_main_session(sb):
    """A forked subagent writes into the same transcript. Its records are `isSidechain`; reading
    them as the session's own activity would call a stalled main session active."""
    sb["configure"](session_name_pattern=PATTERN, stall_minutes=20)
    write_transcript(sb["root"], "s-builder-R1", [
        say("Working the queue.", NOW - 30 * 60),
        say("subagent chatter", NOW - 60, isSidechain=True)])
    line = block(sb, [sess("s-builder-R1")])[1]
    assert 'last active 30m ago: "Working the queue."' in line
    assert "STALLED (silent over 20m)" in line


def test_the_limit_phrase_quoted_inside_a_sentence_is_not_the_limit(sb):
    """A session reporting on another ("builder 3 said: You've hit your session limit") is not
    itself at the limit. The harness's own message starts the line; a quotation does not."""
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [say("Builder 3 said: " + LIMIT, NOW - 60)])
    assert "STALLED" not in block(sb, [sess("s-builder-R1")])[1]


@pytest.mark.parametrize("bad", [True, 0, -5, "20", 2.5])
def test_a_stall_minutes_that_is_not_a_positive_integer_falls_back_to_the_default(sb, bad):
    """JSON `true` is a Python bool, and a bool is an int — without the explicit refusal it would
    read as a one-minute clock."""
    sb["configure"](session_name_pattern=PATTERN, stall_minutes=bad)
    assert sb["mod"].stall_minutes() == sb["mod"].STALL_MINUTES_DEFAULT


def test_a_reply_after_the_limit_clears_it(sb):
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [
        say(LIMIT, NOW - 600, error="rate_limit"), say("Resumed.", NOW - 30)])
    assert "STALLED" not in block(sb, [sess("s-builder-R1")])[1]


def test_pattern_unset_means_no_block_and_no_process_read(sb, monkeypatch):
    """Off is the shipped state. The counter is what sees the guard: without it the block would
    still be empty for an unmatched name, and the `ps` would still have run."""
    calls = []
    monkeypatch.setattr(sb["mod"], "live_sessions", lambda: calls.append(1) or [])
    assert sb["mod"].facts_lines(worktrees="- Worktrees …", now=NOW, root=sb["root"]) == []
    assert calls == []
    sb["configure"](session_name_pattern=PATTERN)
    sb["mod"].facts_lines(now=NOW, root=sb["root"])
    assert calls == [1]


def test_an_unmanaged_name_and_this_session_itself_are_not_listed(sb, monkeypatch):
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [say(LIMIT, NOW - 60)])
    assert block(sb, [sess("my-scratch-session")]) == []
    monkeypatch.setattr(sb["procs"], "claude_pid", lambda *a, **k: 4242)
    assert block(sb, [sess("s-builder-R1", pid=4242)]) == []
    assert block(sb, [sess("s-builder-R1", pid=4243)])[0].startswith("- Managed sessions (1 live")


def test_a_session_with_no_reply_yet_is_reported_and_not_called_stalled(sb):
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [])
    line = block(sb, [sess("s-builder-R1")])[1]
    assert "no reply yet" in line and "STALLED" not in line


# ------------------------------------------------------------------ finding the transcript

def test_only_a_TITLE_record_identifies_the_transcript(sb):
    """The seat's own transcript mentions the builder's name in messages it sends. Matching on the
    name anywhere would report the seat's words as the builder's."""
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], None, [
        {"type": "message", "recipient": "s-builder-R1"}, say("I am the seat.", NOW - 5)],
        fname="seat.jsonl")
    assert sb["mod"].find_transcript("s-builder-R1", NOW - 3600, sb["root"]) is None
    real = write_transcript(sb["root"], "s-builder-R1", [say("I am the builder.", NOW - 5)])
    assert sb["mod"].find_transcript("s-builder-R1", NOW - 3600, sb["root"]) == real
    line = block(sb, [sess("s-builder-R1")])[1]
    assert "I am the builder." in line


def test_a_name_that_is_a_PREFIX_of_another_does_not_match_it(sb):
    """Builder 1 and builder 10 run side by side in this fleet. A title test that was a substring
    test would hand builder 1 the newer of the two transcripts."""
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R10", [say("I am ten.", NOW - 5)], fname="ten.jsonl")
    assert sb["mod"].find_transcript("s-builder-R1", NOW - 3600, sb["root"]) is None


def test_a_transcript_older_than_the_process_is_not_this_session(sb):
    sb["configure"](session_name_pattern=PATTERN)
    p = write_transcript(sb["root"], "s-builder-R1", [say("Old run.", NOW - 86400)])
    os.utime(p, (NOW - 86400, NOW - 86400))
    assert "no transcript found" in block(sb, [sess("s-builder-R1", age=3600)])[1]


# ------------------------------------------------------------------ the worktree line

def test_the_worktree_line_is_folded_under_the_block(sb):
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [say("ok", NOW - 5)])
    out = block(sb, [sess("s-builder-R1")], worktrees="- Worktrees still open in this repo: X.")
    assert out[-1] == "  - Worktrees still open in this repo: X."


def test_without_a_block_the_worktree_line_still_prints_on_its_own(sb):
    """Session start used to print the worktree line by itself. Folding it under the block must
    not lose it for every installation that never set a pattern — which is nearly all of them."""
    wt = "- Worktrees still open in this repo: X."
    assert sb["mod"].block(wt, now=NOW, sessions=[], root=sb["root"]) == [wt]
    assert sb["mod"].block(None, now=NOW, sessions=[], root=sb["root"]) == []
    sb["configure"](session_name_pattern=PATTERN)
    write_transcript(sb["root"], "s-builder-R1", [say("ok", NOW - 5)])
    out = sb["mod"].block(wt, now=NOW, sessions=[sess("s-builder-R1")], root=sb["root"])
    assert out[0].startswith("- Managed sessions") and out[-1] == "  " + wt
    assert out.count("  " + wt) == 1 and wt not in out


# ------------------------------------------------------------------ the parsers

def test_etime_parses_every_ps_shape():
    import stallbrief                                                    # noqa: PLC0415
    assert stallbrief._etime_seconds("05") == 5
    assert stallbrief._etime_seconds("02:05") == 125
    assert stallbrief._etime_seconds("1:02:05") == 3725
    assert stallbrief._etime_seconds("2-01:02:05") == 2 * 86400 + 3725
    assert stallbrief._etime_seconds("x") is None


def test_live_sessions_reads_name_and_model_from_a_real_ps_shape(monkeypatch):
    import stallbrief, subprocess                                        # noqa: PLC0415
    table = ("  101  08:13:08 claude --model claude-opus-5 --effort medium --name a-builder-19 "
             "--remote-control Title\n"
             "  102     00:10 /bin/zsh -c claude --name not-claude-itself\n"
             "  103     00:20 claude --name=b-audit-X\n"
             "  104     00:30 claude\n")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, table, ""))
    got = stallbrief.live_sessions()
    assert [(s["pid"], s["name"], s["model"], s["age"]) for s in got] == [
        (101, "a-builder-19", "claude-opus-5", 8 * 3600 + 13 * 60 + 8),
        (103, "b-audit-X", None, 20)]
