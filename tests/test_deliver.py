"""deliver.py — the messages the resume gate parked are shown to their recipient (CONTEXTMSG-1).

Every rule has a POSITIVE control (it delivers) and a NEGATIVE one (it does not, or it keeps the
line). The registry, the state directory and the config are redirected into tmp_path: this suite
never reads a real registry and never touches a real parked file.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
SID = "aaaaaaaa-1111-2222-3333-444444444444"


@pytest.fixture
def world(tmp_path, monkeypatch):
    state, sessions = tmp_path / "state", tmp_path / "sessions"
    state.mkdir()
    sessions.mkdir()
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"),
               GEDAECHTNIS_VAULT=str(tmp_path / "Vault"), CLAUDE_SESSIONS_DIR=str(sessions),
               CLAUDE_PROJECTS_DIR=str(tmp_path / "projects"))
    env.pop("GEDAECHTNIS_LIMITS", None)
    for k in ("GEDAECHTNIS_STATE_DIR", "GEDAECHTNIS_CONFIG", "GEDAECHTNIS_VAULT",
              "CLAUDE_SESSIONS_DIR", "CLAUDE_PROJECTS_DIR"):
        monkeypatch.setenv(k, env[k])
    return dict(state=state, sessions=sessions, env=env)


def register(w, name="seat-one", sid=SID):
    (w["sessions"] / f"{sid[:6]}.json").write_text(json.dumps(
        {"pid": os.getpid(), "sessionId": sid, "name": name, "status": "idle"}))


def park_row(w, name="seat-one", text="row 5 is done", sender="builder-3", age_s=60, reason="r"):
    at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - age_s))
    row = {"schema": "parked/1", "kind": "refused-wake", "to": name, "from": sender,
           "reason": reason, "text": text, "at": at}
    with (w["state"] / f"parked-{name}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def run(w, event="UserPromptSubmit", source=None, sid=SID):
    payload = {"session_id": sid, "hook_event_name": event}
    if source:
        payload["source"] = source
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "deliver.py")], input=json.dumps(payload),
                       capture_output=True, text=True, env=w["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return None
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == event
    return out["additionalContext"]


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _mod():
    sys.path.insert(0, str(HOOKS))
    import deliver                                     # noqa: PLC0415
    return deliver


# ------------------------------------------------------------------------------ delivery ----

@pytest.mark.parametrize("event,source", [("UserPromptSubmit", None), ("SessionStart", "startup"),
                                          ("SessionStart", "resume"), ("SessionStart", "compact")])
def test_parked_messages_are_shown_at_every_wired_event_then_moved(world, event, source):
    register(world)
    park_row(world, text="first report", age_s=600)
    park_row(world, text="second report", age_s=60)
    text = run(world, event, source)
    assert text and "first report" in text and "second report" in text
    assert text.index("second report") < text.index("first report")      # newest first
    assert "not the owner's word" in text and "builder-3" in text
    assert not (world["state"] / "parked-seat-one.jsonl").exists()
    assert len(rows(world["state"] / "parked-seat-one.delivered.jsonl")) == 2
    assert run(world, event, source) is None                              # delivered once


def test_nothing_is_shown_to_a_session_of_another_name(world):
    register(world, name="seat-two")
    park_row(world, name="seat-one")
    assert run(world) is None
    assert len(rows(world["state"] / "parked-seat-one.jsonl")) == 1       # untouched


def test_the_recipient_is_named_by_the_registry_not_by_its_launch_flag(world):
    """A resumed session carries no `--name`; the registry is where the gate names it."""
    register(world, name="seat-one")
    park_row(world)
    assert "row 5 is done" in run(world)
    assert run(world, sid="bbbbbbbb-0000-0000-0000-000000000000") is None   # an unknown id: nothing


def test_at_most_the_budget_is_shown_and_the_rest_waits_for_the_next_delivery(world):
    register(world)
    for i in range(5):
        park_row(world, text=f"msg{i} " + "x" * 1900, age_s=600 - i)
    text = run(world)
    assert len(text) <= 6000
    assert "msg4" in text and "msg0" not in text and "wait in" in text
    left = rows(world["state"] / "parked-seat-one.jsonl")
    assert left and {r["text"][:4] for r in left} <= {"msg0", "msg1", "msg2"}
    second = run(world)
    assert "msg0" in second or "msg1" in second


def test_a_message_older_than_48_hours_is_listed_not_shown(world):
    register(world)
    park_row(world, text="STALE BODY", sender="old-builder", age_s=49 * 3600)
    park_row(world, text="fresh body", age_s=60)
    text = run(world)
    assert "fresh body" in text                                           # negative control
    assert "STALE BODY" not in text and "old-builder" in text and "delivered.jsonl" in text
    assert len(rows(world["state"] / "parked-seat-one.delivered.jsonl")) == 2


def test_a_foreign_line_in_the_parked_file_is_kept(world):
    register(world)
    park_row(world)
    with (world["state"] / "parked-seat-one.jsonl").open("a") as fh:
        fh.write('{"schema": "other/1"}\n')
    assert "row 5 is done" in run(world)
    assert rows(world["state"] / "parked-seat-one.jsonl") == [{"schema": "other/1"}]


def test_a_message_parked_between_the_claim_and_the_read_is_not_lost(world, monkeypatch):
    d = _mod()
    _mod()
    import fleet                                       # noqa: PLC0415
    register(world)
    park_row(world, text="before the claim")
    real = d._claim

    def racing(live, pid):
        got = real(live, pid)
        fleet.park("seat-one", "late-sender", "r", "parked in between")   # the gate, mid-delivery
        return got
    monkeypatch.setattr(d, "_claim", racing)
    text = d.deliver(SID)
    assert "before the claim" in text and "parked in between" not in text
    left = rows(world["state"] / "parked-seat-one.jsonl")
    assert [r["text"] for r in left] == ["parked in between"]                 # waiting, not lost
    monkeypatch.setattr(d, "_claim", real)
    assert "parked in between" in d.deliver(SID)


def test_without_the_claim_the_same_race_would_lose_the_line(world, monkeypatch):
    """Positive control for the race test: had delivery read the LIVE file and then removed it,
    the line parked in between is gone. The rename is what saves it."""
    _mod()
    import fleet                                       # noqa: PLC0415
    register(world)
    park_row(world, text="before")
    live = world["state"] / "parked-seat-one.jsonl"
    live.read_text()                                   # read ...
    fleet.park("seat-one", "late", "r", "in between")  # ... a park lands ...
    live.unlink()                                      # ... and a naive delivery removes the file
    assert rows(live) == []


def test_a_delivery_error_prints_nothing_and_leaves_the_file_as_it_was(world):
    register(world)
    park_row(world, text="one")
    park_row(world, text="two")
    live = world["state"] / "parked-seat-one.jsonl"
    before = live.read_bytes()
    (world["state"] / "parked-seat-one.delivered.jsonl").mkdir()     # the mark cannot be written
    assert run(world) is None
    assert live.read_bytes() == before
    assert not list(world["state"].glob("parked-seat-one.jsonl.claim-*"))
    assert "error" in (world["state"] / "deliver.log").read_text()


def test_a_claim_left_by_a_dead_delivery_is_picked_up(world):
    register(world)
    row = park_row(world, text="orphaned")
    live = world["state"] / "parked-seat-one.jsonl"
    live.rename(live.with_name(live.name + ".claim-999999-1"))       # no such process
    assert "orphaned" in run(world)
    assert not list(world["state"].glob("parked-seat-one.jsonl.claim-*"))
    assert rows(world["state"] / "parked-seat-one.delivered.jsonl")[0]["text"] == row["text"]


def test_a_live_deliverys_claim_is_left_alone(world):
    register(world)
    park_row(world, text="theirs")
    live = world["state"] / "parked-seat-one.jsonl"
    theirs = live.with_name(f"{live.name}.claim-{os.getppid()}-1")   # a live pid, not this hook's
    live.rename(theirs)
    assert run(world) is None and theirs.exists()


def test_no_parked_file_at_all_is_silent_and_cheap(world):
    register(world)
    assert run(world) is None
    assert not (world["state"] / "deliver.log").exists()


def test_a_broken_input_still_exits_zero(world):
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "deliver.py")], input="{not json",
                       capture_output=True, text=True, env=world["env"], timeout=30)
    assert p.returncode == 0 and p.stdout == ""


def test_the_delivery_is_wired_to_session_start_and_every_prompt():
    hj = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
    ss = [g for g in hj["SessionStart"] if any("deliver.py" in h["command"] for h in g["hooks"])]
    assert len(ss) == 1 and set(ss[0]["matcher"].split("|")) == {"startup", "resume", "compact"}
    ups = [h for g in hj["UserPromptSubmit"] for h in g["hooks"] if "deliver.py" in h["command"]]
    assert len(ups) == 1


def test_delivered_files_are_not_counted_as_parked(world):
    _mod()
    import fleet                                       # noqa: PLC0415
    register(world)
    park_row(world)
    run(world)
    assert fleet.all_parked() == {}
    park_row(world)
    assert fleet.all_parked() == {"seat-one": 1}


# ---------------------------------------------------------------------- the sender's name ----

def test_the_sender_is_named_through_the_registry_by_its_session_id(world, monkeypatch):
    _mod()
    import fleet, procs                                # noqa: PLC0415
    monkeypatch.setattr(procs, "session_name", lambda pid: None)       # no `--name`, as resumed
    register(world, name="resumed-seat", sid="cccccccc-0000-0000-0000-000000000000")
    assert fleet.session_name_of("cccccccc-0000-0000-0000-000000000000") == "resumed-seat"
    assert fleet.session_name_of("dddddddd-0000-0000-0000-000000000000") is None   # unknown id
    monkeypatch.setattr(procs, "session_name", lambda pid: "by-flag")
    assert fleet.session_name_of("dddddddd-0000-0000-0000-000000000000") == "by-flag"  # fallback


# ------------------------------------------------ review round 1: a write already in flight ----

def test_a_park_whose_file_moved_between_open_and_lock_writes_to_the_new_file(world, monkeypatch):
    """The reviewer's race: park opened the parked file, then delivery renamed it away. The park
    must notice the open file is no longer the one at the path and write to the new one."""
    _mod()
    import common, fleet                               # noqa: PLC0415
    live = world["state"] / "parked-seat-one.jsonl"
    park_row(world, text="already there")
    claim = live.with_name(live.name + ".claim-1-1")
    real, moved = common.lock_file, []

    def lock_after_rename(fh):
        if not moved:
            os.rename(live, claim)                     # delivery claims between park's open and lock
            moved.append(1)
        real(fh)
    monkeypatch.setattr(common, "lock_file", lock_after_rename)
    fleet.park("seat-one", "late", "r", "in flight")
    assert [r["text"] for r in rows(live)] == ["in flight"]
    assert [r["text"] for r in rows(claim)] == ["already there"]


def test_control_a_write_through_a_handle_opened_before_the_rename_lands_in_the_claim(world):
    """Positive control for the test above: without the same-file check the line goes to the claim,
    which delivery deletes after reading."""
    live = world["state"] / "parked-seat-one.jsonl"
    park_row(world, text="already there")
    fh = live.open("a")
    claim = live.with_name(live.name + ".claim-1-1")
    os.rename(live, claim)
    fh.write('{"schema": "parked/1", "text": "lost"}\n')
    fh.close()
    assert "lost" in claim.read_text() and not live.exists()


def test_delivery_waits_for_a_write_in_flight_before_it_reads(world):
    import threading
    d = _mod()
    import common                                      # noqa: PLC0415
    register(world)
    park_row(world, text="first")
    live = world["state"] / "parked-seat-one.jsonl"
    fh = live.open("a")                                # a park that holds the lock mid-write
    common.lock_file(fh)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("text", d.deliver(SID)))
    t.start()
    time.sleep(0.3)
    assert t.is_alive()                                # renamed, now waiting on the lock
    fh.write(json.dumps({"schema": "parked/1", "from": "w", "reason": "r", "text": "mid-write",
                         "at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
    fh.flush()
    common.unlock_file(fh)
    fh.close()
    t.join(10)
    assert "mid-write" in out["text"] and "first" in out["text"]


def test_a_restore_never_replaces_a_file_parked_meanwhile(world):
    d = _mod()
    live = world["state"] / "parked-seat-one.jsonl"
    park_row(world, text="claimed")
    claim = live.with_name(live.name + ".claim-1-1")
    os.rename(live, claim)
    park_row(world, text="parked meanwhile")
    d._restore(live, [claim])
    assert sorted(r["text"] for r in rows(live)) == ["claimed", "parked meanwhile"]
    assert not claim.exists()


def test_the_budget_holds_with_the_more_line_and_a_long_old_list(world):
    register(world)
    for i in range(3):
        park_row(world, text=f"big{i} " + "x" * 1990, age_s=600 - i)
    for i in range(200):
        park_row(world, text="old", sender=f"b{i}", age_s=50 * 3600 + i)
    text = run(world)
    assert len(text) <= 6000 and "wait in" in text


def test_append_lines_writes_only_into_the_file_still_at_the_path(world, monkeypatch):
    """Review round 2, M5: put-back lines follow the path, never a file another delivery claimed."""
    _mod()
    import common, fleet                               # noqa: PLC0415
    live = world["state"] / "parked-seat-one.jsonl"
    park_row(world, text="claimed by another delivery")
    claim = live.with_name(live.name + ".claim-1-1")
    real, moved = common.lock_file, []

    def lock_after_rename(fh):
        if not moved:
            os.rename(live, claim)
            moved.append(1)
        real(fh)
    monkeypatch.setattr(common, "lock_file", lock_after_rename)
    fleet.append_lines(live, '{"schema": "parked/1", "text": "put back"}\n')
    assert [r["text"] for r in rows(live)] == ["put back"]
    assert [r["text"] for r in rows(claim)] == ["claimed by another delivery"]
