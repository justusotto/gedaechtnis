# Changelog

What changed, when, and what it means for someone running this plugin. Newest first.

Dates are the day the work landed. Versions follow the `version` field in
`.claude-plugin/plugin.json`; this is pre-1.0 software, so a minor version may still change
behaviour — where it does, the entry says so under **Changed** and names what to re-check.

Sections used: **Added** · **Changed** · **Fixed** · **Removed** · **Known limits**.

## Unreleased — package hygiene

The plugin was still assuming its parent directory. Three things depended on the private repo it
grew inside; a copy of the plugin alone hit them silently. That is what this entry closes.

### Added

- **`eval/pricing.json` — the price table the package ships.** A live eval run prices every call
  and refuses to start when it cannot; until now the only table was the fleet's
  `scripts/concilium.py`, resolved as a sibling of the plugin directory, so a published copy could
  not price a run at all. The table now ships inside the package and is the DEFAULT. The override
  order is unchanged and still wins: `--pricing PATH`, then `$GEDAECHTNIS_PRICING_PY`, then the
  packaged file. Both shapes load — a `.py` module exposing
  `PRICE`/`PRICE_ASOF`/`price_usage`/`resolve_price_key`/`usage_from_model_usage`, or a JSON table
  in the packaged shape.
- **`eval/pricing.py` — the loader, and the drift guard.** The shipped table is a DERIVED COPY, and
  it says so: its `provenance` block names the module it came from, the upstream pricing page, and
  the date it was copied. A copy of an authority document diverges silently, so the copy is guarded
  rather than trusted — `python3 eval/pricing.py --check <path to that module>` compares the
  numbers, the aliases, the as-of date, AND what a probe usage block prices to under each
  implementation (so a formula that drifts is caught, not only a number), and exits 1 listing every
  difference. Both sides of that check have positive controls in the test suite.
- **A facts line for the region claim.** The claim hooks call a region-claim helper that this
  package deliberately does not ship — it is a fleet asset, and copying a coordination mechanism
  forks it. So on an ordinary install the region claim is **OFF**, which is supported; what is not
  supported is OFF looking like ON. `config.claim_tool_state()` now returns which of four states an
  install is in (`ready` · `off-disabled` · `off-missing` · `off-no-helper`) with a sentence saying
  so; `hooks/claim.py` prints it at SessionStart wherever a claim would otherwise have been taken
  and was not, and `/gedaechtnis-status` prints it unconditionally. Outside a region there is
  nothing to claim and nothing is printed.
- **This changelog.**

### Changed

- `memory_eval/run.py` and `fresh_home/run.py`: the `--pricing` default is now the packaged table
  rather than `<plugin>/../scripts/concilium.py`. Nothing that passed `--pricing` or set
  `$GEDAECHTNIS_PRICING_PY` changes behaviour.
- The live-eval test module no longer skips wholesale where the fleet's pricing module is absent.
  Only the handful of tests that speak specifically about THAT module skip; the rest run over the
  packaged table, which is the path a published copy takes.

### Fixed

- **The test suite is green in a copy of the plugin alone.** Outside the repo it was written in,
  `tests/test_memory_eval_live.py::test_live_without_claude_refuses` and
  `::test_the_stub_fail_switch_is_off_by_default` failed, because the run refused on the missing
  price table before reaching the thing each test was about. Both were symptoms of the dependence
  above and are fixed by removing it, not by relaxing an assertion.

### Known limits

- The packaged table is only as fresh as its `price_asof`. A run quoting a later period should
  re-read the upstream pricing page and re-derive — a price date is part of a pricing answer.
- With no claim helper, one-writer-per-region is a convention this plugin states and does not
  enforce. Coordinate by hand, or point `claim_tool` at a helper.

## 0.1.0 — 2026-09-08

First version. The plugin turns the deterministic rules of a persistent-memory vault into
machinery; rules needing judgment stay prose.

### Added

- **PreToolUse locked doors** (`hooks/gate.py`): vault git law (no `git add -A`, no bare commit, no
  `--amend`, path-limited commits), model/effort pinning on programmatic launches, data integrity
  (cache files, `rm`), reserved-stem protection, and append-only discipline on shared surfaces.
  Every denial cites the entry it enforces, so a session learns the reason rather than only meeting
  a wall.
- **PostToolUse chores** (`hooks/chore.py`): artifact index, trailing-newline repair on queue files,
  SHA checks.
- **SessionStart facts** (`hooks/session_start.py`): lane and write partition, vault HEAD and dirty
  paths, boot cost of the @-import chain, unanswered ledger rows, a by-name `~/Downloads` sweep.
- **Stop-time auto-commit** (`hooks/commit.py`), scoped to the lane's declared paths and nothing
  else; an unknown lane commits nothing, ever.
- **Region claim** (`hooks/claim.py`), taken at SessionStart and released at Stop, via an external
  helper.
- **Worktree hooks** (`hooks/worktree.py`): copy-on-write clones.
- **Context-economy notices** (`hooks/context_economy.py`): non-blocking re-read and big-read
  notices; both always allow.
- **The vault side:** `recall.py`, `importer.py`, `logstore.py`, `views.py`, `ledger.py`,
  `retention.py`, `init.py`, `names.json` (role-file display names in `en`/`de`/`latin`).
- **Commands** `/gedaechtnis-status`, `/gedaechtnis-recall`, `/gedaechtnis-debrief`, and the
  `memory-reviewer` / `memory-debriefer` agents.
- **`tools/publish_check.py`** — refuses to publish a tree carrying a person with it (home paths,
  emails, names, private repo names, anything shaped like a credential) and, for a mirror clone,
  inherited tags. No allowlist; it scans itself.
- **The eval harness** (`eval/`): the memory eval (four arms, dry-run and live), the recall bench,
  the fresh-HOME probe, and the safety eval — 52 adversarial commands with 52 legitimate twins,
  plus mutant runs that turn one rule off and show the case set fail without it.

### Known limits

- Claude Code fires `Stop` when the main agent finishes responding — every turn, not only at the
  end of a session. The claim pair is therefore honest about locks (nothing is left held by a dead
  session) but its claim covers the turn that took it, not the whole session.
- The memory eval's `automemory` arm does not exercise Claude Code's own memory loader, and a live
  run takes one sample per (arm, task). No number from it ranks two arms differing by one task.
