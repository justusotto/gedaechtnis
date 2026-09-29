# Why hooks, not prompts

## The problem

A rule written in a prompt or a `CLAUDE.md` file works only if the model remembers it at the
moment it matters. Most of the time it does. Sometimes it does not: a long session, a compaction,
a rule read 100,000 tokens earlier, or a command that looks harmless in context. A notes file that
many sessions write to collects rules like "never `git add -A` here" or "never `--amend`" for this
reason, and each one usually records a time it was broken.

Adding more rules to the prompt has a cost too. Every session reads them again at start, whether
or not it will touch the files they protect.

## The choice

For the rules a script can check without judgment, this plugin uses a `PreToolUse` hook instead of
prose. The hook sees the tool call before it runs. If the call breaks the rule, the hook refuses it
through Claude Code's documented hook decision and returns the reason, citing the note it enforces.
The model does not have to remember the rule; it is told at the moment it matters, and it gets the
reason, not only a refusal.

Rules that need judgment ("is this entry in the right file?", "does this contradict an earlier
decision?") stay prose, and are checked by the read-only reviewer agent when asked.

## What it cost, and what was measured

All figures are from one machine, the author's, and are dated.

- **What prose rules cost at session start.** Over 157 interactive sessions in the 30 days to
  2026-09-19, a session had read a median of 141,965 tokens before its first action. The
  always-loaded memory files were 21–30% of that, depending on the project.
- **What the hooks cost.** `claude plugin details` (2026-09-23) lists the hooks as *harness-only —
  no model context cost*. The plugin's always-on cost, about 854 tokens, is its commands and agents
  being listed to the model.
- **Whether reminders would have been enough.** A design that surfaced past lessons at session
  start was built and measured three times. Of seventeen lessons known to have been broken again, one
  would have been in the start-of-session block of the session that broke it. That design ships
  switched off (see [measurements](measurements.md)).
- **What the doors refused.** `deny.log` holds 487 lines from 2026-09-08 to 2026-09-29. The largest
  groups: `rm` outside a scratch folder (142), a git worktree placed outside the repository's
  `.claude/worktrees/` (108), wake-up messages to idle sessions that would have been expensive to
  wake (114), and commands that would have swept, amended or removed vault files or history (35). The log
  does not separate real sessions from test and probe runs, so these are upper bounds on real
  refusals.
- **Whether the doors refuse the right things.** A safety eval runs 52 harmful commands and their 52
  closest harmless twins through the real gate in a throwaway sandbox. On 2026-09-23 all 52 were
  refused and none of the twins. Turning off one rule at a time made that rule's cases fail, which
  shows the cases depend on the rule.

## The limits

- **A hook cannot judge.** It matches what a script can decide. A guard that guesses produces false
  refusals, and a false refusal costs work that then does not happen. Rules that need judgment stay
  prose.
- **A hook sees tool calls only.** It cannot stop a wrong sentence in a note, only a destructive or
  misplaced write of one.
- **New doors start in warn mode.** Most of the newer doors log what they would have refused and
  only refuse from a date you set, so you can read their log first.
- **A broken plugin fails quietly.** If the plugin's manifest fails validation, Claude Code loads
  none of its hooks and does not say so. On 2026-09-09 that lasted fifteen hours before a review
  noticed. An optional check outside the plugin covers this ([install](install.md)).
- **One machine.** The figures above come from one person's use on macOS. Linux and Windows run the
  same code paths only in tests so far ([platforms](platforms.md)).
