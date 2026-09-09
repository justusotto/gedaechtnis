---
description: The facts about this session's memory — lane, vault, partition mode, dirty paths, boot cost, unanswered ledger rows.
allowed-tools: Bash(python3:*)
---

Run this, and nothing else:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/tools/status.py" --session-id "$SESSION_ID"
```

Substitute the session id from this session's boot facts block (the line beginning "Session id");
if you do not have one, drop the `--session-id` flag rather than guessing. If
`${CLAUDE_PLUGIN_ROOT}` is unset, the script sits at `tools/status.py` inside the gedaechtnis
plugin directory (the one holding `hooks/`, `recall.py` and `ledger.py`).

Then show the output to the user, unchanged, and add at most two sentences: what stands out
(uncommitted work outside the partition, unanswered ledger rows, an absent vault) and what to do
about it. **Do not re-derive any of these values yourself** — not the dirty count, not the lane,
not the boot cost. The script calls the same functions the hooks act on; a number you compute a
second way is a second implementation, and the two will disagree exactly when it matters.
