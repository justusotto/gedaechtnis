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
# Two SHAPE rows (`# PREAMBLE` + `# ORDER`) per role file that has entries: Alpha/Canon,
# Alpha/Errata, Alpha/Position, Global/Aporia. They are rows in the log and not entries, so every
# count below says which of the two it means.
EXPECTED_SHAPE_ROWS = 8


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
    assert run("logstore.py", "check", env=mini["env"]).strip().endswith(
        f"{EXPECTED_ENTRIES + 1 + EXPECTED_SHAPE_ROWS} row(s)")


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


def test_whole_file_identity_is_reported_and_BITES(mini):
    """POSITIVE and NEGATIVE control on the strongest claim in the report. Per-entry fidelity is
    blind to the preamble and the heading order, so the whole-file count is what a reader should
    trust — and it has to be able to fall, or it is a decoration."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    w = score(mini)["whole_file"]
    assert w["identical"] == w["views"] and w["views"] > 0, w
    v = mini["vault"] / ".gedaechtnis" / "views" / "Alpha" / "Canon.md"
    v.write_text(v.read_text(encoding="utf-8").replace("intro", "intro, meddled with"), encoding="utf-8")
    w2 = score(mini)["whole_file"]
    assert w2["identical"] == w["identical"] - 1, w2
    assert w2["differing"][0]["view"].endswith("Canon.md")


def test_a_commit_to_a_GENERATED_VIEW_is_not_a_hand_edit(mini):
    """NEGATIVE control against the shadow measuring itself. `.gedaechtnis/views/Alpha/Position.md`
    matches the role-stem pattern perfectly well, so without the live-only rule the counter reads
    the shadow's OWN commits as edits to the memory it is shadowing. Excluded for being GENERATED,
    not for carrying the automated identity — this commit is by a human."""
    v = mini["vault"]
    git(v, "init", "-q")
    git(v, "-c", "user.name=t", "-c", "user.email=t@x", "add", "-A")
    git(v, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "seed")
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    score(mini)
    git(v, "-c", "user.name=h", "-c", "user.email=human@x", "add", "-f", "--", ".gedaechtnis")
    git(v, "-c", "user.name=h", "-c", "user.email=human@x", "commit", "-q", "-m", "commit the views")
    c = score(mini)["counter"]
    assert c["commits_in_window"] == 1, "the window must contain the commit"
    assert c["hand_edit_commits"] == 0, "but a generated view is not a live role file"


# ============================================ the `src` column and the SESSION WRITE PATH =======
# Council 3, K-3 (2026-09-10): day 0 measured the importer's round trip, because the session write
# path did not exist. These are the controls for the path that now does.

HOOKS = ROOT / "hooks"


def chore_write(mini, path: Path, sid: str = "S1", cwd: str = None):
    """Drive the REAL PostToolUse chore the way a session's Edit does — never a stub. A test that
    faked the hook's input would prove the appender works and nothing about whether it is reached."""
    p = subprocess.run([sys.executable, str(HOOKS / "chore.py"), "write"],
                       input=json.dumps({"cwd": cwd or str(mini["tmp"]), "session_id": sid,
                                         "tool_name": "Edit",
                                         "tool_input": {"file_path": str(path)}}),
                       capture_output=True, text=True, env=mini["env"], timeout=120)
    assert p.returncode == 0, p.stderr
    return p.stdout


def log_rows(mini, **filt) -> list:
    args = ["read", "--json"]
    for k, v in filt.items():
        args += [f"--{k}", v]
    return json.loads(run("logstore.py", *args, env=mini["env"]))


def entry_rows(mini, **filt) -> list:
    """The rows that are ENTRIES — shape rows filtered out. Kept separate everywhere, because a
    count that silently mixed the two would make "how many memories" mean "how many memories plus
    twice the number of files"."""
    return [r for r in log_rows(mini, **filt)
            if not ({"preamble", "order"} & set(r["flags"]))]


def test_a_row_written_before_the_src_column_reads_as_importer(mini):
    """POSITIVE control on the backward-compatible grammar. Every row in the live log was written
    without the ninth field; if an 8-field row stopped parsing, the shadow's whole history would
    read as an empty log and every count would silently become a fact about the parser."""
    sys.path.insert(0, str(ROOT))
    import logstore                                     # noqa: E402 — pure function, no vault read
    line = ("Alpha·2026-09-01T00:00:00·deadbeef\t2026-09-01T00:00:00\tAlpha\tdecision\tCanon"
            "\t## One\tbody\tord=0")
    d = logstore.parse_line(line)
    assert d["src"] == "importer" and d["src_explicit"] is False
    d9 = logstore.parse_line(line + "\tsession")
    assert d9["src"] == "session" and d9["src_explicit"] is True
    for k in ("id", "ts", "region", "kind", "stem", "heading", "body", "flags"):
        assert d[k] == d9[k], k                          # the eight original fields do not move


def test_the_grammar_refuses_an_unknown_src(mini):
    """NEGATIVE control on the same boundary: `src` is a closed set, or the cut-over ratio's
    numerator is whatever anybody felt like typing."""
    p = subprocess.run([sys.executable, str(ROOT / "logstore.py"), "append", "--region", "Alpha",
                        "--kind", "decision", "--stem", "Canon", "--heading", "## x",
                        "--src", "telepathy"],
                       capture_output=True, text=True, env=mini["env"], timeout=60)
    assert p.returncode != 0
    assert "invalid choice" in (p.stdout + p.stderr)


def test_migrate_stamps_the_column_without_changing_one_row_of_content(mini):
    """POSITIVE control on the migration's one promise. Every row's id, hash, heading, body and
    flags must survive byte-identical — a migration that re-minted an id would break every
    reference to it, and `check` would go green on the new ids and tell nobody."""
    run("importer.py", env=mini["env"])
    log = next((mini["vault"] / ".gedaechtnis" / "log").glob("*.tsv"))
    lines = log.read_text(encoding="utf-8").splitlines()
    original = [l for l in lines if not l.startswith("#")]
    # strip the column back off, as if the log had been written before it existed
    stripped = [l if l.startswith("#") else l.rsplit("\t", 1)[0] for l in lines]
    log.write_text("\n".join(stripped) + "\n", encoding="utf-8")
    out = run("logstore.py", "migrate", env=mini["env"])
    assert f"stamped src=importer on {EXPECTED_ENTRIES + EXPECTED_SHAPE_ROWS} row(s)" in out
    after = [l for l in log.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
    assert after == original, "a migrated row must be the original line plus `\\timporter`"
    assert run("logstore.py", "check", env=mini["env"]).strip().endswith(
        f"{EXPECTED_ENTRIES + EXPECTED_SHAPE_ROWS} row(s)")


def test_migrate_is_idempotent_and_a_second_run_stamps_nothing(mini):
    """NEGATIVE control on the same act — a migration that ran twice and appended `importer` twice
    would corrupt every row it had already fixed, and the second run is the one nobody watches."""
    run("importer.py", env=mini["env"])
    log = next((mini["vault"] / ".gedaechtnis" / "log").glob("*.tsv"))
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join(l if l.startswith("#") else l.rsplit("\t", 1)[0] for l in lines) + "\n",
                   encoding="utf-8")
    run("logstore.py", "migrate", env=mini["env"])
    text = log.read_text(encoding="utf-8")
    second = run("logstore.py", "migrate", env=mini["env"])
    assert "stamped src=importer on 0 row(s)" in second
    assert log.read_text(encoding="utf-8") == text
    run("logstore.py", "check", env=mini["env"])


def test_the_migration_backup_is_not_read_back_as_log(mini):
    """NEGATIVE control on the backup's NAME. `all_rows` globs `*.tsv`; a backup called `<x>.tsv`
    would be parsed as a second copy of the whole log and every count would double, silently."""
    run("importer.py", env=mini["env"])
    log_dir = mini["vault"] / ".gedaechtnis" / "log"
    log = next(log_dir.glob("*.tsv"))
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join(l if l.startswith("#") else l.rsplit("\t", 1)[0] for l in lines) + "\n",
                   encoding="utf-8")
    run("logstore.py", "migrate", env=mini["env"])
    backups = [p for p in log_dir.iterdir() if ".pre-srccol-" in p.name]
    assert len(backups) == 1, [p.name for p in log_dir.iterdir()]
    assert not backups[0].name.endswith(".tsv")
    assert len(log_rows(mini)) == EXPECTED_ENTRIES + EXPECTED_SHAPE_ROWS


def test_a_session_edit_to_a_role_file_appends_a_src_session_row(mini):
    """POSITIVE control on the whole point of K-3: an edit a SESSION makes becomes a row in the same
    act, stamped as the session's — not waiting for the next importer sweep."""
    run("importer.py", env=mini["env"])
    canon = mini["vault"] / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8") + "## Four\n\nWritten by a session.\n",
                     encoding="utf-8")
    chore_write(mini, canon)
    sess = entry_rows(mini, src="session")
    assert len(sess) == 1, [r["heading"] for r in sess]
    assert sess[0]["heading"] == "## Four" and sess[0]["region"] == "Alpha"
    assert sess[0]["body"] == "\nWritten by a session.\n"
    assert len(entry_rows(mini, src="importer")) == EXPECTED_ENTRIES
    # and the file's SHAPE moved with it: a new heading is a new order, so the `# ORDER` row is
    # re-minted as the session's. A cold build reading a stale one would rebuild the file as it
    # looked before the edit and score the difference as a fidelity miss.
    shape = [r for r in log_rows(mini, src="session") if "order" in r["flags"]]
    assert len(shape) == 1 and shape[0]["body"].splitlines()[-1] == "## Four"


def test_the_session_row_wins_the_identity_so_a_later_import_does_not_re_stamp_it(mini):
    """POSITIVE control on the reason `src` is OUT of the content hash. The importer sweeping the
    same entry afterwards must be a NO-OP: two rows for one entry disagreeing about who wrote it is
    exactly the ambiguity the cut-over ratio cannot survive."""
    run("importer.py", env=mini["env"])
    canon = mini["vault"] / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8") + "## Four\n\nSession first.\n",
                     encoding="utf-8")
    chore_write(mini, canon)
    res = import_json(mini)
    assert res["appended"] == 0, "the importer must find nothing new to add"
    assert res["shape_appended"] == 0, "nor any shape row"
    assert len(entry_rows(mini, src="session")) == 1
    assert len(entry_rows(mini, src="importer")) == EXPECTED_ENTRIES


def test_the_writer_ignores_a_file_that_is_not_a_live_role_file(mini):
    """NEGATIVE control, three shapes at once: a Map (not one of the five stems), a generated view
    (under `.gedaechtnis/`, and it carries the stem name perfectly well), and a role file in a
    folder with no Map.md (not a region). Any one of them appending a row would make the log a
    record of file writes rather than of memory."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    before = len(log_rows(mini))
    for p in (mini["vault"] / "Alpha" / "Map.md",
              mini["vault"] / ".gedaechtnis" / "views" / "Alpha" / "Canon.md",
              mini["vault"] / "Alpha" / "notes" / "Canon.md"):
        chore_write(mini, p)
    assert len(log_rows(mini)) == before
    assert log_rows(mini, src="session") == []


def test_the_writer_appends_nothing_when_no_log_exists_yet(mini):
    """NEGATIVE control on the ordering rule: the session writer FOLLOWS the importer and never
    precedes it. A machine with no shadow must not grow one out of an ordinary role-file edit —
    a first row minted by a hook would have no start pin, no counts and no owner behind it."""
    canon = mini["vault"] / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8") + "## Four\n\nNo log yet.\n",
                     encoding="utf-8")
    chore_write(mini, canon)
    assert not (mini["vault"] / ".gedaechtnis" / "log").exists()


# ============================================ orphan rows (M-03) ================================

def views_json(mini) -> dict:
    return json.loads(run("views.py", "--json", env=mini["env"]))


def test_a_clean_shadow_reports_zero_orphans(mini):
    """NEGATIVE control on the orphan count — it must be able to be ZERO, or the number is noise.
    Every row came from a live entry, so nothing is orphaned."""
    run("importer.py", env=mini["env"])
    assert views_json(mini)["orphan_rows"] == 0


def test_a_row_whose_heading_left_the_live_file_STILL_RENDERS_and_is_counted(mini):
    """POSITIVE control on M-03, the defect council 3 named at `views.py:78`: before this, a row
    whose heading is absent from the live file could never be drawn, so a deleted entry was carried
    by the log and invisible in the view — and the fidelity number could not see it either."""
    run("importer.py", env=mini["env"])
    canon = mini["vault"] / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8")
                     .replace("### Three\n\nA sub-heading is an entry too.\n", ""), encoding="utf-8")
    res = views_json(mini)
    assert res["orphan_rows"] == 1, res["skipped"]
    view = (mini["vault"] / ".gedaechtnis" / "views" / "Alpha" / "Canon.md").read_text(encoding="utf-8")
    assert "A sub-heading is an entry too." in view, "the orphan row must RENDER, not vanish"
    assert view.rstrip().endswith("A sub-heading is an entry too."), "appended at the end"


def test_an_orphan_makes_the_view_differ_from_the_live_file_and_that_is_reported(mini):
    """POSITIVE control on the COST of the M-03 fix, stated rather than smoothed over: a view
    carrying an entry the live file has dropped is no longer byte-identical to it, so the whole-file
    identity count falls by exactly one. A generator that hid the orphan to protect the number would
    be reporting its own silence as fidelity."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    clean = score(mini)["whole_file"]
    canon = mini["vault"] / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8")
                     .replace("### Three\n\nA sub-heading is an entry too.\n", ""), encoding="utf-8")
    run("views.py", env=mini["env"])
    after = score(mini)["whole_file"]
    assert after["identical"] == clean["identical"] - 1, after
    assert after["differing"][0]["view"].endswith("Canon.md")


def test_the_cut_over_ratio_counts_session_rows_over_rows_plus_hand_edits(mini):
    """POSITIVE control on the number the council asked for, WITH its denominator. It is deliberately
    two units — rows over rows-plus-commits — and the report has to say so, because a share whose
    denominator nobody can name is how this fleet has been misled before."""
    v = mini["vault"]
    git(v, "init", "-q")
    git(v, "-c", "user.name=t", "-c", "user.email=t@x", "add", "-A")
    git(v, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "seed")
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    zero = score(mini)["counter"]
    assert zero["session_rows"] == 0 and zero["session_share"] == 0.0     # the 0/… the council named

    canon = v / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8") + "## Four\n\nby a session.\n", encoding="utf-8")
    chore_write(mini, canon)
    git(v, "-c", "user.name=h", "-c", "user.email=human@x", "add", "--", "Alpha/Canon.md")
    git(v, "-c", "user.name=h", "-c", "user.email=human@x", "commit", "-q", "-m", "a hand edit")
    run("views.py", env=mini["env"])
    c = score(mini)["counter"]
    assert c["session_rows"] == 1
    assert c["hand_edit_commits"] == 1
    assert c["session_share_denominator"] == 2
    assert c["session_share"] == pytest.approx(0.5)
    assert "different units" in c["session_share_population"]


# ============================================ the COLD BUILD (K-3, commit 2) ====================
# Day 0 reported "72 of 72 views byte-identical" and council 3 closed 5-0 that the number could not
# fail on the bytes it named: `views.py` took the preamble and the heading ORDER from the very live
# file it was then compared against (C-04). The cold build reads no live file at all.

def cold_json(mini, *args, expect=0) -> dict:
    return json.loads(run("views.py", "--cold", "--json", *args, env=mini["env"], expect=expect))


def cold_compare(mini) -> dict:
    return json.loads(run("views.py", "--cold", "--compare", "--json", env=mini["env"]))


def move_live_aside(mini) -> Path:
    """Physically move every live role file out of the vault. The cold build not READING them is a
    property of the code; this is the check that does not depend on believing the code."""
    aside = mini["tmp"] / "aside"
    for p in sorted(mini["vault"].rglob("*.md")):
        if ".gedaechtnis" in p.parts or p.stem not in ("Canon", "Errata", "Patterns",
                                                       "Position", "Aporia"):
            continue
        dest = aside / p.relative_to(mini["vault"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        p.rename(dest)
    return aside


def restore_live(mini, aside: Path) -> None:
    for p in sorted(aside.rglob("*.md")):
        (mini["vault"] / p.relative_to(aside)).parent.mkdir(parents=True, exist_ok=True)
        p.rename(mini["vault"] / p.relative_to(aside))


def test_the_importer_mints_one_shape_row_pair_per_file_with_entries(mini):
    """POSITIVE control on the cold build's inputs, counted by construction: four role files carry
    entries, so eight shape rows and not one more. The stub-shaped files in this vault (a folder
    with no Map, a skipped top-level folder) must contribute none, or the cold build would invent a
    view for a file the shadow never covered."""
    res = import_json(mini)
    assert res["shape_rows"] == EXPECTED_SHAPE_ROWS
    assert res["shape_appended"] == EXPECTED_SHAPE_ROWS
    counts = json.loads(run("logstore.py", "count", env=mini["env"]))
    assert counts["shape_rows"] == EXPECTED_SHAPE_ROWS
    assert counts["entry_rows"] == EXPECTED_ENTRIES
    assert counts["per_src"] == {"importer": EXPECTED_ENTRIES + EXPECTED_SHAPE_ROWS, "session": 0}


def test_a_shape_row_is_not_an_entry_in_any_instrument_that_counts_entries(mini):
    """NEGATIVE control on the seam the shape rows cross. They are rows, so every consumer that
    counts ROWS sees them; not one that counts ENTRIES may. A shape row inside the escape rate's
    denominator would be measuring how long the vault's frontmatter is, and inside the fidelity
    denominator it would be an entry the live file does not have."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    res = score(mini)
    assert res["escape"]["shape_rows_excluded"] == EXPECTED_SHAPE_ROWS
    assert res["escape"]["all"]["entries"] == EXPECTED_ENTRIES
    assert sum(a["entries"] for a in res["per_stem"].values()) == EXPECTED_ENTRIES
    assert res["escape"]["binding"]["entries"] + res["escape"]["narrative"]["entries"] == \
        EXPECTED_ENTRIES


def test_the_cold_build_reproduces_every_file_WITH_THE_LIVE_FILES_MOVED_ASIDE(mini):
    """POSITIVE control on the day-4 falsifier's first clause, run the strong way: the live files are
    not merely unread, they are GONE from the vault while the build runs. Byte-identical here is a
    claim about the log; the day-0 number was a claim about a copy."""
    run("importer.py", env=mini["env"])
    aside = move_live_aside(mini)
    res = cold_json(mini)
    assert len(res["views"]) == 4, res["skipped"]
    assert res["skipped"] == [] and res["orphan_rows"] == 0
    restore_live(mini, aside)
    cmp = cold_compare(mini)
    assert cmp["identical"] == cmp["views"] == 4, cmp["differing"]


def test_the_cold_build_never_reads_the_live_file_even_to_fail(mini):
    """NEGATIVE control on the substitution this whole commit exists to prevent. With the log EMPTY
    of shape rows the cold build must REFUSE the file, not fall back to reading the live one — a
    fallback would be the old generator wearing a new flag, and its green would mean nothing."""
    run("importer.py", env=mini["env"])
    log = next((mini["vault"] / ".gedaechtnis" / "log").glob("*.tsv"))
    kept = [l for l in log.read_text(encoding="utf-8").splitlines()
            if "# PREAMBLE" not in l and "# ORDER" not in l]
    log.write_text("\n".join(kept) + "\n", encoding="utf-8")
    # exit 1, and that is the point: no view built is a REFUSAL, not a quiet empty run
    res = cold_json(mini, expect=1)
    assert res["views"] == [], "no shape row means no cold view, ever"
    assert len(res["skipped"]) == 4
    assert all("carries no" in why for _name, why in res["skipped"]), res["skipped"]


def test_the_cold_build_BITES_when_a_row_is_wrong(mini):
    """POSITIVE control that the comparison can FAIL, and by the right file. A count of identical
    files that cannot fall is a decoration; this plants one wrong byte in one row and expects
    exactly one file to differ, with a real diff attached."""
    run("importer.py", env=mini["env"])
    log = next((mini["vault"] / ".gedaechtnis" / "log").glob("*.tsv"))
    text = log.read_text(encoding="utf-8")
    assert "NEVER do the thing." in text
    log.write_text(text.replace("NEVER do the thing.", "NEVER do the thing!"), encoding="utf-8")
    run("views.py", "--cold", env=mini["env"])
    cmp = cold_compare(mini)
    assert cmp["identical"] == 3 and cmp["views"] == 4
    assert cmp["differing"][0]["file"].endswith("Canon.md")
    assert "NEVER do the thing!" in cmp["differing"][0]["diff"]


def test_the_cold_build_takes_its_ORDER_from_the_log_and_not_from_the_file(mini):
    """POSITIVE control on the reason `# ORDER` is a row of its own. Reorder the live file WITHOUT
    changing any entry's text: no entry row changes (content is identity), so a build that read
    order off `ord=` flags would emit the OLD order and be wrong. The re-minted order row is what
    keeps the cold build current, and the live build must agree with it."""
    run("importer.py", env=mini["env"])
    canon = mini["vault"] / "Alpha" / "Canon.md"
    pre, *_ = canon.read_text(encoding="utf-8").partition("## One")
    one = "## One\n\nNEVER do the thing.\n\n"
    two = "## Two\n\nSee [[Global/Map]] for the index.\n\n"
    three = "### Three\n\nA sub-heading is an entry too.\n"
    canon.write_text(pre + two + one + three, encoding="utf-8")
    res = import_json(mini)
    assert res["appended"] == 0, "no entry's TEXT changed, so no entry row is new"
    assert res["shape_appended"] == 1, "but the file's order did, so the # ORDER row is re-minted"
    aside = move_live_aside(mini)
    cold_json(mini)
    restore_live(mini, aside)
    cold = (mini["vault"] / ".gedaechtnis" / "views-cold" / "Alpha" / "Canon.md").read_text(encoding="utf-8")
    assert cold.split("\n", 1)[1] == canon.read_text(encoding="utf-8")
    assert cold.index("## Two") < cold.index("## One")


def test_the_cold_build_renders_an_orphan_and_counts_it(mini):
    """POSITIVE control that M-03 is gone on the cold path too, and NEGATIVE on the identity claim:
    an orphan is exactly what makes a cold view differ from its live file, and the count says so
    instead of the diff being a surprise."""
    run("importer.py", env=mini["env"])
    canon = mini["vault"] / "Alpha" / "Canon.md"
    canon.write_text(canon.read_text(encoding="utf-8")
                     .replace("### Three\n\nA sub-heading is an entry too.\n", ""), encoding="utf-8")
    run("importer.py", env=mini["env"])          # re-mints # ORDER without the dropped heading
    res = cold_json(mini)
    assert res["orphan_rows"] == 1, res["skipped"]
    cmp = cold_compare(mini)
    assert cmp["identical"] == 3 and cmp["differing"][0]["file"].endswith("Canon.md")
    cold = (mini["vault"] / ".gedaechtnis" / "views-cold" / "Alpha" / "Canon.md").read_text(encoding="utf-8")
    assert "A sub-heading is an entry too." in cold


def test_the_cold_views_never_land_on_top_of_the_live_files_or_the_warm_views(mini):
    """NEGATIVE control on placement, twice over. The shadow is ADDITIVE: a cold build that wrote
    into a region would have ended the shadow without saying so, and one that overwrote
    `.gedaechtnis/views/` would leave the ordinary run with nothing to be compared against."""
    run("importer.py", env=mini["env"])
    run("views.py", env=mini["env"])
    warm = {p: p.read_bytes() for p in (mini["vault"] / ".gedaechtnis" / "views").rglob("*.md")}
    live = {p: p.read_bytes() for p in sorted(mini["vault"].rglob("*.md"))
            if ".gedaechtnis" not in p.parts}
    run("views.py", "--cold", env=mini["env"])
    assert {p: p.read_bytes() for p in warm} == warm
    assert {p: p.read_bytes() for p in live} == live
    for p in (mini["vault"] / ".gedaechtnis" / "views-cold").rglob("*.md"):
        assert "views-cold" in p.parts


def test_the_comparison_refuses_rather_than_reporting_a_measured_zero(mini):
    """NEGATIVE control on the instrument itself: with nothing built, `--compare` must REFUSE, not
    print `0/0 byte-identical`. A zero with no population behind it has sent this fleet down a wrong
    path before, and a comparison of an empty directory is the easiest way to produce one."""
    run("importer.py", env=mini["env"])
    p = subprocess.run([sys.executable, str(ROOT / "views.py"), "--cold", "--compare"],
                       capture_output=True, text=True, env=mini["env"], timeout=60)
    assert p.returncode != 0
    assert "REFUSED" in (p.stdout + p.stderr)


def test_a_rewritten_entry_that_MOVED_DOWN_the_file_renders_its_current_text(mini):
    """POSITIVE control on the row-selection bug the cold probe caught on the live vault.

    Two rows share a heading — the original at `ord=0` and its rewrite at `ord=1`, because an entry
    was inserted above it. The old rule matched the OLD row (it compared against a count of how many
    times the heading had been seen, which is 0 for a unique heading, and only that row carried
    `ord=0`), so the view rendered 2,317 bytes of superseded text under a heading whose live body was
    a single newline — and scored it as an ordinary fidelity miss. Both builds must render the
    CURRENT text.
    """
    run("importer.py", env=mini["env"])
    canon = mini["vault"] / "Alpha" / "Canon.md"
    text = canon.read_text(encoding="utf-8")
    pre, _sep, rest = text.partition("## One")
    moved = (pre + "## Zero\n\nInserted above, so everything below shifts down.\n\n"
             + "## One" + rest.replace("NEVER do the thing.", "NEVER do the thing, rewritten."))
    canon.write_text(moved, encoding="utf-8")
    run("importer.py", env=mini["env"])

    ones = [r for r in log_rows(mini, region="Alpha", stem="Canon") if r["heading"] == "## One"]
    assert len(ones) == 2, "the premise: the original row and its rewrite both live in the log"
    assert sorted(f for r in ones for f in r["flags"] if f.startswith("ord=")) == ["ord=0", "ord=1"]

    run("views.py", env=mini["env"])
    warm = (mini["vault"] / ".gedaechtnis" / "views" / "Alpha" / "Canon.md").read_text(encoding="utf-8")
    assert "NEVER do the thing, rewritten." in warm
    assert "NEVER do the thing.\n" not in warm, "the superseded row must not be rendered"
    assert warm.split("\n", 1)[1] == moved

    aside = move_live_aside(mini)
    cold_json(mini)
    restore_live(mini, aside)
    cold = (mini["vault"] / ".gedaechtnis" / "views-cold" / "Alpha" / "Canon.md").read_text(encoding="utf-8")
    assert cold.split("\n", 1)[1] == moved
    assert cold_compare(mini)["identical"] == 4
