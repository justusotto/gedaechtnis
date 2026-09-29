# What has been measured, and the limits

Each row says when it was measured and how to run it again. Rows dated 2026-09-23 were re-run that
day; the one row that was not says why. The test-suite row is a count, not a claim about quality. `CHANGELOG.md` and `eval/` carry the full working of each
number, including runs that failed their own pre-registered bar.

| what | result | when | how to run it |
|---|---|---|---|
| Test suite | 3,101 tests: 3,088 passed, 11 skipped, 2 failed. Both failures are the publish check refusing an old tag in the release clone, not the tree. Four of the passed tests also reported a teardown error: the suite checks that the user's real files did not change while it ran, and another program on the machine wrote one. Errors are counted separately from tests, so the numbers do not add up to 3,101 | 2026-09-30 | `python3 -m pytest tests -q` |
| Mutation checks | Rules carry a positive and a negative control, and a control counts once turning its rule off makes it fail. The five rules the safety eval can turn off each failed cases when turned off (12, 9, 7, 3 and 11 cases). A separate review the same day turned off each of the 13 rules added since 2026-09-17 and all 13 controls failed. It also found two exceptions: the two parsers that read `@`-import lines had no test that fails when the `@` requirement is dropped. Both got one the same day (`tests/test_maintenance.py`, `tests/test_boot_check.py`) | 2026-09-23 | `python3 eval/safety/run_safety.py --mutant vault_git` (and `data_integrity`, `bash_partition`, `d1_whole_file_write`, `write_partition`) |
| Safety eval | 52 adversarial commands denied and their 52 legitimate twins allowed, through the real gate in a throwaway sandbox | 2026-09-23 | `python3 eval/safety/run_safety.py` |
| Two writers, one file | 1,000 of 1,000 appends kept with the hooks, 28 of 1,000 without, at 500 appends per writer. The number without the hooks depends on timing: a second run the same evening kept 37 | 2026-09-23 | `python3 tests/sim_two_writers.py --n 500`, then with `--no-doors` |
| Context cost | `claude plugin details` puts the always-on cost at ~854 tokens and labels the hooks *harness-only — no model context cost*. The 854 is the commands and agents being listed to the model | 2026-09-23 | `claude plugin details gedaechtnis` |
| Recall ranking | 42 of 60 held-out questions about the author's vault answered from 8.4 KB read, at the shipped constant, with the constant frozen before the questions were drawn (2026-09-11). The same 60 questions against the same vault twelve days later: 40 and 39 of 60 in two back-to-back runs, from about 10.7 KB. With the vault's `topology` left out of the config, the same run scores 23 of 60 from about 994 KB, because its queue folders are then searched as memory | 2026-09-11, re-run 2026-09-23 | `python3 eval/recall_bench/run.py --arm grep --vault <vault> --questions <questions.json> --limit 3 --max-bytes 6000`; the question set and the pre-registered bar: `eval/recall_bench/PREREGISTRATION.md` |
| Against a plain memory file | Sessions with the plugin answered 36 of 36 cross-session tasks. A byte-matched 3 KB file pasted into the prompt answered 35 of 36. No memory answered 0 of 36. 192 real sessions with `claude-sonnet-5` at effort `low`, $4.76. Not re-run: the result is already inside the run's noise floor (see Limits), so a repeat would not change what the row can claim | 2026-09-10 | `python3 eval/memory_eval/run.py --live --claude "$(command -v claude)" --model claude-sonnet-5 --effort low --ceiling-usd 10` |
| The install path | A fresh `HOME` loads the plugin through the real symlink, at zero cost. The manifest outage check has 65 test cases, all passing | 2026-09-23 | `python3 eval/fresh_home/run.py --live --claude "$(command -v claude)" --model claude-sonnet-5 --effort low --ceiling-usd 1`; `python3 -m pytest tests/test_outage_check.py -q` |
| A 90-day install, simulated | Drives the real hooks over 90 simulated days of a low-load user, no model calls. At that load the three script-checkable criteria pass: cleanup falls due on day 15, is proposed on day 89 and applies on day 90 once switched on; the boot chain stays inside its budget. The 90 days all happen on one calendar date, so it says nothing about time-based triggers. Its live half (real sessions over a garden-notes vault, with a control arm) attributed one extra question to the plugin by word count and none by reading the transcripts | 2026-09-23 | `python3 eval/dadtest/run.py --load low` (the simulator it builds on: `eval/simulator/run.py`) |
| In real use, one machine | 59 denies, 229 session starts and 382 partition warnings in the live logs, counted at 23:08. The logs do not tell ordinary sessions apart from test and probe runs, and some of the denies are probes | 2026-09-08 to 2026-09-23 | `wc -l deny.log session.log partition.log` in the state directory |
| In real use, one machine, later | 487 lines in `deny.log`: 347 shell commands, 114 wake-up messages to other sessions refused plus 24 let through with a stated override reason, 1 write, 1 sub-agent call; the largest groups are the delete door (142) and the worktree-placement door (108). Same caveat: test and probe runs are in the count | 2026-09-08 to 2026-09-29 | `cut -f2 deny.log \| sort \| uniq -c` in the state directory |

## Limits, and what has not been measured

- **It is not shown to beat a 3 KB memory file** on the task shape the memory eval used. The
  one-task gap is inside the run's noise floor, and each test vault held one entry, so recall had
  nothing to rank. Whether it helps where a flat file cannot be pasted into the prompt (multi-entry
  recall, a vault too large to fit, a fact recorded many sessions before the question) is untested,
  as is whether it beats Claude Code's own auto-memory: the eval's file arm does not load it.
- **The write-partition rule is in `warn`, not `deny`**, until its log has been read over a week.
- **Unattended splitting is off** (`split_auto_apply`), on a measurement, not on caution. A dry run
  over a real 1,133-entry vault would have created five folders and named four of them after
  document boilerplate, the words that vault puts in every entry, instead of after a subject. A
  breadth rule now strips those words before clustering, but it needs at least three folders that
  vote, so a one- or two-folder vault, which is what installing this creates, gets no protection
  from it. `--dry-run` works with the knob off, moves nothing, and prints exactly what `--auto`
  would have done. Check the names in your own vault that way first.
- **Lesson surfacing is off, at a write and at boot** (`lesson_push_enabled`,
  `lesson_push_boot_enabled`). Three designs were built and measured; none met its own bar, so all
  ship off. `eval/lesson_push_replay.py` (`--boot` for the third) re-measures them.
- **The context notice tells you where you are and does nothing else.** It cannot compact, summarise,
  hand off or stop anything, and it never blocks a tool call. What happens at the line is your decision.
- **Only the project shape has been measured.** The starter files' headings now come from one
  question at install (`--shape project|notes|study|other`), because readers in both arms of a test
  against a personal log called the old `Shipped / In flight / Next` boilerplate that did not fit.
  Re-tested against the same log, that complaint is gone in both arms. But `project` is the shape
  every published measurement here was taken over, it is what is used when nothing answers the
  question, and `study` has not been probed at all. All four differ only in headings; nothing
  enforces them and they are yours to rewrite.
- **In progress:** English file names on disk; adding your own folders without adopting the layout.
