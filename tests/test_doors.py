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


# =============================== D1's bash half: no whole-file truncation (WP9 gaps 1 and 2) =====
# `rule_bash_no_whole_file_write` in gate.py. Same exemptions as D1, same MODE-INDEPENDENCE (it
# fires whatever `partition.mode` says — no `world["state"]/"partition.mode"` is ever written in
# this section, on purpose): it is a data-loss guard, not a partition rule.

def test_bash_truncate_refuses_a_redirect_over_an_existing_prose_file(world):
    """POSITIVE control, gap 1. PROVES the door fires on the one Bash shape with no anchor at all —
    worse than `Write`, which D1 already catches: a plain `>` carries no compare-and-swap whatsoever."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry another session wrote\n")
    res = bash(world, f"echo 'gone' > {f}")
    assert decision(res) == "deny"
    r = reason(res)
    assert "truncates `Mnemosyne/UkrainianCard/Errata.md`" in r
    assert "Use Edit" in r and "Design §5.2 D1" in r


def test_bash_append_redirect_leaves_the_file_alone(world):
    """NEGATIVE control: `>>` is a pure append, never refused by this rule."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    assert bash(world, f"echo '- more' >> {f}") is None


def test_bash_truncate_exempts_a_file_that_does_not_exist(world):
    """NEGATIVE control: nothing in a file that is not there to lose."""
    assert bash(world, f"echo hi > {region(world) / 'Brand-New.md'}") is None


def test_bash_truncate_exempts_an_existing_but_EMPTY_file(world):
    """NEGATIVE control, tested apart from the new-file case, same reasoning as D1's own."""
    f = region(world) / "Errata.md"
    f.write_text("")
    assert f.exists() and f.stat().st_size == 0
    assert bash(world, f"echo hi > {f}") is None


@pytest.mark.parametrize("name", ["report.html", "verdict.json", "rows.tsv"])
def test_bash_truncate_exempts_files_that_are_not_markdown(world, name):
    """NEGATIVE controls: a generator's own output file is not memory."""
    f = region(world) / name
    f.write_text("existing generated content\n")
    assert bash(world, f"echo hi > {f}") is None, name


def test_bash_truncate_exempts_a_path_outside_the_vault(world, tmp_path):
    """NEGATIVE control: `bash_write_targets` never even sees a non-vault path."""
    f = tmp_path / "scratch.md"
    f.write_text("existing content\n")
    assert bash(world, f"echo hi > {f}") is None


def test_bash_truncate_exempts_a_file_THIS_session_created_and_still_refuses_another_session(world):
    """The discriminating pair — the SAME created-set D1 reads (no second bookkeeping channel): A
    creates the file via Write and may then truncate it with Bash; B, meeting the identical file a
    turn later, may not."""
    f = region(world) / "Position.md"
    assert gate_write(world, f, sid="A") is None            # the creation: gate stamps pre-exists=0
    f.write_text("# Position\n\nfirst content\n")
    chore_write(world, f, sid="A")                          # PostToolUse records the creation
    assert bash(world, f"echo 'rewritten by its author' > {f}", sid="A") is None
    res = bash(world, f"echo 'clobbered by a stranger' > {f}", sid="B")
    assert decision(res) == "deny" and "truncates" in reason(res)


@pytest.mark.parametrize("cmd_tpl", [
    "cp {SRC} {DST}",
    "mv {SRC} {DST}",
    "install {SRC} {DST}",
    "truncate -s 0 {DST}",
])
def test_bash_truncate_covers_cp_mv_install_and_truncate(world, tmp_path, cmd_tpl):
    """POSITIVE controls on the other truncating shapes named in the gap: `cp`/`mv`/`install` ONTO
    an existing non-empty vault `.md`, and `truncate` itself, are refused exactly like `>`."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    src = tmp_path / "src.md"; src.write_text("replacement\n")
    cmd = cmd_tpl.format(SRC=src, DST=f)
    res = bash(world, cmd, sid=f"tester-{cmd_tpl.split()[0]}")
    assert decision(res) == "deny", cmd
    assert "truncates" in reason(res) or "SHARED surface" in reason(res), cmd


def test_bash_truncate_covers_dd_of(world):
    """POSITIVE control: `dd of=` names its target as a `key=value` argument, not a bare token or a
    `>`-spelled redirect — a caller that only recognised the `>` family would miss it entirely."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    res = bash(world, f"dd if=/dev/zero of={f} bs=1 count=1")
    assert decision(res) == "deny" and "truncates" in reason(res)


def test_sed_i_is_deliberately_NOT_covered_by_this_rule(world):
    """NEGATIVE control, and a documented scope decision, not an oversight: `sed -i` does not
    replace a file's whole content by construction (an ordinary substitution leaves the rest of the
    file alone), and a syntactic guess at "does this script empty the file" would either miss real
    wipes or refuse legitimate targeted edits. It stays covered by D2's mutex only."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    assert bash(world, f"sed -i '' 's/an entry/a replaced entry/' {f}") is None


def test_tee_without_a_truncates_but_tee_dash_a_appends(world):
    """The discriminating pair the gap named explicitly: plain `tee` truncates like `>` and is
    refused; `tee -a` is a pure append like `>>` and is allowed."""
    f = region(world) / "Errata.md"
    f.write_text("# Errata\n\n- an entry\n")
    res = bash(world, f"echo 'gone' | tee {f}")
    assert decision(res) == "deny" and "truncates" in reason(res)
    assert bash(world, f"echo '- more' | tee -a {f}") is None


def test_bash_truncate_refuses_the_lanes_OWN_queue_file_and_names_the_append_only_rule(world):
    """POSITIVE control, gap 2: `path_in_partition` says this queue file is CARD's, but a queue row
    grammar is atomic single-Edit appends, never a read-modify-write — even for its own declaring
    lane. A bash `>` has no anchor at all, so it is refused regardless of partition mode, and the
    message names the rule rather than talking about partitions."""
    q = world["vault"] / "Pharos" / "queues" / "regions" / "ukrainian-card.md"
    q.write_text("- [ ] `q:CA-2026-01-01-SEED-1` seed row\n")
    marker = world["repo"] / ".atlas-lane"
    marker.write_text(marker.read_text() + "path: Pharos/queues/regions/ukrainian-card.md\n")
    res = bash(world, f"echo 'clobbered' > {q}")
    assert decision(res) == "deny"
    r = reason(res)
    assert "SHARED surface" in r and "atomic single-Edit appends" in r and "never a read-modify-write" in r


def test_bash_append_to_the_lanes_OWN_queue_file_is_allowed(world):
    """NEGATIVE control, the twin of the case above: `>>` is a pure append, and this rule never
    refuses one, own-lane or not."""
    q = world["vault"] / "Pharos" / "queues" / "regions" / "ukrainian-card.md"
    q.write_text("- [ ] `q:CA-2026-01-01-SEED-1` seed row\n")
    marker = world["repo"] / ".atlas-lane"
    marker.write_text(marker.read_text() + "path: Pharos/queues/regions/ukrainian-card.md\n")
    assert bash(world, f"echo '- [ ] \\`q:CA-2026-01-02-NEW-1\\` new row' >> {q}") is None


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


# ===================================== the two-writer simulator and its control (DESIGN §5.4) ===
# N is 120 per worker here so the pytest run stays in seconds; `SIM_N=500 pytest …` runs the
# design's number. Measured 2026-09-09 at N=500: doors 1000/1000 kept, control 7/1000.

SIM_N = int(os.environ.get("SIM_N", "120"))


def test_two_concurrent_writers_lose_nothing(world, tmp_path):
    """THE PACKET'S CENTRAL CLAIM. Two real processes, the real gate.py and chore.py, one file:
    every entry survives, none is duplicated, and each worker's own entries stay in the order it
    wrote them (a valid interleaving). Then D3's commit carries the whole file.

    This test found the defect it exists to find: the first version of `take_filelock` did a
    read-then-write with no guard, two gates both saw the file free, and 1 of 240 entries was lost
    WITH the doors on. The check-and-set is now under an flock."""
    import sim_two_writers as sim
    res = sim.run_sim(tmp_path / "sim", n=SIM_N, doors=True)
    assert res["errors"] == [], res["errors"]
    assert res["lost"] == 0, f"entries lost WITH the doors on: {res}"
    assert res["kept"] == res["expected"] == 2 * SIM_N
    assert res["duplicated"] == 0 and res["unexpected"] == 0
    assert res["out_of_order"] == 0, "a worker's own entries came out of the order it wrote them"
    # D2 really was contended — a green from an uncontended run would say nothing about the mutex.
    assert sum(w["gate_denied"] for w in res["workers"]) > 0, "no D2 refusal fired: the run never overlapped"
    # D1 really was exercised: the whole-file shape the control uses is refused here.
    assert sum(w["d1_denied"] for w in res["workers"]) == 2
    # D3: both sessions recorded the file, and the commit carries what is on disk.
    assert all(c["has_file"] for c in res["commits"].values()), res["commits"]
    assert res["committed_matches_disk"] and res["committed_lines"] == res["text_lines"]


def test_the_simulator_without_the_doors_DOES_lose_entries(world, tmp_path):
    """THE DISCRIMINATING CONTROL, and it is not optional. Same timing, same jitter, same number of
    appends — only the doors removed and the CAS replaced by the whole-file overwrite from a stale
    read that D1 refuses. If this run ever finishes with nothing lost then the interleaving never
    overlapped, the harness is VACUOUS, and every green above is a fact about the scheduler rather
    than about D1/D2 — so a zero here FAILS the suite instead of passing it."""
    import sim_two_writers as sim
    res = sim.run_sim(tmp_path / "ctl", n=SIM_N, doors=False)
    assert res["errors"] == [], res["errors"]
    assert res["lost"] > 0, ("the no-doors control lost NOTHING — this harness cannot tell the doors "
                             f"from their absence and proves nothing: {res}")


# ================================== the vault protection is DERIVED, not the literal `/Atlas` ===
# Council 2 (Balthasar, closure) found `_PROTECTED` matching the literal `/Atlas`, which left a
# stranger's own vault unprotected by the one rule that must hold everywhere. It derives from
# config.VAULT now; these are the regression tests, because nothing else re-measures a fix.


def strangers_world(tmp_path):
    """A world whose vault is `Gedaechtnis`, with a decoy `Atlas` directory beside it."""
    vault = tmp_path / "Gedaechtnis"
    (vault / "Notes").mkdir(parents=True)
    (tmp_path / "Atlas").mkdir()                       # a stranger's own unrelated folder
    (vault / "Global").mkdir()
    (vault / "Global" / "fleet-roster.md").write_text("```fleet-roster\nlane: X\nrepo: repo\n```\n")
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: X\npath: Notes/\n")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, env=env, tmp=tmp_path)


def test_a_strangers_vault_is_protected_exactly_like_the_owners(tmp_path):
    """POSITIVE control. `rm -rf <tmp>/Gedaechtnis` must be refused when that IS the vault — the
    rule is about the vault, not about a directory called Atlas."""
    w = strangers_world(tmp_path)
    res = run("gate.py", "bash", {"cwd": str(w["repo"]), "tool_name": "Bash", "session_id": "A",
                                  "tool_input": {"command": f"rm -rf {w['vault']}"}}, w["env"])
    assert decision(res) == "ask"                      # this class refuses AND asks: the owner may say yes
    assert "the vault and its history" in reason(res)


def test_a_directory_called_Atlas_that_is_NOT_the_vault_is_not_flagged(tmp_path):
    """NEGATIVE control, and the half that proves the rule DERIVES rather than merely matching one
    more name: with `protect_everywhere` off, a stranger's own `Atlas` folder is theirs to delete.
    A door still carrying the literal would refuse this and be unable to say why."""
    w = strangers_world(tmp_path)
    res = run("gate.py", "bash", {"cwd": str(w["repo"]), "tool_name": "Bash", "session_id": "A",
                                  "tool_input": {"command": f"rm -rf {w['tmp']}/Atlas"}}, w["env"])
    assert res is None, reason(res)


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
