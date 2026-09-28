"""deferral_door.py — a line that puts work off without naming the queue row it went to (DEFERDOOR-1).

WHY. Owner 2026-09-22: "what can we do to keep this from happening — losing the info, decisions,
skipping things". Specimen: 2,600 authored per-sense examples sat unlanded for 25 days, "filed as
their own row" in a docstring, a report, a notice and a review section — and no row existed.
Every one of those sentences was true when written; the row it promised was never made.

WHAT IT DOES. After an Edit/Write/MultiEdit, each NEW line of the written text that says the work
went somewhere else ("filed", "file this", "tracked", "deferred", "later", "own row",
"follow-up") and carries no queue id (`q:XX-YYYY-MM-DD-NAME`) on the same line is listed in one
note, with its line number in the file. It never blocks. It reads:
  * `.md` files — reports, handoffs and `Channels/` notices;
  * `.py` files — only comments and docstrings, where such promises are written;
and skips test and fixture files, whose text is data. Off unless the config sets
`deferral_door: true`.

WHAT A "NEW" LINE IS. For an Edit, a line of `new_string` that is not a line of `old_string`; for
a Write, every line of the content (the tool payload does not carry what the file held before, so
an overwrite is read whole). A docstring edit whose opening quotes are outside the edited fragment
is not seen as a docstring — the door is quiet there rather than loud.
"""
from __future__ import annotations
import re
from pathlib import Path

import config

PHRASE = re.compile(r"\b(?:filed|file this|tracked|deferred|later|own row|follow-?up)\b", re.I)
QID = re.compile(r"q:[A-Z]{2}-\d{4}-\d{2}-\d{2}-[A-Z0-9-]+")
MAX_LINES = 5


def enabled() -> bool:
    return config.flag("deferral_door", False)


def kind_of(p: Path) -> str | None:
    """"md", "py", or None when the door does not read this file."""
    parts = {x.lower() for x in p.parts[:-1]}
    name = p.name.lower()
    if (parts & {"tests", "test", "fixtures", "fixture"} or name.startswith("test_")
            or name == "conftest.py" or "fixture" in name):
        return None
    return {".md": "md", ".py": "py"}.get(p.suffix.lower())


def _pairs(tool_input: dict) -> list[tuple[str, str]]:
    """(old, new) text pairs from an Edit, a Write or a MultiEdit payload."""
    if not isinstance(tool_input, dict):
        return []
    out = []
    if isinstance(tool_input.get("content"), str):
        out.append(("", tool_input["content"]))
    if isinstance(tool_input.get("new_string"), str):
        out.append((tool_input.get("old_string") or "", tool_input["new_string"]))
    for e in tool_input.get("edits") or []:
        if isinstance(e, dict) and isinstance(e.get("new_string"), str):
            out.append((e.get("old_string") or "", e["new_string"]))
    return out


def _prose_lines(text: str, kind: str) -> list[tuple[str, str]]:
    """(line, the part of it that is prose) — a `.py` line's comment or docstring text only."""
    if kind == "md":
        return [(l, l) for l in text.split("\n")]
    out, in_doc = [], False
    for line in text.split("\n"):
        n = line.count('"""') + line.count("'''")
        if in_doc or n:
            out.append((line, line))
        elif "#" in line:
            out.append((line, line.split("#", 1)[1]))
        if n % 2:
            in_doc = not in_doc
    return out


def findings(p: Path, tool_input: dict) -> list[str]:
    kind = kind_of(p)
    if kind is None:
        return []
    try:
        file_lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        file_lines = []
    first_at: dict[str, int] = {}                      # one pass, not one scan per finding
    for i, fl in enumerate(file_lines):
        first_at.setdefault(fl, i + 1)
    out = []
    for old, new in _pairs(tool_input):
        before = set(old.split("\n"))
        for line, prose in _prose_lines(new, kind):
            if line in before or not PHRASE.search(prose) or QID.search(line):
                continue
            n = first_at.get(line)
            out.append(f"{p.name}:{n or '?'}: {line.strip()[:140]}")
    return out


def notice(p: Path, tool_input: dict) -> str | None:
    if not enabled():
        return None
    got = findings(p, tool_input)
    if not got:
        return None
    more = f" (and {len(got) - MAX_LINES} more)" if len(got) > MAX_LINES else ""
    return ("Deferral without a queue row — these new lines put work off but name no `q:` id:\n  "
            + "\n  ".join(got[:MAX_LINES]) + more
            + "\nIf the work is not done here, file the row and put its id on the line.")
