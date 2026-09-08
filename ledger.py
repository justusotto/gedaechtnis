#!/usr/bin/env python3
"""ledger.py — the Channels ledger: one shared, append-only file; rows, not files.

    python3 ledger.py append --from CARD --to MINING-OPS --kind fact --ref <path-or-id> "one-line body"
    python3 ledger.py read   --to MINING-OPS --unacked             # rows the lane has not answered (the act is the receipt)
    python3 ledger.py read   --to MINING-OPS [--since-cursor]      # rows since the lane last LOOKED (advances a cursor; a report, not a receipt)
    python3 ledger.py ack    --from MINING-OPS N-2026-09-08-0007   # a read receipt is a two-word row
    python3 ledger.py check  [--file <ledger>]                     # grammar + monotone ids + size

Grammar, one row per line, tab-separated:
    id \t ts \t from \t to \t kind \t ref \t body
    id   = N-YYYY-MM-DD-NNNN (date + per-day sequence, minted here, never by hand)
    kind = fact | notice | request | handoff | read | ack
    ref  = a path, a q-id or a prior row id (a read/ack row's ref IS the row it answers)
    body = one line; a longer body lives in a file and the row points at it

Too long? The ledger ROTATES monthly: rows land in Channels/ledger/YYYY-MM.tsv; a reader keeps a
per-lane cursor (last row id seen) in its own state dir, so a boot reads only rows after its cursor,
never the whole history. Rows are never edited or deleted; a correction is a new row with ref = the
old id. The append-only hook (gate.py) refuses any write to a ledger file that is not a pure append
of well-formed rows.
"""
from __future__ import annotations
import argparse, fcntl, json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import config

HOME = config.HOME
VAULT = config.VAULT
STATE = config.STATE
LEDGER_DIR = VAULT / "Channels" / "ledger"
KINDS = ("fact", "notice", "request", "handoff", "read", "ack")
ROW = re.compile(r"^(N-\d{4}-\d{2}-\d{2}-\d{4})\t(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\t([A-Z][A-Z0-9-]*)\t([A-Z][A-Z0-9-]*|\*)\t(fact|notice|request|handoff|read|ack)\t([^\t]*)\t([^\t\n]*)$")


def month_file(ts: str | None = None) -> Path:
    ts = ts or time.strftime("%Y-%m")
    return LEDGER_DIR / f"{ts[:7]}.tsv"


def all_rows() -> list[tuple]:
    rows = []
    if not LEDGER_DIR.is_dir():
        return rows
    for f in sorted(LEDGER_DIR.glob("*.tsv")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            m = ROW.match(line)
            if m:
                rows.append(m.groups())
    return rows


def mint_id(day: str) -> str:
    n = 0
    for r in all_rows():
        if r[0].startswith(f"N-{day}-"):
            n = max(n, int(r[0][-4:]))
    return f"N-{day}-{n + 1:04d}"


def validate_line(line: str) -> str | None:
    """Return an error string for a malformed row, else None. Used by the append-only hook."""
    if not line or line.startswith("#"):
        return None
    m = ROW.match(line)
    if not m:
        return "row does not match `id\\tts\\tfrom\\tto\\tkind\\tref\\tbody` with id N-YYYY-MM-DD-NNNN"
    if m.group(5) in ("read", "ack") and not re.match(r"^N-\d{4}-\d{2}-\d{2}-\d{4}$", m.group(6)):
        return "a read/ack row's ref must be the row id it answers"
    return None


def append(frm: str, to: str, kind: str, ref: str, body: str) -> str:
    body = " ".join(body.split())
    if "\t" in ref or len(body) > 400:
        raise SystemExit("ref must not contain tabs; body is one line ≤400 chars — put a longer body in a file and point at it")
    day = time.strftime("%Y-%m-%d"); ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    rid = mint_id(day)
    line = f"{rid}\t{ts}\t{frm}\t{to}\t{kind}\t{ref}\t{body}"
    err = validate_line(line)
    if err:
        raise SystemExit(f"refused: {err}")
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    f = month_file(day)
    lock = LEDGER_DIR / ".lock"
    with open(lock, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)              # mint + append under one lock: no duplicate ids from two lanes
        rid = mint_id(day)
        line = f"{rid}\t{ts}\t{frm}\t{to}\t{kind}\t{ref}\t{body}"
        new = not f.exists()
        with open(f, "a", encoding="utf-8") as fh:
            if new:
                fh.write("# Channels ledger — append-only; id\tts\tfrom\tto\tkind\tref\tbody (see gedaechtnis/ledger.py)\n")
            fh.write(line + "\n")
    _commit(f, f"ledger: {rid} {frm}→{to} {kind}")
    return rid


def _commit(f: Path, msg: str) -> None:
    """A ledger row is committed the moment it is appended, path-limited, as the vault's own identity — the
    Stop hook stages only a lane's declared paths, and the ledger is EVERY lane's surface and no lane's path."""
    if not (VAULT / ".git").exists():
        return
    rel = str(f.relative_to(VAULT))
    for attempt in range(4):
        a = subprocess.run(["git", "-C", str(VAULT), "add", "--", rel], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        c = subprocess.run(["git", "-C", str(VAULT), "-c", "user.name=atlas", "-c", "user.email=atlas@local", "commit", "-q", "-m", msg, "--", rel],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if c.returncode == 0 or "index.lock" not in (a.stderr + c.stderr):
            return
        time.sleep(0.2 * (attempt + 1))


def cursor_path(lane: str) -> Path:
    return STATE / f"ledger-cursor-{lane}.txt"


def unacked(to: str) -> list[tuple]:
    """Rows addressed to `to` (or *) that `to` has not answered with a read/ack row. No cursor: the act is the receipt."""
    rows = all_rows()
    answered = {r[5] for r in rows if r[2] == to and r[4] in ("read", "ack")}
    return [r for r in rows if r[3] in (to, "*") and r[2] != to and r[0] not in answered]


def read(to: str, since_cursor: bool) -> list[tuple]:
    rows = [r for r in all_rows() if r[3] in (to, "*")]
    if since_cursor:
        try:
            last = cursor_path(to).read_text(encoding="utf-8").strip()
            rows = [r for r in rows if r[0] > last]
        except OSError:
            pass
    if rows and since_cursor:
        STATE.mkdir(parents=True, exist_ok=True)
        cursor_path(to).write_text(rows[-1][0] + "\n", encoding="utf-8")
    return rows


def check(path: Path | None) -> int:
    files = [path] if path else sorted(LEDGER_DIR.glob("*.tsv")) if LEDGER_DIR.is_dir() else []
    bad = 0; last = ""
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            err = validate_line(line)
            if err:
                print(f"{f}:{n}: {err}"); bad += 1; continue
            if line and not line.startswith("#"):
                rid = line.split("\t", 1)[0]
                if rid <= last:
                    print(f"{f}:{n}: id {rid} is not after {last} (ids must be monotone)"); bad += 1
                last = rid
        size = f.stat().st_size
        if size > 512_000:
            print(f"{f}: {size:,} B — over the 500 KB per-month guide; rows are cheap, but a month this busy wants weekly files")
    print(f"ledger check: {bad} problem(s) across {len(files)} file(s)")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append"); a.add_argument("--from", dest="frm", required=True); a.add_argument("--to", required=True)
    a.add_argument("--kind", choices=KINDS, required=True); a.add_argument("--ref", default="-"); a.add_argument("body")
    r = sub.add_parser("read"); r.add_argument("--to", required=True); r.add_argument("--since-cursor", action="store_true"); r.add_argument("--unacked", action="store_true"); r.add_argument("--json", action="store_true")
    k = sub.add_parser("ack"); k.add_argument("--from", dest="frm", required=True); k.add_argument("row_id"); k.add_argument("--kind", choices=("read", "ack"), default="read")
    c = sub.add_parser("check"); c.add_argument("--file")
    args = ap.parse_args()
    if args.cmd == "append":
        print(append(args.frm, args.to, args.kind, args.ref, args.body)); return 0
    if args.cmd == "ack":
        rows = {x[0]: x for x in all_rows()}
        if args.row_id not in rows:
            raise SystemExit(f"refused: {args.row_id} is not a row in the ledger")
        print(append(args.frm, rows[args.row_id][2], args.kind, args.row_id, f"{args.kind} {args.row_id}")); return 0
    if args.cmd == "read":
        rows = unacked(args.to) if args.unacked else read(args.to, args.since_cursor)
        if args.json:
            print(json.dumps([dict(zip(("id", "ts", "from", "to", "kind", "ref", "body"), r)) for r in rows], indent=1))
        else:
            for x in rows:
                print("\t".join(x))
            print(f"# {len(rows)} row(s) for {args.to}" + (" since cursor" if args.since_cursor else ""), file=sys.stderr)
        return 0
    if args.cmd == "check":
        return check(Path(args.file) if args.file else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
