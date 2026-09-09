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
