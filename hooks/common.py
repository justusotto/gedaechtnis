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


def git_root(start: str | Path | None) -> Path | None:
    """The git toplevel containing `start`, or None — the same bounded walk `find_marker` does.

    No subprocess: `.git` is a directory in a checkout and a file in a worktree, and both are
    what "this is a repo" means here. HOME is never the answer even when it is itself a repo:
    offering a memory to a user's entire home directory is never what they meant."""
    if not start:
        return None
    d = Path(start)
    for _ in range(12):
        if d == d.parent or d == HOME:
            return None
        if (d / ".git").exists():
            return d
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


# ---- the per-session record: what THIS session touched, so Stop commits that and nothing else ----
# One file per session id (session_start.py writes it, claim.py adds its claims, chore.py appends
# the vault paths the session actually wrote, commit.py reads them back). Every writer goes
# through the two helpers below so the read-modify-write is locked: two hook processes appending
# a path in the same turn must not lose one of them, and neither may drop another key.

def session_state_path(sid: str) -> Path:
    return STATE / f"session-start-{sid or '-'}.json"


def update_session_state(sid: str, mutate) -> dict:
    """Read-modify-write the session's record under an exclusive lock; returns the new document.

    `mutate(doc)` edits the dict in place. A malformed or missing file is treated as an empty
    document rather than an error: the record is bookkeeping, and a session must never die of it."""
    import fcntl
    path = session_state_path(sid)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(STATE / f"session-{(sid or '-')}.lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict):
                    doc = {}
            except (OSError, ValueError):
                doc = {}
            mutate(doc)
            path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            fcntl.flock(lk, fcntl.LOCK_UN)
            return doc
    except OSError as e:
        log("hook-errors", f"session-state\t{sid}\t{e}")
        return {}


def record_touched(sid: str, rel: str) -> None:
    """Note that this session wrote one vault-relative path. Order preserved, no duplicates."""
    def add(doc: dict) -> None:
        t = doc.get("touched")
        if not isinstance(t, list):
            t = []
        if rel not in t:
            t.append(rel)
        doc["touched"] = t
    update_session_state(sid, add)


def touched_paths(sid: str) -> list[str]:
    """The vault paths this session wrote, or [] — [] means "wrote nothing", never "commit all"."""
    try:
        doc = json.loads(session_state_path(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    t = doc.get("touched") if isinstance(doc, dict) else None
    return [p for p in t if isinstance(p, str)] if isinstance(t, list) else []


# ---- was this write a CREATION? the PreToolUse gate is the only place that can still see ----
# A PostToolUse chore runs after the file exists, so it cannot tell a new file from an edited one.
# The gate, which runs immediately before the same tool call, can: it stamps one tiny marker per
# path saying whether the path existed at that moment, and the chore reads it back and consumes it.
# Chosen over an mtime/ctime heuristic because it is a RECORDED OBSERVATION rather than an
# inference — it does not move with the filesystem's timestamp granularity, a `Write` that
# rewrites an existing file byte-for-byte, or a file copied into place by something else.

def pre_exists_marker(p: Path) -> Path:
    import hashlib
    return STATE / ("pre-exists-" + hashlib.sha1(str(p).encode("utf-8")).hexdigest()[:16])


def note_pre_exists(p: Path) -> None:
    """Record, at PreToolUse time, whether `p` is already on disk. Rewritten on every gate call
    for that path, so a marker left behind by a DENIED write is corrected before it is ever read."""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        pre_exists_marker(p).write_text("1" if p.exists() else "0", encoding="utf-8")
    except OSError:
        pass


def was_created(p: Path) -> bool:
    """True when the gate saw `p` ABSENT immediately before this write; consumes the marker.

    No marker (the gate is not wired, or it never saw this path) → False. A chore that cannot
    PROVE the file is new treats it as old: the Map row is added on evidence, never on a guess."""
    m = pre_exists_marker(p)
    try:
        v = m.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    try:
        m.unlink()
    except OSError:
        pass
    return v == "0"


def record_created(sid: str, rel: str) -> None:
    """Note that THIS session created this vault-relative path (chore.py, on the evidence of the
    gate's pre-exists marker — a PostToolUse record, so the file really was written).

    D1 reads this back: a whole-file `Write` to a file this session made is not a lost update,
    because there is no other session's content in it to lose. The record is per session id and
    lives beside the touched set, so it dies with the session as it should."""
    def add(doc: dict) -> None:
        c = doc.get("created")
        if not isinstance(c, list):
            c = []
        if rel not in c:
            c.append(rel)
        doc["created"] = c
    update_session_state(sid, add)


def created_paths(sid: str) -> list[str]:
    """The vault paths this session created, or [] — [] means "created nothing", never "all"."""
    try:
        doc = json.loads(session_state_path(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    c = doc.get("created") if isinstance(doc, dict) else None
    return [p for p in c if isinstance(p, str)] if isinstance(c, list) else []


# ---- D2: the per-file mutex, PreToolUse → PostToolUse (DESIGN §5.2 D2) ----
# Edit is a compare-and-swap, but the swap happens inside the tool: read, match the anchor, write.
# Two sessions can be inside that window on the same file at the same moment, and the second one's
# match is then against text the first is about to replace. The mutex closes exactly that window:
# the PreToolUse gate takes a per-file lock, the PostToolUse chore drops it, and a second session
# meeting a live lock is told to RETRY — which makes the model re-read, which is the correct fix.
#
# The lock lives under the STATE dir, never in the vault: it is session bookkeeping, and a lock
# file inside the vault would be committed, synced and read as memory.
#
# STALENESS IS THE WHOLE FAIL-SAFE. A PostToolUse that never fires — the tool errored, the hook
# process died, the session was killed mid-turn — would otherwise leave a file locked forever. So
# a lock older than LOCK_TTL seconds is not a lock: it is taken over silently by the next writer,
# who overwrites it with its own session id. The cost of the takeover being wrong is one lost
# 10-second race; the cost of not having it is a file no session can ever edit again.

LOCK_TTL = 10.0


def filelock_path(p: Path) -> Path:
    import hashlib
    try:
        real = os.path.realpath(str(p))
    except OSError:
        real = str(p)
    return STATE / "filelocks" / hashlib.sha1(real.encode("utf-8")).hexdigest()


def read_filelock(p: Path) -> dict | None:
    try:
        doc = json.loads(filelock_path(p).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def take_filelock(p: Path, sid: str) -> tuple[bool, float, str]:
    """Try to hold `p` for this session. Returns (ok, age_of_blocking_lock, blocking_session).

    Refuses only for a lock that is BOTH foreign and younger than LOCK_TTL. Our own lock is
    re-stamped (a retry of the same edit must not deadlock against itself), and a stale one is
    taken over — see the staleness note above."""
    cur = read_filelock(p)
    if cur:
        age = max(0.0, time.time() - float(cur.get("ts") or 0))
        if cur.get("session_id") != sid and age < LOCK_TTL:
            return False, age, str(cur.get("session_id") or "?")
    try:
        d = filelock_path(p)
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(json.dumps({"session_id": sid, "ts": time.time(), "path": str(p)}), encoding="utf-8")
    except OSError as e:
        log("hook-errors", f"filelock-take\t{p}\t{e}")
    return True, 0.0, sid


def release_filelock(p: Path, sid: str) -> bool:
    """Drop the lock on `p` if it is OURS. A foreign session's lock is never removed: releasing
    somebody else's mutex is the same defect as taking it."""
    cur = read_filelock(p)
    if not cur or cur.get("session_id") != sid:
        return False
    try:
        filelock_path(p).unlink()
        return True
    except OSError:
        return False


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
