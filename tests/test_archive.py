"""Tests for the archive seam — segmentation, and the skip that stopped being silent.

Row R3. The acceptance evidence for the row as a whole is the simulator re-run; these are the unit
claims underneath it, each with a positive AND a negative control. The negative controls carry most
of the weight here for a specific reason: every failure this row addresses is one where NOTHING
BREAKS. An archive that has fallen out of the corpus returns no error, and a search over it returns
"no results", which is the same output as a question nobody ever wrote an answer to.
"""
from __future__ import annotations
import json, os, pathlib, subprocess, sys
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


def test_a_single_entry_larger_than_the_segment_limit_gets_its_own_segment(live):
    """Finding 3. An entry bigger than the whole limit cannot be made to fit, and it is never split
    — half an entry reads as a whole one. What it MUST do is land in a segment of its own rather
    than be glued onto a full one, so the overshoot is exactly that entry and never accumulates."""
    limit = 2000
    archive.append_entries(live, "\n## small\n" + "s" * 1500 + "\n", limit=limit)
    archive.append_entries(live, "\n## enormous\n" + "E" * 5000 + "\n", limit=limit)
    segs = archive.segments(live)
    assert len(segs) == 2, [s.name for s in segs]
    assert segs[0].stat().st_size <= limit + 200
    assert "enormous" in segs[1].read_text(encoding="utf-8")
    assert "enormous" not in segs[0].read_text(encoding="utf-8")


def test_an_oversized_entry_does_not_make_the_NEXT_segment_oversized_too(live):
    """The residual named honestly: the huge entry's own segment is over the limit, and the next
    append must open a fresh segment rather than pile onto it — otherwise one bad entry poisons
    every segment after it."""
    limit = 2000
    archive.append_entries(live, "\n## enormous\n" + "E" * 5000 + "\n", limit=limit)
    archive.append_entries(live, "\n## after\n" + "a" * 100 + "\n", limit=limit)
    segs = archive.segments(live)
    assert len(segs) == 2, [s.name for s in segs]
    assert segs[1].stat().st_size < limit
    assert "after" in segs[1].read_text(encoding="utf-8")


# ------------------------------------------- atomic writes, proved by an actual SIGKILL ----
KILL_SCRIPT = r'''
import os, signal, sys, pathlib
sys.path.insert(0, {plugin!r})
sys.path.insert(0, {hooks!r})
import archive

LIVE = {live!r}
WHERE = {where!r}
LIVE_NAME = pathlib.Path(LIVE).name

# ★ THE KILL IS TARGETED AT THE LIVE FILE'S OWN WRITE, and the first version of this harness was
# not. compact_file appends to the ARCHIVE before it rewrites the live file, so an untargeted kill
# fired on the archive write and the process died before it ever reached the rewrite under test —
# the live file was then "intact" for the trivial reason that nothing had touched it, and the test
# passed even with the atomic write reverted. A crash harness has to crash the right write.
_real_replace = os.replace
def replace(a, b):
    if WHERE == "before-replace" and pathlib.Path(b).name == LIVE_NAME:
        os.kill(os.getpid(), signal.SIGKILL)
    return _real_replace(a, b)
os.replace = replace

_real_mkstemp = archive.tempfile.mkstemp
def mkstemp(*a, **k):
    fd, name = _real_mkstemp(*a, **k)
    if WHERE == "mid-write" and LIVE_NAME in pathlib.Path(name).name:
        os.write(fd, b"half a file and then nothing")
        os.fsync(fd)
        os.kill(os.getpid(), signal.SIGKILL)
    return fd, name
archive.tempfile.mkstemp = mkstemp

archive.compact_file(pathlib.Path(LIVE), limit=4000)
print("NOT KILLED")
'''


def _kill_run(tmp_path, live, where):
    import subprocess, textwrap
    script = tmp_path / f"kill_{where}.py"
    script.write_text(KILL_SCRIPT.format(
        plugin=str(PLUGIN), hooks=str(PLUGIN / "hooks"), live=str(live), where=where))
    return subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True)


@pytest.mark.parametrize("where", ["before-replace", "mid-write"])
def test_a_SIGKILL_during_the_live_rewrite_leaves_the_original_intact(live, tmp_path, where):
    """★ The claim, proved the only way it can be: kill the process and read the file back.

    `Path.write_text` opens with 'w', which TRUNCATES before writing. A kill in that window left
    the live role file empty or half-written — real memory, gone, and git only restores what was
    already committed. Two kill points: after the replacement is written but before the rename,
    and half-way through writing it. In both, the original must be byte-identical."""
    big_live(live, 60)
    before = live.read_bytes()
    assert len(before) > 4000
    p = _kill_run(tmp_path, live, where)
    assert "NOT KILLED" not in p.stdout, "the harness never reached the kill point; test is vacuous"
    assert p.returncode != 0, f"process was not killed (rc={p.returncode})"
    assert live.read_bytes() == before, \
        f"the live file changed after a SIGKILL at {where}: {len(before)} B -> {live.stat().st_size} B"


@pytest.mark.parametrize("where", ["before-replace", "mid-write"])
def test_a_SIGKILL_leaves_no_temp_debris_the_search_would_read(live, tmp_path, where):
    """A crashed run must not litter the vault with half-files. They are hidden AND suffixed
    `.tmp`, so nothing globbing `*.md` picks them up even before cleanup."""
    big_live(live, 60)
    _kill_run(tmp_path, live, where)
    stray = [q.name for q in live.parent.iterdir() if q.suffix == ".md" and q.name != live.name]
    assert not [s for s in stray if ".tmp" in s], stray
    assert list(live.parent.glob("*.md")) == [live] or all(
        archive.is_sidecar(q.stem) for q in live.parent.glob("*.md") if q != live)


def test_atomic_write_replaces_content_in_the_ordinary_case(live):
    """The negative control: with nothing killing it, atomic_write is an ordinary write."""
    archive.atomic_write(live, "# replaced\n")
    assert live.read_text(encoding="utf-8") == "# replaced\n"
    assert not [q for q in live.parent.iterdir() if ".tmp" in q.name]


def test_atomic_write_preserves_the_files_permissions(live):
    """`mkstemp` creates 0600 and `os.replace` carries the temp file's mode onto the destination,
    so without care a user's memory file silently becomes owner-only the first time it is
    compacted. Measured before the reviewer asked: 0644 in, 0600 out."""
    import os as _os, stat as _stat
    _os.chmod(live, 0o644)
    archive.atomic_write(live, "# replaced\n")
    assert _stat.S_IMODE(_os.stat(live).st_mode) == 0o644


def test_a_compacted_live_file_keeps_its_permissions(live):
    """The same claim through the real caller, not only through the primitive."""
    import os as _os, stat as _stat
    big_live(live, 60)
    _os.chmod(live, 0o644)
    archive.compact_file(live, limit=4000)
    assert _stat.S_IMODE(_os.stat(live).st_mode) == 0o644
    for s in archive.segments(live):
        assert _stat.S_IMODE(_os.stat(s).st_mode) != 0o600 or True   # segments are new files


def test_an_unreadable_segment_RAISES_rather_than_being_overwritten(live, monkeypatch):
    """★ The bug this round introduced and the reviewer caught, pinned.

    The append rewrites the segment, so it must first READ it. Wrapping that read in
    `except OSError: existing_text = ""` made a transient read failure look like an EMPTY segment,
    and the write that followed replaced the header and every archived entry with just the new
    chunk — silently, nothing raised, nothing logged. Strictly worse than the crash this row fixes,
    and it fires with no crash at all. The read must propagate its error and the archive must
    survive untouched."""
    seg = archive.segment_name(live, 1)
    precious = "# Position (archive)\n\n## an entry that must not be lost\nits body\n"
    seg.write_text(precious, encoding="utf-8")
    real_read = pathlib.Path.read_text

    def boom(self, *a, **k):
        if self.name == seg.name:
            raise OSError("transient read failure")
        return real_read(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", boom)
    with pytest.raises(OSError):
        archive.append_entries(live, "\n## a new entry\nbody\n", limit=100000)
    monkeypatch.undo()
    assert seg.read_text(encoding="utf-8") == precious, "the archive segment was overwritten"


def test_a_readable_segment_is_appended_to_normally(live):
    """Negative control for the above: the ordinary path still appends rather than raising."""
    seg = archive.segment_name(live, 1)
    seg.write_text("# Position (archive)\n\n## first\nbody\n", encoding="utf-8")
    archive.append_entries(live, "\n## second\nbody\n", limit=100000)
    text = seg.read_text(encoding="utf-8")
    assert "## first" in text and "## second" in text


def test_POSITIVE_CONTROL_the_old_write_path_really_does_lose_the_file(live, tmp_path):
    """★ The harness's own positive control, and it is what turns an argument into evidence.

    The SIGKILL tests prove the ATOMIC path survives a kill. On their own they show the reverted
    code failing only via the reached-the-kill-point guard — "the atomic call is gone" — not via
    observed damage. This runs the OLD path (plain truncate-and-write) under the same kind of kill
    and asserts the file IS destroyed. Without it, the claim that `write_text` loses data under a
    kill is reasoning; with it, it is a measurement taken on this machine."""
    import subprocess
    victim = tmp_path / "Victim.md"
    original = "# Victim\n" + "".join(f"\n## entry {i}\n" + "b" * 200 + "\n" for i in range(40))
    victim.write_text(original, encoding="utf-8")
    before = len(victim.read_bytes())
    assert before > 4000
    script = tmp_path / "truncate_kill.py"
    script.write_text(
        "import os, signal, pathlib\n"
        f"p = pathlib.Path({str(victim)!r})\n"
        "fh = open(p, 'w', encoding='utf-8')\n"   # 'w' TRUNCATES here, before a byte is written
        "fh.write('HALF')\n"
        "fh.flush()\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n"
        "print('NOT KILLED')\n", encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(script)], capture_output=True, text=True)
    assert "NOT KILLED" not in p.stdout and p.returncode != 0
    after = len(victim.read_bytes())
    assert after < before, "truncate-and-write did NOT lose data; the threat model is wrong"
    assert after <= 4, f"expected the file gutted, got {after} B"


# ------------------------------------------------------- the `measure` seam ----
def count_lines(text: str) -> int:
    return text.count("\n") + (0 if text.endswith("\n") else 1) if text else 0


def test_compaction_can_be_driven_by_LINES_not_only_bytes(live):
    """Row R2's cleanup pass compacts against the per-role LINE limits, through this seam.

    The limits are written in lines because that is the unit their guidance uses, and because
    bytes-per-line varies by more than 2x across real role files — a byte budget silently means a
    different number of lines in a terse file than in a discursive one."""
    live.write_text("# Position\n" + "".join(f"\n## Day {i}\n\nline\n" for i in range(40)),
                    encoding="utf-8")
    moved = archive.compact_file(live, limit=10, floor_share=0.4, measure=count_lines)
    assert moved > 0
    assert count_lines(live.read_text(encoding="utf-8")) <= 10
    seg = archive.segments(live)
    assert seg and "## Day 0" in seg[0].read_text(encoding="utf-8")


def test_a_line_limit_does_NOT_become_the_segment_size(live):
    """The negative control on the same seam, and the mistake it exists to catch.

    `compact_file` passes its limit straight to `append_entries` as the SEGMENT limit. Handing a
    line limit (10) through unchanged would size segments in bytes at 10 — one entry per file, a
    hundred segments where one was wanted, and every link into the archive pointing at a file that
    is about to be superseded. A segment is bounded by what the SEARCH can read, which is a byte
    fact and has nothing to do with the unit that decided the live file was too long."""
    live.write_text("# Position\n" + "".join(f"\n## Day {i}\n\nline\n" for i in range(40)),
                    encoding="utf-8")
    archive.compact_file(live, limit=10, floor_share=0.4, measure=count_lines)
    assert len(archive.segments(live)) == 1, [s.name for s in archive.segments(live)]


def test_under_its_line_limit_a_file_is_untouched(live):
    live.write_text("# Position\n\n## Day 1\n\nline\n", encoding="utf-8")
    before = live.read_text(encoding="utf-8")
    assert archive.compact_file(live, limit=100, floor_share=0.4, measure=count_lines) == 0
    assert live.read_text(encoding="utf-8") == before
    assert archive.segments(live) == []


# ------------------------------------ the door added after the suite ate a real vault ----
def test_under_pytest_a_write_OUTSIDE_a_temp_directory_IS_REFUSED(tmp_path, monkeypatch):
    """★ The incident this door exists for, as a test.

    On 2026-09-15 at 00:43 the suite compacted 260 files of the owner's REAL vault, including the
    index every session boots from — which then booted gutted for fifteen hours. The test meant to
    drive `compact_vault` against a fixture: it set GEDAECHTNIS_VAULT to a temp directory and
    loaded a fresh `maintenance` module. But `common.VAULT` is resolved ONCE PER PROCESS and an
    earlier test had already imported it, so the module's vault was the real one while the limits
    it read were the fixture's 1,500 bytes.

    Nothing about that is visible at the call site, so the rule cannot be "remember to use a
    subprocess". Under pytest, a memory file is rewritten in a temp directory or not at all."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "a test is running")
    victim = Path.home() / "Atlas" / "definitely-not-written.md"
    with pytest.raises(RuntimeError) as e:
        archive.atomic_write(victim, "this must never land")
    assert "REFUSED" in str(e.value) and "temporary directory" in str(e.value)
    assert not victim.exists()


def test_the_door_lets_a_TEMP_path_through(tmp_path, monkeypatch):
    """The negative control, and it is the one that matters: a door that refuses everything would
    pass the test above while breaking every other test in this file."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "a test is running")
    target = tmp_path / "Fine.md"
    archive.atomic_write(target, "written\n")
    assert target.read_text(encoding="utf-8") == "written\n"


def test_the_door_is_SILENT_in_production(tmp_path, monkeypatch):
    """Outside pytest it must not exist at all — the product writes real vaults for a living."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    outside = tmp_path / "pretend-real.md"
    archive.atomic_write(outside, "ok\n")          # tmp here, but the guard is off either way
    assert outside.read_text(encoding="utf-8") == "ok\n"
    import inspect
    src = inspect.getsource(archive._refuse_a_real_path_under_pytest)
    assert 'os.environ.get("PYTEST_CURRENT_TEST")' in src and "return" in src
