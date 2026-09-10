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


def scan(vault: Path = None) -> dict:
    """-> {'entries': [...], 'per_stem': {...}, 'unparsed': [...]} — a pure read of the vault."""
    vault = vault or VAULT
    entries = []
    per_stem = {s: 0 for s in STEMS}
    unparsed = []
    for region, d in regions(vault):
        for stem, p in role_files(d):
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                unparsed.append((str(p.relative_to(vault)), f"unreadable: {e}"))
                continue
            pre, blocks = split_entries(text)
            if not blocks and text.strip():
                # not an error by itself — a stub role file has no entries — but say so out loud
                unparsed.append((str(p.relative_to(vault)),
                                 "no `##`/`###` heading and no top-level `- ` bullet"))
            elif joined(pre, blocks) != text:
                # LOUD, never normalised: the only shape that reaches here is a file whose last line
                # is a heading with no terminating newline, and a shadow that silently added that
                # byte would be reporting its own formatter as fidelity.
                unparsed.append((str(p.relative_to(vault)),
                                 "entries do not re-join byte-identical (missing final newline?)"))
            for n, (heading, body) in enumerate(blocks):
                entries.append({"region": region, "stem": stem, "kind": KIND_OF_STEM[stem],
                                "heading": heading, "body": body,
                                "flags": entry_flags(heading, body, n),
                                "path": str(p.relative_to(vault))})
                per_stem[stem] += 1
    return {"entries": entries, "per_stem": per_stem, "unparsed": unparsed}


def run(dry_run: bool = False, vault: Path = None) -> dict:
    found = scan(vault)
    appended = 0
    skipped = 0
    if not dry_run:
        known = logstore._index(logstore.all_rows())
        for e in found["entries"]:
            _rid, wrote = logstore.append(e["region"], e["kind"], e["stem"], e["heading"],
                                          e["body"], e["flags"], known=known)
            if wrote:
                appended += 1
            else:
                skipped += 1
    return {"per_stem": found["per_stem"], "entries": len(found["entries"]),
            "appended": appended, "already_present": skipped,
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
