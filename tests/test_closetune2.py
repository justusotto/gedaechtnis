"""CLOSETUNE-2 — the safe close also covers sessions started by hand in a screen window.

A session `sessions.py launch` did not start is admissible when the CLI's registry record
(`<pid>.json`) names its pid AND a `procStart` within 1 s of the live process's start, its entrypoint
is `cli` (never Desktop), and it runs in its own screen window. Everything else is unchanged: never a
terminal window, a host, a `session_close_never` name, an attended session, a screen holding anything
foreign — and never one the owner typed into after its first prompt (a HARD keep for these rows).

One positive control end to end, and one negative control per fact. Nothing here reads the machine's
real sessions, ledger or state: the state, config and sessions dirs are redirected into tmp_path,
every process fact is injected, and no process is signalled — the close itself is a recorder.
"""
from __future__ import annotations
import importlib.util, json, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HOOKS))

NOW = 1_800_000_000.0
H = 3600.0
S = "Tue 29 Sep 20:00:00 2026"                       # the start `ps -o lstart` prints (local time)


def utc_of(local_ps: str, shift_s: float = 0.0) -> str:
    """The registry's `procStart` for a process `ps` says started at `local_ps` (UTC, CLI order)."""
    t = time.mktime(time.strptime(local_ps, "%a %d %b %H:%M:%S %Y")) + shift_s
    return time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime(t))


SCREEN_PID, LOGIN_PID, SHELL_PID, PID = 810, 811, 812, 800
ENV = "claude HOME=/h STY=810.h-1"
ROW = {"name": "h-1", "pid": PID, "pid_start": S, "reg_start": utc_of(S), "reg_file_pid": PID,
       "entrypoint": "cli", "mux": "screen", "mux_session": "h-1", "outside": True, "named": True,
       "cwd": "/r", "sid": "0000-h1", "launched_at": NOW - 40 * H}


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


def table(over=None):
    """SCREEN -> login -> zsh -> claude: a window he opened with `screen -S h-1` and typed `claude`."""
    t = {1: {"ppid": 0, "start": S, "toks": ["/sbin/launchd"]},
         SCREEN_PID: {"ppid": 1, "start": S, "toks": ["SCREEN", "-S", "h-1"]},
         LOGIN_PID: {"ppid": SCREEN_PID, "start": S, "toks": ["login", "-pflq", "u", "/bin/zsh"]},
         SHELL_PID: {"ppid": LOGIN_PID, "start": S, "toks": ["-zsh"]},
         PID: {"ppid": SHELL_PID, "start": S, "toks": ["claude"]}}
    t.update(over or {})
    return t


def refusals(fl, row=None, tbl=None, anchors=(SCREEN_PID,), env=ENV, never=("* host", "rc-host*")):
    return fl.close_refusals(dict(ROW, **(row or {})), table() if tbl is None else tbl,
                             None if anchors is None else list(anchors), env, list(never))


def end(**over):
    e = {"ts": NOW - 4 * H, "text": "Done.", "open_bg": [], "open_bg_ts": {}, "began": NOW - 5 * H,
         "sent": [], "wake_until": 0.0, "cron": 0, "writes": {}, "reports": [], "humans": 1}
    e.update(over)
    return e


def facts(**over):
    f = {"is_self": False, "alive": True, "handoff": False, "handoff_done": None, "seat": None,
         "status": "idle", "scanned": True, "end": end(), "screen": "❯ \n  ? for shortcuts",
         "attached": False, "unsaved": [], "wrote": [], "outside": True, "mux": "screen",
         "exempt": False}
    f.update(over)
    return f


# ------------------------------------------------------------------ the admission, pure ----

def test_positive_control_a_registry_known_screen_window_session_is_admitted(fl):
    assert fl.registry_refusals(ROW, table()) == []
    assert refusals(fl) == []


def test_the_start_tolerance_is_one_second(fl):
    assert fl.registry_start_matches(utc_of(S, 1), S)
    assert fl.registry_start_matches(utc_of(S, -1), S)
    assert not fl.registry_start_matches(utc_of(S, 2), S)
    assert not fl.registry_start_matches(None, S) and not fl.registry_start_matches(utc_of(S), None)
    assert not fl.registry_start_matches("not a time", S)


@pytest.mark.parametrize("case, row, word", [
    ("pid mismatch: the record names another pid than its file", {"reg_file_pid": 4711}, "registry:"),
    ("pid not a number", {"pid": "800"}, "registry:"),
    ("start-time mismatch: a reused pid", {"reg_start": utc_of(S, 60)}, "registry:"),
    ("no procStart in the record", {"reg_start": None}, "registry:"),
    ("a Desktop session", {"entrypoint": "claude-desktop"}, "desktop:"),
    ("a Remote Control thread (sdk-cli)", {"entrypoint": "sdk-cli"}, "registry:"),
    ("no entrypoint", {"entrypoint": None}, "registry:"),
    ("a terminal window: no screen", {"mux": None, "mux_session": None}, "window:"),
    ("a tmux pane is not a screen window", {"mux": "tmux"}, "window:"),
])
def test_negative_each_registry_fact_refuses_with_its_reason(fl, case, row, word):
    why = refusals(fl, row=row)
    assert why, case
    assert why[0].startswith("ledger: not launched by `sessions.py launch`"), why
    assert any(w.startswith(word) for w in why), (case, why)


def test_negative_a_record_with_no_registry_fields_stays_refused_as_before(fl):
    bare = {"name": "x", "pid": PID, "pid_start": S, "mux": "screen", "mux_session": "h-1",
            "outside": True}
    why = fl.close_refusals(bare, table(), [SCREEN_PID], ENV, [])
    assert "ledger: not launched by `sessions.py launch` — closing it is the owner's" in why


@pytest.mark.parametrize("case, kw, word", [
    ("a terminal window: its parent chain is a Terminal login",
     {"tbl": table({SHELL_PID: {"ppid": 900, "start": S, "toks": ["-zsh"]},
                    900: {"ppid": 1, "start": S, "toks": ["login", "-pf", "u"]}})}, "chain:"),
    ("a host", {"tbl": table({PID: {"ppid": SHELL_PID, "start": S,
                                    "toks": ["claude", "remote-control", "--name", "demo", "host"]}})},
     "host:"),
    ("a host's thread: the host sits between it and the screen",
     {"tbl": table({850: {"ppid": SHELL_PID, "start": S, "toks": ["claude", "remote-control"]},
                    PID: {"ppid": 850, "start": S, "toks": ["claude.exe", "--sdk-url", "x"]}})},
     "chain:"),
    ("on the never list", {"row": {"name": "rc-host-x", "mux_session": "rc-host-x"}}, "never:"),
    ("attended", {"env": ENV + " CLAUDE_CODE_SESSION_ATTENDED=1"}, "attended:"),
    ("another window in its screen",
     {"tbl": table({700: {"ppid": SCREEN_PID, "start": S, "toks": ["login", "-pflq", "u", "/bin/zsh"]},
                    701: {"ppid": 700, "start": S, "toks": ["vim", "notes.txt"]}})}, "screen:"),
    ("no screen of that exact name", {"anchors": ()}, "screen:"),
    ("pid gone", {"tbl": {k: v for k, v in table().items() if k != PID}}, "process:"),
    ("ps start differs from the recorded identity", {"row": {"pid_start": "Mon 28 Sep 09:00:00 2026"}},
     "identity:"),
])
def test_negative_every_old_safety_fact_still_refuses_an_admitted_row(fl, case, kw, word):
    why = refusals(fl, **kw)
    assert any(w.startswith(word) for w in why), (case, why)


# ------------------------------------------------------------- the time rules, and typed-into ----

def test_negative_a_hand_started_session_he_typed_into_is_a_hard_keep_however_old(fl):
    keep = fl.classify(facts(end=end(ts=NOW - 30 * H, humans=2)), NOW, 180.0)
    assert any(k.startswith("conversation:") and "his to close" in k for k in keep), keep


def test_the_same_fact_on_a_ledger_row_is_still_the_soft_12h_keep(fl):
    assert fl.classify(facts(outside=False, end=end(ts=NOW - 30 * H, humans=2)), NOW, 180.0) == []


def test_negative_a_young_hand_started_session_is_kept(fl):
    keep = fl.classify(facts(end=end(ts=NOW - 600)), NOW, 180.0)
    assert any(k.startswith("finished:") for k in keep), keep


def test_negative_a_hand_started_session_outside_a_screen_is_kept(fl):
    keep = fl.classify(facts(mux=None), NOW, 180.0)
    assert any(k.startswith("window:") for k in keep), keep


def test_positive_a_finished_hand_started_session_is_finished_by_time(fl):
    assert fl.classify(facts(), NOW, 180.0) == []


# ---------------------------------------------------- reading the registry: outside_sessions ----

def _record(tmp_path, pid=PID, raw_pid=None, **over):
    d = {"pid": pid if raw_pid is None else raw_pid, "sessionId": "0000-h1", "cwd": "/r", "startedAt": int((NOW - 40 * H) * 1000),
         "procStart": utc_of(S), "kind": "interactive", "entrypoint": "cli", "name": "h-1"}
    d.update(over)
    (tmp_path / "sessions").mkdir(exist_ok=True)
    (tmp_path / "sessions" / f"{pid}.json").write_text(json.dumps(d))


def test_outside_sessions_reads_pid_start_entrypoint_and_window(fl, tmp_path, monkeypatch):
    _record(tmp_path)
    monkeypatch.setattr(fl, "pid_start", lambda pid: S)
    monkeypatch.setattr(fl, "screen_of", lambda pid: "h-1")
    rows = fl.outside_sessions([])
    assert len(rows) == 1
    r = rows[0]
    assert (r["pid"], r["reg_file_pid"], r["entrypoint"], r["mux"], r["mux_session"]) == \
        (PID, PID, "cli", "screen", "h-1")
    assert r["pid_start"] == S and r["reg_start"] == utc_of(S)
    assert fl.registry_refusals(r, table()) == []


def test_negative_a_start_mismatch_keeps_the_registry_identity_so_the_process_reads_as_another(
        fl, tmp_path, monkeypatch):
    _record(tmp_path, procStart=utc_of(S, 3600))
    monkeypatch.setattr(fl, "pid_start", lambda pid: S)
    monkeypatch.setattr(fl, "screen_of", lambda pid: "h-1")
    r = fl.outside_sessions([])[0]
    assert r["pid_start"] != S
    assert any(w.startswith("identity:") for w in fl.close_refusals(r, table(), [SCREEN_PID], ENV, []))


# --------------------------------------------------------------------- end to end, recorded ----

class Fake:
    """One hand-started session in its screen, beside nothing else; the close is recorded only."""

    def __init__(self, fl, row=None, tbl=None, e=None):
        self.fl, self.closed = fl, []
        self.row = dict(ROW, **(row or {}))
        self.tbl = tbl or table()
        self.e = e or end(ts=NOW - 4 * H)

    def gather(self, r, mux, me):
        return facts(end=self.e)

    def refusals(self, r):
        why = self.fl.close_refusals(r, self.tbl, [SCREEN_PID], ENV, ["* host", "rc-host*"])
        return why, (None if why else SCREEN_PID)

    def close(self, r, mux, anchor):
        self.closed.append((r["name"], anchor))
        return "screen quit"

    def run(self, apply=True):
        fl = self.fl
        return fl.close_when_needed(
            apply=apply, by="test", settle_s=0, rows=[self.row],
            readings_fn=lambda: {"swap": 1.0, "swap_mb": 9000.0 - 5000.0 * len(self.closed),
                                 "level": 50.0},
            sweep_fn=lambda **kw: fl.sweep(now=NOW, gather_fn=self.gather,
                                           refusals_fn=self.refusals, **kw),
            apply_fn=lambda i, by: fl.apply_item(i, by=by, gather_fn=self.gather,
                                                 refusals_fn=self.refusals, close_fn=self.close,
                                                 clock=lambda: NOW))


@pytest.fixture
def alive(fl, monkeypatch):
    monkeypatch.setattr(fl, "same_process", lambda pid, start: True)
    monkeypatch.setattr(fl, "multiplexer", lambda: "screen")
    return fl


def test_positive_end_to_end_a_finished_registry_known_screen_session_closes_and_is_logged(
        alive, tmp_path):
    f = Fake(alive)
    res = f.run()
    assert f.closed == [("h-1", SCREEN_PID)]
    assert [i["name"] for i in res["closed"]] == ["h-1"]
    assert res["closed"][0]["admission"] == "registry: pid + start time match, screen window"
    log = (tmp_path / "state" / "close.log").read_text()
    assert "\tclosed\th-1\tpid 800\t" in log and "claude -r h-1" in log


@pytest.mark.parametrize("case, kw, word", [
    ("pid mismatch", {"row": {"reg_file_pid": 4711}}, "registry:"),
    ("start-time mismatch", {"row": {"reg_start": utc_of(S, 60)}}, "registry:"),
    ("terminal window", {"row": {"mux": None, "mux_session": None}}, "window:"),
    ("Desktop", {"row": {"entrypoint": "claude-desktop"}}, "desktop:"),
    ("host", {"tbl": table({PID: {"ppid": SHELL_PID, "start": S,
                                  "toks": ["claude", "remote-control", "--name", "demo", "host"]}})},
     "host:"),
    ("typed-into", {"e": end(ts=NOW - 30 * H, humans=3)}, "conversation:"),
    ("young", {"e": end(ts=NOW - 600)}, "finished:"),
])
def test_negative_end_to_end_nothing_is_closed(alive, case, kw, word):
    f = Fake(alive, **kw)
    res = f.run()
    assert f.closed == [], case
    item = res["items"][0]
    assert any(w.startswith(word) for w in item["keep"] + item["refused"]), (case, item)


def test_the_dry_run_names_the_new_admission(alive, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("sessions_ct2", TOOLS / "sessions.py")
    s = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s)
    f = Fake(alive)
    real = alive.sweep
    monkeypatch.setattr(s.fleet, "sweep", lambda **kw: real(
        now=NOW, gather_fn=f.gather, refusals_fn=f.refusals, rows=[f.row],
        **{k: v for k, v in kw.items() if k != "rows"}))
    monkeypatch.setattr(s.fleet, "record_proposals", lambda items: None)
    assert s.main(["close", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would close  h-1 (pid 800): finished; registry: pid + start time match, screen window" in out
    assert f.closed == []


# ---------------------------------------------------- review fixes (CLOSETUNE-2 high review) ----

def _transcript(tmp_path, stem, sid, humans=1, title=None, age_h=30.0):
    """A finished transcript: `humans` typed prompts, its last turn ended `age_h` hours before NOW."""
    from datetime import datetime, timezone
    ts = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
    t0 = NOW - age_h * H
    recs = [{"type": "custom-title", "customTitle": title}] if title else []
    for k in range(humans):
        recs.append({"type": "user", "sessionId": sid, "timestamp": ts(t0 - 60 + k),
                     "message": {"role": "user", "content": f"prompt {k}"}})
    recs.append({"type": "assistant", "sessionId": sid, "timestamp": ts(t0),
                 "message": {"role": "assistant", "stop_reason": "end_turn",
                             "content": [{"type": "text", "text": "Done."}]}})
    d = tmp_path / "projects" / "-r"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")


@pytest.fixture
def quiet(fl, monkeypatch):
    """gather() with every machine read faked: no ps, no screen, no git."""
    monkeypatch.setattr(fl, "same_process", lambda pid, start: True)
    monkeypatch.setattr(fl, "registry_status", lambda pid: "idle")
    monkeypatch.setattr(fl, "read_screen", lambda m, n: "❯ \n  ? for shortcuts")
    monkeypatch.setattr(fl, "screen_attached", lambda n: False)
    monkeypatch.setattr(fl, "session_unsaved", lambda cwd, wt, w: [])
    return fl


def test_mf1_positive_its_own_transcript_by_session_id_carries_the_typed_into_fact(quiet, tmp_path):
    _transcript(tmp_path, "sid-A", "sid-A", humans=2, title="h-1")
    f = quiet.gather(dict(ROW, sid="sid-A"), "screen", None)
    assert f["scanned"] and f["end"]["humans"] == 2
    assert any(k.startswith("conversation:") for k in quiet.classify(f, NOW, 180.0))


def test_mf1_negative_no_name_fallback_to_another_transcript_with_the_same_title(quiet, tmp_path):
    """Its own transcript is missing; ANOTHER one titled `h-1` with one prompt must not stand in."""
    _transcript(tmp_path, "sid-OTHER", "sid-OTHER", humans=1, title="h-1")
    f = quiet.gather(dict(ROW, sid="sid-A"), "screen", None)
    assert not f["scanned"]
    assert any(k.startswith("transcript:") for k in quiet.classify(f, NOW, 180.0))


def test_mf1_negative_a_file_whose_records_name_another_session_is_not_its_transcript(quiet, tmp_path):
    _transcript(tmp_path, "sid-A", "sid-B", humans=1)
    f = quiet.gather(dict(ROW, sid="sid-A"), "screen", None)
    assert not f["scanned"]


def test_mf1_a_ledger_row_still_uses_the_name_fallback(quiet, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(quiet, "transcript_for", lambda pid, name, at: seen.append(name) or None)
    quiet.gather(dict(ROW, outside=False, sid=None), "screen", None)
    assert seen == ["h-1"]


@pytest.mark.parametrize("raw, listed", [(PID, True), (str(PID), True), (float(PID), False),
                                         (PID + 0.9, False), (True, False), ("8o0", False),
                                         ("²", False), ("٨٠٠", False)])
def test_mf2_outside_sessions_reads_the_pid_strictly(fl, tmp_path, monkeypatch, raw, listed):
    _record(tmp_path, pid=PID, raw_pid=raw)
    monkeypatch.setattr(fl, "pid_start", lambda pid: S)
    monkeypatch.setattr(fl, "screen_of", lambda pid: "h-1")
    rows = fl.outside_sessions([])
    assert bool(rows) == listed, rows
    if listed:
        assert rows[0]["pid"] == PID and fl.registry_refusals(rows[0], table()) == []


@pytest.mark.parametrize("raw", [800.0, True, "800x", None])
def test_mf2_registry_refusals_type_checks_the_raw_record_pid(fl, raw):
    why = fl.registry_refusals(dict(ROW, reg_pid=raw), table())
    assert any(w.startswith("registry:") and "names pid" in w for w in why), why


def test_opt3_close_when_needed_with_no_rows_gathers_the_registry_sessions_too(fl, monkeypatch):
    monkeypatch.setattr(fl, "current_launches", lambda rows=None: [])
    monkeypatch.setattr(fl, "outside_sessions", lambda rows=None: [dict(ROW)])
    monkeypatch.setattr(fl, "same_process", lambda pid, start: True)
    got = []
    fl.close_when_needed(apply=False, by="t", all_finished=True,
                         readings_fn=lambda: {"swap": 0.0, "swap_mb": 0.0, "level": 90.0},
                         sweep_fn=lambda **kw: got.extend(kw["rows"]) or [])
    assert [r["name"] for r in got] == ["h-1"]


def test_opt4_procstart_is_utc_whatever_the_machine_zone(fl, monkeypatch):
    """`utc_of` depends on local time, so pin a zone that is NOT UTC: a local-time parse of the
    registry's UTC field would then be 1-2 h off and fail."""
    monkeypatch.setenv("TZ", "Europe/Berlin")
    time.tzset()
    try:
        assert fl.registry_start_matches("Tue Sep 29 18:00:00 2026", "Tue 29 Sep 20:00:00 2026")
        assert not fl.registry_start_matches("Tue Sep 29 20:00:00 2026", "Tue 29 Sep 20:00:00 2026")
    finally:
        monkeypatch.undo()
        time.tzset()


def test_fixreview_a_ledger_row_still_scans_its_transcript_found_by_name(quiet, tmp_path, monkeypatch):
    """The sid check is for registry rows only: a ledger row with no sid keeps its by-name scan."""
    _transcript(tmp_path, "sid-L", "sid-L", humans=1, title="h-1")
    monkeypatch.setattr(quiet, "transcript_for",
                        lambda pid, name, at: tmp_path / "projects" / "-r" / "sid-L.jsonl")
    f = quiet.gather(dict(ROW, outside=False, sid=None), "screen", None)
    assert f["scanned"]


def test_fixreview_one_malformed_record_does_not_stop_the_pass(fl, tmp_path, monkeypatch):
    _record(tmp_path, pid=PID)
    (tmp_path / "sessions" / "801.json").write_text(json.dumps({"pid": "²", "kind": "interactive"}))
    monkeypatch.setattr(fl, "pid_start", lambda pid: S)
    monkeypatch.setattr(fl, "screen_of", lambda pid: "h-1")
    assert [r["pid"] for r in fl.outside_sessions([])] == [PID]
