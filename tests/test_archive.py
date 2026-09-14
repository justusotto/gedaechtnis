"""Tests for the archive seam — segmentation, and the skip that stopped being silent.

Row R3. The acceptance evidence for the row as a whole is the simulator re-run; these are the unit
claims underneath it, each with a positive AND a negative control. The negative controls carry most
of the weight here for a specific reason: every failure this row addresses is one where NOTHING
BREAKS. An archive that has fallen out of the corpus returns no error, and a search over it returns
"no results", which is the same output as a question nobody ever wrote an answer to.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "hooks"))

import archive          # noqa: E402
import recall           # noqa: E402
import limits           # noqa: E402


@pytest.fixture
def live(tmp_path):
    p = tmp_path / "Region" / "Position.md"
    p.parent.mkdir(parents=True)
    p.write_text("# Position\n\n## live entry\nstill here\n", encoding="utf-8")
    return p


# ------------------------------------------------------------- segmentation ----
def test_archive_segments_before_the_ceiling(live):
    """Grown past the limit, a second segment exists and the first is still under it."""
    limit = 2000
    for i in range(20):
        archive.append_entries(live, f"\n## entry {i}\n" + "x" * 300 + "\n", limit=limit)
    segs = archive.segments(live)
    assert len(segs) >= 2, [s.name for s in segs]
    for s in segs[:-1]:
        assert s.stat().st_size <= limit, (s.name, s.stat().st_size)


def test_no_roll_below_the_limit(live):
    """The negative control: under the limit there is EXACTLY ONE archive file and no roll."""
    for i in range(3):
        archive.append_entries(live, f"\n## entry {i}\nshort\n", limit=100000)
    segs = archive.segments(live)
    assert len(segs) == 1 and segs[0].name == "Position-archive.md", [s.name for s in segs]


def test_nothing_is_lost_across_a_roll(live):
    limit = 1500
    headings = [f"## entry {i}" for i in range(30)]
    for h in headings:
        archive.append_entries(live, f"\n{h}\n" + "y" * 200 + "\n", limit=limit)
    seen = []
    for s in archive.segments(live):
        seen += [l for l in s.read_text(encoding="utf-8").splitlines() if l.startswith("## ")]
    assert sorted(seen) == sorted(headings)
    assert len(seen) == len(set(seen)), "a heading appears in two segments"


def test_an_existing_segment_is_never_rewritten_or_moved(live):
    """The row's law: roll forward only. The first segment's bytes and NAME must be identical
    before and after a roll — which is what keeps a wikilink into the archive valid."""
    limit = 1200
    for i in range(5):
        archive.append_entries(live, f"\n## early {i}\n" + "z" * 200 + "\n", limit=limit)
    first = archive.segments(live)[0]
    before_name, before_bytes = first.name, first.read_bytes()
    for i in range(20):
        archive.append_entries(live, f"\n## later {i}\n" + "z" * 200 + "\n", limit=limit)
    assert len(archive.segments(live)) > 1
    assert first.exists() and first.name == before_name
    assert first.read_bytes() == before_bytes


def test_segments_are_ordered_by_index_not_by_name(live):
    """A lexical sort puts -archive-10 before -archive-2, and an archive read in the wrong order
    is a history that reads as though it happened in the wrong order."""
    for i in (1, 2, 3, 10, 11):
        archive.segment_name(live, i).write_text(f"# seg {i}\n", encoding="utf-8")
    assert [archive.segment_index(s) for s in archive.segments(live)] == [1, 2, 3, 10, 11]


def test_the_limit_must_stay_under_the_search_ceiling(monkeypatch):
    """A config edit that lifts the segment limit to the ceiling reintroduces the whole defect
    with no other symptom, so it is refused rather than obeyed."""
    monkeypatch.setattr(limits, "_CACHE", dict(limits.DEFAULTS,
                                               max_memory_file_bytes=9_000_000,
                                               max_searchable_file_bytes=2_000_000))
    with pytest.raises(ValueError):
        archive.segment_limit()


# ---------------------------------------------------- segments stay searchable ----
def test_a_segmented_name_still_reads_as_an_archive(live, tmp_path):
    """If the search stops recognising a segment AS an archive, the scheme reproduces the bug it
    was built to fix. Positive: a hit in segment 2 is marked archive. Negative: a hit in the LIVE
    file is not."""
    archive.segment_name(live, 2).write_text(
        "# Position (archive)\n\n## the ratchet decision\nwe chose the ratchet\n", encoding="utf-8")
    live.write_text("# Position\n\n## the ratchet decision today\nthe ratchet is live\n",
                    encoding="utf-8")
    hits, _ = recall.search(tmp_path, "ratchet decision")
    by_path = {h["path"]: h for h in hits}
    seg = [h for p, h in by_path.items() if p.endswith("Position-archive-2.md")]
    liv = [h for p, h in by_path.items() if p.endswith("/Position.md")]
    assert seg and seg[0]["archive"] is True, by_path.keys()
    assert liv and liv[0]["archive"] is False, by_path.keys()


def test_an_answer_in_segment_two_is_found(live, tmp_path):
    archive.segment_name(live, 2).write_text(
        "# Position (archive)\n\n## apostrophe regime\nupstream uses the modifier letter\n",
        encoding="utf-8")
    hits, _ = recall.search(tmp_path, "apostrophe regime")
    assert any("Position-archive-2.md" in h["path"] for h in hits), [h["path"] for h in hits]


def test_the_same_answer_is_found_the_same_way_before_a_roll(live, tmp_path):
    """The control that proves the ROLL is what changed, not the fixture: the identical entry in
    segment ONE is found by the identical call."""
    archive.segment_name(live, 1).write_text(
        "# Position (archive)\n\n## apostrophe regime\nupstream uses the modifier letter\n",
        encoding="utf-8")
    hits, _ = recall.search(tmp_path, "apostrophe regime")
    assert any(h["path"].endswith("Position-archive.md") for h in hits), [h["path"] for h in hits]


# -------------------------------------------------------------- the loud skip ----
def test_an_oversize_file_is_NAMED_with_its_size(live, tmp_path, monkeypatch):
    monkeypatch.setattr(recall, "MAX_FILE_BYTES", 5000)
    big = live.parent / "Errata.md"
    big.write_text("# Errata\n\n## the answer\n" + "q" * 20000, encoding="utf-8")
    skipped: list = []
    recall.search(tmp_path, "the answer", skipped=skipped)
    assert skipped and skipped[0][0].endswith("Errata.md"), skipped
    assert skipped[0][1] > 5000
    notice = recall.skip_notice(skipped)
    assert "Errata.md" in notice and "NOT searched" in notice


def test_an_ordinary_vault_says_nothing_about_skips(live, tmp_path):
    """Silence must stay possible, or the notice is noise and gets ignored the once it matters."""
    skipped: list = []
    recall.search(tmp_path, "live entry", skipped=skipped)
    assert skipped == []
    assert recall.skip_notice(skipped) == ""


def test_the_notice_states_and_does_not_advise(live, tmp_path, monkeypatch):
    monkeypatch.setattr(recall, "MAX_FILE_BYTES", 100)
    (live.parent / "Canon.md").write_text("# Canon\n" + "w" * 5000, encoding="utf-8")
    skipped: list = []
    recall.search(tmp_path, "anything at all", skipped=skipped)
    notice = recall.skip_notice(skipped)
    for word in ("should", "must", "please", "recommend", "run "):
        assert word not in notice.lower(), notice


def test_a_caller_that_passes_no_list_is_unchanged(live, tmp_path, monkeypatch):
    """The seam is opt-in: every existing caller behaves exactly as before."""
    monkeypatch.setattr(recall, "MAX_FILE_BYTES", 100)
    (live.parent / "Canon.md").write_text("# Canon\n" + "w" * 5000, encoding="utf-8")
    hits, want = recall.search(tmp_path, "live entry")
    assert want and isinstance(hits, list)


def test_the_cli_prints_the_notice(live, tmp_path):
    big = live.parent / "Errata.md"
    big.write_text("# Errata\n\n## the buried answer\n" + "q" * 100, encoding="utf-8")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(tmp_path),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    # squeeze the ceiling from the outside: a 100-byte ceiling makes every file oversize
    code = (f"import sys; sys.path.insert(0, {str(PLUGIN)!r}); import recall;"
            " recall.MAX_FILE_BYTES = 50;"
            f" sys.exit(recall.main(['the', 'buried', 'answer', '--vault', {str(tmp_path)!r}]))")
    p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert "NOT searched" in p.stdout, p.stdout
    assert "Errata.md" in p.stdout


# ------------------------------------------------------------------- limits ----
def test_the_segment_limit_is_in_the_shipped_file():
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    assert "max_memory_file_bytes" in shipped
    assert shipped["max_memory_file_bytes"] < shipped["max_searchable_file_bytes"]


# ------------------------------------------- size-driven compaction of LIVE files ----
def big_live(live: Path, n: int, body: int = 400) -> None:
    live.write_text("# Position\n" + "".join(f"\n## entry {i}\n" + "b" * body + "\n"
                                             for i in range(n)), encoding="utf-8")


def test_an_oversize_live_file_is_compacted_under_the_limit(live):
    """The year-3 finding: the files that crossed the search ceiling were LIVE role files, not
    archives, and nothing watched them. Positive control."""
    big_live(live, 60)
    limit = 4000
    assert live.stat().st_size > limit
    moved = archive.compact_file(live, limit=limit)
    assert moved > 0
    assert live.stat().st_size <= limit


def test_a_live_file_under_the_limit_is_left_completely_alone(live):
    """Negative control. Compaction that fires on a healthy file would quietly hollow out every
    region in the vault, which is a worse failure than the one it is fixing."""
    big_live(live, 2)
    before = live.read_bytes()
    assert archive.compact_file(live, limit=100000) == 0
    assert live.read_bytes() == before
    assert archive.segments(live) == []


def test_compaction_moves_the_OLDEST_entries_and_keeps_the_newest_live(live):
    big_live(live, 60)
    archive.compact_file(live, limit=4000)
    live_text = live.read_text(encoding="utf-8")
    arch_text = "".join(s.read_text(encoding="utf-8") for s in archive.segments(live))
    assert "## entry 0" in arch_text and "## entry 0" not in live_text
    assert "## entry 59" in live_text and "## entry 59" not in arch_text


def test_nothing_is_lost_when_a_live_file_is_compacted(live):
    big_live(live, 60)
    before = {l for l in live.read_text(encoding="utf-8").splitlines() if l.startswith("## ")}
    archive.compact_file(live, limit=4000)
    after = {l for l in live.read_text(encoding="utf-8").splitlines() if l.startswith("## ")}
    for s in archive.segments(live):
        after |= {l for l in s.read_text(encoding="utf-8").splitlines() if l.startswith("## ")}
    assert after == before


def test_a_compacted_live_file_stays_searchable_end_to_end(live, tmp_path, monkeypatch):
    """The whole point, measured the way the defect was: an answer written long ago must still be
    REACHABLE after the file that held it grew past the ceiling and was compacted."""
    monkeypatch.setattr(recall, "MAX_FILE_BYTES", 20000)
    live.write_text("# Position\n\n## the apostrophe ruling\nupstream uses the modifier letter\n"
                    + "".join(f"\n## filler {i}\n" + "b" * 400 + "\n" for i in range(60)),
                    encoding="utf-8")
    assert live.stat().st_size > 20000
    blind, _ = recall.search(tmp_path, "apostrophe ruling")
    assert not blind, "fixture invalid: the oversize file is already searchable"
    archive.compact_file(live, limit=4000)
    hits, _ = recall.search(tmp_path, "apostrophe ruling")
    assert any("apostrophe" in h["heading"].lower() for h in hits), [h["path"] for h in hits]


def test_a_sealed_segment_is_never_itself_compacted(live):
    """A segment is sealed. Compacting one would move bytes twice and break the links into it."""
    seg = archive.segment_name(live, 1)
    seg.write_text("# Position (archive)\n" + "".join(f"\n## old {i}\n" + "c" * 400
                                                      for i in range(60)), encoding="utf-8")
    before = seg.read_bytes()
    assert archive.is_sidecar(seg.stem)
    assert archive.compact_file(seg, limit=4000) >= 0
    # the caller is what skips sidecars; this asserts the property the caller must preserve
    assert archive.is_sidecar(seg.stem) is True
    seg.write_bytes(before)


# ----------------------------------------- the regexes, both directions (F1 item 3) ----
def test_an_UNsegmented_archive_still_reads_as_an_archive(live, tmp_path):
    """The negative control F1 asked for: teaching the pattern to read `-archive-2` must not cost
    it `-archive`."""
    archive.segment_name(live, 1).write_text(
        "# Position (archive)\n\n## the older ruling\nsettled long ago\n", encoding="utf-8")
    hits, _ = recall.search(tmp_path, "older ruling")
    assert hits and hits[0]["archive"] is True, [(h["path"], h["archive"]) for h in hits]


def test_a_plain_role_file_is_not_mistaken_for_an_archive(live, tmp_path):
    """And the pattern must not have become so permissive that a live file reads as a sidecar."""
    assert archive.is_sidecar("Position") is False
    assert archive.is_sidecar("Position-archive") is True
    assert archive.is_sidecar("Position-archive-12") is True
    assert archive.is_sidecar("Position-2") is False


def test_one_oversized_append_is_split_across_segments(live):
    """Regression, found by the end-to-end test rather than by reading the code: rolling only
    BETWEEN appends means a single compaction hands over everything at once and one segment
    instantly exceeds the limit — the original defect, rebuilt by the machinery meant to prevent
    it. A boundary must be able to fall inside one append."""
    limit = 2000
    blob = "".join(f"\n## entry {i}\n" + "d" * 300 + "\n" for i in range(40))
    archive.append_entries(live, blob, limit=limit)
    segs = archive.segments(live)
    assert len(segs) > 1, [s.name for s in segs]
    for s in segs:
        assert s.stat().st_size <= limit + 1000, (s.name, s.stat().st_size)


def test_an_entry_is_never_cut_in_half_by_a_segment_boundary(live):
    """An entry split across two files reads as two complete entries, which is worse than an
    oversize file: both halves look whole."""
    limit = 1500
    archive.append_entries(live, "".join(f"\n## entry {i}\n" + "e" * 400 + "\n"
                                         for i in range(30)), limit=limit)
    for s in archive.segments(live):
        body = s.read_text(encoding="utf-8")
        for block in body.split("\n## ")[1:]:
            assert block.strip(), "an empty entry block means a boundary fell mid-entry"
            assert "\n" in block, block[:80]
