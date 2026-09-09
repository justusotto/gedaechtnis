---
description: Ask the vault what it already knows about something, and read the answer verbatim before deciding.
argument-hint: <question in plain words>
allowed-tools: Bash(python3:*)
---

Search the memory vault for: **$ARGUMENTS**

Run:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/recall.py" "$ARGUMENTS"
```

(If `${CLAUDE_PLUGIN_ROOT}` is unset, `recall.py` sits at the top of the gedaechtnis plugin
directory — the one holding `hooks/`, `commands/` and `ledger.py`. `--limit N` and
`--max-bytes N` widen or narrow the answer; the defaults are 6 entries and 6,000 B.)

Then report what came back:

- **Quote the entries verbatim**, each with its file path. Never paraphrase one — a summary of a
  decision is a new decision, and the paraphrase is what the next reader will act on.
- Say plainly whether the question is answered, partly answered, or not covered. "No vault entry
  matches" is a real answer: it means whatever is decided now is the first entry, and it should be
  written down.
- An entry marked `(archive)` is the narrative behind a rule; the live file is the authority where
  the two differ.
- If nothing found is relevant, say so rather than stretching a weak match into a fit.

Recall never writes anything. If the answer shows the vault is missing something it should hold,
that is a note to make afterwards, in the file the operating rules point at.
