"""Tests for the maintenance trigger hook — every arm with a positive AND a negative control.

Same world as `test_commit.py` and `test_subagent_stop.py`: a git repository in tmp_path pointed at
by GEDAECHTNIS_VAULT, the state directory and the limits file redirected, so the suite never reads
or writes a real vault, a real state dir or the shipped `rules/limits.json`.

**Why the thresholds are scaled down in the fixture.** Reaching the shipped volume threshold
honestly costs 301 git commits per assertion. The suite instead points `GEDAECHTNIS_LIMITS` at a
small file and proves the MECHANISM — that the arm fires at its threshold, stays quiet below it,
and that the bookkeeping exclusion is not vacuous (the quiet case carries 50 bookkeeping commits
that must not count). What the shipped file actually says is pinned separately, by
`test_limits_file_matches_defaults`, so neither half can drift without a red test.

The negative controls are the load-bearing half throughout. A trigger that fires is easy; the
failure this hook exists to end is the opposite one — a vault where nothing ever fires, silently,
because the state it read did not exist.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
PLUGIN_ROOT = PLUGIN


def git(vault: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=True)
    return p.stdout


def commit(vault: Path, subject: str, path: str = "Proj/Position.md") -> None:
    f = vault / path
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(subject + "\n")
    git(vault, "add", "--", path)
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", subject,
        "--", path)


def days_ago(n: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))


LIMITS = {
    "boot_budget_bytes": 20000,
    "role_soft_limits_lines": {"Position": 10, "Canon": 800},
    "cleanup_trigger_days": 14,
    "cleanup_trigger_commits": 3,
    "synthesis_trigger_days": 14,
    "synthesis_trigger_entries": 5,
}


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Proj").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    (vault / "Proj" / "Map.md").write_text("# Proj\n")          # what makes Proj a REGION
    (vault / "Proj" / "Position.md").write_text("start\n")
    (vault / "Proj" / "Errata.md").write_text("# Errata\n")
    (vault / "Proj" / "Patterns.md").write_text("# Patterns\n")
    git(vault, "add", "-A")
    # BACKDATED, deliberately. A vault whose own first commit falls inside the test's window is a
    # vault with no history to be new relative to — every arm reads differently there, and that
    # world has its own test (`test_a_young_vault_reports_no_topology_events`) rather than being
    # the accidental shape of every other one.
    old_env = dict(os.environ, GIT_AUTHOR_DATE=f"{days_ago(400)} 12:00:00",
                   GIT_COMMITTER_DATE=f"{days_ago(400)} 12:00:00")
    subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "root"], env=old_env, check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# project\n")
    state = tmp_path / "state"
    state.mkdir()
    limits_file = tmp_path / "limits.json"
    limits_file.write_text(json.dumps(LIMITS))
    env = dict(os.environ,
               GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_LIMITS=str(limits_file),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-such-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, state=state, env=env, tmp=tmp_path,
                limits_file=limits_file)


def run_stop(w, cwd=None) -> subprocess.CompletedProcess:
    payload = json.dumps({"session_id": "s1", "cwd": str(cwd or w["repo"])})
    return subprocess.run([sys.executable, "-B", str(HOOKS / "maintenance.py")],
                          input=payload, capture_output=True, text=True, env=w["env"], timeout=120)


def run_session_start(w) -> str:
    payload = json.dumps({"session_id": "s1", "cwd": str(w["repo"]), "source": "startup"})
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "session_start.py")],
                       input=payload, capture_output=True, text=True, env=w["env"], timeout=120)
    # The hook emits ONE JSON envelope whose additionalContext is a single string with escaped
    # newlines. Asserting against the raw stdout would match the operating-rules prose the same
    # block carries — which is how the first draft of the "no advice" test passed on a word that
    # was never in the facts line at all.
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def state_doc(w) -> dict:
    return json.loads((w["state"] / "maintenance.json").read_text(encoding="utf-8"))


def set_last(w, cleanup=None, synthesis=None) -> None:
    """Seed the state as a previous run would have left it, then let the hook evaluate it."""
    today = time.strftime("%Y-%m-%d")
    doc = {"version": 1, "created": today,
           "last_cleanup": cleanup or today, "last_synthesis": synthesis or today}
    (w["state"] / "maintenance.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ first run ----
def test_first_run_creates_state_and_fires_nothing(world):
    """A fresh install must not open by telling its user their empty vault is untidy."""
    assert not (world["state"] / "maintenance.json").exists()
    run_stop(world)
    doc = state_doc(world)
    today = time.strftime("%Y-%m-%d")
    assert doc["last_cleanup"] == today and doc["last_synthesis"] == today
    assert "cleanup" not in doc and "synthesis" not in doc     # nothing was EVALUATED, not merely quiet


def test_second_run_same_day_does_not_rewrite(world):
    run_stop(world)
    first = (world["state"] / "maintenance.json").read_text()
    mtime = (world["state"] / "maintenance.json").stat().st_mtime
    time.sleep(0.01)
    run_stop(world)
    doc = state_doc(world)
    assert doc["cleanup"]["due"] is False and doc["synthesis"]["due"] is False
    # the second run DOES evaluate (the state now exists) but must not report anything due
    assert first != (world["state"] / "maintenance.json").read_text()   # arms were added once
    third = (world["state"] / "maintenance.json").read_text()
    run_stop(world)
    assert (world["state"] / "maintenance.json").read_text() == third   # and then it is stable


# ------------------------------------------------------------- cleanup: time ----
def test_time_arm_fires_at_fifteen_days(world):
    set_last(world, cleanup=days_ago(15))
    run_stop(world)
    doc = state_doc(world)
    assert doc["cleanup"]["arms"]["time"]["fired"] is True
    assert doc["cleanup"]["due"] is True


def test_time_arm_quiet_at_thirteen_days(world):
    set_last(world, cleanup=days_ago(13))
    run_stop(world)
    doc = state_doc(world)
    assert doc["cleanup"]["arms"]["time"]["fired"] is False
    assert doc["cleanup"]["due"] is False


# ----------------------------------------------------------- cleanup: volume ----
def test_volume_arm_fires_above_threshold(world):
    set_last(world, cleanup=days_ago(1))
    for i in range(4):                                   # threshold 3
        commit(world["vault"], f"real content {i}")
    run_stop(world)
    arm = state_doc(world)["cleanup"]["arms"]["volume"]
    assert arm["fired"] is True and arm["commits"] >= 4


def test_volume_arm_quiet_below_threshold_and_the_exclusion_is_not_vacuous(world):
    """Two substantive commits and FIFTY bookkeeping ones. If the exclusion did nothing, this
    fires; it is the only shape that can tell a working filter from an absent one."""
    set_last(world, cleanup=days_ago(1))
    commit(world["vault"], "real content A")
    commit(world["vault"], "real content B")
    for i in range(50):
        commit(world["vault"], f"session-end auto-commit {i}")
    run_stop(world)
    arm = state_doc(world)["cleanup"]["arms"]["volume"]
    assert arm["fired"] is False
    assert arm["commits"] == 2, arm
    assert arm["total_commits"] == 52                    # they were SEEN and then excluded


# ------------------------------------------------------- cleanup: size (lines) ----
def test_size_arm_fires_one_line_over_the_limit(world):
    (world["vault"] / "Proj" / "Position.md").write_text("x\n" * 11)     # limit 10
    run_stop(world)                                                       # first run: creates state
    run_stop(world)
    arm = state_doc(world)["cleanup"]["arms"]["size_lines"]
    assert arm["fired"] is True
    assert any(f["path"] == "Proj/Position.md" for f in arm["files"]), arm


def test_size_arm_quiet_one_line_under_the_limit(world):
    (world["vault"] / "Proj" / "Position.md").write_text("x\n" * 10)      # limit 10, not OVER
    run_stop(world)
    run_stop(world)
    assert state_doc(world)["cleanup"]["arms"]["size_lines"]["fired"] is False


def test_a_stem_with_no_line_limit_is_never_flagged(world):
    """The table's absence of a stem is its meaning, not an omission — Patterns has no limit."""
    (world["vault"] / "Proj" / "Patterns.md").write_text("x\n" * 5000)
    run_stop(world)
    run_stop(world)
    arm = state_doc(world)["cleanup"]["arms"]["size_lines"]
    assert not any(f["path"].endswith("Patterns.md") for f in arm["files"]), arm


# ------------------------------------------------------- cleanup: size (bytes) ----
def test_byte_arm_fires_when_an_imported_file_blows_the_budget(world):
    big = world["tmp"] / "big.md"
    big.write_text("y" * 30000)                                           # budget 20,000
    (world["repo"] / "CLAUDE.md").write_text(f"# project\n@{big}\n")
    run_stop(world)
    run_stop(world)
    arm = state_doc(world)["cleanup"]["arms"]["boot_bytes"]
    assert arm["fired"] is True and arm["bytes"] > 20000
    assert any(m["path"] == str(big) for m in arm["largest"]), arm


def test_byte_arm_quiet_when_the_same_file_is_not_imported(world):
    """The v7.6 repair, ported by construction: a file outside the chain cannot move the number.

    ★ The fixture PUTS THE REAL PATH IN THE TEXT, in prose rather than as an @-import line. The
    first draft wrote a sentence that never named the file at all, and a test like that cannot
    fail: loosening `IMPORT_LINE_RE` (dropping its anchors, or `.match()` → `.search()`) left it
    green both times, because there was nothing in the fixture for a broken parser to latch onto.
    A negative control has to place the bait where a regression would take it — and `.match()`
    anchors at position 0 whatever the pattern says, so the path must lead its line. With prose in
    front of it the bait is unreachable and the control is decorative; with the path leading, a
    pattern that stops requiring the `@` picks it up and the test goes red."""
    big = world["tmp"] / "big.md"
    big.write_text("y" * 30000)
    (world["repo"] / "CLAUDE.md").write_text(
        f"# project\n{big} — named at the START of a line, deliberately with no leading `@`\n")
    run_stop(world)
    run_stop(world)
    arm = state_doc(world)["cleanup"]["arms"]["boot_bytes"]
    assert arm["fired"] is False, arm
    assert not any(m["path"] == str(big) for m in arm["largest"])


# --------------------------------------------------------- synthesis: entries ----
def test_synthesis_entry_arm_fires_at_the_threshold(world):
    set_last(world, synthesis=days_ago(1))
    p = world["vault"] / "Proj" / "Errata.md"
    p.write_text("# Errata\n" + "".join(f"## entry {i}\nbody\n" for i in range(5)))  # threshold 5
    git(world["vault"], "add", "--", "Proj/Errata.md")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
        "-m", "five entries", "--", "Proj/Errata.md")
    run_stop(world)
    arm = state_doc(world)["synthesis"]["arms"]["entries"]
    assert arm["fired"] is True and arm["entries"] == 5, arm


def test_synthesis_entry_arm_quiet_one_below(world):
    set_last(world, synthesis=days_ago(1))
    p = world["vault"] / "Proj" / "Errata.md"
    p.write_text("# Errata\n" + "".join(f"## entry {i}\nbody\n" for i in range(4)))
    git(world["vault"], "add", "--", "Proj/Errata.md")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
        "-m", "four entries", "--", "Proj/Errata.md")
    run_stop(world)
    arm = state_doc(world)["synthesis"]["arms"]["entries"]
    assert arm["fired"] is False and arm["entries"] == 4, arm


# ---------------------------------------------------------- synthesis: events ----
def test_topology_arm_fires_on_a_new_top_level_surface(world):
    set_last(world, synthesis=days_ago(1))
    (world["vault"] / "Newsurface").mkdir()
    (world["vault"] / "Newsurface" / "a.md").write_text("hi\n")
    git(world["vault"], "add", "--", "Newsurface/a.md")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
        "-m", "new surface", "--", "Newsurface/a.md")
    run_stop(world)
    arm = state_doc(world)["synthesis"]["arms"]["events"]
    assert arm["fired"] is True and any("Newsurface" in e for e in arm["events"]), arm


def test_topology_arm_quiet_when_an_existing_surface_gains_a_file(world):
    """`Proj/` existed before the window. A directory that merely grew is not a new surface."""
    set_last(world, synthesis=days_ago(1))
    (world["vault"] / "Proj" / "Canon.md").write_text("# Canon\n")
    git(world["vault"], "add", "--", "Proj/Canon.md")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
        "-m", "a file in an old surface", "--", "Proj/Canon.md")
    run_stop(world)
    arm = state_doc(world)["synthesis"]["arms"]["events"]
    assert arm["fired"] is False, arm


# ------------------------------------------------------------ regions, not names ----
def test_a_region_is_a_directory_with_a_map_not_a_name_on_a_list(world):
    """No region name is hardcoded anywhere. A second directory with a Map.md is a region; one
    without is not, and its Errata entries are therefore not counted."""
    set_last(world, synthesis=days_ago(1))
    for name, has_map in (("Second", True), ("NotARegion", False)):
        d = world["vault"] / name
        d.mkdir()
        if has_map:
            (d / "Map.md").write_text("# m\n")
        (d / "Errata.md").write_text("# Errata\n" + "".join(f"## e{i}\nb\n" for i in range(3)))
    git(world["vault"], "add", "-A")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "two dirs")
    run_stop(world)
    arm = state_doc(world)["synthesis"]["arms"]["entries"]
    assert arm["entries"] == 3, arm      # Second counted, NotARegion not


# ----------------------------------------------------------------- facts line ----
def test_facts_line_appears_when_something_fired(world):
    set_last(world, cleanup=days_ago(20))
    run_stop(world)
    out = run_session_start(world)
    assert "a cleanup pass is due" in out
    assert "20 days since the last pass" in out


def test_facts_block_says_nothing_about_maintenance_when_nothing_fired(world):
    set_last(world)
    run_stop(world)
    out = run_session_start(world)
    facts = out.split("\n\nThe operating rules")[0]
    assert "Maintenance" not in facts, facts


def test_facts_line_states_the_trigger_and_gives_no_advice(world):
    set_last(world, cleanup=days_ago(20))
    run_stop(world)
    out = run_session_start(world)
    facts = out.split("\n\nThe operating rules")[0]
    line = [l for l in facts.splitlines() if "cleanup pass is due" in l][0]
    for word in ("should", "must", "recommend", "warning", "⚠", "please"):
        assert word not in line.lower(), line


# -------------------------------------------------------------------- limits ----
def test_limits_file_matches_defaults():
    """The shipped `rules/limits.json` and `limits.DEFAULTS` are one opinion, not two."""
    sys.path.insert(0, str(HOOKS))
    import limits as limits_mod
    limits_mod._reset_for_tests()
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    for key, default in limits_mod.DEFAULTS.items():
        assert key in shipped, f"{key} is in DEFAULTS but not in the shipped limits.json"
        assert shipped[key] == default, f"{key}: shipped {shipped[key]!r} != default {default!r}"


def test_the_hook_holds_no_copy_of_any_threshold():
    """Every number the hook acts on comes from limits.py. A literal here is the drift trap the
    whole file exists to close, so the source is checked for one."""
    src = (HOOKS / "maintenance.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    body = body.split('"""', 2)[-1]                       # past the module docstring
    for number in ("14", "300", "20", "2000000", "20000"):
        assert f"= {number}" not in body, f"threshold {number} is inlined in maintenance.py"


def test_a_documentation_key_is_never_a_value():
    sys.path.insert(0, str(HOOKS))
    import limits as limits_mod
    with pytest.raises(KeyError):
        limits_mod.get("_boot_budget_bytes")


def test_a_malformed_limits_file_falls_back_rather_than_crashing(world, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json at all")
    env = dict(world["env"], GEDAECHTNIS_LIMITS=str(bad))
    p = subprocess.run([sys.executable, "-B", "-c",
                        f"import sys; sys.path.insert(0, {str(HOOKS)!r}); import limits;"
                        " print(limits.get('cleanup_trigger_commits'))"],
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "300"                      # the shipped default, not a crash


# ------------------------------------------------------------------- hygiene ----
def test_the_hook_writes_nothing_into_the_vault(world):
    set_last(world, cleanup=days_ago(20))
    before = git(world["vault"], "status", "--porcelain")
    head = git(world["vault"], "rev-parse", "HEAD")
    run_stop(world)
    assert git(world["vault"], "status", "--porcelain") == before
    assert git(world["vault"], "rev-parse", "HEAD") == head


def test_a_vault_that_does_not_exist_is_not_an_error(world, tmp_path):
    env = dict(world["env"], GEDAECHTNIS_VAULT=str(tmp_path / "nowhere"))
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "maintenance.py")],
                       input=json.dumps({"session_id": "s", "cwd": str(world["repo"])}),
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    assert not (world["state"] / "maintenance.json").exists()


def test_the_stop_hook_is_registered_alongside_the_existing_ones():
    d = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    stop = [h["command"] for h in d["hooks"]["Stop"][0]["hooks"]]
    assert any("maintenance.py" in c for c in stop)
    assert any("commit.py" in c for c in stop)            # merged onto, never replacing
    assert any("claim.py" in c for c in stop)
    assert "SubagentStop" in d["hooks"]                    # the other session's key survives


def test_a_young_vault_reports_no_topology_events(world, tmp_path):
    """A vault with no commit before the window has nothing for a surface to be NEW relative to.
    Reporting every directory as an event there is the fresh-install false alarm one day late."""
    vault = tmp_path / "young"
    (vault / "Proj").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    (vault / "Proj" / "Map.md").write_text("# m\n")
    git(vault, "add", "-A")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")
    env = dict(world["env"], GEDAECHTNIS_VAULT=str(vault))
    state = Path(world["state"]) / "maintenance.json"
    state.write_text(json.dumps({"version": 1, "created": days_ago(1),
                                 "last_cleanup": days_ago(1), "last_synthesis": days_ago(1)}))
    subprocess.run([sys.executable, "-B", str(HOOKS / "maintenance.py")],
                   input=json.dumps({"session_id": "s", "cwd": str(world["repo"])}),
                   capture_output=True, text=True, env=env, timeout=60, check=False)
    arm = json.loads(state.read_text())["synthesis"]["arms"]["events"]
    assert arm["fired"] is False, arm


# ------------------------------------------- a failed check is UNCHECKED, never zero ----
def test_a_vault_that_is_not_a_git_repo_reports_UNCHECKED_not_zero(world, tmp_path):
    """The reviewer's finding, pinned. A git-backed arm returns 0 when the command fails and 0 when
    the vault was genuinely quiet — opposite facts, identical output. Here the vault is a directory
    with no `.git` at all and 30 fresh entries in it: the counts are meaningless, and what must NOT
    happen is that they read as a healthy quiet vault."""
    vault = tmp_path / "notgit"
    (vault / "Proj").mkdir(parents=True)
    (vault / "Proj" / "Map.md").write_text("# m\n")
    (vault / "Proj" / "Errata.md").write_text(
        "# Errata\n" + "".join(f"## entry {i}\nbody\n" for i in range(30)))
    env = dict(world["env"], GEDAECHTNIS_VAULT=str(vault))
    state = Path(world["state"]) / "maintenance.json"
    state.write_text(json.dumps({"version": 1, "created": days_ago(2),
                                 "last_cleanup": days_ago(2), "last_synthesis": days_ago(2)}))
    subprocess.run([sys.executable, "-B", str(HOOKS / "maintenance.py")],
                   input=json.dumps({"session_id": "s", "cwd": str(world["repo"])}),
                   capture_output=True, text=True, env=env, timeout=60, check=False)
    doc = json.loads(state.read_text())
    assert doc["cleanup"]["arms"]["volume"]["checked"] is False
    assert doc["synthesis"]["arms"]["entries"]["checked"] is False
    # and it must SAY so, rather than let an unmeasured arm pass for a quiet one
    sys.path.insert(0, str(HOOKS))
    import maintenance
    text = "\n".join(maintenance.facts_lines(doc))
    assert "UNCHECKED" in text and "could NOT be measured" in text, text


def test_a_real_git_vault_reports_checked(world):
    """The negative control for the control: an ordinary vault must not be labelled UNCHECKED, or
    the notice is noise and the distinction it carries is worthless."""
    set_last(world, cleanup=days_ago(2), synthesis=days_ago(2))
    run_stop(world)
    doc = state_doc(world)
    assert doc["cleanup"]["arms"]["volume"]["checked"] is True
    assert doc["synthesis"]["arms"]["entries"]["checked"] is True
    assert doc["synthesis"]["arms"]["events"]["checked"] is True
    sys.path.insert(0, str(HOOKS))
    import maintenance
    assert "UNCHECKED" not in "\n".join(maintenance.facts_lines(doc))


# --------------------------------------- the search ceiling, named rather than silent ----
def test_a_file_over_the_search_ceiling_is_NAMED(world, tmp_path):
    """F1's acceptance condition: the ceiling itself may rise, but a skip must never again be
    silent. Positive control."""
    limits_file = world["limits_file"]
    limits_file.write_text(json.dumps(dict(LIMITS, max_searchable_file_bytes=5000)))
    big = world["vault"] / "Proj" / "Errata.md"
    big.write_text("# Errata\n" + "z" * 20000)
    run_stop(world)
    run_stop(world)
    doc = state_doc(world)
    assert doc["unsearchable"]["n_files"] >= 1
    assert any(f["path"] == "Proj/Errata.md" for f in doc["unsearchable"]["files"]), doc
    sys.path.insert(0, str(HOOKS))
    import maintenance
    text = "\n".join(maintenance.facts_lines(doc))
    assert "Proj/Errata.md" in text and "NOT searched" in text, text


def test_an_ordinary_vault_says_nothing_about_the_search_ceiling(world):
    """Negative control. Silence has to stay possible, or the notice is noise and is skipped the
    one time it matters."""
    run_stop(world)
    run_stop(world)
    doc = state_doc(world)
    assert doc["unsearchable"]["n_files"] == 0
    sys.path.insert(0, str(HOOKS))
    import maintenance
    assert "NOT searched" not in "\n".join(maintenance.facts_lines(doc))


# ---------------------------------- compaction actually RUNS in production (R3, finding 1) ----
def test_the_stop_hook_compacts_an_oversize_memory_file(world):
    """★ The test whose absence let a whole row ship a fix nothing called.

    Every other test in the R3 set exercised `archive.compact_file` directly, so all of them were
    green while no hook invoked it — the acceptance evidence described the simulator's own calls,
    not the product. This asserts the PRODUCTION path: run the Stop hook, and an oversize memory
    file is smaller afterwards with its entries in a segment."""
    world["limits_file"].write_text(json.dumps(dict(LIMITS, max_memory_file_bytes=4000)))
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text("# Canon\n" + "".join(f"\n## entry {i}\n" + "b" * 400 + "\n" for i in range(60)))
    before = live.stat().st_size
    assert before > 4000
    run_stop(world)                      # first run creates state
    run_stop(world)
    assert live.stat().st_size < before, "the Stop hook did not compact anything"
    assert live.stat().st_size <= 4000
    segs = sorted(p.name for p in (world["vault"] / "Proj").glob("Canon-archive*.md"))
    assert segs, "entries were removed from the live file and are in no segment"
    moved = "".join((world["vault"] / "Proj" / s).read_text() for s in segs)
    assert "## entry 0" in moved


def test_the_stop_hook_leaves_a_healthy_vault_untouched(world):
    """Negative control. Compaction that fires on a healthy file would hollow out every region in
    the vault — a worse failure than the one it fixes."""
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text("# Canon\n\n## one entry\nshort\n")
    before = live.read_bytes()
    run_stop(world)
    run_stop(world)
    assert live.read_bytes() == before
    assert list((world["vault"] / "Proj").glob("Canon-archive*.md")) == []


def test_a_retried_compaction_after_a_crash_does_not_duplicate(world):
    """Finding 2. The crash window is: the archive write lands, the live truncation never runs. A
    retry then recomputes the same oldest entries and would append them a second time."""
    sys.path.insert(0, str(PLUGIN_ROOT))
    import archive as arch
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text("# Canon\n" + "".join(f"\n## entry {i}\n" + "c" * 400 + "\n" for i in range(60)))
    text = live.read_text()
    parts = text.split("\n## ")
    # replay exactly the half that survives such a crash: the archive append, no live rewrite
    arch.append_entries(live, "".join("\n## " + e for e in parts[1:40]), limit=4000)
    arch.compact_file(live, limit=4000)          # the retry
    headings = []
    for s in arch.segments(live):
        headings += [l for l in s.read_text().splitlines() if l.startswith("## ")]
    assert len(headings) == len(set(headings)), \
        f"{len(headings) - len(set(headings))} heading(s) duplicated by the retry"


def test_compaction_is_COMMITTED_not_left_as_vault_dirt(world):
    """★ The second reviewer's disqualifying finding.

    `commit.py` commits the session's TOUCHED SET — the paths `chore.py` records on Edit/Write tool
    calls — and its own docstring says a script's writes are not in it. Compaction is a script's
    write. Left alone it leaves a modified live file and untracked archive segments behind forever,
    because no later session's touched set will contain them either.

    This asserts the vault is CLEAN after the Stop hook, which is the only form of the claim that
    cannot pass while the defect is present."""
    world["limits_file"].write_text(json.dumps(dict(LIMITS, max_memory_file_bytes=4000)))
    # A MARKER, because without one `auto_commit`'s lane fail-safe correctly stages nothing and the
    # test would have "passed" on the wrong mechanism. (It did, on the first attempt.)
    (world["repo"] / ".atlas-lane").write_text("lane: PROJ\npath: Proj/\n")
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text("# Canon\n" + "".join(f"\n## entry {i}\n" + "b" * 400 + "\n" for i in range(60)))
    git(world["vault"], "add", "--", "Proj/Canon.md")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
        "-m", "the file before compaction", "--", "Proj/Canon.md")
    assert git(world["vault"], "status", "--porcelain") == ""
    run_stop(world)
    run_stop(world)
    assert live.stat().st_size <= 4000, "compaction did not run; this test cannot see its subject"
    dirt = git(world["vault"], "status", "--porcelain")
    assert dirt == "", f"compaction left the vault dirty:\n{dirt}"
    assert "Compaction" in git(world["vault"], "log", "-1", "--format=%s")


def test_a_compacted_file_outside_the_lane_partition_is_left_for_its_own_lane(world):
    """The negative control, and it must NOT be read as a bug. Compaction may legitimately touch a
    file this lane does not own; the answer is the same as for any other write — leave it for the
    lane that does. `auto_commit` drops it, we do not special-case it, and the file stays dirty
    rather than being committed under the wrong lane's name."""
    world["limits_file"].write_text(json.dumps(dict(LIMITS, max_memory_file_bytes=4000)))
    (world["repo"] / ".atlas-lane").write_text("lane: PROJ\npath: Proj/\n")
    other = world["vault"] / "Other" / "Canon.md"
    other.parent.mkdir(exist_ok=True)
    other.write_text("# Canon\n" + "".join(f"\n## e {i}\n" + "b" * 400 + "\n" for i in range(60)))
    run_stop(world)
    run_stop(world)
    assert other.stat().st_size <= 4000, "the file was not compacted at all"
    dirt = git(world["vault"], "status", "--porcelain")
    assert "Other/" in dirt, "another lane's file was committed under this lane's name"


def test_a_file_outside_any_region_is_compacted_too(world):
    """The coverage gap the reviewer reproduced: a memory file at the vault root, or in a directory
    with no `Map.md`, grew past the bound across repeated session ends and was never touched — the
    year-3 failure relocated to whichever files happen to sit outside a region."""
    world["limits_file"].write_text(json.dumps(dict(LIMITS, max_memory_file_bytes=4000)))
    (world["repo"] / ".atlas-lane").write_text("lane: PROJ\npath: Proj/\npath: NoMap/\n")
    loose = world["vault"] / "Notes.md"
    nomap = world["vault"] / "NoMap" / "Canon.md"
    nomap.parent.mkdir()
    body = "# x\n" + "".join(f"\n## entry {i}\n" + "b" * 400 + "\n" for i in range(60))
    loose.write_text(body)
    nomap.write_text(body)
    run_stop(world)
    run_stop(world)
    assert loose.stat().st_size <= 4000, "a vault-root memory file was never compacted"
    assert nomap.stat().st_size <= 4000, "a file in a Map-less directory was never compacted"


def test_compaction_never_rewrites_a_queue_or_a_generated_mirror(world):
    """Compaction REWRITES what it touches, so a scope that is too wide is destructive rather than
    merely wasteful: a work queue and a lane notice outbox are operational surfaces other machinery
    parses, and nothing asked this hook to edit them. The exclusions are recall.py's own."""
    world["limits_file"].write_text(json.dumps(dict(LIMITS, max_memory_file_bytes=4000)))
    (world["repo"] / ".atlas-lane").write_text("lane: PROJ\npath: Proj/\npath: Pharos/\n")
    body = "# q\n" + "".join(f"\n## row {i}\n" + "b" * 400 + "\n" for i in range(60))
    queue = world["vault"] / "Pharos" / "queues" / "regions" / "proj.md"
    queue.parent.mkdir(parents=True)
    queue.write_text(body)
    mirror = world["vault"] / ".gedaechtnis" / "views" / "Proj" / "Canon.md"
    mirror.parent.mkdir(parents=True)
    mirror.write_text(body)
    before_q, before_m = queue.read_bytes(), mirror.read_bytes()
    run_stop(world)
    run_stop(world)
    assert queue.read_bytes() == before_q, "compaction rewrote a work queue"
    assert mirror.read_bytes() == before_m, "compaction rewrote a generated mirror"


def test_a_file_ALREADY_over_the_search_ceiling_is_still_compacted(world):
    """★ The files that most need compacting must not be the only ones it cannot see.

    `recall.md_files()` drops anything over its own MAX_FILE_BYTES — right for a reader, fatal for
    the writer whose job is to bring such a file back under. A vault that arrives already oversize
    (a rollout onto an old vault, a stretch with compaction disabled, one burst write) would never
    self-heal, and the state doc would say nothing at all."""
    ceiling = 20000
    world["limits_file"].write_text(json.dumps(
        dict(LIMITS, max_memory_file_bytes=4000, max_searchable_file_bytes=ceiling)))
    (world["repo"] / ".atlas-lane").write_text("lane: PROJ\npath: Proj/\n")
    live = world["vault"] / "Proj" / "Canon.md"
    live.write_text("# Canon\n" + "".join(f"\n## entry {i}\n" + "b" * 400 + "\n" for i in range(120)))
    # The fixture has to cross the ceiling the SEARCH actually uses, not the one the test wrote
    # down. The first version of this test set 20,000 in limits.json while recall.py carried a
    # hardcoded 2,000,000, so the file was never over anything and the test passed under the very
    # mutation it was written to catch. recall.py now reads the config value, which is what makes
    # this assertion mean what it says.
    import subprocess as _sp
    probe = _sp.run([sys.executable, "-B", "-c",
                     f"import sys; sys.path.insert(0, {str(PLUGIN)!r}); import recall;"
                     " print(recall.MAX_FILE_BYTES)"],
                    capture_output=True, text=True, env=world["env"])
    assert probe.stdout.strip() == str(ceiling), \
        f"recall does not use the configured ceiling ({probe.stdout.strip()!r}); test is vacuous"
    assert live.stat().st_size > ceiling, "fixture is not over the search ceiling"
    run_stop(world)
    run_stop(world)
    assert live.stat().st_size <= 4000, "a file past the search ceiling was never compacted"
    assert state_doc(world)["compacted"], "compaction happened but was not recorded"
