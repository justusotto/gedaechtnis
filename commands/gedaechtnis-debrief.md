---
description: End the session — the four-line debrief, then exactly what the Stop hook is about to commit.
allowed-tools: Bash(python3:*), Bash(git:*), Read, Glob, Grep
---

Close this session out. Two parts, in order.

**1. The debrief.** Exactly the four lines the operating rules define, nothing before or after:

    Wrote: <the vault entries made this session, one clause each>
    Needs a decision: <questions only a person can answer, numbered — or "none">
    Committing: <the paths the Stop hook will commit>
    Open: <what is left for the next session — or "nothing">

Rules for it: name entries, not files touched — "the apostrophe decision, in Decisions
(Canon.md)" beats "edited three files". Invent nothing; if this session made no vault entry, say
so, and say whether it should have (a decision settled, a bug fixed or a question raised without
an entry is the failure this line exists to catch). Put only genuine judgment calls under *Needs a
decision* — anything safe, cheap or mechanical was yours to decide and should already be recorded.

**2. What is about to be committed.** Run:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/tools/status.py" --session-id "$SESSION_ID"
```

using the session id from this session's boot facts block (drop the flag if you do not have one;
if `${CLAUDE_PLUGIN_ROOT}` is unset, the script is at `tools/status.py` in the gedaechtnis plugin
directory). Fill the *Committing* line from its output — do not list files from memory.

Two things worth saying out loud if the output shows them: a vault file this session wrote that
sits OUTSIDE the write partition (it will not be committed here, and the owning lane has been
told through its region's Inbox), and a file already committed by a session that shares it.
Neither is an error; both are surprising to a reader who does not know the rule.

Do not run vault `git` commands to make the commit happen. The Stop hook does that, path-limited,
and a hand-made commit is how a half-written sibling edit gets swept in.
