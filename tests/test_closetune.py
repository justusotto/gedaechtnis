"""CLOSETUNE-1 — the safe close frees memory by itself.

FINISHED is a fact about time (`closerules.finished`, `fleet.classify`); an UNKNOWN keeps a finished
session only up to idle × 4; two more ways to be finished (a done/landed/stopped handoff with no turn
since its commit; a report sent to the seat 30 minutes ago); and `close_when_needed` closes the
oldest finished sessions only while the machine is over its memory line.

Every rule has a positive and a negative control. Nothing here reads the machine's real sessions,
ledger, memory figures or state: the state dir, config and sessions dir are redirected into
tmp_path, every process fact and memory reading is injected, and no process is signalled.
"""
from __future__ import annotations
import importlib.util, json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HOOKS))

NOW = 1_800_000_000.0
IDLE = 180.0
H = 3600.0


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


def end(**over):
    e = {"ts": NOW - 4 * H, "text": "Handoff written.", "open_bg": [], "open_bg_ts": {},
         "began": NOW - 5 * H, "sent": [], "wake_until": 0.0, "cron": 0, "writes": {},
         "reports": [], "humans": 1}
    e.update(over)
    return e


def facts(**over):
    f = {"is_self": False, "alive": True, "handoff": False, "handoff_done": None, "seat": None,
         "status": "idle", "scanned": True, "end": end(), "screen": "❯ \n  ? for shortcuts",
         "attached": False, "unsaved": [], "wrote": [], "outside": False, "mux": "screen",
         "exempt": False}
    f.update(over)
    return f


# ------------------------------------------------------------------ A. finished = time ----

def test_positive_a_session_idle_for_the_threshold_is_finished_whatever_it_said(fl):
    f = facts(end=end(text="HOLDING — waiting on: the seat. Shall I merge?"))
    assert fl.classify(f, NOW, IDLE) == []
    assert any("last words" in n for n in fl.notes(f, NOW, IDLE))


def test_negative_a_session_younger_than_the_threshold_is_not_finished(fl):
    keep = fl.classify(facts(end=end(ts=NOW - 2 * H)), NOW, IDLE)
    assert any(k.startswith("finished:") for k in keep), keep


def test_the_threshold_is_the_boundary(fl):
    assert fl.classify(facts(end=end(ts=NOW - IDLE * 60)), NOW, IDLE) == []
    assert fl.classify(facts(end=end(ts=NOW - IDLE * 60 + 1)), NOW, IDLE)


def test_negative_a_turn_that_has_not_ended_keeps_however_old(fl):
    keep = fl.classify(facts(end=None), NOW, IDLE)
    assert any(k.startswith("idle:") for k in keep)


def test_negative_a_background_task_younger_than_the_threshold_keeps(fl):
    f = facts(end=end(open_bg=["t1"], open_bg_ts={"t1": NOW - 600}, ts=NOW - 30 * H))
    keep = fl.classify(f, NOW, IDLE)
    assert any(k.startswith("background:") and "less than" in k for k in keep), keep


def test_negative_a_background_task_with_no_recorded_start_is_young(fl):
    f = facts(end=end(open_bg=["t1"], open_bg_ts={"t1": None}, ts=NOW - 30 * H))
    assert any(k.startswith("background:") and "less than" in k for k in fl.classify(f, NOW, IDLE))


def test_positive_an_old_background_task_keeps_only_until_the_12h_line(fl):
    old = {"open_bg": ["t1"], "open_bg_ts": {"t1": NOW - 40 * H}}
    assert fl.classify(facts(end=end(ts=NOW - 5 * H, **old)), NOW, IDLE)       # under 12 h: kept
    assert fl.classify(facts(end=end(ts=NOW - 13 * H, **old)), NOW, IDLE) == []  # past: closeable


@pytest.mark.parametrize("over, rule", [
    ({"unsaved": None}, "no-unsaved-work"),
    ({"unsaved": ["/r/x.py"]}, "no-unsaved-work"),
    ({"screen": None}, "no-prompt"),
    ({"end": end(humans=3)}, "conversation"),
])
def test_an_unknown_keeps_under_the_12h_line_and_not_past_it(fl, over, rule):
    young = facts(**over)
    young["end"] = dict(young["end"], ts=NOW - 5 * H)
    keep = fl.classify(young, NOW, IDLE)
    assert any(k.startswith(rule + ":") and "12 h" in k for k in keep), keep
    old = facts(**over)
    old["end"] = dict(old["end"], ts=NOW - 12 * H)
    assert fl.classify(old, NOW, IDLE) == [], "past the 12 h line an unknown does not keep"


@pytest.mark.parametrize("over, rule", [
    ({"screen": "Do you want to proceed?\n❯ 1. Yes"}, "no-prompt"),
    ({"status": "busy"}, "idle"),
    ({"attached": True}, "attached"),
    ({"end": end(wake_until=NOW + 60)}, "background"),
    ({"end": end(cron=1)}, "background"),
    ({"exempt": True}, "exempt"),
    ({"outside": True, "mux": None}, "window"),
])
def test_a_hard_fact_keeps_even_past_the_12h_line(fl, over, rule):
    f = facts(**over)
    f["end"] = dict(f["end"], ts=NOW - 30 * H)
    keep = fl.classify(f, NOW, IDLE)
    assert any(k.startswith(rule + ":") for k in keep), keep


# ------------------------------------------------ the git read: relative, removed, unknown ----

def _git(repo: Path, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "a.txt").write_text("a")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-qm", "a")
    return r


def test_a_relative_worktree_is_read_under_the_sessions_cwd(fl, repo, monkeypatch, tmp_path):
    wt = repo / ".claude" / "worktrees" / "ROW"
    _git(repo, "worktree", "add", "-q", str(wt))
    (wt / "dirty.txt").write_text("x")
    monkeypatch.chdir(tmp_path)                     # the sweep's own folder is NOT the session's
    assert fl.session_unsaved(str(repo), ".claude/worktrees/ROW", {}) == ["dirty.txt"]


def test_a_removed_worktree_is_no_dirt_not_unknown(fl, repo, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert fl.session_unsaved(str(repo), ".claude/worktrees/GONE", {}) == []


def test_negative_a_git_that_fails_is_unknown(fl, tmp_path):
    bad = tmp_path / "notarepo" / ".claude" / "worktrees" / "X"
    bad.mkdir(parents=True)
    assert fl.session_unsaved(None, str(bad), {}) is None


def test_the_close_carries_uncommitted_paths_or_says_unknown(fl):
    assert fl.uncommitted_note(facts(unsaved=[])) is None
    assert "b.py" in fl.uncommitted_note(facts(unsaved=["/r/b.py"]))
    n = fl.uncommitted_note(facts(unsaved=None, wrote=["/r/c.md"]))
    assert n.startswith("uncommitted: unknown") and "/r/c.md" in n


# --------------------------------------- criterion (a): a done/landed/stopped handoff ----

@pytest.mark.parametrize("text, done", [
    ("# H\n\n## State\n\n**STOPPED at the context line, NOT LANDED.**\n", True),
    ("State: LANDED `abc123`.\n", True),
    ("**State:** DONE — all rows closed\n", True),
    ("## State\n\nNOT LANDED. Two reds.\n", False),
    ("State: in progress\n", False),
    ("The suite is done and landed.\n", False),        # no State line: prose never counts
    ("## State\n\nlanded (lower case is prose)\n", False),
    # the review's cases (CLOSETUNE-1 MF1): the value must START with the word
    ("State: not LANDED\n", False),
    ("State: NOT YET LANDED\n", False),
    ("State: NOT-LANDED\n", False),
    ("State: Not DONE\n", False),
    ("State: NOT  LANDED\n", False),
    ("State: IN PROGRESS — DONE after review\n", False),
    ("State: HOLDING; will be DONE once CI passes\n", False),
    ("State: BLOCKED (was LANDED, reverted)\n", False),
    ("State machine: DONE handling\n## State\n\nWIP\n", False),
    ("```\nState: LANDED\n```\n## State\n\nWIP\n", False),
    ("## State\n\n**LANDED** `caf843f7`.\n", True),
    # the fix review's cases: a State heading or list line carries its value on the same line, and
    # the FIRST State line decides even when a later quoted line says DONE
    ("## State: IN PROGRESS\n\n## Before\nState: DONE (CT-0)\n", False),
    ("# State — IN PROGRESS\nState: DONE\n", False),
    ("- State: IN PROGRESS\nState: DONE\n", False),
    ("> State: IN PROGRESS\nState: DONE\n", False),
    ("State - IN PROGRESS\nState: DONE\n", False),
    ("**State** IN PROGRESS\nState: DONE\n", False),
    ("## State: LANDED\n", True),
    ("- **State:** STOPPED at the line\n", True),
    ("````\n```\nx\n```\nState: DONE\n````\nState: WIP\n", False),
])
def test_the_handoff_state_line(fl, text, done):
    assert fl.handoff_state_done(text) is done


def test_handoff_done_is_the_commit_time_of_a_done_handoff(fl, repo):
    h = repo / "HANDOFF-X.md"
    h.write_text("## State\n\nLANDED `abc`.\n")
    assert fl.handoff_done("HANDOFF-X.md", str(repo)) is None      # not committed: not (a)
    _git(repo, "add", "HANDOFF-X.md")
    _git(repo, "commit", "-qm", "h")
    t = fl.handoff_done("HANDOFF-*.md", str(repo))
    assert t and abs(t - float(subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct"],
                                               capture_output=True, text=True).stdout)) < 1
    h.write_text("## State\n\nNOT LANDED.\n")
    _git(repo, "commit", "-qam", "h2")
    assert fl.handoff_done("HANDOFF-X.md", str(repo)) is None


def test_positive_a_young_session_whose_handoff_says_done_is_finished(fl):
    """CLOSETUNE-2 (2026-10-02): after the same 30 minutes of quiet as a report to its seat."""
    f = facts(end=end(ts=NOW - 1900, began=NOW - 2200), handoff_done=NOW - 2000)
    assert fl.classify(f, NOW, IDLE) == []
    assert "handoff" in fl.notes(f, NOW, IDLE)[0]


def test_negative_a_done_handoff_younger_than_30_minutes_is_not_finished_yet(fl):
    f = facts(end=end(ts=NOW - 600, began=NOW - 900), handoff_done=NOW - 700)
    assert any(k.startswith("finished:") for k in fl.classify(f, NOW, IDLE))


def test_negative_a_commit_long_after_the_turn_ended_is_someone_elses(fl):
    """The review's MF2 probe: the seat re-commits the handoff 10 min after the turn ended."""
    f = facts(end=end(ts=NOW - 2400, began=NOW - 2700), handoff_done=NOW - 1800)
    assert any(k.startswith("finished:") for k in fl.classify(f, NOW, IDLE))
    f = facts(end=end(ts=NOW - 2400, began=NOW - 2700), handoff_done=NOW - 2400 + 290)
    assert fl.classify(f, NOW, IDLE) == []                  # within the 5-minute slack


def test_handoff_done_reads_the_author_time_not_the_committer_time(fl, repo):
    h = repo / "HANDOFF-Y.md"
    h.write_text("State: LANDED\n")
    _git(repo, "add", "HANDOFF-Y.md")
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "y"], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                            GIT_AUTHOR_DATE="@1700000000 +0000", GIT_COMMITTER_DATE="@1700009999 +0000"))
    assert fl.handoff_done("HANDOFF-Y.md", str(repo)) == 1700000000.0


def test_negative_a_turn_that_began_after_the_handoff_commit_is_not_finished_by_it(fl):
    f = facts(end=end(ts=NOW - 2400, began=NOW - 2450), handoff_done=NOW - 2500)
    assert any(k.startswith("finished:") for k in fl.classify(f, NOW, IDLE))


# --------------------------------------------------- criterion (b): a report to its seat ----

def test_positive_a_report_to_its_seat_and_30_min_quiet_is_finished(fl):
    f = facts(seat="seat-a", end=end(ts=NOW - 31 * 60, sent=[{"to": "seat-a", "ts": NOW - 32 * 60}]))
    assert fl.classify(f, NOW, IDLE) == []


@pytest.mark.parametrize("seat, sent, age_min", [
    ("seat-a", [{"to": "seat-a", "ts": 0}], 20),           # quiet only 20 minutes
    ("seat-a", [{"to": "someone-else", "ts": 0}], 60),     # not its seat
    (None, [{"to": "seat-a", "ts": 0}], 60),               # no seat recorded
    ("seat-a", [], 60),                                    # sent nothing
])
def test_negative_the_report_criterion_needs_its_seat_and_the_quiet(fl, seat, sent, age_min):
    f = facts(seat=seat, end=end(ts=NOW - age_min * 60, sent=sent))
    assert any(k.startswith("finished:") for k in fl.classify(f, NOW, IDLE))


def _rec(typ, ts, content, **kw):
    r = {"type": typ, "timestamp": ts, "message": {"content": content}}
    r.update(kw)
    return json.dumps(r)


def test_scan_records_the_turn_start_the_messages_sent_and_task_start_times(fl):
    import closerules
    lines = [
        _rec("user", "2026-09-30T00:00:00Z", "do the row"),
        _rec("assistant", "2026-09-30T00:01:00Z",
             [{"type": "tool_use", "id": "u1", "name": "Bash", "input": {"run_in_background": True}}]),
        _rec("user", "2026-09-30T00:01:05Z",
             [{"type": "tool_result", "tool_use_id": "u1", "content": "Command running in background with ID: bg77"}]),
        _rec("assistant", "2026-09-30T00:02:00Z",
             [{"type": "tool_use", "id": "u2", "name": "SendMessage", "input": {"to": "seat-a", "message": "report"}}]),
        _rec("user", "2026-09-30T00:02:01Z", [{"type": "tool_result", "tool_use_id": "u2", "content": "sent"}]),
        _rec("assistant", "2026-09-30T00:02:30Z",
             [{"type": "tool_use", "id": "u3", "name": "SendMessage", "input": {"to": "seat-b", "message": "x"}}]),
        _rec("user", "2026-09-30T00:02:31Z",
             [{"type": "tool_result", "tool_use_id": "u3", "content": "refused", "is_error": True}]),
        json.dumps({"type": "assistant", "timestamp": "2026-09-30T00:03:00Z",
                    "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Reported."}]}}),
    ]
    s = closerules.scan(Path("x"), lines=lines)
    last = s["last"]
    assert last["began"] == closerules._ts("2026-09-30T00:00:00Z")
    assert last["sent"] == [{"to": "seat-a", "ts": closerules._ts("2026-09-30T00:02:01Z")}]
    assert last["open_bg_ts"] == {"bg77": closerules._ts("2026-09-30T00:01:05Z")}


def test_scan_resets_the_sent_list_at_a_new_turn(fl):
    import closerules
    lines = [
        _rec("user", "2026-09-30T00:00:00Z", "one"),
        _rec("assistant", "2026-09-30T00:00:10Z",
             [{"type": "tool_use", "id": "u1", "name": "SendMessage", "input": {"to": "seat-a"}}]),
        _rec("user", "2026-09-30T00:00:11Z", [{"type": "tool_result", "tool_use_id": "u1", "content": "ok"}]),
        json.dumps({"type": "assistant", "timestamp": "2026-09-30T00:00:20Z",
                    "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "a"}]}}),
        _rec("user", "2026-09-30T00:10:00Z", "new work"),
        json.dumps({"type": "assistant", "timestamp": "2026-09-30T00:10:20Z",
                    "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "b"}]}}),
    ]
    s = closerules.scan(Path("x"), lines=lines)
    assert s["last"]["sent"] == [] and s["ends"][0]["sent"]


# ------------------------------------------------------------ B. the memory line ----

def test_over_line(fl):
    assert fl.over_line({"swap_mb": 4095.0, "level": 40}, 4096, 30) == []
    assert fl.over_line({"swap_mb": 4096.0, "level": 40}, 4096, 30)          # at the limit: over
    assert fl.over_line({"swap_mb": 100.0, "level": 29}, 4096, 30)           # pressure alone: over
    assert fl.over_line({"swap_mb": None, "level": 50}, 4096, 30) == []      # no swap figure
    assert fl.over_line({"swap_mb": None, "level": None}, 4096, 30) is None  # unreadable: never a reason


def test_a_shrinking_swap_total_with_used_constant_does_not_flip_the_verdict(fl):
    """Measured 2026-09-30 00:18-00:47: macOS shrank the swap TOTAL 9.2 → 7.2 → 5.1 GB
    as it drained. The line is the MB in use; the share (and the total) never decides."""
    used = 3000.0
    verdicts = {total: fl.over_line({"swap": used / total, "swap_mb": used, "level": 76}, 4096, 30)
                for total in (9200.0, 7200.0, 5100.0, 3100.0)}
    assert set(map(tuple, verdicts.values())) == {()}, verdicts         # under at every total
    used = 5000.0
    assert all(fl.over_line({"swap": used / t, "swap_mb": used, "level": 76}, 4096, 30)
               for t in (9200.0, 7200.0, 5100.0))                        # over at every total


@pytest.mark.parametrize("raw", [True, 0, -1, float("nan"), float("inf"), "abc", None, [1]])
def test_a_bad_swap_limit_falls_back_to_the_shipped_4096(fl, monkeypatch, raw):
    import limits
    real = limits.get
    monkeypatch.setattr(limits, "get", lambda k, d=None: raw if k == "session_close_swap_used_mb" else real(k, d))
    assert fl.swap_used_limit_mb() == 4096.0


def test_the_shipped_line(fl):
    assert fl.swap_used_limit_mb() == 4096 and fl.memory_floor() == 30


S = "Wed 30 Sep 00:00:00 2026"


class Fleet:
    """A fake fleet: N ledger sessions, each claude in its own screen; closes are recorded, never
    sent; the swap figure falls by `per_close` with each close."""

    def __init__(self, fl, n=5, swap=9000.0, per_close=1000.0, level=50.0, over=None):
        self.fl, self.closed, self.swap, self.per, self.level = fl, [], swap, per_close, level
        self.rows, self.table, self.anchor, self.env, self.ends = [], {1: {"ppid": 0, "start": S, "toks": ["launchd"]}}, {}, {}, {}
        for k in range(n):
            name, pid, spid = f"b-{k}", 700 + k, 600 + k
            self.rows.append({"name": name, "pid": pid, "pid_start": S, "mux": "screen",
                              "mux_session": name, "cwd": "/r", "launched_at": NOW - 40 * H})
            self.table[spid] = {"ppid": 1, "start": S, "toks": ["SCREEN", "-dmS", name]}
            self.table[pid] = {"ppid": spid, "start": S, "toks": ["claude", "--name", name]}
            self.anchor[name] = [spid]
            self.env[name] = f"claude --name {name} HOME=/h"
            self.ends[name] = end(ts=NOW - (10 + k) * H)       # b-4 is the oldest
        for name, kind in (over or {}).items():
            self.spoil(name, kind)

    def spoil(self, name, kind):
        row = next(r for r in self.rows if r["name"] == name)
        pid, spid = row["pid"], self.anchor[name][0]
        if kind == "attended":
            self.env[name] += " CLAUDE_CODE_SESSION_ATTENDED=1"
        elif kind == "host":
            self.table[pid]["toks"] = ["claude", "remote-control", "--name", "demo host"]
        elif kind == "never":
            row["name"] = row["mux_session"] = "rc-host-x"
            self.anchor["rc-host-x"], self.env["rc-host-x"] = self.anchor[name], self.env[name]
            self.ends["rc-host-x"] = self.ends[name]
            self.table[spid]["toks"] = ["SCREEN", "-dmS", "rc-host-x"]
        elif kind == "young":
            self.ends[name] = end(ts=NOW - 600)
        elif kind == "foreign-window":
            self.table[9000 + pid] = {"ppid": spid, "start": S, "toks": ["vim", "notes.txt"]}

    def gather(self, r, mux, me):
        return facts(end=self.ends[r["name"]])

    def refusals(self, r):
        a = self.anchor[r["name"]]
        why = self.fl.close_refusals(r, self.table, a, self.env[r["name"]], ["* host", "rc-host*"])
        return why, (None if why else a[0])

    def close(self, r, mux, anchor):
        self.closed.append(r["name"])
        return "screen quit"

    def readings(self):
        mb = max(0.0, self.swap - self.per * len(self.closed))
        return {"swap": mb / 9000.0, "swap_mb": mb, "level": self.level}

    def run(self, apply=True, all_finished=False):
        fl = self.fl
        return fl.close_when_needed(
            apply=apply, by="test", readings_fn=self.readings, settle_s=0, rows=self.rows,
            all_finished=all_finished,
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


def test_positive_over_the_line_the_oldest_close_first_until_under(alive):
    f = Fleet(alive, swap=7000.0)                      # 7000 MB → 6000 → 5000 → 4000: under 4096
    res = f.run()
    assert f.closed == ["b-4", "b-3", "b-2"]           # oldest first, stops once under 4096 MB
    assert res["stopped"] == "back under the line"
    assert res["after"]["swap_mb"] == pytest.approx(4000.0)
    assert [i["name"] for i in res["closed"]] == f.closed


def test_negative_under_the_line_nothing_is_gathered_or_closed(alive):
    f = Fleet(alive, swap=1000.0)
    calls = []
    res = alive.close_when_needed(apply=True, by="t", readings_fn=f.readings, settle_s=0,
                                  rows=f.rows, sweep_fn=lambda **kw: calls.append(kw) or [])
    assert res["stopped"] == "under the line" and calls == [] and f.closed == []


def test_negative_unreadable_figures_close_nothing(alive):
    f = Fleet(alive)
    res = alive.close_when_needed(apply=True, by="t", settle_s=0, rows=f.rows,
                                  readings_fn=lambda: {"swap": None, "swap_mb": None, "level": None},
                                  sweep_fn=lambda **kw: pytest.fail("no sweep on unreadable figures"))
    assert "could be read" in res["stopped"] and f.closed == []


def test_negative_a_dry_run_over_the_line_closes_nothing_and_lists_oldest_first(alive):
    f = Fleet(alive)
    res = f.run(apply=False)
    assert f.closed == [] and [i["name"] for i in res["candidates"]] == ["b-4", "b-3", "b-2", "b-1", "b-0"]


@pytest.mark.parametrize("kind, word", [
    ("attended", "attended:"),
    ("host", "host:"),
    ("never", "never:"),
    ("foreign-window", "screen:"),
])
def test_negative_each_unsafe_session_is_refused_with_its_reason_even_over_the_line(alive, kind, word):
    f = Fleet(alive, per_close=0.0, over={"b-4": kind})   # the line never clears: all get tried
    res = f.run()
    name = "rc-host-x" if kind == "never" else "b-4"
    assert name not in f.closed
    assert f.closed == ["b-3", "b-2", "b-1", "b-0"]
    item = next(i for i in res["items"] if i["name"] == name)
    assert any(w.startswith(word) for w in item["refused"]), item["refused"]


def test_negative_a_young_session_is_kept_even_over_the_line(alive):
    f = Fleet(alive, per_close=0.0, over={"b-4": "young"})
    res = f.run()
    assert "b-4" not in f.closed
    item = next(i for i in res["items"] if i["name"] == "b-4")
    assert any(k.startswith("finished:") for k in item["keep"])


def test_all_finished_closes_every_finished_session_whatever_the_line(alive):
    f = Fleet(alive, swap=100.0)
    f.run(all_finished=True)
    assert f.closed == ["b-4", "b-3", "b-2", "b-1", "b-0"]


def test_the_close_log_and_reopen_line_carry_the_uncommitted_paths(alive, tmp_path):
    f = Fleet(alive, n=1)
    f.gather = lambda r, mux, me: facts(end=f.ends[r["name"]], unsaved=None, wrote=["/r/w.md"],
                                         )
    f.ends["b-0"] = end(ts=NOW - 13 * H)                  # past the 12 h line: unknown does not keep
    res = f.run()
    assert f.closed == ["b-0"]
    assert "uncommitted: unknown" in res["closed"][0]["resume"] and "/r/w.md" in res["closed"][0]["resume"]
    log = (tmp_path / "state" / "close.log").read_text()
    assert "closed\tb-0" in log and "uncommitted: unknown" in log and "/r/w.md" in log


# ----------------------------------------------------------------- the command line ----

def _sessions():
    spec = importlib.util.spec_from_file_location("sessions_ct", TOOLS / "sessions.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_cli_when_needed_under_the_line_prints_one_line_and_sweeps_nothing(fl, monkeypatch, capsys):
    s = _sessions()
    monkeypatch.setattr(s.fleet, "memory_readings", lambda: {"swap": 0.9, "swap_mb": 800.0, "level": 70.0})
    monkeypatch.setattr(s.fleet, "sweep", lambda **kw: pytest.fail("no sweep under the line"))
    assert s.main(["close", "--when-needed"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("under the line") and out.count("\n") == 1


def test_cli_when_needed_over_the_line_dry_run_names_the_order(alive, monkeypatch, capsys):
    s = _sessions()
    f = Fleet(alive)
    real = alive.close_when_needed
    monkeypatch.setattr(s.fleet, "close_when_needed",
                        lambda **kw: real(**kw, readings_fn=f.readings, settle_s=0, rows=f.rows,
                                          sweep_fn=lambda **k: alive.sweep(now=NOW, gather_fn=f.gather,
                                                                           refusals_fn=f.refusals, **k)))
    assert s.main(["close", "--when-needed", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "over the line" in out and "dry run" in out and "oldest first: b-4, b-3" in out
    assert "closed 0" in out and f.closed == []


def test_negative_figures_are_read_again_after_the_sweep_before_the_first_close(alive):
    """The review's MF3 probe: over the line at the first reading, under by the end of the sweep."""
    f = Fleet(alive)
    seq = iter([{"swap": 1, "swap_mb": 9000.0, "level": 50.0}] + [{"swap": 0, "swap_mb": 100.0, "level": 80.0}] * 5)
    res = alive.close_when_needed(
        apply=True, by="t", settle_s=0, rows=f.rows, readings_fn=lambda: next(seq),
        sweep_fn=lambda **kw: alive.sweep(now=NOW, gather_fn=f.gather, refusals_fn=f.refusals, **kw),
        apply_fn=lambda i, by: pytest.fail("closed on a stale reading"))
    assert res["closed"] == [] and res["stopped"] == "back under the line"
    assert res["after"]["swap_mb"] == 100.0                  # the last reading, not the stale one


def test_an_error_on_one_session_stops_the_loop_and_keeps_the_summary(alive):
    f = Fleet(alive, per_close=0.0)
    calls = []

    def boom(i, by):
        calls.append(i["name"])
        if len(calls) == 2:
            raise OSError("disk")
        return alive.apply_item(i, by=by, gather_fn=f.gather, refusals_fn=f.refusals,
                                close_fn=f.close, clock=lambda: NOW)
    res = alive.close_when_needed(
        apply=True, by="t", settle_s=0, rows=f.rows, readings_fn=f.readings,
        sweep_fn=lambda **kw: alive.sweep(now=NOW, gather_fn=f.gather, refusals_fn=f.refusals, **kw),
        apply_fn=boom)
    assert f.closed == ["b-4"] and "raised OSError" in res["stopped"]
