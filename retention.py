#!/usr/bin/env python3
"""retention.py — cleanliness as a mechanism: nothing important is ever lost, nothing unimportant kept.

    python3 retention.py scan   <review-root> [--json out.json] [--md out.md] [--top 15]
    python3 retention.py apply  <review-root> --yes [--trash]        # bundles candidates; never rm
    python3 retention.py close-round <round-dir> [--date YYYY-MM-DD]  # writes ROUND.json {closed: …}
    python3 retention.py inventory <dir>... --out inventory.tsv        # hashes of the irreplaceable set
    python3 retention.py inventory --verify inventory.tsv              # what changed or vanished

Classes are declared by CONVENTION first (the same directory names recur across hundreds of
arcs), by a per-arc MANIFEST.toml second, and never by guessing: a file no rule names is UNKNOWN,
and UNKNOWN means IRREPLACEABLE. Retention is decided per ROUND, never per arc and never by age:
a DERIVED file is a candidate only when the round it belongs to is CLOSED (a ROUND.json carrying
`closed`) and its arc has a tracked generator. The default is a dry run that prints a list.
`apply` gathers candidates into ONE `Cleanup YYYY-MM-DD/` bundle with origin structure preserved
and a self-contained README, and — only with --trash — moves that one folder to the Trash through
Finder. Nothing here ever calls rm. The Trash is never emptied.
"""
from __future__ import annotations
import argparse, hashlib, html, json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import config

DERIVED_DIRS = {"_shots", "shots", "_render_check", "_smoke", "_fonts", "node_modules", "__pycache__",
                "downloads-dupes", "_stale-dev-renders", "renders", "_renders", "screenshots", "_screens"}
DERIVED_DIR_PREFIXES = ("_fonts_", "_stale", "_shots", "round-shots")
DERIVED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".woff2", ".woff", ".ttf", ".zip", ".pyc",
               ".mp4", ".mp3", ".wav", ".m4a", ".mov", ".heic", ".tiff", ".tif", ".bmp"}
EVIDENCE_EXT = {".md", ".json", ".py", ".sh", ".tsv", ".csv", ".txt", ".js", ".toml", ".yaml", ".yml", ".sql"}
GENERATOR_GLOBS = ("build_*.py", "*.py", "*.sh", "Makefile", "*.js")
ROUND_DIR = re.compile(r"^(?:answers[-_])?round[-_]?\d+$", re.I)


def read_manifest(arc: Path) -> dict:
    """Minimal MANIFEST.toml: [classes] derived = ["glob", …] / evidence = [...]; no toml lib needed."""
    m = arc / "MANIFEST.toml"
    out = {"derived": [], "evidence": []}
    if not m.is_file():
        return out
    try:
        for line in m.read_text(encoding="utf-8").splitlines():
            mm = re.match(r"^\s*(derived|evidence)\s*=\s*\[(.*)\]\s*$", line)
            if mm:
                out[mm.group(1)] = [x.strip().strip('"').strip("'") for x in mm.group(2).split(",") if x.strip()]
    except OSError:
        pass
    return out


def classify(rel: Path, manifest: dict, has_generator: bool) -> tuple[str, str]:
    """Return (class, reason). class ∈ DERIVED · EVIDENCE · UNKNOWN."""
    parts = rel.parts
    for g in manifest["evidence"]:
        if rel.match(g):
            return "EVIDENCE", f"manifest evidence `{g}`"
    for g in manifest["derived"]:
        if rel.match(g):
            return "DERIVED", f"manifest derived `{g}`"
    name = rel.name
    if name in ("ROUND.json", "MANIFEST.toml", "atlas-record.json") or name.upper().startswith(("DISPOSITION", "REPORT", "README")):
        return "EVIDENCE", "record file"
    if name.startswith("answers") or "/answers" in str(rel.parent):
        return "EVIDENCE", "owner answers"
    for d in parts[:-1]:
        if d in DERIVED_DIRS or d.startswith(DERIVED_DIR_PREFIXES):
            return "DERIVED", f"in `{d}/`"
    ext = rel.suffix.lower()
    if ext in DERIVED_EXT:
        return "DERIVED", f"`{ext}` render/export"
    if ext == ".html":
        return ("DERIVED", "built page; generator tracked") if has_generator else ("EVIDENCE", "page with no tracked generator")
    if ext in EVIDENCE_EXT:
        return "EVIDENCE", f"`{ext}`"
    return "UNKNOWN", "no rule names it → irreplaceable"


def tracked_files(root: Path) -> set[str]:
    try:
        p = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", "."], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        if p.returncode != 0:
            return set()
        return set(x.decode("utf-8", "replace") for x in p.stdout.split(b"\0") if x)
    except (OSError, subprocess.TimeoutExpired):
        return set()


def repo_root(path: Path) -> Path | None:
    try:
        p = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
        return Path(p.stdout.strip()) if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def round_of(rel: Path, arc: Path) -> tuple[Path, str | None]:
    """The round dir a file belongs to (nearest enclosing round-N dir, else the arc), and its closed date."""
    d = (arc / rel).parent
    while d != arc and d != d.parent:
        if ROUND_DIR.match(d.name):
            return d, closed_date(d)
        d = d.parent
    return arc, closed_date(arc)


def closed_date(d: Path) -> str | None:
    f = d / "ROUND.json"
    if not f.is_file():
        return None
    try:
        j = json.loads(f.read_text(encoding="utf-8"))
        c = j.get("closed")
        return str(c) if c else None
    except (OSError, json.JSONDecodeError):
        return None


def scan(review_root: Path) -> dict:
    review_root = review_root.resolve()
    root = repo_root(review_root) or review_root
    tracked = tracked_files(root)
    arcs = sorted(p for p in review_root.iterdir() if p.is_dir() and not p.name.startswith("."))
    report = {"root": str(review_root), "scanned": time.strftime("%Y-%m-%dT%H:%M:%S"), "arcs": [], "totals": {}}
    tot = {k: [0, 0] for k in ("candidate", "derived_protected", "evidence", "unknown")}
    for arc in arcs:
        manifest = read_manifest(arc)
        arc_rel = str(arc.relative_to(root)) if root in arc.parents or root == arc.parent else arc.name
        gen = any(f.startswith(arc_rel + "/") and Path(f).suffix in (".py", ".sh", ".js") for f in tracked) or (arc_rel + "/Makefile") in tracked
        row = {"arc": arc.name, "generator_tracked": bool(gen), "classes": {k: [0, 0] for k in tot}, "candidates": [], "unknown": [], "protected_reasons": {}}
        for dirpath, dirnames, filenames in os.walk(arc):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for fn in filenames:
                p = Path(dirpath) / fn
                if fn == ".DS_Store":
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                rel = p.relative_to(arc)
                cls, why = classify(rel, manifest, gen)
                if cls == "EVIDENCE":
                    key = "evidence"
                elif cls == "UNKNOWN":
                    key = "unknown"; row["unknown"].append(str(rel))
                else:
                    rd, closed = round_of(rel, arc)
                    if not gen:
                        key = "derived_protected"; row["protected_reasons"]["generator not tracked in git"] = row["protected_reasons"].get("generator not tracked in git", 0) + 1
                    elif not closed:
                        key = "derived_protected"; r = f"round `{rd.relative_to(arc) if rd != arc else '.'}` not closed (no ROUND.json closed:)"
                        row["protected_reasons"][r] = row["protected_reasons"].get(r, 0) + 1
                    else:
                        key = "candidate"; row["candidates"].append({"path": str(rel), "bytes": size, "why": why, "round_closed": closed})
                row["classes"][key][0] += 1; row["classes"][key][1] += size
                tot[key][0] += 1; tot[key][1] += size
        report["arcs"].append(row)
    report["totals"] = {k: {"files": v[0], "bytes": v[1]} for k, v in tot.items()}
    return report


def fmt_bytes(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} GB"


def report_md(rep: dict, top: int) -> str:
    t = rep["totals"]
    L = [f"# Retention scan — {rep['root']} — {rep['scanned']}", "",
         "Dry run. Nothing was moved. A file is a CANDIDATE only when it is DERIVED by a declared rule, its round is CLOSED, and its arc has a tracked generator. UNKNOWN means irreplaceable.", "",
         "| class | files | bytes |", "|---|---|---|"]
    for k, lab in (("candidate", "candidates (derived · round closed · generator tracked)"), ("derived_protected", "derived but PROTECTED"),
                   ("evidence", "evidence (kept)"), ("unknown", "UNKNOWN (kept, irreplaceable until a rule names it)")):
        L.append(f"| {lab} | {t[k]['files']:,} | {fmt_bytes(t[k]['bytes'])} |")
    L += ["", f"## Top {top} arcs by protected-derived bytes (what a closed round would release)", "", "| arc | derived protected | candidates | unknown | why protected |", "|---|---|---|---|---|"]
    arcs = sorted(rep["arcs"], key=lambda a: -(a["classes"]["derived_protected"][1] + a["classes"]["candidate"][1]))[:top]
    for a in arcs:
        c = a["classes"]
        why = "; ".join(f"{k} ×{v}" for k, v in sorted(a["protected_reasons"].items(), key=lambda x: -x[1])[:2]) or "—"
        L.append(f"| {a['arc']} | {c['derived_protected'][0]:,} / {fmt_bytes(c['derived_protected'][1])} | {c['candidate'][0]:,} / {fmt_bytes(c['candidate'][1])} | {c['unknown'][0]:,} | {why} |")
    unk = [(a["arc"], u) for a in rep["arcs"] for u in a["unknown"]]
    L += ["", f"## UNKNOWN files ({len(unk)}) — each needs a rule or a manifest line before it can ever be a candidate", ""]
    for arc, u in unk[:60]:
        L.append(f"- `{arc}/{u}`")
    if len(unk) > 60:
        L.append(f"- … +{len(unk)-60} more (see the JSON)")
    return "\n".join(L) + "\n"


def apply(review_root: Path, rep: dict, trash: bool) -> Path:
    day = time.strftime("%Y-%m-%d")
    bundle = review_root.parent / f"Cleanup {day}"
    bundle.mkdir(parents=True, exist_ok=True)
    rows = []
    for a in rep["arcs"]:
        for c in a["candidates"]:
            src = review_root / a["arc"] / c["path"]
            dst = bundle / a["arc"] / c["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)                          # a move, never a delete
            rows.append((a["arc"], c["path"], c["bytes"], c["why"], c["round_closed"]))
    readme = bundle / "README-what-went-where.html"
    body = ["<!doctype html>", '<meta charset="utf-8">', f"<title>Cleanup {day}</title>",
            "<style>body{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:24px;max-width:1100px}table{border-collapse:collapse}td,th{padding:4px 8px;border-bottom:1px solid #ddd;text-align:left;font-size:13px}</style>",
            f"<h1>Cleanup {day}</h1><p>Every file below was DERIVED (regenerable from a tracked generator in its arc), belonged to a round whose decision is CLOSED, and was moved here in one bundle by <code>gedaechtnis/retention.py apply</code>. Nothing was deleted. To restore a file, move it back to <code>{html.escape(str(review_root))}/&lt;arc&gt;/&lt;path&gt;</code>. To regenerate it, run the arc's build script.</p>",
            "<table><tr><th>arc</th><th>path</th><th>bytes</th><th>why derived</th><th>round closed</th></tr>"]
    for arc, path, b, why, closed in rows:
        body.append(f"<tr><td>{html.escape(arc)}</td><td>{html.escape(path)}</td><td>{b:,}</td><td>{html.escape(why)}</td><td>{html.escape(str(closed))}</td></tr>")
    body.append("</table>")
    readme.write_text("\n".join(body), encoding="utf-8")
    if trash and not config.no_trash():          # GEDAECHTNIS_NO_TRASH leaves the bundle in place
        script = f'tell application "Finder" to delete POSIX file "{bundle}"'
        subprocess.run(["osascript", "-e", script], check=False, stdin=subprocess.DEVNULL, timeout=120)
    return bundle


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(dirs: list[Path], out: Path) -> int:
    n = 0
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("# path\tbytes\tsha256\tmtime\n")
        for d in dirs:
            for dirpath, dirnames, filenames in os.walk(d):
                dirnames[:] = [x for x in dirnames if x not in (".git", "__pycache__")]
                for fn in sorted(filenames):
                    p = Path(dirpath) / fn
                    try:
                        st = p.stat()
                        fh.write(f"{p}\t{st.st_size}\t{sha256(p)}\t{time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(st.st_mtime))}\n")
                        n += 1
                    except OSError:
                        fh.write(f"{p}\tUNREADABLE\t-\t-\n")
    return n


def verify(inv: Path) -> tuple[list[str], list[str]]:
    missing, changed = [], []
    for line in inv.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        path, size, digest, _ = line.split("\t")
        p = Path(path)
        if not p.is_file():
            missing.append(path); continue
        if digest != "-" and sha256(p) != digest:
            changed.append(path)
    return missing, changed


def main() -> int:
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("root"); s.add_argument("--json"); s.add_argument("--md"); s.add_argument("--top", type=int, default=15)
    a = sub.add_parser("apply"); a.add_argument("root"); a.add_argument("--yes", action="store_true"); a.add_argument("--trash", action="store_true")
    c = sub.add_parser("close-round"); c.add_argument("round_dir"); c.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    i = sub.add_parser("inventory"); i.add_argument("dirs", nargs="*"); i.add_argument("--out"); i.add_argument("--verify")
    args = ap.parse_args()
    if args.cmd == "scan":
        rep = scan(Path(args.root).expanduser())
        md = report_md(rep, args.top)
        if args.json: Path(args.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")
        if args.md: Path(args.md).write_text(md, encoding="utf-8")
        print(md if not args.md else md.split("\n## ")[0])
        return 0
    if args.cmd == "apply":
        if not args.yes:
            print("apply refuses without --yes (and moves to the Trash only with --trash). Run `scan` first and read the list."); return 2
        rep = scan(Path(args.root).expanduser())
        n = sum(len(a["candidates"]) for a in rep["arcs"])
        if n == 0:
            print("no candidates: nothing is DERIVED + round-closed + generator-tracked. Nothing moved."); return 0
        b = apply(Path(args.root).expanduser(), rep, args.trash)
        print(f"moved {n} candidate files into {b} (README inside){' and sent the bundle to the Trash' if args.trash else ''}."); return 0
    if args.cmd == "close-round":
        d = Path(args.round_dir).expanduser(); f = d / "ROUND.json"
        j = {}
        if f.is_file():
            try: j = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError: j = {}
        j["closed"] = args.date; j.setdefault("closed_by", "session")
        f.write_text(json.dumps(j, indent=1) + "\n", encoding="utf-8"); print(f"{f}: closed {args.date}"); return 0
    if args.cmd == "inventory":
        if args.verify:
            m, ch = verify(Path(args.verify))
            print(f"missing {len(m)} · changed {len(ch)}")
            for x in m: print(f"MISSING\t{x}")
            for x in ch: print(f"CHANGED\t{x}")
            return 1 if (m or ch) else 0
        if not args.out or not args.dirs:
            print("inventory needs dirs and --out"); return 2
        n = inventory([Path(d).expanduser() for d in args.dirs], Path(args.out)); print(f"{n} files hashed → {args.out}"); return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
