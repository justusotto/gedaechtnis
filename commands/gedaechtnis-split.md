---
description: When one topic has outgrown its region, give it its own — show what would move, take a name, and move it. Nothing moves without your word.
allowed-tools: Bash(python3:*), Read, Glob, Grep
---

## 1. Look

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/split.py"
```

If `${CLAUDE_PLUGIN_ROOT}` is unset, `split.py` sits in the gedaechtnis plugin directory (the one
holding `hooks/`, `recall.py` and `cleanup.py`).

Show the output. If there are no proposals, say so and stop — that is the ordinary answer, and a
vault with no topic big enough to need its own room does not need this command.

**A proposal comes in two kinds, and only one of them has an apply.**

- **`split`** — the cluster is big enough to be its own room AND the parent keeps enough entries to
  still be one. Steps 2–4 below are about this kind.
- **`refocus`** — the cluster is real, but taking it out would leave the parent under the floor. The
  region IS the topic. **Nothing moves, and `--apply` refuses this id**; do not try it, and do not
  offer to move the entries by hand. What the output is showing them is the handful of entries the
  region would have been left holding — read those out, because that list is the whole finding.
  The suggested name is a better name for **the region itself**, and renaming is a separate pass:
  say so, and do not rename anything here.

Do not present a refocus as a smaller or failed split. It is a different answer to the same
question, and the honest sentence is "this region does not have a topic that outgrew it — it has
grown into one topic, and here is what else is still in there".

## 2. Suggest a name, and let them change it

The script's `suggested_name` is derived from the cluster's shared words. It is a starting point and
it is often clumsy, because vocabulary is not a title.

**Read the eight-or-more headings yourself and propose a better one**, in one line, with the
script's suggestion beside it so they can see where it came from. Then ask which they want. This is
the one judgment in the whole command: the name becomes a directory, appears in every wikilink that
points into it, and is what someone reads a year from now.

Keep it short, in the vault's own language, and a noun — the room, not the activity in it.

## 3. Apply, once they have said the word

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/split.py" --apply <region> <cluster-id> --name <TheirName>
```

**Do not run this before they have answered.** Unlike the cleanup pass, which applies by itself
because it is reversible and dull, this one changes paths that other files point at. It asks
because the answer is worth having, not to be polite.

Then show the receipt it prints, unchanged.

## What to say afterwards

Two sentences at most: what moved where, and that the receipt lives in the new sub-region as
`SPLIT-RECEIPT.md`.

Nothing was deleted — every entry is in exactly one live file before and after, and the receipt
names each origin and destination, so the move can be undone by hand.

If the receipt lists anything under **left untouched**, say so plainly: a file that changed between
the proposal and the apply is skipped on purpose, because at a different position sits a different
entry. Re-run step 1 and the proposal will be computed fresh.

If anything came back **UNCHECKED**, repeat that word. A region that could not be read makes "no
proposals" a lower bound rather than a result.

**Do not move anything yourself.** Do not edit the files, do not create the directory, do not fix up
a wikilink by hand. The script does the move in one act so that what it reports and what happened
are the same thing; an edit alongside it is a second, unrecorded mover.
