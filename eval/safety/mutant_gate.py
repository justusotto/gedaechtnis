#!/usr/bin/env python3
"""mutant_gate.py — invoke the REAL `hooks/gate.py`, in-process, with exactly ONE named rule
disabled by monkeypatch.

Used only by `run_safety.py --mutant NAME` (and its tests) — never imported by the plugin itself,
never a substitute for `gate.py` in real use, and it never edits `hooks/gate.py`'s source. It exists
to prove the OPPOSITE of a normal green run: with the rule genuinely gone, at least one adversarial
case that rule alone covers must now get through — the mutation-testing discipline in
`Global/Patterns-verification.md` ("a check whose trigger condition never occurs in its fixture is
VACUOUS — mutate the guarded code and confirm the check goes red").

    GEDAECHTNIS_EVAL_MUTANT=vault_git python3 mutant_gate.py bash    < payload.json

Five named mutants, one deterministic monkeypatch each, chosen to each own a DISTINCT class of the
cases in `cases.json` so a mutant that stays green really does mean "no case exercises this rule":

  vault_git            rule_vault_git            → no-op   (git add -A / --amend / bare commit / …)
  data_integrity       rule_data_integrity        → no-op   (rm on caches/media/Trash/anki_mining.db/…)
  bash_partition       rule_bash_partition        → no-op   (mv-out, shared-surface bash append, cross-lane bash write)
  d1_whole_file_write  rule_no_whole_file_write   → no-op   (whole-file Write over an existing role file)
  write_partition      path_in_partition          → always True   (every Write/Edit partition check passes)

The in-process import (rather than a subprocess) is what makes the monkeypatch possible at all —
`gate.py bash`/`write`/`agent` normally runs as a fresh subprocess per call (see harness.py), and a
subprocess cannot see a patch applied in this process. This file is the one deliberate exception,
confined to `eval/safety/`.
"""
from __future__ import annotations
import os, sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS))
import gate  # noqa: E402  (the real, unmodified gate.py — we only ever patch an ATTRIBUTE on it)

MUTANTS = {
    "vault_git": ("rule_vault_git", lambda *a, **k: None),
    "data_integrity": ("rule_data_integrity", lambda *a, **k: None),
    "bash_partition": ("rule_bash_partition", lambda *a, **k: None),
    "d1_whole_file_write": ("rule_no_whole_file_write", lambda *a, **k: None),
    "write_partition": ("path_in_partition", lambda rel, prefixes: True),
}


def main() -> None:
    name = os.environ.get("GEDAECHTNIS_EVAL_MUTANT", "")
    if name not in MUTANTS:
        sys.stderr.write(f"mutant_gate.py: GEDAECHTNIS_EVAL_MUTANT must be one of {sorted(MUTANTS)}, got {name!r}\n")
        sys.exit(2)
    attr, fn = MUTANTS[name]
    if not hasattr(gate, attr):
        sys.stderr.write(f"mutant_gate.py: gate.py has no attribute {attr!r} — the mutant table is stale\n")
        sys.exit(2)
    setattr(gate, attr, fn)
    gate.guarded(gate.main)   # same crash-proofing real invocations get (common.guarded)


if __name__ == "__main__":
    main()
