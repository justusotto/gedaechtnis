"""Tests for the cleanup pass — the one organ in this package that CHANGES memory without asking.

Same world as `test_commit.py` and `test_maintenance.py`: a git repository in tmp_path pointed at by
GEDAECHTNIS_VAULT, the state directory and the limits file redirected, so the suite never touches a
real vault. The pass is driven as a SUBPROCESS through `cleanup.py`'s own entry point rather than by
importing its functions, because the vault it reads comes from the environment at import time and a
test that called `propose()` in-process would be describing a different vault than the one the walk
scans — which is exactly the shape of defect this file is meant to catch.

**Why the negative controls carry the weight here.** Every other organ in the package fails by
staying quiet. This one fails by ACTING: on a healthy vault, on an entry it should have left alone,
on the wrong copy of a pair. So each detector is checked at its boundary in both directions, and
two whole-vault controls sit underneath — nothing is written when nothing is wrong, and no byte
removed from a live file goes missing. The conservation check has its own positive control (the
naive delete-in-place path run over the same fixture), because a check that cannot fail is not a
check.
"""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, time
from collections import Counter
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
CLEANUP = PLUGIN / "cleanup.py"

LIMITS = {
    "role_soft_limits_lines": {"Position": 10, "Canon": 800},
    "stale_entry_days": 56,
    "compaction_floor_share": 0.4,
    "max_memory_file_bytes": 500000,
    "max_searchable_file_bytes": 2000000,
}


def days_ago(n: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))


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
    git(vault, "add", "-A")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: MY-PROJECT\npath: Proj/\npath: Cleanup/\n")
    limits_file = tmp_path / "limits.json"
    limits_file.write_text(json.dumps(LIMITS))
    env = dict(os.environ,
               GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_LIMITS=str(limits_file),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-such-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, env=env, tmp=tmp_path, limits_file=limits_file)


def run(w, *args, cwd=None) -> dict:
    """The product's own entry point. Returns the parsed receipt."""
    p = subprocess.run([sys.executable, "-B", str(CLEANUP), "--json", *args],
                       capture_output=True, text=True, env=w["env"], timeout=120,
                       cwd=str(cwd or w["repo"]))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def proposals(w, *args) -> list[dict]:
    """What the detectors found, without applying: the dry run's own list."""
    r = run(w, "--dry-run", *args)
    return r["would_change"] + r["reported"]


def snapshot(vault: Path) -> dict[str, str]:
    return {str(p.relative_to(vault)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(vault.rglob("*")) if p.is_file() and ".git/" not in str(p)}


DUP = "\n## A repeated entry\n\nThe same words, twice.\n"
ENTRY = "\n## Something else\n\nDifferent words.\n"


# ------------------------------------------------------------- duplicate ----
def test_an_exact_duplicate_entry_is_found_and_moved(world):
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + ENTRY + DUP)
    found = proposals(world)
    assert [p["kind"] for p in found] == ["duplicate"]
    r = run(world)
    assert len(r["moved"]) == 1
    # The FIRST copy survives, in place, and the file still parses as it did.
    text = f.read_text()
    assert text.count("## A repeated entry") == 1
    assert text.startswith("# Canon\n") and "## Something else" in text
    # And the removed copy exists, byte-identical, inside the bundle.
    copy = world["vault"] / r["bundle"] / r["moved"][0]["bundle_copy"]
    assert DUP.strip() in copy.read_text()


def test_two_entries_under_one_heading_with_DIFFERENT_bodies_are_not_duplicates(world):
    """The negative control that carries the most weight in this file.

    Superseding an entry in place under its original heading is how a vault records that a rule
    changed. A detector keyed on the heading alone would collapse the correction and the thing it
    corrected into one, keeping whichever came first — deleting the newer rule, silently."""
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n## A rule\n\nThe old rule.\n\n## A rule\n\nSUPERSEDED: the new rule.\n")
    before = f.read_text()
    r = run(world)
    assert r["moved"] == [] and r["folded"] == []
    assert f.read_text() == before


def test_the_duplicate_detector_is_not_vacuous_on_that_fixture(world):
    """The positive control FOR the negative control: the same fixture with the bodies made equal
    does fire. Without this, the test above passes just as well against a detector that is broken
    in every direction, or against a scan that never reached the file."""
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n## A rule\n\nThe old rule.\n\n## A rule\n\nThe old rule.\n")
    assert [p["kind"] for p in proposals(world)] == ["duplicate"]


# -------------------------------------------------------------- oversize ----
def long_position(n: int) -> str:
    return "# Position\n" + "".join(f"\n## Day {i}\n\nline\n" for i in range(n))


def test_a_file_over_its_role_line_limit_is_folded_into_its_archive(world):
    f = world["vault"] / "Proj" / "Position.md"
    f.write_text(long_position(20))                       # limit is 10 lines in the fixture
    assert [p["kind"] for p in proposals(world)] == ["oversize"]
    r = run(world)
    assert len(r["folded"]) == 1 and r["folded"][0]["entries"] > 0
    assert f.read_text().count("\n") + 1 <= LIMITS["role_soft_limits_lines"]["Position"]
    seg = world["vault"] / "Proj" / "Position-archive.md"
    assert seg.is_file() and "## Day 0" in seg.read_text()


def test_a_file_AT_its_limit_is_left_alone(world):
    f = world["vault"] / "Proj" / "Position.md"
    f.write_text("x\n" * 9 + "x")                          # exactly 10 lines
    before = f.read_text()
    assert run(world)["folded"] == []
    assert f.read_text() == before


def test_a_stem_with_no_line_limit_is_never_flagged_however_long(world):
    """The table's absence is its meaning. A stem not in it has no line limit — which is why a
    vault full of files nobody wrote a limit for is not reported as a hundred problems."""
    f = world["vault"] / "Proj" / "Apparatus.md"
    f.write_text("# Apparatus\n" + "line\n" * 5000)
    assert run(world)["folded"] == []
    assert f.read_text().count("line\n") == 5000


# ----------------------------------------------------------------- stale ----
def test_an_entry_past_its_review_horizon_is_REPORTED_and_not_moved(world):
    f = world["vault"] / "Proj" / "Aporia.md"
    body = f"# Aporia\n\n## An open question\n\n**Last revisited:** {days_ago(100)}\n\nWhy?\n"
    f.write_text(body)
    found = proposals(world)
    assert [p["kind"] for p in found] == ["stale"]
    assert found[0]["action"] == "report"
    r = run(world)
    assert len(r["reported"]) == 1 and r["moved"] == [] and r["folded"] == []
    assert f.read_text() == body                      # the whole point: it stays where it is


def test_a_recently_revisited_entry_is_quiet(world):
    f = world["vault"] / "Proj" / "Aporia.md"
    f.write_text(f"# Aporia\n\n## An open question\n\n**Last revisited:** {days_ago(3)}\n\nWhy?\n")
    assert run(world)["reported"] == []


def test_an_unreadable_date_is_UNCHECKED_never_fresh(world):
    f = world["vault"] / "Proj" / "Aporia.md"
    f.write_text("# Aporia\n\n## An open question\n\n**Last revisited:** soon\n\nWhy?\n")
    r = run(world)
    assert r["reported"] == []
    assert r["unparsable_dates"] == 1, "a date that could not be read must be COUNTED, not ignored"


# ------------------------------------------------- the three together ----
def test_the_fixture_from_the_row_brief_yields_exactly_three_proposals(world):
    """REPORT-COMPLETE-1 §4, row R2, verbatim: 'fixture (duplicate, oversize, stale Aporia) → 3
    proposals; clean → nothing'."""
    (world["vault"] / "Proj" / "Canon.md").write_text("# Canon\n" + DUP + DUP)
    (world["vault"] / "Proj" / "Position.md").write_text(long_position(20))
    (world["vault"] / "Proj" / "Aporia.md").write_text(
        f"# Aporia\n\n## Open\n\n**Last revisited:** {days_ago(100)}\n\nWhy?\n")
    found = proposals(world)
    assert sorted(p["kind"] for p in found) == ["duplicate", "oversize", "stale"]


def test_a_clean_vault_is_not_written_to_at_all(world):
    """Not merely 'no proposals'. A pass that rewrote every file it read with identical content
    would satisfy a proposal count and still churn the vault's git history every session."""
    (world["vault"] / "Proj" / "Canon.md").write_text("# Canon\n" + ENTRY)
    (world["vault"] / "Proj" / "Position.md").write_text("# Position\n\n## Now\n\nfine\n")
    before = snapshot(world["vault"])
    r = run(world)
    assert r["moved"] == [] and r["folded"] == [] and r["reported"] == []
    assert r["bundle"] is None
    assert snapshot(world["vault"]) == before, "a clean vault must come out byte-identical"
    assert not (world["vault"] / "Cleanup").exists()


# ------------------------------------------------------------ the rules ----
def entry_counts(text: str) -> Counter:
    """How many times each entry occurs — a COUNT, never a membership test.

    Membership is the wrong instrument here and the first version of this check used it: every
    entry a duplicate-removal touches has, by definition, an identical twin still in the file, so
    `entry in survivors` is true however many copies were destroyed. The check passed against a
    deliberately destructive implementation, which is how it was caught."""
    return Counter(("\n## " + e).strip() for e in text.split("\n## ")[1:])


def test_nothing_is_lost_every_removed_entry_survives_somewhere(world):
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + ENTRY + DUP)
    before = entry_counts(f.read_text())
    r = run(world)
    survivors = entry_counts(f.read_text())
    for m in r["moved"]:
        survivors += entry_counts((world["vault"] / r["bundle"] / m["bundle_copy"]).read_text())
    assert survivors == before, f"before {before}, after {survivors}"


def test_the_conservation_check_can_actually_fail(world):
    """The positive control for the test above. It reproduces what this module refuses to do —
    delete the duplicate in place — and asserts the same check goes red. A conservation assertion
    that passes against a destructive implementation is decoration, and this one was."""
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + ENTRY + DUP)
    before = entry_counts(f.read_text())
    head, *entries = f.read_text().split("\n## ")
    f.write_text(head + "".join("\n## " + e for e in entries[:-1]))   # the naive delete-in-place
    assert entry_counts(f.read_text()) != before, \
        "the check would not have noticed a deletion — it proves nothing"


def test_no_vault_file_disappears(world):
    (world["vault"] / "Proj" / "Canon.md").write_text("# Canon\n" + DUP + DUP)
    (world["vault"] / "Proj" / "Position.md").write_text(long_position(20))
    before = set(snapshot(world["vault"]))
    run(world)
    assert before <= set(snapshot(world["vault"]))


FORBIDDEN_CALLS = {"unlink", "rmtree", "remove", "removedirs", "rmdir", "truncate"}


def test_the_module_calls_nothing_that_deletes(world):
    """A rule the code keeps better than the prose can.

    Parsed, not grepped: the first version searched the SOURCE TEXT and failed on its own
    docstring, which promises there is no `unlink` in the module. A grep of a file that documents
    its own constraints matches the documentation — so this walks the AST and looks at what is
    actually CALLED, and at the mode a file is actually opened in."""
    import ast
    tree = ast.parse(CLEANUP.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else "")
        assert name not in FORBIDDEN_CALLS, f"{name}() at line {node.lineno}"
        if name == "open":
            modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
            modes += [k.value.value for k in node.keywords
                      if k.arg == "mode" and isinstance(k.value, ast.Constant)]
            for m in modes:
                assert "w" not in m and "x" not in m, f"open(..., {m!r}) at line {node.lineno}"


def test_that_rule_would_notice_a_deletion(world):
    """The positive control: the same walk over a module that DOES delete goes red. Without it,
    the test above passes on a tree it failed to parse as well as on a clean one."""
    import ast
    bad = ast.parse("from pathlib import Path\ndef f(p):\n    Path(p).unlink()\n")
    hits = [n for n in ast.walk(bad)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in FORBIDDEN_CALLS]
    assert hits


def test_running_twice_changes_nothing_the_second_time(world):
    (world["vault"] / "Proj" / "Canon.md").write_text("# Canon\n" + DUP + DUP)
    (world["vault"] / "Proj" / "Position.md").write_text(long_position(20))
    run(world)
    mid = snapshot(world["vault"])
    r2 = run(world)
    assert r2["moved"] == [] and r2["folded"] == []
    after = snapshot(world["vault"])
    changed = {k for k in mid if after.get(k) != mid[k]}
    assert not changed, changed


INTERLEAVE = """
import json, sys
sys.path.insert(0, {plugin!r})
import cleanup
from pathlib import Path
f = Path(sys.argv[1])
found = cleanup.propose()
f.write_text(sys.argv[2], encoding="utf-8")        # somebody else edits between scan and apply
print(json.dumps({{"found": [p["kind"] for p in found["proposals"]],
                   "receipt": cleanup.apply(found)}}))
"""


def test_a_file_changed_between_the_scan_and_the_apply_is_left_untouched(world, tmp_path):
    """The one destructive mistake this module could make: removing entry number 3 after somebody
    else moved a different entry into that position. The apply re-reads and compares.

    The interleaving runs in its OWN process. An earlier version did it in the test process with
    `os.environ.update` and an `importlib.reload`, which passed alone and failed in the suite: the
    env it injected outlived the test and the limits module's cache did too, so every later test in
    that process read a different vault. A test that changes the world for its neighbours is a
    harness bug however green it is on its own."""
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + DUP)
    script = tmp_path / "interleave.py"
    script.write_text(INTERLEAVE.format(plugin=str(PLUGIN)), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(script), str(f),
                        "# Canon\n" + ENTRY + "\n## Innocent\n\nnot a duplicate\n"],
                       capture_output=True, text=True, env=world["env"],
                       cwd=str(world["repo"]), timeout=120)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["found"] == ["duplicate"], "the scan never reached the file; the test proves nothing"
    receipt = out["receipt"]
    assert receipt["moved"] == []
    assert any("changed since the scan" in x for x in receipt["failed"])
    assert "Innocent" in f.read_text()


# --------------------------------------------------------------- receipt ----
def test_the_bundle_readme_says_where_everything_went(world):
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + DUP)
    r = run(world)
    readme = (world["vault"] / r["bundle"] / "README-what-went-where.html").read_bytes()
    head = readme[:1024].decode("utf-8", "replace")
    assert head.startswith("<!doctype html>\n<meta charset=\"utf-8\">"), \
        "a file:// page declares its encoding in its first bytes or it is read as latin-1"
    page = readme.decode("utf-8")
    assert "A repeated entry" in page                      # what moved
    assert "Proj/Canon.md" in page                         # where from, and the surviving copy
    assert "Trash" in page                                 # whose job deleting the bundle is


def test_the_receipt_asks_no_question(world):
    """The ruling is that the pass never asks. A receipt carrying a question mark in its prose is
    the first place that would come back."""
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + DUP)
    p = subprocess.run([sys.executable, "-B", str(CLEANUP)], capture_output=True, text=True,
                       env=world["env"], cwd=str(world["repo"]), timeout=120)
    assert p.returncode == 0, p.stderr
    assert "?" not in p.stdout, p.stdout
    assert "applied" in p.stdout and "Nothing was deleted" in p.stdout


def test_a_clean_vault_says_so_in_one_line(world):
    p = subprocess.run([sys.executable, "-B", str(CLEANUP)], capture_output=True, text=True,
                       env=world["env"], cwd=str(world["repo"]), timeout=120)
    assert "nothing to do" in p.stdout and "?" not in p.stdout


# ----------------------------------------------------------------- git ----
def test_what_the_pass_wrote_is_COMMITTED(world):
    """Otherwise every cleanup leaves the vault permanently dirty — a modified live file and an
    untracked bundle — and dirt of that kind makes other machinery defer rather than announce
    itself. Same reasoning as the compaction commit in `maintenance.py`."""
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + DUP)
    git(world["vault"], "add", "--", "Proj/Canon.md")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
        "-m", "seed", "--", "Proj/Canon.md")
    run(world)
    dirty = git(world["vault"], "status", "--porcelain")
    assert dirty.strip() == "", dirty
    subject = git(world["vault"], "log", "-1", "--format=%s")
    assert subject.startswith("Cleanup (MY-PROJECT):"), subject


def test_no_marker_means_no_commit_and_the_cleanup_still_happens(world):
    """The negative control on the commit half. An unknown lane has no partition, so nothing is
    staged — but the user's cleanup is not cancelled for it, and the receipt is still written."""
    (world["repo"] / ".atlas-lane").unlink()
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + DUP)
    r = run(world)
    assert len(r["moved"]) == 1
    assert f.read_text().count("## A repeated entry") == 1
    assert git(world["vault"], "status", "--porcelain").strip() != ""


# ---------------------------------------------------------- the bundle ----
SEARCH_BUNDLE = """
import sys
sys.path.insert(0, {plugin!r})
import recall
from pathlib import Path
v = Path(sys.argv[1])
for flag in (False, True):                      # default, and "search everything"
    hits = [str(p) for p in recall.md_files(v, include_queues=flag)]
    print(flag, any("Cleanup" in h for h in hits), len(hits))
"""


def test_the_bundle_is_not_searchable_memory_EVEN_WITH_include_queues(world, tmp_path):
    """A receipt the search reads hands back the duplicate the pass just removed, with nothing to
    say which copy is live.

    The `include_queues` half is the finding, not the flourish. `Cleanup` first went into
    `NON_MEMORY_DIRS` beside `Pharos` and `Channels` — which are excluded BY DEFAULT and come back
    with `--include-queues`. That is right for a work queue, which a user may deliberately want to
    search, and never right here: "search everything" must not resurrect content the user's own
    cleanup removed. It has its own unconditional set now."""
    f = world["vault"] / "Proj" / "Canon.md"
    f.write_text("# Canon\n" + DUP + DUP)
    run(world)
    assert (world["vault"] / "Cleanup").is_dir(), "the bundle was never written; the test is vacuous"
    script = tmp_path / "search.py"
    script.write_text(SEARCH_BUNDLE.format(plugin=str(PLUGIN)), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(script), str(world["vault"])],
                       capture_output=True, text=True, env=world["env"], timeout=120)
    assert p.returncode == 0, p.stderr
    for line in p.stdout.strip().splitlines():
        flag, saw_bundle, n_hits = line.split()
        assert saw_bundle == "False", f"the bundle was searched with include_queues={flag}"
        assert int(n_hits) > 0, "no file was searched at all; the assertion above proves nothing"


# ----------------------------------------------------- the guard on the guard ----
def guard():
    """`conftest.py`'s two functions, loaded BY PATH.

    `import conftest` works when this suite runs alone and raises when it runs beside another
    suite that has a `conftest.py` of its own — which the enclosing project's does: the bare
    module name is ambiguous and the import goes to whichever was found first. Caught by running
    both suites in ONE pytest invocation, which neither suite's own green run exercises."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gedaechtnis_tests_conftest", Path(__file__).resolve().parent / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_real_vault_guard_would_notice_a_write(tmp_path):
    """The positive control for `conftest.py`'s session fixture.

    A guard whose comparison cannot change is a guard that passes forever."""
    fingerprint, changed = guard().fingerprint, guard().changed
    v = tmp_path / "vault"
    v.mkdir()
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    (v / "Map.md").write_text("# x\n")
    git(v, "add", "-A")
    git(v, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")
    before = fingerprint(v)
    assert before is not None
    (v / "Canon.md").write_text("# Canon\n")           # exactly what a stray test would do
    moved = changed(before, fingerprint(v))
    assert [Path(m).name for m in moved] == ["Canon.md"], moved
    assert fingerprint(tmp_path / "no-such-vault") is None


def test_the_guard_sees_a_GITIGNORED_file_being_modified(tmp_path):
    """The reviewer's finding, as a test.

    The first fingerprint was git's view — HEAD, porcelain status, top-level names — and git has
    no opinion about an ignored file's contents. In this vault the ignored files are precisely the
    hook-written status files that are @-imported into every session and whose absence fails
    silently: a stray test corrupting one would have sailed through clean."""
    fingerprint, changed = guard().fingerprint, guard().changed
    v = tmp_path / "vault"
    (v / "Global").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    (v / ".gitignore").write_text("Global/status.md\n")
    (v / "Global" / "status.md").write_text("the hook wrote this\n")
    git(v, "add", "-A")
    git(v, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")
    assert git(v, "status", "--porcelain").strip() == "", "the fixture's file is not ignored"
    before = fingerprint(v)
    (v / "Global" / "status.md").write_text("something else entirely, and longer\n")
    assert git(v, "status", "--porcelain").strip() == "", "git still sees nothing — the point"
    moved = changed(before, fingerprint(v))
    assert [Path(m).name for m in moved] == ["status.md"], moved


def test_a_DATED_cleanup_folder_is_excluded_too(world, tmp_path):
    """★ The exact-name defect, found by a synthesis pass while the quarantine from a real incident
    sat inside the vault being searched.

    The convention is `Cleanup YYYY-MM-DD <what happened>/`, so the exact-name test
    `"Cleanup 2026-09-15 accidental-compaction" in {"Cleanup"}` was False and a folder holding
    hundreds of copied role files counted as memory — every vault-wide query returning three to five
    copies of each entry. A name was used where a class was meant."""
    sys.path.insert(0, str(PLUGIN))
    import recall
    assert recall.is_removed_dir("Cleanup")
    assert recall.is_removed_dir("Cleanup 2026-09-15 accidental-compaction")
    assert recall.is_removed_dir("Cleanup-2026-09-15")
    # NEGATIVE CONTROL: a real memory folder whose name merely begins the same way is NOT excluded.
    assert not recall.is_removed_dir("Cleanups")
    assert not recall.is_removed_dir("CleanupNotes")
    assert not recall.is_removed_dir("Canon")

    dated = world["vault"] / "Cleanup 2026-09-15 an incident" / "removed" / "Proj"
    dated.mkdir(parents=True)
    (dated / "Canon.md").write_text("# Canon\n\n## a quarantined copy\n\nwords\n")
    (world["vault"] / "Proj" / "Canon.md").write_text("# Canon\n\n## a live entry\n\nwords\n")
    found = [str(p) for p in recall.md_files(world["vault"], include_queues=True)]
    assert not any("Cleanup 2026-09-15" in f for f in found), found
    # POSITIVE CONTROL: the live file beside it IS searched, so the exclusion is about the folder.
    assert any(f.endswith("Proj/Canon.md") for f in found), found


# ---------------- the second net's own unit tests, which it did not have ----
def test_compaction_signature_flags_a_SHRINK_WITH_NO_ARCHIVE(world):
    """★ The mode the net was decorative in, and a reviewer had to call it directly to find out.

    It required BOTH a shrink AND new `-archive` files — the compactor's exact fingerprint — so a
    memory file cut from 5,000 bytes to 500 by anything else returned `[]`. The door catches writes
    through `atomic_write`; this net exists for everything the door does not see, and demanding the
    compactor's signature meant it caught only what was already caught.

    It had ZERO dedicated tests: it ran only implicitly against the live vault during full-suite
    runs, where by design it stays quiet. A guard whose only exercise is the case where it must say
    nothing has never been shown to say anything."""
    g = guard()
    before = {"/v/Proj/Canon.md": (5000, 1)}
    after = {"/v/Proj/Canon.md": (500, 2)}
    assert g.compaction_signature(before, after) == ["/v/Proj/Canon.md"]


def test_compaction_signature_flags_a_VANISHED_file(world):
    g = guard()
    assert g.compaction_signature({"/v/Proj/Canon.md": (100, 1)}, {}) == ["/v/Proj/Canon.md"]


def test_compaction_signature_is_QUIET_on_growth_and_on_noise(world):
    """The negative controls, and they are what keep the widened net usable. A file that GREW is
    somebody writing; the generated mirrors and the queue/notice surfaces churn constantly and are
    every false positive this guard has actually produced."""
    g = guard()
    grew = g.compaction_signature({"/v/Proj/Canon.md": (100, 1)}, {"/v/Proj/Canon.md": (900, 2)})
    assert grew == [], grew
    for noisy in ("/v/.gedaechtnis/views/Speculum/Errata.md",
                  "/v/Pharos/queues/regions/x.md",
                  "/v/Channels/CURSUS/a-notice.md"):
        out = g.compaction_signature({noisy: (5000, 1)}, {noisy: (10, 2)})
        assert out == [], f"{noisy} should be noise, got {out}"


def test_compaction_signature_ignores_a_QUARANTINE(world):
    """A file leaving a `Cleanup …/` bundle is a RESTORE, which is the opposite of damage."""
    g = guard()
    q = "/v/Cleanup 2026-09-15 an incident/removed/Proj/Canon.md"
    assert g.compaction_signature({q: (5000, 1)}, {}) == []
    assert g.compaction_signature({q: (5000, 1)}, {q: (10, 2)}) == []


def test_compaction_signature_says_nothing_when_it_could_not_look(world):
    """`None` is "no vault to compare", not "nothing changed"."""
    g = guard()
    assert g.compaction_signature(None, {"/v/a.md": (1, 1)}) == []
    assert g.compaction_signature({"/v/a.md": (1, 1)}, None) == []


def test_the_empty_chain_fallback_holds_UNDER_A_REAL_FAILURE(world, monkeypatch):
    """★ Finding #2: the shipped test for this could not fail.

    It passed a nonexistent cwd expecting to hit `always_loaded`'s `except` branch — but
    `boot_chain_files` swallows a missing path silently and never raises, so the trigger condition
    never occurred and the test passed for a reason unrelated to the property. The fault is injected
    here instead, which is the only way to reach the branch."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("gd_bf_fault", PLUGIN / "bootfile.py")
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)
    import sys
    sys.path.insert(0, str(PLUGIN / "hooks"))
    import session_start

    def boom(*a, **k):
        raise RuntimeError("the chain could not be read")
    monkeypatch.setattr(session_start, "boot_chain_files", boom)
    assert bf.always_loaded("/anything") == set(), "a raising chain must yield NOTHING extra"
    # And the safe direction is what that buys: a Boot file is still protected by its own rule,
    # which needs no chain, while an ordinary file is not protected by a chain nobody could read.
    boot = world["vault"] / "Proj" / "Kernel.md"
    boot.parent.mkdir(parents=True, exist_ok=True)
    boot.write_text("# Boot\n")
    assert bf.is_protected(boot, chain=set()) == "it is a Boot file"
    assert bf.is_protected(world["vault"] / "Proj" / "Canon.md", chain=set()) is None
