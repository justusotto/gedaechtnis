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

One command, run from inside the project you want to give a memory:

```sh
git clone <this repo> ~/src/gedaechtnis && python3 ~/src/gedaechtnis/init.py
```

`init.py` asks nothing. It creates a vault at `~/Gedaechtnis` (or uses `~/Atlas` if that directory
already exists) and `git init`s it; a folder for this project in the vault with six starter files
(Map, Position, Canon, Patterns, Errata, Aporia — every other file is born on its first write); a
*lane* for the project — the writer identity its sessions use — declared in the vault's
`Global/fleet-roster.md` and in a `.atlas-lane` marker in the repo; an @-import line in the repo's
`CLAUDE.md`; `~/.claude/gedaechtnis/config.json` naming the vault; and the symlink
`~/.claude/skills/gedaechtnis` that makes Claude Code load the plugin. It creates only what is
absent and never overwrites a file: run it again and every line reports `kept`. `--dry-run` shows
the plan; `--repo`, `--vault`, `--lane` and `--region` override the defaults.

Then restart Claude Code (or `/reload-plugins`) and open it in the repo: the first session prints a
facts block naming your vault and lane. There is nothing to build and no dependency beyond Python
3.9+ from the standard library.

## What each hook does

| event | hook | rule |
|---|---|---|
| PreToolUse Bash | `gate.py bash` | **Vault git law** — no `add -A`/`-a`/`.`, no bare `commit` (the pathspec is what guarantees you committed only what you meant to), no `--amend`, no backticks inside a double-quoted `-m` (the shell substitutes them away and the commit still succeeds), push only to a `backup` remote, no `reset --hard` / `clean -f` in a directory other sessions are writing. **Launch hygiene** — every `claude` launch pins `--model` and `--effort` rather than inheriting a settings-file default. **Data integrity** — no `rm` against caches, databases, mined media, your Pictures folder, the Trash or the vault; the Trash is never emptied. **Review pages** — a page that stores answers in its own artifact is opened as the artifact, never as a `file://` copy where the answers go nowhere. |
| PreToolUse Edit/Write | `gate.py write` | **Write partition** — a session may write only the vault directories its repo's `.atlas-lane` marker declares, so two sessions working in parallel cannot overwrite each other's notes. Ships in `warn` mode (logs what it would have refused); write `deny` into `partition.mode` in the state directory to enforce. Shared surfaces (queue files, the message ledger, a region inbox) accept an appended well-formed row from anyone, and nothing else. |
| PreToolUse Agent | `gate.py agent` | A subagent call names a `model`, unless its definition already does — an unpinned agent silently inherits the session's model, which can be several times the price you intended. |
| PostToolUse Artifact | `chore.py artifact` | Records a published artifact's title and id in the vault's artifact index and commits that one file, so a page you published is findable later instead of living in a chat scrollback. |
| PostToolUse Edit/Write | `chore.py write` | Repairs a queue file's missing trailing newline (without it the next appended row glues onto the previous line and no parser sees it); reports commit SHAs in the text that resolve in no known repo; and, when an edit removes a heading or an id, lists every other file still citing it. |
| PostToolUse Edit/Write | `chore.py inbox` | When a session writes in a region that is not its own, appends one row to that region's `Inbox.md`, so the record lands where the work happened rather than where the writer had permission. |
| SessionStart | `session_start.py` | States the facts a session should not have to ask for: which lane it is, what it may write, the partition mode, the vault's HEAD and uncommitted count, unread inbox and ledger rows. |

Two more tools ship alongside the hooks:

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
| `state_dir` | `GEDAECHTNIS_STATE_DIR` | `~/.claude/gedaechtnis` |
| `fleet_roster` | `GEDAECHTNIS_FLEET_ROSTER` | `<vault>/Global/fleet-roster.md` |
| `worktrees_dir` | `GEDAECHTNIS_WORKTREES` | `~/.claude/worktrees` |
| `owner_pages_status` | — | none; the session-start hook then says nothing about review pages |
| `python` | — | the interpreter running the hook |

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
