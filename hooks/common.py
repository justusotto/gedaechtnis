"""Shared helpers for the Gedächtnis hooks. stdlib only, macOS python3.9+.

Every hook reads ONE JSON object on stdin (Claude Code's hook input), decides, and either
prints a JSON decision on stdout (exit 0) or prints nothing (exit 0 = no opinion). A hook
never exits non-zero on its own bugs: a crashing guard must not take the session down, so
`main()` wrappers catch everything and log to the state dir.

Every path comes from `config.py` — environment first, then ~/.claude/gedaechtnis/config.json,
then a default. Nothing here names a directory of its own, which is also what lets the test suite
redirect the whole plugin into a tmp dir and never touch the real vault or the real state
directory (Global/Errata: a suite that writes the application's real sidecar makes its own verdict
depend on the machine's state).
"""
from __future__ import annotations
import json, os, re, sys, time, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

HOME = config.HOME
VAULT = config.VAULT
STATE = config.STATE
ROSTER = config.ROSTER

ROLE_STEMS = frozenset(("Map","Vision","Position","Course","Canon","Patterns","Aporia","Eidos",
                        "Errata","Apparatus","Annales","Nomos","Lexicon","Ethos","Praxis","Exempla","Kernel"))


def read_input() -> dict:
    raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def log(name: str, line: str) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / f"{name}.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{line}\n")
    except OSError:
        pass


def deny(event: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def ask(event: str, reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                             "permissionDecision": "ask",
                                             "permissionDecisionReason": reason}}))


def context(event: str, text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))


def expand(p: str, cwd: str | None = None) -> Path:
    """Expand ~, $HOME, ${HOME}; make relative paths absolute against cwd."""
    s = p.strip().strip('"').strip("'")
    s = s.replace("${HOME}", str(HOME)).replace("$HOME", str(HOME))
    s = os.path.expanduser(s)
    path = Path(s)
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    try:
        return Path(os.path.normpath(str(path)))
    except Exception:
        return path


def under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def vault_rel(path: Path) -> str | None:
    try:
        return str(path.relative_to(VAULT))
    except ValueError:
        return None


# ---- lane resolution: DECLARED by a .atlas-lane marker, LOCATED by cwd (same walk as the Stop hook) ----

def find_marker(cwd: str | None) -> Path | None:
    if not cwd:
        return None
    d = Path(cwd)
    for _ in range(8):
        if d == Path("/") or d == HOME:
            break
        m = d / ".atlas-lane"
        if m.is_file():
            return m
        d = d.parent
    return None


def parse_marker(marker: Path) -> tuple[str | None, list[str]]:
    lane, paths = None, []
    try:
        for line in marker.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("lane:"):
                lane = s.split(":", 1)[1].strip()
            elif s.startswith("path:"):
                p = s.split(":", 1)[1].strip().rstrip("/")
                if p.startswith("/") or ".." in p:
                    return None, []          # reject the WHOLE marker, exactly like the Stop hook
                if p:
                    paths.append(p)
    except OSError:
        return None, []
    if not lane or not paths:
        return None, []
    return lane, paths


def lane_for(cwd: str | None) -> tuple[str | None, list[str], Path | None]:
    m = find_marker(cwd)
    if not m:
        return None, [], None
    lane, paths = parse_marker(m)
    return lane, paths, m


def path_in_partition(rel: str, prefixes: list[str]) -> bool:
    for p in prefixes:
        if rel == p or rel.startswith(p + "/"):
            return True
    return False


def fleet_repos() -> list[Path]:
    """`repo:` lines of the roster's fleet-roster block, $HOME-relative, plus the vault itself."""
    out = [VAULT]
    try:
        txt = ROSTER.read_text(encoding="utf-8")
    except OSError:
        return out
    for m in re.finditer(r"^\s*repo:\s*(\S+)", txt, re.M):
        out.append(HOME / m.group(1))
    return out


def guarded(fn):
    """Run a hook body; never let a hook bug crash the session."""
    try:
        fn()
    except SystemExit:
        raise
    except Exception:
        log("hook-errors", traceback.format_exc().replace("\n", " | "))
    sys.exit(0)


# ---- shared surfaces: ANY lane may APPEND one keyed row; nothing else (his ruling 2026-09-08, round 2) ----
import re as _re

def shared_surface(rel: str) -> str | None:
    """Return the surface kind for a vault-relative path, or None."""
    if _re.match(r"^Channels/ledger/\d{4}-\d{2}\.tsv$", rel):
        return "ledger"
    if _re.match(r"^Pharos/queues/regions/[^/]+\.md$", rel):
        return "queue"
    if _re.match(r"^[^/]+/[^/]+/Inbox\.md$", rel) or _re.match(r"^Speculum/Inbox\.md$", rel):
        return "inbox"
    if rel == "Global/fleet-roster.md":
        return "roster"                    # the intended write partition: every lane may APPEND a declaration row, nobody rewrites it
    if rel == "Pharos/artifacts-index.md":
        return "artifacts-index"
    if rel in ("Mnemosyne/Canon.md", "Mnemosyne/Position.md"):
        return "umbrella-shared"          # ACT2: shared by four lanes, atomic single-Edit APPENDS only
    return None


def row_ok(kind: str, line: str) -> bool:
    if kind == "ledger":
        return _re.match(r"^N-\d{4}-\d{2}-\d{2}-\d{4}\t\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\t[A-Z][A-Z0-9-]*\t(?:[A-Z][A-Z0-9-]*|\*)\t(?:fact|notice|request|handoff|read|ack)\t[^\t]*\t[^\t]*$", line) is not None
    if kind == "queue":
        return _re.match(r"^- \[ \] `q:[A-Z]+-\d{4}-\d{2}-\d{2}-[A-Z0-9-]+`", line) is not None or _re.match(r"^  - [a-z_]+:", line) is not None
    if kind == "inbox":
        return _re.match(r"^- \d{4}-\d{2}-\d{2} [A-Z][A-Z0-9-]* ", line) is not None
    if kind == "artifacts-index":
        return _re.match(r"^\| \d{4}-\d{2}-\d{2} \| [^|]+ \| `[0-9a-f-]{36}` \|$", line) is not None
    if kind == "roster":
        return _re.match(r"^\s*(?:#.*|(?:lane|repo|path):\s*\S.*)$", line) is not None
    if kind == "umbrella-shared":
        return bool(line.strip())                 # any content, as long as it is APPENDED
    return False


def pure_append(kind: str, path: Path, tool: str, ti: dict, lane: str | None = None) -> tuple[bool, str]:
    """Is this Edit/Write a pure append of well-formed rows to a shared surface? Returns (ok, why)."""
    try:
        cur = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return False, "cannot read the current file"
    if kind == "roster":
        # The roster's consumer (marker_check) reads ONLY the ```fleet-roster fence, block by block: a row belongs to the
        # LAST `lane:` header above it. So an append is admitted only INSIDE THE APPENDER'S OWN BLOCK — at its end — or
        # as a NEW block `lane: <own lane>` before the closing fence when no block for that lane exists. A `lane:` header
        # for any other lane, or a row under another lane's header, is refused (Balthasar, closure round).
        if not lane:
            return False, "roster append needs a resolved lane (no .atlas-lane marker for this cwd)"
        f0 = cur.find("```fleet-roster"); f1 = cur.rfind("\n```")
        if f0 == -1 or f1 == -1 or f1 <= f0:
            return False, "roster has no fleet-roster fence to append inside"
        if tool == "Write":
            new = ti.get("content") or ""
        else:
            old, rep = ti.get("old_string") or "", ti.get("new_string") or ""
            k = cur.find(old)
            if not old or k == -1 or cur.count(old) != 1 or not rep.startswith(old) and not rep.endswith(old):
                return False, "roster Edit must extend a unique anchor (rows added after the anchor, or before the closing fence)"
            new = cur[:k] + rep + cur[k + len(old):]
        # the change must be ONE insertion: common prefix + inserted text + common suffix
        a = 0
        while a < min(len(cur), len(new)) and cur[a] == new[a]:
            a += 1
        b = 0
        while b < min(len(cur), len(new)) - a and cur[-1 - b] == new[-1 - b]:
            b += 1
        if len(new) < len(cur) or cur[:a] + cur[len(cur) - b:] != cur:
            return False, "roster change is not a pure insertion"
        inserted = new[a: len(new) - b]
        if a < f0 or a > f1 + 1:
            return False, "roster append must sit inside the fleet-roster fence"
        rows = [l for l in inserted.splitlines() if l.strip()]
        if not rows:
            return False, "nothing appended"
        bad = [l for l in rows if not row_ok(kind, l)]
        if bad:
            return False, f"appended line is not a well-formed roster row: {bad[0][:80]!r}"
        # which block does the insertion point fall in? the last `lane:` header above it in the ORIGINAL text
        before = cur[f0:a]
        heads = _re.findall(r"^\s*lane:\s*(\S+)", before, _re.M)
        block_lane = heads[-1] if heads else None
        after_txt = cur[a:f1 + 1]
        at_block_end = _re.match(r"^\s*(?:#[^\n]*\n\s*)*(?:lane:|\s*$)", after_txt) is not None or a >= f1
        new_heads = [_re.match(r"^\s*lane:\s*(\S+)", l).group(1) for l in rows if _re.match(r"^\s*lane:", l)]
        if new_heads:
            if new_heads != [lane] or rows[0].strip().split(":")[0] != "lane":
                return False, f"a new roster block may only be `lane: {lane}` (the appender's own lane), as the first inserted line"
            if _re.search(r"^\s*lane:\s*" + _re.escape(lane) + r"\s*$", cur[f0:f1], _re.M):
                return False, f"lane {lane} already has a block — append inside it, do not open a second"
            if a < f1:
                return False, "a new roster block goes before the closing fence"
            return True, f"new roster block for {lane} ({len(rows)} row(s))"
        if block_lane != lane:
            return False, f"roster rows may only be appended inside the appender's own block (`lane: {lane}`), not under `lane: {block_lane}`"
        if not at_block_end:
            return False, "roster rows are appended at the END of the appender's block (before the next `lane:` header)"
        return True, f"pure append of {len(rows)} roster row(s) inside block {lane}"
    if tool == "Write":
        new = ti.get("content") or ""
        if not new.startswith(cur):
            return False, "Write does not start with the file's current content (not an append)"
        added = new[len(cur):]
    else:
        old, new = ti.get("old_string") or "", ti.get("new_string") or ""
        if not cur.rstrip("\n").endswith(old.rstrip("\n")) or not new.startswith(old):
            return False, "Edit is not at the tail, or the replacement does not start with the replaced text (not an append)"
        added = new[len(old):]
    rows = [l for l in added.splitlines() if l.strip()]
    if not rows:
        return False, "nothing appended"
    bad = [l for l in rows if not row_ok(kind, l)]
    if bad:
        return False, f"appended line is not a well-formed {kind} row: {bad[0][:80]!r}"
    return True, f"pure append of {len(rows)} {kind} row(s)"


def region_of_repo(repo: Path) -> str | None:
    """The vault region a repo belongs to, read from its CLAUDE.md @-imports (`<Umbrella>/<Region>/(Kernel|Position).md`)."""
    try:
        txt = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    except OSError:
        return None
    for m in _re.finditer(r"^@" + _re.escape(str(VAULT)) + r"/((?:[^/\s]+/)?[^/\s]+)/(?:Kernel|Position)\.md", txt, _re.M):
        top = m.group(1).split("/")[0]
        if top in ("Global", "Pharos", "Channels", "Workflows", "Limen", "Concilium"):
            continue
        if "/" in m.group(1) or top == "Speculum" or _is_leaf_region(VAULT / top):
            return m.group(1)
    return None


def _is_leaf_region(d: Path) -> bool:
    """A one-segment directory is a REGION when no child directory of its own carries a
    Position.md or Kernel.md — a stranger's flat vault (`<vault>/<Region>/`) has exactly this
    shape; an Atlas umbrella (`Mnemosyne/`) has child regions and is never one."""
    try:
        if not d.is_dir() or not any((d / f).is_file() for f in ("Position.md", "Kernel.md", "Map.md")):
            return False
        for child in d.iterdir():
            if child.is_dir() and not child.name.startswith(".") and any((child / f).is_file() for f in ("Position.md", "Kernel.md")):
                return False
    except OSError:
        return False
    return True


def repo_root_of(path: Path) -> Path | None:
    d = path if path.is_dir() else path.parent
    for _ in range(12):
        if (d / ".git").exists():
            return d
        if d == d.parent or d == HOME:
            return None
        d = d.parent
    return None
