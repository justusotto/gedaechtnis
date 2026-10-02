"""fleet.py + tools/sessions.py — launching, seeing and closing sessions (TERMOVERLOAD-1).

Every close rule gets a POSITIVE control (the fully-finished fixture is closeable) and a NEGATIVE
control (breaking exactly that one fact KEEPS the session, with that rule's reason). The owner's
bar is "without just killing sessions by mistake", so the direction that matters is the KEEP.

Nothing here reads the machine's real sessions, ledger or state: the state dir, limits and config
are redirected into tmp_path, and every process fact is injected.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HOOKS))


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "Atlas"))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path / "sessions"))
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    import fleet
    return fleet


NOW = 1_800_000_000.0


def end(**over):
    """A turn end as `closerules.scan` snapshots it."""
    e = {"ts": NOW - 4 * 3600, "text": "Merged, row marked. SESSION COMPLETE.", "open_bg": [],
         "wake_until": 0.0, "cron": 0, "writes": {}, "reports": [], "humans": 1}
    e.update(over)
    return e


def finished(**over):
    f = {"is_self": False, "alive": True, "handoff": True, "status": "idle", "scanned": True,
         "end": end(), "screen": "❯ \n  ? for shortcuts", "attached": False, "unsaved": [],
         "outside": False, "mux": "screen", "exempt": False}
    f.update(over)
    return f


# ------------------------------------------------------------------ classify: the close rules ----

def test_positive_control_a_finished_session_is_closeable(iso):
    assert iso.classify(finished(), NOW, 15) == []


# CLOSETUNE-1: idle 180 min; the fixture's last turn ended 4 h ago — finished by time, and younger
# than the 12 h line, so an UNKNOWN (soft) fact still keeps it.
@pytest.mark.parametrize("over, rule", [
    ({"is_self": True}, "launched"),
    ({"alive": False}, "launched"),
    ({"end": end(text="done", ts=NOW - 60)}, "finished"),
    ({"end": end(text="done", ts=None)}, "finished"),
    ({"scanned": False}, "transcript"),
    ({"end": None}, "idle"),
    ({"status": "busy"}, "idle"),
    ({"status": "shell"}, "idle"),
    ({"screen": None}, "no-prompt"),
    ({"screen": "Do you want to proceed?\n❯ 1. Yes\n  2. No"}, "no-prompt"),
    ({"unsaved": None}, "no-unsaved-work"),
    ({"unsaved": ["gedaechtnis/hooks/x.py"]}, "no-unsaved-work"),
    ({"end": end(open_bg=["b1amd7l4j"])}, "background"),
    ({"end": end(open_bg=["b1"], open_bg_ts={"b1": NOW - 600})}, "background"),
    ({"end": end(wake_until=NOW + 600)}, "background"),
    ({"end": end(cron=1)}, "background"),
    ({"end": end(humans=2)}, "conversation"),
    ({"attached": True}, "attached"),
    ({"outside": True, "mux": None}, "window"),
    ({"exempt": True}, "exempt"),
])
def test_each_broken_fact_keeps_the_session_and_names_its_rule(iso, over, rule):
    keep = iso.classify(finished(**over), NOW, 180)
    assert keep, over
    assert any(k.startswith(rule + ":") for k in keep), keep


@pytest.mark.parametrize("text", [
    "HOLDING — waiting on: the seat",
    "Handoff drafted. Waiting for the two suite runs.",
    "Merged. Shall I also remove the worktree?",
    "Done. Over to you for the push.",
    "Suite green on the tip.",
])
def test_last_words_never_keep_a_session_finished_by_time_they_are_a_note(iso, text):
    """CLOSETUNE-1: FINISHED is a fact about time. The ask/wait heuristic kept 26 of 81 sessions
    on 2026-09-30; it is printed as a note now, never obeyed."""
    f = finished(end=end(text=text), handoff=False)
    assert iso.classify(f, NOW, 180) == []
    if iso.closerules.asks_or_waits(text):
        assert any(n.startswith("its last words") for n in iso.notes(f, NOW, 180))


def test_silence_alone_never_closes(iso):
    """A turn that has NOT ended keeps the session however long ago its last record was."""
    old = finished(end=None, status=None)
    assert iso.classify(old, NOW, 15)


def test_absent_registry_status_is_not_a_keep_on_its_own(iso):
    """A session with no registry record is judged by its transcript; `None` is not 'busy'."""
    assert iso.classify(finished(status=None), NOW, 15) == []


# ------------------------------------------------------------------ duplicate guard and cap ----

def alive_all(pid, start):
    return True


def alive_none(pid, start):
    return False


ROWS = [{"name": "b-1", "row": "q:CU-1", "worktree": "/r/.claude/worktrees/ROW1", "pid": 11,
         "pid_start": "Sat Sep 26 20:00:00 2026"}]


@pytest.mark.parametrize("kw, word", [
    ({"name": "b-1", "row": None, "worktree": None}, "already running"),
    ({"name": "b-2", "row": "q:CU-1", "worktree": None}, "row q:CU-1"),
    ({"name": "b-3", "row": None, "worktree": "/r/.claude/worktrees/ROW1"}, "worktree"),
])
def test_a_second_live_session_for_the_same_row_worktree_or_name_is_refused(iso, kw, word):
    why = iso.duplicate(ROWS, kw["name"], kw["row"], kw["worktree"], set(), alive=alive_all)
    assert why and word in why


def test_a_dead_or_recycled_pid_does_not_block_a_relaunch(iso):
    assert iso.duplicate(ROWS, "b-1", "q:CU-1", "/r/.claude/worktrees/ROW1", set(),
                         alive=alive_none) is None


def test_a_different_row_and_worktree_launch_passes(iso):
    assert iso.duplicate(ROWS, "b-9", "q:CU-9", "/r/.claude/worktrees/ROW9", set(),
                         alive=alive_all) is None


def test_a_live_process_with_the_name_refuses_even_outside_the_ledger(iso):
    assert iso.duplicate([], "hand-launched", None, None, {"hand-launched"}, alive=alive_all)


def test_the_latest_ledger_row_per_name_is_the_one_judged(iso):
    rows = [dict(ROWS[0], row="q:OLD"), dict(ROWS[0], row="q:NEW")]
    assert iso.duplicate(rows, "x", "q:OLD", None, set(), alive=alive_all) is None
    assert iso.duplicate(rows, "x", "q:NEW", None, set(), alive=alive_all)


def test_cap_refuses_at_and_above_the_number_only(iso):
    assert iso.over_cap(24, 24)
    assert iso.over_cap(25, 24)
    assert iso.over_cap(23, 24) is None


def test_cap_unset_or_unreadable_count_never_refuses(iso):
    assert iso.over_cap(500, 0) is None
    assert iso.over_cap(None, 5) is None


def test_same_process_needs_the_recorded_start_time(iso):
    me = os.getpid()
    assert iso.same_process(me, iso.pid_start(me))
    assert not iso.same_process(me, "Thu Jan  1 00:00:00 1970")
    assert not iso.same_process(me, None)


# ------------------------------------------------------------------ environment and screens ----

def test_clean_env_removes_every_nested_session_marker_and_keeps_the_rest(iso):
    env = {k: "x" for k in iso.SCRUB_VARS}
    env.update(PATH="/usr/bin", HOME="/h")
    out = iso.clean_env(env)
    assert not set(iso.SCRUB_VARS) & set(out)
    assert out["PATH"] == "/usr/bin" and out["HOME"] == "/h"


def test_the_child_session_marker_is_on_the_scrub_list(iso):
    assert "CLAUDE_CODE_CHILD_SESSION" in iso.SCRUB_VARS
    assert "CLAUDE_PID" in iso.SCRUB_VARS


def test_windows_gets_no_multiplexer(iso, monkeypatch):
    monkeypatch.delenv("GEDAECHTNIS_MUX", raising=False)
    monkeypatch.setenv("GEDAECHTNIS_PLATFORM", "windows")
    assert iso.multiplexer() is None


def test_tmux_is_used_where_screen_is_absent(iso, monkeypatch):
    import common
    monkeypatch.delenv("GEDAECHTNIS_MUX", raising=False)
    monkeypatch.setenv("GEDAECHTNIS_PLATFORM", "linux")
    monkeypatch.setattr(common, "_have", lambda p: p == "tmux")
    assert iso.multiplexer() == "tmux"
    monkeypatch.setattr(common, "_have", lambda p: p in ("tmux", "screen"))
    assert iso.multiplexer() == "screen"


def test_the_prompt_pattern_sees_the_dialog_and_not_an_idle_input_box(iso):
    assert iso.PROMPT_TEXT.search(" Do you want to make this edit to x.py?\n ❯ 1. Yes")
    assert iso.PROMPT_TEXT.search("Yes, I trust this folder\n Enter to confirm · Esc to cancel")
    assert not iso.PROMPT_TEXT.search("> \n  ? for shortcuts        ◯ high · /effort")


# ------------------------------------------------------------------------- transcript facts ----

def _t(path, recs):
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return path


def test_last_turn_ended_only_on_an_end_turn_assistant_record(iso, tmp_path):
    ended = _t(tmp_path / "a.jsonl", [
        {"type": "user", "timestamp": "2026-09-26T20:00:00Z", "message": {"content": "go"}},
        {"type": "assistant", "timestamp": "2026-09-26T20:01:00Z",
         "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]}}])
    assert iso.last_turn(ended)["state"] == "ended"
    assert iso.last_turn(ended)["text"] == "Done."
    pending = _t(tmp_path / "b.jsonl", [
        {"type": "assistant", "timestamp": "2026-09-26T20:01:00Z",
         "message": {"stop_reason": "tool_use", "content": [{"type": "tool_use", "name": "Bash"}]}}])
    assert iso.last_turn(pending)["state"] == "pending"
    answered = _t(tmp_path / "c.jsonl", [
        {"type": "assistant", "timestamp": "2026-09-26T20:01:00Z",
         "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]}},
        {"type": "user", "timestamp": "2026-09-26T20:02:00Z", "message": {"content": "one more"}}])
    assert iso.last_turn(answered)["state"] == "pending"


def test_a_sidechain_record_is_not_the_sessions_turn(iso, tmp_path):
    t = _t(tmp_path / "d.jsonl", [
        {"type": "assistant", "timestamp": "2026-09-26T20:01:00Z",
         "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]}},
        {"type": "assistant", "isSidechain": True, "timestamp": "2026-09-26T20:02:00Z",
         "message": {"stop_reason": "tool_use", "content": []}}])
    assert iso.last_turn(t)["state"] == "ended"


def test_no_transcript_is_none_never_idle(iso):
    assert iso.last_turn(None) is None


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def test_unsaved_work_in_a_declared_worktree_counts_whatever_its_age(iso, tmp_path):
    repo = tmp_path / "wt"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "a.txt").write_text("a")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-qm", "a", cwd=repo)
    assert iso.unsaved_work(None, str(repo), time.time()) == []
    (repo / "a.txt").write_text("changed")
    os.utime(repo / "a.txt", (1, 1))
    assert iso.unsaved_work(None, str(repo), time.time()) == ["a.txt"]


def test_a_shared_checkout_counts_only_dirt_newer_than_the_launch(iso, tmp_path):
    repo = tmp_path / "main"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "old.txt").write_text("someone else's")
    os.utime(repo / "old.txt", (1, 1))
    launched = time.time() - 5
    assert iso.unsaved_work(str(repo), None, launched) == []
    (repo / "mine.txt").write_text("this session's")
    assert iso.unsaved_work(str(repo), None, launched) == ["mine.txt"]


def test_git_that_cannot_answer_is_none_not_clean(iso, tmp_path):
    assert iso.unsaved_work(str(tmp_path / "not-a-repo"), None, 0) is None


def test_handoff_must_be_written_after_the_launch(iso, tmp_path):
    h = tmp_path / "HANDOFF-1.md"
    h.write_text("x")
    os.utime(h, (1000, 1000))
    assert not iso.handoff_written(str(tmp_path / "HANDOFF-*.md"), 2000)
    assert iso.handoff_written(str(tmp_path / "HANDOFF-*.md"), 500)
    assert not iso.handoff_written(None, 0)


# ------------------------------------------------------------------------------------ sweep ----

def _ledger(iso, *rows):
    for r in rows:
        iso.append_launch(r)


SAFE = lambda r: ([], 9001)                                  # noqa: E731 — every safety fact holds


def test_sweep_proposes_and_closes_nothing_on_a_dry_run(iso, monkeypatch):
    monkeypatch.setattr(iso, "close_one", lambda *a: pytest.fail("closed on a dry run"))
    _ledger(iso, {"name": "done-1", "pid": 4242, "pid_start": "s", "launched_at": NOW - 7200})
    items = iso.sweep(apply=False, now=NOW, gather_fn=lambda r, m, me: finished(), refusals_fn=SAFE)
    assert items[0]["keep"] == [] and items[0]["refused"] == [] and not items[0]["closed"]
    assert "proposed\tdone-1" in (iso.config.state() / "session-close.log").read_text()


def test_sweep_with_apply_closes_the_recorded_session_after_re_reading(iso, monkeypatch):
    closed = []
    monkeypatch.setattr(iso, "close_one",
                        lambda r, mux, anchor: closed.append((r["pid"], anchor)) or "screen quit")
    _ledger(iso, {"name": "done-1", "pid": 4242, "pid_start": "s", "launched_at": NOW - 7200})
    items = iso.sweep(apply=True, now=NOW, gather_fn=lambda r, m, me: finished(), refusals_fn=SAFE,
                      by="test")
    assert closed == [(4242, 9001)] and items[0]["closed"]
    log = (iso.config.state() / "close.log").read_text()
    assert "\tclosed\tdone-1\tpid 4242\tstart s\t" in log and log.rstrip().endswith("by test")


def test_sweep_never_closes_a_finished_session_a_safety_fact_refuses(iso, monkeypatch):
    monkeypatch.setattr(iso, "close_one", lambda *a: pytest.fail("closed a refused session"))
    _ledger(iso, {"name": "done-1", "pid": 4242, "pid_start": "s", "launched_at": NOW - 7200})
    items = iso.sweep(apply=True, now=NOW, gather_fn=lambda r, m, me: finished(),
                      refusals_fn=lambda r: (["attended: a person is at it"], None), by="test")
    assert items[0]["refused"] == ["attended: a person is at it"] and not items[0]["closed"]
    assert "\trefused\tdone-1\t" in (iso.config.state() / "close.log").read_text()


def test_sweep_logs_no_refusal_for_a_registry_only_session(iso, monkeypatch):
    """A session the launcher did not start is refused on EVERY pass by design; logging each one
    wrote a line per session per Stop pass. Ledger rows still log (the test above)."""
    monkeypatch.setattr(iso, "close_one", lambda *a: pytest.fail("closed an outside session"))
    row = {"name": "theirs-1", "pid": 4343, "pid_start": "s", "outside": True}
    items = iso.sweep(apply=True, now=NOW, gather_fn=lambda r, m, me: finished(), rows=[row],
                      refusals_fn=lambda r: (["ledger: not launched by `sessions.py launch`"], None),
                      by="test")
    assert items[0]["refused"] and not items[0]["closed"]
    log = iso.config.state() / "close.log"
    assert not log.exists() or "theirs-1" not in log.read_text()


def test_sweep_re_reads_the_safety_facts_before_closing(iso, monkeypatch):
    """Safe at the first read, attended at the second: kept."""
    monkeypatch.setattr(iso, "close_one", lambda *a: pytest.fail("closed on a stale read"))
    _ledger(iso, {"name": "done-1", "pid": 4242, "pid_start": "s", "launched_at": NOW - 7200})
    reads = iter([([], 1), (["attended: set"], None)])
    items = iso.sweep(apply=True, now=NOW, gather_fn=lambda r, m, me: finished(),
                      refusals_fn=lambda r: next(reads), by="test")
    assert items[0]["refused"] == ["attended: set"] and not items[0]["closed"]


R1 = {"name": "done-1", "pid": 4242, "pid_start": "s", "mux": "screen", "mux_session": "done-1"}


def test_close_quits_the_screen_and_reports_how(iso):
    calls = []
    how = iso.close_one(R1, "screen", 77, quit_fn=lambda m, n: calls.append((m, n)),
                        alive_fn=lambda: False, wait_s=0.1)
    assert calls == [("screen", "done-1")] and how == "screen quit"


def test_close_sends_nothing_after_the_quit_and_reports_a_survivor(iso, monkeypatch):
    """HANDLINES-1: no `kill -9` fallback. A session that outlives the quit is reported, and stays."""
    monkeypatch.setattr(iso.os, "kill", lambda *a: pytest.fail("a signal was sent"))
    assert iso.close_one(R1, "screen", 77, quit_fn=lambda m, n: None, alive_fn=lambda: True,
                         wait_s=0.1) is None


def test_the_quit_names_the_exact_screen_by_its_pid(iso, monkeypatch):
    runs = []
    monkeypatch.setattr(iso.subprocess, "run", lambda argv, **k: runs.append(argv))
    iso.mux_quit_exact("screen", "done-1", 5150)
    iso.mux_quit_exact("tmux", "done-1", None)
    assert runs == [["screen", "-S", "5150.done-1", "-X", "quit"],
                    ["tmux", "kill-session", "-t", "=done-1"]]


def test_swap_gate_refuses_at_its_share_and_never_on_an_unreadable_reading(iso):
    assert iso.over_swap(0.60, 0.6)
    assert iso.over_swap(0.59, 0.6) is None
    assert iso.over_swap(None, 0.6) is None
    assert iso.over_swap(0.99, 0.0) is None


def test_swap_share_reads_this_machine_or_says_unknown(iso):
    v = iso.swap_share()
    assert v is None or 0.0 <= v <= 1.0


def test_sweep_keeps_a_session_that_changed_between_the_two_reads(iso, monkeypatch):
    monkeypatch.setattr(iso, "close_one", lambda *a: pytest.fail("closed a changed session"))
    _ledger(iso, {"name": "late-1", "pid": 4343, "pid_start": "s", "launched_at": NOW - 7200})
    reads = iter([finished(), finished(end=None)])
    items = iso.sweep(apply=True, now=NOW, gather_fn=lambda r, m, me: next(reads), refusals_fn=SAFE)
    assert not items[0]["closed"]
    assert items[0]["keep"][0].startswith("changed during the pass")


def test_sweep_never_closes_a_session_with_one_keep_reason(iso, monkeypatch):
    monkeypatch.setattr(iso, "close_one", lambda *a: pytest.fail("closed a kept session"))
    _ledger(iso, {"name": "busy-1", "pid": 4444, "pid_start": "s", "launched_at": NOW - 7200})
    iso.sweep(apply=True, now=NOW, gather_fn=lambda r, m, me: finished(unsaved=["x"]),
              refusals_fn=SAFE)


def test_the_rate_limit_lets_one_pass_through_per_interval(iso):
    assert iso.due(NOW) is True
    assert iso.due(NOW + 10) is False
    assert iso.due(NOW + 301) is True


def test_session_close_ships_on_and_the_config_switches_it_off(iso, tmp_path, monkeypatch):
    """HANDLINES-1: on as shipped (the owner's yes of 2026-09-29); `false` in config.json is the off-switch."""
    import limits
    assert iso.applies() is True
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"limits": {"session_close_apply": False}}))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    assert iso.applies() is False


# --------------------------------------------------------------------------- parked messages ----

def test_a_parked_message_is_shown_to_the_recipients_name_only(iso):
    p = iso.park("builder-7", "cursus-atlas", "too little headroom", "please do row 5")
    assert p and p.exists()
    line = iso.parked_line("builder-7")
    assert "1 message(s) to builder-7" in line and "cursus-atlas" in line
    assert iso.parked_line("builder-8") is None
    assert iso.parked_line(None) is None


def test_parked_rows_carry_their_own_schema_and_a_bounded_text(iso):
    iso.park("b", "s", "r", "x" * 10_000)
    rows = iso.parked("b")
    assert rows[0]["schema"] == iso.PARKED_SCHEMA
    assert len(rows[0]["text"]) == iso.PARKED_TEXT_CHARS


def test_a_foreign_schema_row_in_the_file_is_not_read_as_parked(iso):
    iso.config.state().mkdir(parents=True, exist_ok=True)
    iso.parked_path("b").write_text(json.dumps({"schema": 1, "text": "mailbox row"}) + "\n")
    assert iso.parked("b") == []


# ------------------------------------------------------------------------------ the CLI ----

def _cli(tmp_path, *args, extra_env=None):
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"),
               GEDAECHTNIS_VAULT=str(tmp_path / "Atlas"),
               CLAUDE_PROJECTS_DIR=str(tmp_path / "projects"),
               CLAUDE_SESSIONS_DIR=str(tmp_path / "sessions"))
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(TOOLS / "sessions.py"), *args], env=env,
                          capture_output=True, text=True, timeout=60, cwd=str(tmp_path))


def test_launch_refuses_a_launcher_without_exec_claude(tmp_path):
    l = tmp_path / "l.sh"
    l.write_text("#!/bin/zsh\nclaude --name x\n")
    p = _cli(tmp_path, "launch", "--name", "x", "--cwd", str(tmp_path), str(l))
    assert p.returncode == 2 and "exec claude" in p.stdout


def test_launch_refuses_where_there_is_no_multiplexer(tmp_path):
    l = tmp_path / "l.sh"
    l.write_text("#!/bin/zsh\nexec claude --name x\n")
    p = _cli(tmp_path, "launch", "--name", "x", "--cwd", str(tmp_path), str(l),
             extra_env={"GEDAECHTNIS_MUX": "none"})
    assert p.returncode == 4 and "neither screen nor tmux" in p.stdout


def test_launch_refuses_a_duplicate_name_recorded_live(tmp_path):
    l = tmp_path / "l.sh"
    l.write_text("#!/bin/zsh\nexec claude --name x\n")
    (tmp_path / "state").mkdir()
    me = os.getpid()
    start = subprocess.run(["ps", "-o", "lstart=", "-p", str(me)], capture_output=True,
                           text=True).stdout
    (tmp_path / "state" / "launches.jsonl").write_text(json.dumps(
        {"name": "x", "pid": me, "pid_start": " ".join(start.split())}) + "\n")
    p = _cli(tmp_path, "launch", "--name", "x", "--cwd", str(tmp_path), str(l),
             extra_env={"GEDAECHTNIS_MUX": "screen"})
    assert p.returncode == 3 and "duplicate" in p.stdout


def test_close_proposes_only_while_the_switch_is_off(tmp_path):
    cfg = tmp_path / "off.json"
    cfg.write_text(json.dumps({"limits": {"session_close_apply": False}}))
    p = _cli(tmp_path, "close", extra_env={"GEDAECHTNIS_CONFIG": str(cfg)})
    assert p.returncode == 0 and "session_close_apply is off" in p.stdout


def test_close_dry_run_says_so_and_closes_nothing(tmp_path):
    p = _cli(tmp_path, "close", "--dry-run")
    assert p.returncode == 0 and "dry run: nothing is closed." in p.stdout
    assert not (tmp_path / "state" / "close.log").exists()


# ------------------------------------------------------------------------- the launch door ----

def _gate(tmp_path, cmd, door=None):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"limits": {"session_launch_door": door}} if door else {}))
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"), GEDAECHTNIS_CONFIG=str(cfg),
               GEDAECHTNIS_VAULT=str(tmp_path / "Atlas"))
    env.pop("GEDAECHTNIS_LIMITS", None)
    payload = {"session_id": "s1", "tool_name": "Bash", "cwd": str(tmp_path),
               "tool_input": {"command": cmd}}
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "gate.py"), "bash"],
                       input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    return (json.loads(p.stdout).get("hookSpecificOutput") or {}) if p.stdout.strip() else {}


@pytest.mark.parametrize("cmd", ["screen -dmS b42 zsh /x/launch.sh", "open -a Terminal /x/l.sh",
                                 "tmux new-session -d -s x /x/l.sh"])
def test_the_launch_door_warns_on_a_launch_outside_the_rule(tmp_path, cmd):
    out = _gate(tmp_path, cmd)
    assert "permissionDecision" not in out
    assert "launch door" in out.get("additionalContext", ""), out


@pytest.mark.parametrize("cmd", ["env -u CLAUDE_CODE_CHILD_SESSION -u CLAUDECODE screen -dmS b zsh l.sh",
                                 "open -a Terminal ~/notes.txt", 'open -a "Terminal" -n --args foo',
                                 "open -a Terminal .",
                                 "python3 gedaechtnis/tools/sessions.py launch --name x --cwd . l.sh",
                                 "screen -ls", "screen -r builder42", "tmux ls",
                                 "screen -S x -p 0 -X hardcopy -h out.txt", "grep screen notes.txt"])
def test_the_launch_door_is_silent_on_the_rule_and_on_reads(tmp_path, cmd):
    assert "launch door" not in _gate(tmp_path, cmd).get("additionalContext", "")


def test_the_launch_door_refuses_only_when_the_owner_sets_deny(tmp_path):
    out = _gate(tmp_path, "screen -dmS b42 zsh /x/launch.sh", door="deny")
    assert out.get("permissionDecision") == "deny"
    assert "CLAUDE_CODE_CHILD_SESSION" in out["permissionDecisionReason"]


def test_the_launch_door_off_says_nothing(tmp_path):
    assert "launch door" not in _gate(tmp_path, "screen -dmS b zsh l.sh", door="off").get(
        "additionalContext", "")


def test_hooks_json_runs_the_close_sweep_at_stop():
    doc = json.loads((HOOKS / "hooks.json").read_text())
    cmds = [h["command"] for grp in doc["hooks"]["Stop"] for h in grp["hooks"]]
    assert any(c.endswith('chore.py" sessclose') for c in cmds), cmds


def test_duplicate_uses_the_real_pid_and_start_time_by_default(iso):
    me = os.getpid()
    live = [{"name": "me", "row": "q:ME", "pid": me, "pid_start": iso.pid_start(me)}]
    assert iso.duplicate(live, "other", "q:ME", None, set())                  # really alive
    stale = [dict(live[0], pid_start="Thu Jan  1 00:00:00 1970")]
    assert iso.duplicate(stale, "other", "q:ME", None, set()) is None          # recycled pid


def test_a_rename_in_a_worktree_reports_the_new_path_not_a_garbled_old_one(iso, tmp_path):
    repo = tmp_path / "wt"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "old name.txt").write_text("a")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "a", cwd=repo)
    _git("mv", "old name.txt", "new name.txt", cwd=repo)
    assert iso.unsaved_work(None, str(repo), 0) == ["new name.txt"]


def test_a_pass_past_its_budget_defers_the_rest_and_closes_nothing_more(iso):
    _ledger(iso, {"name": "a", "pid": 1, "pid_start": "s"}, {"name": "b", "pid": 2, "pid_start": "s"})
    items = iso.sweep(apply=False, now=NOW, gather_fn=lambda r, m, me: finished(), budget_s=-1)
    assert items == []
    assert "deferred\ta" in (iso.config.state() / "session-close.log").read_text()


@pytest.mark.parametrize("cmd", ["open -a Terminal /x/launch-builder-42.sh",
                                 "osascript -e 'tell application \"Terminal\" to do script \"claude --name x\"'"])
def test_the_terminal_matcher_still_sees_a_session_launch(iso, cmd):
    assert iso.launch_outside_rule(cmd)
