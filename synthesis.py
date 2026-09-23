#!/usr/bin/env python3
"""synthesis.py — what has this vault learned that it has not yet acted on? stdlib only.

    python3 synthesis.py                  # the candidate brief, for a person
    python3 synthesis.py --json           # the candidate pack, for the synthesis agent
    python3 synthesis.py --mark           # record that a pass completed (refuses without artifacts)

Cleanup asks whether the vault is tidy. Synthesis asks the other question, and it is the only pass
that reads ACROSS regions — so it is the only one that can see the same lesson written down in three
places under three names, or a rule the vault states plainly and has never turned into a check.

**This file is the MECHANICAL half, and it judges nothing.** It surfaces candidates: high recall,
low precision, deliberately. Throwing away is the agent's job (`agents/memory-synthesizer.md`), and
a judgment about meaning is exactly what a regex must not be trusted with.

**Why the mechanical half is a script at all.** The pass it was ported from asked one agent to do
both halves from prose. An agent that cannot read a region returns fewer findings and says nothing;
a scanner that cannot read a region says so, by name, on the same surface as the hits. Every count
this file emits carries a `checked` flag beside it, and a count that could not be taken prints
UNCHECKED — never 0.

**It writes nothing into memory.** Its only write is the pass stamp (`--mark`), and that refuses
unless the artifacts it is stamping for actually exist on disk.

## The two finding classes

**(a) a prose rule that has never been mechanized** — an entry carrying rule-shaped imperative prose
with no check, test or guard named anywhere near it. The proposal is the SHAPE of a check that would
fail on a violation; the agent never writes the check itself, because a read-only pass emitting
unrun code creates a stale artifact that outlives its accuracy.

**(b) a lesson recurring in two or more regions** — the same discriminating vocabulary in entries
that never reference each other, which is what a promotion to the vault's shared top-level file is
for: one statement, with a pointer from each region, instead of two copies that drift apart.

A contradiction against live CODE is a third class and is out of scope here, as it was in the pass
this was ported from: it requires reading the user's repositories, and a memory vault has no
standing to judge them.
"""
from __future__ import annotations

import argparse, json, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import archive                                                            # noqa: E402
import recall                                                             # noqa: E402
import config                                                             # noqa: E402
import maintenance                                                        # noqa: E402

# ★ NO module-level VAULT, and no `from config import vault`. Both freeze the vault at import, and
# on 2026-09-15 that freeze rewrote 263 files of a real memory vault because a test set
# GEDAECHTNIS_VAULT after the module was already in `sys.modules`. Every function here resolves
# `config.vault()` at CALL time.

SOURCE_FILES = ("Errata.md", "Patterns.md")
# The two role files a synthesis reads. Canon is deliberately NOT here: a locked decision is not a
# lesson awaiting promotion, and proposing that one be moved or generalised is a governance act.

PASS_DIR_NAME = "Synthesis"

MIN_SHARED_TERMS = 3
# How many discriminating terms two entries must share before they are treated as the same lesson.
# Two is the vocabulary of a topic ("archive", "budget"); three is the vocabulary of a claim. Tuned
# for recall, not precision — a false cluster costs the agent one paragraph of reading, and a missed
# one costs a promotion nobody proposes again for a fortnight.

COMMON_TERM_FLOOR = 4
# No term is corpus furniture until it appears in at least this many entries, WHATEVER the share
# says. A share alone is a trap in a small vault: at four entries reviewed, `0.25 * 4 = 1`, so every
# term shared by exactly the two entries a cluster is MADE of counts as furniture and the cluster
# can never form. Measured on the first fixture run — class (b) returned 0 on a vault built to
# contain one obvious cluster. `recall.py` records the same shape from the other end (its own
# docstring: when no term clears the share, the normalisation stops discriminating).

COMMON_TERM_SHARE = 0.25
# A term appearing in more than this share of the scanned entries is corpus furniture and does not
# count toward a cluster. MEASURED per run from the entries actually read, never a fixed list of
# English words: a vault about archiving has "archive" in half its entries, and a fixed stoplist
# written elsewhere cannot know that. The threshold is looser than `recall.COMMON_TERM_SHARE`
# (0.05) on purpose — recall ranks one question against a whole vault, where a term shared by 5% of
# entries is already noise; here the corpus is two role files per region and the same 5% would
# discard the vocabulary the cluster is made of.

_RULE_LEAD = re.compile(
    r"^\s*(?:[-*+]\s*)?\*\*(?:rule|do instead|the rule|the durable rule|never|always|"
    r"the fix|instead)\b", re.I | re.M)
_RULE_SENTENCE = re.compile(
    r"(?:^|(?<=[\s*_(]))(?:never|always|must(?:\s+not)?|the fix is to|any\s+\w+\s+must)\b", re.I)
_BACKLINK = re.compile(
    r"(?:tests?/[\w./-]+|[\w./-]+\.py\b|\bguard(?:ed|s)?\b|\bself-check\b|\btrap\b|"
    r"\bpre-commit\b|\bhook\b|\bvalidator\b|\bassert(?:s|ion|ed)?\b)", re.I)
# Deliberately generous. A false "this is already mechanized" DROPS a candidate and nothing ever
# says so, while a false "not mechanized" costs the agent one entry of reading. The asymmetry
# decides the direction: when in doubt, the backlink pattern matches and the entry is dropped only
# if it really does name a mechanism.

_LINK = re.compile(r"\[\[([^\]|#]+)")


def today() -> str:
    return maintenance.today()


# ------------------------------------------------------------------ reading ----
def shared_files(vault: Path) -> list[Path]:
    """The vault's shared top-level memory files — the promotion TARGET for class (b).

    Derived, never named: the `.md` files sitting directly in the vault root, plus those in a
    top-level directory that `maintenance.regions()` deliberately excludes from the region walk
    (the vault this grew from calls that directory `Global/`; the package does not care what it is
    called and must not). A vault laid out flat has one set and a vault laid out in umbrellas has
    the other, and neither is written down here."""
    out = []
    try:
        for p in sorted(vault.glob("*.md")):
            if p.is_file() and not p.is_symlink():
                out.append(p)
        for name in sorted(maintenance.EXCLUDED_REGION_DIRS):
            d = vault / name
            if d.is_dir():
                out.extend(sorted(q for q in d.glob("*.md")
                                  if q.is_file() and not q.is_symlink()))
    except OSError:
        return []
    return out


def read_entries(path: Path) -> tuple[list[dict], bool]:
    """(entries, checked) for one role file. A file that cannot be read is UNCHECKED, not empty."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], False
    _head, blocks = archive.split_entries(text)
    out = []
    for block in blocks:
        # The splitter returns each entry with its leading blank line still attached, so the
        # heading is the first NON-EMPTY line. Taking `partition("\n")` straight off the block
        # yields "" for every heading — which reads, in a report, as an entry with no title rather
        # than as a parser that is wrong.
        line, _, rest = block.lstrip("\n").partition("\n")
        out.append({"heading": line.strip().lstrip("#").strip(), "body": rest})
    return out, True


def significant_terms(heading: str, body: str) -> set[str]:
    """The entry's vocabulary, before the corpus stoplist is applied.

    `recall.terms` is the package's tokenizer; this file does not carry a second one. Only the
    heading and the entry's BOLD lines are read: the lesson of an entry is stated in its heading and
    its emphasised lines, and taking the whole body makes every long entry share terms with every
    other long entry — which is the bulk-wins failure `recall.py`'s own docstring records."""
    bolds = " ".join(re.findall(r"\*\*(.+?)\*\*", body, re.S))
    return {t for t in recall.terms(f"{heading} {bolds}") if len(t) >= 5}


def mark_rare(entries: list[dict]) -> set[str]:
    """Give every entry a `terms` and a `rare` set, and return the stoplist that produced them.

    The stoplist is MEASURED from these entries and belongs to the CROSS-REGION question: a term
    two regions share only because everything in the vault mentions it says nothing about whether
    they learned the same lesson.

    It does not transfer to the within-region question `split.py` asks, and the attempt is recorded
    here because the reasoning is tempting: a region's dominant topic is most of that region, so
    measured against its own region — or against a vault that has only one region, which is what
    this package creates by default — every word the topic is made of is furniture, and the one
    cluster worth finding is the one that can never form. `split.py` therefore shares this module's
    TOKENIZER and not its stoplist, and says so where it does."""
    n = len(entries)
    df: dict[str, int] = {}
    for e in entries:
        e["terms"] = significant_terms(e["heading"], e["body"])
        for t in e["terms"]:
            df[t] = df.get(t, 0) + 1
    common = {t for t, d in df.items()
              if n and d >= COMMON_TERM_FLOOR and d > COMMON_TERM_SHARE * n}
    for e in entries:
        e["rare"] = e["terms"] - common
    return common


def already_promoted(body: str, shared_stems: set[str]) -> bool:
    """Does this entry already point at a shared top-level file?

    A region entry linking `[[Patterns#…]]` when `Patterns.md` is a shared file has been promoted
    already, and re-proposing it is how a periodic pass becomes noise the reader learns to skip.
    Matched on the link TARGET's file stem, so `[[../../Global/Patterns#x]]`, `[[Global/Patterns]]`
    and `[[Patterns]]` are one answer."""
    for target in _LINK.findall(body):
        stem = target.strip().rstrip("/").split("/")[-1]
        if stem.endswith(".md"):
            stem = stem[:-3]
        if stem in shared_stems:
            return True
    return False


# --------------------------------------------------------------- the classes ----
def is_rule_shaped(heading: str, body: str) -> bool:
    return bool(_RULE_LEAD.search(body) or _RULE_SENTENCE.search(heading)
                or _RULE_SENTENCE.search(body))


def names_a_mechanism(body: str) -> bool:
    return bool(_BACKLINK.search(body))


def scan(vault: Path | None = None) -> dict:
    """Every candidate, with a `checked` flag beside every count.

    Returns the pack the agent reads. Nothing here decides whether a candidate is a finding."""
    vault = vault or config.vault()
    shared = shared_files(vault)
    shared_stems = {p.stem for p in shared}

    entries: list[dict] = []
    unreadable: list[str] = []
    regions_checked = True
    try:
        region_dirs = maintenance.regions()
    except Exception:
        region_dirs, regions_checked = [], False
    # A region the walk could not LIST does not raise and does not appear — it simply is not in the
    # list, and the count above it reads as a complete answer about a vault half of which was never
    # opened. Asked directly, by name.
    try:
        blind = maintenance.unreadable_dirs(vault)
    except Exception:
        blind, regions_checked = [], False
    if blind:
        regions_checked = False
        unreadable.extend(f"{d}/ (directory not listable)" for d in blind)

    for region in region_dirs:
        for name in SOURCE_FILES:
            p = region / name
            if not p.is_file():
                continue
            got, ok = read_entries(p)
            rel = str(p.relative_to(vault))
            if not ok:
                unreadable.append(rel)
                continue
            for e in got:
                e["file"] = rel
                e["region"] = str(region.relative_to(vault))
                entries.append(e)

    # --- the corpus stoplist, measured from what was actually read -------------
    n = len(entries)
    common_terms = mark_rare(entries)

    # --- class (a) -------------------------------------------------------------
    class_a = [
        {"region": e["region"], "file": e["file"], "heading": e["heading"],
         "bytes": len(e["body"]), "why": "rule-shaped prose, no mechanism named nearby"}
        for e in entries
        if is_rule_shaped(e["heading"], e["body"]) and not names_a_mechanism(e["body"])
    ]

    # --- class (b) -------------------------------------------------------------
    live = [e for e in entries if not already_promoted(e["body"], shared_stems)]
    parent = list(range(len(live)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    shared_by: dict[tuple[int, int], set[str]] = {}
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            if live[i]["region"] == live[j]["region"]:
                continue                     # one region twice is a motif, not a promotion
            both = live[i]["rare"] & live[j]["rare"]
            if len(both) >= MIN_SHARED_TERMS:
                shared_by[(i, j)] = both
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(len(live)):
        groups.setdefault(find(i), []).append(i)

    class_b = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # The 2+ regions rule is enforced ONCE, on the edge above ("one region twice is a motif"),
        # and a second check here — `if len(regions_in) < 2: continue` — cannot fire: every member
        # of a group was joined by a cross-region edge, so any group of two or more spans two or
        # more regions. It was here, it was VACUOUS, and it made the rule untestable: neutering the
        # edge rule left the second check holding the test up, so the control passed while the rule
        # it guards was gone. Found by mutating the rule and watching its own test stay green —
        # the eighth decorative control in this arc, and the first whose cause was a spare copy of
        # a correct rule rather than a wrong assertion.
        regions_in = sorted({live[i]["region"] for i in members})
        terms_in: set[str] = set()
        for (i, j), both in shared_by.items():
            if i in members and j in members:
                terms_in |= both
        class_b.append({
            "regions": regions_in,
            "shared_terms": sorted(terms_in),
            "entries": [{"region": live[i]["region"], "file": live[i]["file"],
                         "heading": live[i]["heading"]} for i in sorted(members)],
        })
    class_b.sort(key=lambda c: (-len(c["entries"]), c["regions"]))

    dropped_promoted = len(entries) - len(live)
    return {
        "generated": today(),
        "vault": str(vault),
        "window": window(),
        "trigger": trigger(),
        "counts": {
            "regions": len(region_dirs),
            "regions_checked": regions_checked,
            "entries_read": n,
            "files_unreadable": len(unreadable),
            "class_a": len(class_a),
            "class_b": len(class_b),
            "dropped_already_promoted": dropped_promoted,
        },
        "shared_files": [str(p.relative_to(vault)) for p in shared],
        "unreadable": sorted(unreadable),
        "common_terms": sorted(common_terms),
        "class_a": sorted(class_a, key=lambda c: (c["region"], c["file"], c["heading"])),
        "class_b": class_b,
        "pass_dir": str(pass_dir(vault, today()).relative_to(vault)),
    }


# ------------------------------------------------------------------- window ----
def window() -> dict:
    """When the last pass ran, and whether we actually know."""
    state = maintenance.read_state()
    if not state:
        return {"since": None, "checked": False,
                "why": "no maintenance state yet — this vault has not completed a session end"}
    since = state.get("last_synthesis")
    if not since:
        return {"since": None, "checked": False, "why": "maintenance state names no last_synthesis"}
    days = maintenance.days_between(str(since), today())
    return {"since": str(since), "days": days, "checked": days is not None,
            "why": None if days is not None else "the recorded date could not be read as a date"}


def trigger() -> dict:
    """The synthesis arms `hooks/maintenance.py` already computes. R7 adds no trigger logic and
    reads no dates of its own: two implementations of one trigger disagree exactly when it matters."""
    state = maintenance.read_state()
    if not state:
        return {"due": None, "checked": False, "arms": {}}
    try:
        doc = maintenance.compute(state, os.getcwd(), today())
    except Exception as exc:
        return {"due": None, "checked": False, "arms": {}, "error": str(exc)}
    s = doc.get("synthesis") or {}
    return {"due": s.get("due"), "checked": True, "arms": s.get("arms") or {}}


def pass_dir(vault: Path, day: str) -> Path:
    return vault / PASS_DIR_NAME / day


# ------------------------------------------------------------------- render ----
def _arm_line(name: str, arm: dict) -> str:
    if not arm.get("checked", True):
        return f"  {name}: UNCHECKED"
    fired = "FIRED" if arm.get("fired") else "quiet"
    detail = ", ".join(f"{k}={v}" for k, v in arm.items()
                       if k not in ("fired", "checked") and v is not None)
    return f"  {name}: {fired}" + (f" ({detail})" if detail else "")


def render(pack: dict) -> str:
    c = pack["counts"]
    w = pack["window"]
    t = pack["trigger"]
    out = [f"Synthesis candidates — {pack['generated']}."]
    if w["checked"]:
        out.append(f"Window: since {w['since']} ({w['days']} days).")
    else:
        out.append(f"Window: UNCHECKED — {w['why']}.")
    if t["checked"]:
        out.append("Due: " + ("yes" if t["due"] else "no"))
        for name, arm in (t["arms"] or {}).items():
            out.append(_arm_line(name, arm))
    else:
        out.append("Due: UNCHECKED — the maintenance state could not be computed.")

    regions = c["regions"] if c["regions_checked"] else "UNCHECKED"
    out.append("")
    out.append(f"Read {c['entries_read']} entries across {regions} region(s); "
               f"{c['class_a']} class (a) candidate(s), {c['class_b']} class (b) cluster(s), "
               f"{c['dropped_already_promoted']} entry(s) dropped as already promoted.")
    if pack["unreadable"]:
        out.append(f"UNCHECKED — {len(pack['unreadable'])} file(s) could not be read: "
                   + ", ".join(pack["unreadable"]))

    out.append("")
    out.append(f"CLASS (a) — a rule with no mechanism named ({c['class_a']})")
    for i, e in enumerate(pack["class_a"], 1):
        out.append(f"  {i}. {e['file']}: {e['heading']}")
    out.append("")
    out.append(f"CLASS (b) — one lesson, two or more regions ({c['class_b']})")
    for i, cl in enumerate(pack["class_b"], 1):
        out.append(f"  {i}. {', '.join(cl['regions'])} — shared: "
                   f"{', '.join(cl['shared_terms'][:8])}")
        for e in cl["entries"]:
            out.append(f"       {e['file']}: {e['heading']}")
    out.append("")
    out.append("These are CANDIDATES. Nothing above has been judged, and nothing has been changed.")
    return "\n".join(out)


# --------------------------------------------------------------------- mark ----
def mark(vault: Path | None = None, day: str | None = None) -> dict:
    """Record that a pass completed — and REFUSE unless its artifacts exist.

    The stop condition for a synthesis pass is artifact-existence on disk, never a model's judgment
    that it finished. A pass stamped without a report is worse than an unstamped one: the stamp
    moves the window forward, so everything the missing report would have covered is now behind it
    and no later pass will look there again."""
    vault = vault or config.vault()
    day = day or today()
    d = pass_dir(vault, day)
    want = [d / "report.md", d / "findings.json"]
    missing = [str(p.relative_to(vault)) for p in want if not p.is_file()]
    if missing:
        return {"marked": False, "missing": missing, "dir": str(d)}
    stamped = maintenance.record_pass("synthesis", day)
    return {"marked": True, "last_synthesis": stamped, "dir": str(d),
            "wrote": [str(p.relative_to(vault)) for p in want]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Surface what this vault has learned and not yet acted on. Judges nothing.")
    ap.add_argument("--json", action="store_true", help="the candidate pack, for the agent")
    ap.add_argument("--mark", action="store_true",
                    help="record a completed pass (refuses unless its artifacts exist)")
    args = ap.parse_args(argv)

    if args.mark:
        res = mark()
        print(json.dumps(res, indent=2) if args.json else (
            f"Pass recorded: last_synthesis = {res['last_synthesis']}."
            if res["marked"] else
            "REFUSED — a pass is not recorded without its artifacts. Missing: "
            + ", ".join(res["missing"])))
        return 0 if res["marked"] else 1

    pack = scan()
    print(json.dumps(pack, indent=2, default=sorted) if args.json else render(pack))
    return 0


if __name__ == "__main__":
    sys.exit(main())
