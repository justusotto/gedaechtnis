"""tmpsweep.py (MACFIX-1): the session temp folder and the processes dead sessions left.

Every rule that decides a REMOVAL has a case that must go and a case that must stay. The machine's
own sessions and processes are never read: `Snapshot` is replaced by a fake whose three readings
the test sets, and the temp root is a folder under tmp_path.
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

import tmpsweep as _t                                  # noqa: E402
REAL = _t.Snapshot                                     # the class itself, before any test replaces it

SID_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
SID_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
DAY = 86400


class Fake:
    """A Snapshot with chosen readings."""
    live: set = set()
    cmds: dict = {}
    cwd_list: list = []
    readable = True
    session_pids: set = set()
    comms: dict = {}

    def __init__(self):
        self.comm = dict(Fake.comms)
        self.ok, self.sids, self.cwds = Fake.readable, set(Fake.live), list(Fake.cwd_list)
        self.cmds, self.session_pids = dict(Fake.cmds), set(Fake.session_pids)
        self.why_alive = lambda p, s: REAL.why_alive(self, p, s)
        self.under_live_session = lambda pid: REAL.under_live_session(self, pid)


@pytest.fixture
def box(tmp_path, monkeypatch):
    state = tmp_path / "state"; state.mkdir()
    home = tmp_path / "claude"; (home / "projects" / "-p").mkdir(parents=True)
    root = (tmp_path / "tmp" / "claude-501").resolve(); root.mkdir(parents=True)
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(state))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    import limits, tmpsweep
    limits._reset_for_tests()
    vals = {"tmp_sweep": True, "leftover_procs": True, "tmp_sweep_root": str(root), "tmp_sweep_strip_days": 2,
            "tmp_sweep_days": 7, "tmp_sweep_big_file_mb": 1, "tmp_sweep_max_per_pass": 60,
            "leftover_procs_min_age_s": DAY, "tmp_sweep_every_s": 21600}
    monkeypatch.setattr(limits, "get", lambda k, d=None: vals.get(k, d))
    monkeypatch.setattr(tmpsweep, "Snapshot", Fake)
    Fake.live, Fake.cmds, Fake.cwd_list, Fake.readable, Fake.session_pids = set(), {1: (0, "x", "launchd")}, [], True, set()
    Fake.comms = {}
    return root, vals


def unit(root: Path, sid: str, age_days: float, slug: str = "-Users-me-proj") -> Path:
    """A session folder with a note, a big file, a copied repository and a dependency folder."""
    u = root / slug / sid
    (u / "scratchpad" / "repo" / ".git").mkdir(parents=True)
    (u / "scratchpad" / "repo" / ".git" / "HEAD").write_text("ref: refs/heads/x\n")
    (u / "scratchpad" / "repo" / "a.py").write_text("x = 1\n")
    (u / "scratchpad" / "node_modules").mkdir()
    (u / "scratchpad" / "node_modules" / "m.js").write_text("1\n")
    (u / "scratchpad" / "notes.md").write_text("what I found\n")
    (u / "scratchpad" / "big.db").write_bytes(b"0" * (2 << 20))
    (u / "tasks").mkdir()
    age(u, age_days)
    return u


def age(u: Path, days: float) -> None:
    t = time.time() - days * DAY
    for dp, dn, fn in os.walk(u, topdown=False):
        for n in fn + dn:
            os.utime(os.path.join(dp, n), (t, t), follow_symlinks=False)
    os.utime(u, (t, t))


def test_a_dead_sessions_folder_is_STRIPPED_after_two_days_and_its_notes_stay(box):
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 3)
    res = tmpsweep.sweep(apply=True)
    assert res["stripped"] == [str(u)] and res["freed"] >= 2 << 20
    assert (u / "scratchpad" / "notes.md").read_text() == "what I found\n"
    assert not (u / "scratchpad" / "big.db").exists() and not (u / "scratchpad" / "repo").exists()
    assert not (u / "scratchpad" / "node_modules").exists()
    led = (u / "SWEPT.txt").read_text()
    assert "scratchpad/big.db" in led and "scratchpad/repo" in led and "ref: refs/heads/x" in led
    assert tmpsweep.sweep(apply=True)["stripped"] == []            # a second pass finds nothing to do


def test_a_BROWSER_PROFILE_is_heavy_and_a_folder_that_only_looks_like_one_is_not(box):
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 3)
    prof, not_prof = u / "scratchpad" / "prof", u / "scratchpad" / "state"
    (prof / "Default").mkdir(parents=True); (prof / "Local State").write_text("{}")
    (prof / "Default" / "Cookies").write_text("c")
    not_prof.mkdir(); (not_prof / "Local State").write_text("a note with that name")   # no Default/
    age(u, 3)
    assert tmpsweep.sweep(apply=True)["stripped"] == [str(u)]
    assert not prof.exists() and (not_prof / "Local State").exists()
    assert "profile\tscratchpad/prof" in (u / "SWEPT.txt").read_text()


def test_a_folder_younger_than_two_days_is_KEPT_whole(box):
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 1)
    res = tmpsweep.sweep(apply=True)
    assert res["stripped"] == [] and res["removed"] == [] and (u / "scratchpad" / "big.db").exists()
    assert (str(u), tmpsweep.KEPT_YOUNG) in res["kept"]


def test_a_dead_sessions_folder_is_REMOVED_after_seven_days_and_a_six_day_one_is_not(box):
    import tmpsweep
    root, _ = box
    old, mid = unit(root, SID_A, 8), unit(root, SID_B, 6)
    res = tmpsweep.sweep(apply=True)
    assert res["removed"] == [str(old)] and not old.exists()
    assert mid.exists() and (mid / "scratchpad" / "notes.md").exists()


def test_the_strip_does_not_restart_the_seven_days(box):
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 6.5)
    assert tmpsweep.sweep(apply=True)["stripped"] == [str(u)]
    assert (time.time() - tmpsweep.last_activity(u, SID_A)) / DAY > 6.4     # the ledger kept the old time


def test_a_recent_TRANSCRIPT_keeps_an_old_folder(box, tmp_path):
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 9)
    (tmp_path / "claude" / "projects" / "-p" / f"{SID_A}.jsonl").write_text("{}\n")
    assert tmpsweep.sweep(apply=True)["removed"] == [] and u.exists()


@pytest.mark.parametrize("how", ["sid", "cmd", "cwd", "unreadable"])
def test_a_LIVE_sessions_folder_is_never_touched(box, how):
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 9)
    if how == "sid":
        Fake.live = {SID_A}
    elif how == "cmd":
        Fake.cmds = {77: (1, "x", f"chrome --user-data-dir={u}/scratchpad/prof")}
    elif how == "cwd":
        Fake.cwd_list = [(str(u / "scratchpad"), 77)]
    else:
        Fake.readable = False
    res = tmpsweep.sweep(apply=True)
    assert res["removed"] == [] and res["stripped"] == [] and (u / "scratchpad" / "big.db").exists()


def test_a_session_that_comes_alive_between_the_check_and_the_act_is_kept(box, monkeypatch):
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 9)
    n = {"made": 0}

    class Later(Fake):
        def __init__(self):
            n["made"] += 1
            Fake.live = {SID_A} if n["made"] > 1 else set()
            super().__init__()
    monkeypatch.setattr(tmpsweep, "Snapshot", Later)
    assert tmpsweep.sweep(apply=True)["removed"] == [] and u.exists() and n["made"] == 2


def test_ALLOW_LIST_only_slug_uuid_folders_are_units(box):
    import tmpsweep
    root, _ = box
    (root / "bundled-skills" / SID_A).mkdir(parents=True)             # the slug does not start with "-"
    (root / "-Users-me-proj" / "not-a-uuid").mkdir(parents=True)
    (root / "tmpabc").mkdir()
    for p in (root / "bundled-skills", root / "-Users-me-proj" / "not-a-uuid", root / "tmpabc"):
        age(p, 30)
    assert tmpsweep.units(root) == []
    assert tmpsweep.sweep(apply=True)["removed"] == [] and (root / "tmpabc").exists()


def test_a_LINKED_folder_is_no_unit_and_a_link_inside_is_unlinked_not_followed(box, tmp_path):
    import tmpsweep
    root, _ = box
    outside = tmp_path / "precious"; outside.mkdir(); (outside / "keep.txt").write_text("keep")
    (root / "-Users-me-proj").mkdir()
    os.symlink(outside, root / "-Users-me-proj" / SID_B)              # a unit that is a link
    u = unit(root, SID_A, 9)
    os.symlink(outside, u / "scratchpad" / "link")
    age(u, 9)
    res = tmpsweep.sweep(apply=True)
    assert res["removed"] == [str(u)] and (outside / "keep.txt").read_text() == "keep"
    assert os.path.islink(root / "-Users-me-proj" / SID_B)
    assert not tmpsweep.link_free(root / "-Users-me-proj" / SID_B / "x")
    assert all(sid != SID_B for _p, sid in tmpsweep.units(root))
    os.symlink(outside, root / "-linked-slug")                            # a slug that is a link: none of its units
    (outside / SID_B).mkdir()
    assert all("-linked-slug" not in str(p) for p, _s in tmpsweep.units(root))
    assert tmpsweep.classify(root / "-linked-slug" / SID_B, SID_B, Fake(), time.time())[1] == tmpsweep.KEPT_LINK


def test_PROPOSES_by_default_and_removes_nothing(box):
    import tmpsweep, destructive
    root, _ = box
    u = unit(root, SID_A, 9)
    out = tmpsweep.run_pass(False, False)
    assert u.exists() and out["scratch"]["proposed"][0]["action"] == "remove"
    line = tmpsweep.facts_line()
    assert line and "scratch-sweep: proposing only" in line and "tmp_sweep_apply" in line
    assert destructive.applies("scratch-sweep") is False


def test_one_pass_acts_on_at_most_max_per_pass_folders(box):
    import tmpsweep
    root, vals = box
    unit(root, SID_A, 9); unit(root, SID_B, 9)
    vals["tmp_sweep_max_per_pass"] = 1
    res = tmpsweep.sweep(apply=True)
    assert len(res["removed"]) == 1 and any(r == tmpsweep.KEPT_BOUND for _p, r in res["kept"])
    assert len(tmpsweep.sweep(apply=True, everything=True)["removed"]) == 1


def _start(days: float) -> str:
    return time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() - days * DAY))


def test_LEFTOVER_a_dead_sessions_old_browser_is_ended_and_the_others_are_not(box, monkeypatch):
    import tmpsweep
    root, _ = box
    app = "/Applications/" + "Google Chrome.app"          # built from parts: a fixture string,
                                                            # never a real browser launcher (QUIETMAC-1)
    udd = f"--user-data-dir={root}/-Users-me-proj/{SID_A}/scratchpad/prof"
    Fake.session_pids = {500}
    Fake.cmds = {
        1: (0, _start(9), "launchd"),
        100: (1, _start(3), f"{app}/Contents/MacOS/Google Chrome --headless=new {udd}"),
        101: (100, _start(3), f"Google Chrome Helper --type=renderer {udd}"),
        200: (1, _start(0.2), f"chrome {udd}"),                                           # too young
        300: (1, _start(3), f"chrome --user-data-dir={root}/-Users-me-proj/{SID_B}/scratchpad/p"),   # live session
        400: (1, _start(3), "Claude Helper --user-data-dir=/opt/app/Application Support/Claude"),
        500: (1, _start(3), "claude"),
        501: (500, _start(3), f"chrome {udd}"),                                           # under a live session
    }
    # review MF4: what only NAMES a dead session's profile folder is not a browser and is never signalled
    Fake.cmds[600] = (1, _start(3), f"/usr/bin/ssh mini chrome-headless-shell {udd}")
    Fake.cmds[601] = (1, _start(3), f"/opt/py/bin/python drive.py {udd}")
    Fake.cmds[602] = (1, _start(3), f"{app}/Contents/MacOS/Google Chrome --headless "
                                    f"--user-data-dir=/tmp/{root.name}/-Users-me-proj/{SID_A}/p")   # not under this root
    CH = app + "/Contents/MacOS/Google Chrome"
    Fake.comms = {100: CH, 101: CH + " Helper", 200: CH, 300: CH, 400: "/opt/app/Claude Helper", 501: CH,
                  600: "/usr/bin/ssh", 601: "/opt/py/bin/python", 602: CH}
    Fake.live = {SID_B}
    sent = []
    monkeypatch.setattr(tmpsweep, "_term", lambda pid: sent.append(pid) or True)
    monkeypatch.setattr(tmpsweep.time, "sleep", lambda s: None)
    assert [i["pid"] for i in tmpsweep.end_leftovers(root, apply=False)["proposed"]] == [100, 101]
    assert sent == []
    res = tmpsweep.end_leftovers(root, apply=True)
    assert sent == [100, 101] and [i["pid"] for i in res["ended"]] == [100, 101]


def test_LEFTOVER_a_pid_that_now_names_another_process_is_not_signalled(box, monkeypatch):
    import tmpsweep
    root, _ = box
    udd = f"--user-data-dir={root}/-Users-me-proj/{SID_A}/scratchpad/prof"
    first = {1: (0, _start(9), "launchd"), 100: (1, _start(3), f"chrome {udd}")}
    n = {"made": 0}

    class Reused(Fake):
        def __init__(self):
            n["made"] += 1
            Fake.cmds = first if n["made"] == 1 else {1: first[1], 100: (1, _start(0.01), "vim notes.txt")}
            Fake.comms = {100: "/opt/chrome/chrome"}
            super().__init__()
    monkeypatch.setattr(tmpsweep, "Snapshot", Reused)
    sent = []
    monkeypatch.setattr(tmpsweep, "_term", lambda pid: sent.append(pid) or True)
    res = tmpsweep.end_leftovers(root, apply=True)
    assert sent == [] and [i["pid"] for i in res["kept"]] == [100]


def test_LEFTOVER_the_tmp_link_spelling_and_the_second_reading(box, monkeypatch):
    """`--user-data-dir=/tmp/claude-N/…` is the same folder as `/private/tmp/claude-N/…`; and at the
    moment of acting a pid that is no longer a browser, or whose session came alive, is kept."""
    import tmpsweep
    cmd = f"chrome --user-data-dir=/tmp/claude-501/-Users-me-proj/{SID_A}/p"
    Fake.cmds = {1: (0, _start(9), "launchd"), 100: (1, _start(3), cmd)}
    Fake.comms = {100: "/opt/chrome/chrome"}
    assert [i["pid"] for i in tmpsweep.end_leftovers(Path("/private/tmp/claude-501"), apply=False)["proposed"]] == [100]
    sent = []
    monkeypatch.setattr(tmpsweep, "_term", lambda pid: sent.append(pid) or True)
    for change in ("comm", "sid", "unread"):
        n = {"made": 0}

        class Second(Fake):
            def __init__(self):
                n["made"] += 1
                super().__init__()
                if n["made"] > 1:
                    if change == "comm":
                        self.comm = {100: "/usr/bin/vim"}
                    elif change == "sid":
                        self.sids = {SID_A}
                    else:
                        self.ok = False
        monkeypatch.setattr(tmpsweep, "Snapshot", Second)
        res = tmpsweep.end_leftovers(Path("/private/tmp/claude-501"), apply=True)
        assert sent == [] and [i["pid"] for i in res["kept"]] == [100], change
    Fake.readable = False                                                 # the first reading unread: nothing
    monkeypatch.setattr(tmpsweep, "Snapshot", Fake)
    assert tmpsweep.end_leftovers(Path("/private/tmp/claude-501"), apply=True) == {"ended": [], "proposed": [], "kept": []}


def test_a_write_DEEP_in_the_folder_counts_as_activity(box):
    """Review MF1: a session resumed under another id may still write into this folder, at any depth."""
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 9)
    deep = u / "scratchpad" / "a" / "b" / "c"; deep.mkdir(parents=True)
    age(u, 9)
    (deep / "new.txt").write_text("now")
    res = tmpsweep.sweep(apply=True)
    assert res["removed"] == [] and res["stripped"] == [] and (str(u), tmpsweep.KEPT_YOUNG) in res["kept"]


def test_a_second_strip_records_the_activity_it_was_judged_on(box):
    """Review MF3: stripped at day 2, used again, stripped again — the folder's seven days count
    from the later use, not from the first strip's line."""
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 8.5)
    led = u / "SWEPT.txt"
    led.write_text(f"last-activity: {time.time() - 8.5 * DAY}\n# header\nold line\n")
    t = time.time() - 6 * DAY; os.utime(led, (t, t)); os.utime(u, (t, t))  # the first strip, six days ago
    (u / "scratchpad" / "big2.db").write_bytes(b"0" * (2 << 20))
    t = time.time() - 2.5 * DAY; os.utime(u / "scratchpad" / "big2.db", (t, t))
    os.utime(u / "scratchpad", (t, t))                                    # the folder changed when the file was made
    assert tmpsweep.sweep(apply=True)["stripped"] == [str(u)]
    assert "old line" in led.read_text() and "big2.db" in led.read_text()
    assert 2.4 < (time.time() - tmpsweep.last_activity(u, SID_A)) / DAY < 2.6
    res = tmpsweep.sweep(apply=True)
    assert res["removed"] == [] and u.exists()


def test_a_directory_on_ANOTHER_VOLUME_is_never_entered(box, monkeypatch):
    """Review MF5: a disk image or a network volume mounted inside a dead session's folder."""
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 9)
    vol = u / "scratchpad" / "vol"; vol.mkdir(); (vol / "theirs.bin").write_bytes(b"0" * (2 << 20))
    age(u, 9)
    real = tmpsweep._own
    assert real(str(u / "tasks"), os.lstat(u).st_dev) and not real(str(u / "tasks"), -1)   # the volume decides
    monkeypatch.setattr(tmpsweep, "_own", lambda sub, dev: False if sub == str(vol) else real(sub, dev))
    assert all(str(vol) not in i[1] for i in tmpsweep.heavy(u, 1 << 20))
    res = tmpsweep.sweep(apply=True)
    assert (vol / "theirs.bin").exists() and res["removed"] == []
    assert not (u / "scratchpad" / "notes.md").exists()                   # the rest of the folder went
    assert any(why == "part of it could not be removed" for _p, why in res["kept"])


def test_the_sessions_own_folders_are_never_one_heavy_item_and_venv_needs_its_marker(box):
    """A `git init` in scratchpad/ itself does not make every note part of "a repository"."""
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 3)
    (u / "scratchpad" / ".git").mkdir()
    (u / "scratchpad" / "venv").mkdir(); (u / "scratchpad" / "venv" / "notes.txt").write_text("a folder named venv")
    (u / "scratchpad" / ".venv").mkdir(); (u / "scratchpad" / ".venv" / "pyvenv.cfg").write_text("home = x")
    age(u, 3)
    assert tmpsweep.sweep(apply=True)["stripped"] == [str(u)]
    assert (u / "scratchpad" / "notes.md").exists() and (u / "scratchpad" / "venv" / "notes.txt").exists()
    assert not (u / "scratchpad" / ".venv").exists() and not (u / "scratchpad" / "repo").exists()


def test_a_READ_ONLY_heavy_item_is_still_removed(box):
    import tmpsweep
    root, _ = box
    u = unit(root, SID_A, 3)
    os.chmod(u / "scratchpad" / "node_modules", 0o555)
    age(u, 3)
    assert tmpsweep.sweep(apply=True)["stripped"] == [str(u)] and not (u / "scratchpad" / "node_modules").exists()


def test_ONE_unreadable_session_file_and_nothing_is_touched(box, tmp_path, monkeypatch):
    """Review MF2, on the real Snapshot: a session nobody can name may be the one using a folder."""
    import tmpsweep, procs
    sess = tmp_path / "claude" / "sessions"; sess.mkdir()
    monkeypatch.setattr(procs, "pid_alive", lambda pid: True)
    monkeypatch.setattr(REAL, "_ps", lambda self: True)
    monkeypatch.setattr(REAL, "_cwds", lambda self: True)
    (sess / "1.json").write_text('{"pid": 111, "sessionId": "%s"}' % SID_B)
    assert REAL().ok is True
    (sess / "2.json").write_text('{"pid": 222, "sessio')                   # half written
    assert REAL().ok is False
    (sess / "2.json").write_text('{"pid": 222, "sessionId": null}')        # alive, no id
    assert REAL().ok is False
    (sess / "2.json").unlink()
    monkeypatch.setattr(procs, "pid_alive", lambda pid: False)             # none alive = unreadable
    assert REAL().ok is False


def test_the_apply_keys_are_off_shipped_and_on_under_the_atlas_profile():
    import limits
    assert limits.DEFAULTS["tmp_sweep_apply"] is False and limits.DEFAULTS["leftover_procs_apply"] is False
    assert limits.PROFILES["atlas"]["tmp_sweep_apply"] is True and limits.PROFILES["atlas"]["leftover_procs_apply"] is True
    assert "tmp_sweep_apply" not in limits.PROFILES["public"]


def test_term_refuses_pid_0_and_1(monkeypatch):
    import tmpsweep
    sent = []
    monkeypatch.setattr(tmpsweep.os, "kill", lambda pid, sig: sent.append((pid, sig)))   # never the real one
    assert tmpsweep._term(4242) is True and sent == [(4242, tmpsweep.signal.SIGTERM)]
    assert tmpsweep._term(0) is False and tmpsweep._term(1) is False and tmpsweep._term(-5) is False


def test_due_is_true_once_per_interval(box):
    import tmpsweep
    assert tmpsweep.due() is True and tmpsweep.due() is False
    tmpsweep._stamp().write_text(str(time.time() + 9e6))                  # a stamp from the future does not block
    assert tmpsweep.due() is True
