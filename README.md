# Gedächtnis

A Claude Code plugin for people who keep a markdown knowledge base that every session reads.
Such a knowledge base collects rules: "never `git add -A` in the vault", "never `--amend`". Every
session reads them, and one session breaks one anyway. This plugin turns the rules a script can
check into hooks. A `PreToolUse` gate refuses the command and cites the note it is enforcing, so the
session learns the reason, not only the wall. Rules that need judgment stay prose.

It also does the bookkeeping. At the end of a session it commits the vault files that session wrote,
only those, and only inside the paths that session may write. At the start of the next one it prints
which project, which vault, and what is uncommitted. The vault is yours and is not shipped: your
notes live in their own git repo, and nothing here creates or distributes one.

## Install

```sh
git clone <this repo> ~/src/gedaechtnis && python3 ~/src/gedaechtnis/init.py
```

Most people have more than one project, so `init.py` finds them and asks which to set up:

```
Gedächtnis found 5 projects Claude Code has worked in.
    #  project                    last session   region it would get
    1  src/ledger-api             today          ledger-api
    2  src/website                2 days ago     website
    3  code/old-prototype         210 days ago   old-prototype   (no session in 90 days)

Give these projects a memory?  [Enter = all]  [numbers, e.g. 1 3 4]  [none]  [later]
```

Then one question about what the memory is for, because the starter files' section headings depend
on the answer and nothing else can know it:

```
What is this memory mostly for?
  [1] a software project
  [2] notes, a journal, a log
  [3] studying or learning something
  [4] something else
```

`--shape project|notes|study|other` answers it without asking. **Honestly: `project` is the shape
that has been measured.** It is what `Shipped / In flight / Next` was written for, it is what the
published dad-test and simulator numbers were taken over, and it is the fallback whenever nothing
answers — a script, `--yes`, a pipe — which the run prints rather than assumes. `notes` exists
because the dad test measured the alternative failing: over a vault of one person's allotment
notes, day 90 called the shipped files *"a garden log wearing a project's file structure"* and the
three headings *"empty boilerplate"*, in both arms. `study` ships in the same table and has not
been probed. All four differ only in the headings and opening lines of `Position.md` and `Map.md`;
the file names, the roles and every rule are the same.

Enter takes every project you have worked in during the last 90 days. Numbers take exactly those;
the rows you did not name are recorded as declined and you are not asked about them again. `none`
writes nothing. `later` writes nothing and records nothing.

Then restart Claude Code (or `/reload-plugins`) and open it in the repo. The first session prints a
facts block naming your vault and lane. Nothing to build, no dependency beyond Python 3.9+.

**Where the list comes from.** `init.py` reads one key of `~/.claude.json`, `projects`, and never
writes that file; reads the modification times of `~/.claude/projects/*/` as a "last session" signal;
asks `git config --global` for the repos git already tracks; and looks two levels deep, no deeper,
under the directories in `roots`. It does not scan your disk, read your shell history, or ask
Spotlight, and it skips temp directories, agent worktrees, scratchpads, your home directory itself
and the vault. If your repos live elsewhere: `"roots": ["~/src", "~/wherever-mine-are"]`.

**Other ways to run it.** `--repo DIR` sets up one project, no screen. `--discover` shows the screen
anyway. `--decline` records that a project wants no memory. `--offer-declined` lists those again.
`--all` ignores the declined list once. `--dry-run` plans and writes nothing. `--yes` takes the default.

### Installing from a plugin directory

If you installed Gedächtnis from a plugin directory or marketplace, Claude Code already loads it.
Nothing is written anywhere until you say yes: the plugin does nothing to a project until that
project has a memory. To give one a memory, run `init.py` from the installed copy (the session's
facts block names the command), or answer the session's one question. Run from an installed copy,
`init.py` does not add the `~/.claude/skills` symlink and does not write `~/.claude/settings.json`.

Use one install method, not both. If you also clone this repository and run `init.py` from the
clone, the symlink it adds is a second copy of the same plugin, and every hook runs twice.

### What it creates

A vault at `~/Gedaechtnis`, `git init`-ed. If that directory does not exist, it adopts a directory
one level under home that is already a vault — what proves it is the file `Global/fleet-roster.md`
inside it, never the folder's name. Where two directories prove it, neither is adopted and you name
one. Then, per chosen project: a folder in the vault with six starter files; a *lane*, the writer
identity its sessions use, declared in `Global/fleet-roster.md` and in a `.atlas-lane` marker in the
repo; an `@`-import line in the repo's `CLAUDE.md`; `~/.claude/gedaechtnis/config.json`; and the
symlink `~/.claude/skills/gedaechtnis` that loads the plugin. It creates only what is absent and never
overwrites a file: run it again and every line reports `kept`.

```
~/Gedaechtnis/                  the vault: one git repo, yours
├── Global/
│   └── fleet-roster.md         which repo writes where, and what proves this is a vault
├── ledger-api/                 one folder per project, named after the repo folder, verbatim
│   ├── Map.md                  Index          what is here and where
│   ├── Position.md             Status         where it stands now
│   ├── Canon.md                Decisions      settled, with reasons
│   ├── Patterns.md             Patterns       what worked more than once
│   ├── Errata.md               Mistakes       what went wrong and what to do instead
│   └── Aporia.md               Open questions not yet answered
└── website/                    every other file is created on its first write, not in advance

~/src/ledger-api/               your repo, otherwise untouched: one @-import line in CLAUDE.md,
                                and .atlas-lane naming the vault paths its sessions may write
```

Files keep a short Latin or Greek name on disk. You and the model see a plain English one, from
`names.json`:

| file on disk | shown as | what it holds |
|---|---|---|
| `Map.md` | Index | what is here and where |
| `Vision.md` | Purpose | what this is for, what done looks like |
| `Position.md` | Status | where it stands now |
| `Course.md` | Roadmap | what comes next, in order |
| `Canon.md` | Decisions | settled, with reasons |
| `Patterns.md` | Patterns | what worked more than once |
| `Aporia.md` | Open questions | not yet answered |
| `Eidos.md` | Architecture | how it is built |
| `Errata.md` | Mistakes | what went wrong and what to do instead |
| `Apparatus.md` | References | pointers out |
| `Annales.md` | Log | what happened when |
| `Nomos.md` | Rules | what is safe, coordinated, forbidden |
| `Lexicon.md` | Glossary | the words |
| `Ethos.md` | Style | how to speak to the user |
| `Praxis.md` | Procedures | how things are done |
| `Exempla.md` | Examples | worked cases |
| `Kernel.md` | Boot | what every session loads |
| `Inbox.md` | Inbox | what others did here |

The `language` key picks the column (`en`, `de`, `latin`) and renames nothing.

### An optional check that lives outside the plugin

A plugin whose manifest fails validation loads nothing, hooks included, and Claude Code does not say
so. On 2026-09-09 one bad key cost fifteen hours in which every session ran with no hooks; an
unrelated review found it, not the tests. A check for that cannot live inside the thing that fails,
so it has to be registered in your own settings. It is off unless you ask for it:
`python3 init.py --install-outage-check` (or `--outage-check` on a full run) adds one
`UserPromptSubmit` entry to `~/.claude/settings.json` that runs `outage_check.py`. Nothing else in
this plugin writes Claude's settings. On each prompt it does one `stat`; if this session's SessionStart record is
missing, the prompt is refused, naming the likely cause and how to turn the check off:
`GEDAECHTNIS_OUTAGE_CHECK=off` for one session, `"outage_check": "off"` or `"message"` in the config
file, or `python3 init.py --remove-outage-check`. Sessions older than the install stamp are exempt,
and a hook input with no session id never blocks. If one healthy session is blocked within a week
(the log is the count), the check drops to `"message"`.

**A project you add later** is not forgotten and not taken silently. The first time a session starts
in a git repo with no memory, the facts block asks once — *"This project has no memory yet — create
one? (yes / no / never)"* — and then drops it. `never` is remembered; `no` is not asked again that day.

## What runs when

Eight hook events, in fifteen matcher entries carrying twenty-six hook commands (`hooks/hooks.json`):

| when | what it does |
|---|---|
| Before a Bash command | In the vault: refuses `git add -A`, a bare `commit`, `--amend`, backticks inside a double-quoted `-m`, a push to anything but a `backup` remote, and `reset --hard` / `clean -f`. Asks before `rm` against the vault or the Trash (and, in the vault, caches, databases and mined media). With `require_launch_model: true`, requires every `claude` launch to pin `--model` and `--effort`. With `"suite_gate": true`, holds a `git merge` or `git push` until a full test run on the commit being shipped — or one with the identical tree — is recorded green; the run records itself through the repository's root `conftest.py`, and the gate never runs tests. `SUITE_GATE_ALLOW=1` at the start of the command lets one through. |
| Before an Edit or Write | Holds a session to the vault paths its `.atlas-lane` marker declares. Ships in `warn` mode, which logs what it would have refused; write `deny` into `partition.mode` in the state directory to enforce it. Refuses a whole-file `Write` over an existing note and offers Edit instead, so another session's lines are not silently replaced. Takes a per-file lock until the tool returns; a lock older than ten seconds is stale and is taken over. Refuses a new file named after a display name (`Decisions.md`) and gives the stem to use (`Canon.md`). |
| Before a subagent call | In the vault or a marked project: refuses a sub-agent starting its OWN sub-agent unless its brief says `fanout: allowed`, and refuses more than `agent_max_concurrent` live sub-agents in one session. With `require_agent_model: true`, requires a `model` unless the agent's definition names one. |
| Before a Read | Says if this file is byte-identical to one already read this session, or if a whole-file read is over `big_read_kb`. Never refuses, and never answers your permission prompt for you. |
| After an Edit or Write | Adds a queue file's missing trailing newline. Flags commit SHAs that resolve in no known repo. Lists files still citing a heading an edit just removed. Adds a newly written role file's row to that project's `Map.md`. Records the vault path written, which is what the Stop commit is built from. Appends a row to a folder's `Inbox.md` when the writer came from elsewhere. |
| After a Bash command | Releases the per-file locks a shell write took. |
| After a published artifact | Records its title and id in the vault's artifact index and commits that one file. |
| After any tool | Says once per level that this session has passed the warn line or the cap. Never blocks, never acts. Loads a facet — a `Kernel-<name>.md` rule file beside a folder's boot file — the first time this call matches the tools, command patterns or paths its frontmatter lists — from the session's own folders, or from anywhere when the facet says `scope: always`; once per session, logged to `facets.log`. |
| At session start | Prints lane, writable paths, partition mode, vault HEAD, uncommitted count, unread inbox and ledger rows. On a fresh start in the vault or a marked repo it also injects the operating rules. Lists the facets of this session's folder and what loads each. Takes this session's per-region writer claim; each later prompt takes it if it is not yet held, and loads a facet the prompt names with `facet:<name>` or through a queue row it cites. With a `session_name_pattern` set, lists the managed sessions that are still running and marks the ones that have stopped moving. |
| When a message from another session arrives | If it says it merged a commit (`MERGED <sha>`, ``merged `<sha>` ``), checks that commit against `main` with git — in this repository, else in the vault — and adds the one-line answer to the session's context. Never blocks. `python3 hooks/mergesha.py verify-merge <sha> [<repo>]` asks the same question by hand. |
| At Stop | Gives back exactly the claims this session took. Commits this session's own vault writes, path-limited, as a fixed machine identity: never `git add -A`, never `--amend`, never another session's half-written file. Then works out whether a cleanup or a synthesis pass is due, and whether anything the next session boots with has stopped being true. It can also move the oldest entries of an over-long memory file or Boot file into an archive file, and remove finished worktrees under `.claude/worktrees/`; until you switch those on it only lists what it would do (see below). If this session was launched with a `--name` the installation manages, and it either wrote a handoff or ran out of context, it leaves one message for the session that started it. |
| When a subagent stops | Gives back what it was holding, so its parent is not left waiting. |
| On worktree create / remove | Makes a copy-on-write clone for an isolated build. On removal it fetches the clone's commits back into the source repo first, then deletes the clone only if it holds nothing unique. Uncommitted work goes to the Trash, never `rm`. |

Logs live under the state directory, one file per hook or door (`deny.log`, `partition.log`, `chore.log`, `session.log`, `hook-errors.log`, `facets.log`, `scope.log` and others); nothing is logged anywhere else.

## The doors

A door is a `PreToolUse` hook that can stop a tool call. A stopped call returns the reason to the
session through Claude Code's documented hook decision (`deny`, or `ask` for the ones you may still
approve). No hook ever returns `allow`: a note from this plugin never answers your permission
prompt for you.

**Where they act.** Most doors act only inside your vault. The ones that could apply anywhere act
only in the vault or in a project carrying an `.atlas-lane` marker, which `init.py` writes when you
give that project a memory, or in a session started in one. The directory a session was started
in counts only when it carries the marker itself; an unmarked one changes nothing. Everywhere else
they tell the session what they noticed and let the call run.

| door | where it acts | default | how to turn it off |
|---|---|---|---|
| Vault git rules: `add -A`/`-a`/`.`, a bare or `-a` commit, `--amend`, push to anything but `backup`, `reset --hard`, `clean -f`, `git rm` | the vault only | refuses | not switchable; they only act on the vault's own git |
| Whole-file `Write` (or `>` redirect) over an existing note | the vault only | refuses, offers Edit | not switchable |
| Generated view edited by hand | the vault only | refuses | not switchable |
| New file named after a display name (`Decisions.md`) | the vault only | refuses, names the stem | not switchable |
| Another session editing the same file | the vault only | refuses for up to ten seconds | not switchable |
| Write partition (paths the project's marker declares) | the vault only | warns and logs | `deny` in `<state>/partition.mode` turns it on; remove the file for `warn` |
| Reserved file names in one folder | the vault, only when `topology.stem_rule_dir` is set | refuses | leave `stem_rule_dir` unset |
| Authority ledger record its reader cannot resolve | the one file `authority_log` names | follows the partition mode (warns by default) | leave `authority_log` unset |
| `rm` of the vault or the Trash; emptying the Trash; destructive SQL on `anki_mining.db` | anywhere | asks you | not switchable; `protect_everywhere: true` also covers caches, databases and media outside the vault |
| Worktree placed outside `<repo>/.claude/worktrees/` | vault or marked project | refuses; a note elsewhere | remove the marker |
| `git push --tags` / `--follow-tags` from a clone carrying non-release tags | vault or marked project | refuses; a note elsewhere | remove the marker |
| Sub-agent starting a sub-agent without `fanout: allowed` in its brief | vault or marked project | refuses; a note elsewhere | `agent_fanout_allowed: true` |
| More than `agent_max_concurrent` (8) live sub-agents | vault or marked project | refuses; a note elsewhere | raise it in the config's `limits` |
| Message that would wake a large or cold idle session or sub-agent | vault or marked project | refuses; a note elsewhere | start the message with `RESUME-OVERRIDE: <reason>` |
| `open` of a local copy of a page that keeps its answers in an artifact store | vault or marked project | refuses; a note elsewhere | remove the marker |
| `claude` launch without `--model` / `--effort` | anywhere | off | `require_launch_model` / `require_launch_effort: true` turn it on |
| Sub-agent call without a `model` | anywhere | off | `require_agent_model: true` turns it on |
| Merge or push while another session holds the merge window | repositories carrying `.merge-window` | refuses | remove `.merge-window` |
| Merge or push before a green full test run | repositories with `gedaechtnis/tests`, only with `suite_gate: true` | off | leave `suite_gate` unset; `SUITE_GATE_ALLOW=1` lets one through |
| `rm` outside a scratchpad, `.claude/worktrees/` or `__pycache__` | anywhere, only with `delete_door: true` | off; when on, warns for one day, then refuses | leave `delete_door` unset |

## What this plugin runs, sends and fetches

**It sends nothing and fetches nothing.** No file the plugin ships imports a network library, and
no command it runs contacts a remote. It has no account, no telemetry and no update check. Your
notes stay in your vault on your disk; the plugin's own state (logs, per-session records) stays in
`~/.claude/gedaechtnis/`.

The programs its hooks and tools start, all locally (checked by `tests/test_publish_surface.py`
against the source):

- `git` — reads the vault's status and history, and commits the vault files a session wrote. The
  only `git fetch` copies commits from a local worktree clone into its source repository. Nothing
  pushes.
- `ps`, `lsof` — to tell whether a session or a worktree is still in use.
- `grep` — to find notes that still cite a heading an edit removed.
- `cp` — to make a copy-on-write clone for an isolated build.
- `osascript` — to move a file to the Trash through Finder (the worktree sweep and `retention.py
  apply`). The path is passed as an argument, never as script text.
- `python3` — the plugin's own scripts, and two commands only if you configure them:
  `notify_command` and `answer_router`.
- `bash` — the region-claim helper, only if `claim_tool` names one. The plugin ships none.

## Commands, agents and tools

Eight commands:

- `/gedaechtnis-status` — lane, vault, dirty paths, boot cost, and what the Stop hook will commit
- `/gedaechtnis-recall <question>` — what the vault already says about this
- `/gedaechtnis-debrief` — the four-line wrap-up plus that commit list
- `/gedaechtnis-cleanup` — tidies; applies by itself, never asks
- `/gedaechtnis-synthesis` — what the vault has learned and not acted on; proposes, changes nothing
- `/gedaechtnis-split` — one topic has outgrown its folder: shows what would move, takes a name, moves it
- `/gedaechtnis-graduate` — one folder has outgrown the boot budget: measures, drafts a boot file, refuses a draft that drops a binding rule, applies on your word
- `/gedaechtnis-facets` — lists a folder's facets (rule files kept beside its boot file) and what loads each one: a tool, a command pattern, a path or a work-queue row

Five agents. All are read-only; none can edit the vault.

- **memory-debriefer** — writes the end-of-session debrief from the diff since this session's start
- **memory-reviewer** — checks an entry before it lands: placement, supersession, evidence
- **memory-cleaner** — the half of a cleanup that needs a reader: the same thing said twice, an entry a newer one contradicts
- **memory-synthesizer** — a rule the vault states and never turned into a check; a lesson learned twice in two places
- **memory-distiller** — reads one folder and returns the sections of its boot file

Three tools you run yourself (`--help` on each):

- **`recall.py`** — greps the whole vault, archives included, and returns whole entries verbatim with their paths, ranked by how many of your terms an entry covers, not how often it repeats one
- **`ledger.py`** — an append-only, tab-separated message ledger for sessions that need to tell each other something; each reader keeps a cursor
- **`retention.py`** — classifies files under a review directory as derived, evidence or unknown. A derived file becomes a deletion candidate only if its round is closed and its generator is tracked in git. `apply` gathers candidates into one dated bundle with a README and never calls `rm`

## Configuration

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

## What has been measured

Each row says when it was measured and how to run it again. Rows dated 2026-09-23 were re-run that
day; the one row that was not says why. `CHANGELOG.md` and `eval/` carry the full working of each
number, including runs that failed their own pre-registered bar.

| what | result | when | how to run it |
|---|---|---|---|
| Test suite | 1,999 tests collected: 1,996 passed, 3 skipped, 2 errors. The suite stops a test with an error when another program writes the real vault while it runs; which tests that hits changes from run to run (two in `test_gradpath.py` in this run), and each passes when run again on its own (402 at 0.1.0) | 2026-09-23 | `python3 -m pytest tests -q` |
| Mutation checks | Rules carry a positive and a negative control, and a control counts once turning its rule off makes it fail. The five rules the safety eval can turn off each failed cases when turned off (12, 9, 7, 3 and 11 cases). A separate review the same day turned off each of the 13 rules added since 2026-09-17 and all 13 controls failed. It also found two exceptions: the two parsers that read `@`-import lines had no test that fails when the `@` requirement is dropped. Both got one the same day (`tests/test_maintenance.py`, `tests/test_boot_check.py`) | 2026-09-23 | `python3 eval/safety/run_safety.py --mutant vault_git` (and `data_integrity`, `bash_partition`, `d1_whole_file_write`, `write_partition`) |
| Safety eval | 52 adversarial commands denied and their 52 legitimate twins allowed, through the real gate in a throwaway sandbox | 2026-09-23 | `python3 eval/safety/run_safety.py` |
| Two writers, one file | 1,000 of 1,000 appends kept with the hooks, 28 of 1,000 without, at 500 appends per writer. The number without the hooks depends on timing: a second run the same evening kept 37 | 2026-09-23 | `python3 tests/sim_two_writers.py --n 500`, then with `--no-doors` |
| Context cost | `claude plugin details` puts the always-on cost at ~854 tokens and labels the hooks *harness-only — no model context cost*. The 854 is the commands and agents being listed to the model | 2026-09-23 | `claude plugin details gedaechtnis` |
| Recall ranking | 42 of 60 held-out questions about the author's vault answered from 8.4 KB read, at the shipped constant, with the constant frozen before the questions were drawn (2026-09-11). The same 60 questions against the same vault twelve days later: 40 and 39 of 60 in two back-to-back runs, from about 10.7 KB. With the vault's `topology` left out of the config, the same run scores 23 of 60 from about 994 KB, because its queue folders are then searched as memory | 2026-09-11, re-run 2026-09-23 | `python3 eval/recall_bench/run.py --arm grep --vault <vault> --questions <questions.json> --limit 3 --max-bytes 6000`; the question set and the pre-registered bar: `eval/recall_bench/PREREGISTRATION.md` |
| Against a plain memory file | Sessions with the plugin answered 36 of 36 cross-session tasks. A byte-matched 3 KB file pasted into the prompt answered 35 of 36. No memory answered 0 of 36. 192 real sessions, $4.76. Not re-run: the result is already inside the run's noise floor (see Limits), so a repeat would not change what the row can claim | 2026-09-10 | `python3 eval/memory_eval/run.py --live --claude "$(command -v claude)" --model claude-sonnet-5 --effort low --ceiling-usd 10` |
| The install path | A completely fresh `HOME` loads the plugin through the real symlink, at zero cost. The manifest outage check has 65 test cases, all passing | 2026-09-23 | `python3 eval/fresh_home/run.py --live --claude "$(command -v claude)" --model claude-sonnet-5 --effort low --ceiling-usd 1`; `python3 -m pytest tests/test_outage_check.py -q` |
| In real use, one machine | 59 denies, 229 session starts and 382 partition warnings in the live logs, counted at 23:08. The logs do not tell ordinary sessions apart from test and probe runs, and some of the denies are probes | 2026-09-08 to 2026-09-23 | `wc -l deny.log session.log partition.log` in the state directory |

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
  `lesson_push_boot_enabled`). It was built three times and refused by its own gates every time —
  matching shared vocabulary at a write, matching citations at a write, and selecting by structure
  at boot. Of the lessons known to have been re-broken, none would have been surfaced before the
  write that re-broke it, and one of seventeen would have been in the boot block of the session
  that re-broke it. The boot arm is the one that separated from its controls, and it has LANE
  resolution rather than session resolution: over 92 sessions it produced the same five lines every
  time, because a boot chain and a queue are properties of the lane, not of the session. All three
  designs' numbers sit beside the keys; `eval/lesson_push_replay.py` (`--boot` for the third)
  re-measures them.
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

## Publishing and contributing

Before sharing or publishing this tree:

```sh
python3 tools/publish_check.py
```

It refuses, listing every hit, if any file carries a home-directory path, an email address, a
private repository name or anything shaped like an API key. There is no allowlist and it scans
itself. Every excerpt is redacted. A walk that finds zero files prints `UNCHECKED` and exits
non-zero, because "we found nothing" and "we never looked" must not read the same. `--root DIR` runs
the identical sweep over any repository (2026-09-18). There it also looks for phone-number and IBAN
shapes, any `.env`-style file (flagged by existing, not only by its contents) and, in a git working
tree, any file `.gitignore` covers that is tracked anyway.

**Never `git push --tags` and never `git push --follow-tags`.** A clone made from a private
repository inherits that repository's tags, and each points at a private commit. Pushing them
publishes those commits and their whole ancestry. On 2026-09-10 one `--tags` published five
inherited tags, and deleting them from the remote minutes later does not remove the objects until
the host collects them. Two guards hold the rule: `publish_check.py` refuses a clone carrying any
tag that is not a `v*` release, and a `PreToolUse` gate refuses `--tags` from such a clone when the
session runs in the vault or a marked project (elsewhere it tells you). A single
tag pushed by name is never blocked. A release is one orphan commit, not a continuation:

```sh
bash tools/release_orphan.sh --sha <the commit you are publishing>
```

It extracts the tree at that commit out of git, builds it in a temporary repository as a single commit
with no ancestry, runs `publish_check.py` over it as a git root, refuses on any finding, pushes
nothing, and prints the two commands for a person to run.

**Tests:** `python3 -m pytest tests -q`. State is redirected into a temporary directory through the
environment variables above, so the suite never touches a real vault. The two-writer proof ships with
its control: the same run with the hooks removed, which must lose entries. A control that loses nothing
means the two processes never overlapped, and the test fails on that rather than passing.
`SIM_N=500 python3 -m pytest tests/test_doors.py -q` runs it at the full 500 appends per writer.

**Design rules**, for anyone changing this. Every refusal cites its reason. Defaults never delete:
cleanup moves files into one documented bundle and at most to the Trash, and nothing calls `rm`. A
file no rule names is kept until someone writes the rule that names it; age never decides. A rule
that needs judgment stays prose, because a guard that guesses produces false refusals, and a false
refusal is paid for in work that quietly does not happen. A hook bug never takes the session down:
every hook body logs its traceback and exits 0. No file names a directory of its own; all read `hooks/config.py`.

## Licence

MIT — see [LICENSE](LICENSE).
