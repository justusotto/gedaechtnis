"""Shared helpers for the Gedächtnis hooks. stdlib only, macOS python3.9+.

Every hook reads ONE JSON object on stdin (Claude Code's hook input), decides, and either
prints a JSON decision on stdout (exit 0) or prints nothing (exit 0 = no opinion). A hook
never exits non-zero on its own bugs: a crashing guard must not take the session down, so
`main()` wrappers catch everything and log to the state dir.

Paths are overridable by environment so the test suite never touches the real vault or the
real state directory (Global/Errata: a suite that writes the application's real sidecar makes
its own verdict depend on the machine's state):
  GEDAECHTNIS_VAULT      the vault root            (default ~/Atlas)
  GEDAECHTNIS_STATE_DIR  where hooks keep state    (default ~/.claude/gedaechtnis)
  GEDAECHTNIS_FLEET_ROSTER  fleet roster path      (default <vault>/Global/fleet-roster.md)
"""
from __future__ import annotations
import json, os, re, sys, time, traceback
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
VAULT = Path(os.environ.get("GEDAECHTNIS_VAULT", str(HOME / "Atlas"))).expanduser()
STATE = Path(os.environ.get("GEDAECHTNIS_STATE_DIR", str(HOME / ".claude" / "gedaechtnis"))).expanduser()
ROSTER = Path(os.environ.get("GEDAECHTNIS_FLEET_ROSTER", str(VAULT / "Global" / "fleet-roster.md")))

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
