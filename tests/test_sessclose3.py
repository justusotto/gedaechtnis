"""SESSCLOSE-3 (2026-10-06) — finished sessions close by themselves.

The owner's order of 2026-10-03: a session that has finished and reported closes soon by itself, a
launch never waits on memory, and he runs no close command. Four causes were measured on the day:
  1. the launch guard and the closer read different figures (the guard refused, the closer said
     "under the line");
  2. a file the session wrote and that was COMMITTED still counted as its unsaved work once
     somebody else changed it;
  3. a report sent to the seat's socket address was not seen as a report, and a wait loop left in
     the background kept a session that had reported, for days;
  4. a launch into a folder the CLI does not trust sat at the trust question unseen.
Each rule below has a positive and a negative control. Nothing here reads the machine's sessions,
processes, screens or memory: every reading is made up.
"""
from __future__ import annotations
import importlib.util, json, os, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HOOKS))
import closerules as cr  # noqa: E402

NOW = 1_800_000_000.0
IDLE = 180.0
M = 60.0
SOCK = "uds:/tmp/cc-socks/4242.sock"


@pytest.fixture
def fl(tmp_path, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "Vault"))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cli"))
    monkeypatch.delenv("GEDAECHTNIS_LIMITS", raising=False)
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    import fleet
    return fleet


def end(age_min=90.0, **over):
    e = {"ts": NOW - age_min * M, "text": "Reported.", "open_bg": [], "open_bg_ts": {},
         "began": NOW - age_min * M - 600, "sent": [], "wake_until": 0.0, "cron": 0, "writes": {},
         "reports": [], "humans": 1}
    e.update(over)
    return e


def sent(to):
    return [{"to": to, "ts": NOW - 91 * M}]


def facts(**over):
    f = {"is_self": False, "alive": True, "handoff": False, "handoff_done": None, "seat": "seat-a",
         "seat_addrs": [SOCK], "status": "idle", "scanned": True, "end": end(sent=sent("seat-a")),
         "screen": "❯ \n  ? for shortcuts", "attached": False, "unsaved": [], "wrote": [],
         "outside": False, "mux": "screen", "exempt": False, "children": [], "work": [],
         "report_min": 30, "stale_shell_min": 60, "abandoned_min": 1440}
    f.update(over)
    return f


def _git(*a, cwd, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    if when:
        env.update(GIT_AUTHOR_DATE=f"@{int(when)} +0000", GIT_COMMITTER_DATE=f"@{int(when)} +0000")
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env=env)


# ------------------------------------------------------ 3. a report to the seat, by its socket ----

def test_positive_a_report_to_the_seats_socket_is_a_report():
    v = cr.finished(end(40, sent=sent(SOCK)), NOW, IDLE, seat="seat-a", seat_addrs=[SOCK])
    assert v["how"] and "sent its report to its seat" in v["how"] and v["said"] and not v["hard"]


def test_negative_a_message_to_another_socket_or_with_no_seat_address_is_not_a_report():
    other = cr.finished(end(40, sent=sent("uds:/tmp/cc-socks/9.sock")), NOW, IDLE, seat="seat-a",
                        seat_addrs=[SOCK])
    assert other["how"] is None and not other["said"]
    unknown = cr.finished(end(40, sent=sent(SOCK)), NOW, IDLE, seat="seat-a")
    assert unknown["how"] is None and not unknown["said"]
    assert cr.finished(end(40, sent=sent(SOCK)), NOW, IDLE)["said"] is False


def test_seat_addresses_come_from_the_registry_records_named_like_the_seat(fl):
    recs = [{"name": "seat-a", "pid": 4242, "messagingSocketPath": "/tmp/cc-socks/4242.sock"},
            {"name": "seat-b", "pid": 7, "messagingSocketPath": "/tmp/cc-socks/7.sock"},
            {"name": "seat-a", "pid": 8}, "not a record"]
    assert fl.seat_addresses("seat-a", recs) == [SOCK]
    assert fl.seat_addresses(None, recs) == [] and fl.seat_addresses("nobody", recs) == []


def test_the_quiet_after_a_report_is_the_key_not_a_constant(fl, tmp_path, monkeypatch):
    e = end(12, sent=sent("seat-a"))
    assert cr.finished(e, NOW, IDLE, seat="seat-a", report_min=10)["how"]
    assert cr.finished(e, NOW, IDLE, seat="seat-a", report_min=15)["how"] is None      # negative
    assert cr.finished(e, NOW, IDLE, seat="seat-a")["how"] is None                     # default 30
    assert cr.live_moment(e, IDLE, None, "seat-a", report_min=10) == e["ts"] + 600
    assert fl.report_minutes() == 30
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"limits": {"session_close_report_minutes": 10,
                                          "session_close_stale_shell_minutes": "x"}}))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    assert fl.report_minutes() == 10 and fl.stale_shell_minutes() == 60     # not a number → shipped


# ------------------------------------------------------------- 3. the wait loop left behind ----

BORN, LOOP_AT = NOW - 400 * M, NOW - 300 * M      # the report (`sent`) is at NOW - 91 min


def _table(kids, at=LOOP_AT):
    """pid 100 = the session; kids: [(pid, ppid, command)], all started at `at`."""
    def stamp(t):
        return time.strftime("%a %d %b %H:%M:%S %Y", time.localtime(t))
    t = {100: {"ppid": 50, "start": stamp(BORN), "toks": ["claude", "--name", "x"]}}
    for pid, ppid, cmd in kids:
        t[pid] = {"ppid": ppid, "start": stamp(at), "toks": cmd.split()}
    return t


def _w(waits, at=LOOP_AT, idle=None):
    return [{"label": "pid 201 (zsh)", "waits": waits, "started": at,
             "idle": waits if idle is None else idle}]


LOOP = [(201, 100, "/bin/zsh -c until ssh mini true; do sleep 10; done"), (202, 201, "sleep 10")]


def test_own_work_tells_a_wait_loop_from_work(fl):
    assert fl.own_work(100, _table(LOOP)) == _w(True)
    fork = LOOP + [(203, 201, LOOP[0][2])]                 # the loop's own `$(…)` fork: same command
    assert fl.own_work(100, _table(fork)) == _w(True)
    for work in ("/opt/py/bin/Python -m pytest -q", "ssh mini grep done runner.log",
                 "/opt/browser/bin/browser page.html"):
        t = _table([(201, 100, LOOP[0][2]), (202, 201, work)])
        assert fl.own_work(100, t) == _w(False), work


def _day_facts(fl, kids, age_min=1500.0, at=None, **over):
    """A session that never reported, last turn `age_min` ago, its shell started AFTER that."""
    work = fl.own_work(100, _table(kids, at=NOW - 1400 * M if at is None else at))
    f = facts(status="shell", work=work, children=[w["label"] for w in work], unsaved=[],
              end=end(age_min, open_bg=["bg1"], open_bg_ts={"bg1": NOW - 1400 * M}))
    f.update(over)
    return f


# ------------------------------------------------- 3b. a process that has stood still (MACFIX-1B) ----

HUNG = [(201, 100, "/bin/zsh -c for f in a b; do grep -m2 x; done | tr x y"), (202, 201, "/bin/zsh -c for f in a b"),
        (203, 202, "ugrep -G -m2 -E ^(model|effort):"), (204, 202, "tr \\n  ")]
_APP = "/Applications/" + "Google Chrome.app"               # built from parts: a fixture string,
                                                              # never a real browser launcher (QUIETMAC-1)
CHROME = _APP + "/Contents/MacOS/Google Chrome --headless=new --user-data-dir=/private/tmp/x/prof"
HELPER = (_APP + "/Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/"
          "Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper --type=gpu-process")
BROWSER = [(201, 100, "/bin/zsh -c cd /private/tmp/x && \"" + _APP + "/Contents/MacOS/Google Chrome\" --headless=new"),
           (202, 201, CHROME), (203, 202, HELPER)]
DAY = 1440 * M


def _comm(table):
    """What `ps -o comm=` prints: the executable, which for Chrome has spaces in it."""
    out = {}
    for pid, proc in table.items():
        text = " ".join(proc["toks"])
        out[pid] = text.split(" --", 1)[0] if text.startswith(_APP) else proc["toks"][0]
    return out


def _still(fl, kids, cpu0, cpu1, gap=DAY + 60, kids1=None, mid=True):
    """Three readings: `gap` seconds ago, halfway (unless `mid` is False), and at NOW."""
    t0, t1 = _table(kids), _table(kids if kids1 is None else kids1)
    import config
    (config.state() / fl.CHILD_STILL).unlink(missing_ok=True)             # each case from nothing
    fl.still_since(fl.own_work(100, t0), t0, NOW - gap, cpu=cpu0, comm=_comm(t0))
    if mid:
        fl.still_since(fl.own_work(100, t0), t0, NOW - gap / 2, cpu=cpu0, comm=_comm(t0))
    work = fl.own_work(100, t1)
    return facts(status="shell", work=work, children=[w["label"] for w in work], unsaved=[],
                 still=fl.still_since(work, t1, NOW, cpu=cpu1, comm=_comm(t1)), child_still_min=1440,
                 end=end(3000.0, open_bg=["bg1"], open_bg_ts={"bg1": NOW - 2900 * M}))


def test_positive_a_hung_command_and_an_idle_browser_stop_keeping_after_a_day(fl):
    """MACFIX-1B specimens: mnemosyne-PERFAUDIT-ASR (a `for … grep` reading a terminal, 0.03 s in
    3.5 days) and mnemosyne-JUDGEBASE-1 (headless Chrome under zsh, ~44 s a day)."""
    hung = {201: 0.02, 202: 0.0, 203: 0.01, 204: 0.0}
    f = _still(fl, HUNG, hung, dict(hung))
    assert f["still"] == {"pid 201 (zsh)": NOW - DAY - 60}
    assert fl.stale_loops(f, NOW) == ["pid 201 (zsh)"] and fl.classify(f, NOW, IDLE) == []
    assert any("stood still" in n for n in fl.notes(f, NOW, IDLE))
    f = _still(fl, BROWSER, {201: 0.01, 202: 115.0, 203: 14.0}, {201: 0.01, 202: 140.0, 203: 30.0})
    assert fl.stale_loops(f, NOW) == ["pid 201 (zsh)"] and fl.classify(f, NOW, IDLE) == []
    crash = BROWSER + [(204, 202, _APP + "/Contents/Frameworks/x/Helpers/chrome_crashpad_handler --monitor-self")]
    f = _still(fl, crash, {201: 0.0, 202: 1.0, 203: 1.0, 204: 0.0}, {201: 0.0, 202: 1.0, 203: 1.0, 204: 0.0})
    assert fl.stale_loops(f, NOW) == ["pid 201 (zsh)"]


def test_negative_a_child_that_used_processor_time_or_may_wait_outside_keeps(fl):
    def kept(f):
        assert fl.stale_loops(f, NOW) == [] and any(k.startswith("background:") for k in fl.classify(f, NOW, IDLE))

    hung = {201: 0.02, 202: 0.0, 203: 0.01, 204: 0.0}
    kept(_still(fl, HUNG, hung, {**hung, 203: 0.02}))               # one hundredth of a second
    kept(_still(fl, HUNG, hung, dict(hung), gap=DAY - 600))               # still, but not for a day
    f = _still(fl, HUNG, hung, dict(hung)); f["child_still_min"] = 0      # the key at 0: off
    kept(f)
    f = _still(fl, HUNG, hung, dict(hung)); f["unsaved"] = ["a.py"]       # the day rule's own terms hold
    assert fl.stale_loops(f, NOW) == []
    kept(_still(fl, BROWSER, {201: 0.01, 202: 115.0, 203: 14.0}, {201: 0.01, 202: 300.0, 203: 30.0}))  # a browser at work
    kept(_still(fl, BROWSER, {201: 0.01, 202: 115.0, 203: 14.0}, {201: 0.02, 202: 116.0, 203: 14.0}))  # its shell did something
    # the same shell, another child at the second reading: a loop that starts things (SUBMATCH-2)
    poll = [(201, 100, "/bin/zsh -c while :; do cat a | grep x; done"), (202, 201, "cat a")]
    kept(_still(fl, poll, {201: 0.0, 202: 0.0}, {201: 0.0, 205: 0.0}, kids1=[poll[0], (205, 201, "cat a")]))
    # what may be waiting for something outside is never read as standing still, at zero seconds too
    for prog in ("ssh mini ./train.sh", "sleep 99999", "tail -f run.log", "/opt/py/bin/Python job.py", "curl -N https://x",
                 "cat gate", "head -1 pipe",
                 # another program that only NAMES a browser (review MF6)
                 "/usr/bin/ssh mini /opt/chrome-headless-shell/chrome-headless-shell",
                 "/usr/bin/tail -f /private/tmp/x/Chromium.log", "/usr/bin/curl -N https://x -o /dl/Google Chrome.dmg"):
        kids = [(201, 100, "/bin/zsh -c run"), (202, 201, prog)]
        f = _still(fl, kids, {201: 0.0, 202: 0.0}, {201: 0.0, 202: 0.0})
        assert f["still"] == {}, prog
        kept(f)
    # a shell that waits with no process to show for it (review MF7)
    for cmd in ("/bin/zsh -c read -r go < /private/tmp/x/gate; ./deploy.sh", "/bin/zsh -c zmodload zsh/zselect; zselect -t 60480000; ./weekly.sh",
                "/bin/bash -c exec 3<>/dev/tcp/mini/9000; cat <&3", "/bin/zsh -c wait",
                "/bin/zsh -c cd x && eval 'while read -r l; do :; done < gate'", "/bin/zsh -c grep x a | nc mini 9",
                # fix review: a word in front hides nothing, and a script FILE cannot be read at all
                "/bin/bash -c while IFS= read -r line; do ./h.sh; done < gate", "/bin/zsh -c IFS=, read -r a b < gate; ./deploy.sh",
                "/bin/zsh -c if ! read -r go < gate; then :; fi", "/bin/zsh -c command read x", "/bin/zsh -c time read x",
                "/bin/bash /opt/proj/wait-for-gate.sh", "/bin/zsh ./gate.zsh", "/bin/zsh -c vared -p go x"):
        f = _still(fl, [(201, 100, cmd)], {201: 0.0}, {201: 0.0})
        assert f["still"] == {}, cmd
        kept(f)
    # … and the word inside a path or an argument is not that command
    f = _still(fl, [(201, 100, "/bin/zsh -c cd /x/read/wait && grep --select x a")], {201: 0.0}, {201: 0.0})
    assert f["still"] != {}
    kept(_still(fl, HUNG, hung, dict(hung), mid=False))                   # two readings: one clock step is not a day
    t = _table(HUNG)                                                      # unread seconds: no entry
    assert fl.still_since(fl.own_work(100, t), t, NOW, cpu={201: 0.0}, comm=_comm(t)) == {}
    assert fl.still_since(fl.own_work(100, t), None, NOW, cpu=hung, comm=_comm(t)) == {}
    assert fl.still_since(fl.own_work(100, t), t, NOW, cpu=hung, comm=None) == {}


def test_a_reading_that_cannot_be_taken_drops_the_baseline(fl):
    """Review MF8: `while :; do R=$(ssh …); sleep 21600; done` read alone, then with ssh under it,
    then alone again: the first reading must not stand."""
    alone, busy = [(201, 100, "/bin/zsh -c while :; do R=$(ssh mini x); sleep 21600; done")], None
    busy = alone + [(202, 201, "ssh mini x")]
    ta, tb = _table(alone), _table(busy)
    for i in range(3):
        fl.still_since(fl.own_work(100, ta), ta, NOW - 3 * DAY + i, cpu={201: 0.0}, comm=_comm(ta))
    assert fl.still_since(fl.own_work(100, ta), ta, NOW - 2 * DAY, cpu={201: 0.0}, comm=_comm(ta)) != {}
    assert fl.still_since(fl.own_work(100, tb), tb, NOW - DAY, cpu={201: 0.0, 202: 0.0}, comm=_comm(tb)) == {}
    assert fl.still_since(fl.own_work(100, ta), ta, NOW, cpu={201: 0.0}, comm=_comm(ta)) == {}


def test_still_step_and_the_readers(fl):
    old = {"since": NOW - 5 * DAY, "at": NOW - DAY, "n": 4, "b0": 40.0, "procs": {"1|a": [1.0, False], "2|a": [50.0, True]}}
    kept = fl.still_step(old, {"1|a": [1.0, False], "2|a": [90.0, True]}, NOW, 120)
    assert kept == {"since": NOW - 5 * DAY, "at": NOW, "n": 5, "b0": 40.0, "procs": {"1|a": [1.0, False], "2|a": [90.0, True]}}
    # the allowance is for the time since the LAST reading: five idle days do not buy 171 s in one
    assert fl.still_step(old, {"1|a": [1.0, False], "2|a": [171.0, True]}, NOW, 120)["since"] == NOW
    assert fl.still_step(dict(old, at=NOW - 60), {"1|a": [1.0, False], "2|a": [56.0, True]}, NOW, 120)["since"] == NOW
    # many short readings, each inside its own allowance, do not add up past the day's (fix review)
    assert fl.still_step(dict(old, since=NOW - DAY, at=NOW - 60, b0=0.0, procs={"1|a": [1.0, False], "2|a": [170.0, True]}),
                         {"1|a": [1.0, False], "2|a": [171.0, True]}, NOW, 120)["since"] == NOW
    assert fl.still_step(dict(old, b0="x"), old["procs"], NOW, 120)["since"] == NOW
    assert fl.still_step(dict(old, at=NOW + 9), old["procs"], NOW, 120)["since"] == NOW          # a clock set back
    assert fl.still_step(old, {"1|a": [1.01, False], "2|a": [50.0, True]}, NOW, 120)["since"] == NOW
    assert fl.still_step(old, {"1|a": [0.5, False], "2|a": [50.0, True]}, NOW, 120)["since"] == NOW    # a pid reused
    assert fl.still_step(old, {"1|a": [1.0, False]}, NOW, 120)["since"] == NOW
    assert fl.still_step({"since": NOW + 5, "at": NOW + 5, "n": 3, "b0": 0.0, "procs": old["procs"]}, old["procs"], NOW, 120)["since"] == NOW
    assert fl.still_step("x", old["procs"], NOW, 120)["since"] == NOW
    assert fl.parse_cpu("1:55.33") == pytest.approx(115.33) and fl.parse_cpu("2-01:00:00") == 2 * 86400 + 3600
    assert fl.parse_cpu("abc") is None and fl.parse_cpu("") is None
    c = _comm(_table(BROWSER))
    assert [fl.is_browser(c[p]) for p in (201, 202, 203)] == [False, True, True]
    assert not fl.is_browser("Google Chrome") and not fl.is_browser("/usr/bin/ssh") and not fl.is_browser(None)
    assert fl.child_still_minutes() == 1440 and fl.browser_idle_cpu_s() == 120


def test_a_dead_process_leaves_the_still_file(fl, tmp_path):
    hung = {201: 0.02, 202: 0.0, 203: 0.01, 204: 0.0}
    t = _table(HUNG)
    fl.still_since(fl.own_work(100, t), t, NOW, cpu=hung, comm=_comm(t))
    doc = json.loads((tmp_path / "state" / "child-still.json").read_text())
    assert len(doc) == 1 and len(next(iter(doc.values()))["procs"]) == 4
    other = _table([(301, 100, "/bin/zsh -c grep x")])
    fl.still_since(fl.own_work(100, other), other, NOW, cpu={301: 0.0}, comm=_comm(other))
    assert [k.split("|")[0] for k in json.loads((tmp_path / "state" / "child-still.json").read_text())] == ["301"]


REMOTE = [(201, 100, "/bin/zsh -c until ssh mini grep -q \"kill: done\" log; do sleep 10; done; ssh mini grep x log"),
          (202, 201, "sleep 10")]


def test_positive_after_a_day_idle_shells_and_wait_loops_no_longer_keep_reported_or_not(fl):
    """Owner, 2026-10-06: a session whose last turn ended more than a day ago, with nothing
    unsaved and only an idle shell or a shell + sleep loop left, closes."""
    for kids in (LOOP, REMOTE, [(201, 100, "-zsh")], [(201, 100, "/bin/zsh -i")]):
        f = _day_facts(fl, kids)
        assert len(fl.stale_loops(f, NOW)) == 1, kids
        assert fl.classify(f, NOW, IDLE) == [], kids


def test_negative_what_the_day_rule_still_keeps(fl):
    def kept(f):
        assert fl.stale_loops(f, NOW) == [] and fl.classify(f, NOW, IDLE), f["work"]

    kept(_day_facts(fl, LOOP, age_min=1400.0))                           # under a day
    kept(_day_facts(fl, LOOP, abandoned_min=0))                          # the key at 0: off
    kept(_day_facts(fl, LOOP, status="busy"))
    job = [(201, 100, "/bin/zsh -c run"), (202, 201, "/opt/py/bin/Python -m pytest -q")]
    kept(_day_facts(fl, job))                                            # a shell running a job
    asleep = [(201, 100, "/bin/zsh -c while :; do rsync a b; sleep 600; done"), (202, 201, "sleep 600")]
    kept(_day_facts(fl, asleep))                                         # a job between two runs
    kept(_day_facts(fl, [(201, 100, "/bin/zsh -c sleep 99999 && ./deploy.sh"), (202, 201, "sleep 99999")]))
    kept(_day_facts(fl, LOOP + [(301, 100, "/opt/py/bin/Python -m http.server")]))
    kept(_day_facts(fl, [(201, 100, "/bin/bash run_suite.sh")]))          # a script between two commands
    two = "/bin/zsh -c for i in 1 2; do ./step.sh $i; sleep 600; done; until [ -f ok ]; do sleep 5; done"
    kept(_day_facts(fl, [(201, 100, two), (202, 201, "sleep 600")]))     # a job loop before the wait
    back = "/bin/zsh -c ./job.sh & until [ -f ok ]; do sleep 5; done"
    kept(_day_facts(fl, [(201, 100, back), (202, 201, "sleep 5")]))       # a job in the background
    kept(_day_facts(fl, [(201, 100, "-zsh"), (202, 201, "/opt/py/bin/Python job.py")]))  # bare shell, a child
    assert fl.stale_loops(_day_facts(fl, LOOP, age_min=1440.0), NOW) != []     # at the key: closes
    assert any("it did not report" in n for n in fl.notes(_day_facts(fl, LOOP), NOW, IDLE))
    kept(_day_facts(fl, LOOP + [(203, 201, "/bin/bash run_suite.sh")]))   # a script under the loop
    for unsaved in (["/r/HANDOFF.md"], None):                           # its own work, or git unread
        f = _day_facts(fl, LOOP, unsaved=unsaved)
        assert fl.stale_loops(f, NOW) == []


CLI_LOOP = ("/bin/zsh -c source /x/snapshot.sh 2>/dev/null || true && eval 'S=/tmp/s; until [ -f $S/ok ]; "
            "do sleep 120; done; echo \"66 samples reached\"; date' < /dev/null && pwd -P >| /tmp/claude-4c5e-cwd")


def test_a_wait_loop_is_told_by_its_command_not_by_the_instant(fl):
    """The review's must-fix 2: a job sampled during its `sleep` is not a wait loop."""
    shown = "/bin/zsh -c until [ -f o ] && grep -c x o | grep -q 2; do sleep 10; done; cat o | tail -3"
    for cmd in (LOOP[0][2], CLI_LOOP, shown, "/bin/zsh -c while true; do sleep 5; done"):
        assert fl.wait_loop_text(cmd), cmd                                             # positive
        assert fl.own_work(100, _table([(201, 100, cmd), (202, 201, "sleep 5")])) == _w(True), cmd
    jobs = ("/bin/zsh -c while :; do rsync -a a b; sleep 600; done",      # the body does work
            "/bin/zsh -c sleep 3600 && ./deploy.sh",                      # no loop: a delayed job
            "/bin/zsh -c until [ -f ok ]; do sleep 5; done; ./deploy.sh", # work after the wait
            "/bin/zsh -c until [ -f ok ]; do sleep 5; done; echo x | mail me",
            "/bin/zsh -c until [ -f ok ]; do sleep 5; done; echo ok > flag",
            "/bin/zsh -c until [ -f ok ]; do sleep 5; done; cat ok | sh",
            # the fix review's forms: work BEFORE the loop, beside it, or on its exit
            "/bin/zsh -c for h in a b; do ssh $h restart; sleep 900; done; until curl -sf h; do sleep 10; done",
            "/bin/zsh -c sleep 7200 && ./deploy.sh; until [ -f d ]; do sleep 5; done",
            "/bin/zsh -c (sleep 7200; ./deploy.sh) & until [ -f d ]; do sleep 5; done",
            "/bin/zsh -c trap \"rm -rf build\" EXIT; until [ -f d ]; do sleep 5; done",
            "/bin/zsh -c until [ -f ok ]; do sleep 5; done; echo go & ./deploy.sh",
            "/bin/zsh -c until ./deploy.sh & [ -f d ]; do sleep 5; done",        # a job sent to the back
            "/bin/zsh -c sleep 7200 && ./deploy.sh # until x; do sleep 5; done",
            CLI_LOOP.replace("eval 'S=/tmp/s; until", "eval './prepare.sh; until"),
            "/bin/zsh -c until [ -f ok ]; do sleep 5; done; cat ok & ./deploy.sh",
            "/bin/zsh -c until ssh mini true; do sleep 5; done; ssh mini grep x log",   # remote: not judged
            "/bin/bash run_suite.sh",                                     # between two commands
            "/bin/zsh -c x", "-zsh")                                      # a bare shell: read / wait
    for cmd in jobs:
        assert not fl.wait_loop_text(cmd), cmd
        assert fl.own_work(100, _table([(201, 100, cmd), (202, 201, "sleep 5")]))[0]["waits"] is False, cmd
    script = LOOP + [(203, 201, "/bin/bash run_suite.sh")]     # a shell under the loop that is no fork
    assert fl.own_work(100, _table(script)) == _w(False)
    assert fl.own_work(100, None) is None and fl.own_processes(100, _table(LOOP)) == ["pid 201 (zsh)"]


def _loop_facts(fl, kids=LOOP, **over):
    work = fl.own_work(100, _table(kids))
    f = facts(status="shell", work=work, children=[w["label"] for w in work],
              end=end(90, sent=sent("seat-a"), open_bg=["bg1"], open_bg_ts={"bg1": NOW - 300 * M}))
    f.update(over)
    return f


def test_positive_a_reported_session_is_not_kept_by_the_wait_loop_it_left(fl):
    for f in (_loop_facts(fl), _loop_facts(fl, end=end(90, sent=sent(SOCK), open_bg=["bg1"],
                                                       open_bg_ts={"bg1": NOW - 300 * M}))):
        assert fl.stale_loops(f, NOW) == ["pid 201 (zsh)"]
        assert fl.classify(f, NOW, IDLE) == []
        assert any(n.startswith(fl.STALE_NOTE + ": pid 201 (zsh)") for n in fl.notes(f, NOW, IDLE))


def test_negative_what_still_keeps_a_session_with_a_background_shell(fl):
    def kept(f, word):
        k = fl.classify(f, NOW, IDLE)
        assert fl.stale_loops(f, NOW) == [] and any(word in x for x in k), (word, k)

    kept(_loop_facts(fl, end=end(90, open_bg=["bg1"], open_bg_ts={"bg1": NOW - 300 * M})),
         "says 'shell'")                                                # never reported: a waiter
    kept(_loop_facts(fl, end=end(45, sent=sent("seat-a"))), "says 'shell'")      # under 60 min
    kept(_loop_facts(fl, stale_shell_min=0), "still running")                   # the key at 0: off
    kept(_loop_facts(fl, status="busy"), "says 'busy'")
    kept(_loop_facts(fl, status=None), "still running")                 # registry unread
    # the review's must-fix 1: "I report when they finish", THEN the loop — it waits on it
    same_second = fl.own_work(100, _table(LOOP, at=NOW - 91 * M))        # whole seconds: after
    kept(_loop_facts(fl, work=same_second), "says 'shell'")
    after = fl.own_work(100, _table(LOOP, at=NOW - 90.5 * M))
    kept(_loop_facts(fl, work=after), "says 'shell'")
    kept(_loop_facts(fl, end=end(90, sent=sent("seat-a"), open_bg=["bg1"],
                                 open_bg_ts={"bg1": NOW - 90.5 * M})), "says 'shell'")
    kept(_loop_facts(fl, end=end(90, sent=[{"to": "seat-a", "ts": 0.0}])), "says 'shell'")  # undated
    unread = [dict(w, started=None) for w in fl.own_work(100, _table(LOOP))]
    kept(_loop_facts(fl, work=unread), "says 'shell'")                  # a start nobody could read
    sleeping = [(201, 100, "/bin/zsh -c while :; do rsync a b; sleep 600; done"), (202, 201, "sleep 600")]
    kept(_loop_facts(fl, kids=sleeping), "still running")       # must-fix 2: a job asleep is a job
    suite = [(201, 100, "/bin/zsh -c run"), (202, 201, "/opt/py/bin/Python -m pytest -q")]
    kept(_loop_facts(fl, kids=suite), "still running")          # the CLOSETUNE-2 wrong-close specimen
    two = LOOP + [(301, 100, "/opt/py/bin/Python -m http.server")]
    kept(_loop_facts(fl, kids=two), "2 process(es)")                    # one loop, one job: both keep
    assert fl.classify(_loop_facts(fl, attached=True), NOW, IDLE) == ["attached: someone has its screen open"]


# -------------------------------------------------------------------- 2. committed = saved ----

def _repo(tmp_path):
    repo = tmp_path / "main"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "HANDOFF.md").write_text("State: DONE\n")
    (repo / "other.md").write_text("x\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "seat commits the handoff", cwd=repo, when=NOW)
    return repo


def test_positive_a_file_committed_after_the_write_is_saved_whoever_changed_it_since(fl, tmp_path):
    repo = _repo(tmp_path)
    (repo / "HANDOFF.md").write_text("State: DONE\nthe seat's later note\n")
    full = str(repo / "HANDOFF.md")
    os.utime(full, (NOW + 5000, NOW + 5000))               # the seat's note, after the session ended
    assert fl.session_unsaved(str(repo), None, {full: NOW - 3600}, NOW + 60) == []
    assert fl.session_unsaved(str(repo), None, {full: NOW}, NOW + 60) == []     # at the commit: saved


def test_negative_a_later_commit_does_not_save_what_the_session_changed_itself(fl, tmp_path):
    """The review's must-fix 3: commit time ≥ write time shows a commit touched the path later,
    not that it carried the write. The file must have changed AFTER the session's last turn."""
    repo = _repo(tmp_path)
    full = str(repo / "HANDOFF.md")
    left = [os.path.join(os.path.realpath(repo), "HANDOFF.md")]
    (repo / "HANDOFF.md").write_text("v2, edited after `git add`, or by its own `sed -i`\n")
    os.utime(full, (NOW + 30, NOW + 30))                           # inside its last turn
    assert fl.session_unsaved(str(repo), None, {full: NOW - 3600}, NOW + 60) == left
    os.utime(full, (NOW + 60, NOW + 60))                           # the same second: not after
    assert fl.session_unsaved(str(repo), None, {full: NOW - 3600}, NOW + 60) == left
    os.utime(full, (NOW + 5000, NOW + 5000))
    assert fl.session_unsaved(str(repo), None, {full: NOW - 3600}) == left         # end unknown
    assert fl.session_unsaved(str(repo), None, {full: NOW - 3600}, NOW + 60) == []  # the control


def test_negative_what_is_still_unsaved(fl, tmp_path):
    repo = _repo(tmp_path)
    real = os.path.realpath(repo)
    h = str(repo / "HANDOFF.md")
    (repo / "HANDOFF.md").write_text("changed after the commit, by the session\n")
    os.utime(h, (NOW + 5000, NOW + 5000))
    assert fl.session_unsaved(str(repo), None, {h: NOW + 60}, NOW + 90) == [os.path.join(real, "HANDOFF.md")]
    assert fl.session_unsaved(str(repo), None, {h: 0}, NOW + 90) == [os.path.join(real, "HANDOFF.md")]
    assert fl.session_unsaved(str(repo), None, [h], NOW + 90) == [os.path.join(real, "HANDOFF.md")]   # no times
    (repo / "new.md").write_text("never committed\n")
    assert fl.session_unsaved(str(repo), None, {str(repo / "new.md"): NOW - 9}) == [
        os.path.join(real, "new.md")]
    _git("add", "new.md", cwd=repo)                                        # added, not committed
    assert fl.session_unsaved(str(repo), None, {str(repo / "new.md"): NOW - 9}) == [
        os.path.join(real, "new.md")]
    (repo / "other.md").unlink()                                           # deleted
    assert fl.session_unsaved(str(repo), None, {str(repo / "other.md"): NOW - 9}) == [
        os.path.join(real, "other.md")]


def test_a_throwaway_checkout_in_the_temp_folder_is_not_work_to_save(fl, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "new.md").write_text("scratch\n")
    work = tmp_path / "elsewhere"
    work.mkdir()
    writes = {str(repo / "new.md"): NOW + 60}
    assert os.path.realpath("/tmp") in fl.temp_roots()           # the shipped roots, by real path
    monkeypatch.setattr(fl, "temp_roots", lambda: [os.path.realpath(tmp_path / "main")])
    assert fl.session_unsaved(str(work), None, writes) == []
    assert fl.session_unsaved(str(repo), None, writes) != []     # negative: the session RUNS there
    monkeypatch.setattr(fl, "temp_roots", lambda: [os.path.realpath(tmp_path / "mai")])
    assert fl.session_unsaved(str(work), None, writes) != []     # negative: a prefix is not a parent


def test_negative_a_worktree_in_temp_of_a_repository_elsewhere_is_work(fl, tmp_path, monkeypatch):
    """The review's must-fix 4: only a checkout whose REPOSITORY is in temp is a throwaway."""
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _git("worktree", "add", "-q", str(scratch / "wt"), "-b", "side", cwd=repo)
    (scratch / "wt" / "new.md").write_text("real work on a real branch\n")
    work = tmp_path / "elsewhere"
    work.mkdir()
    monkeypatch.setattr(fl, "temp_roots", lambda: [os.path.realpath(scratch)])
    assert fl.session_unsaved(str(work), None, {str(scratch / "wt" / "new.md"): NOW}) == [
        os.path.join(os.path.realpath(scratch / "wt"), "new.md")]
    monkeypatch.undo()
    monkeypatch.setenv("TMPDIR", "")                    # no temp folder named: never the cwd
    assert os.path.realpath(os.getcwd()) not in fl.temp_roots()


# ------------------------------------------------------------------------- 1. one signal ----

def _needed(fl, guard, swap_mb=2853.0, level=40.0):
    """`close_when_needed` over two finished sessions; `guard()` is the launch guard's answer."""
    rows = [{"name": "b-1", "pid": 11, "launched_at": NOW - 9000}, {"name": "b-2", "pid": 12,
                                                                    "launched_at": NOW - 8000}]
    ages = {"b-1": 400, "b-2": 300}
    closed, sweeps = [], []

    def gather(r, mux, me):
        return facts(end=end(ages[r["name"]], sent=sent("seat-a")), alive=r["name"] not in closed)

    def sweep(**kw):
        sweeps.append(1)
        return fl.sweep(now=NOW, gather_fn=gather, refusals_fn=lambda r: ([], None), **kw)

    res = fl.close_when_needed(
        apply=True, by="t", settle_s=0, rows=rows, guard_fn=lambda: guard(closed),
        readings_fn=lambda: {"swap": 0.3, "swap_mb": swap_mb, "level": level}, sweep_fn=sweep,
        apply_fn=lambda i, by: fl.apply_item(
            i, by=by, gather_fn=gather, refusals_fn=lambda r: ([], None), clock=lambda: NOW,
            close_fn=lambda r, m, a: closed.append(r["name"]) or "screen quit"))
    return res, closed, sweeps


@pytest.fixture
def alive(fl, monkeypatch):
    monkeypatch.setattr(fl, "same_process", lambda pid, start: True)
    monkeypatch.setattr(fl, "multiplexer", lambda: "screen")
    return fl


def test_positive_when_the_launch_guard_refuses_the_closer_is_over_its_line(alive):
    # the notice's figures: pressure 2, level 40, swap 2,853 MB — under the closer's own line
    res, closed, _ = _needed(alive, lambda c: None if c else "memory pressure is warn")
    assert res["over"] == [alive.GUARD_OVER + ": memory pressure is warn"]
    assert closed == ["b-1"] and res["stopped"] == "back under the line"   # oldest first, then stop


def test_negative_with_the_guard_passing_the_closer_reads_and_does_nothing(alive):
    res, closed, sweeps = _needed(alive, lambda c: None)
    assert res["stopped"] == "under the line" and closed == [] and sweeps == []


def test_a_caller_with_its_own_readings_never_reads_the_machines_guard(alive, monkeypatch):
    monkeypatch.setattr(alive, "launch_guard", lambda: pytest.fail("the machine's guard was read"))
    res = alive.close_when_needed(apply=False, by="t", readings_fn=lambda: {"swap_mb": 1.0, "level": 90.0})
    assert res["stopped"] == "under the line"


@pytest.fixture
def launch(fl, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("sessions_sc3", TOOLS / "sessions.py")
    ses = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ses)
    launcher = tmp_path / "l.sh"
    launcher.write_text("#!/bin/zsh\nexec claude --name x\n")
    folder = tmp_path / "work"
    folder.mkdir()
    state = {"pressure": 2, "closer": [], "ran": []}

    def run(*extra, closes=(), wait=None, capsys=None, helps=True):
        def closer(**kw):
            state["closer"].append(kw)
            if closes and helps:
                state["pressure"] = 1                          # closing brought the pressure down
            return {"closed": [{"name": n} for n in closes]}
        monkeypatch.setattr(ses.fleet, "launch_readings", lambda: {
            "pressure": state["pressure"], "level": 40.0, "swap_free_mb": 900.0, "swap_total_mb": 3072.0})
        monkeypatch.setattr(ses.fleet, "claude_processes", lambda: [])
        monkeypatch.setattr(ses.fleet, "working_count", lambda p, status_fn=None: 0)
        monkeypatch.setattr(ses.fleet, "sweep", lambda **_kw: [])
        monkeypatch.setattr(ses.fleet, "multiplexer", lambda: "screen")
        monkeypatch.setattr(ses.fleet, "close_when_needed", closer)
        if wait is not None:
            monkeypatch.setattr(ses.fleet, "launch_wait_s", lambda: wait)
        monkeypatch.setattr(ses.subprocess, "run", lambda argv, **_kw: state["ran"].append(argv) or
                            type("P", (), {"returncode": 1, "stdout": "", "stderr": "no screen"})())
        code = ses.main(["launch", "--name", "x", "--cwd", str(folder), *extra, str(launcher)])
        return code, capsys.readouterr().out

    run.state, run.folder, run.cli = state, folder, tmp_path / "cli"
    return run


def test_positive_a_refused_launch_closes_first_and_then_goes_on(launch, capsys):
    code, out = launch(closes=("b-1",), capsys=capsys)
    assert "closed to make room: b-1" in out and "refused (" not in out
    assert code == 1 and "failed:" in out and len(launch.state["ran"]) == 1     # it reached screen
    assert launch.state["closer"][0]["apply"] is True


def test_negative_a_refused_launch_stays_refused_when_nothing_closes(launch, capsys):
    code, out = launch(capsys=capsys)
    assert code == 3 and out.startswith("refused (memory)") and launch.state["ran"] == []
    assert len(launch.state["closer"]) == 1
    launch.state["closer"].clear()
    code, out = launch(wait=0, capsys=capsys)                 # the key at 0: no close, no wait
    assert code == 3 and launch.state["closer"] == []


def test_negative_a_close_that_did_not_help_does_not_let_the_launch_through(launch, capsys):
    code, out = launch(closes=("b-1",), helps=False, wait=0.001, capsys=capsys)
    assert code == 3 and "refused (memory)" in out and launch.state["ran"] == []
    assert len(launch.state["closer"]) == 1


def test_negative_an_owner_go_never_runs_the_closer(launch, capsys):
    launch("--owner-go", "go now", capsys=capsys)
    assert launch.state["closer"] == [] and len(launch.state["ran"]) == 1


# ------------------------------------------------------------------- the 5-minute tick ----

@pytest.fixture
def tick(fl, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("sessions_sc3_tick", TOOLS / "sessions.py")
    ses = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ses)
    seen = {"calls": [], "due": []}

    def run(*argv, over=False, due=True, apply_on=True, closes=()):
        seen["calls"].clear(), seen["due"].clear()

        def closer(**kw):
            seen["calls"].append(kw)
            done = [{"name": n, "pid": 1, "resume": "claude -r " + n} for n in closes] if kw["apply"] else []
            if over and not kw.get("all_finished"):
                return {"stopped": "nothing finished to close", "candidates": [], "closed": done,
                        "over": ["swap"], "after": {}, "before": {}}
            return {"stopped": "under the line", "candidates": list(closes), "closed": done,
                    "over": None, "after": {}, "before": {}, "items": [], "stats": {}}
        monkeypatch.setattr(ses.fleet, "close_when_needed", closer)
        monkeypatch.setattr(ses.fleet, "due", lambda consume=True: seen["due"].append(consume) or due)
        monkeypatch.setattr(ses.fleet, "applies", lambda: apply_on)
        monkeypatch.setattr(ses.fleet, "figures", lambda r: "figures")
        monkeypatch.setattr(ses.fleet, "record_proposals", lambda items: None)
        monkeypatch.setattr(ses.fleet, "close_log", lambda line: None)
        monkeypatch.setattr(ses.fleet, "pass_line", lambda *a, **k: "pass")
        monkeypatch.setattr(ses, "_under_line", lambda res: "under the line (figures)")
        code = ses.main(["close", "--tick", *argv])
        return code, capsys.readouterr().out, [bool(c.get("all_finished")) for c in seen["calls"]]

    run.seen = seen
    return run


def test_positive_the_tick_closes_every_finished_session_when_due(tick):
    code, out, passes = tick(closes=("b-1",))
    assert code == 0 and passes == [False, True] and "closed  b-1" in out
    assert all(c["apply"] is True for c in tick.seen["calls"]) and tick.seen["due"] == [True]


def test_negative_the_tick_does_one_cheap_read_when_not_due_or_over_the_line(tick):
    code, out, passes = tick(due=False, closes=("b-1",))
    assert code == 0 and passes == [False] and out.startswith("under the line")      # quiet for the log
    code, out, passes = tick(over=True)
    assert code == 0 and passes == [False] and out.startswith("tick: over the line") and tick.seen["due"] == []


def test_negative_a_dry_tick_closes_nothing_and_keeps_the_next_passes_turn(tick):
    for argv, on in ((("--dry-run",), True), ((), False)):          # asked dry, or apply switched off
        code, out, passes = tick(*argv, apply_on=on, closes=("b-1",))
        assert code == 0 and passes == [False, True] and "closed  b-1" not in out
        assert all(c["apply"] is False for c in tick.seen["calls"])
        assert tick.seen["due"] == [False]                          # asked, not consumed


# ---------------------------------------------------------------- 4. trust and PROMPT age ----

def _cli(fl, tmp_path, projects):
    (tmp_path / "cli").mkdir(exist_ok=True)
    (tmp_path / "cli" / ".claude.json").write_text(json.dumps({"projects": projects}))


def test_folder_trust_reads_the_clis_own_record(fl, tmp_path):
    repo, bad = tmp_path / "repo", tmp_path / "bad"
    (repo / ".claude" / "worktrees" / "ROW").mkdir(parents=True)
    bad.mkdir()
    assert fl.folder_trust(str(repo)) is None                                  # no file
    _cli(fl, tmp_path, {str(repo): {"hasTrustDialogAccepted": True},
                        str(bad): {"hasTrustDialogAccepted": False},
                        str(tmp_path): {"hasTrustDialogAccepted": False}})
    assert fl.folder_trust(str(repo)) is True
    assert fl.folder_trust(str(repo / ".claude" / "worktrees" / "ROW")) is True   # a parent vouches
    assert fl.folder_trust(str(bad)) is False
    assert fl.folder_trust(str(tmp_path / "never-opened")) is None
    link = tmp_path / "alias"                                # the review's must-fix 5
    link.symlink_to(repo)
    for order in ((str(repo), True, str(link), False), (str(link), False, str(repo), True)):
        _cli(fl, tmp_path, {order[0]: {"hasTrustDialogAccepted": order[1]},
                            order[2]: {"hasTrustDialogAccepted": order[3]}})
        assert fl.folder_trust(str(repo)) is True, order
        _cli(fl, tmp_path, {str(repo): {"hasTrustDialogAccepted": True}, str(bad): {"hasTrustDialogAccepted": False}})
    (tmp_path / "cli" / ".claude.json").write_text("{not json")
    assert fl.folder_trust(str(bad)) is None


def test_a_launch_into_an_untrusted_folder_is_refused_and_says_how(fl, launch, tmp_path, capsys):
    _cli(fl, tmp_path, {str(launch.folder): {"hasTrustDialogAccepted": False}})
    launch.state["pressure"] = 1
    code, out = launch(capsys=capsys)
    assert code == 2 and out.startswith("refused (trust)") and "--untrusted-ok" in out
    assert launch.state["ran"] == []
    code, out = launch("--untrusted-ok", capsys=capsys)                        # the way past it
    assert code == 1 and "failed:" in out and "refused" not in out
    _cli(fl, tmp_path, {str(launch.folder): {"hasTrustDialogAccepted": True}})
    code, out = launch(capsys=capsys)                                          # negative: trusted
    assert code == 1 and "refused" not in out


def test_a_prompt_is_timed_from_when_it_was_first_seen_and_flagged(fl):
    r = {"name": "b-1", "pid": 11, "pid_start": "s"}
    yes = lambda pid, start: True                                              # noqa: E731
    assert fl.observe_prompt(r, False, NOW, alive_fn=yes) is None and fl.read_prompt_seen() == {}
    assert fl.observe_prompt(r, True, NOW, "screen -r b-1", alive_fn=yes) == NOW
    assert fl.observe_prompt(r, True, NOW + 23 * M, alive_fn=yes) == NOW       # kept, not restarted
    assert fl.observe_prompt(r, None, NOW + 24 * M, alive_fn=yes) == NOW       # unread: unchanged
    assert fl.prompt_words(NOW, NOW + 23 * M) == "PROMPT 23 min — FLAGGED"
    assert fl.prompt_words(NOW, NOW + 2 * M) == "PROMPT 2 min"                 # negative: young
    assert fl.prompt_words(NOW, NOW + 23 * M, flag_min=0) == "PROMPT 23 min"   # the key at 0: off
    other = {"name": "b-2", "pid": 12, "pid_start": "s"}
    assert fl.observe_prompt(other, True, NOW, since=NOW - 3600, alive_fn=yes) == NOW - 3600
    # the first is answered, the second's process is gone: both entries go
    assert fl.observe_prompt(r, False, NOW + 30 * M, alive_fn=lambda pid, start: False) is None
    assert fl.read_prompt_seen() == {}
