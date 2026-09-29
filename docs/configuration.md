# Configuration

Per setting: environment variable → `~/.claude/gedaechtnis/config.json` → default. `init.py` writes
that file with the vault it created; edit it to move the vault.

| key | environment variable | default |
|---|---|---|
| `vault` | `GEDAECHTNIS_VAULT` | `~/Gedaechtnis`, or the one directory under home carrying `Global/fleet-roster.md` |
| `roots` | `GEDAECHTNIS_ROOTS` | `~/Projects`, `~/projects`, `~/src`, `~/code`, `~/dev`, `~/repos`, `~/Developer`, `~/IdeaProjects`, `~/AndroidStudioProjects`, `~/Documents/GitHub`, `~/go/src` — two levels deep, nowhere else |
| `state_dir` | `GEDAECHTNIS_STATE_DIR` | `~/.claude/gedaechtnis` |
| `claim_tool` | `GEDAECHTNIS_CLAIM_TOOL` | a helper this package does not ship, so the region claim is off out of the box and says so at session start. Point this at a helper to turn it on |
| `auto_claim` | `GEDAECHTNIS_AUTO_CLAIM` | `true` |
| `auto_commit` | `GEDAECHTNIS_AUTO_COMMIT` | `true` — the Stop hook commits this session's vault writes |
| `compact_point` | `GEDAECHTNIS_COMPACT_POINT` | off — `"on"` turns on the compact ritual: the `/compact` check and the word-for-word re-injection after compaction |
| `compact_point_fresh_min` | `GEDAECHTNIS_COMPACT_POINT_FRESH_MIN` | `30` — how many minutes old a compact point may be when `/compact` is typed |
| `inject_rules` | `GEDAECHTNIS_INJECT_RULES` | `true` — the operating rules are injected at session start, in the vault or a marked project only. They also name the two questions a session ever asks you: the install screen and the no-memory question |
| `language` | `GEDAECHTNIS_LANGUAGE` | `en`; `de` and `latin` are the alternatives |
| `context_economy` | `GEDAECHTNIS_CONTEXT_ECONOMY` | `true` — the two read notices |
| `big_read_kb` | `GEDAECHTNIS_BIG_READ_KB` | `24` — the size in KB above which a whole-file read gets a notice |
| `session_name_pattern` | — | none — so no session is a managed session and nothing is reported to anyone. Set it to a regex over the `--name` you launch sessions with (a `seat` group names who hears back) and a session that finishes or runs out of context leaves one message for the session that started it |
| `notify_command` | — | none. Where a message goes besides the seat's mailbox: run as `<command> <seat>` with the message as JSON on stdin. The mailbox is written first, so a transport that is missing or slow costs a late message and never a lost one |
| `topology` | — | none. The shipped shape is `Global/` plus one folder per project. A vault that has grown work queues, per-lane outboxes, a cross-lane ledger, an artifact index, shared umbrella files or a folder with reserved file names names them here; each rule is off until it does. Keys: `non_region_tops`, `queues_dir`, `artifacts_index`, `ledger_dir`, `channels_dir`, `shared_append_files`, `stem_rule_dir`. A key that is present and unusable is named back to you at session start, not ignored |

**Chores that move or remove files.** Three Stop-hook chores change your files: compaction (moves
the oldest entries of a memory file over `max_memory_file_bytes` into an archive file), the Boot-file
roll (the same for a Boot file over its budget, inside its declared window only) and the worktree
sweep (removes clean, merged, unused worktrees under a repository's `.claude/worktrees/`). On a
fresh install each only proposes: the next session start says what it would do, with details in
`proposals.json` in the state directory. To let one act, set `compaction_apply`, `boot_roll_apply`
or `worktree_sweep_apply` to `true` in the `limits` object of `config.json`. Each touches only its
own kind of file (compaction: files named for a memory role, never a queue, notice or other note),
moves at most `max_move_share` (a quarter) of a file and changes at most `max_files_per_pass` files
per pass, leaves a file alone if it changed while the pass was working, and writes into its commit
what it moved and the command that undoes it.

`GEDAECHTNIS_CONFIG` moves the config file itself. `GEDAECHTNIS_NO_TRASH=1` makes the two tools that would
send something to the Trash leave it in place. `declined`, written only by `init.py --decline`, lists refusals.
