# Das Gedächtnis

A Claude Code plugin that turns the rules of a persistent-memory vault into machinery. If you keep
a markdown knowledge base that every Claude Code session loads — decisions, failure modes, standing
constraints — you have probably noticed that written rules decay: the file says "never `git add -A`
in the vault", every session reads it, and one session does it anyway. Gedächtnis is the other half
of that system. It is a set of hooks that make the breakable rules *impossible* to break instead of
prose that asks to be remembered: a `PreToolUse` gate denies the command, cites the entry it is
enforcing, and the session learns the reason rather than only meeting a wall. Rules that genuinely
need judgment stay prose; only deterministic ones live here.

The vault itself — your notes — is the data half and is not part of this repo. The plugin reads and
protects a vault; it never ships one.

## State, measured (2026-09-10)

This is version 0.1.0, two days old as a public repository. What follows is what has been measured,
with the date, and what has not. The numbers refresh as the checkpoints run; nothing here is
a promise about the next version.

**Measured and green**

- **402 tests** pass (`python3 -m pytest tests -q`, 2026-09-10). Every rule has a positive and a
  negative control.
- **Safety eval, all green:** 52 adversarial commands denied, and their 52 legitimate twins allowed,
  through the real gate in a throwaway sandbox (`eval/safety/results/results.md`). The five mutant
  runs each turn one rule off and show the case set fail without it.
- **Two writers, one file:** 1,000 of 1,000 appends kept with the doors, 7 of 1,000 without
  (`tests/sim_two_writers.py`, 500 per writer).
- **Seven hooks load** (PreToolUse, PostToolUse, SessionStart, Stop, WorktreeCreate, WorktreeRemove,
  UserPromptSubmit), confirmed by `claude plugin details`, at an always-on cost of about 330
  tokens per session.
- **The outage check bites:** the scratch plugin carrying the exact 2026-09-09 defect is rejected by
  the real validator (`claude plugin validate`, `agents: Invalid input`) and the first prompt of a
  session with no SessionStart record is refused, exit 2; the shipped manifest validates and the
  same check is silent (`tests/test_outage_check.py`, 33 cases).
- **The log-and-views shadow, day 0:** the vault's 1,229 entries across 72 role files were imported
  into an append-only log and regenerated as views; 72 of 72 views came back byte-identical to
  the live file. This is the first of seven checkpoints (days 0, 1, 2, 4, 7, 14, 30) for the
  design that may replace hand-edited role files with a log and generated views. It runs beside
  the vault and writes nothing into it.
- **Recall bench, grep arm, first real run:** 30 held-out questions drawn by a separate model
  session from a real vault of 988 files. **recall@3 was 0 of 30**, and the expected entry sat
  at median rank 30 while sections of 665 KB took the top places. The cause is the ranking rule
  (coverage first, so the largest section covers every term), confirmed by re-running on role
  files only (5 of 30). A length-aware ranking is pre-registered as arm (a′) in
  `eval/recall_bench/PREREGISTRATION.md` with its bar stated before it is built. The number is
  bad and it is the point of having the bench.
- **Recall bench, arm (a′), the length-aware rank, same day:** the ranking now discounts a term that
  is common in the corpus and divides frequency by entry size, and skips queue and notice surfaces;
  on the same 30 questions it reaches **11 of 30 at 16 KB read before the answer** (was 0 of 30 at
  1.23 MB) — the pre-registered bar was 15 of 30 at ≤ 20 KB, so it fails on hits and the index arm
  is next; on the same entries served as one-entry-per-row files it reaches 18 of 30 at 7 KB.
- **Memory eval, second live run — a negative result on the comparison that matters:** 12
  cross-session tasks (10 written by two model sessions that did not build the plugin, drawn from
  10 regions of a real vault), 4 arms, 3 samples per cell, Sonnet 5 at low effort, 192 real
  sessions, $4.76. Sessions with the plugin answered **36 of 36** day-N tasks from what day 1 had
  recorded — but a **byte-matched** prior-decisions file in the prompt answered **35 of 36**, and
  the pre-registered bar was two whole tasks. **The plugin is not shown to beat a 3 KB memory file
  on this task shape**; the one-task gap is inside the run's own noise floor. No memory answered
  **0 of 36**, so the tasks are genuinely unanswerable without it, and when the recorded fact was
  replaced by its opposite the sessions followed the opposite **35 of 36 times**, so the memory is
  read rather than guessed around. The first run's 3-vs-2 headline was a checker artefact, in both
  directions, and is retracted. Limits: one model, one effort, the plugin loaded through staged
  project settings rather than the install path, and the file arm is not Claude Code's own memory
  loader. Details in `eval/memory_eval/` and the run's report; nothing here is a percentage.
- **In real use on one machine:** 12 denies, 10 session starts and 9 partition warnings in the
  live logs since 2026-09-08, from ordinary sessions, none of them staged.

**Found by measuring, and fixed**

- Between 2026-09-09 12:39 and 2026-09-10 03:30 the plugin loaded nothing at all. The manifest
  carried an `agents` key that Claude Code rejects, and a rejected manifest disables every hook
  silently. A blind control review found it; a test now pins the key's absence, and the rule
  learned is in the design rules below: a hook is live only when a fresh session's own log row
  says so.

**Not yet measured**

- Whether the plugin beats Claude Code's own auto-memory: the live eval's file arm cannot load
  it, so that comparison is still open. (Whether the first run's 3 of 3 held at three samples per
  cell, on tasks not written by the builders, IS now measured — it held, and so did the file arm's;
  see the second run above.) Whether the plugin helps on a task shape where a flat file cannot
  simply be pasted into the prompt — multi-entry recall, a vault too large to fit, a fact recorded
  many sessions before the question — is untested, and is where the second run says to look next.
- Whether the entry index finds the right answer in fewer bytes than the grep recall
  (`eval/recall_bench/`, pre-registered, not run).
- The write-partition door is in WARN, not DENY, until the partition log has been read over a
  week of real sessions.

**In progress**

- English, common-practice file names on disk; today the six role files carry their Greek and
  Latin names.
- Adding your own files, folders and projects to a vault without adopting the whole layout.
- The shadow's remaining checkpoints. `v0.1` is tagged on this state (2026-09-10); the refreshed
  numbers land as `v0.2`.

## Install

One command, run from anywhere:

```sh
git clone <this repo> ~/src/gedaechtnis && python3 ~/src/gedaechtnis/init.py
```

Most people have more than one project, so `init.py` finds them and asks **one** question:

```
Gedächtnis found 5 projects Claude Code has worked in.

    #  project                          last session   region it would get
    1  src/ledger-api                   today          ledger-api
    2  src/website                      2 days ago     website
    3  work/scratch-notes               31 days ago    scratch-notes
    4  code/old-prototype               210 days ago   old-prototype   (no session in 90 days)

Enter takes every row except #4 (the rows with a note); name a number to include one anyway.
Found via Claude Code's own project list (~/.claude.json, `projects` key only) and a depth-2 look
under: ~/Projects, ~/src, ~/code. Nothing else on this disk was searched.

Give these projects a memory?  [Enter = all]  [numbers, e.g. 1 3 4]  [none]  [later]
```

Enter takes every project you have worked in during the last 90 days. Typing numbers takes exactly
those, and the rows you did not name are remembered as declined so you are never asked about them
again. `none` writes nothing; `later` writes nothing and remembers nothing. Everything you choose is
set up in one act, and the vault gets one first commit.

**Where the list comes from — and what is never read.** Claude Code already keeps a list of the
directories it has been opened in, and that list is your working set. Gedächtnis reads exactly one
key of `~/.claude.json` — `projects` — and never writes that file; it reads the modification times
of `~/.claude/projects/*/` as a "last session" signal only; it asks `git config --global` for the
repos git itself already tracks; and it looks **two levels deep, no deeper**, under the directories
in the `roots` setting. It does not scan your disk, read your shell history (which carries commands
and, routinely, secrets), or ask Spotlight. Anything under a temp directory, in an agent worktree,
in a scratchpad, or in the vault itself is skipped, as is your home directory itself.

If your repos live somewhere the default `roots` do not name — JetBrains' project directory, say,
which this plugin deliberately does not spell out — add one line to the config file:
`"roots": ["~/src", "~/wherever-mine-are"]`.

Other ways to run it: `--repo DIR` sets up that one project and shows no screen; `--discover` shows
the screen even then; `--decline` (optionally `--repo DIR`) records that a project wants no memory
and writes nothing else; `--offer-declined` lists the declined ones again, unticked; `--all` ignores
the declined list for one run; `--dry-run` shows the plan and writes nothing; `--yes`, like a
non-terminal stdin, takes the default without asking.

For each chosen project it creates: a vault at `~/Gedaechtnis` (or uses `~/Atlas` when that
directory is already an Atlas vault — what proves it is the file `~/Atlas/Global/fleet-roster.md`,
not the folder's name), `git init`-ed; a folder for the project in the vault, named after the repo folder
**verbatim**, with six starter files — `Map.md`, `Position.md`, `Canon.md`, `Patterns.md`,
`Errata.md`, `Aporia.md`, shown to you and to the model as Index, Status, Decisions, Patterns,
Mistakes, Open questions (every other file is born on its first write); a *lane* — the writer
identity its sessions use — declared in the vault's `Global/fleet-roster.md` and in a
`.atlas-lane` marker in the repo; an @-import line in the repo's `CLAUDE.md`;
`~/.claude/gedaechtnis/config.json` naming the vault; and the symlink
`~/.claude/skills/gedaechtnis` that makes Claude Code load the plugin. It creates only what is
absent and never overwrites a file: run it again and every line reports `kept`.

Then restart Claude Code (or `/reload-plugins`) and open it in the repo: the first session prints a
facts block naming your vault and lane. There is nothing to build and no dependency beyond Python
3.9+ from the standard library.

**The install also registers one hook outside the plugin, and this is deliberate.** A plugin whose
manifest fails validation loads *nothing* — hooks included — and Claude Code says so nowhere: on
2026-09-09 a single bad key cost fifteen hours in which every session ran with no doors at all, and
it was found by an unrelated review, not by any of the tests. A check for that failure cannot live
inside the thing that fails, so `init.py` adds one `hooks.UserPromptSubmit` entry to
`~/.claude/settings.json` running `outage_check.py`. On every prompt it does one `stat`: if the
plugin's own SessionStart record for this session — `~/.claude/gedaechtnis/session-start-<session
id>.json` — is missing, the prompt is refused (exit 2) with a message saying that Gedächtnis did not
load, that a rejected manifest is the likely cause (`claude plugin list` shows the ✘;
`claude plugin validate <plugin dir>` names the key), and how to switch the check off:
`GEDAECHTNIS_OUTAGE_CHECK=off claude` for one session, `"outage_check": "off"` in the config file
permanently, `"outage_check": "message"` to keep the warning without the refusal, or
`python3 init.py --remove-outage-check` to uninstall it. If the hook input carries no session id at
all — a schema change rather than an outage — it never blocks: it exits 0 and appends one row to
`~/.claude/gedaechtnis/outage-check.log`, where every block and every warning is also recorded. It
is settings.json surgery, so it merges into whatever is already there, never duplicates itself, and
leaves a file it cannot parse untouched and says so. `--no-outage-check` skips the install, and
`python3 init.py --install-outage-check` adds it on its own, writing nothing else — which is the
command for a machine that is already set up, since a full run there would name a region after the
repo folder and create it.
**Its falsifier is written down and cheap to check:** if one *healthy* session is blocked within a
week — the log is the count — it is downgraded to `"outage_check": "message"`. The known ways that
could happen are the SessionStart hook timing out or crashing and a state directory pruned under a
live session. (`tests/test_plugin_manifest.py` also pins the manifest, but it *skips* where the
`claude` CLI is absent or the plugin is unregistered, so it is not a gate; this check is the one
that runs on your machine, every prompt.)

**A project you add later** is not forgotten and not taken silently. The first time a session starts
in a git repo with no memory, the facts block asks you once, in one line — *"This project has no
memory yet — create one? (yes / no / never)"* — and then drops the subject. `never` is remembered;
`no` is not asked again that day.

## What your sessions do with it

Once installed, a session in that project gets four things without being asked, and you can leave
your `CLAUDE.md` empty:

- **The rules arrive at boot.** Every session that starts is handed
  [`rules/operating-rules.md`](rules/operating-rules.md) — under 3,000 B saying which of the six
  files a decision, a bug, a working approach, an open question or a state change goes in, to
  write it while the work happens rather than in a summary at the end, never to delete a vault
  file, how to end the session, and the three questions a session ever asks you (the install
  screen, the no-memory question, a cleanup with candidates — and nothing else). Injected at
  `startup` only, because a resumed session already has it. `inject_rules: false` turns it off.
- **The notes commit themselves.** At Stop, the vault files *this session wrote* — recorded as it
  writes them, and intersected with the paths its `.atlas-lane` marker declares — are staged one
  by one and committed as `Gedächtnis <gedaechtnis@local>`. Never `git add -A`, never `--amend`,
  and never another session's half-written file: two sessions in the same lane each commit their
  own work. A session with no marker commits nothing at all and says so in the log.
- **Three commands**, when you want them: `/gedaechtnis-status` (lane, vault, dirty paths, boot
  cost, and exactly what the Stop hook is about to commit), `/gedaechtnis-recall <question>`, and
  `/gedaechtnis-debrief` (the four-line wrap-up plus that commit list).
- **Recall, as a habit.** [`recall.py`](recall.py) greps the whole vault, archives included, and
  returns whole entries verbatim with their paths — ranked so that an entry covering three of your
  terms beats one repeating a single term thirty times. Use it before deciding something the vault
  may already have settled; it is the cheapest way not to contradict yourself six weeks later.

Two agents ship alongside: **memory-debriefer** (writes the end-of-session debrief from the diff
since *this session's* start, and names the entry you should have made) and **memory-reviewer**
(ratifies an entry before it lands — placement, supersession, evidence). Both are read-mostly and
pin a small model.

## What the files are called

The file on disk keeps a short Greek/Latin name (`Canon.md`, never renamed to `Decisions.md`);
every surface a person or the model reads — the session facts block, `init`'s templates, the
commands, these docs — shows a plain-English name instead ([`hooks/names.py`](hooks/names.py),
[`names.json`](names.json)):

| file (on disk) | shown as | what it holds |
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

The `language` switch (`config.json`, or `GEDAECHTNIS_LANGUAGE`) picks the column: `en` (default,
the table above), `de` (built into `names.json`, off by default — `Canon.md` shows as
*Entscheidungen*, `Errata.md` as *Fehler*), or `latin`, which shows the stem itself. Changing it
never renames a file — it only changes what a session calls the file it already has.

## What each hook does

| event | hook | rule |
|---|---|---|
| PreToolUse Bash | `gate.py bash` | **Vault git law** — no `add -A`/`-a`/`.`, no bare `commit` (the pathspec is what guarantees you committed only what you meant to), no `--amend`, no backticks inside a double-quoted `-m` (the shell substitutes them away and the commit still succeeds), push only to a `backup` remote, no `reset --hard` / `clean -f` in a directory other sessions are writing. **Launch hygiene** — every `claude` launch pins `--model` and `--effort` rather than inheriting a settings-file default. **Data integrity** — no `rm` against caches, databases, mined media, your Pictures folder, the Trash or the vault; the Trash is never emptied. **Review pages** — a page that stores answers in its own artifact is opened as the artifact, never as a `file://` copy where the answers go nowhere. |
| PreToolUse Edit/Write | `gate.py write` | **Write partition** — a session may write only the vault directories its repo's `.atlas-lane` marker declares, so two sessions working in parallel cannot overwrite each other's notes. Ships in `warn` mode (logs what it would have refused); write `deny` into `partition.mode` in the state directory to enforce. Shared surfaces (queue files, the message ledger, a region inbox) accept an appended well-formed row from anyone, and nothing else. **Display names are never file names** — a new file called `Decisions.md`, `Fehler.md` or `open-questions.md` is refused with the stem it means (`Canon.md`, `Errata.md`, `Aporia.md`) and the path to write instead, so one role never ends up as two files; a file of that name already on disk is your data and is left alone. |
| PreToolUse Edit/Write | `gate.py write` | **Two sessions, one file, nothing lost.** A whole-file `Write` over an existing note is refused: it replaces the file from what you read a minute ago, and every line another session added since is gone with no error. Edit is offered instead — its anchor is checked against the file as it is now, so a stale edit fails loudly rather than clobbering. New files, empty files, anything that is not `.md`, and anything inside a `Cleanup */` bundle are exempt, as is a file this session created. Alongside it, a **per-file lock** held from the moment permission is asked until the tool has finished: a second session meeting it is told to retry, which makes it re-read. A lock older than ten seconds is stale and is taken over, so a tool that errors or a hook that dies never leaves a file locked. Shell writes to a note (`>`, `>>`, `tee`, `sed -i`) take the same lock. |
| PreToolUse Agent | `gate.py agent` | A subagent call names a `model`, unless its definition already does — an unpinned agent silently inherits the session's model, which can be several times the price you intended. |
| PreToolUse Read, PreToolUse Bash | `gate.py read`, `gate.py bash` | **Context economy** — two non-blocking notices, never a refusal. See "Context economy" below. |
| PostToolUse Artifact | `chore.py artifact` | Records a published artifact's title and id in the vault's artifact index and commits that one file, so a page you published is findable later instead of living in a chat scrollback. |
| PostToolUse Edit/Write | `chore.py write` | Repairs a queue file's missing trailing newline (without it the next appended row glues onto the previous line and no parser sees it); reports commit SHAs in the text that resolve in no known repo; when an edit removes a heading or an id, lists every other file still citing it; and, the first time a role file is written in a project (`Eidos.md`, `Nomos.md`, `Annales.md`…), adds its one row to that project's `Map.md` and commits it, so a file is born by being written and nothing has to be created in advance. |
| PostToolUse Bash | `chore.py bash` | Releases the per-file locks a shell write took, the moment the command returns, instead of making the next session wait for them to age out. |
| PostToolUse Edit/Write | `chore.py inbox` | When a session writes in a region that is not its own, appends one row to that region's `Inbox.md`, so the record lands where the work happened rather than where the writer had permission. |
| PostToolUse Edit/Write | `chore.py write` | Also releases this session's lock on the file, records the vault path just written against this session's id — the touched set the Stop commit is built from — and, when the gate saw the file was absent, records that this session created it. |
| PostToolUse Read, PostToolUse Bash | `chore.py read`, `chore.py bash` | **Context economy** — records that the read happened, so the *next* read of the same unchanged file gets the notice. |
| SessionStart | `session_start.py` | States the facts a session should not have to ask for: which lane it is, what it may write, the partition mode, the vault's HEAD and uncommitted count, unread inbox and ledger rows. On a `startup` (not a resume or a compact) it also injects the operating rules. In a git repo that has no memory yet, it tells the session to ask you once — *"create one? (yes / no / never)"* — and at most once per project per day. |
| SessionStart | `claim.py start` | Takes this session's per-region writer claim, so the vault's one-writer-per-region rule is kept by machinery instead of by a ritual performed from memory. Does nothing at all when no claim helper is configured, when the session has no lane, or when its partition names no region. |
| Stop | `claim.py stop` | Gives back exactly the claims this session took, and nothing else. A claim that is never released is worse than none: the next session defers to a holder that no longer exists. |
| Stop | `commit.py` | Commits this session's own vault writes, inside the lane's declared paths, path-limited and as a fixed machine identity. Not "everything dirty in the partition": the collision that actually happens is one session sweeping another's half-written file, and only the touched set can tell two sessions in one lane apart. No marker ⇒ nothing is staged, one line in the log, exit clean. |

Three more tools ship alongside the hooks:

- **`recall.py`** — the vault, grepped. `recall.py "<question>"` returns the best-matching entries
  verbatim with their paths, archives included, ranked by how many of the question's terms an
  entry covers rather than how often it repeats one, and capped so the answer costs less than the
  re-derivation it prevents. Read-only. `python3 recall.py --help`.
- **`ledger.py`** — an append-only, tab-separated message ledger for sessions that need to tell each
  other something. Rows, not files; each reader keeps a cursor so a new session reads only what
  arrived since it last looked. `python3 ledger.py --help`.
- **`retention.py`** — cleanup as a mechanism. It classifies files under an arc/review directory as
  DERIVED, EVIDENCE or UNKNOWN, and a derived file becomes a deletion candidate only if its round
  is closed and its arc has a generator tracked in git. Everything else is kept. `apply` gathers
  candidates into one dated bundle with a README explaining where each file came from; it never
  calls `rm`. `python3 retention.py --help`.

Every refusal and repair is logged under the state directory: `deny.log`, `partition.log`,
`chore.log`, `session.log`, `hook-errors.log`.

## Context economy

Two notices, both non-blocking — they ride `additionalContext` on an explicit **allow**, so a
session sees them on its next turn and nothing is ever refused:

- **Re-read.** Reading a file (`Read`, or `cat`/`head`/`tail`/`sed -n` of one file from a shell
  command) whose content is byte-identical to what this session already read earlier in the
  session says so, with how long ago and how many tokens the re-read would cost, and suggests
  Grep for the section instead.
- **Big-read.** Reading a file whole (no `offset`/`limit`; `cat`, not `head`/`tail`/`sed -n`)
  over `big_read_kb` KB (default **24**) says how big it is and how many tokens that is, and
  suggests Grep-for-the-heading-then-offset instead. Never fires for a file this session wrote
  itself this session, for a small file, or for an image/PDF/binary.

Both are on by default (`context_economy: true`); turn them off with `GEDAECHTNIS_CONTEXT_ECONOMY=0`
or `"context_economy": false` in the config file. `python3 tools/status.py --session-id <SID>`
reports how many of each fired this session.

## Configuration

Precedence, per setting: environment variable → `~/.claude/gedaechtnis/config.json` → default.
`init.py` writes the config file with the vault it created; edit it to move the vault.

| JSON key | environment variable | default |
|---|---|---|
| `vault` | `GEDAECHTNIS_VAULT` | `~/Gedaechtnis`, or `~/Atlas` when it is a vault — i.e. `~/Atlas/Global/fleet-roster.md` is a file |
| `roots` | `GEDAECHTNIS_ROOTS` (colon-separated) | `~/Projects`, `~/projects`, `~/src`, `~/code`, `~/dev`, `~/repos`, `~/Developer`, `~/IdeaProjects`, `~/AndroidStudioProjects`, `~/Documents/GitHub`, `~/go/src` — looked under two levels deep, and nowhere else |
| `declined` | — | none; appended by `init.py --decline`, and nothing else writes it |
| `state_dir` | `GEDAECHTNIS_STATE_DIR` | `~/.claude/gedaechtnis` |
| `fleet_roster` | `GEDAECHTNIS_FLEET_ROSTER` | `<vault>/Global/fleet-roster.md` |
| `worktrees_dir` | `GEDAECHTNIS_WORKTREES` | `~/.claude/worktrees` |
| `owner_pages_status` | — | none; the session-start hook then says nothing about review pages |
| `python` | — | the interpreter running the hook |
| `tool_root` | `GEDAECHTNIS_TOOL_ROOT` | the checkout this plugin lives in |
| `claim_tool` | `GEDAECHTNIS_CLAIM_TOOL` | `<tool_root>/skills/atlas-region/helpers/region_claim.sh`; absent = the claim hooks do nothing |
| `auto_claim` | `GEDAECHTNIS_AUTO_CLAIM` | `true` — coordination that must be switched on is coordination that is off |
| `auto_commit` | `GEDAECHTNIS_AUTO_COMMIT` | `true` — the Stop hook commits this session's vault writes |
| `inject_rules` | `GEDAECHTNIS_INJECT_RULES` | `true` — the operating rules are injected at session start |
| `language` | `GEDAECHTNIS_LANGUAGE` | `en` — `de` and `latin` are the switch; see "What the files are called" above |
| `context_economy` | `GEDAECHTNIS_CONTEXT_ECONOMY` | `true` — the two non-blocking read notices below |
| `big_read_kb` | `GEDAECHTNIS_BIG_READ_KB` | `24` — the size (KB) above which a whole-file read gets the big-read notice |

```json
{
  "vault": "~/Gedaechtnis",
  "state_dir": "~/.claude/gedaechtnis",
  "worktrees_dir": "~/.claude/worktrees"
}
```

`GEDAECHTNIS_CONFIG` moves the config file itself; the test suite uses it so that no test ever
reads or writes the real state directory. `GEDAECHTNIS_NO_TRASH=1` makes the two tools that would
send something to the Trash leave it in place instead.

## Tests

From this directory (`pytest` and Python 3.9+ are all you need):

```sh
python3 -m pytest tests -q
```

Every rule has a **positive control** (the violation is denied) and a **negative control** (the
legitimate form is allowed), because a gate that only ever fires is as broken as one that never
does. All state is redirected into a temporary directory through the environment variables above,
so the suite never touches a real vault.

The two-writer claim is the one that cannot be checked by reading the code, so it has a harness of
its own: `tests/sim_two_writers.py` runs two real processes appending to one file through the real
hooks, and asserts that every entry survives in a valid order and that the commit carries all of
them. It ships with its **control** — the same run with the doors removed, which must LOSE entries.
A control that loses nothing means the two processes never actually overlapped, and then the green
run proves nothing; the test fails on that instead of passing. Measured at 500 appends per writer:
1,000 of 1,000 kept with the doors, 7 of 1,000 without. It runs at 120 per writer in the suite
(about 25 seconds); `SIM_N=500 python3 -m pytest tests/test_doors.py -q` runs the full number.

`eval/live_two_writers.sh` is the same proof with two real model sessions instead of two
processes. It is **not** part of the suite and costs money to run; read its header first.

Before publishing or sharing this tree:

```sh
python3 tools/publish_check.py
```

It refuses, listing every hit, if any file carries a home-directory path, an email address, a
private repository name or anything shaped like an API key. It has no allowlist and scans itself.

## Publishing

This plugin is developed inside a private repository and published from a mirror clone. Two things
travel with a push that no file check can see: **refs and history**.

Refresh the mirror clone without taking the upstream's tags:

```sh
git pull --no-rebase --no-tags
```

Publish a release by NAME, never with a flag that sends every ref:

```sh
python3 tools/publish_check.py --root .    # tree AND refs
git push origin main
git push origin v0.1                       # the tag, by name
```

**Never `git push --tags` and never `git push --follow-tags`.** A clone made from a private
repository inherits that repository's tags, and each one points at a private commit: pushing them
publishes those commits and their whole ancestry, under tag names that have nothing to do with this
project. That is not hypothetical — on 2026-09-10 one `--tags` published five inherited tags, and
deleting them from the remote minutes later does not remove the objects until the host collects
them.

Two guards hold the rule so nobody has to remember it: `tools/publish_check.py` refuses a clone that
carries any tag other than a `v*` release (naming it), and a PreToolUse door in `hooks/gate.py`
refuses `--tags` / `--follow-tags` from such a clone while allowing the same command from a clone
whose tags are all releases. A single tag pushed by name is never blocked.

## Design rules

- **Every deny cites its reason.** A wall teaches nothing; a wall with the entry it enforces
  attached teaches the rule, and the next session does not need the wall.
- **Defaults never delete.** Cleanup moves files into one documented bundle and, at most, to the
  Trash. Nothing here calls `rm`, and the Trash is never emptied.
- **Unknown means irreplaceable.** A file no rule names is kept, forever, until someone writes the
  rule that names it. Retention is never decided by age.
- **A rule that needs judgment stays prose.** Only deterministic rules become hooks. A guard that
  has to guess produces false refusals, and a false refusal is paid for in work that quietly does
  not happen.
- **A hook bug must never take the session down.** Every hook body is wrapped: it logs the
  traceback and exits 0 with no opinion.
- **Configuration in one place.** No file in this plugin names a directory of its own; they all
  read `hooks/config.py`. A hard-coded path works on one machine and is a lie everywhere else.

## Licence

MIT — see [LICENSE](LICENSE).
