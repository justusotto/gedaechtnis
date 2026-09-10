#!/usr/bin/env python3
"""recall_bench/run.py — WP6 skeleton: score `recall.py` against a held-out question set.

    python3 run.py --arm grep --vault DIR [--questions FILE] [--out DIR] [--limit 3]
    python3 run.py --arm index --vault DIR    # -> NotImplementedError, on purpose (see below)
    python3 run.py --arm walk  --vault DIR    # -> NotImplementedError, on purpose

Three arms, per DESIGN.md §4.4: **(a) grep** — today's `recall.py`, implemented here; **(b) index**
— an index-first lookup; **(c) walk** — a link-walk from the region Map. Both (b) and (c) are
WP6's DEFERRED work (DESIGN.md §8, R7) and raise `NotImplementedError` rather than a silent
fallback to grep — a bench that quietly ran arm (a) under arm (b)'s name would report a real
number for a claim nobody has actually tested, which is worse than refusing outright.

**No model call, ever.** This bench measures a lexical search tool against a fixed corpus; nothing
here talks to `claude` or any other model, dry-run or otherwise — `stub_claude.py` belongs to
`memory_eval/`, not this bench.

**What "grep" measures**, exactly, and why: see `PREREGISTRATION.md` in this folder, which also
carries the pre-registered ≥30%/≥70% claims this bench exists to (eventually) decide, quoted from
DESIGN.md §4.4 unchanged.

**Reproducibility.** The results table (`results.md`) is never typed by hand — `render_markdown()`
below is a pure function of the JSONL rows, and running it twice on the same `results.jsonl` must
produce byte-identical output; that equality is exactly what `tests/test_eval_skeletons.py` checks
(the general "a report generated from an authority document must be DERIVED, not copied" rule —
`Global/Patterns.md`).
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN))
import recall  # noqa: E402  (the real, unmodified recall.py — this bench never reimplements it)

ARMS = ("grep", "index", "walk")
DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "questions.example.json"


def load_questions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path}: expected a non-empty JSON array of question rows")
    for row in data:
        for key in ("question", "expected", "region"):
            if key not in row:
                raise ValueError(f"{path}: a row is missing {key!r}: {row}")
    return data


def normalize_heading(heading: str) -> str:
    """Strip the leading `#`s `recall.py` keeps on a heading, so `## Foo` and `Foo` compare equal."""
    return heading.lstrip("#").strip()


def score_question(vault: Path, row: dict, limit: int, max_bytes: int,
                   include_queues: bool = False, ranking: str = "a-prime") -> dict:
    """One question, scored against arm (a). Bytes-to-answer sums every shown hit's body up to
    and including the first one that matches `expected` (see PREREGISTRATION.md for the exact
    definition); when the answer is never found, the reader paid for all `limit` hits and got
    nothing, which is the correct, unforgiving number for that case."""
    file_part, _, heading_part = row["expected"].partition("#")
    t0 = time.monotonic()
    hits, _want = recall.search(vault, row["question"], include_queues=include_queues,
                                ranking=ranking)
    wall_ms = (time.monotonic() - t0) * 1000
    top = hits[:limit]
    bytes_read = 0
    hit_rank = None
    for i, h in enumerate(top):
        bytes_read += len(h["body"].encode("utf-8"))
        if h["path"] == file_part and normalize_heading(h["heading"]) == heading_part:
            hit_rank = i + 1
            break
    # Where the expected entry sits in the WHOLE hit list, not just the top N — `null` when the
    # search never found it at all. recall@N alone cannot tell "the ranking buried it at 30" from
    # "it is not in the corpus", and on 2026-09-10 that distinction was the entire finding: 0 of
    # 30 with the right entry present every time. Recording it here means the number comes out of
    # the run directory instead of a second script nobody kept.
    full_rank = next((i + 1 for i, h in enumerate(hits)
                      if h["path"] == file_part and normalize_heading(h["heading"]) == heading_part),
                     None)
    return {
        "question": row["question"], "expected": row["expected"], "region": row.get("region", ""),
        "hit_rank": hit_rank, "recall_at_limit": hit_rank is not None, "full_rank": full_rank,
        "bytes_to_answer": bytes_read, "total_hits": len(hits), "wall_ms": round(wall_ms, 3),
    }


def score_grep(vault: Path, questions: list[dict], limit: int, max_bytes: int,
               include_queues: bool = False, ranking: str = "a-prime") -> list[dict]:
    """Arm (a) / (a′). The two knobs are the arm's CONFIGURATION, not a reimplementation: both are
    forwarded to the real `recall.search`, so a bench row can never be scored against a ranking
    that recall.py does not itself ship. `ranking="flat"` reproduces the pre-2026-09-10 order
    exactly, which is what makes an exclusion-only run readable against a rank-only run."""
    return [score_question(vault, row, limit, max_bytes, include_queues, ranking)
            for row in questions]


def aggregate(rows: list[dict]) -> dict:
    """`.get("full_rank")` on purpose: JSONL written before 2026-09-10 has no such column, and a
    result file must stay re-renderable after the instrument grows a field — otherwise the first
    schema change quietly retires every run already on disk."""
    n = len(rows) or 1
    found = sorted(r["full_rank"] for r in rows if r.get("full_rank") is not None)
    return {
        "n_questions": len(rows),
        "recall_at_limit_rate": sum(1 for r in rows if r["recall_at_limit"]) / n,
        "mean_bytes_to_answer": sum(r["bytes_to_answer"] for r in rows) / n,
        "mean_wall_ms": sum(r["wall_ms"] for r in rows) / n,
        "expected_present": len(found),
        "median_full_rank": found[len(found) // 2] if found else None,
    }


def floor_between(agg1: dict, agg2: dict) -> dict:
    """Per-metric noise floor: |value(run 1) - value(run 2)| for each numeric aggregate metric —
    see PREREGISTRATION.md. `recall.py` is deterministic, so this is expected to be 0; that is the
    correct number for a deterministic instrument, not evidence the check is vacuous."""
    return {k: abs(agg1[k] - agg2[k]) for k in agg1 if isinstance(agg1[k], (int, float))
            and isinstance(agg2.get(k), (int, float))}


def render_markdown(rows: list[dict], summary: dict | None = None, floor: dict | None = None) -> str:
    """Pure function of the rows (+ optional summary/floor): call it twice on the same input and
    get byte-identical output. This is the ONLY place a markdown table is produced — `main()`
    calls it, and so does the test that proves the on-disk `.md` is a true regeneration."""
    lines = ["| # | region | question | recall_at_limit | hit_rank | full_rank | bytes_to_answer | wall_ms |",
             "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        q = r["question"].replace("|", "\\|")
        lines.append(f"| {i} | {r.get('region', '')} | {q} | {r['recall_at_limit']} | "
                     f"{r['hit_rank'] if r['hit_rank'] is not None else '-'} | "
                     f"{r.get('full_rank') if r.get('full_rank') is not None else '-'} | "
                     f"{r['bytes_to_answer']} | {r['wall_ms']} |")
    if summary is not None:
        lines.append("")
        lines.append(f"**recall@limit rate:** {summary['recall_at_limit_rate']:.3f} · "
                     f"**mean bytes-to-answer:** {summary['mean_bytes_to_answer']:.1f} · "
                     f"**mean wall ms:** {summary['mean_wall_ms']:.3f} · "
                     f"**n:** {summary['n_questions']} · "
                     f"**expected entry present:** {summary.get('expected_present', '-')} · "
                     f"**median rank of it:** {summary.get('median_full_rank', '-')}")
    if floor is not None:
        lines.append(f"**noise floor (repeat control):** " +
                     ", ".join(f"{k}={v:.4f}" for k, v in sorted(floor.items())))
    return "\n".join(lines) + "\n"


def write_jsonl(path: Path, rows: list[dict], floor: dict | None = None) -> None:
    """Every per-question row, plus — when given — ONE trailing `{"summary_floor": {...}}` row.
    The floor is not derivable from the question rows alone (it needs the repeat run's own
    aggregate, which is not itself a per-question fact), so it has to round-trip through the
    JSONL too, or `render_from_jsonl` could never reproduce `results.md` byte-for-byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
        if floor is not None:
            fh.write(json.dumps({"summary_floor": floor}) + "\n")


def render_from_jsonl(jsonl_path: Path) -> str:
    """Re-derive the markdown table from a JSONL file already on disk — used by the test that
    proves `results.md` is a true regeneration and not hand-typed or drifted. `main()` below
    calls this SAME function to produce `results.md` in the first place, so the two can never
    drift apart by construction."""
    lines = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    floor = next((l["summary_floor"] for l in lines if "summary_floor" in l), None)
    rows = [l for l in lines if "summary_floor" not in l]
    summary = aggregate(rows) if rows else None
    return render_markdown(rows, summary=summary, floor=floor)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WP6 recall-bench skeleton: score recall.py, or refuse an unbuilt arm.")
    ap.add_argument("--arm", choices=ARMS, help="required, unless --render-only")
    ap.add_argument("--vault", help="the vault (or throwaway fixture) to search; required unless --render-only")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="question-set JSON (default: the shipped example)")
    ap.add_argument("--out", default=None, help="output directory for results.jsonl/.md (default: ./results next to this script)")
    ap.add_argument("--limit", type=int, default=3, help="recall@N (default 3, matching the pre-registered claim)")
    ap.add_argument("--include-queues", action="store_true",
                    help="forwarded to recall.search: also search Pharos/ and Channels/ "
                         "(recall.py excludes them by default since 2026-09-10)")
    ap.add_argument("--rank", choices=recall.RANKINGS, default="a-prime",
                    help="forwarded to recall.search: `flat` is the pre-2026-09-10 order — the "
                         "negative control for arm (a′) — and `length-only` is a′ without its "
                         "measured stoplist, which attributes a change to one half of the rank")
    ap.add_argument("--max-bytes", type=int, default=6000, help="forwarded conceptually to match recall.py's own default cap")
    ap.add_argument("--render-only", default=None, metavar="JSONL",
                    help="skip the bench entirely: regenerate results.md from an existing JSONL and exit. "
                         "Exists so 'derived, never typed' can be exercised (and tested) as a standalone "
                         "command-line step, not only as an internal function call.")
    a = ap.parse_args(argv)

    if a.render_only:
        out = Path(a.out).expanduser().resolve() if a.out else Path(a.render_only).expanduser().resolve().parent
        md = render_from_jsonl(Path(a.render_only).expanduser().resolve())
        (out / "results.md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    if not a.arm or not a.vault:
        ap.error("--arm and --vault are required unless --render-only is given")

    if a.arm in ("index", "walk"):
        raise NotImplementedError(
            f"recall_bench: arm {a.arm!r} is not built yet. See DESIGN.md §4.3 (\"the winner: role "
            "files as containers, entries as the unit, one derived index\") and §4.4 (WP6). Only "
            "'grep' — today's recall.py — is implemented in this skeleton; run.py refuses to fall "
            "back to it silently under another arm's name.")

    vault = Path(a.vault).expanduser().resolve()
    if not vault.is_dir():
        print(f"recall_bench: no vault at {vault}", file=sys.stderr)
        return 2
    questions = load_questions(Path(a.questions).expanduser().resolve())

    cfg = dict(include_queues=a.include_queues, ranking=a.rank)
    run1 = score_grep(vault, questions, a.limit, a.max_bytes, **cfg)
    run2 = score_grep(vault, questions, a.limit, a.max_bytes, **cfg)   # repeat control, byte-identical config
    agg1, agg2 = aggregate(run1), aggregate(run2)
    floor = floor_between(agg1, agg2)

    out = Path(a.out).expanduser().resolve() if a.out else Path(__file__).resolve().parent / "results"
    write_jsonl(out / "results.jsonl", run1, floor=floor)
    md = render_from_jsonl(out / "results.jsonl")   # regenerate, never hand-assemble — see its docstring
    (out / "results.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"noise floor (repeat control): {json.dumps(floor)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
