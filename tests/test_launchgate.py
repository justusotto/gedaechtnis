"""LAUNCHGATE-1 — the launch memory test reads the kernel's pressure level, never a share of swap.

Measured 2026-10-01: three launches refused at swap 79-82% of total while the pressure level read
normal, the memory level 39, and nothing was closeable. The rule now: a launch passes when the
pressure level is 1 (normal) AND the memory level is at or above `session_launch_memory_floor`;
`--owner-go "<words>"` launches past the memory test only, and is logged.

Every branch has a positive and a negative control. One test reads this machine's three readings
and checks only their shape. No other test reads this machine's memory, sessions or state: the
readings and the process list are injected, the state dir and config are redirected into tmp_path,
and every subprocess call in a launch is replaced by one that fails — a launch that reaches the
step that would start `screen` has passed every check before it.
"""
from __future__ import annotations
import importlib.util, json, os, sys
from pathlib import Path
from types import SimpleNamespace
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HOOKS))

FLOOR, SWAP = 20.0, 512.0


def m(pressure=1, level=39.0, free=1432.0, total=7168.0):
    return {"pressure": pressure, "level": level, "swap_free_mb": free, "swap_total_mb": total}


@pytest.fixture
def fl(tmp_path, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "Vault"))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("GEDAECHTNIS_MUX", "screen")
    monkeypatch.setenv("GEDAECHTNIS_SEAT", "a-seat")
    monkeypatch.delenv("GEDAECHTNIS_LIMITS", raising=False)
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    import fleet
    return fleet


# ------------------------------------------------------------------ the rule, on fake readings ----

def test_positive_normal_pressure_and_level_over_the_floor_passes_whatever_the_swap(fl):
    # The measured case: swap 5,736 of 7,168 MB in use (80%), pressure normal, level 39.
    assert fl.launch_memory(m(), FLOOR, SWAP) == (None, "")
    # Swap FULL and pressure normal: still no reason. Swap is never a reason by itself.
    assert fl.launch_memory(m(free=0.0), FLOOR, SWAP) == (None, "")


@pytest.mark.parametrize("pressure,word", [(2, "warn"), (4, "critical")])
def test_negative_warn_and_critical_refuse_with_the_numbers(fl, pressure, word):
    why, note = fl.launch_memory(m(pressure=pressure, level=35.0, free=992.0), FLOOR, SWAP)
    assert why and note == ""
    assert f"memory pressure is {word}" in why and f"pressure level {pressure} ({word})" in why
    assert "memory level 35" in why and "swap free 992 MB" in why
    assert "session_launch_swap_free_mb" not in why          # swap free is over its floor here


def test_negative_a_pressure_level_nobody_named_is_not_normal(fl):
    why, _ = fl.launch_memory(m(pressure=3), FLOOR, SWAP)
    assert why and "not normal" in why and "pressure level 3 (not a known level)" in why
    assert fl.launch_memory(m(pressure=0), FLOOR, SWAP)[0]


def test_the_level_floor_is_the_boundary(fl):
    assert fl.launch_memory(m(level=20.0), FLOOR, SWAP)[0] is None
    why, _ = fl.launch_memory(m(level=19.0), FLOOR, SWAP)
    assert why and "memory level 19 is under session_launch_memory_floor 20" in why
    assert fl.launch_memory(m(level=19.0), 0.0, SWAP)[0] is None       # floor 0: the level never refuses


def test_the_swap_backstop_is_named_only_beside_a_pressure_that_is_not_normal(fl):
    why, _ = fl.launch_memory(m(pressure=2, free=100.0), FLOOR, SWAP)
    assert "swap free is under session_launch_swap_free_mb 512 MB" in why
    assert fl.launch_memory(m(pressure=1, free=100.0), FLOOR, SWAP) == (None, "")     # negative
    why, _ = fl.launch_memory(m(pressure=2, free=100.0), FLOOR, 0.0)                  # backstop off
    assert why and "session_launch_swap_free_mb" not in why
    why, _ = fl.launch_memory(m(pressure=2, free=0.0, total=0.0), FLOOR, SWAP)        # no swap at all
    assert why and "session_launch_swap_free_mb" not in why


def test_an_unreadable_pressure_level_falls_back_to_the_memory_level_in_words(fl):
    why, note = fl.launch_memory(m(pressure=None, level=39.0), FLOOR, SWAP)
    assert why is None and "pressure level could not be read" in note and "memory level alone" in note
    why, note = fl.launch_memory(m(pressure=None, level=10.0), FLOOR, SWAP)
    assert why and "under session_launch_memory_floor" in why and "pressure level unreadable" in why
    # Swap low with a readable level: the level decides, not the swap.
    assert fl.launch_memory(m(pressure=None, level=39.0, free=10.0), FLOOR, SWAP)[0] is None


def test_an_unreadable_memory_level_is_said_and_the_pressure_level_decides(fl):
    why, note = fl.launch_memory(m(level=None), FLOOR, SWAP)
    assert why is None and "memory level could not be read" in note
    assert fl.launch_memory(m(pressure=4, level=None), FLOOR, SWAP)[0]


def test_nothing_readable_never_passes_silently(fl):
    why, note = fl.launch_memory(m(None, None, None, None), FLOOR, SWAP)
    assert why is None and "launched without a memory test" in note
    assert "neither the pressure level nor the memory level could be read" in note
    why, note = fl.launch_memory(m(None, None, 100.0, 4096.0), FLOOR, SWAP)        # the backstop alone
    assert why and "neither the pressure level nor the memory level" in why and note == ""
    assert fl.launch_memory(m(None, None, 600.0, 4096.0), FLOOR, SWAP)[0] is None  # negative
    assert fl.launch_memory(m(None, None, 512.0, 4096.0), FLOOR, SWAP)[0] is None  # the boundary
    assert fl.launch_memory(m(None, None, 511.9, 4096.0), FLOOR, SWAP)[0]


def test_a_level_just_under_the_floor_never_prints_as_the_floor(fl):
    why, _ = fl.launch_memory(m(level=19.96), FLOOR, SWAP)
    assert "memory level 19.9 is under session_launch_memory_floor 20" in why
    assert "memory level 20" not in why
    assert fl._level(39.0) == "39" and fl._level(19.6) == "19.6"


def test_the_help_names_what_this_refusal_was_judged_by(fl):
    h = fl.launch_memory_help(m(pressure=2), FLOOR, SWAP)
    assert "pressure level is 1 (normal) and the memory level is at least 20" in h and "--owner-go" in h
    h = fl.launch_memory_help(m(pressure=None, level=10.0), FLOOR, SWAP)
    assert "It passes when the memory level is at least 20" in h and "pressure level is 1" not in h
    h = fl.launch_memory_help(m(None, None, 100.0, 4096.0), FLOOR, SWAP)
    assert "swap free is at least 512 MB" in h and "pressure level is 1" not in h
    assert "those decide" in h


@pytest.mark.parametrize("key,fn,shipped", [("session_launch_memory_floor", "launch_level_floor", 20.0),
                                            ("session_launch_swap_free_mb", "launch_swap_free_floor_mb", 512.0)])
@pytest.mark.parametrize("raw", [True, "lots", None, -1, float("nan"), float("inf")])
def test_a_bad_number_falls_back_to_the_shipped_one(fl, monkeypatch, key, fn, shipped, raw):
    import limits
    real = limits.get
    monkeypatch.setattr(limits, "get", lambda k, d=None: raw if k == key else real(k, d))
    assert getattr(fl, fn)() == shipped


def test_the_shipped_numbers_and_a_configured_one(fl, tmp_path, monkeypatch):
    assert fl.launch_level_floor() == 20.0 and fl.launch_swap_free_floor_mb() == 512.0
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"limits": {"session_launch_memory_floor": 35,
                                          "session_launch_swap_free_mb": 0}}))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    assert fl.launch_level_floor() == 35.0 and fl.launch_swap_free_floor_mb() == 0.0


def test_launch_readings_reads_this_machine_or_says_none(fl):
    r = fl.launch_readings()
    assert set(r) == {"pressure", "level", "swap_free_mb", "swap_total_mb"}
    assert r["pressure"] is None or isinstance(r["pressure"], int)
    assert r["level"] is None or 0 <= r["level"] <= 100


def test_launch_readings_parses_the_three_sysctls(fl, monkeypatch):
    vals = {"kern.memorystatus_vm_pressure_level": "2", "kern.memorystatus_level": "35",
            "vm.swapusage": "total = 8192.00M  used = 7200.12M  free = 991.88M  (encrypted)"}
    monkeypatch.setattr(fl.common, "platform", lambda: "macos")
    monkeypatch.setattr(fl, "_sysctl", lambda name: vals.get(name))
    assert fl.launch_readings() == {"pressure": 2, "level": 35.0, "swap_free_mb": 991.88,
                                    "swap_total_mb": 8192.0}
    monkeypatch.setattr(fl, "_sysctl", lambda name: None)                  # a Mac without them
    assert fl.launch_readings() == NOTHING


NOTHING = {"pressure": None, "level": None, "swap_free_mb": None, "swap_total_mb": None}


@pytest.mark.parametrize("vals", [
    {"kern.memorystatus_vm_pressure_level": "normal", "kern.memorystatus_level": "lots",
     "vm.swapusage": "total = 1.2.3M  used = 1.00M  free = 0.5.5M"},
    {"kern.memorystatus_vm_pressure_level": "1.0", "kern.memorystatus_level": "101",
     "vm.swapusage": "vm.swapusage: unknown oid"},
    {"kern.memorystatus_vm_pressure_level": "", "kern.memorystatus_level": "nan",
     "vm.swapusage": ""},
    {"kern.memorystatus_vm_pressure_level": "1 2", "kern.memorystatus_level": "-1"},
])
def test_negative_garbage_from_sysctl_is_no_reading_and_no_traceback(fl, monkeypatch, vals):
    monkeypatch.setattr(fl.common, "platform", lambda: "macos")
    monkeypatch.setattr(fl, "_sysctl", lambda name: vals.get(name))
    assert fl.launch_readings() == NOTHING


def _meminfo(fl, monkeypatch, text):
    def read():
        if text is None:
            raise OSError("no /proc here")
        return text
    monkeypatch.setattr(fl.common, "platform", lambda: "linux")
    monkeypatch.setattr(fl, "Path", lambda _p: SimpleNamespace(read_text=read))
    return fl.launch_readings()


def test_the_linux_readings_have_no_pressure_level_and_a_checked_memory_level(fl, monkeypatch):
    good = "MemTotal: 1000 kB\nMemAvailable: 390 kB\nSwapTotal: 2097152 kB\nSwapFree: 1048576 kB\n"
    assert _meminfo(fl, monkeypatch, good) == {"pressure": None, "level": 39.0,
                                               "swap_free_mb": 1024.0, "swap_total_mb": 2048.0}
    assert _meminfo(fl, monkeypatch, None) == NOTHING
    assert _meminfo(fl, monkeypatch, "MemTotal: 1000 kB\n") == NOTHING
    # Under 0 is 0 (it refuses), never "unread"; a swap pair out of order is clamped, never dropped.
    r = _meminfo(fl, monkeypatch, "MemTotal: 1000 kB\nMemAvailable: -5 kB\nSwapTotal: 1024 kB\nSwapFree: 4096 kB\n")
    assert r == {"pressure": None, "level": 0.0, "swap_free_mb": 1.0, "swap_total_mb": 1.0}
    assert fl.launch_memory(r, FLOOR, SWAP)[0]
    r = _meminfo(fl, monkeypatch, "SwapTotal: 2097152 kB\nSwapFree: -5 kB\n")
    assert r["level"] is None and r["swap_free_mb"] == 0.0 and fl.launch_memory(r, FLOOR, SWAP)[0]
    for bad in ("MemTotal: 1000 kB\nMemAvailable: nan kB\n",
                "MemTotal: 1000 kB\nMemAvailable: 5000 kB\n",
                "MemTotal: 0 kB\nMemAvailable: 0 kB\n",
                "MemTotal: 1000 kB\nMemAvailable: 390 kB\nSwapTotal: nan kB\nSwapFree: 1 kB\n"):
        r = _meminfo(fl, monkeypatch, bad)
        assert r["swap_free_mb"] is None and r["swap_total_mb"] is None
        assert r["level"] in (None, 39.0) and (r["level"] is None) == ("Swap" not in bad)


# ------------------------------------------------------------------------- the launch itself ----

@pytest.fixture
def launch(fl, tmp_path, monkeypatch):
    """Run `cmd_launch` with injected readings. Returns (exit code, output, calls to the multiplexer).
    The multiplexer step fails by design: exit 1 with "failed:" means every check before it passed."""
    spec = importlib.util.spec_from_file_location("sessions_lg", TOOLS / "sessions.py")
    ses = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ses)
    launcher = tmp_path / "l.sh"
    launcher.write_text("#!/bin/zsh\nexec claude --name x\n")
    calls = []

    def fake_run(argv, **_kw):                                # every subprocess call, not only screen
        calls.append(argv)
        return SimpleNamespace(returncode=1, stdout="", stderr="no screen in a test")

    def run(readings, *, owner_go=None, procs=(), cap=0, name="x", capsys=None):
        monkeypatch.setattr(ses.fleet, "launch_readings", lambda: readings)
        monkeypatch.setattr(ses.fleet, "claude_processes", lambda: list(procs))
        monkeypatch.setattr(ses.fleet, "working_count", lambda p, status_fn=None: len(p))
        monkeypatch.setattr(ses.fleet, "cap", lambda: cap)
        monkeypatch.setattr(ses.fleet, "sweep", lambda **_kw: [])
        monkeypatch.setattr(ses.subprocess, "run", fake_run)
        del calls[:]
        argv = ["launch", "--name", name, "--cwd", str(tmp_path)]
        if owner_go is not None:
            argv += ["--owner-go", owner_go]
        code = ses.main(argv + [str(launcher)])
        return code, capsys.readouterr().out, list(calls)

    run.log = tmp_path / "state" / "owner-go.log"
    return run


def test_positive_a_launch_passes_at_normal_pressure_with_swap_80_percent_used(launch, capsys):
    code, out, calls = launch(m(), capsys=capsys)
    assert code == 1 and "failed:" in out and "refused" not in out and len(calls) == 1
    assert not launch.log.exists()                          # no override, no log line


def test_negative_a_launch_at_warn_is_refused_and_says_what_would_pass(launch, capsys):
    code, out, calls = launch(m(pressure=2, level=35.0, free=992.0), capsys=capsys)
    assert code == 3 and calls == []
    assert out.startswith("refused (memory): memory pressure is warn")
    assert "pressure level is 1 (normal)" in out and "at least 20" in out and "--owner-go" in out
    assert "session_swap_cap_share" not in out
    assert not launch.log.exists()


def test_negative_a_launch_under_the_level_floor_is_refused(launch, capsys):
    code, out, calls = launch(m(level=12.0), capsys=capsys)
    assert code == 3 and calls == [] and "memory level 12 is under" in out


def test_positive_owner_go_launches_past_the_memory_test_and_is_logged(launch, capsys):
    words = "Shouldn't be a problem\tif I give\nthe go."
    code, out, calls = launch(m(pressure=4, level=8.0, free=40.0), owner_go=words, capsys=capsys)
    assert code == 1 and "failed:" in out and len(calls) == 1          # it reached the launch step
    assert "owner-go: launching past the memory test" in out and "owner-go.log" in out
    lines = launch.log.read_text().splitlines()
    assert len(lines) == 1
    cols = lines[0].split("\t")
    assert len(cols) == 7 and cols[1:4] == ["launch", "x", "by a-seat"]
    assert cols[4] == "pressure level 4 (critical), memory level 8, swap free 40 MB"
    assert cols[5].startswith("past: memory pressure is critical")
    assert cols[6] == "words: Shouldn't be a problem if I give the go."


def test_owner_go_is_not_logged_when_the_memory_test_passes_anyway(launch, capsys):
    code, out, calls = launch(m(), owner_go="go", capsys=capsys)
    assert code == 1 and len(calls) == 1 and "owner-go:" not in out and not launch.log.exists()


@pytest.mark.parametrize("words", ["", "   ", "\t\n", "\u200b", "\ufeff\u2060", "\x1b", "...", "!?"])
def test_negative_an_empty_owner_go_is_no_go(launch, capsys, words):
    code, out, calls = launch(m(pressure=2), owner_go=words, capsys=capsys)
    assert code == 2 and calls == [] and "an empty go is not a go" in out and not launch.log.exists()


def test_negative_owner_go_does_not_pass_the_session_cap(launch, capsys):
    procs = [{"pid": 10_000 + i, "name": f"s{i}"} for i in range(3)]
    code, out, calls = launch(m(pressure=2), owner_go="go ahead", procs=procs, cap=3, capsys=capsys)
    assert code == 3 and calls == [] and out.startswith("refused (cap): 3 claude sessions are working")
    assert "never a cap" in out and not launch.log.exists()
    # Positive control for the cap itself: under it, the same go launches.
    code, out, calls = launch(m(pressure=2), owner_go="go ahead", procs=procs, cap=4, capsys=capsys)
    assert code == 1 and len(calls) == 1 and launch.log.exists()


def test_negative_owner_go_does_not_pass_the_seats_cap(launch, capsys, fl, tmp_path, monkeypatch):
    # One working session this seat launched is live (this test process), and the seat's cap is 1.
    me = os.getpid()
    monkeypatch.setattr(fl, "pid_start", lambda pid: "T" if pid == me else None)   # no `ps` here
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "launches.jsonl").write_text(json.dumps(
        {"name": "other", "pid": me, "pid_start": "T", "seat": "a-seat"}) + "\n")
    monkeypatch.setattr(fl, "seat_cap", lambda: 1)
    code, out, calls = launch(m(pressure=2), owner_go="go ahead", capsys=capsys)
    assert code == 3 and calls == [] and out.startswith("refused (cap):") and "a-seat" in out
    assert "never a cap" in out and not launch.log.exists()
    # Positive control for the seat's cap: at 2, the same go launches.
    monkeypatch.setattr(fl, "seat_cap", lambda: 2)
    code, out, calls = launch(m(pressure=2), owner_go="go ahead", capsys=capsys)
    assert code == 1 and len(calls) == 1 and launch.log.exists()


def test_the_words_stay_one_readable_column(launch, capsys, fl):
    words = "go\x1b[2J\x08\x07\x7f now\u2028yes\r\n\udcff ok \u200b"
    code, out, calls = launch(m(pressure=2), owner_go=words, capsys=capsys)
    assert code == 1 and len(calls) == 1
    line = launch.log.read_text(encoding="utf-8")
    assert line.count("\n") == 1 and len(line.split("\t")) == 7
    assert all(c.isprintable() or c in "\t\n" for c in line)
    assert line.rstrip("\n").split("\t")[6] == "words: go [2J now yes ok"


def test_long_words_are_cut_and_the_cut_is_said(launch, capsys, fl):
    code, out, _ = launch(m(pressure=2), owner_go="x" * 620, capsys=capsys)
    assert code == 1 and "(the first 500 characters)" in out
    col = launch.log.read_text(encoding="utf-8").rstrip("\n").split("\t")[6]
    assert col == "words: " + "x" * 500 + " … [+120 chars]"
    launch.log.unlink()
    code, out, _ = launch(m(pressure=2), owner_go="y" * 500, capsys=capsys)       # negative: fits
    assert "the first 500" not in out
    assert launch.log.read_text(encoding="utf-8").rstrip("\n").endswith("words: " + "y" * 500)


def test_negative_owner_go_does_not_pass_the_duplicate_check(launch, capsys):
    procs = [{"pid": 10_001, "name": "x"}]                  # a live claude process with this name
    code, out, calls = launch(m(pressure=2), owner_go="go ahead", procs=procs, capsys=capsys)
    assert code == 3 and calls == [] and "refused (duplicate)" in out and not launch.log.exists()
    code, out, calls = launch(m(pressure=2), owner_go="go ahead", procs=procs, name="y",
                              capsys=capsys)
    assert code == 1 and len(calls) == 1                    # another name: the go applies


def test_negative_a_go_that_cannot_be_logged_does_not_apply(launch, capsys, fl, monkeypatch):
    monkeypatch.setattr(fl, "owner_go_log", lambda *a, **k: False)
    code, out, calls = launch(m(pressure=2), owner_go="go ahead", capsys=capsys)
    assert code == 3 and calls == [] and "could not be written" in out


def test_owner_go_log_returns_false_when_the_file_cannot_be_written(fl, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "owner-go.log").mkdir()                        # a directory where the log should be
    assert fl.owner_go_log("x", "go", "why", m(), "a-seat") is False
    (state / "owner-go.log").rmdir()
    assert fl.owner_go_log("x", "go", "why", m(), "a-seat") is True


def test_a_missing_reading_is_printed_at_launch(launch, capsys):
    code, out, calls = launch(m(pressure=None), capsys=capsys)
    assert code == 1 and len(calls) == 1 and "note: the pressure level could not be read" in out
    code, out, _ = launch(m(None, None, None, None), capsys=capsys)
    assert "note: neither the pressure level nor the memory level could be read" in out
    code, out, _ = launch(m(), capsys=capsys)
    assert "note:" not in out                               # negative: nothing missing, no note


def test_the_swap_share_no_longer_refuses_a_launch(launch, capsys, tmp_path, monkeypatch, fl):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"limits": {"session_swap_cap_share": 0.75}}))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    import limits
    monkeypatch.setattr(limits, "_STATE", None, raising=False)
    monkeypatch.setattr(fl, "memory_share", lambda: (0.9, "swap"))
    assert fl.swap_cap() == 0.75                            # the old line is configured and "over"
    code, out, calls = launch(m(), capsys=capsys)
    assert code == 1 and len(calls) == 1 and "refused" not in out
