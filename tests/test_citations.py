"""citations.py + the write-time citation door (STALEWRITE-1).

A citation goes stale in silence: the row closes, the ruling is superseded, the id was mistyped.
Nothing errors and the sentence reads exactly as it did when it was true. This resolves the tokens
at the moment the text is written, and says only what is worth acting on.

Its dangerous failure is NOISE, so the negative controls carry the weight: a token inside a fence
or a blockquote is being SHOWN, not cited; an open row is silent; a live ruling is silent; and an
installation with no queue files or no ledger says nothing at all rather than flagging everything.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import citations                                      # noqa: E402  — pure module
import authority                                      # noqa: E402


# ------------------------------------------------------------------ what counts as a citation ----

def test_a_token_inside_a_fence_or_blockquote_is_not_a_citation():
    """★ THE MANDATORY NEGATIVE CONTROL (GATEPROSE-1's class). A document that reproduces an old
    queue row, or shows the format as an example, is not making a claim about that row's state.
    The defect this guards against is a rule that matched an id in quoted prose because it searched
    the raw text instead of the citable body."""
    qs, _, _ = citations.tokens("cite q:AA-1\n```\nq:BB-2\n```\n")
    assert qs == ["AA-1"]
    qs, _, _ = citations.tokens("> quoting q:CC-3 here\nq:DD-4\n")
    assert qs == ["DD-4"]
    qs, _, _ = citations.tokens("~~~\nq:EE-5\n~~~\nq:FF-6\n")
    assert qs == ["FF-6"]


def test_inline_backticks_ARE_still_citations():
    """The other direction, and the one that matters more: `q:…` in backticks is how a real
    citation is written in these files. Excluding it would switch the feature off while looking
    like caution."""
    qs, _, _ = citations.tokens("see `q:EE-5` and owner-ruling-x-2026-01-01")
    assert qs == ["EE-5"]


def test_an_unclosed_fence_blanks_the_rest_rather_than_reverting_to_scanning():
    qs, _, _ = citations.tokens("q:AA-1\n```\nq:BB-2\nq:CC-3\n")
    assert qs == ["AA-1"]


def test_the_cap_bounds_the_work_and_is_reported():
    many = " ".join(f"q:X-{i}" for i in range(30))
    qs, rs, capped = citations.tokens(many)
    assert capped and len(qs) + len(rs) == citations.CAP


# ------------------------------------------------------------------ row identity: TWO readings ----

QUEUE = """
- [ ] `q:CU-OPEN-1` do a thing | q:CU-OPEN-1
- [x] `q:CU-DONE-1` did a thing | q:CU-DONE-1
- [ ] `q:CU-CITER-1` this row merely CITES q:CU-DONE-1 in its text | q:CU-CITER-1
"""


def test_a_cited_id_is_not_owned_by_the_row_that_merely_mentions_it():
    """★ A q-id is not unique in a queue file — rows cite each other. Anchoring on the id's first
    or any occurrence reports rows as whatever state the CITING row happens to be in. Ignoring this
    once already produced a report calling two closed rows open."""
    assert citations.row_state("CU-DONE-1", QUEUE) == "closed"
    assert citations.row_state("CU-OPEN-1", QUEUE) == "open"
    assert citations.row_state("CU-NEVER-FILED", QUEUE) == "absent"


def test_disagreeing_readings_are_AMBIGUOUS_not_a_guess():
    """A sweep whose match is not unique stops instead of choosing."""
    q = "- [x] `q:CU-A-1` text | q:CU-B-1\n"
    assert citations.row_state("CU-A-1", q) == "ambiguous"
    assert citations.row_state("CU-B-1", q) == "ambiguous"


def test_two_rows_claiming_one_id_in_different_states_is_ambiguous():
    q = "- [ ] `q:CU-DUP-1` a | q:CU-DUP-1\n- [x] `q:CU-DUP-1` b | q:CU-DUP-1\n"
    assert citations.row_state("CU-DUP-1", q) == "ambiguous"


def test_a_row_with_only_one_of_the_two_readings_still_resolves():
    """The queues use both spellings; demanding both would make most real rows unresolvable."""
    assert citations.row_state("CU-F-1", "- [x] did it | q:CU-F-1\n") == "closed"
    assert citations.row_state("CU-A-2", "- [ ] `q:CU-A-2` open\n") == "open"


# ------------------------------------------------------------------ ruling state ----

LEDGER = ("- id: owner-ruling-live-2026-01-01\n  ref: owner-ruling-live-2026-01-01\n\n"
          "- id: owner-ruling-old-2026-01-01\n  ref: owner-ruling-old-2026-01-01\n"
          "  superseded-by: owner-ruling-live-2026-01-01 (2026-01-02; FULL)\n")


def test_ruling_state_reads_live_superseded_and_absent():
    recs = authority.records(LEDGER)
    rx = authority.SUPERSEDED_BY
    assert citations.ruling_state("owner-ruling-live-2026-01-01", recs, rx) == "live"
    assert citations.ruling_state("owner-ruling-old-2026-01-01", recs, rx) == "superseded"
    assert citations.ruling_state("owner-ruling-nope-2026-01-01", recs, rx) == "absent"


# ------------------------------------------------------------------ what it says ----

def test_it_is_silent_when_everything_resolves():
    assert citations.notes({"a": "open"}, {"r": "live"}, False) == []


def test_each_answer_worth_acting_on_produces_a_line():
    out = citations.notes({"a": "closed", "b": "absent", "c": "ambiguous"},
                          {"owner-ruling-x": "superseded", "owner-ruling-y": "absent"}, True)
    blob = "\n".join(out)
    for expect in ("CLOSED", "NO row", "AMBIGUOUS", "SUPERSEDED", "NO record"):
        assert expect in blob, expect
    assert f"first {citations.CAP}" in blob


def test_the_cap_line_appears_only_when_there_is_something_to_say():
    assert citations.notes({"a": "open"}, {}, True) == []


# ------------------------------------------------------------------ the door, as a subprocess ----

@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "Vault"
    (vault / "Queues" / "regions").mkdir(parents=True)
    (vault / "Notes").mkdir(parents=True)
    (vault / "Queues" / "regions" / "notes.md").write_text(QUEUE, encoding="utf-8")
    ledger = tmp_path / "rulings.log"
    ledger.write_text(LEDGER, encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"authority_log": str(ledger),
                               "topology": {"queues_dir": "Queues/regions"}}), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_CONFIG=str(cfg),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-memory.md"),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"))
    return dict(tmp=tmp_path, vault=vault, ledger=ledger, cfg=cfg, env=env)


def write_note(world, text, name="Notes/Position.md"):
    target = world["vault"] / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")         # the chore runs AFTER the write
    payload = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Write",
               "tool_input": {"file_path": str(target), "content": text},
               "cwd": str(world["tmp"])}
    p = subprocess.run([sys.executable, str(HOOKS / "chore.py"), "write"],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=world["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    if not out:
        return ""
    return (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext") or ""


def test_the_door_reports_a_closed_row_and_a_superseded_ruling(world):
    body = write_note(world, "Work continues under q:CU-DONE-1 and owner-ruling-old-2026-01-01.\n")
    assert "CU-DONE-1" in body and "CLOSED" in body
    assert "owner-ruling-old-2026-01-01" in body and "SUPERSEDED" in body


def test_the_door_is_silent_on_live_citations(world):
    assert write_note(world, "Live work: q:CU-OPEN-1 under owner-ruling-live-2026-01-01.\n") == ""


def test_the_door_is_silent_on_fenced_and_quoted_ids(world):
    """The negative control end to end, through the real hook rather than the pure function."""
    text = ("Example of the row format:\n\n```\n- [x] `q:CU-DONE-1` done | q:CU-DONE-1\n```\n\n"
            "> quoting an old note about owner-ruling-old-2026-01-01\n")
    assert write_note(world, text) == ""


def test_the_door_says_nothing_when_the_installation_has_no_ledger(world):
    """Unresolvable is not wrong. An install naming no ledger must not have every ruling citation
    reported as absent — that would be a fact about the install, not about the citation."""
    world["cfg"].write_text(json.dumps({}), encoding="utf-8")
    assert write_note(world, "Cites owner-ruling-nope-2026-01-01 only.\n") == ""


def test_the_door_survives_text_with_no_citations_at_all(world):
    assert write_note(world, "Just prose, no ids.\n") == ""


def test_sentence_punctuation_is_not_part_of_the_token():
    """★ A citation almost always sits at the end of a sentence. Sweeping the trailing `.` into the
    token makes it resolve against nothing, so a LIVE ruling reports as ABSENT — the most alarming
    answer this module can give, manufactured out of punctuation."""
    qs, rs, _ = citations.tokens("under owner-ruling-live-2026-01-01. And q:CU-OPEN-1.\n")
    assert rs == ["owner-ruling-live-2026-01-01"], rs
    assert qs == ["CU-OPEN-1"], qs
    qs, rs, _ = citations.tokens("see q:CU-A-1, owner-ruling-x-2026-01-01; done\n")
    assert qs == ["CU-A-1"] and rs == ["owner-ruling-x-2026-01-01"]


def test_the_door_says_nothing_when_the_installation_has_no_QUEUE_FILES(world):
    """The other half of 'unresolvable is not wrong', and the one that had no test: with no queue
    files, EVERY q-id would resolve as ABSENT and every write citing one would be flagged. That is
    a fact about the installation, not about the citation."""
    import shutil
    shutil.rmtree(world["vault"] / "Queues")
    assert write_note(world, "Cites q:CU-NEVER-FILED and nothing else.\n") == ""


def test_a_configured_but_missing_ledger_is_silent_too(world):
    """A named ledger that is not there is the same answer as no ledger: say nothing."""
    world["cfg"].write_text(json.dumps({"authority_log": str(world["tmp"] / "gone.log")}),
                            encoding="utf-8")
    assert write_note(world, "Cites owner-ruling-live-2026-01-01 only.\n") == ""


def test_the_normal_paths_never_reach_the_catch_all(world):
    """★ A guard whose removal changes no BEHAVIOUR can still be load-bearing — here the difference
    between "decided not to resolve" and "threw, and the catch-all swallowed it". Both return no
    notes, so a behavioural test cannot tell them apart; the log can. This asserts the door reaches
    its own exception handler on NONE of the ordinary paths, which is what stops a redundant-looking
    guard from being deleted later on the evidence that nothing changed."""
    # No ledger configured — but the vault's topology STAYS, or the queue half of this door goes
    # inert and the third assertion below would pass for the wrong reason.
    world["cfg"].write_text(json.dumps({"topology": {"queues_dir": "Queues/regions"}}), encoding="utf-8")
    assert write_note(world, "Cites owner-ruling-nope-2026-01-01.\n") == ""
    assert write_note(world, "Live: q:CU-OPEN-1.\n") == ""
    assert write_note(world, "Closed: q:CU-DONE-1.\n") != ""
    log = world["tmp"] / "state" / "chore.log"
    if log.exists():
        assert "citations\t" not in log.read_text(encoding="utf-8"), \
            "a normal write reached the citation catch-all — something raised that should not have"


def test_a_row_carrying_TWO_id_fields_is_ambiguous_not_decided():
    """★ The `len(fids) > 1` half of the identity guard, which had no test: a row line with two
    `| q:` fields has no single field reading, so its identity is not decidable. Without this the
    anchored id resolves CLOSED and the other ids resolve ABSENT — a confident wrong answer of
    exactly the kind this module refuses to give, on a row whose own identity is broken."""
    q = "- [x] `q:CU-A-1` text | q:CU-B-1 | q:CU-C-1\n"
    assert citations.row_state("CU-A-1", q) == "ambiguous"
    assert citations.row_state("CU-B-1", q) == "ambiguous"
    assert citations.row_state("CU-C-1", q) == "ambiguous"


def test_a_ruling_spanning_TWO_blocks_is_superseded_if_EITHER_carries_the_marker():
    """★ The `any()` in `ruling_state`, which had no test here. This format grows ADDENDA that
    repeat a ref, so a ref legitimately spans several blocks and the forward marker sits on one of
    them — `authority.py` uses `any()` for the same reason. With `all()` a genuinely superseded
    ruling reads as LIVE, which is the answer that lets a superseded authority go on being cited."""
    ledger = ("- ref: owner-ruling-two-2026-01-01\n"
              "  superseded-by: owner-ruling-new-2026-01-02 (2026-01-02; FULL)\n\n"
              "- ref: owner-ruling-two-2026-01-01 (ADDENDUM)\n  note: more\n\n"
              "- id: owner-ruling-new-2026-01-02\n  ref: owner-ruling-new-2026-01-02\n")
    recs = authority.records(ledger)
    assert citations.ruling_state("owner-ruling-two-2026-01-01", recs,
                                  authority.SUPERSEDED_BY) == "superseded"
    assert citations.ruling_state("owner-ruling-new-2026-01-02", recs,
                                  authority.SUPERSEDED_BY) == "live"


def test_an_oversized_queue_tree_goes_QUIET_rather_than_slow_or_wrong(world, monkeypatch):
    """The byte ceiling on the queue-tree read. Past it the q-half says nothing — a PARTIAL read
    would report rows that exist as ABSENT, which is worse than silence, and the whole point of the
    ceiling is that the cost cannot grow without bound as the queues do."""
    import citations as c
    big = world["vault"] / "Queues" / "regions" / "huge.md"
    big.write_text("x" * 4096, encoding="utf-8")
    env = dict(world["env"], GEDAECHTNIS_CITATION_MAX_QUEUE_BYTES="1024")
    saved, world["env"] = world["env"], env
    try:
        # With the ceiling below the tree size the closed row is no longer reported...
        assert "CU-DONE-1" not in write_note(world, "Cites q:CU-DONE-1.\n")
    finally:
        world["env"] = saved
    # ...and with the shipped ceiling it still is. Without this half the test would pass over a
    # ceiling that silenced everything.
    assert "CU-DONE-1" in write_note(world, "Cites q:CU-DONE-1 again.\n")
