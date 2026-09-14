"""Tests for the operating rules — the text every session that starts is handed.

A rules file is a CONTRACT WITH THE MODEL, and the defect this file exists to prevent is the one
row R5 found on arrival: the rules still promised the user a cleanup-approval question that row R2
had removed from the product a few hours earlier. The old test passed throughout, because it pinned
what the file SAID and nothing tied that to what the code DID. So the assertions here reach across
the seam wherever they can — the rules and `cleanup.py`, the rules and the command, the rules and
the agent roster — rather than quoting the file back to itself.

The second half covers the assembly. Since R5 the text is built for the vault in front of it, and
the only thing that may vary is a paragraph that CANNOT APPLY there. Every standing rule is
asserted to survive every vault shape, sentence by sentence, because "the rules got smaller" and
"a rule went missing" look identical in a byte count.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
RULES = PLUGIN / "rules" / "operating-rules.md"
RULES_TEXT = RULES.read_text(encoding="utf-8")


def flat(text: str) -> str:
    """The text with every run of whitespace collapsed.

    Every assertion here matches against this rather than the raw file. A rules sentence spans a
    line break wherever the paragraph happens to wrap, so pinning the wrap makes rewrapping a
    paragraph fail a test about its MEANING — and the first version of this file did exactly that,
    twice."""
    return " ".join(text.split())

# Sentences that must reach EVERY session, whatever its vault looks like. Each is a rule a session
# cannot follow if it is not told, and each names a way to lose or corrupt somebody's memory.
STANDING = [
    "Never delete a vault file",
    "Edit an existing note, never rewrite it whole",
    "Committing is the hook's job",
    "Never run `git add -A`",
    "Consult recall before re-deriving",
    "Write immediately, while the work is happening",
    "Nothing else in these rules asks the user anything",
    "Wrote:",
    "Needs a decision:",
    "Committing:",
    "Open:",
]


def assemble(conditions: dict) -> str:
    sys.path.insert(0, str(HOOKS))
    import importlib.util
    spec = importlib.util.spec_from_file_location("gedaechtnis_session_start",
                                                  HOOKS / "session_start.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.assemble_rules(RULES_TEXT, conditions)


# ------------------------------------------------------------ two questions ----
def test_the_rules_name_exactly_two_questions_and_claim_no_others():
    assert "exactly two things" in flat(RULES_TEXT)
    assert "which of the projects found should get a memory" in flat(RULES_TEXT)
    assert "(yes / no / never)" in flat(RULES_TEXT)
    assert "Nothing else in these rules asks the user anything" in flat(RULES_TEXT)


def test_the_rules_do_not_promise_a_cleanup_question_THE_PRODUCT_DOES_NOT_ASK():
    """★ The test that would have caught R5's opening defect, and the reason it is written across
    the seam instead of against the file.

    Until R5 the rules told a session to ask the user "whether to gather them into one folder"
    before a cleanup — a question row R2 had already removed from `cleanup.py`, on a ruling that
    cleanup asks nothing. The old assertion pinned that sentence and stayed green, because a rules
    file quoted back to itself agrees with itself by construction. This one fails if EITHER half
    moves: if the question returns to the rules, or if a question appears in the applier."""
    assert "gather them into one folder" not in flat(RULES_TEXT)
    assert "three things" not in flat(RULES_TEXT)
    src = (PLUGIN / "cleanup.py").read_text(encoding="utf-8")
    assert "input(" not in src, "the applier must never wait for the user"


def test_the_readme_agrees_with_the_rules_about_the_count():
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "two questions a session ever asks" in readme
    assert "three questions a session ever asks" not in readme


# ---------------------------------------------------------------- the chain ----
def test_the_ratification_chain_is_stated_as_a_rule():
    """The generic form of the chain the vault this grew from runs by hand: a change that
    contradicts something already written is ratified before it lands, APPROVE and APPROVE WITH
    REVISIONS are applied without asking, and only REJECT and ESCALATE reach the user."""
    assert "memory-reviewer" in flat(RULES_TEXT)
    assert ("APPROVE and APPROVE WITH REVISIONS you apply yourself, without asking anyone"
            in flat(RULES_TEXT))
    assert "only REJECT and ESCALATE reach the user" in flat(RULES_TEXT)
    # and the agent it names is actually installed, or the rule is unfollowable
    assert (PLUGIN / "agents" / "memory-reviewer.md").is_file()


def test_the_chain_carries_no_private_vocabulary():
    """REPORT §4's 'must lose' for this row, as a check on the CONCEPTS rather than the names —
    the publish check already refuses a personal name, and would not notice a private workflow."""
    # NOT a bare "atlas-": `.atlas-lane` is the marker filename, which is this package's own
    # public contract with every repo that installs it, and forbidding the substring would forbid
    # the rules from naming the file they depend on. The agent names are what must not travel.
    for leaked in ("atlas-debriefer", "atlas-design-reviewer", "atlas-lustrum", "atlas-mathesis",
                   "atlas-recall", "atlas-region", "lustrum", "mathesis", "tier promotion", "Y/N"):
        assert leaked.lower() not in flat(RULES_TEXT).lower(), leaked


# ----------------------------------------------------------- the assembly ----
ALL_ON = {"kernel": True, "reviewer": True}
ALL_OFF = {"kernel": False, "reviewer": False}


@pytest.mark.parametrize("conditions", [ALL_ON, ALL_OFF,
                                        {"kernel": True, "reviewer": False},
                                        {"kernel": False, "reviewer": True}])
def test_every_standing_rule_survives_every_vault_shape(conditions):
    """The load-bearing assertion of the whole mechanism. 'The rules got smaller' and 'a rule went
    missing' are the same number; only naming the sentences tells them apart."""
    text = assemble(conditions)
    for sentence in STANDING:
        assert sentence in flat(text), f"{sentence!r} dropped under {conditions}"


def test_a_vault_with_no_boot_file_is_not_told_about_boot_files():
    on, off = assemble(ALL_ON), assemble({"kernel": False, "reviewer": True})
    assert "**Boot** (`Kernel.md`)" in flat(on)
    assert "Boot" not in off
    assert len(off) < len(on)
    print(f"\nassembled: kernel-on {len(on.encode())} B, kernel-off {len(off.encode())} B")


def test_an_install_without_the_reviewer_is_not_told_to_use_it():
    """An instruction to use a tool that is not installed is worse than silence: it invites the
    model to invent a substitute for it."""
    off = assemble({"kernel": True, "reviewer": False})
    assert "memory-reviewer" not in off
    assert "Wrote:" in off                                   # the debrief itself is NOT conditional


def test_an_unknown_condition_name_KEEPS_its_paragraph():
    """The direction of this default is the only thing protecting the mechanism. A typo in a
    sentinel must leave the rule in every session, never delete it from every session."""
    text = assemble({})                                       # nothing known at all
    for sentence in STANDING:
        assert sentence in flat(text)
    assert "memory-reviewer" in text and "**Boot** (`Kernel.md`)" in flat(text)


def test_the_assembly_leaves_no_sentinel_in_the_text():
    for conditions in (ALL_ON, ALL_OFF):
        assert "only-if" not in assemble(conditions)
        assert "<!--" not in assemble(conditions)


def test_the_assembled_text_has_no_torn_seams():
    """A dropped span must not leave a doubled blank line or a line ending in a space — the text
    goes into a model's context, and a paragraph with a hole in it reads as a truncation."""
    for conditions in (ALL_ON, ALL_OFF):
        text = assemble(conditions)
        assert "\n\n\n" not in text
        assert not any(line != line.rstrip() for line in text.splitlines())


def test_the_rules_file_is_still_small():
    assert len(RULES_TEXT.encode("utf-8")) <= 4500, len(RULES_TEXT.encode("utf-8"))


# ------------------------------------------------- the conditions are REAL ----
@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Proj").mkdir(parents=True)
    (vault / "Proj" / "Map.md").write_text("# Proj\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# p\n")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault),
               GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"))
    return dict(vault=vault, repo=repo, env=env)


def boot_context(w) -> str:
    payload = json.dumps({"session_id": "s1", "cwd": str(w["repo"]), "source": "startup"})
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "session_start.py")],
                       input=payload, capture_output=True, text=True, env=w["env"], timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def test_the_condition_is_computed_from_the_REAL_vault_at_boot(world):
    """Not from a flag, and not from a fixture: the hook walks the vault it is pointed at. Both
    directions, in one test, so a condition wired to a constant fails one of them."""
    without = boot_context(world)
    assert "Boot" not in without, "a vault with no Kernel.md was told about boot files"
    assert "Never delete a vault file" in without, "the rules did not reach the session at all"
    (world["vault"] / "Proj" / "Kernel.md").write_text("# boot\n")
    with_kernel = boot_context(world)
    assert "**Boot** (`Kernel.md`)" in with_kernel
