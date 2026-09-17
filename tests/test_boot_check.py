"""Tests for the boot check — does what a session boots with still tell the truth?

Driven as a SUBPROCESS through the real hook, for the reason `test_cleanup.py` records: the vault a
module reads is resolved from the environment at import time, so an in-process test describes
whichever vault was live when the first import happened.

**The negative controls are about the same thing throughout: "nothing reported" must mean "I looked
and everything held", never "I did not look".** A checker that scans no files reports zero failures,
which is exactly what a healthy vault reports — so every quiet assertion here is paired with a
claim count proving the extractor reached the file.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"


def git(vault: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=True)
    return p.stdout


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Proj").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    (vault / "Proj" / "Map.md").write_text("# Proj\n")
    (vault / "Proj" / "Position.md").write_text("# Position\n")
    git(vault, "add", "-A")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")
    head = git(vault, "rev-parse", "--short", "HEAD").strip()
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "config.json"))
    return dict(vault=vault, repo=repo, state=state, env=env, tmp=tmp_path, head=head)


def boot_file(w, text: str) -> None:
    """The repo's own CLAUDE.md — a file every session in that repo boots with."""
    (w["repo"] / "CLAUDE.md").write_text(text, encoding="utf-8")


def run_stop(w) -> dict:
    payload = json.dumps({"session_id": "s1", "cwd": str(w["repo"])})
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "boot_check.py")],
                       input=payload, capture_output=True, text=True, env=w["env"], timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads((w["state"] / "boot_check.json").read_text(encoding="utf-8"))


def kinds(doc) -> list[str]:
    return sorted(f["kind"] for f in doc["failures"])


# ------------------------------------------------------------ the four kinds ----
def test_a_dead_path_a_dead_sha_a_dead_wikilink_and_a_dead_import_are_all_found(world):
    # NOT a home-shaped path, even a fabricated one: the publish check refuses those anywhere in
    # the tree, and it refused this file on its first commit. A fixture is part of what ships.
    boot_file(world, f"""# Project

See /no-such-root/missing/file.md for the plan.
Fixed in commit `deadbee` last week.
The decision is in [[NoSuchFileAnywhere]].
@/no-such-root/missing/imported.md
""")
    doc = run_stop(world)
    assert kinds(doc) == ["import", "path", "sha", "wikilink"], doc["failures"]
    for f in doc["failures"]:
        assert f["line"] and f["file"].endswith("CLAUDE.md") and f["detail"]


def test_the_same_four_claims_ALIVE_report_nothing(world):
    """And the claim COUNT proves the extractor saw them — without it, this passes just as well
    against a checker that scanned no files at all."""
    live_path = world["tmp"] / "real-file.md"
    live_path.write_text("hello\n")
    boot_file(world, f"""# Project

See {live_path} for the plan.
Fixed in commit `{world['head']}` last week.
The decision is in [[Position]].
@{live_path}
""")
    doc = run_stop(world)
    assert doc["failures"] == [] and doc["n_failures"] == 0
    assert doc["n_claims"] >= 4, f"the extractor found {doc['n_claims']} claims; it never looked"


def test_a_hex_run_with_no_letter_is_a_number_not_a_sha(world):
    """The false positive the vault's own checker learned from: `2026` and `1500000` are backticked
    numbers, not commits. Carried as a rule in the extractor rather than as a skip list — a skip
    list is where a real detector goes to die quietly."""
    boot_file(world, "Budget `20000` bytes, year `2026`, count `1234567`.\n")
    doc = run_stop(world)
    assert doc["failures"] == []
    # POSITIVE CONTROL: a hex token WITH a letter, equally dead, IS reported.
    boot_file(world, "Budget `20000` bytes, and commit `abc1234` fixed it.\n")
    assert kinds(run_stop(world)) == ["sha"]


# ------------------------------------------------- the population is the CHAIN ----
def test_a_dead_claim_OUTSIDE_the_boot_chain_is_not_checked(world):
    """This is a BOOT check. A stale line in a file nobody loads is a tidiness problem; the same
    line in every session's context shapes every answer for as long as it stands."""
    (world["vault"] / "Proj" / "Notes.md").write_text("[[NoSuchFileAnywhere]]\n")
    boot_file(world, "# Project\n\nNothing claimed here.\n")
    doc = run_stop(world)
    assert doc["failures"] == []
    # POSITIVE CONTROL: the identical claim inside the chain IS found, so the quiet above is about
    # the POPULATION and not about the detector being broken.
    boot_file(world, "# Project\n\n[[NoSuchFileAnywhere]]\n")
    assert kinds(run_stop(world)) == ["wikilink"]


def test_an_at_import_pulls_its_target_INTO_the_chain(world):
    imported = world["tmp"] / "imported.md"
    imported.write_text("A claim lives here: [[NoSuchFileAnywhere]]\n")
    boot_file(world, f"# Project\n\n@{imported}\n")
    doc = run_stop(world)
    assert kinds(doc) == ["wikilink"]
    assert doc["failures"][0]["file"] == str(imported), doc["failures"]
    assert doc["n_boot_files"] >= 2


# ------------------------------------------------------ unchecked is not passing ----
def test_a_vault_with_no_git_reports_its_SHA_claims_as_UNCHECKED(world):
    import shutil
    shutil.move(str(world["vault"] / ".git"), str(world["tmp"] / "git-moved-away"))
    boot_file(world, "Fixed in commit `deadbee`.\n")
    doc = run_stop(world)
    assert doc["n_unchecked"] == 1 and doc["n_failures"] == 0
    assert "UNCHECKED" in doc["unchecked"][0]["detail"]
    sys.path.insert(0, str(HOOKS))
    import importlib.util
    spec = importlib.util.spec_from_file_location("gd_boot_check", HOOKS / "boot_check.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    line = " ".join(mod.facts_lines(doc))
    assert "UNCHECKED, never as sound" in line


# ---------------------------------------------------------------- the seam ----
EXT_OK = '''
def run(vault):
    return [{"kind": "house-rule", "claim": "something", "file": "X.md", "detail": "broke"}]
'''
EXT_RAISES = '''
def run(vault):
    raise RuntimeError("the detector could not look")
'''


def with_extension(w, body: str) -> Path:
    mod = w["tmp"] / "ext.py"
    mod.write_text(body)
    (w["tmp"] / "config.json").write_text(json.dumps({"extra_checks": [str(mod)]}))
    return mod


def test_an_extensions_findings_reach_the_report_and_the_facts_line(world):
    with_extension(world, EXT_OK)
    doc = run_stop(world)
    assert doc["n_extra"] == 1 and doc["extra"][0]["kind"] == "house-rule"


def test_an_extension_that_RAISES_is_UNCHECKED_and_costs_nothing_else(world):
    """A detector that could not look must never read as a detector that found nothing — and it
    must not take the built-in checks down with it."""
    with_extension(world, EXT_RAISES)
    boot_file(world, "[[NoSuchFileAnywhere]]\n")
    doc = run_stop(world)
    assert doc["extra_unchecked"] and "RuntimeError" in doc["extra_unchecked"][0]
    assert kinds(doc) == ["wikilink"], "the built-in checks stopped when the extension raised"


def test_an_extension_returning_the_wrong_shape_is_UNCHECKED_not_silently_dropped(world):
    with_extension(world, "def run(vault):\n    return 'not a list'\n")
    doc = run_stop(world)
    assert doc["extra_unchecked"] and "TypeError" in doc["extra_unchecked"][0]


def test_a_missing_extension_module_is_UNCHECKED(world):
    (world["tmp"] / "config.json").write_text(
        json.dumps({"extra_checks": [str(world["tmp"] / "no-such-module.py")]}))
    doc = run_stop(world)
    assert doc["extra_unchecked"], doc


# ------------------------------------------------- the shipped extension ----
EXTRA = Path(__file__).resolve().parents[2] / "scripts" / "gedaechtnis_extra_checks.py"


@pytest.mark.skipif(not EXTRA.is_file(), reason="the fleet's extension is not in this tree")
def test_the_shipped_extension_finds_a_stale_umbrella_and_stays_quiet_on_a_fresh_one(world, tmp_path):
    """The seam's real caller. A seam with no caller outside its own tests is the failure class
    this arc has already paid for once."""
    sys.path.insert(0, str(EXTRA.parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location("fleet_extra", EXTRA)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    v = world["vault"]
    (v / "Umb" / "Child").mkdir(parents=True)
    (v / "Umb" / "Position.md").write_text("# umbrella\n")
    (v / "Umb" / "Child" / "Map.md").write_text("# child\n")
    (v / "Umb" / "Child" / "Position.md").write_text("# child state\n")
    git(v, "add", "-A")
    git(v, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "both together")
    assert mod.run(v) == [], "nothing has diverged yet"
    (v / "Umb" / "Child" / "Position.md").write_text("# child state, moved on\n")
    git(v, "add", "--", "Umb/Child/Position.md")
    git(v, "-c", "user.name=t", "-c", "user.email=t@t",
        "-c", "commit.gpgsign=false", "commit", "-q", "-m", "child moves",
        "--date", "now", "--", "Umb/Child/Position.md")
    out = mod.run(v)
    assert len(out) == 1 and out[0]["kind"] == "umbrella-divergence"
    assert "Umb/Child/Position.md" in out[0]["detail"]


@pytest.mark.skipif(not EXTRA.is_file(), reason="the fleet's extension is not in this tree")
def test_the_shipped_extension_RAISES_rather_than_reporting_nothing_when_git_fails(tmp_path):
    """An empty list from a detector that could not look is the exact failure the organ exists to
    end. The hook's contract makes raising the SAFE thing to do — it is reported as UNCHECKED."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fleet_extra2", EXTRA)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    v = tmp_path / "not-a-repo"
    (v / "Umb" / "Child").mkdir(parents=True)
    (v / "Umb" / "Position.md").write_text("# u\n")
    (v / "Umb" / "Child" / "Map.md").write_text("# c\n")
    with pytest.raises(Exception):
        mod.run(v)


# ------------------------------------------- the reviewer's three reproductions ----
def bc_module():
    sys.path.insert(0, str(HOOKS))
    import importlib.util
    spec = importlib.util.spec_from_file_location("gd_bc_probe", HOOKS / "boot_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_vault_that_cannot_be_INDEXED_makes_wikilinks_UNCHECKED_not_dead(tmp_path):
    """★ This organ's own defect class, found living inside the organ.

    `basename_index` swallowed an `os.walk` failure and returned an EMPTY index — and an empty
    index makes every wikilink in every boot file report as a confident failure. A state that could
    not be measured, rendering as a measurement."""
    mod = bc_module()
    idx, complete = mod.basename_index(tmp_path / "no-such-vault")
    assert idx == {} and complete is False
    why = mod.check_claim({"kind": "wikilink", "claim": "Position", "line": 1},
                          tmp_path, idx, [], complete)
    assert why and why.startswith("UNCHECKED"), why
    # POSITIVE CONTROL: with a real index, a genuinely absent target is still a real failure —
    # the fix must not have turned the detector off.
    (tmp_path / "Real.md").write_text("x\n")
    idx2, complete2 = mod.basename_index(tmp_path)
    assert complete2 and idx2.get("Real")
    assert mod.check_claim({"kind": "wikilink", "claim": "Missing", "line": 1},
                           tmp_path, idx2, [], complete2) == "no file of that name in the vault"


def test_a_URL_is_not_a_path_claim():
    """Reproduced by the row's reviewer: a URL's double slash leaves the second `/` preceded by the
    first, which the lookbehind did not exclude, so `http://example.com/a/b` was extracted as the
    path `/example.com/a/b` and reported missing from disk."""
    mod = bc_module()
    claims = mod.extract_claims("see http://example.com/a/b/c and https://x.org/y/z\n")
    assert [c for c in claims if c["kind"] == "path"] == [], claims
    # POSITIVE CONTROL: a real absolute path on the same line is still extracted.
    claims = mod.extract_claims("see http://example.com/a/b and /no-such-root/real/file.md\n")
    paths = [c["claim"] for c in claims if c["kind"] == "path"]
    assert paths == ["/no-such-root/real/file.md"], paths


def test_an_AMBIGUOUS_short_sha_is_UNCHECKED_not_a_dead_commit(tmp_path):
    """`git cat-file -t` fails identically for a commit that does not exist and for a prefix that
    matches several objects. Calling the second one dead is a false accusation about a commit that
    is right there."""
    mod = bc_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # A FOUR-character collision, brute-forced over cheap blobs. Git refuses a prefix shorter than
    # four as "not a valid object name" rather than as ambiguous, so a shorter fixture would test
    # the wrong branch — which is how the first version of this test failed.
    #
    # Why not seven, which is the shortest the extractor will even offer? Because a 7-hex collision
    # needs on the order of 10^5 objects to find, and building one would take longer than the whole
    # suite. That gap IS the reviewer's finding stated honestly: real ambiguity at seven characters
    # is rare today and gets likelier as a repository grows, and the branch that handles it is
    # proven here at the length where a fixture can reach it.
    prefixes = {}
    ambiguous = None
    for i in range(900):
        (repo / "f.txt").write_text(f"content {i}\n")
        sha = subprocess.run(["git", "-C", str(repo), "hash-object", "-w", "f.txt"],
                             capture_output=True, text=True, check=True).stdout.strip()
        if sha[:4] in prefixes and prefixes[sha[:4]] != sha:
            ambiguous = sha[:4]
            break
        prefixes[sha[:4]] = sha
    assert ambiguous, "the fixture never produced a colliding prefix; the test proves nothing"
    why = mod.check_claim({"kind": "sha", "claim": ambiguous, "line": 1}, repo, {}, [repo])
    assert why and why.startswith("UNCHECKED"), why
    # POSITIVE CONTROL: a token that is genuinely in no repo is still reported as a real failure.
    why2 = mod.check_claim({"kind": "sha", "claim": "deadbee", "line": 1}, repo, {}, [repo])
    assert why2 and not why2.startswith("UNCHECKED"), why2


def test_the_state_file_is_written_atomically(world):
    """Same class R3-FIX closed in `archive.py`: a truncate-then-kill. The stakes are far lower —
    the file is regenerated every session end — but a second copy of a bug this arc already fixed
    is not worth two saved lines."""
    src = (HOOKS / "boot_check.py").read_text(encoding="utf-8")
    assert "archive.atomic_write(state_path()" in src
    assert "state_path().write_text(" not in src
