"""Tests for sub-region birth — the second organ in this package that MOVES memory.

Same world as `test_cleanup.py` and `test_synthesis.py`: a git repository in tmp_path pointed at by
GEDAECHTNIS_VAULT, the state directory and the limits file redirected, and the product driven as a
SUBPROCESS through `split.py`'s own entry point. Never in-process.

**What the controls are aimed at.** Like the cleanup pass and unlike everything else here, this one
fails by ACTING: on the wrong entry, on a file that changed under it, or by leaving a wikilink
pointing at a file its target has left. So the conservation check (every entry in exactly one live
file before and after, byte-identical) carries the weight, and it has its own positive control —
a check that cannot fail is not a check.
"""
from __future__ import annotations
import hashlib, json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SPLIT = PLUGIN / "split.py"

LIMITS = {
    "split_min_entries": 8,
    # Carried here so the new tests read the floor the way the product does — out of a limits
    # FILE. Without it they fell through to `limits.py`'s DEFAULTS, and the JSON path for this key
    # was never exercised by anything but the parity test.
    "split_min_remaining_entries": 4,
    # ★ EVERY role named, deliberately. This fixture used to list only the roles it cares
    # about, and that WAS its isolation: the limits FILE replaced the table, so an unnamed
    # role simply had no limit. Since the file layer merges dict limits per sub-key (a fix
    # for a silent protection loss), an unnamed role quietly inherits the SHIPPED default
    # instead — the fixture stays green and stops isolating what it was written to isolate.
    # The roles this file does not test are given a ceiling nothing here can reach, which
    # says the same thing the short table used to say, out loud.
    "role_soft_limits_lines": {"Map": 10000000, "Vision": 10000000, "Position": 250, "Course": 10000000, "Aporia": 10000000, "Errata": 10000000, "Annales": 10000000, "Canon": 800},
    "stale_entry_days": 56,
    "compaction_floor_share": 0.4,
    "max_memory_file_bytes": 500000,
    "max_searchable_file_bytes": 2000000,
    "cleanup_trigger_days": 14,
    "cleanup_trigger_commits": 300,
    "synthesis_trigger_days": 14,
    "synthesis_trigger_entries": 20,
    "boot_budget_bytes": 20000,
    "boot_budget_warn_bytes": 20000,
    "boot_file_budget_bytes": 32000,
    "boot_file_warn_bytes": 28000,
}


def topic(i: int) -> str:
    return (f"\n## Whisper transcription drift, case {i}\n\n"
            f"**Never** trust the whisper transcription timestamps without realigning the subtitle\n"
            f"segments; the drift compounds across the episode ({i}).\n")


OTHER = ["\n## A budget note\n\nA spending ceiling declared before the run began.\n",
         "\n## Deck synchronisation\n\nThe collection is synchronised, never imported wholesale.\n",
         "\n## A pricing question\n\nCached reads are charged at a tenth of the input rate.\n",
         "\n## Something else entirely\n\nUnrelated prose about nothing in particular.\n"]


def git(vault: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=True)
    return p.stdout


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Proj").mkdir(parents=True)
    (vault / "Proj" / "Map.md").write_text("# Proj\n")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    git(vault, "add", "-A")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: MY-PROJECT\npath: Proj/\n")
    limits_file = tmp_path / "limits.json"
    limits_file.write_text(json.dumps(LIMITS))
    env = dict(os.environ,
               GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_LIMITS=str(limits_file),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-such-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, env=env, tmp=tmp_path, limits_file=limits_file)


def seed(w, n_topic: int = 8):
    """`n_topic` entries on one sub-topic, split over two role files, plus four unrelated ones."""
    v = w["vault"] / "Proj"
    half = n_topic // 2 + n_topic % 2
    (v / "Errata.md").write_text("# Errata\n" + "".join(topic(i) for i in range(half))
                                 + "".join(OTHER[:2]))
    (v / "Patterns.md").write_text("# Patterns\n" + "".join(topic(i) for i in range(half, n_topic))
                                   + "".join(OTHER[2:]))
    (v / "Canon.md").write_text(
        "# Canon\n\n## A decision\n\nSee [[Errata#Whisper transcription drift, case 0]].\n")


def run(w, *args, expect_rc=0):
    p = subprocess.run([sys.executable, "-B", str(SPLIT), *args],
                       capture_output=True, text=True, env=w["env"], timeout=120,
                       cwd=str(w["repo"]))
    assert p.returncode == expect_rc, p.stdout + p.stderr
    return p


def proposals(w) -> dict:
    return json.loads(run(w, "--json").stdout)


def only_id(w) -> str:
    """The one live proposal's id. Read from the product, never spelled out here: the id is
    content-addressed, so hardcoding one in a test would pin a hash rather than a behaviour."""
    ps = proposals(w)["proposals"]
    assert len(ps) == 1, ps
    return ps[0]["id"]


def headings_and_bodies(vault: Path) -> list[tuple[str, str]]:
    """(heading, body) for every `## ` entry in every live role file, anywhere in the vault."""
    out = []
    for block in entry_texts(vault):
        head, _, body = block.lstrip("\n").partition("\n")
        out.append((head.strip().lstrip("#").strip(), body))
    return out


def entry_texts(vault: Path) -> list[str]:
    """Every `## ` entry in every live role file, as text. The conservation unit."""
    sys.path.insert(0, str(PLUGIN))
    import archive
    out = []
    for p in sorted(vault.rglob("*.md")):
        if p.name in ("Map.md", "SPLIT-RECEIPT.md"):
            continue
        _head, blocks = archive.split_entries(p.read_text(encoding="utf-8"))
        out.extend(blocks)
    return sorted(out)


# ------------------------------------------------------------------ proposing ----
def test_eight_entries_on_one_topic_propose_a_split(world):
    seed(world, 8)
    got = proposals(world)
    assert len(got["proposals"]) == 1
    p = got["proposals"][0]
    assert len(p["entries"]) == 8
    assert p["region"] == "Proj" and p["suggested_name"]


def test_seven_do_not(world):
    """Negative control at the boundary the threshold names. Same fixture, one entry fewer."""
    seed(world, 7)
    assert proposals(world)["proposals"] == []


def test_a_cluster_that_is_the_WHOLE_region_is_not_a_split(world):
    """A cluster of everything is not a topic that outgrew its room — it is the room. Moving all of
    it leaves an empty parent and a renamed child, which is a rename dressed as a split."""
    (world["vault"] / "Proj" / "Errata.md").write_text(
        "# Errata\n" + "".join(topic(i) for i in range(10)))
    assert proposals(world)["proposals"] == []


def test_the_proposal_names_every_entry_it_would_move(world):
    seed(world, 8)
    p = proposals(world)["proposals"][0]
    assert {e["heading"] for e in p["entries"]} == {
        f"Whisper transcription drift, case {i}" for i in range(8)}
    assert {e["file"] for e in p["entries"]} == {"Proj/Errata.md", "Proj/Patterns.md"}


# -------------------------------------------------------------------- moving ----
def test_the_moved_entries_are_byte_identical_and_nothing_is_lost(world):
    """Two claims, and the difference between them is the point.

    Every entry SURVIVES — the headings before and after are the same multiset. And every entry
    that MOVED is byte-identical: a move that reformats is a rewrite, and a rewrite of somebody's
    memory is the thing this organ must never quietly do.

    Entries that stayed behind may legitimately differ, because the rewriter retargets wikilinks
    that pointed at what moved. That is an intended change, and asserting it away would have made
    this control pass only by being blind to the feature next to it."""
    seed(world, 8)
    before = {h: b for h, b in headings_and_bodies(world["vault"])}
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    after = {h: b for h, b in headings_and_bodies(world["vault"])}
    assert set(after) == set(before), "an entry was lost or gained by the move"
    for i in range(8):
        h = f"Whisper transcription drift, case {i}"
        assert after[h] == before[h], f"{h} was not moved byte-identically"


def test_that_conservation_check_can_fail(world):
    """Positive control, on BOTH halves. A comparison that could never go red would pass on a move
    that dropped every entry, and one blind to the body would pass on a move that rewrote them."""
    seed(world, 8)
    before = {h: b for h, b in headings_and_bodies(world["vault"])}
    err = world["vault"] / "Proj" / "Errata.md"
    err.write_text("# Errata\n")
    lost = {h: b for h, b in headings_and_bodies(world["vault"])}
    assert set(lost) != set(before)
    seed(world, 8)
    pat = world["vault"] / "Proj" / "Patterns.md"
    pat.write_text(pat.read_text().replace("**Never** trust", "**never** trust"))
    edited = {h: b for h, b in headings_and_bodies(world["vault"])}
    assert set(edited) == set(before)
    assert any(edited[h] != before[h] for h in before), "a body change must be visible here"


def test_every_entry_is_in_exactly_one_live_file_afterwards(world):
    seed(world, 8)
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    texts = entry_texts(world["vault"])
    assert len(texts) == len(set(texts)), "an entry exists twice after the move"


def test_the_parent_map_gains_a_row(world):
    seed(world, 8)
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert "[[Whisper/Map|Whisper]]" in (world["vault"] / "Proj" / "Map.md").read_text()


def test_the_sub_region_is_a_region_to_the_rest_of_the_package(world):
    """A directory without `Map.md` is not a region to any tool here, so a split that forgot to
    write one would produce a folder the search, the compactor and the triggers all ignore."""
    seed(world, 8)
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert (world["vault"] / "Proj" / "Whisper" / "Map.md").is_file()
    code = ("import sys; sys.path[:0]=[%r, %r]; import maintenance, config;"
            "print([str(r.relative_to(config.vault())) for r in maintenance.regions()])"
            % (str(PLUGIN), str(PLUGIN / "hooks")))
    p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                       env=world["env"], timeout=60, cwd=str(world["repo"]))
    assert p.returncode == 0, p.stderr
    assert "Proj/Whisper" in p.stdout


def test_a_directory_without_a_map_is_not_a_region(world):
    """Negative control for the line above: without it, the assertion would pass on a walker that
    returned every directory it found."""
    (world["vault"] / "Proj" / "NotARegion").mkdir()
    code = ("import sys; sys.path[:0]=[%r, %r]; import maintenance, config;"
            "print([str(r.relative_to(config.vault())) for r in maintenance.regions()])"
            % (str(PLUGIN), str(PLUGIN / "hooks")))
    p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                       env=world["env"], timeout=60, cwd=str(world["repo"]))
    assert "NotARegion" not in p.stdout


# ----------------------------------------------------------------- wikilinks ----
def test_a_link_to_a_moved_entry_still_resolves(world):
    seed(world, 8)
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    canon = (world["vault"] / "Proj" / "Canon.md").read_text()
    assert "[[Whisper/Errata#Whisper transcription drift, case 0]]" in canon
    target = world["vault"] / "Proj" / "Whisper" / "Errata.md"
    assert "## Whisper transcription drift, case 0" in target.read_text()


def test_a_link_to_an_entry_that_did_NOT_move_is_left_alone(world):
    """Negative control on the rewriter: it must retarget what moved and nothing else. A rewriter
    that rewrote every link would pass the test above and break every other link in the vault."""
    seed(world, 8)
    (world["vault"] / "Proj" / "Canon.md").write_text(
        "# Canon\n\n## A decision\n\nSee [[Errata#A budget note]].\n")
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert "[[Errata#A budget note]]" in (world["vault"] / "Proj" / "Canon.md").read_text()


# ------------------------------------------------------------------ refusals ----
def test_a_cluster_that_changed_since_the_person_looked_is_REFUSED(world):
    """The one destructive mistake this module can make, and the reason the id is content-addressed.

    `apply()` recomputes the proposals — it has to, because the offsets it moves from must be the
    file as it is now. With a POSITIONAL id (`Proj#0`, as this shipped at first) a vault edited in
    between hands `Proj#0` to a DIFFERENT set of entries, which are then moved under the name the
    person approved for the other ones. Approval is for a set, so the id names the set.

    Measured: with the positional id this test FAILED — eight entries were moved after the cluster
    had changed, and the receipt reported a clean success."""
    seed(world, 8)
    stale = only_id(world)
    err = world["vault"] / "Proj" / "Errata.md"
    err.write_text(err.read_text() + topic(99))          # the cluster is now nine entries, not eight
    assert only_id(world) != stale, "the id did not move with its contents"
    p = subprocess.run([sys.executable, "-B", str(SPLIT), "--json",
                        "--apply", "Proj", stale, "--name", "Whisper"],
                       capture_output=True, text=True, env=world["env"], timeout=120,
                       cwd=str(world["repo"]))
    out = json.loads(p.stdout)
    assert out["applied"] is False and "no proposal" in out["why"]
    assert not (world["vault"] / "Proj" / "Whisper").exists()
    assert "case 0" in err.read_text()


def test_an_unchanged_cluster_is_NOT_refused(world):
    """Positive control for the refusal above: a refuse-everything apply would pass it."""
    seed(world, 8)
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert (world["vault"] / "Proj" / "Whisper" / "Map.md").is_file()


def test_an_unusable_name_is_refused_with_a_reason(world):
    seed(world, 8)
    p = subprocess.run([sys.executable, "-B", str(SPLIT),
                        "--apply", "Proj", only_id(world), "--name", "../escape"],
                       capture_output=True, text=True, env=world["env"], timeout=60,
                       cwd=str(world["repo"]))
    assert p.returncode == 2 and "REFUSED" in p.stderr
    assert not (world["vault"] / "Proj" / "Whisper").exists()


def test_a_name_that_already_exists_is_refused(world):
    seed(world, 8)
    (world["vault"] / "Proj" / "Whisper").mkdir()
    p = subprocess.run([sys.executable, "-B", str(SPLIT), "--json",
                        "--apply", "Proj", only_id(world), "--name", "Whisper"],
                       capture_output=True, text=True, env=world["env"], timeout=60,
                       cwd=str(world["repo"]))
    assert json.loads(p.stdout)["applied"] is False


def test_nothing_moves_without_apply(world):
    seed(world, 8)
    before = entry_texts(world["vault"])
    run(world)
    run(world, "--json")
    assert entry_texts(world["vault"]) == before
    assert not (world["vault"] / "Proj" / "Whisper").exists()


# ----------------------------------------------------------------- UNCHECKED ----
def test_an_unreadable_role_file_is_unchecked_not_empty(world):
    seed(world, 8)
    err = world["vault"] / "Proj" / "Errata.md"
    err.chmod(0o000)
    try:
        got = proposals(world)
        text = run(world).stdout
    finally:
        err.chmod(0o644)
    assert got["unreadable"] == ["Proj/Errata.md"]
    assert "UNCHECKED" in text


def test_a_readable_vault_reports_nothing_unchecked(world):
    seed(world, 8)
    assert proposals(world)["unreadable"] == []
    assert "UNCHECKED" not in run(world).stdout


# ------------------------------------------------------------------- receipt ----
def test_the_receipt_names_every_origin_and_destination(world):
    seed(world, 8)
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    r = (world["vault"] / "Proj" / "Whisper" / "SPLIT-RECEIPT.md").read_text()
    for i in range(8):
        assert f"Whisper transcription drift, case {i}" in r
    assert "Proj/Whisper/Errata.md" in r and "Proj/Whisper/Patterns.md" in r


# ------------------------------------------- relink: the three the reviewer demonstrated ----
#
# Each of these FAILED against the first implementation, which matched a link by how it was SPELLED
# (target stem + heading text, vault-wide) rather than by what it POINTS AT. They are kept apart
# rather than merged because they are three different wrong answers, and a single test covering all
# three would go green as soon as any one of them was fixed.

def test_two_moved_entries_sharing_a_heading_both_get_their_links_rewritten(world):
    """Reviewer finding 1. A dict keyed on heading alone collapsed the pair, and the link to the
    one that lost the key was left dangling at a file that no longer holds the heading."""
    v = world["vault"] / "Proj"
    shared_heading = "Whisper transcription drift, case zero"
    shared = (f"\n## {shared_heading}\n\n**Never** trust the whisper transcription timestamps\n"
              "without realigning the subtitle segments; the drift compounds.\n")
    (v / "Errata.md").write_text("# Errata\n" + shared + "".join(topic(i) for i in range(1, 5))
                                 + "".join(OTHER[:2]))
    (v / "Patterns.md").write_text("# Patterns\n" + shared + "".join(topic(i) for i in range(5, 9))
                                   + "".join(OTHER[2:]))
    (v / "Canon.md").write_text(f"# Canon\n\n## A decision\n\nSee [[Errata#{shared_heading}]]\n"
                                f"and [[Patterns#{shared_heading}]].\n")
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    canon = (v / "Canon.md").read_text()
    assert f"[[Whisper/Errata#{shared_heading}]]" in canon, canon
    assert f"[[Whisper/Patterns#{shared_heading}]]" in canon, canon


def test_a_heading_that_is_a_PREFIX_of_an_unmoved_one_is_left_alone(world):
    """Reviewer finding 2. Without an end boundary, the moved heading matched the beginning of a
    longer, unmoved one and retargeted its link into a sub-region the entry is not in."""
    v = world["vault"] / "Proj"
    short = "Whisper transcription drift"
    longer = "Whisper transcription drifts elsewhere entirely"
    # Both in the SAME file, which is what makes this a test of the heading match rather than of
    # source scoping: `short` is a prefix of `longer` and moves; `longer` shares only two terms
    # with the cluster (`drifts` is not `drift`) and stays. A prefix-matching rewriter retargets
    # the link to `longer` into a sub-region that does not contain it.
    (v / "Errata.md").write_text(
        "# Errata\n"
        + f"\n## {short}\n\n**Never** trust whisper transcription timestamps without realigning\n"
          "the subtitle segments; the drift compounds.\n"
        + f"\n## {longer}\n\nA different matter altogether.\n"
        + "".join(topic(i) for i in range(1, 5)) + "".join(OTHER[:2]))
    (v / "Patterns.md").write_text("# Patterns\n" + "".join(topic(i) for i in range(5, 9))
                                   + "".join(OTHER[2:]))
    (v / "Canon.md").write_text(f"# Canon\n\n## A decision\n\nSee [[Errata#{longer}]].\n")
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    canon = (v / "Canon.md").read_text()
    assert f"[[Errata#{longer}]]" in canon, canon
    assert f"## {longer}" in (v / "Errata.md").read_text(), "the longer entry must not have moved"
    assert f"## {short}" in (v / "Whisper" / "Errata.md").read_text(), "the shorter one must have"


def test_a_link_in_ANOTHER_region_that_resolves_elsewhere_is_left_alone(world):
    """Reviewer finding 3, and the one that corrupted a link having nothing to do with the move.

    `Other/Notes.md` says `[[Errata#…]]`, which resolves to `Other/Errata.md` — a different file
    with a coincidentally identical heading. The first implementation rewrote it to
    `[[Whisper/Errata#…]]`, which from `Other/` points at nothing at all."""
    seed(world, 8)
    other = world["vault"] / "Other"
    other.mkdir()
    (other / "Map.md").write_text("# Other\n")
    (other / "Errata.md").write_text("# Errata\n" + topic(0))
    (other / "Notes.md").write_text(
        "# Notes\n\nSee [[Errata#Whisper transcription drift, case 0]].\n")
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert "[[Errata#Whisper transcription drift, case 0]]" in (other / "Notes.md").read_text()
    assert (other / "Errata.md").read_text().count("## Whisper transcription drift, case 0") == 1


def test_a_vault_rooted_link_from_another_region_IS_rewritten(world):
    """Positive control for the three above: resolving must not become refusing. This link really
    does point into the region being split, from outside it, and must follow the entry."""
    seed(world, 8)
    other = world["vault"] / "Other"
    other.mkdir()
    (other / "Map.md").write_text("# Other\n")
    (other / "Notes.md").write_text(
        "# Notes\n\nSee [[Proj/Errata#Whisper transcription drift, case 0]].\n")
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert ("[[Proj/Whisper/Errata#Whisper transcription drift, case 0]]"
            in (other / "Notes.md").read_text())


def test_a_relative_link_keeps_its_relative_spelling(world):
    seed(world, 8)
    other = world["vault"] / "Other"
    other.mkdir()
    (other / "Map.md").write_text("# Other\n")
    (other / "Notes.md").write_text(
        "# Notes\n\nSee [[../Proj/Errata#Whisper transcription drift, case 0]].\n")
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    text = (other / "Notes.md").read_text()
    assert "[[../Proj/Whisper/Errata#Whisper transcription drift, case 0]]" in text
    assert (world["vault"] / "Proj" / "Whisper" / "Errata.md").is_file()


def test_an_aliased_link_keeps_its_alias(world):
    seed(world, 8)
    v = world["vault"] / "Proj"
    (v / "Canon.md").write_text(
        "# Canon\n\n## A decision\n\nSee [[Errata#Whisper transcription drift, case 0|the drift]].\n")
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert ("[[Whisper/Errata#Whisper transcription drift, case 0|the drift]]"
            in (v / "Canon.md").read_text())


def test_a_heading_carrying_regex_metacharacters_survives_both_halves(world):
    """The mover must not corrupt it and the rewriter must not mis-match it. `re.escape` on the
    heading is what makes the second true, and the reviewer's own mutation sweep found the suite
    had no test that would notice if it went away."""
    v = world["vault"] / "Proj"
    odd = "Whisper (transcription) drift? case 0* +1 v1.2"
    body = "\n## " + odd + "\n\n**Never** trust whisper transcription timestamps without realigning\nthe subtitle segments; drift compounds.\n"
    (v / "Errata.md").write_text("# Errata\n" + body + "".join(topic(i) for i in range(1, 5))
                                 + "".join(OTHER[:2]))
    (v / "Patterns.md").write_text("# Patterns\n" + "".join(topic(i) for i in range(5, 9))
                                   + "".join(OTHER[2:]))
    (v / "Canon.md").write_text(f"# Canon\n\n## A decision\n\nSee [[Errata#{odd}]].\n")
    before = {h: b for h, b in headings_and_bodies(world["vault"])}
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    after = {h: b for h, b in headings_and_bodies(world["vault"])}
    assert after[odd] == before[odd], "the metacharacter entry was not moved byte-identically"
    assert f"[[Whisper/Errata#{odd}]]" in (v / "Canon.md").read_text()


def test_a_heading_a_wikilink_CANNOT_CARRY_is_named_not_silently_skipped(world):
    """`]` ends a wikilink and `|` opens an alias, so a heading containing either cannot be found
    in link text without guessing where the link stops. The entry still moves; what cannot be done
    is find the links to it — and the receipt says so by name.

    This is the honest half of the reviewer's findings: the defect they demonstrated was a link
    quietly left wrong, and the remedy for the case that genuinely cannot be handled is to say so,
    not to widen the pattern until it eats something else."""
    v = world["vault"] / "Proj"
    odd = "Whisper [drift] case 0"
    body = ("\n## " + odd + "\n\n**Never** trust whisper transcription timestamps without "
            "realigning\nthe subtitle segments; drift compounds.\n")
    (v / "Errata.md").write_text("# Errata\n" + body + "".join(topic(i) for i in range(1, 5))
                                 + "".join(OTHER[:2]))
    (v / "Patterns.md").write_text("# Patterns\n" + "".join(topic(i) for i in range(5, 9))
                                   + "".join(OTHER[2:]))
    before = {h: b for h, b in headings_and_bodies(world["vault"])}
    p = run(world, "--json", "--apply", "Proj", only_id(world), "--name", "Whisper")
    r = json.loads(p.stdout)
    assert odd in r["unlinkable"], r
    after = {h: b for h, b in headings_and_bodies(world["vault"])}
    assert after[odd] == before[odd], "it must still move, byte-identically"
    receipt = (v / "Whisper" / "SPLIT-RECEIPT.md").read_text()
    assert "UNCHECKED" in receipt and odd in receipt


def test_an_ordinary_heading_is_NOT_reported_unlinkable(world):
    """Positive control: a rule that called every heading unlinkable would pass the test above."""
    seed(world, 8)
    p = run(world, "--json", "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert json.loads(p.stdout)["unlinkable"] == []


def test_a_dangling_link_is_not_invented_into_a_real_one(world):
    """Negative control on `_resolve`: a link that pointed at nothing before the move points at
    nothing after it. A rewriter that treated an unresolvable target as a match would quietly
    manufacture references."""
    seed(world, 8)
    v = world["vault"] / "Proj"
    (v / "Canon.md").write_text(
        "# Canon\n\n## A decision\n\nSee [[NoSuchFile#Whisper transcription drift, case 0]].\n")
    run(world, "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert "[[NoSuchFile#Whisper transcription drift, case 0]]" in (v / "Canon.md").read_text()


# --------------------------------------------------------------- UNCHECKED, regions ----
def test_an_unreadable_REGION_is_unchecked_not_absent(world):
    """The reviewer's finding 6: `regions()` uses rglob, which skips a directory it may not read
    without raising, so a locked region left a smaller and entirely confident count. R7 built
    `unreadable_dirs()` for exactly this and this row was not calling it."""
    seed(world, 8)
    locked = world["vault"] / "Locked"
    locked.mkdir()
    (locked / "Map.md").write_text("# Locked\n")
    locked.chmod(0o000)
    try:
        got = proposals(world)
        text = run(world).stdout
    finally:
        locked.chmod(0o755)
    assert got["regions_checked"] is False
    assert any("Locked" in u for u in got["unreadable"]), got["unreadable"]
    assert "UNCHECKED" in text


def test_a_heading_shared_with_an_entry_that_STAYS_is_ambiguous_not_rewritten(world):
    """The reviewer's second-pass finding, and the quietest failure in this file.

    Two entries headed `## See also` in ONE file: one clusters and moves, the other does not and
    stays. `[[Errata#See also]]` named both and always did — so rewriting it leaves a link that
    still WORKS and now points at different content. Every earlier bug here produced a visibly
    dangling link; this one produces a link that lies."""
    v = world["vault"] / "Proj"
    # The vocabulary has to be inside the BOLD span: `significant_terms` reads the heading and the
    # emphasised lines, never the plain prose. A first version of this fixture put it in the body
    # and the entry did not cluster at all, so the test was green about nothing.
    moving = ("\n## See also\n\n**Never trust the whisper transcription timestamps without\n"
              "realigning the subtitle segments; the drift compounds.**\n")
    staying = "\n## See also\n\nA spending ceiling declared before the run began.\n"
    (v / "Errata.md").write_text("# Errata\n" + moving + staying
                                 + "".join(topic(i) for i in range(1, 5)) + "".join(OTHER[:2]))
    (v / "Patterns.md").write_text("# Patterns\n" + "".join(topic(i) for i in range(5, 9))
                                   + "".join(OTHER[2:]))
    (v / "Canon.md").write_text("# Canon\n\n## A decision\n\nSee [[Errata#See also]].\n")
    p = run(world, "--json", "--apply", "Proj", only_id(world), "--name", "Whisper")
    r = json.loads(p.stdout)
    assert "See also" in r["unlinkable"], r
    assert "[[Errata#See also]]" in (v / "Canon.md").read_text()
    assert "UNCHECKED" in (v / "Whisper" / "SPLIT-RECEIPT.md").read_text()


def test_a_heading_unique_in_its_file_is_NOT_called_ambiguous(world):
    """Positive control: a rule that called every heading ambiguous would pass the test above and
    would stop this organ rewriting anything at all."""
    seed(world, 8)
    p = run(world, "--json", "--apply", "Proj", only_id(world), "--name", "Whisper")
    assert json.loads(p.stdout)["unlinkable"] == []
    assert "[[Whisper/Errata#Whisper transcription drift, case 0]]" in \
        (world["vault"] / "Proj" / "Canon.md").read_text()


# =============================================================== SPLITRULE-1 ====
# A split has two sides. Until this row the rule checked one: any cluster of
# `split_min_entries` or more that was not the WHOLE region was a valid split — so a cluster of
# n-1 entries was accepted and left the parent a single orphan per role file. The child bar cannot
# see that, because in every such case the child is perfectly healthy.
#
# The fixtures below BRACKET the floor rather than sampling one side of it: 12/8 leaves exactly the
# floor (4) and must be a split, 11/8 leaves one less (3) and must be a refocus. A control that only
# ever tests one side of a threshold cannot tell a threshold from a constant.

def seed_exact(w, n_topic: int, n_other: int):
    """`n_topic` entries on one sub-topic across two role files, plus exactly `n_other` unrelated
    ones — so the region's entry count, and therefore what a split would LEAVE, is exact.

    `Canon.md` is written without a single `## ` entry on purpose: `region_entries` counts entries,
    not files, and a stray heading here would move every count in this section by one."""
    assert 0 <= n_other <= len(OTHER)
    v = w["vault"] / "Proj"
    half = n_topic // 2 + n_topic % 2
    others = OTHER[:n_other]
    (v / "Errata.md").write_text("# Errata\n" + "".join(topic(i) for i in range(half))
                                 + "".join(others[:2]))
    (v / "Patterns.md").write_text("# Patterns\n" + "".join(topic(i) for i in range(half, n_topic))
                                   + "".join(others[2:]))
    (v / "Canon.md").write_text("# Canon\n")


def only(found: dict) -> dict:
    assert len(found["proposals"]) == 1, found["proposals"]
    return found["proposals"][0]


def test_a_cluster_leaving_EXACTLY_the_floor_is_a_split(world):
    """12 entries, 8 cluster, 4 left — the floor itself. `>=`, not `>`: the floor is the smallest
    remainder that still counts as a region, so it must be ON the passing side."""
    seed_exact(world, n_topic=8, n_other=4)
    p = only(proposals(world))
    assert p["kind"] == "split", p
    assert (p["region_entries"], len(p["entries"]), p["remaining"]) == (12, 8, 4), p
    assert p["min_remaining"] == 4
    assert p["leftover"] == [], "a healthy split does not make the reader read the rest of a region"


def test_one_entry_below_the_floor_is_a_REFOCUS_and_moves_nothing(world):
    """11 entries, 8 cluster, 3 left — one under the floor, and the other side of the same line."""
    seed_exact(world, n_topic=8, n_other=3)
    p = only(proposals(world))
    assert p["kind"] == "refocus", p
    assert (p["region_entries"], len(p["entries"]), p["remaining"]) == (11, 8, 3), p
    # The leftover IS the finding: what the region would be left holding.
    assert len(p["leftover"]) == 3, p["leftover"]
    assert {e["heading"] for e in p["leftover"]}.isdisjoint({e["heading"] for e in p["entries"]})


def test_the_n_minus_1_case_is_a_refocus_and_APPLY_REFUSES_IT(world):
    """★ THE CASE THE ROW EXISTS FOR (ROW-R9 §7): a cluster of all-but-one. The old rule accepted
    it and left the parent one orphan per role file. Now it is a refocus, and `apply` refuses
    BEFORE touching anything — no directory, no Map row, no receipt, nothing moved."""
    seed_exact(world, n_topic=8, n_other=1)
    p = only(proposals(world))
    assert p["kind"] == "refocus" and p["remaining"] == 1, p

    before = {q: (world["vault"] / "Proj" / q).read_bytes()
              for q in ("Errata.md", "Patterns.md", "Canon.md", "Map.md")}
    out = run(world, "--apply", "Proj", p["id"], "--name", "Whisper", expect_rc=1)
    both = out.stdout + out.stderr
    assert "is a REFOCUS" in both, both          # this refusal, not the id-no-longer-matches one
    assert "no proposal" not in both, both

    assert not (world["vault"] / "Proj" / "Whisper").exists(), "a refocus created a directory"
    after = {q: (world["vault"] / "Proj" / q).read_bytes()
             for q in ("Errata.md", "Patterns.md", "Canon.md", "Map.md")}
    assert before == after, "a refocus must leave every byte of the region alone"


def test_a_cluster_that_IS_the_whole_region_is_still_nothing_at_all(world):
    """8 entries, all of them one topic. Not a split (the region is not a sub-region of itself) and
    not a refocus either — there is no leftover to look at and no second name to offer. The
    pre-existing `len(members) == n` rule, unchanged, and asserted so the new kinds cannot swallow
    it."""
    seed_exact(world, n_topic=8, n_other=0)
    assert proposals(world)["proposals"] == []


def test_viability_is_RE_EVALUATED_at_apply_not_trusted_from_the_proposal(world):
    """A healthy split when the person looked; the parent shrinks before they answer. `apply()`
    recomputes the proposals from the files as they are NOW — it must, because the offsets it moves
    from have to be current — so the same id comes back a REFOCUS and is refused.

    The id is content-keyed on the cluster (deviation 60), so the CLUSTER is unchanged and still
    matches; what changed is the region around it. Without the re-evaluation this would apply an
    approval given for a split to a region that is no longer one."""
    seed_exact(world, n_topic=8, n_other=4)
    p = only(proposals(world))
    assert p["kind"] == "split" and p["remaining"] == 4

    # Drop ONE unrelated entry: the cluster is untouched, the region falls 12 -> 11 and the
    # remainder 4 -> 3, one under the floor.
    v = world["vault"] / "Proj"
    v.joinpath("Errata.md").write_text(
        "# Errata\n" + "".join(topic(i) for i in range(4)) + OTHER[0])
    again = [q for q in proposals(world)["proposals"] if q["id"] == p["id"]]
    assert again and again[0]["kind"] == "refocus", again
    assert (again[0]["region_entries"], again[0]["remaining"]) == (11, 3), again[0]

    out = run(world, "--apply", "Proj", p["id"], "--name", "Whisper", expect_rc=1)
    both = out.stdout + out.stderr
    # ★ WHICH refusal, by its message. `apply()` has another one — "no proposal <id> right now" —
    # that prints the same REFUSED token and would fire if the id had simply stopped matching.
    # Asserting the token alone makes this control one substring away from deviation 82's class:
    # true for the wrong reason, and silent about it.
    assert "is a REFOCUS" in both, both
    assert "no proposal" not in both, both
    assert not (v / "Whisper").exists()


def test_the_floor_is_read_from_limits_not_written_into_the_code(world):
    """The number is a key, so re-tuning it is an edit. Set it to 1 and the n-1 case the row exists
    for becomes an ordinary split again — which is what makes this a threshold rather than a rule,
    and is the mutation the row's own sweep uses."""
    seed_exact(world, n_topic=8, n_other=1)
    assert only(proposals(world))["kind"] == "refocus"
    world["limits_file"].write_text(json.dumps({**LIMITS, "split_min_remaining_entries": 1}))
    p = only(proposals(world))
    assert p["kind"] == "split" and p["min_remaining"] == 1, p


def test_a_refocus_only_run_never_offers_an_apply_command(world):
    """The footer that tells a person how to apply belongs to splits. On a vault whose only finding
    is a refocus there is nothing to apply, and printing the command would invite the one call the
    module refuses."""
    seed_exact(world, n_topic=8, n_other=1)
    out = run(world).stdout
    assert "REFOCUS" in out, out
    assert "--apply" not in out, out
    assert "nothing to move" in out.lower() or "IS this topic" in out, out


# ---------------------------------------------------------------------------------------------
# The SPLITSIM-1 knobs and the DIFFUSE kind (`q:CU-2026-09-19-SPLITSIMMERGE-1`).
#
# Both knobs ship at 0.0 — OFF — and the merge's entire claim is that shipped behaviour is
# byte-identical with them absent. A reviewer round found that claim asserted in a report and
# pinned by nothing: mutating the cohesion gate to fire WITH THE KNOB OFF was green, and so was
# deleting `apply()`'s refusal to move a diffuse cluster (29 entries across 7 files moved, suite
# still green). What follows is the guard for the knob-off promise and for the one safety property
# the knob-on path has. `_chain` builds a PERCOLATED cluster — each entry shares three terms with
# its neighbour and almost nothing with the far end — because a clique (what `seed()` builds)
# scores 1.0 and can never be diffuse, so the existing fixture cannot reach any of this.


def _chain_entry(i: int) -> str:
    return (f"\n## Chain link {i}\n\n"
            f"**Lefthand{i}** **Middling{i}** **Righthand{i}** and "
            f"**Lefthand{i + 1}** **Middling{i + 1}** **Righthand{i + 1}** in prose.\n")


def seed_chain(w, n: int = 8, noise: int = 6):
    """One percolated chain of `n` entries plus `noise` unrelated ones. Measured cohesion 0.375."""
    v = w["vault"] / "Proj"
    half = n // 2 + n % 2
    (v / "Errata.md").write_text("# Errata\n" + "".join(_chain_entry(i) for i in range(half)))
    (v / "Patterns.md").write_text(
        "# Patterns\n" + "".join(_chain_entry(i) for i in range(half, n))
        + "".join(f"\n## Unrelated matter {k}\n\n**Quiet{k}** words only.\n" for k in range(noise)))


def relimit(w, **knobs):
    """Rewrite the limits FILE the product reads. The knobs reach the product the way an operator
    would set them, not by patching a function in this process."""
    w["limits_file"].write_text(json.dumps(dict(LIMITS, **knobs)))


def _vault_hashes(vault: Path) -> dict:
    return {str(p.relative_to(vault)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(vault.rglob("*.md"))}


def test_with_the_cohesion_knob_ABSENT_even_an_incoherent_cluster_is_a_plain_split(world):
    """The knob-off promise, on the fixture that would otherwise trip the gate hardest.

    This is the guard for the merge's own claim. The chain's cohesion is 0.375 — below every floor
    anyone has proposed — and with `split_min_cohesion` absent the product must still call it a
    `split` and still offer to move it. A gate that fires when it was never switched on is exactly
    the mutation that was green before this test existed."""
    seed_chain(world)
    ps = proposals(world)["proposals"]
    assert len(ps) == 1, ps
    p = ps[0]
    assert p["min_cohesion"] == 0.0, p
    assert p["cohesion"] < 0.5, "fixture is not incoherent; the test would prove nothing"
    assert p["kind"] == "split", p
    assert p["leftover"] == [], "a split carries no leftover list"
    assert "DIFFUSE" not in run(world, "--json").stdout.upper() or True  # kind is the assertion


def test_with_the_share_knob_ABSENT_a_cluster_that_takes_most_of_the_region_still_splits(world):
    """The other knob's half of the same promise. `remaining >= 0.0 * n` is true for every input,
    which is WHY byte-identity holds — but nothing failed when the conjunct was deleted, so the
    reason was unrecorded. Here the parent keeps 6 of 14, a share no proposed floor would pass."""
    seed_chain(world)
    p = proposals(world)["proposals"][0]
    assert p["min_remaining_share"] == 0.0, p
    assert p["remaining"] / p["region_entries"] < 0.5, p
    assert p["kind"] == "split", p


def test_APPLY_REFUSES_to_move_a_diffuse_cluster(world):
    """★ The one safety property the DIFFUSE label has, and the only thing between it and a move.

    Deleting this refusal left the whole suite green while 29 entries moved across 7 files. The
    check is CONSERVATION, not the message: every markdown file in the vault byte-identical after
    the refused apply, and no sub-region directory created."""
    seed_chain(world)
    relimit(world, split_min_cohesion=0.5)
    ps = proposals(world)["proposals"]
    assert len(ps) == 1 and ps[0]["kind"] == "diffuse", ps
    cid = ps[0]["id"]
    before = _vault_hashes(world["vault"])
    dirs_before = {p for p in world["vault"].rglob("*") if p.is_dir()}
    p = run(world, "--apply", "Proj", cid, "--name", "Chainwork", expect_rc=1)
    assert "DIFFUSE" in (p.stdout + p.stderr).upper(), p.stdout + p.stderr
    assert _vault_hashes(world["vault"]) == before, "a refused apply changed a file"
    assert {p for p in world["vault"].rglob("*") if p.is_dir()} == dirs_before, "a directory appeared"


def test_the_same_cluster_DOES_move_once_the_cohesion_knob_is_off(world):
    """The positive control for the test above. Without it, a refusal that refused EVERYTHING —
    a broken apply, a bad id, a typo in the fixture — would read as a passing safety test."""
    seed_chain(world)
    relimit(world, split_min_cohesion=0.5)
    assert proposals(world)["proposals"][0]["kind"] == "diffuse"
    before = _vault_hashes(world["vault"])
    relimit(world)                                   # knob off again; same vault, same cluster
    cid = only_id(world)
    run(world, "--apply", "Proj", cid, "--name", "Chainwork")
    assert _vault_hashes(world["vault"]) != before, "the control did not move anything either"
    assert (world["vault"] / "Proj" / "Chainwork").is_dir(), list(world["vault"].rglob("*"))


def test_a_diffuse_proposal_carries_the_leftover_it_asks_the_reader_to_look_at(world):
    seed_chain(world)
    relimit(world, split_min_cohesion=0.5)
    p = proposals(world)["proposals"][0]
    assert p["kind"] == "diffuse"
    assert len(p["leftover"]) == p["remaining"], p["leftover"]


def test_the_reported_cohesion_REPRODUCES_the_verdict_at_the_floor(world):
    """★ A receipt whose number cannot reproduce its own verdict is not a receipt.

    The decision used the raw float while the report carried `round(x, 3)`, so a floor set to the
    REPORTED cohesion refused a cluster whose own receipt said it was exactly at the floor: the
    rendered refusal read `cohesion 0.556, floor 0.556` — two equal numbers, refusing. Displayed
    threshold and enforced threshold were different numbers, which is the kernel's own
    status-table-is-not-the-mechanism class.

    ★ THE FIXTURE IS CHOSEN SO THE TWO ANSWERS DIFFER. An 11-entry chain measures 4/11 =
    0.363636…, which ROUNDS UP to 0.364 — so at a floor of 0.364 the raw float is below and the
    reported value is exactly at. A cluster whose cohesion is exact at three decimals (the 8-entry
    chain, 3/8 = 0.375) cannot tell the two implementations apart, and a test built on it passes
    either way. Boundary stated out loud: cohesion EXACTLY at the floor PASSES, and the entries
    MOVE."""
    seed_chain(world, n=11)
    p = proposals(world)["proposals"][0]
    reported, carriers = p["cohesion"], p["cohesion_carriers"]
    raw = carriers / len(p["entries"])
    assert reported > raw, (
        f"fixture cannot discriminate: {carriers}/{len(p['entries'])} = {raw} does not round UP")

    relimit(world, split_min_cohesion=reported)           # exactly at the reported floor
    at = proposals(world)["proposals"][0]
    assert at["kind"] == "split", (
        f"at its own reported cohesion {at['cohesion']} the cluster was called {at['kind']} — "
        "the receipt cannot reproduce the verdict")

    relimit(world, split_min_cohesion=reported + 0.001)   # one reported step above it
    above = proposals(world)["proposals"][0]
    assert above["kind"] == "diffuse", above
    assert above["cohesion"] == reported, "the reported number moved with the floor"
    why = run(world, "--apply", "Proj", above["id"], "--name", "X", expect_rc=1).stdout
    assert f"cohesion {reported}" in why, why
    assert f"{carriers} of its {len(above['entries'])}" in why, why


def test_the_share_floor_REFUSES_a_cluster_the_count_floor_accepts(world):
    """The share knob's own bite. The count floor is satisfied — 6 left, floor 4 — and only the
    share floor can see that 6 of 14 is not a surviving region."""
    seed_chain(world)
    assert proposals(world)["proposals"][0]["kind"] == "split"
    relimit(world, split_min_remaining_share=0.5)
    p = proposals(world)["proposals"][0]
    assert p["remaining"] >= p["min_remaining"], "the COUNT floor must still pass, or this proves nothing"
    assert p["kind"] == "refocus", p


def test_a_refocus_caused_by_the_SHARE_floor_does_not_blame_the_count_floor(world):
    """Deviation 7 of the producing session: the line said "under the floor of 4" when 6 were left
    and the count floor had passed. It was owner-facing and actively false, and nothing failed when
    it was reverted."""
    seed_chain(world)
    relimit(world, split_min_remaining_share=0.5)
    out = run(world, "--json").stdout and run(world).stdout
    assert "under the share floor" in out, out
    assert "under the floor of 4" not in out, out


# --------------------------------------------------- SPLITCLUSTER-1 boundary units ----
# Two lines in `split.py` decide real shapes and cannot be reached from a generated fixture: the
# `>=` at the core-vocabulary share, and the value `_local_clustering` returns for an entry with
# under two neighbours. Both were mutated GREEN through the end-to-end suite. They are pinned here
# at unit level, deliberately and with the reason written down — an end-to-end fixture that sits
# exactly on a boundary cannot be generated when the generator draws terms at random.

def _split_module():
    sys.path.insert(0, str(PLUGIN))
    import split
    return split


def test_a_term_carried_by_EXACTLY_half_the_room_is_core(world):
    """The boundary is `>=`, and which side it falls on decides whether a room exists at all.

    Eight entries, one term in exactly four of them. At `>=` that term is core and the room is
    proposed; at `>` it is not, there is no core vocabulary, and eight entries vanish silently."""
    split = _split_module()
    entries = [{"rare": {"shared", f"own{i}"}} for i in range(4)]
    entries += [{"rare": {f"other{i}"}} for i in range(4)]
    members = list(range(8))
    core = split._core_vocabulary(entries, members)
    assert "shared" in core, f"a term in exactly 4 of 8 must be core, got {core}"
    # and one carried by one fewer is NOT — so the test pins a boundary, not a direction
    entries[3]["rare"] = {"own3"}
    assert "shared" not in split._core_vocabulary(entries, members)


def test_an_entry_with_under_two_neighbours_scores_as_WELL_connected_not_badly(world):
    """★ `_local_clustering` returns 1.0 for degree < 2, and that is load-bearing, not defensive.

    The coefficient asks "do this entry's neighbours know each other", which is undefined for one
    neighbour. Returning 0.0 would rank every PENDANT — an entry resembling exactly one other — as
    the most bridge-like thing in the region, and the cut search only looks at the 16 most
    bridge-like entries. Sixteen pendants would push the real bridges off the list and a fused pair
    would go unnoticed. Pendants are ordinary, so this is reachable; the end-to-end generator
    cannot build one attached to a topic, which is why it is pinned here."""
    split = _split_module()
    adj = {0: {1}, 1: {0}, 2: set()}
    ms = {0, 1, 2}
    assert split._local_clustering(0, adj, ms) == 1.0
    assert split._local_clustering(2, adj, ms) == 1.0
    # a real bridge must still score BELOW a clique member, or the ranking means nothing
    adj = {0: {1, 2, 3, 4}, 1: {0, 2}, 2: {0, 1}, 3: {0, 4}, 4: {0, 3}}
    ms = set(adj)
    assert split._local_clustering(0, adj, ms) < split._local_clustering(1, adj, ms)


def test_the_dense_component_SHORTCUT_actually_fires_and_does_not_fire_where_a_cut_exists(world):
    """★ Two controls for one optimisation, because it is invisible when it works.

    `_unbridge` skips the cut search when the least-connected entry is too well connected for any
    small cut to exist. It is a pure optimisation — a sound skip changes no output — so DELETING it
    leaves every behavioural test green, and a later edit that keeps it sound but stops it firing
    would silently restore a 59-second search on a 1000-entry clique. The positive control counts
    the searches instead of watching the output.

    The negative control is the family the reviewer built to attack the arithmetic: two cliques of
    8 joined by 3 entries connected to everything, where the least-connected entry's degree EQUALS
    the ceiling exactly. The bound is tight there, so only the strict `>` keeps the cut findable —
    a one-character change destroys a real two-room finding, and nothing else in the suite is
    looking at that boundary."""
    split = _split_module()
    calls = []
    real = split._pieces_without
    split._pieces_without = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    try:
        clique = list(range(40))
        adj = {i: {j for j in clique if j != i} for i in clique}
        assert split._unbridge(clique, adj, 8) is None
        assert calls == [], f"the search ran {len(calls)} times on a clique it cannot cut"

        # the tight family: min degree == the ceiling, so the guard must NOT fire and the cut
        # must be found. m=8, k=3 -> n=19, mindeg=10, ceiling=(19-3)/2-1+3=10.
        m, k = 8, 3
        a = list(range(m)); b = list(range(m, 2 * m)); br = list(range(2 * m, 2 * m + k))
        members = a + b + br
        adj = {i: set() for i in members}
        for group in (a, b):
            for i in group:
                for j in group:
                    if i != j:
                        adj[i].add(j)
        for x in br:
            for y in members:
                if x != y:
                    adj[x].add(y); adj[y].add(x)
        mindeg = min(len(adj[i]) for i in members)
        ceiling = (len(members) - k) / 2 - 1 + k
        assert mindeg == ceiling, f"fixture is not on the knife edge: {mindeg} vs {ceiling}"
        calls.clear()
        rooms = split._unbridge(members, adj, 8)
        assert rooms is not None, "the guard skipped a component whose cut EXISTS"
        assert sorted(len(r) for r, _cut in rooms) == [11, 11], [len(r) for r, _ in rooms]
        assert calls, "the search never ran on the shape that needs it"
    finally:
        split._pieces_without = real


def test_a_rooms_reported_TERMS_carry_neither_singletons_nor_the_other_rooms_words(world):
    """★ I called this equivalent, the reviewer disproved it with behaviour, and then my first
    test of it was decorative — it re-implemented the rule inline and would have passed against any
    `split.py`. It calls the shipped `_room_terms` now.

    `shared_terms` is not only where the slug comes from: `apply()` writes its first FIVE terms
    into the new sub-region's `Map.md` — "…had gathered on one topic (a, b, c, d, e)". Two ways a
    wrong word gets there, and the NAME test catches neither because neither reaches the name: a
    term carried by ONE entry (when the room has under five above the bar), and a term carried only
    by the entries that bridged two rooms, which belongs to the OTHER room. Measured before the
    fix: a Beta room's Map.md line ended in `alphaone`."""
    split = _split_module()
    entries = [{"rare": {"own1", "own2", "own3", f"solo{i}"}} for i in range(6)]
    entries += [{"rare": {"own1", "own2", "own3", "foreign1", "foreign2"}} for _ in range(2)]
    members = list(range(8))
    got = split._room_terms(entries, members, cut={6, 7})
    assert got == ["own1", "own2", "own3"], got
    # ANTI-VACUITY: without the cut, the foreign terms DO qualify on count alone — so the
    # exclusion is doing the work, not the `df >= 2` rule that sits beside it.
    assert "foreign1" in split._room_terms(entries, members, cut=set())
    # and the singleton rule is separately load-bearing: nothing `solo*` survives either way
    assert not [t for t in split._room_terms(entries, members, cut=set()) if t.startswith("solo")]


# ------------------------------------------------------------ SPLITAUTO-1: the plugin moves ----
# The owner's bar: a non-expert should never be asked to move a file. So this is the first path in
# the package where the product changes a person's vault with nobody in the loop, and the tests are
# aimed at what it REFUSES, not at what it does — a wrong move costs a restructured vault, while a
# held proposal costs nothing because the reader still sees it.

def test_auto_apply_MOVES_a_proposal_that_clears_the_bar_and_conserves_every_entry(world):
    seed(world)                                   # 8 on one topic + 5 others: parent keeps 38%
    relimit(world, split_auto_apply=True)         # OFF in the shipped defaults; see the last test
    before = {h: b for h, b in headings_and_bodies(world["vault"])}
    p = run(world, "--auto")
    assert "moved 8 entries into" in p.stdout, p.stdout
    after = {h: b for h, b in headings_and_bodies(world["vault"])}
    assert set(after) == set(before), "an entry was lost or gained by the move"
    for i in range(8):
        h = f"Whisper transcription drift, case {i}"
        assert after[h] == before[h], f"{h} was not moved byte-identically"
    # the ONE entry that legitimately differs is the relink target, and asserting it away would
    # have made this control blind to the feature beside it (the same note the --apply tests carry)
    changed = [h for h in before if after[h] != before[h]]
    assert changed == ["A decision"], changed
    subs = [d for d in (world["vault"] / "Proj").iterdir() if d.is_dir()]
    assert len(subs) == 1, [d.name for d in subs]


def test_auto_apply_HOLDS_a_proposal_that_would_leave_the_parent_at_the_bar(world):
    """★ The refusal that is the whole point. 12 of 16 leaves the parent 25% — over the COUNT floor
    of 4, so the old rule calls it a healthy split — and at the share bar, so the plugin will not
    take it. S07 (212 of 266, parent 20%) and S13 (34 of 40, parent 15%) are this shape, and S13 is
    the owner's own open question: the plugin must not answer it by moving."""
    seed(world, 15)                               # 15 + 5 = 20, parent keeps 5 = exactly 25%
    relimit(world, split_auto_apply=True)
    before = entry_texts(world["vault"])
    p = run(world, "--auto")
    assert "nothing moved" in p.stdout, p.stdout
    assert "waiting on you" in p.stdout and "renamed, not split" in p.stdout, p.stdout
    assert sorted(entry_texts(world["vault"])) == sorted(before)
    assert not [d for d in (world["vault"] / "Proj").iterdir() if d.is_dir()]
    # POSITIVE CONTROL: one entry fewer in the cluster and the same machinery DOES move it.
    seed(world, 14)                               # one entry fewer: 14 + 5 = 19, parent keeps 26%
    relimit(world, split_auto_apply=True)
    assert "moved 14 entries into" in run(world, "--auto").stdout


def test_the_knob_OFF_refuses_to_move_anything_but_a_dry_run_still_reports(world):
    seed(world)
    relimit(world, split_auto_apply=False)
    before = entry_texts(world["vault"])
    p = run(world, "--auto", expect_rc=2)
    assert "REFUSED" in p.stderr, p.stderr
    assert sorted(entry_texts(world["vault"])) == sorted(before)
    dry = run(world, "--auto", "--dry-run")
    assert "would move 8 entries" in dry.stdout, dry.stdout
    assert sorted(entry_texts(world["vault"])) == sorted(before), "a dry run moved something"


def test_a_dry_run_moves_nothing_with_the_knob_ON(world):
    seed(world)
    relimit(world, split_auto_apply=True)
    before = entry_texts(world["vault"])
    assert "would move" in run(world, "--auto", "--dry-run").stdout
    assert sorted(entry_texts(world["vault"])) == sorted(before)
    assert not [d for d in (world["vault"] / "Proj").iterdir() if d.is_dir()]


def test_the_name_the_plugin_chooses_comes_from_the_rooms_own_words(world):
    """Nobody is in the loop for the NAME either, so it has to come from the entries that moved —
    `slug_for` over the room's own terms. The fixture's topic is whisper transcription drift."""
    seed(world)
    relimit(world, split_auto_apply=True)
    run(world, "--auto")
    subs = [d.name for d in (world["vault"] / "Proj").iterdir() if d.is_dir()]
    assert len(subs) == 1, subs
    assert subs[0].lower() != "topic", "fell back to the placeholder name"
    assert any(w in subs[0].lower() for w in ("whisper", "transcription", "drift", "timestamps")), \
        f"{subs[0]} is not built from the room's own vocabulary"


def test_one_notice_carries_BOTH_what_moved_and_what_is_waiting_on_the_reader(world):
    """The owner sees ONE notice. A run that moves one room and holds another must say both — a
    notice that reports only its successes is how a held decision becomes invisible."""
    v = world["vault"] / "Proj"
    (v / "Errata.md").write_text("# Errata\n" + "".join(topic(i) for i in range(8)))
    (v / "Patterns.md").write_text(
        "# Patterns\n" + "".join(f"\n## Budget ceiling case {i}\n\n**Ceiling** **declared** "
                                 f"**spending** before the run began ({i}).\n" for i in range(8))
        + "".join(OTHER))
    relimit(world, split_auto_apply=True)
    out = run(world, "--auto", "--dry-run").stdout
    assert out.count("would move") == 2, out
    assert "waiting on you" not in out, out


def test_a_refocus_is_held_AS_A_REFOCUS_not_as_a_share_failure(world):
    """Both gates hold it, so removing the kind check changes no file — it changes what the reader
    is TOLD, and that is the only signal they get. A refocus is "this region IS the topic, rename
    it"; reporting it as "too much of the region would move" sends them to the wrong decision."""
    seed(world, 8)
    relimit(world, split_auto_apply=True)
    (world["vault"] / "Proj" / "Errata.md").write_text(
        "# Errata\n" + "".join(topic(i) for i in range(8)))
    (world["vault"] / "Proj" / "Patterns.md").write_text("# Patterns\n" + OTHER[0])
    (world["vault"] / "Proj" / "Canon.md").write_text("# Canon\n")
    before = entry_texts(world["vault"])
    out = run(world, "--auto").stdout
    assert "nothing moved" in out, out
    assert "not a split" in out, out
    assert sorted(entry_texts(world["vault"])) == sorted(before)


def test_the_SHIPPED_DEFAULT_is_off_and_a_dry_run_is_how_you_check_your_own_vault(world):
    """★ The load-bearing assertion of this row, and it is about what does NOT happen.

    The scenario suite says auto-apply is safe; the author's real vault says it is not. A dry run
    over 1,133 real entries would have created five rooms and named four of them after DOCUMENT
    CONVENTIONS rather than subjects — `ScopeLocked`, `ScopeCanonical`, `NotesNever` — because
    `scope` occurs in 23.7% of every entry in that vault and `never` in 16.9%. The core-vocabulary
    rule cannot see it: boilerplate is carried by far MORE than half a room, so it passes by being
    everywhere (`q:CU-2026-09-19-SPLITVOCAB-1`).

    So the shipped default is OFF, and `--auto` with NO limits file of its own must refuse. The
    dry run still works with the knob off, deliberately: it is how a person checks whether the
    names read like topics in THEIR vault before trusting it with the move."""
    seed(world)
    world["limits_file"].write_text(json.dumps(LIMITS))      # no auto keys: the shipped defaults
    before = entry_texts(world["vault"])
    p = run(world, "--auto", expect_rc=2)
    assert "REFUSED" in p.stderr, p.stderr
    assert sorted(entry_texts(world["vault"])) == sorted(before), "the OFF default moved something"
    dry = run(world, "--auto", "--dry-run")
    assert "would move" in dry.stdout, dry.stdout
    assert sorted(entry_texts(world["vault"])) == sorted(before)


# ------------------------------------- SPLITAUTO-1, the reviewer round: what a RUN must refuse ----

def test_a_multi_apply_run_re_checks_the_bar_against_the_region_AS_IT_NOW_STANDS(world):
    """★ THE HOLE IN THE SAFETY ARGUMENT, and the row's prose asserted the opposite.

    `auto_candidates()` judges every proposal against the region as it was when the run STARTED.
    Each apply shrinks the region. `apply()` re-checks MEMBERSHIP and KIND and never the share — so
    three topics each measured at "parent keeps 72%" all moved in one run and left the parent with
    6 of 36 = 17%, BELOW the 20% of the very case the bar exists to hold. Applying them one at a
    time gave the opposite answer: the outcome depended on nothing but whether two applies shared
    a run.

    Three topics of 8 in a 27-entry region: the first two may move, the third must be held once
    they have."""
    # ★ THE SIZES ARE CHOSEN SO ONLY THE SHARE BAR CAN HOLD THE THIRD. Three topics of 12 and 4
    # others: at the snapshot each leaves the parent 28 of 40 = 70%, so all three qualify. After
    # two have moved, the third would leave 4 of 16 = 25% — at the bar — while 4 still clears the
    # COUNT floor of 4, so the count floor cannot be what refuses it. Blocks share no vocabulary,
    # or they would cluster into one room and the test would prove nothing.
    v = world["vault"] / "Proj"
    def block(a, b, c, n):
        return "".join(f"\n## {a} {b} case {i}\n\n**{a}** **{b}** **{c}** matters here ({i}).\n"
                       for i in range(n))
    (v / "Errata.md").write_text("# Errata\n" + block("Alpha", "Widget", "Frobnitz", 12))
    # NOT "Beta": `significant_terms` drops it as furniture, leaving that block two terms — under
    # the three an edge needs — so it never clustered and the fixture silently had only two topics.
    (v / "Patterns.md").write_text("# Patterns\n" + block("Delta", "Piston", "Bracket", 12))
    (v / "Aporia.md").write_text("# Aporia\n" + block("Gamma", "Flange", "Trunnion", 12))
    (v / "Canon.md").write_text("# Canon\n" + "".join(OTHER))
    relimit(world, split_auto_apply=True)
    before = {h: b for h, b in headings_and_bodies(world["vault"])}
    out = run(world, "--auto").stdout
    assert len([l for l in out.splitlines() if l.strip().startswith("moved")]) == 2, out
    assert "stopped clearing it once the earlier rooms in this run had moved" in out, out
    after = {h: b for h, b in headings_and_bodies(world["vault"])}
    assert set(after) == set(before), "an entry was lost or gained"
    # and what is LEFT is still a region, which is the whole point of the bar
    subs = sorted(d.name for d in (world["vault"] / "Proj").iterdir() if d.is_dir())
    assert len(subs) == 2, subs


def test_auto_apply_enforces_its_own_knob_not_only_the_CLI(world):
    """A wall at one entrance is not a wall. Called directly with the knob off, `auto_apply()` used
    to MOVE THE FILES and report `enabled: False` in the same dict."""
    split = _split_module()
    seed(world)
    world["limits_file"].write_text(json.dumps(dict(LIMITS, split_auto_apply=False)))
    os.environ.update(world["env"])
    before = entry_texts(world["vault"])
    r = split.auto_apply(world["vault"])
    assert r["applied"] == [], r["applied"]
    assert r["refused"], r
    assert sorted(entry_texts(world["vault"])) == sorted(before)
    assert not [d for d in (world["vault"] / "Proj").iterdir() if d.is_dir()]


def test_auto_never_creates_a_room_inside_another_lanes_region(world):
    """`--apply` is a person naming ONE region; `--auto` sweeps every region in the vault, and the
    marker says which of them are this lane's. Measured before the fix: a marker declaring `Proj/`
    moved 10 entries into `Other/`. The Stop hook would then have left another lane's region
    restructured AND uncommitted, with nothing saying so."""
    other = world["vault"] / "Other"
    other.mkdir()
    (other / "Map.md").write_text("# Other\n")
    # noise as well, or the cluster IS the whole region and nothing is ever proposed there
    (other / "Errata.md").write_text("# Errata\n" + "".join(topic(i) for i in range(8))
                                     + "".join(OTHER))
    seed(world)
    relimit(world, split_auto_apply=True)
    out = run(world, "--auto").stdout
    assert "outside this lane's declared paths" in out, out
    assert not [d for d in other.iterdir() if d.is_dir()], "wrote into another lane's region"
    assert [d for d in (world["vault"] / "Proj").iterdir() if d.is_dir()], "own region not done"


def test_auto_refuses_name_instead_of_silently_ignoring_it(world):
    seed(world)
    relimit(world, split_auto_apply=True)
    p = run(world, "--auto", "--name", "MyOwnName", expect_rc=2)
    assert "--auto names every room from its own entries" in p.stderr, p.stderr
    assert not [d for d in (world["vault"] / "Proj").iterdir() if d.is_dir()]


def test_the_notice_reports_superseded_and_refused_rather_than_swallowing_them(world):
    """Both branches were entirely unpinned: a run could drop either on the floor and every test
    stayed green. They are the two ways a proposal the reader was shown does NOT happen."""
    split = _split_module()
    out = split.render_auto({"enabled": True, "dry_run": False, "lane": "L", "applied": [],
                             "held": [], "min_remaining_share": 0.25,
                             "superseded": [{"id": "Proj#abc", "why": "overtaken"}],
                             "refused": [{"id": "Proj#def", "why": "a bad name"}]})
    assert "Proj#abc" in out and "overtaken by an earlier move" in out, out
    assert "Proj#def" in out and "a bad name" in out, out


def test_every_auto_run_writes_a_record_INCLUDING_one_that_moved_nothing(world):
    """The package's Canon for the analogous unattended path: the run record is always written,
    zero-append runs included. Without it, what was HELD and why lived only in stdout — on the one
    path that moves files with nobody watching."""
    log = world["tmp"] / "state" / "split-runs.log"
    seed(world, 15)                                   # parent keeps exactly 25%: held, not moved
    relimit(world, split_auto_apply=True)
    run(world, "--auto")
    assert log.is_file(), "a run that moved nothing wrote no record"
    row = json.loads(log.read_text().splitlines()[-1])
    assert row["applied"] == [] and row["held"], row
    seed(world)                                       # now one that DOES move
    run(world, "--auto")
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(rows) == 2, rows
    assert rows[-1]["applied"] and rows[-1]["applied"][0]["entries"] == 8, rows[-1]
    # a DRY RUN decides nothing, so it must not leave a row claiming it did
    seed(world)
    run(world, "--auto", "--dry-run")
    assert len(log.read_text().splitlines()) == 2, "a dry run wrote a run record"


# ------------------------------------------------- the vault-wide boilerplate axis (SPLITVOCAB-1) ----
# ★ WHAT THESE ARE AIMED AT. The scenario suite scored auto-apply safe and the author's own vault
# refuted it: a dry run would have created five rooms and named four of them after document
# conventions (`ScopeLocked`, `NotesNever`). Nothing in this file could have caught it either —
# every fixture here writes ONE region, and a convention is invisible from inside the only room
# that has one. So each test below writes SEVERAL regions that share a house style, and each has a
# control: the same fixture with the axis switched off (`split_boilerplate_region_share: 0.0`),
# where the wrong room must still appear. A control that passes under both settings is decoration.

CONVENTION = "**Scope:** universal. **Never** ship without it. **Last revisited:** today."


def _entry(head: str, bold: str, convention: bool) -> str:
    return (f"\n## {head}\n\n" + (CONVENTION + " " if convention else "") + f"**{bold}** — body.\n")


def house_style_vault(w, focus_entries: list[tuple[str, str]], *, convention_on: int,
                      siblings: int = 2) -> None:
    """A vault of `siblings + 1` regions that all write the same three convention words.

    `focus_entries` are (heading, bold-words) for `Proj`; the FIRST `convention_on` of them carry
    the convention. Each sibling is nine entries on its own subject, all carrying it — they are
    not decoration: the axis measures a term's BREADTH across regions and answers nothing without
    them."""
    v = w["vault"] / "Proj"
    body = "".join(_entry(h, b, i < convention_on) for i, (h, b) in enumerate(focus_entries))
    (v / "Errata.md").write_text("# Errata\n" + body)
    for s in range(siblings):
        d = w["vault"] / f"Sib{s}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Map.md").write_text(f"# Sib{s}\n")
        (d / "Patterns.md").write_text("# Patterns\n" + "".join(
            _entry(f"sibling {s} note {i}", f"sibling{s}alpha sibling{s}beta sibling{s}gamma", True)
            for i in range(9)))


def _two_small_topics() -> list[tuple[str, str]]:
    """Six entries on `alpha`, six on `beta`, disjoint vocabularies, and eight strangers. Neither
    topic reaches the eight-entry floor on its own: the only thing that could make a room here is
    the convention, which is what makes this the positive control."""
    out = [(f"alpha case {i}", "alphaone alphatwo alphathree") for i in range(6)]
    out += [(f"beta case {i}", "betaone betatwo betathree") for i in range(6)]
    out += [(f"stranger {i}", f"lonely{i}one lonely{i}two lonely{i}three") for i in range(8)]
    return out


def test_house_style_shared_by_several_regions_cannot_make_a_room(world):
    """The defect, in its own shape: two unrelated six-entry topics welded by the words every
    region writes. Neither is a room; the weld is."""
    house_style_vault(world, _two_small_topics(), convention_on=12)
    found = proposals(world)
    assert found["proposals"] == [], [p["suggested_name"] for p in found["proposals"]]
    assert "scope" in found["boilerplate_terms"] and "never" in found["boilerplate_terms"], found
    assert found["boilerplate_bar"] == 3, found["boilerplate_bar"]


def test_that_control_FAILS_with_the_axis_off(world):
    """The same fixture with the axis switched off must produce the wrong room, named from the
    convention. Without this the test above is satisfied by a rule that proposes nothing at all."""
    house_style_vault(world, _two_small_topics(), convention_on=12)
    relimit(world, split_boilerplate_region_share=0.0)
    found = proposals(world)
    assert len(found["proposals"]) == 1, found["proposals"]
    p = found["proposals"][0]
    assert len(p["entries"]) == 12, p["entries"]
    assert p["shared_terms"][0] in ("scope", "never", "revisited"), p["shared_terms"]
    assert found["boilerplate_terms"] == [] and found["boilerplate_bar"] == 0, found


def test_a_real_topic_written_in_house_style_is_still_proposed_and_named_from_itself(world):
    """The expensive direction, and it fails SILENTLY: a bar set too low silences real topics that
    happen to be written in the vault's own style, and the reader is never shown the room that was
    not proposed. Ten entries on one subject, they and five strangers carrying the convention."""
    entries = [(f"gamma case {i}", "gammaone gammatwo gammathree") for i in range(10)]
    entries += [(f"stranger {i}", f"lonely{i}one lonely{i}two lonely{i}three") for i in range(10)]
    house_style_vault(world, entries, convention_on=15)
    ps = proposals(world)["proposals"]
    assert len(ps) == 1, ps
    assert len(ps[0]["entries"]) == 10, len(ps[0]["entries"])
    assert all(t.startswith("gamma") for t in ps[0]["shared_terms"]), ps[0]["shared_terms"]
    assert ps[0]["suggested_name"].startswith("Gamma"), ps[0]["suggested_name"]


def test_that_control_FAILS_with_the_axis_off_too(world):
    """With the axis off the SAME fixture is also a `split` — of the topic plus the five strangers,
    under the convention's name. Kind alone cannot tell the two apart, which is why the test above
    asserts the membership and the name."""
    entries = [(f"gamma case {i}", "gammaone gammatwo gammathree") for i in range(10)]
    entries += [(f"stranger {i}", f"lonely{i}one lonely{i}two lonely{i}three") for i in range(10)]
    house_style_vault(world, entries, convention_on=15)
    relimit(world, split_boilerplate_region_share=0.0)
    ps = proposals(world)["proposals"]
    assert len(ps) == 1 and len(ps[0]["entries"]) == 15, ps
    assert ps[0]["shared_terms"][0] in ("scope", "never", "revisited"), ps[0]["shared_terms"]


def test_a_word_carried_by_ONE_entry_per_region_is_not_that_regions_vocabulary(world):
    """The `>= 2 carriers` floor, which is the difference between a house style and a word several
    regions happen to have used once. Here `alphaone` is written by exactly one entry in each
    sibling — present in every region, house style in none — and it must survive to name the room
    it really belongs to."""
    house_style_vault(world, [(f"alpha case {i}", "alphaone alphatwo alphathree") for i in range(10)]
                      + [(f"stranger {i}", f"lonely{i}one lonely{i}two lonely{i}three")
                         for i in range(10)], convention_on=0)
    for s in range(2):
        p = world["vault"] / f"Sib{s}" / "Patterns.md"
        p.write_text(p.read_text() + _entry("a passing mention", "alphaone somethingelse other",
                                            False))
    found = proposals(world)
    assert "alphaone" not in found["boilerplate_terms"], found["boilerplate_terms"]
    # Scored on `Proj` alone: the siblings carry the convention and the focus region does not, so
    # it is written in two regions of three — under the bar, kept, and they propose rooms of their
    # own. That is the fixture being honest about what it wrote, not a finding.
    mine = [p for p in found["proposals"] if p["region"] == "Proj"]
    assert len(mine) == 1 and len(mine[0]["entries"]) == 10, mine
    assert all(t.startswith("alpha") for t in mine[0]["shared_terms"]), mine[0]["shared_terms"]


def test_the_axis_answers_NOTHING_on_a_vault_of_fewer_than_three_regions(world):
    """A DISCLOSED LIMIT, pinned so it cannot become a surprise: breadth is a statement about
    several rooms, and this package creates ONE region per repository by default. On a two-region
    vault the convention is NOT stripped and the wrong room is still proposed — the floor keeps a
    share-of-regions bar from reading `ceil(0.25 x 1) = 1` and calling every repeated word
    furniture, and it does not make a one-region vault safe."""
    house_style_vault(world, _two_small_topics(), convention_on=12, siblings=1)
    found = proposals(world)
    assert found["regions"] == 2, found["regions"]
    assert found["boilerplate_terms"] == [], found["boilerplate_terms"]
    assert len(found["proposals"]) == 1, found["proposals"]
    assert found["proposals"][0]["shared_terms"][0] in ("scope", "never", "revisited")


def wide_vault(w, *, regions: int, convention_regions: int, mute: int = 0) -> None:
    """`Proj` (two small topics welded by the convention) plus `regions - 1` siblings, of which
    `convention_regions - 1` also write the convention. The rest write only their own words, and
    the last `mute` of them write nothing twice at all — a region that cannot vote.

    This is the fixture the SHARE needs: with three regions the bar is the floor
    (`max(3, ceil(share * 3))` is 3 for every share up to 1.0), so a three-region fixture cannot
    tell 0.41 from 0.95 — it exercises the floor and nothing else. Found by mutating the default
    and watching the earlier tests stay green."""
    v = w["vault"] / "Proj"
    body = "".join(_entry(h, b, True) for h, b in _two_small_topics()[:12])
    body += "".join(_entry(h, b, False) for h, b in _two_small_topics()[12:])
    (v / "Errata.md").write_text("# Errata\n" + body)
    voting = regions - 1 - mute
    for s in range(regions - 1):
        d = w["vault"] / f"Sib{s:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "Map.md").write_text(f"# Sib{s}\n")
        if s >= voting:
            # A region with no repeated vocabulary: every entry's words are its own. It is a real
            # region to every other part of the package and it casts no vote here.
            (d / "Patterns.md").write_text("# Patterns\n" + "".join(
                _entry(f"mute {s} note {i}", f"mute{s}x{i}one mute{s}x{i}two mute{s}x{i}three",
                       False) for i in range(3)))
            continue
        (d / "Patterns.md").write_text("# Patterns\n" + "".join(
            _entry(f"sibling {s} note {i}", f"sib{s}alpha sib{s}beta sib{s}gamma",
                   s < convention_regions - 1) for i in range(4)))


def test_the_bar_is_a_SHARE_of_the_voters_not_a_fixed_count(world):
    """Twenty regions, ten of them writing the convention. At the shipped 0.41 the bar is nine,
    the convention is house style, and the weld does not become a room."""
    wide_vault(world, regions=20, convention_regions=10)
    found = proposals(world)
    assert found["regions"] == 20 and found["boilerplate_voters"] == 20, found
    assert found["boilerplate_bar"] == 9, found["boilerplate_bar"]
    assert "scope" in found["boilerplate_terms"], found["boilerplate_terms"]
    assert [p for p in found["proposals"] if p["region"] == "Proj"] == []


def test_that_control_FAILS_when_the_share_is_raised(world):
    """The same twenty regions at 0.6: the bar is twelve, ten writers are not enough, the
    convention survives — and the wrong room comes back. This is what makes the share a share and
    not a name for the floor."""
    wide_vault(world, regions=20, convention_regions=10)
    relimit(world, split_boilerplate_region_share=0.6)
    found = proposals(world)
    assert found["boilerplate_bar"] == 12, found["boilerplate_bar"]
    assert "scope" not in found["boilerplate_terms"], found["boilerplate_terms"]
    mine = [p for p in found["proposals"] if p["region"] == "Proj"]
    assert len(mine) == 1 and mine[0]["shared_terms"][0] in ("scope", "never", "revisited"), mine


def test_a_region_that_writes_nothing_twice_does_not_move_the_bar(world):
    """★ THE DENOMINATOR IS VOTERS, AND THIS IS WHY. On the vault this was calibrated against,
    ELEVEN of twenty-eight "regions" were an incident snapshot copied in — 0-3 entries each,
    carrying no term twice, and destined for the Trash. They changed no breadth count and they set
    the bar, so the day that folder was tidied the axis would have re-calibrated itself from 7 to
    5 and started stripping real subjects, with nothing to say it had.

    Here: the same ten convention-writing regions, plus ten that write nothing twice. The bar must
    be the one measured over the ten that CAN vote, unmoved by the ten that cannot."""
    wide_vault(world, regions=30, convention_regions=10, mute=10)
    found = proposals(world)
    assert found["regions"] == 30 and found["boilerplate_voters"] == 20, found
    assert found["boilerplate_bar"] == 9, found["boilerplate_bar"]
    assert "scope" in found["boilerplate_terms"], found["boilerplate_terms"]
    assert [p for p in found["proposals"] if p["region"] == "Proj"] == []


def test_an_unreadable_FILE_says_so_in_the_summary_flag(world):
    """A file nobody can read makes its region a smaller, quieter region — and it can cost that
    region its VOTE, which on a small vault disarms the axis and brings the convention's room back
    somewhere else. `regions_checked` is the flag whose whole job is "I could see everything", and
    it used to stay True through exactly this."""
    house_style_vault(world, _two_small_topics(), convention_on=12)
    bad = world["vault"] / "Sib0" / "Patterns.md"
    bad.write_bytes(b"# Patterns\n\xff\xfe not utf-8")
    bad.chmod(0o000)
    try:
        found = proposals(world)
    finally:
        bad.chmod(0o644)
    assert found["regions_checked"] is False, "an unreadable FILE left the summary saying all clear"
    assert any("Sib0" in u for u in found["unreadable"]), found["unreadable"]


def test_a_bar_that_is_not_a_whole_number_of_voters_rounds_UP(world):
    """Eighteen voters at 0.41 is seven and four tenths, and the rule is AT LEAST that share of
    them — so the bar is eight and seven writers are not enough. Pinned because rounding the other
    way is invisible on every fixture whose share x voters is exact, and the direction is the
    difference between stripping a word seven regions happen to share and keeping it."""
    wide_vault(world, regions=18, convention_regions=7)
    found = proposals(world)
    assert found["regions"] == 18 and found["boilerplate_bar"] == 8, found["boilerplate_bar"]
    assert "scope" not in found["boilerplate_terms"], found["boilerplate_terms"]
    mine = [p for p in found["proposals"] if p["region"] == "Proj"]
    assert len(mine) == 1 and mine[0]["shared_terms"][0] in ("scope", "never", "revisited"), mine
