"""authority.py — an authority ledger must stay resolvable by the tool that reads it.

WHAT AN AUTHORITY LEDGER IS. Some installations keep an append-only record of decisions that
authorize automated action — a ruling, its date, the words that were actually said, and a `ref:`
token that scripts cite when they act under it. The ledger is only worth having if a reader can
still resolve it later, and two things quietly destroy that:

  * **A record with no `ref:` line.** The grep that confirms "this action is authorized" finds
    nothing, so the ruling exists for a human reader and does not exist for the tool. It fails in
    the reassuring direction: the ledger looks fuller than it is.
  * **An amendment with no forward marker on what it amended.** A later record says it supersedes
    an earlier one, the earlier one says nothing, and every reader who arrives at the earlier record
    first — which is most of them, since it comes first — reads a superseded rule as current.

The second is the larger class by measurement. In the census this module was built from, an
append-only record amended by a later block with NO forward marker in the older one was the single
biggest mechanism, at 9 of 46 specimens; together with records written where the next reader does
not look, the two account for 22 of 46.

WHAT THIS CHECKS, on the content of a write, before it lands:

  1. every record the write ADDS carries a `ref:` (or `id:`) line;
  2. a record the write adds that declares `amends:`/`supersedes:` a ref WHICH EXISTS IN THIS
     LEDGER leaves that older record carrying a `superseded-by:` line, in the SAME write.

Rule 2 fires only when the amended thing is a record in this ledger. Amending a design document, a
section number, or anything else is ordinary and is not this module's business — that restraint is
what keeps the rule from becoming noise, and it has its own negative control.

WARN NOW, DENY WITH THE PARTITION FLIP. The caller reads `partition_mode()`, so this door and the
partition door flip together in one act: a second refusing door must not land before the first one
has been proven in the open.

VAULT-AGNOSTIC BY CONSTRUCTION. This module knows no path. The ledger is named by `authority_log`
in the config file, and an installation that configures none never reaches any of this.
"""
from __future__ import annotations
import re

# A record opens at column 0 in one of three shapes this format has accumulated. All three are
# read, because a checker that knows only the newest shape reports the older ones as violations —
# MEASURED on the ledger this was built against: 50 `## ruling:` headers, 66 `- id:` rows and 18
# standalone `- ref:` rows, 134 records in all.
#
# ★ `- ref:` is ambiguous and the ambiguity is the whole difficulty: it opens a record on its own,
# AND it is the first field INSIDE a `## ruling:` record. Treating every one as an opener splits
# each header record in two and reports the header half as having no `ref:` line — 53 false
# violations on a ledger with none. So a `- ref:` directly under a `## ruling:` header is that
# header's field, not a new record.
HEADER = re.compile(r"^## ruling:\s*(?P<ref>\S+)")
RECORD_OPENERS = (
    HEADER,
    re.compile(r"^- id:\s*(?P<ref>\S+)"),
    re.compile(r"^- ref:\s*(?P<ref>\S+)"),
)
# `ref:` / `id:` at any indent — the shapes differ on whether the fields under a record are indented.
#
# ★ The whitespace around a field's value is `[ \t]`, NEVER `\s`. These patterns are searched
# against a whole multi-line record, and `\s` matches a NEWLINE: with `\s*` after the colon, a
# `ref:` line whose value is missing (a stray trailing space, or the value typed on the following
# line) let the pattern walk past the end of the line and take the NEXT line's first token as the
# ref. A record with no ref at all then read as having one, and the rule this module exists for
# went quiet — a false negative with no symptom, in the one shape it is most likely to meet.
REF_LINE = re.compile(r"^[ \t]*(?:-[ \t]*)?(?:ref|id):[ \t]*(?P<ref>\S+)", re.M)
SUPERSEDED_BY = re.compile(r"^[ \t]*(?:-[ \t]*)?superseded-by:", re.M)
AMENDS = re.compile(r"^[ \t]*(?:-[ \t]*)?(?P<verb>amends|supersedes):[ \t]*(?P<value>.+)$", re.M)
# A ref token as it appears inside an `amends:` value, which is prose: "owner-ruling-x (2026-01-01;
# PARTIAL — …)" or a bare id. Deliberately NOT length-limited: what makes a token a ref is that it
# NAMES A RECORD IN THIS LEDGER (`t in by_ref` below), and that test does the real work. An earlier
# draft also demanded four characters, which bought nothing and silently exempted every short ref
# from the rule — a blind spot with no symptom, since the door simply stayed quiet.
REF_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def records(text: str) -> "list[tuple[str, str]]":
    """[(ref, block_text)] in file order. A record's block runs to the next record opener."""
    lines = (text or "").split("\n")
    starts: list[tuple[int, str]] = []
    prev_nonblank = ""
    for i, line in enumerate(lines):
        for rx in RECORD_OPENERS:
            m = rx.match(line)
            if m:
                if rx is not HEADER and HEADER.match(prev_nonblank):
                    break                              # a field of the header record above, not a record
                starts.append((i, m.group("ref").strip()))
                break
        if line.strip():
            prev_nonblank = line
    out = []
    for n, (i, ref) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out.append((ref, "\n".join(lines[i:end])))
    return out


def problems(old_text: str, new_text: str) -> "list[str]":
    """What is wrong with the records this write ADDS. Empty list = nothing to say.

    Judged on what the write ADDS, never on what the ledger already holds: an append-only file
    accumulates history under rules that changed, and a door that re-judged the whole file on every
    write would refuse an unrelated append because of somebody's record from a year ago.
    """
    before = {ref for ref, _ in records(old_text or "")}
    after = records(new_text or "")
    # A ref can name MORE THAN ONE record — this format grows addenda, which repeat the ref of the
    # record they extend. Keeping only the last would report a forward marker sitting on the first
    # twin as missing, so every record under a ref is kept and the marker may be on any of them.
    by_ref: dict[str, list[str]] = {}
    for ref, block in after:
        by_ref.setdefault(ref, []).append(block)
    found: list[str] = []

    for ref, block in after:
        if ref in before:
            continue                                   # not this write's record

        if not REF_LINE.search(block):
            found.append(
                f"the new record `{ref}` carries no `ref:` line. The tooling that confirms an "
                f"action is authorized greps for that line, so without it this ruling exists for a "
                f"human reader and does not exist for any script citing it. Add `- ref: {ref}` "
                f"inside the record.")

        for m in AMENDS.finditer(block):
            verb, value = m.group("verb"), m.group("value").strip()
            targets = [t for t in dict.fromkeys(REF_TOKEN.findall(value)) if t in by_ref and t != ref]
            for target in targets:
                if not any(SUPERSEDED_BY.search(b) for b in by_ref[target]):
                    found.append(
                        f"the new record `{ref}` {verb} `{target}`, but `{target}` carries no "
                        f"`superseded-by:` line. A reader who reaches `{target}` first — which most "
                        f"do, since it comes earlier — would read a superseded ruling as current. "
                        f"Add `- superseded-by: {ref} (date; FULL or PARTIAL — what changed)` to "
                        f"`{target}` in this same write.")
    return found


def post_write_text(current: str, tool: str, ti: dict) -> "str | None":
    """The file's content AFTER this tool call, or None when it cannot be determined honestly.

    None is a real answer and the caller must treat it as one: a door that guessed at the resulting
    text would judge a write nobody is about to make. Every shape it cannot reconstruct — a Bash
    redirect whose content the payload does not carry, an anchor that does not appear in the file,
    an anchor that appears more times than the edit expects — returns None and the door stands down.
    """
    if tool == "Write":
        c = ti.get("content")
        return c if isinstance(c, str) else None
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    if not isinstance(edits, list):
        return None
    text = current
    for e in edits:
        if not isinstance(e, dict):
            return None
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return None
        n = text.count(old)
        if n == 0:
            # Absent anchor. This branch is load-bearing for `replace_all`, where `str.replace`
            # would otherwise return the text UNCHANGED and hand the door a post-write text for an
            # edit that is going to fail.
            return None
        if n > 1 and not e.get("replace_all"):
            return None                                # ambiguous; the edit will fail too
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text
