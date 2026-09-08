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
    return None


def row_ok(kind: str, line: str) -> bool:
    if kind == "ledger":
        return _re.match(r"^N-\d{4}-\d{2}-\d{2}-\d{4}\t\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\t[A-Z][A-Z0-9-]*\t(?:[A-Z][A-Z0-9-]*|\*)\t(?:fact|notice|request|handoff|read|ack)\t[^\t]*\t[^\t]*$", line) is not None
    if kind == "queue":
        return _re.match(r"^- \[ \] `q:[A-Z]+-\d{4}-\d{2}-\d{2}-[A-Z0-9-]+`", line) is not None or line.startswith("  - ")
    if kind == "inbox":
        return _re.match(r"^- \d{4}-\d{2}-\d{2} [A-Z][A-Z0-9-]* ", line) is not None
    return False


def pure_append(kind: str, path: Path, tool: str, ti: dict) -> tuple[bool, str]:
    """Is this Edit/Write a pure tail-append of well-formed rows? Returns (ok, why)."""
    try:
        cur = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return False, "cannot read the current file"
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
    for m in _re.finditer(r"^@" + _re.escape(str(VAULT)) + r"/([^/\s]+/[^/\s]+)/(?:Kernel|Position)\.md", txt, _re.M):
        if m.group(1).split("/")[0] not in ("Global",):
            return m.group(1)
    return None


def repo_root_of(path: Path) -> Path | None:
    d = path if path.is_dir() else path.parent
    for _ in range(12):
        if (d / ".git").exists():
            return d
        if d == d.parent or d == HOME:
            return None
        d = d.parent
    return None
