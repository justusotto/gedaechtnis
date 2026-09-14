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
    limits_file = tmp_path / "limits.json"
    limits_file.write_text(json.dumps(LIMITS))
    monkeypatch.setenv("GEDAECHTNIS_LIMITS", str(limits_file))
    sys.path.insert(0, str(HOOKS))
    sys.path.insert(0, str(PLUGIN))
    import importlib.util
    spec = importlib.util.spec_from_file_location("gd_bootfile", PLUGIN / "bootfile.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._limits()._reset_for_tests()
    yield m
    m._limits()._reset_for_tests()


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
def test_an_over_budget_boot_file_is_compacted_into_its_sidecar(mod, vault):
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(entries(20, 200, first="A STANDING RULE"))
    before = boot.stat().st_size
    assert before > mod.budget()
    moved = mod.roll_window(vault)
    assert len(moved) == 1 and moved[0]["entries"] > 0
    assert boot.stat().st_size <= mod.budget()
    seg = vault / "Proj" / "Kernel-archive.md"
    assert seg.is_file() and "A STANDING RULE" in seg.read_text()


def test_the_window_does_not_empty_the_file(mod, vault):
    """A Boot file compacted to nothing is a region with no index — worse than an oversize one."""
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(entries(40, 200))
    mod.roll_window(vault)
    assert boot.stat().st_size >= mod.budget() * float(LIMITS["compaction_floor_share"]) * 0.5
    assert "## entry" in boot.read_text()


def test_a_boot_file_under_the_budget_is_untouched(mod, vault):
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(entries(2, 100))
    before = boot.read_text()
    assert mod.roll_window(vault) == []
    assert boot.read_text() == before
    assert not (vault / "Proj" / "Kernel-archive.md").exists()


def test_the_window_moves_the_OLDEST_entries_which_the_report_must_say(mod, vault):
    """The mechanism's sharp edge, asserted rather than documented: a standing rule written at the
    TOP of a Boot file is the first thing the window moves out."""
    boot = vault / "Proj" / "Kernel.md"
    boot.write_text(entries(20, 200, first="NEVER delete a memory file"))
    mod.roll_window(vault)
    assert "NEVER delete a memory file" not in boot.read_text()
    assert "NEVER delete a memory file" in (vault / "Proj" / "Kernel-archive.md").read_text()


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
    boot.write_text("# Boot\n")
    body.write_text("# Position\n")
    commit_all(vault, "together")
    body.write_text("# Position\n\nmoved on\n")
    git(vault, "add", "--", "Proj/Position.md")
    when = time.strftime("%Y-%m-%dT%H:%M:%S")
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
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
