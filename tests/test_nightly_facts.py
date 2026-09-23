"""NIGHTLY-1 item (a) — the last nightly invariant run, said once at SessionStart.

The runner has appended a record a night for weeks and nothing read it. These controls are about
what a reader ACTS on, so most of them are about what the line does NOT say: a ten-field record
printed verbatim every session is a wall of green tokens, and the one FAIL in it arrives looking
exactly like the nine PASSes.

Every record here is built field-by-field rather than pasted from the live log, and no test asserts
a field's POSITION — the runner's own header says consumers read gates by name, and a suite that
pinned column order would break the next time a gate is appended, which is precisely the failure
item (b) of this row exists to clean up.
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

import nightly  # noqa: E402


def rec(*fields: str) -> str:
    return "\t".join(fields)


def log_with(tmp_path: Path, *lines: str) -> Path:
    p = tmp_path / "invariant-runs.log"
    p.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    return p


def stamp(now: float, days_ago: float = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - days_ago * 86400))


@pytest.fixture
def now():
    return time.time()


# ------------------------------------------------------------------ the clean case ----

def test_a_passing_run_gets_ONE_SHORT_LINE_and_names_no_gate(tmp_path, now):
    """★ The green record must not be printed. Nine PASS tokens every session is how a reader
    learns to skip the line, and then the FAIL that eventually appears is skipped too."""
    f = log_with(tmp_path, rec(stamp(now), "OK", "duration=9s", "pytest=PASS 1p/0f/0e/0s",
                               "mission-ledger=PASS 7 verified", "placement=PASS 0 findings"))
    line = nightly.facts_line(f, now)
    assert line == "- Nightly invariants: last run today — OK."
    for token in ("pytest", "mission-ledger", "placement", "PASS"):
        assert token not in line, f"a passing gate was named: {line}"


def test_a_failing_run_names_the_FAILING_gates_and_only_those(tmp_path, now):
    f = log_with(tmp_path, rec(stamp(now), "FAIL", "duration=9s", "pytest=PASS 1p/0f/0e/0s",
                               "notice-check=FAIL 45 of 528 invalid",
                               "mission-ledger=PASS 7 verified"))
    line = nightly.facts_line(f, now)
    assert "notice-check=FAIL 45 of 528 invalid" in line
    assert "pytest" not in line and "mission-ledger" not in line


def test_a_failing_gate_keeps_its_WHOLE_value(tmp_path, now):
    """`FAIL 45 of` is a number with its denominator amputated, and it reads as a smaller problem
    than `FAIL 45 of 528 invalid`. A word-capped prefix did exactly that before this control."""
    f = log_with(tmp_path, rec(stamp(now), "FAIL", "duration=1s",
                               "notice-check=FAIL 45 of 528 invalid"))
    assert "45 of 528 invalid" in nightly.facts_line(f, now)


# ---------------------------------------------------- UNREADABLE is not a pass ----

def test_UNREADABLE_is_reported_as_a_failure(tmp_path, now):
    """★ An arm that could not read what it needed has NOT passed. Treating UNREADABLE as anything
    else is the false-reassurance direction, and this vault's own rule is that a blocked listing is
    never read as a zero."""
    f = log_with(tmp_path, rec(stamp(now), "FAIL", "duration=1s",
                               "placement=UNREADABLE 2 arm(s) could-not-read"))
    line = nightly.facts_line(f, now)
    assert "placement=UNREADABLE" in line


def test_an_embedded_route_half_does_not_make_a_PASSING_gate_fail(tmp_path, now):
    """★ THIS TEST USED TO PASS FOR THE WRONG REASON, and a mutation is what said so.

    It was written to cover a `_OWN_VOCABULARY` exemption for `route=`, on the strength of the
    runner's header calling it a gate with its own vocabulary. Deleting that exemption left this
    green — because `route=` is never a FIELD at all. The runner appends it INSIDE field 8, onto
    the owner-pages value (`nightly_invariants.sh:356,363`), so nothing is ever named `route` and
    the exemption's trigger condition could not occur. A check whose trigger never occurs is
    vacuous, and its green is a fact about the fixture.

    What this now drives is the real behaviour: the field is judged on its OWN head."""
    f = log_with(tmp_path, rec(stamp(now), "OK", "duration=1s",
                               "owner-pages=PASS 0 uncollected route=ERR script-missing"))
    assert nightly.facts_line(f, now) == "- Nightly invariants: last run today — OK."


def test_a_FAILING_owner_pages_gate_is_reported_WITH_its_route_half(tmp_path, now):
    """The other direction, which the vacuous version could not see: when the gate itself fails,
    the whole value is reported, route half included, because that half is part of what a reader
    needs to act."""
    f = log_with(tmp_path, rec(stamp(now), "FAIL", "duration=1s",
                               "owner-pages=FAIL script-missing route=ERR script-missing"))
    line = nightly.facts_line(f, now)
    assert "owner-pages=FAIL script-missing route=ERR script-missing" in line


# ---------------------------------------------------------------- staleness ----

def test_a_STALE_passing_run_says_how_old_it_is(tmp_path, now):
    """★ "The last run passed" and "the last run was six days ago" are different facts, and a
    reader told only the first acts on a verdict about a repo that has changed underneath it."""
    f = log_with(tmp_path, rec(stamp(now, 6), "OK", "duration=1s", "pytest=PASS 1p"))
    line = nightly.facts_line(f, now)
    assert "6 days ago" in line


def test_today_and_yesterday_are_said_in_words(tmp_path, now):
    assert "today" in nightly.facts_line(
        log_with(tmp_path, rec(stamp(now), "OK", "d=1s")), now)
    assert "yesterday" in nightly.facts_line(
        log_with(tmp_path, rec(stamp(now, 1), "OK", "d=1s")), now)


def test_an_UNPARSEABLE_stamp_is_not_reported_as_fresh(tmp_path, now):
    """`age_days` returns None rather than 0. A stamp that cannot be read is not a recent one, and
    quietly calling it "today" would make a broken writer look like a healthy nightly."""
    assert nightly.age_days("not-a-date") is None
    f = log_with(tmp_path, rec("not-a-date", "OK", "d=1s"))
    assert "not a readable date" in nightly.facts_line(f, now)


# ---------------------------------------------------------------- reading the log ----

def test_the_NEWEST_record_is_the_one_reported(tmp_path, now):
    f = log_with(tmp_path,
                 rec(stamp(now, 2), "FAIL", "d=1s", "pytest=FAIL 0p/1f/0e/0s"),
                 rec(stamp(now), "OK", "d=1s", "pytest=PASS 1p/0f/0e/0s"))
    line = nightly.facts_line(f, now)
    assert line.endswith("OK.") and "pytest" not in line


def test_comments_and_blank_lines_are_not_records(tmp_path, now):
    f = log_with(tmp_path, "# a header someone added", "",
                 rec(stamp(now), "OK", "d=1s"), "")
    assert nightly.facts_line(f, now) == "- Nightly invariants: last run today — OK."


def test_a_log_with_no_records_says_NOTHING(tmp_path, now):
    """Silence, not a reassuring line. A header-only log means the nightly has never written a
    record, which is not the same as a run that passed."""
    assert nightly.facts_line(log_with(tmp_path, "# header only"), now) is None


def test_a_missing_log_says_nothing(tmp_path, now):
    assert nightly.facts_line(tmp_path / "absent.log", now) is None


def test_no_configured_log_says_nothing(monkeypatch):
    """The shipped state. A vault whose fleet runs no nightly has no line to report."""
    import config
    monkeypatch.setattr(config, "invariants_log", lambda: None)
    assert nightly.facts_line() is None


def test_a_malformed_record_is_reported_as_malformed(tmp_path, now):
    """Not silently skipped: a log whose newest line has one field is a broken WRITER, and the
    session that could notice is the one being spoken to."""
    f = log_with(tmp_path, "just-one-field")
    assert "unreadable" in nightly.facts_line(f, now)


def test_a_FAIL_verdict_with_no_failing_gate_is_reported_as_the_contradiction_it_is(tmp_path, now):
    """The runner sets the verdict from its gates, so this state should be impossible — which is
    exactly why it must not print a bare "FAIL." with nothing named. Saying the two disagree points
    at the runner; saying nothing points at nobody."""
    f = log_with(tmp_path, rec(stamp(now), "FAIL", "d=1s", "pytest=PASS 1p"))
    line = nightly.facts_line(f, now)
    assert "contradicts" in line


def test_gates_are_read_by_NAME_not_by_column(tmp_path, now):
    """★ The runner's header promises that appending a field is a safe extension because consumers
    split on `=`. This drives that promise: the same two gates, in two different orders, with an
    extra field appended, must produce the same finding."""
    a = log_with(tmp_path / "a", rec(stamp(now), "FAIL", "d=1s",
                                     "pytest=PASS 1p", "notice-check=FAIL 2 invalid"))
    (tmp_path / "b").mkdir(exist_ok=True)
    b = log_with(tmp_path / "b", rec(stamp(now), "FAIL", "d=1s", "notice-check=FAIL 2 invalid",
                                     "pytest=PASS 1p", "brand-new-gate=PASS 0"))
    assert nightly.facts_line(a, now) == nightly.facts_line(b, now)


@pytest.fixture(autouse=True)
def _mkdir_a(tmp_path):
    (tmp_path / "a").mkdir(exist_ok=True)
    yield


# ------------------------------- the reviewer's three untested paths ----

def test_a_FUTURE_stamp_is_named_not_clamped_to_today(tmp_path, now):
    """★ A mutation removing the `max(0, ...)` clamp stayed green, which is how the clamp got
    looked at — and looking at it showed the clamp was the wrong behaviour, not just untested.

    A record dated ahead of now means this machine's clock and the writer's disagree. Clamping
    turned that into "last run today", the single most reassuring line this module can print, for
    a symptom that deserves naming."""
    f = log_with(tmp_path, rec(stamp(now, -3), "OK", "d=1s"))
    line = nightly.facts_line(f, now)
    assert "FUTURE" in line and "today" not in line
    assert nightly.age_days(stamp(now, -3), now) < 0


def test_an_INDENTED_comment_is_still_a_comment(tmp_path, now):
    """`ln.lstrip().startswith('#')` vs `ln.startswith('#')` — a mutation to the latter stayed
    green because no fixture indented a comment. A leading-space comment read as a RECORD would
    be reported to the owner as the nightly's verdict."""
    f = log_with(tmp_path, rec(stamp(now), "OK", "d=1s"), "   # a trailing note someone indented")
    assert nightly.facts_line(f, now) == "- Nightly invariants: last run today — OK."


def test_invariants_log_expands_a_tilde_and_refuses_a_non_file(tmp_path, monkeypatch):
    """★ `config.invariants_log` had ZERO direct coverage: the only test touching it monkeypatched
    it away. Dropping its `expanduser` left everything green, so a `~`-spelled path — the way a
    person writes one in a config file — would have silently resolved to nothing."""
    import config, importlib
    importlib.reload(config)
    real = tmp_path / "runs.log"
    real.write_text("x\n", encoding="utf-8")

    monkeypatch.setattr(config, "_load", lambda: {"invariants_log": str(real)})
    assert config.invariants_log() == real

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config, "_load", lambda: {"invariants_log": "~/runs.log"})
    assert config.invariants_log() == real, "a ~-spelled path did not expand"

    a_dir = tmp_path / "adir"
    a_dir.mkdir()
    monkeypatch.setattr(config, "_load", lambda: {"invariants_log": str(a_dir)})
    assert config.invariants_log() is None, "a directory was accepted as a log"

    monkeypatch.setattr(config, "_load", lambda: {"invariants_log": str(tmp_path / "absent")})
    assert config.invariants_log() is None
    monkeypatch.setattr(config, "_load", lambda: {})
    assert config.invariants_log() is None
