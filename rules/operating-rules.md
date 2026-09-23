# Operating rules — how a session uses the vault

The vault is this project's memory. Each project folder (a *region*) carries six files, shown
under a plain-English name; the file on disk keeps its short name (`Canon.md`, not
`Decisions.md`) either way — **Index** (`Map.md`, what the project is, plus an index), **Status**
(`Position.md`, where it stands), **Decisions** (`Canon.md`, settled decisions), **Patterns**
(`Patterns.md`, what works), **Mistakes** (`Errata.md`, what went wrong), **Open questions**
(`Aporia.md`, unanswered). <!--only-if:kernel-->A region may also carry a **Boot** (`Kernel.md`) —
its always-loaded index. <!--/only-if-->Other files are created only when there is something to put
in them.

## What goes where

- A decision becomes settled → **Decisions** (`Canon.md`): what was decided, the date, why.
  Supersede an entry in place with a dated line; never rewrite one from memory.
- A bug is diagnosed and fixed, or a mistake is corrected → **Mistakes** (`Errata.md`): the
  mechanism first, then what to do instead, so the same mistake is not made twice.
- An approach works for the second time → **Patterns** (`Patterns.md`): the rule in a few lines.
- A question surfaces that nobody has answered → **Open questions** (`Aporia.md`), with an
  urgency and today's date.
- The state of the work changes (shipped, in flight, deferred, next) → **Status** (`Position.md`).
- A role file is added to the region → the hook adds its row to **Index** (`Map.md`) for you, the
  first time you write it. Just write the file; nothing has to be created or indexed in advance.

Knowledge that applies to every project goes in `Global/`, with a pointer from the region — never
a second copy: a duplicated fact diverges, and the stale half is the one that gets read.

## How to write

- **Write immediately, while the work is happening**, not in a summary at the end; an ordinary
  entry needs no permission.
- **Be terse and structured.** Every line is read by every later session: the operative rule,
  not the story.
- **Consult recall before re-deriving.** Before deciding something the vault may already cover,
  search it (`/gedaechtnis-recall <question>`) and read the entry rather than reasoning from
  scratch.
- **Never delete a vault file.** Anything that has to go moves into a `Cleanup YYYY-MM-DD/` folder
  with a note saying where each file came from.
- **Edit an existing note, never rewrite it whole.** Another session may have added something
  since you read it, and a whole-file write drops that silently; an edit's anchor is checked
  against the file as it is now, so a stale one fails instead. If an edit is refused because
  another session is in that file, **retry the same edit** — the retry re-reads it.
- **You ask the user exactly two things, ever, and each at most once**: at install, which of the
  projects found should get a memory; and, in a project that has none, "create one? (yes / no /
  never)". **Nothing else in these rules asks the user anything** — an entry, a new file, an
  archive move, a cleanup and the commit all happen without a question.
- **Committing is the hook's job, not yours.** The Stop hook stages exactly the paths this repo's
  `.atlas-lane` marker declares and commits them, path-limited. Never run `git add -A`, `-a` or
  `--amend` in the vault.

<!--only-if:reviewer-->## Before a change that is not an ordinary entry

An entry goes straight in. A change that **contradicts something already written** — a rule
reversed, an entry superseded, a file's contents moved somewhere else — goes to the
`memory-reviewer` agent first, which reads what is on disk and returns one of four verdicts.
**APPROVE and APPROVE WITH REVISIONS you apply yourself, without asking anyone**; only REJECT and
ESCALATE reach the user, with what the reviewer said. Nothing else is escalated: a safe, cheap or
mechanical choice was yours to make and belongs in the vault, not in a question.

<!--/only-if-->

## Ending a session

When work wraps up, give the debrief in four lines:

    Wrote: <the vault entries made this session, one clause each>
    Needs a decision: <questions only a person can answer, numbered — or "none">
    Committing: <the paths the Stop hook will commit>
    Open: <what is left for the next session — or "nothing">
