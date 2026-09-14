"""test_simulator.py — the S0 growth harness (`eval/simulator/run.py`).

Every claim the harness makes gets a POSITIVE control (the thing it detects, detected) and a
NEGATIVE control (the same check, on a vault where the thing is absent, staying quiet). A check
that has only ever been run against the case it was written for cannot tell you whether it fires
on anything else, and its green is a fact about the fixture.

Nothing here reads or writes the real vault or the real state directory: every sandbox is a fresh
temp tree wired through the plugin's own env seam, and `test_sandbox_never_touches_the_real_state`
asserts exactly that rather than trusting it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "eval" / "simulator"))
sys.path.insert(0, str(PLUGIN))

import run as sim  # noqa: E402
import recall  # noqa: E402


# --------------------------------------------------------------- fixtures ----

@pytest.fixture
def sandbox(tmp_path):
    """An installed sandbox: real `init.py`, real vault, real hooks, all inside tmp_path."""
    sb = sim.Sandbox(tmp_path / "box")
    sb.install()
    return sb


def grow(sb, n, stem=sim.BOOT_STEM, seed=1, day=1):
    a = sim.Author(sb, seed)
    a.day = day
    for _ in range(n):
        a.entry(stem, sim.ENTRY_B)
    return a


# ------------------------------------------------------- the sandbox seam ----

def test_sandbox_never_touches_the_real_state(sandbox, tmp_path):
    """Every path the plugin resolves under this env must be inside tmp_path.

    The rule this enforces: a test suite that writes the application's REAL sidecar makes its
    own verdict depend on the machine's state."""
    env = sandbox.env
    for key in ("GEDAECHTNIS_VAULT", "GEDAECHTNIS_STATE_DIR", "GEDAECHTNIS_CONFIG",
                "GEDAECHTNIS_USER_MEMORY", "HOME"):
        assert Path(env[key]).is_relative_to(tmp_path), f"{key} escapes the sandbox: {env[key]}"
    assert sandbox.vault.is_dir() and (sandbox.vault / ".git").exists()
    real_state = Path(os.path.expanduser("~")) / ".claude" / "gedaechtnis"
    assert not Path(env["GEDAECHTNIS_STATE_DIR"]).is_relative_to(real_state)


# ---------------------------------------------------- the boot-bytes anchor ----

def test_in_process_boot_bytes_equals_the_hooks_own_number(sandbox):
    """POSITIVE: the number the harness reports per day is the number the real SessionStart hook
    writes to session-start.json. This equality is what makes the harness a hook harness."""
    grow(sandbox, 5)
    assert sandbox.boot_bytes() == sandbox.hook_boot_bytes(day=1)


def test_snapshot_raises_when_the_two_boot_numbers_disagree(sandbox, monkeypatch):
    """NEGATIVE control for the anchor: if the two measurements ever drift apart, the run must
    RAISE, not average them or prefer one. Proved by making them disagree on purpose.

    Without this, the assertion in `snapshot` is a line nobody has seen fail, and an assertion
    that has never fired is not known to be reachable."""
    author = grow(sandbox, 5)
    monkeypatch.setattr(sandbox, "hook_boot_bytes", lambda day: sandbox.boot_bytes() + 1)
    with pytest.raises(AssertionError, match="boot bytes disagree"):
        sim.snapshot(sandbox, author, day=1, n_questions=2, limit=3, seed=1)


# ------------------------------------------------------------- compaction ----

def test_compaction_fires_over_budget_and_moves_text_rather_than_deleting_it(sandbox):
    """POSITIVE: over budget, entries move to the -archive sibling under a byte-identical heading,
    the boot chain comes back under budget, and NOTHING is lost."""
    grow(sandbox, 60)                      # ~42 KB, well over the 25 KB budget
    before = sandbox.boot_bytes()
    headings_before = set(sim.HEADING_RE.findall(
        sandbox.role(sim.BOOT_STEM).read_text(encoding="utf-8")))
    assert before > 25_000
    moved = sim.compact(sandbox, budget=25_000)
    assert moved > 0
    assert sandbox.boot_bytes() <= 25_000
    arch = sandbox.role(sim.BOOT_STEM).with_name(f"{sim.BOOT_STEM}-archive.md")
    live_after = set(sim.HEADING_RE.findall(
        sandbox.role(sim.BOOT_STEM).read_text(encoding="utf-8")))
    arch_after = set(sim.HEADING_RE.findall(arch.read_text(encoding="utf-8")))
    assert headings_before <= (live_after | arch_after), "a heading was lost, not moved"
    assert len(live_after & arch_after) == 0, "a heading is in both files: it was copied, not moved"


def test_compaction_stays_quiet_under_budget(sandbox):
    """NEGATIVE control: under budget nothing moves and no archive file is created. A compactor
    that only ever ran on an over-budget fixture is not known to leave a small vault alone."""
    grow(sandbox, 3)
    assert sandbox.boot_bytes() < 25_000
    assert sim.compact(sandbox, budget=25_000) == 0
    assert not sandbox.role(sim.BOOT_STEM).with_name(f"{sim.BOOT_STEM}-archive.md").exists()


def test_compaction_stops_at_the_floor_and_never_empties_the_boot_file(sandbox):
    grow(sandbox, 60)
    sim.compact(sandbox, budget=25_000, floor_share=0.4)
    assert sandbox.boot_bytes() > 0
    assert sim.HEADING_RE.findall(sandbox.role(sim.BOOT_STEM).read_text(encoding="utf-8"))


# --------------------------------------------------------- wikilink health ----

def test_wikilink_health_is_one_when_every_target_exists(sandbox):
    """NEGATIVE control: an untouched vault reports perfect health. A link checker that has only
    been shown broken links cannot tell you a clean vault scores 1.0."""
    grow(sandbox, 20)
    resolved, total = sim.wikilink_health(sandbox)
    assert total > 0, "the fixture wrote no links, so this check is VACUOUS"
    assert resolved == total


def test_wikilink_health_falls_when_compaction_moves_a_target(sandbox):
    """POSITIVE: the naive rolling window breaks links, and the harness sees it.

    This is the evidence handed to row R3 (archive seams), not a defect to be patched here."""
    grow(sandbox, 60)
    before_resolved, before_total = sim.wikilink_health(sandbox)
    assert before_resolved == before_total
    sim.compact(sandbox, budget=25_000)
    after_resolved, after_total = sim.wikilink_health(sandbox)
    assert after_total == before_total, "links were deleted, not just broken"
    assert after_resolved < before_resolved


# ------------------------------------------------- the silent recall cliff ----

def test_recall_goes_blind_above_its_own_max_file_bytes(sandbox):
    """POSITIVE: a role file over `recall.MAX_FILE_BYTES` is skipped by recall.py with NO error,
    so the answer inside it becomes unfindable while every surface still looks healthy.

    Found by the harness on 2026-09-14 (extra-high load, day 90: tool recall fell to 0.00 with the
    expected entry present in the vault). Pinned here so the finding cannot silently regress, and
    so row R3's archive seam has a test to satisfy: the fix is a SEGMENTED archive, not a bigger
    constant."""
    author = grow(sandbox, 4)
    q = author.questions[-1]
    hit = sim.bench.score_question(sandbox.vault, q, limit=3, max_bytes=6000)
    assert hit["recall_at_limit"], "the fixture's own answer is not findable; the test is invalid"

    # Push the file holding it past recall's ceiling, changing nothing else about it.
    path = sandbox.vault / q["expected"].split("#")[0]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n## filler\n\n" + ("padding. " * 64 + "\n") * 4000)
    assert path.stat().st_size > recall.MAX_FILE_BYTES
    assert sim.unsearchable(sandbox), "the harness does not report the file it can no longer read"

    blind = sim.bench.score_question(sandbox.vault, q, limit=3, max_bytes=6000)
    assert not blind["recall_at_limit"]
    assert blind["total_hits"] == 0, "expected NO hit at all: the whole file is skipped"


def test_unsearchable_is_empty_for_an_ordinary_vault(sandbox):
    """NEGATIVE control for the same check."""
    grow(sandbox, 20)
    assert sim.unsearchable(sandbox) == []


# ------------------------------------------------------------ recall halves ----

def test_boot_recall_counts_only_what_is_in_the_import_chain(sandbox):
    """POSITIVE + NEGATIVE in one: an answer in the boot file counts; the same answer, moved to the
    archive by compaction, does not — while remaining findable by tool call. That difference is
    the entire point of reporting two recall numbers instead of one."""
    author = grow(sandbox, 60)
    first = author.questions[0]                      # oldest entry: the first to be archived
    assert sim.boot_recall(sandbox, [first]) == 1.0
    sim.compact(sandbox, budget=25_000)
    assert sim.boot_recall(sandbox, [first]) == 0.0
    assert sim.bench.score_question(sandbox.vault, sim.located(sandbox, first), limit=3,
                                    max_bytes=6000)["recall_at_limit"], \
        "compaction must move the text, not lose it: recall.py searches archives"


def test_located_follows_an_entry_into_the_archive(sandbox):
    """POSITIVE + NEGATIVE for `located`, the fix above. Before compaction it must leave the
    question alone; after, it must point at the archive sibling — and for a heading that exists
    nowhere it must leave the miss intact rather than inventing a location."""
    author = grow(sandbox, 60)
    first = author.questions[0]
    assert sim.located(sandbox, first)["expected"] == first["expected"]
    sim.compact(sandbox, budget=25_000)
    moved = sim.located(sandbox, first)["expected"]
    assert moved.startswith(f"{sandbox.region}/{sim.BOOT_STEM}-archive.md#")
    assert moved.partition("#")[2] == first["expected"].partition("#")[2], "heading changed"
    ghost = {**first, "expected": f"{sandbox.region}/{sim.BOOT_STEM}.md#no such heading"}
    assert sim.located(sandbox, ghost)["expected"] == ghost["expected"]


def test_recall_is_none_not_zero_when_there_is_nothing_to_ask(sandbox):
    """An UNMEASURED number must never print as 0.00 — a vault with no facts has no recall score,
    and averaging a None in as zero would make a control arm look like a failing one."""
    assert sim.boot_recall(sandbox, []) is None
    assert sim.tool_recall(sandbox, [], limit=3) == (None, None)
    assert sim.fmt(None, 6).strip() == "—"


# ------------------------------------------------------------------ curves ----

def test_curves(sandbox):
    assert sim.curve_factor("linear", 1) == sim.curve_factor("linear", 300) == 1.0
    assert sim.curve_factor("exponential", 60) == pytest.approx(2.0)
    assert sim.curve_factor("bursty", 1) == 5.0 and sim.curve_factor("bursty", 8) == 0.2
    with pytest.raises(ValueError):
        sim.curve_factor("sigmoid", 1)


# --------------------------------------------------------- end-to-end cells ----

def test_zero_growth_control_cell(tmp_path):
    """The NEGATIVE control arm end to end: no growth, so no compaction, no oversize file, perfect
    links, and recall UNMEASURED rather than zero."""
    r = sim.simulate("none", "linear", horizon=30, budget=25_000, n_questions=4)
    snap = r["snapshots"][30]
    assert r["compactions"] == 0 and r["entries_written"] == 0
    assert snap["oversize"] == [] and snap["unsearchable"] == []
    assert snap["wikilink_health"] == 1.0
    assert snap["tool_recall"] is None and snap["boot_recall"] is None
    assert r["shape_limited"] is False


def test_growing_cell_holds_the_budget(tmp_path):
    """The POSITIVE arm end to end: medium load crosses the budget, compaction fires, and the boot
    file is still under budget at the horizon — the plan's claim, measured through the hook."""
    r = sim.simulate("medium", "linear", horizon=30, budget=25_000, n_questions=6)
    snap = r["snapshots"][30]
    assert r["compactions"] > 0
    assert snap["boot_bytes"] <= 25_000
    assert snap["tool_recall"] is not None


def test_ab_refuses_an_all_unmeasured_arm():
    """A zero-growth load cannot be an A/B arm: averaging its UNMEASURED recall in as 0.0 would
    make the winner an artefact of a cell that had no question to answer. It must REFUSE."""
    with pytest.raises(ValueError, match="UNMEASURED"):
        sim.ab([25_000], ["none"], ["linear"], horizon=10, seeds=1, n_questions=4, limit=3)


def test_ab_reports_a_measured_noise_floor_and_a_winner(tmp_path):
    out = sim.ab([20_000, 25_000], ["low"], ["linear"], horizon=30, seeds=2,
                 n_questions=6, limit=3)
    assert out["winner"] in (20_000, 25_000)
    assert out["population"] == "recent"
    assert out["answered_noise_floor"] >= 0.0
    assert set(out["arms"]) == {"20000", "25000"}
    for arm in out["arms"].values():
        assert arm["n_cells"] == 2


def test_json_output_round_trips(tmp_path):
    path = tmp_path / "out.json"
    rc = sim.main(["--horizon", "10", "--load", "low", "--curve", "linear",
                   "--questions", "4", "--json", str(path), "--quiet"])
    assert rc == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["runs"] and data["runs"][0]["load"] == "low"
    assert "wall_seconds" in data
