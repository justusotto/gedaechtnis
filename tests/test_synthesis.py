"""Tests for the synthesis pass — the mechanical half, which judges nothing and writes almost nothing.

Same world as `test_cleanup.py`: a git repository in tmp_path pointed at by GEDAECHTNIS_VAULT with
the state directory and the limits file redirected, and the product driven as a SUBPROCESS through
`synthesis.py`'s own entry point. Never in-process: the vault comes from the environment, and the
one time this arc called a maintenance function in-process against what it believed was a fixture
it rewrote 263 files of a real vault.

**What the controls are aimed at here.** This pass fails by staying QUIET — a candidate it drops is
a proposal nobody makes again for a fortnight, and there is no symptom. So every rule is checked at
its boundary in both directions, and the two whole-run controls underneath are: the pass changes no
memory file (with a positive control proving that check can go red), and a region it cannot read is
reported UNCHECKED rather than counted as zero.
"""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, time
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SYNTH = PLUGIN / "synthesis.py"

LIMITS = {
    # ★ EVERY role named, deliberately. This fixture used to list only the roles it cares
    # about, and that WAS its isolation: the limits FILE replaced the table, so an unnamed
    # role simply had no limit. Since the file layer merges dict limits per sub-key (a fix
    # for a silent protection loss), an unnamed role quietly inherits the SHIPPED default
    # instead — the fixture stays green and stops isolating what it was written to isolate.
    # The roles this file does not test are given a ceiling nothing here can reach, which
    # says the same thing the short table used to say, out loud.
    "role_soft_limits_lines": {"Map": 10000000, "Vision": 10000000, "Position": 250, "Course": 10000000, "Aporia": 10000000, "Errata": 10000000, "Annales": 10000000, "Canon": 800},
    "stale_entry_days": 56,
    "compaction_floor_share": 0.4,
    "max_memory_file_bytes": 500000,
    "max_searchable_file_bytes": 2000000,
    "cleanup_trigger_days": 14,
    "cleanup_trigger_commits": 300,
    "synthesis_trigger_days": 14,
    "synthesis_trigger_entries": 20,
    "boot_budget_bytes": 20000,
    "boot_budget_warn_bytes": 20000,
    "boot_file_budget_bytes": 32000,
    "boot_file_warn_bytes": 28000,
    "cleanup_apply": True,          # the stamp test runs an APPLIED pass (CLEANUPGATE-1)
}

RULE = ("\n## A measured zero needs a positive control\n\n"
        "**Never** trust a count of zero from a glob that could have failed; the pipeline still\n"
        "reports zero when the command itself died.\n")
RULE_TWIN = ("\n## Counting needs a control\n\n"
             "**Never** report a measured zero without a positive control: a failed glob returns\n"
             "the same zero a genuinely empty directory does.\n")
MECHANIZED = ("\n## Already has a check\n\n"
              "**Always** assert on the encoded bytes — see tests/test_charset.py.\n")
NOT_A_RULE = "\n## An observation\n\nSome prose that states no rule at all.\n"


def days_ago(n: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))


def git(vault: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=True)
    return p.stdout


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "vault"
    for region in ("Alpha", "Beta"):
        (vault / region).mkdir(parents=True)
        (vault / region / "Map.md").write_text(f"# {region}\n")
    (vault / "Global").mkdir()
    (vault / "Global" / "Patterns.md").write_text("# Patterns\n\n## A shared entry\n\nnothing.\n")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    git(vault, "add", "-A")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: MY-PROJECT\npath: Alpha/\npath: Beta/\n")
    limits_file = tmp_path / "limits.json"
    limits_file.write_text(json.dumps(LIMITS))
    state = tmp_path / "state"
    env = dict(os.environ,
               GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_LIMITS=str(limits_file),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-such-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, env=env, tmp=tmp_path, state=state)


def run(w, *args, expect_rc=0):
    p = subprocess.run([sys.executable, "-B", str(SYNTH), *args],
                       capture_output=True, text=True, env=w["env"], timeout=120,
                       cwd=str(w["repo"]))
    assert p.returncode == expect_rc, p.stdout + p.stderr
    return p


def pack(w) -> dict:
    return json.loads(run(w, "--json").stdout)


def snapshot(vault: Path) -> dict[str, str]:
    return {str(p.relative_to(vault)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(vault.rglob("*")) if p.is_file() and ".git/" not in str(p)}


def write_state(w, **keys):
    w["state"].mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "created": days_ago(30),
           "last_cleanup": days_ago(1), "last_synthesis": days_ago(1)}
    doc.update(keys)
    (w["state"] / "maintenance.json").write_text(json.dumps(doc))
    return doc


# ------------------------------------------------------------------ class (a) ----
def test_a_rule_with_no_mechanism_is_a_candidate(world):
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE)
    got = pack(world)
    assert [c["heading"] for c in got["class_a"]] == ["A measured zero needs a positive control"]


def test_the_same_rule_naming_a_test_is_not(world):
    """Negative control on the dropping half. The entry is byte-identical to the one above except
    for the sentence naming a test — so a candidate list that ignored the backlink would return
    both, and one that never returned anything would also pass the test above."""
    (world["vault"] / "Alpha" / "Errata.md").write_text(
        "# Errata\n" + RULE.replace("the command itself died.",
                                    "the command itself died — see tests/test_zero.py."))
    assert pack(world)["class_a"] == []


def test_prose_that_states_no_rule_is_not_a_candidate(world):
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + NOT_A_RULE)
    assert pack(world)["class_a"] == []


def test_the_heading_is_the_headings_text(world):
    """The splitter hands back each entry with its leading blank line attached. Taking the first
    line straight off the block yields "" for every heading — which reads in a report as an entry
    with no title rather than as a parser that is wrong, so it is asserted rather than eyeballed."""
    (world["vault"] / "Alpha" / "Patterns.md").write_text("# Patterns\n" + RULE + MECHANIZED)
    for c in pack(world)["class_a"]:
        assert c["heading"] and not c["heading"].startswith("#")


# ------------------------------------------------------------------ class (b) ----
def test_one_lesson_in_two_regions_clusters(world):
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE)
    (world["vault"] / "Beta" / "Patterns.md").write_text("# Patterns\n" + RULE_TWIN)
    got = pack(world)
    assert len(got["class_b"]) == 1
    assert got["class_b"][0]["regions"] == ["Alpha", "Beta"]
    assert len(got["class_b"][0]["shared_terms"]) >= 3


def test_the_same_lesson_twice_in_ONE_region_does_not(world):
    """Negative control: two entries in one region are that region's motif, not a promotion. The
    fixture is the clustering fixture with both entries moved into Alpha, so the only difference
    between red and green here is the region rule."""
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE)
    (world["vault"] / "Alpha" / "Patterns.md").write_text("# Patterns\n" + RULE_TWIN)
    assert pack(world)["class_b"] == []


def test_an_entry_already_pointing_at_the_shared_file_is_dropped(world):
    (world["vault"] / "Alpha" / "Errata.md").write_text(
        "# Errata\n" + RULE + "\nSee [[../Global/Patterns#A shared entry]].\n")
    (world["vault"] / "Beta" / "Patterns.md").write_text("# Patterns\n" + RULE_TWIN)
    got = pack(world)
    assert got["class_b"] == []
    assert got["counts"]["dropped_already_promoted"] == 1


def test_a_link_to_something_that_is_not_a_shared_file_does_not_drop_it(world):
    """Negative control for the drop above: the link exists, but its target is not one of the
    vault's shared top-level files, so the entry has NOT been promoted and must still cluster.
    Without this, `already_promoted` could return True for every entry carrying any wikilink."""
    (world["vault"] / "Alpha" / "Errata.md").write_text(
        "# Errata\n" + RULE + "\nSee [[Alpha/Course#Somewhere]].\n")
    (world["vault"] / "Beta" / "Patterns.md").write_text("# Patterns\n" + RULE_TWIN)
    got = pack(world)
    assert len(got["class_b"]) == 1
    assert got["counts"]["dropped_already_promoted"] == 0


def test_a_shared_term_floor_keeps_a_small_corpus_from_stoplisting_itself(world):
    """The measured stoplist times the corpus size is a trap below about sixteen entries: at four
    entries `0.25 * 4 = 1`, so every term the two clustered entries share is 'furniture' and the
    cluster can never form. Measured on the first fixture run, which returned zero clusters on a
    vault built to contain exactly one."""
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE + MECHANIZED)
    (world["vault"] / "Beta" / "Patterns.md").write_text("# Patterns\n" + RULE_TWIN + NOT_A_RULE)
    got = pack(world)
    assert got["counts"]["entries_read"] == 4
    assert len(got["class_b"]) == 1


# --------------------------------------------------------------- UNCHECKED ----
def test_an_unreadable_file_is_unchecked_not_zero(world):
    f = world["vault"] / "Alpha" / "Errata.md"
    f.write_text("# Errata\n" + RULE)
    f.chmod(0o000)
    try:
        got = pack(world)
    finally:
        f.chmod(0o644)
    assert got["unreadable"] == ["Alpha/Errata.md"]
    assert got["counts"]["files_unreadable"] == 1
    assert "UNCHECKED" in run(world).stdout


def test_a_readable_file_is_not_reported_unchecked(world):
    """Positive control for the line above: without it, a scanner that called EVERY file unreadable
    would pass, and so would one whose UNCHECKED line was always printed."""
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE)
    got = pack(world)
    assert got["unreadable"] == [] and got["counts"]["files_unreadable"] == 0
    assert "UNCHECKED — 0" not in run(world).stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads unreadable files")
def test_unreadable_is_reachable_at_all(world):
    """The control for the control. `chmod 000` is a no-op for root and on some filesystems, and a
    test asserting UNCHECKED would then be asserting nothing, quietly, forever."""
    f = world["vault"] / "Alpha" / "Errata.md"
    f.write_text("x")
    f.chmod(0o000)
    try:
        with pytest.raises(OSError):
            f.read_text()
    finally:
        f.chmod(0o644)


# -------------------------------------------------------------------- mark ----
def test_mark_refuses_without_the_artifacts(world):
    write_state(world, last_synthesis=days_ago(30))
    p = run(world, "--mark", expect_rc=1)
    assert "REFUSED" in p.stdout
    doc = json.loads((world["state"] / "maintenance.json").read_text())
    assert doc["last_synthesis"] == days_ago(30), "the window moved on a refused mark"


def test_mark_records_the_pass_when_they_are_there(world):
    write_state(world, last_synthesis=days_ago(30))
    day = time.strftime("%Y-%m-%d")
    d = world["vault"] / "Synthesis" / day
    d.mkdir(parents=True)
    (d / "report.md").write_text("# Synthesis\n")
    (d / "findings.json").write_text("{}")
    run(world, "--mark")
    doc = json.loads((world["state"] / "maintenance.json").read_text())
    assert doc["last_synthesis"] == day
    assert doc["version"], "a stamped state must carry the schema version it was written under"


def test_recording_a_pass_clears_its_own_time_arm(world):
    """The reason `record_pass` exists. Before it, nothing wrote either stamp after the state file
    was created, so a time arm that fired once fired in every session afterwards whatever the user
    did — a trigger that cannot be satisfied is a notice, and a notice that never goes away is one
    the reader learns to skim."""
    write_state(world, last_synthesis=days_ago(30))
    assert pack(world)["trigger"]["due"] is True
    day = time.strftime("%Y-%m-%d")
    d = world["vault"] / "Synthesis" / day
    d.mkdir(parents=True)
    (d / "report.md").write_text("# Synthesis\n")
    (d / "findings.json").write_text("{}")
    run(world, "--mark")
    assert pack(world)["trigger"]["due"] is False


def test_an_unknown_pass_name_raises_rather_than_writing_a_key_nothing_reads(world):
    write_state(world)
    code = ("import sys; sys.path[:0]=[%r, %r]; import maintenance; maintenance.record_pass('lustrum')"
            % (str(PLUGIN), str(PLUGIN / "hooks")))
    p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                       env=world["env"], timeout=60, cwd=str(world["repo"]))
    assert p.returncode != 0 and "unknown maintenance pass" in p.stderr
    doc = json.loads((world["state"] / "maintenance.json").read_text())
    assert "lustrum" not in json.dumps(doc)


# ------------------------------------------------------ the pass writes nothing ----
def test_the_scan_changes_no_memory_file(world):
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE)
    (world["vault"] / "Beta" / "Patterns.md").write_text("# Patterns\n" + RULE_TWIN)
    before = snapshot(world["vault"])
    run(world)
    run(world, "--json")
    assert snapshot(world["vault"]) == before


def test_that_conservation_check_can_fail(world):
    """Positive control for the line above. A snapshot comparison that could never go red would
    pass on a pass that rewrote the whole vault, and nothing would say so."""
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE)
    before = snapshot(world["vault"])
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE + NOT_A_RULE)
    assert snapshot(world["vault"]) != before


# --------------------------------------------------- the pass output is not memory ----
def test_a_synthesis_bundle_is_excluded_from_recall_by_class(world):
    import importlib, sys as _s
    _s.path.insert(0, str(PLUGIN))
    recall = importlib.import_module("recall")
    assert recall.is_removed_dir("Synthesis")
    assert recall.is_removed_dir("Synthesis 2026-09-17")
    assert recall.is_removed_dir("Synthesis-2026-09-17")
    assert recall.is_removed_dir("Cleanup 2026-09-15 accidental-compaction")


def test_a_region_that_merely_starts_with_the_word_is_not(world):
    """Negative control on the same rule, and the reason the prefix alone was not enough: what
    follows the separator must be a DATE. A rule broad enough to catch `Cleanup 2026-09-15 x` is
    broad enough to make a region called `Synthesis-of-Law` silently unsearchable."""
    import importlib, sys as _s
    _s.path.insert(0, str(PLUGIN))
    recall = importlib.import_module("recall")
    assert not recall.is_removed_dir("Synthesis-of-Law")
    assert not recall.is_removed_dir("SynthesisNotes")
    assert not recall.is_removed_dir("Cleanups")
    assert not recall.is_removed_dir("Canon")


# --------------------------------------------------------- shipped, and named ----
def test_every_command_and_agent_the_package_ships_is_named_in_the_readme():
    """The README said 'Three commands' and 'Two agents' while five and four were on disk. A count
    in prose is a claim about the tree, and this is the cheapest thing that can check it.

    NOT a duplicate of `test_commands.py` / `test_agents.py`: those assert the directory matches a
    registry, which is what a stranger's INSTALL depends on. This asserts the prose matches the
    directory, which is what a stranger's READING depends on. Both went stale in the same row, and
    only one of them had a test."""
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    missing = []
    for p in sorted((PLUGIN / "commands").glob("*.md")):
        if f"/{p.stem}" not in readme:
            missing.append(f"command {p.stem}")
    for p in sorted((PLUGIN / "agents").glob("*.md")):
        if p.stem not in readme:
            missing.append(f"agent {p.stem}")
    assert not missing, f"shipped but never named in README.md: {missing}"


def test_that_readme_check_bites():
    """Negative control: a name that is NOT in the README must be reported. Without it the test
    above passes on a README that happens to contain every word in the language."""
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "gedaechtnis-no-such-command" not in readme


def test_an_unreadable_REGION_is_unchecked_not_absent(world):
    """The one the file-level control could not see, found by this row's reviewer.

    `Path.rglob` skips a directory it may not read WITHOUT raising, and `maintenance.regions()`
    swallows OSError into an empty list — so before `unreadable_dirs()` existed, a vault with one
    locked region came back `{"regions": 1, "regions_checked": true}`: the region had not vanished
    from the vault, only from the count, and nothing said so."""
    locked = world["vault"] / "Locked"
    locked.mkdir()
    (locked / "Map.md").write_text("# Locked\n")
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE)
    locked.chmod(0o000)
    try:
        got = pack(world)
        text = run(world).stdout
    finally:
        locked.chmod(0o755)
    assert got["counts"]["regions_checked"] is False
    assert any("Locked" in u for u in got["unreadable"]), got["unreadable"]
    assert "UNCHECKED" in text


def test_a_fully_readable_vault_reports_its_regions_checked(world):
    """Positive control: without it, a probe that called every vault unreadable would pass above."""
    (world["vault"] / "Alpha" / "Errata.md").write_text("# Errata\n" + RULE)
    got = pack(world)
    assert got["counts"]["regions_checked"] is True
    assert got["unreadable"] == []


def test_the_cleanup_pass_stamps_its_own_window(world):
    """`cleanup.py`'s new `record_pass` call had no test — verified by hand at review time, which
    is the state a test exists to replace."""
    write_state(world, last_cleanup=days_ago(30))
    p = subprocess.run([sys.executable, "-B", str(PLUGIN / "cleanup.py"), "--json"],
                       capture_output=True, text=True, env=world["env"], timeout=120,
                       cwd=str(world["repo"]))
    assert p.returncode == 0, p.stderr
    doc = json.loads((world["state"] / "maintenance.json").read_text())
    assert doc["last_cleanup"] == time.strftime("%Y-%m-%d")
    assert doc["version"], "the stamp must leave a schema version behind"


def test_a_dry_run_does_not_stamp(world):
    """Negative control: a pass that only looked has not closed its window."""
    write_state(world, last_cleanup=days_ago(30))
    p = subprocess.run([sys.executable, "-B", str(PLUGIN / "cleanup.py"), "--json", "--dry-run"],
                       capture_output=True, text=True, env=world["env"], timeout=120,
                       cwd=str(world["repo"]))
    assert p.returncode == 0, p.stderr
    doc = json.loads((world["state"] / "maintenance.json").read_text())
    assert doc["last_cleanup"] == days_ago(30)
