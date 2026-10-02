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
| `profile` | `GEDAECHTNIS_PROFILE` | `public`. `atlas` turns on the four doors written for a vault with work queues, review pages and a shared roster (see [the doors](doors.md)) |
| `language` | `GEDAECHTNIS_LANGUAGE` | `en`; `de` and `latin` are the alternatives |
| `context_economy` | `GEDAECHTNIS_CONTEXT_ECONOMY` | `true` — the two read notices |
| `big_read_kb` | `GEDAECHTNIS_BIG_READ_KB` | `24` — the size in KB above which a whole-file read gets a notice |
| `session_name_pattern` | — | none — so no session is a managed session and nothing is reported to anyone. Set it to a regex over the `--name` you launch sessions with (a `seat` group names who hears back) and a session that finishes or runs out of context leaves one message for the session that started it |
| `notify_command` | — | none. Where a message goes besides the seat's mailbox: run as `<command> <seat>` with the message as JSON on stdin. The mailbox is written first, so a transport that is missing or slow costs a late message and never a lost one |
| `topology` | — | none. The shipped shape is `Global/` plus one folder per project. A vault that has grown work queues, per-lane outboxes, a cross-lane ledger, an artifact index, shared umbrella files or a folder with reserved file names names them here; each rule is off until it does. Keys: `non_region_tops`, `queues_dir`, `artifacts_index`, `ledger_dir`, `channels_dir`, `shared_append_files`, `stem_rule_dir`. A key that is present and unusable is named back to you at session start, not ignored |

**Chores that move or remove files.** Three Stop-hook chores change your files: compaction (moves
the oldest entries of a memory file over `max_memory_file_bytes` into an archive file), the Boot-file
roll (the same for a Boot file over its budget, inside its declared window only) and the worktree
sweep (removes clean, merged, unused worktrees under a repository's `.claude/worktrees/`, and moves
finished copy-on-write clones to `~/Downloads/To delete <date>/` with a ledger). On a
fresh install each only proposes: the next session start says what it would do, with details in
`proposals.json` in the state directory. To let one act, set `compaction_apply`, `boot_roll_apply`
or `worktree_sweep_apply` to `true` in the `limits` object of `config.json`. Each touches only its
own kind of file (compaction: files named for a memory role, never a queue, notice or other note),
moves at most `max_move_share` (a quarter) of a file and changes at most `max_files_per_pass` files
per pass, leaves a file alone if it changed while the pass was working, and writes into its commit
what it moved and the command that undoes it. The clone move is one rename of a whole
folder: it counts against `max_files_per_pass`, is judged once just before the move, and its undo
command is in that folder's `ledger.tsv`, not in a commit.

**Closing finished sessions.** `session_close_apply` in `limits` (on as shipped) lets the Stop hook
close a session it judges finished, by quitting that session's own `screen` or `tmux` session. It
sends no signal to a process. Which sessions count as finished, and which are left alone, is in the
At Stop row of [what runs when](hooks.md). Set it to `false` to get the list only;
`tools/sessions.py close --dry-run` prints what would close and why.

**Changing a limit: `tools/configure.py`.** `python3 tools/configure.py show` lists every limit with its
value, where the value comes from (shipped, the profile or `config.json`) and its class; `get KEY`, `set KEY VALUE`
and `unset KEY` read and change one. `set` refuses a key this package does not read and a value of the
wrong type, keeps a dated backup (`config.json.pre-<key>-<date>`, never overwritten), writes the file
in one rename, reads it back, and logs the change to `config.log` in the state directory. A session
uses it instead of editing `config.json` by hand.

**Which limits are yours.** Every limit is classed in the shipped `rules/limits.json` (`_key_class`).
A `live` limit ships on, or at its working value, and has an off-switch: any session may change it
with `configure.py set`. A limit whose effect costs money, cannot be undone, touches a learner's data or
changes which model runs work is the owner's switch (`owner:money`, `owner:irreversible`,
`owner:learner-data`, `owner:routing`), and so is one a recorded decision reserved to the owner
(`owner:ruled`); `configure.py set` refuses those unless you pass `--owner` from your own terminal, and
refuses `--owner` inside a Claude session. A new limit is added under the same rule: on, with an
off-switch, unless it is one of those classes.

**Landing a branch, and running a reviewed script, without a typed line.** Three tools make these
safe enough for a narrow allow rule:

- `python3 hooks/mergesha.py ff <branch> [--expect-main <sha>]`, run in the checkout that holds
  `main`, reads `main` and fast-forwards it to the branch tip in one command. It refuses when `main`
  is not an ancestor of the tip (main moved: restack), when `--expect-main` names another commit,
  when another session holds the merge window, and when the suite gate is on and no green full run
  covers the tip (the door's typed `SUITE_GATE_ALLOW=1` escape does not reach it). It then prints
  the `verify-merge` line.
- `bash tools/run_reviewed.sh <script.py> [args]` runs a Python script only when the SHA-256 of its
  bytes is in the reviewed ledger (`reviewed.tsv` in the state directory), and runs exactly the
  file it hashed, in an isolated interpreter (`python -I`): the `python` named in `config.json`,
  else the one running the tool, never one found in the script's tree. Any edit changes the sha,
  so an edited script is refused, with its sha and the ledger path. A review adds
  a row with `python3 tools/reviewed.py add <script> --review <record> --by <reviewer>`;
  `reviewed.py check <script>` prints the verdict. What it does not cover, named: a session that
  writes a row into the ledger itself (it is a plain file), what the script imports (a module
  planted beside it runs, as under `python script.py`), and the plugin's own code, which is trusted
  like every hook.
- `bash tools/apply-allow-rules.sh` merges the rules in `rules/allow-rules.json` into each
  repository's `.claude/settings.json` (the roster's repositories, the one the plugin lives in, and
  `--repo DIR`): the two tools above, `configure.py set|unset|get|show`, the four exact
  `sessions.py close` forms (never `close:*`, which would also allow `--replay --out <any file>`),
  and a deny rule for `configure.py … --owner`. Every rule names the plugin's INSTALLED path, never a
  path relative to the session's working tree, which the session could edit before running it. It prints the diff and writes nothing unless you pass
  `--apply`; then it keeps a dated backup beside each file, adds only the missing rules, and
  leaves every other key as it was. Running it again changes nothing.

`GEDAECHTNIS_CONFIG` moves the config file itself. `GEDAECHTNIS_NO_TRASH=1` makes the two tools that would
send something to the Trash leave it in place. `declined`, written only by `init.py --decline`, lists refusals.
