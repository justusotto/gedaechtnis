# Authoring a memory-eval task

A memory-eval task is a **two-session experiment**. On day 1 a session decides and records one
small fact. On a later day a *different* session, with no shared conversation state, must answer a
question that only that recorded fact answers. Four arms differ in one thing only: what memory
substrate the day-N session gets.

The tasks must **not** be written by whoever built the harness — a builder writes tasks his own
mechanism happens to be good at. Hence this file: the spec an outside author works from.

## Ground rules

1. **The fact is something a prior session would really have recorded** — a decision, a config
   name, a threshold, a rule, an identifier. Draw the *material* from a real region of the vault
   you were assigned, then restate it as a self-contained project fact. The fixture repo is empty
   and the vault the harness builds is a throwaway called `Demo`; nothing of the real vault reaches
   the session. So the task must never depend on the model having seen the real vault.
2. **The `none` arm must be able to FAIL it.** If a competent model with no memory could guess the
   answer from the day-N prompt, the general shape of software projects, or the empty repo, the
   task is worthless. Prefer facts that are *arbitrary or counterintuitive*: a specific filename, a
   specific number, an identifier, a proper noun, a rule that a sensible default would contradict.
   Never `PostgreSQL`-shaped defaults, never "use type hints", never "write tests".
3. **The answer must be STATED, not mentioned.** A day-N reply of the form "I have no record —
   could you tell me what convention you want (e.g. …, no bare excepts, …)?" mentions the right
   words and must score FAIL. That is why every task carries a `checker_must_match` regex that
   requires the fact asserted *as the project's rule/value*, and why the harness applies a shared
   list of hedge patterns that disqualify an answer regardless of what words it contains.
4. **One short answer.** The day-N prompt asks for one sentence, or for the value alone. Long
   answers make checkers fragile.
5. **The fact must be a distinctive token.** `checker_must_contain` is a plain substring test on
   the answer; pick a token a model would not emit by accident (`settings.toml`, `MODEL_ID
   1850100010`, `U+02BC`, `casefold`), not a common word.
6. **Nothing leaks between tasks.** No task's fact may appear in another task's day-N prompt (the
   harness refuses to run if one does).

## The swap

Every task also carries a `swap` block: the **same** task with the recorded fact replaced by a
plausible *opposite or alternative*. The swapped arm gives the day-N session that altered memory.
It passes only if the answer **follows the swap**. This is what separates "the session read the
memory" from "the model guessed well" — a swapped cell that still returns the true fact proves the
memory was never read.

The swap fact must be **distinct from the true fact and absent from the true task's text**, and
vice versa. The harness automatically adds the true fact to the swapped checker's
`must_not_match`, so an answer that names the truth fails the swapped cell by construction.

## File format

One JSON file per task, named `NNN-short-slug.json`, UTF-8, no comments.

```json
{
  "task_id": "004-apostrophe-regime",
  "title": "Apostrophe regime at the ingest boundary",
  "author": "author-a",
  "source_region": "Mnemosyne/UkrainianVocab",
  "positive_control": false,
  "description": "One or two sentences: what the task is, and WHY the none arm cannot guess it.",

  "day1_prompt": "Day 1: <the session decides and records the fact, in one or two sentences>.",
  "memory_sentence": "<the recorded fact as one declarative sentence — this is what goes into the memory substrate AND what the stub answers with in a dry run, so it must itself satisfy checker_must_match>",
  "canon_heading": "<a short heading for the vault entry, no markdown>",
  "fact": "<the distinctive token>",

  "day_n": 5,
  "day_n_prompt": "Day 5: <the question>. Answer in one sentence.",

  "checker_file": "apostrophe-answer.txt",
  "checker_must_contain": "<the distinctive token, exactly as it must appear>",
  "checker_must_match": ["(?i)\\bregex asserting the rule\\b"],
  "checker_must_not_match": [],

  "swap": {
    "day1_prompt": "Day 1: <the same session deciding the ALTERNATIVE>.",
    "memory_sentence": "<the alternative as one declarative sentence>",
    "canon_heading": "<heading for the alternative>",
    "fact": "<the alternative's distinctive token>",
    "checker_must_contain": "<the alternative token>",
    "checker_must_match": ["(?i)\\bregex asserting the alternative\\b"],
    "checker_must_not_match": []
  }
}
```

### Field notes

- `day_n` — any integer > 1. It only appears in labels.
- `checker_file` — a unique filename per task; the harness writes the day-N answer there.
- `checker_must_match` — a list of Python `re` patterns; **all** must find a match in the answer.
  This is the "stated, not mentioned" requirement. Write it so the *memory sentence itself* matches
  it (the dry run asserts this), and so a hedging non-answer does not. Escape backslashes for JSON.
- `checker_must_not_match` — optional extra disqualifiers on top of the harness's shared hedge
  list. Use it to reject the *other* plausible value.
- `positive_control` — leave `false`; task 001 already carries it.

### Self-check before you hand a task over

- `python3 -c "import json;json.load(open('NNN-....json'))"` parses.
- `re.search(checker_must_match[i], memory_sentence)` finds a match, for every i.
- `checker_must_contain in memory_sentence` is true; same for the swap block.
- The true `fact` does **not** appear anywhere in the swap block's text, and the swap `fact` does
  not appear anywhere in the true task's text.
- You can say, in one sentence, why a model with no memory cannot answer the day-N prompt.
