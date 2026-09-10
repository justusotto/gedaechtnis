#!/usr/bin/env python3
"""views.py — regenerate each role file FROM THE LOG, under byte-identical headings.

    python3 views.py                 # write every view the log can build
    python3 views.py --list          # say what would be written, write nothing
    python3 views.py --json          # the same, machine-readable (orphan counts included)
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

**Every rendered ENTRY comes from a row, orphans included** (council 3, M-03). A row whose heading
has no live counterpart — an entry deleted, or a heading renamed — used to be silently undrawable;
it now renders at the end of its file and is COUNTED. The count is the number to watch: orphans are
what the live file has dropped and the log still carries, and a view carrying one is no longer
byte-identical to the file it shadows.

The `# GENERATED` first line is not decoration. `hooks/gate.py` refuses any Edit, Write, shell
redirect or `sed -i` against a file whose first line starts with it, and tells the actor to append a
row instead — so an edit made in the wrong place becomes a row rather than a lost edit. During the
shadow that door only ever bites under `.gedaechtnis/views/`, because nothing else carries the line.
"""
from __future__ import annotations
import argparse, hashlib, json, sys
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


def _newest(pool: list, k: int):
    """THE NEWEST ROW WINS. An entry the owner rewrites in the live file arrives as a SECOND row
    under the same heading and the same ordinal — the log is append-only, so a correction is a new
    row and never an edit. Picking pool[k] positionally would silently render the SUPERSEDED text
    and score it as a fidelity miss with no hint of the cause (observed live during checkpoint 0,
    when two `Speculum/Position` entries were rewritten mid-run). Ordinal first — that is what
    distinguishes two genuinely different entries sharing a heading; timestamp second — that is what
    distinguishes a correction from its original."""
    at_k = [r for r in pool if _ordinal(r) == k]
    return max(at_k or pool, key=lambda x: (x["ts"], x.get("seq", 0)))


def build(region: str, stem: str, rows: list, vault: Path = None) -> tuple:
    """-> (text, used_row_ids, missing_headings, orphan_headings). FileNotFoundError if the live file is gone.

    The live file supplies the preamble and the heading order; **every rendered entry comes from a
    ROW.** A heading the log has no row for is left out and REPORTED — a generator that quietly
    copied the live body for a missing row would score 100% fidelity while carrying nothing.

    ORPHANS (council 3, M-03, fixed here). Until 2026-09-10 this loop walked the LIVE file's
    headings and nothing else, so a row whose heading is absent from the live file could never
    render: an entry the owner deleted, or a heading a session renamed, was carried by the log and
    invisible in the view, and the fidelity number could not see it either. Orphan rows now RENDER,
    appended after the live entries in their own ordinal order, and their count is reported.

    Appended at the END rather than at the ordinal the row remembers, because that ordinal is a
    position in a file that no longer has that entry: putting it back there is a guess about where
    it went, and appending it is not. The consequence is deliberate and must not be smoothed over —
    a view with an orphan in it is NO LONGER byte-identical to its live file, so the whole-file
    identity count falls by exactly the files that have one. That fall is information: it says the
    log is carrying something the memory has dropped.
    """
    vault = vault or VAULT
    live = (vault / region / f"{stem}.md") if region != "." else (vault / f"{stem}.md")
    text = live.read_text(encoding="utf-8")
    preamble, entries = importer.split_entries(text)

    entry_rows = [r for r in rows if not logstore.is_shape(r)]
    by_heading = {}
    for r in sorted(entry_rows, key=lambda x: (_ordinal(x), x["ts"], x.get("seq", 0))):
        by_heading.setdefault(r["heading"], []).append(r)
    taken = {}

    parts = []
    used = []
    missing = []
    for heading, _body in entries:
        pool = by_heading.get(heading, [])
        k = taken.get(heading, 0)
        taken[heading] = k + 1
        if not pool:
            missing.append(heading)
            continue
        row = _newest(pool, k)
        parts.append(importer.reconstruct(row["heading"], row["body"]))
        used.append(row["id"])

    live_headings = {h for h, _b in entries}
    orphans = []
    for heading, pool in sorted(by_heading.items(), key=lambda kv: (_ordinal(kv[1][0]), kv[0])):
        if heading in live_headings:
            continue
        row = max(pool, key=lambda x: (x["ts"], x.get("seq", 0)))
        parts.append(importer.reconstruct(row["heading"], row["body"]))
        used.append(row["id"])
        orphans.append(heading)

    digest = hashlib.sha256("\n".join(used).encode("utf-8")).hexdigest()
    head = f"{GENERATED_PREFIX}{digest}\n"
    return head + preamble + "".join(parts), used, missing, orphans


def view_path(region: str, stem: str) -> Path:
    return (VIEW_DIR / region / f"{stem}.md") if region != "." else (VIEW_DIR / f"{stem}.md")


def generate(only_region: str = "", only_stem: str = "", list_only: bool = False) -> dict:
    grouped = rows_by_file()
    written = []
    skipped = []
    missing_total = 0
    orphan_total = 0
    for (region, stem), rows in sorted(grouped.items()):
        if only_region and region != only_region:
            continue
        if only_stem and stem != only_stem:
            continue
        try:
            text, used, missing, orphans = build(region, stem, rows)
        except OSError as e:
            skipped.append((f"{region}/{stem}", f"live file unreadable: {e}"))
            continue
        missing_total += len(missing)
        orphan_total += len(orphans)
        if missing:
            skipped.append((f"{region}/{stem}",
                            f"{len(missing)} heading(s) in the live file have no row"))
        if orphans:
            skipped.append((f"{region}/{stem}",
                            f"{len(orphans)} ORPHAN row heading(s) with no live counterpart, "
                            f"rendered at the end: {'; '.join(h[:60] for h in orphans[:3])}"
                            + (" …" if len(orphans) > 3 else "")))
        p = view_path(region, stem)
        if not list_only:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        written.append({"region": region, "stem": stem, "path": str(p), "rows": len(used),
                        "orphans": len(orphans), "missing": len(missing),
                        "bytes": len(text.encode("utf-8"))})
    return {"views": written, "skipped": skipped, "headings_without_a_row": missing_total,
            "orphan_rows": orphan_total,
            "orphan_population": ("rows whose heading appears in no live entry of the file they "
                                  "belong to, counted over every (region, stem) the log carries"),
            "view_dir": str(VIEW_DIR)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", default="")
    ap.add_argument("--stem", default="", choices=("",) + logstore.STEMS)
    ap.add_argument("--list", action="store_true", dest="list_only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = generate(args.region, args.stem, args.list_only)
    if args.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        if not res["views"]:
            raise SystemExit("REFUSED: no view was built. The log is empty — run importer.py first.")
        return 0
    verb = "would write" if args.list_only else "wrote"
    total_rows = sum(v["rows"] for v in res["views"])
    print(f"{verb} {len(res['views'])} view(s), {total_rows} row(s) rendered, under {res['view_dir']}")
    for v in res["views"]:
        print(f"  {v['region']}/{v['stem']:9} rows={v['rows']:5} bytes={v['bytes']:8}"
              + (f" orphans={v['orphans']}" if v["orphans"] else ""))
    print(f"orphan rows rendered: {res['orphan_rows']} — {res['orphan_population']}")
    if res["skipped"]:
        print("NOTE:")
        for name, why in res["skipped"]:
            print(f"  {name}: {why}")
    if not res["views"]:
        raise SystemExit("REFUSED: no view was built. The log is empty — run importer.py first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
