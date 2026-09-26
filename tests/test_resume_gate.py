"""RESUMEGATE-1 — a message that would wake an idle session at a cost a fresh start avoids is refused.

Every bound has a POSITIVE control (refused) and the legitimate form has a NEGATIVE control
(passes); the same-window limit-stopped session is the negative control's real case. The session
registry, the projects directory, the state directory and the config are all redirected into
tmp_path: this suite never reads a real transcript and never writes the real ~/.claude/gedaechtnis.

The registry record shape is the MEASURED one (`~/.claude/sessions/<pid>.json`: pid, sessionId,
name, status). A registry pid must be alive for the door to treat it as a session, so the records
here carry the test process's own pid.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
ME = "11111111-2222-3333-4444-555555555555"
TARGET = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def world(tmp_path):
    state, projects, sessions = tmp_path / "state", tmp_path / "projects", tmp_path / "sessions"
    (projects / "-proj").mkdir(parents=True)
    sessions.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    # The sending session runs in a MARKED repo (its cwd is `projects`): the wake refusal applies
    # only there (PLUGDIR-1); the unmarked case is its own negative control below.
    (projects / ".atlas-lane").write_text("lane: TEST\npath: Test/\n")
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(state), GEDAECHTNIS_CONFIG=str(cfg),
               GEDAECHTNIS_VAULT=str(tmp_path / "Vault"), CLAUDE_PROJECTS_DIR=str(projects),
               CLAUDE_SESSIONS_DIR=str(sessions))
    env.pop("GEDAECHTNIS_LIMITS", None)
    return dict(state=state, projects=projects, sessions=sessions, cfg=cfg, env=env)


def _ts(age_s):
    return datetime.fromtimestamp(time.time() - age_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def session(w, name="builder-7", status="idle", total=None, age_s=None, sid=TARGET, ttl="1h",
            model="claude-opus-5-5", older=None):
    """Register a live session called `name`; give it a transcript when `total` is set.

    `ttl` is what the last usage record says it wrote: "1h", "5m", or None (no cache_creation).
    `older` = (age_s, total) prepends an earlier assistant record, so "the LAST record" is tested
    against a transcript where first and last differ."""
    (w["sessions"] / f"{sid[:6]}.json").write_text(json.dumps(
        {"pid": os.getpid(), "sessionId": sid, "name": name, "status": status, "kind": "interactive"}))
    if total is None:
        return

    def rec(mid, tot, age, cc):
        usage = {"input_tokens": 2, "cache_read_input_tokens": tot - 1002,
                 "cache_creation_input_tokens": 1000}
        if cc is not None:
            usage["cache_creation"] = cc
        return {"type": "assistant", "timestamp": _ts(age),
                "message": {"id": mid, "model": model, "usage": usage}}

    cc = {"1h": {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 0},
          "5m": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 1000},
          None: None}[ttl]
    recs = [{"type": "user", "timestamp": _ts(age_s), "message": {"content": "hi"}}]
    if older:
        recs.insert(0, rec("msg_0", older[1], older[0], cc))
    recs.append(rec("msg_1", total, age_s, cc))
    (w["projects"] / "-proj" / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def opener(w, text, sid=ME, meta_first=True):
    """Give the SENDER a transcript whose first prompt is `text` (after a meta record, as a real
    one has, so "the first user record" is tested against a transcript where it is not the first)."""
    recs = []
    if meta_first:
        recs.append({"type": "user", "isMeta": True, "message": {"content": "<caveat>"}})
    recs.append({"type": "user", "message": {"content": text}})
    (w["projects"] / "-proj" / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")


SPECIMEN_OPENER = ("You are the TWENTY-FIFTH builder of the COMPLETE-1 line, Opus 5.5 medium, ignited "
                   "2026-09-23 ~11:00 by the seat cursus-atlas [d49b15] on the owner's word. Read first.")


AGENT = "a52d8cf965a32a9a2"


def subagent(w, total=None, age_s=60, model="claude-sonnet-5", ttl="5m", parent=ME, agent=AGENT,
             main_chain_noise=False):
    """Write a sub-agent transcript where the harness writes one: every record `isSidechain`.
    `main_chain_noise` adds a non-sidechain record that must NOT be read as the sub-agent's."""
    d = w["projects"] / "-proj" / parent / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    cc = {"1h": {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 0},
          "5m": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 1000}}[ttl]
    recs = [{"type": "user", "isSidechain": True, "agentId": agent, "timestamp": _ts(age_s),
             "message": {"content": "brief"}}]
    if total is not None:
        recs.append({"type": "assistant", "isSidechain": True, "agentId": agent,
                     "timestamp": _ts(age_s), "message": {"id": "msg_a", "model": model, "usage": {
                         "input_tokens": 2, "cache_read_input_tokens": total - 1002,
                         "cache_creation_input_tokens": 1000, "cache_creation": cc}}})
    if main_chain_noise:
        recs.append({"type": "assistant", "timestamp": _ts(age_s), "message": {
            "id": "msg_m", "model": "claude-opus-5-5", "usage": {
                "input_tokens": 2, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 0}}})
    (d / f"agent-{agent}.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def send(w, to="builder-7", message="continue"):
    payload = {"session_id": ME, "tool_name": "SendMessage", "cwd": str(w["projects"]),
               "tool_input": {"to": to, "message": message}}
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "gate.py"), "agent"],
                       input=json.dumps(payload), capture_output=True, text=True, env=w["env"],
                       timeout=30)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)["hookSpecificOutput"] if p.stdout.strip() else {}
    return out


def denied(out) -> bool:
    return out.get("permissionDecision") == "deny"


def note_of(out) -> str:
    assert "permissionDecision" not in out, out
    return out["additionalContext"]


# ------------------------------------------------ positive controls, one per refusal reason ----

def test_COLD_AND_LARGE_is_refused_and_told_to_start_fresh(world):
    session(world, total=300_000, age_s=10 * 3600)
    out = send(world)
    assert denied(out), out
    why = out["permissionDecisionReason"]
    assert "cold and its context is at or above resume_cold_cap 200,000" in why, why
    assert "context 300,000 tokens" in why and "600 min old" in why, why
    assert "FRESH from its handoff" in why and "RESUME-OVERRIDE:" in why, why


def test_exactly_at_the_cold_cap_is_refused(world):
    """`>=`, not `>`: the bound is the first refused value."""
    session(world, total=200_000, age_s=10 * 3600)
    assert denied(send(world))


def test_HEADROOM_under_the_minimum_is_refused_even_warm(world):
    session(world, total=355_000, age_s=60)
    out = send(world)
    assert denied(out), out
    assert "65,000 tokens of headroom, under resume_min_headroom 70,000" in out["permissionDecisionReason"]


def test_exactly_at_the_headroom_bound_is_refused(world):
    session(world, total=350_000, age_s=60)
    assert denied(send(world))


def test_a_COLD_FABLE_session_is_refused_at_any_size(world):
    """RESUMEGATE-2: cold small Fable, not the sender's igniter — the Fable rule is the only reason."""
    session(world, total=60_000, age_s=2 * 3600, model="claude-fable-5-1")
    out = send(world)
    assert denied(out), out
    why = out["permissionDecisionReason"]
    assert "runs claude-fable-5-1 and is cold" in why and "cold and its context" not in why, why


def test_a_cold_large_FABLE_session_is_refused(world):
    """The row's positive control: cold Fable at 300k."""
    session(world, total=300_000, age_s=10 * 3600, model="claude-fable-5-1")
    why = send(world)["permissionDecisionReason"]
    assert "runs claude-fable-5-1 and is cold" in why and "resume_cold_cap" in why, why


def test_a_5_MINUTE_cache_is_cold_after_20_minutes(world):
    session(world, total=300_000, age_s=20 * 60, ttl="5m")
    out = send(world)
    assert denied(out), out
    assert "cache lifetime of 300 s (its own cache)" in out["permissionDecisionReason"]


def test_the_bounds_come_from_limits(world):
    world["cfg"].write_text(json.dumps({"limits": {
        "resume_cold_cap": 100_000, "resume_window": 300_000, "resume_min_headroom": 10_000}}))
    session(world, total=120_000, age_s=10 * 3600)
    out = send(world)
    assert denied(out) and "resume_cold_cap 100,000" in out["permissionDecisionReason"], out
    session(world, total=295_000, age_s=60)
    assert "under resume_min_headroom 10,000" in send(world)["permissionDecisionReason"]


def test_the_fallback_lifetime_comes_from_limits_when_the_transcript_does_not_say(world):
    world["cfg"].write_text(json.dumps({"limits": {"resume_cold_s": 7200}}))
    session(world, total=300_000, age_s=90 * 60, ttl=None)
    note = note_of(send(world))
    assert "cache lifetime of 7,200 s (resume_cold_s, no cache record)" in note, note


def test_a_refusal_is_logged_with_its_numbers_and_price(world):
    session(world, total=300_000, age_s=10 * 3600)
    send(world)
    log = (world["state"] / "deny.log").read_text()
    assert "\tresume\t" in log and "total=300000" in log and f"target={TARGET}" in log, log
    assert "writes it again for ~$1.50" in log, log


# ------------------------------------------------ negative controls ----

def test_a_1_HOUR_cache_is_warm_after_20_minutes(world):
    session(world, total=300_000, age_s=20 * 60, ttl="1h")
    note = note_of(send(world))
    assert "— warm; context 300,000 tokens, 120,000 of headroom" in note, note
    assert "re-reads it for ~$0.06 (claude-opus-5-5, cache read $0.2/M)" in note, note


def test_a_large_WARM_session_with_headroom_is_woken(world):
    """Owner 2026-09-23: 300-350k is fine while the cache is warm."""
    session(world, total=340_000, age_s=300)
    assert "warm" in note_of(send(world))


def test_a_small_COLD_session_is_woken_with_its_price(world):
    session(world, total=120_000, age_s=10 * 3600)
    note = note_of(send(world))
    assert "cold" in note and "writes it again for ~$0.60" in note and "assumed" in note, note


def test_a_small_warm_non_fable_session_is_woken(world):
    """The Fable refusal's negative: the same session on Opus passes."""
    session(world, total=60_000, age_s=60, model="claude-opus-5")
    assert "warm" in note_of(send(world))


def test_a_limit_stopped_session_inside_its_cache_lifetime_still_gets_its_continue(world):
    """The real negative case: stopped by the usage limit 40 minutes ago, at 300k, 1-hour cache."""
    session(world, total=300_000, age_s=40 * 60)
    assert "warm" in note_of(send(world, message="continue"))


def test_age_is_read_from_the_LAST_record_not_the_first(world):
    session(world, total=300_000, age_s=60, older=(10 * 3600, 250_000))
    assert "warm" in note_of(send(world))


def test_an_unknown_model_says_its_price_is_unknown(world):
    session(world, total=120_000, age_s=60, model="claude-future-9")
    assert "wake price unknown (no rate for model claude-future-9)" in note_of(send(world))


def test_a_busy_session_is_not_woken_so_nothing_is_refused(world):
    session(world, status="busy", total=450_000, age_s=10 * 3600)
    assert send(world) == {}


def test_a_target_that_is_not_a_session_here_passes_silently(world):
    """Not a named session and not a sub-agent id. (A sub-agent id passed silently here until
    SUBAGENTGATE-1; it is now measured — see the sub-agent section.)"""
    session(world, total=450_000, age_s=10 * 3600)
    assert send(world, to="main") == {}
    assert send(world, to="some-teammate") == {}


def test_the_name_with_its_ref_resolves_to_the_same_session(world):
    session(world, total=300_000, age_s=10 * 3600)
    assert denied(send(world, to=f"builder-7 [{TARGET[:6]}]"))


def test_an_unmeasurable_idle_session_is_allowed_WITH_a_note(world):
    session(world, total=None)
    assert "could NOT measure" in note_of(send(world))


def test_a_transcript_with_no_usage_record_is_unmeasured_too(world):
    """Transcript present, age readable, context not: still the note, never a crash or a 0."""
    session(world, total=None)
    (world["projects"] / "-proj" / f"{TARGET}.jsonl").write_text(
        json.dumps({"type": "user", "timestamp": _ts(60), "message": {"content": "hi"}}) + "\n")
    assert "could NOT measure" in note_of(send(world))


# ---------------------------------------------------------------- override ----

def test_the_override_line_sends_and_is_logged_with_its_reason(world):
    session(world, total=300_000, age_s=10 * 3600)
    out = send(world, message="RESUME-OVERRIDE: owner asked for this one\ncontinue")
    assert "permissionDecision" not in out, out
    log = (world["state"] / "deny.log").read_text()
    assert "resume-override" in log and "reason=owner asked for this one" in log, log


def test_an_override_without_a_reason_does_not_count(world):
    session(world, total=300_000, age_s=10 * 3600)
    assert denied(send(world, message="RESUME-OVERRIDE:\ncontinue"))


def test_the_override_must_be_the_first_line(world):
    session(world, total=300_000, age_s=10 * 3600)
    assert denied(send(world, message="continue\nRESUME-OVERRIDE: late"))


# ---------------------------------------------------------------- wiring ----

def test_hooks_json_routes_SendMessage_through_the_existing_entry_with_a_timeout():
    pre = json.loads((HOOKS / "hooks.json").read_text())["hooks"]["PreToolUse"]
    hits = [m for m in pre if "SendMessage" in (m.get("matcher") or "").split("|")]
    assert len(hits) == 1, hits
    assert all(h.get("timeout") for h in hits[0]["hooks"])
    assert hits[0]["hooks"][0]["command"].replace("\"", "").endswith("gate.py agent")


# ---------------------------------------------------------------- tail reading ----

def test_a_subagent_or_synthetic_record_last_in_the_tail_is_not_the_sessions_own(world):
    """The last lines of a live transcript are often a sub-agent's (isSidechain) or the harness's
    `<synthetic>` error record; neither says what model or cache the SESSION runs."""
    session(world, total=300_000, age_s=20 * 60, ttl="1h", model="claude-opus-5-5")
    p = world["projects"] / "-proj" / f"{TARGET}.jsonl"
    side = {"type": "assistant", "isSidechain": True, "timestamp": _ts(20 * 60),
            "message": {"id": "msg_s", "model": "claude-fable-5-1", "usage": {
                "input_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 9,
                "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 9}}}}
    synth = {"type": "assistant", "timestamp": _ts(20 * 60),
             "message": {"id": "msg_x", "model": "<synthetic>", "usage": {
                 "input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                 "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 5}}}}
    p.write_text(p.read_text() + json.dumps(side) + "\n" + json.dumps(synth) + "\n")
    note = note_of(send(world))
    assert "warm" in note and "claude-opus-5-5" in note, note


def test_a_record_that_wrote_BOTH_cache_lifetimes_counts_as_one_hour(world):
    session(world, total=300_000, age_s=20 * 60, ttl="1h")
    p = world["projects"] / "-proj" / f"{TARGET}.jsonl"
    p.write_text(p.read_text().replace('"ephemeral_5m_input_tokens": 0', '"ephemeral_5m_input_tokens": 500'))
    assert "cache lifetime of 3,600 s" in note_of(send(world))


# ------------------------------------------------ RESUMEGATE-2: Fable refused only when cold ----

def test_the_BUILDER_25_SPECIMEN_a_warm_fable_seat_that_ignited_the_sender_passes(world):
    """Replay: the seat warm (0 min against its 1 h cache), 301,782 tokens, 118k of headroom."""
    session(world, name="cursus-atlas", total=301_782, age_s=0, model="claude-fable-5-1")
    opener(world, SPECIMEN_OPENER)
    note = note_of(send(world, to="cursus-atlas [d49b15]"))
    assert "warm; context 301,782 tokens, 118,218 of headroom" in note, note
    assert "re-reads it for ~$0.08 (claude-fable-5-1" in note, note


def test_a_warm_fable_session_passes_even_when_it_did_not_ignite_the_sender(world):
    """Warm is enough: the row refuses Fable only when COLD or IDLE."""
    session(world, total=60_000, age_s=60, model="claude-fable-5-1")
    opener(world, "You are a worker. Drain the queue.")
    assert "warm" in note_of(send(world))


def test_a_COLD_fable_IGNITER_passes_with_its_price(world):
    """The igniter check's own positive: cold, small, Fable — and it launched the sender."""
    session(world, name="cursus-atlas", total=60_000, age_s=2 * 3600, model="claude-fable-5-1")
    opener(world, SPECIMEN_OPENER)
    note = note_of(send(world, to="cursus-atlas"))
    assert "It ignited this session" in note and "writes it again for ~$0.75" in note, note
    assert "\tigniter" in (world["state"] / "resume_gate.log").read_text()


def test_a_cold_fable_target_named_WITHOUT_ignite_is_still_refused(world):
    """Naming the seat is not enough; the sentence must say it ignited the sender."""
    session(world, name="cursus-atlas", total=60_000, age_s=2 * 3600, model="claude-fable-5-1")
    opener(world, "Report to cursus-atlas when done. You were started this morning.")
    assert denied(send(world, to="cursus-atlas"))


def test_a_name_that_only_CONTAINS_the_target_is_not_the_igniter(world):
    session(world, name="cursus", total=60_000, age_s=2 * 3600, model="claude-fable-5-1")
    opener(world, SPECIMEN_OPENER)                       # names "cursus-atlas", not "cursus"
    assert denied(send(world, to="cursus"))


def test_an_igniter_that_is_cold_AND_large_is_still_refused(world):
    """Keep cold-and-large: the igniter exemption is the Fable rule's alone."""
    session(world, name="cursus-atlas", total=300_000, age_s=10 * 3600, model="claude-fable-5-1")
    opener(world, SPECIMEN_OPENER)
    why = send(world, to="cursus-atlas")["permissionDecisionReason"]
    assert "resume_cold_cap" in why and "claude-fable-5-1" not in why.split("(")[0], why


def test_an_igniter_does_not_exempt_a_NON_FABLE_cold_large_target(world):
    """Reviewer (RESUMEGATE-2): the igniter reading is the Fable rule's alone."""
    session(world, name="cursus-atlas", total=300_000, age_s=10 * 3600, model="claude-opus-5-5")
    opener(world, SPECIMEN_OPENER)
    why = send(world, to="cursus-atlas")["permissionDecisionReason"]
    assert "resume_cold_cap" in why and "It ignited" not in why, why


def test_the_igniter_is_read_from_the_FIRST_prompt_only(world):
    """A later message that mentions ignition does not make the target the igniter."""
    session(world, name="cursus-atlas", total=60_000, age_s=2 * 3600, model="claude-fable-5-1")
    p = world["projects"] / "-proj" / f"{ME}.jsonl"
    recs = [{"type": "user", "message": {"content": "You are a worker."}},
            {"type": "user", "message": {"content": "cursus-atlas ignited builder 9."}}]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    assert denied(send(world, to="cursus-atlas"))


def test_an_opener_in_text_BLOCKS_is_read(world):
    session(world, name="cursus-atlas", total=60_000, age_s=2 * 3600, model="claude-fable-5-1")
    p = world["projects"] / "-proj" / f"{ME}.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {"content": [
        {"type": "text", "text": SPECIMEN_OPENER}]}}) + "\n")
    assert "It ignited this session" in note_of(send(world, to="cursus-atlas"))


def test_a_sender_with_no_transcript_is_not_an_igniter(world):
    session(world, name="cursus-atlas", total=60_000, age_s=2 * 3600, model="claude-fable-5-1")
    assert denied(send(world, to="cursus-atlas"))


# ------------------------------------------------ SUBAGENTGATE-1: in-process sub-agent wakes ----

def test_a_300k_SUBAGENT_ten_hours_cold_is_refused(world):
    """The row's positive control."""
    subagent(world, total=300_000, age_s=10 * 3600)
    out = send(world, to=AGENT)
    assert denied(out), out
    why = out["permissionDecisionReason"]
    assert "cold and its context is at or above resume_cold_cap 200,000" in why, why
    assert "context 300,000 tokens" in why and "fresh agent from the brief" in why, why
    assert "\tresume-subagent\t" in (world["state"] / "deny.log").read_text()


def test_a_FRESH_subagent_passes_with_its_price(world):
    """The row's negative control."""
    subagent(world, total=40_000, age_s=30)
    note = note_of(send(world, to=AGENT))
    assert "warm; context 40,000 tokens" in note and "claude-sonnet-5" in note, note


def test_a_subagent_with_NO_transcript_PASSES_unmeasured_and_is_logged(world):
    """Never refuse on a missing measurement."""
    out = send(world, to=AGENT)
    assert "UNMEASURED — no transcript" in note_of(out)
    assert "unmeasured\tsubagent\tno-transcript" in (world["state"] / "resume_gate.log").read_text()


def test_a_subagent_transcript_under_ANOTHER_parent_is_found(world):
    """The sender is not always the parent (a peer session, or a worktree session)."""
    subagent(world, total=300_000, age_s=10 * 3600, parent="77777777-0000-0000-0000-000000000000")
    assert denied(send(world, to=AGENT))


def test_a_subagent_is_measured_from_ITS_OWN_chain_not_a_main_chain_record(world):
    """A sub-agent file's records are all isSidechain; a stray main-chain record must not be its
    size — the main-chain filter the session measurer uses would read exactly that record."""
    subagent(world, total=300_000, age_s=10 * 3600, main_chain_noise=True)
    assert "context 300,000 tokens" in send(world, to=AGENT)["permissionDecisionReason"]


def test_a_subagent_near_the_window_is_refused_even_warm(world):
    subagent(world, total=360_000, age_s=30, ttl="1h")
    assert "under resume_min_headroom" in send(world, to=AGENT)["permissionDecisionReason"]


def test_a_COLD_FABLE_subagent_is_refused(world):
    subagent(world, total=40_000, age_s=2 * 3600, model="claude-fable-5-1")
    assert "runs claude-fable-5-1 and is cold" in send(world, to=AGENT)["permissionDecisionReason"]


def test_a_subagent_wake_can_be_overridden(world):
    subagent(world, total=300_000, age_s=10 * 3600)
    out = send(world, to=AGENT, message="RESUME-OVERRIDE: it holds the only copy of the result")
    assert "permissionDecision" not in out, out


def test_a_name_that_merely_starts_with_a_is_not_a_subagent_id(world):
    assert send(world, to="atlas-seat") == {}
    assert send(world, to="a12") == {}


def test_the_subagent_id_length_bound_is_EIGHT_hex(world):
    """Reviewer (SUBAGENTGATE-1): the regex's lower bound had no test at the boundary."""
    assert send(world, to="a1234567") == {}                                  # 7: not an id
    assert "UNMEASURED — no transcript" in note_of(send(world, to="a12345678"))  # 8: an id


# ------------------------------------------ MARKER SCOPE (PLUGDIR-1): a note outside it ----

def test_scope_an_unmarked_sender_gets_the_refusal_as_a_note_and_the_message_goes(world):
    """NEGATIVE control for the scope: the same cold, large wake that is refused from a marked
    repo (test_COLD_AND_LARGE_is_refused_and_told_to_start_fresh) is a note from an unmarked one."""
    (world["projects"] / ".atlas-lane").unlink()
    world["env"].pop("CLAUDE_PROJECT_DIR", None)
    session(world, total=300_000, age_s=10 * 3600)
    out = send(world)
    assert not denied(out), out
    text = note_of(out)
    assert "not enforced here" in text and "cold and its context is at or above" in text, text
