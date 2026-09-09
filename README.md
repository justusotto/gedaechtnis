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

For each chosen project it creates: a vault at `~/Gedaechtnis` (or uses `~/Atlas` if that directory
already exists), `git init`-ed; a folder for the project in the vault, named after the repo folder
**verbatim**, with six starter files (Map, Position, Canon, Patterns, Errata, Aporia — every other
file is born on its first write); a *lane* — the writer identity its sessions use — declared in the
vault's `Global/fleet-roster.md` and in a `.atlas-lane` marker in the repo; an @-import line in the
repo's `CLAUDE.md`; `~/.claude/gedaechtnis/config.json` naming the vault; and the symlink
`~/.claude/skills/gedaechtnis` that makes Claude Code load the plugin. It creates only what is
absent and never overwrites a file: run it again and every line reports `kept`.

Then restart Claude Code (or `/reload-plugins`) and open it in the repo: the first session prints a
facts block naming your vault and lane. There is nothing to build and no dependency beyond Python
3.9+ from the standard library.

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

## What each hook does

| event | hook | rule |
|---|---|---|
| PreToolUse Bash | `gate.py bash` | **Vault git law** — no `add -A`/`-a`/`.`, no bare `commit` (the pathspec is what guarantees you committed only what you meant to), no `--amend`, no backticks inside a double-quoted `-m` (the shell substitutes them away and the commit still succeeds), push only to a `backup` remote, no `reset --hard` / `clean -f` in a directory other sessions are writing. **Launch hygiene** — every `claude` launch pins `--model` and `--effort` rather than inheriting a settings-file default. **Data integrity** — no `rm` against caches, databases, mined media, your Pictures folder, the Trash or the vault; the Trash is never emptied. **Review pages** — a page that stores answers in its own artifact is opened as the artifact, never as a `file://` copy where the answers go nowhere. |
| PreToolUse Edit/Write | `gate.py write` | **Write partition** — a session may write only the vault directories its repo's `.atlas-lane` marker declares, so two sessions working in parallel cannot overwrite each other's notes. Ships in `warn` mode (logs what it would have refused); write `deny` into `partition.mode` in the state directory to enforce. Shared surfaces (queue files, the message ledger, a region inbox) accept an appended well-formed row from anyone, and nothing else. |
| PreToolUse Agent | `gate.py agent` | A subagent call names a `model`, unless its definition already does — an unpinned agent silently inherits the session's model, which can be several times the price you intended. |
| PostToolUse Artifact | `chore.py artifact` | Records a published artifact's title and id in the vault's artifact index and commits that one file, so a page you published is findable later instead of living in a chat scrollback. |
| PostToolUse Edit/Write | `chore.py write` | Repairs a queue file's missing trailing newline (without it the next appended row glues onto the previous line and no parser sees it); reports commit SHAs in the text that resolve in no known repo; and, when an edit removes a heading or an id, lists every other file still citing it. |
| PostToolUse Edit/Write | `chore.py inbox` | When a session writes in a region that is not its own, appends one row to that region's `Inbox.md`, so the record lands where the work happened rather than where the writer had permission. |
| PostToolUse Edit/Write | `chore.py write` | Also records the vault path just written against this session's id — the touched set the Stop commit is built from. |
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

## Configuration

Precedence, per setting: environment variable → `~/.claude/gedaechtnis/config.json` → default.
`init.py` writes the config file with the vault it created; edit it to move the vault.

| JSON key | environment variable | default |
|---|---|---|
| `vault` | `GEDAECHTNIS_VAULT` | `~/Gedaechtnis`, or `~/Atlas` if it exists |
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

Before publishing or sharing this tree:

```sh
python3 tools/publish_check.py
```

It refuses, listing every hit, if any file carries a home-directory path, an email address, a
private repository name or anything shaped like an API key. It has no allowlist and scans itself.

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
