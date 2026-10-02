"""HANDLINES-1 — the safe close: a session is closed only when every safety fact holds.

`fleet.close_refusals` is pure: the process table, the multiplexer's session pids and the process
environment are passed in. One positive control, and one NEGATIVE control per fact, each refused
with its reason. Then the same on a REAL process: `cat` started through a link named `claude`, started in a
detached `screen` of a sandbox name, closed by quitting that screen — the only live process any
test here touches, and it is its own.
"""
from __future__ import annotations
import json, os, random, shutil, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

S = "Tue 29 Sep 20:00:00 2026"
SCREEN_PID, SHELL_PID, PID = 500, 550, 600


@pytest.fixture
def fl(tmp_path, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "Vault"))
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("GEDAECHTNIS_LIMITS", raising=False)
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    import fleet
    return fleet


def table(over=None):
    t = {1: {"ppid": 0, "start": S, "toks": ["/sbin/launchd"]},
         SCREEN_PID: {"ppid": 1, "start": S, "toks": ["SCREEN", "-dmS", "b-1"]},
         PID: {"ppid": SCREEN_PID, "start": S, "toks": ["/usr/local/bin/claude", "--name", "b-1"]}}
    t.update(over or {})
    return t


ROW = {"name": "b-1", "pid": PID, "pid_start": S, "mux": "screen", "mux_session": "b-1"}
ENV = "claude --name b-1 PATH=/usr/bin HOME=/h"


def refusals(fl, row=None, tbl=None, anchors=(SCREEN_PID,), env=ENV, never=("* host",)):
    return fl.close_refusals(dict(ROW, **(row or {})), table() if tbl is None else tbl,
                             None if anchors is None else list(anchors), env, list(never))


def test_positive_control_every_fact_holds(fl):
    assert refusals(fl) == []


def test_a_shell_between_the_session_and_its_screen_is_allowed(fl):
    t = table({SHELL_PID: {"ppid": SCREEN_PID, "start": S, "toks": ["/bin/zsh", "-c", "x"]},
                 PID: {"ppid": SHELL_PID, "start": S, "toks": ["claude", "--name", "b-1"]}})
    assert refusals(fl, tbl=t) == []


@pytest.mark.parametrize("case, kw, word", [
    ("attended", {"env": ENV + " CLAUDE_CODE_SESSION_ATTENDED=1"}, "attended:"),
    ("environment unreadable", {"env": None}, "attended:"),
    ("pid reused: another start time", {"row": {"pid_start": "Mon 28 Sep 09:00:00 2026"}},
     "identity:"),
    ("no start time recorded", {"row": {"pid_start": None}}, "ledger:"),
    ("not launched by the tool", {"row": {"outside": True}}, "ledger:"),
    ("on the never list", {"never": ("b-*",)}, "never:"),
    ("no screen of that exact name", {"anchors": ()}, "screen:"),
    ("the multiplexer unreadable", {"anchors": None}, "screen:"),
    ("no screen recorded", {"row": {"mux": None}}, "screen:"),
    ("pid gone", {"row": {"pid": 4711}}, "process:"),
])
def test_each_missing_fact_refuses_with_its_reason(fl, case, kw, word):
    why = refusals(fl, **kw)
    assert any(w.startswith(word) for w in why), (case, why)


def test_a_host_is_refused_twice_as_a_host_and_by_its_name(fl):
    t = table({PID: {"ppid": SCREEN_PID, "start": S,
                       "toks": ["claude", "remote-control", "--name", "demo", "host", "--spawn",
                                "same-dir"]}})
    why = refusals(fl, tbl=t)
    assert any(w.startswith("host:") for w in why)
    assert any(w.startswith("never:") and "demo host" in w for w in why)


def test_screens_own_login_between_the_screen_and_the_session_is_allowed(fl):
    """Measured on macOS: SCREEN -> `login -pflq <user> <cmd>` -> the window's command."""
    t = table({SHELL_PID: {"ppid": SCREEN_PID, "start": S, "toks": ["login", "-pflq", "u", "/bin/zsh"]},
               PID: {"ppid": SHELL_PID, "start": S, "toks": ["claude", "--name", "b-1"]}})
    assert refusals(fl, tbl=t) == []


def test_a_process_outside_the_screen_is_refused(fl):
    """The same pid and start time, but its parent is a Terminal login, not the launcher's screen."""
    t = table({700: {"ppid": 1, "start": S, "toks": ["login", "-pf", "someone"]},
                 PID: {"ppid": 700, "start": S, "toks": ["claude", "--name", "b-1"]}})
    why = refusals(fl, tbl=t)
    assert any(w.startswith("chain:") and "login" in w for w in why), why


def test_a_process_under_another_screen_is_refused(fl):
    why = refusals(fl, anchors=(999,))
    assert any(w.startswith("chain:") for w in why), why


def test_a_process_that_is_not_claude_is_refused(fl):
    t = table({PID: {"ppid": SCREEN_PID, "start": S, "toks": ["/usr/bin/python3", "x.py"]}})
    assert any(w.startswith("process:") for w in refusals(fl, tbl=t))


def test_an_unreadable_process_table_refuses(fl):
    assert fl.close_refusals(ROW, None, [SCREEN_PID], ENV, []) == [
        "process: the process table could not be read"]


def _two_windows(extra_toks):
    """SCREEN -> login -> zsh -> claude (the launcher's window) AND SCREEN -> login -> zsh -> <x>."""
    return table({SHELL_PID: {"ppid": SCREEN_PID, "start": S, "toks": ["login", "-pflq", "u", "/bin/zsh"]},
                  551: {"ppid": SHELL_PID, "start": S, "toks": ["/bin/zsh", "launch.sh"]},
                  PID: {"ppid": 551, "start": S, "toks": ["claude", "--name", "b-1"]},
                  610: {"ppid": PID, "start": S, "toks": ["/bin/zsh", "-c", "tool"]},   # its own child
                  700: {"ppid": SCREEN_PID, "start": S, "toks": ["login", "-pflq", "u", "/bin/zsh"]},
                  701: {"ppid": 700, "start": S, "toks": ["/bin/zsh"]},
                  702: {"ppid": 701, "start": S, "toks": extra_toks}})


def test_a_second_window_in_the_same_screen_is_refused(fl):
    """MF1: a quit ends EVERY window. A `vim` he opened with Ctrl-a c in the session's screen."""
    why = refusals(fl, tbl=_two_windows(["vim", "notes"]))
    assert any(w.startswith("screen:") and "702 (vim)" in w and "701 (zsh)" in w for w in why), why


def test_one_window_with_the_sessions_own_children_is_not_refused(fl):
    """Positive control for MF1: the launcher's own chain and the session's own children pass."""
    t = _two_windows(["vim"])
    for p in (700, 701, 702):
        del t[p]
    assert refusals(fl, tbl=t) == []


def test_a_second_tmux_pane_is_refused(fl):
    t = table({PID: {"ppid": 1, "start": S, "toks": ["claude", "--name", "b-1"]},
               800: {"ppid": 1, "start": S, "toks": ["/bin/zsh"]}})
    why = refusals(fl, row={"mux": "tmux"}, tbl=t, anchors=(PID, 800))
    assert any(w.startswith("screen:") and "800" in w for w in why), why
    assert refusals(fl, row={"mux": "tmux"}, tbl=t, anchors=(PID,)) == []


def test_a_tmux_pane_pid_is_the_session_itself(fl):
    assert fl.chain_to(PID, table(), [PID]) == (PID, None)


def test_the_shipped_never_list_names_the_hosts(fl):
    import fnmatch
    never = fl.close_never()
    for name in ("demo-repo host", "demo-host", "rc-host", "rc-host-demo"):
        assert any(fnmatch.fnmatchcase(name, p) for p in never), name
    assert not any(fnmatch.fnmatchcase("builder-7", p) for p in never)


# --------------------------------------------------------------- the kill door does not fire ----

@pytest.mark.parametrize("cmd", ["python3 gedaechtnis/tools/sessions.py close",
                                 "python3 gedaechtnis/tools/sessions.py close --dry-run",
                                 "python3 tools/sessions.py close"])
def test_the_kill_door_passes_the_sanctioned_close(cmd):
    import killdoor
    assert killdoor.check(cmd) is None
    assert killdoor.check("pkill -P $(pgrep -o -f x || echo 1)") is not None   # the door is live


# ------------------------------------------------------------------ a real process, closed ----

@pytest.mark.skipif(shutil.which("screen") is None, reason="no screen on this machine")
def test_a_real_launched_session_is_closed_by_its_screen_and_nothing_else(fl, tmp_path):
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    # argv[0], what `ps` prints, is named claude (a COPY of a platform binary is killed at exec by
    # code signing). `cat` reads its terminal, as claude does, so it ends when the screen's pty
    # closes; `sleep` does not, and outlives `screen -X quit` (measured HANDLINES-1).
    fake.symlink_to("/bin/cat")
    name = f"gdsafe-{os.getpid()}-{random.randrange(10**6)}"
    pidfile = tmp_path / "pid"
    env = fl.clean_env()
    env.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
    subprocess.run(["screen", "-dmS", name, "/bin/zsh", "-c",
                    f"echo $$ > {pidfile}; exec {fake}"], env=env, check=True, timeout=10)
    pid = None
    try:
        for _ in range(50):
            if pidfile.exists() and pidfile.read_text().strip():
                pid = int(pidfile.read_text())
                break
            time.sleep(0.1)
        assert pid, "the sandbox screen never wrote its pid"
        time.sleep(0.3)
        row = {"name": name, "pid": pid, "pid_start": fl.pid_start(pid), "mux": "screen",
               "mux_session": name}
        why, anchor = fl.live_refusals(row)
        assert why == [] and anchor, why                      # positive control, on real facts
        assert fl.live_refusals(dict(row, pid_start="Mon 1 Jan 00:00:00 2001"))[0]   # reused pid
        assert fl.live_refusals(dict(row, mux_session=name + "-x"))[0]               # other screen
        assert fl.close_one(row, "screen", anchor, wait_s=10) == "screen quit"
        assert not fl.same_process(pid, row["pid_start"])
    finally:
        subprocess.run(["screen", "-S", name, "-X", "quit"], capture_output=True, timeout=10)


# ------------------------------------------------------------------ the policy table ----

def test_every_shipped_key_has_a_class_and_the_classes_are_the_policys():
    doc = json.loads((HOOKS.parent / "rules" / "limits.json").read_text())
    classes = doc["_key_class"]
    keys = {k for k in doc if not k.startswith("_")}
    assert keys == set(classes), keys ^ set(classes)
    allowed = {"live", "owner:money", "owner:irreversible", "owner:learner-data", "owner:routing",
               "owner:ruled"}
    assert set(classes.values()) <= allowed, set(classes.values()) - allowed
    for k in ("compaction_apply", "worktree_sweep_apply", "kernel_entry_deny_from"):
        assert classes[k].startswith("owner:"), k
    for k in ("session_close_apply", "context_reach_margin_tokens", "resume_short_chars"):
        assert classes[k] == "live", k


def test_the_contextmsg_values_ship_live():
    import limits
    d = limits.DEFAULTS
    assert (d["context_reach_margin_tokens"], d["resume_short_chars"], d["resume_short_window"],
            d["session_close_apply"]) == (20000, 1500, 400000, True)


def test_a_tmux_pane_missing_from_the_table_is_refused_not_raised(fl):
    """Fix review OPTIONAL: a pane opened between the table read and list-panes."""
    t = table({PID: {"ppid": 1, "start": S, "toks": ["claude", "--name", "b-1"]}})
    why = refusals(fl, row={"mux": "tmux"}, tbl=t, anchors=(PID, 800))
    assert any(w.startswith("screen:") and "800 (?)" in w for w in why), why
