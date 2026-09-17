"""Tests for the Boot file — graduation, the rolling window, and freshness.

**Why each arm's negative control is the load-bearing half, again but differently.** These three
arms fail in opposite directions. Graduation fails by proposing a Boot file for a region that does
not need one — noise, and noise teaches a reader to skip the line. Freshness fails by reporting
FRESH for a Boot file whose bodies have moved, which is the reassuring failure: a stale summary that
reads as current, in every session's context, with nothing to contradict it. And the window fails by
compacting a file it should not have touched, which moves somebody's memory.

So: every quiet assertion here carries a measured figure proving the arm looked, and the freshness
test builds the SAME-SECOND case explicitly, because that is the one a timestamp comparison gets
wrong and a reachability comparison gets right.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"

LIMITS = {
    "boot_file_budget_bytes": 2000,
    "boot_file_warn_bytes": 1500,
    "compaction_floor_share": 0.4,
    "max_memory_file_bytes": 500000,
    "max_searchable_file_bytes": 2000000,
    "role_soft_limits_lines": {"Position": 250},
    "stale_entry_days": 56,
    "cleanup_trigger_days": 14, "cleanup_trigger_commits": 300,
    "synthesis_trigger_days": 14, "synthesis_trigger_entries": 20,
    "boot_budget_bytes": 20000,
}


def git(v: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(v), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=True)
    return p.stdout


def commit_all(v: Path, msg: str) -> None:
    git(v, "add", "-A")
    git(v, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)


@pytest.fixture
def mod(tmp_path, monkeypatch):
    """The module under test, pointed at a small limits file.

    ★ The redirect is `LIMITS_PATH`, NOT the environment variable, and the difference cost six
    failures that appeared only in the full suite. `limits.py` resolves `LIMITS_PATH` ONCE at
    import time; by the time this fixture runs, some earlier test has already imported it, so
    setting GEDAECHTNIS_LIMITS afterwards moves nothing and every threshold here silently comes
    from the SHIPPED file. Alone, this file imported limits first and passed. It is the same
    import-order trap row R2 hit from the other direction, and the general form is worth carrying:
    **a module that reads its configuration at import time cannot be reconfigured by environment
    afterwards — redirect the resolved value, not the input it was resolved from.**"""
    limits_file = tmp_path / "limits.json"
    limits_file.write_text(json.dumps(LIMITS))
    monkeypatch.setenv("GEDAECHTNIS_LIMITS", str(limits_file))
    sys.path.insert(0, str(HOOKS))
    sys.path.insert(0, str(PLUGIN))
    import importlib.util
    spec = importlib.util.spec_from_file_location("gd_bootfile", PLUGIN / "bootfile.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    lim = m._limits()
    monkeypatch.setattr(lim, "LIMITS_PATH", limits_file)
    lim._reset_for_tests()
    assert m.budget() == LIMITS["boot_file_budget_bytes"], \
        "the fixture failed to redirect the limits; every threshold below would be the shipped one"
    yield m
    lim._reset_for_tests()


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Proj").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    (v / "Proj" / "Map.md").write_text("# Proj\n")
    commit_all(v, "root")
    return v


def entries(n: int, size: int = 200, first: str = "") -> str:
    head = "# Boot\n"
    body = (f"\n## {first}\n" + "x" * size + "\n") if first else ""
    return head + body + "".join(f"\n## entry {i}\n" + "x" * size + "\n" for i in range(n))


def real_boot_file(n_resume: int = 20, size: int = 200, marker: bool = True) -> str:
    """A Boot file shaped like one a person writes, which is where the first version broke.

    Named structural sections, NOT a chronological log: an index, a resume point that does grow,
    a standing-constraints section carrying a NEVER rule, and pointers. The reviewer's repro built
    exactly this and watched the standing constraints move into an archive nothing reads at boot."""
    resume = "".join(f"\n## resume {i}\n" + "x" * size + "\n" for i in range(n_resume))
    return ("# Boot\n\n## At a glance\n\nwhat this region is\n"
            + "\n## Standing constraints\n\nNEVER delete a memory file.\n"
            + ("\n" + WINDOW_OPEN + "\n" if marker else "")
            + resume
            + ("\n" + WINDOW_CLOSE + "\n" if marker else "")
            + "\n## Canon headlines\n\nlocked decisions\n"
            + "\n## Pointer map\n\nwhere the bodies are\n")


WINDOW_OPEN = "<!-- gedaechtnis:window -->"
WINDOW_CLOSE = "<!-- gedaechtnis:/window -->"


# ------------------------------------------------------------- graduation ----
def test_a_region_over_the_budget_with_no_boot_file_is_proposed_one(mod, vault):
    (vault / "Proj" / "Canon.md").write_text("y" * 3000)
    out = mod.graduation_candidates(vault)
    assert len(out) == 1 and out[0]["region"] == "Proj"
    assert out[0]["bytes"] > out[0]["threshold"]
    assert any(m["path"] == "Canon.md" for m in out[0]["largest"])


def test_a_region_that_ALREADY_has_a_boot_file_is_not_proposed_one(mod, vault):
    (vault / "Proj" / "Canon.md").write_text("y" * 3000)
    (vault / "Proj" / "Kernel.md").write_text("# Boot\n")
    assert mod.graduation_candidates(vault) == []
    # POSITIVE CONTROL: the payload really is over the budget, so the quiet is about the Boot
    # file's presence and not about an arm that measured nothing.
    total, _ = mod.region_payload(vault / "Proj")
    assert total > mod.budget()


def test_a_small_region_is_left_alone(mod, vault):
    (vault / "Proj" / "Canon.md").write_text("small\n")
    assert mod.graduation_candidates(vault) == []
    total, members = mod.region_payload(vault / "Proj")
    assert 0 < total < mod.budget() and members


def test_the_payload_excludes_the_boot_file_and_its_archive(mod, vault):
    """An archive is where the window moves entries TO. Counting it would make a compaction look
    like growth, and the region would be proposed a Boot file it already has."""
    (vault / "Proj" / "Kernel.md").write_text("k" * 5000)
    (vault / "Proj" / "Canon-archive.md").write_text("a" * 5000)
    (vault / "Proj" / "Canon.md").write_text("c" * 100)
    total, members = mod.region_payload(vault / "Proj")
    counted = {m["path"] for m in members}
    assert counted == {"Canon.md", "Map.md"}, counted        # Map.md IS payload; the fixture wrote it
    assert total == 100 + (vault / "Proj" / "Map.md").stat().st_size
    assert total < mod.budget(), "the two excluded files are 10,000 B; counting either blows this"


# ---------------------------------------------------------- the window ----
def test_an_over_budget_boot_file_is_compacted_BELOW_ITS_MARKER(mod, vault):
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(real_boot_file())
    assert boot.stat().st_size > mod.budget()
    moved = mod.roll_window(vault)
    assert len(moved) == 1 and moved[0]["entries"] > 0, moved
    seg = vault / "Proj" / "Kernel-archive.md"
    assert seg.is_file() and "## resume 0" in seg.read_text()


def test_A_STANDING_CONSTRAINT_ABOVE_THE_MARKER_IS_NEVER_MOVED(mod, vault):
    """★ The defect this row was REJECTED for, as a test.

    The first version compacted a Boot file's OLDEST `## ` sections. A real Boot file's sections
    are named and structural, not ordered by age — so the reviewer's realistic fixture had its
    `## Standing constraints` section, carrying a NEVER rule, moved wholesale into an archive file
    nothing reads at boot. The rule that forbids exactly that lives in the kind of file the
    mechanism had just emptied."""
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(real_boot_file(n_resume=60))
    assert boot.stat().st_size > mod.budget() * 2
    mod.roll_window(vault)
    after = boot.read_text()
    assert "NEVER delete a memory file." in after, "a binding rule was moved out of the boot file"
    assert "## At a glance" in after and "## Standing constraints" in after
    assert "## Canon headlines" in after and "## Pointer map" in after, \
        "a structural section BELOW the window was carried off — the second half of the defect"
    seg = (vault / "Proj" / "Kernel-archive.md").read_text()
    assert "NEVER delete a memory file." not in seg
    assert "locked decisions" not in seg
    # POSITIVE CONTROL: the window DID move something, so the survival above is not "nothing ran".
    assert "## resume 0" in seg


def test_a_boot_file_with_NO_marker_is_reported_and_never_written(mod, vault):
    """The safe default, in the only direction that matters: not compacting costs a large file;
    compacting the wrong thing costs a rule that silently stops being loaded."""
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(real_boot_file(marker=False))
    before = boot.read_text()
    out = mod.roll_window(vault)
    assert len(out) == 1 and out[0]["entries"] == 0
    assert "declares no window" in out[0]["skipped"]
    assert WINDOW_OPEN in out[0]["skipped"] and WINDOW_CLOSE in out[0]["skipped"], \
        "the report must say how to opt in"
    assert boot.read_text() == before
    assert not (vault / "Proj" / "Kernel-archive.md").exists()


def test_the_window_does_not_empty_the_file(mod, vault):
    """A Boot file compacted to nothing is a region with no index — worse than an oversize one."""
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(real_boot_file(n_resume=40))
    mod.roll_window(vault)
    live = boot.read_text()
    assert "## resume" in live
    assert "## Standing constraints" in live and "## Pointer map" in live


def test_a_boot_file_under_the_budget_is_untouched(mod, vault):
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(entries(2, 100))
    before = boot.read_text()
    assert mod.roll_window(vault) == []
    assert boot.read_text() == before
    assert not (vault / "Proj" / "Kernel-archive.md").exists()


def test_the_window_moves_the_oldest_entries_BELOW_the_marker(mod, vault):
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(real_boot_file(n_resume=30))
    mod.roll_window(vault)
    seg = (vault / "Proj" / "Kernel-archive.md").read_text()
    live = boot.read_text()
    assert "## resume 0" in seg and "## resume 0\n" not in live
    # The NEWEST window entry stays live: a window emptied to nothing is the same loss, slower.
    assert "## resume 29" in live


def test_a_window_holding_ONE_GIANT_entry_is_reported_not_emptied(mod, vault):
    """Measured by the reviewer on the first version: `compact_file`'s floor share does not bind
    when a file has few large entries — a single 39,000 B entry compacted the file to 36 bytes,
    head only. The loop stops one entry short, always, and says why it could do nothing."""
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text("# Boot\n\n## Standing constraints\n\nNEVER.\n\n" + WINDOW_OPEN
                    + "\n\n## the only entry\n" + "x" * 5000 + "\n" + WINDOW_CLOSE + "\n")
    before = boot.read_text()
    out = mod.roll_window(vault)
    assert len(out) == 1 and out[0]["entries"] == 0
    assert "too large to move even one" in out[0]["skipped"]
    assert boot.read_text() == before


def test_a_window_with_no_entries_at_all_is_reported_not_cut(mod, vault):
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text("# Boot\n\n## Standing constraints\n\nNEVER.\n\n" + WINDOW_OPEN
                    + "\n\nprose with no headings at all\n" + "x" * 5000 + "\n"
                    + WINDOW_CLOSE + "\n")
    before = boot.read_text()
    out = mod.roll_window(vault)
    assert len(out) == 1 and out[0]["entries"] == 0
    assert "no `## ` entries" in out[0]["skipped"]
    assert boot.read_text() == before


def test_a_WARN_band_file_is_reported_but_not_compacted(mod, vault):
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text("k" * 1600)                       # between warn 1500 and budget 2000
    states = {f["path"]: f["state"] for f in mod.oversize_boot_files(vault)}
    assert states == {"Proj/Kernel.md": "WARN"}
    before = boot.read_text()
    assert mod.roll_window(vault) == []
    assert boot.read_text() == before


# ------------------------------------------------------------- freshness ----
def test_a_body_committed_AFTER_the_boot_file_makes_it_stale(mod, vault):
    (vault / "Proj" / "Kernel.md").write_text("# Boot\n")
    (vault / "Proj" / "Position.md").write_text("# Position\n")
    commit_all(vault, "boot and body together")
    stale, unchecked = mod.stale_boot_files(vault)
    assert stale == [] and unchecked == [], "nothing has moved yet"
    (vault / "Proj" / "Position.md").write_text("# Position\n\nthe region has moved on\n")
    commit_all(vault, "the body moves")
    stale, unchecked = mod.stale_boot_files(vault)
    assert len(stale) == 1 and stale[0]["path"] == "Proj/Kernel.md"
    assert stale[0]["moved"] == ["Proj/Position.md"]


def test_the_SAME_SECOND_case_which_a_timestamp_comparison_gets_wrong(mod, vault):
    """★ The reason this arm compares ancestry. This package's own Stop hook commits several files
    in one second; with `%ct` the body and its Boot file compare EQUAL and the staleness is
    silently reported as fresh."""
    boot = vault / "Proj" / "Kernel.md"
    body = vault / "Proj" / "Position.md"
    # BOTH commits are pinned to one explicit timestamp. Relying on them landing in the same
    # second by accident made this test pass alone and fail in the suite, where the two commits
    # straddled a second boundary — a flaky fixture, caught by its own guard below rather than by
    # producing a wrong verdict.
    when = "2026-09-15T12:00:00"
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    boot.write_text("# Boot\n")
    body.write_text("# Position\n")
    git(vault, "add", "-A")
    subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "together"], env=env, check=True)
    body.write_text("# Position\n\nmoved on\n")
    git(vault, "add", "--", "Proj/Position.md")
    subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "same second", "--", "Proj/Position.md"],
                   env=env, check=True)
    # The timestamps are now EQUAL — the comparison the vault's own detector uses.
    a = git(vault, "log", "-1", "--format=%ct", "--", "Proj/Kernel.md").strip()
    b = git(vault, "log", "-1", "--format=%ct", "--", "Proj/Position.md").strip()
    assert a == b, "the fixture failed to produce the same-second case; the test proves nothing"
    stale, _ = mod.stale_boot_files(vault)
    assert len(stale) == 1, "a same-second body change was reported as fresh"


def test_a_body_that_is_NOT_watched_does_not_make_a_boot_file_stale(mod, vault):
    """An Errata or a Patterns entry ADDS to a region without contradicting its index. Treating
    every file as a trigger would make every Boot file permanently stale, which is the same as
    never reporting one."""
    (vault / "Proj" / "Kernel.md").write_text("# Boot\n")
    (vault / "Proj" / "Errata.md").write_text("# Errata\n")
    commit_all(vault, "together")
    (vault / "Proj" / "Errata.md").write_text("# Errata\n\n## a new bug\n")
    commit_all(vault, "errata grows")
    stale, _ = mod.stale_boot_files(vault)
    assert stale == []
    # POSITIVE CONTROL: a WATCHED body moving in the same vault does fire.
    (vault / "Proj" / "Canon.md").write_text("# Canon\n\n## a decision\n")
    commit_all(vault, "canon appears")
    assert len(mod.stale_boot_files(vault)[0]) == 1


def test_a_vault_with_no_git_is_UNCHECKED_not_FRESH(mod, tmp_path):
    v = tmp_path / "nogit"
    (v / "Proj").mkdir(parents=True)
    (v / "Proj" / "Map.md").write_text("# p\n")
    (v / "Proj" / "Kernel.md").write_text("# Boot\n")
    stale, unchecked = mod.stale_boot_files(v)
    assert stale == [] and unchecked and "not a git repository" in unchecked[0]


# --------------------------------------------------------- it is CONFIG ----
def test_the_budget_is_a_number_in_the_limits_file_not_a_literal(mod, tmp_path, monkeypatch):
    """"8k tokens" was PROSE in the vault this came from, and prose is what three of its own Boot
    files drifted 50-70% past while a status table printed OVER and nothing acted."""
    assert mod.budget() == LIMITS["boot_file_budget_bytes"]
    other = tmp_path / "other-limits.json"
    other.write_text(json.dumps({**LIMITS, "boot_file_budget_bytes": 99}))
    # `LIMITS_PATH` is resolved at import time, so moving the env var alone changes nothing — which
    # the first version of this test discovered by failing. The path is what is redirected.
    lim = mod._limits()
    monkeypatch.setattr(lim, "LIMITS_PATH", other)
    lim._reset_for_tests()
    assert mod.budget() == 99
    src = (PLUGIN / "bootfile.py").read_text(encoding="utf-8")
    assert "32000" not in src and "8k" not in src.replace("8k-token", "")


def test_the_shipped_limits_carry_both_keys():
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(HOOKS))
    import limits as lim
    for key in ("boot_file_budget_bytes", "boot_file_warn_bytes"):
        assert shipped[key] == lim.DEFAULTS[key], key
        assert shipped[f"_{key}"], "a threshold ships with its reasoning beside it"


def test_no_private_vocabulary_travelled(mod):
    """The owner's own name is assembled rather than written, because `publish_check.py` refuses
    that string ANYWHERE in the tree — including inside the test that checks for it. It refused
    this file on its first run, which is the check working exactly as intended."""
    src = (PLUGIN / "bootfile.py").read_text(encoding="utf-8").lower()
    owner = "just" + "us"
    for leaked in ("atlas", "speculum", "mnemosyne", "cursus", "lustrum", owner):
        assert leaked not in src, leaked


# ------------------------------------ the second REJECT, and its two siblings ----
GENERIC_PROBE = """
import json, sys
sys.path.insert(0, {hooks!r})
import maintenance
print(json.dumps([m["path"] for m in maintenance.compact_vault()]))
"""


def test_THE_GENERIC_COMPACTOR_NEVER_TOUCHES_A_BOOT_FILE(vault, tmp_path):
    """★ The defect this row was rejected for the SECOND time, and it was never in this module.

    `maintenance.compact_vault` is R3's generic compactor: it walks every memory file and moves the
    OLDEST `## ` sections of anything over `max_memory_file_bytes`. It has no concept of a Boot
    file, no concept of the declared window, and `main()` runs it BEFORE `roll_window` — so for any
    Boot file over the generic bound the unsafe compactor got there first and deleted the standing
    constraints the careful one had just been built to protect. The reviewer reproduced the original
    wipe through this door, against the fixed module.

    One compactor per file class, and this asserts the generic one declines.

    Driven in a SUBPROCESS, for the reason this file has now learned three times: `common.VAULT` is
    resolved ONCE per process, so an in-process import of `maintenance` reads whichever vault the
    first import in that process saw — and passes alone while failing in the suite.
    """
    limits_file = tmp_path / "gen-limits.json"
    limits_file.write_text(json.dumps({**LIMITS, "max_memory_file_bytes": 1500}))
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(real_boot_file(n_resume=40, marker=False))     # the normal, unmigrated state
    role = vault / "Proj" / "Position.md"
    role.write_text("# Position\n" + "".join(f"\n## day {i}\n" + "x" * 200 + "\n"
                                            for i in range(20)))
    assert boot.stat().st_size > 1500 and role.stat().st_size > 1500
    before, role_before = boot.read_text(), role.stat().st_size

    script = tmp_path / "probe.py"
    script.write_text(GENERIC_PROBE.format(hooks=str(HOOKS)), encoding="utf-8")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault),
               GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_LIMITS=str(limits_file),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"))
    p = subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True,
                       env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    moved = json.loads(p.stdout)

    assert boot.read_text() == before, "the generic compactor rewrote a Boot file"
    assert "NEVER delete a memory file." in boot.read_text()
    # POSITIVE CONTROL: it DID compact the ordinary role file beside it, so the refusal above is
    # about the Boot file and not about a compactor that did nothing at all.
    assert any(m.endswith("Position.md") for m in moved), moved
    assert role.stat().st_size < role_before
    assert (vault / "Proj" / "Position-archive.md").is_file()


def test_a_code_FENCE_inside_the_window_is_not_torn(mod, vault):
    """A fence is content, not structure. The reviewer built a window entry containing a fenced
    block with a `## ` line inside it and watched the fence torn across live and archive — the
    opener carried off, the orphaned closing fence promoted to a spurious live heading.
    Byte-preserving, and structurally corrupting in the same spirit as the defect above.

    ★ The fenced entry is LAST on purpose, and the first version of this test was decorative
    without that. The window always keeps its newest entry, so a fence-blind split — which sees the
    fenced entry as TWO — moves the opening half and keeps the closing half, tearing it exactly at
    the boundary. With the entry placed anywhere else, both halves travel together and a
    fence-blind implementation passes the test."""
    boot = vault / "Proj" / "Kernel.md"
    fenced = ("\n## an entry with a fence\n\n```\n## this is not a heading\n```\n"
              + "x" * 400 + "\n")
    # The FIXED part is deliberately larger than the whole target, so the loop cannot reach it by
    # moving window entries and runs all the way to its stop-one-short bound. Without that it
    # stopped early, both halves of the fence travelled together, and a fence-blind implementation
    # passed this test — which is how the first version of it was decorative.
    boot.write_text("# Boot\n\n## Standing constraints\n\nNEVER. " + "n" * 1600 + "\n\n"
                    + WINDOW_OPEN + "\n"
                    + "".join(f"\n## real {i}\n" + "x" * 400 + "\n" for i in range(6))
                    + fenced
                    + WINDOW_CLOSE + "\n\n## Pointer map\n\nbodies\n")
    mod.roll_window(vault)
    seg = (vault / "Proj" / "Kernel-archive.md").read_text()
    live = boot.read_text()
    # EACH file balanced, not just the pair: a tear leaves one fence on each side, and the two
    # halves sum to an even number however badly it went.
    assert live.count("```") % 2 == 0, "the live file holds half a fence"
    assert seg.count("```") % 2 == 0, "the archive holds half a fence"
    # And the fenced entry stayed whole, in one file, with its own heading.
    holder = seg if "an entry with a fence" in seg else live
    assert holder.count("```") == 2 and "## this is not a heading" in holder
    # POSITIVE CONTROL: the window did move something, so none of this is "nothing happened".
    assert "## real 0" in seg


def test_a_differently_spelled_vault_path_does_not_crash(mod, vault, tmp_path):
    """`/tmp` against `/private/tmp` on macOS: two spellings of one directory. `relative_to` raises
    on that, and the reviewer reproduced the crash. Nothing in production reaches it today — which
    is the kind of latent break that surfaces under a symlinked worktree at the worst moment."""
    (vault / "Proj" / "Kernel.md").write_text("# Boot\n")
    link = tmp_path / "vault-by-another-name"
    link.symlink_to(vault)
    assert mod.oversize_boot_files(link) == []            # no crash, and no false finding
    stale, unchecked = mod.stale_boot_files(link)
    assert isinstance(stale, list) and isinstance(unchecked, list)


def test_the_splitter_round_trips_every_byte(mod):
    """`head + "".join(entries)` must reproduce the window EXACTLY, because that identity is what
    makes the compaction's byte conservation an equality rather than an approximation. A splitter
    that drops a separator loses a blank line per entry, forever, invisibly."""
    for window in (
        "\n## a\nbody\n\n## b\nbody\n",
        "preamble\n\n## a\nbody\n",
        "## a\nbody\n",                                   # no leading newline
        "\n## a\n\n```\n## not a heading\n```\n\n## b\n",
        "no headings at all\n",
        "",
    ):
        head, entries = mod.split_entries(window)
        assert head + "".join(entries) == window, repr(window)
        # And each entry keeps the newline before its heading, because `\n## ` is the unit
        # `archive.py` splits on — a mismatch there breaks its retry-deduplication silently, and a
        # slicing splitter that put the newline on the WRONG side would still round-trip.
        for e in entries:
            assert e.startswith("\n## ") or window.startswith(e), repr(e[:20])


# ------------------------------------------- the third REJECT: two more doors ----
# Both SIDES of the fence are large on purpose: under a naive split the entry becomes two chunks of
# ~700 B each, which cannot share a 900 B segment, so the boundary is FORCED to fall inside the
# fence. Sized smaller, the two halves travelled together and a naive implementation passed — the
# same way the first fence test in this file was decorative.
FENCED_ENTRY = ("\n## an entry with a fence\n" + "y" * 700
                + "\n```\n## this is not a heading\n```\n" + "y" * 700 + "\n")


def test_a_fence_survives_a_SEGMENT_BOUNDARY_in_the_archive(mod, vault):
    """★ The third reviewer's second finding. `bootfile` kept the fenced entry whole and handed it
    to `archive.append_entries`, which RE-SPLIT the joined text naively for its own segment
    rollover — so the moment a segment boundary fell inside a fenced entry, the fence was torn
    across two archive files. Harmless while everything lands in one segment, which is exactly why
    the suite never saw it: the fixture kept the fenced entry newest, so it never travelled.

    The splitter is the package's one definition now, and the archive uses it too."""
    archive = mod._archive()
    live = vault / "Proj" / "Kernel.md"
    live.write_text("# Boot\n")
    # A segment limit small enough that the boundary MUST fall between these entries.
    payload = ("".join(f"\n## filler {i}\n" + "z" * 300 + "\n" for i in range(3))
               + FENCED_ENTRY
               + "".join(f"\n## after {i}\n" + "z" * 300 + "\n" for i in range(3)))
    archive.append_entries(live, payload, limit=900)
    segs = archive.segments(live)
    assert len(segs) > 1, "the fixture never crossed a segment boundary; it proves nothing"
    for s in segs:
        body = s.read_text(encoding="utf-8")
        assert body.count("```") % 2 == 0, f"{s.name} holds half a fence"
    holder = [s for s in segs if "an entry with a fence" in s.read_text(encoding="utf-8")]
    assert len(holder) == 1
    assert "## this is not a heading" in holder[0].read_text(encoding="utf-8")


def test_the_CLEANUP_pass_never_touches_a_boot_file(mod, vault, tmp_path):
    """★ The third reviewer's first finding, and the fourth door. `cleanup.py` walks the same memory
    files, had its OWN naive splitter, and applies straight away without asking — so a duplicated
    entry inside a Boot file was "deduplicated" with a torn fence, unattended.

    A Boot file belongs to the window and to nothing else."""
    import importlib.util
    boot = vault / "Proj" / "Kernel.md"
    dup = "\n## a repeated entry\n\nthe same words twice\n"
    boot.write_text("# Boot\n\n## Standing constraints\n\nNEVER.\n" + dup + dup)
    before = boot.read_text()
    script = tmp_path / "cleanup_probe.py"
    script.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(PLUGIN)!r})\n"
        "import cleanup\n"
        "print(json.dumps([p['path'] for p in cleanup.propose()['proposals']]))\n",
        encoding="utf-8")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault),
               GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"))
    # POSITIVE CONTROL: an ordinary file with the same duplicate IS proposed, so the silence about
    # the Boot file is a rule and not a pass that found nothing.
    (vault / "Proj" / "Canon.md").write_text("# Canon\n" + dup + dup)
    p = subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True,
                       env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    paths = json.loads(p.stdout)
    assert "Proj/Canon.md" in paths, paths
    assert not any(x.endswith("Kernel.md") for x in paths), paths
    assert boot.read_text() == before


def l_is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def test_every_splitter_in_the_package_is_the_SAME_ONE(mod):
    """The rule that would have prevented three review rounds. Four modules each split on
    `\\n## `; each tore a fence; each was fixed one at a time.

    It scans EVERY module in the package, because the first version of this very test listed four
    files BY HAND — and the fourth reviewer then found the fifth copy in `eval/simulator/run.py`,
    which the list did not mention. A guard whose reach is a hand-written list protects exactly the
    places somebody had already thought of, which are the places that were already fixed."""
    offenders = []
    for path in sorted(PLUGIN.rglob("*.py")):
        rel = path.relative_to(PLUGIN)
        if rel.parts[0] == "tests" or "__pycache__" in rel.parts:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if l_is_comment(line) or '"""' in line or 'split("\\n## ")' not in line:
                continue
            # A WAIVER AT THE SITE, not an exemption list in this test. A file list here would
            # protect the places somebody had already thought of — which is how the fifth copy hid.
            # A line that genuinely splits something other than memory says so where it is written,
            # and a reader of that line sees the claim it is making.
            window = "\n".join(lines[max(0, i - 4):i])
            if "not-an-entry-splitter" in window:
                continue
            offenders.append(f"{rel}:{i + 1}")
    assert not offenders, f"these modules have their own entry splitter again: {offenders}"


# ------------------- the file that fell through: membership, not a filename ----
CHAIN_PROBE = """
import json, sys
sys.path.insert(0, {plugin!r})
sys.path.insert(0, {hooks!r})
import maintenance
skipped = []
moved = maintenance.compact_vault(cwd=sys.argv[1], skipped=skipped)
print(json.dumps({{"moved": [m["path"] for m in moved],
                  "skipped": [(s["path"], s["why"]) for s in skipped]}}))
"""


def test_a_file_the_SESSION_LOADS_is_never_compacted_even_if_it_is_not_a_boot_file(vault, tmp_path):
    """★ The file that fell through on 2026-09-15, as a test.

    The guard keyed on the FILENAME `Kernel.md`. A vault's top-level index is @-imported by every
    session and is not called Kernel, so it went to the generic compactor and came back 87 lines
    shorter — and every session for fifteen hours booted on the remains. A name is not a property;
    membership in the chain a session actually loads is."""
    index = vault / "Index.md"          # @-imported, NOT named like a boot file
    index.write_text("# Index\n\n## Standing rules\n\nNEVER delete a memory file.\n"
                     + "".join(f"\n## note {i}\n" + "x" * 300 + "\n" for i in range(12)))
    ordinary = vault / "Proj" / "Position.md"
    ordinary.write_text("# Position\n" + "".join(f"\n## day {i}\n" + "x" * 300 + "\n"
                                                 for i in range(12)))
    user_memory = tmp_path / "USER-CLAUDE.md"
    user_memory.write_text(f"# user\n\n@{index}\n")
    limits_file = tmp_path / "chain-limits.json"
    limits_file.write_text(json.dumps({**LIMITS, "max_memory_file_bytes": 1500}))
    before = index.read_text()

    script = tmp_path / "chain_probe.py"
    script.write_text(CHAIN_PROBE.format(plugin=str(PLUGIN), hooks=str(HOOKS)), encoding="utf-8")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault),
               GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_LIMITS=str(limits_file),
               GEDAECHTNIS_USER_MEMORY=str(user_memory),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-roster.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"))
    p = subprocess.run([sys.executable, "-B", str(script), str(tmp_path)],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)

    assert index.read_text() == before, "a file the session @-imports was compacted"
    assert "NEVER delete a memory file." in index.read_text()
    assert any(s[0] == "Index.md" and "boot chain" in s[1] for s in out["skipped"]), out["skipped"]
    # POSITIVE CONTROL: the ordinary file beside it, identical in shape and size, WAS compacted —
    # so the protection is about membership and not about a compactor that did nothing.
    assert any(m.endswith("Position.md") for m in out["moved"]), out["moved"]
    assert ordinary.stat().st_size < 1500


def test_an_unreadable_chain_protects_NOTHING_EXTRA_rather_than_everything(mod, vault):
    """The direction of this failure is deliberate and is the opposite of the usual one.

    A guard that expanded to the whole vault when it could not read the chain would stop every
    compaction and look exactly like a healthy quiet vault — the reassuring failure. It returns an
    empty set instead, so an unreadable chain costs the EXTRA protection and nothing else; Boot
    files stay protected by their own rule, which needs no chain at all."""
    # `X == set() or True` is ALWAYS truthy — the first version of this line asserted nothing at
    # all, and a reviewer caught it. Sixth instance of the decorative-control class in this arc.
    empty = mod.always_loaded("/nonexistent-cwd-xyz")
    assert isinstance(empty, set), type(empty)
    assert not any("nonexistent" in m for m in empty), empty
    # NOTE: a nonexistent cwd does NOT exercise the `except` branch — `boot_chain_files` swallows
    # a missing path silently and never raises. The real fault-injection test for that branch lives
    # in `test_cleanup.py::test_the_empty_chain_fallback_holds_UNDER_A_REAL_FAILURE`; what this one
    # asserts is the weaker, still-useful claim that an absent cwd contributes no members.
    # POSITIVE CONTROL: the same call against a REAL chain returns members, so "empty" above is a
    # verdict about the cwd and not about a function that always returns nothing.
    assert mod.always_loaded(str(Path(__file__).resolve().parents[2])), \
        "always_loaded found no chain even for a real repo; the test above proves nothing"
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text("# Boot\n")
    assert mod.is_protected(boot, chain=set()) == "it is a Boot file"
    assert mod.is_protected(vault / "Proj" / "Position.md", chain=set()) is None
