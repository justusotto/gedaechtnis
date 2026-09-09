# Operating rules — how a session uses the vault

The vault is this project's memory. Each project folder (a *region*) carries six files: **Map**
(what the project is, plus an index), **Position** (where it stands), **Canon** (settled
decisions), **Patterns** (what works), **Errata** (what went wrong), **Aporia** (open questions).
A region may also carry a **Kernel** — its always-loaded index. Other files are created only when
there is something to put in them.

## What goes where

- A decision becomes settled → **Canon**: what was decided, the date, why. Supersede an entry in
  place with a dated line; never rewrite one from memory.
- A bug is diagnosed and fixed, or a mistake is corrected → **Errata**: the mechanism first, then
  what to do instead, so the same mistake is not made twice.
- An approach works for the second time → **Patterns**: the rule in a few lines.
- A question surfaces that nobody has answered → **Aporia**, with an urgency and today's date.
- The state of the work changes (shipped, in flight, deferred, next) → **Position**.
- A file is added to the region → one row in **Map** pointing at it.

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
- **You ask the user exactly three things, ever, and each at most once**: at install, which of the
  projects found should get a memory; in a project that has none, "create one? (yes / no / never)";
  and, when a cleanup pass has candidates, whether to gather them into one folder. **Nothing else
  in these rules asks the user anything** — an entry, a new file, an archive move and the commit
  all happen without a question.
- **Committing is the hook's job, not yours.** The Stop hook stages exactly the paths this repo's
  `.atlas-lane` marker declares and commits them, path-limited. Never run `git add -A`, `-a` or
  `--amend` in the vault.

## Ending a session

When work wraps up, give the debrief in four lines:

    Wrote: <the vault entries made this session, one clause each>
    Needs a decision: <questions only a person can answer, numbered — or "none">
    Committing: <the paths the Stop hook will commit>
    Open: <what is left for the next session — or "nothing">
