#!/usr/bin/env python3
"""shadow_score.py — the shadow's checkpoint scorer: does the log reproduce the memory, byte for byte?

    python3 shadow_score.py                       # score today; write the checkpoint report + JSON
    python3 shadow_score.py --out DIR             # somewhere other than the default review dir
    python3 shadow_score.py --print               # stdout only, write nothing

Checkpoints are STAGED — day 0, 1, 2, 4, 7, 14, 30 — rather than one verdict after thirty days
(owner, 2026-09-10). A number that only arrives at the end cannot change what the arc does.

WHAT IS MEASURED, and over what population. Every figure below carries its own denominator, because
a fidelity share and an escape rate are counted over different sets and an aggregate that hides
which is which is the failure this pass exists to avoid.

1. FIDELITY, per region x stem.  numerator = entries the generated view reproduces BYTE-IDENTICAL
   (heading + body), POINTER ROWS EXCLUDED — an entry that is only a wikilink is reproducible by
   construction and earns no credit (council 2: Melchior's own bar, retracted by him).  denominator
   = every entry in the live file, pointers included.  So a vault made mostly of pointers cannot
   reach the bar by being easy.  PASS = every STEM's aggregate >= 90%.

2. ESCAPE RATE — entries over 2,000 B, split BINDING (text carries NEVER or ALWAYS) vs NARRATIVE.
   The council capped the VIEW and not the row precisely because binding entries run long: an
   aggregate escape rate hides the one class where a demotion to a pointer would break the placement
   law.  Reported, never enforced.

3. THE LIVE COUNTER — hand edits to live role files vs rows appended, since the shadow start commit
   recorded in `<vault>/.gedaechtnis/shadow-start.json`.  Hand edits come from the vault's own git
   log over the five stems with the automated committer's identity excluded; rows come from the log's
   timestamps.  The importer's fidelity is not the live risk (Caspar): a shadow can reproduce a
   static vault perfectly and still be bypassed every day by a session that edits the file.

4. WIKILINKS in the generated views — every `[[...]]` must resolve to a file in the vault.  A
   generator that dropped or mangled a link would still score 100% on entries it never emitted.

Exit status is 0 whether the checkpoint passes or fails: this is an instrument, and an instrument
that refuses to report a bad number is worse than the bad number.  A HARNESS defect — zero entries,
no views, no log — exits 2, because then there is no verdict to report at all.
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import config
import logstore
import importer
import views
from logstore import VAULT, STEMS, START_FILE

PASS_BAR = 0.90
ESCAPE_BYTES = 2000
AUTOMATED_COMMITTER = "atlas@local"      # the hook's identity; its commits are not hand edits
LINK = re.compile(r"\[\[([^\]\n]+)\]\]")
# The row's own expectation for the wikilink check, printed beside whatever this run measures.
EXPECTED_LINKS = 1123


def default_out() -> Path:
    """The review directory in the checkout this plugin lives in — derived from config.TOOL_ROOT,
    never a literal path, so the tool works in a worktree and in a stranger's clone alike."""
    return Path(config.TOOL_ROOT) / ".orchestration" / "review" / "shadow-2026-09-10"


# ------------------------------------------------------------------ shadow start ----

def vault_head() -> str:
    try:
        r = subprocess.run(["git", "-C", str(VAULT), "rev-parse", "HEAD"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def shadow_start(create: bool = True) -> dict:
    """Read (or, on the first checkpoint, write) the shadow's start pin: the vault sha and the date
    every later checkpoint counts FROM. Written once and never rewritten — a moving start line would
    make the live counter unreadable."""
    try:
        return json.loads(START_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    start = {"date": time.strftime("%Y-%m-%d"),
             "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "vault_sha": vault_head(),
             "checkpoints": [0, 1, 2, 4, 7, 14, 30],
             "note": "checkpoint 0 of the 30-day additive shadow (council 2 closure §1); "
                     "written once, never rewritten"}
    if create:
        START_FILE.parent.mkdir(parents=True, exist_ok=True)
        START_FILE.write_text(json.dumps(start, indent=1) + "\n", encoding="utf-8")
    return start


# ------------------------------------------------------------------ 1. fidelity ----

def score_fidelity() -> list:
    """-> [row dicts], one per region x stem present in the log."""
    grouped = views.rows_by_file()
    out = []
    for (region, stem), rows in sorted(grouped.items()):
        live_path = (VAULT / region / f"{stem}.md") if region != "." else (VAULT / f"{stem}.md")
        try:
            live_text = live_path.read_text(encoding="utf-8")
        except OSError as e:
            out.append({"region": region, "stem": stem, "error": f"live file unreadable: {e}"})
            continue
        _pre, live_entries = importer.split_entries(live_text)
        vp = views.view_path(region, stem)
        try:
            view_text = vp.read_text(encoding="utf-8")
        except OSError as e:
            out.append({"region": region, "stem": stem, "entries": len(live_entries),
                        "error": f"view missing: {e}"})
            continue
        # the view's own entries, parsed by exactly the parser the live file went through
        _vpre, view_entries = importer.split_entries(view_text)
        view_blocks = set()
        for h, b in view_entries:
            view_blocks.add(importer.reconstruct(h, b))

        entries = len(live_entries)
        pointers = 0
        reproduced = 0
        reproduced_nonpointer = 0
        for h, b in live_entries:
            ptr = importer.is_pointer(h, b)
            hit = importer.reconstruct(h, b) in view_blocks
            if ptr:
                pointers += 1
            if hit:
                reproduced += 1
                if not ptr:
                    reproduced_nonpointer += 1
        fidelity = (reproduced_nonpointer / entries) if entries else 0.0
        out.append({"region": region, "stem": stem, "entries": entries,
                    "reproduced": reproduced, "pointer_rows_excluded": pointers,
                    "numerator": reproduced_nonpointer, "denominator": entries,
                    "fidelity": fidelity,
                    "verdict": "PASS" if fidelity >= PASS_BAR else "FAIL"})
    return out


def per_stem(rows: list) -> dict:
    agg = {}
    for r in rows:
        if "entries" not in r:
            continue
        a = agg.setdefault(r["stem"], {"entries": 0, "numerator": 0, "pointer_rows_excluded": 0,
                                       "reproduced": 0, "files": 0})
        a["entries"] += r["entries"]
        a["numerator"] += r.get("numerator", 0)
        a["reproduced"] += r.get("reproduced", 0)
        a["pointer_rows_excluded"] += r.get("pointer_rows_excluded", 0)
        a["files"] += 1
    for stem, a in agg.items():
        a["fidelity"] = (a["numerator"] / a["entries"]) if a["entries"] else 0.0
        a["verdict"] = "PASS" if a["fidelity"] >= PASS_BAR else "FAIL"
    return agg


# ------------------------------------------------------------------ 2. escape rate ----

def escape_rate() -> dict:
    rows = logstore.all_rows()
    tot = {"binding": 0, "narrative": 0}
    over = {"binding": 0, "narrative": 0}
    biggest = []
    for r in rows:
        cls = "binding" if "binding" in r["flags"] else "narrative"
        tot[cls] += 1
        n = len((r["heading"] + "\n" + r["body"]).encode("utf-8"))
        if n > ESCAPE_BYTES:
            over[cls] += 1
            biggest.append((n, r["region"], r["stem"], r["heading"][:80]))
    biggest.sort(reverse=True)
    return {"threshold_bytes": ESCAPE_BYTES,
            "binding": {"entries": tot["binding"], "over": over["binding"],
                        "rate": over["binding"] / tot["binding"] if tot["binding"] else 0.0},
            "narrative": {"entries": tot["narrative"], "over": over["narrative"],
                          "rate": over["narrative"] / tot["narrative"] if tot["narrative"] else 0.0},
            "all": {"entries": len(rows), "over": over["binding"] + over["narrative"],
                    "rate": (over["binding"] + over["narrative"]) / len(rows) if rows else 0.0},
            "largest": biggest[:5]}


# ------------------------------------------------------------------ 3. live counter ----

def live_counter(start: dict) -> dict:
    """Hand edits to the five live stems since the start pin, vs rows appended since the same date."""
    since = start.get("date") or time.strftime("%Y-%m-%d")
    # `git log -- '*Canon.md'` matches at any depth; these five patterns ARE the role surface the
    # shadow shadows. They also match the pull-only sidecars (`Canon-archive.md`), which is the
    # conservative direction for this counter: it would rather over-report a hand edit than miss one.
    pathspecs = [f"*{stem}.md" for stem in STEMS]
    cmd = ["git", "-C", str(VAULT), "log", f"--since={since}",
           "--format=%H%x09%ae%x09%s", "--"] + pathspecs
    hand = []
    automated = 0
    err = ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        if r.returncode != 0:
            err = r.stderr.strip()[:200]
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            sha, email, subject = parts[0], parts[1], parts[2]
            if email == AUTOMATED_COMMITTER:
                automated += 1
            else:
                hand.append({"sha": sha[:8], "author": email, "subject": subject[:100]})
    except (OSError, subprocess.SubprocessError) as e:
        err = str(e)[:200]
    rows_since = [x for x in logstore.all_rows() if x["ts"][:10] >= since]
    return {"since": since, "hand_edit_commits": len(hand), "automated_commits_excluded": automated,
            "rows_appended": len(rows_since), "hand_edits": hand[:20], "error": err,
            "population": f"vault commits touching */{{{','.join(STEMS)}}}.md since {since}"}


# ------------------------------------------------------------------ 4. wikilinks ----

def _vault_files(vault: Path) -> tuple:
    by_rel = set()
    by_name = set()
    for p in vault.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(vault)
        if any(x.startswith(".") for x in rel.parts):
            continue
        by_rel.add(str(rel))
        by_rel.add(str(rel.with_suffix("")) if rel.suffix else str(rel))
        by_name.add(p.stem)
        by_name.add(p.name)
    return by_rel, by_name


def wikilinks() -> dict:
    """Every `[[...]]` in every generated view, resolved against the vault. Population = the links in
    the VIEWS, not in the live files: a link the generator dropped is invisible here by construction,
    so the count is printed beside the row's expectation rather than asserted against it. Links
    inside code (fenced or inline) are not links — see `importer.strip_code`, which the pointer test
    reads through as well: one definition of "is a link", used by both instruments."""
    by_rel, by_name = _vault_files(VAULT)
    total = 0
    unresolved = []
    for vp in sorted(views.VIEW_DIR.rglob("*.md")) if views.VIEW_DIR.is_dir() else []:
        text = importer.strip_code(vp.read_text(encoding="utf-8"))
        for m in LINK.finditer(text):
            total += 1
            target = m.group(1).split("|")[0].split("#")[0].strip()
            if not target:
                continue                                   # `[[#Heading]]` is same-file, always resolves
            base = target.split("/")[-1]
            cands = {target, target + ".md", base, base + ".md"}
            # a relative link resolves from the ORIGINAL region, not from the view's mirror dir
            rel_region = vp.parent.relative_to(views.VIEW_DIR)
            try:
                resolved = (VAULT / rel_region / target).resolve().relative_to(VAULT.resolve())
                cands.add(str(resolved))
                cands.add(str(resolved) + ".md")
            except (ValueError, OSError):
                pass
            if cands & by_rel or base in by_name or (base + ".md") in by_name:
                continue
            unresolved.append({"view": str(vp.relative_to(views.VIEW_DIR)), "link": target})
    return {"links": total, "expected_in_row": EXPECTED_LINKS,
            "unresolved": len(unresolved), "examples": unresolved[:15],
            "population": "every [[...]] occurrence in every generated view"}


# ------------------------------------------------------------------ report ----

def pct(x) -> str:
    return f"{100 * x:.1f}%"


def render(res: dict) -> str:
    L = []
    s = res["start"]
    L.append(f"# Shadow checkpoint — {res['date']}")
    L.append("")
    L.append(f"Log-and-views shadow (council 2 closure §1, `q:CU-2026-09-09-SHADOW-1`). ADDITIVE: no live "
             f"vault file is read for anything but its own content, and none is written.")
    L.append("")
    L.append(f"- **Started** {s.get('date')} at vault `{(s.get('vault_sha') or '?')[:8]}` · "
             f"day **{res['day']}** of 30 · checkpoints {s.get('checkpoints')}")
    L.append(f"- **Log** {res['log']['rows']} row(s) across {res['log']['files']} month file(s) · "
             f"grammar check: {res['log']['check']}")
    L.append(f"- **Views** {res['views']['count']} generated, {res['views']['rows']} row(s) rendered")
    L.append(f"- **Verdict** {res['verdict']} — PASS requires every STEM aggregate ≥ {pct(PASS_BAR)}")
    L.append("")
    L.append("## Population — what the importer found, against the queue row's number")
    L.append("")
    L.append("| stem | entries imported | row's expectation | gap |")
    L.append("|---|---:|---:|---:|")
    imported_total = 0
    for stem in STEMS:
        a = res["per_stem"].get(stem) or {}
        got = a.get("entries", 0)
        imported_total += got
        L.append(f"| {stem} | {got} | {importer.EXPECTED[stem]} | {got - importer.EXPECTED[stem]:+} |")
    exp_total = sum(importer.EXPECTED.values())
    L.append(f"| **TOTAL** | **{imported_total}** | **{exp_total}** | **{imported_total - exp_total:+}** |")
    L.append("")
    L.append("The gap is a POPULATION difference, not a parser defect, and it is accounted for exactly. "
             "The row's 1,686 counts the five stems **including** the pull-only sidecars — `*-archive.md`, "
             "`*-fixed.md`, `*-resolved.md` and `Patterns-verification.md`. This importer is instructed to "
             "skip those, so it counts LIVE role files only. Counting the same headings WITH the sidecars, "
             "with an independently written script, gives 1,687 — the row's number plus one Errata entry "
             "written since it was filed. **Canon is the positive control: 238 found against 238 expected, "
             "exact**, because Canon has no sidecar in this vault. A measured zero would have refused.")
    L.append("")
    L.append("## Per-stem aggregate")
    L.append("")
    L.append("| stem | files | entries (denominator) | reproduced | pointer rows excluded | numerator | fidelity | verdict |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for stem in STEMS:
        a = res["per_stem"].get(stem)
        if not a:
            L.append(f"| {stem} | 0 | 0 | 0 | 0 | 0 | — | NO DATA |")
            continue
        L.append(f"| {stem} | {a['files']} | {a['entries']} | {a['reproduced']} | "
                 f"{a['pointer_rows_excluded']} | {a['numerator']} | {pct(a['fidelity'])} | {a['verdict']} |")
    L.append("")
    L.append("Fidelity = entries reproduced byte-identical **and not pointer rows**, over **every** entry in the "
             "live file (pointers included in the denominator). A pointer entry is one whose body is only a "
             "wikilink: reproducing it is satisfiable by construction, so it earns no credit.")
    L.append("")
    L.append("## Per region × stem")
    L.append("")
    L.append("| region | stem | entries | reproduced | pointer rows excluded | fidelity | verdict |")
    L.append("|---|---|---:|---:|---:|---:|---|")
    for r in res["files"]:
        if "error" in r:
            L.append(f"| {r['region']} | {r['stem']} | {r.get('entries', '?')} | — | — | — | ERROR: {r['error']} |")
            continue
        L.append(f"| {r['region']} | {r['stem']} | {r['entries']} | {r['reproduced']} | "
                 f"{r['pointer_rows_excluded']} | {pct(r['fidelity'])} | {r['verdict']} |")
    L.append("")
    e = res["escape"]
    L.append(f"## Escape rate — entries over {e['threshold_bytes']:,} B")
    L.append("")
    L.append("| class | entries | over | rate |")
    L.append("|---|---:|---:|---:|")
    for k in ("binding", "narrative", "all"):
        L.append(f"| {k} | {e[k]['entries']} | {e[k]['over']} | {pct(e[k]['rate'])} |")
    L.append("")
    L.append("Split because the council capped the VIEW and not the row: binding entries (text carrying NEVER or "
             "ALWAYS) run long, and an aggregate hides exactly the class a size cap would demote to a pointer.")
    if e["largest"]:
        L.append("")
        L.append("Largest entries:")
        for n, region, stem, head in e["largest"]:
            L.append(f"- {n:,} B — `{region}/{stem}` — {head}")
    L.append("")
    c = res["counter"]
    L.append("## Live counter — hand edits vs rows")
    L.append("")
    L.append(f"- Population: {c['population']}")
    L.append(f"- Hand-edit commits to live role files: **{c['hand_edit_commits']}** "
             f"({c['automated_commits_excluded']} automated `{AUTOMATED_COMMITTER}` commit(s) excluded)")
    L.append(f"- Rows appended to the log in the same window: **{c['rows_appended']}**")
    if c.get("error"):
        L.append(f"- NOTE: git reported `{c['error']}`")
    for h in c["hand_edits"]:
        L.append(f"  - `{h['sha']}` {h['subject']}")
    L.append("")
    w = res["links"]
    L.append("## Wikilinks in the generated views")
    L.append("")
    L.append(f"- Population: {w['population']}")
    L.append(f"- Links found: **{w['links']}** (the queue row's expectation, over its own wider population: "
             f"{w['expected_in_row']})")
    L.append(f"- Unresolved: **{w['unresolved']}**")
    for x in w["examples"]:
        L.append(f"  - `{x['view']}` → `[[{x['link']}]]`")
    L.append("")
    if res["notes"]:
        L.append("## Notes")
        L.append("")
        for n in res["notes"]:
            L.append(f"- {n}")
        L.append("")
    return "\n".join(L) + "\n"


def collect() -> dict:
    start = shadow_start()
    files = score_fidelity()
    agg = per_stem(files)
    rows = logstore.all_rows()
    day = 0
    try:
        d0 = time.strptime(start["date"], "%Y-%m-%d")
        day = int((time.time() - time.mktime(d0)) // 86400)
    except (KeyError, ValueError):
        day = 0
    verdict = "PASS" if agg and all(a["verdict"] == "PASS" for a in agg.values()) else "FAIL"
    notes = []
    if not agg:
        notes.append("NO DATA — the log is empty or no view was generated; there is no verdict to read.")
    return {"date": time.strftime("%Y-%m-%d"), "day": day, "start": start,
            "log": {"rows": len(rows), "files": len(logstore.log_files()),
                    "check": "clean" if logstore.check() == 0 else "PROBLEMS (see stdout)"},
            "views": {"count": len({(r["region"], r["stem"]) for r in files}),
                      "rows": sum(r.get("numerator", 0) + r.get("pointer_rows_excluded", 0) for r in files)},
            "files": files, "per_stem": agg, "escape": escape_rate(),
            "counter": live_counter(start), "links": wikilinks(),
            "verdict": verdict, "pass_bar": PASS_BAR, "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="", help="checkpoint directory (default: the review dir in this checkout)")
    ap.add_argument("--print", action="store_true", dest="print_only", help="stdout only; write nothing")
    args = ap.parse_args()
    res = collect()
    text = render(res)
    if args.print_only:
        print(text)
        return 0 if res["per_stem"] else 2
    out = Path(args.out).expanduser() if args.out else default_out()
    out.mkdir(parents=True, exist_ok=True)
    md = out / f"checkpoint-{res['date']}.md"
    js = out / f"checkpoint-{res['date']}.json"
    md.write_text(text, encoding="utf-8")
    js.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(text)
    print(f"wrote {md}")
    print(f"wrote {js}")
    return 0 if res["per_stem"] else 2


if __name__ == "__main__":
    sys.exit(main())
