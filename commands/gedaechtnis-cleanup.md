---
description: Tidy the memory vault — duplicates out, over-long files folded into their archive, nothing deleted. Applies straight away; never asks.
allowed-tools: Bash(python3:*)
---

Run this, and nothing else:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/cleanup.py" --session-id "$SESSION_ID"
```

Substitute the session id from this session's boot facts block (the line beginning "Session id");
if you do not have one, drop the `--session-id` flag rather than guessing. If
`${CLAUDE_PLUGIN_ROOT}` is unset, the script sits at `cleanup.py` inside the gedaechtnis plugin
directory (the one holding `hooks/`, `recall.py` and `archive.py`).

Then show the output to the user, unchanged.

## Do not ask them anything

**There is no approval step in this command, and you must not invent one.** Do not offer the list
first, do not ask which items to apply, do not ask whether to run it, and do not ask afterwards
whether to keep the result. The pass has already run by the time you are reading its output, and it
is designed so that it can: nothing is deleted, every removed entry is copied into a dated
`Cleanup/<date>/` bundle inside the vault with a `README-what-went-where.html` naming where it came
from and where the surviving copy is, and the whole thing is reversible with a copy-and-paste.

The reason is worth knowing, because it will look like helpfulness to ask. Tidying decisions are
hard for someone who did not design the vault, there are usually many of them at once, and a list
of twenty questions is a list nobody answers — so the pass that needs approval is the pass that
never runs. Reversibility is what replaces the approval, and it is already paid for.

## What to add

At most two sentences after the output: what changed, and where the bundle is if anything moved.
If the receipt names an entry as **reviewed, not moved**, say that this is deliberate — an open
question that has not been revisited is left in the live file precisely so it stays visible.

If the receipt reports something as UNCHECKED (a date it could not read, a file it could not read),
repeat that word. A measurement that could not be taken is not a clean result.

**Do not re-derive any of this yourself.** Do not count duplicates, do not measure a file against
its limit, do not decide something is stale. The script reads the same limits the hooks act on; a
number you work out a second way is a second implementation, and the two will disagree exactly when
it matters.
