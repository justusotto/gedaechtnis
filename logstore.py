#!/usr/bin/env python3
"""logstore.py — ONE vault-wide append-only log of memory entries; rows, not files.

    python3 logstore.py append --region Speculum --kind decision --stem Canon \
        --heading "## The vault is at the home directory" --body "…" [--flags binding,pointer]
    python3 logstore.py read   [--region R] [--stem S] [--kind K] [--json]
    python3 logstore.py check  [--file <log>]        # grammar + no duplicate ids + no duplicate content
    python3 logstore.py count                        # rows per stem, rows per region

The store is `<vault>/.gedaechtnis/log/YYYY-MM.tsv` — a HIDDEN directory on purpose: during the
30-day additive shadow nothing the owner sees in Obsidian may change, so the log and the generated
views live where the vault's own reader never walks. Where they live afterwards is a later ruling.

Grammar, one row per line, tab-separated — `ledger.py`'s grammar generalised from lane notices to
memory entries:

    id \t ts \t region \t kind \t stem \t heading \t body \t flags

    id      <region>·<ts>·<8 hex of sha256(region, stem, heading, body)> — DERIVED, never minted.
            A sequence would need a central counter; a content hash needs nothing, and makes the
            second import of an unchanged vault a no-op by construction.
    ts      YYYY-MM-DDTHH:MM:SS, when the row was WRITTEN (provenance, not identity)
    region  the vault-relative region folder ("Global", "Mnemosyne/UkrainianCard") — a COLUMN,
            which is attribution, never permission
    kind    decision · lesson · state · question · note
    stem    the role file the entry came from: Canon · Errata · Patterns · Position · Aporia
    heading the entry's own heading line (or, in a bullet-structured file, its first line)
    body    the entry text below the heading; newlines escaped `\\n`, tabs `\\t`, backslash `\\\\`
    flags   comma list, possibly empty: `pointer` (the entry is only a wikilink to somewhere else),
            `binding` (its text carries NEVER or ALWAYS), `ord=N` (its 0-based position in its file)

APPEND-ONLY, and idempotent. `append` refuses nothing and rewrites nothing: if a row with the same
region and the same content hash is already in the log it returns that row's id and writes no line.
Two rows can therefore never disagree about one entry, and re-running the importer over an unchanged
vault appends zero rows — which is the property the shadow's whole fidelity claim rests on.

The IDENTITY of a row is (region, content hash). The `ts` in the middle of the id is provenance:
two runs of the importer a day apart over the same entry would mint two different id STRINGS, so
duplicate detection compares the region and the hash, and `check` reports either kind of collision.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import config  # noqa: E402  (path is set above; this is the only place a directory is named)

VAULT = config.VAULT
LOG_DIR = VAULT / ".gedaechtnis" / "log"
VIEW_DIR = VAULT / ".gedaechtnis" / "views"
START_FILE = VAULT / ".gedaechtnis" / "shadow-start.json"

KINDS = ("decision", "lesson", "state", "question", "note")
STEMS = ("Canon", "Errata", "Patterns", "Position", "Aporia")
FIELDS = ("id", "ts", "region", "kind", "stem", "heading", "body", "flags")

HEADER = ("# Gedaechtnis memory log — append-only; "
          "id\tts\tregion\tkind\tstem\theading\tbody\tflags (see gedaechtnis/logstore.py)\n")

ROW = re.compile(
    r"^([A-Za-z0-9_./-]+·\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}·[0-9a-f]{8})"
    r"\t(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"\t([A-Za-z0-9_./-]+)"
    r"\t(decision|lesson|state|question|note)"
    r"\t(Canon|Errata|Patterns|Position|Aporia)"
    r"\t([^\t\n]*)"
    r"\t([^\t\n]*)"
    r"\t([^\t\n]*)$"
)


# --------------------------------------------------------------------- escaping ----
# A TSV row cannot carry a newline or a tab, and an entry body is full of newlines. The escape is
# the smallest one that round-trips exactly: unescape(escape(x)) == x for every string, which the
# test suite asserts over the real corpus. Anything less exact would make "byte-identical" a claim
# about the escaper rather than about the memory.

def escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def unescape(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def content_hash(region: str, stem: str, heading: str, body: str) -> str:
    """The row's identity: the raw (unescaped) text, never the escaped line — an escaper change
    must not silently re-mint every id in the log."""
    h = hashlib.sha256()
    for part in (region, stem, heading, body):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:8]


def make_id(region: str, ts: str, digest: str) -> str:
    return f"{region}·{ts}·{digest}"


def split_id(row_id: str):
    """-> (region, ts, digest). The region may itself contain no `·`, which the grammar enforces."""
    parts = row_id.split("·")
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


# --------------------------------------------------------------------- reading ----

def log_files() -> list:
    return sorted(LOG_DIR.glob("*.tsv")) if LOG_DIR.is_dir() else []


def month_file(ts: str = "") -> Path:
    ts = ts or time.strftime("%Y-%m")
    return LOG_DIR / f"{ts[:7]}.tsv"


def parse_line(line: str):
    """-> dict for a well-formed row, None for a comment/blank, raises ValueError otherwise."""
    if not line or line.startswith("#"):
        return None
    m = ROW.match(line)
    if not m:
        raise ValueError("row does not match `id\\tts\\tregion\\tkind\\tstem\\theading\\tbody\\tflags`")
    d = dict(zip(FIELDS, m.groups()))
    d["heading"] = unescape(d["heading"])
    d["body"] = unescape(d["body"])
    d["flags"] = [f for f in d["flags"].split(",") if f]
    return d


def all_rows() -> list:
    """-> [row] in LOG ORDER, each carrying a derived `seq` (its position in that order).

    `seq` is not part of the grammar and is never written: it is the tiebreaker for two rows whose
    `ts` is identical, which happens whenever an import writes a correction in the same second as
    the row it corrects. Without it "the newest row wins" is decided by dict iteration order.
    """
    rows = []
    for f in log_files():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                d = parse_line(line)
            except ValueError:
                continue                       # `check` is the place that reports malformed rows
            if d:
                d["seq"] = len(rows)
                rows.append(d)
    return rows


def _index(rows) -> dict:
    """(region, digest) -> row. The identity map the idempotent append consults."""
    out = {}
    for r in rows:
        parts = split_id(r["id"])
        if parts:
            out[(parts[0], parts[2])] = r
    return out


# --------------------------------------------------------------------- writing ----

def append(region: str, kind: str, stem: str, heading: str, body: str,
           flags=(), ts: str = "", known: dict = None) -> tuple:
    """-> (row_id, appended). Idempotent: a row whose (region, content hash) is already in the log
    is a NO-OP and returns the existing id.

    `known` lets a bulk importer pass the identity map it already built, so importing 1,229 entries
    is one read of the log rather than 1,229 — the caller must keep it current, which the importer
    does by inserting each row it writes.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    if stem not in STEMS:
        raise ValueError(f"stem must be one of {STEMS}, not {stem!r}")
    if "·" in region or "\t" in region or not region:
        raise ValueError(f"region must be a vault-relative folder with no tab and no `·`: {region!r}")
    digest = content_hash(region, stem, heading, body)
    idx = known if known is not None else _index(all_rows())
    hit = idx.get((region, digest))
    if hit:
        return hit["id"], False
    ts = ts or time.strftime("%Y-%m-%dT%H:%M:%S")
    row_id = make_id(region, ts, digest)
    flag_s = ",".join(str(f) for f in flags)
    line = "\t".join((row_id, ts, region, kind, stem, escape(heading), escape(body), flag_s))
    if not ROW.match(line):
        raise ValueError(f"refused: the composed row does not match the grammar: {line[:160]}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    f = month_file(ts)
    new = not f.exists()
    with open(f, "a", encoding="utf-8") as fh:      # "a" and only ever "a": the file is append-only
        if new:
            fh.write(HEADER)
        fh.write(line + "\n")
    row = dict(zip(FIELDS, (row_id, ts, region, kind, stem, heading, body, list(flags))))
    if known is not None:
        known[(region, digest)] = row
    return row_id, True


# --------------------------------------------------------------------- checking ----

def check(path=None) -> int:
    files = [Path(path)] if path else log_files()
    bad = 0
    by_id = {}
    by_content = {}
    n = 0
    for f in files:
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            try:
                d = parse_line(line)
            except ValueError as e:
                print(f"{f}:{lineno}: {e}")
                bad += 1
                continue
            if not d:
                continue
            n += 1
            if d["id"] in by_id:
                print(f"{f}:{lineno}: duplicate id {d['id']} (first seen {by_id[d['id']]})")
                bad += 1
            by_id[d["id"]] = f"{f}:{lineno}"
            parts = split_id(d["id"])
            if not parts:
                print(f"{f}:{lineno}: id is not <region>·<ts>·<hash>")
                bad += 1
                continue
            region, _ts, digest = parts
            if region != d["region"]:
                print(f"{f}:{lineno}: id region {region!r} != region column {d['region']!r}")
                bad += 1
            want = content_hash(d["region"], d["stem"], d["heading"], d["body"])
            if digest != want:
                print(f"{f}:{lineno}: id hash {digest} != sha256 of the row's own content ({want})")
                bad += 1
            key = (d["region"], digest)
            if key in by_content:
                print(f"{f}:{lineno}: duplicate content for {d['region']} (first seen {by_content[key]})")
                bad += 1
            by_content[key] = f"{f}:{lineno}"
    print(f"logstore check: {bad} problem(s) across {len(files)} file(s), {n} row(s)")
    return 1 if bad else 0


def counts() -> dict:
    rows = all_rows()
    per_stem = {}
    per_region = {}
    for r in rows:
        per_stem[r["stem"]] = per_stem.get(r["stem"], 0) + 1
        per_region[r["region"]] = per_region.get(r["region"], 0) + 1
    return {"rows": len(rows), "per_stem": per_stem, "per_region": per_region}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append")
    a.add_argument("--region", required=True)
    a.add_argument("--kind", choices=KINDS, required=True)
    a.add_argument("--stem", choices=STEMS, required=True)
    a.add_argument("--heading", required=True)
    a.add_argument("--body", default="")
    a.add_argument("--flags", default="")
    r = sub.add_parser("read")
    r.add_argument("--region")
    r.add_argument("--stem")
    r.add_argument("--kind")
    r.add_argument("--json", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("--file")
    sub.add_parser("count")
    args = ap.parse_args()

    if args.cmd == "append":
        flags = [f for f in args.flags.split(",") if f]
        row_id, wrote = append(args.region, args.kind, args.stem, args.heading, args.body, flags)
        print(f"{row_id}\t{'appended' if wrote else 'already present (no-op)'}")
        return 0
    if args.cmd == "read":
        rows = [x for x in all_rows()
                if (not args.region or x["region"] == args.region)
                and (not args.stem or x["stem"] == args.stem)
                and (not args.kind or x["kind"] == args.kind)]
        if args.json:
            print(json.dumps(rows, indent=1, ensure_ascii=False))
        else:
            for x in rows:
                print("\t".join((x["id"], x["region"], x["stem"], x["heading"][:100])))
            print(f"# {len(rows)} row(s)", file=sys.stderr)
        return 0
    if args.cmd == "check":
        return check(args.file)
    if args.cmd == "count":
        print(json.dumps(counts(), indent=1, ensure_ascii=False))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
