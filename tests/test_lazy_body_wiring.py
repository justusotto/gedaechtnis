"""lazy_body at the DOOR — the four `chore.py` call sites, driven as the harness drives them.

★ MUST-FIX 2, from the reviewer, and it names a real gap: `test_lazy_body.py` tests the MODULE.
Nothing tested the WIRING, and the wiring is where a PostToolUse arm actually fails — a hook that
prints a second JSON object corrupts the protocol for every other arm in the same process, and no
module test can see that.

So every assertion here is made against the SUBPROCESS's stdout, not against a return value:
exactly one line, and that line parses. `test_gate.py::run` asserts a zero exit and does the
parse, which is why it is borrowed rather than reimplemented.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

from test_gate import world, run                                # noqa: F401 — the shared sandbox

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
PLUGIN = Path(__file__).resolve().parents[1]


def graduated(world, region="Studio/Cards"):
    """A region with a Boot file and two bodies — the only shape this arm fires for."""
    d = world["vault"] / region
    d.mkdir(parents=True, exist_ok=True)
    (d / "Kernel.md").write_text("# Boot\n\nAn index.\n", encoding="utf-8")
    (d / "Position.md").write_text("# Position\n\nTHE REGION STANDS HERE.\n", encoding="utf-8")
    (d / "Canon.md").write_text("# Canon\n\nA LOCKED DECISION.\n", encoding="utf-8")
    return d


def env_with(world, **limits):
    """The sandbox env plus a limits file carrying `limits` — the arm ships OFF, so every
    positive control here has to turn it on explicitly, which is itself the shipped-default proof."""
    env = dict(world["env"])
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    shipped.update(limits)
    path = Path(env["GEDAECHTNIS_STATE_DIR"]).parent / f"limits-{len(limits)}-{sorted(limits)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(shipped), encoding="utf-8")
    env["GEDAECHTNIS_LIMITS"] = str(path)
    return env


def raw(script, which, payload, env):
    """The subprocess's RAW stdout. `run` parses and would hide a second object entirely."""
    p = subprocess.run([sys.executable, str(HOOKS / script), which], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stdout


def one_json_object(out: str) -> dict:
    """Exactly one JSON object on stdout, and it parses. The protocol contract, asserted."""
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 1, f"a PostToolUse arm must emit at most one JSON object, got {len(lines)}:\n{out}"
    return json.loads(lines[0])


def ctx(doc: dict) -> str:
    return doc.get("hookSpecificOutput", {}).get("additionalContext", "")


# ------------------------------------------------------------------ the three doors ----

def test_the_write_door_injects_on_a_vault_path(world):
    d = graduated(world)
    env = env_with(world, lazy_bodies_enabled=True)
    out = raw("chore.py", "write", {"cwd": str(world["repo"]), "session_id": "w1",
                                    "tool_name": "Write",
                                    "tool_input": {"file_path": str(d / "Position.md"),
                                                   "content": "x\n"}}, env)
    text = ctx(one_json_object(out))
    assert "THE REGION STANDS HERE." in text and "A LOCKED DECISION." in text


def test_the_read_door_injects(world):
    d = graduated(world)
    env = env_with(world, lazy_bodies_enabled=True)
    out = raw("chore.py", "read", {"cwd": str(world["repo"]), "session_id": "r1",
                                   "tool_name": "Read",
                                   "tool_input": {"file_path": str(d / "Canon.md")}}, env)
    assert "THE REGION STANDS HERE." in ctx(one_json_object(out))


def test_the_bash_door_injects_on_a_heredoc_write(world):
    d = graduated(world)
    env = env_with(world, lazy_bodies_enabled=True)
    cmd = f"cat > {d / 'Position.md'} <<'EOF'\nx\nEOF"
    out = raw("chore.py", "bash", {"cwd": str(world["repo"]), "session_id": "b1",
                                   "tool_name": "Bash", "tool_input": {"command": cmd}}, env)
    assert "THE REGION STANDS HERE." in ctx(one_json_object(out))


def test_the_non_vault_early_return_still_speaks(world, tmp_path):
    """(c) in the reviewer's mandate. `do_write` returns early for a path outside the vault, and
    everything below that return is silent — so an injection placed after it would be lost for
    exactly the population LESSONYIELD-1 measured the re-occurrences in: repo artifacts."""
    graduated(world)
    env = env_with(world, lazy_bodies_enabled=True)
    # A repo of our own that DECLARES the region, so the resolution under test is the repo
    # branch (`region_of_repo`) rather than whatever the shared fixture's repo happens to say.
    repo = tmp_path / "cards-repo"; repo.mkdir()
    (repo / "CLAUDE.md").write_text(f"@{world['vault']}/Studio/Cards/Kernel.md\n", encoding="utf-8")
    (repo / ".git").mkdir()
    f = repo / "ROW-REPORT.md"
    f.write_text("x\n", encoding="utf-8")
    out = raw("chore.py", "write", {"cwd": str(repo), "session_id": "n1",
                                    "tool_name": "Write",
                                    "tool_input": {"file_path": str(f), "content": "x\n"}}, env)
    assert "THE REGION STANDS HERE." in ctx(one_json_object(out))


# ------------------------------------------------------------------ the shipped default ----

@pytest.mark.parametrize("door,payload_key", [("write", "file_path"), ("read", "file_path")])
def test_every_door_is_silent_at_the_SHIPPED_default(world, door, payload_key):
    """(d). The arm ships FALSE. A door that spoke anyway would charge every installation of this
    package for a feature its own limits file says is off."""
    d = graduated(world)
    env = dict(world["env"])                                    # the SHIPPED limits, untouched
    out = raw("chore.py", door, {"cwd": str(world["repo"]), "session_id": f"off-{door}",
                                 "tool_name": door.capitalize(),
                                 "tool_input": {payload_key: str(d / "Position.md"),
                                                "content": "x\n"}}, env)
    assert "THE REGION STANDS HERE." not in out, f"the {door} door injected with the arm off"


def test_the_second_touch_is_silent_at_the_DOOR_too(world):
    """The dedupe is per session and the doors are separate PROCESSES — so the state has to be on
    disk for this to hold, and a module test that never leaves one process cannot show it."""
    d = graduated(world)
    env = env_with(world, lazy_bodies_enabled=True)
    pay = lambda p: {"cwd": str(world["repo"]), "session_id": "dedupe", "tool_name": "Read",
                     "tool_input": {"file_path": str(p)}}
    first = raw("chore.py", "read", pay(d / "Position.md"), env)
    second = raw("chore.py", "read", pay(d / "Canon.md"), env)
    assert "THE REGION STANDS HERE." in first
    assert "THE REGION STANDS HERE." not in second, "a separate process re-injected the region"
