#!/usr/bin/env python3
"""ruledoors.py — six rules that were prose in a Boot file and a script can decide (DOORS-2).

The kernel-budget census (2026-09-24) sorted every bullet of two Boot files into ENFORCED (a door
already decides it), DECIDABLE (a script could, nothing does), JUDGMENT and pointer. Eleven were
DECIDABLE. Four of those were already decided by `maintenance.py` (the cleanup and synthesis
triggers); the other seven are the six doors below (`html_head` carries two), one module so they
share one mode rule, one log and one scope rule.

    door            reads                                    rule it enforces
    secret          Edit/Write: the lines the write ADDS     a secret belongs in `.env`, never in a file
    html_head       Edit/Write of a `.html` page             `<!doctype html>` + `<meta charset="utf-8">`
                                                             as the first two lines, the meta inside the
                                                             first 1024 bytes
    launch_pin      Bash: a `claude` launch                  every launch pins `--model` AND `--effort`
    judge_md        Write of a `.md` that asks for a verdict a page someone judges is HTML + verdict JSON
    row_identity    Edit/Write: queue-row lines it ADDS      the backticked id after the checkbox and the
                                                             `| q:` field name the same row
    marker_roster   Edit/Write of `.atlas-lane` or the       a lane's marker `path:` lines equal its
                    roster                                   roster row (the two-places rule)

WARN, THEN DENY — the flip is a DATE, per door, in the `limits` object of config.json:
`<door>_deny_from: "YYYY-MM-DD"`. Before that day, or with the key empty or malformed, the tool
call runs and the model is told what the door would have said. `<door>_door: false` turns one off.
`marker_roster` is WARN-ONLY by design and has no DENY date: a two-places change is two writes,
and refusing the first would make the rule impossible to follow.

SCOPE (PLUGDIR-1): a door refuses only inside the vault or a repo carrying an `.atlas-lane` marker
(`common.in_scope`); elsewhere its text arrives as a note. Every judgment is one line in
`<state>/ruledoors.log`: `<mode>\\t<door>\\t<path or command>`.

Every refusal names the rule and the id it came from — the owner ruling where the rule cites one,
otherwise the queue row that made it a door.
"""
from __future__ import annotations
import datetime
import re
from pathlib import Path

import authority
import citations
import common
import config
import limits

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

RULES = {
    "secret": ("SECRET", "a secret (an API key, a token, a private key, a card number) is never hardcoded in a file — "
               "it lives in `.env` and is read from the environment", "q:CU-2026-09-24-DOORS-2a"),
    "html_head": ("HTML-HEAD", "an HTML page opened over `file://` declares `<!doctype html>` and "
                  "`<meta charset=\"utf-8\">` as its first two lines, the meta inside the first 1024 bytes, or the "
                  "browser guesses the encoding", "q:CU-2026-09-24-DOORS-2a"),
    "launch_pin": ("LAUNCH-PIN", "every `claude` launch, scripted or by hand, pins `--model` and `--effort` "
                   "from its own routing source; a settings default is never a routing decision",
                   "owner-ruling-mb1-routing-2026-08-05"),
    "judge_md": ("JUDGE-MD", "anything a person judges is an interactive HTML page with a verdict-JSON export, "
                 "never a bare Markdown file", "q:CU-2026-09-24-DOORS-2b"),
    "row_identity": ("ROW-IDENTITY", "a queue row is read two ways — the backticked `q:` id right after the "
                     "checkbox and the `| q:` field — and the two must name the same row, once",
                     "q:CU-2026-09-24-DOORS-2b"),
    "marker_roster": ("MARKER-ROSTER", "a lane's `.atlas-lane` `path:` lines equal that lane's row in the fleet "
                      "roster (the two-places rule)", "q:CU-2026-09-24-DOORS-2b"),
}
WARN_ONLY = {"marker_roster"}
WRITE_DOORS = ("secret", "html_head", "judge_md", "row_identity", "marker_roster")


# ---- mode ----------------------------------------------------------------------------------------

def enabled(door: str) -> bool:
    return bool(limits.get(f"{door}_door", True))


def deny_from(door: str) -> str:
    v = limits.get(f"{door}_deny_from", "")
    return v.strip() if isinstance(v, str) else ""


def mode(door: str) -> str:
    """`deny` from the door's configured date on; `warn` before it, without it, or with a bad one."""
    if door in WARN_ONLY:
        return "warn"
    d = deny_from(door)
    if not _ISO.match(d):
        return "warn"
    try:
        datetime.date.fromisoformat(d)
    except ValueError:
        return "warn"
    return "deny" if common.today() >= d else "warn"


def words(door: str, finding: str) -> str:
    title, rule, rid = RULES[door]
    return f"{title}: {finding}\nRule: {rule} ({rid})."


def warn_prefix(door: str) -> str:
    if door in WARN_ONLY:
        return "NOTE (this door only ever warns) — "
    return (f"WARN — this door has no DENY date yet (`limits.{door}_deny_from`), or it is still ahead, so the "
            "call runs; from that date on it is refused. ")


def facts_line() -> str:
    """One SessionStart line: which of these doors refuse and which only warn."""
    deny = [d for d in RULES if enabled(d) and mode(d) == "deny"]
    warn = [d for d in RULES if enabled(d) and mode(d) == "warn"]
    off = [d for d in RULES if not enabled(d)]
    parts = []
    if deny:
        parts.append("DENY " + ", ".join(deny))
    if warn:
        parts.append("WARN " + ", ".join(warn))
    if off:
        parts.append("off " + ", ".join(off))
    return "- Rule doors (DOORS-2): " + " · ".join(parts) + "."


# ---- shared text helpers -------------------------------------------------------------------------

def added_lines(before: str, after: str) -> list[tuple[int, str]]:
    """(1-based line number in `after`, line) for each line `after` holds more times than `before`."""
    have: dict = {}
    for ln in before.splitlines():
        have[ln] = have.get(ln, 0) + 1
    out = []
    for i, ln in enumerate(after.splitlines(), 1):
        if have.get(ln, 0) > 0:
            have[ln] -= 1
            continue
        out.append((i, ln))
    return out


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8") if p.is_file() else ""
    except (OSError, UnicodeDecodeError, ValueError):
        return None


# ---- secret --------------------------------------------------------------------------------------

_SECRETS = (
    ("an Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("an OpenAI-style API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("an AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("a private key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("a secret assigned in code", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b[\"']?\s*[:=]\s*[\"']"
        r"(?=[A-Za-z0-9_\-+/=]*\d)(?=[A-Za-z0-9_\-+/=]*[A-Za-z])[A-Za-z0-9_\-+/=]{24,}[\"']")),
)
_CARD = re.compile(r"\b\d{4}([ -])\d{4}\1\d{4}\1\d{4}\b")
_PLACEHOLDER = re.compile(r"(?i:example|fake|dummy|sentinel|placeholder|probe|do-not-leak|redacted)|"
                          r"x{6,}|X{6,}|\*{4,}|<[^>]*>|\.\.\.|…|0{12,}")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def secret_hits(line: str) -> list[str]:
    """What kind of secret this line carries, or []; a placeholder is never a secret."""
    out = []
    for what, rx in _SECRETS:
        for m in rx.finditer(line):
            if not _PLACEHOLDER.search(m.group(0)):
                out.append(what)
                break
    for m in _CARD.finditer(line):
        if _luhn(re.sub(r"\D", "", m.group(0))):
            out.append("a payment-card number")
            break
    return out


def _is_env_file(p: Path) -> bool:
    n = p.name
    return n == ".env" or n.startswith(".env.") or n.endswith(".env")


def check_secret(p: Path, before: str, after: str) -> str | None:
    if _is_env_file(p):
        return None
    hits = []
    for i, ln in added_lines(before, after):
        kinds = secret_hits(ln)
        if kinds:
            hits.append(f"  - line {i}: {', '.join(kinds)}")
    if not hits:
        return None
    return words("secret", f"this write puts what looks like a secret into `{p.name}` "
                           "(the value is not repeated here):\n" + "\n".join(hits[:5]) +
                 "\nMove it to `.env` (git-ignored) and read it from the environment; a placeholder such as "
                 "`EXAMPLE` or `xxxxxx` is never flagged.")


# ---- html_head -----------------------------------------------------------------------------------

_DOCTYPE = re.compile(r"^\s*<!doctype html\s*>", re.I)
_META = re.compile(r"<meta\s+charset\s*=\s*[\"']?utf-8[\"']?\s*/?>", re.I)
_WHOLE_PAGE = re.compile(r"<html[\s>]|<head[\s>]|<body[\s>]", re.I)


def _skip_path(p: Path) -> bool:
    """Test and fixture files are data; a page published as an Artifact (named `*-artifact.html`, or
    drafted in the session's own scratchpad) is served with its encoding — none of them is judged."""
    parts = {x.lower() for x in p.parts[:-1]}
    if parts & {"tests", "test", "fixtures", "fixture"}:
        return True
    if p.stem.lower().endswith("-artifact") or ".artifact" in p.name.lower():
        return True                       # the house name for a page published as an Artifact
    base = common.session_tmp_base()
    try:
        return base is not None and common.under(p, base)
    except (OSError, ValueError):
        return False


def check_html_head(p: Path, before: str, after: str) -> str | None:
    if p.suffix.lower() not in (".html", ".htm") or _skip_path(p):
        return None
    if not _WHOLE_PAGE.search(after):
        return None                       # a fragment or a template partial has no head of its own
    lines = after.lstrip("﻿").splitlines()
    first = lines[0] if lines else ""
    second = lines[1] if len(lines) > 1 else ""
    miss = []
    if not _DOCTYPE.match(first):
        miss.append("line 1 is not `<!doctype html>`")
    if not _META.search(second):
        miss.append("line 2 is not `<meta charset=\"utf-8\">`")
    m = _META.search(after)
    if m is None:
        miss.append("the page has no `<meta charset=\"utf-8\">` at all")
    elif len(after[: m.end()].encode("utf-8")) > 1024:
        miss.append(f"the meta ends at byte {len(after[: m.end()].encode('utf-8'))}, past the first 1024 "
                    "(browsers only look there)")
    if not miss:
        return None
    return words("html_head", f"`{p.name}`: " + "; ".join(miss) + ".")


# ---- judge_md ------------------------------------------------------------------------------------

_JUDGE_WORD = re.compile(r"\b(?:verdicts?|judge|judging|pick one|your call|approve or reject|ear[- ]check)\b", re.I)
_QROW = re.compile(r"q:[A-Z]{2}-\d{4}-\d{2}-\d{2}-")
_BALLOT = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*(?:\[ \]|☐)\s+\S|\((?:Y/N|y/n|Y / N)\)\s*:?\s*$|\bY\s*/\s*N\s*:?\s*_+")


def ballot_lines(text: str) -> int:
    """Lines that ask the reader to mark a choice: an unticked box that is not a queue row, or a
    trailing `(Y/N)` / `Y/N: ___` blank."""
    return sum(1 for ln in text.splitlines() if _BALLOT.search(ln) and not _QROW.search(ln))


def check_judge_md(p: Path, tool: str, after: str) -> str | None:
    if p.suffix.lower() != ".md" or tool != "Write" or _skip_path(p):
        return None
    heads = "\n".join(ln for ln in after.splitlines() if ln.lstrip().startswith("#"))
    if not (_JUDGE_WORD.search(p.stem.replace("-", " ").replace("_", " ")) or _JUDGE_WORD.search(heads)):
        return None
    n = ballot_lines(after)
    if n < 2:
        return None
    return words("judge_md", f"`{p.name}` asks its reader to judge ({n} unmarked choices under a verdict/judge "
                             "heading or name) in plain Markdown. Build it as an HTML page with the choices as "
                             "controls, a note field per item and a verdict-JSON export; this Markdown file can "
                             "stay as the page's source or its report.")


# ---- row_identity --------------------------------------------------------------------------------

_ROW = re.compile(r"^\s*-\s*\[[ xX]\]\s")


def row_problem(line: str) -> str | None:
    """Why this checkbox line's two readings disagree, or None."""
    if not _ROW.match(line):
        return None
    a = citations.ANCHORED.match(line)
    aid = a.group(1) if a else None
    fids = sorted(set(citations.FIELD_QID.findall(line)))
    if len(fids) > 1:
        return f"it carries {len(fids)} different `| q:` fields ({', '.join('q:' + f for f in fids)})"
    if aid and fids and aid != fids[0]:
        return f"the anchor says `q:{aid}` and the field says `q:{fids[0]}`"
    return None


def check_row_identity(p: Path, before: str, after: str) -> str | None:
    if p.suffix.lower() != ".md" or _skip_path(p):
        return None
    bad = []
    for i, ln in added_lines(before, after):
        why = row_problem(ln)
        if why:
            bad.append(f"  - line {i}: {why} — {ln.strip()[:100]}")
    if not bad:
        return None
    return words("row_identity", f"this write adds {len(bad)} queue row(s) to `{p.name}` whose id is ambiguous:\n"
                 + "\n".join(bad[:5]) + "\nA sweep that reads one of the two would flip or skip the wrong row. "
                 "Make the anchor and the field the same id.")


# ---- marker_roster -------------------------------------------------------------------------------

_LANE = re.compile(r"^\s*lane:(.*)$")
_PATH = re.compile(r"^\s*path:(.*)$")
_REPO = re.compile(r"^\s*repo:(.*)$")
ROSTER_FENCE = "fleet-roster"


def parse_marker_text(text: str) -> tuple[str | None, list[str]]:
    """`lane:` (first, all whitespace removed) and every `path:` — the Stop hook's own reading."""
    lane, paths = None, []
    for ln in text.splitlines():
        m = _LANE.match(ln)
        if m and lane is None:
            lane = re.sub(r"\s+", "", m.group(1)) or None
            continue
        m = _PATH.match(ln)
        if m and m.group(1).strip():
            paths.append(m.group(1).strip())
    return lane, paths


def parse_roster_text(text: str) -> dict:
    """{lane: {"repos": [...], "paths": [...]}} from the fenced ```fleet-roster block only."""
    lanes: dict = {}
    inside, current = False, None
    for ln in text.splitlines():
        s = ln.strip()
        if not inside:
            if s.startswith("```") and s[3:].strip() == ROSTER_FENCE:
                inside = True
            continue
        if s.startswith("```"):
            break
        m = _LANE.match(ln)
        if m:
            current = lanes.setdefault(re.sub(r"\s+", "", m.group(1)), {"repos": [], "paths": []})
            continue
        if current is None:
            continue
        m = _REPO.match(ln)
        if m and m.group(1).strip():
            current["repos"].append(m.group(1).strip())
            continue
        m = _PATH.match(ln)
        if m and m.group(1).strip():
            current["paths"].append(m.group(1).strip())
    return lanes


def _diff(lane: str, marker_paths: list[str], roster_paths: list[str], where: str) -> str | None:
    want, have = set(roster_paths), set(marker_paths)
    if want == have:
        return None
    parts = []
    if have - want:
        parts.append("only in the marker: " + ", ".join(f"`{x}`" for x in sorted(have - want)))
    if want - have:
        parts.append("only in the roster: " + ", ".join(f"`{x}`" for x in sorted(want - have)))
    return f"  - lane {lane} ({where}): " + "; ".join(parts)


def check_marker_roster(p: Path, before: str, after: str) -> str | None:
    try:
        roster = config.roster()
    except Exception:                                  # noqa: BLE001 — no roster configured: no rule
        return None
    is_roster = False
    try:
        is_roster = p.resolve() == roster.resolve()
    except OSError:
        pass
    found = []
    if p.name == ".atlas-lane":
        lane, paths = parse_marker_text(after)
        rtext = _read(roster)
        if not lane or not rtext:
            return None
        row = parse_roster_text(rtext).get(lane)
        if row is None:
            return None
        d = _diff(lane, paths, row["paths"], "this marker against the roster")
        if d:
            found.append(d)
        tail = f"Make the matching edit in the roster ({roster.name}) in this session."
    elif is_roster:
        old, new = parse_roster_text(before), parse_roster_text(after)
        for lane, row in new.items():
            if old.get(lane) == row:
                continue                                # only rows this write changes are judged
            for repo in row["repos"]:
                mtext = _read(config.home() / repo / ".atlas-lane")
                if not mtext:
                    continue
                mlane, mpaths = parse_marker_text(mtext)
                if mlane != lane:
                    continue
                d = _diff(lane, mpaths, row["paths"], f"the roster against `{repo}/.atlas-lane`")
                if d:
                    found.append(d)
        tail = "Make the matching edit in each marker named above in this session."
    else:
        return None
    if not found:
        return None
    return words("marker_roster", "after this write the marker and the roster disagree:\n" + "\n".join(found[:5]) +
                 "\n" + tail)


# ---- entry points --------------------------------------------------------------------------------

def check_write(p: Path, tool: str, ti: dict) -> list[tuple[str, str]]:
    """[(door, words)] for an Edit / MultiEdit / Write, in WRITE_DOORS order; [] when nothing fires or
    the text after the write cannot be known honestly."""
    live = [d for d in WRITE_DOORS if enabled(d)]
    if not live:
        return []
    before = _read(p)
    if before is None:
        return []
    after = authority.post_write_text(before, tool, ti)
    if after is None:
        return []
    out = []
    for d in live:
        if d == "secret":
            w = check_secret(p, before, after)
        elif d == "html_head":
            w = check_html_head(p, before, after)
        elif d == "judge_md":
            w = check_judge_md(p, tool, after)
        elif d == "row_identity":
            w = check_row_identity(p, before, after)
        else:
            w = check_marker_roster(p, before, after)
        if w:
            out.append((d, w))
    return out


_CLAUDE_SUBCMDS = {"plugin", "mcp", "config", "doctor", "update", "login", "logout", "setup-token", "agents",
                   "install", "migrate-installer", "--version", "-v", "--help", "-h", "auth", "upgrade"}


def _has(w: list[str], flag: str) -> bool:
    return flag in w or any(x.startswith(flag + "=") for x in w)


def check_launch(cmd: str, segments) -> str | None:
    """The launch-pin door's words for a Bash command, or None. `segments` is gate.py's splitter."""
    if not enabled("launch_pin"):
        return None
    for seg in segments(cmd):
        w = seg.split()
        while w and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]):
            w = w[1:]
        if w and Path(w[0]).name == "env":             # `env [-i] [-u NAME] [VAR=val]… claude …`
            w = w[1:]
            while w and (w[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0])):
                w = w[2:] if w[0] in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string") else w[1:]
        if not w or Path(w[0]).name not in ("claude", "claude.exe"):
            continue
        if len(w) > 1 and w[1] in _CLAUDE_SUBCMDS:
            continue
        miss = [f for f in ("--model", "--effort") if not _has(w, f)]
        if miss:
            return words("launch_pin", "this `claude` launch does not pin " + " or ".join(f"`{m}`" for m in miss) +
                         " — without them it runs on whatever the settings file names, at the platform's default "
                         "effort. Add `--model <id> --effort <low|medium|high>`.")
    return None
