#!/usr/bin/env python3
"""views.py — regenerate each role file FROM THE LOG, under byte-identical headings.

    python3 views.py                 # write every view the log can build
    python3 views.py --list          # say what would be written, write nothing
    python3 views.py --region Speculum --stem Canon

Output goes to `<vault>/.gedaechtnis/views/<Region>/<Stem>.md` — never over the live file. For the
30 additive days the live file remains the memory and the view is only evidence about whether it
could stop being.

Shape of a generated view:

    # GENERATED sha256:<hash of the row ids it was built from>
    <the live file's frontmatter and preamble, byte for byte>
    <each entry, in the live file's own order, rebuilt from its row>

Two things come from the LIVE file and not from the log: the frontmatter/preamble, and the ORDER of
the headings. **The shadow reproduces; it does not reorder.** A generator that sorted its rows would
score the same fidelity per entry and hand back a file no reader recognises, and the difference
between those two outcomes is exactly what the 30 days are for.

The `# GENERATED` first line is not decoration. `hooks/gate.py` refuses any Edit, Write, shell
redirect or `sed -i` against a file whose first line starts with it, and tells the actor to append a
row instead — so an edit made in the wrong place becomes a row rather than a lost edit. During the
shadow that door only ever bites under `.gedaechtnis/views/`, because nothing else carries the line.
"""
from __future__ import annotations
import argparse, hashlib, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logstore
import importer
from logstore import VAULT, VIEW_DIR

GENERATED_PREFIX = "# GENERATED sha256:"


def rows_by_file(rows=None) -> dict:
    """-> {(region, stem): [row]} in log order."""
    out = {}
    for r in rows if rows is not None else logstore.all_rows():
        out.setdefault((r["region"], r["stem"]), []).append(r)
    return out


def _ordinal(row) -> int:
    for f in row["flags"]:
        if f.startswith("ord="):
            try:
                return int(f[4:])
            except ValueError:
                return 10 ** 9
    return 10 ** 9


def build(region: str, stem: str, rows: list, vault: Path = None) -> tuple:
    """-> (text, used_row_ids, missing_headings). Raises FileNotFoundError when the live file is gone.

    The live file supplies the preamble and the heading order; every entry BODY comes from a row. A
    heading the log has no row for is left out and REPORTED — a generator that quietly copied the
    live body for a missing row would score 100% fidelity while carrying nothing.
    """
    vault = vault or VAULT
    live = (vault / region / f"{stem}.md") if region != "." else (vault / f"{stem}.md")
    text = live.read_text(encoding="utf-8")
    preamble, entries = importer.split_entries(text)

    by_heading = {}
    for r in sorted(rows, key=_ordinal):
        by_heading.setdefault(r["heading"], []).append(r)
    taken = {}

    parts = []
    used = []
    missing = []
    for heading, _body in entries:
        pool = by_heading.get(heading, [])
        k = taken.get(heading, 0)
        taken[heading] = k + 1
        if k < len(pool):
            row = pool[k]
        elif pool:
            row = pool[0]                    # a repeated heading the log saw once: reuse its row
        else:
            missing.append(heading)
            continue
        parts.append(importer.reconstruct(row["heading"], row["body"]))
        used.append(row["id"])

    digest = hashlib.sha256("\n".join(used).encode("utf-8")).hexdigest()
    head = f"{GENERATED_PREFIX}{digest}\n"
    return head + preamble + "".join(parts), used, missing


def view_path(region: str, stem: str) -> Path:
    return (VIEW_DIR / region / f"{stem}.md") if region != "." else (VIEW_DIR / f"{stem}.md")


def generate(only_region: str = "", only_stem: str = "", list_only: bool = False) -> dict:
    grouped = rows_by_file()
    written = []
    skipped = []
    missing_total = 0
    for (region, stem), rows in sorted(grouped.items()):
        if only_region and region != only_region:
            continue
        if only_stem and stem != only_stem:
            continue
        try:
            text, used, missing = build(region, stem, rows)
        except OSError as e:
            skipped.append((f"{region}/{stem}", f"live file unreadable: {e}"))
            continue
        missing_total += len(missing)
        if missing:
            skipped.append((f"{region}/{stem}",
                            f"{len(missing)} heading(s) in the live file have no row"))
        p = view_path(region, stem)
        if not list_only:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        written.append({"region": region, "stem": stem, "path": str(p),
                        "rows": len(used), "bytes": len(text.encode("utf-8"))})
    return {"views": written, "skipped": skipped, "headings_without_a_row": missing_total,
            "view_dir": str(VIEW_DIR)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", default="")
    ap.add_argument("--stem", default="", choices=("",) + logstore.STEMS)
    ap.add_argument("--list", action="store_true", dest="list_only")
    args = ap.parse_args()
    res = generate(args.region, args.stem, args.list_only)
    verb = "would write" if args.list_only else "wrote"
    total_rows = sum(v["rows"] for v in res["views"])
    print(f"{verb} {len(res['views'])} view(s), {total_rows} row(s) rendered, under {res['view_dir']}")
    for v in res["views"]:
        print(f"  {v['region']}/{v['stem']:9} rows={v['rows']:5} bytes={v['bytes']:8}")
    if res["skipped"]:
        print("NOTE:")
        for name, why in res["skipped"]:
            print(f"  {name}: {why}")
    if not res["views"]:
        raise SystemExit("REFUSED: no view was built. The log is empty — run importer.py first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
