#!/usr/bin/env python3
"""views.py — regenerate each role file FROM THE LOG, under byte-identical headings.

    python3 views.py                 # write every view the log can build
    python3 views.py --list          # say what would be written, write nothing
    python3 views.py --json          # the same, machine-readable (orphan counts included)
    python3 views.py --region Speculum --stem Canon
    python3 views.py --cold          # THE COLD BUILD: from rows alone, no live file read at all
    python3 views.py --cold --compare   # cold views vs the live files: identical N/M, or the diff

Output goes to `<vault>/.gedaechtnis/views/<Region>/<Stem>.md` — never over the live file. For the
30 additive days the live file remains the memory and the view is only evidence about whether it
could stop being.

Shape of a generated view:

    # GENERATED sha256:<hash of the row ids it was built from>
    <the live file's frontmatter and preamble, byte for byte>
    <each entry, in the live file's own order, rebuilt from its row>

TWO MODES, and the difference between them is the whole measurement.

**Warm** (the default) takes two things from the LIVE file: the frontmatter/preamble, and the ORDER
of the headings. **The shadow reproduces; it does not reorder.** A generator that sorted its rows
would score the same fidelity per entry and hand back a file no reader recognises, and the
difference between those two outcomes is exactly what the 30 days are for.

**Cold** (`--cold`) takes NOTHING from the live file — preamble and order come from the log's own
`# PREAMBLE` and `# ORDER` shape rows (see `logstore.py`). This is the mode that can actually fail.
Council 3 closed 5–0 (K-3, C-04) that day 0's "72 of 72 byte-identical" could not fail on the
preamble or the heading order, because the warm generator copies both from the very file it is then
compared against. The cold build is the answer to that: run it with the live files moved aside and
`--compare` says byte-identical or shows the diff, per file.

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
import argparse, difflib, hashlib, itertools, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logstore
import importer
import rootguard
import logstore
# VAULT, VIEW_DIR deliberately NOT imported by name: a `from` import binds the value
# ONCE, which is the frozen-path defect this package was bitten by. Read through the
# module object (logstore.VAULT) so PEP 562 re-resolves on every access.

GENERATED_PREFIX = "# GENERATED sha256:"
# The cold build gets its OWN directory rather than overwriting the shadow's ordinary views: the two
# answer different questions, and a cold run that clobbered `views/` would leave nothing to compare.
# ★ Derived from the vault, so resolved PER CALL for the same reason the vault is: a constant
# here would re-freeze the path one level down from the fix.

def cold_dir():
    """The cold build gets its OWN directory rather than overwriting the shadow's ordinary views:
    the two answer different questions, and a cold run that clobbered `views/` would leave nothing
    to compare."""
    return logstore.VAULT / ".gedaechtnis" / "views-cold"


# `VAULT` and `VIEW_DIR` are RE-EXPORTED here: `shadow_score` reads them as `views.VIEW_DIR`, and
# this module used to carry them as `from logstore import ...` bindings. They are forwarded through
# the accessor table rather than re-imported by name, so the forwarding is per-call too — a re-export
# that froze would reintroduce the defect at the seam between two fixed modules.
_ACCESSORS = {"COLD_DIR": cold_dir,
              "VAULT": lambda: logstore.VAULT,
              "VIEW_DIR": lambda: logstore.VIEW_DIR}


def __getattr__(name: str):
    fn = _ACCESSORS.get(name)
    if fn is not None:
        return fn()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


def _newest(pool: list, index: int):
    """THE NEWEST ROW WINS. An entry the owner rewrites in the live file arrives as a SECOND row
    under the same heading — the log is append-only, so a correction is a new row and never an edit.
    Picking positionally would silently render the SUPERSEDED text and score it as a fidelity miss
    with no hint of the cause (observed live during checkpoint 0, when two `Speculum/Position`
    entries were rewritten mid-run). Ordinal first — that is what distinguishes two genuinely
    different entries sharing a heading; timestamp second — that is what distinguishes a correction
    from its original.

    `index` is the entry's position IN ITS FILE, which is what `ord=N` records. Until 2026-09-10
    this argument was instead a count of how many times that heading had already been seen, and the
    two agree only for a unique heading at position 0 — so a rewritten FIRST entry that had since
    been pushed down the file matched its own STALE row (`ord=0`) and not its current one, and the
    view rendered 2,317 bytes of superseded text under a heading whose live body was one newline.
    Found by the cold build on `Speculum/Errata.md`; it is the only class of mismatch in that probe
    that was not a real orphan.

    Where no row carries this position — every ordinal in the log is stale after an insertion above
    it — the whole pool is the candidate set and the newest row wins, which is the right answer for
    a heading that occurs once.
    """
    at_index = [r for r in pool if _ordinal(r) == index]
    return max(at_index or pool, key=lambda x: (x["ts"], x.get("seq", 0)))


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
    vault = vault or logstore.VAULT
    live = (vault / region / f"{stem}.md") if region != "." else (vault / f"{stem}.md")
    text = live.read_text(encoding="utf-8")
    preamble, entries = importer.split_entries(text)

    entry_rows = [r for r in rows if not logstore.is_shape(r)]
    by_heading = {}
    for r in sorted(entry_rows, key=lambda x: (_ordinal(x), x["ts"], x.get("seq", 0))):
        by_heading.setdefault(r["heading"], []).append(r)
    parts = []
    used = []
    missing = []
    for i, (heading, _body) in enumerate(entries):
        pool = by_heading.get(heading, [])
        if not pool:
            missing.append(heading)
            continue
        row = _newest(pool, i)
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


def build_cold(region: str, stem: str, rows: list) -> tuple:
    """-> (text, used_row_ids, missing_headings, orphan_headings) — built from ROWS ALONE.

    THE COLD BUILD (council 3, K-3). `build` above reads the live file for two things: the preamble
    and the heading order. That is why day 0's "72 of 72 byte-identical" could not fail on those
    bytes — the generator was copying them from the very file it was being compared against
    (C-04). This function reads NO live file at all. The preamble comes from the newest `# PREAMBLE`
    row, the heading order from the newest `# ORDER` row, every entry body from its own row. Run it
    with the live files moved aside and it produces the same text or it does not, and either answer
    is about the log.

    Raises LookupError when the log has no shape row for this file — a cold build that quietly fell
    back to the live file would be `build` wearing a different name, which is the exact substitution
    this exists to make impossible.
    """
    shape = {}
    for r in rows:
        for f in r["flags"]:
            if f in logstore.SHAPE_FLAGS:
                cur = shape.get(f)
                if cur is None or (r["ts"], r.get("seq", 0)) > (cur["ts"], cur.get("seq", 0)):
                    shape[f] = r                    # newest wins, exactly as a corrected entry does
    if "preamble" not in shape or "order" not in shape:
        raise LookupError(f"{region}/{stem}: the log carries no "
                          f"{'# PREAMBLE' if 'preamble' not in shape else '# ORDER'} row — "
                          "run importer.py, which mints one pair per file")
    preamble = shape["preamble"]["body"]
    order = shape["order"]["body"].split("\n") if shape["order"]["body"] else []

    entry_rows = [r for r in rows if not logstore.is_shape(r)]
    by_heading = {}
    for r in sorted(entry_rows, key=lambda x: (_ordinal(x), x["ts"], x.get("seq", 0))):
        by_heading.setdefault(r["heading"], []).append(r)

    parts, used, missing = [], [], []
    for i, heading in enumerate(order):
        pool = by_heading.get(heading, [])
        if not pool:
            missing.append(heading)
            continue
        row = _newest(pool, i)
        parts.append(importer.reconstruct(row["heading"], row["body"]))
        used.append(row["id"])

    in_order = set(order)
    orphans = []
    for heading, pool in sorted(by_heading.items(), key=lambda kv: (_ordinal(kv[1][0]), kv[0])):
        if heading in in_order:
            continue
        row = max(pool, key=lambda x: (x["ts"], x.get("seq", 0)))
        parts.append(importer.reconstruct(row["heading"], row["body"]))
        used.append(row["id"])
        orphans.append(heading)

    digest = hashlib.sha256("\n".join(used).encode("utf-8")).hexdigest()
    return f"{GENERATED_PREFIX}{digest}\n" + preamble + "".join(parts), used, missing, orphans


def view_path(region: str, stem: str, cold: bool = False) -> Path:
    root = cold_dir() if cold else logstore.VIEW_DIR
    return (root / region / f"{stem}.md") if region != "." else (root / f"{stem}.md")


def generate(only_region: str = "", only_stem: str = "", list_only: bool = False,
             cold: bool = False) -> dict:
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
            text, used, missing, orphans = (build_cold(region, stem, rows) if cold
                                            else build(region, stem, rows))
        except LookupError as e:
            skipped.append((f"{region}/{stem}", str(e)))
            continue
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
        p = view_path(region, stem, cold=cold)
        if not list_only:
            # The region comes off a log row, i.e. out of a file, i.e. it is data. `logstore.append`
            # now refuses a traversing region at write time, but rows written before that check
            # existed are still on disk and this is the thing that turns one into a path.
            rootguard.permit(p, f"generated view {region}/{stem}")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        written.append({"region": region, "stem": stem, "path": str(p), "rows": len(used),
                        "orphans": len(orphans), "missing": len(missing),
                        "bytes": len(text.encode("utf-8"))})
    return {"views": written, "skipped": skipped, "headings_without_a_row": missing_total,
            "orphan_rows": orphan_total, "cold": cold,
            "orphan_population": (
                ("rows whose heading the file's `# ORDER` row does not name" if cold else
                 "rows whose heading appears in no live entry of the file they belong to")
                + ", counted over every (region, stem) the log carries"),
            "view_dir": str(cold_dir() if cold else logstore.VIEW_DIR)}


def compare(cold: bool = True, limit: int = 10) -> dict:
    """Compare every generated view against the LIVE file it shadows, byte for byte.

    The `# GENERATED` first line is stripped before comparing — it is the generator's own
    provenance line and is not claimed to be in the memory. Everything after it is.

    Reported per file, never as one share: a count of identical files with no list of the ones that
    differ is a number nobody can act on, so each differing file carries its byte counts and the
    first hunk of a real diff.
    """
    root = cold_dir() if cold else logstore.VIEW_DIR
    same, differ = [], []
    for vp in sorted(root.rglob("*.md")) if root.is_dir() else []:
        rel = vp.relative_to(root)
        try:
            got = vp.read_text(encoding="utf-8").split("\n", 1)[1]
        except (OSError, IndexError) as e:
            differ.append({"file": str(rel), "why": f"view unreadable: {str(e)[:100]}"})
            continue
        try:
            want = (logstore.VAULT / rel).read_text(encoding="utf-8")
        except OSError as e:
            differ.append({"file": str(rel), "why": f"live file unreadable: {str(e)[:100]}"})
            continue
        if got == want:
            same.append(str(rel))
            continue
        d = list(itertools.islice(
            difflib.unified_diff(want.splitlines(True), got.splitlines(True),
                                 fromfile=f"live/{rel}", tofile=f"{root.name}/{rel}", n=1), 0, 12))
        differ.append({"file": str(rel), "view_bytes": len(got.encode("utf-8")),
                       "live_bytes": len(want.encode("utf-8")),
                       "diff": "".join(d)})
    return {"mode": "cold" if cold else "live", "root": str(root),
            "identical": len(same), "views": len(same) + len(differ),
            "differing": differ[:limit], "differing_total": len(differ),
            "population": (f"every generated view under {root}, compared against the live role "
                           "file at the same vault-relative path, with the `# GENERATED` line "
                           "removed from the view")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", default="")
    ap.add_argument("--stem", default="", choices=("",) + logstore.STEMS)
    ap.add_argument("--list", action="store_true", dest="list_only")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cold", action="store_true",
                    help="build from ROWS ALONE — preamble and heading order from the log's shape "
                         "rows, no live file read at all; writes under .gedaechtnis/views-cold/")
    ap.add_argument("--compare", action="store_true",
                    help="compare the generated views against the live files and report "
                         "byte-identical or the diff, per file (use with --cold for the cold set)")
    args = ap.parse_args()

    if args.compare:
        res = compare(cold=args.cold)
        if args.json:
            print(json.dumps(res, indent=1, ensure_ascii=False))
            return 0
        print(f"{res['mode']} build vs the live files: "
              f"{res['identical']}/{res['views']} byte-identical")
        print(f"population: {res['population']}")
        for d in res["differing"]:
            print(f"  DIFFERS {d['file']}"
                  + (f"  view={d.get('view_bytes')} B live={d.get('live_bytes')} B"
                     if "view_bytes" in d else f"  {d.get('why', '')}"))
            for line in (d.get("diff") or "").splitlines():
                print(f"    {line}")
        if res["differing_total"] > len(res["differing"]):
            print(f"  … and {res['differing_total'] - len(res['differing'])} more")
        if not res["views"]:
            raise SystemExit(f"REFUSED: no view found under {res['root']} — nothing was compared. "
                             "A measured zero needs something to have been counted.")
        return 0

    res = generate(args.region, args.stem, args.list_only, cold=args.cold)
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
