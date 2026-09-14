#!/usr/bin/env python3
"""subagent_stop.py — commit what a SUBAGENT wrote to the vault, when that subagent ends.

A subagent's vault writes are not lost today: its tool calls run under the parent session's id,
so `chore.py` records them in the parent's touched set and the parent's next Stop commits them.
What is wrong is the LATENCY. A long managing session delegates a dozen builds over an afternoon;
every note any of them writes sits uncommitted until the parent finally stops, which is also the
window in which another lane's news watch and the Pharos sorter DEFER on a dirty vault. The
subagent ended hours ago and its work is finished; there is nothing left to wait for.

**The naive version of this hook reintroduces the one collision this vault has actually suffered.**
`commit.py` commits the TOUCHED SET rather than "everything dirty in the partition" precisely
because a sweep once carried a sibling session's half-written file — four times in one night. At
SubagentStop the parent's touched set contains the parent's OWN in-flight edits from earlier in
the same turn. Pointing this event at the unchanged Stop hook would commit those, doing to the
parent exactly what the sweep did to the sibling. So the unit here is narrower still: the paths
attributable to THIS subagent, and nothing else.

**Attribution is direct, and it was measured rather than assumed** (2026-09-14, a dump of the raw
PreToolUse / PostToolUse / SubagentStop payloads from a fresh session that spawned one subagent):

    SubagentStop     session_id (the PARENT's), agent_id, agent_type, agent_transcript_path
    a subagent's
      tool calls     session_id (the PARENT's), agent_id, agent_type
    the parent's
      own calls      session_id, and NO agent_id

So `chore.py` splits its record at write time — `agent_touched[<agent_id>]` for a subagent's
writes, `direct_touched` for the parent's own — and this hook commits

    agent_touched[agent_id]  MINUS  direct_touched  ∩  the lane's declared paths

**The subtraction is not defensive tidiness; it is the hard case.** A BACKGROUND subagent runs
while the parent keeps editing, so a file both of them wrote holds the parent's possibly
half-finished edit as well, and git cannot split one file between two authors. Such a file is
left alone here and the parent's Stop takes it — late, which is the defect this hook exists to
reduce, but late is recoverable and a committed half-edit is not.

**Everything else is `commit.py`'s, unchanged and uncopied:** the `auto_commit` off switch, the
lane fail-safe (no marker / unparsable / absolute-or-`..` path ⇒ stage nothing, one log line,
exit clean), the dirty intersection, the explicit `git add -- <file>`, the pathspec read back
from the index, `Gedächtnis <gedaechtnis@local>`, never `-A`, never a bare commit, never
`--amend`. This file chooses the paths and writes the subject; it implements no git law of its own.

A payload with no `agent_id` — a harness that stops sending one, a hand-run — commits NOTHING and
says so: the whole safety of this hook is that it knows whose writes it is carrying, and a hook
that falls back to the parent's set when attribution is missing is the naive version again.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import commit
from common import read_input, log, agent_touched_paths, direct_touched_paths, guarded


def main() -> None:
    inp = read_input()
    sid = inp.get("session_id", "-")
    agent_id = inp.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        log("commit", f"sid={sid} event=subagent-stop agent=UNKNOWN "
                      f"action=staged-nothing exit=clean")
        return
    agent_type = inp.get("agent_type") or "-"
    parent_own = set(direct_touched_paths(sid))

    def select(s: str, _prefixes: list[str]) -> list[str]:
        mine = agent_touched_paths(s, agent_id)
        keep = [p for p in mine if p not in parent_own]
        held = [p for p in mine if p in parent_own]
        if held:
            # Named, not hidden: the parent wrote these too, so their working-tree content is
            # partly its edit. Left for the parent's Stop rather than committed under the
            # subagent's name — see the module docstring's hard case.
            log("commit", f"sid={s} event=subagent-stop agent={agent_id} "
                          f"held-for-parent-stop (parent edited the same file): {' '.join(held)}")
        return keep

    commit.auto_commit(
        inp, select,
        lambda lane: f"subagent auto-commit: [{lane}] {agent_type} {time.strftime('%Y-%m-%d')}",
        tag=f"event=subagent-stop agent={agent_id}")


if __name__ == "__main__":
    guarded(main)
