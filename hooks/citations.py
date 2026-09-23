"""citations.py — resolve the citation tokens in text at the moment it is WRITTEN.

WHY AT WRITE TIME. A citation goes stale silently: the row it names gets closed, the ruling it
cites gets superseded, the id was mistyped and never existed. Nothing errors, and the sentence goes
on reading exactly as it did when it was true. A batch that swept the vault afterwards would arrive
after the file was written, which is too late to change what the writer believes — so this runs on
the write, and what it produces is a note to the writer, never a refusal.

WHAT IT RESOLVES, and what each answer means:

  `q:<ID>`            against the queue files. The row is OPEN, CLOSED, or ABSENT. Citing a CLOSED
                      row as if it were live work, or an id no row carries, is the common shape.
  `owner-ruling-*`    against the authority ledger. The record is LIVE, SUPERSEDED, or ABSENT.
                      Citing a superseded ruling is the mechanism this package's own census found
                      to be the largest single class.

★ A q-id IS NOT UNIQUE IN A QUEUE FILE — rows cite each other, so the id's first occurrence is
usually not the row that OWNS it. Ownership is decided by the same two independent readings the
fleet's own sweep uses: the backticked anchor directly after the checkbox, and the `| q:<ID>`
metadata field. When both are present and disagree, the row is AMBIGUOUS and this module says so
rather than choosing — a sweep whose match is not unique stops instead of guessing. That rule is
written down because ignoring it once already produced a report calling two closed rows open.

TWO THINGS IT DELIBERATELY DOES NOT DO:

  * It does not fire on a token inside a FENCED CODE BLOCK or a BLOCKQUOTE. There the id is being
    shown, quoted or explained, not cited — a document that reproduces an old queue row, or an
    example of the format, is not making a claim about that row's state. This is the mandatory
    negative control, and it is the same shape as the defect where a rule matched an id inside
    quoted prose because it searched the raw string instead of the cleaned body. Inline backticks
    are NOT excluded: `q:CU-…` in backticks is how a real citation is written here, and excluding
    it would switch the feature off while looking like caution.
  * It resolves at most `CAP` tokens per write. A document can mention a hundred ids; resolving all
    of them would put a filesystem sweep on the critical path of every edit.

TWO SHAPES IT FIRES ON THAT ARE ARGUABLY NOT CITATIONS, named rather than fixed: an id inside a
markdown link URL, and an id inside a table cell. Neither occurs in the live queue files today, and
both read as real citations more often than not — but a reader meeting a note about one should know
it was a judgment, not an oversight.

WHAT BOUNDS THE COST, honestly. `CAP` bounds the per-token resolution and NOT the dominant cost,
which is reading the queue tree ONCE per write that cites anything (measured: ~65ms over a 2.4 MB /
23-file tree, against ~0.03ms for a write with no citation tokens at all, which exits before any
I/O). That read grows with the queue tree, so `MAX_QUEUE_BYTES` puts a ceiling on it: past that size
the q-half goes quiet rather than slow, which is the same "unresolvable is not wrong" answer the
module gives when there are no queue files. Do not read `CAP` as a cost guarantee — the early exit
and the byte ceiling are what keep this off the critical path.
"""
from __future__ import annotations
import os, re

CAP = 20
# The ceiling on the queue-tree read described above. Generous on purpose: it exists so the cost
# cannot grow without bound as the queues do, not to switch the feature off — the tree it was
# measured against is 2.4 MB.
MAX_QUEUE_BYTES = int(os.environ.get("GEDAECHTNIS_CITATION_MAX_QUEUE_BYTES") or 16 * 1024 * 1024)

# ★ Both patterns must END on an alphanumeric. A citation almost always sits at the end of a
# sentence, and a trailing `.` or `-` swept into the token makes it resolve against nothing — so a
# LIVE ruling reports as ABSENT, which is the most alarming answer this module can give and is
# produced by punctuation. The failure is loud rather than silent, which is the only reason it was
# caught at once.
QID = re.compile(r"\bq:([A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)")
RULING = re.compile(r"\b(owner-ruling-[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
# The two readings of a row's OWN id — deliberately the same pair the fleet's sweep uses.
ROW_ANY = re.compile(r"^\s*-\s*\[([ xX])\]\s")
ANCHORED = re.compile(r"^\s*-\s*\[[ xX]\]\s+`q:([A-Za-z0-9][A-Za-z0-9\-]*)`")
FIELD_QID = re.compile(r"\|\s*q:([A-Za-z0-9][A-Za-z0-9\-]*)\b")
FENCE = re.compile(r"^\s*(?:```|~~~)")


def quotable_text(text: str) -> str:
    """The text with fenced code blocks and blockquotes blanked out, lines preserved.

    Lines are replaced rather than removed so that anything reported about the result still lines
    up with the original — and so a fence that is never closed blanks the rest of the document
    rather than silently reverting to scanning it.
    """
    out, in_fence = [], False
    for line in (text or "").split("\n"):
        if FENCE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence or line.lstrip().startswith(">"):
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)


def tokens(text: str) -> "tuple[list[str], list[str], bool]":
    """(q-ids, ruling refs, capped?) found in the citable part of `text`, in first-seen order."""
    body = quotable_text(text)
    qs = list(dict.fromkeys(QID.findall(body)))
    rs = list(dict.fromkeys(RULING.findall(body)))
    capped = len(qs) + len(rs) > CAP
    while len(qs) + len(rs) > CAP:
        (rs if len(rs) >= len(qs) else qs).pop()
    return qs, rs, capped


def row_state(qid: str, queue_text: str) -> str:
    """'open' | 'closed' | 'ambiguous' | 'absent' — the state of the row that OWNS this id."""
    states = set()
    for line in queue_text.split("\n"):
        m = ROW_ANY.match(line)
        if not m:
            continue
        a = ANCHORED.match(line)
        aid = a.group(1) if a else None
        fids = set(FIELD_QID.findall(line))
        fid = fids.pop() if len(fids) == 1 else None
        if len(fids) > 1 or (aid and fid and aid != fid):
            # The row's own identity is not decidable; if either reading names our id, say so
            # rather than counting it as a match or as an absence.
            if qid in ({aid} | set(FIELD_QID.findall(line))):
                states.add("ambiguous")
            continue
        own = aid or fid
        if own == qid:
            states.add("closed" if m.group(1).lower() == "x" else "open")
    if "ambiguous" in states:
        return "ambiguous"
    if not states:
        return "absent"
    if states == {"closed"}:
        return "closed"
    if states == {"open"}:
        return "open"
    return "ambiguous"                                 # the same id owned by two rows in two states


def ruling_state(ref: str, records: "list[tuple[str, str]]", superseded_rx) -> str:
    """'live' | 'superseded' | 'absent', over the parsed authority ledger."""
    blocks = [b for r, b in records if r == ref]
    if not blocks:
        return "absent"
    return "superseded" if any(superseded_rx.search(b) for b in blocks) else "live"


def notes(qstates: "dict[str, str]", rstates: "dict[str, str]", capped: bool) -> "list[str]":
    """The lines the writer sees. Only the answers worth acting on; silence is the common case."""
    out = []
    closed = [q for q, s in qstates.items() if s == "closed"]
    absent = [q for q, s in qstates.items() if s == "absent"]
    ambig = [q for q, s in qstates.items() if s == "ambiguous"]
    if closed:
        out.append("Cited row(s) already CLOSED: " + ", ".join(f"q:{q}" for q in closed)
                   + ". If this text describes live work, it is citing finished work; if it is a "
                     "record of what was done, say so in the sentence.")
    if absent:
        out.append("Cited q-id(s) that NO row in the queue files carries: "
                   + ", ".join(f"q:{q}" for q in absent)
                   + ". Either the id is mistyped or the row was never filed — a citation whose "
                     "target does not exist reads exactly like one that does.")
    if ambig:
        out.append("Cited q-id(s) whose owning row is AMBIGUOUS (two readings disagree, or two rows "
                   "claim the id): " + ", ".join(f"q:{q}" for q in ambig)
                   + ". Refusing to guess which row you meant.")
    sup = [r for r, s in rstates.items() if s == "superseded"]
    gone = [r for r, s in rstates.items() if s == "absent"]
    if sup:
        out.append("Cited ruling(s) marked SUPERSEDED in the authority ledger: " + ", ".join(sup)
                   + ". The record that replaced it is named in its `superseded-by:` line — cite "
                     "that one, or say which clause of the old ruling still stands.")
    if gone:
        out.append("Cited ruling(s) with NO record in the authority ledger: " + ", ".join(gone)
                   + ". An authority that cannot be resolved is not an authority; record it, or "
                     "cite the one that exists.")
    if out and capped:
        out.append(f"(Only the first {CAP} citation tokens in this write were resolved.)")
    return out
