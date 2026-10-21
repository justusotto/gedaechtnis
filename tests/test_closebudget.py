"""CLOSEBUDGET-1 — a close pass that runs out of time still gets through every session.

Measured 2026-10-02: the Stop pass's 35 s were spent gathering the facts of ~180 ENDED ledger rows
before the first live session (8,160 "deferred" lines, `judged: 0`), and every pass began at the
same first row. Here: rows the pass lists itself are tested for a live process first; the budget
counts from the first session judged; a pass cut short remembers where it stopped and the next one
begins there; over the memory line the pass does not stop before it has found one session to
close; and what is left over is ONE log line.

Clocks and session tables are made up: every `gather` moves a fake clock, no process is read or
signalled, and the state dir is in tmp_path (the `fl` fixture of test_closetune).
"""
from __future__ import annotations
import importlib.util
import json
import pytest

from test_closetune import Fleet, facts, end, fl, alive, NOW, H, S, TOOLS  # noqa: F401 — fixtures


class Slow(Fleet):
    """`Fleet`, with a clock that every `gather` moves on by `cost` seconds, and distinct ages."""

    def __init__(self, fl, n=50, cost=20.0, **kw):
        super().__init__(fl, n=n, **kw)
        self.t, self.cost, self.gathered = 1000.0, cost, []
        for k, r in enumerate(self.rows):
            r["launched_at"] = NOW - 40 * H + k                # b-0 is the oldest launch

    def gather(self, r, mux, me):
        self.t += self.cost
        self.gathered.append(r["name"])
        return super().gather(r, mux, me)

    def clock(self):
        return self.t

    def live(self):
        return [r for r in self.rows if r["name"] not in self.closed]

    def run(self, apply=True, all_finished=False):
        fl = self.fl
        self.gathered = []
        return fl.close_when_needed(
            apply=apply, by="test", readings_fn=self.readings, settle_s=0, rows=self.live(),
            all_finished=all_finished,
            sweep_fn=lambda **kw: fl.sweep(now=NOW, gather_fn=self.gather, timer=self.clock,
                                           refusals_fn=self.refusals, **kw),
            apply_fn=lambda i, by: fl.apply_item(i, by=by, gather_fn=Fleet.gather.__get__(self),
                                                 refusals_fn=self.refusals, close_fn=self.close,
                                                 clock=lambda: NOW))


def log_lines(fl, what):
    p = fl.config.state() / "session-close.log"
    return [l.split("\t") for l in p.read_text().splitlines() if l.split("\t")[1] == what] \
        if p.exists() else []


def cursor(fl, name=None):
    return json.loads(fl.cursor_path(name or fl.NEEDED_CURSOR).read_text())


# ------------------------------------------------------------------ the two controls asked for ----

def test_positive_fifty_slow_sessions_over_the_line_close_across_passes(alive):
    f = Slow(alive, n=50, cost=20.0, swap=90000.0, per_close=10.0)     # stays over the line
    res = f.run()
    assert f.gathered == [f"b-{k}" for k in range(13)]         # 240 s at 20 s each, oldest launch first
    assert len(f.closed) == 13 and res["stats"] == {"live": 50, "judged": 13, "deferred": 37,
                                                    "found": 13}
    assert cursor(alive)["name"] == "b-12"
    passes = 1
    while len(f.closed) < 50:
        before = len(f.closed)
        f.run()
        passes += 1
        assert len(f.closed) > before                          # every pass closes at least one
        assert passes <= 5
    assert passes == 4 and sorted(f.closed) == sorted(r["name"] for r in f.rows)
    assert cursor(alive) == {}                                 # the last pass reached the end
    assert len(log_lines(alive, "deferred")) == 3              # one line per pass that ran out
    assert len(log_lines(alive, "pass")) == 4


def test_negative_under_the_line_two_figures_are_read_and_nothing_else(alive):
    f = Slow(alive, n=50, swap=1000.0)
    reads = []
    res = alive.close_when_needed(apply=True, by="t", settle_s=0, rows=f.rows,
                                  readings_fn=lambda: reads.append(1) or f.readings(),
                                  sweep_fn=lambda **kw: pytest.fail("no sweep under the line"))
    assert res["stopped"] == "under the line" and reads == [1] and f.closed == []
    assert not alive.config.state().exists() or list(alive.config.state().iterdir()) == []


# ------------------------------------------------------------------------- the cursor ----

def test_positive_a_pass_cut_short_resumes_after_the_last_session_judged(alive):
    f = Slow(alive, n=30, cost=20.0, swap=90000.0, over={f"b-{k}": "young" for k in range(30)})
    f.run()                                                    # nothing closable: 3 budgets, 37 > 30
    assert f.gathered == [f"b-{k}" for k in range(30)] and cursor(alive) == {}
    f.rows = f.rows                                            # same fleet, all_finished: 240 s only
    f.run(all_finished=True)
    assert f.gathered == [f"b-{k}" for k in range(13)] and cursor(alive)["name"] == "b-12"
    f.run(all_finished=True)
    assert f.gathered == [f"b-{k}" for k in range(13, 26)]
    f.run(all_finished=True)                                   # wraps: the rest, then the oldest again
    assert f.gathered == [f"b-{k}" for k in range(26, 30)] + [f"b-{k}" for k in range(9)]
    assert f.closed == []                                      # a young session is never closed


def test_negative_without_resume_every_pass_begins_at_the_first_row(alive):
    f = Slow(alive, n=30, cost=20.0)
    for _ in range(2):
        f.gathered = []
        alive.sweep(apply=False, now=NOW, rows=f.rows, gather_fn=f.gather, timer=f.clock,
                    refusals_fn=f.refusals, budget_s=240.0, log_proposals=False)
        assert f.gathered == [f"b-{k}" for k in range(13)]
    assert [p.name for p in alive.config.state().iterdir()] == ["session-close.log"]


def test_pass_order_turns_the_list_and_keeps_every_row_once(alive):
    rows = [{"name": n, "launched_at": t} for n, t in (("c", 3.0), ("a", 1.0), ("d", 4.0), ("b", 2.0))]
    names = lambda cur: [r["name"] for r in alive.pass_order(rows, cur)]      # noqa: E731
    assert names(None) == ["a", "b", "c", "d"]
    assert names({"name": "b", "pid": None, "launched_at": 2.0}) == ["c", "d", "a", "b"]
    assert names({"name": "d", "pid": None, "launched_at": 4.0}) == ["a", "b", "c", "d"]    # wrap
    assert names({"name": "b", "pid": 7, "launched_at": 2.0}) == ["c", "d", "a", "b"]   # other pid: by age
    assert names({"name": "gone", "launched_at": 2.5}) == ["c", "d", "a", "b"]  # first launched after
    assert names({"name": "gone", "launched_at": 9.0}) == ["a", "b", "c", "d"]
    assert alive.pass_order([], {"name": "x", "launched_at": 0.0}) == []
    assert [r["name"] for r in alive.pass_order([{"name": "n"}, {"name": "m", "launched_at": True}],
                                                None)] == ["n", "m"]           # no age: order kept


def test_an_unreadable_cursor_is_no_cursor(alive):
    alive.config.state().mkdir(parents=True, exist_ok=True)
    for text in ("", "{", "[1]", '{"name": 7}', '{"launched_at": 3}'):
        alive.cursor_path().write_text(text)
        assert alive.read_cursor() is None
    for bad in ('"x"', "true", "NaN", "Infinity", "-Infinity", "1" + "0" * 400):
        alive.cursor_path().write_text('{"name": "b-1", "pid": 4, "launched_at": %s}' % bad)
        assert alive.read_cursor() == {"name": "b-1", "pid": 4, "launched_at": 0.0}, bad
    alive.cursor_path().write_text("[" * 200_000)              # review MF2: never a crash
    assert alive.read_cursor() is None
    alive.write_cursor({"name": "b-2", "pid": 9, "launched_at": 5})
    assert alive.read_cursor() == {"name": "b-2", "pid": 9, "launched_at": 5.0}
    alive.write_cursor({"name": "b-3", "pid": 9, "launched_at": True})
    assert json.loads(alive.cursor_path().read_text())["launched_at"] == 0.0
    alive.write_cursor(None)
    assert alive.read_cursor() is None and alive.cursor_path().read_text() == "{}\n"
    assert [p.name for p in alive.config.state().iterdir()] == ["session-close.cursor"]   # no .tmp left
    alive.write_cursor({"name": "n", "pid": 1}, alive.NEEDED_CURSOR)
    assert alive.read_cursor(alive.NEEDED_CURSOR)["name"] == "n" and alive.read_cursor() is None


def test_a_row_whose_launch_time_is_no_number_sorts_first_and_never_raises(alive):
    rows = [{"name": "ok", "launched_at": 5.0}] + [
        {"name": f"x{n}", "launched_at": v} for n, v in enumerate(
            (True, float("nan"), float("inf"), 10 ** 400, "7", None))]
    assert [r["name"] for r in alive.pass_order(rows, None)][-1] == "ok"
    assert [alive._launched(r) for r in rows[1:]] == [0.0] * 6


def test_positive_two_live_sessions_with_one_name_do_not_trap_the_cursor(alive):
    # review MF1: `claude -r <name>` on a session that still runs gives two live rows with one name
    f = Slow(alive, n=5, cost=150.0, over={f"b-{k}": "young" for k in range(5)})
    f.rows[2]["name"] = f.rows[2]["mux_session"] = "b-0"
    f.ends["b-0"] = f.ends["b-2"]
    seen = []
    for n in range(6):
        f.run(all_finished=True)                               # 240 s at 150 s each: two a pass
        seen.append([r for r in f.gathered])
        if n == 3:
            assert cursor(alive)["pid"] == f.rows[2]["pid"]    # the place is the SECOND b-0
    assert seen == [["b-0", "b-1"], ["b-0", "b-3"], ["b-4", "b-0"], ["b-1", "b-0"],
                    ["b-3", "b-4"], ["b-0", "b-1"]]            # by name alone: b-1, b-0 for ever


def test_negative_a_pass_that_judged_nobody_keeps_the_place(fl, monkeypatch):
    f = stop_world(fl, monkeypatch, n_dead=0, n_live=6)
    stop_pass(fl, f)
    place = cursor(fl, fl.STOP_CURSOR)
    assert place["name"] == "b-3"
    f.listing_s = 100.0
    assert stop_pass(fl, f) == [] and cursor(fl, fl.STOP_CURSOR) == place
    assert not fl.cursor_path(fl.NEEDED_CURSOR).exists()       # each kind of pass has its own place


# ----------------------------------------------------- at least one close while over the line ----

def test_positive_over_the_line_the_pass_runs_past_its_budget_to_the_first_finished_one(alive):
    over = {f"b-{k}": "young" for k in range(40) if k != 30}
    f = Slow(alive, n=40, cost=20.0, swap=90000.0, over=over)
    res = f.run()
    assert f.closed == ["b-30"] and f.gathered[-1] == "b-30"   # 31 judged: 620 s, past 240 s
    assert res["stats"]["judged"] == 31 and res["stats"]["deferred"] == 9
    assert log_lines(alive, "deferred")[0][2:] == [
        "9 session(s)", "pass budget 240s spent after 31 judged; the next pass resumes after b-30"]


def test_negative_the_stretch_ends_after_three_budgets_and_the_next_pass_goes_on(alive):
    over = {f"b-{k}": "young" for k in range(49)}
    f = Slow(alive, n=50, cost=20.0, swap=90000.0, over=over)
    res = f.run()
    assert f.closed == [] and res["stats"]["judged"] == 37     # 3 × 240 s at 20 s each
    assert res["stopped"] == "no finished session to close"
    f.run()
    assert f.closed == ["b-49"] and f.gathered[0] == "b-37"


def test_negative_all_finished_by_hand_keeps_its_plain_budget(alive):
    over = {f"b-{k}": "young" for k in range(40) if k != 30}
    f = Slow(alive, n=40, cost=20.0, swap=90000.0, over=over)
    f.run(all_finished=True)
    assert f.closed == [] and len(f.gathered) == 13


# ------------------------------------------------------------- nothing the close keeps is closed ----

@pytest.mark.parametrize("kind", ["attended", "host", "never", "young", "foreign-window"])
def test_negative_a_kept_session_stays_through_every_pass(alive, kind):
    f = Slow(alive, n=20, cost=40.0, swap=90000.0, over={"b-3": kind, "b-11": kind})
    for _ in range(6):
        f.run()
    kept = {"rc-host-x"} if kind == "never" else {"b-3", "b-11"}
    assert len(f.closed) == 20 - len(kept if kind != "never" else {1, 2})
    assert not kept & set(f.closed)


# ------------------------------------------------------------------------ the Stop pass ----

def stop_world(fl, monkeypatch, n_dead=180, n_live=6, listing_s=0.0, cost=10.0):
    f = Slow(fl, n=n_dead + n_live, cost=cost)
    dead = {r["pid"] for r in f.rows[:n_dead]}                 # the OLDEST rows are the ended ones
    monkeypatch.setattr(fl, "same_process", lambda pid, start: pid not in dead)
    monkeypatch.setattr(fl, "gather", f.gather)                # the shipped wiring: no gather_fn given
    monkeypatch.setattr(fl, "current_launches", lambda rows=None: list(f.rows))

    f.listing_s = listing_s

    def outside(rows=None):
        f.t += f.listing_s
        return []
    monkeypatch.setattr(fl, "outside_sessions", outside)
    monkeypatch.setattr(fl, "multiplexer", lambda: "screen")
    return f


def stop_pass(fl, f, **kw):
    f.gathered = []
    return fl.sweep(apply=False, now=NOW, refusals_fn=f.refusals, timer=f.clock,
                    resume=True, cap_s=fl.SWEEP_CAP_S, log_proposals=False, **kw)


def test_positive_ended_rows_cost_the_stop_pass_nothing(fl, monkeypatch):
    f = stop_world(fl, monkeypatch)
    stats = {}
    items = stop_pass(fl, f, stats=stats)
    assert f.gathered == [f"b-{k}" for k in range(180, 184)]   # 35 s at 10 s each: 4 of the 6 live
    assert len(items) == 4 and stats == {"live": 6, "judged": 4, "deferred": 2, "found": 4}
    assert log_lines(fl, "deferred") and len(log_lines(fl, "deferred")) == 1
    stop_pass(fl, f)
    assert f.gathered[:2] == ["b-184", "b-185"]                # the next pass begins with the other two
    assert not any(n in f.gathered for n in (f"b-{k}" for k in range(180)))


def test_negative_an_injected_gather_decides_alive_itself(fl, monkeypatch):
    # a caller that injects `gather_fn` and no `alive_fn` is not filtered by the real process read
    monkeypatch.setattr(fl, "same_process", lambda pid, start: False)
    monkeypatch.setattr(fl, "multiplexer", lambda: "screen")
    f = Slow(fl, n=3, cost=1.0)
    kw = dict(apply=False, now=NOW, rows=f.rows, gather_fn=f.gather, refusals_fn=f.refusals,
              timer=f.clock, log_proposals=False)
    assert len(fl.sweep(**kw)) == 3
    assert fl.sweep(alive_fn=lambda pid, start: pid == f.rows[1]["pid"], **kw)[0]["name"] == "b-1"


def test_positive_given_rows_are_filtered_too_and_the_pass_own_row_stays(fl, monkeypatch):
    # `sessions.py launch` hands the sweep the whole ledger when it lists idle sessions
    f = stop_world(fl, monkeypatch, n_dead=20, n_live=2, cost=1.0)
    import procs
    monkeypatch.setattr(procs, "claude_pid", lambda: f.rows[0]["pid"])       # an ended row with MY pid
    fl.sweep(apply=False, now=NOW, rows=list(f.rows), refusals_fn=f.refusals, timer=f.clock,
             log_proposals=False)
    assert f.gathered == ["b-0", "b-20", "b-21"]


def test_positive_the_budget_counts_from_the_first_session_judged(fl, monkeypatch):
    f = stop_world(fl, monkeypatch, n_dead=0, n_live=40, listing_s=100.0, cost=10.0)
    fl.sweep(apply=False, now=NOW, refusals_fn=f.refusals, timer=f.clock, log_proposals=False,
             budget_s=240.0)                                   # the hand-run pass: no cap
    assert len(f.gathered) == 25                               # 100 s of listing took none of the 240 s


def test_positive_a_slow_process_read_stays_inside_the_cap(fl, monkeypatch):
    # review optional 2: the filter of ended rows is inside the cap too
    f = stop_world(fl, monkeypatch, n_dead=0, n_live=30)
    reads = []

    def slow_ps(pid, start):
        reads.append(pid)
        f.t += 5.0
        return True
    monkeypatch.setattr(fl, "same_process", slow_ps)
    stats = {}
    assert stop_pass(fl, f, stats=stats) == [] and len(reads) == 8        # 35 s at 5 s a read
    assert stats["live"] == 30 and stats["deferred"] == 30     # the unread rows stay, for later


def test_negative_the_filter_reads_each_row_with_its_recorded_start(fl, monkeypatch):
    f = stop_world(fl, monkeypatch, n_dead=0, n_live=3, cost=1.0)
    for k, r in enumerate(f.rows):
        r["pid_start"] = f"start-{k}"
    monkeypatch.setattr(fl, "same_process", lambda pid, start: start == "start-1")
    stop_pass(fl, f)
    assert f.gathered == ["b-1"]


def test_negative_the_stop_pass_never_runs_past_its_cap(fl, monkeypatch):
    f = stop_world(fl, monkeypatch, n_dead=0, n_live=6, listing_s=100.0)
    stats = {}
    assert stop_pass(fl, f, stats=stats) == [] and f.gathered == []
    assert stats == {"live": 6, "judged": 0, "deferred": 6, "found": 0}
    assert log_lines(fl, "deferred")[0][2] == "6 session(s)"
    f2 = stop_world(fl, monkeypatch, n_dead=0, n_live=8, listing_s=33.0, cost=1.0)
    stop_pass(fl, f2)
    assert len(f2.gathered) == 3                               # 33 s listing: 35 s from the START
    assert fl.SWEEP_CAP_S == fl.SWEEP_BUDGET_S == 35.0         # the Stop entry is no later than before


def test_negative_until_found_does_not_carry_a_pass_past_its_cap(fl, monkeypatch):
    f = stop_world(fl, monkeypatch, n_dead=0, n_live=30, cost=10.0)
    for r in f.rows:
        f.spoil(r["name"], "young")
    stop_pass(fl, f, until_found=1)
    assert len(f.gathered) == 4                                # 35 s cap from the start, not 105 s


def test_the_stop_door_writes_one_pass_line_only_when_something_happened(fl, monkeypatch):
    import chore
    calls = []

    def sweep(**kw):
        calls.append(kw)
        kw["stats"].update(live=5, judged=3, deferred=sweep.deferred, found=0)
        return [{"name": "a", "pid": 1, "keep": ["idle: x"], "closed": False}] * 3
    monkeypatch.setattr(fl, "due", lambda: True)
    monkeypatch.setattr(fl, "sweep", sweep)
    monkeypatch.setattr(fl, "applies", lambda: False)
    sweep.deferred = 0
    chore.do_sessclose({})
    assert log_lines(fl, "pass") == []
    sweep.deferred = 2
    chore.do_sessclose({})
    assert log_lines(fl, "pass")[0][2:] == ["at Stop", "live 5, judged 3, closed 0, deferred 2"]
    assert all(c["resume"] is True and c["cap_s"] == 35.0 and "budget_s" not in c
               and "cursor" not in c for c in calls)
    sweep.deferred = 0
    monkeypatch.setattr(fl, "sweep", lambda **kw: kw["stats"].update(live=2, judged=2, deferred=0, found=1)
                        or [{"name": "a", "pid": 1, "keep": [], "closed": True},
                            {"name": "b", "pid": 2, "keep": ["idle: x"], "closed": False}])
    chore.do_sessclose({})
    assert log_lines(fl, "pass")[-1][2:] == ["at Stop", "live 2, judged 2, closed 1, deferred 0"]
    assert json.loads((fl.config.state() / "session-close-proposals.json").read_text())["judged"] == 2


def test_negative_a_hand_run_under_the_line_writes_no_pass_line(alive):
    f = Slow(alive, n=5, cost=1.0, swap=1000.0)
    f.run(all_finished=True)
    assert len(f.closed) == 5 and log_lines(alive, "pass") == []


def test_a_refused_session_is_not_the_one_the_pass_was_looking_for(alive):
    over = {f"b-{k}": "young" for k in range(20)}
    over.update({"b-2": "attended", "b-15": None})
    f = Slow(alive, n=20, cost=20.0, swap=90000.0, over={k: v for k, v in over.items() if v})
    f.ends["b-15"] = end(ts=NOW - 30 * H)
    res = f.run()                                              # b-2 is finished but refused: go on
    assert f.closed == ["b-15"] and res["stats"] == {"live": 20, "judged": 16, "deferred": 4,
                                                     "found": 1}


def test_the_pass_line_says_what_the_pass_did(alive):
    f = Slow(alive, n=3, cost=1.0, swap=4200.0, per_close=200.0)
    f.run()
    line = log_lines(alive, "pass")[0]
    assert line[2:] == ["when needed", "live 3, judged 3, closed 1, deferred 0",
                        "swap 4200 MB used, memory level 50 -> swap 4000 MB used, memory level 50",
                        "back under the line"]
    f2 = Slow(alive, n=3, cost=1.0, swap=90000.0, over={f"b-{k}": "young" for k in range(3)})
    f2.run()                                                   # over the line, nothing to close: a line
    assert log_lines(alive, "pass")[1][3] == "live 3, judged 3, closed 0, deferred 0"
    f2.run(apply=False)                                        # a dry run writes none
    assert len(log_lines(alive, "pass")) == 2


def test_a_deferred_line_without_resume_promises_no_next_pass(alive):
    f = Slow(alive, n=30, cost=20.0)
    alive.sweep(apply=False, now=NOW, rows=f.rows, gather_fn=f.gather, timer=f.clock,
                refusals_fn=f.refusals, budget_s=240.0, log_proposals=False)
    assert log_lines(alive, "deferred")[0][2:] == ["17 session(s)", "pass budget 240s spent after 13 judged"]


def test_the_command_says_how_many_it_did_not_judge(alive, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("sessions_cb", TOOLS / "sessions.py")
    sessions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sessions)
    calls = []

    def sweep(**kw):
        calls.append(kw)
        kw["stats"].update(live=9, judged=4, deferred=5, found=0)
        return []
    monkeypatch.setattr(alive, "sweep", sweep)
    monkeypatch.setattr(alive, "applies", lambda: False)
    monkeypatch.setattr(sessions, "fleet", alive, raising=False)
    a = type("A", (), {"replay": None, "dry_run": True, "when_needed": False, "all_finished": False})()
    assert sessions.cmd_close(a) == 0
    assert calls[0]["budget_s"] == 240.0 and calls[0]["apply"] is False and "resume" not in calls[0]
    assert "judged 4 of 9 live sessions in this pass's time; 5 were not judged." in capsys.readouterr().out
    monkeypatch.setattr(alive, "close_when_needed", lambda **kw: {
        "before": {"swap_mb": 5000.0, "level": 50.0}, "after": {"swap_mb": 5000.0, "level": 50.0},
        "over": ["swap"], "closed": [], "candidates": [], "items": [], "stopped": "x", "target": 4096.0,
        "floor": 30.0, "stats": {"live": 9, "judged": 4, "deferred": 5, "found": 0}})
    a.when_needed = True
    assert sessions.cmd_close(a) == 0
    assert "when needed: judged 4 of 9 live sessions" in capsys.readouterr().out
