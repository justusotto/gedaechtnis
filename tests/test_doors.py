"""The true-multitasking doors: D1 (no whole-file Write) and D2 (the per-file mutex).

Every rule gets a POSITIVE control (the unsafe shape is refused) and a NEGATIVE control (the
legitimate shape is allowed) — a door that only ever fires proves nothing about what it lets
through, and a false refusal here costs a session its edit. Each exemption is tested on its own,
because an exemption list checked as a block cannot tell which member is doing the work.

All state is redirected into tmp_path: the suite never reads the real vault and never writes the
real ~/.claude/gedaechtnis (Global/Errata: a suite that writes the application's real sidecar makes
its own verdict depend on the machine's state).
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
# Deliberately NOT on sys.path and never imported: every hook here is exercised as the SUBPROCESS
# Claude Code actually runs, and importing `common`/`config` into the test process would cache the
# FIRST test's vault in sys.modules — which broke a later test that loads common.py fresh.


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "Atlas"
    (vault / "Global").mkdir(parents=True)
    (vault / "Pharos" / "queues" / "regions").mkdir(parents=True)
    (vault / "Mnemosyne" / "UkrainianCard").mkdir(parents=True)
    (vault / "Concilium" / "Melchior").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    (vault / "Global" / "fleet-roster.md").write_text("```fleet-roster\nlane: CARD\nrepo: repo\n```\n")
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: CARD\npath: Mnemosyne/UkrainianCard/\npath: Global/\n")
    state = tmp_path / "state"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, state=state, env=env, tmp=tmp_path)


def run(script, which, payload, env):
    p = subprocess.run([sys.executable, str(HOOKS / script), which], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out) if out else None


def decision(res):
    return (res or {}).get("hookSpecificOutput", {}).get("permissionDecision")


def reason(res):
    return (res or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def gate_write(w, path, tool="Write", sid="A", content="new whole text\n", old=None, new=None, cwd=None):
    ti = {"file_path": str(path)}
    ti.update({"content": content} if tool == "Write" else {"old_string": old or "x", "new_string": new or "y"})
    return run("gate.py", "write", {"cwd": cwd or str(w["repo"]), "tool_name": tool,
                                    "session_id": sid, "tool_input": ti}, w["env"])


def chore_write(w, path, sid="A", tool="Write", content="x", cwd=None):
    return run("chore.py", "write", {"cwd": cwd or str(w["repo"]), "tool_name": tool, "session_id": sid,
                                     "tool_input": {"file_path": str(path), "content": content}}, w["env"])


def bash(w, cmd, sid="A"):
    return run("gate.py", "bash", {"cwd": str(w["repo"]), "tool_name": "Bash", "session_id": sid,
                                   "tool_input": {"command": cmd}}, w["env"])


def region(w) -> Path:
    return w["vault"] / "Mnemosyne" / "UkrainianCard"


# ============================================================== D1 — no whole-file Write ========

def test_d1_refuses_a_whole_file_write_over_an_existing_prose_file(world):
    """POSITIVE control. PROVES the door fires on the one shape that has no anchor, and that the
    deny says what to do instead — a refusal a model cannot act on just costs the turn."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry another session wrote\n")
    res = gate_write(world, f)
    assert decision(res) == "deny"
    r = reason(res)
    assert "Whole-file Write to `Mnemosyne/UkrainianCard/Errata.md` refused" in r
    assert "Use Edit" in r and "Design §5.2 D1" in r


def test_d1_leaves_the_anchored_edit_alone(world):
    """NEGATIVE control, and the point of the whole door: Edit is the safe shape, so it passes."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry another session wrote\n")
    assert gate_write(world, f, tool="Edit", old="- an entry", new="- an entry, extended") is None


def test_d1_exempts_a_file_that_does_not_exist(world):
    """NEGATIVE control: there is nothing in a file that is not there to lose."""
    assert gate_write(world, region(world) / "Brand-New.md") is None


def test_d1_exempts_an_existing_but_EMPTY_file(world):
    """NEGATIVE control, tested apart from the new-file case: an empty file is on disk, so only
    the size check can exempt it, and a door that read `exists()` alone would refuse it."""
    f = region(world) / "Errata.md"
    f.write_text("")
    assert f.exists() and f.stat().st_size == 0
    assert gate_write(world, f) is None


@pytest.mark.parametrize("name", ["report.html", "verdict.json", "rows.tsv"])
def test_d1_exempts_files_that_are_not_markdown(world, name):
    """NEGATIVE controls [R3]. A generator writing a page or a verdict export into the vault is not
    editing memory; D1 binds prose memory files and nothing else."""
    f = region(world) / name
    f.write_text("existing generated content\n")
    assert gate_write(world, f) is None, name


@pytest.mark.parametrize("rel", ["Cleanup 2026-09-09/anything.md",
                                 "Cleanup 2026-09-09/Mnemosyne/UkrainianCard/Errata.md"])
def test_d1_exempts_anything_under_a_Cleanup_bundle_at_any_depth(world, rel):
    """NEGATIVE controls [R3], at depth 1 and depth 3: a cleanup bundle is written whole by
    construction (Global/Nomos §Data integrity), and the glob is on ANY ancestor directory, not
    only the parent — a door checking `p.parent.name` would pass the first and refuse the second."""
    f = world["vault"] / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("bundled\n")
    assert gate_write(world, f) is None, rel


def test_d1_exempts_a_file_THIS_session_created_and_still_refuses_another_session(world):
    """The discriminating pair, and the reason the created-set is per SESSION ID rather than a
    flag on the file: A creates the file and may overwrite its own work; B, holding the identical
    stale read that D1 exists to catch, may not. Same path, same instant, opposite verdicts."""
    f = region(world) / "Position.md"
    assert gate_write(world, f, sid="A") is None            # the creation: gate stamps pre-exists=0
    f.write_text("# Position\n\nfirst content\n")
    chore_write(world, f, sid="A")                          # PostToolUse records the creation
    assert gate_write(world, f, sid="A", content="# Position\n\nrewritten by its author\n") is None
    chore_write(world, f, sid="A")                          # A finishes: its mutex comes off
    res = gate_write(world, f, sid="B", content="# Position\n\nclobbered by a stranger\n")
    assert decision(res) == "deny" and "Whole-file Write" in reason(res)


def test_d1_does_not_credit_a_creation_the_gate_REFUSED(world):
    """PROVES why the created-set is written by the PostToolUse chore and not by the gate: a
    creation the door denied never happened, so it must not license a later overwrite of a file
    somebody else went on to write. The gate stamps its pre-exists marker on denied calls too, so
    a created-set written at the gate would have credited this session with a file it never made."""
    f = region(world) / "Decisions.md"
    assert decision(gate_write(world, f, sid="A")) == "deny"       # the display-name door
    f.write_text("# the file somebody else then wrote here\n")
    chore_write(world, f, sid="A")            # even a chore run now must not record a creation
    res = gate_write(world, f, sid="A", content="mine now\n")
    assert decision(res) == "deny" and "Whole-file Write" in reason(res)


def test_d1_lets_a_verified_keyed_row_append_to_a_shared_surface_through(world):
    """NEGATIVE control on the seam with the shared-surface rule. That rule re-reads the file HERE
    and admits the write only if it extends what is on disk NOW — the same guarantee D1 asks of
    Edit — so D1 must not retract the keyed-row affordance §5.2 keeps. The paired POSITIVE control
    is in test_gate.py: a whole-file REWRITE of the same queue file is denied by D1."""
    q = world["vault"] / "Pharos" / "queues" / "regions" / "mining-ops.md"
    q.write_text("- [ ] `q:MN-2026-09-01-X-1` a row | q:MN-2026-09-01-X-1\n")
    body = q.read_text() + "- [ ] `q:MN-2026-09-09-NEW-1` another row | q:MN-2026-09-09-NEW-1\n"
    assert gate_write(world, q, content=body) is None


def test_d1_does_not_bind_MultiEdit_or_NotebookEdit(world):
    """NEGATIVE control on the tool name itself: only `Write` replaces a whole file blind."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    assert gate_write(world, f, tool="MultiEdit", old="- an entry", new="- an entry+") is None
    assert gate_write(world, f, tool="NotebookEdit", old="- an entry", new="- an entry+") is None


# ================================================================== D2 — the per-file mutex =====

def plant_lock(world, path, sid, age_s):
    """Write a lock file directly, aged by `age_s` seconds — the only way to test the takeover
    branch without sleeping through it. The path is derived the same way common.py derives it —
    sha1 of the realpath — rather than by importing it, see the note at the top of this file."""
    lk = Path(str(world["state"])) / "filelocks"
    lk.mkdir(parents=True, exist_ok=True)
    import hashlib
    name = hashlib.sha1(os.path.realpath(str(path)).encode("utf-8")).hexdigest()
    (lk / name).write_text(json.dumps({"session_id": sid, "ts": time.time() - age_s, "path": str(path)}))
    return lk / name


def read_lock(world, path):
    import hashlib
    name = hashlib.sha1(os.path.realpath(str(path)).encode("utf-8")).hexdigest()
    f = Path(str(world["state"])) / "filelocks" / name
    return json.loads(f.read_text()) if f.is_file() else None


def test_d2_refuses_while_a_FOREIGN_lock_is_fresh_and_says_retry(world):
    """POSITIVE control. PROVES the deny tells the model the one thing that fixes it — retry, which
    re-reads. A deny that read 'this file is locked' would invite the model to give up or to route
    around the door with a shell redirect."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    plant_lock(world, f, sid="OTHER", age_s=1)
    res = gate_write(world, f, tool="Edit", sid="A")
    assert decision(res) == "deny"
    r = reason(res)
    assert "Another session is editing `Mnemosyne/UkrainianCard/Errata.md` right now" in r
    assert "retry the same edit in a moment" in r and "Design §5.2 D2" in r


def test_d2_lets_a_session_past_ITS_OWN_fresh_lock(world):
    """NEGATIVE control, and a deadlock test: a session that retries its own edit must not be
    blocked by the lock it took on the previous attempt."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    plant_lock(world, f, sid="A", age_s=1)
    assert gate_write(world, f, tool="Edit", sid="A") is None
    assert read_lock(world, f)["session_id"] == "A"


def test_d2_takes_over_a_STALE_foreign_lock_and_records_the_new_holder(world):
    """PROVES the missing-PostToolUse path [R3]: when a tool errors or a hook dies the release
    never runs, and without this branch the file would be locked forever. 11s > the 10s TTL, so
    the lock is not a lock — it is taken over, and it now names the session that took it."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    plant_lock(world, f, sid="DEAD-SESSION", age_s=11)
    assert gate_write(world, f, tool="Edit", sid="A") is None
    assert read_lock(world, f)["session_id"] == "A"


def test_d2_the_PostToolUse_chore_releases_the_lock(world):
    """PROVES the release half — without it every lock would have to age out, and the mutex would
    serialise two sessions at ten seconds per edit instead of per edit."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    assert gate_write(world, f, tool="Edit", sid="A") is None
    assert read_lock(world, f) is not None
    chore_write(world, f, sid="A", tool="Edit")
    assert read_lock(world, f) is None


def test_d2_a_foreign_sessions_chore_does_NOT_release_our_lock(world):
    """NEGATIVE control on the release. Dropping somebody else's mutex is the same defect as
    taking it: B's chore would open A's file to a third writer mid-edit."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    gate_write(world, f, tool="Edit", sid="A")
    chore_write(world, f, sid="B", tool="Edit")
    assert (read_lock(world, f) or {}).get("session_id") == "A"


def test_d2_a_refused_write_does_not_leave_the_file_locked(world):
    """PROVES the release-on-refusal path. The tool call is not going to happen, so a lock held
    for the next ten seconds would block a sibling for a write that never occurred — a door that
    punished the whole lane for one session's mistake."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    assert decision(gate_write(world, f, sid="A")) == "deny"        # D1 refuses the whole-file Write
    assert read_lock(world, f) is None
    assert gate_write(world, f, tool="Edit", sid="B") is None       # so B is not blocked by it


def test_d2_binds_bash_redirects_and_the_bash_chore_releases_them(world):
    """PROVES the Bash half: a shell redirect is a whole-file overwrite with no anchor, so it takes
    the same mutex — otherwise the door would be a rule about which TOOL a session used, and
    `>> Errata.md` would be the way around it. NEGATIVE control on the release side too."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    assert bash(world, f"echo '- another' >> {f}", sid="A") is None
    assert read_lock(world, f)["session_id"] == "A"
    res = bash(world, f"echo '- mine' >> {f}", sid="B")
    assert decision(res) == "deny" and "Design §5.2 D2" in reason(res)
    run("chore.py", "bash", {"cwd": str(world["repo"]), "tool_name": "Bash", "session_id": "A",
                             "tool_input": {"command": f"echo '- another' >> {f}"}}, world["env"])
    assert read_lock(world, f) is None
    assert bash(world, f"echo '- mine' >> {f}", sid="B") is None


def test_d2_locks_live_in_the_state_dir_and_NEVER_in_the_vault(world):
    """PROVES the placement. A lock file inside the vault would be committed by the Stop hook,
    pushed to the backup remote and read back by a later session as memory — session bookkeeping
    is not memory, and the vault is not a scratch directory."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    gate_write(world, f, tool="Edit", sid="A")
    assert read_lock(world, f) is not None                              # it does exist somewhere
    assert (Path(str(world["state"])) / "filelocks").is_dir()
    stray = [p for p in world["vault"].rglob("*") if "filelock" in p.name or p.suffix == ".lock"]
    assert stray == [], stray


# ============================ the Concilium exemption from the display-name door (WP3-B) ========

def test_concilium_Index_is_allowed_but_a_role_stem_there_is_still_refused(world):
    """PROVES the exemption and its exact boundary. `Index` is the Concilium-native name of
    `Concilium/<Entity>/Index.md` AND Map's display name, so the display door would refuse the one
    file name the vault requires there. The Concilium STEM RULE is untouched: `Map.md` in the same
    directory is still denied, so the exemption opens one door and not the other."""
    entity = world["vault"] / "Concilium" / "Melchior"
    assert gate_write(world, entity / "Index.md") is None
    res = gate_write(world, entity / "Map.md")
    assert decision(res) == "deny" and "STEM RULE" in reason(res)
    # NEGATIVE control on the scope: outside Concilium the display door still refuses `Index.md`.
    other = gate_write(world, region(world) / "Index.md")
    assert decision(other) == "deny" and "`Map.md`" in reason(other)


# ================================================================= hooks.json wiring ============

def test_hooks_json_is_valid_and_every_command_file_it_names_exists(world):
    """PROVES the wiring, which nothing else does: a hook whose command file is missing fails
    silently in Claude Code — the door simply never runs, and the suite stays green."""
    doc = json.loads((HOOKS / "hooks.json").read_text())
    named = []
    for event, entries in doc["hooks"].items():
        for entry in entries:
            for h in entry["hooks"]:
                cmd = h["command"]
                assert "${CLAUDE_PLUGIN_ROOT}" in cmd, cmd
                script = cmd.split("/hooks/")[1].split()[0]
                assert (HOOKS / script).is_file(), f"{event}: {script} does not exist"
                named.append((event, entry.get("matcher"), script))
    assert ("PostToolUse", "Bash", "chore.py") in named, "the Bash release chore is not wired"
