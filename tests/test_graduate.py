"""test_graduate.py — GRADPATH-2 (`graduate.py`), the Boot-file drafting path.

GRADPATH-1 made the cleanup pass say what an over-budget boot chain needs. This is the half that
produces it, and it is the largest new WRITER in the package: it copies a file into the vault and
rewrites an @-import line, which is the line deciding what every future session reads.

**What the controls are aimed at.** The failure that matters here is silent and permanent: a Boot
file REPLACES the bodies at boot, so a binding rule the draft omits does not become less binding —
it becomes invisible. So the coverage check gets the heaviest controls, including the one that
matters most for a lexical check: **proof that its corpus is not empty**. A check whose trigger
condition never occurs returns "covered" forever, and its green is a fact about the fixture.

Same world as `test_split.py`: a git vault in tmp_path, the env seams redirected, the product
driven as a SUBPROCESS through `graduate.py`'s own entry point.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
GRADUATE = PLUGIN / "graduate.py"

IMPORTS_OPEN = "<!-- gedaechtnis:imports -->"
IMPORTS_CLOSE = "<!-- gedaechtnis:/imports -->"

LIMITS = {
    "split_min_entries": 8, "split_min_remaining_entries": 4,
    # ★ EVERY role named, deliberately. This fixture used to list only the roles it cares
    # about, and that WAS its isolation: the limits FILE replaced the table, so an unnamed
    # role simply had no limit. Since the file layer merges dict limits per sub-key (a fix
    # for a silent protection loss), an unnamed role quietly inherits the SHIPPED default
    # instead — the fixture stays green and stops isolating what it was written to isolate.
    # The roles this file does not test are given a ceiling nothing here can reach, which
    # says the same thing the short table used to say, out loud.
    "role_soft_limits_lines": {"Map": 10000000, "Vision": 10000000, "Position": 250, "Course": 10000000, "Aporia": 10000000, "Errata": 10000000, "Annales": 10000000, "Canon": 800},
    "stale_entry_days": 56, "compaction_floor_share": 0.4,
    "max_memory_file_bytes": 500000, "max_searchable_file_bytes": 2000000,
    "cleanup_trigger_days": 14, "cleanup_trigger_commits": 300,
    "synthesis_trigger_days": 14, "synthesis_trigger_entries": 20,
    "boot_budget_bytes": 20000, "boot_budget_warn_bytes": 20000,
    "boot_file_budget_bytes": 32000, "boot_file_warn_bytes": 28000,
}

# Two binding lines, written the two ways a real body writes them: one shouting and bold, one not.
# The coverage check must find BOTH — "a rule does not stop binding when its author stops shouting".
CANON = """# Canon

## The collection is synced

**NEVER import an `.apkg` over a live collection** — the sync is the path.

## Naming

Always name a region for the room, never for the activity in it.
"""

SECTIONS = {
    "at_a_glance": ["Proj is the thing this vault remembers."],
    "resume_point": ["Mid-flight: the second half of the arc."],
    "state_table": ["arc | live | second half"],
    "standing_constraints": [
        "**NEVER import an `.apkg` over a live collection** — the sync is the path.",
        "Always name a region for the room, never for the activity in it.",
    ],
    "canon_headlines": ["The collection is synced, never imported."],
    "open_questions": ["Whether the naming pass runs before or after the split."],
    "pointer_map": ["[[Canon]] — the locked decisions and why."],
}


def git(vault: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=True)
    return p.stdout


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Proj").mkdir(parents=True)
    (vault / "Proj" / "Map.md").write_text("# Proj\n", encoding="utf-8")
    (vault / "Proj" / "Canon.md").write_text(CANON, encoding="utf-8")
    (vault / "Proj" / "Position.md").write_text("# Position\n\n## now\n\nworking.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    git(vault, "add", "-A")
    git(vault, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "root")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: MY-PROJECT\npath: Proj/\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text(
        f"# Proj\n\nmy own instructions\n\n{IMPORTS_OPEN}\n@{vault / 'Proj' / 'Position.md'}\n"
        f"{IMPORTS_CLOSE}\n", encoding="utf-8")
    limits_file = tmp_path / "limits.json"
    limits_file.write_text(json.dumps(LIMITS), encoding="utf-8")
    state = tmp_path / "state"
    env = dict(os.environ,
               GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_LIMITS=str(limits_file),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-such-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, state=state, env=env, tmp=tmp_path)


def run(w, *args, expect_rc=0):
    p = subprocess.run([sys.executable, "-B", str(GRADUATE), *args], capture_output=True,
                       text=True, env=w["env"], timeout=120, cwd=str(w["repo"]))
    assert p.returncode == expect_rc, f"rc={p.returncode}\n{p.stdout}\n{p.stderr}"
    return p


def write_sections(w, sections) -> Path:
    q = w["tmp"] / "sections.json"
    q.write_text(json.dumps(sections), encoding="utf-8")
    return q


def assemble(w, sections=None, expect_rc=0):
    q = write_sections(w, SECTIONS if sections is None else sections)
    return run(w, "--assemble", "Proj", "--from", str(q), "--json", expect_rc=expect_rc)


# ------------------------------------------------------------------ check ----
def test_check_reports_and_writes_nothing(world):
    before = {p: p.read_bytes() for p in world["vault"].rglob("*.md")}
    out = json.loads(run(world, "--check", "Proj", "--json").stdout)
    assert out["region"] == "Proj" and out["boot_file"] is False
    assert out["rules"] == 2, out          # the corpus is NOT empty — see the vacuity test below
    assert {p: p.read_bytes() for p in world["vault"].rglob("*.md")} == before


def test_worthiness_names_a_dormant_region_rather_than_refusing_it(world):
    """A region nobody has touched in 30 days is REPORTED as dormant, and the command stops to ask.
    It is a fact a reader should have before spending a judgment, not a refusal."""
    out = run(world, "--check", "Proj").stdout
    assert "WORTHINESS" in out
    # the fixture's only commit is the root commit, made now, so it is not dormant
    assert "DORMANT" not in out, out


# --------------------------------------------------------------- assemble ----
def test_the_window_is_declared_BY_CONSTRUCTION(world):
    """★ The agent never sees a marker, so it cannot forget one. `bootfile.split_window` — the same
    function the compactor uses to decide whether it may touch a file — must find a closed window in
    every draft this path produces."""
    out = json.loads(assemble(world).stdout)
    assert out["ok"] is True, out
    text = Path(out["draft"]).read_text(encoding="utf-8")
    sys.path.insert(0, str(PLUGIN))
    import bootfile
    parts = bootfile.split_window(text)
    assert parts is not None, "a draft without a closed window is one the compactor can never touch"
    _before, window, _after = parts
    assert "Mid-flight" in window, "the resume point must be INSIDE the window"
    assert "NEVER import" not in window, "a binding rule must never sit in the rolling window"


def test_a_missing_rule_line_REFUSES_the_draft_and_names_it(world):
    """The coverage floor. Drop one of the two constraints and the draft is refused, the missing
    line quoted — and nothing is written."""
    short = dict(SECTIONS, standing_constraints=[SECTIONS["standing_constraints"][0]])
    p = assemble(world, short, expect_rc=9)
    out = json.loads(p.stdout)
    assert out["ok"] is False
    assert any("Always name a region" in m for m in out["missing_rules"]), out
    assert not (world["state"] / "drafts").exists(), "a refused draft was written anyway"


def test_an_unshouted_rule_counts_as_much_as_a_shouted_one(world):
    """Both fixture rules are real: one bold and upper-case, one plain prose. If the matcher only
    saw the shouted one this test would pass with the plain one dropped — so it drops exactly
    that one."""
    short = dict(SECTIONS, standing_constraints=[SECTIONS["standing_constraints"][1]])
    out = json.loads(assemble(world, short, expect_rc=9).stdout)
    assert any("NEVER import" in m for m in out["missing_rules"]), out


def test_a_paraphrased_rule_does_not_satisfy_the_check(world):
    """The match is on the rule's own first words. A constraint that means the same thing and says
    it differently is a NEW rule as far as this check is concerned, which is the conservative
    direction: the cost of a false refusal is one more agent run."""
    para = dict(SECTIONS, standing_constraints=[
        "Do not import an .apkg over a live collection; sync instead.",
        "Always name a region for the room, never for the activity in it."])
    out = json.loads(assemble(world, para, expect_rc=9).stdout)
    assert any("NEVER import" in m for m in out["missing_rules"]), out


def test_formatting_differences_DO_NOT_cause_a_false_refusal(world):
    """The other side of the same line, and the reason the check normalises at all: a draft that
    carries the rule faithfully but re-emphasises it has carried the rule."""
    reflowed = dict(SECTIONS, standing_constraints=[
        "NEVER import an `.apkg` over a live collection (the sync is the path).",
        "**Always** name a region for the room — never for the activity in it!"])
    out = json.loads(assemble(world, reflowed).stdout)
    assert out["ok"] is True, out


def test_an_empty_resume_point_is_refused(world):
    """A declared window with nothing in it can never roll, so the file would be uncompactable from
    the day it was written — the exact state the fleet's own Boot files are in."""
    out = json.loads(assemble(world, dict(SECTIONS, resume_point=[]), expect_rc=9).stdout)
    assert "window" in out["why"] and "roll" in out["why"], out


def test_a_missing_section_is_refused_not_rendered_blank(world):
    out = json.loads(assemble(world, {k: v for k, v in SECTIONS.items() if k != "pointer_map"},
                              expect_rc=9).stdout)
    assert "pointer_map" in out["why"], out


def test_an_over_budget_draft_is_refused(world):
    big = dict(SECTIONS, canon_headlines=["x" * 40_000])
    out = json.loads(assemble(world, big, expect_rc=9).stdout)
    assert "over the" in out["why"] and "budget" in out["why"], out


# ---------------------------------------------------- the check is not vacuous ----
def test_a_region_with_NO_rule_body_is_UNCHECKED_never_covered(world):
    """★ THE CONTROL THAT MAKES EVERY OTHER COVERAGE TEST MEAN SOMETHING.

    A lexical check over an empty corpus returns "nothing missing" — and the first version of this
    module pointed at a role file the package does not ship, so in a stranger's install exactly half
    its corpus was absent and it would have said `covered` forever. A check that cannot run reports
    UNCHECKED; it does not pass."""
    (world["vault"] / "Proj" / "Canon.md").unlink()
    out = json.loads(assemble(world, expect_rc=9).stdout)
    assert "UNCHECKED" in out["why"], out
    assert not (world["state"] / "drafts").exists()


# ------------------------------------------------------------------ apply ----
def drafted(world) -> Path:
    return Path(json.loads(assemble(world).stdout)["draft"])


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_apply_lands_the_boot_file_repoints_the_import_and_writes_a_receipt(world):
    d = drafted(world)
    out = json.loads(run(world, "--apply", "Proj", "--draft", str(d),
                         "--repo", str(world["repo"]), "--json").stdout)
    assert out["applied"] is True, out

    boot = world["vault"] / "Proj" / "Kernel.md"
    assert boot.is_file() and boot.read_text(encoding="utf-8") == d.read_text(encoding="utf-8")

    md = (world["repo"] / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"@{boot}" in md, md
    assert "Position.md" not in md, "the body import was not replaced"
    assert "my own instructions" in md, "the user's own text outside the block was touched"
    assert md.count(IMPORTS_OPEN) == 1 and md.count(IMPORTS_CLOSE) == 1

    receipt = (world["vault"] / "Proj" / "GRADUATION-RECEIPT.md").read_text(encoding="utf-8")
    assert "Nothing was deleted" in receipt
    assert f"@{boot}" in receipt and "Position.md" in receipt, "the receipt must name both sides"
    assert Path(out["backup"]).is_file(), "the previous CLAUDE.md was not kept"
    # nothing was deleted: every body the draft summarises is still there, byte for byte
    assert (world["vault"] / "Proj" / "Position.md").is_file()
    assert (world["vault"] / "Proj" / "Canon.md").read_text(encoding="utf-8") == CANON


def test_an_UNMARKED_import_block_is_left_byte_identical_and_the_lines_are_PRINTED(world):
    """★ exit 7. A block without the markers was written by a person or by an install older than
    them, and this script cannot tell which of its lines are its business. Rewriting by guess is how
    a hand-tuned import list loses an entry with no error — so it prints and touches nothing.

    The positive control is the byte comparison: not merely "no crash", but that the file a person
    hand-wrote is exactly as they left it."""
    claude = world["repo"] / "CLAUDE.md"
    hand = f"# Proj\n\nmine\n\n@{world['vault'] / 'Proj' / 'Position.md'}\n@/somewhere/else.md\n"
    claude.write_text(hand, encoding="utf-8")
    d = drafted(world)
    before = tree(world["vault"])

    p = run(world, "--apply", "Proj", "--draft", str(d), "--repo", str(world["repo"]), expect_rc=7)
    assert "no marked import block" in p.stderr, p.stderr
    assert f"@{world['vault'] / 'Proj' / 'Kernel.md'}" in p.stderr, "it must print the line to paste"

    assert claude.read_text(encoding="utf-8") == hand, "an unmarked block was edited"
    assert tree(world["vault"]) == before, "the vault moved on a refusal"


def test_apply_REFUSES_when_a_body_has_moved_since_the_draft(world):
    """Approval is for a distillation of a STATE. Ancestry, not timestamps — this package's own
    hook commits several files inside one second."""
    d = drafted(world)
    pos = world["vault"] / "Proj" / "Position.md"
    pos.write_text("# Position\n\n## now\n\neverything changed.\n", encoding="utf-8")
    git(world["vault"], "add", "-A")
    git(world["vault"], "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "moved")

    before = tree(world["vault"])
    p = run(world, "--apply", "Proj", "--draft", str(d), "--repo", str(world["repo"]), expect_rc=9)
    assert "have moved since it was made" in p.stderr, p.stderr
    assert "Position.md" in p.stderr
    assert tree(world["vault"]) == before
    assert not (world["vault"] / "Proj" / "Kernel.md").exists()


def test_apply_REFUSES_a_draft_with_no_provenance_file(world):
    """A draft whose sidecar is gone cannot have its bodies checked at all. UNCHECKED is not fresh,
    so it is not applied — and a hand-written 'draft' cannot be slipped in past the freshness rule
    by simply not having one."""
    d = drafted(world)
    d.with_suffix(".json").unlink()
    p = run(world, "--apply", "Proj", "--draft", str(d), "--repo", str(world["repo"]), expect_rc=9)
    assert "provenance" in p.stderr, p.stderr
    assert not (world["vault"] / "Proj" / "Kernel.md").exists()


def test_apply_REFUSES_a_draft_without_a_closed_window(world):
    """Belt to the assembler's braces: a hand-edited draft that lost its markers would produce
    exactly the uncompactable file the window exists to prevent."""
    d = drafted(world)
    sys.path.insert(0, str(PLUGIN))
    import bootfile
    d.write_text(d.read_text(encoding="utf-8").replace(bootfile.WINDOW_CLOSE, ""), encoding="utf-8")
    p = run(world, "--apply", "Proj", "--draft", str(d), "--repo", str(world["repo"]), expect_rc=9)
    assert "CLOSED window" in p.stderr, p.stderr
    assert not (world["vault"] / "Proj" / "Kernel.md").exists()


def test_apply_REFUSES_a_second_graduation(world):
    d = drafted(world)
    run(world, "--apply", "Proj", "--draft", str(d), "--repo", str(world["repo"]), "--json")
    p = run(world, "--apply", "Proj", "--draft", str(d), "--repo", str(world["repo"]), expect_rc=2)
    assert "already exists" in p.stderr, p.stderr


def test_the_graduated_region_is_no_longer_a_graduation_candidate(world):
    """★ THE LOOP CLOSES, asserted end to end rather than argued. Before: the region has no Boot
    file and its bodies are what the chain would load. After: `bootfile` stops naming it, because
    the remedy GRADPATH-1's facts line describes has actually been carried out."""
    sys.path.insert(0, str(PLUGIN))
    import importlib, bootfile
    d = drafted(world)
    run(world, "--apply", "Proj", "--draft", str(d), "--repo", str(world["repo"]), "--json")
    code = (
        "import json, os, sys\n"
        f"sys.path[:0] = [{str(PLUGIN)!r}, {str(PLUGIN / 'hooks')!r}]\n"
        "import bootfile, config\n"
        "print(json.dumps({'candidates': [g['region'] for g in "
        "bootfile.graduation_candidates(config.vault())]}))\n"
    )
    p = subprocess.run([sys.executable, "-B", "-c", code], env=world["env"],
                       cwd=str(world["repo"]), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "Proj" not in json.loads(p.stdout)["candidates"]


# ------------------------------- the shapes a real region writes its rules in ----
MIDLINE_CANON = """# Canon

## Where the personal facts live

They are read only by explicitly opening the file while working this arc. **Never add them to any
@-import block**, and never quote their contents into a file that is imported.

## The pointer

A pointer here — never a CV-grade fact: no employer, no grade, no salary.
"""


def test_a_rule_that_does_not_START_a_line_is_still_a_rule(world):
    """★ FOUND BY RUNNING AGAINST A REAL REGION, not by a fixture. The first matcher anchored to
    `^`, and a real region's binding rules — about not putting personal facts into an @-import
    block, which is exactly what a Boot file IS — sit mid-sentence. It found ZERO and the coverage
    check passed in silence, on the one region where getting it wrong would leak.

    Three shapes here, all from that region: after a sentence stop and bold, after `, and`, and
    after an em dash."""
    (world["vault"] / "Proj" / "Canon.md").write_text(MIDLINE_CANON, encoding="utf-8")
    out = json.loads(run(world, "--check", "Proj", "--json").stdout)
    assert out["rules"] >= 3, out["rule_lines"]
    joined = " ".join(out["rule_lines"])
    assert "Never add them" in joined and "never quote" in joined and "never a CV-grade" in joined

    # and a draft that omits them is REFUSED, which is the half that matters
    out2 = json.loads(assemble(world, dict(SECTIONS, standing_constraints=["something else"]),
                               expect_rc=9).stdout)
    assert any("Never add them" in m for m in out2["missing_rules"]), out2


def test_ZERO_rules_found_is_REPORTED_never_silently_covered(world):
    """A rule body that exists and yields no rule line is not a verified draft — it is a check with
    nothing to check. The two produce identical output ("nothing missing") and mean opposite things,
    so the vacuous one says so, in `--check` and again on the written draft."""
    (world["vault"] / "Proj" / "Canon.md").write_text(
        "# Canon\n\n## A decision\n\nThe collection is synced.\n", encoding="utf-8")
    text = run(world, "--check", "Proj").stdout
    assert "ZERO rule lines were FOUND" in text, text

    out = json.loads(assemble(world).stdout)
    assert out["ok"] is True and out["coverage_vacuous"] is True, out
    plain = run(world, "--assemble", "Proj", "--from", str(write_sections(world, SECTIONS))).stdout
    assert "NOTHING TO CHECK" in plain, plain


def test_a_region_WITH_rules_is_not_reported_vacuous(world):
    """The negative control for the line above — otherwise "vacuous" could be printed always and
    every assertion about it would still pass."""
    out = json.loads(assemble(world).stdout)
    assert out["coverage_vacuous"] is False and out["rules_covered"] == 2, out
    plain = run(world, "--assemble", "Proj", "--from", str(write_sections(world, SECTIONS))).stdout
    assert "NOTHING TO CHECK" not in plain, plain


def test_a_rule_carried_INSIDE_a_longer_constraint_counts_as_carried(world):
    """★ THE FALSE-REFUSAL CASE, and the live control is what found it. The first matcher compared
    each constraint's OWN first eight words against the rule's, which only works when a constraint
    opens with the rule. A real draft does not write that way — it sets the rule up and carries it
    mid-sentence. On a real region all three rules were carried faithfully and all three were
    reported missing.

    A false refusal is the survivable direction, but it would have sent every honest draft round the
    loop for ever, and the obvious 'fix' for that is to loosen the check until it stops complaining
    — which is how the floor quietly stops being a floor."""
    embedded = dict(SECTIONS, standing_constraints=[
        "The collection is synchronised rather than replaced: **NEVER import an `.apkg` over a live "
        "collection** — the sync is the path, and an import loses scheduling state.",
        "Naming is a judgment worth making slowly. Always name a region for the room, never for the "
        "activity in it, because the activity changes and the room does not.",
    ])
    out = json.loads(assemble(world, embedded).stdout)
    assert out["ok"] is True, out.get("missing_rules")
    assert out["rules_covered"] == 2


def test_a_constraint_that_merely_SHARES_WORDS_does_not_satisfy_the_check(world):
    """The negative control for the line above — otherwise "contains" could have been loosened to
    any-word-overlap and every assertion about coverage would still pass. The words are all there;
    the rule's own eight, consecutively, are not."""
    shuffled = dict(SECTIONS, standing_constraints=[
        "An `.apkg` should never be imported; a live collection is synced instead.",
        "Always name a region for the room, never for the activity in it.",
    ])
    out = json.loads(assemble(world, shuffled, expect_rc=9).stdout)
    assert any("NEVER import" in m for m in out["missing_rules"]), out


# ------------------------------------------- the guard, in the state a real user is in ----
#
# ★ Every test above drives `graduate.py` in a subprocess built from `dict(os.environ, ...)`, so
# PYTEST_CURRENT_TEST is INHERITED — and `rootguard.permit` has a clause that permits anything
# under the run's temp root while that variable is set. The fixtures put the repo there. So the
# whole `--apply` path was exercised with the guard in its permissive mode, and on a real machine,
# where the repo is in a checkout and the vault is elsewhere, `--apply` raised `OutsideRoot` at the
# backup and died with a traceback: rc 1, no refusal message, the feature simply unusable.
#
# It is the row's own lesson one layer up. The coverage check's fixtures encoded its author's
# assumptions about how rules are written; these encoded the harness's assumption about where the
# guard is standing. Neither was reachable from inside the thing that shared the assumption — this
# one needed the variable REMOVED, which is the one thing the suite that sets it cannot do to
# itself by accident.

def _no_pytest_env(w) -> dict:
    """The world's env with PYTEST_CURRENT_TEST removed — the guard in its shipped mode."""
    env = dict(w["env"])
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def test_apply_works_on_a_repo_OUTSIDE_the_vault_with_the_guard_in_its_SHIPPED_mode(world):
    """The positive control the suite did not have: the graduation applied by a real user, whose
    CLAUDE.md is in a checkout that is not the vault, not the state dir and not a temp root.

    The precondition is ASSERTED rather than assumed. This test means nothing unless the repo is
    genuinely outside every root `rootguard` resolves from this env — and that is a property of the
    `world` fixture, not of the code under test. A later fixture change that moved the repo under a
    configured root would leave the test passing while testing nothing, which is the shape the whole
    row is about: a check that cannot fail reads exactly like a check that passed."""
    assemble(world)
    roots = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path[:0]=[%r,%r]\nimport rootguard\n"
         "print('\\n'.join(str(r) for r in rootguard.roots()))" % (str(PLUGIN), str(PLUGIN / "hooks"))],
        capture_output=True, text=True, env=_no_pytest_env(world), timeout=60)
    assert roots.returncode == 0, roots.stderr
    resolved = [Path(line) for line in roots.stdout.split("\n") if line.strip()]
    assert resolved, "no roots resolved — the probe, not the guard, is broken"
    repo = world["repo"].resolve()
    for r in resolved:
        assert not (repo == r or r in repo.parents), (
            f"the fixture's repo {repo} is INSIDE root {r}: this test would pass without the "
            f"scratch declaration and prove nothing")
    draft = world["state"] / "drafts" / "Proj" / "Kernel.md"
    p = subprocess.run([sys.executable, "-B", str(GRADUATE), "--apply", "Proj",
                        "--draft", str(draft), "--repo", str(world["repo"])],
                       capture_output=True, text=True, env=_no_pytest_env(world),
                       timeout=120, cwd=str(world["repo"]))
    assert p.returncode == 0, f"rc={p.returncode}\n{p.stdout}\n{p.stderr}"
    assert "OutsideRoot" not in p.stderr and "Traceback" not in p.stderr
    md = (world["repo"] / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"@{world['vault'] / 'Proj' / 'Kernel.md'}" in md
    assert "my own instructions" in md, "the prose outside the marked block must survive"
    backups = list(world["repo"].glob("CLAUDE.md.pre-graduate-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") != md


def test_the_scratch_root_is_BOUNDED_to_the_repo_it_was_declared_for(world):
    """The negative control, and the load-bearing half: the fix must not have turned the guard off.
    A declared scratch permits ONE directory; a write to a sibling directory outside every root is
    still refused, with PYTEST_CURRENT_TEST removed so the temp-root clause cannot answer instead."""
    probe = (
        "import sys, pathlib; sys.path.insert(0, {plugin!r}); sys.path.insert(0, {hooks!r});\n"
        "import archive, rootguard\n"
        "ok = pathlib.Path({repo!r}) / 'inside.md'\n"
        "no = pathlib.Path({other!r}) / 'elsewhere.md'\n"
        "archive.atomic_write(ok, 'x', scratch={repo!r})\n"
        "try:\n"
        "    archive.atomic_write(no, 'x', scratch={repo!r})\n"
        "    print('LEAKED')\n"
        "except rootguard.OutsideRoot:\n"
        "    print('REFUSED-OTHER')\n"
        "try:\n"
        "    archive.atomic_write(pathlib.Path({repo!r}) / 'noscratch.md', 'x')\n"
        "    print('LEAKED-NOSCRATCH')\n"
        "except rootguard.OutsideRoot:\n"
        "    print('REFUSED-NOSCRATCH')\n"
    ).format(plugin=str(PLUGIN), hooks=str(PLUGIN / "hooks"), repo=str(world["repo"]),
             other=str(world["tmp"] / "elsewhere"))
    (world["tmp"] / "elsewhere").mkdir()
    p = subprocess.run([sys.executable, "-B", "-c", probe], capture_output=True, text=True,
                       env=_no_pytest_env(world), timeout=60)
    assert p.returncode == 0, p.stderr
    assert p.stdout.split() == ["REFUSED-OTHER", "REFUSED-NOSCRATCH"], p.stdout


def test_the_scratch_declared_at_each_call_site_is_the_REPO_ITSELF_and_nothing_wider():
    """A STRUCTURAL check, and it says so, because no behavioural one is possible.

    The mutation sweep for this row widened `scratch=repo` to `scratch=repo.parent` and every test
    stayed green — correctly, because `apply()` writes only `CLAUDE.md` and its backup, and both are
    inside the repo under either declaration. Nothing a user could observe changes, so nothing a
    behavioural test can assert changes either.

    That is exactly when a structural assertion earns its place. The widened form is not wrong
    TODAY; it is a permission granted to every line added to this function LATER, and the cost of
    discovering it then is a write into a sibling checkout that the guard waved through. Protection
    derives from the declared root, never from what the current code happens to do with it — so the
    narrowness is pinned here, at the call sites, where a future edit will meet it.

    The sibling mutation (giving a VAULT write `scratch=repo`) also stays green and is NOT a defect:
    the vault is already a root, so a redundant scratch there cannot widen anything. A mutation that
    changes no behaviour is not evidence of a decorative control, and is not recorded as one."""
    import ast
    tree = ast.parse(GRADUATE.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "apply")
    found = [kw.value for n in ast.walk(fn) if isinstance(n, ast.Call)
             for kw in n.keywords if kw.arg == "scratch"]
    assert found, "apply() declares no scratch root at all — the repo writes cannot work"
    for v in found:
        assert isinstance(v, ast.Name) and v.id == "repo", (
            f"a scratch root in apply() is {ast.unparse(v)!r}, not the bare `repo`: a wider root "
            f"permits every future write under it")


def test_a_SECOND_graduation_on_the_same_day_does_not_overwrite_the_first_backup(world):
    """★ A BACKUP THAT RESTORES A STATE OLDER THAN THE EDITS IS WORSE THAN NONE — IT LOOKS LIKE ONE.

    The backup name carries the date and the write was skipped when it already existed. So:
    graduate region A, hand-edit `CLAUDE.md`, graduate region B the same day — the second run found
    today's backup present, left it alone, and replaced the file. What survives is a copy of the
    state before the FIRST graduation; the hand edits in between are in no backup at all.

    A backup whose content does not match what is about to be replaced is not a backup of that
    thing, so a second one is written beside it."""
    assemble(world)
    draft = world["state"] / "drafts" / "Proj" / "Kernel.md"
    claude = world["repo"] / "CLAUDE.md"
    first = claude.read_text(encoding="utf-8")

    p = subprocess.run([sys.executable, "-B", str(GRADUATE), "--apply", "Proj",
                        "--draft", str(draft), "--repo", str(world["repo"])],
                       capture_output=True, text=True, env=_no_pytest_env(world), timeout=120,
                       cwd=str(world["repo"]))
    assert p.returncode == 0, p.stdout + p.stderr
    backups = sorted(world["repo"].glob("CLAUDE.md.pre-graduate-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == first

    edited = claude.read_text(encoding="utf-8") + "\n@/somewhere/else/Notes.md\n"
    claude.write_text(edited, encoding="utf-8")

    (world["vault"] / "Second").mkdir(exist_ok=True)
    (world["vault"] / "Second" / "Position.md").write_text("# Position\n\n## a\nbody\n", encoding="utf-8")
    d2 = world["state"] / "drafts" / "Second" / "Kernel.md"
    d2.parent.mkdir(parents=True, exist_ok=True)
    d2.write_text(draft.read_text(encoding="utf-8"), encoding="utf-8")
    (d2.parent / (d2.stem + ".json")).write_text(
        (draft.parent / (draft.stem + ".json")).read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run([sys.executable, "-B", str(GRADUATE), "--apply", "Second",
                    "--draft", str(d2), "--repo", str(world["repo"])],
                   capture_output=True, text=True, env=_no_pytest_env(world), timeout=120,
                   cwd=str(world["repo"]))

    saved = {b.read_text(encoding="utf-8") for b in world["repo"].glob("CLAUDE.md.pre-graduate-*")}
    assert first in saved, "the first backup was lost"
    assert edited in saved, "the hand edits between the two runs are in no backup at all"
