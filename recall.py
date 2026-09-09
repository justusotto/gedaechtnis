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

**Ranked by COVERAGE first.** An entry that mentions three of the question's terms beats one that
mentions a single term thirty times — a file that merely repeats one common word is the classic
false first hit. Ties break on weighted frequency, with a term in the heading counting triple.

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
MAX_FILE_BYTES = 2_000_000
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


def md_files(vault: Path):
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
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


def score(heading: str, body: str, want: list[str]) -> tuple[int, int]:
    """(distinct terms matched, weighted frequency). Coverage first — see the module docstring."""
    hay, head = body.lower(), heading.lower()
    covered = weight = 0
    for t in want:
        pat = re.compile(r"\b" + re.escape(t) + (r"\w*" if len(t) >= 5 else r"\b"))
        n_body, n_head = len(pat.findall(hay)), len(pat.findall(head))
        if n_body or n_head:
            covered += 1
            weight += n_body + 3 * n_head
    return covered, weight


def search(vault: Path, question: str) -> tuple[list[dict], list[str]]:
    """Every matching entry, best first. The caller decides how many to print — the count of
    what was NOT printed is part of the answer, so it is never truncated here."""
    want = terms(question)
    hits: list[dict] = []
    if not want:
        return hits, want
    for path in md_files(vault):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(vault))
        archived = bool(ARCHIVE_RE.search(path.stem))
        for heading, body in entries(path, text):
            covered, weight = score(heading, body, want)
            if not covered:
                continue
            hits.append({"path": rel, "heading": heading, "body": body, "archive": archived,
                         # An archive entry loses a hair of weight, never coverage: it should
                         # surface, but the live file is the authority when both match equally.
                         "rank": (covered, weight - (1 if archived else 0))})
    # Coverage, then weight, then the SHORTER entry (a precise section beats the chapter that
    # contains it), then the path, so the order is total and the same on every machine.
    hits.sort(key=lambda h: (-h["rank"][0], -h["rank"][1], len(h["body"]), h["path"]))
    return hits, want


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
    a = ap.parse_args(argv)
    vault = Path(os.path.expanduser(a.vault)).resolve() if a.vault else config.VAULT
    if not vault.is_dir():
        print(f"recall: no vault at {vault} — run init.py to create one.", file=sys.stderr)
        return 2
    question = " ".join(a.question)
    hits, want = search(vault, question)
    print(render(hits[: max(1, a.limit)], want, question, max(500, a.max_bytes), len(hits)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
