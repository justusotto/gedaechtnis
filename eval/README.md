# Gedächtnis eval — three benches: two harness-only, one complete

Three benches back the "Anthropic bar" claims in `.orchestration/review/gedaechtnis-everybody-2026-09-09/DESIGN.md`
§7.2:

- **`recall_bench/`** (WP6) — does the entry index beat today's grep-shaped `recall.py` at
  finding the right answer, in fewer bytes read into context? See `recall_bench/PREREGISTRATION.md`
  for the exact, dated claim this bench exists to decide.
- **`memory_eval/`** (WP8) — does Gedächtnis actually help a session do a cross-session task better
  than no memory at all, or than Claude Code's own built-in auto-memory? Three arms: `none` ·
  `automemory` · `gedaechtnis`.
- **`safety/`** (WP9, §7.2.3 — COMPLETE, not a skeleton) — do the doors ever let user data be
  destroyed, and do they ever refuse the legitimate twin of an attack? See below.

## Safety (WP9)

**What it proves.** `safety/cases.json` holds 50 adversarial commands/writes (git history
destruction, cache/media/Trash/anki_mining.db deletion, cross-lane writes, whole-file overwrites,
stale/foreign locks, malformed shared-surface rows, …) and their 50 **legitimate twins** — the
closest benign form of the same act (`git add -- file` next to `git add -A`, `git mv` next to a
plain `mv` out of the vault, an Edit next to a whole-file Write, …). `safety/run_safety.py` runs
every one of the 100 through the REAL `hooks/gate.py`, each in its own fresh throwaway sandbox (no
real vault, no real `~/.claude`, no network, no model). A green run means: every attack was denied
or refused-and-asked citing its rule, and not one legitimate twin was refused. `--mutant NAME`
(one of `vault_git` · `data_integrity` · `bash_partition` · `d1_whole_file_write` ·
`write_partition`) disables exactly that one rule by monkeypatch and re-runs — proving the case set
actually DEPENDS on the rule, not just that the rule exists (`eval/safety/mutant_gate.py`).

**How to run it:**

```sh
python3 eval/safety/run_safety.py                        # baseline — should print ALL GREEN, exit 0
python3 eval/safety/run_safety.py --mutant vault_git      # should print "mutant bit: N case(s)…", exit 0
```

Both write `results.json` (machine-readable, DERIVED — never hand-typed) and `results.md` (the
same table this prints to stdout) into `eval/safety/results/` by default (`--out DIR` to change
it); that directory is gitignored, same as `recall_bench`'s and `memory_eval`'s own `results/`.

**What a red means.** For the baseline run: `hooks/gate.py` no longer refuses something it used to
— a real regression in the doors, not a test problem; fix the door, then re-run. For a `--mutant`
run that prints `MUTANT DID NOT BITE`: no case in `cases.json` actually exercises that rule — the
mutant table or the case set needs a new case, not the door. `gedaechtnis/tests/test_safety_eval.py`
runs both shapes as part of the plugin's own test suite.

## What is built, and what is deliberately not

Per DESIGN.md §8 (the R7 sequencing decision): **WP6 and WP8 are DEFERRED until the owner's session
limit is comfortable — only their harness skeletons are written.** That means, today:

- Both benches run END TO END against a **stub** `claude` (`stub_claude.py`) that returns a
  deterministic canned answer for a known prompt and refuses everything else. **No model call, no
  network, anywhere in this folder**, unless you deliberately point `--claude` at a real binary —
  which nothing here does by default, and which is not yet a supported, tested path.
- `recall_bench`'s arm (a), grep, calls the real `recall.py` — that part is genuinely measuring
  something. Arms (b) index-first and (c) link-walk are not built; asking for them raises
  `NotImplementedError` naming exactly what is missing, rather than silently falling back to arm
  (a) and reporting a number for a claim nobody actually tested.
- `memory_eval`'s three arms are all real code paths (real `init.py`, real `recall.py`, a
  Claude-Code-shaped fake `MEMORY.md`), but the model in the loop is the stub — so today's numbers
  say "the harness's plumbing is correct and the positive control actually requires memory," not
  "Gedächtnis helps a real session." That second claim needs a real `--claude`, deliberately wired
  in later, per R7.

## Running both in dry-run (stub) mode

Nothing below reads or writes a real vault, `~/.claude`, or touches the network. Run from the
`gedaechtnis/` directory (or adjust the paths).

```sh
# WP8 — memory eval, all three arms, through the stub
python3 eval/memory_eval/run.py --dry-run

# WP6 — recall bench, arm (a), against a throwaway vault you supply
python3 eval/recall_bench/run.py --arm grep --vault /path/to/a/small/test/vault

# WP6 — arms (b)/(c): confirm they refuse rather than silently substituting
python3 eval/recall_bench/run.py --arm index --vault /path/to/a/small/test/vault   # -> NotImplementedError
```

`memory_eval/run.py --dry-run` needs no `--vault` — it builds its own throwaway HOME/repo/vault
per (arm, task) under a system temp directory (printed at the end, kept for inspection) and cleans
up nothing, since it is a temp directory to begin with. `recall_bench/run.py` always needs a
`--vault`; there is no built-in demo vault to avoid the bench accidentally scoring the same tiny
fixture every time someone forgets to pass one. `eval/recall_bench/questions.example.json` ships
as the example question set (`--questions`, default), matched to a small three-file demo vault —
see `gedaechtnis/tests/test_eval_skeletons.py` for the exact fixture that reproduces it.

## Both results tables are DERIVED, never typed

Every `run.py` writes `results.jsonl` (one JSON row per question or per arm×task) and then
`render_markdown()` — a pure function of those rows — produces `results.md`. Nothing hand-writes a
markdown table: run `render_markdown()`/`render_from_jsonl()` again on the same JSONL and the
output is byte-identical, which is exactly what the tests assert (`Global/Patterns.md`: "a
generator that carries a COPY of its authority document diverges silently — derive it").

## Token/usage accounting

`stub_claude.py` emits a final `USAGE_JSON: {...}` line with approximate token counts (prompt and
answer length over 4 — the same rough heuristic used across this fleet's cost dashboards, not a
real usage report). `eval/_stub_client.py` is the one place both benches parse that line, so a
future switch to a real `--claude` needs its usage-parsing written once, not twice.

## Wiring in a live arm later (not done here)

`--claude`/`--model` exist on both `run.py` scripts precisely so this does not require a rewrite:
point `--claude` at a real launcher that accepts `--model PROMPT` and (ideally) prints its own
`USAGE_JSON:` line in the same shape, or extend `_stub_client.call_claude`'s fallback (it already
degrades to "answer = whole stdout, tokens unmeasured" when no such line appears, rather than
crashing). That is a deliberate, reviewed act — not a flag flip — per R7.
