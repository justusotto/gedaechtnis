---
description: Ask the vault what it has learned and not yet acted on — a rule with no check, a lesson written down twice in two regions. Proposes; changes nothing.
allowed-tools: Bash(python3:*), Read, Glob, Grep, Write, Task
---

Four steps, in order. Do not reorder them and do not skip the last one.

## 1. Gather the candidates

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/synthesis.py" --json
```

If `${CLAUDE_PLUGIN_ROOT}` is unset, `synthesis.py` sits in the gedaechtnis plugin directory (the
one holding `hooks/`, `recall.py` and `cleanup.py`).

This judges nothing. It is a wide net, and most of what it returns is not a finding.

## 2. Hand them to the synthesizer

Launch the **`memory-synthesizer`** agent with the pack from step 1 in its prompt, and tell it the
vault path. It reads the entries the pack names, throws away what is not a finding, and returns two
artifacts as content: `report.md` and `findings.json`.

**Do not do this work yourself in this session.** The agent has no write tools, which is the only
thing that makes "it proposes and never edits" a property of the system rather than an intention.

## 3. Write the two artifacts

Write exactly what the agent returned to the directory named in the pack's `pass_dir`
(`<vault>/Synthesis/<date>/`): `report.md` and `findings.json`, unchanged. Do not summarise, do not
merge them, do not reformat the JSON.

That directory is not memory — the search excludes it by the same rule it excludes a cleanup
bundle. A proposal about what the vault should learn is not something the vault has learned, and a
search that returned one would hand it back looking exactly like a decision.

## 4. Record the pass

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/synthesis.py" --mark
```

**It refuses if the two files are not there**, and that refusal is the point. Marking the pass moves
the window forward, so everything an absent report would have covered is now behind it and no later
pass looks there again. A stamp without a report is worse than no stamp.

If it refuses, the artifacts were not written — go back to step 3. Do not tell the user the pass is
recorded when the command said it was not.

## What to say afterwards

Show the report, then at most two sentences: how many findings, and where the file is.

Every finding is a **proposal**. Nothing has been changed, and nothing should be applied because it
appears here — a promotion is an edit to the vault's memory, and that is the user's to make.

If anything came back **UNCHECKED**, repeat that word. A region that could not be read makes the
finding count a lower bound, not a result, and the pass looks identical to a clean one from the
outside.

**Do not re-derive any of this.** Do not count entries a second way, do not decide a candidate is a
finding, do not work out whether the pass is due. The script reads the same state the hooks act on;
a number you compute a second way is a second implementation, and the two disagree exactly when it
matters.
