#!/usr/bin/env python3
"""nightly.py — the last nightly invariant run, said once at SessionStart.

The fleet's nightly runner appends one tab-separated record per run to a log, and for weeks
NOBODY READ IT. A check whose result reaches no one is not a check; it is a cost. So the newest
line is surfaced where every session already looks — and only the part a person would act on.

    <ts>  OK|FAIL  duration=..  pytest=..  mission-ledger=..  …

The record's own contract is the header table in `scripts/nightly_invariants.sh`, which numbers
its fields and states the count in prose. This module DOES NOT hard-code that count or the field
order: it splits on `=` and reads gates BY NAME, exactly as `health_digest._gate_fields` does, so
appending a field stays the safe extension the runner's header says it is.

★ WHAT IT REPORTS IS THE FAILING GATES, NOT THE WHOLE LINE. A ten-field record printed verbatim
every session is a wall of green tokens that a reader learns to skip within a week, and the one
FAIL in it arrives looking exactly like the nine PASSes. A clean run gets one short line; a failing
run names the gates that failed and nothing else.

★★ AND A STALE LOG IS ITS OWN FINDING. "The last run passed" and "the last run was six days ago
and nothing has run since" are different facts, and a reader who is only told the first will act on
a verdict about a repo that has changed underneath it. Age is stated whenever it is not today's.
"""
from __future__ import annotations
import calendar, time
from pathlib import Path

import config

# Words that mean "this gate did not pass". UNREADABLE is deliberately among them: an arm that
# could not read what it needed has not passed, and reporting it as anything else is the
# false-reassurance direction.
_BAD = ("FAIL", "UNREADABLE")

# ★ THERE IS NO `route=` EXEMPTION HERE, AND THERE MUST NOT BE ONE. An earlier draft carried a
# list of "gates with their own vocabulary" containing `route`, on the strength of the runner's
# header calling it exactly that. A mutation deleting the list stayed GREEN, which was the clue:
# `route=` is never a FIELD. The runner appends it INSIDE field 8, space-separated onto the
# owner-pages value (`nightly_invariants.sh:356,363`), so nothing is ever NAMED `route` and the
# exemption could not fire. It was dead code that read as load-bearing, and the test covering it
# passed because the owner-pages head was PASS — not because the exemption did anything.
#
# The behaviour that actually matters is the right one by construction: `owner-pages=PASS …
# route=ERR …` is judged on its head (PASS) and stays unreported, while `owner-pages=FAIL …` is
# reported WITH its whole value, route half included, which is what a reader needs.


def last_line(path: Path) -> str | None:
    """The newest non-comment, non-blank record, or None.

    Reads the whole file rather than seeking the tail: this log gains one line a day, and a tail
    seek that got the encoding wrong would fail in the direction of reporting nothing, which is
    indistinguishable from a nightly that never ran."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rows = [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    return rows[-1] if rows else None


def failing_gates(fields: list[str]) -> list[str]:
    """The names of gates whose value is not a pass, read BY NAME and never by column."""
    out = []
    for f in fields[2:]:
        name, sep, value = f.partition("=")
        if not sep:
            continue
        head = value.split()[0] if value.split() else ""
        if head in _BAD:
            # The WHOLE value, not a word-capped prefix: these are a handful of words each, and a
            # cut at three turned "FAIL 45 of 528 invalid" into "FAIL 45 of" — a number with its
            # denominator amputated, which reads as a smaller problem than it is.
            out.append(f"{name}={' '.join(value.split())}")
    return out


def age_days(stamp: str, now: float | None = None) -> int | None:
    """Whole days between the record's UTC stamp and now, or None if it cannot be parsed.

    None is returned rather than 0 — an unparseable stamp is not a fresh one, and the caller says
    so in words instead of quietly reporting a run as current."""
    try:
        t = time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None
    days = int(((now if now is not None else time.time()) - calendar.timegm(t)) // 86400)
    # ★ A FUTURE STAMP IS NOT "TODAY". Clamping it to 0 was the first version, and it turns clock
    # skew or a bad writer into the most reassuring line this module can print. A record dated
    # ahead of now is a symptom, so it is returned as a NEGATIVE number and the caller names it.
    return days


def facts_line(path: Path | None = None, now: float | None = None) -> str | None:
    """The SessionStart line, or None when no log is configured or it holds no record."""
    path = path if path is not None else config.invariants_log()
    if path is None:
        return None
    line = last_line(Path(path))
    if not line:
        return None
    fields = line.split("\t")
    if len(fields) < 2:
        return f"- Nightly invariants: the newest log line is unreadable ({line[:60]!r})."
    stamp, overall = fields[0], fields[1]
    days = age_days(stamp, now)
    if days is None:
        when = f"stamped {stamp!r}, which is not a readable date"
    elif days < 0:
        when = (f"stamped {stamp} — IN THE FUTURE, so this machine's clock and the writer's "
                f"disagree; treat the verdict below as unplaced in time")
    elif days == 0:
        when = "today"
    elif days == 1:
        when = "yesterday"
    else:
        when = f"{days} days ago"
    bad = failing_gates(fields)
    if overall == "OK" and not bad:
        return f"- Nightly invariants: last run {when} — OK."
    # The whole line is NOT printed even here. What a reader acts on is which gate failed; the
    # nine that passed are noise at the moment one has not.
    named = "; ".join(bad) if bad else "no gate named a failure, which contradicts the verdict"
    return (f"- Nightly invariants: last run {when} — {overall}. Failing: {named}. "
            f"Full record in the log.")
