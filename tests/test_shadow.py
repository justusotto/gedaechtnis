"""The log-and-views shadow: logstore · importer · views · shadow_score.

Every claim gets a POSITIVE control (the property holds where it should) and, where the claim is
about a boundary, a NEGATIVE control (it does NOT hold where it should not) — a fidelity number that
can only go up is not an instrument.

The four modules are exercised as SUBPROCESSES against a mini-vault in tmp_path, with
GEDAECHTNIS_VAULT pointed at it. Nothing here reads or writes the real vault: `config.VAULT` is
resolved at import time, so a test that imported the modules in-process would cache the first test's
vault in sys.modules — the same trap `test_doors.py` documents.

The mini-vault's entry count is KNOWN BY CONSTRUCTION (written out below, one entry at a time), so
the importer's count is checked against a number a reader can verify by eye, not against a re-run of
the importer itself.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

# The mini-vault, entry by entry. 3 Canon + 2 Errata + 2 Position + 1 Aporia = EXPECTED_ENTRIES.
EXPECTED_ENTRIES = 8
EXPECTED_PER_STEM = {"Canon": 3, "Errata": 2, "Patterns": 0, "Position": 2, "Aporia": 1}


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def mini(tmp_path):
    v = tmp_path / "Vault"
    write(v / "Map.md", "# root\n")
    write(v / "Alpha" / "Map.md", "# Alpha\n")
    write(v / "Global" / "Map.md", "# Global\n")
    write(v / "Alpha" / "Canon.md",
          "---\ntype: canon\n---\n\n# Alpha Canon\n\nintro\n\n"
          "## One\n\nNEVER do the thing.\n\n"
          "## Two\n\nSee [[Global/Map]] for the index.\n\n"
          "### Three\n\nA sub-heading is an entry too.\n")
    write(v / "Alpha" / "Errata.md",
          "# Errata\n\n## A bug\n\nfenced below:\n\n```\n## not a heading\n[[also-not-a-link]]\n```\n\ndone.\n\n"
          "## Another bug\n\nA template written inline: `[[File#heading]]`, and a bash test `[[ -f x ]]`.\n")
    write(v / "Alpha" / "Position.md", "# Position\n\n- state one\n  continued\n- state two\n")
    write(v / "Global" / "Aporia.md", "# Aporia\n\n## An open question\n\nunanswered.\n")
    # a folder with NO Map.md is not a region, and a skipped top-level folder is not walked
    write(v / "Alpha" / "notes" / "Canon.md", "# not a region\n\n## ignored\n\nbody\n")
    write(v / "Pharos" / "Map.md", "# Pharos\n")
    write(v / "Pharos" / "Canon.md", "# skipped\n\n## also ignored\n\nbody\n")
    # a pull-only sidecar is never imported
    write(v / "Alpha" / "Canon-archive.md", "# archive\n\n## archived\n\nbody\n")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(v),
               GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"),
               GEDAECHTNIS_TOOL_ROOT=str(tmp_path / "tool"))
    return dict(vault=v, env=env, tmp=tmp_path)


def run(module, *args, env=None, expect=0):
    p = subprocess.run([sys.executable, str(ROOT / module), *args],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == expect, f"exit {p.returncode}\n{p.stdout}\n{p.stderr}"
    return p.stdout


def import_json(mini) -> dict:
    return json.loads(run("importer.py", "--json", env=mini["env"]))


# ================================================================== logstore =====================

def test_the_escape_round_trips_every_shape_a_role_file_contains(mini):
    """POSITIVE control on the one property the word 'byte-identical' rests on. A body carries
    newlines, tabs, backslashes and CRs; unescape(escape(x)) must be x for all of them, or the
    fidelity number is a fact about the escaper."""
    sys.path.insert(0, str(ROOT))
    import logstore                                     # noqa: E402 — pure functions, no vault read
    for s in ("plain", "two\nlines\n", "a\tb", "back\\slash", "\\n literal", "cr\r\n", "",
              "mixed \\ \t \n end", "…unicode ✓ Ukraїnian"):
        assert logstore.unescape(logstore.escape(s)) == s, repr(s)


def test_a_row_is_refused_when_its_kind_or_stem_is_not_in_the_grammar(mini):
    """NEGATIVE control on the grammar: the store must not accept a kind the views cannot render."""
    p = subprocess.run([sys.executable, str(ROOT / "logstore.py"), "append", "--region", "Alpha",
                        "--kind", "gossip", "--stem", "Canon", "--heading", "## x"],
                       capture_output=True, text=True, env=mini["env"], timeout=60)
    assert p.returncode != 0
    assert "invalid choice" in (p.stdout + p.stderr)


def test_appending_the_same_entry_twice_is_a_no_op(mini):
    """POSITIVE control on idempotency at the row level — the property the whole shadow rests on."""
    args = ["append", "--region", "Alpha", "--kind", "decision", "--stem", "Canon",
            "--heading", "## One", "--body", "NEVER do the thing."]
    first = run("logstore.py", *args, env=mini["env"])
    second = run("logstore.py", *args, env=mini["env"])
    assert "appended" in first and "no-op" in second
    assert first.split("\t")[0] == second.split("\t")[0], "the id must be stable across runs"
    assert run("logstore.py", "check", env=mini["env"]).strip().endswith("1 row(s)")


def test_a_different_body_under_the_same_heading_is_a_NEW_row(mini):
    """NEGATIVE control on the same rule — dedupe keyed on the heading alone would silently drop a
    correction, which is the exact failure the append-only log exists to prevent."""
    base = ["append", "--region", "Alpha", "--kind", "decision", "--stem", "Canon", "--heading", "## One"]
    a = run("logstore.py", *base, "--body", "first", env=mini["env"])
    b = run("logstore.py", *base, "--body", "second", env=mini["env"])
    assert "appended" in a and "appended" in b
    assert a.split("\t")[0] != b.split("\t")[0]


def test_check_reports_a_hand_broken_row(mini):
    """POSITIVE control that `check` BITES: a green check over a log nobody could corrupt proves
    nothing about the log that people will."""
    run("importer.py", env=mini["env"])
    log = next((mini["vault"] / ".gedaechtnis" / "log").glob("*.tsv"))
    log.write_text(log.read_text(encoding="utf-8") + "not a row at all\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(ROOT / "logstore.py"), "check"],
                       capture_output=True, text=True, env=mini["env"], timeout=60)
    assert p.returncode == 1
    assert "does not match" in p.stdout


def test_check_catches_an_id_whose_hash_no_longer_matches_its_content(mini):
    """POSITIVE control on the sharper corruption: the row still PARSES, and only the hash betrays
    that somebody edited the log in place. Append-only is enforced by this check or not at all."""
    run("importer.py", env=mini["env"])
    log = next((mini["vault"] / ".gedaechtnis" / "log").glob("*.tsv"))
    lines = log.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            continue
        f = ln.split("\t")
        f[6] = f[6] + " tampered"
        lines[i] = "\t".join(f)
        break
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(ROOT / "logstore.py"), "check"],
                       capture_output=True, text=True, env=mini["env"], timeout=60)
    assert p.returncode == 1
    assert "sha256 of the row's own content" in p.stdout


# ================================================================== importer =====================

def test_the_importer_finds_exactly_the_entries_the_mini_vault_contains(mini):
    """POSITIVE control with a count known by construction, not by re-running the importer. Also the
    negative half in one shot: the skipped folder, the non-region folder and the `-archive` sidecar
    contribute entries that would push the total over EXPECTED_ENTRIES if any were walked."""
    res = import_json(mini)
    assert res["entries"] == EXPECTED_ENTRIES, res["per_stem"]
    assert res["per_stem"] == EXPECTED_PER_STEM
    assert res["appended"] == EXPECTED_ENTRIES


def test_a_heading_inside_a_fenced_block_is_not_an_entry(mini):
    """NEGATIVE control on the parser. `Errata.md` contains a fenced `## not a heading`; if the
    fence were ignored the file would yield 3 entries instead of 2."""
    assert import_json(mini)["per_stem"]["Errata"] == 2


def test_a_second_import_appends_nothing(mini):
    """POSITIVE control on idempotency end to end — the claim that makes a daily checkpoint cheap."""
    first = import_json(mini)
    second = import_json(mini)
    assert first["appended"] == EXPECTED_ENTRIES
    assert second["appended"] == 0
    assert second["already_present"] == EXPECTED_ENTRIES


def test_the_importer_never_writes_a_live_file(mini):
    """POSITIVE control on the one NEVER in this arc: additive means the live vault is untouched.
    Hashed before and after, so a rewrite that happened to produce the same length still fails."""
    import hashlib
    def snapshot():
        out = {}
        for p in sorted(mini["vault"].rglob("*.md")):
            if ".gedaechtnis" in p.parts:
                continue
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out
    before = snapshot()
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    assert snapshot() == before


def test_entries_rejoin_byte_identical_to_the_file_they_came_from(mini):
    """POSITIVE control on the split: preamble + every reconstructed entry IS the file. Without this
    'byte-identical' would only ever be checked against the splitter's own output."""
    sys.path.insert(0, str(ROOT))
    import importer                                     # noqa: E402
    for p in sorted(mini["vault"].rglob("*.md")):
        text = p.read_text(encoding="utf-8")
        pre, entries = importer.split_entries(text)
        assert importer.joined(pre, entries) == text, p


# ================================================================== views ========================

def test_a_generated_view_starts_with_the_GENERATED_marker(mini):
    """POSITIVE control. The marker is what the gate door keys on; a view without it is a view the
    door cannot protect."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    v = mini["vault"] / ".gedaechtnis" / "views" / "Alpha" / "Canon.md"
    first = v.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# GENERATED sha256:")
    assert len(first.split(":")[1]) == 64


def test_the_view_reproduces_every_heading_byte_identical_and_in_the_live_order(mini):
    """POSITIVE control on the claim the fidelity number reports — asserted against the LIVE file's
    own bytes, not against the row the view was built from."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    live = (mini["vault"] / "Alpha" / "Canon.md").read_text(encoding="utf-8")
    view = (mini["vault"] / ".gedaechtnis" / "views" / "Alpha" / "Canon.md").read_text(encoding="utf-8")
    assert view.split("\n", 1)[1] == live, "view minus its marker line must be the live file"
    assert [l for l in view.splitlines() if l.startswith("##")] == \
           [l for l in live.splitlines() if l.startswith("##")]


def test_the_view_never_lands_on_top_of_the_live_file(mini):
    """NEGATIVE control on placement: the views live under `.gedaechtnis/views/`, and an additive
    shadow that wrote one file into a region would have ended the shadow without saying so."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    for p in (mini["vault"] / ".gedaechtnis" / "views").rglob("*.md"):
        assert ".gedaechtnis" in p.parts


# ================================================================== shadow_score =================

def score(mini) -> dict:
    out = mini["tmp"] / "cp"
    run("shadow_score.py", "--out", str(out), env=mini["env"])
    js = next(out.glob("checkpoint-*.json"))
    return json.loads(js.read_text(encoding="utf-8"))


def test_a_clean_shadow_reproduces_every_entry(mini):
    """POSITIVE control. Every entry is reproduced; fidelity is that count MINUS the pointers, over
    every entry — so a perfect view scores 1.0 only in a stem with no pointer in it. The CEILING is
    (entries − pointers) / entries, and it is worth stating out loud: the bar sits at 90%, so a stem
    more than a tenth pointers cannot pass however perfect the generator is."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    res = score(mini)
    for stem, agg in res["per_stem"].items():
        assert agg["reproduced"] == agg["entries"], (stem, agg)
        ceiling = (agg["entries"] - agg["pointer_rows_excluded"]) / agg["entries"]
        assert agg["fidelity"] == pytest.approx(ceiling), (stem, agg)
    # and the bar is read off that, not off "did it reproduce": this mini-vault's Canon is a third
    # pointers, so a PERFECT generator still fails Canon — the interaction is real and deliberate.
    assert res["per_stem"]["Canon"]["verdict"] == "FAIL"
    assert res["per_stem"]["Errata"]["verdict"] == "PASS"
    assert res["verdict"] == "FAIL"


def test_a_planted_mismatch_lowers_fidelity_by_exactly_one_entry(mini):
    """POSITIVE control that the scorer BITES, and by the right amount. A metric that cannot go down
    is a decoration; one that goes down by an unpredictable amount cannot carry a 90% bar."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    before = score(mini)
    v = mini["vault"] / ".gedaechtnis" / "views" / "Alpha" / "Canon.md"
    v.write_text(v.read_text(encoding="utf-8").replace("NEVER do the thing.", "NEVER do the thing!"),
                 encoding="utf-8")
    after = score(mini)
    b = before["per_stem"]["Canon"]
    a = after["per_stem"]["Canon"]
    assert a["entries"] == b["entries"], "the denominator must not move"
    assert a["numerator"] == b["numerator"] - 1
    assert a["fidelity"] == pytest.approx((b["numerator"] - 1) / b["entries"])


def test_a_pointer_entry_is_excluded_from_the_numerator(mini):
    """POSITIVE control on the council's own amendment. `## Two` is a pointer (its body is only a
    wikilink); Canon holds 3 entries, so a perfect view scores 2/3, never 3/3 — and the pointer stays
    in the DENOMINATOR, which is what stops a vault of pointers clearing the bar by being easy."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    res = score(mini)
    canon = res["per_stem"]["Canon"]
    assert canon["entries"] == 3
    assert canon["pointer_rows_excluded"] == 1
    assert canon["reproduced"] == 3
    assert canon["numerator"] == 2
    assert canon["fidelity"] == pytest.approx(2 / 3)


def test_every_reported_number_carries_its_population(mini):
    """POSITIVE control on the reporting rule itself: a share with no denominator beside it has sent
    this fleet down a wrong path before."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    res = score(mini)
    for r in res["files"]:
        assert "denominator" in r and "numerator" in r
    assert "population" in res["links"] and "population" in res["counter"]
    assert res["escape"]["binding"]["entries"] + res["escape"]["narrative"]["entries"] == \
           res["escape"]["all"]["entries"]


def test_the_escape_rate_splits_binding_from_narrative(mini):
    """POSITIVE control on the split the council insisted on: `## One` carries NEVER, so exactly one
    entry is binding. An aggregate escape rate would hide that class entirely."""
    run("importer.py", env=mini["env"])
    res = score(mini)
    assert res["escape"]["binding"]["entries"] == 1
    assert res["escape"]["narrative"]["entries"] == EXPECTED_ENTRIES - 1


def test_the_shadow_start_pin_is_written_once_and_never_rewritten(mini):
    """POSITIVE control on the live counter's baseline. A start line that moved with every checkpoint
    would make 'hand edits since the start' unreadable."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    score(mini)
    pin = mini["vault"] / ".gedaechtnis" / "shadow-start.json"
    first = pin.read_text(encoding="utf-8")
    score(mini)
    assert pin.read_text(encoding="utf-8") == first
    assert json.loads(first)["checkpoints"] == [0, 1, 2, 4, 7, 14, 30]


def test_a_wikilink_inside_code_is_not_a_wikilink(mini):
    """POSITIVE control on the EXTRACTOR repair. The mini-vault's Errata carries three shapes that
    look like links and are not: one in a fenced block, one `[[File#heading]]` template in
    backticks, one bash `[[ -f x ]]` test. All three would resolve to nothing and be reported as
    broken links at every checkpoint — a check nobody reads by day 7."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    res = score(mini)
    assert res["links"]["unresolved"] == 0, res["links"]["examples"]


def test_a_real_wikilink_IS_counted_and_resolved(mini):
    """NEGATIVE control on the same repair — the half that proves `strip_code` blanks code and not
    the document. `Alpha/Canon.md` links `[[Global/Map]]`, which exists."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    res = score(mini)
    assert res["links"]["links"] >= 1
    assert res["links"]["unresolved"] == 0


def test_a_wikilink_to_a_file_that_does_not_exist_IS_reported(mini):
    """POSITIVE control that the link check still BITES after the repair — otherwise the fix for
    three false positives would have been a fix for the check itself."""
    (mini["vault"] / "Alpha" / "Aporia.md").write_text(
        "# Aporia\n\n## Where does it go\n\nSee [[No/Such/File]].\n", encoding="utf-8")
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    res = score(mini)
    assert res["links"]["unresolved"] == 1
    assert res["links"]["examples"][0]["link"] == "No/Such/File"


def test_a_corrected_entry_renders_the_NEWEST_row_not_the_first(mini):
    """POSITIVE control on append-only semantics reaching the view. The log never edits: a rewritten
    entry arrives as a SECOND row under the same heading and the same ordinal, and the view must
    render the newer one. Picking positionally would render the SUPERSEDED text and score it as a
    fidelity miss with no hint of the cause — observed live during checkpoint 0, when two
    `Speculum/Position` entries were rewritten mid-run."""
    run("importer.py", env=mini["env"])
    canon = mini["vault"] / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8").replace("NEVER do the thing.",
                                                              "NEVER do the thing, corrected."),
                     encoding="utf-8")
    res = import_json(mini)
    assert res["appended"] == 1, "exactly one entry changed, so exactly one row is new"
    run("views.py", env=mini["env"])
    view = (mini["vault"] / ".gedaechtnis" / "views" / "Alpha" / "Canon.md").read_text(encoding="utf-8")
    assert "NEVER do the thing, corrected." in view
    assert "NEVER do the thing.\n" not in view
    assert score(mini)["per_stem"]["Canon"]["reproduced"] == 3


def test_the_superseded_row_is_still_in_the_log(mini):
    """NEGATIVE control on the same act: 'newest wins' is about the VIEW, never about the log. The
    original row stays on disk — an append-only store that loses its history is a file."""
    run("importer.py", env=mini["env"])
    canon = mini["vault"] / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8").replace("NEVER do the thing.", "corrected."),
                     encoding="utf-8")
    run("importer.py", env=mini["env"])
    log = next((mini["vault"] / ".gedaechtnis" / "log").glob("*.tsv")).read_text(encoding="utf-8")
    assert "NEVER do the thing." in log and "corrected." in log
    assert run("logstore.py", "check", env=mini["env"]).strip().endswith(f"{EXPECTED_ENTRIES + 1} row(s)")


# ================================================== the live counter (git-backed) ===============

def git(vault, *args, env=None):
    p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_the_live_counter_sees_a_hand_edit_and_excludes_the_automated_committer(mini):
    """POSITIVE control on the counter AND its own control line. Checkpoint 0 measured a FALSE ZERO
    here: `git log --since=<date> -- '*Position.md'` returns nothing on a real vault while the same
    pathspec without `--since` returns the commit — `--since` prunes the traversal and path
    simplification finds nothing left. It failed silently, and read as "no hand edits", which is the
    answer this counter most wants to be true. Anchored on the start SHA now, and the run reports
    how many commits are in the window at all so a zero is readable."""
    v = mini["vault"]
    git(v, "init", "-q")
    git(v, "-c", "user.name=t", "-c", "user.email=t@x", "add", "-A")
    git(v, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "seed")
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    score(mini)                                        # writes the start pin at this SHA

    canon = v / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8") + "\n## Four\n\nadded by hand.\n", encoding="utf-8")
    git(v, "-c", "user.name=h", "-c", "user.email=human@x", "add", "--", "Alpha/Canon.md")
    git(v, "-c", "user.name=h", "-c", "user.email=human@x", "commit", "-q", "-m", "a hand edit")
    (v / "Global" / "Errata.md").write_text("# Errata\n\n## Auto\n\nbody\n", encoding="utf-8")
    git(v, "-c", "user.name=a", "-c", "user.email=atlas@local", "add", "--", "Global/Errata.md")
    git(v, "-c", "user.name=a", "-c", "user.email=atlas@local", "commit", "-q", "-m", "auto")

    c = score(mini)["counter"]
    assert c["commits_in_window"] == 2, c
    assert c["hand_edit_commits"] == 1, c
    assert c["automated_commits_excluded"] == 1, c
    assert c["hand_edits"][0]["subject"] == "a hand edit"


def test_a_commit_that_touches_no_role_file_is_not_counted(mini):
    """NEGATIVE control — the half that proves the counter reads PATHS and not merely commits. A
    counter that reported every commit would read the vault's bookkeeping stream as hand editing."""
    v = mini["vault"]
    git(v, "init", "-q")
    git(v, "-c", "user.name=t", "-c", "user.email=t@x", "add", "-A")
    git(v, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "seed")
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    score(mini)
    (v / "Alpha" / "Map.md").write_text("# Alpha, retitled\n", encoding="utf-8")
    git(v, "-c", "user.name=h", "-c", "user.email=human@x", "add", "--", "Alpha/Map.md")
    git(v, "-c", "user.name=h", "-c", "user.email=human@x", "commit", "-q", "-m", "not a role file")
    c = score(mini)["counter"]
    assert c["commits_in_window"] == 1, "the window must contain the commit"
    assert c["hand_edit_commits"] == 0, "but it touched no role file"
