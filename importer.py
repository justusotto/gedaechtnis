#!/usr/bin/env python3
"""importer.py — read every region's role files and append one log row per entry. READ-ONLY on the vault.

    python3 importer.py                 # import; prints a per-stem table against the expected counts
    python3 importer.py --dry-run       # parse and count, append nothing
    python3 importer.py --json          # the same counts as JSON

What it walks. Every REGION — a folder that carries a `Map.md`, the vault root's own umbrellas and
`Global` included. Skipped, because they are not memory and carry no role files: `Concilium/`,
`Pharos/`, `Channels/`, `Workflows/`, `Limen/`, every hidden directory, and the pull-only sidecars
`*-archive.md`, `*-fixed.md`, `*-resolved.md`.

What it parses. The five stems `Canon · Errata · Patterns · Position · Aporia`. An ENTRY is:

  * a `##` or `###` heading with everything under it up to the next heading — the shape every
    substantive role file in this vault actually uses; or
  * where a file carries no such heading at all, a top-level `- ` bullet with its indented
    continuation lines — the bullet-structured shape (a thin Position file, a stub Aporia).

Headings inside a fenced code block are not headings. That one rule is worth its lines: a fence in
`Errata.md` quoting a shell transcript would otherwise mint an entry out of a comment.

**This module never opens a vault file for writing.** If a file cannot be parsed into entries
without changing it, the importer prints it under `UNPARSED` and moves on — the file is reported,
never repaired.

Also written, per role file that has entries: two SHAPE rows, `# PREAMBLE` (the bytes above the
first entry) and `# ORDER` (the entry headings in the file's own order). They are counted apart from
the entries everywhere, and they exist so `views.py --cold` can rebuild a file from the log alone.
A file with no entries gets neither — it is not in the shadow at all, and giving it shape rows would
invent a view out of a stub.

Idempotent by construction: `logstore.append` is keyed on (region, content hash), so a second run
over an unchanged vault appends zero rows and says so.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logstore
from logstore import VAULT, STEMS

SKIP_TOP = ("Concilium", "Pharos", "Channels", "Workflows", "Limen")
SIDECAR = re.compile(r"-(archive|fixed|resolved)$")

# The stem a memory entry came from decides what KIND of thing it is. This is the whole of the
# generalisation from `ledger.py`'s lane kinds: a Canon entry is a decision wherever it lives.
KIND_OF_STEM = {"Canon": "decision", "Errata": "lesson", "Patterns": "lesson",
                "Position": "state", "Aporia": "question"}

# The row's population, from `q:CU-2026-09-09-SHADOW-1`. Kept here so the importer prints its own
# count BESIDE the number it is supposed to match, rather than leaving the reader to remember it.
EXPECTED = {"Canon": 238, "Errata": 365, "Patterns": 436, "Position": 477, "Aporia": 170}

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING = re.compile(r"^##(#)? ")
BULLET = re.compile(r"^- ")
LINK = re.compile(r"\[\[[^\]\n]+\]\]")
BINDING = re.compile(r"\bNEVER\b|\bALWAYS\b")
FENCE_BLOCK = re.compile(r"^(?:\s*)(`{3,}|~{3,}).*?(?:\n(?:\s*)\1|\Z)", re.S | re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`")


def strip_code(text: str) -> str:
    """Blank out fenced blocks and inline code spans, PRESERVING length so nothing else shifts.

    A `[[…]]` inside backticks is not a wikilink: this vault writes bash `[[ -f x ]]` tests and
    `[[File#heading]]` TEMPLATES in prose, and both the pointer test and the checkpoint's link check
    read them as links otherwise. Repaired in the EXTRACTOR rather than silenced with a skip list —
    checkpoint 0 produced exactly three "unresolved links" and all three were code.
    """
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return INLINE_CODE.sub(blank, FENCE_BLOCK.sub(blank, text))


def regions(vault: Path = None) -> list:
    """-> [(region_name, dir)] every folder carrying a Map.md, vault-relative name, sorted."""
    vault = vault or VAULT
    out = []
    for m in sorted(vault.rglob("Map.md")):
        rel = m.parent.relative_to(vault)
        parts = rel.parts
        if any(p.startswith(".") for p in parts):
            continue
        if parts and parts[0] in SKIP_TOP:
            continue
        out.append((str(rel) if parts else ".", m.parent))
    return out


def role_files(region_dir: Path) -> list:
    """-> [(stem, path)] the five stems present in this region, sidecars excluded."""
    out = []
    for stem in STEMS:
        p = region_dir / f"{stem}.md"
        if p.is_file() and not SIDECAR.search(p.stem):
            out.append((stem, p))
    return out


def _fence_mask(lines) -> list:
    """-> [bool] True where the line is inside (or is) a fenced code block."""
    mask = [False] * len(lines)
    fence = None
    for i, ln in enumerate(lines):
        m = FENCE.match(ln)
        if m:
            tok = m.group(1)[0]
            mask[i] = True
            if fence is None:
                fence = tok
            elif fence == tok:
                fence = None
            continue
        mask[i] = fence is not None
    return mask


def split_entries(text: str) -> tuple:
    """-> (preamble, [(heading, body)]) with preamble + ''.join(heading + '\\n' + body) == text.

    Byte-exactness is the point of this function, and the test suite asserts the identity above over
    every live role file: a shadow that reproduces 'the same entry, reformatted' has measured its own
    formatter, not the memory.
    """
    lines = text.splitlines(keepends=True)
    mask = _fence_mask(lines)
    starts = [i for i, ln in enumerate(lines) if not mask[i] and HEADING.match(ln)]
    if not starts:
        return _split_bullets(lines, mask)
    return _cut(lines, starts)


def _split_bullets(lines, mask) -> tuple:
    """The bullet-structured fallback: only for a file with no `##`/`###` heading at all. A top-level
    `- ` bullet opens an entry; its indented continuation lines and the blank lines after it belong
    to it, exactly as they sit on disk."""
    starts = [i for i, ln in enumerate(lines) if not mask[i] and BULLET.match(ln)]
    if not starts:
        return "".join(lines), []
    return _cut(lines, starts)


def _cut(lines, starts) -> tuple:
    preamble = "".join(lines[:starts[0]])
    out = []
    for k, i in enumerate(starts):
        j = starts[k + 1] if k + 1 < len(starts) else len(lines)
        heading = lines[i].rstrip("\n")
        body = "".join(lines[i + 1:j])
        out.append((heading, body))
    return preamble, out


def reconstruct(heading: str, body: str) -> str:
    """The one and only way an entry becomes text again. `views.py` and `shadow_score.py` both call
    it, so a byte the generator adds is a byte the scorer sees — the alternative is two spellings of
    'the same entry' and a fidelity number that measures the gap between them."""
    return heading + "\n" + body


def joined(preamble: str, entries) -> str:
    return preamble + "".join(reconstruct(h, b) for h, b in entries)


def is_pointer(heading: str, body: str) -> bool:
    """An entry that is only a wikilink to somewhere else — it carries a location, not knowledge.

    Excluded from the fidelity NUMERATOR (council 2, Melchior's own bar retracted): reproducing a
    one-line pointer byte-identical is satisfiable by construction and proves nothing about whether
    the shadow can carry an argument.
    """
    text = strip_code(body)                      # a `[[…]]` inside backticks is not a link
    if not LINK.search(text):
        return False
    rest = LINK.sub(" ", text)
    rest = re.sub(r"[#*_>`\-–—:;.,()\[\]|]", " ", rest)
    return len(re.sub(r"\s+", " ", rest).strip()) < 40


def is_binding(heading: str, body: str) -> bool:
    """Its text carries NEVER or ALWAYS. The escape-rate split turns on this and nothing else:
    the class that most often exceeds a size cap is exactly the class a cap must not demote."""
    return bool(BINDING.search(heading) or BINDING.search(body))


def entry_flags(heading: str, body: str, ordinal: int) -> list:
    flags = []
    if is_pointer(heading, body):
        flags.append("pointer")
    if is_binding(heading, body):
        flags.append("binding")
    flags.append(f"ord={ordinal}")
    return flags


def entries_of_file(region: str, stem: str, path: Path) -> tuple:
    """-> ([entry dict], [shape dict], [(rel, why)]) for ONE role file: the per-file half of `scan`.

    Split out so the session writer (hooks/chore.py) can log the entries of the single file a
    session just edited without walking the vault — one parser, one definition of "an entry",
    used by the sweep and by the live writer alike. Two spellings of that would make the src
    column measure the difference between two parsers instead of the difference between two
    writers.

    THE SHAPE ROWS are the second return value: `# PREAMBLE` (the bytes above the first entry) and
    `# ORDER` (the entry headings, one per line, in the file's own order). They exist because a
    cold build — `views.py --cold`, the file rebuilt from the log with the live file moved aside —
    needs both, and until now `views.py` read both off the live file. Council 3's C-04: that is
    precisely why day 0's "72 of 72 byte-identical" could not fail on those bytes.

    ORDER is a row of its OWN rather than a flag on each entry, because an entry that MOVES does
    not change its own content and so mints no new row: the `ord=N` flags in the log go stale the
    moment anything is inserted above them, silently. The `# ORDER` row does change when the order
    changes, so it is re-minted, and the newest one wins like every other correction.

    A file with no entries gets NO shape rows and no entry rows — it is not in the shadow at all,
    which is what it already was; giving it shape rows alone would invent a 73rd view out of a stub.
    """
    unparsed = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [], [], [(str(path), f"unreadable: {e}")]
    pre, blocks = split_entries(text)
    if not blocks and text.strip():
        unparsed.append((str(path), "no `##`/`###` heading and no top-level `- ` bullet"))
    elif joined(pre, blocks) != text:
        unparsed.append((str(path), "entries do not re-join byte-identical (missing final newline?)"))
    out = []
    for n, (heading, body) in enumerate(blocks):
        out.append({"region": region, "stem": stem, "kind": KIND_OF_STEM[stem],
                    "heading": heading, "body": body,
                    "flags": entry_flags(heading, body, n), "path": str(path)})
    shape = []
    if blocks:
        for heading, body, flag in ((logstore.PREAMBLE_HEADING, pre, "preamble"),
                                    (logstore.ORDER_HEADING,
                                     "\n".join(h for h, _b in blocks), "order")):
            shape.append({"region": region, "stem": stem, "kind": "note", "heading": heading,
                          "body": body, "flags": [flag], "path": str(path)})
    return out, shape, unparsed


def log_one_file(region: str, stem: str, path: Path, src: str = "session") -> dict:
    """Append a row for every entry of ONE live role file the log does not already carry.

    This is the SESSION WRITE PATH (council 3, K-3): `hooks/chore.py` calls it the moment a session's
    Edit or Write to a role file lands, so the edit becomes a row in the same act rather than
    waiting for the next importer sweep. Rows are stamped `src=session`, and because `src` is not
    part of the identity hash, an entry the importer already swept is a no-op here — the column
    counts who got there FIRST, which is exactly the cut-over numerator.

    The SHAPE rows go in too, and they must: a session that adds an entry has changed the file's
    heading order, and a cold build reading a stale `# ORDER` row would rebuild the file the way it
    looked before the edit and call the difference a fidelity miss.

    -> {'entries', 'appended': [row ids], 'shape_appended': [row ids], 'unparsed'}. Reads the file,
    appends rows; it never writes a vault role file, same as every other function in this module.
    """
    found, shape, bad = entries_of_file(region, stem, path)
    known = logstore._index(logstore.all_rows())
    appended, shape_appended = [], []
    for e in found:
        rid, wrote = logstore.append(e["region"], e["kind"], e["stem"], e["heading"], e["body"],
                                     e["flags"], known=known, src=src)
        if wrote:
            appended.append(rid)
    for s in shape:
        rid, wrote = logstore.append(s["region"], s["kind"], s["stem"], s["heading"], s["body"],
                                     s["flags"], known=known, src=src)
        if wrote:
            shape_appended.append(rid)
    return {"entries": len(found), "appended": appended, "shape_appended": shape_appended,
            "unparsed": bad}


def scan(vault: Path = None) -> dict:
    """-> {'entries': [...], 'per_stem': {...}, 'unparsed': [...]} — a pure read of the vault."""
    vault = vault or VAULT
    entries = []
    shape = []
    per_stem = {s: 0 for s in STEMS}
    unparsed = []
    for region, d in regions(vault):
        for stem, p in role_files(d):
            # `unparsed` is LOUD, never normalised: the shape that reaches it is a file whose last
            # line is a heading with no terminating newline, and a shadow that silently added that
            # byte would be reporting its own formatter as fidelity.
            found, shp, bad = entries_of_file(region, stem, p)
            for _path, why in bad:
                unparsed.append((str(p.relative_to(vault)), why))
            for e in found:
                e["path"] = str(p.relative_to(vault))
                entries.append(e)
                per_stem[stem] += 1
            for s in shp:
                s["path"] = str(p.relative_to(vault))
                shape.append(s)
    return {"entries": entries, "shape": shape, "per_stem": per_stem, "unparsed": unparsed}


def run(dry_run: bool = False, vault: Path = None) -> dict:
    found = scan(vault)
    appended = 0
    skipped = 0
    shape_appended = 0
    if not dry_run:
        known = logstore._index(logstore.all_rows())
        for e in found["entries"]:
            _rid, wrote = logstore.append(e["region"], e["kind"], e["stem"], e["heading"],
                                          e["body"], e["flags"], known=known,
                                          src="importer")
            if wrote:
                appended += 1
            else:
                skipped += 1
        # counted apart from the entries, and always: a shape row is not a memory, and folding it
        # into `appended` would move the number the EXPECTED table is read against.
        for s in found["shape"]:
            _rid, wrote = logstore.append(s["region"], s["kind"], s["stem"], s["heading"],
                                          s["body"], s["flags"], known=known, src="importer")
            if wrote:
                shape_appended += 1
    return {"per_stem": found["per_stem"], "entries": len(found["entries"]),
            "appended": appended, "already_present": skipped,
            "shape_rows": len(found["shape"]), "shape_appended": shape_appended,
            "unparsed": found["unparsed"], "regions": len(regions(vault))}


def report(res: dict) -> None:
    total = sum(res["per_stem"].values())
    exp_total = sum(EXPECTED.values())
    print(f"regions walked: {res['regions']}")
    print(f"{'stem':10} {'found':>7} {'expected':>9} {'gap':>6}")
    for stem in STEMS:
        got = res["per_stem"][stem]
        exp = EXPECTED[stem]
        print(f"{stem:10} {got:7} {exp:9} {got - exp:+6}")
    print(f"{'TOTAL':10} {total:7} {exp_total:9} {total - exp_total:+6}")
    print(f"appended: {res['appended']}   already present (no-op): {res['already_present']}")
    print(f"shape rows (# PREAMBLE / # ORDER, one pair per file with entries): "
          f"{res['shape_rows']} found, {res['shape_appended']} appended — these are what "
          f"`views.py --cold` rebuilds a file's preamble and heading order from")
    if res["unparsed"]:
        print("UNPARSED (reported, never repaired):")
        for path, why in res["unparsed"]:
            print(f"  {path}: {why}")
    if total == 0:
        raise SystemExit("REFUSED: the importer found ZERO entries. A measured zero needs a positive "
                         "control — check the vault path and run `--dry-run` against a known file.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="parse and count; append nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    # There is deliberately no `--vault`: the log lives INSIDE the vault, so reading one vault while
    # writing another's log is a shape this tool must not be able to express. Point GEDAECHTNIS_VAULT
    # at a mini-vault instead and both halves move together (that is what the test suite does).
    res = run(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0
    report(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
