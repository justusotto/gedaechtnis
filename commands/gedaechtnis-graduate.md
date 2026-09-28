---
description: Give a region that has outgrown its boot budget a Boot file — measure it, draft it, show you the draft, and only then apply. Nothing is written without your word.
allowed-tools: Bash(python3:*), Read, Glob, Grep, Task
---

## 1. Measure, and read the worthiness line before anything is drafted

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/graduate.py" --check <Region>
```

If `${CLAUDE_PLUGIN_ROOT}` is unset, `graduate.py` sits in the gedaechtnis plugin directory (the one
holding `hooks/`, `split.py` and `cleanup.py`).

Show the output. **If the worthiness line says DORMANT or CHURNING STATE, stop and say so**, in one
sentence, and ask whether to go on anyway. Those are not refusals and they are not formalities:
distilling a region nobody is working spends a judgment on a room nobody is in, and distilling one
whose every commit is a state update produces an index that is stale the day after it is written.

If it says UNCHECKED, repeat that word. A measurement that could not be taken is not a clean result.

## 2. Draft it — the agent reads, you do not

Run the `memory-distiller` agent on that region. It returns a JSON object of sections and writes
nothing. Save its JSON to a file and assemble:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/graduate.py" --assemble <Region> --from <sections.json>
```

**The assembler can REFUSE, and a refusal is information, not an error to work around.** It names
every binding `Never`/`Always` line from the region's `Nomos.md` and `Canon.md` that the draft does
not carry. If it refuses, run the agent again with the missing lines quoted to it. **Do not hand-add
the lines to the JSON yourself and do not edit the rendered file to satisfy the check** — the check
exists because a rule that stops being loaded is silent, and satisfying it by hand is exactly how
that becomes silent again.

Never hand-write the window markers. The assembler places them; that is the whole reason the agent
returns sections rather than a file.

## 3. Show the draft, and ask ONE thing

Show them the rendered draft — the actual file, not a description of it — and its size against the
budget. Then ask exactly one question: **apply it, edit the draft file first, or drop it.**

Nothing has been written into the vault at this point. The draft is in the state directory.

## 4. Apply, once they have said the word

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/graduate.py" --apply <Region> --draft <path> --repo <repo>
```

This copies the draft to `<Region>/Kernel.md`, repoints the repo's marked import block at it, keeps
the previous `CLAUDE.md` beside it as a dated backup, and writes `GRADUATION-RECEIPT.md` into the
region. Show the receipt unchanged.

**It refuses rather than guessing, and each refusal means something specific:**

- **exit 7, no marked import block** — the repo's `CLAUDE.md` was written by hand or by an older
  install, so the script printed the line to paste and edited NOTHING. Offer to paste it; do not
  rewrite the block yourself.
- **the bodies have moved** — the draft summarises a state the region has left. Re-run from step 1.
  Do not apply it anyway; an approval is for a distillation of a particular state.
- **a Boot file already exists** — a Boot file is changed by editing it, never by a second
  graduation.

## What to say afterwards

Two sentences: that the region now loads one file instead of several, and where the receipt is.
Nothing was deleted — every body is still on disk and is read on demand; what changed is which of
them a session loads at boot. The undo is in the receipt.
