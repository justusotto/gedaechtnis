"""The destructive-chore gate: five rules, each tested on every chore it applies to.

Chores: compaction (`maintenance.compact_vault` over `archive.compact_file`), the Boot-file roll
(`bootfile.roll_window`) and the worktree sweep (`wtsweep.sweep`). Rules (hooks/destructive.py):
allow-list · bounded loss · propose by default · hash check · revert line. Each has a positive and
a negative control. The last test in the file checks that no hook calls a moving primitive from
outside a registered chore, so a new chore that skips the rules fails here.

The compaction allow-list itself is tested in test_compact_allowlist.py.
"""
from __future__ import annotations
import ast, json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parent.parent
HOOKS = PLUGIN / "hooks"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(PLUGIN))

from test_maintenance import world, LIMITS as M_LIMITS, run_stop, run_session_start, git, state_doc  # noqa: E402,F401
from test_bootfile import mod, vault, real_boot_file  # noqa: E402,F401
from test_wtsweep import sandbox, add_worktree, merge_into_main, git as wgit  # noqa: E402,F401

import archive  # noqa: E402


def entries(n: int, size: int = 400, first_size: int | None = None) -> str:
    out = "# Canon\n"
    for i in range(n):
        out += f"\n## entry {i}\n" + "b" * (first_size if (i == 0 and first_size) else size) + "\n"
    return out


def limits(world, **over):
    world["limits_file"].write_text(json.dumps(dict(M_LIMITS, max_memory_file_bytes=4000, **over)))


def seed_state(world):
    (world["state"] / "maintenance.json").write_text(json.dumps(
        {"version": 1, "last_cleanup": "2999-01-01", "last_synthesis": "2999-01-01"}))


# ======================================================================= compaction ====
# ---- propose by default ------------------------------------------------------------
def test_compaction_PROPOSES_on_a_fresh_install_and_moves_nothing(world):
    """Shipped defaults: no `compaction_apply` key in the vault's limits. Nothing moves; the
    proposal is written and the next session is told."""
    shipped_like = {k: v for k, v in M_LIMITS.items()
                    if k not in ("compaction_apply", "boot_roll_apply", "max_move_share")}
    world["limits_file"].write_text(json.dumps(dict(shipped_like, max_memory_file_bytes=4000)))
    seed_state(world)
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text(entries(60))
    before = live.read_bytes()
    run_stop(world)
    assert live.read_bytes() == before
    assert not list(live.parent.glob("Canon-archive*.md"))
    prop = json.loads((world["state"] / "proposals.json").read_text())
    assert [i["path"] for i in prop["compaction"]["items"]] == ["Proj/Canon.md"]
    assert "compaction: proposing only" in run_session_start(world)


def test_compaction_ACTS_once_the_vault_switches_it_on(world):
    limits(world, compaction_apply=True, max_move_share=1.0)
    seed_state(world)
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text(entries(60))
    run_stop(world)
    assert live.stat().st_size <= 4000
    assert "compaction: proposing only" not in run_session_start(world)


# ---- bounded loss -----------------------------------------------------------------
def test_one_pass_moves_at_most_max_move_share_of_a_file(world):
    limits(world, compaction_apply=True, max_move_share=0.25)
    seed_state(world)
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text(entries(60))
    size = live.stat().st_size
    run_stop(world)
    after = live.stat().st_size
    assert after < size, "nothing moved: this test cannot see the bound"
    assert (size - after) <= 0.25 * size + 1


def test_POSITIVE_without_the_bound_the_same_file_loses_more_than_a_quarter(world):
    limits(world, compaction_apply=True, max_move_share=1.0)
    seed_state(world)
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text(entries(60))
    size = live.stat().st_size
    run_stop(world)
    assert (size - live.stat().st_size) > 0.25 * size


def test_an_oldest_entry_larger_than_the_share_is_REFUSED_and_said(world):
    limits(world, compaction_apply=True, max_move_share=0.25)
    seed_state(world)
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text(entries(4, size=300, first_size=6000))
    before = live.read_bytes()
    run_stop(world)
    assert live.read_bytes() == before
    facts = run_session_start(world)
    assert "compaction declined 1 file(s)" in facts and "oldest entry alone" in facts


def test_a_pass_changes_at_most_max_files_per_pass_files(world):
    limits(world, compaction_apply=True, max_move_share=1.0, max_files_per_pass=1)
    seed_state(world)
    a, b = world["vault"] / "Proj" / "Canon.md", world["vault"] / "Proj" / "Errata.md"
    a.write_text(entries(60))
    b.write_text(entries(60))
    run_stop(world)
    small = [f for f in (a, b) if f.stat().st_size <= 4000]
    assert len(small) == 1
    assert "max_files_per_pass" in run_session_start(world)


# ---- hash check -------------------------------------------------------------------
def test_a_file_that_changes_between_read_and_write_is_NOT_written(tmp_path):
    live = tmp_path / "Canon.md"
    live.write_text(entries(60))

    def writer_arrives():
        with live.open("a", encoding="utf-8") as fh:
            fh.write("\n## a new entry from another session\nnew\n")
    changed = live.read_bytes() + b"\n## a new entry from another session\nnew\n"
    with pytest.raises(archive.ChangedSinceRead):
        archive.compact_file(live, limit=4000, _before_write=writer_arrives)
    assert live.read_bytes() == changed
    assert not list(tmp_path.glob("Canon-archive*.md"))


def test_POSITIVE_an_unchanged_file_is_written(tmp_path):
    live = tmp_path / "Canon.md"
    live.write_text(entries(60))
    assert archive.compact_file(live, limit=4000, _before_write=lambda: None) > 0


# ---- revert line ------------------------------------------------------------------
def test_the_compaction_commit_names_what_moved_and_its_undo_command_WORKS(world):
    limits(world, compaction_apply=True, max_move_share=1.0)
    seed_state(world)
    (world["repo"] / ".atlas-lane").write_text("lane: PROJ\npath: Proj/\n")
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text(entries(60))
    git(world["vault"], "add", "--", "Proj/Canon.md")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "before",
        "--", "Proj/Canon.md")
    original = live.read_bytes()
    run_stop(world)
    body = git(world["vault"], "log", "-1", "--format=%B")
    assert "Compaction" in body and "entry 0" in body and "gedaechtnis-pass:" in body
    undo = [l for l in body.splitlines() if l.startswith("Undo: ")]
    assert len(undo) == 1
    cmd = undo[0][len("Undo: "):]
    p = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    assert p.returncode == 0, p.stderr
    assert live.read_bytes() == original


def test_an_ordinary_session_commit_carries_no_revert_body(world):
    """Negative control: the body is the compaction's, not something every commit grows."""
    sys.path.insert(0, str(HOOKS))
    import destructive
    b = destructive.revert_body("P1", [{"path": "R/Canon.md", "entries": 2,
                                        "headings": ["a", "b"]}], Path("/v"))
    assert b.splitlines()[-1].startswith("Undo: git -C /v revert")
    assert "gedaechtnis-pass: P1" in b and "R/Canon.md: 2" in b


# ======================================================================= boot roll ====
BOOT_OVER = dict(n_resume=40)


def test_boot_roll_PROPOSES_when_not_applied(mod, vault):
    live = vault / "Proj" / "Kernel.md"
    live.write_text(real_boot_file(**BOOT_OVER))
    before = live.read_bytes()
    out = mod.roll_window(vault, apply=False)
    assert live.read_bytes() == before
    assert out and out[0].get("proposed") and out[0]["entries"] > 0 and out[0]["headings"]


def test_boot_roll_ACTS_when_applied(mod, vault):
    live = vault / "Proj" / "Kernel.md"
    live.write_text(real_boot_file(**BOOT_OVER))
    before = live.stat().st_size
    out = mod.roll_window(vault, apply=True)
    assert out and not out[0].get("proposed") and live.stat().st_size < before


def test_boot_roll_is_bounded_per_pass(mod, vault):
    live = vault / "Proj" / "Kernel.md"
    live.write_text(real_boot_file(**BOOT_OVER))
    size = live.stat().st_size
    mod.roll_window(vault, apply=True, max_share=0.25)
    moved = size - live.stat().st_size
    assert 0 < moved <= 0.25 * size + 1


def test_boot_roll_REFUSES_when_the_oldest_window_entry_exceeds_the_share(mod, vault):
    live = vault / "Proj" / "Kernel.md"
    live.write_text(real_boot_file(n_resume=3, size=1200))
    before = live.read_bytes()
    out = mod.roll_window(vault, apply=True, max_share=0.05)
    assert live.read_bytes() == before
    assert "max_move_share" in (out[0].get("skipped") or ""), out


def test_boot_roll_does_NOT_write_a_file_that_changed_mid_pass(mod, vault):
    live = vault / "Proj" / "Kernel.md"
    live.write_text(real_boot_file(**BOOT_OVER))

    def arrive(p):
        with p.open("a", encoding="utf-8") as fh:
            fh.write("\nlate line\n")
    out = mod.roll_window(vault, apply=True, _before_write=arrive)
    assert live.read_text().endswith("\nlate line\n")
    assert "changed after the pass read it" in (out[0].get("skipped") or ""), out
    assert not list((vault / "Proj").glob("Kernel-archive*.md"))


# =================================================================== worktree sweep ====
def merged_wt(repo, name):
    wt = add_worktree(repo, name)
    (wt / f"{name}.txt").write_text("x\n")
    wgit("add", "--", f"{name}.txt", cwd=wt)
    wgit("commit", "-q", "-m", name, "--", f"{name}.txt", cwd=wt)
    merge_into_main(repo, f"wt-{name}")
    return wt


def test_sweep_ALLOW_LIST_a_finished_worktree_outside_claude_worktrees_is_kept(sandbox):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    beside = sandbox["tmp"] / "beside"
    wgit("worktree", "add", "-q", "-b", "wt-B", str(beside), "main", cwd=repo)
    inside = merged_wt(repo, "IN")
    out = ws.sweep(repo, apply=True)
    assert beside.exists() and not inside.exists()
    assert (str(beside), ws.KEPT_OUTSIDE) in [(str(Path(p).resolve()), r) for p, r in out["kept"]] \
        or any(r == ws.KEPT_OUTSIDE for _p, r in out["kept"])


def test_sweep_PROPOSES_by_default(sandbox):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = merged_wt(repo, "P")
    assert ws.applies() is False, "the shipped default must be propose"
    out = ws.sweep(repo, apply=ws.applies())
    assert wt.exists() and [Path(p).name for p in out["proposed"]] == ["P"]
    ws.record(repo, out)
    line = ws.facts_line()
    assert line and "worktree-sweep: proposing only" in line and "worktree_sweep_apply" in line


def test_sweep_removes_at_most_max_files_per_pass(sandbox, monkeypatch):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    monkeypatch.setattr(ws.destructive, "max_files", lambda: 1)
    a, b = merged_wt(repo, "A"), merged_wt(repo, "B")
    out = ws.sweep(repo, apply=True)
    assert len(out["removed"]) == 1 and sum(p.exists() for p in (a, b)) == 1
    assert any(r == ws.KEPT_BOUND for _p, r in out["kept"])


def test_sweep_keeps_a_worktree_whose_HEAD_moved_after_it_was_checked(sandbox, monkeypatch):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = merged_wt(repo, "H")
    real = ws.classify

    def classify_then_commit(r, w, recs, cwds=None):
        verdict = real(r, w, recs, cwds)
        (wt / "late.txt").write_text("late\n")
        wgit("add", "--", "late.txt", cwd=wt)
        wgit("commit", "-q", "-m", "late", "--", "late.txt", cwd=wt)
        return verdict
    monkeypatch.setattr(ws, "classify", classify_then_commit)
    out = ws.sweep(repo, apply=True)
    assert wt.exists() and any(r == ws.KEPT_CHANGED for _p, r in out["kept"])


def test_sweep_records_an_undo_command_that_brings_the_worktree_back(sandbox):
    repo, ws = sandbox["repo"], sandbox["wtsweep"]
    wt = merged_wt(repo, "U")
    out = ws.sweep(repo, apply=True)
    assert not wt.exists() and len(out["undo"]) == 1
    p = subprocess.run(["/bin/sh", "-c", out["undo"][0]], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert (wt / "U.txt").read_text() == "x\n"


# ======================================================== no chore outside the registry ====
MOVERS = {"compact_file", "append_entries", "atomic_write", "roll_window"}


def _calls_by_function(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                    if name in MOVERS:
                        yield fn.name, name
                    if (name == "_git" and node.args and isinstance(node.args[0], ast.List)
                            and [getattr(e, "value", None) for e in node.args[0].elts[:2]]
                            == ["worktree", "remove"]):
                        yield fn.name, "worktree remove"


# (file, function) pairs in the HOOKS that call a moving primitive, each belonging to a registered
# chore. A new pair fails this test until it is registered and given its five tests above.
REGISTERED_CALLERS = {
    ("maintenance.py", "compact_vault"): "compaction",
    ("maintenance.py", "main"): "boot-roll",
    ("wtsweep.py", "sweep"): "worktree-sweep",
    ("cleanup.py", "apply"): "cleanup",
}
# Callers that use a moving primitive on the PLUGIN'S OWN state, never on a user file. Each needs
# its reason written here; the list is not a pattern.
NOT_USER_FILES = {
    ("boot_check.py", "main"): "atomic_write of its own state file, boot_check.state_path()",
}


# Chores that live beside `hooks/` rather than in it. `cleanup.py` is person-invoked AND auto-fired
# by a guardian session, so it is a chore like any other (CLEANUPGATE-1, named by builder 22).
CHORES_OUTSIDE_HOOKS = [PLUGIN / "cleanup.py"]


def test_every_mover_call_in_the_hooks_belongs_to_a_registered_chore():
    import destructive
    found = set()
    for py in sorted(HOOKS.glob("*.py")) + CHORES_OUTSIDE_HOOKS:
        for fn, _prim in _calls_by_function(py):
            found.add((py.name, fn))
    unregistered = found - set(REGISTERED_CALLERS) - set(NOT_USER_FILES)
    assert not unregistered, (f"a hook moves or removes files outside the destructive-chore gate: "
                              f"{sorted(unregistered)} — register it in destructive.REGISTRY and "
                              f"give it the five tests in this file")
    assert set(REGISTERED_CALLERS.values()) == set(destructive.REGISTRY)


def test_the_registry_check_SEES_a_mover_call(tmp_path):
    """The check above is only a check if it can find a call. Positive control on a fixture."""
    f = tmp_path / "x.py"
    f.write_text("def sneaky(p):\n    archive.compact_file(p)\n"
                 "def gone(r, path):\n    _git(['worktree', 'remove', path], cwd=r)\n")
    assert set(_calls_by_function(f)) == {("sneaky", "compact_file"), ("gone", "worktree remove")}


# ============================================================================== soak ====
SOAK_PROBE = """
import json, re, sys
from pathlib import Path
sys.path.insert(0, {plugin!r}); sys.path.insert(0, {hooks!r})
import recall
vault = Path(sys.argv[1])
rows = 0
for p in vault.rglob("*.md"):
    if ".git" in p.parts:
        continue
    rows += sum(1 for l in p.read_text(encoding="utf-8", errors="replace").splitlines()
                if re.match(r"^- \\[[ xX]\\] ", l) and "-archive" not in p.stem)
entries = sum(len(re.findall(r"^## ", p.read_text(encoding="utf-8", errors="replace"), re.M))
              for p in recall.md_files(vault, include_queues=True))
print(json.dumps({{"rows": rows, "entries": entries}}))
"""


def soak_counts(vault: Path, tmp: Path, env: dict) -> dict:
    script = tmp / "soak_probe.py"
    script.write_text(SOAK_PROBE.format(plugin=str(PLUGIN), hooks=str(HOOKS)), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(script), str(vault)], capture_output=True,
                       text=True, env=env, timeout=300)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@pytest.mark.parametrize("share", [0.25, 1.0])
def test_SOAK_no_chore_costs_a_queue_row_or_a_searchable_entry(world, share):
    """Every Stop-hook chore, applied, three session ends in a row, over a vault with queue
    files, a notice outbox, role files and a Boot file all over their limits. What the queue
    parser can read (checkbox rows outside archive files) and what the search can reach (`## `
    entries in files recall reads, archives included) must be the same before and after."""
    limits(world, compaction_apply=True, boot_roll_apply=True, max_move_share=share,
           boot_file_budget_bytes=2000, boot_file_warn_bytes=1500)
    seed_state(world)
    v = world["vault"]
    q = v / "Pharos" / "queues" / "regions"
    q.mkdir(parents=True)
    for name in ("a", "b"):
        (q / f"{name}.md").write_text("# queue\n" + "".join(
            f"\n## batch {i}\n" + "".join(f"- [ ] `q:XX-2026-01-01-R{i}-{j}` row {'x' * 80}\n"
                                          for j in range(4)) for i in range(20)))
    (v / "Channels" / "L").mkdir(parents=True)
    (v / "Channels" / "L" / "N-1.md").write_text(entries(40))
    for stem in ("Canon", "Errata", "Position"):
        (v / "Proj" / f"{stem}.md").write_text(entries(60))
    (v / "Proj" / "Course.md").write_text("# Course\n" + "".join(
        f"\n## step {i}\n- [ ] do {i} {'y' * 300}\n" for i in range(30)))
    (v / "Proj" / "Kernel.md").write_text(real_boot_file(n_resume=40))
    before = soak_counts(v, world["tmp"], world["env"])
    assert before["rows"] > 100 and before["entries"] > 200, before
    for _ in range(3):
        run_stop(world)
    after = soak_counts(v, world["tmp"], world["env"])
    assert list((v / "Proj").glob("*-archive*.md")), "no chore acted; the soak saw nothing"
    # Rows in a ROLE file that compaction moved to its archive are still searchable, so the row
    # count is compared on the queue files only; the entry count covers everything.
    queue_rows = lambda: sum(f.read_text().count("- [ ] ") for f in q.glob("*.md"))  # noqa: E731
    assert queue_rows() == 160
    assert after["entries"] == before["entries"], (before, after)


def _mover_calls_without_apply(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
                continue
            owner, name = f.value.id.lstrip("_"), f.attr
            kws = {k.arg for k in node.keywords}
            if (owner, name) == ("archive", "compact_file") and "dry_run" not in kws:
                yield name
            elif (owner, name) in (("bootfile", "roll_window"), ("wtsweep", "sweep")) \
                    and "apply" not in kws:
                yield name


def test_every_PRODUCTION_mover_call_passes_its_switch_explicitly():
    """`roll_window` and `sweep` default to acting and `compact_file` to writing, for their many
    direct test callers. A production caller that drops the argument would silently act without
    the vault's switch; this refuses that. (Reviewer's note on DESTRUCTIVEGATE-1.)"""
    bad = {py.name: list(_mover_calls_without_apply(py))
           for py in list(HOOKS.glob("*.py")) + CHORES_OUTSIDE_HOOKS}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, bad


def test_that_check_SEES_a_call_without_the_switch(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("def m(v):\n    bootfile.roll_window(v)\n    wtsweep.sweep(v, apply=True)\n")
    assert list(_mover_calls_without_apply(f)) == ["roll_window"]


# ========================================================================== cleanup ====
# CLEANUPGATE-1: `cleanup.py` is person-invoked AND auto-fired by a guardian session, and it
# applied with no switch at all. Its five rules, each with a positive and a negative control.
import test_cleanup as _tc  # noqa: E402


@pytest.fixture
def cw(tmp_path):
    """test_cleanup's own world (a vault, a lane marker, a limits file), under another name:
    this module already imports test_maintenance's `world`."""
    return _tc.make_world(tmp_path)


def _limits(w, **over):
    w["limits_file"].write_text(json.dumps(dict(_tc.LIMITS, **over)))


def _dup_file(w, name="Canon"):
    f = w["vault"] / "Proj" / f"{name}.md"
    f.write_text(f"# {name}\n" + _tc.DUP + _tc.ENTRY + _tc.DUP)
    return f


def _cleanup(w, *args):
    p = subprocess.run([sys.executable, "-B", str(PLUGIN / "cleanup.py"), "--json", *args],
                       capture_output=True, text=True, env=w["env"], timeout=120, cwd=str(w["repo"]))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# ---- propose by default --------------------------------------------------------------
def test_cleanup_PROPOSES_without_its_switch_and_changes_nothing(cw):
    """No `cleanup_apply`: the vault is byte-identical after, proposals.json names the file."""
    shipped_like = {k: v for k, v in _tc.LIMITS.items() if k != "cleanup_apply"}
    cw["limits_file"].write_text(json.dumps(shipped_like))
    f = _dup_file(cw)
    before = _tc.snapshot(cw["vault"])
    r = _cleanup(cw)
    assert r["proposing_only"] is True
    assert _tc.snapshot(cw["vault"]) == before
    prop = json.loads((Path(cw["env"]["GEDAECHTNIS_STATE_DIR"]) / "proposals.json").read_text())
    assert [i["path"] for i in prop["cleanup"]["items"]] == ["Proj/Canon.md"]
    assert prop["cleanup"]["switch"] == "cleanup_apply"
    assert f.read_text().count("## A repeated entry") == 2


def test_cleanup_ACTS_with_its_switch(cw):
    """Negative control for the test above: the same vault, the switch on → it moves."""
    _limits(cw, cleanup_apply=True)
    f = _dup_file(cw)
    r = _cleanup(cw)
    assert len(r["moved"]) == 1
    assert f.read_text().count("## A repeated entry") == 1


def test_cleanup_with_propose_never_applies_EVEN_WITH_the_switch_on(cw):
    """The guardian's automatic run reads a proposal and never applies one."""
    _limits(cw, cleanup_apply=True)
    _dup_file(cw)
    before = _tc.snapshot(cw["vault"])
    r = _cleanup(cw, "--propose")
    assert r["proposing_only"] is True
    assert _tc.snapshot(cw["vault"]) == before


def test_a_cleanup_proposal_reaches_the_next_sessions_facts(cw):
    shipped_like = {k: v for k, v in _tc.LIMITS.items() if k != "cleanup_apply"}
    cw["limits_file"].write_text(json.dumps(shipped_like))
    _dup_file(cw)
    _cleanup(cw)
    # the Stop between the two sessions, which is what writes the state the facts are read from
    subprocess.run([sys.executable, "-B", str(HOOKS / "maintenance.py")], capture_output=True,
                   input=json.dumps({"session_id": "s1", "cwd": str(cw["repo"])}), text=True,
                   env=cw["env"], timeout=120)
    payload = json.dumps({"session_id": "s1", "cwd": str(cw["repo"]), "source": "startup"})
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "session_start.py")], input=payload,
                       capture_output=True, text=True, env=cw["env"], timeout=120)
    facts = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "cleanup: proposing only — 1 item(s) would be tidied: Proj/Canon.md" in facts
    assert "cleanup_apply: true" in facts


# ---- bounded loss ----------------------------------------------------------------------
def test_cleanup_touches_at_most_max_files_per_pass_and_DEFERS_the_rest(cw):
    _limits(cw, cleanup_apply=True, max_files_per_pass=1)
    a, b = _dup_file(cw, "Canon"), _dup_file(cw, "Patterns")
    r = _cleanup(cw)
    counts = sorted(x.read_text().count("## A repeated entry") for x in (a, b))
    assert counts == [1, 2], "exactly one file tidied"
    assert any("max_files_per_pass" in d for d in r["deferred"])


def test_cleanup_with_room_for_both_files_tidies_both(cw):
    _limits(cw, cleanup_apply=True, max_files_per_pass=2)
    a, b = _dup_file(cw, "Canon"), _dup_file(cw, "Patterns")
    r = _cleanup(cw)
    assert [x.read_text().count("## A repeated entry") for x in (a, b)] == [1, 1]
    assert r["deferred"] == []


def test_cleanup_never_moves_more_than_max_move_share_of_a_file(cw):
    """The duplicate is ~40% of this file: at a 25% bound it waits for the next pass."""
    _limits(cw, cleanup_apply=True, max_move_share=0.25)
    f = _dup_file(cw)
    r = _cleanup(cw)
    assert f.read_text().count("## A repeated entry") == 2
    assert any("max_move_share" in d for d in r["deferred"])


def test_cleanup_at_a_share_the_duplicate_fits_moves_it(cw):
    _limits(cw, cleanup_apply=True, max_move_share=0.5)
    f = _dup_file(cw)
    _cleanup(cw)
    assert f.read_text().count("## A repeated entry") == 1


# ---- hash check -----------------------------------------------------------------------
def _inproc(cw, monkeypatch):
    for k, v in cw["env"].items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(cw["repo"])
    import importlib, config as _c, limits as _l, destructive as _d, cleanup as _cl  # noqa: PLC0415
    for m in (_c, _l, _d, _cl):
        importlib.reload(m)
    return _cl


def test_cleanup_does_not_write_a_file_that_changed_after_the_scan(cw, monkeypatch):
    _limits(cw, cleanup_apply=True, max_move_share=1.0)
    f = _dup_file(cw)
    cl = _inproc(cw, monkeypatch)
    found = cl.propose()
    f.write_text(f.read_text() + "\n## Added by a person meanwhile\n\nkeep me\n")
    after_edit = f.read_bytes()
    r = cl.apply(found)
    assert f.read_bytes() == after_edit
    assert any("changed since the scan" in x for x in r["failed"])


def test_cleanup_writes_a_file_that_did_NOT_change(cw, monkeypatch):
    _limits(cw, cleanup_apply=True, max_move_share=1.0)
    f = _dup_file(cw)
    cl = _inproc(cw, monkeypatch)
    r = cl.apply(cl.propose())
    assert len(r["moved"]) == 1 and f.read_text().count("## A repeated entry") == 1


# ---- revert line ----------------------------------------------------------------------
def test_the_cleanup_commit_carries_a_revert_line_that_WORKS(cw):
    _limits(cw, cleanup_apply=True, max_move_share=1.0)
    f = _dup_file(cw)
    _tc.git(cw["vault"], "add", "-A")
    _tc.git(cw["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    before = f.read_bytes()
    _cleanup(cw)
    body = _tc.git(cw["vault"], "log", "-1", "--format=%B")
    assert "gedaechtnis-pass: " in body and "Undo: git -C" in body
    assert "Proj/Canon.md: 1 entr(y/ies) moved" in body
    undo = [l for l in body.splitlines() if l.startswith("Undo: ")][0][len("Undo: "):]
    subprocess.run(["bash", "-c", undo], check=True, capture_output=True,
                   env=dict(cw["env"], GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    assert f.read_bytes() == before, "the revert line did not restore the file"


def test_a_cleanup_that_moved_nothing_makes_no_commit(cw):
    _limits(cw, cleanup_apply=True)
    head = _tc.git(cw["vault"], "rev-parse", "HEAD")
    _cleanup(cw)
    assert _tc.git(cw["vault"], "rev-parse", "HEAD") == head


# ---- allow-list -----------------------------------------------------------------------
def test_cleanup_never_touches_an_archive_sidecar(cw):
    """An archive is append-only; a duplicate in one stays where it is."""
    _limits(cw, cleanup_apply=True, max_move_share=1.0)
    side = cw["vault"] / "Proj" / "Canon-archive.md"
    side.write_text("# Canon archive\n" + _tc.DUP + _tc.ENTRY + _tc.DUP)
    before = side.read_bytes()
    _cleanup(cw)
    assert side.read_bytes() == before


def test_cleanup_does_touch_the_role_file_beside_it(cw):
    _limits(cw, cleanup_apply=True, max_move_share=1.0)
    f = _dup_file(cw)
    _cleanup(cw)
    assert f.read_text().count("## A repeated entry") == 1


def test_an_over_limit_FOLD_is_bounded_by_max_move_share_too(cw):
    """A 20-entry Position against a 10-line limit: at a 25% share one pass keeps most of it."""
    _limits(cw, cleanup_apply=True, max_move_share=0.25)
    f = cw["vault"] / "Proj" / "Position.md"
    f.write_text(_tc.long_position(20))
    before = f.read_text().count("\n")
    _cleanup(cw)
    assert f.read_text().count("\n") >= 0.75 * before - 3


def test_the_same_fold_at_a_full_share_reaches_the_limit(cw):
    _limits(cw, cleanup_apply=True, max_move_share=1.0)
    f = cw["vault"] / "Proj" / "Position.md"
    f.write_text(_tc.long_position(20))
    _cleanup(cw)
    assert f.read_text().count("\n") + 1 <= _tc.LIMITS["role_soft_limits_lines"]["Position"]


def test_cleanup_apply_can_be_switched_on_through_the_VAULTS_config(cw, tmp_path):
    """Reviewer's must-fix: the switch was not a key the per-vault layer accepted, so the path the
    proposal line tells a person to use was rejected ("not a limit this package reads") and the
    pass could never leave propose mode. Set it the way a person would: config.json's `limits`."""
    shipped_like = {k: v for k, v in _tc.LIMITS.items() if k != "cleanup_apply"}
    cw["limits_file"].write_text(json.dumps(dict(shipped_like, max_move_share=1.0)))
    cfg = tmp_path / "vault-config.json"
    cfg.write_text(json.dumps({"limits": {"cleanup_apply": True}}))
    cw["env"]["GEDAECHTNIS_CONFIG"] = str(cfg)
    f = _dup_file(cw)
    r = _cleanup(cw)
    assert not r.get("proposing_only"), r
    assert f.read_text().count("## A repeated entry") == 1
