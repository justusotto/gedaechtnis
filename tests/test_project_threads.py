"""PROJECTDESK-1 — which live THREADs are real Project threads.

A Project thread's registry `bridgeSessionId` (`session_01X…`) and its Project id (`cse_01X…`)
share the suffix (measured 2026-09-28); a host also spawns sessions for itself within seconds of
its own start. The id decides, never the name.
"""
from __future__ import annotations

from test_sessions_ls import PS, by_pid, fl, rec, records, world  # noqa: F401 — shared fixtures

# One more session of host 100, started 15 s after the host: the host's own.
PS_OWN = PS + ("  250   100 Mon 28 Sep 13:02:40 2026     /x/claude.exe --print "
               "--sdk-url https://x/v1/code/sessions/cse_01OWN --session-id cse_01OWN\n")


def recs():
    return records() + [rec(210, "sid-card-thread", "card-9a", utc="Mon Sep 28 18:40:00 2026"),
                        rec(250, "sid-own", "demo-repo-2", utc="Mon Sep 28 11:02:40 2026",
                            bridgeSessionId="session_01OWN")]


def verdicts(world, ids, ps=PS_OWN, rs=None):
    t = world.parse_process_table(ps)
    return {e["pid"]: e["verdict"]
            for e in world.project_threads(world.live_view(rs or recs(), t), t, ids)}


def test_thread_ids_read_both_prefixes_from_any_text(world):
    text = '[{"session_id": "cse_01A", "title": "x"}, "session_01B", "nope_01C", "cse_"]'
    assert world.thread_ids(text) == {"01A", "01B"}
    assert world.thread_ids("") == set() and world.thread_ids(None) == set()


def test_a_thread_in_the_projects_list_is_a_project_thread(world):
    v = verdicts(world, world.thread_ids("cse_01A"))
    assert v[200] == "PROJECT"
    assert v[201] == "UNCONFIRMED"          # same name as 200, not in the list
    assert v[250] == "HOST-OWN"
    assert v[210] == "UNCONFIRMED"          # host 110, no id anywhere, started late


def test_only_threads_are_judged(world):
    v = verdicts(world, world.thread_ids("cse_01A cse_01B"))
    assert set(v) == {200, 201, 210, 250}   # no HOST, SESSION, FORK or BLIND


def test_the_command_line_id_counts_when_the_record_has_none(world):
    rs = [dict(r, bridgeSessionId=None) if r["pid"] == 200 else r for r in recs()]
    assert verdicts(world, {"01A"}, rs=rs)[200] == "PROJECT"


def test_the_record_id_counts_when_the_command_line_has_none(world):
    ps = PS_OWN.replace("--sdk-url https://x/cse_01A --session-id cse_01A", "--print")
    assert verdicts(world, {"01A"}, ps=ps)[200] == "PROJECT"


def test_a_name_never_decides(world):
    # The thread's name and session id are in the set given to the judge; its remote id is not.
    assert verdicts(world, {"demo-repo-57", "sid-thread-a", "01ZZZ"})[200] == "UNCONFIRMED"
    # …and a name in a pasted list never becomes an id.
    assert world.thread_ids("demo-repo-57 cse_01ZZZ") == {"01ZZZ"}


def test_a_listed_thread_is_project_even_if_it_started_with_its_host(world):
    assert verdicts(world, {"01OWN"})[250] == "PROJECT"


def _started(world, hms):
    """Thread 250 started at local `hms` (its record's UTC start moved with it)."""
    ps = PS_OWN.replace("13:02:40 2026     /x/claude.exe", f"{hms} 2026     /x/claude.exe")
    h, m, sec = hms.split(":")
    rs = [dict(r, procStart=f"Mon Sep 28 {int(h) - 2:02d}:{m}:{sec} 2026") if r["pid"] == 250
          else r for r in recs()]
    return verdicts(world, set(), ps=ps, rs=rs)[250]


def test_the_host_own_window_is_bounded_on_both_sides(world):
    # the host started at 13:02:25
    assert _started(world, "13:02:55") == "HOST-OWN"        # 30 s: still the host's own
    assert _started(world, "13:02:56") == "UNCONFIRMED"     # 31 s
    assert _started(world, "13:02:25") == "HOST-OWN"        # the same second
    assert _started(world, "13:02:20") == "UNCONFIRMED"     # before its host: not its child's start


def test_projects_prints_a_line_per_thread_and_says_when_no_list_was_given(world, monkeypatch,
                                                                             capsys, tmp_path):
    import sessions
    f = world
    monkeypatch.setattr(f, "registry_records", recs)
    monkeypatch.setattr(f, "process_table", lambda: f.parse_process_table(PS_OWN))
    lst = tmp_path / "threads.json"
    lst.write_text('{"sessions": [{"session_id": "cse_01A", "title": "Codex live"}]}')
    assert sessions.main(["projects", "--ids", str(lst)]) == 0
    out = capsys.readouterr().out
    assert "4 live thread(s) under a Remote Control host · 1 Project thread id(s) given" in out
    assert "- PROJECT session sid-thread-a · pid 200 · host demo-repo host · remote 01A" in out
    assert "- HOST-OWN session sid-own · pid 250" in out
    assert "- UNCONFIRMED session sid-thread-b · pid 201" in out
    assert "no Project thread list given" not in out
    assert sessions.main(["projects"]) == 0
    assert "no Project thread list given" in capsys.readouterr().out


def test_projects_refuses_without_a_process_table_or_a_readable_list(world, monkeypatch, capsys,
                                                                     tmp_path):
    import sessions
    monkeypatch.setattr(world, "registry_records", recs)
    monkeypatch.setattr(world, "process_table", lambda: None)
    assert sessions.main(["projects", "--ids-text", "cse_01A"]) == 2
    monkeypatch.setattr(world, "process_table", lambda: world.parse_process_table(PS_OWN))
    assert sessions.main(["projects", "--ids", str(tmp_path / "missing.txt")]) == 2
