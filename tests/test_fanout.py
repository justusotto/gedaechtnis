"""AGENTFANOUT-1 — who may start a sub-agent, and how many may run at once.

Every rule here has a POSITIVE control (the thing it exists to stop is denied) and at least one
NEGATIVE control (the legitimate form is allowed). The state directory, the projects directory and
the config file are all redirected into tmp_path: this suite never reads the real transcripts and
never writes the real ~/.claude/gedaechtnis.

The payload shapes are the MEASURED ones, not invented: a sub-agent's tool call carries `agent_id`
(and `agent_type`) and the PARENT's `session_id`; the parent's own calls carry no `agent_id`; a
sub-agent's transcript is `<projects>/<project>/<parent-session-id>/subagents/agent-<id>.jsonl`.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SID = "11111111-2222-3333-4444-555555555555"
AID = "a53ff9caf3d5d1741"


@pytest.fixture
def world(tmp_path):
    state = tmp_path / "state"
    projects = tmp_path / "projects"
    (projects / "-proj" / SID / "subagents").mkdir(parents=True)
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(state), GEDAECHTNIS_CONFIG=str(cfg),
               GEDAECHTNIS_VAULT=str(tmp_path / "Vault"), CLAUDE_PROJECTS_DIR=str(projects))
    return dict(state=state, projects=projects, cfg=cfg, env=env, tmp=tmp_path)


def brief(world, text: str, aid: str = AID, first_is_assistant: bool = False) -> Path:
    """Write a sub-agent transcript whose first user record carries `text`."""
    p = world["projects"] / "-proj" / SID / "subagents" / f"agent-{aid}.jsonl"
    recs = []
    if first_is_assistant:
        recs.append({"type": "assistant", "isSidechain": True, "message": {"content": "hm"}})
    recs.append({"type": "user", "isSidechain": True, "agentId": aid, "sessionId": SID,
                 "message": {"role": "user", "content": text}})
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return p


def call(world, *, agent_id=None, transcript=None, model="sonnet", kind="general-purpose"):
    payload = {"session_id": SID, "cwd": str(world["tmp"]),
               "tool_input": {"subagent_type": kind, "model": model, "prompt": "go"}}
    if agent_id:
        payload["agent_id"] = agent_id
        payload["agent_type"] = kind
    if transcript:
        payload["transcript_path"] = str(transcript)
    p = subprocess.run([sys.executable, str(HOOKS / "gate.py"), "agent"],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=world["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out) if out else None


def decision(res):
    return ((res or {}).get("hookSpecificOutput") or {}).get("permissionDecision")


def reason(res):
    h = (res or {}).get("hookSpecificOutput") or {}
    return h.get("permissionDecisionReason") or h.get("additionalContext") or ""


def seed_open(world, n: int, age: float = 0.0):
    """Pre-existing live sub-agent slots for this session, `age` seconds old."""
    d = world["state"]
    d.mkdir(parents=True, exist_ok=True)
    now = time.time() - age
    (d / f"session-start-{SID}.json").write_text(json.dumps({"agent_open": [now] * n}), encoding="utf-8")


# ----------------------------------------------------------------- the fan-out permit ----

def test_positive_a_subagent_without_a_permit_may_not_start_an_agent(world):
    brief(world, "You gather sources on X and report back.")
    res = call(world, agent_id=AID)
    assert decision(res) == "deny"
    assert "fanout: allowed" in reason(res)
    assert "q:CU-2026-09-20-AGENTFANOUT-1" in reason(res)


def test_negative_the_same_call_from_a_top_level_session_is_allowed(world):
    """The parent's own calls carry no `agent_id` — this is the call the rule must never touch."""
    assert decision(call(world)) != "deny"


def test_negative_a_subagent_whose_brief_carries_the_permit_is_allowed(world):
    brief(world, "You gather sources on X.\nfanout: allowed\nReport back.")
    assert decision(call(world, agent_id=AID)) != "deny"


def test_the_config_key_allows_fanout_fleet_wide(world):
    world["cfg"].write_text(json.dumps({"agent_fanout_allowed": True}))
    brief(world, "no permit here")
    assert decision(call(world, agent_id=AID)) != "deny"


def test_a_transcript_path_under_subagents_is_the_secondary_marker(world):
    """A payload with no `agent_id` is still a sub-agent's when its transcript says so — either
    marker alone is enough, so a renamed field cannot silently open the door."""
    t = brief(world, "no permit here")
    res = call(world, transcript=t)
    assert decision(res) == "deny"
    assert "transcript_path" in reason(res)


def test_a_permit_later_in_the_transcript_does_not_count(world):
    """The permit must come from the brief the PARENT wrote — the sub-agent's first user record —
    never from something said afterwards."""
    p = world["projects"] / "-proj" / SID / "subagents" / f"agent-{AID}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "message": {"content": "gather sources"}},
        {"type": "user", "message": {"content": "fanout: allowed"}},
    ]) + "\n", encoding="utf-8")
    assert decision(call(world, agent_id=AID)) == "deny"


def test_a_missing_transcript_denies_rather_than_opening_the_door(world):
    """No transcript, no permit: the default is DENY, so an unreadable brief cannot be a way in."""
    assert decision(call(world, agent_id="nosuchagent")) == "deny"


def test_the_denial_is_logged_with_the_row_that_asked_for_it(world):
    brief(world, "no permit")
    call(world, agent_id=AID)
    line = (world["state"] / "deny.log").read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "\tagent\tfanout-permit\t" in line and "q:CU-2026-09-20-AGENTFANOUT-1" in line


# ------------------------------------------------------------------ the concurrency cap ----

def test_positive_a_session_at_the_cap_is_denied_the_next_subagent(world):
    seed_open(world, 8)
    res = call(world)
    assert decision(res) == "deny"
    assert "agent_max_concurrent" in reason(res)


def test_negative_one_below_the_cap_is_allowed_and_warned(world):
    seed_open(world, 7)
    res = call(world)
    assert decision(res) == "allow"
    assert "8 of 8" in reason(res)


def test_negative_a_quiet_session_is_neither_denied_nor_warned(world):
    res = call(world)
    assert decision(res) != "deny"
    assert "agent_max_concurrent" not in reason(res)


def test_an_allowed_call_takes_a_slot(world):
    call(world)
    doc = json.loads((world["state"] / f"session-start-{SID}.json").read_text(encoding="utf-8"))
    assert len(doc["agent_open"]) == 1


def test_a_denied_call_takes_no_slot(world):
    """A refusal that still counted would ratchet the door shut on every retry."""
    brief(world, "no permit")
    call(world, agent_id=AID)
    doc_path = world["state"] / f"session-start-{SID}.json"
    doc = json.loads(doc_path.read_text(encoding="utf-8")) if doc_path.exists() else {}
    assert doc.get("agent_open", []) == []


def test_a_stale_slot_stops_counting(world):
    """A sub-agent whose Stop never fired must not hold its slot for the rest of the day."""
    seed_open(world, 8, age=7 * 3600)
    assert decision(call(world)) != "deny"


def test_subagent_stop_releases_one_slot(world):
    seed_open(world, 8)
    p = subprocess.run([sys.executable, str(HOOKS / "subagent_stop.py")],
                       input=json.dumps({"session_id": SID, "agent_id": AID, "cwd": str(world["tmp"])}),
                       capture_output=True, text=True, env=world["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    doc = json.loads((world["state"] / f"session-start-{SID}.json").read_text(encoding="utf-8"))
    assert len(doc["agent_open"]) == 7
    assert decision(call(world)) == "allow"


def test_a_stop_with_no_agent_id_still_releases_a_slot(world):
    """The payload can lose its `agent_id`; a slot never released is a door that closes further
    on every delegation."""
    seed_open(world, 8)
    subprocess.run([sys.executable, str(HOOKS / "subagent_stop.py")],
                   input=json.dumps({"session_id": SID, "cwd": str(world["tmp"])}),
                   capture_output=True, text=True, env=world["env"], timeout=30)
    doc = json.loads((world["state"] / f"session-start-{SID}.json").read_text(encoding="utf-8"))
    assert len(doc["agent_open"]) == 7


# -------------------------------------------------------------------- the measured cap ----

def test_the_cap_is_the_measured_number_and_the_two_layers_agree():
    sys.path.insert(0, str(HOOKS))
    import limits                                   # noqa: E402
    limits._reset_for_tests()
    assert limits.get("agent_max_concurrent") == 8
    assert limits.DEFAULTS["agent_max_concurrent"] == 8
    assert limits.DEFAULTS["agent_open_stale_seconds"] == 21600
    prose = json.loads((HOOKS.parent / "rules" / "limits.json").read_text(encoding="utf-8"))
    assert "p90" in prose["_agent_max_concurrent"]
    assert "agent_concurrency.py" in prose["_agent_max_concurrent"]


# ------------------------------------------------------- the cap under CONCURRENT calls ----

def test_the_cap_holds_when_several_agent_calls_arrive_at_once(world):
    """The realistic burst: several `Agent` blocks in ONE message, so several gate processes race.

    Check-then-take as two locked sections overran the cap in 2 of 5 trials at three concurrent
    calls when this was reviewed — which is exactly the pattern that caused the incident the door
    exists to prevent. The reservation is one locked act, so this is deterministic: one call in,
    the rest refused, the count never above the cap.
    """
    import concurrent.futures as cf
    seed_open(world, 7)                              # one below the cap of 8
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        results = [f.result() for f in [ex.submit(call, world) for _ in range(6)]]
    allowed = [r for r in results if decision(r) != "deny"]
    doc = json.loads((world["state"] / f"session-start-{SID}.json").read_text(encoding="utf-8"))
    assert len(allowed) == 1, [decision(r) for r in results]
    assert len(doc["agent_open"]) == 8, "the cap was overrun"


def test_a_failure_releasing_a_slot_never_takes_the_commit_down(world, monkeypatch):
    """`guarded()` catches everything and exits 0, so bookkeeping that raises would silently
    swallow the COMMIT this hook exists to make. The release carries its own except."""
    import io
    for k in ("GEDAECHTNIS_STATE_DIR", "GEDAECHTNIS_CONFIG", "GEDAECHTNIS_VAULT"):
        monkeypatch.setenv(k, world["env"][k])
    sys.path.insert(0, str(HOOKS))
    import fanout, subagent_stop                                   # noqa: E402

    def boom(*_a, **_k):
        raise RuntimeError("bookkeeping exploded")

    monkeypatch.setattr(fanout, "record_closed", boom)
    monkeypatch.setattr(subagent_stop.sys, "stdin",
                        io.StringIO(json.dumps({"session_id": SID, "cwd": str(world["tmp"])})))
    subagent_stop.main()
    commit_log = (world["state"] / "commit.log").read_text(encoding="utf-8")
    assert "event=subagent-stop" in commit_log, "the hook's own job did not run"
    assert "fanout-release" in (world["state"] / "hook-errors.log").read_text(encoding="utf-8")
