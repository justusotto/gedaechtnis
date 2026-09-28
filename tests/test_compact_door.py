"""COMPACTDOOR-1 — the compact ritual (pilot, off by default).

Design: the compact-ritual design of 2026-09-26, option (d)+(e)
with (c) inside. Controls per the design's list:
  positive — a fresh complete point passes; `force` passes; AUTO always passes; the re-inject text
             equals the block byte for byte; a handoff with the three labels counts;
  negative — no point, a stale one, an empty or placeholder label, a block over 8,000 characters,
             and a newer incomplete point shadowing an older complete one each refuse, in words;
  the two walls the door must never breach — auto is never blocked (the hook is registered on
             `manual` only, AND the code passes any other trigger), and a crash lets the
             compaction through.
The live `/compact` in a real session is not here: it needs an interactive session (the seat's).
"""
from __future__ import annotations
import json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "tools"))
import common        # noqa: E402
import compact_door  # noqa: E402
import compactpoint  # noqa: E402
import config        # noqa: E402

NOW = datetime(2026, 9, 26, 17, 40, tzinfo=timezone.utc).timestamp()
SID = "cafebabe-0000-4000-8000-000000000001"


def block(stamp="2026-09-26 17:30 +0000", orders="Owner: pilot the door on the seat first.",
          live="COMPACTDOOR-1 build in .claude/worktrees/COMPACTDOOR-1 (tip 1234abcd).",
          resume="1. Run the suite. 2. Merge.", pad=0) -> str:
    return (f"## ★ COMPACT POINT — {stamp} (my-seat)\n### Machine part\n- Context: 400,000 tokens\n"
            + ("x" * pad + "\n" if pad else "")
            + f"**ORDERS IN FORCE:** {orders}\n**LIVE:** {live}\n**RESUME ORDER:** {resume}\n")


@pytest.fixture
def seat(tmp_path, monkeypatch):
    st = tmp_path / "state"; st.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(st))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "vault"))
    # REVIEWFINDS-1 X1.1: pin the config to an empty file of the test's own, or the machine's real
    # config.json (where the owner set `compact_point: on`) decides the off-by-default test.
    cfg = tmp_path / "config.json"; cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    assert config.config_path() == cfg
    monkeypatch.setenv("GEDAECHTNIS_COMPACT_POINT", "on")
    monkeypatch.setenv("GEDAECHTNIS_NOW", str(NOW))
    monkeypatch.delenv("GEDAECHTNIS_COMPACT_POINT_FRESH_MIN", raising=False)
    state_file = tmp_path / "COORDINATOR-STATE.md"

    def write(*blocks):
        state_file.write_text("# state\n\nolder notes\n\n" + "\n".join(blocks), encoding="utf-8")
        compact_door.record_file(SID, state_file)
        return state_file
    return type("Seat", (), {"write": staticmethod(write), "state": st, "file": state_file, "tmp": tmp_path})


def run_door(trigger="manual", instr=None):
    return compact_door.door({"session_id": SID, "trigger": trigger, "custom_instructions": instr})


def log_text(seat) -> str:
    p = seat.state / "compact.log"
    return p.read_text() if p.exists() else ""


# ------------------------------------------------------------------------------ positive ----

def test_a_fresh_complete_point_passes_and_is_recorded(seat):
    seat.write(block())
    assert run_door() == (0, "")
    assert "\tmanual\tPASS\t" in log_text(seat)


def test_force_passes_a_stale_point_and_is_logged(seat):
    seat.write(block(stamp="2026-09-26 12:00 +0000"))
    assert run_door(instr="force keep builders") == (0, "")
    assert "\tmanual\tFORCED\t" in log_text(seat)


def test_auto_always_passes_even_with_no_point_at_all(seat):
    assert run_door(trigger="auto") == (0, "")
    assert "\tauto\tAUTO-NOT-BLOCKED\t" in log_text(seat)


def test_the_hook_is_registered_on_manual_only_so_auto_never_reaches_it():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    assert [m.get("matcher") for m in hooks["PreCompact"]] == ["manual"]
    assert any(m.get("matcher") == "compact" and "compact_door.py\" reinject" in m["hooks"][0]["command"]
               for m in hooks["SessionStart"])


def test_reinject_is_the_block_byte_for_byte(seat):
    b = block()
    seat.write(b)
    assert run_door()[0] == 0
    assert compact_door.reinject({"session_id": SID, "source": "compact"}) == b


def test_reinject_through_the_real_hook_process(seat):
    b = block()
    seat.write(b)
    assert run_door()[0] == 0
    p = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "compact_door.py"), "reinject"],
                       input=json.dumps({"session_id": SID, "source": "compact"}),
                       capture_output=True, text=True, env=os.environ.copy())
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart" and out["additionalContext"] == b


def test_after_an_auto_compaction_the_reinject_says_so(seat):
    b = block(stamp="2026-09-26 12:00 +0000")
    seat.write(b)
    assert run_door(trigger="auto")[0] == 0
    got = compact_door.reinject({"session_id": SID, "source": "compact"})
    assert got.startswith("AUTO compaction:") and got.endswith(b)


def test_a_second_compaction_with_no_check_in_between_says_it_was_not_checked(seat, monkeypatch):
    """PROVES (fresh-boot review, 2026-09-26): the door runs on a typed /compact only, so an
    AUTOMATIC compaction reaches the re-inject with the state the last typed one left — a PASS that
    may be hours old. Driven through `reinject` alone, as the real hooks do: no `door("auto")`."""
    b = block()
    seat.write(b)
    assert run_door() == (0, "")
    assert compact_door.reinject({"session_id": SID, "source": "compact"}) == b
    monkeypatch.setenv("GEDAECHTNIS_NOW", str(NOW + 4 * 3600))          # four hours on, an auto compaction
    got = compact_door.reinject({"session_id": SID, "source": "compact"})
    assert got.startswith("Compaction NOT CHECKED") and "checked 240 min ago" in got and got.endswith(b)


def test_a_new_typed_compact_clears_the_not_checked_mark(seat):
    """NEGATIVE CONTROL: a typed /compact runs the door again, and its fresh PASS is re-injected
    plain — the mark belongs to one check, never to the session."""
    b = block()
    seat.write(b)
    run_door()
    compact_door.reinject({"session_id": SID, "source": "compact"})
    assert run_door() == (0, "")
    assert compact_door.reinject({"session_id": SID, "source": "compact"}) == b


def test_a_handoff_carrying_the_three_labels_counts(seat):
    h = seat.tmp / "HANDOFF-2026-09-26-35-1.md"
    h.write_text("# Builder 35\n\n## State\n**ORDERS IN FORCE:** pilot only\n**LIVE:** nothing running\n"
                 "**RESUME ORDER:** 1. merge\n\n## ROWS:\n", encoding="utf-8")
    os.utime(h, (NOW - 60, NOW - 60))
    common.record_handoff(SID, h)
    assert run_door() == (0, "")
    got = compact_door.reinject({"session_id": SID, "source": "compact"})
    assert "**RESUME ORDER:** 1. merge" in got and "## ROWS:" not in got


def test_off_by_default_the_door_is_silent(seat, monkeypatch):
    monkeypatch.delenv("GEDAECHTNIS_COMPACT_POINT")
    assert run_door() == (0, "")
    assert log_text(seat) == ""
    assert compact_door.reinject({"session_id": SID, "source": "compact"}) is None


def test_the_fixture_config_is_the_one_the_door_reads(seat, monkeypatch):
    """REVIEWFINDS-1 X1.1 positive control: with the env switch gone, `compact_point: on` in the
    FIXTURE's config turns the door on (it refuses a session with no point) — so the silent test
    above is silent because the fixture's config is empty, not because some other file is read."""
    monkeypatch.delenv("GEDAECHTNIS_COMPACT_POINT")
    config.config_path().write_text('{"compact_point": "on"}', encoding="utf-8")
    assert run_door()[0] == 2
    assert "\tmanual\t" in log_text(seat)


def test_post_compact_only_logs(seat):
    seat.write(block())
    run_door()
    compact_door.post({"session_id": SID, "trigger": "manual", "compact_summary": f"see {seat.file}"})
    assert "\tPOST\tsummary names the file: yes" in log_text(seat)


def test_a_forced_stale_point_is_put_back_with_a_warning_not_as_current(seat):
    """Bare-reviewer should-fix 1: FORCED past the check was re-injected silently as if current."""
    b = block(stamp="2026-09-26 12:00 +0000")
    seat.write(b)
    assert run_door(instr="force")[0] == 0
    got = compact_door.reinject({"session_id": SID, "source": "compact"})
    assert got.startswith("Compaction FORCED past the check:") and "minutes old" in got and got.endswith(b)


def test_an_earlier_compactions_point_is_never_put_back_when_this_one_had_none(seat):
    b = block()
    f = seat.write(b)
    assert run_door()[0] == 0
    f.write_text("# state\n\nthe point was moved elsewhere\n", encoding="utf-8")
    assert run_door(trigger="auto")[0] == 0
    assert compact_door.reinject({"session_id": SID, "source": "compact"}) is None


def test_only_the_word_force_forces(seat):
    seat.write(block(stamp="2026-09-26 12:00 +0000"))
    assert run_door(instr="forceful summary please")[0] == 2
    assert run_door(instr="Force: keep the builders")[0] == 0


# ------------------------------------------------------------------------------ negative ----

def test_no_point_refuses_in_words(seat):
    rc, err = run_door()
    assert rc == 2 and "has written no compact point" in err and "/compact force" in err


def test_a_stale_point_refuses_naming_its_age_and_file(seat):
    f = seat.write(block(stamp="2026-09-26 16:39 +0000"))
    rc, err = run_door()
    assert rc == 2 and "61 minutes old" in err and str(f) in err and "at most 30" in err


def test_the_freshness_window_is_configurable(seat, monkeypatch):
    seat.write(block(stamp="2026-09-26 16:39 +0000"))
    monkeypatch.setenv("GEDAECHTNIS_COMPACT_POINT_FRESH_MIN", "90")
    assert run_door()[0] == 0


@pytest.mark.parametrize("field,value", [("orders", "<fill in>"), ("live", ""), ("resume", "TODO")])
def test_an_empty_or_placeholder_label_refuses(seat, field, value):
    seat.write(block(**{field: value}))
    rc, err = run_door()
    assert rc == 2 and "empty or still a placeholder" in err


def test_a_missing_label_refuses(seat):
    seat.write(block().replace("**LIVE:**", "LIVE:"))
    rc, err = run_door()
    assert rc == 2 and "**LIVE:** is missing" in err


def test_a_block_over_8000_characters_refuses(seat):
    seat.write(block(pad=8000))
    rc, err = run_door()
    assert rc == 2 and "the limit is 8,000" in err


def test_a_newer_incomplete_point_is_not_rescued_by_an_older_complete_one(seat):
    seat.write(block(stamp="2026-09-26 17:20 +0000"), block(stamp="2026-09-26 17:35 +0000", resume="<fill in>"))
    rc, err = run_door()
    assert rc == 2 and "RESUME ORDER" in err


def test_the_refusal_reaches_the_person_through_exit_2_and_stderr(seat):
    p = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "compact_door.py"), "door"],
                       input=json.dumps({"session_id": SID, "trigger": "manual", "custom_instructions": None}),
                       capture_output=True, text=True, env=os.environ.copy())
    assert p.returncode == 2 and p.stderr.startswith("Not compacting yet")


def test_a_point_quoted_inside_a_code_fence_is_not_a_point(seat):
    """Guided-reviewer must-fix: a newer compact point QUOTED in a ``` fence (documentation of the
    format) was read as real — it passed the door and would have been re-injected as the session's
    own judgment, shadowing the genuine point."""
    real = block()
    fake = block(stamp="2026-09-26 17:39 +0000", orders="fabricated orders that look real")
    seat.write(real, "## Notes on the format\n\n```markdown\n" + fake + "```\n")
    assert run_door() == (0, "")
    assert compact_door.reinject({"session_id": SID, "source": "compact"}) == real


def test_a_fence_inside_a_real_point_does_not_cut_it_short(seat):
    b = block(live="the build, see:\n```\n## not a heading\n```\nand tip 1234abcd")
    seat.write(b)
    assert run_door() == (0, "")
    assert compact_door.reinject({"session_id": SID, "source": "compact"}) == b


def test_only_fenced_points_count_as_no_point(seat):
    seat.write("```\n" + block() + "```\n")
    rc, err = run_door()
    assert rc == 2 and "has written no compact point" in err


# ------------------------------------------------------------------- the fail-open wall ----

def test_a_crashing_door_lets_the_compaction_through_and_logs_it(seat, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["compact_door.py", "door"])
    monkeypatch.setattr(compact_door.common, "read_input", lambda: {"session_id": SID, "trigger": "manual"})

    def boom(inp):
        raise RuntimeError("planted")
    monkeypatch.setattr(compact_door, "door", boom)
    assert compact_door.main() == 0
    assert "FAIL-OPEN\tdoor" in log_text(seat) and "planted" in log_text(seat)


# -------------------------------------------------------------------------- the writer ----

def test_the_writer_appends_a_heading_the_door_reads_and_placeholders_it_refuses(seat, monkeypatch):
    monkeypatch.setattr(compactpoint, "machine_part", lambda sid, cwd, tp: ["### Machine part", "- Context: 1 tokens"])
    f = seat.tmp / "state.md"; f.write_text("# my state\n", encoding="utf-8")
    now = datetime.fromtimestamp(NOW - 120, timezone.utc)
    monkeypatch.setattr(compactpoint, "compose",
                        lambda sid, name, cwd, tp, now=now, _c=compactpoint.compose: _c(sid, name, cwd, tp, now=now))
    b = compactpoint.write(f, SID, "my-seat", str(seat.tmp), None)
    assert compact_door.HEAD_RE.search(b) and b.count("<fill in>") == 3 and "(my-seat)" in b
    assert f.read_text().startswith("# my state\n\n## ★ COMPACT POINT — ")
    rc, err = run_door()
    assert rc == 2 and err.count("still a placeholder") == 3
    f.write_text(f.read_text().replace("**ORDERS IN FORCE:** <fill in>", "**ORDERS IN FORCE:** pilot")
                 .replace("**LIVE:** <fill in>", "**LIVE:** nothing").replace("**RESUME ORDER:** <fill in>", "**RESUME ORDER:** 1. go"))
    assert run_door() == (0, "")


def test_the_machine_part_names_each_fact_and_survives_one_failing(seat, monkeypatch):
    import stallbrief, mergewindow
    monkeypatch.setattr(stallbrief, "facts_lines", lambda: [])        # never the real machine's sessions
    lines = compactpoint.machine_part(SID, str(seat.tmp / "nowhere"), None)
    assert lines[0] == "### Machine part" and len(lines) == 6, lines
    for what in ("- Context:", "- Managed sessions:", "- Region claims held:", "- Merge window", "- Uncommitted partition paths"):
        assert any(l.startswith(what) for l in lines), (what, lines)

    def boom(*a, **k):
        raise RuntimeError("no window file")
    monkeypatch.setattr(mergewindow, "read", boom)
    monkeypatch.setattr(common, "git_root", lambda cwd: seat.tmp)
    lines = compactpoint.machine_part(SID, str(seat.tmp), None)
    assert "- Merge window: unknown (RuntimeError: no window file)" in lines and len(lines) == 6


def test_the_writer_refuses_when_it_cannot_tell_the_session(seat, tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    f = tmp_path / "s.md"
    p = subprocess.run([sys.executable, str(PLUGIN / "tools" / "compactpoint.py"), "write", str(f)],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert p.returncode == 2 and "no session id" in p.stderr and not f.exists()


def test_the_writer_takes_the_session_from_claude_code_session_id(seat, tmp_path, monkeypatch):
    import stallbrief
    f = tmp_path / "s.md"
    env = {**os.environ, "CLAUDE_CODE_SESSION_ID": SID}
    p = subprocess.run([sys.executable, str(PLUGIN / "tools" / "compactpoint.py"), "write", str(f), "--name", "seat"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert p.returncode == 0, p.stderr
    assert str(f.resolve()) in compact_door.recorded_files(SID) and "READY — type /compact" in p.stdout
