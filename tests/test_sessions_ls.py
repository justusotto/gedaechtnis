"""`sessions.py ls` sees every live session, not only the ones it launched (SESSLSBLIND-1).

2026-09-28 15:35: `ls` listed the seven Remote Control hosts as BLIND sessions and said "parked for
cursus-atlas (no live session)" while cursus-atlas ran (resumed by hand, its name only in the CLI's
registry). The registry `~/.claude/sessions/<pid>.json` is now the second source; a host is shown as
HOST, a Project thread as THREAD of its host, a daemon background job (`kind: bg`) as FORK with the
session it was copied from. A session is named by its SESSION ID: a host restarts an idle thread
under a new derived name.

One fixture per kind, each with the control that breaks it. Nothing reads the real registry or the
real process table: both are injected.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(TOOLS))


@pytest.fixture
def fl(tmp_path, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "Atlas"))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path / "sessions"))
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    import fleet
    return fleet


# `ps -Ao pid=,ppid=,lstart=,command=` as this Mac printed it (CEST, day before month).
PS = """\
  100     1 Mon 28 Sep 13:02:25 2026     claude remote-control --name demo-repo host --spawn same-dir --capacity 4 --permission-mode auto
  200   100 Mon 28 Sep 20:24:44 2026     /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe --print --sdk-url https://x/cse_01A --session-id cse_01A
  201   100 Mon 28 Sep 20:30:00 2026     /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe --print --sdk-url https://x/cse_01B --session-id cse_01B
  300   299 Mon 28 Sep 15:23:15 2026     claude --resume 2de0e225-d7a7-42e7-86be-9a80f8e29cf7
  400   399 Mon 28 Sep 16:00:00 2026     claude --name blind-one
  500   499 Mon 28 Sep 17:00:00 2026     claude --fork-session --resume 11111111-aaaa-bbbb-cccc-000000000000
  600   599 Mon 28 Sep 17:05:00 2026     claude --fork-session
  700   699 Mon 28 Sep 18:00:00 2026     /bin/zsh -c claude --name not-a-claude-image
  800   799 Mon 28 Sep 18:10:00 2026     node /usr/local/bin/claude --name via-node
  110     1 Mon 28 Sep 13:03:00 2026     claude remote-control --name card host --spawn same-dir
  210   110 Mon 28 Sep 20:40:00 2026     /x/claude.exe --print --sdk-url https://x/cse_01C
  220   110 Mon 28 Sep 20:41:00 2026     /x/claude.exe --print --sdk-url https://x/cse_01D --fork-session
  230   229 Mon 28 Sep 20:42:00 2026     claude --resume remote-control
"""


def rec(pid, sid, name, *, utc, kind="interactive", status="idle", **extra):
    """A registry record; `procStart` is UTC, as the CLI writes it (measured 2026-09-27)."""
    d = {"pid": pid, "sessionId": sid, "name": name, "kind": kind, "status": status,
         "procStart": utc, "cwd": "/w"}
    d.update(extra)
    return d


@pytest.fixture
def world(fl, monkeypatch):
    # The fixture's local zone is CEST, so a UTC procStart must be converted to compare.
    monkeypatch.setenv("TZ", "Europe/Berlin")
    import time
    time.tzset()
    yield fl
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


def records():
    return [
        rec(200, "sid-thread-a", "demo-repo-57", utc="Mon Sep 28 18:24:44 2026",
            status="busy", bridgeSessionId="session_01A"),
        rec(201, "sid-thread-b", "demo-repo-57", utc="Mon Sep 28 18:30:00 2026",
            bridgeSessionId="session_01B"),
        rec(300, "2de0e225-d7a7-42e7-86be-9a80f8e29cf7", "cursus-atlas",
            utc="Mon Sep 28 13:23:15 2026"),
        rec(500, "sid-fork-argv", "fork-a", kind="bg", utc="Mon Sep 28 15:00:00 2026"),
        rec(600, "sid-fork-none", "fork-b", kind="bg", utc="Mon Sep 28 15:05:00 2026"),
        rec(800, "sid-fork-rec", "fork-c", kind="bg", utc="Mon Sep 28 16:10:00 2026",
            forkParentSessionId="22222222-parent"),
        rec(900, "sid-dead", "dead-one", utc="Mon Sep 28 10:00:00 2026"),          # no process
    ]


def by_pid(view):
    return {e["pid"]: e for e in view}


# ------------------------------------------------------------------------ the process table ----

def test_the_table_keeps_hosts_threads_and_sessions_and_drops_a_shell(world):
    t = world.parse_process_table(PS)
    assert set(t) == {100, 110, 200, 201, 210, 220, 230, 300, 400, 500, 600, 800}
    assert 700 not in t                   # a shell whose ARGUMENTS mention claude is not claude
    assert t[200]["ppid"] == 100 and t[300]["start"] == "Mon 28 Sep 15:23:15 2026"


def test_a_host_is_told_by_its_subcommand_and_its_name_is_whole(world):
    t = world.parse_process_table(PS)
    assert world.is_host(t[100]["toks"]) and not world.is_host(t[300]["toks"])
    assert world.host_name(t[100]["toks"]) == "demo-repo host"
    assert world.host_name(["claude", "remote-control", "--name=x host"]) == "x host"
    assert world.host_name(["claude", "remote-control"]) is None


# ------------------------------------------------------------------------------ the view ----

def test_each_kind_is_classified(world):
    v = by_pid(world.live_view(records(), world.parse_process_table(PS)))
    assert v[100]["kind"] == "HOST" and v[100]["threads"] == 2
    assert v[200]["kind"] == "THREAD" and v[200]["host"] == "demo-repo host"
    assert v[300]["kind"] == "SESSION" and v[300]["name"] == "cursus-atlas"
    assert v[400]["kind"] == "BLIND" and v[400]["name"] == "blind-one"
    assert v[500]["kind"] == "FORK" and v[500]["source"] == "11111111-aaaa-bbbb-cccc-000000000000"
    assert v[600]["kind"] == "FORK" and v[600]["source"] is None
    assert v[800]["kind"] == "FORK" and v[800]["source"] == "22222222-parent"


def test_a_host_is_never_a_blind_session(world):
    """The live case: seven hosts listed BLIND. Negative control: the host has no record."""
    v = world.live_view(records(), world.parse_process_table(PS))
    assert [e["kind"] for e in v if e["pid"] == 100] == ["HOST"]


def test_a_session_resumed_by_hand_is_live_by_its_record(world):
    """Its argv carries no --name; only the registry names it."""
    table = world.parse_process_table(PS)
    assert "--name" not in table[300]["toks"]
    assert "cursus-atlas" in world.live_names(world.live_view(records(), table))
    assert "cursus-atlas" not in world.live_names(world.live_view(
        [r for r in records() if r["pid"] != 300], table))


def test_a_dead_pid_and_a_reused_pid_are_not_live(world):
    table = world.parse_process_table(PS)
    v = by_pid(world.live_view(records(), table))
    assert 900 not in v                                           # no such process
    no_start = [{"pid": 901, "sessionId": "s", "name": "n", "kind": "interactive"}]
    assert 901 not in by_pid(world.live_view(no_start, table))    # …even with no procStart
    reused = [rec(300, "old-sid", "old-name", utc="Sun Sep 27 08:00:00 2026")]
    assert 300 not in by_pid(world.live_view(reused, table))


def test_the_utc_start_is_converted_before_it_is_compared(world):
    """Control on the conversion: a record whose procStart were read as LOCAL would be two hours
    off and dropped. The same wall-clock tokens without conversion must NOT match."""
    table = world.parse_process_table(PS)
    local_as_utc = [rec(300, "s", "cursus-atlas", utc="Mon Sep 28 15:23:15 2026")]
    assert 300 not in {e["pid"] for e in world.live_view(local_as_utc, table)
                       if e["kind"] == "SESSION"}


def test_two_threads_with_one_name_are_two_sessions_by_id(world):
    v = world.live_view(records(), world.parse_process_table(PS))
    ids = [e["session_id"] for e in v if e.get("name") == "demo-repo-57"]
    assert sorted(ids) == ["sid-thread-a", "sid-thread-b"]


def test_a_hosts_name_is_not_a_live_session_for_parked_messages(world):
    v = world.live_view(records(), world.parse_process_table(PS))
    assert "demo-repo host" not in world.live_names(
        [e for e in v if e["kind"] == "HOST"])


def test_without_a_process_table_liveness_falls_back_to_the_pid(world, monkeypatch):
    monkeypatch.setattr(world.procs, "pid_alive", lambda pid: pid in (300,))
    v = by_pid(world.live_view(records(), None))
    assert set(v) == {300} and v[300]["kind"] == "SESSION"


# ------------------------------------------------------------------------------- the page ----

def test_ls_prints_host_thread_fork_and_delivers_parked_to_a_live_name(world, monkeypatch, capsys):
    import sessions
    f = world
    monkeypatch.setattr(f, "current_launches", lambda rows=None: [])
    monkeypatch.setattr(f, "claude_processes", lambda: [])
    monkeypatch.setattr(f, "registry_records", records)
    monkeypatch.setattr(f, "process_table", lambda: f.parse_process_table(PS))
    monkeypatch.setattr(f, "screen_of", lambda pid: "rc-host" if pid == 100 else None)
    monkeypatch.setattr(f, "read_screen", lambda mux, name: None)
    monkeypatch.setattr(f, "memory_share", lambda: (None, "swap"))
    monkeypatch.setattr(f, "all_parked", lambda: {"cursus-atlas": 11, "gone-session": 2})
    monkeypatch.setattr(f, "multiplexer", lambda: "screen")
    assert sessions.cmd_ls(None) == 0
    out = capsys.readouterr().out
    assert "- HOST demo-repo host · pid 100 · 2 thread session(s) running" in out
    assert "answer it: screen -r rc-host" in out
    assert "- THREAD of host demo-repo host session sid-thread-a" in out
    assert "the name is not stable: address it by session id" in out
    assert "- FORK session sid-fork-argv" in out and "copied from 11111111-" in out
    assert "copied from: not recorded" in out
    assert "session 2de0e225-d7a7-42e7-86be-9a80f8e29cf7 · pid 300 · IDLE · name cursus-atlas" in out
    assert "blind-one · pid 400 · BLIND" in out
    assert ("parked for cursus-atlas (live, session 2de0e225-d7a7-42e7-86be-9a80f8e29cf7: "
            "deliver them): 11") in out
    assert "parked for gone-session (no live session): 2" in out
    assert "demo-repo host · pid 100 · BLIND" not in out
    assert "dead-one" not in out


# ------------------------------------------------------------------- review round 1 fixes ----

def more_records():
    return records() + [
        rec(210, "sid-card-thread", "card-9a", utc="Mon Sep 28 18:40:00 2026"),
        rec(220, "sid-bg-under-host", "bg-x", kind="bg", utc="Mon Sep 28 18:41:00 2026"),
    ]


def test_threads_are_counted_per_host_and_a_bg_job_under_a_host_is_a_fork(world):
    v = by_pid(world.live_view(more_records(), world.parse_process_table(PS)))
    assert v[100]["threads"] == 2 and v[110]["threads"] == 1
    assert v[210]["kind"] == "THREAD" and v[210]["host"] == "card host"
    assert v[220]["kind"] == "FORK"


def test_remote_control_must_be_the_subcommand_to_be_a_host(world):
    t = world.parse_process_table(PS)
    assert not world.is_host(t[230]["toks"])
    assert 230 not in {e["pid"] for e in world.live_view([], t) if e["kind"] == "HOST"}


def test_resume_without_fork_session_names_no_source(world):
    assert world._fork_source({}, ["claude", "--resume", "abc"]) is None
    assert world._fork_source({}, ["claude", "--fork-session", "--resume", "abc"]) == "abc"


@pytest.mark.parametrize("bad", [{"name": ["a"]}, {"status": ["busy"]}, {"sessionId": 7},
                                 {"cwd": {"x": 1}}, {"bridgeSessionId": ["s"]}])
def test_odd_registry_fields_do_not_crash_the_page(world, bad, monkeypatch, capsys):
    r = rec(300, "sid", "cursus-atlas", utc="Mon Sep 28 13:23:15 2026")
    r.update(bad)
    table = world.parse_process_table(PS)
    v = world.live_view([r, "not a dict", 5, {"pid": True}, {"pid": "300x"}], table)
    world.live_names(v)
    import sessions
    for e in v:
        sessions._view_line(e)


def test_a_record_without_procstart_is_tied_by_startedat_or_not_at_all(world):
    table = world.parse_process_table(PS)
    t0 = world._start_epoch("Mon 28 Sep 15:23:15 2026")
    ok = {"pid": 300, "sessionId": "s-ok", "name": "n", "startedAt": (t0 + 2) * 1000}
    old = {"pid": 300, "sessionId": "s-old", "name": "n", "startedAt": (t0 - 3600) * 1000}
    bare = {"pid": 300, "sessionId": "s-bare", "name": "n"}
    assert by_pid(world.live_view([ok], table))[300]["session_id"] == "s-ok"
    assert 300 not in by_pid(world.live_view([old], table))       # the pid was reused
    assert 300 not in by_pid(world.live_view([bare], table))      # cannot be tied: not trusted


def test_one_pid_is_listed_once(world):
    r = rec(300, "sid-1", "cursus-atlas", utc="Mon Sep 28 13:23:15 2026")
    r2 = dict(r, sessionId="sid-2")
    v = world.live_view([r, r2], world.parse_process_table(PS))
    assert [e["session_id"] for e in v if e["pid"] == 300] == ["sid-1"]


def test_a_host_with_a_record_is_shown_once_as_host(world):
    r = rec(100, "sid-host", "demo-repo host", utc="Mon Sep 28 11:02:25 2026")
    v = world.live_view([r], world.parse_process_table(PS))
    assert [e["kind"] for e in v if e["pid"] == 100] == ["HOST"]


def _page(world, monkeypatch, capsys, parked, launches=(), gather_alive=True):
    import sessions
    f = world
    monkeypatch.setattr(f, "current_launches", lambda rows=None: list(launches))
    monkeypatch.setattr(f, "gather", lambda r, mux, me: {"alive": gather_alive, "handoff": False,
                                                          "status": "idle", "scanned": True,
                                                          "end": {}, "screen": None})
    monkeypatch.setattr(f, "transcript_for", lambda *a, **k: None)
    monkeypatch.setattr(f, "parked", lambda name: [])
    monkeypatch.setattr(f, "claude_processes", lambda: [])
    monkeypatch.setattr(f, "registry_records", more_records)
    monkeypatch.setattr(f, "process_table", lambda: f.parse_process_table(PS))
    monkeypatch.setattr(f, "screen_of", lambda pid: None)
    monkeypatch.setattr(f, "read_screen", lambda mux, name: None)
    monkeypatch.setattr(f, "memory_share", lambda: (None, "swap"))
    monkeypatch.setattr(f, "all_parked", lambda: dict(parked))
    monkeypatch.setattr(f, "multiplexer", lambda: "screen")
    assert sessions.cmd_ls(None) == 0
    return capsys.readouterr().out


def test_a_blind_name_is_not_called_deliverable(world, monkeypatch, capsys):
    out = _page(world, monkeypatch, capsys, {"blind-one": 1})
    assert "parked for blind-one (no live session)" in out and "deliver them" not in out


def test_a_name_two_threads_carry_lists_both_session_ids(world, monkeypatch, capsys):
    out = _page(world, monkeypatch, capsys, {"demo-repo-57": 1})
    line = next(l for l in out.splitlines() if l.startswith("- parked for demo-repo-57"))
    assert "sid-thread-a" in line and "sid-thread-b" in line and "never by the name" in line
    assert "deliver them" not in line


def test_a_parked_name_for_a_thread_is_deliverable_by_its_id(world, monkeypatch, capsys):
    out = _page(world, monkeypatch, capsys, {"card-9a": 1})
    assert "parked for card-9a (live, session sid-card-thread: deliver them)" in out


def test_a_parked_file_name_is_matched_in_its_safe_form(world, monkeypatch, capsys):
    safe = world.common.safe_sid("demo-repo host")
    recs = more_records() + [rec(300, "sid-spaced", "odd name/with slash",
                                 utc="Mon Sep 28 13:23:15 2026")]
    monkeypatch.setattr(world, "registry_records", lambda: recs)
    v = world.live_view(recs[:2] + recs[-1:], world.parse_process_table(PS))
    stored = world.common.safe_sid("odd name/with slash")
    assert [e["session_id"] for e in world.holders(v, stored)] == ["sid-spaced"]
    assert safe  # the helper exists for every name


def test_a_launched_session_and_its_record_are_listed_once(world, monkeypatch, capsys):
    row = {"name": "cursus-atlas", "pid": 300, "mux": "screen", "mux_session": "cursus-atlas"}
    out = _page(world, monkeypatch, capsys, {}, launches=[row])
    assert out.count("pid 300 ") == 1 and out.count("pid 300") == 1


def test_the_header_counts_what_the_registry_sees(world, monkeypatch, capsys):
    out = _page(world, monkeypatch, capsys, {})
    assert "registry and process table: 2 host(s), 3 thread(s)" in out
