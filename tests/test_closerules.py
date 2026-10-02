"""closerules.py + the SESSCLOSE-2 parts of fleet.py / tools/sessions.py.

Positive and negative controls for every transcript rule, the session-attributed unsaved work, the
out-of-ledger sessions, the working-session cap, the per-seat share, the memory fallback and the
replay. Synthetic transcripts only; the real-transcript controls live in the repo's own tests
(`tests/gedaechtnis/test_sessclose_real.py`). Processes are stubs this test starts itself.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HOOKS))
import closerules as cr  # noqa: E402

T0 = 1_800_000_000.0


def iso_ts(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def rec(kind, t, content, stop=None, **extra):
    r = {"type": kind, "timestamp": iso_ts(t), "message": {"content": content}}
    if stop:
        r["message"]["stop_reason"] = stop
    r.update(extra)
    return json.dumps(r)


def say(t, text):
    return rec("assistant", t, [{"type": "text", "text": text}], stop="end_turn")


def human(t, text="go"):
    return rec("user", t, text, origin={"kind": "human"})


def peer(t, text="from the seat"):
    return rec("user", t, "Another Claude session says: " + text, isMeta=True, origin={"kind": "peer"})


def use(t, uid, name, **inp):
    return rec("assistant", t, [{"type": "tool_use", "id": uid, "name": name, "input": inp}],
               stop="tool_use")


def result(t, uid, text="ok"):
    return rec("user", t, [{"type": "tool_result", "tool_use_id": uid, "content": text}])


def title(name):
    return json.dumps({"type": "custom-title", "customTitle": name})


def write_t(path: Path, *lines):
    path.write_text("\n".join(lines) + "\n")
    return path


# --------------------------------------------------------------------------------- words ----

@pytest.mark.parametrize("text", [
    "HOLDING — waiting on: the seat's commit id.",
    "Suite is at 18%. I'll wait for it to finish.",
    "Handoff written. Standing by for the merge window.",
    "Merged. Shall I remove the worktree?",
    "Done. Want me to open the page?",
    "Reported. The push is your call.",
    "Row done, but the reviewer is still running.",
    "Blocked: the classifier refused the write.",
    "All three arms are running in the background.",
])
def test_words_that_ask_or_wait_are_seen(text):
    assert cr.asks_or_waits(text)


@pytest.mark.parametrize("text", [
    "Merged 1a2b3c4d, row marked, worktree removed. SESSION COMPLETE.",
    "Handoff committed. Needs a decision: none. Nothing left to do.",
    "Landed on main; suite green.",
])
def test_final_words_are_not_read_as_asking(text):
    assert cr.asks_or_waits(text) is None
    assert cr.says_final(text)


def test_only_the_last_words_count():
    early = "Should I start? " + "x " * 600 + "Merged. SESSION COMPLETE."
    assert cr.asks_or_waits(early) is None


# ---------------------------------------------------------------------------------- scan ----

def test_scan_ends_a_turn_only_on_end_turn_with_no_open_call(tmp_path):
    t = write_t(tmp_path / "a.jsonl", title("b-1"), human(T0), use(T0 + 1, "u1", "Bash", command="ls"))
    s = cr.scan(t)
    assert s["name"] == "b-1" and s["last"] is None and s["ends"] == []
    t = write_t(tmp_path / "b.jsonl", title("b-1"), human(T0), use(T0 + 1, "u1", "Bash"),
                result(T0 + 2, "u1"), say(T0 + 3, "Merged. SESSION COMPLETE."))
    s = cr.scan(t)
    assert s["last"] and s["last"]["text"].endswith("SESSION COMPLETE.")


def test_a_message_after_the_end_makes_the_session_pending(tmp_path):
    t = write_t(tmp_path / "a.jsonl", human(T0), say(T0 + 1, "Done."), peer(T0 + 60))
    s = cr.scan(t)
    assert s["last"] is None and len(s["ends"]) == 1 and max(s["activity"]) == T0 + 60


def test_background_tasks_stay_open_until_their_notification(tmp_path):
    lines = [human(T0), use(T0 + 1, "u1", "Bash", command="pytest", run_in_background=True),
             result(T0 + 2, "u1", "Command running in background with ID: bx1. Output …"),
             say(T0 + 3, "Suite launched.")]
    s = cr.scan(write_t(tmp_path / "a.jsonl", *lines))
    assert s["last"]["open_bg"] == ["bx1"]
    lines += [rec("user", T0 + 99, "<task-notification>\n<task-id>bx1</task-id>\n<status>completed"
                  "</status></task-notification>", origin={"kind": "task-notification"}),
              say(T0 + 100, "Suite green. SESSION COMPLETE.")]
    s = cr.scan(write_t(tmp_path / "b.jsonl", *lines))
    assert s["last"]["open_bg"] == []


def test_agents_and_monitors_count_as_background(tmp_path):
    s = cr.scan(write_t(tmp_path / "a.jsonl", human(T0), use(T0 + 1, "a", "Agent"),
                        result(T0 + 2, "a", "Async agent launched successfully. agentId: ag1 (internal"),
                        use(T0 + 3, "m", "Monitor"),
                        result(T0 + 4, "m", "Monitor started (task mo1, expires in 30m"),
                        say(T0 + 5, "Done.")))
    assert s["last"]["open_bg"] == ["ag1", "mo1"]


def test_wakeups_crons_writes_reports_and_humans_are_recorded(tmp_path):
    s = cr.scan(write_t(tmp_path / "a.jsonl", human(T0), human(T0 + 1, "and also"),
                        use(T0 + 2, "w", "ScheduleWakeup", delaySeconds=900), result(T0 + 3, "w"),
                        use(T0 + 4, "c", "CronCreate"), result(T0 + 5, "c"),
                        use(T0 + 6, "f", "Write", file_path="/r/x/HANDOFF-2026-09-27-50-1.md"),
                        result(T0 + 7, "f"), use(T0 + 8, "g", "Edit", file_path="/r/y/code.py"),
                        result(T0 + 9, "g"), say(T0 + 10, "Done.")))
    e = s["last"]
    assert e["wake_until"] == T0 + 2 + 900 and e["cron"] == 1 and e["humans"] == 2
    assert set(e["writes"]) == {"/r/x/HANDOFF-2026-09-27-50-1.md", "/r/y/code.py"}
    assert e["reports"] == ["/r/x/HANDOFF-2026-09-27-50-1.md"]


def test_sidechain_records_are_not_the_sessions_turn(tmp_path):
    side = json.loads(say(T0 + 5, "Waiting on nothing."))
    side["isSidechain"] = True
    s = cr.scan(write_t(tmp_path / "a.jsonl", human(T0), say(T0 + 1, "Done."), json.dumps(side)))
    assert s["last"]["text"] == "Done."


# ------------------------------------------------------------------------------- verdict ----

def E(**over):
    e = {"ts": T0, "text": "Merged. SESSION COMPLETE.", "open_bg": [], "wake_until": 0.0,
         "cron": 0, "writes": {}, "reports": [], "humans": 1}
    e.update(over)
    return e


def test_verdict_positive_control():
    assert cr.verdict(E(), T0 + 181 * 60, 180) == []


@pytest.mark.parametrize("over, rule", [
    ({"text": "Merged. Waiting for the seat."}, "not-waiting"),
    ({"text": "Merged. OK to delete the branch?"}, "not-waiting"),
    ({"open_bg": ["b1"]}, "background"),
    ({"wake_until": T0 + 999 * 60}, "background"),
    ({"cron": 1}, "background"),
    ({"humans": 2}, "conversation"),
    ({"text": "Suite green on the tip."}, "finished"),
    ({"ts": None}, "idle"),
])
def test_verdict_each_rule_keeps(over, rule):
    keep = cr.verdict(E(**over), T0 + 181 * 60, 180)
    assert any(k.startswith(rule + ":") for k in keep), keep


def test_verdict_idle_boundary_and_evidence():
    assert cr.verdict(E(), T0 + 179 * 60, 180)
    assert cr.verdict(E(), T0 + 180 * 60, 180) == []
    plain = E(text="Suite green on the tip.")
    assert cr.verdict(plain, T0 + 999 * 60, 180)
    assert cr.verdict(plain, T0 + 999 * 60, 180, handoff=True) == []
    assert cr.verdict(E(text="Suite green.", reports=["/x/REPORT.md"]), T0 + 999 * 60, 180) == []


def test_a_pending_turn_is_kept_forever():
    assert cr.verdict(None, T0 + 10**9, 180)


# -------------------------------------------------------------------------------- replay ----

def test_replay_flags_a_session_used_after_its_close_moment(tmp_path):
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    write_t(root / "a.jsonl", title("w-1"), human(T0), say(T0 + 10, "Handoff committed. SESSION COMPLETE."),
            peer(T0 + 10 + 200 * 60, "one more thing"))
    write_t(root / "b.jsonl", title("r-1"), human(T0), say(T0 + 10, "Merged. SESSION COMPLETE."),
            peer(T0 + 60 * 60, "please also"), say(T0 + 61 * 60, "Merged. SESSION COMPLETE."))
    write_t(root / "c.jsonl", title("q-1"), human(T0), say(T0 + 10, "Merged. Should I push?"))
    files = cr.transcripts(tmp_path / "projects", 0)
    rows = {r["name"]: r for r in cr.replay(files, T0 + 10**6, T0 - 1, 180)}
    # CLOSETUNE-2 (2026-10-02): the replay runs the LIVE rule. A message from someone else after
    # the close is a reopen (`reused`), not a wrong close; last words are printed, not obeyed.
    assert rows["w-1"]["reused"] and not rows["w-1"]["wrong"]
    assert rows["w-1"]["after_kind"] == "a message from another session"
    assert rows["r-1"]["closed_at"] == T0 + 61 * 60 + 180 * 60 and not rows["r-1"]["wrong"]
    assert rows["q-1"]["closed_at"] == T0 + 10 + 180 * 60 and not rows["q-1"]["reused"]


def task_note(t, tid="bg1", status="completed"):
    return rec("user", t, f"<task-notification>\n<task-id>{tid}</task-id>\n<status>{status}</status>"
                          f"\n</task-notification>", origin={"kind": "task-notification"})


def test_replay_calls_a_turn_the_session_did_on_its_own_a_wrong_close(tmp_path):
    """Positive: a task of its own reports after the close moment. Negative: a peer's message."""
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    write_t(root / "a.jsonl", title("own-1"), human(T0), say(T0 + 10, "Done."),
            task_note(T0 + 10 + 200 * 60), say(T0 + 10 + 201 * 60, "The suite is green."))
    write_t(root / "b.jsonl", title("msg-1"), human(T0), say(T0 + 10, "Done."),
            peer(T0 + 10 + 200 * 60, "one more thing"))
    rows = {r["name"]: r for r in cr.replay(cr.transcripts(tmp_path / "projects", 0),
                                            T0 + 10**6, T0 - 1, 180)}
    assert rows["own-1"]["wrong"] and rows["own-1"]["after_kind"] == "a background task reported"
    assert rows["msg-1"]["reused"] and not rows["msg-1"]["wrong"]


def queued(t, content, **kw):
    return json.dumps({"type": "queue-operation", "operation": "enqueue", "timestamp": iso_ts(t),
                       "content": content, **kw})


NOTE = "<task-notification>\n<task-id>bg1</task-id>\n<status>completed</status>\n</task-notification>"
LATER = T0 + 10 + 200 * 60


@pytest.mark.parametrize("after, kind", [
    # the shape an idle session's own task arrives in first: a queued notification
    (queued(LATER, NOTE), "a background task reported"),
    (queued(LATER, "report", origin={"kind": "task-notification"}), "a background task reported"),
    (say(LATER, "The suite is green."), "it wrote a reply"),
    # a record the replay does not know is never read as somebody else's act
    (rec("user", LATER, "hook says", isMeta=True), "a system/meta message"),
])
def test_replay_counts_everything_but_someone_elses_act_as_a_wrong_close(tmp_path, after, kind):
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."), after)
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["wrong"] and not row["reused"] and row["after_kind"] == kind, row


@pytest.mark.parametrize("after", [human(LATER, "one more"), peer(LATER), queued(LATER, "typed while busy")])
def test_negative_someone_elses_act_after_a_close_is_a_reopen(tmp_path, after):
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."), after)
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["reused"] and not row["wrong"], row


def test_a_reopen_does_not_end_the_walk_a_later_wrong_close_makes_the_row_wrong(tmp_path):
    """Review MF1: closed, reopened by a peer, worked, closed again — and THEN its own task reports."""
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    h = 3600
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."),
            peer(T0 + 4 * h, "one more thing"), say(T0 + 4 * h + 60, "Done again."),
            task_note(T0 + 14 * h), say(T0 + 14 * h + 30, "The task finished."))
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["wrong"] and row["after_kind"] == "a background task reported", row
    assert row["closed_at"] == T0 + 4 * h + 60 + 180 * 60
    # negative: without the late report the row stays what its first close was, a reopen
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."),
            peer(T0 + 4 * h, "one more thing"), say(T0 + 4 * h + 60, "Done again."))
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["reused"] and not row["wrong"] and row["closed_at"] == T0 + 10 + 180 * 60, row


def test_the_replay_table_also_states_the_bound_with_every_old_task_doubt_dropped(tmp_path):
    """An old task that never reported, then reports: unread (the default) it keeps for 12 h and the
    report comes first; replayed as if nothing ran, the close at 3 h is a wrong one, and is named."""
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    h = 3600
    write_t(root / "a.jsonl", title("suite-1"), human(T0),
            use(T0 + 5, "u1", "Bash", run_in_background=True),
            result(T0 + 6, "u1", "Command running in background with ID: bg1. Output is"),
            say(T0 + 10, "The suite runs."), task_note(T0 + 5 * h), say(T0 + 5 * h + 9, "Green."))
    files = cr.transcripts(tmp_path / "projects", 0)
    (row,) = cr.replay(files, T0 + 10**6, T0 - 1, 180)
    assert not row["wrong"] and row["closed_at"] == T0 + 5 * h + 9 + 180 * 60, row
    (hi,) = cr.replay(files, T0 + 10**6, T0 - 1, 180, nothing_runs=True)
    assert hi["wrong"] and hi["closed_at"] == T0 + 10 + 180 * 60, hi
    sys.path.insert(0, str(TOOLS))
    import sessions
    text = sessions.replay_table([row], T0 + 10**6, T0 - 1, 180, 1, (), [hi])
    assert "closes 1 and 1 of those did another turn on their own (suite-1)" in text
    assert "on its own after the close moment): 0." in text


def test_a_record_scan_does_not_count_never_stands_in_for_the_sessions_own_turn(tmp_path):
    """Fix review MF1: a system/attachment/dequeue record in the same millisecond as the session's own
    report must not be read as somebody's act; nor a queue record with no text."""
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    for i, before in enumerate([
            json.dumps({"type": "system", "timestamp": iso_ts(LATER), "subtype": "x"}),
            json.dumps({"type": "attachment", "timestamp": iso_ts(LATER), "attachment": {"type": "x"}}),
            json.dumps({"type": "queue-operation", "operation": "remove", "timestamp": iso_ts(LATER),
                        "content": "typed"})]):
        write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."), before, task_note(LATER))
        (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
        assert row["wrong"] and row["after_kind"] == "a background task reported", (i, row)
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."), queued(LATER, None))
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["wrong"] and row["after_kind"] == "a queue record without text", row


def test_a_new_launch_under_the_name_does_not_hide_the_old_transcripts_own_turn(tmp_path):
    """Fix review MF2: a.jsonl closes; b.jsonl (same name) is prompted first; then a's task reports."""
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    h = 3600
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."), task_note(T0 + 5 * h))
    write_t(root / "b.jsonl", title("s-1"), human(T0 + 4 * h, "a new launch"))
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["wrong"] and row["after"] == T0 + 5 * h, row
    # negative: when a's own transcript goes on with a peer's message, the row stays a reopen
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."), peer(T0 + 5 * h))
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["reused"] and not row["wrong"], row
    # the first review's survivor e6: another transcript that BEGINS with a reply is an own turn
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."))
    write_t(root / "b.jsonl", title("s-1"), say(T0 + 4 * h, "Still here."))
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["wrong"] and row["after_kind"] == "it wrote a reply", row


def test_another_transcript_that_begins_with_the_sessions_own_turn_is_a_wrong_close(tmp_path):
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Done."))
    write_t(root / "b.jsonl", title("s-1"), task_note(T0 + 5 * 3600))
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["wrong"] and row["after_kind"] == "a background task reported", row
    # negative: one that begins with anything else was started by someone (a launch or a resume)
    write_t(root / "b.jsonl", title("s-1"), rec("user", T0 + 5 * 3600, "session start", isMeta=True))
    (row,) = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert row["reused"] and not row["wrong"] and "another transcript" in row["after_kind"], row


def test_replay_sees_a_resume_under_the_same_name_as_a_wrong_close(tmp_path):
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    write_t(root / "a.jsonl", title("s-1"), human(T0), say(T0 + 10, "Merged. SESSION COMPLETE."))
    write_t(root / "b.jsonl", title("s-1"), human(T0 + 5 * 3600, "resume: continue"))
    rows = cr.replay(cr.transcripts(tmp_path / "projects", 0), T0 + 10**6, T0 - 1, 180)
    assert len(rows) == 1 and rows[0]["reused"] and not rows[0]["wrong"] and rows[0]["files"] == 2
    assert "another transcript under its name" in rows[0]["after_kind"]


def test_the_replay_cli_writes_its_table_and_exits_red_on_a_wrong_close(tmp_path):
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    now = time.time()
    write_t(root / "a.jsonl", title("w-1"), human(now - 9 * 3600),
            say(now - 9 * 3600 + 10, "Merged. SESSION COMPLETE."), task_note(now - 3600))
    out = tmp_path / "REPLAY.md"
    env = dict(os.environ, CLAUDE_PROJECTS_DIR=str(tmp_path / "projects"),
               GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"), GEDAECHTNIS_CONFIG=str(tmp_path / "no.json"))
    p = subprocess.run([sys.executable, str(TOOLS / "sessions.py"), "close", "--replay", "1",
                        "--out", str(out)], capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "did another turn on its own after the close moment): 1" in out.read_text()
    # negative: a message from another session after the close is a reopen, and the exit is 0
    write_t(root / "a.jsonl", title("w-1"), human(now - 9 * 3600),
            say(now - 9 * 3600 + 10, "Merged. SESSION COMPLETE."), peer(now - 3600))
    p = subprocess.run([sys.executable, str(TOOLS / "sessions.py"), "close", "--replay", "1",
                        "--out", str(out)], capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "on its own after the close moment): 0" in out.read_text()
    assert "reopened — a message from another session" in out.read_text()


# ----------------------------------------------------------------- fleet: SESSCLOSE-2 parts ----

@pytest.fixture
def fl(tmp_path, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path / "sessions"))
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    import fleet
    return fleet


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def test_standing_dirt_of_a_shared_checkout_is_not_the_sessions(fl, tmp_path):
    repo = tmp_path / "main"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "mine.py").write_text("a")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "a", cwd=repo)
    (repo / "someone-elses.txt").write_text("standing dirt, written today")
    assert fl.session_unsaved(str(repo), None, {}) == []
    assert fl.session_unsaved(str(repo), None, {str(repo / "mine.py"): 1}) == []
    (repo / "mine.py").write_text("changed by the session")
    got = fl.session_unsaved(str(repo), None, {str(repo / "mine.py"): 1})
    assert got == [os.path.join(os.path.realpath(repo), "mine.py")]


def test_a_file_outside_any_repo_is_saved_and_does_not_count(fl, tmp_path):
    (tmp_path / "scratch.txt").write_text("x")
    assert fl.session_unsaved(str(tmp_path), None, {str(tmp_path / "scratch.txt"): 1}) == []


def test_a_cwd_under_claude_worktrees_is_the_sessions_own_checkout(fl, tmp_path):
    wt = tmp_path / "repo" / ".claude" / "worktrees" / "ROW-1"
    wt.mkdir(parents=True)
    _git("init", "-q", cwd=wt)
    (wt / "a.txt").write_text("a")
    _git("add", ".", cwd=wt)
    _git("commit", "-qm", "a", cwd=wt)
    assert fl.own_worktree(str(wt / "sub"), None) == os.path.realpath(wt)
    assert fl.session_unsaved(str(wt), None, {}) == []
    (wt / "b.txt").write_text("written by a shell command, not in the transcript")
    assert fl.session_unsaved(str(wt), None, {}) == ["b.txt"]
    assert fl.own_worktree(str(tmp_path / "repo"), None) is None


def test_start_times_match_across_the_two_formats(fl):
    assert fl._same_start("Sun 27 Sep 15:18:13 2026", "Sun Sep 27 15:18:13 2026")
    assert not fl._same_start("Sun 27 Sep 15:18:13 2026", "Sun Sep 27 15:18:14 2026")
    assert not fl._same_start(None, "Sun Sep 27 15:18:13 2026")
    loc = fl.registry_start_local("Sun Sep 27 11:58:44 2026")
    import calendar
    t = calendar.timegm(time.strptime("Sun Sep 27 11:58:44 2026", "%a %b %d %H:%M:%S %Y"))
    assert sorted(loc.split()) == sorted(time.strftime("%a %d %b %H:%M:%S %Y",
                                                       time.localtime(t)).lstrip("0").split()) \
        or loc.split()[-2] == time.strftime("%H:%M:%S", time.localtime(t))
    assert fl.registry_start_local("garbage") is None


def test_a_session_outside_the_ledger_is_judged_by_its_registry_record(fl, tmp_path):
    stub = subprocess.Popen(["sleep", "30"])
    try:
        import calendar
        ps = fl.pid_start(stub.pid)
        t = time.mktime(time.strptime(" ".join(ps.split()), "%a %d %b %H:%M:%S %Y")) \
            if ps.split()[1].isdigit() else time.mktime(time.strptime(ps, "%a %b %d %H:%M:%S %Y"))
        utc = time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime(t))
        (tmp_path / "sessions").mkdir()
        (tmp_path / "sessions" / f"{stub.pid}.json").write_text(json.dumps(
            {"pid": stub.pid, "sessionId": "sid-1", "cwd": str(tmp_path), "procStart": utc,
             "kind": "interactive", "name": "stub-outside", "status": "idle", "startedAt": t * 1000}))
        rows = fl.outside_sessions([])
        assert [r["name"] for r in rows] == ["stub-outside"]
        r = rows[0]
        assert fl.same_process(r["pid"], r["pid_start"])
        assert r["outside"] and r["mux"] is None
        assert fl.resume_line(r) == f"cd {tmp_path} && claude -r stub-outside"
        assert fl.outside_sessions([{"name": "x", "pid": stub.pid}]) == []
    finally:
        stub.kill()
        stub.wait()


def test_proposals_carry_the_reopen_line(fl):
    fl.record_proposals([{"name": "a", "pid": 1, "keep": [], "closed": False, "outside": True,
                          "resume": "cd /r && claude -r a"},
                         {"name": "b", "pid": 2, "keep": ["idle: x"], "closed": False}])
    doc = json.loads((fl.config.state() / "session-close-proposals.json").read_text())
    assert doc["judged"] == 2 and doc["kept"] == 1
    assert doc["items"] == [{"name": "a", "pid": 1, "outside": True, "reopen": "cd /r && claude -r a"}]


def test_the_sweep_logs_the_reopen_line_with_every_proposal(fl):
    items = fl.sweep(apply=False, now=T0, rows=[{"name": "d-1", "pid": 7, "pid_start": "s",
                                                  "cwd": "/r"}],
                     gather_fn=lambda r, m, me: {"alive": True, "handoff": True, "status": "idle",
                                                 "scanned": True, "end": E(ts=T0 - 4 * 3600),
                                                 "screen": "❯", "unsaved": [], "mux": "screen"},
                     refusals_fn=lambda r: ([], None))
    assert items[0]["keep"] == []
    log = (fl.config.state() / "session-close.log").read_text()
    assert "proposed\td-1" in log and "reopen: cd /r && claude -r d-1" in log


def test_the_cap_counts_working_sessions_only(fl):
    procs = [{"pid": 1}, {"pid": 2}, {"pid": 3}, {"pid": 4}]
    st = {1: "idle", 2: "busy", 3: None, 4: "shell"}.get
    assert fl.working_count(procs, st) == 3                   # no record counts as working
    assert fl.working_count(None, st) is None
    assert fl.over_cap(3, 3) and not fl.over_cap(2, 3)


def test_each_seat_gets_its_share(fl):
    rows = [{"name": f"b-{i}", "pid": i, "pid_start": "s", "seat": "seat-a"} for i in (1, 2, 3)]
    rows.append({"name": "m-1", "pid": 9, "pid_start": "s", "seat": "seat-b"})
    alive = lambda pid, start: True
    busy = lambda pid: "busy" if pid != 3 else "idle"
    assert fl.over_seat("seat-a", rows, 2, alive, busy)       # 2 working of seat-a
    assert fl.over_seat("seat-a", rows, 3, alive, busy) is None
    assert fl.over_seat("seat-b", rows, 2, alive, busy) is None
    assert fl.over_seat(None, rows, 1, alive, busy) is None
    assert fl.over_seat("seat-a", rows, 0, alive, busy) is None


def test_the_launching_seat_is_read_from_the_parents_registry_record(fl, tmp_path):
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "4495.json").write_text(json.dumps({"pid": 4495, "name": "cursus-atlas"}))
    assert fl.launching_seat({"CLAUDE_PID": "4495"}) == "cursus-atlas"
    assert fl.launching_seat({"CLAUDE_PID": "1"}) is None
    assert fl.launching_seat({"GEDAECHTNIS_SEAT": "x", "CLAUDE_PID": "4495"}) == "x"


def test_memory_pressure_stands_in_when_there_is_no_swap(fl, monkeypatch):
    monkeypatch.setattr(fl, "swap_share", lambda: 0.5)
    assert fl.memory_share() == (0.5, "swap")
    monkeypatch.setattr(fl, "swap_share", lambda: None)
    share, source = fl.memory_share()
    assert source in ("memory pressure", "unknown")
    if share is not None:
        assert 0 <= share <= 1
    assert fl.over_swap(0.8, 0.75, "memory pressure").startswith("memory pressure is 80%")
    assert fl.over_swap(None, 0.75, "unknown") is None


def test_an_exempt_name_is_never_proposed(fl, monkeypatch):
    cfg = Path(os.environ["GEDAECHTNIS_CONFIG"])
    cfg.write_text(json.dumps({"limits": {"session_close_exempt": ["mnemosyne-*"]}}))
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    assert fl.exempt_patterns() == ["mnemosyne-*"]


def test_the_launch_ids_parse_from_the_tools_real_wording(tmp_path):
    """The two strings are copied verbatim (ids and all) from a real transcript of 2026-09-27
    (complete1-builder-48): the Agent tool's launch result and the Monitor tool's. The agent's
    completion notice carries the same id as its `agentId:` line (read in that transcript)."""
    agent = ("Async agent launched successfully. (This tool result is internal metadata — never quote "
             "or paste any part of it, including the agentId below, into a user-facing reply.) agentId: "
             "a5a80d73e500dce1b (internal ID - do not mention to user. Use SendMessage with to: ")
    mon = ("Monitor started (task bqlein7cu, expires in 30m unless the source ends first; you get one "
           "notice at expiry — re-arm if you still need the watch).")
    s = cr.scan(write_t(tmp_path / "a.jsonl", human(T0), use(T0 + 1, "a", "Agent"), result(T0 + 2, "a", agent),
                        use(T0 + 3, "m", "Monitor"), result(T0 + 4, "m", mon), say(T0 + 5, "Done."),
                        rec("user", T0 + 9, "<task-notification>\n<task-id>a5a80d73e500dce1b</task-id>\n"
                            "<status>completed</status>", origin={"kind": "task-notification"}),
                        say(T0 + 10, "Done.")))
    assert s["ends"][0]["open_bg"] == ["a5a80d73e500dce1b", "bqlein7cu"]
    assert s["last"]["open_bg"] == ["bqlein7cu"]


def test_a_tmux_session_with_a_client_is_attached(monkeypatch):
    import fleet
    class P:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **k: P(0, "/dev/ttys003: s-1 [80x24]\n"))
    assert fleet.tmux_attached("s-1")
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **k: P(0, ""))
    assert not fleet.tmux_attached("s-1")
    monkeypatch.setattr(fleet.subprocess, "run", lambda *a, **k: P(1, ""))
    assert not fleet.tmux_attached("s-1")
