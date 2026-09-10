#!/usr/bin/env python3
"""recall.py — what does the vault already know about this? stdlib only, read-only.

    python3 recall.py "why is the queue file's trailing newline load-bearing"
    python3 recall.py --limit 3 --max-bytes 2000 "apostrophe"

Prints the best-matching entries VERBATIM, each under its file path and heading, newest question
first, and nothing else. It never writes, moves, or commits anything; the only filesystem calls
it makes are reads.

**Why verbatim, and why an entry rather than a line.** A summary of a decision is a new decision:
the reason a vault is written down at all is that a paraphrase drifts, and the paraphrase that
drifts is the one that gets acted on. So a hit is returned as the text that is on disk, and the
unit is the ENTRY — a heading and everything under it until the next heading — because a matched
line lifted out of its entry loses the qualifier that made it true.

**Lexical, deliberately.** Terms from the question are matched as whole words, and (for terms of
five characters or more) as prefixes, so `commit` finds `commits` and `committed`. There is no
embedding model and no network call: this must work on a laptop with no key configured, and a
grep that finds four of five entries is worth more than a semantic search that is not installed.

**Ranked by COVERAGE first — of the terms that DISCRIMINATE — and no entry wins on BULK.** An
entry that mentions three of the question's terms beats one that mentions a single term thirty
times; a file that merely repeats one common word is the classic false first hit. Two corrections,
both measured on the recall bench against a real vault on 2026-09-10 (`eval/recall_bench/`):

1. *Coverage is itself a bulk signal.* A long section contains more of a question's incidental
   words — `add`, `look`, `files`, `once` — so it covers MORE distinct terms than the entry that
   actually answers the question, and wins before size is ever consulted. A 665 KB queue section
   covers every question completely; that ranking scored **0 of 30** with the right entry in the
   hit list every time. So a term that appears in more than `COMMON_TERM_SHARE` of the entries
   that matched at all does not count toward coverage: the stoplist is MEASURED from the corpus
   per question, not read from a fixed list of English function words.
2. *Frequency is a bulk signal too.* The tie-break at equal coverage is weighted frequency
   divided by the entry's size relative to the mean matched entry (`LENGTH_NORM_B`), so the entry
   that says it in one paragraph beats the chapter that says it in forty.

`--rank flat` restores the pre-2026-09-10 order (plain coverage, raw frequency) and `--rank
length-only` applies the size normalisation without the measured stoplist — the two controls the
bench uses to attribute a change in the numbers to one half or the other.

**Queues and outboxes are not memory.** `Pharos/` (work queues, roadmaps) and `Channels/` (lane
notice outboxes) are excluded by default — they are append-heavy operational surfaces that restate
the whole vault's vocabulary and answer no question about what was decided or what went wrong.
`--include-queues` searches them anyway, for the caller who is actually asking about a queue.

**Archives are searched.** A `-archive`, `-fixed` or `-resolved` sibling holds the narrative that
the live file compressed away; excluding it would hide exactly the reasoning the caller is asking
for. Their entries are marked `(archive)` so the caller knows the live file is the authority.

**The output is capped** (6,000 B by default). A recall answer is read into a session's context,
so an uncapped one costs more than the re-derivation it exists to prevent. An entry too long to
fit is cut with a marked `[…]` rather than dropped, and the footer says how many hits were not
shown.
"""
from __future__ import annotations
import argparse, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import config  # noqa: E402  (every path is resolved there)

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".obsidian", ".trash"}
# Not memory: work queues and lane notice outboxes, at any depth. Excluded unless asked for.
NON_MEMORY_DIRS = frozenset({"Pharos", "Channels"})
MAX_FILE_BYTES = 2_000_000

# BM25's length normalisation, and the one number that tunes it: a hit's weighted frequency is
# divided by `(1 - b) + b * size / pivot`, where `pivot` is the mean size of the entries that
# matched. At b = 1 the division is FULL — the tie-break is term density, which is what the bench
# measured against the failure mode (a section winning because it is long), so full is what ships;
# 0.75 is BM25's textbook value and leaves a long section able to win on bulk. This is the constant
# to move if the ranking is ever retuned.
LENGTH_NORM_B = 1.0
# Below this many characters an entry stops being "more precise" and is just short — a bare
# heading with a subsection under it is not a better answer than the paragraph that answers the
# question, so length stops buying rank here. Applied to each entry AND to the pivot.
MIN_ENTRY_CHARS = 200
# A query term found in more than this SHARE of the entries that matched anything is common in
# this corpus, for this question, and does not count toward coverage. 0.05 is deliberately harsh:
# on the bench, 0.05 through 0.20 all scored the same recall@3 (18 of 30 on the row corpus), and
# the lower the threshold the fewer bytes the caller reads before the answer. The share is
# measured over the MATCHED entries, so it needs no index and no second pass over the vault.
COMMON_TERM_SHARE = 0.05
# The rankings `search()` will apply. `a-prime` is the shipped one; the other two exist so a
# measurement can attribute a change to one half of it — see the module docstring.
RANKINGS = ("a-prime", "length-only", "flat")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
ARCHIVE_RE = re.compile(r"-(archive|fixed|resolved)$", re.I)

# Words that would match half the vault and rank nothing. Kept short on purpose: a stoplist that
# grows starts eating the domain terms a question is actually about.
STOP = frozenset("""
a an and are as at be because been before but by can did do does for from had has have how i if
in into is it its me my no not of on or our so than that the their then there these they this to
was we were what when where which who why will with without you your
""".split())


def terms(question: str) -> list[str]:
    """The question's salient terms: lowercased, de-duplicated, stopwords and one/two-letter
    tokens dropped. Order is preserved so the report can name them back to the caller."""
    out: list[str] = []
    for tok in re.findall(r"[0-9A-Za-z_À-ɏͰ-ӿ]+", question.lower()):
        if len(tok) > 2 and tok not in STOP and tok not in out:
            out.append(tok)
    return out


def md_files(vault: Path, include_queues: bool = False):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")
                             and (include_queues or d not in NON_MEMORY_DIRS))
        for name in sorted(filenames):
            if name.endswith(".md"):
                p = Path(dirpath) / name
                try:
                    if p.is_file() and not p.is_symlink() and p.stat().st_size <= MAX_FILE_BYTES:
                        yield p
                except OSError:
                    continue


def entries(path: Path, text: str):
    """(heading, body) for each heading in the file, plus the preamble above the first one.

    An entry runs to the NEXT heading of any level, so a subsection is its own entry and is not
    also swallowed by its parent: a question about the subsection should be answered with the
    subsection, not with twelve screens of the section it happens to live in."""
    lines = text.splitlines()
    cuts = [i for i, l in enumerate(lines) if HEADING_RE.match(l)]
    if not cuts or cuts[0] > 0:
        head = f"(top of {path.name})"
        body = "\n".join(lines[:cuts[0]] if cuts else lines).strip("\n")
        if body.strip():
            yield head, body
    for n, start in enumerate(cuts):
        end = cuts[n + 1] if n + 1 < len(cuts) else len(lines)
        yield lines[start].strip(), "\n".join(lines[start:end]).rstrip()


def patterns(want: list[str]) -> list:
    """One compiled pattern per term, built ONCE per question rather than once per entry: whole
    word, and a prefix for terms of five characters or more, so `commit` finds `committed`."""
    return [re.compile(r"\b" + re.escape(t) + (r"\w*" if len(t) >= 5 else r"\b")) for t in want]


def score(heading: str, body: str, pats: list) -> list[int]:
    """Weighted frequency PER TERM, in the question's own term order — a term in the heading
    counts triple. Per term, not summed, because the rank needs to know WHICH terms an entry
    matched: how common a term is across the corpus decides whether it counts toward coverage."""
    hay, head = body.lower(), heading.lower()
    return [len(p.findall(hay)) + 3 * len(p.findall(head)) for p in pats]


def search(vault: Path, question: str, include_queues: bool = False,
           ranking: str = "a-prime") -> tuple[list[dict], list[str]]:
    """Every matching entry, best first. The caller decides how many to print — the count of
    what was NOT printed is part of the answer, so it is never truncated here.

    `ranking="flat"` is the pre-2026-09-10 order (plain coverage, raw weighted frequency) and
    `"length-only"` is that order with the size normalisation but without the measured stoplist.
    Both are kept as CONTROLS: with `flat`, the bulky section that merely repeats the question's
    terms wins again, which is what proves the new rank is what changed the order rather than some
    other edit made the same day."""
    if ranking not in RANKINGS:
        raise ValueError(f"unknown ranking {ranking!r}: expected one of {', '.join(RANKINGS)}")
    want = terms(question)
    hits: list[dict] = []
    if not want:
        return hits, want
    pats = patterns(want)
    for path in md_files(vault, include_queues=include_queues):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(vault))
        archived = bool(ARCHIVE_RE.search(path.stem))
        for heading, body in entries(path, text):
            tfs = score(heading, body, pats)
            if not any(tfs):
                continue
            hits.append({"path": rel, "heading": heading, "body": body, "archive": archived,
                         "size": max(MIN_ENTRY_CHARS, len(body)), "tfs": tfs,
                         # An archive entry loses a hair of weight, never coverage: it should
                         # surface, but the live file is the authority when both match equally.
                         "weight": sum(tfs) - (1 if archived else 0)})
    rank(hits, ranking)
    # Coverage, then length-normalised weight, then the SHORTER entry (a precise section beats the
    # chapter that contains it), then the path, so the order is total and the same on every machine.
    hits.sort(key=lambda h: (-h["rank"][0], -h["rank"][1], len(h["body"]), h["path"]))
    return hits, want


def rank(hits: list[dict], ranking: str = "a-prime") -> list[dict]:
    """Give every hit its `rank` key `(coverage, length-normalised weight)`, in place.

    The pivot is the matched entries' own mean size, so the rank adapts to the corpus instead of
    to a number chosen against one vault: in a vault of short Errata entries a 4 KB section is
    bulky, in a vault of long Position sections it is not. The common-term share is measured the
    same way, over the same set, for the same reason.

    **The fallback matters.** In a small corpus every term is "common" — with six matched entries,
    one occurrence is already 17% — so when NO term clears `COMMON_TERM_SHARE` for any entry, the
    coverage term falls back to plain coverage rather than ranking every hit at zero. A corpus
    statistic needs a corpus; below that size this behaves exactly like the old rule plus the size
    normalisation."""
    if not hits:
        return hits
    n = len(hits)
    n_terms = len(hits[0]["tfs"])
    pivot = max(MIN_ENTRY_CHARS, sum(h["size"] for h in hits) / n)
    df = [sum(1 for h in hits if h["tfs"][i]) for i in range(n_terms)]
    # `0 < d` matters: a term NO entry matched has df 0, and a rarity test that called it rare
    # would leave every hit at zero coverage and hand the ranking to the tie-break alone.
    rare = [0 < d <= COMMON_TERM_SHARE * n for d in df]
    if ranking != "a-prime" or not any(rare):
        rare = [True] * n_terms
    for h in hits:
        covered = sum(1 for i, tf in enumerate(h["tfs"]) if tf and rare[i])
        norm = 1.0 if ranking == "flat" else (
            (1 - LENGTH_NORM_B) + LENGTH_NORM_B * (h["size"] / pivot))
        h["rank"] = (covered, h["weight"] / norm)
    return hits


def render(hits: list[dict], want: list[str], question: str, max_bytes: int, total: int) -> str:
    if not want:
        return f"recall: {question!r} has no searchable terms — ask it with a noun in it."
    if not hits:
        return (f"No vault entry matches {' '.join(want)}.\n"
                f"Nothing is recorded about this yet; whatever you decide is the first entry.")
    out, used, shown = [], 0, 0
    for h in hits:
        mark = " (archive)" if h["archive"] else ""
        head = f"── {h['path']}{mark}\n"
        room = max_bytes - used - len(head.encode("utf-8"))
        if room < 200:
            break
        body = h["body"]
        if len(body.encode("utf-8")) > room:
            body = body.encode("utf-8")[:room].decode("utf-8", "ignore").rstrip() + "\n[…]"
        out.append(head + body)
        used += len(head.encode("utf-8")) + len(body.encode("utf-8")) + 1
        shown += 1
    footer = f"\n{shown} of {total} matching entr{'y' if total == 1 else 'ies'} for: {' '.join(want)}"
    if shown < total:
        footer += " — narrow the question to see the rest"
    return "\n\n".join(out) + "\n" + footer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Search the vault and print the matching entries verbatim.")
    ap.add_argument("question", nargs="+", help="the question, in plain words")
    ap.add_argument("--limit", type=int, default=6, help="entries to print (default 6)")
    ap.add_argument("--max-bytes", type=int, default=6000, help="output cap (default 6000)")
    ap.add_argument("--vault", default=None, help="search this directory instead of the configured vault")
    ap.add_argument("--include-queues", action="store_true",
                    help="also search Pharos/ and Channels/ (excluded by default: queues and notice "
                         "outboxes are operational surfaces, not memory)")
    ap.add_argument("--rank", choices=RANKINGS, default="a-prime",
                    help="`flat` is the pre-2026-09-10 order (plain coverage, raw frequency) and "
                         "`length-only` adds the size normalisation without the measured stoplist. "
                         "Controls for the bench; `flat` makes the biggest section win again.")
    a = ap.parse_args(argv)
    vault = Path(os.path.expanduser(a.vault)).resolve() if a.vault else config.VAULT
    if not vault.is_dir():
        print(f"recall: no vault at {vault} — run init.py to create one.", file=sys.stderr)
        return 2
    question = " ".join(a.question)
    hits, want = search(vault, question, include_queues=a.include_queues, ranking=a.rank)
    print(render(hits[: max(1, a.limit)], want, question, max(500, a.max_bytes), len(hits)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
