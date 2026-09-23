"""boot_citations.py — "superseded since your source", said once, at boot.

THE FAILURE. A session boots with documents it is told to trust: the memory files, the instruction
files, everything the @-import chain pulls in. Those documents cite things — a queue row, a recorded
ruling. The cited thing then changes, and the citing document does not: the row is closed, the
ruling is superseded, and the sentence that names it goes on reading exactly as it did when it was
true. The session acts on it, and nothing anywhere says otherwise. That is the one class a
mid-session reader structurally cannot see, because by the time the session is running, its sources
are already loaded and already believed.

WHAT IS CHECKED, and the second condition that keeps it quiet:

  1. the boot document cites something that is stale NOW — a ruling the ledger marks SUPERSEDED,
     or (OFF by default, see below) a `q:` row that is CLOSED; AND
  2. the owner moved AFTER the citing document's own last commit — STRICTLY after. Equal commit
     times mean the two moved in the same commit, which is a document and its owner being updated
     together: the opposite of the thing being reported.

Condition 2 is what makes this worth reading. A document written after a row closed is citing
finished work KNOWINGLY — that is a record, not a mistake, and warning about it would be noise on
every boot forever. Only the case where the ground moved under a document that is still loaded is
reported, and that is the case its author could not have known about.

★ WHY CLOSED ROWS ARE OFF AT BOOT, measured rather than assumed. Across this installation's own
boot chain: **6 of 8 cited row-ids name rows that are already CLOSED (75%), against 3 of 27 cited
rulings superseded (11%)**. Citing a closed row is what an always-loaded file NORMALLY does with a
row id — it is provenance, naming the work under which a thing was decided, and it stays true
forever. Reporting it would put a line on every boot that the reader can do nothing about, which is
how a warning becomes scenery. A SUPERSEDED RULING is the opposite: the instruction derived from it
may have changed, and the reader is about to act on the document that states it.

So `include_rows` defaults to False and the row arm stays available and tested — the same check is
genuinely useful at WRITE time (`citations.py`), where the author is actively making the claim and
can judge whether they mean provenance or live work. Boot is the wrong moment for a question only
the author can answer.

COST, and why it is not a sweep. Staleness is decided BEFORE any commit time is fetched, so a boot
where nothing is stale — the ordinary boot — pays no `git` calls at all, however many citations its
documents carry. The sources are the files `session_start` has ALREADY resolved for
its `boot:` line, so the chain costs nothing extra. Resolution is one `git log -1` per OWNER FILE —
not per token — and the owners are a handful of files (the queue tree, the ledger), so a boot adds a
small constant number of git calls, hard-capped by `MAX_GIT_CALLS` and each with a timeout. A
session that cites nothing stale does no git work at all beyond the owners it has to date.

SILENT UNLESS SOMETHING MOVED. No line at all in the ordinary case — which is nearly every boot.
"""
from __future__ import annotations
import os, subprocess
from pathlib import Path

import citations

MAX_GIT_CALLS = int(os.environ.get("GEDAECHTNIS_BOOT_CITATION_MAX_GIT") or 24)
MAX_DOCS = 40
GIT_TIMEOUT = 5


def _commit_time(path: Path, calls: list) -> "int | None":
    """Unix time of the last commit touching `path`, or None if unknown/uncommitted/over budget."""
    if len(calls) >= MAX_GIT_CALLS:
        return None
    calls.append(path)
    repo = path.parent
    try:
        p = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%ct", "--", str(path)],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out.isdigit():
        return None                                   # not a repo, not committed, or git said no
    return int(out)


def stale_citations(docs, queue_text: str, queue_files, ledger_records, ledger_path,
                    superseded_rx, _commit=_commit_time, include_rows: bool = False) -> "list[str]":
    """One line per boot document whose citation went stale after that document was written.

    `_commit` is injectable so the tests can drive the time comparison directly instead of building
    a git history for every case — the comparison IS the rule here, and a test that cannot vary it
    freely would only ever exercise one side of it.
    """
    calls: list = []
    owner_times: dict = {}

    def owner_time(p: Path):
        if p not in owner_times:
            owner_times[p] = _commit(p, calls)
        return owner_times[p]

    out = []
    docs = list(docs)
    truncated = len(docs) > MAX_DOCS
    for doc in docs[:MAX_DOCS]:
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        qs, rs, _ = citations.tokens(text)
        if not qs and not rs:
            continue
        # STALENESS FIRST, TIMES SECOND. Deciding what is stale is pure — a lookup in tables the
        # caller already has — while every commit time is a `git log` subprocess. Asking the cheap
        # question first means a boot where nothing is stale (the ordinary boot) pays NO git calls
        # at all, instead of one per document that happens to cite anything.
        stale_rows = [q for q in (qs if include_rows else [])
                      if queue_text and citations.row_state(q, queue_text) == "closed"]
        stale_rulings = [r for r in rs if ledger_records
                         and citations.ruling_state(r, ledger_records, superseded_rx) == "superseded"]
        if not stale_rows and not stale_rulings:
            continue
        doc_time = _commit(doc, calls)
        if doc_time is None:
            continue                                  # no commit to compare against: say nothing
        moved = []
        for q in stale_rows:
            t = max((owner_time(f) or 0) for f in queue_files) if queue_files else 0
            if t > doc_time:
                moved.append(f"q:{q} (row now CLOSED)")
        for r in stale_rulings:
            t = owner_time(ledger_path) if ledger_path else None
            if t and t > doc_time:
                moved.append(f"{r} (ruling now SUPERSEDED)")
        if moved:
            out.append(f"  - {doc}: " + "; ".join(moved))
    if truncated or len(calls) >= MAX_GIT_CALLS:
        # An UNDISCLOSED cap is a silent miss: the reader cannot tell "nothing is stale" from
        # "I stopped looking". `citations.py` discloses its own cap for the same reason, and this
        # one is disclosed even when nothing was found, because that is exactly when it misleads.
        out.append(f"  - (not every boot document was checked: {len(docs)} documents, "
                   f"{len(calls)} commit lookups, caps {MAX_DOCS}/{MAX_GIT_CALLS}.)")
    return out


def facts_lines(stale: "list[str]") -> "list[str]":
    if not stale:
        return []
    return ["- SUPERSEDED SINCE YOUR SOURCE — a file this session boots with cites something that "
            "changed after that file was last written. What you loaded still reads as it did then:"
            ] + stale + [
            "  Nothing is blocked; read the owner before acting on the citing sentence."]
