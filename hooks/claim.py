#!/usr/bin/env python3
"""claim.py — the per-region writer claim, taken at SessionStart and given back at Stop.

The vault's rule is one writer per region at a time. The mechanism for it already exists as a
shell helper (an advisory `mkdir` arbiter with an owner file, a reaper and honest exit codes);
what did not exist was anyone reliably CALLING it. A session was supposed to claim its region as
the first act of its boot ritual, from memory, every time — and a ritual performed from memory is
performed sometimes. So the claim moves here: taken like breathing, on the SessionStart event,
and given back on Stop.

    claim.py start    # SessionStart — claim this repo's region(s) for this session
    claim.py stop     # Stop — release exactly what this session claimed, and nothing else

**Why both halves ship together.** A start that claims without a stop that releases does not
leave the lock where it was; it leaves it held by a session that no longer exists, and the next
lane to want that region defers to a ghost. Releasing is the more important half of the pair.

**This hook never edits the claim mechanism, it only calls it.** The helper's contract is the
whole interface, and three of its rules shape the code below:

  - Ownership is asserted ONLY by a winning `mkdir`. `claim` returns 0 when the lock is held by
    this pid, 1 when another holder has it, 4 when it won the directory but lost it again before
    the owner file landed (it is then holding NOTHING). So a claim is recorded here only on 0 —
    a 1 or a 4 records nothing, because recording it would produce a release attempt against a
    lock this session does not own.
  - `release` returns 0 only when the lock is GONE, and 5 when the lock SURVIVES the attempt.
    A 5 is logged and kept in the session's claim list; it is never treated as a release.
  - An interactive claim's pid is the argument that keeps its lock alive: the helper vetoes
    reaping a lock whose pid is confirmed live on this host, and grants such a lock a long
    ceiling. A hook process lives for milliseconds, so claiming with the hook's own pid produces
    a lock that is stale before the session's first prompt. The pid handed over is therefore the
    Claude Code process itself, found by walking up the parent chain (`_claude_pid`).

**Doing nothing is a first-class outcome.** The hook prints nothing and calls nothing when the
claim helper is not installed, when no `.atlas-lane` marker resolves from the session's cwd, when
the marker names no region, or when `auto_claim` is off. A stranger who installs this plugin has
no claim helper and never sees this hook at all.

**`auto_claim` defaults to TRUE.** Coordination that has to be switched on is coordination that is
off: the sessions that most need a claim are the ones nobody configured.

KNOWN LIMIT, stated rather than hidden: Claude Code fires `Stop` when the main agent finishes
responding — that is every turn, not only at the end of a session. The pair below is therefore
honest about locks (nothing is ever left held by a dead session) but its claim covers the turn
that took it, not the whole session; a later turn runs unclaimed until the next SessionStart.
Closing that gap needs a per-turn re-claim on an event this plugin does not yet hook, and the
re-claim is safe when it comes: `claim` against a lock this pid already holds is idempotent.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import read_input, context, log, lane_for, region_of_repo, repo_root_of, guarded
import common
# VAULT, STATE deliberately NOT imported by name: a `from` import binds the value
# ONCE, which is the frozen-path defect this package was bitten by. Read through the
# module object (common.VAULT) so PEP 562 re-resolves on every access.

EV = "SessionStart"

# Vault top-level directories that are NOT regions: the shared surface and the non-tier folders.
# A claim against one of these would serialize every lane in the fleet against every other.
NON_REGION_TOPS = frozenset(("Global", "Pharos", "Channels", "Workflows", "Limen", "Concilium"))


# ---- the claim helper -------------------------------------------------------------------

def _run_tool(tool: Path, sub: str, region: str, pid: int) -> tuple[int, str]:
    """One call to the claim helper. Returns (exit code, stderr). The helper reads the vault
    root from ATLAS, so the configured vault is passed explicitly — a hook must never act on a
    different vault than the one the rest of the plugin is looking at."""
    argv = [str(tool)] if os.access(str(tool), os.X_OK) else ["/bin/bash", str(tool)]
    argv += [sub, region, str(pid)]
    env = dict(os.environ, ATLAS=str(common.VAULT))
    try:
        p = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=10, env=env)
        return p.returncode, (p.stderr or "").strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 3, str(e)


# ---- which process gets to own the lock -------------------------------------------------

def _ps(pid: int) -> tuple[int, str] | None:
    """(ppid, full command) for one pid, or None if it cannot be read."""
    try:
        p = subprocess.run(["ps", "-o", "ppid=,command=", "-p", str(pid)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return None
    line = (p.stdout or "").strip()
    if p.returncode != 0 or not line:
        return None
    head, _, rest = line.partition(" ")
    try:
        return int(head), rest.strip()
    except ValueError:
        return None


def _is_claude(cmd: str) -> bool:
    """True when a command line RUNS claude, not merely when it MENTIONS it.

    Every process in this chain mentions it: the hook's own command line contains the plugin's
    path under a `.claude` directory, and the shell that ran it inherits that string. A substring
    test would therefore match the transient shell — the one process whose pid must never end up
    in a lock file. So the test is on each argument's BASENAME: `.claude` is a directory
    component, never a basename, while `claude`, `/usr/local/bin/claude` and `bin/claude` all
    reduce to the basename `claude`."""
    for tok in cmd.split():
        if os.path.basename(tok.rstrip("/")) == "claude":
            return True
    return False


def _claude_pid(max_hops: int = 6) -> int | None:
    """The Claude Code process this hook is running under, found by walking the parent chain.

    Returns None rather than a guess. There is no fallback to the hook's own pid or to a shell's:
    a lock owned by a process that exits in milliseconds is worse than no lock, because it reads
    as a live holder to `status` and as a reapable wedge to everyone else."""
    me = os.getpid()
    cur = me
    for _ in range(max_hops):
        info = _ps(cur)
        if not info:
            return None
        ppid = info[0]
        if ppid <= 1:
            return None
        parent = _ps(ppid)
        if not parent:
            return None
        if _is_claude(parent[1]):
            return ppid if ppid != me else None
        cur = ppid
    return None


# ---- which regions this session may claim ------------------------------------------------

def _regions_for(cwd: str) -> tuple[str | None, list[str]]:
    """(lane, regions) for a session's cwd — DECLARED, never inferred from the directory name.

    Two declared sources, in order: the repo's own `CLAUDE.md` @-imports, which name the region
    the repo is FOR, and then any `<Umbrella>/<Region>` directory in the `.atlas-lane` marker's
    partition. Shared and non-tier prefixes (`Global/`, `Pharos/…`), single-segment umbrellas and
    individual files are not regions and are never claimed. A region that does not exist in the
    vault is dropped: the claim would be about nothing."""
    lane, prefixes, _marker = lane_for(cwd)
    if not lane:
        return None, []
    regions: list[str] = []
    root = repo_root_of(Path(cwd))
    primary = region_of_repo(root) if root else None
    if primary:
        regions.append(primary)
    for p in prefixes:
        parts = p.split("/")
        if len(parts) != 2 or "." in parts[1] or parts[0] in NON_REGION_TOPS:
            continue
        if p not in regions:
            regions.append(p)
    return lane, [r for r in regions if (common.VAULT / r).is_dir()]


# ---- the session's own record of what it holds -------------------------------------------

def _state_file(sid: str) -> Path:
    return common.STATE / f"session-start-{sid}.json"


def _read_claims(sid: str) -> tuple[dict, list[dict]]:
    try:
        doc = json.loads(_state_file(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, []
    if not isinstance(doc, dict):
        return {}, []
    claims = doc.get("claims")
    return doc, [c for c in claims if isinstance(c, dict)] if isinstance(claims, list) else []


def _write_claims(sid: str, doc: dict, claims: list[dict]) -> None:
    doc = dict(doc)
    doc["claims"] = claims
    try:
        common.STATE.mkdir(parents=True, exist_ok=True)
        _state_file(sid).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    except OSError as e:
        log("hook-errors", f"claim\tcannot write {_state_file(sid)}: {e}")


# ---- the two entry points ------------------------------------------------------------------

def _preflight(inp: dict) -> tuple[Path, str, list[str]] | None:
    """(tool, cwd, regions), or None for any of the ordinary reasons to do nothing."""
    if not config.flag("auto_claim", True):
        return None
    tool = config.claim_tool()
    if tool is None:
        return None
    cwd = inp.get("cwd") or os.getcwd()
    _lane, regions = _regions_for(cwd)
    if not regions:
        return None
    return tool, cwd, regions


def cmd_start(inp: dict) -> None:
    pre = _preflight(inp)
    if pre is None:
        return
    tool, cwd, regions = pre
    sid = inp.get("session_id", "-")
    pid = _claude_pid()
    if pid is None:
        log("hook-errors", f"claim\tstart\tsid={sid}\tno claude process found within 6 hops of "
                           f"pid {os.getpid()}; claiming nothing (a transient pid would be reaped "
                           f"as a wedge and read as a live holder in the meantime)")
        return
    doc, claims = _read_claims(sid)
    held = {(c.get("region"), c.get("pid")) for c in claims}
    got, deferred = [], []
    for region in regions:
        rc, err = _run_tool(tool, "claim-interactive", region, pid)
        log("claim", f"start\tsid={sid}\tself={os.getpid()}\tclaude={pid}\tregion={region}\trc={rc}"
                     + (f"\t{err}" if err else ""))
        if rc == 1:
            # Held by another writer — possibly a DEAD one: `claim-interactive` does not reap, and
            # on its first live run (2026-09-09) this hook was refused by a lock whose pid had been
            # dead for twelve days. Ask the helper's own reaper (it applies its thresholds; a live
            # holder is never touched), then try ONCE more. Never loop.
            rrc, rerr = _run_tool(tool, "reap", region, pid)
            log("claim", f"reap\tsid={sid}\tregion={region}\trc={rrc}" + (f"\t{rerr}" if rerr else ""))
            if rrc == 0:
                rc, err = _run_tool(tool, "claim-interactive", region, pid)
                log("claim", f"retry\tsid={sid}\tclaude={pid}\tregion={region}\trc={rc}"
                             + (f"\t{err}" if err else ""))
        if rc == 0:                                    # 0 is the ONLY code that means "held by me"
            got.append(region)
            if (region, pid) not in held:
                claims.append({"region": region, "pid": pid})
                held.add((region, pid))
        else:                                          # 1 held-by-other · 3 error · 4 raced-lost
            deferred.append(f"{region} (rc {rc})")
    _write_claims(sid, doc, claims)
    lines = []
    if got:
        lines.append(f"- Region claim held for this session: {', '.join(got)} (pid {pid}); it is "
                     f"released automatically — you do not need to run the claim helper by hand.")
    if deferred:
        lines.append(f"- Region claim NOT held: {', '.join(deferred)} — another writer holds it. "
                     f"Coordinate before writing there; the lock advises, it does not block.")
    if lines:
        context(EV, "\n".join(lines))


def cmd_stop(inp: dict) -> None:
    """Release exactly what `start` recorded for this session id, and nothing else.

    Never a sweep: releasing "all locks that look like ours" would yank a sibling session's claim
    on the same host. The recorded (region, pid) pairs are the whole authority, and the helper
    refuses anything else anyway — a refusal it reports as exit 5."""
    sid = inp.get("session_id", "-")
    doc, claims = _read_claims(sid)
    if not claims:
        return
    tool = config.claim_tool()
    if tool is None:
        return
    kept = []
    for c in claims:
        region, pid = c.get("region"), c.get("pid")
        if not region or not isinstance(pid, int):
            continue
        rc, err = _run_tool(tool, "release-interactive", region, pid)
        log("claim", f"stop\tsid={sid}\tregion={region}\tpid={pid}\trc={rc}" + (f"\t{err}" if err else ""))
        if rc != 0:                                    # 5 = the lock SURVIVES; keep it recorded
            kept.append(c)
            log("hook-errors", f"claim\tstop\tregion={region}\tpid={pid}\trc={rc}\tthe lock was NOT "
                               f"released and SURVIVES; clear it with the pid `region_claim.sh status` shows")
    _write_claims(sid, doc, kept)


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    inp = read_input()
    if which == "start":
        cmd_start(inp)
    elif which == "stop":
        cmd_stop(inp)


if __name__ == "__main__":
    guarded(main)
