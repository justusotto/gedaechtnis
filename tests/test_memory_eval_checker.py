"""The memory-eval CHECKER — the thing that decides whether a day-N answer counts.

The first live run (`.orchestration/review/memoryeval-2026-09-10/RESULT.md`) scored task 003 with a
plain substring test, and it got the answer wrong in BOTH directions on the same task:

  * the `none` arm PASSED with a reply that had no memory at all and merely listed the right words
    inside a question about what convention the user wanted;
  * the byte-matched arm FAILED with a materially correct answer, because it wrote the token with
    markdown backticks.

Both transcripts are kept here VERBATIM as fixtures. A checker fix that is only tested against
sentences invented afterwards proves nothing about the specimen that caused it — and this file's
`test_the_old_rule_really_did_pass_it` is the positive control that the regression is real rather
than a story: it asserts the OLD rule accepted the very string the new rule rejects.

Everything here is a pure function of strings, so it runs in-process. `run.py` puts `eval/` on
`sys.path` for `_stub_client` and imports nothing from `hooks/`, so it does not carry the
`import config` hazard that forces the other suites into subprocesses.
"""
from __future__ import annotations
import importlib.util, json, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
RUN_PY = PLUGIN / "eval" / "memory_eval" / "run.py"
TASKS = PLUGIN / "eval" / "memory_eval" / "tasks"


def _load_run():
    spec = importlib.util.spec_from_file_location("_memory_eval_run", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


run = _load_run()


def task(name: str) -> dict:
    return json.loads((TASKS / name).read_text(encoding="utf-8"))


EXC = task("003-exception-rule.json")
DB = task("001-db-choice.json")

# ---------------------------------------------------------------------- fixtures ----
# Verbatim from live/none-003-exception-rule-day4.usage.json, the first run's `none` arm.
NONE_003 = ("No memory exists yet, so I don't have a recorded exception-handling rule for this "
            "project — could you tell me what convention you want followed (e.g., specific "
            "exception types, logging requirements, no bare excepts, etc.)?")
# Verbatim from live/automemory-003-exception-rule-day4.usage.json — correct, and scored FAIL.
AUTOMEMORY_003 = "Never use a bare `except`—always catch a specific exception class."
# Verbatim from live/gedaechtnis-003-exception-rule-day4.usage.json — correct, and scored pass.
GEDAECHTNIS_003 = ("The exception-handling rule this project follows: never use a bare except — "
                   "always catch a specific exception class.")
# Verbatim from live/none-001-db-choice-day3.usage.json and none-002 — the other two `none` cells.
NONE_001 = ("I don't have any record of this decision in memory yet — no database choice has been "
            "discussed or saved in this project.")
NONE_002 = ("No memory exists yet on this, and there's no repo content to check either. I don't "
            "have information about what the config file must be named — could you clarify?")


# ------------------------------------------------- the regression, both directions ----
def test_the_none_arm_mention_now_fails():
    """THE regression. This exact string passed task 003 in the first live run."""
    verdict = run.check_answer(EXC, NONE_003)
    assert verdict["success"] is False
    assert verdict["disqualified_by"], "it should be caught as a hedge, not merely miss a pattern"


def test_the_old_rule_really_did_pass_it():
    """Positive control for the test above: without it, "the new checker rejects this string" could
    just mean the string was always rejected and nothing was ever broken."""
    assert EXC["checker_must_contain"] in NONE_003, (
        "if this ever stops holding, the fixture has been edited and the regression it pins is gone")


def test_the_other_two_none_answers_also_fail():
    """They failed under the old rule too, by luck of phrasing; they must fail under the new one by
    RULE. A checker that only bites the one specimen it was written for is a patch, not a fix."""
    assert run.check_answer(EXC, NONE_002)["success"] is False
    for t, answer in ((DB, NONE_001), (DB, NONE_002)):
        assert run.check_answer(t, answer)["success"] is False


def test_a_correct_answer_with_markdown_backticks_now_passes():
    """The other direction of the same bug: this answer is right and the old substring test failed
    it, which is why the first run's byte-matched arm read 2 of 3 instead of 3 of 3."""
    assert EXC["checker_must_contain"] not in AUTOMEMORY_003, "the fixture must keep its backticks"
    assert run.check_answer(EXC, AUTOMEMORY_003)["success"] is True


def test_a_correct_plain_answer_still_passes():
    assert run.check_answer(EXC, GEDAECHTNIS_003)["success"] is True


# --------------------------------------------------------- stated, not mentioned ----
@pytest.mark.parametrize("answer", [
    "I have no record of an exception-handling rule for this project.",
    "There is no stored rule; do you want me to forbid a bare except?",
    "Possible conventions include (e.g., no bare except, typed exceptions).",
    "What rule should you use — a bare except, or specific classes?",
])
def test_hedges_cannot_pass_however_right_the_words_are(answer):
    assert run.check_answer(EXC, answer)["success"] is False


def test_the_rule_must_be_asserted_not_named():
    """`checker_must_match` is the clause that separates naming the topic from stating the rule."""
    named = "The project has an opinion about the bare except pattern in retry loops."
    assert EXC["checker_must_contain"] in named
    v = run.check_answer(EXC, named)
    assert v["success"] is False and v["missing_patterns"]


# ------------------------------------------------------------------- the swap ----
def test_the_swapped_side_passes_only_by_following_the_swap():
    assert run.check_answer(EXC, "Always catch BaseException so nothing escapes.", swapped=True)["success"] is True
    assert run.check_answer(EXC, GEDAECHTNIS_003, swapped=True)["success"] is False


def test_naming_the_true_fact_fails_the_swapped_side_by_construction():
    """No task author writes this clause: `checker_spec` appends the true fact to the swapped
    side's `must_not_match`, so an answer that hedges toward the truth cannot score a swap follow."""
    both = "Always catch BaseException, though a bare except is also acceptable."
    v = run.check_answer(EXC, both, swapped=True)
    assert v["success"] is False and v["disqualified_by"]
    assert run.checker_spec(EXC, swapped=True)["must_not_match"], "the clause must actually be added"
    assert not [p for p in run.checker_spec(EXC, swapped=False)["must_not_match"]
                if "BaseException" in p], "the true side must NOT get the mirrored clause"


# ------------------------------------------------------------- normalisation ----
def test_normalisation_strips_markdown_and_collapses_whitespace():
    assert run.normalise_answer("a  `b`\n *c* ") == "a b c"


def test_normalisation_does_not_fold_case_itself():
    """The substring test folds case; the regexes carry their own `(?i)`. If `normalise_answer`
    folded case too, a task author's case-sensitive pattern would silently stop being one."""
    assert run.normalise_answer("PostgreSQL") == "PostgreSQL"


# --------------------------------------------------------------- task validation ----
def test_every_shipped_task_validates():
    for p in sorted(TASKS.glob("*.json")):
        run.validate_task(json.loads(p.read_text(encoding="utf-8")), str(p))


def test_a_task_whose_memory_sentence_fails_its_own_checker_is_refused():
    """The substrate this harness writes IS the memory. If it would not pass, no arm could pass the
    cell honestly, and the resulting FAIL would be the task's, not the arm's."""
    bad = json.loads((TASKS / "003-exception-rule.json").read_text(encoding="utf-8"))
    bad["memory_sentence"] = "We talked about exception handling on day 1."
    with pytest.raises(ValueError, match="memory_sentence"):
        run.validate_task(bad, "fixture")


def test_a_task_whose_swap_leaks_the_true_fact_is_refused():
    bad = json.loads((TASKS / "001-db-choice.json").read_text(encoding="utf-8"))
    bad["swap"]["memory_sentence"] = "The project's database is MySQL, not PostgreSQL."
    with pytest.raises(ValueError, match="appears in the swap block"):
        run.validate_task(bad, "fixture")


def test_a_task_whose_prompt_leaks_the_swap_fact_is_refused():
    bad = json.loads((TASKS / "001-db-choice.json").read_text(encoding="utf-8"))
    bad["day_n_prompt"] = bad["day_n_prompt"] + " (it is not MySQL)"
    with pytest.raises(ValueError, match="appears in the true task"):
        run.validate_task(bad, "fixture")


def test_a_task_with_no_swap_block_is_refused():
    bad = json.loads((TASKS / "001-db-choice.json").read_text(encoding="utf-8"))
    del bad["swap"]
    with pytest.raises(ValueError, match="swap"):
        run.validate_task(bad, "fixture")


def test_the_valid_task_is_the_negative_control_for_all_four_refusals():
    """Each refusal above mutates ONE field of a task that validates cleanly here; without this,
    a refusal could be firing on something else entirely."""
    run.validate_task(json.loads((TASKS / "001-db-choice.json").read_text(encoding="utf-8")), "ok")


# ---------------------------------------------------------------- byte matching ----
def test_padding_hits_the_target_byte_count_exactly():
    t = json.loads((TASKS / "001-db-choice.json").read_text(encoding="utf-8"))
    for target in (600, 2454, 3071):
        assert len(run.automemory_text(t, target).encode("utf-8")) == target


def test_the_padded_file_still_carries_the_fact_and_no_other_task_answer():
    t = json.loads((TASKS / "001-db-choice.json").read_text(encoding="utf-8"))
    text = run.automemory_text(t, 3000)
    assert t["memory_sentence"] in text
    for other in sorted(TASKS.glob("*.json")):
        o = json.loads(other.read_text(encoding="utf-8"))
        if o["task_id"] != t["task_id"]:
            assert o["fact"].casefold() not in text.casefold(), (
                f"filler leaks {o['task_id']}'s answer into another task's memory file")


def test_without_a_target_the_file_is_the_short_unpadded_one():
    """Negative control for the two tests above: byte-matching is a CHOICE, and the unpadded file
    is what the first run used — so the two runs' arms stay distinguishable."""
    t = json.loads((TASKS / "001-db-choice.json").read_text(encoding="utf-8"))
    assert len(run.automemory_text(t).encode("utf-8")) < 300
