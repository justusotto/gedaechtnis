"""authority.py + the gate's authority-ledger door (STALEAUTH-1).

The rule cuts the two mechanisms that were the largest class in the census it was built from: a
record with no `ref:` line (invisible to the tooling that cites it) and an amendment that leaves no
forward marker on what it amended (so the older, superseded record reads as current to everyone who
reaches it first).

Both directions, everywhere. This door's dangerous failure is NOISE — it sits on a file people
append to under time pressure, and a door that cries wolf gets worked around — so the negative
controls here matter more than the positive ones, and the real ledger is used as a fixture.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import authority                                    # noqa: E402  — a pure module, safe to import

# The installation's real ledger, used as a fixture when it is present: a rule about a hand-grown
# format is worth exactly as much as its behaviour on the real thing. Skipped, never faked, when
# this checkout has none — an absent fixture is UNCHECKED, which is a third outcome.
REAL_LEDGER = Path(__file__).resolve().parents[2] / ".orchestration" / "rulings" / "rulings.log"

WELL_FORMED = """## ruling: owner-ruling-alpha
- ref: owner-ruling-alpha
- recorded: 2026-01-01
"A ruling."
"""


# ------------------------------------------------------------------ parsing the three shapes ----

def test_all_three_record_shapes_are_read():
    """A checker that knows only the newest shape reports every older record as a violation."""
    text = ("## ruling: a\n- ref: a\n\n"
            "- id: b\n  ref: b\n\n"
            "- ref: c\n  date: 2026-01-01\n")
    assert [r for r, _ in authority.records(text)] == ["a", "b", "c"]


def test_a_ref_line_under_a_header_is_a_FIELD_not_a_new_record():
    """The ambiguity the whole parser turns on. `- ref:` opens a record AND is the first field
    inside a `## ruling:` record; treating every one as an opener splits each header record in two
    and reports the header half as having no `ref:` line — 53 false violations on a real ledger
    that had none."""
    assert [r for r, _ in authority.records("## ruling: a\n- ref: a\n- recorded: x\n")] == ["a"]
    assert authority.problems("", "## ruling: a\n- ref: a\n") == []


# ------------------------------------------------------------------ rule 1: the ref: line ----

def test_a_record_with_no_ref_line_is_flagged_and_the_fix_is_named():
    found = authority.problems("", "## ruling: owner-ruling-flat\n\"words with no fields\"\n")
    assert len(found) == 1
    assert "owner-ruling-flat" in found[0]
    assert "- ref: owner-ruling-flat" in found[0], "the fix must be named, not merely demanded"


def test_a_record_with_a_ref_line_passes_in_every_shape():
    for text in ("## ruling: a\n- ref: a\n", "- id: b\n  ref: b\n", "- ref: c\n  date: x\n"):
        assert authority.problems("", text) == [], text


# ------------------------------------------------------------------ rule 2: the forward marker ----

def test_an_amendment_without_a_forward_marker_is_flagged():
    text = ("- id: old\n  ref: old\n  ruling: X\n\n"
            "- id: new\n  ref: new\n  supersedes: old\n  ruling: not X\n")
    found = authority.problems("", text)
    assert len(found) == 1 and "superseded-by" in found[0]
    assert "`old`" in found[0] and "`new`" in found[0]


def test_a_well_formed_amend_pair_passes():
    """The row's third mandated control."""
    text = ("- id: old\n  ref: old\n  superseded-by: new (2026-01-02; FULL)\n  ruling: X\n\n"
            "- id: new\n  ref: new\n  supersedes: old\n  ruling: not X\n")
    assert authority.problems("", text) == []


def test_the_marker_may_sit_on_any_record_sharing_the_amended_ref():
    """This format grows ADDENDA that repeat the ref of the record they extend. Keeping only the
    last record under a ref would report a marker sitting on the first twin as missing."""
    text = ("- ref: old\n  superseded-by: new (2026-01-02; FULL)\n\n"
            "- ref: old (ADDENDUM)\n  note: more\n\n"
            "- id: new\n  ref: new\n  supersedes: old\n")
    assert authority.problems("", text) == []


def test_amending_something_that_is_not_a_record_here_does_NOT_fire():
    """The restraint that keeps this from becoming noise. Records amend design documents, section
    numbers and prose all the time; only an amendment to a record IN THIS LEDGER is this rule's
    business, and without this the door would fire on most real appends."""
    text = ("- id: new\n  ref: new\n"
            "  amends: docs/some-analysis-2026-07.md §4.7 (the per-batch human confirm)\n")
    assert authority.problems("", text) == []


def test_only_the_records_this_write_ADDS_are_judged():
    """An append-only ledger accumulates history under rules that changed. Re-judging the whole
    file on every write would refuse an unrelated append because of a record from a year ago."""
    existing = "## ruling: ancient\n\"no ref line, written before the rule existed\"\n"
    assert authority.problems("", existing), "precondition: the old record IS malformed"
    assert authority.problems(existing, existing + WELL_FORMED) == []


# ------------------------------------------------------------------ the real ledger ----

@pytest.mark.skipif(not REAL_LEDGER.is_file(), reason="no authority ledger in this checkout")
def test_the_real_ledgers_existing_records_pass_on_an_append():
    """The row's second mandated control, on the real corpus rather than a fixture of my own
    making: appending one well-formed record to the live ledger must produce NOTHING."""
    text = REAL_LEDGER.read_text(encoding="utf-8")
    assert len(authority.records(text)) > 100, "precondition: the ledger really is the big one"
    assert authority.problems(text, text + "\n" + WELL_FORMED) == []


@pytest.mark.skipif(not REAL_LEDGER.is_file(), reason="no authority ledger in this checkout")
def test_the_real_ledger_has_no_missing_ref_lines_left():
    """The `ref:` half, asserted over every record the live ledger holds. This is the half that was
    repaired before this door was built; if it regresses, the door was built on sand."""
    text = REAL_LEDGER.read_text(encoding="utf-8")
    missing = [f for f in authority.problems("", text) if "carries no `ref:` line" in f]
    assert missing == [], f"{len(missing)} record(s) lost their ref: line"


# ------------------------------------------------------------------ post-write reconstruction ----

def test_post_write_text_reconstructs_each_tool_shape():
    assert authority.post_write_text("old", "Write", {"content": "new"}) == "new"
    assert authority.post_write_text("a b c", "Edit",
                                     {"old_string": "b", "new_string": "B"}) == "a B c"
    assert authority.post_write_text("a a", "Edit",
                                     {"old_string": "a", "new_string": "X", "replace_all": True}) == "X X"
    assert authority.post_write_text("x", "MultiEdit", {"edits": [
        {"old_string": "x", "new_string": "y"}, {"old_string": "y", "new_string": "z"}]}) == "z"


def test_post_write_text_returns_None_rather_than_guessing():
    """None is a real answer. A door that guessed at the resulting text would judge a write nobody
    is about to make — and each of these shapes is one the edit itself would FAIL on."""
    assert authority.post_write_text("abc", "Edit", {"old_string": "zzz", "new_string": "q"}) is None
    # The absent-anchor case specifically under `replace_all`, where `str.replace` would otherwise
    # return the text UNCHANGED — a plausible-looking post-write text for an edit that will fail.
    assert authority.post_write_text("abc", "Edit", {"old_string": "zzz", "new_string": "q",
                                                     "replace_all": True}) is None
    assert authority.post_write_text("a a", "Edit", {"old_string": "a", "new_string": "q"}) is None
    assert authority.post_write_text("abc", "Write", {}) is None
    assert authority.post_write_text("abc", "Bash", {"command": "echo x >> f"}) is None


# ------------------------------------------------------------------ the door, as a subprocess ----

@pytest.fixture
def world(tmp_path):
    ledger = tmp_path / "rulings.log"
    ledger.write_text(WELL_FORMED, encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"authority_log": str(ledger)}), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(state), GEDAECHTNIS_CONFIG=str(cfg),
               GEDAECHTNIS_VAULT=str(tmp_path / "Atlas"))
    return dict(tmp=tmp_path, ledger=ledger, cfg=cfg, state=state, env=env)


def run(world, ti, tool="Write"):
    payload = {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": ti, "cwd": str(world["tmp"])}
    p = subprocess.run([sys.executable, str(HOOKS / "gate.py"), "write"],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=world["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def decision(res):
    return ((res or {}).get("hookSpecificOutput") or {}).get("permissionDecision")


def reason(res):
    h = (res or {}).get("hookSpecificOutput") or {}
    return h.get("permissionDecisionReason") or h.get("additionalContext") or ""


def test_the_door_WARNS_and_allows_before_the_partition_flip(world):
    """FLAG now, DENY with the partition flip: one flip act, two doors."""
    bad = WELL_FORMED + "\n## ruling: owner-ruling-flat\n\"no fields\"\n"
    res = run(world, {"file_path": str(world["ledger"]), "content": bad})
    assert decision(res) is None, "before the flip this door must not refuse anything"
    assert "owner-ruling-flat" in reason(res)


def test_the_door_DENIES_once_the_partition_mode_is_flipped(world):
    (world["state"] / "partition.mode").write_text("deny", encoding="utf-8")
    bad = WELL_FORMED + "\n## ruling: owner-ruling-flat\n\"no fields\"\n"
    res = run(world, {"file_path": str(world["ledger"]), "content": bad})
    assert decision(res) == "deny"
    assert "owner-ruling-flat" in reason(res)


def test_the_door_is_silent_on_a_well_formed_append(world):
    """The NEGATIVE control on the door itself, in both modes: a good append is never mentioned."""
    good = WELL_FORMED + "\n- id: owner-ruling-beta\n  ref: owner-ruling-beta\n  ruling: fine\n"
    assert run(world, {"file_path": str(world["ledger"]), "content": good}) is None
    (world["state"] / "partition.mode").write_text("deny", encoding="utf-8")
    assert run(world, {"file_path": str(world["ledger"]), "content": good}) is None


def test_the_door_ignores_every_other_file(world):
    """It is scoped to the configured ledger and nothing else."""
    other = world["tmp"] / "notes.md"
    other.write_text("hello\n", encoding="utf-8")
    assert run(world, {"file_path": str(other),
                       "content": "## ruling: owner-ruling-flat\n\"no fields\"\n"}) is None


def test_the_door_is_INERT_when_no_ledger_is_configured(world):
    """The shipped state. A plugin that hardcoded one installation's ledger would be carrying
    somebody else's vault around, so an unconfigured install must reach none of this."""
    world["cfg"].write_text(json.dumps({}), encoding="utf-8")
    bad = WELL_FORMED + "\n## ruling: owner-ruling-flat\n\"no fields\"\n"
    assert run(world, {"file_path": str(world["ledger"]), "content": bad}) is None


# ------------------------------------------------------------------ the newline-swallowing class ----

def test_an_empty_ref_value_does_not_borrow_the_NEXT_lines_token():
    """★ The field patterns are searched against a whole multi-line record, so `\\s` after the colon
    would match a NEWLINE and take the following line's first token as the value. A record with a
    `ref:` line and no ref then read as well-formed, and the rule went quiet — a false negative with
    no symptom, in the shape it is most likely to meet (a stray trailing space, or the value typed
    on the next line out of habit).

    Both positions matter: when the empty field is the LAST line of a record there is no next line
    to borrow from and the bug does not fire, so a test using only that position would pass over it.
    """
    mid = "## ruling: owner-ruling-empty\n- ref:   \n- recorded: 2026-01-01\n"
    last = "## ruling: owner-ruling-empty2\n- ref:   \n"
    assert len(authority.problems("", mid)) == 1, "an empty ref mid-record must still be flagged"
    assert len(authority.problems("", last)) == 1
    assert "carries no `ref:` line" in authority.problems("", mid)[0]


def test_an_empty_amends_value_does_not_borrow_the_next_line_as_its_target():
    """The same defect in the other value-capturing pattern. A blank `supersedes:` must name no
    target at all rather than silently adopting whatever follows it."""
    text = ("- id: old\n  ref: old\n  ruling: X\n\n"
            "- id: new\n  ref: new\n  supersedes:   \n  note: old\n")
    assert authority.problems("", text) == []


def test_field_values_are_still_found_with_tabs_and_odd_indentation():
    """The NEGATIVE control for the fix: narrowing the whitespace class to `[ \\t]` must not stop
    the patterns matching the real shapes, which are indented inconsistently across three formats."""
    for text in ("- id: a\n\tref:\ta\n", "- id: b\n    ref:   b\n", "## ruling: c\n-  ref:  c\n"):
        assert authority.problems("", text) == [], text


def test_the_door_emits_exactly_one_json_object_for_a_ledger_INSIDE_the_vault(tmp_path):
    """The ledger may live in a repo OR inside the memory vault — the module says so. Inside the
    vault the door's branch runs before the vault-only rules that follow it, and if it ever fell
    through to them, ONE PreToolUse call would print TWO `hookSpecificOutput` objects, which is not
    two notices but malformed hook output."""
    vault = tmp_path / "Atlas"
    (vault / "Global").mkdir(parents=True)
    # `.md`, deliberately: the whole-file-write door downstream binds prose memory files, so THIS is
    # the shape where a fall-through would actually reach a second printing branch. A `.log` ledger
    # would leave the bug unreachable and the test green over it.
    ledger = vault / "Global" / "rulings.md"
    ledger.write_text(WELL_FORMED, encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"authority_log": str(ledger)}), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(state), GEDAECHTNIS_CONFIG=str(cfg),
               GEDAECHTNIS_VAULT=str(vault))
    payload = {"session_id": "s1", "hook_event_name": "PreToolUse", "tool_name": "Write",
               "tool_input": {"file_path": str(ledger),
                              "content": WELL_FORMED + "\n## ruling: owner-ruling-flat\n\"no fields\"\n"},
               "cwd": str(tmp_path)}
    p = subprocess.run([sys.executable, str(HOOKS / "gate.py"), "write"],
                       input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    assert out.count('"hookSpecificOutput"') == 1, f"expected exactly one JSON object, got: {out}"
    json.loads(out)                                   # and it parses as one object, not two concatenated
    assert "owner-ruling-flat" in out
