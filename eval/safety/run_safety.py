#!/usr/bin/env python3
"""run_safety.py — WP9 destruction eval: 50 adversarial commands/writes and their 50 legitimate
twins, run through the REAL `hooks/gate.py` doors in a fresh sandbox per case.

    python3 run_safety.py                        # baseline: every case through the real doors
    python3 run_safety.py --mutant vault_git      # one named rule disabled — proves it is covered
    python3 run_safety.py --out DIR               # where results.json / results.md land (default: this dir)

**No network, no model call, ever.** Every case is a subprocess of `hooks/gate.py` (or, for
`--mutant`, the in-process `mutant_gate.py` wrapper — see its docstring) reading a synthetic,
throwaway vault under a system temp directory; nothing here reads or writes `~/Atlas` or the real
`~/.claude`.

Exit 1 on any miss — an adversarial case that was allowed, or a twin that was refused — and the
table names exactly which. `--mutant NAME` additionally requires at least one MISS (an adversarial
case the disabled rule alone was protecting); a mutant with zero misses means the rule is not
exercised by any case in `cases.json` and is reported as `MUTANT DID NOT BITE — rule not covered`,
per the mutation-testing rule in Global/Patterns-verification.md ("a check whose trigger condition
never occurs in its fixture is VACUOUS").

Both `results.json` and `results.md` are DERIVED from the same in-memory list of per-case dicts —
`render_markdown()` is a pure function of it, so running it twice on the same data is
byte-identical (Global/Patterns.md: "a generator that carries a COPY of its authority document
diverges silently — derive it, or hard-gate the drift").
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness

DEFAULT_CASES = Path(__file__).resolve().parent / "cases.json"
MUTANTS = ("vault_git", "data_integrity", "bash_partition", "d1_whole_file_write", "write_partition")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    ap.add_argument("--mutant", choices=MUTANTS, default=None,
                    help="disable exactly one named rule (monkeypatch) and expect at least one miss")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "results",
                    help="directory for results.json / results.md (default: ./results next to this script)")
    ap.add_argument("--id", default=None, help="run a single case by id (debugging)")
    args = ap.parse_args()

    cases = harness.load_cases(args.cases)
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]
        if not cases:
            print(f"no case with id {args.id!r}", file=sys.stderr)
            return 2

    results = harness.run_all(cases, mutant=args.mutant)
    s = harness.summarize(results)

    args.out.mkdir(parents=True, exist_ok=True)
    suffix = f".mutant-{args.mutant}" if args.mutant else ""
    (args.out / f"results{suffix}.json").write_text(
        json.dumps({"mutant": args.mutant, "summary": s, "results": results}, indent=2) + "\n",
        encoding="utf-8")
    md = harness.render_markdown(results, mutant=args.mutant)
    (args.out / f"results{suffix}.md").write_text(md, encoding="utf-8")
    print(md)

    if args.mutant:
        if s["all_ok"]:
            print(f"MUTANT DID NOT BITE — rule {args.mutant!r} not covered by any case in {args.cases.name}")
            return 1
        print(f"mutant {args.mutant!r} bit: {len(s['misses'])} case(s) now wrong "
             f"({', '.join(s['misses'][:10])}{'…' if len(s['misses']) > 10 else ''})")
        return 0

    if not s["all_ok"]:
        print(f"{len(s['misses'])} MISS(ES): {', '.join(s['misses'])}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
