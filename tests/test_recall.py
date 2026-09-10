"""Tests for recall — the grep-shaped memory lookup.

The vault under test is synthetic and lives in tmp_path: nothing here reads a real one. Each
property gets a control that could fail — the ranking is checked against a decoy that mentions
one term many times, the archive against a live file that also matches, and the cap against an
entry far larger than it.
"""
from __future__ import annotations
import hashlib, os, subprocess, sys
from pathlib import Path
import pytest

RECALL = Path(__file__).resolve().parents[1] / "recall.py"


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "MyProject").mkdir(parents=True)
    (v / "Global").mkdir()
    (v / "MyProject" / "Canon.md").write_text(
        "# MyProject — Canon\n\n"
        "## The apostrophe regime is per layer\n\n"
        "**Decided:** 2026-01-02. The upstream pipeline normalises the apostrophe to U+02BC and "
        "the persistence layer to U+0027; the ingest converts at the boundary.\n\n"
        "## Backups run nightly\n\n"
        "**Decided:** 2026-01-03. Nothing to do with punctuation.\n", encoding="utf-8")
    (v / "MyProject" / "Errata.md").write_text(
        "# MyProject — Errata\n\n"
        "## A decoy that says apostrophe a great many times\n\n"
        + "apostrophe " * 60 + "\n", encoding="utf-8")
    (v / "MyProject" / "Errata-archive.md").write_text(
        "# MyProject — Errata (archive)\n\n"
        "## The night the queue rows vanished\n\n"
        "A queue file whose last line lacked its trailing newline glued the next appended row "
        "onto it, where no checkbox parser saw it.\n", encoding="utf-8")
    (v / "Global" / "Patterns.md").write_text(
        "# Patterns\n\n## Validate before scaling\n\nSmoke-test every parser first.\n",
        encoding="utf-8")
    return v


def recall(vault: Path, *args: str):
    p = subprocess.run([sys.executable, str(RECALL), "--vault", str(vault), *args],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    return p.returncode, p.stdout, p.stderr


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_right_entry_comes_first(vault):
    """Coverage beats frequency: the decoy repeats ONE term sixty times, the Canon entry answers
    the question. Without the coverage rule the decoy wins, which is the classic false hit."""
    rc, out, err = recall(vault, "which apostrophe does the persistence layer use")
    assert rc == 0, err
    first = out.split("──")[1]
    assert "MyProject/Canon.md" in first and "U+0027" in first
    assert "The apostrophe regime is per layer" in first
    assert "decoy" not in first


def test_the_entry_is_returned_verbatim(vault):
    rc, out, _ = recall(vault, "apostrophe regime")
    on_disk = (vault / "MyProject" / "Canon.md").read_text(encoding="utf-8")
    entry = on_disk.split("## The apostrophe regime is per layer", 1)[1].split("## Backups")[0]
    assert entry.strip() in out


def test_an_archive_entry_is_found(vault):
    """The narrative a live file compressed away lives in the archive; excluding it hides the
    reasoning the question is asking for. It is marked, so the caller knows which is authority."""
    rc, out, _ = recall(vault, "why did the queue rows vanish trailing newline")
    assert "MyProject/Errata-archive.md (archive)" in out
    assert "glued the next appended row" in out


def test_a_live_file_is_not_marked_as_an_archive(vault):
    rc, out, _ = recall(vault, "apostrophe regime")
    assert "Canon.md (archive)" not in out


def test_the_cap_holds(vault):
    """POSITIVE control on the budget: an entry ten times the cap is CUT, not dropped, and the
    whole output still fits — a recall answer is read into a session's context."""
    (vault / "MyProject" / "Position.md").write_text(
        "# Position\n\n## A very long entry about apostrophe handling\n\n"
        + ("apostrophe handling detail. " * 4000), encoding="utf-8")
    rc, out, _ = recall(vault, "apostrophe")
    assert rc == 0
    assert len(out.encode("utf-8")) <= 6000 + 300, len(out)      # cap + the footer line
    assert "[…]" in out


def test_a_smaller_cap_is_honoured(vault):
    rc, out, _ = recall(vault, "apostrophe", "--max-bytes", "900")
    assert len(out.encode("utf-8")) <= 900 + 300


def test_no_match_says_so_plainly(vault):
    rc, out, _ = recall(vault, "kubernetes ingress controller")
    assert rc == 0 and "No vault entry matches" in out
    assert "──" not in out


def test_recall_modifies_nothing(vault):
    """Read-only is a rule, not just an intention: the whole tree is hashed either side."""
    before = tree(vault)
    recall(vault, "apostrophe regime and the queue newline")
    assert tree(vault) == before


def test_a_missing_vault_refuses_rather_than_reporting_nothing_found(vault, tmp_path):
    """An absent vault must not answer 'nothing is recorded' — that reads as a fact about the
    memory when it is a fact about the path."""
    rc, out, err = recall(tmp_path / "nowhere", "apostrophe regime")
    assert rc == 2 and "no vault at" in err and out.strip() == ""


# --------------------------------------------------------------------- arm (a′) ----
# The length-aware rank and the queue exclusion, built 2026-09-10 after the recall bench
# scored the old rank 0 of 30 on the real vault. Every property below has a control that
# fails: the ranking is checked by flipping ONE flag and watching the order reverse, so a
# green here cannot be produced by some other edit made the same day.

FILLER = "This paragraph is routine status text with no query words in it whatsoever. " * 4


@pytest.fixture
def bulk_vault(tmp_path):
    """A short entry that ANSWERS the question and a bulky one that merely mentions it thirty
    times, both covering the same three terms — plus a queue file and a notice outbox."""
    v = tmp_path / "bulk"
    (v / "Notes").mkdir(parents=True)
    (v / "Pharos" / "queues").mkdir(parents=True)
    (v / "Channels" / "LANE").mkdir(parents=True)
    (v / "Notes" / "Canon.md").write_text(
        "# Canon\n\n## The ingest converts the apostrophe at the boundary\n\n"
        "The upstream layer normalises the apostrophe to U+02BC; the ingest converts it at "
        "the boundary.\n", encoding="utf-8")
    (v / "Notes" / "Position.md").write_text(
        "# Position\n\n## Running status, week by week\n\n"
        + ("The ingest boundary apostrophe work continued. " + FILLER) * 30 + "\n",
        encoding="utf-8")
    (v / "Pharos" / "queues" / "notes.md").write_text(
        "# Queue\n\n## Open rows\n\n- [ ] a row about the apostrophe telemetry backfill\n",
        encoding="utf-8")
    (v / "Channels" / "LANE" / "notice.md").write_text(
        "# Notices\n\n## Notice: the apostrophe telemetry backfill shipped\n\nBody.\n",
        encoding="utf-8")
    return v


def first_hit(out: str) -> str:
    return out.split("──")[1]


def test_a_bulky_section_does_not_win_on_bulk(bulk_vault):
    """POSITIVE control for the length normalisation. Both entries cover all three terms; the
    bulky one repeats them thirty times over 10 KB. The one-paragraph answer must come first."""
    rc, out, err = recall(bulk_vault, "ingest boundary apostrophe")
    assert rc == 0, err
    assert "Notes/Canon.md" in first_hit(out), out[:400]
    assert "U+02BC" in first_hit(out)


def test_with_the_length_penalty_off_the_bulky_section_wins(bulk_vault):
    """NEGATIVE control, and the whole point of keeping `--rank flat`: with the normalisation
    off — one flag, nothing else changed — the order reverses and the 10 KB status section is
    first again. That is what makes the test above evidence about the RANK."""
    rc, out, err = recall(bulk_vault, "ingest boundary apostrophe", "--rank", "flat")
    assert rc == 0, err
    assert "Notes/Position.md" in first_hit(out), out[:400]


def test_queues_and_outboxes_are_excluded_by_default(bulk_vault):
    """`Pharos/` and `Channels/` are operational surfaces, not memory. The question names terms
    that appear NOWHERE else, so a hit from them would be the top of the list if searched."""
    rc, out, err = recall(bulk_vault, "apostrophe telemetry backfill")
    assert rc == 0, err
    assert "Pharos/" not in out and "Channels/" not in out, out[:400]


def test_include_queues_searches_them(bulk_vault):
    """The other half of the flag: asked for, they come back — and they are the best hits, which
    is what proves the default excluded them rather than merely ranking them low."""
    rc, out, err = recall(bulk_vault, "apostrophe telemetry backfill", "--include-queues")
    assert rc == 0, err
    assert "Pharos/queues/notes.md" in out and "Channels/LANE/notice.md" in out, out[:600]


@pytest.fixture
def common_terms_vault(tmp_path):
    """Sixty routine entries between them making `commit`, `files` and `later` ordinary words in
    this corpus, one long status section that says all three many times, and one short entry that
    says `multipath` — the shape the bench found on the real vault, where the long section covered
    MORE of a question's terms simply by being long."""
    v = tmp_path / "common"
    (v / "Filler").mkdir(parents=True)
    (v / "Global").mkdir()
    ordinary = ["a commit landed", "several files moved", "it was reviewed later"]
    (v / "Filler" / "Notes.md").write_text(
        "# Notes\n\n" + "".join(
            f"## Routine note {i}\n\nNothing of consequence: {ordinary[i % 3]}.\n\n"
            for i in range(60)), encoding="utf-8")
    (v / "Global" / "Position.md").write_text(
        "# Position\n\n## Running status of the release train\n\n"
        + ("A commit landed, files moved, and it was reviewed later. " + FILLER) * 20 + "\n",
        encoding="utf-8")
    (v / "Global" / "Errata.md").write_text(
        "# Errata\n\n## A multipath add stages nothing\n\n"
        "A multipath commit still succeeds and claims what it does not carry.\n",
        encoding="utf-8")
    return v


def test_a_common_term_does_not_buy_coverage(common_terms_vault):
    """POSITIVE control for the measured stoplist. `commit`, `files` and `later` are in almost
    every matched entry, so they say nothing about which entry answers the question; `multipath`
    is in one. The short entry covering the rare term must beat the long one covering three
    common ones."""
    rc, out, err = recall(common_terms_vault, "commit files later multipath")
    assert rc == 0, err
    assert "Global/Errata.md" in first_hit(out), out[:400]


def test_without_the_measured_stoplist_the_broad_section_wins(common_terms_vault):
    """NEGATIVE control for that half specifically: `--rank length-only` keeps the length
    normalisation and drops only the stoplist, and the long section — which covers three of the
    four terms against the answer's two — takes rank 1 again."""
    rc, out, err = recall(common_terms_vault, "commit files later multipath",
                          "--rank", "length-only")
    assert rc == 0, err
    assert "Global/Position.md" in first_hit(out), out[:400]


def test_a_tiny_corpus_falls_back_to_plain_coverage(bulk_vault):
    """In a corpus of a handful of entries every term is "common" — one occurrence in six hits is
    17% — so the measured stoplist would rank every hit at zero coverage. It falls back instead,
    and the entry that covers three terms still beats one that covers a single term."""
    rc, out, err = recall(bulk_vault, "ingest boundary apostrophe upstream")
    assert rc == 0, err
    assert "Notes/Canon.md" in first_hit(out), out[:400]


def test_an_unknown_ranking_is_refused(bulk_vault):
    """A typo in an arm name must not silently score the default ranking under another arm's
    name — the same rule `recall_bench/run.py` follows for its unbuilt arms."""
    rc, out, err = recall(bulk_vault, "apostrophe", "--rank", "densityy")
    assert rc != 0 and "densityy" in err
