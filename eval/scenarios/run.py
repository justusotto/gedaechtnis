#!/usr/bin/env python3
"""run.py — sweep the split-rule scenario set across rule variants; write RUNS-ready tables + JSON.

    python3 run.py --out results/run-1            # every scenario x every variant
    python3 run.py --only S04,S08 --variants floor4,share20
    python3 run.py --mutant no_topics ...          # the generator writes noise only: every 'split'
                                                   # scenario must go quiet, or the harness is not
                                                   # reading the product

**What a variant is.** A limits FILE (`harness.propose`): the shipped rule is exercised the way a
person re-tuning `rules/limits.json` exercises it. A knob the rule under test does not carry is
reported as `n/a` for that variant — the product refused to echo it (`harness.UnsupportedKnob`) — never
as "no effect".

**What the tables are.** One cell per scenario x variant: the product's proposal kind(s) with the
numbers (cluster size -> remaining, parent share). A second table scores the DELTA against each
scenario's `want` (`scenarios.py`) — `ok`, `WRONG`, `owner` (the case is the taste question), or `-`
(reported, not scored). Growth scenarios add a FLIPS column: how many times the kind changed across
steps.

No model is called anywhere here. The plugin's rule is deterministic; a run costs subprocess time.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _sibling import load as _load  # noqa: E402

H = _load("harness")
SC = _load("scenarios")

VARIANTS = {
    "floor0": {"split_min_remaining_entries": 0},
    "floor2": {"split_min_remaining_entries": 2},
    "floor4": {"split_min_remaining_entries": 4},          # SHIPPED (3b7c5c0c)
    "floor6": {"split_min_remaining_entries": 6},
    "floor8": {"split_min_remaining_entries": 8},
    "share20": {"split_min_remaining_entries": 0, "split_min_remaining_share": 0.2},
    "share25": {"split_min_remaining_entries": 0, "split_min_remaining_share": 0.25},
    "floor4+share20": {"split_min_remaining_entries": 4, "split_min_remaining_share": 0.2},
    "floor4+share25": {"split_min_remaining_entries": 4, "split_min_remaining_share": 0.25},
    # The axis SWITCHED OFF, which is the pre-SPLITVOCAB-1 rule exactly (with an empty term set
    # `cluster()` takes the same path it always took). It is the control for S22/S23: a scenario
    # that scores `ok` under both variants is not testing the axis.
    "floor4+noboiler": {"split_min_remaining_entries": 4, "split_boilerplate_region_share": 0.0},
    "floor4+coh50": {"split_min_remaining_entries": 4, "split_min_cohesion": 0.5},
    "floor4+coh75": {"split_min_remaining_entries": 4, "split_min_cohesion": 0.75},
    "floor4+share25+coh50": {"split_min_remaining_entries": 4, "split_min_remaining_share": 0.25,
                             "split_min_cohesion": 0.5},
}

MUTANTS = {
    # The generator writes NOISE ONLY (every topic's entries get unique vocabulary). Every scenario
    # whose want is a split/refocus must come back `nothing` — proving the tables come from the
    # product reading the fixture, not from the scenario's own labels.
    "no_topics": "SPLITSIM_MUTANT_NO_TOPICS",
}


def cell(summary: dict) -> str:
    if summary is None:
        return "n/a"
    if "error" in summary:
        return summary["error"]
    ps = summary.get("proposals") or []
    if len(ps) == 1 and "size" in ps[0] and "remaining" in ps[0]:
        p = ps[0]
        share = f" ({p['remaining_share']:.0%})" if p.get("remaining_share") is not None else ""
        extra = f" coh {p['cohesion']:.2f}" if "cohesion" in p else ""
        return f"{p['kind']} {p['size']}->{p['remaining']}{share}{extra}"
    return summary["label"]


# ★ SCENARIOS WHOSE WHOLE SUBJECT IS THE VAULT-WIDE AXIS (SPLITVOCAB-1, second reviewer round).
# `run.py`'s own note above states the diagnostic — "a scenario that scores `ok` under both
# variants is not testing the axis" — and for two rounds nothing enforced it. A fixture can go
# quiet by DEGREE as well as by absence: drop the focus region's convention from 60% of its entries
# to 10% and the weld never forms, so S22 and S23 both pass with the axis ON and with it OFF, and
# the sweep prints the tally the row was accepted on. The precondition asserts are inside
# `run_multi` and cannot see across variants; this can, because both scores are in hand here.
AXIS_SCENARIOS = SC.AXIS_SCENARIOS          # declared in scenarios.py, where they are written
AXIS_OFF_VARIANT = "floor4+noboiler"


def axis_delta_missing(results: dict, sid: str, variants: list[str]) -> bool:
    """True when an axis scenario scored the SAME with the axis on and off in this run."""
    if sid not in AXIS_SCENARIOS or AXIS_OFF_VARIANT not in variants:
        return False
    want = next((s[2] for s in SC.SCENARIOS if s[0] == sid), None)
    off = results["cells"].get(sid, {}).get(AXIS_OFF_VARIANT)
    on = [v for v in variants if v != AXIS_OFF_VARIANT
          and (results["variants"].get(v) or {}).get("split_boilerplate_region_share") != 0.0]
    return bool(on) and all(score(want, results["cells"].get(sid, {}).get(v), delta=False)
                            == score(want, off, delta=False) for v in on)


def score(want, summary: dict | None, delta: bool = True) -> str:
    if want is None:
        return "-"
    if summary is None:
        return "n/a"
    if delta and summary.get("axis_delta_missing"):
        return "VACUOUS (same with the axis off)"
    if delta and summary.get("axis_unchecked"):
        # ★ THE GUARD'S OWN ABSENCE, SAID OUT LOUD. Without the axis-off variant in the run there
        # is no delta to take, so this scenario's `ok` means only "it scored what the reader
        # wanted" — which a vacuous fixture also does. A targeted re-run (`--variants floor4`) is
        # the ordinary way anyone pokes at these two, and it was the one shape where the invariant
        # silently did not exist.
        return "n/a (no axis-off variant in this run)"
    if "error" in summary:
        return "ERROR"
    if want == SC.OWNER:
        return "owner"
    label = summary["label"]
    if want == "no move":
        return "ok" if label in ("nothing", "diffuse") else "WRONG"
    if want == "split on its own vocabulary":
        # Kind AND name: the defect this scores is a room proposed under the vault's house style,
        # which is a `split` exactly like the right answer is.
        if label != "split":
            return "WRONG"
        return "WRONG (named from the convention)" if summary.get("named_from_convention") else "ok"
    if want == "stable":
        return "ok" if summary.get("flips", 0) == 0 else f"WRONG ({summary['flips']} flips)"
    if want.startswith("child:"):
        return "ok" if ("child regrown: split 8->8" in label and "parent: nothing" in label) \
            else "WRONG"
    if want == "2 x split then nothing":
        trail = summary.get("trail", [])
        return "ok" if trail and trail[-1] == "nothing" and trail[0] == "3 x split" else "WRONG"
    return "ok" if label == want else "WRONG"


def run_all(only: set | None, variants: list[str], root: Path, mutant: str | None) -> dict:
    env_backup = dict(os.environ)
    if mutant:
        os.environ[MUTANTS[mutant]] = "1"
    results: dict = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "variants": {}, "cells": {},
                     "mutant": mutant}
    try:
        for v in variants:
            limits = VARIANTS[v]
            results["variants"][v] = limits
            for sid, title, want, why, driver in SC.SCENARIOS:
                if only and sid not in only:
                    continue
                here = root / v / sid
                here.mkdir(parents=True, exist_ok=True)
                try:
                    summary = driver(here, limits)
                except H.UnsupportedKnob as e:
                    summary = None
                    results.setdefault("unsupported", {})[v] = str(e)
                except Exception as e:  # a harness error is a result, never a silent gap
                    summary = {"label": "ERROR", "error": f"ERROR {e.__class__.__name__}: {e}"[:160]}
                results["cells"].setdefault(sid, {})[v] = summary
    finally:
        os.environ.clear()
        os.environ.update(env_backup)
    axis_blind = AXIS_OFF_VARIANT not in variants
    for sid in AXIS_SCENARIOS:
        if sid not in results["cells"]:
            continue
        for cell_ in results["cells"][sid].values():
            if cell_ is None:
                continue
            if axis_blind:
                cell_["axis_unchecked"] = True
            elif axis_delta_missing(results, sid, variants):
                cell_["axis_delta_missing"] = True
    results["axis_scenarios"] = sorted(AXIS_SCENARIOS)
    results["axis_unchecked"] = axis_blind and bool(AXIS_SCENARIOS & set(results["cells"]))
    results["scenarios"] = [{"id": s[0], "title": s[1], "want": s[2], "why": s[3]}
                            for s in SC.SCENARIOS if not only or s[0] in only]
    return results


def render(results: dict) -> str:
    variants = list(results["variants"])
    out = [f"# Scenario sweep — {results['generated']}" + (f" — MUTANT {results['mutant']}"
                                                            if results.get("mutant") else ""), ""]
    if results.get("unsupported"):
        out.append("Variants the rule under test does NOT carry (product echoed no such threshold; "
                   "reported as `n/a`, never as no effect):")
        for v, msg in results["unsupported"].items():
            out.append(f"- `{v}`: {msg}")
        out.append("")
    out.append("## Proposals per scenario x variant")
    out.append("")
    out.append("| id | scenario | want | " + " | ".join(variants) + " |")
    out.append("|---|---|---|" + "---|" * len(variants))
    for s in results["scenarios"]:
        cells = results["cells"].get(s["id"], {})
        out.append(f"| {s['id']} | {s['title']} | {s['want'] or '-'} | "
                   + " | ".join(cell(cells.get(v)) for v in variants) + " |")
    out.append("")
    if results.get("axis_unchecked"):
        out.append("★ **This run cannot check the vault-wide axis.** "
                   f"`{AXIS_OFF_VARIANT}` is not among its variants, so "
                   f"{', '.join(sorted(AXIS_SCENARIOS & set(results['cells'])))} have no "
                   "axis-off score to be compared against and are reported `n/a` rather than "
                   "scored: a fixture that has gone quiet would score `ok` here.")
        out.append("")
    out.append("## Delta against the sensible reader (`ok` / `WRONG` / `owner` / `-`)")
    out.append("")
    out.append("| id | want | " + " | ".join(variants) + " |")
    out.append("|---|---|" + "---|" * len(variants))
    tally = {v: {"ok": 0, "WRONG": 0} for v in variants}
    for s in results["scenarios"]:
        cells = results["cells"].get(s["id"], {})
        row = []
        for v in variants:
            sc = score(s["want"], cells.get(v))
            row.append(sc)
            if sc == "ok":
                tally[v]["ok"] += 1
            elif sc.startswith("WRONG"):
                tally[v]["WRONG"] += 1
        out.append(f"| {s['id']} | {s['want'] or '-'} | " + " | ".join(row) + " |")
    out.append("| **tally** | ok / WRONG | " + " | ".join(f"{t['ok']} / {t['WRONG']}"
                                                          for t in tally.values()) + " |")
    out.append("")
    # Growth detail
    for sid in ("S14", "S15"):
        cells = results["cells"].get(sid)
        if not cells:
            continue
        out.append(f"## {sid} — kind per step, then flips")
        out.append("")
        for v in variants:
            sm = cells.get(v)
            if sm and "steps" in sm:
                out.append(f"- `{v}`: {sm['label']} — **{sm['flips']} flips**, same cluster id "
                           f"throughout: {sm['same_cluster_id_throughout']}")
            else:
                out.append(f"- `{v}`: {cell(sm)}")
        out.append("")
    # Real vault detail
    for sid in ("S19", "S20"):
        cells = results["cells"].get(sid)
        if not cells:
            continue
        out.append(f"## {sid} — per region, per variant")
        out.append("")
        for v in variants:
            sm = cells.get(v)
            if not sm or "error" in sm:
                out.append(f"- `{v}`: {cell(sm)}")
                continue
            rows = sm.get("proposals") or sm.get("regions") or []
            parts = []
            for r in rows:
                if "kind" in r:
                    parts.append(f"{r['region']} {r['kind']} {r['size']}/{r['n']} "
                                 f"({r['remaining_share']:.0%}"
                                 + (f", coh {r['cohesion']:.2f}" if "cohesion" in r else "") + ")")
                else:
                    parts.append(f"{r['region']} {r['label']}")
            out.append(f"- `{v}` — {sm['label']}: " + "; ".join(parts))
        out.append("")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="directory for results.json + results.md")
    ap.add_argument("--only", help="comma-separated scenario ids")
    ap.add_argument("--variants", help="comma-separated variant names (default: all)")
    ap.add_argument("--mutant", choices=sorted(MUTANTS))
    ap.add_argument("--work", help="sandbox root (default: a fresh temp dir)")
    args = ap.parse_args(argv)
    variants = args.variants.split(",") if args.variants else list(VARIANTS)
    only = set(args.only.split(",")) if args.only else None
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="splitsim-"))
    results = run_all(only, variants, work, args.mutant)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    md = render(results)
    (out / "results.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"sandbox: {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
