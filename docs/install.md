# Installing, and what it creates

## From a clone

```sh
git clone <this repository's URL> ~/src/gedaechtnis
python3 ~/src/gedaechtnis/init.py
```

With no `--repo`, `init.py` lists the projects Claude Code has worked in and asks which to set up:

```
Gedächtnis found 5 projects Claude Code has worked in.
    #  project                    last session   region it would get
    1  src/ledger-api             today          ledger-api
    2  src/website                2 days ago     website
    3  code/old-prototype         210 days ago   old-prototype   (no session in 90 days)

Give these projects a memory?  [Enter = all]  [numbers, e.g. 1 3 4]  [none]  [later]
```

Enter takes every project worked in during the last 90 days. Numbers take exactly those; the rows
you did not name are recorded as declined and not offered again. `none` writes nothing. `later`
writes nothing and records nothing.

Then one question about what the memory is for, because the starter files' headings depend on it:

```
What is this memory mostly for?
  [1] a software project
  [2] notes, a journal, a log
  [3] studying or learning something
  [4] something else
```

`--shape project|notes|study|other` answers it without asking. `project` is the shape the published
measurements were taken over, and it is the fallback when nothing answers (a script, `--yes`, a
pipe); the run prints that it fell back. `notes` exists because a 90-day test over one person's
garden notes had readers call the project headings boilerplate. `study` has not been tested. The four
shapes differ only in the headings and opening lines of `Position.md` and `Map.md`; file names,
roles and rules are the same.

Restart Claude Code (or `/reload-plugins`) and open it in the repo. The first session prints a facts
block naming your vault and lane. Python 3.9 or later; nothing to build.

**Where the list comes from.** `init.py` reads the `projects` key of `~/.claude.json` (and never
writes that file), the modification times of `~/.claude/projects/*/` as a "last session" signal,
`git config --global` for the repos git already knows, and two levels under the directories in
`roots`. It does not scan the disk, read shell history or ask Spotlight, and it skips temp
directories, agent worktrees, scratchpads, the home directory itself and the vault. If your repos
live elsewhere: `"roots": ["~/src", "~/wherever-mine-are"]`.

**Other options.** `--repo DIR` sets up one project without the screen. `--discover` shows the
screen anyway. `--decline` records that a project wants no memory. `--offer-declined` lists those
again. `--all` ignores the declined list once. `--dry-run` plans and writes nothing. `--yes` takes
the default.

## From a plugin directory

If you installed Gedächtnis from a plugin directory or marketplace, Claude Code already loads it.
Nothing is written until you say yes: the plugin does nothing to a project until that project has a
memory. To give one a memory, run `init.py` from the installed copy (the session's facts block names
the command), or answer the session's one question. Run from an installed copy, `init.py` does not
add the `~/.claude/skills` symlink and does not write `~/.claude/settings.json`.

Use one install method. A clone plus a directory install loads the plugin twice, and every hook
runs twice.

## What it creates

A vault at `~/Gedaechtnis`, `git init`-ed. If that directory does not exist, it adopts a directory
one level under home that is already a vault, recognised by the file `Global/fleet-roster.md`
inside it, never by the folder's name. If two directories qualify, neither is adopted and you name
one. Then, per chosen project: a folder in the vault with six starter files; a *lane*, the writer
identity its sessions use, declared in `Global/fleet-roster.md` and in a `.atlas-lane` marker in the
repo; an `@`-import line in the repo's `CLAUDE.md`; `~/.claude/gedaechtnis/config.json`; and the
symlink `~/.claude/skills/gedaechtnis` that loads the plugin. It creates only what is absent and
does not overwrite a file: a second run reports `kept` on every line.

```
~/Gedaechtnis/                  the vault: one git repo, yours
├── Global/
│   └── fleet-roster.md         which repo writes where, and what marks this as a vault
├── ledger-api/                 one folder per project, named after the repo folder
│   ├── Map.md                  Index          what is here and where
│   ├── Position.md             Status         where it stands now
│   ├── Canon.md                Decisions      settled, with reasons
│   ├── Patterns.md             Patterns       what worked more than once
│   ├── Errata.md               Mistakes       what went wrong and what to do instead
│   └── Aporia.md               Open questions not yet answered
└── website/                    other files are created on their first write

~/src/ledger-api/               your repo: one @-import line in CLAUDE.md,
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

## A project you add later

The first time a session starts in a git repo with no memory, the facts block asks once: *"This
project has no memory yet — create one? (yes / no / never)"*. `never` is remembered; `no` is not
asked again that day. On a machine with no memory folder yet, the question also names the folder a
`yes` creates.

## An optional check that lives outside the plugin

A plugin whose manifest fails validation loads nothing, hooks included, and Claude Code does not say
so. On 2026-09-09 one bad key left every session without hooks for fifteen hours; a review found it,
not the tests. A check for that cannot live inside the plugin, so it is registered in your own
settings, and only if you ask: `python3 init.py --install-outage-check` (or `--outage-check` on a
full run) adds one `UserPromptSubmit` entry to `~/.claude/settings.json` that runs
`outage_check.py`. Nothing else in this plugin writes Claude's settings.

On each prompt it does one `stat`; if this session's SessionStart record is missing, the prompt is
refused, naming the likely cause and how to turn the check off: `GEDAECHTNIS_OUTAGE_CHECK=off` for
one session, `"outage_check": "off"` or `"message"` in the config file, or
`python3 init.py --remove-outage-check`. Sessions older than the install are exempt, and a hook
input with no session id does not block. If one healthy session is blocked within a week (the log
is the count), the check drops to `"message"`.
