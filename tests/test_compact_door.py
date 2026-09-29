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
import json, os, shlex, stat, subprocess, sys, threading, time
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
    # `rewake` waits a second before it re-judges a refusal; in-process tests skip the wait (the
    # tests of the wait put it back), the subprocess test runs it for real.
    monkeypatch.setattr(compact_door, "REWAKE_SETTLE_S", 0)
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


# ------------------------------------------- COMPACTDOOR-2: the session answers a refusal ----

LIVE_CASE = "2026-09-26 16:54 +0000"      # 46 minutes before NOW: the 2026-09-28 15:03 refusal's age


def run_check(f, sid=SID):
    return compactpoint.check(Path(f), sid)


def test_CHECK_POSITIVE_a_fresh_complete_point_is_READY(seat):
    rc, out = run_check(seat.write(block()))
    assert rc == 0 and out.startswith("READY")


@pytest.mark.parametrize("b,word", [(block(stamp=LIVE_CASE), "46 minutes old"),
                                    (block(live="<fill in>"), "**LIVE:** is empty"),
                                    (block(pad=8200), "8,000")])
def test_CHECK_NEGATIVE_age_labels_and_size_each_refuse_in_words(seat, b, word):
    rc, out = run_check(seat.write(b))
    assert rc == 1 and out.startswith("NOT READY") and word in out


@pytest.mark.parametrize("b", [block(), block(stamp=LIVE_CASE), block(orders="TBD"), block(pad=8200),
                               block(stamp="2026-09-26 17:15 +0000")])
def test_CHECK_measures_exactly_as_the_door_does(seat, b):
    f = seat.write(b)
    assert (run_check(f)[0] == 0) == (run_door()[0] == 0)


def test_CHECK_a_stale_point_names_renew_because_an_edit_keeps_the_old_stamp(seat):
    rc, out = run_check(seat.write(block(stamp=LIVE_CASE)))
    assert rc == 1 and "does not renew its stamp" in out and "renew" in out


def test_CHECK_text_written_below_the_point_is_named_when_it_breaks_the_limit(seat):
    addendum = "### Addendum after the point\n" + "y" * 8200 + "\n"
    rc, out = run_check(seat.write(block() + addendum))
    assert rc == 1 and "come AFTER **RESUME ORDER:**" in out
    rc, out = run_check(seat.write(block() + "### Short addendum\nfine\n"))
    assert rc == 0                                   # under the limit it is harmless, and not named


def test_CHECK_no_point_in_the_file_refuses(seat, tmp_path):
    f = tmp_path / "empty.md"; f.write_text("# nothing here\n")
    rc, out = run_check(f)
    assert rc == 1 and "no compact point" in out


def test_RENEW_POSITIVE_restamps_only_the_newest_heading_and_the_check_then_passes(seat):
    f = seat.write(block(stamp="2026-09-26 12:00 +0000"), block(stamp=LIVE_CASE))
    before = f.read_text()
    rc, out = compactpoint.renew(f)                 # the door's clock: GEDAECHTNIS_NOW
    assert rc == 0 and out.startswith("Renewed:")
    (stamp, _), = [b for b in compact_door.blocks_in(f.read_text()) if b[0].timestamp() == NOW]
    after = f.read_text()
    assert "2026-09-26 12:00 +0000" in after and LIVE_CASE not in after
    assert [l for l in before.splitlines() if "COMPACT POINT" not in l] == \
           [l for l in after.splitlines() if "COMPACT POINT" not in l]
    assert run_check(f)[0] == 0 and run_door() == (0, "")


def test_RENEW_NEGATIVE_a_file_without_a_point_is_refused_in_words_and_unchanged(seat, tmp_path):
    f = tmp_path / "notes.md"; f.write_text("# notes\n**LIVE:** x\n")
    rc, out = compactpoint.renew(f)
    assert rc == 2 and "no `## ★ COMPACT POINT` heading" in out and f.read_text() == "# notes\n**LIVE:** x\n"


def test_WRITE_POSITIVE_the_new_point_is_the_last_thing_in_the_file(seat, monkeypatch):
    monkeypatch.setattr(compactpoint, "machine_part", lambda sid, cwd, tp: ["### Machine part"])
    f = seat.tmp / "s.md"; f.write_text("# state\n")
    b = compactpoint.write(f, SID, "seat", str(seat.tmp), None)
    assert f.read_text().endswith(b)


def test_WRITE_NEGATIVE_text_left_after_the_point_is_refused_in_words(seat, monkeypatch):
    monkeypatch.setattr(compactpoint, "machine_part", lambda sid, cwd, tp: ["### Machine part"])
    real = compact_door._read
    monkeypatch.setattr(compact_door, "_read", lambda p: (real(p) or "") + "\n### another writer's line\n")
    f = seat.tmp / "s.md"; f.write_text("# state\n")
    with pytest.raises(RuntimeError, match="must be the last thing in the file"):
        compactpoint.write(f, SID, "seat", str(seat.tmp), None)
    assert str(f.resolve()) not in compact_door.recorded_files(SID)


def run_rewake(trigger="manual", instr=None):
    return compact_door.rewake({"session_id": SID, "trigger": trigger, "custom_instructions": instr})


def test_REWAKE_POSITIVE_a_refusal_becomes_a_task_for_the_session(seat):
    f = seat.write(block(stamp=LIVE_CASE))
    rc, task = run_rewake()
    assert rc == 2 and "REFUSED" in task and "46 minutes old" in task
    assert f"check {f}" in task and f"renew {f}" in task and task.rstrip().endswith("READY — type /compact")
    assert "\tREWAKE\t" in log_text(seat)


def test_REWAKE_with_no_point_tells_the_session_to_write_one(seat):
    rc, task = run_rewake()
    assert rc == 2 and "compactpoint.py write <file> --session-id " + SID in task


@pytest.mark.parametrize("trigger,instr,make", [("manual", None, True), ("manual", "force now", False),
                                                ("auto", None, False)])
def test_REWAKE_NEGATIVE_a_pass_a_force_and_an_auto_wake_nobody(seat, trigger, instr, make):
    if make:
        seat.write(block())
    assert run_rewake(trigger, instr) == (0, "")


def test_REWAKE_writes_no_state_the_door_keeps_the_verdict(seat):
    seat.write(block(stamp=LIVE_CASE))
    run_rewake()
    assert "compact_point" not in common._session_doc(SID)
    assert not (seat.state / f"compact-point-{common.safe_sid(SID)}.md").exists()


def test_REWAKE_off_by_default(seat, monkeypatch):
    monkeypatch.delenv("GEDAECHTNIS_COMPACT_POINT")
    assert run_rewake() == (0, "")


def test_REWAKE_is_registered_async_beside_the_blocking_door_on_manual_only():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    (group,) = hooks["PreCompact"]
    assert group["matcher"] == "manual"
    by = {h["command"].rsplit(" ", 1)[-1]: h for h in group["hooks"]}
    assert set(by) == {"door", "rewake"}
    assert by["rewake"].get("asyncRewake") is True and not by["door"].get("asyncRewake")
    assert not by["door"].get("async")


def test_REWAKE_through_the_real_hook_process(seat):
    seat.write(block(stamp=LIVE_CASE))
    p = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "compact_door.py"), "rewake"],
                       input=json.dumps({"session_id": SID, "trigger": "manual", "custom_instructions": None}),
                       capture_output=True, text=True, env=os.environ.copy(), timeout=30)
    assert p.returncode == 2 and "READY — type /compact" in p.stderr


def test_the_door_refusal_names_the_exact_check_command(seat):
    f = seat.write(block(stamp=LIVE_CASE))
    rc, err = run_door()
    tool = PLUGIN.resolve() / "tools" / "compactpoint.py"
    assert rc == 2 and f"`python3 {tool} check {f} --session-id {SID}`" in err


def _backticked_after(text, lead):
    rest = text.split(lead, 1)[1]
    assert rest.startswith("`"), rest[:40]
    return rest[1:].split("`", 1)[0]


def test_DOOR_the_check_command_is_one_shell_line_even_for_a_path_with_a_space(seat):
    """xhigh review must-fix 3: the path had backticks of its own inside the backticked command
    (`…check `/path/STATE.md``); pasted into zsh, the inner pair ran the file. Now the path is
    shell-quoted, and the command between the backticks splits back into exactly its words."""
    d = seat.tmp / "my state dir"; d.mkdir()
    f = d / "STATE.md"; f.write_text("# s\n\n" + block(stamp=LIVE_CASE), encoding="utf-8")
    compact_door.record_file(SID, f)
    tool = str(PLUGIN.resolve() / "tools" / "compactpoint.py")
    rc, err = run_door()
    cmd = _backticked_after(err, "The check it runs is ")
    assert rc == 2 and "`" not in cmd
    assert shlex.split(cmd) == ["python3", tool, "check", str(f.resolve()), "--session-id", SID]
    rc, task = run_rewake()
    cmd = _backticked_after(task, "; run ")
    assert rc == 2 and shlex.split(cmd) == ["python3", tool, "check", str(f.resolve()), "--session-id", SID]


def test_DOOR_the_refusal_says_what_to_do_when_the_session_does_not_answer(seat):
    seat.write(block(stamp=LIVE_CASE))
    rc, err = run_door()
    assert rc == 2 and "not answered within a minute, ask it to run /compact-ready" in err
    assert "not been seen live" in err


# ---------------------------- COMPACTDOOR-2 round 1: check judges the point the door judges ----

SAME = "2026-09-26 17:30 +0000"


def test_CHECK_equal_stamps_the_later_point_is_judged_as_the_door_does(seat):
    """(a) Two points stamped the same minute, the first filled, the second still `<fill in>`: the
    door takes the later one and refuses; `check` took the first and said READY."""
    f = seat.write(block(stamp=SAME), block(stamp=SAME, resume="<fill in>"))
    assert run_door()[0] == 2
    rc, out = run_check(f)
    assert rc == 1 and "RESUME ORDER" in out
    rc, out = run_check(f, sid=None)                 # the file-only reading uses the same rule
    assert rc == 1 and "RESUME ORDER" in out


def test_CHECK_equal_stamps_NEGATIVE_a_filled_later_point_is_READY(seat):
    f = seat.write(block(stamp=SAME, resume="<fill in>"), block(stamp=SAME))
    assert run_door() == (0, "")
    assert run_check(f)[0] == 0
    rc, out = run_check(f, sid=None)                 # door on, no id: never READY (finding C)
    assert rc == 1 and out.startswith("NOT CONFIRMED") and "passes on its own" in out


def _other_file(seat, *blocks):
    g = seat.tmp / "HANDOFF-other.md"
    g.write_text("# other\n\n" + "\n".join(blocks), encoding="utf-8")
    compact_door.record_file(SID, g)
    return g


def test_CHECK_a_newer_incomplete_point_in_another_recorded_file_is_named(seat):
    """(b) `check A`, A complete at 17:30, a recorded B incomplete at 17:35: the door judges B."""
    a = seat.write(block(stamp=SAME))
    b = _other_file(seat, block(stamp="2026-09-26 17:35 +0000", live="<fill in>"))
    assert run_door()[0] == 2
    rc, out = run_check(a)
    assert rc == 1 and out.startswith("NOT READY") and f"the door judges {b.resolve()}" in out


def test_CHECK_an_older_point_in_another_recorded_file_does_not_shadow_this_one(seat):
    """NEGATIVE control for (b): the other recorded file's point is OLDER, so A is the door's."""
    _other_file(seat, block(stamp="2026-09-26 17:25 +0000", live="<fill in>"))
    a = seat.write(block(stamp=SAME))
    assert run_door() == (0, "")
    assert run_check(a)[0] == 0


def test_CHECK_a_point_in_a_file_not_recorded_for_this_session_is_not_READY(seat):
    """(c) A complete point in a file the door never looks at."""
    f = seat.tmp / "unrecorded.md"; f.write_text(block(), encoding="utf-8")
    assert run_door()[0] == 2
    rc, out = run_check(f)
    assert rc == 1 and "the door finds no compact point for session" in out and str(f) in out
    compact_door.record_file(SID, f)                 # NEGATIVE control: recorded, it is the door's
    assert run_door() == (0, "") and run_check(f)[0] == 0


def test_CHECK_the_command_line_takes_the_session_from_the_env_or_the_flag(seat):
    a = seat.write(block(stamp=SAME))
    _other_file(seat, block(stamp="2026-09-26 17:35 +0000", live="<fill in>"))
    tool = [sys.executable, str(PLUGIN / "tools" / "compactpoint.py"), "check", str(a)]
    bare = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    for extra, env in (([], {**bare, "CLAUDE_CODE_SESSION_ID": SID}), (["--session-id", SID], bare)):
        p = subprocess.run(tool + extra, capture_output=True, text=True, env=env, timeout=30)
        assert p.returncode == 1 and "the door judges" in p.stderr, (extra, p.stderr)
    p = subprocess.run(tool, capture_output=True, text=True, env=bare, timeout=30)
    assert p.returncode == 1 and p.stderr.startswith("NOT CONFIRMED — pass --session-id") and not p.stdout


def test_CHECK_READY_names_the_minute_the_point_turns_too_old(seat, monkeypatch):
    f = seat.write(block(stamp=SAME))
    until = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc).astimezone()
    rc, out = run_check(f)
    assert rc == 0 and out.startswith(f"READY until {until:%H:%M} ")
    monkeypatch.setenv("GEDAECHTNIS_COMPACT_POINT_FRESH_MIN", "90")
    later = datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc).astimezone()
    assert run_check(f)[1].startswith(f"READY until {later:%H:%M} ")


# ------------------------------------ COMPACTDOOR-2 round 1: renew changes only the heading ----

def _renewed_stamp():
    return f"{datetime.fromtimestamp(NOW, timezone.utc).astimezone():%Y-%m-%d %H:%M %z}"


def test_RENEW_keeps_CRLF_line_endings(seat):
    f = seat.tmp / "crlf.md"
    raw = ("# state\n\n" + block(stamp=LIVE_CASE)).replace("\n", "\r\n").encode("utf-8")
    f.write_bytes(raw)
    compact_door.record_file(SID, f)
    rc, out = compactpoint.renew(f)
    after = f.read_bytes()
    assert rc == 0 and after.count(b"\r\n") == raw.count(b"\r\n") and b"\n" not in after.replace(b"\r\n", b"")
    assert after == raw.replace(LIVE_CASE.encode(), _renewed_stamp().encode())
    assert run_check(f)[0] == 0 and run_door() == (0, "")


def test_RENEW_refuses_a_file_that_is_not_UTF8_and_leaves_it_untouched(seat):
    f = seat.tmp / "latin1.md"
    raw = b"# caf\xe9\n\n" + block(stamp=LIVE_CASE).encode("utf-8")
    f.write_bytes(raw)
    rc, out = compactpoint.renew(f)
    assert rc == 2 and "not UTF-8" in out and "Nothing was changed" in out
    assert f.read_bytes() == raw and not list(seat.tmp.glob("*.compactpoint-tmp"))


def test_RENEW_a_symlink_stays_a_symlink_and_its_target_keeps_its_mode(seat):
    target = seat.tmp / "real-state.md"
    target.write_text("# s\n\n" + block(stamp=LIVE_CASE), encoding="utf-8")
    os.chmod(target, 0o640)
    link = seat.tmp / "STATE-link.md"
    link.symlink_to(target)
    compact_door.record_file(SID, link)              # recorded resolved, as the door reads it
    rc, out = compactpoint.renew(link)
    assert rc == 0 and link.is_symlink() and os.readlink(link) == str(target)
    assert _renewed_stamp() in target.read_text() and LIVE_CASE not in target.read_text()
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert run_door() == (0, "")


def test_RENEW_restamps_the_later_of_two_equal_stamps(seat):
    f = seat.write(block(stamp=LIVE_CASE), block(stamp=LIVE_CASE, orders="the later point"))
    assert compactpoint.renew(f)[0] == 0
    ((stamp, b),) = [x for x in compact_door.blocks_in(f.read_text()) if x[0].timestamp() == NOW]
    assert "the later point" in b and LIVE_CASE in f.read_text()


def test_TTY_a_door_started_on_a_terminal_prints_usage_and_never_reads(seat):
    """The tty guard (main): a hook entry started with a TERMINAL on stdin returns the usage
    instead of waiting for an end-of-input a terminal never sends. Bounded by a timeout, and the
    terminal's master end is closed on the way out, so a regression fails instead of hanging."""
    pty = pytest.importorskip("pty", reason="no pty module on this platform")
    try:
        master, slave = pty.openpty()
    except OSError as e:
        pytest.skip(f"no pseudo-terminal available here ({e})")
    p = subprocess.Popen([sys.executable, str(PLUGIN / "hooks" / "compact_door.py"), "door"],
                         stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    os.close(slave)
    try:
        try:
            rc = p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            rc = None
    finally:
        os.close(master)                             # EOF/EIO for a reader still waiting on the tty
        if p.poll() is None:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()
    out = p.stdout.read()
    assert rc == 0 and out.startswith("usage: compact_door.py"), (rc, out)


@pytest.mark.parametrize("arg", ["--help", "-h", "nonsense"])
def test_HELP_returns_at_once_with_stdin_left_open(arg):
    """`compact_door.py --help` read stdin and waited for an end-of-input a terminal never sends.
    Here stdin is a pipe kept OPEN: the old code blocks until the timeout, the fixed one returns."""
    p = subprocess.Popen([sys.executable, str(PLUGIN / "hooks" / "compact_door.py"), arg],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        rc = p.wait(timeout=10)                      # stdin still open: a read would block here
        out = p.stdout.read()
    finally:
        p.stdin.close()
    assert rc == 0 and out.startswith("usage: compact_door.py")


# ------------------- COMPACTDOOR-2 round 2 (xhigh review, optional findings A-D, all taken) ----

def _race(seat, seconds=1.5):
    """The reviewer's probe, in-process: one thread runs the door on a fresh complete point over and
    over (each PASS rewrites the session record), while this thread judges the same /compact as
    `rewake` does. Every REFUSED here is a disagreement. Returns (disagreements, reads, writes)."""
    seat.write(block())
    stop, writes = threading.Event(), [0]

    def door_loop():
        while not stop.is_set():
            assert run_door() == (0, "")
            writes[0] += 1
    t = threading.Thread(target=door_loop, daemon=True)
    t.start()
    bad = reads = 0
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            _, missing, _, _ = compact_door.evaluate({"session_id": SID, "trigger": "manual"})
            reads += 1
            bad += bool(missing)
    finally:
        stop.set()
        t.join(timeout=10)
    return bad, reads, writes[0]


def test_A_POSITIVE_rewake_never_reads_a_half_written_record_while_the_door_writes(seat):
    bad, reads, writes = _race(seat)
    assert reads > 200 and writes > 20, (reads, writes)          # the race actually ran
    assert bad == 0, f"{bad} of {reads} readings saw REFUSED while the door passed ({writes} writes)"
    assert not [p.name for p in seat.state.iterdir() if p.name.endswith(".tmp")]


def test_A_NEGATIVE_the_probe_bites_on_a_writer_that_truncates_first(seat, monkeypatch):
    """Control for the test above: the old `write_text` (truncate, then write), with the gap between
    the two widened a little, is seen half written by the same probe."""
    def truncate_then_write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text[: len(text) // 2]); fh.flush()
            time.sleep(0.0005)
            fh.write(text[len(text) // 2:])
    monkeypatch.setattr(common, "write_text_atomic", truncate_then_write)
    bad, reads, _ = _race(seat, seconds=1.0)
    assert bad > 0, f"0 of {reads}: the probe cannot see a half-written record"


def test_A_write_text_atomic_keeps_the_mode_writes_through_a_link_and_leaves_no_temp(tmp_path):
    f = tmp_path / "session-start-x.json"
    common.write_text_atomic(f, "{}")                              # new: the umask's default, as write_text
    probe = tmp_path / "probe"; probe.write_text("")
    assert stat.S_IMODE(f.stat().st_mode) == stat.S_IMODE(probe.stat().st_mode) and f.read_text() == "{}"
    os.chmod(f, 0o600)
    link = tmp_path / "link.json"; link.symlink_to(f)
    common.write_text_atomic(link, '{"a": "★"}')
    assert link.is_symlink() and json.loads(f.read_text(encoding="utf-8")) == {"a": "★"}
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["link.json", "probe", "session-start-x.json"]


def _stale_then(seat, monkeypatch, during):
    """A stale point, `rewake` with its real one-second wait, and `during()` run in that wait (the
    door finishing its write). Returns rewake's answer and the wait it asked for."""
    seat.write(block(stamp=LIVE_CASE))
    monkeypatch.setattr(compact_door, "REWAKE_SETTLE_S", 1.0)
    asked = []
    monkeypatch.setattr(compact_door.time, "sleep", lambda s: (asked.append(s), during()))
    return run_rewake(), asked


def test_A_REWAKE_a_point_passing_at_the_second_reading_wakes_nobody(seat, monkeypatch):
    got, asked = _stale_then(seat, monkeypatch, lambda: seat.write(block()))
    assert got == (0, "") and asked == [1.0] and "\tREWAKE-SETTLED\t" in log_text(seat)


def _door_said(verdict, ago_s):
    def put():
        at = datetime.fromtimestamp(NOW - ago_s, timezone.utc).isoformat()
        common.update_session_state(SID, lambda d: d.__setitem__(
            "compact_point", {"file": "f", "verdict": verdict, "missing": [], "at": at}))
    return put


@pytest.mark.parametrize("verdict", ["PASS", "FORCED"])
def test_A_REWAKE_a_pass_the_door_recorded_for_this_compact_wakes_nobody(seat, monkeypatch, verdict):
    got, _ = _stale_then(seat, monkeypatch, _door_said(verdict, 0))
    assert got == (0, "") and "the door recorded a pass" in log_text(seat)


@pytest.mark.parametrize("verdict,ago", [("PASS", 60), ("AUTO-NOT-BLOCKED", 0), ("NONE", 0)])
def test_A_REWAKE_NEGATIVE_an_older_pass_or_another_verdict_still_wakes(seat, monkeypatch, verdict, ago):
    got, _ = _stale_then(seat, monkeypatch, _door_said(verdict, ago))
    assert got[0] == 2 and "REFUSED" in got[1]


def _stale_handoff(seat):
    h = seat.tmp / "HANDOFF-row.md"
    h.write_text("# Handoff\n\n**ORDERS IN FORCE:** pilot first.\n**LIVE:** build at 1234abcd.\n"
                 "**RESUME ORDER:** 1. merge.\n", encoding="utf-8")
    old = NOW - 46 * 60
    os.utime(h, (old, old))
    common.record_handoff(SID, h)
    return h


def test_B_REWAKE_a_stale_handoff_is_saved_not_renewed(seat):
    h = _stale_handoff(seat)
    rc, task = run_rewake()
    assert rc == 2 and "46 minutes old" in task
    assert f"handoff {h}" in task and "save it" in task and "modification time" in task
    assert " renew " not in task and "renew {h}".format(h=h) not in task
    rc, err = run_door()
    assert rc == 2 and "save the handoff" in err and "compactpoint.py renew" not in err


def test_B_NEGATIVE_a_stale_heading_is_still_renewed(seat):
    f = seat.write(block(stamp=LIVE_CASE))
    rc, task = run_rewake()
    assert rc == 2 and f"renew {f}" in task and "save it" not in task


def test_C_door_off_and_no_session_id_is_still_READY(seat, monkeypatch):
    f = seat.write(block())
    monkeypatch.delenv("GEDAECHTNIS_COMPACT_POINT")
    rc, out = run_check(f, sid=None)
    assert rc == 0 and out.startswith("READY until") and "The door is off here" in out


def test_C_door_on_and_no_session_id_is_NOT_CONFIRMED_even_for_a_file_the_door_ignores(seat):
    f = seat.tmp / "unrecorded.md"; f.write_text(block(), encoding="utf-8")
    assert run_door()[0] == 2
    rc, out = run_check(f, sid=None)
    assert rc == 1 and out.startswith("NOT CONFIRMED — pass --session-id") and "READY until" not in out


def test_D_the_task_says_renew_only_after_re_checking_the_three_lines(seat):
    f = seat.write(block(stamp=LIVE_CASE))
    rc, task = run_rewake()
    assert rc == 2 and task.index("re-check the three lines") < task.index("only after that re-check")
    assert task.index("only after that re-check") < task.index(f"renew {f}")
    rc, out = run_check(f)
    assert rc == 1 and "re-check its three lines against what is true now, and only then run" in out


def test_A_every_other_writer_of_the_session_record_goes_through_the_atomic_write(seat, monkeypatch):
    """`update_session_state` is covered by the race above. The region-claim record is checked by
    running its writer; SessionStart runs only as a hook process, so its three writes of the record
    are checked in its source."""
    import claim
    seen = []
    monkeypatch.setattr(common, "write_text_atomic", lambda p, t: seen.append((Path(p).name, json.loads(t))))
    claim._write_claims(SID, {"x": 1}, [{"region": "R"}])
    assert seen == [(f"session-start-{SID}.json", {"x": 1, "claims": [{"region": "R"}]})]
    src = (PLUGIN / "hooks" / "session_start.py").read_text(encoding="utf-8")
    writes = [ln for ln in src.splitlines() if "session-start" in ln or "sid_file" in ln]
    assert not [ln for ln in writes if ".write_text(" in ln]
    assert sum("common.write_text_atomic(" in ln for ln in writes) == 3


def test_A_the_lock_covers_the_whole_read_modify_write_across_processes(tmp_path):
    """Round-3 xhigh finding 1: 12 processes x 25 increments keep all 300 (without the lock, 44 of 300)."""
    state = tmp_path / "state"
    code = ("import sys; sys.path.insert(0, %r); import common\n"
            "for _ in range(25):\n"
            "    common.update_session_state('sid-lock', lambda d: d.__setitem__('n', d.get('n', 0) + 1))\n"
            % str(PLUGIN / "hooks"))
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(state), GEDAECHTNIS_VAULT=str(tmp_path / "vault"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"))
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env) for _ in range(12)]
    assert [p.wait(timeout=120) for p in procs] == [0] * 12
    doc = json.loads((state / "session-start-sid-lock.json").read_text(encoding="utf-8"))
    assert doc["n"] == 300
