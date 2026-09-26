"""context_cap.py — one PostToolUse line when a session is running out of room to finish cleanly.

Every rule gets a POSITIVE control (the notice fires) and a NEGATIVE control (it stays silent). A
notice that only ever fires proves nothing about what it lets through, and this one's failure mode
is the quiet direction: a session that is never told keeps going until it dies mid-handoff.

The four counting traps each get a test of their own, because each of them, alone, silently turns
the figure into a different quantity:
  * a streaming message is written to the transcript many times under ONE `message.id`;
  * a `isSidechain` record is a SUBAGENT's context, not this session's;
  * a `<synthetic>` record is the harness reporting an error, with an all-zero usage block;
  * `input_tokens` alone is a tiny number — the context is it plus both cache figures.

State and limits are redirected into tmp_path; the suite never reads the real vault, the real
~/.claude/gedaechtnis, or the machine's real transcripts.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def rec(mid, *, inp=2, read=0, create=0, out=10, side=False, model="claude-opus-5", rtype="assistant"):
    return {"type": rtype, "isSidechain": side, "timestamp": "2026-09-19T20:00:00Z",
            "message": {"id": mid, "model": model, "role": "assistant",
                        "usage": {"input_tokens": inp, "cache_read_input_tokens": read,
                                  "cache_creation_input_tokens": create, "output_tokens": out}}}


def transcript(path: Path, records) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def world(tmp_path):
    state = tmp_path / "state"
    limits_file = tmp_path / "limits.json"
    # The shipped file plus the two thresholds this module reads, so a test can reach a ceiling
    # without needing a 700,000-token fixture.
    shipped = json.loads((Path(__file__).resolve().parents[1] / "rules" / "limits.json").read_text())
    shipped["context_hard_cap_tokens"] = 500
    shipped["context_warn_over_floor_tokens"] = 300
    limits_file.write_text(json.dumps(shipped), encoding="utf-8")
    env = dict(os.environ,
               GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_LIMITS=str(limits_file),
               # LIMITSEAM-1's lesson: clearing GEDAECHTNIS_* by prefix stopped being isolation once
               # limits read config.json — the seam has to be REDIRECTED, not merely unset.
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"),
               GEDAECHTNIS_VAULT=str(tmp_path / "Atlas"),
               CLAUDE_PROJECTS_DIR=str(tmp_path / "projects"))
    return dict(tmp=tmp_path, state=state, env=env, limits=limits_file)


def run(world, payload):
    p = subprocess.run([sys.executable, str(HOOKS / "context_cap.py")],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=world["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out) if out else None


def text_of(res):
    return ((res or {}).get("hookSpecificOutput") or {}).get("additionalContext") or ""


def payload(sid, tpath, **kw):
    d = {"session_id": sid, "hook_event_name": "PostToolUse", "tool_name": "Bash",
         "tool_input": {"command": "ls"}}
    if tpath is not None:
        d["transcript_path"] = str(tpath)
    d.update(kw)
    return d


# ------------------------------------------------------------------ the two levels ----

def test_hard_cap_fires_and_names_the_number(world):
    t = transcript(world["tmp"] / "t.jsonl",
                   [rec("m1", read=100), rec("m2", read=600)])       # 102 -> 602, cap 500
    res = run(world, payload("s1", t))
    body = text_of(res)
    assert "HARD CAP" in body and "602" in body, body
    assert "handoff" in body.lower()
    assert (res or {}).get("hookSpecificOutput", {}).get("hookEventName") == "PostToolUse"


def test_warn_fires_on_work_done_since_the_floor(world):
    t = transcript(world["tmp"] / "t.jsonl",
                   [rec("m1", read=100), rec("m2", read=410)])       # floor 102, now 412: +310
    body = text_of(run(world, payload("s2", t)))
    assert "CONTEXT" in body and "HARD CAP" not in body, body
    assert "310" in body and "102" in body, body                     # work done AND the floor


def test_silent_below_both_thresholds(world):
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=150)])
    assert run(world, payload("s3", t)) is None


def test_each_level_fires_once_per_session(world):
    """A notice repeated on every tool call is noise, and noise is how a warning gets scrolled past."""
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    assert text_of(run(world, payload("s4", t))) != ""
    assert run(world, payload("s4", t)) is None                      # same session, second call
    assert text_of(run(world, payload("s5", t))) != ""               # a DIFFERENT session still hears it


def test_warn_does_not_fire_after_the_cap_already_did(world):
    """Past the cap the warn line is strictly worse advice — it says 'finish the row'."""
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    assert "HARD CAP" in text_of(run(world, payload("s6", t)))
    assert run(world, payload("s6", t)) is None


# ------------------------------------------------------------------ the counting traps ----

def test_streaming_records_under_one_id_collapse_to_the_LAST_one(world):
    """One assistant message is written many times as it streams, and the EARLY records can carry a
    partial usage block. The floor must be the message's finished figure, not its first partial
    write — so the first message here streams twice, 52 then 102. Keying per RECORD instead of per
    `message.id` takes the 52, and every work-done figure after it is 50 tokens too large."""
    t = transcript(world["tmp"] / "t.jsonl",
                   [rec("m1", read=50, out=1), rec("m1", read=100, out=99), rec("m2", read=410)])
    body = text_of(run(world, payload("s7", t)))
    assert "310" in body and "102" in body, body                     # not 360 from a floor of 52

def test_sidechain_records_are_a_subagents_context_not_this_sessions(world):
    """A subagent's turns live in the parent's transcript. A deep subagent must not fire the
    parent's cap: the parent's own context is what it can and cannot finish with."""
    # The subagent's record is the LAST one in the file — which is what a parent sees while a
    # delegate is mid-run. If it counted, the parent would be told to hand over on a context that
    # is not its own.
    t = transcript(world["tmp"] / "t.jsonl",
                   [rec("m1", read=100), rec("m2", read=150), rec("sub", read=9000, side=True)])
    assert run(world, payload("s8", t)) is None


def test_synthetic_error_records_are_not_calls(world):
    """A `<synthetic>` record carries an all-zero usage block. Counting it as the first call makes
    the boot floor 0, and a floor of 0 turns the warn threshold into an absolute one silently."""
    t = transcript(world["tmp"] / "t.jsonl",
                   [rec("err", inp=0, read=0, create=0, out=0, model="<synthetic>"),
                    rec("m1", read=100), rec("m2", read=410)])
    body = text_of(run(world, payload("s9", t)))
    assert "102" in body, body                                       # the floor is m1, not the error


def test_context_is_all_three_usage_figures(world):
    """`input_tokens` alone is 2 in a 93,000-token session. A reader that used it would never fire."""
    t = transcript(world["tmp"] / "t.jsonl",
                   [rec("m1", inp=1, read=50, create=51), rec("m2", inp=1, read=300, create=301)])
    body = text_of(run(world, payload("s10", t)))
    assert "602" in body, body


# ------------------------------------------------------------------ inputs and failure ----

def test_transcript_is_found_by_session_id_when_the_payload_omits_the_path(world):
    """The whole input is a field of someone else's payload. If it stopped arriving, the notice
    would switch off with no symptom — so the fallback is real and is exercised."""
    proj = world["tmp"] / "projects" / "-Users-x-repo"
    proj.mkdir(parents=True)
    transcript(proj / "s11.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    assert "HARD CAP" in text_of(run(world, payload("s11", None)))


def test_no_notice_and_no_crash_on_unusable_input(world):
    """A cost notice must never cost a turn."""
    missing = world["tmp"] / "nope.jsonl"
    assert run(world, payload("s12", missing)) is None               # path does not exist
    empty = transcript(world["tmp"] / "empty.jsonl", [])
    assert run(world, payload("s13", empty)) is None                 # no records at all
    junk = world["tmp"] / "junk.jsonl"
    junk.write_text("not json\n{\n", encoding="utf-8")
    assert run(world, payload("s14", junk)) is None                  # malformed lines
    no_usage = transcript(world["tmp"] / "nousage.jsonl",
                          [{"type": "assistant", "message": {"id": "m1", "role": "assistant"}}])
    assert run(world, payload("s15", no_usage)) is None              # records, no usage figures


def test_a_usage_less_record_is_not_a_call_that_hides_the_last_real_one(world):
    """`_usage_context` returns None, never 0, for a record carrying no usage figures. If it
    returned 0 that record would become the session's most recent 'call' with a context of zero,
    and the notice would go quiet at precisely the moment it is needed — the quiet failure this
    module is most exposed to. Here the last record has no usage and the one before is over cap."""
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    with t.open("a", encoding="utf-8") as fh:
        # A usage block that is PRESENT but carries none of the three context figures — which is
        # what the `any()` guard is about. A record with no `usage` key at all is turned away one
        # line earlier by the isinstance check, so it cannot exercise this rule.
        fh.write(json.dumps({"type": "assistant",
                             "message": {"id": "m3", "model": "claude-opus-5", "role": "assistant",
                                         "usage": {"output_tokens": 5}}}) + "\n")
    assert "HARD CAP" in text_of(run(world, payload("s19", t)))


def test_it_never_denies_anything(world):
    """Not a gate, at either level: PostToolUse output here carries no permission decision."""
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    res = run(world, payload("s16", t))
    assert "permissionDecision" not in json.dumps(res)


def test_thresholds_come_from_limits_not_from_the_call_site(world):
    """The number lives in rules/limits.json. A constant inlined here would drift from it silently."""
    shipped = json.loads(world["limits"].read_text())
    shipped["context_hard_cap_tokens"] = 100000
    shipped["context_warn_over_floor_tokens"] = 100000
    world["limits"].write_text(json.dumps(shipped), encoding="utf-8")
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    assert run(world, payload("s17", t)) is None                     # 602 is nowhere near 100,000


def test_warn_is_relative_to_the_floor_not_absolute(world):
    """The whole point of the floor-relative warn: a session in a heavy-boot repo has not done more
    WORK for having booted heavier. An absolute threshold would warn it before it had done any.
    Floor 450, current 460 — ten tokens of work, well over the 300 threshold in absolute terms."""
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=448), rec("m2", read=458)])
    assert run(world, payload("s18", t)) is None


# ------------------------------------------------------------------ the broken-state-dir trade-off ----

def _break_state_dir(world):
    """Make the state directory unwritable by putting a plain FILE where the dir must be."""
    state = world["state"]
    if state.exists():
        import shutil
        shutil.rmtree(state)
    state.write_text("not a directory", encoding="utf-8")


def test_the_notice_still_fires_when_its_own_state_cannot_be_written(world):
    """The warning survives a broken installation. Going quiet here would disable the notice
    entirely and silently, and the failure it exists to prevent is a session that never hears it."""
    _break_state_dir(world)
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    assert "HARD CAP" in text_of(run(world, payload("s20", t)))


def test_an_unrecordable_fire_repeats_and_is_named_in_the_log(world):
    """The COST of that choice, pinned so nobody meets it by surprise: with no state to remember
    the fire, the notice repeats. It is logged as `fire-unrecorded` so the repetition has a cause a
    reader can find, rather than looking like a bug in the once-per-level rule."""
    _break_state_dir(world)
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    first = text_of(run(world, payload("s21", t)))
    assert "HARD CAP" in first
    assert "could not be recorded" in first and "repeat" in first, first
    assert "HARD CAP" in text_of(run(world, payload("s21", t)))      # and again — this is the cost


def test_a_writable_state_dir_still_fires_exactly_once(world):
    """The NEGATIVE control for the pair above: the repetition must be caused by the broken state
    dir and by nothing else, or the two tests above would pass over a broken once-per-level rule."""
    t = transcript(world["tmp"] / "t.jsonl", [rec("m1", read=100), rec("m2", read=600)])
    body = text_of(run(world, payload("s22", t)))
    assert "HARD CAP" in body
    assert "could not be recorded" not in body, "the admission must not ride on a healthy install"
    assert run(world, payload("s22", t)) is None


def test_a_breached_head_bound_switches_WARN_off_and_says_so_in_the_log(world):
    """The one silent direction left in the module. `boot_floor` reads a BOUNDED head; if the first
    assistant record ever sat beyond that bound, the floor would be unavailable and the WARN level
    would simply stop firing, with the CAP level unaffected and nothing else different. Measured
    2026-09-19, the deepest real first-record offset is 546,526 bytes against a 2 MB bound — about
    4x of margin — but a margin nobody asserts is a margin nobody notices closing. Here the bound is
    lowered until it actually bites."""
    world["env"] = dict(world["env"], GEDAECHTNIS_CONTEXT_HEAD_BYTES="400")
    filler = {"type": "user", "message": {"role": "user", "content": "x" * 900}}
    t = transcript(world["tmp"] / "deephead.jsonl", [filler, rec("m1", read=100), rec("m2", read=410)])
    assert run(world, payload("s23", t)) is None                     # WARN cannot fire: no floor
    assert "floor-not-found" in (world["state"] / "context_cap.log").read_text()


def test_the_cap_level_survives_a_breached_head_bound(world):
    """NEGATIVE control for the test above: CAP reads the TAIL and must be unaffected, or the bound
    would be taking down both levels and the test above would be describing the wrong failure."""
    world["env"] = dict(world["env"], GEDAECHTNIS_CONTEXT_HEAD_BYTES="400")
    filler = {"type": "user", "message": {"role": "user", "content": "x" * 900}}
    t = transcript(world["tmp"] / "deephead2.jsonl", [filler, rec("m1", read=100), rec("m2", read=600)])
    assert "HARD CAP" in text_of(run(world, payload("s24", t)))
