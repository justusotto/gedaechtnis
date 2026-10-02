#!/usr/bin/env python3
"""ruledoors.py — rules that were prose in a Boot file and a script can decide (DOORS-2).

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
    kill            Bash: `kill` / `pkill` / `killall` /     a kill's target is a pid someone typed —
                    `pgrep` (hooks/killdoor.py, KILLDOOR-1)  never computed, never `-P`/`-f`, never 0/1/-n
    marker_roster   Edit/Write of `.atlas-lane` or the       a lane's marker `path:` lines equal its
                    roster                                   roster row (the two-places rule)

WARN, THEN DENY — the flip is a DATE, per door, in the `limits` object of config.json:
`<door>_deny_from: "YYYY-MM-DD"`. Before that day, or with the key empty or malformed, the tool
call runs and the model is told what the door would have said. `<door>_door: false` turns one off.
`judge_md`, `row_identity` and `marker_roster` ship OFF (PUBLICDOORS-1): they suit one working style,
and `"profile": "atlas"` in config.json, or `<door>_door: true`, turns them on.
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
    "kill": ("KILL", "a process is ended by the pid captured when it was launched, typed as literal digits — "
             "never by a target the shell computes (a substitution, backticks, a variable, `xargs`), never "
             "`pkill -P`, `pkill -f` or `pgrep -f`, never pid 0, pid 1 or a negative pid",
             "q:CU-2026-09-28-KILLDOOR-1"),
    "marker_roster": ("MARKER-ROSTER", "a lane's `.atlas-lane` `path:` lines equal that lane's row in the fleet "
                      "roster (the two-places rule)", "q:CU-2026-09-24-DOORS-2b"),
}
WARN_ONLY = {"marker_roster"}
WRITE_DOORS = ("secret", "html_head", "judge_md", "row_identity", "marker_roster")


# ---- mode ----------------------------------------------------------------------------------------

def enabled(door: str) -> bool:
    return bool(limits.get(f"{door}_door", f"{door}_door" not in limits.PROFILE_DOORS))


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
    art = "on" if limits.get("artifact_open_door", False) else "off"
    return ("- Rule doors (DOORS-2): " + " · ".join(parts) + f". Profile `{limits.profile()}`; "
            f"artifact-open door {art}.")


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


# Subcommands and flags that start no session (LAUNCHPINFIX-1). `remote-control` hosts sessions whose
# model the Project sets; it has no `--model` flag, so a pin cannot be asked of it.
_CLAUDE_SUBCMDS = {"plugin", "plugins", "mcp", "config", "doctor", "update", "login", "logout", "setup-token",
                   "agents", "install", "migrate-installer", "auth", "upgrade", "remote-control"}
_INFO_FLAGS = {"--version", "-v", "--help", "-h"}
_TIMEOUTS = ("timeout", "gtimeout")


_OPS = {"&", "&&", ";", ";;", ";&", ";;&", "|", "||", "|&", "(", ")"}
_REDIRECT = re.compile(r"^(?:\d*|&)?(?:>>?|<<?<?-?|>&|<&|&>>?|>\||<>)$")
# shlex hands a run of punctuation over as ONE token (`)&`, `()`, `;&`): it is cut into the shell's
# own operators, longest first, so `(claude -p "task")& git --version` still cuts at the `&`
_PUNCT = "();<>|&"
_PUNCT_OPS = ("&>>", "<<<", ";;&", ">>", "<<", ">&", "<&", "&>", ">|", "<>", "&&", "||", ";;", ";&", "|&",
              "&", ";", "|", "(", ")", "<", ">")
_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}", "coproc"}
_CLAUDE_BARE_FLAGS = {"--verbose", "--debug"}      # take no value, so the word after them is the subcommand
# take ONE value, so the word after that is the subcommand: `claude --settings s.json mcp list`
_CLAUDE_VALUE_FLAGS = {"--settings", "--setting-sources", "--mcp-config", "--plugin-dir", "--model", "--effort"}
_MAX_DEPTH = 8                                      # `$( )` inside `$( )`: deeper than this is not read


def _drop_comments(line: str) -> str:
    """`line` with its comments removed as bash removes them: a `#` starts one only at the START of a
    word (after a blank or an operator), never inside one (`https://x.test/#a`, `$#`), never quoted."""
    out, i, n, q = [], 0, len(line), None
    while i < n:
        c = line[i]
        if q == "'":
            out.append(c)
            if c == "'":
                q = None
            i += 1; continue
        if c == "\\" and i + 1 < n:
            out.append(line[i:i + 2]); i += 2; continue
        if q == '"':
            out.append(c)
            if c == '"':
                q = None
            i += 1; continue
        if c in ("'", '"'):
            q = c; out.append(c); i += 1; continue
        if c == "#" and (i == 0 or line[i - 1] in " \t\n" + _PUNCT):
            j = line.find("\n", i)
            i = n if j == -1 else j; continue       # the newline itself stays
        out.append(c); i += 1
    return "".join(out)


_SUBST_WORD = "__SUBST__"                           # what a `$( )`, `<( )`, `>( )` or backtick span reads as


def _placehold(line: str) -> str:
    """`line` with every `$( )`, `$(( ))`, `<( )`, `>( )` and backtick span replaced by ONE word,
    `_SUBST_WORD`, so its parentheses cut no command: `claude --resume $(cat .sid) --model x` keeps
    its `--model`. Nested and quote-aware; a single-quoted stretch is literal. Each span's contents
    are read separately (`claude_argvs` reads `killdoor.substitutions`). An unclosed span runs to
    the end. Iterative, so a 1000-deep nesting costs no recursion."""
    out, i, n, q = [], 0, len(line), None
    while i < n:
        c = line[i]
        if q == "'":
            out.append(c)
            if c == "'":
                q = None
            i += 1; continue
        if c == "\\" and i + 1 < n:
            out.append(line[i:i + 2]); i += 2; continue
        if c == "'" and q is None:
            q = "'"; out.append(c); i += 1; continue
        if c == '"':
            q = None if q == '"' else '"'
            out.append(c); i += 1; continue
        if line.startswith(("$(", "<(", ">("), i):
            depth, j, qq = 1, i + 2, None
            while j < n and depth:
                d = line[j]
                if qq == "'":
                    if d == "'":
                        qq = None
                elif d == "\\":
                    j += 1
                elif qq == '"':
                    if d == '"':
                        qq = None
                elif d in ("'", '"'):
                    qq = d
                elif d == "(":
                    depth += 1
                elif d == ")":
                    depth -= 1
                j += 1
            out.append(_SUBST_WORD); i = j; continue
        if c == "`":
            j = i + 1
            while j < n and line[j] != "`":
                j += 2 if line[j] == "\\" else 1
            out.append(_SUBST_WORD); i = j + 1; continue
        out.append(c); i += 1
    return "".join(out)


_HEREDOC_END = re.compile(r"""(?<!<)<<-?\s*(?:'(\w+)'|"(\w+)"|\\?(\w+))""")


def _drop_heredoc_bodies(seg: str) -> str:
    """`seg` with every heredoc body cut out, quoted or not, the lines around it kept: `claude -p
    "$(cat <<'EOF'` ⏎ body ⏎ `EOF` ⏎ `)" --model x --effort high` reads as `claude -p "$(cat <<'EOF'`
    ⏎ `)" --model x --effort high`, so the pins after `)"` are read. A body is not claude's words;
    a `$( )` in an unquoted body is read on its own (`killdoor.substitutions`). Only a `<<` the shell
    reads counts: one inside quotes (`claude -p "std::cout << x` ⏎ `…"`) or `$(( ))` is text, while
    `$(`, `<(`, `>(` and a backtick start a fresh unquoted context, even inside double quotes, as in
    `_placehold`. A body starts after the next unquoted newline; with no end-word line after it, the
    lines are kept. No `<<`, no change."""
    if "<<" not in seg:
        return seg
    out, i, n = [], 0, len(seg)
    stack = [[None, 0, ""]]                         # [quote, paren depth, kind: "" top, "(", "((", "`"]
    pending: "list[str]" = []
    while i < n:
        f = stack[-1]
        c, q = seg[i], f[0]
        if q == "'":
            f[0] = None if c == "'" else q
            out.append(c); i += 1; continue
        if c == "\\" and i + 1 < n:
            out.append(seg[i:i + 2]); i += 2; continue
        if c == "\n" and q is None and pending:
            out.append(c); i += 1
            for word in pending:                    # bodies follow in the order their `<<` appear
                j = i
                while j < n:
                    e = seg.find("\n", j)
                    e = n if e < 0 else e
                    if seg[j:e].strip() == word:
                        i = e + 1                   # past the end-word line itself
                        break
                    j = e + 1
            pending = []
            if i >= n and out[-1] == "\n":
                out.pop()
            continue
        if q is None and c == "'":
            f[0] = "'"; out.append(c); i += 1; continue
        if c == '"':
            f[0] = None if q == '"' else '"'
            out.append(c); i += 1; continue
        if seg.startswith("$((", i):
            stack.append([None, 2, "(("]); out.append("$(("); i += 3; continue
        if seg.startswith("$(", i) or (q is None and seg.startswith(("<(", ">("), i)):
            stack.append([None, 1, "("]); out.append(seg[i:i + 2]); i += 2; continue
        if c == "`":
            if f[2] == "`" and q is None:
                stack.pop()
            else:
                stack.append([None, 0, "`"])
            out.append(c); i += 1; continue
        if q is None and f[2] in ("(", "((") and c in "()":
            f[1] += 1 if c == "(" else -1
            if f[1] == 0:
                stack.pop()
            out.append(c); i += 1; continue
        if q is None and f[2] != "((" and c == "<":
            m = _HEREDOC_END.match(seg, i)
            if m:
                pending.append(next(g for g in m.groups() if g))
                out.append(m.group(0)); i = m.end(); continue
        out.append(c); i += 1
    return "".join(out)


def _split_punct(t: str) -> list[str]:
    out, i = [], 0
    while i < len(t):
        op = next((o for o in _PUNCT_OPS if t.startswith(o, i)), t[i])
        out.append(op); i += len(op)
    return out


def _commands(line: str) -> "list[list[str]]":
    """`line`'s simple commands as the shell hands over their words: quotes removed (a quoted prompt
    is ONE word, so `claude -p "what does --help do"` is not read as `--help`), a comment dropped
    (only a `#` at a word start begins one), every `$( )`/`<( )`/`>( )`/backtick span one word
    (`_placehold`, so its parentheses cut nothing), cut at every `&`, `&&`, `;`, `|`, `||`, `(`, `)` — a
    glued `)&` too — and every redirection dropped with its target (`> out.md`, `2>/dev/null`,
    `<<'EOF'`). `claude -p "task" & git --version` is two commands, and the `--version` belongs to
    git. A line shlex cannot parse (an unclosed quote) falls back to whitespace words, one command."""
    import shlex
    line = _placehold(_drop_comments(line))
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""                         # bash's rule is applied above; shlex's is not bash's
        toks = [p for t in lex for p in (_split_punct(t) if t and all(c in _PUNCT for c in t) else [t])]
    except ValueError:
        return [line.split()]
    out, cur, skip = [], [], False
    for t in toks:
        if skip:
            skip = False; continue
        if t in _OPS:
            if cur:
                out.append(cur)
            cur = []; continue
        if _REDIRECT.match(t):
            if cur and cur[-1].isdigit():
                cur.pop()                           # `2>/dev/null` lexes as `2`, `>`, `/dev/null`
            skip = True; continue                   # the target is the next token
        cur.append(t)
    if cur:
        out.append(cur)
    return out


def _is_lookup(w: list[str]) -> bool:
    """`command -v x`, `command -pv x`, `command -p -V x`: `command` looks the name up and runs nothing."""
    for x in w[1:]:
        if x == "--" or not x.startswith("-"):
            return False
        if "v" in x or "V" in x:
            return True
    return False


def _strip_argv_prefix(w: list[str]) -> list[str]:
    """`w` from its command on: shell keywords (`then`, `do`, `!`, `{`, `function f`), `VAR=val`
    words, the `strip_prefix` words with their options (`sudo -u me`, `env --chdir /tmp`,
    `time -p`, `nice -n 5`), `timeout [opts] <n>` and `env -S '<command>'`, in any order. A
    `command -v` lookup is no command: []."""
    import shlex
    import shellread
    prefix = None
    while w:
        head = Path(w[0]).name
        if not prefix and w[0] in _KEYWORDS:
            w = w[1:]; continue
        if not prefix and w[0] == "function":
            w = w[2:]; continue                     # `function f { claude …`
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]):
            w = w[1:]; continue
        if head == "command" and _is_lookup(w):
            return []
        if head in _TIMEOUTS:
            w = w[1:]
            while w and w[0].startswith("-"):
                w = w[2:] if w[0] in ("-s", "-k", "--signal", "--kill-after") else w[1:]
            w = w[1:]; prefix = None; continue      # the duration
        if head in shellread._PREFIXES:
            prefix = head; w = w[1:]; continue
        if prefix == "env" and len(w) > 1 and w[0] in ("-S", "--split-string"):
            try:                                    # `env -S 'claude -p hi'`: the value IS the command
                w = shlex.split(w[1]) + w[2:]
            except ValueError:
                w = w[1].split() + w[2:]
            continue
        if prefix and w[0].startswith("-"):
            w = w[2:] if w[0] in shellread._PREFIXES[prefix] else w[1:]
            continue
        break
    return w


def claude_argvs(seg: str, _depth: int = 0) -> "list[list[str]]":
    """Every `claude` command in this segment, each as its own words from `claude` on. A heredoc's
    body is not a command (its lines are cut out, the lines around it read: `claude -p <<'EOF'` then
    body text; `claude -p "$(cat <<'EOF'` … `)" --model x` keeps its `--model`) unless a shell reads it (`bash <<'EOF'`, `cat <<EOF | bash`); the `claude` inside `$( )` or
    backticks is read too (`x=$(claude -p "hi")`), but not inside a QUOTED heredoc's body, which the
    shell never expands (`killdoor._expanded`: a commit message quoting `claude -p` runs nothing)."""
    import shellread
    import killdoor
    out: "list[list[str]]" = []
    if _depth > _MAX_DEPTH:
        return out
    for body in killdoor.substitutions(killdoor._expanded(seg)):
        for inner in shellread.segments(body):
            out.extend(claude_argvs(inner, _depth + 1))
    first = seg.split("\n", 1)[0]
    heredoc = "\n" in seg and killdoor._HEREDOC_OP.search(first)
    if heredoc and shellread._heredoc_reader(seg) == "shell":
        for inner in shellread.segments(shellread._heredoc_body(seg)):
            out.extend(claude_argvs(inner, _depth + 1))
    for w in _commands(_drop_heredoc_bodies(seg)):
        w = _strip_argv_prefix(w)
        if w and Path(w[0]).name in ("claude", "claude.exe"):
            out.append(w)
    return out


def launches_nothing(w: list[str]) -> bool:
    """A `claude` call that prints something and starts no session: a subcommand of the set above as
    its first word after the valueless `--verbose`/`--debug` and the one-value `--settings`-class
    flags, or `--help` / `--version` anywhere in its OWN words (a heredoc body, the command after
    `&` and a comment are not its words)."""
    k = 1
    while k < len(w) and (w[k] in _CLAUDE_BARE_FLAGS or w[k] in _CLAUDE_VALUE_FLAGS):
        k += 2 if w[k] in _CLAUDE_VALUE_FLAGS else 1
    if k < len(w) and w[k] in _CLAUDE_SUBCMDS:
        return True
    own = w[1:w.index("--")] if "--" in w else w[1:]   # after `--` a `--help` is the prompt
    return any(x in _INFO_FLAGS for x in own)


def launch_words(cmd: str, segments):
    """Each `claude` call in `cmd` that STARTS a session, as its words. gate.py's older opt-in rule
    reads through this too, so the two doors can never disagree on what a launch is."""
    for seg in segments(cmd):
        for w in claude_argvs(seg):
            if not launches_nothing(w):
                yield w


def _has(w: list[str], flag: str) -> bool:
    return flag in w or any(x.startswith(flag + "=") for x in w)


def check_launch(cmd: str, segments) -> str | None:
    """The launch-pin door's words for a Bash command, or None. `segments` is gate.py's splitter."""
    if not enabled("launch_pin"):
        return None
    for w in launch_words(cmd, segments):
        miss = [f for f in ("--model", "--effort") if not _has(w, f)]
        if miss:
            return words("launch_pin", "this `claude` launch does not pin " + " or ".join(f"`{m}`" for m in miss) +
                         " — without them it runs on whatever the settings file names, at the platform's default "
                         "effort. Add `--model <id> --effort <low|medium|high>`.")
    return None
