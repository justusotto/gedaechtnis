# Privacy

Gedächtnis runs on your computer and keeps everything there. It has no server, no account, no
analytics and no update check. It sends nothing anywhere and fetches nothing from anywhere.

## What it stores, and where

- **Your notes.** The memory files live in a folder you choose, `~/Gedaechtnis` unless you name
  another. It is a local git repository. The plugin writes there only what a session wrote, and
  commits it locally. It never pushes that repository anywhere.
- **Its own state.** Logs and small per-session records live in `~/.claude/gedaechtnis/`: which
  project and folder a session worked in, which commands a hook refused and why, and timestamps.
  The settings file `config.json` is in the same folder.
- **Inside a project's own `.git/` folder, only if you turn these on.** A project carrying a
  `.merge-window` file gets `.git/gedaechtnis/merge-window.json` (which session may merge now); with
  `suite_gate: true`, `.git/gedaechtnis/suite-runs.jsonl` records which test runs passed.
- **Build copies.** When Claude Code asks for an isolated worktree, the plugin makes a copy of the
  project in `~/.claude/worktrees/`. It removes the copy when the worktree is removed, after
  fetching its commits back; a copy holding uncommitted work goes to the Trash instead. With
  `worktree_sweep_apply` on, a finished copy is moved to `~/Downloads/To delete <date>/`, with a
  ledger file and a short README there.
- **Two lines in each project you set up.** An `.atlas-lane` file that names the project's memory
  folder, and an import line in the project's `CLAUDE.md`.
- **Optional.** If you ask for the plugin-outage check (`init.py --install-outage-check`), one entry
  is added to `~/.claude/settings.json`. Nothing is added there otherwise.

## What it reads

To do its job the plugin reads, on your computer only: the memory folder; the Claude Code session
transcripts under `~/.claude/projects/` (to measure a session's size and to see whether a
sub-agent's brief allows it to start another); the list of projects in `~/.claude.json`, when
`init.py` offers to set them up; and git status and history of the projects it works in. None of
it is copied anywhere else.

## Removing it

1. Uninstall the plugin in Claude Code (or remove the `~/.claude/skills/gedaechtnis` link if you
   installed it by hand).
2. If you added the outage check: `python3 init.py --remove-outage-check`.
3. Delete `~/.claude/gedaechtnis/` if you do not want its logs.
4. Your notes stay in your memory folder. Keep them, or delete the folder yourself. In each project,
   the `.atlas-lane` file and the import line in `CLAUDE.md` can be removed by hand.

## Questions

Open an issue on the repository this plugin is published from.
