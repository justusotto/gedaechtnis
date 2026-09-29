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
ONLY A NEW SIGNATURE FIRES (KERNELENTRY-2, 2026-09-29). An added bullet is judged by the signatures
it carries that the file's bullets BEFORE the write do not already carry — the whole before-text,
so a bullet this write removes counts, and a signature moved from one bullet to another is not new.
A signature's identity is its TEXT: the code span that holds the match (`scripts/lint.py`), or, for
a line match outside any span, the bare word around it (`review/x.html`, `claude -p`; a Markdown
link counts by its target, not its text); a bare word
and a span with the same text are different entries, so dropping the backticks reads as new. Only
RULE BULLETS count as before (not a bullet-shaped line inside a fenced block or the frontmatter):
a command that stood in a fenced block or in prose and is now put into a
bullet IS new, because a bullet is a rule and that promotion is the entry this door exists to
stop. An added line INSIDE a fence or the frontmatter is judged against every bullet-shaped
line of the file, so an edited example that keeps its command passes. A fence indented four spaces
or more still counts as a fence (fences nested in list items need that).
Rewording a bullet that already named `kernel_budget_guard.py` therefore passes; adding a second,
new script to it does not. Measured before the change: 97 of 714 bullets in nine kernels
already carried a signature, and every edit of any of them read as an addition.
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


def _bare(text: str, start: int, end: int) -> str:
    """The whitespace-bounded word(s) around a match outside every span; for a Markdown link only
    its target, so rewording the link text changes nothing; a trailing possessive is dropped."""
    a, b = start, end
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    w = text[a:b]
    if "](" in w:
        w = w[w.index("](") + 2:]
    w = w.strip(".,;:!?()[]*_\"'\u2018\u2019")
    return w[:-2] if w.endswith(("'s", "\u2019s")) else w


def signature_tokens(line: str) -> list:
    """(name, identity) for every signature-bearing match in the line — what a before/after
    comparison uses. A span match is `span:` plus the span's trimmed text (the pattern is matched on
    the UNTRIMMED text, exactly as `signatures` does, so both see the same signatures); a line match
    inside a span is that span; a line match outside every span is `bare:` plus the word(s) around it."""
    text = _WIKILINK.sub("", line)
    spans = [(m.start(), m.end(), m.group(2), "span:" + m.group(2).strip()) for m in _SPAN.finditer(text)]
    out = []
    for name, where, rx in SIGNATURES:
        if where == "span":
            out += [(name, ident) for _, _, raw, ident in spans if rx.search(raw)]
            continue
        for m in rx.finditer(text):
            inside = next((ident for a, b, _, ident in spans if a <= m.start() < b), None)
            out.append((name, inside if inside is not None else "bare:" + _bare(text, m.start(), m.end())))
    return list(dict.fromkeys(out))


# A fence opens on three or more backticks (with no backtick later on the line: ```x``` in prose is
# an inline span) or tildes, and closes on a run of the same character at least as long, alone.
_FENCE_OPEN = re.compile(r"^\s*(`{3,}(?!.*`)|~{3,})")
_FENCE_CLOSE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")


def bullet_lines(text: str) -> list:
    """(is_rule, line) for every bullet-shaped line of `text`. A rule is a bullet outside a leading
    `---` frontmatter block and outside fenced code blocks; the others are examples (they are still
    loaded with the file, so they are still judged, but they never make a command known to a rule)."""
    lines = text.splitlines()
    front = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                front = j + 1
                break
    out, fence = [], None
    for i, ln in enumerate(lines):
        if i < front:
            if _BULLET.match(ln):
                out.append((False, ln))
            continue
        if fence is None:
            m = _FENCE_OPEN.match(ln)
            if m:
                fence = m.group(1)
                continue
        else:
            m = _FENCE_CLOSE.match(ln)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
                continue
        if _BULLET.match(ln):
            out.append((fence is None, ln))
    return out


def rule_bullets(text: str) -> list:
    """The bullet lines a reader takes as rules (see `bullet_lines`)."""
    return [ln for rule, ln in bullet_lines(text) if rule]


def known_tokens(before: str, rules_only: bool = True) -> set:
    """The signature identities the before-text's bullets already carry — its RULE bullets only by
    default; with `rules_only=False` its fenced and frontmatter bullets as well."""
    return {tok for rule, ln in bullet_lines(before) if rule or not rules_only for _, tok in signature_tokens(ln)}


def added_bullet_lines(before: str, after: str) -> list:
    """(is_rule, line) for the bullet lines `after` holds more times than `before`, counted per
    kind: a rule line is added unless a rule line of `before` matches it, so a line moved word for
    word out of a fence into the rules is ADDED; a removal or a move within one kind adds nothing."""
    have: dict = {}
    for rule, ln in bullet_lines(before):
        k = (rule, ln.strip())
        have[k] = have.get(k, 0) + 1
    out = []
    for rule, ln in bullet_lines(after):
        k = (rule, ln.strip())
        if have.get(k, 0) > 0:
            have[k] -= 1
            continue
        out.append((rule, ln))
    return out


def added_bullets(before: str, after: str) -> list:
    """The added bullet lines, both kinds (see `added_bullet_lines`)."""
    return [ln for _, ln in added_bullet_lines(before, after)]


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
    # A rule is judged against the rule bullets; an example line (fence, frontmatter) against every
    # bullet-shaped line, so editing a fenced example that keeps its command passes.
    known = {True: known_tokens(before), False: known_tokens(before, rules_only=False)}
    hits = []
    for rule, ln in added_bullet_lines(before, after):
        new = [(n, t) for n, t in signature_tokens(ln) if t not in known[rule]]
        if new:
            hits.append((ln.strip(), new, _facet_for(region, ln)))
    if not hits:
        return None
    body = []
    for ln, new, facet in hits[:5]:
        sig = list(dict.fromkeys(n for n, _ in new))
        texts = list(dict.fromkeys(t.split(":", 1)[1] for _, t in new))
        shown = ", ".join(f"`{t}`" for t in texts[:3]) + (", …" if len(texts) > 3 else "")
        where = (f"its trigger matches the facet `{region}/Kernel-{facet}.md` — put it there" if facet
                 else f"no facet of `{region}` has a trigger for it — start one (`{region}/Kernel-<name>.md` "
                      "with a `bash:` / `paths:` / `tools:` trigger) or make it a door")
        body.append(f"  - [{', '.join(sig)}] new to this file's bullets: {shown}\n    {ln[:140]}"
                    f"{'…' if len(ln) > 140 else ''}\n    → {where}")
    more = f"\n  (+{len(hits) - 5} more)" if len(hits) > 5 else ""
    return (f"KERNEL-ENTRY: this write adds {len(hits)} bullet(s) with a tool signature new to `{rel}`, the "
            "Boot file every session loads. A rule that binds only when one tool is in use belongs in "
            "a facet, loaded when that tool is used, or in a door:\n" + "\n".join(body) + more +
            "\nA rule that binds before the session knows its task stays here — write it without the "
            "tool's text (the facet carries the specifics).")
