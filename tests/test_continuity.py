"""CONTINUITY-1 — auto-continue after a usage reset, and the 80 % hand-off-line warning.

Every behaviour has a POSITIVE control (it acts) and a NEGATIVE control (it stays silent):
  * a limit-cut session whose window has reset is found and typed into; a FINISHED one, one still
    waiting for its reset, a dead one and one with a dialog open are not;
  * the resume check still refuses a cold, large session — and nothing is parked for it;
  * a session at 80 % of its line is told once; one at 79 % gets nothing.

The registry, projects directory, state directory and config are redirected into tmp_path, the
screen is a fake (`read_screen`, `typer` are injected): nothing here types into a real window or
reads a real transcript.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _clock(epoch):
    """`epoch` as the limit text prints a clock time, in UTC: "5:10pm"."""
    d = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return f"{d.hour % 12 or 12}:{d.minute:02d}{'pm' if d.hour >= 12 else 'am'}"


def usage_rec(mid, total, epoch, model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": _iso(epoch),
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": 2, "cache_read_input_tokens": total - 1002,
                                  "cache_creation_input_tokens": 1000,
                                  "cache_creation": {"ephemeral_1h_input_tokens": 1000}}}}


def limit_rec(epoch, text):
    return {"type": "assistant", "timestamp": _iso(epoch), "error": "rate_limit",
            "isApiErrorMessage": True, "uuid": f"u-{epoch}",
            "message": {"id": f"lim-{epoch}", "model": "<synthetic>", "role": "assistant",
                        "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": 0, "output_tokens": 0}}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    state, projects, sessions = tmp_path / "state", tmp_path / "projects", tmp_path / "sessions"
    (projects / "-proj").mkdir(parents=True)
    sessions.mkdir()
    state.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"limits": {"usage_continue": True}}))
    for k, v in dict(GEDAECHTNIS_STATE_DIR=state, GEDAECHTNIS_CONFIG=cfg,
                     GEDAECHTNIS_VAULT=tmp_path / "Vault", CLAUDE_PROJECTS_DIR=projects,
                     CLAUDE_SESSIONS_DIR=sessions).items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.delenv("GEDAECHTNIS_LIMITS", raising=False)
    monkeypatch.delenv("GEDAECHTNIS_PROFILE", raising=False)
    import limits
    limits._reset_for_tests()
    yield dict(tmp=tmp_path, state=state, projects=projects, sessions=sessions, cfg=cfg)
    limits._reset_for_tests()


# Live pids the registry can name: resume_gate treats a record as a session only when its pid lives.
@pytest.fixture
def pids():
    procs = [subprocess.Popen(["sleep", "60"]) for _ in range(3)]
    yield [os.getpid()] + [p.pid for p in procs]
    for p in procs:
        p.terminate()


def add_session(w, pid, name, records, status="idle"):
    sid = f"{pid:08d}-0000-0000-0000-{name[-12:].rjust(12, '0')}"
    (w["sessions"] / f"{pid}.json").write_text(json.dumps(
        {"pid": pid, "sessionId": sid, "name": name, "status": status, "kind": "interactive"}))
    (w["projects"] / "-proj" / f"{sid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n")
    with (w["state"] / "launches.jsonl").open("a") as fh:
        fh.write(json.dumps({"name": name, "pid": pid, "pid_start": "x", "mux": "screen",
                             "mux_session": name}) + "\n")
    return sid


def cut(total, *, reset_in_s, now=None, at_ago=3 * 3600):
    """A transcript that worked to `total` tokens and was then cut, resetting `reset_in_s` from now."""
    now = time.time() if now is None else now
    at = now - at_ago
    text = f"You've hit your session limit · resets {_clock(now + reset_in_s)} (UTC)"
    return [{"type": "user", "timestamp": _iso(at - 60), "message": {"content": "go"}},
            usage_rec("m1", total, at - 30), limit_rec(at, text)]


def passes(**kw):
    import autocontinue
    typed = []
    screen = kw.pop("screen", "❯ \n")
    ok = kw.pop("type_ok", True)
    out = autocontinue.run_pass(alive=kw.pop("alive", lambda p, s: True),
                                locate=kw.pop("locate", lambda row: ("screen", f"1.{row['name']}")),
                                read_screen=lambda mux, target: screen,
                                typer=lambda mux, target, text: typed.append(
                                    (target.split(".", 1)[1], text)) or ok,
                                **kw)
    return {i["name"]: i for i in out}, typed


# ------------------------------------------------------------------- auto-continue ----

def test_cut_session_after_reset_is_found_and_typed_into_once(world, pids):
    add_session(world, pids[1], "builder-cut", cut(50_000, reset_in_s=-1800))
    got, typed = passes(apply=True)
    assert got["builder-cut"]["outcome"] == "sent", got
    assert typed and typed[0][0] == "builder-cut" and typed[0][1].startswith("continue")
    again, typed2 = passes(apply=True)                       # the same limit record: never twice
    assert again["builder-cut"]["outcome"] == "already-sent" and typed2 == []
    assert "sent\tbuilder-cut" in (world["state"] / "usage_continue.log").read_text()


def test_finished_session_is_not_found(world, pids):
    now = time.time()
    add_session(world, pids[1], "builder-done",
                [{"type": "user", "timestamp": _iso(now - 99), "message": {"content": "go"}},
                 usage_rec("m1", 50_000, now - 90)])
    # a session that was cut and then MOVED ON (a later prompt) is not cut any more
    add_session(world, pids[2], "builder-moved", cut(50_000, reset_in_s=-1800)
                + [{"type": "user", "timestamp": _iso(now - 10), "message": {"content": "continue"}}])
    got, typed = passes(apply=True)
    assert got == {} and typed == []


def test_waits_for_the_reset(world, pids):
    add_session(world, pids[1], "builder-early", cut(50_000, reset_in_s=1800, at_ago=600))
    got, typed = passes(apply=True)
    assert got["builder-early"]["outcome"] == "waiting" and typed == []


def test_resume_check_refuses_cold_and_large_and_parks_nothing(world, pids):
    add_session(world, pids[1], "builder-big", cut(300_000, reset_in_s=-1800))
    got, typed = passes(apply=True)
    assert got["builder-big"]["outcome"] == "refused", got
    assert "resume_cold_cap" in got["builder-big"]["why"]
    assert typed == []
    assert not list(world["state"].glob("parked-*")), "a refused auto-continue must not be parked"


def test_dead_session_and_open_dialog_and_dry_run_type_nothing(world, pids):
    add_session(world, pids[1], "builder-dead", cut(50_000, reset_in_s=-1800))
    got, typed = passes(apply=True, alive=lambda p, s: False)
    assert got == {} and typed == []
    got, typed = passes(apply=True, screen="Do you want to proceed?\n❯ 1. Yes\n")
    assert got["builder-dead"]["outcome"] == "dialog" and typed == []
    got, typed = passes(apply=False)
    assert got["builder-dead"]["outcome"] == "would-send" and typed == []
    got, typed = passes(apply=True)                         # a dry run marks nothing as sent
    assert got["builder-dead"]["outcome"] == "sent"


def test_limit_text_without_a_reset_time_is_reported_not_woken(world, pids):
    now = time.time()
    add_session(world, pids[1], "builder-fable",
                [usage_rec("m1", 50_000, now - 4000),
                 limit_rec(now - 3600, "You've reached your Fable limit. Run /usage-credits to "
                                       "continue or switch models with /model.")])
    got, typed = passes(apply=True)
    assert got["builder-fable"]["outcome"] == "no-reset-time" and typed == []


def test_reset_parsing():
    import autocontinue as ac
    at = datetime(2026, 9, 29, 14, 46, tzinfo=timezone.utc).timestamp()
    assert ac.reset_at("5:10pm", "UTC", at) == datetime(2026, 9, 29, 17, 10, tzinfo=timezone.utc).timestamp()
    assert ac.reset_at("1am", "UTC", at) == datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc).timestamp()
    assert ac.reset_at("Oct 1 at 4am", "UTC", at) == datetime(2026, 10, 1, 4, tzinfo=timezone.utc).timestamp()
    late = datetime(2026, 12, 30, tzinfo=timezone.utc).timestamp()
    assert ac.reset_at("Jan 2 at 4am", "UTC", late) == datetime(2027, 1, 2, 4, tzinfo=timezone.utc).timestamp()
    assert ac.reset_at("soon", "UTC", at) is None and ac.reset_at("5pm", "Not/AZone", at) is None


def test_off_on_a_public_install_on_under_the_atlas_profile(world):
    import autocontinue, limits
    world["cfg"].write_text("{}")
    limits._reset_for_tests()
    assert autocontinue.enabled() is False
    world["cfg"].write_text(json.dumps({"profile": "atlas"}))
    limits._reset_for_tests()
    assert autocontinue.enabled() is True


def test_stop_arm_is_registered_and_does_nothing_when_off(world, pids):
    hooks = json.loads((HOOKS / "hooks.json").read_text())
    stop = [h["command"] for g in hooks["hooks"]["Stop"] for h in g["hooks"]]
    assert any(c.endswith("chore.py\" usagecontinue") for c in stop)
    add_session(world, pids[1], "builder-cut", cut(50_000, reset_in_s=-1800))
    world["cfg"].write_text("{}")                            # public: off
    p = subprocess.run([sys.executable, str(HOOKS / "chore.py"), "usagecontinue"],
                       input=json.dumps({"session_id": "s"}), capture_output=True, text=True,
                       env=dict(os.environ), timeout=60)
    assert p.returncode == 0 and p.stdout.strip() == ""
    assert not (world["state"] / "usage_continue.log").exists()
    assert not (world["state"] / "usage-continue.last").exists()


# ------------------------------------------------------------- the 80 % hand-off line ----

def _ctx_world(w, limits_over=None):
    w["cfg"].write_text(json.dumps({"limits": limits_over or {}}))
    env = dict(os.environ)
    env.pop("GEDAECHTNIS_HANDOFF_LINE", None)
    return env


def _transcript(w, sid, total, opener="build the thing"):
    now = time.time()
    t = w["projects"] / "-proj" / f"{sid}.jsonl"
    t.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "timestamp": _iso(now - 99), "message": {"content": opener}},
        usage_rec("m0", 100_000, now - 90), usage_rec("m1", total, now - 10)]) + "\n")
    return t


def _notice(env, sid, t):
    p = subprocess.run([sys.executable, str(HOOKS / "context_cap.py")],
                       input=json.dumps({"session_id": sid, "transcript_path": str(t),
                                         "hook_event_name": "PostToolUse", "tool_name": "Bash"}),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return ((json.loads(out) if out else {}).get("hookSpecificOutput") or {}).get("additionalContext") or ""


def test_line_from_env_fires_at_80_percent_once_and_not_at_79(world):
    env = _ctx_world(world)
    env["GEDAECHTNIS_HANDOFF_LINE"] = "280k"
    assert _notice(env, "s79", _transcript(world, "s79", 221_200)) == ""        # 79 %
    t = _transcript(world, "s80", 224_000)                                       # 80 %
    first = _notice(env, "s80", t)
    assert "80 % OF YOUR HAND-OFF LINE" in first and "224,000 of 280,000" in first, first
    assert "review and gates now" in first and "write the handoff" in first
    assert _notice(env, "s80", t) == ""                                          # once only


def test_line_from_the_opening_prompt(world):
    env = _ctx_world(world)
    opener = "Method: one review, one suite, hand off at 280k. Opus 5.5 medium."
    assert "your opening prompt" in _notice(env, "o1", _transcript(world, "o1", 230_000, opener))
    assert _notice(env, "o2", _transcript(world, "o2", 223_000, opener)) == ""


def test_no_declared_line_no_notice_and_config_line_works(world):
    env = _ctx_world(world)
    assert _notice(env, "n1", _transcript(world, "n1", 250_000)) == ""          # nothing declared
    env = _ctx_world(world, {"context_handoff_line_tokens": 300000})
    assert "context_handoff_line_tokens" in _notice(env, "n2", _transcript(world, "n2", 250_000))
    env = _ctx_world(world, {"context_handoff_line_tokens": 300000, "context_handoff_warn_share": 0.0})
    assert _notice(env, "n3", _transcript(world, "n3", 250_000)) == ""          # share 0 = off


# ------------------------------------------------- review round 1 (CONTINUITY-1 must-fixes) ----

def _reg(w, pid, sid, name, status="idle"):
    (w["sessions"] / f"{pid}.json").write_text(json.dumps(
        {"pid": pid, "sessionId": sid, "name": name, "status": status, "kind": "interactive"}))


def test_resume_check_must_name_this_session_or_nothing_is_typed(world, pids):
    # A cold 300k session: the resume check refuses it — unless the check measures something else.
    sid = add_session(world, pids[1], "builder-big", cut(300_000, reset_in_s=-1800))
    _reg(world, pids[2], "other-sid", "builder-big")                    # (A) a second live record
    got, typed = passes(apply=True)
    assert got["builder-big"]["outcome"] == "unchecked" and typed == []
    (world["sessions"] / f"{pids[2]}.json").unlink()
    _reg(world, pids[1], sid, "")                                       # (B) no name
    got, typed = passes(apply=True)
    assert got["builder-big"]["outcome"] == "unchecked" and typed == []
    small = add_session(world, pids[3], "small", cut(20_000, reset_in_s=-1800))
    _reg(world, pids[1], sid, "small")                                  # (C) another session's name
    got, typed = passes(apply=True)
    assert got["builder-big"]["outcome"] == "unchecked"
    assert [t for t in typed if t[0] == "builder-big"] == []


def test_reset_boundary_waits_out_the_grace_minute(world, pids):
    R = (int(time.time()) // 60) * 60 - 600                             # a whole minute, 10 min ago
    text = f"You've hit your session limit · resets {_clock(R)} (UTC)"
    add_session(world, pids[1], "builder-edge",
                [usage_rec("m1", 50_000, R - 7200), limit_rec(R - 7000, text)])
    got, typed = passes(apply=True, now=R + 30)
    assert got["builder-edge"]["outcome"] == "waiting" and typed == []
    got, typed = passes(apply=True, now=R + 61)
    assert got["builder-edge"]["outcome"] == "sent" and typed


def test_busy_session_is_skipped(world, pids):
    add_session(world, pids[1], "builder-busy", cut(50_000, reset_in_s=-1800), status="busy")
    got, typed = passes(apply=True)
    assert got["builder-busy"]["outcome"] == "busy" and typed == []


def test_only_a_rate_limit_record_with_the_limit_text_counts(world, pids):
    now = time.time()
    other = limit_rec(now - 3600, "You've hit your session limit · resets 1am (UTC)")
    other["error"] = "overloaded"
    add_session(world, pids[1], "builder-err", [usage_rec("m1", 50_000, now - 4000), other])
    wrong = limit_rec(now - 3600, "API Error: 429 rate_limit_error")
    add_session(world, pids[2], "builder-429", [usage_rec("m1", 50_000, now - 4000), wrong])
    got, typed = passes(apply=True)
    assert got == {} and typed == []


def test_a_subagent_record_after_the_limit_does_not_hide_it(world, pids):
    recs = cut(50_000, reset_in_s=-1800)
    recs.append({"type": "user", "isSidechain": True, "timestamp": _iso(time.time() - 60),
                 "message": {"content": "sub"}})
    add_session(world, pids[1], "builder-side", recs)
    got, _ = passes(apply=False)
    assert got["builder-side"]["outcome"] == "would-send"


def test_unreadable_window_no_window_and_failed_keys(world, pids):
    add_session(world, pids[1], "builder-w", cut(50_000, reset_in_s=-1800))
    got, typed = passes(apply=True, screen=None)
    assert got["builder-w"]["outcome"] == "unreadable" and typed == []
    got, typed = passes(apply=True, locate=lambda row: None)
    assert got["builder-w"]["outcome"] == "no-window" and typed == []
    got, typed = passes(apply=True, type_ok=False)
    assert got["builder-w"]["outcome"] == "send-failed"
    got, typed = passes(apply=True)                                      # not marked: tried again
    assert got["builder-w"]["outcome"] == "sent"


def test_dialog_only_counts_in_the_last_lines(world, pids):
    add_session(world, pids[1], "builder-d", cut(50_000, reset_in_s=-1800))
    old = "Do you want to proceed?\n" + "\n".join(f"line {i}" for i in range(40)) + "\n❯ \n"
    got, typed = passes(apply=True, screen=old)
    assert got["builder-d"]["outcome"] == "sent"


def test_window_is_pinned_to_the_process(world, monkeypatch):
    import autocontinue, fleet
    row = {"name": "b", "mux": "screen", "mux_session": "b", "pid": 4242}
    monkeypatch.setattr(fleet, "screen_of", lambda pid: "b")
    monkeypatch.setattr(fleet, "mux_session_pids", lambda mux, name: [77])
    assert autocontinue.window_of(row) == ("screen", "77.b")
    monkeypatch.setattr(fleet, "mux_session_pids", lambda mux, name: [77, 78])
    assert autocontinue.window_of(row) is None                          # two screens of that name
    monkeypatch.setattr(fleet, "mux_session_pids", lambda mux, name: [77])
    monkeypatch.setattr(fleet, "screen_of", lambda pid: "someone-else")
    assert autocontinue.window_of(row) is None                          # not this process's screen

    class P:
        returncode, stdout = 0, "%3 999\n%4 4242\n"
    seen = []
    monkeypatch.setattr(autocontinue.subprocess, "run", lambda argv, **k: seen.append(argv) or P())
    trow = dict(row, mux="tmux")
    assert autocontinue.window_of(trow) == ("tmux", "%4")
    assert "=b" in seen[-1]                                             # exact session name
    P.stdout = "%3 999\n"
    assert autocontinue.window_of(trow) is None


def test_rate_limit_and_budget_and_one_log_line_per_outcome(world, pids):
    import autocontinue, limits
    assert autocontinue.due(now=1000.0) is True
    assert autocontinue.due(now=1100.0) is False
    assert autocontinue.due(now=1400.0) is True
    add_session(world, pids[1], "builder-wait", cut(50_000, reset_in_s=1800, at_ago=600))
    passes(apply=True)
    passes(apply=True)
    log = (world["state"] / "usage_continue.log").read_text()
    assert log.count("waiting\tbuilder-wait") == 1, log
    got, typed = passes(apply=True, budget_s=-1)
    assert got == {} and typed == [] and "deferred" in (world["state"] / "usage_continue.log").read_text()


def test_reset_parsing_dst_late_stamp_and_text_forms():
    import autocontinue as ac
    fall = datetime(2026, 10, 25, 0, 10, tzinfo=timezone.utc).timestamp()      # 02:10 CEST
    assert ac.reset_at("2:30am", "Europe/Berlin", fall) == datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).timestamp()
    week = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    assert ac.reset_at("Oct 25 at 2:30am", "Europe/Berlin", week) == datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc).timestamp()
    late = datetime(2026, 9, 29, 17, 10, 20, tzinfo=timezone.utc).timestamp()
    assert ac.reset_at("5:10pm", "UTC", late) == datetime(2026, 9, 29, 17, 10, tzinfo=timezone.utc).timestamp()
    for text in ("You've hit your session limit ∙ resets 5pm (UTC)",
                 "You've hit your Opus weekly limit · resets Oct 1 at 4am (UTC) · /upgrade to keep going"):
        assert ac.LIMIT_RE.search(text), text


def test_line_threshold_rounds_up_and_opener_needs_a_unit(world):
    env = _ctx_world(world)
    env["GEDAECHTNIS_HANDOFF_LINE"] = "100001"
    assert _notice(env, "c1", _transcript(world, "c1", 80_000)) == ""             # 79.9992 %
    assert "OF YOUR HAND-OFF LINE" in _notice(env, "c2", _transcript(world, "c2", 80_001))
    env = _ctx_world(world)
    for i, opener in enumerate(("hand off at 15,000 lines of diff", "hand off at 20k",
                                "do not hand off at 200k please")):
        assert _notice(env, f"u{i}", _transcript(world, f"u{i}", 260_000, opener)) == "", opener
    msg = _notice(env, "u9", _transcript(world, "u9", 230_000, "do not hand off at 200k; hand off at 280k"))
    assert "of 280,000" in msg, msg


def test_line_still_fires_after_reach_but_never_after_cap(world):
    env = _ctx_world(world, {"resume_window": 300000, "resume_min_headroom": 0,
                             "context_reach_margin_tokens": 0})
    env["GEDAECHTNIS_HANDOFF_LINE"] = "400k"
    t = _transcript(world, "r1", 310_000)
    assert "REACH" in _notice(env, "r1", t)
    t = _transcript(world, "r1", 330_000)
    assert "OF YOUR HAND-OFF LINE" in _notice(env, "r1", t)
    env = _ctx_world(world, {"context_hard_cap_tokens": 330000})
    env["GEDAECHTNIS_HANDOFF_LINE"] = "400k"
    t = _transcript(world, "k1", 340_000)
    assert "HARD CAP" in _notice(env, "k1", t)
    assert _notice(env, "k1", t) == ""


# ------------------------------------------------- review round 2 (CONTINUITY-1 fix review) ----

def test_spring_gap_time_is_never_early():
    import autocontinue as ac
    gap_day = datetime(2026, 3, 28, 12, tzinfo=timezone.utc).timestamp()
    gap_start = datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc).timestamp()      # 02:00 CET → 03:00 CEST
    for when in ("2:30am", "Mar 29 at 2:30am"):
        got = ac.reset_at(when, "Europe/Berlin", gap_day)
        assert got >= gap_start, (when, got - gap_start)


def test_unmeasured_context_is_unchecked_not_typed(world, pids):
    recs = cut(300_000, reset_in_s=-1800)
    big = {"type": "user", "timestamp": recs[1]["timestamp"],
           "message": {"content": [{"type": "tool_result", "content": "x" * 700_000}]}}
    recs.insert(2, big)                         # the last usage record falls outside the tail read
    add_session(world, pids[1], "builder-blind", recs)
    got, typed = passes(apply=True)
    assert got["builder-blind"]["outcome"] == "unchecked" and typed == []


def test_compacted_refusal_parks_nothing_when_asked(world, pids):
    import resume_gate
    now = time.time()
    recs = [usage_rec("m1", 300_000, now - 7200, model="claude-fable-5-1"),
            {"type": "system", "subtype": "compact_boundary", "timestamp": _iso(now - 7100)}]
    add_session(world, pids[1], "fab", recs)
    v = resume_gate.decide({"session_id": "x", "tool_input": {"to": "fab", "message": "hi"}},
                           now, park=False)
    assert v and v[0] == "deny", v
    assert not list(world["state"].glob("parked-*"))
    v = resume_gate.decide({"session_id": "x", "tool_input": {"to": "fab", "message": "hi"}}, now)
    assert v and v[0] == "deny" and list(world["state"].glob("parked-*"))   # the default still parks
