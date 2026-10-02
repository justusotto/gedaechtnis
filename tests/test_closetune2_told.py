"""CLOSETUNE-2 (2026-10-02) — what "cannot be told" is told.

The owner: "did we still not fix the slot thing? … as long as things work and don't get worse."
49 sessions were open, the machine was over its memory line, and the close pass kept all of them:
  (a) a background task that reported WHILE the session worked is queued and handed over as an
      attachment, never as a user record — unread, so the task "never reported back";
  (b) the screen read named a screen by a prefix two screens share, and read the hardcopy file
      before `screen` had written it;
  (c) no launch names a handoff, so the handoff rule saw none — it now reads the one the session
      wrote, and waits the same 30 minutes of quiet as a report to its seat.
And the doubt that is left is READ: a live process of its own keeps a session, named; with the
registry idle and no such process, an old unreported task or an unreadable screen no longer does.

Every rule has a positive and a negative control. Nothing here reads the machine's sessions,
processes or screens: transcripts and process tables are made up, and `screen` is a stub script.
"""
from __future__ import annotations
import json, os, stat, subprocess, sys, tempfile, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import closerules as cr  # noqa: E402

NOW = 1_800_000_000.0
IDLE = 180.0
H = 3600.0


@pytest.fixture
def fl(tmp_path, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "Vault"))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("GEDAECHTNIS_LIMITS", raising=False)
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    import fleet
    return fleet


def end(**over):
    e = {"ts": NOW - 4 * H, "text": "Handoff written.", "open_bg": [], "open_bg_ts": {},
         "began": NOW - 5 * H, "sent": [], "wake_until": 0.0, "cron": 0, "writes": {},
         "reports": [], "humans": 1}
    e.update(over)
    return e


def facts(**over):
    f = {"is_self": False, "alive": True, "handoff": False, "handoff_done": None, "seat": None,
         "status": "idle", "scanned": True, "end": end(), "screen": "❯ \n  ? for shortcuts",
         "attached": False, "unsaved": [], "wrote": [], "outside": False, "mux": "screen",
         "exempt": False, "children": []}
    f.update(over)
    return f


OLD_TASK = {"open_bg": ["bg1"], "open_bg_ts": {"bg1": NOW - 6 * H}}


# ------------------------------------------- (a) a task that reported while the session worked ----

def _r(typ, ts, **kw):
    return json.dumps({"type": typ, "timestamp": ts, **kw})


NOTE = ("<task-notification>\n<task-id>{}</task-id>\n<tool-use-id>u1</tool-use-id>\n"
        "<status>{}</status>\n<summary>Background command</summary>\n</task-notification>")


def _turn(*middle):
    return [
        _r("user", "2026-10-01T10:00:00Z", message={"content": "do the row"}),
        _r("assistant", "2026-10-01T10:01:00Z", message={"content": [
            {"type": "tool_use", "id": "u1", "name": "Bash", "input": {"run_in_background": True}}]}),
        _r("user", "2026-10-01T10:01:05Z", message={"content": [
            {"type": "tool_result", "tool_use_id": "u1",
             "content": "Command running in background with ID: bg77. Output is being written"}]}),
        *middle,
        _r("assistant", "2026-10-01T10:09:00Z",
           message={"stop_reason": "end_turn", "content": [{"type": "text", "text": "Reported."}]}),
    ]


def test_negative_a_task_with_no_notification_stays_open():
    assert cr.scan(Path("x"), lines=_turn())["last"]["open_bg"] == ["bg77"]


@pytest.mark.parametrize("record", [
    _r("queue-operation", "2026-10-01T10:05:00Z", operation="enqueue", content=NOTE.format("bg77", "completed")),
    _r("attachment", "2026-10-01T10:05:00Z",
       attachment={"type": "queued_command", "prompt": NOTE.format("bg77", "failed"),
                   "commandMode": "task-notification"}),
])
def test_positive_a_notification_delivered_mid_turn_closes_its_task(record):
    assert cr.scan(Path("x"), lines=_turn(record))["last"]["open_bg"] == []


@pytest.mark.parametrize("record", [
    # a notification that is not final (the task still runs)
    _r("queue-operation", "2026-10-01T10:05:00Z", operation="enqueue", content=NOTE.format("bg77", "running")),
    # another task's notification
    _r("attachment", "2026-10-01T10:05:00Z",
       attachment={"type": "queued_command", "prompt": NOTE.format("other", "completed")}),
    # a prompt somebody queued that only QUOTES a notification, further down or right away
    _r("queue-operation", "2026-10-01T10:05:00Z", operation="enqueue",
       content="please look at this: " + "x" * 300 + NOTE.format("bg77", "completed")),
    _r("queue-operation", "2026-10-01T10:05:00Z", operation="enqueue",
       content="the seat saw: " + NOTE.format("bg77", "completed")),
    _r("attachment", "2026-10-01T10:05:00Z",
       attachment={"type": "queued_command", "prompt": "fyi " + NOTE.format("bg77", "completed")}),
    # an attachment of another kind
    _r("attachment", "2026-10-01T10:05:00Z",
       attachment={"type": "file", "prompt": NOTE.format("bg77", "completed")}),
])
def test_negative_what_is_not_this_tasks_final_notification_leaves_it_open(record):
    assert cr.scan(Path("x"), lines=_turn(record))["last"]["open_bg"] == ["bg77"]


def test_two_notifications_in_one_body_are_paired_block_by_block():
    """bg77 still runs and another task is done: bg77's id must not take the other's status."""
    both = NOTE.format("bg77", "running") + "\n" + NOTE.format("other", "completed")
    rec = _r("queue-operation", "2026-10-01T10:05:00Z", operation="enqueue", content=both)
    assert cr.scan(Path("x"), lines=_turn(rec))["last"]["open_bg"] == ["bg77"]
    both = NOTE.format("other", "running") + "\n" + NOTE.format("bg77", "completed")   # positive
    rec = _r("queue-operation", "2026-10-01T10:05:00Z", operation="enqueue", content=both)
    assert cr.scan(Path("x"), lines=_turn(rec))["last"]["open_bg"] == []


def test_a_final_message_in_two_records_is_one_turn_end():
    two = [
        _r("user", "2026-10-01T10:00:00Z", message={"content": "go"}),
        _r("assistant", "2026-10-01T10:01:00Z",
           message={"stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": "…"}]}),
        _r("assistant", "2026-10-01T10:01:04Z",
           message={"stop_reason": "end_turn", "content": [{"type": "text", "text": "Landed."}]}),
    ]
    s = cr.scan(Path("x"), lines=two)
    assert len(s["ends"]) == 1 and s["last"]["text"] == "Landed."
    assert s["last"]["ts"] == cr._ts("2026-10-01T10:01:04Z")
    # negative: a prompt between two ends makes them two turns
    three = two + [_r("user", "2026-10-01T11:00:00Z", message={"content": "more"}),
                   _r("assistant", "2026-10-01T11:01:00Z",
                      message={"stop_reason": "end_turn", "content": [{"type": "text", "text": "b"}]})]
    assert len(cr.scan(Path("x"), lines=three)["ends"]) == 2
    # negative (review MF2): a reply HOURS later with nothing recorded in between is its own turn
    late = two + [_r("assistant", "2026-10-01T15:01:04Z",
                     message={"stop_reason": "end_turn", "content": [{"type": "text", "text": "c"}]})]
    assert len(cr.scan(Path("x"), lines=late)["ends"]) == 2
    # ... unless the records carry the same message id, whatever the clock says
    same = [two[0]] + [_r("assistant", ts, message={"id": "msg_1", "stop_reason": "end_turn",
                                                    "content": [{"type": "text", "text": "x"}]})
                       for ts in ("2026-10-01T10:01:00Z", "2026-10-01T10:03:30Z")]
    assert len(cr.scan(Path("x"), lines=same)["ends"]) == 1
    other = [two[0]] + [_r("assistant", ts, message={"id": mid, "stop_reason": "end_turn",
                                                     "content": [{"type": "text", "text": "x"}]})
                        for ts, mid in (("2026-10-01T10:01:00Z", "msg_1"), ("2026-10-01T10:01:02Z", "msg_2"))]
    assert len(cr.scan(Path("x"), lines=other)["ends"]) == 2          # two messages: two ends


# --------------------------------------------------- the old-task doubt is read, not waited out ----

def test_positive_an_old_task_does_not_keep_a_session_nothing_of_whose_runs(fl):
    f = facts(end=end(**OLD_TASK))
    assert fl.classify(f, NOW, IDLE) == []
    assert any("never reported back" in n and "no process of its own" in n for n in fl.notes(f, NOW, IDLE))


@pytest.mark.parametrize("over, word", [
    ({"children": None}, "cannot be told"),                 # the process table could not be read
    ({"status": None}, "cannot be told"),                   # the registry could not be read
    ({"status": "shell"}, "registry says 'shell'"),         # the CLI says a shell of its own runs
    ({"children": ["pid 8183 (zsh)"]}, "pid 8183 (zsh)"),   # a live process of its own, named
])
def test_negative_an_old_task_keeps_when_something_may_run(fl, over, word):
    keep = fl.classify(facts(end=end(**OLD_TASK), **over), NOW, IDLE)
    assert any(word in k for k in keep), keep


def test_negative_a_young_task_keeps_whatever_runs(fl):
    f = facts(end=end(open_bg=["bg1"], open_bg_ts={"bg1": NOW - 600}))
    assert any("less than" in k for k in fl.classify(f, NOW, IDLE))


def test_negative_a_live_process_of_its_own_keeps_however_old_and_with_no_task_open(fl):
    f = facts(end=end(ts=NOW - 30 * H), children=["pid 7 (zsh)", "pid 9 (Python)"])
    keep = fl.classify(f, NOW, IDLE)
    assert keep == ["background: 2 process(es) it started are still running (pid 7 (zsh), pid 9 (Python))"]
    assert fl.classify(facts(end=end(ts=NOW - 30 * H)), NOW, IDLE) == []      # positive: none, closes


def _table(fl, born, kids):
    """pid 100 = the session, started at `born`; kids: [(pid, ppid, seconds after, command)]."""
    def stamp(t):
        return time.strftime("%a %d %b %H:%M:%S %Y", time.localtime(t))
    t = {100: {"ppid": 50, "start": stamp(born), "toks": ["claude", "--name", "x"]}}
    for pid, ppid, after, cmd in kids:
        t[pid] = {"ppid": ppid, "start": stamp(born + after), "toks": cmd.split()}
    return t


def test_own_processes_names_the_work_and_leaves_out_the_boot_helpers(fl):
    t = _table(fl, NOW, [
        (101, 100, 1, "/opt/python/bin/Python /repo/mcp_server.py"),      # an MCP server: a helper
        (102, 100, 1, "/bin/zsh -c source snapshot.sh && pytest -q"),     # a shell: work, even at boot
        (103, 100, 3600, "/opt/python/bin/Python -m http.server"),        # started later: work
        (104, 100, 3600, "caffeinate -i -t 300"),                         # the CLI's keep-awake
        (105, 101, 3600, "/opt/python/bin/Python worker.py"),             # the helper's own child
        (106, 999, 3600, "/bin/zsh -c somebody-else"),                    # not this session's
    ])
    assert fl.own_processes(100, t) == ["pid 102 (zsh)", "pid 103 (Python)"]
    # negative: what only LOOKS like a helper is work
    t = _table(fl, NOW, [
        (201, 100, 1, "caffeinate -i make all"),                          # caffeinate running a job
        (202, 100, 1, "/opt/homebrew/bin/fish -c ./long-job"),            # another shell, at boot
        (203, 100, -5, "/opt/python/bin/Python reused-pid.py"),           # "started before" its parent
    ])
    t[204] = {"ppid": 100, "start": "not a date", "toks": ["node", "mcp.js"]}   # start unreadable
    assert fl.own_processes(100, t) == ["pid 201 (caffeinate)", "pid 202 (fish)", "pid 203 (Python)",
                                        "pid 204 (node)"]
    assert fl.own_processes(100, _table(fl, NOW, [(101, 100, 1, "node mcp.js")])) == []


def test_negative_own_processes_is_unknown_without_the_table_or_the_session_row(fl):
    assert fl.own_processes(100, None) is None
    assert fl.own_processes(555, _table(fl, NOW, [])) is None
    assert fl.own_processes("not-a-pid", _table(fl, NOW, [])) is None


# --------------------------------------------------------------------- (b) the screen read ----

STUB = r'''#!/bin/sh
# a stand-in for `screen`: -ls prints $STUB/ls; a hardcopy logs its target and writes per $STUB/mode
if [ "$1" = "-ls" ]; then cat "$STUB/ls"; exit 0; fi
target="$2"; file="$8"
echo "$target" >> "$STUB/targets"
case "$target" in [0-9]*.*) ;; *) n=$(grep -c "\.$target" "$STUB/ls"); [ "$n" -gt 1 ] && { echo "There are several suitable screens on:"; exit 1; } ;; esac
case "$(cat "$STUB/mode")" in
  late)  ( sleep 0.4; printf 'line one\n  ? for shortcuts\n' > "$file" ) >/dev/null 2>&1 & ;;
  bursts) ( printf 'line one\n' > "$file"; sleep 0.12; printf 'Do you want to proceed?\n' >> "$file" ) >/dev/null 2>&1 & ;;
  never) ;;
  blank) printf '\000\000   \n' > "$file" ;;
esac
exit 0
'''


@pytest.fixture
def screen(fl, tmp_path, monkeypatch):
    stub = tmp_path / "stub"
    (stub / "bin").mkdir(parents=True)
    exe = stub / "bin" / "screen"
    exe.write_text(STUB)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STUB", str(stub))
    monkeypatch.setenv("PATH", f"{stub / 'bin'}{os.pathsep}{os.environ['PATH']}")
    box = tmp_path / "tmp"
    box.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(box))

    def setup(ls, mode):
        (stub / "ls").write_text("There are screens on:\n" + "".join(
            f"\t{pid}.{name}\t(Detached)\n" for pid, name in ls) + f"{len(ls)} Sockets in /x.\n")
        (stub / "mode").write_text(mode)
        return stub
    setup.box = box
    return setup


def test_positive_a_hardcopy_written_late_is_still_read_and_nothing_is_left_behind(fl, screen):
    screen([(111, "X-A")], "late")
    assert "? for shortcuts" in fl.read_screen("screen", "X-A")
    assert list(screen.box.iterdir()) == []


@pytest.mark.parametrize("mode", ["never", "blank"])
def test_negative_a_hardcopy_that_never_has_text_is_unreadable(fl, screen, mode):
    screen([(111, "X-A")], mode)
    t0 = time.time()
    assert fl.read_screen("screen", "X-A", wait_s=0.3) is None
    assert time.time() - t0 < 2 and list(screen.box.iterdir()) == []


def test_positive_a_name_that_is_a_prefix_of_another_is_read_by_its_own_pid(fl, screen):
    stub = screen([(111, "X-A"), (222, "X-A2")], "late")
    assert fl.read_screen("screen", "X-A") is not None
    assert (stub / "targets").read_text().split() == ["111.X-A"]


@pytest.mark.parametrize("ls", [[(111, "X-A"), (222, "X-A")],      # two of exactly that name
                                [(222, "X-A2")],                   # none: only a longer name
                                []])
def test_negative_without_one_screen_of_exactly_that_name_no_screen_is_read(fl, screen, ls):
    stub = screen(ls, "late")
    assert fl.read_screen("screen", "X-A") is None
    assert not (stub / "targets").exists()                 # no hardcopy was asked of any screen


def test_a_hardcopy_written_in_two_bursts_is_read_whole(fl, screen):
    """The prompt lines are the LAST lines: a read that stops at the first text would miss them."""
    screen([(111, "X-A")], "bursts")
    assert "Do you want to proceed?" in fl.read_screen("screen", "X-A")


def test_positive_an_unreadable_screen_does_not_keep_a_session_that_can_have_no_prompt(fl):
    f = facts(screen=None)
    assert fl.classify(f, NOW, IDLE) == []
    assert any("no prompt can be up" in n for n in fl.notes(f, NOW, IDLE))


@pytest.mark.parametrize("over", [
    {"children": None},                                     # whether something runs: unread
    {"status": None},
    {"end": end(**OLD_TASK)},                               # a task is still open: it may be asking
    {"scanned": False},                                     # no transcript to say the turn ended
])
def test_negative_an_unreadable_screen_keeps_when_a_prompt_cannot_be_ruled_out(fl, over):
    keep = fl.classify(facts(screen=None, **over), NOW, IDLE)
    assert any(k.startswith(("no-prompt: its screen could not be read", "transcript:")) for k in keep), keep


def test_negative_a_prompt_that_IS_read_on_the_screen_keeps_whatever_else_holds(fl):
    keep = fl.classify(facts(screen="Do you want to proceed?\n❯ 1. Yes"), NOW, IDLE)
    assert keep == ["no-prompt: a permission prompt is on its screen"]


# ------------------------------------------------- (c) the handoff the session wrote itself ----

def _git(repo: Path, *a, when="2026-10-01T12:00:00"):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", GIT_AUTHOR_DATE=when))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "review").mkdir(parents=True)
    _git(r, "init", "-q")
    (r / "review" / "HANDOFF-2026-10-01-CHAIN.md").write_text("# HANDOFF\n\n## State\n\nLANDED — main `abc`.\n")
    (r / "review" / "HANDOFF-WIP.md").write_text("## State\n\nIN PROGRESS.\n")
    (r / "review" / "HANDOFF-LOOSE.md").write_text("## State\n\nDONE.\n")
    _git(r, "add", "review/HANDOFF-2026-10-01-CHAIN.md", "review/HANDOFF-WIP.md")
    _git(r, "commit", "-qm", "handoffs")
    return r


def test_own_handoffs_are_the_handoff_files_it_wrote_newest_last(fl):
    w = {"/r/review/HANDOFF-B.md": 20.0, "/r/src/a.py": 30.0, "/r/review/REPORT.md": 40.0,
         "/r/app/handoff-a.md": 10.0}
    assert fl.own_handoffs(end(writes=w, began=5.0)) == ["/r/app/handoff-a.md", "/r/review/HANDOFF-B.md"]
    assert fl.own_handoffs(None) == [] and fl.own_handoffs(end()) == []
    # negative: a handoff it touched BEFORE its final turn began is not its last act
    assert fl.own_handoffs(end(writes=w, began=15.0)) == ["/r/review/HANDOFF-B.md"]
    assert fl.own_handoffs(end(writes=w, began=None)) == []


def test_positive_a_handoff_the_session_wrote_is_seen_without_a_ledger_name(fl, repo):
    p = str(repo / "review" / "HANDOFF-2026-10-01-CHAIN.md")
    t = fl.handoff_done(None, str(repo), [p])
    assert t == time.mktime(time.strptime("2026-10-01T12:00:00", "%Y-%m-%dT%H:%M:%S"))


def test_positive_a_handoff_written_in_a_removed_worktree_is_read_on_main(fl, repo):
    gone = str(repo / ".claude" / "worktrees" / "CHAIN" / "review" / "HANDOFF-2026-10-01-CHAIN.md")
    assert fl.handoff_done(None, str(repo), [gone]) is not None


@pytest.mark.parametrize("name", ["HANDOFF-WIP.md",          # its State does not say done
                                  "HANDOFF-LOOSE.md",        # says DONE, never committed
                                  "HANDOFF-NOWHERE.md"])     # no such file
def test_negative_a_handoff_that_is_not_done_and_committed_is_not_evidence(fl, repo, name):
    assert fl.handoff_done(None, str(repo), [str(repo / "review" / name)]) is None
    assert fl.handoff_done(None, str(repo), []) is None
    assert fl.handoff_done(None, str(repo), [str(repo / "review" / "HAND\x00OFF.md")]) is None


def test_gather_passes_the_sessions_own_handoff_and_processes_to_the_rule(fl, repo, monkeypatch, tmp_path):
    """The wiring: `gather` reads the handoff from the transcript's writes and the process table."""
    p = str(repo / "review" / "HANDOFF-2026-10-01-CHAIN.md")
    tp = tmp_path / "t.jsonl"
    tp.write_text("\n".join([
        _r("user", "2026-10-01T09:59:00Z", message={"content": "go"}),
        _r("assistant", "2026-10-01T10:00:00Z", message={"content": [
            {"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": p}}]}),
        _r("user", "2026-10-01T10:00:01Z", message={"content": [
            {"type": "tool_result", "tool_use_id": "w1", "content": "ok"}]}),
        _r("assistant", "2026-10-01T10:00:30Z",
           message={"stop_reason": "end_turn", "content": [{"type": "text", "text": "Landed."}]}),
    ]) + "\n")
    monkeypatch.setattr(fl, "transcript_for", lambda *a: tp)
    monkeypatch.setattr(fl, "same_process", lambda *a: True)
    monkeypatch.setattr(fl, "read_screen", lambda *a, **k: "❯ ")
    monkeypatch.setattr(fl, "screen_attached", lambda *a: False)
    monkeypatch.setattr(fl, "full_process_table",
                        lambda: _table(fl, NOW, [(102, 100, 5000, "/bin/zsh -c sleep 99")]))
    f = fl.gather({"name": "s", "pid": 100, "pid_start": "x", "cwd": str(repo), "mux": "screen",
                   "launched_at": 0}, "screen", None)
    assert f["handoff_done"] is not None and f["children"] == ["pid 102 (zsh)"]


# ------------------------------------------------------------- the replay runs the live rule ----

def test_live_moment_is_the_first_moment_the_live_rule_closes():
    ts = NOW
    assert cr.live_moment(end(ts=ts), IDLE) == ts + IDLE * 60                       # time
    sent = end(ts=ts, sent=[{"to": "seat", "ts": ts - 5}])
    assert cr.live_moment(sent, IDLE, seat="seat") == ts + 30 * 60                  # report + 30 min
    assert cr.live_moment(sent, IDLE, seat="other") == ts + IDLE * 60
    done = end(ts=ts, began=ts - 600)
    assert cr.live_moment(done, IDLE, handoff_done=ts - 60) == ts + 30 * 60         # handoff + 30 min
    old = end(ts=ts, open_bg=["b"], open_bg_ts={"b": ts - 9 * H})
    assert cr.live_moment(old, IDLE, nothing_runs=True) == ts + IDLE * 60           # read: nothing runs
    assert cr.live_moment(old, IDLE) == ts + 4 * IDLE * 60                          # unread: a doubt, 12 h
    young = end(ts=ts, open_bg=["b"], open_bg_ts={"b": ts - 60})
    assert cr.live_moment(young, IDLE, nothing_runs=True) == ts + IDLE * 60         # never earlier
    assert cr.live_moment(young, IDLE) == ts + 4 * IDLE * 60                        # young, then a doubt
    late = end(ts=ts, open_bg=["b"], open_bg_ts={"b": ts + 600})                    # (a wake-up's task)
    assert cr.live_moment(late, IDLE, nothing_runs=True) == ts + 600 + IDLE * 60


def test_negative_live_moment_never_comes_for_a_recurring_job_or_an_unstamped_end():
    assert cr.live_moment(end(cron=1), IDLE) is None
    assert cr.live_moment(end(ts=None), IDLE) is None
