"""test_clock_seam.py — `GEDAECHTNIS_TODAY`, and the five behaviours that were unreachable without it.

★ WHY THIS FILE EXISTS. Every date-gated behaviour in the package read the real clock directly, so
no simulation could ever reach one: the dad test walks ninety simulated days inside a few real
seconds, all of them on the same calendar date, `days_between` is 0 throughout, and a 14-day
trigger cannot fire. Part A reported *"cleanup never became due in 90 days"* and that was read for
a day as a calibration finding about how much a real person writes. It was a fact about the
harness — and four more behaviours were in the same position, none of them ever exercised:

  1. the cleanup time trigger        `cleanup_trigger_days` (14)
  2. the synthesis time trigger      `synthesis_trigger_days` (14)
  3. the stale-entry report          `stale_entry_days` (56) on `Last revisited:`
  4. the once-per-day marker offer   a stamp compared against today
  5. the monthly log/ledger rollover `%Y-%m` in two stores

Each gets a POSITIVE control ON the day it must fire and one the day before, so a check that fires
on everything is caught. The NEGATIVE control is the whole point of the seam: **unset means the
real clock**, and a malformed value is IGNORED rather than honoured — a typo in a simulation's env
must never move a real vault's maintenance dates, and there is no correct date to guess.
"""
from __future__ import annotations

import datetime
import importlib
import os
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
for extra in (str(PLUGIN / "hooks"), str(PLUGIN)):
    if extra not in sys.path:
        sys.path.insert(0, extra)

import common  # noqa: E402


def day(base: str, n: int) -> str:
    return (datetime.date.fromisoformat(base) + datetime.timedelta(days=n)).isoformat()


@pytest.fixture
def seam(monkeypatch):
    """Set/clear `GEDAECHTNIS_TODAY` for one test, never leaking into another."""
    def _set(value: str | None):
        if value is None:
            monkeypatch.delenv(common.TODAY_ENV, raising=False)
        else:
            monkeypatch.setenv(common.TODAY_ENV, value)
    monkeypatch.delenv(common.TODAY_ENV, raising=False)
    return _set


# ------------------------------------------------------------------ the seam itself ----
def test_unset_is_the_real_clock_and_that_is_the_product(seam):
    """NEGATIVE CONTROL. Everything below is reachable only because the seam exists; this is the
    assertion that its existence changes nothing for anyone who does not set it."""
    seam(None)
    assert common.today() == time.strftime("%Y-%m-%d")
    assert common.month() == time.strftime("%Y-%m")


def test_a_malformed_seam_is_IGNORED_not_honoured(seam):
    """A typo must not move a real vault's maintenance dates, and there is no correct date to
    guess. Includes a date that is well-formed and impossible — the shape check alone would take
    it."""
    for junk in ("", "   ", "banana", "2026-13-01", "2026-02-30", "26-01-01", "2026-1-1",
                 "2026-01-01T00:00:00", "tomorrow",
                 # ★ THESE TWO FOUND A HOLE IN THIS TEST. Removing the `_ISO_DAY` shape check left
                 # every value above still rejected — by `date.fromisoformat`, which the guard
                 # calls anyway — so the mutation came back GREEN and the shape check read as a
                 # redundant guard. It is not: `fromisoformat` ACCEPTS the compact form and ISO
                 # week dates. `20260101` would be returned verbatim and `month()` would slice it
                 # to `2026010`, naming a log file after a month that does not exist.
                 "20260101", "2026-W01-1"):
        seam(junk)
        assert common.today() == time.strftime("%Y-%m-%d"), junk


def test_a_well_formed_seam_is_taken_and_month_follows_it(seam):
    seam("2026-02-14")
    assert common.today() == "2026-02-14" and common.month() == "2026-02"
    seam(" 2026-12-31 ")
    assert common.today() == "2026-12-31", "surrounding whitespace should not defeat it"


def test_every_seamed_module_reads_the_ONE_clock(seam):
    """The seam is worth nothing if a module keeps its own `strftime`. Each of these has a
    `today()` of its own by name; they must all be the same clock."""
    import maintenance
    sys.path.insert(0, str(PLUGIN))
    import cleanup, split, synthesis
    seam("2026-04-05")
    for mod in (maintenance, cleanup, split, synthesis):
        assert mod.today() == "2026-04-05", mod.__name__


# -------------------------------------------- 1 + 2. the cleanup and synthesis time triggers ----
def _arms(monkeypatch, tmp_path, state: dict, on: str) -> dict:
    import maintenance
    monkeypatch.setenv(common.TODAY_ENV, on)
    return maintenance.compute(state, str(tmp_path), maintenance.today())


def test_cleanup_becomes_due_on_the_day_its_threshold_says_and_not_the_day_before(
        monkeypatch, tmp_path):
    import limits, maintenance
    base = "2026-03-01"
    n = int(limits.get("cleanup_trigger_days"))
    state = {"last_cleanup": base, "last_synthesis": base}
    before = _arms(monkeypatch, tmp_path, state, day(base, n - 1))
    on_the_day = _arms(monkeypatch, tmp_path, state, day(base, n))
    assert before["cleanup"]["arms"]["time"]["fired"] is False, before["cleanup"]["arms"]["time"]
    assert on_the_day["cleanup"]["arms"]["time"]["fired"] is True
    assert on_the_day["cleanup"]["arms"]["time"]["days"] == n


def test_synthesis_becomes_due_on_the_day_its_threshold_says_and_not_the_day_before(
        monkeypatch, tmp_path):
    import limits
    base = "2026-03-01"
    n = int(limits.get("synthesis_trigger_days"))
    state = {"last_cleanup": base, "last_synthesis": base}
    before = _arms(monkeypatch, tmp_path, state, day(base, n - 1))
    on_the_day = _arms(monkeypatch, tmp_path, state, day(base, n))
    assert before["synthesis"]["arms"]["time"]["fired"] is False
    assert on_the_day["synthesis"]["arms"]["time"]["fired"] is True


def test_a_FROZEN_clock_can_never_fire_either_one_which_is_the_defect_this_row_names(
        monkeypatch, tmp_path):
    """★ THE REGRESSION THIS ROW EXISTS FOR, asserted rather than described: pinned to one date —
    which is what ninety simulated days inside a few real seconds amounted to — no number of days
    reaches any time threshold."""
    frozen = "2026-03-01"
    state = {"last_cleanup": frozen, "last_synthesis": frozen}
    doc = _arms(monkeypatch, tmp_path, state, frozen)
    assert doc["cleanup"]["arms"]["time"]["days"] == 0
    assert doc["cleanup"]["arms"]["time"]["fired"] is False
    assert doc["synthesis"]["arms"]["time"]["fired"] is False


# ------------------------------------------------------ 3. the stale-entry report ----
def test_an_entry_is_stale_on_the_day_the_horizon_says_and_not_the_day_before(seam):
    sys.path.insert(0, str(PLUGIN))
    import cleanup, limits
    n = int(limits.get("stale_entry_days"))
    base = "2026-01-10"
    text = ("# Open questions\n\n## Whether to move the beds\n\n"
            f"**Urgency:** NEXT\n**Last revisited:** {base}\n\nSome body text.\n")
    seam(day(base, n - 1))
    out, _unparsable = cleanup.detect_stale("x/Aporia.md", text, cleanup.today())
    assert out == [], f"called stale {n - 1} days in"
    seam(day(base, n))
    out, _unparsable = cleanup.detect_stale("x/Aporia.md", text, cleanup.today())
    assert len(out) == 1, out


def test_under_a_frozen_clock_no_entry_can_ever_BE_old_enough(seam):
    """The same shape as the trigger case and worth its own assertion: a frozen-clock run reports a
    clean stale-entry sweep over a vault full of entries nobody has touched in a year."""
    sys.path.insert(0, str(PLUGIN))
    import cleanup
    base = "2026-01-10"
    text = f"# Open questions\n\n## A\n\n**Last revisited:** {base}\n\nbody\n"
    seam(base)
    out, _ = cleanup.detect_stale("x/Aporia.md", text, cleanup.today())
    assert out == []


# ------------------------------------------------- 4. the once-a-day marker offer ----
def test_the_marker_offer_repeats_the_NEXT_day_and_not_the_same_day(seam, tmp_path, monkeypatch):
    """"Offered again tomorrow" had never been tested, because under a frozen clock there is no
    tomorrow.

    ★ THE FIRST VERSION OF THIS TEST WAS DECORATIVE and a mutation proved it: it wrote the stamp
    itself and compared it against `common.today()`, so it asserted that `common.today()` equals
    `common.today()` and passed happily while `session_start.py` kept its own `strftime`. It now
    drives `no_memory_line()` — the real function — and reads the stamp the HOOK wrote."""
    import subprocess
    import session_start
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    vault = tmp_path / "vault"
    (vault / "Global").mkdir(parents=True)
    (vault / "Global" / "fleet-roster.md").write_text("```fleet-roster\n```\n", encoding="utf-8")
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(vault))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-such-config.json"))

    seam("2026-05-01")
    first = session_start.no_memory_line(str(repo))
    again = session_start.no_memory_line(str(repo))
    assert first != session_start.NO_MARKER, "the offer should be made on a fresh repo"
    assert again == session_start.NO_MARKER, "twice in one day is a nag"

    seam("2026-05-02")
    tomorrow = session_start.no_memory_line(str(repo))
    assert tomorrow != session_start.NO_MARKER, "the offer must come back the NEXT day"

    import hashlib
    stamp = (tmp_path / "state" /
             ("offer-" + hashlib.sha1(str(repo).encode()).hexdigest() + ".stamp"))
    assert stamp.read_text(encoding="utf-8").strip() == "2026-05-02", \
        "the hook must stamp the seamed day, not the machine's"


# ---------------------------------------------------- 5. the monthly rollover ----
def test_the_log_and_ledger_roll_over_at_a_month_boundary(seam, monkeypatch, tmp_path):
    sys.path.insert(0, str(PLUGIN))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    import ledger, logstore
    seam("2026-01-31")
    jan = (ledger.month_file(), logstore.month_file())
    seam("2026-02-01")
    feb = (ledger.month_file(), logstore.month_file())
    assert jan[0].name == "2026-01.tsv" and feb[0].name == "2026-02.tsv"
    assert jan[1].name == "2026-01.tsv" and feb[1].name == "2026-02.tsv"
    assert jan != feb


def test_a_frozen_clock_never_rolls_a_month_over(seam):
    sys.path.insert(0, str(PLUGIN))
    import ledger
    seam("2026-01-31")
    a = ledger.month_file()
    b = ledger.month_file()
    assert a == b


# ------------------------------------------------------ the harnesses advance it ----
def test_the_sandbox_advances_its_calendar_and_hands_it_to_every_subprocess(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_clockseam_sim",
                                                  PLUGIN / "eval" / "simulator" / "run.py")
    sim = importlib.util.module_from_spec(spec)
    sys.modules["_clockseam_sim"] = sim
    spec.loader.exec_module(sim)
    sb = sim.Sandbox(tmp_path / "s", start_day="2026-01-01")
    assert sb.advance(1) == "2026-01-01"
    assert sb.advance(15) == "2026-01-15", "day 15 is where a 14-day trigger first fires"
    assert sb.env["GEDAECHTNIS_TODAY"] == "2026-01-15", "the subprocess must see the same day"
    assert os.environ["GEDAECHTNIS_TODAY"] == "2026-01-15", "in-process callers too"
    assert sb.advance(90) == "2026-03-31", "ninety days must cross month boundaries"
    del os.environ["GEDAECHTNIS_TODAY"]

    plain = sim.Sandbox(tmp_path / "p")
    assert plain.advance(9) is None, "no calendar means the real clock"
    assert "GEDAECHTNIS_TODAY" not in plain.env


def test_the_calendar_ends_on_TODAY_so_the_sandboxs_other_clocks_agree(tmp_path):
    """Git commit times and file mtimes inside the sandbox still say `now`. A vault whose own
    maintenance dates sat months in the past would put the two in disagreement — a `--since
    <last_cleanup>` sweep over a window that, by the vault's reckoning, had not happened yet."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_clockseam_sim2",
                                                  PLUGIN / "eval" / "simulator" / "run.py")
    sim = importlib.util.module_from_spec(spec)
    sys.modules["_clockseam_sim2"] = sim
    spec.loader.exec_module(sim)
    start = sim.calendar_start(90)
    assert day(start, 89) == datetime.date.today().isoformat()
    assert sim.calendar_start(90, "2020-01-01") == "2020-01-01", "--start-day pins it"
