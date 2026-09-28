"""boot_citations.py — "superseded since your source" at SessionStart (STALETRUTH M4).

The class it cuts is the one a mid-session reader structurally cannot see: by the time the session
runs, its sources are loaded and believed. So the tests are mostly about SILENCE — a door that
speaks on an ordinary boot is a door people learn to scroll past, and this one fires at the moment a
session is least able to afford noise.

The rule has TWO conditions and the second is the interesting one: the citation must be stale NOW,
AND the owner must have moved AFTER the citing document's own last commit. A document written after
a row closed is citing finished work KNOWINGLY. Every test below varies that comparison directly,
which is why `stale_citations` takes its commit-time function as an argument.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import boot_citations                                 # noqa: E402
import authority                                      # noqa: E402

QUEUE = ("- [x] `q:CU-DONE-1` finished | q:CU-DONE-1\n"
         "- [ ] `q:CU-OPEN-1` live | q:CU-OPEN-1\n")
LEDGER = ("- id: owner-ruling-live-2026-01-01\n  ref: owner-ruling-live-2026-01-01\n\n"
          "- id: owner-ruling-old-2026-01-01\n  ref: owner-ruling-old-2026-01-01\n"
          "  superseded-by: owner-ruling-live-2026-01-01 (2026-01-02; FULL)\n")
QFILES = [Path("/q/queues.md")]
LEDGER_PATH = Path("/q/rulings.log")


def clock(times):
    """A commit-time function driven by a dict, so the comparison can be varied freely."""
    def _c(path, calls):
        calls.append(path)
        return times.get(Path(path))
    return _c


def run(doc, times, include_rows=True):
    return boot_citations.stale_citations(
        [doc], QUEUE, QFILES, authority.records(LEDGER), LEDGER_PATH,
        authority.SUPERSEDED_BY, _commit=clock(times), include_rows=include_rows)


# ------------------------------------------------------------------ the two conditions ----

def test_a_closed_row_whose_owner_MOVED_AFTER_the_document_prints(tmp_path):
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Work proceeds under q:CU-DONE-1.\n", encoding="utf-8")
    out = run(doc, {doc: 100, QFILES[0]: 200})
    assert len(out) == 1 and "CU-DONE-1" in out[0] and "CLOSED" in out[0]


def test_the_same_closed_row_is_SILENT_when_the_document_is_newer(tmp_path):
    """★ The second condition, and the whole reason this is readable. A document written AFTER the
    row closed is citing finished work knowingly — a record, not a mistake. Without this the door
    would fire on every boot forever, for every historical document in the chain."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Work proceeded under q:CU-DONE-1.\n", encoding="utf-8")
    assert run(doc, {doc: 300, QFILES[0]: 200}) == []


def test_closed_rows_are_OFF_by_default_and_a_superseded_ruling_is_not(tmp_path):
    """★ The measured default. In this installation's own boot chain 6 of 8 cited row-ids name
    CLOSED rows (75%) — provenance, which stays true — against 3 of 27 rulings superseded (11%).
    A line the reader can do nothing about, on every boot, is how a warning becomes scenery."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Under q:CU-DONE-1 and owner-ruling-old-2026-01-01.\n", encoding="utf-8")
    times = {doc: 100, QFILES[0]: 200, LEDGER_PATH: 200}
    default = run(doc, times, include_rows=False)
    assert len(default) == 1 and "owner-ruling-old" in default[0]
    assert "CU-DONE-1" not in default[0]
    # ...and the arm still works when a caller asks for it, which is what write time does.
    assert "CU-DONE-1" in "".join(run(doc, times, include_rows=True))


def test_an_OPEN_row_never_prints_however_the_times_fall(tmp_path):
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Live work: q:CU-OPEN-1.\n", encoding="utf-8")
    assert run(doc, {doc: 100, QFILES[0]: 999}) == []


def test_a_superseded_ruling_whose_ledger_moved_after_the_document_prints(tmp_path):
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Under owner-ruling-old-2026-01-01.\n", encoding="utf-8")
    out = run(doc, {doc: 100, LEDGER_PATH: 200})
    assert len(out) == 1 and "owner-ruling-old-2026-01-01" in out[0] and "SUPERSEDED" in out[0]


def test_a_superseded_ruling_is_SILENT_when_the_document_is_NEWER_than_the_ledger(tmp_path):
    """Condition 2 for the ruling arm, which the row arm's version of this test does not cover. A
    document written after the supersession is describing a settled change knowingly; without this
    the door reports every historical mention of every superseded ruling, forever."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Under owner-ruling-old-2026-01-01.\n", encoding="utf-8")
    assert run(doc, {doc: 300, LEDGER_PATH: 200}) == []


def test_closed_rows_are_off_when_the_caller_passes_NOTHING(tmp_path):
    """The DEFAULT, exercised by omitting the argument. The sibling test passes include_rows=False
    explicitly, which asserts the behaviour and says nothing about what an ordinary caller gets —
    and `session_start` is an ordinary caller."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Cites q:CU-DONE-1.\n", encoding="utf-8")
    out = boot_citations.stale_citations(
        [doc], QUEUE, QFILES, authority.records(LEDGER), LEDGER_PATH,
        authority.SUPERSEDED_BY, _commit=clock({doc: 100, QFILES[0]: 200}))
    assert out == []


def test_a_LIVE_ruling_never_prints(tmp_path):
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Under owner-ruling-live-2026-01-01.\n", encoding="utf-8")
    assert run(doc, {doc: 100, LEDGER_PATH: 999}) == []


def test_an_uncommitted_document_says_nothing(tmp_path):
    """No commit to compare against is not evidence of staleness."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Cites q:CU-DONE-1.\n", encoding="utf-8")
    assert run(doc, {QFILES[0]: 999}) == []


def test_an_owner_with_no_commit_time_says_nothing(tmp_path):
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Cites q:CU-DONE-1.\n", encoding="utf-8")
    assert run(doc, {doc: 100}) == []


def test_fenced_and_quoted_citations_do_not_count(tmp_path):
    """Inherited from citations.py, asserted here because this door is the one that meets old
    documents full of quoted examples."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("```\nq:CU-DONE-1\n```\n> owner-ruling-old-2026-01-01\n", encoding="utf-8")
    assert run(doc, {doc: 100, QFILES[0]: 999, LEDGER_PATH: 999}) == []


def test_a_document_that_cites_nothing_costs_no_git_calls(tmp_path):
    """The cost claim, asserted rather than asserted-about: a boot whose documents cite nothing
    must not pay for a single commit lookup."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Plain prose, no ids at all.\n", encoding="utf-8")
    seen = []

    def counting(path, calls):
        seen.append(path)
        calls.append(path)
        return 100
    boot_citations.stale_citations([doc], QUEUE, QFILES, authority.records(LEDGER),
                                   LEDGER_PATH, authority.SUPERSEDED_BY, _commit=counting)
    assert seen == []


def test_commit_time_refuses_to_exceed_its_budget(tmp_path):
    calls = []
    for _ in range(boot_citations.MAX_GIT_CALLS + 5):
        boot_citations._commit_time(tmp_path / "nope.md", calls)
    assert len(calls) == boot_citations.MAX_GIT_CALLS


# ------------------------------------------------------------------ the facts line ----

def test_facts_lines_is_empty_when_nothing_moved():
    assert boot_citations.facts_lines([]) == []


def test_facts_lines_names_the_class_and_blocks_nothing():
    out = "\n".join(boot_citations.facts_lines(["  - /x/CLAUDE.md: q:CU-DONE-1 (row now CLOSED)"]))
    assert "SUPERSEDED SINCE YOUR SOURCE" in out
    assert "Nothing is blocked" in out


# ------------------------------------------------------------------ the live hook ----

def test_session_start_stays_silent_on_a_clean_boot(tmp_path):
    """END-TO-END NEGATIVE CONTROL: a real SessionStart over a sandbox whose documents cite nothing
    stale prints no line of this kind. This is the direction that matters — the hook runs on every
    single boot."""
    vault = tmp_path / "Atlas"
    (vault / "Global").mkdir(parents=True)
    (vault / "Pharos" / "queues").mkdir(parents=True)
    (vault / "Pharos" / "queues" / "q.md").write_text(QUEUE, encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("Nothing stale here. q:CU-OPEN-1 is live.\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-memory.md"),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"))
    p = subprocess.run([sys.executable, str(HOOKS / "session_start.py")],
                       input=json.dumps({"session_id": "s1", "hook_event_name": "SessionStart",
                                         "cwd": str(repo)}),
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    assert "SUPERSEDED SINCE YOUR SOURCE" not in p.stdout
    assert "session_start/boot_citations" not in (state / "hook-errors.log").read_text(
        encoding="utf-8") if (state / "hook-errors.log").exists() else True


def test_an_owner_moved_in_the_SAME_commit_is_not_stale(tmp_path):
    """★ The boundary of the comparison the module calls its rule. Equal commit times mean the
    document and its owner moved TOGETHER — someone updating both in one act, which is the opposite
    of the thing being reported. `>=` would turn every such pair into a warning."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Under owner-ruling-old-2026-01-01.\n", encoding="utf-8")
    assert run(doc, {doc: 200, LEDGER_PATH: 200}) == []
    assert run(doc, {doc: 199, LEDGER_PATH: 200}) != []     # one second later DOES report


def test_the_row_arms_boundary_is_the_same(tmp_path):
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Cites q:CU-DONE-1.\n", encoding="utf-8")
    assert run(doc, {doc: 200, QFILES[0]: 200}, include_rows=True) == []
    assert run(doc, {doc: 199, QFILES[0]: 200}, include_rows=True) != []


def test_truncation_is_DISCLOSED_even_when_nothing_was_found(tmp_path, monkeypatch):
    """An undisclosed cap is a silent miss: the reader cannot tell "nothing is stale" from "I
    stopped looking". Disclosed even on a clean result, because that is precisely when it misleads."""
    monkeypatch.setattr(boot_citations, "MAX_DOCS", 2)
    docs = []
    for i in range(5):
        d = tmp_path / f"doc{i}.md"
        d.write_text("Plain prose, nothing cited.\n", encoding="utf-8")
        docs.append(d)
    out = boot_citations.stale_citations(docs, "", [], [], None, None, _commit=clock({}))
    assert len(out) == 1 and "not every boot document was checked" in out[0]
    assert "5 documents" in out[0]


def test_the_document_cap_actually_STOPS_at_the_cap(tmp_path, monkeypatch):
    """The disclosure alone does not pin the slice: with the cap announced but not applied, every
    document is still examined and the message stays true-looking. Assert the WORK stopped."""
    monkeypatch.setattr(boot_citations, "MAX_DOCS", 2)
    docs, times = [], {}
    for i in range(5):
        d = tmp_path / f"doc{i}.md"
        d.write_text("Under owner-ruling-old-2026-01-01.\n", encoding="utf-8")
        docs.append(d)
        times[d] = 100
    times[LEDGER_PATH] = 200
    out = boot_citations.stale_citations(docs, "", [], authority.records(LEDGER), LEDGER_PATH,
                                         authority.SUPERSEDED_BY, _commit=clock(times))
    findings = [l for l in out if "not every boot document" not in l]
    assert len(findings) == 2, f"expected the cap to stop after 2 documents, got {len(findings)}"


def test_no_truncation_line_when_everything_was_checked(tmp_path):
    """The negative control: the disclosure must not appear on an ordinary boot."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Plain prose.\n", encoding="utf-8")
    assert boot_citations.stale_citations([doc], "", [], [], None, None, _commit=clock({})) == []


def test_a_boot_citing_only_LIVE_things_pays_no_git_calls(tmp_path):
    """The real cost claim, and the ordinary case. Deciding staleness is pure; fetching a commit
    time is a subprocess. A document full of live citations must cost nothing — the earlier version
    paid one `git log` per document that cited ANYTHING, stale or not."""
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("Under owner-ruling-live-2026-01-01 and q:CU-OPEN-1.\n", encoding="utf-8")
    seen = []

    def counting(path, calls):
        seen.append(path)
        calls.append(path)
        return 100
    boot_citations.stale_citations([doc], QUEUE, QFILES, authority.records(LEDGER), LEDGER_PATH,
                                   authority.SUPERSEDED_BY, _commit=counting, include_rows=True)
    assert seen == [], f"paid {len(seen)} commit lookup(s) for a document with nothing stale"
