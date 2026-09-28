#!/usr/bin/env python3
"""kernelentry.py — a rule with a tool signature does not go into a Boot file (KERNELENTRY-1).

The Boot file (`<Region>/Kernel.md`) is loaded by every session. A rule that only binds when the
work touches one tool — `git`, `pytest`, a `.html` page, `claude -p`, a script, a path glob — is
paid for by every session and read by the few that need it. Its home is a FACET beside the Boot
file (`<Region>/Kernel-<name>.md`, loaded the first time its trigger fires; `lazy_body.py`) or a
door. Kernels grew past their budget because an addition had nowhere else to go; once facets
exist, the cheapest way to keep the core small is to stop the addition at the edit, with the
facet named.

What the door reads. An Edit / MultiEdit / Write on a vault file named exactly `Kernel.md` (a
facet, `Kernel-<name>.md`, is where such rules belong and is never judged). The file's text after
the write is reconstructed with `authority.post_write_text` — shared, not copied; when it cannot
be reconstructed honestly the door stands down. A line is ADDED when it is a bullet (`- `, `* `,
`+ `, `1. `) in the text after the write that the text before does not already hold as many times:
a pure removal, a reorder and a move within the file add nothing, so a split that shrinks the
kernel is never judged. An added bullet carries a SIGNATURE when, after its wikilinks are dropped
(a pointer to a body is the cure, not the disease), any of these hold:

    command       a code span that starts with a command word (`git …`, `pytest`, `rm …`, …)
    claude -p     the programmatic launch, anywhere in the line
    html          a `.html` path, anywhere in the line
    path glob     a code span holding `**` or `*.<ext>`
    script path   a code span naming a `.py` / `.sh` / `.js` / `.mjs` / `.ts` file
    tool name     a code span that is exactly a harness tool name, or an `mcp__…` name
    shell syntax  a code span holding a flag (` -x` / ` --long`), `&&`, a pipe or `$(`

MEASURED BEFORE THE LIST WAS FIXED (2026-09-26, the vault's own history, 30 days): 86 commits
touched a Kernel.md, adding 667 bullet lines; this list fires on 105 of them (16%) in 33 commits.
Before code spans were paired and wikilinks dropped it fired on 202 (30%) — `**bold**` read as a
glob between two unrelated backticks, and an Errata anchor quoted inside `[[…]]` read as shell.
Bash writes to a Kernel.md (`sed -i`, a heredoc) are NOT seen: the payload does not carry the
text; the vault's pre-commit is where a later row would close that.

WARN, THEN DENY — the flip is a DATE in the config, never a person remembering. `limits`
`kernel_entry_deny_from: "YYYY-MM-DD"`: from that day on an added signature bullet is refused;
before it, or with the key empty, the write runs and the model is told what the door would have
said (a note, never a permission decision). A value that is not an ISO date is WARN and the
SessionStart line says so. Every judgment is one line in `<state>/kernelentry.log`.
`kernel_entry_door: false` in `limits` turns the door off.
"""
from __future__ import annotations
import datetime
import fnmatch
import re
from pathlib import Path

import authority
import common
import lazy_body
import limits

BOOT_NAME = "Kernel.md"
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_WIKILINK = re.compile(r"\[\[[^\]]*\]\]")
_SPAN = re.compile(r"(`+)(.+?)\1")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_COMMANDS = ("git|pytest|python|python3|\\.venv/bin/python|claude|bash|sh|zsh|rm|trash|/usr/bin/trash|sed|awk|"
             "grep|find|curl|ps|pgrep|ls|cat|mv|cp|npm|npx|node|ffmpeg|open|osascript|xxd|tail|head|gh|"
             "wrangler|sqlite3|jq|mktemp|timeout|gtimeout")
_TOOLS = ("Agent|Bash|Edit|Write|Read|MultiEdit|NotebookEdit|SendMessage|Artifact|WebFetch|WebSearch|"
          "Monitor|ToolSearch|Skill")
# (name, applies-to, pattern). "line" patterns search the whole line (wikilinks dropped); "span"
# patterns search each code span's CONTENT, so a backtick pair is never matched across prose.
SIGNATURES = (
    ("command", "span", re.compile(rf"^\s*(?:sudo\s+)?(?:{_COMMANDS})(?:\s|$)")),
    ("claude -p", "line", re.compile(r"\bclaude -p\b")),
    ("html", "line", re.compile(r"\.html\b")),
    ("path glob", "span", re.compile(r"\*\*|\*\.[A-Za-z0-9]+")),
    ("script path", "span", re.compile(r"\.(?:py|sh|js|mjs|ts)\b")),
    ("tool name", "span", re.compile(rf"^(?:{_TOOLS})$|^mcp__\w+")),
    ("tool name", "line", re.compile(r"\bmcp__\w+")),
    ("shell syntax", "span", re.compile(r"\s--?[A-Za-z][\w-]*|&&|\|\s|\$\(")),
)


def enabled() -> bool:
    return bool(limits.get("kernel_entry_door", True))


def deny_from() -> str:
    v = limits.get("kernel_entry_deny_from", "")
    return v.strip() if isinstance(v, str) else ""


def mode() -> str:
    """`deny` from the configured date on; `warn` before it, without it, or with a malformed one."""
    d = deny_from()
    if not _ISO.match(d):
        return "warn"
    try:
        datetime.date.fromisoformat(d)
    except ValueError:
        return "warn"
    return "deny" if common.today() >= d else "warn"


def facts_line() -> str:
    d, m = deny_from(), mode()
    if m == "deny":
        head = f"DENY since {d} (an added `Kernel.md` bullet with a tool/path/command signature is refused)"
    elif not d:
        head = "WARN, no DENY date set (`limits.kernel_entry_deny_from` in config.json is empty)"
    elif not _ISO.match(d):
        head = f"WARN — `limits.kernel_entry_deny_from` is {d!r}, not a YYYY-MM-DD date"
    else:
        head = f"WARN until {d}, then DENY"
    return (f"- Kernel-entry door: {head}. A rule that binds only with one tool goes in a facet "
            "(`<Region>/Kernel-<name>.md`) or a door.")


def signatures(line: str) -> list:
    """The signature names an added bullet carries, in SIGNATURES order, each once."""
    text = _WIKILINK.sub("", line)
    spans = [m.group(2) for m in _SPAN.finditer(text)]
    out = []
    for name, where, rx in SIGNATURES:
        if name in out:
            continue
        if (rx.search(text) if where == "line" else any(rx.search(s) for s in spans)):
            out.append(name)
    return out


def added_bullets(before: str, after: str) -> list:
    """Bullet lines `after` holds more times than `before` — a removal or a move adds nothing."""
    have: dict = {}
    for ln in before.splitlines():
        k = ln.strip()
        have[k] = have.get(k, 0) + 1
    out = []
    for ln in after.splitlines():
        if not _BULLET.match(ln):
            continue
        k = ln.strip()
        if have.get(k, 0) > 0:
            have[k] -= 1
            continue
        out.append(ln)
    return out


def _facet_for(region: str, line: str) -> str | None:
    """The first facet of `region` whose own trigger matches this line, or None.

    The facet file's frontmatter decides (`lazy_body.facets_of`); this door ships no knowledge of
    any vault's surfaces. A `bash` regex is searched in the line; `paths` and `tools` globs are
    matched against the line's code spans."""
    text = _WIKILINK.sub("", line)
    spans = [m.group(2).strip() for m in _SPAN.finditer(text)]
    try:
        facets = lazy_body.facets_of(region)
    except Exception:
        return None
    for f in facets:
        if any(rx.search(text) for rx in f.get("bash", [])):
            return f["name"]
        for g in f.get("paths", []) + f.get("tools", []):
            if any(fnmatch.fnmatch(s, g) for s in spans):
                return f["name"]
    return None


def check(p: Path, rel: str, tool: str, ti: dict) -> str | None:
    """The door's words for this write, or None when it has nothing to say."""
    if p.name != BOOT_NAME or not enabled():
        return None
    try:
        before = p.read_text(encoding="utf-8") if p.is_file() else ""
    except (OSError, UnicodeDecodeError):             # a ValueError, not an OSError (review, 2026-09-26)
        return None
    after = authority.post_write_text(before, tool, ti)
    if after is None:
        return None
    region = rel[: -len(BOOT_NAME)].rstrip("/")
    hits = []
    for ln in added_bullets(before, after):
        sig = signatures(ln)
        if sig:
            hits.append((ln.strip(), sig, _facet_for(region, ln)))
    if not hits:
        return None
    body = []
    for ln, sig, facet in hits[:5]:
        where = (f"its trigger matches the facet `{region}/Kernel-{facet}.md` — put it there" if facet
                 else f"no facet of `{region}` has a trigger for it — start one (`{region}/Kernel-<name>.md` "
                      "with a `bash:` / `paths:` / `tools:` trigger) or make it a door")
        body.append(f"  - [{', '.join(sig)}] {ln[:140]}{'…' if len(ln) > 140 else ''}\n    → {where}")
    more = f"\n  (+{len(hits) - 5} more)" if len(hits) > 5 else ""
    return (f"KERNEL-ENTRY: this write adds {len(hits)} bullet(s) with a tool signature to `{rel}`, the "
            "Boot file every session loads. A rule that binds only when one tool is in use belongs in "
            "a facet, loaded when that tool is used, or in a door:\n" + "\n".join(body) + more +
            "\nA rule that binds before the session knows its task stays here — write it without the "
            "tool's text (the facet carries the specifics).")
