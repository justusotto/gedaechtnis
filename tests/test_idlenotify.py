"""IDLENOTIFY-1 — a finished session tells the seat that ignited it.

Every guard here has a POSITIVE and a NEGATIVE control, because each one has a silent failure
direction and they are not the same silence:

  * the door is OFF by default, so "sent nothing" is the correct output for almost every session
    that will ever run this code — a suite that only proved the send would pass just as well
    against a module that sends to everybody;
  * the door is ON for a named session, so "sent nothing" is also what a broken pattern, an
    unreadable mailbox and an un-nameable seat all print.

The three trigger inputs are built from REAL artefacts rather than asserted through mocks: a real
session record written by `common`, a real transcript in the harness's own JSONL shape, and a real
`ps` command line for the name reader. The one thing stubbed is the transport, which is a seam by
design and whose contract is "it may fail and nothing is lost".
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

PATTERN = r"^(?P<seat>[a-z0-9-]+?)-(?P<lane>builder|audit)-(?P<qid>[A-Za-z0-9-]+)$"


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A temp state dir, a temp vault, and a config file the modules actually read.

    `GEDAECHTNIS_STATE_DIR` is the seam every session record goes through. Without it this suite
    would write the MACHINE's live session records and read its live mailboxes — and the verdicts
    would then depend on who else is working right now. (That is not hypothetical: invoking the
    Stop hook by hand during this row's build overwrote the machine's own `session-start.json`.)
    """
    state = tmp_path / "state"
    state.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    # ★ `GEDAECHTNIS_CONFIG`, not a moved HOME. `config.home()` expands `~` fresh on every call
    # and is not env-overridable; the seam for the config FILE is this variable. Without it
    # `set_config` below writes the machine's real `~/.claude/gedaechtnis/config.json` — which is
    # what happened on the first run of this suite, and `vault_sentinel` named the file and the
    # test in one line. That is the guard doing its job, and this comment is here so the next
    # author reaches for the seam rather than for HOME.
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(state))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(vault))

    import importlib
    import config, common, idlenotify, procs                          # noqa: PLC0415
    importlib.reload(config)
    importlib.reload(common)
    importlib.reload(procs)
    importlib.reload(idlenotify)
    assert str(config.state()) == str(state), "the sandbox state dir is not bound"
    assert str(config.config_path()) == str(cfg), "the sandbox config file is not bound"

    def set_config(**kw):
        (config.config_path()).parent.mkdir(parents=True, exist_ok=True)
        config.config_path().write_text(json.dumps(kw), encoding="utf-8")
        importlib.reload(config)
        importlib.reload(idlenotify)
        return idlenotify

    return {"state": state, "vault": vault, "tmp": tmp_path,
            "idlenotify": idlenotify, "common": common, "config": config,
            "set_config": set_config}


@pytest.fixture(autouse=True)
def no_ambient_session_name(request, monkeypatch):
    """★ THE HARNESS'S OWN LAUNCH NAME IS NOT A FIXTURE. `notify(name=None)` means "look it up",
    and the lookup is `procs.session_name(procs.claude_pid())` — a real `ps` walk of whatever
    process happens to be pytest's ancestor. A session launched with `--name cursus-builder-ROW`
    therefore SUPPLIES a passing value to every test that forgot to pass one, and the same file
    is red in a plain terminal, in CI, and in any session started without `--name`. That is a
    precondition a fixture supplied and nobody asserted: six tests here were green only in the
    builder's own pane (`q:CU-2026-09-23-IDLETEST-1`).

    So the ambient answer is pinned to None for every test that drives the module through `box`:
    a test either passes `name=` explicitly, or arranges the lookup itself (both of those win,
    being applied after this one), and no test can be carried by the launcher again.

    The two tests that are ABOUT the real reader (the `--name` spellings one and the
    no-name-flag one) do not use `box`, and that is exactly the line drawn here: for them the
    real `procs.session_name` IS the thing under test, and this stub would have them assert
    against itself.
    `box` is resolved FIRST because it `importlib.reload`s `procs`, which would otherwise rebind
    `session_name` back to the real one after this patch was applied."""
    if "box" not in request.fixturenames:
        return
    request.getfixturevalue("box")
    import procs                                                          # noqa: PLC0415
    monkeypatch.setattr(procs, "session_name", lambda pid: None)


def transcript(tmp: Path, lines: list[dict]) -> Path:
    p = tmp / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in lines) + "\n", encoding="utf-8")
    return p


def assistant(text: str, sidechain: bool = False) -> dict:
    return {"type": "assistant", "isSidechain": sidechain,
            "message": {"id": "m1", "content": [{"type": "text", "text": text}]}}


# --------------------------------------------------------------------- the pattern is the switch


def test_off_by_default_sends_nothing_and_reads_nothing(box, tmp_path, monkeypatch):
    """NEGATIVE control for the whole module: no pattern, no send — and nothing consulted.

    ★ THE LAST ASSERTION IS THE ONLY ONE THAT SEES THE FIRST GUARD. Deleting `notify`'s opening
    `session_name_pattern is None` check left this test green, because `match_name` answers None
    for an unset pattern too and the RESULT is identical. What is not identical is the work done
    on the way there: without that line, every Stop on every machine that never opted in runs a
    `ps` to read a name nobody will look at. So the guard is real, it is about cost and about not
    inspecting the user's processes uninvited, and this counter is what makes it visible.

    The other assertions cover the outputs: `notify` is handed a session that WOULD qualify on
    every other condition, and the state dir is asserted to hold no mailbox at all."""
    idle, common = box["idlenotify"], box["common"]
    import procs                                                          # noqa: PLC0415
    calls = []
    monkeypatch.setattr(procs, "session_name", lambda pid: calls.append(pid) or "a-builder-ROW")
    common.record_handoff("s1", tmp_path / "HANDOFF-2026-09-22-20-1.md")
    assert idle.enabled() is False
    assert idle.notify("s1", str(tmp_path), None, opener="seat: a-seat") is None
    assert not (box["state"] / "notify").exists()
    assert calls == [], "the door read a process name for a session nobody asked it to manage"


def test_a_configured_pattern_turns_the_door_on(box):
    idle = box["set_config"](session_name_pattern=PATTERN)
    assert idle.enabled() is True
    assert idle.match_name("cursus-builder-ROW1") == {"seat": "cursus", "lane": "builder",
                                                      "qid": "ROW1"}
    assert idle.match_name("some-other-session") is None
    assert idle.match_name(None) is None


def test_a_pattern_that_does_not_compile_is_NAMED_not_swallowed(box):
    """A broken pattern and no pattern print the same silence everywhere else in this module.

    Positive: the problem line exists and names the key. Negative: a valid pattern produces no
    problem line, so the surface is not simply always complaining."""
    idle = box["set_config"](session_name_pattern="^(unbalanced")
    prob = idle.pattern_problem()
    assert prob and "session_name_pattern" in prob
    assert idle.match_name("anything") is None            # and it does not raise
    idle = box["set_config"](session_name_pattern=PATTERN)
    assert idle.pattern_problem() is None


def test_a_matching_pattern_with_no_named_groups_is_still_a_match(box):
    """`{}` is a MATCH. Truth-testing the result would make the plainest possible opt-in
    (`^audit-.*$`) indistinguishable from no match at all."""
    idle = box["set_config"](session_name_pattern=r"^audit-.*$")
    assert idle.match_name("audit-7") == {}
    assert idle.match_name("build-7") is None


# ------------------------------------------------------------------------------- who gets told


def test_the_opener_seat_line_outranks_the_name_group(box):
    idle = box["set_config"](session_name_pattern=PATTERN)
    groups = idle.match_name("cursus-builder-R1")
    assert idle.igniting_seat("cursus-builder-R1", groups, "line\nseat: other-seat [d49b15]\n") == "other-seat"
    assert idle.igniting_seat("cursus-builder-R1", groups, "no seat here") == "cursus"


def test_an_unnameable_seat_sends_to_NOBODY(box, tmp_path):
    """There is deliberately no fallback. A handoff delivered to the wrong reader is worse than one
    delivered to none, because the wrong reader may act on it."""
    idle = box["set_config"](session_name_pattern=r"^(?P<lane>audit)-(?P<qid>\d+)$")
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-x.md")
    assert idle.igniting_seat("audit-7", {"lane": "audit"}, None) is None
    assert idle.notify("s1", str(tmp_path), None, name="audit-7") is None
    assert not list((box["state"] / "notify").glob("*.jsonl")) if (box["state"] / "notify").exists() else True


def test_a_seat_name_can_never_escape_the_state_directory(box):
    """The seat arrives from a config pattern and a process command line; neither is this
    package's to trust with a path segment."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    p = idle.mailbox("../../etc/passwd")
    assert ".." not in p.parts
    assert p.parent == box["state"] / "notify"


# ----------------------------------------------------------------------------- when it fires


def test_named_session_plus_handoff_sends_exactly_one_message(box, tmp_path):
    """THE POSITIVE CONTROL of the row. One row in the mailbox, and it carries the three things a
    seat needs: which session, the handoff path, and what it last said."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    handoff = tmp_path / "HANDOFF-2026-09-22-20-1.md"
    handoff.write_text("x", encoding="utf-8")
    box["common"].record_handoff("s1", handoff)
    tp = transcript(tmp_path, [assistant("row 1 merged, moving to row 2")])
    sent = idle.notify("s1", str(tmp_path), str(tp), opener="seat: the-seat",
                       name="n-builder-R1")
    assert sent and "the-seat" not in sent               # the message names the SENDER, not the seat
    rows = idle.unread("the-seat")
    assert len(rows) == 1
    r = rows[0]
    assert r["reason"] == "handoff"
    assert r["handoff"] == str(handoff)
    assert r["last_line"] == "row 1 merged, moving to row 2"
    assert r["schema"] == idle.SCHEMA


def test_an_unnamed_session_sends_nothing(box, tmp_path, monkeypatch):
    """NEGATIVE control #1 from the row text. Everything else qualifies; only the name is missing.

    ★ The name is NOT passed as None here, and that is the point. `name=None` means "look it up",
    so a first version of this test handed `notify` a None and watched it find the name of the REAL
    session the suite was running inside — and report it as managed. The unnamed case in the world
    is a PROCESS with no `--name`, so that is what is arranged: the lookup itself answers None.

    The file-wide `no_ambient_session_name` stub now pins the same answer, so this test arranges
    its own and COUNTS it: the assertion that matters here is that the None came out of the
    LOOKUP PATH — `notify` consulted the reader and then sent nothing — and not out of some
    earlier guard that would print the same silence with the reader never called."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    import procs                                                          # noqa: PLC0415
    looked_up = []
    monkeypatch.setattr(procs, "session_name", lambda pid: looked_up.append(pid) or None)
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    assert idle.notify("s1", str(tmp_path), None, opener="seat: the-seat") is None
    assert looked_up, "the name was never looked up — this is not the unnamed-session path"
    assert idle.unread("the-seat") == []


def test_no_handoff_and_a_normal_end_sends_nothing(box, tmp_path):
    """NEGATIVE control #2 from the row text — the ordinary session, which is most of them."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    tp = transcript(tmp_path, [assistant("still working")])
    assert idle.notify("s1", str(tmp_path), str(tp), opener="seat: the-seat",
                       name="the-seat-builder-R1") is None
    assert idle.unread("the-seat") == []


def test_the_cap_is_the_other_trigger_and_a_warn_is_not(box, tmp_path, monkeypatch):
    """A session killed at its cap never writes a handoff — that is the failure being reported, so
    it cannot be the evidence. A session that merely WARNED and finished is the healthy path."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    import context_cap                                                    # noqa: PLC0415
    monkeypatch.setattr(context_cap, "_levels_fired", lambda sid: {"warn"})
    assert idle.at_cap("s1") is False
    assert idle.notify("s1", str(tmp_path), None, opener="seat: the-seat",
                       name="the-seat-builder-R1") is None
    monkeypatch.setattr(context_cap, "_levels_fired", lambda sid: {"warn", "cap"})
    assert idle.at_cap("s1") is True
    sent = idle.notify("s1", str(tmp_path), None, opener="seat: the-seat",
                       name="the-seat-builder-R1")
    assert sent and "context cap" in sent
    assert [r["reason"] for r in idle.unread("the-seat")] == ["cap"]


def test_it_sends_once_per_session_not_once_per_turn(box, tmp_path):
    """A Stop hook runs at the end of every TURN. Without the sent-mark the seat would be told the
    same thing again on every turn the session took after writing its handoff."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    args = ("s1", str(tmp_path), None)
    assert idle.notify(*args, opener="seat: the-seat", name="n-builder-R1") is not None
    assert idle.notify(*args, opener="seat: the-seat", name="n-builder-R1") is None
    assert idle.notify(*args, opener="seat: the-seat", name="n-builder-R1") is None
    assert len(idle.unread("the-seat")) == 1


def test_a_mailbox_that_cannot_be_written_is_RETRIED_not_marked_sent(box, tmp_path):
    """`deliver` returns whether the message was RECORDED. If the mark were written first, a state
    dir that was briefly unwritable would lose the message permanently and silently.

    The stub is swapped back BY HAND rather than with `monkeypatch.undo()`: that call undoes every
    patch on the test's monkeypatch object, the fixture's env seams included, so the retry half ran
    against the machine's real config and silently tested nothing."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    real_deliver = idle.deliver
    idle.deliver = lambda seat, row: False
    try:
        assert idle.notify("s1", str(tmp_path), None, opener="seat: the-seat",
                           name="n-builder-R1") is None
        assert idle.already_sent("s1") is False
    finally:
        idle.deliver = real_deliver
    assert idle.notify("s1", str(tmp_path), None, opener="seat: the-seat",
                       name="n-builder-R1") is not None
    assert idle.already_sent("s1") is True
    assert len(idle.unread("the-seat")) == 1


# --------------------------------------------------------------------------- the transport seam


def test_the_transport_runs_and_receives_the_row(box, tmp_path):
    idle = box["set_config"](session_name_pattern=PATTERN)
    sink = tmp_path / "sink.json"
    cmd = tmp_path / "transport.py"
    cmd.write_text("import sys, json, pathlib\n"
                   f"pathlib.Path({str(sink)!r}).write_text("
                   "json.dumps({'seat': sys.argv[1], 'row': json.loads(sys.stdin.read())}))\n",
                   encoding="utf-8")
    idle = box["set_config"](session_name_pattern=PATTERN, notify_command=str(cmd))
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    assert idle.notify("s1", str(tmp_path), None, opener="seat: the-seat",
                       name="n-builder-R1") is not None
    got = json.loads(sink.read_text())
    assert got["seat"] == "the-seat"
    assert got["row"]["reason"] == "handoff"


def test_a_transport_that_fails_loses_nothing(box, tmp_path):
    """The mailbox is written FIRST, so a transport that is missing, wedged or exploding costs a
    late delivery and never a lost one. A `deliver` that reported the transport's failure would
    invite a retry, and the retry would post the message twice."""
    cmd = tmp_path / "boom.py"
    cmd.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    idle = box["set_config"](session_name_pattern=PATTERN, notify_command=str(cmd))
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    assert idle.notify("s1", str(tmp_path), None, opener="seat: the-seat",
                       name="n-builder-R1") is not None
    assert len(idle.unread("the-seat")) == 1


# ------------------------------------------------------------------------ reading a transcript


def test_the_last_line_is_the_LAST_one_and_never_a_subagent(box, tmp_path):
    """A sidechain record is a SUBAGENT's output. Reporting one as what the session said would put
    a reviewer's sentence in a seat's brief under the builder's name."""
    idle = box["idlenotify"]
    # ★ THE SIDECHAIN RECORD IS LAST, and that is the whole test. With it in the middle — the
    # first way this fixture was written — a later main-chain line won either way, so deleting the
    # filter altogether left the test green: a check whose trigger condition its own fixture never
    # produced. Last is also the realistic shape, since a subagent typically finishes after its
    # parent's final sentence.
    tp = transcript(tmp_path, [assistant("first"),
                               assistant("blocked on a permission prompt"),
                               assistant("the reviewer speaking", sidechain=True)])
    assert idle.last_assistant_line(tp) == "blocked on a permission prompt"


def test_a_transcript_with_nothing_readable_yields_None_never_a_summary(box, tmp_path):
    idle = box["idlenotify"]
    assert idle.last_assistant_line(None) is None
    assert idle.last_assistant_line(tmp_path / "missing.jsonl") is None
    torn = tmp_path / "torn.jsonl"
    torn.write_text('{"type": "assistant", "mess\n', encoding="utf-8")
    assert idle.last_assistant_line(torn) is None


def test_a_message_says_so_when_there_is_no_last_line(box):
    """Positive and negative on the one branch a reader would otherwise mistake for a bug: a seat
    seeing no quoted line must be able to tell "it said nothing readable" from "the field was
    dropped"."""
    idle = box["idlenotify"]
    assert "nothing this session that could be read back" in idle.message("n", None, None, "cap")
    assert "Its last line: hello" in idle.message("n", None, "hello", "handoff")


# ------------------------------------------------------------------- the handoff record itself


def test_only_a_handoff_is_recorded_as_one(box, tmp_path):
    common = box["common"]
    common.record_handoff("s1", tmp_path / "HANDOFF-2026-09-22-20-1.md")
    common.record_handoff("s1", tmp_path / "handoff-lowercase.md")     # a real spelling in the wild
    common.record_handoff("s1", tmp_path / "notes.md")
    common.record_handoff("s1", tmp_path / "HANDOFF.txt")
    got = [Path(x).name for x in common.handoff_paths("s1")]
    assert got == ["HANDOFF-2026-09-22-20-1.md", "handoff-lowercase.md"]


def test_the_handoff_list_is_bounded_and_keeps_the_newest(box, tmp_path):
    common = box["common"]
    for i in range(common.HANDOFFS_MAX + 4):
        common.record_handoff("s1", tmp_path / f"HANDOFF-{i:02d}.md")
    got = common.handoff_paths("s1")
    assert len(got) == common.HANDOFFS_MAX
    assert Path(got[-1]).name == f"HANDOFF-{common.HANDOFFS_MAX + 3:02d}.md"


def test_both_write_doors_record_a_handoff(box, tmp_path, monkeypatch):
    """★ The Edit/Write door alone would miss most of them: five writes in six in this population
    are a heredoc or a redirect (measured by LESSONPUSH-2), and this fleet writes its handoffs with
    a heredoc. A test of one door would have been green and the feature mostly blind."""
    import importlib
    import chore                                                          # noqa: PLC0415
    importlib.reload(chore)
    common = box["common"]

    written = tmp_path / "HANDOFF-edit.md"
    written.write_text("x", encoding="utf-8")
    chore.do_write({"session_id": "s1", "cwd": str(tmp_path),
                    "tool_input": {"file_path": str(written), "content": "x"}})
    assert [Path(p).name for p in common.handoff_paths("s1")] == ["HANDOFF-edit.md"]

    chore.do_bash({"session_id": "s1", "cwd": str(tmp_path),
                   "tool_input": {"command": "cat > HANDOFF-bash.md <<'EOF'\nbody\nEOF"}})
    assert [Path(p).name for p in common.handoff_paths("s1")] == ["HANDOFF-edit.md",
                                                                 "HANDOFF-bash.md"]


# ------------------------------------------------------------------------- the receiving end


def test_a_seat_is_told_at_boot_that_it_has_mail(box, tmp_path):
    idle = box["set_config"](session_name_pattern=PATTERN)
    assert idle.facts_line("the-seat") is None            # negative: an empty mailbox says nothing
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    idle.notify("s1", str(tmp_path), None, opener="seat: the-seat", name="the-seat-builder-R1")
    line = idle.facts_line("the-seat")
    assert line and "the-seat-builder-R1" in line and "handoff" in line


def test_the_boot_line_is_silent_when_the_door_is_off(box, tmp_path):
    """The mailbox may be full from an earlier, configured run; turning the pattern off must turn
    the whole module off, reading included."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    idle.notify("s1", str(tmp_path), None, opener="seat: the-seat", name="the-seat-builder-R1")
    assert idle.facts_line("the-seat") is not None
    idle = box["set_config"]()
    assert idle.facts_line("the-seat") is None


def test_unread_since_a_timestamp(box, tmp_path):
    idle = box["set_config"](session_name_pattern=PATTERN)
    for i in (1, 2):
        box["common"].record_handoff(f"s{i}", tmp_path / f"HANDOFF-{i}.md")
        idle.notify(f"s{i}", str(tmp_path), None, opener="seat: the-seat",
                    name=f"x{i}-builder-R{i}")
    rows = idle.unread("the-seat")
    assert len(rows) == 2
    assert idle.unread("the-seat", since=rows[-1]["ts"]) == []


# ------------------------------------------------------------- the name, read off the process


def test_the_name_is_read_off_the_process_in_both_spellings():
    """A real child process, because the thing under test is what `ps` prints for a real command
    line. The two spellings are both in use and a reader of one is blind to the other."""
    import procs                                                          # noqa: PLC0415
    for argv, want in (("--name", "sp-a"), ("--name=sp-b", None)):
        if want is None:
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", argv],
                                 stdout=subprocess.DEVNULL)
            expect = "sp-b"
        else:
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", argv, want],
                                 stdout=subprocess.DEVNULL)
            expect = want
        try:
            assert procs.session_name(p.pid) == expect
        finally:
            p.terminate()
            p.wait(timeout=10)


def test_a_process_with_no_name_flag_has_no_name():
    """NEGATIVE control: the ordinary interactive session, which is the common case and must cost
    nothing. A reader that fell back to a guess here would make every unnamed session managed."""
    import procs                                                          # noqa: PLC0415
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                         stdout=subprocess.DEVNULL)
    try:
        assert procs.session_name(p.pid) is None
    finally:
        p.terminate()
        p.wait(timeout=10)
    assert procs.session_name(None) is None
    assert procs.session_name(-1) is None


# ------------------------------------- the five controls the reviewer's GREEN mutations named


def test_the_door_does_not_even_READ_the_opener_when_it_is_off(box, tmp_path):
    """★ MUST-FIX. The caller used to pass `opener=session_start.opener_text(inp)` — a transcript
    open and up to 200 JSON parses — and Python evaluates a keyword argument BEFORE the function it
    is passed to runs. So every Stop of every turn on every machine paid that read to reach a door
    that was off, and the module's own "off means nothing is read" was false as shipped. The
    callable is what makes it true, and this counter is what sees it."""
    idle = box["idlenotify"]
    calls = []
    def opener():
        calls.append(1)
        return "seat: the-seat"
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    assert idle.notify("s1", str(tmp_path), None, opener=opener, name="the-seat-builder-R1") is None
    assert calls == [], "the opener was read for a session nobody asked this door to manage"
    idle = box["set_config"](session_name_pattern=PATTERN)
    assert idle.notify("s1", str(tmp_path), None, opener=opener, name="the-seat-builder-R1")
    assert calls == [1], "the opener must be read exactly once, once the door is on"


def test_the_stop_hook_caller_does_not_read_the_opener_when_the_door_is_off(box, tmp_path,
                                                                           monkeypatch):
    """The control above drives `notify` directly, so it cannot see what the CALLER hands it:
    with `chore.do_idlenotify` put back to the eager `opener=session_start.opener_text(inp)`,
    that control stays green. This one goes through the Stop hook's own entry point and counts
    the transcript read there — it is red on the eager form and green on the callable."""
    import chore, procs, session_start                                    # noqa: PLC0415
    calls = []
    monkeypatch.setattr(session_start, "opener_text",
                        lambda inp: calls.append(1) or "seat: the-seat")
    monkeypatch.setattr(procs, "session_name", lambda pid: "the-seat-builder-R1")
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    inp = {"session_id": "s1", "cwd": str(tmp_path), "transcript_path": None}
    chore.do_idlenotify(inp)
    assert calls == [], "the Stop hook read the opener for a session nobody asked it to manage"
    box["set_config"](session_name_pattern=PATTERN)
    chore.do_idlenotify(inp)
    assert calls == [1], "once the door is on, the opener is read exactly once"


def test_an_opener_that_explodes_does_not_take_the_session_with_it(box, tmp_path):
    """A Stop hook must never raise. The opener is a transcript another process is appending to."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    def boom():
        raise OSError("transcript vanished")
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    sent = idle.notify("s1", str(tmp_path), None, opener=boom, name="the-seat-builder-R1")
    assert sent is not None                       # it falls back to the pattern's own seat group
    assert len(idle.unread("the-seat")) == 1


def test_a_seat_from_the_PATTERN_is_validated_too_not_only_one_from_the_opener(box):
    """The escape-the-state-directory control drives the OPENER path. A pattern whose `seat` group
    can capture a path separator reaches the same place by the other door, and nothing tested it —
    dropping `_SEAT_OK` from the groups branch left the suite green."""
    idle = box["set_config"](session_name_pattern=r"^(?P<seat>.+)-builder$")
    assert idle.igniting_seat("../../etc/passwd-builder",
                              {"seat": "../../etc/passwd"}, None) is None
    assert idle.igniting_seat("ok-seat-builder", {"seat": "ok-seat"}, None) == "ok-seat"


def test_the_boot_line_names_the_NEWEST_five_and_counts_the_rest(box, tmp_path):
    """No test produced more than two mailbox rows, so neither the newest-five selection nor the
    "and N earlier" arithmetic was verified: `rows[-5:]` → `rows[:5]` stayed green."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    for i in range(7):
        box["common"].record_handoff(f"s{i}", tmp_path / f"HANDOFF-{i}.md")
        idle.notify(f"s{i}", str(tmp_path), None, opener="seat: the-seat",
                    name=f"n{i}-builder-R{i}")
    line = idle.facts_line("the-seat")
    assert "and 2 earlier" in line
    for i in (2, 3, 4, 5, 6):
        assert f"n{i}-builder-R{i}" in line
    for i in (0, 1):
        assert f"n{i}-builder-R{i}" not in line


def test_a_transport_that_HANGS_or_cannot_run_still_leaves_the_message(box, tmp_path,
                                                                      monkeypatch):
    """Every transport control so far used a process that exited non-zero — which `subprocess.run`
    does not raise on, so the `except` clause around it was never reached and deleting it stayed
    green. These are the two failures that DO raise: a transport that runs past its timeout, and
    one that cannot be executed at all."""
    monkeypatch.setattr(box["idlenotify"], "DELIVER_TIMEOUT", 1)
    slow = tmp_path / "slow.py"
    slow.write_text("import time; time.sleep(30)\n", encoding="utf-8")
    idle = box["set_config"](session_name_pattern=PATTERN, notify_command=str(slow))
    monkeypatch.setattr(idle, "DELIVER_TIMEOUT", 1)
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    assert idle.notify("s1", str(tmp_path), None, opener="seat: the-seat",
                       name="n-builder-R1") is not None
    assert len(idle.unread("the-seat")) == 1

    monkeypatch.setattr(idle.config, "python", lambda: "/nonexistent/interpreter")
    box["common"].record_handoff("s2", tmp_path / "HANDOFF-b.md")
    assert idle.notify("s2", str(tmp_path), None, opener="seat: the-seat",
                       name="n-builder-R1") is not None
    assert len(idle.unread("the-seat")) == 2


def test_a_torn_mailbox_line_is_skipped_not_raised(box, tmp_path):
    """A mailbox is appended to by one process while another reads it. A half-written last line is
    an ordinary state, and `last_assistant_line` had this control while `unread` did not."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    idle.notify("s1", str(tmp_path), None, opener="seat: the-seat", name="n-builder-R1")
    box_file = idle.mailbox("the-seat")
    with box_file.open("a", encoding="utf-8") as fh:
        fh.write('{"schema": 1, "seat": "the-s\n')          # torn
        fh.write('["not", "a", "dict"]\n')                  # well-formed JSON, wrong shape
    rows = idle.unread("the-seat")
    assert len(rows) == 1 and rows[0]["from_name"] == "n-builder-R1"


# ----------------------------------------------------------- the launcher's trailing notify


def test_the_launcher_path_finds_the_session_by_NAME(box, tmp_path):
    """★ The hook is not the only exit. A session killed by its account limit, by a crash, or by a
    person closing the pane never runs its Stop hook — and those are exactly the exits a seat most
    needs to hear about. The launcher's trailing call finds the record the hooks already wrote, so
    the two paths cannot disagree about what happened."""
    idle = box["set_config"](session_name_pattern=PATTERN)
    box["common"].update_session_state("s1", lambda d: d.update(
        {"session_id": "s1", "name": "the-seat-builder-R1", "cwd": str(tmp_path),
         "ts": "2026-09-22T10:00:00"}))
    box["common"].record_handoff("s1", tmp_path / "HANDOFF-a.md")
    assert idle.record_for_name("the-seat-builder-R1")["session_id"] == "s1"
    assert idle.record_for_name("nobody") is None
    assert idle.notify("s1", str(tmp_path), None, name="the-seat-builder-R1") is not None
    assert len(idle.unread("the-seat")) == 1
