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
    Claude Code process itself, found by walking up the parent chain (`procs.session_process`) —
    under a Remote Control host, the thread's own process, never the host's.

**Doing nothing is a first-class outcome — but it is SAID, not silent.** The hook calls nothing
when the claim helper is not installed, when no `.atlas-lane` marker resolves from the session's
cwd, when the marker names no region, or when `auto_claim` is off. This package ships no copy of
the helper (it is a fleet asset, and copying a trip-wired coordination mechanism forks it), so on
an ordinary install the claim is OFF. Where a claim WOULD have been taken — the session is in a
region — and was not, the session gets a one-line statement of which state it is in, because
silence and a held claim look identical from inside a session and only one of them is safe to act
on. Outside a region there is nothing to claim and nothing to say.

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
import json, os, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import read_input, context, log, lane_for, region_of_repo, repo_root_of, guarded
import common
import procs
# VAULT, STATE deliberately NOT imported by name: a `from` import binds the value
# ONCE, which is the frozen-path defect this package was bitten by. Read through the
# module object (common.VAULT) so PEP 562 re-resolves on every access.

EV = "SessionStart"


def _event(inp: dict) -> str:
    """The event this run answers, read from the hook's own input — never a constant.

    `hooks.json` runs `claim.py start` on SessionStart AND on UserPromptSubmit, and Claude Code
    refuses a hook whose output names another event than the one it was called for ("Hook returned
    incorrect event name: expected 'UserPromptSubmit' but got 'SessionStart'", 76 times in 11
    hours, 2026-09-28): the claim was taken and its context line dropped. `EV` stays only as the
    answer for an input that names no event."""
    ev = inp.get("hook_event_name")
    return ev if isinstance(ev, str) and ev else EV

# Vault top-level directories that are NOT regions: the shared surface and whatever non-tier
# folders THIS vault has grown (`config.topology()["non_region_tops"]`; `Global/` alone by
# default). A claim against one of these would serialize every lane in the fleet against every
# other, so the set is read rather than assumed — a vault whose shared folder has another name
# would otherwise have it claimed as a region.


# ---- the claim helper -------------------------------------------------------------------

def _run_tool(tool: Path, sub: str, region: str, pid: int, capture: bool = False) -> tuple[int, str]:
    """One call to the claim helper. Returns (exit code, stderr). The helper reads the vault
    root from ATLAS, so the configured vault is passed explicitly — a hook must never act on a
    different vault than the one the rest of the plugin is looking at."""
    argv = [str(tool)] if os.access(str(tool), os.X_OK) else ["/bin/bash", str(tool)]
    argv += [sub, region, str(pid)]
    env = dict(os.environ, ATLAS=str(common.VAULT))
    try:
        p = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=10, env=env)
        return p.returncode, ((p.stdout or "") if capture else (p.stderr or "").strip())
    except (subprocess.TimeoutExpired, OSError) as e:
        return 3, str(e)


# ---- which process gets to own the lock -------------------------------------------------
# The parent-chain walk and the `claude` basename test moved to `procs.py` when the worktree sweep
# needed the same two answers — a second implementation of either is the duplicated fact this
# package refuses everywhere else. Behaviour is unchanged; `test_claim.py` is what says so.

#
# PROJECTCLAIM-1 (2026-09-29): the holder is the SESSION's own process — `procs.session_process`,
# which tells a Remote Control host's threads apart (each thread is its own `--sdk-url` child) and
# never answers the host (`claude remote-control …`), which outlives every thread it starts. A plain
# `claude` session gets exactly the pid `claude_pid` gave it before.

_ps = procs.ps_info
_is_claude = procs.is_claude
_claude_pid = procs.claude_pid


# ---- which regions this session may claim ------------------------------------------------

def _regions_for(cwd: str) -> tuple[str | None, list[str]]:
    """(lane, regions) for a session's cwd — DECLARED, never inferred from the directory name.

    Two declared sources, in order: the repo's own `CLAUDE.md` @-imports, which name the region
    the repo is FOR, and then any `<Umbrella>/<Region>` directory in the `.atlas-lane` marker's
    partition. Shared and non-tier prefixes, single-segment umbrellas and
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
        if len(parts) != 2 or "." in parts[1] or parts[0].startswith(".") or parts[0] in config.non_region_tops():
            continue
        if p not in regions:
            regions.append(p)
    return lane, [r for r in regions if (common.VAULT / r).is_dir()]


# ---- the session's own record of what it holds -------------------------------------------

def _state_file(sid: str) -> Path:
    return common.STATE / f"session-start-{common.safe_sid(sid)}.json"


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
        common.write_text_atomic(_state_file(sid), json.dumps(doc, indent=1))   # never seen half written
    except OSError as e:
        log("hook-errors", f"claim\tcannot write {_state_file(sid)}: {e}")


# ---- the two entry points ------------------------------------------------------------------

def _preflight(inp: dict) -> tuple[Path, str, list[str]] | None:
    """(tool, cwd, regions), or None for any of the ordinary reasons to do nothing."""
    tool, state, _why = config.claim_tool_state()
    if state != "ready":
        return None
    cwd = inp.get("cwd") or os.getcwd()
    _lane, regions = _regions_for(cwd)
    if not regions:
        return None
    return tool, cwd, regions


def _off_line(inp: dict) -> str | None:
    """The facts line for a session that is in a region but takes no claim.

    Silence and a held claim look identical from inside a session, and the second is the one worth
    acting on — so where a claim WOULD have been taken and was not, the session is told, in the
    same words `tools/status.py` uses. Outside a region there is nothing to claim and nothing to
    say: the line marks a feature that is off, not every session on earth."""
    _tool, state, why = config.claim_tool_state()
    if state == "ready":
        return None
    _lane, regions = _regions_for(inp.get("cwd") or os.getcwd())
    if not regions:
        return None
    return (f"- Region claim ({', '.join(regions)}): {why} The vault's one-writer-per-region rule "
            f"still holds — coordinate by hand before writing there.")


def _line_file(sid: str) -> Path:
    return common.STATE / f"claim-line-{common.safe_sid(sid)}.txt"


def _say(inp: dict, text: str) -> None:
    """Print the claim line — at UserPromptSubmit only when it differs from the one this session
    was last shown (CONTEXTMSG-1). The same line on every prompt is context spent on nothing; a
    changed holder, a claim won or lost, is news and is printed. SessionStart always prints: a new
    or compacted window has not seen it. Bookkeeping that fails prints, never goes quiet."""
    sid = inp.get("session_id", "-")
    path = _line_file(sid)
    if _event(inp) == "UserPromptSubmit":
        try:
            if path.read_text(encoding="utf-8") == text:
                return
        except OSError:
            pass
    try:
        common.STATE.mkdir(parents=True, exist_ok=True)
        common.write_text_atomic(path, text)
    except OSError as e:
        log("hook-errors", f"claim\tcannot write {path}: {e}")
    if text:
        context(_event(inp), text)


def cmd_start(inp: dict) -> None:
    pre = _preflight(inp)
    if pre is None:
        _say(inp, _off_line(inp) or "")
        return
    tool, cwd, regions = pre
    sid = inp.get("session_id", "-")
    pid, kind = procs.session_process()
    if kind == "thread" and _event(inp) == "SessionStart":
        # A Remote Control host starts a session of its own when it comes up, and that session
        # never has a turn and never leaves: on 2026-09-28 one held a region's lock for 656
        # minutes while other threads failed that claim 36 times. A thread of a host
        # therefore claims at its first PROMPT (UserPromptSubmit runs this same `start`), never at
        # start-up — a session that is never spoken to claims nothing.
        log("claim", f"start\tsid={sid}\tthread={pid}\tskipped at SessionStart: a thread of a "
                     f"host claims at its first prompt")
        return
    if pid is None:
        if kind == "host" and _event(inp) != "UserPromptSubmit":
            log("claim", f"start\tsid={sid}\tself={os.getpid()}\tunder a Remote Control host "
                         f"with no thread process between; claiming nothing (a host is never a holder)")
        elif kind == "host":
            # A host's own session is never spoken to, so a PROMPT that reaches the host means a
            # thread whose process was not recognised: said, not silent.
            log("hook-errors", f"claim\tstart\tsid={sid}\ta prompt under a Remote Control host with "
                               f"no recognised thread process between; claiming nothing")
            _say(inp, f"- Region claim NOT taken for {', '.join(regions)}: this session's "
                      f"process was not recognised under its Remote Control host. Coordinate "
                      f"by hand before writing there.")
        else:
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
            deferred.append((region, rc))
    _write_claims(sid, doc, claims)
    lines = []
    if got:
        lines.append(f"- Region claim held for this session: {', '.join(got)} (pid {pid}); it is "
                     f"released automatically — you do not need to run the claim helper by hand.")
    if deferred:
        # GUARDSILENT-1's second specimen: a session found a holder in the claim table and waited
        # on it — and the holder was ITSELF (CARDKERNEL-2). Name each holder's pid and say when it
        # is this session's own Claude process, so nobody waits on themselves.
        holders = _holders(tool, pid) if any(rc == 1 for _r, rc in deferred) else {}
        mine = [r for r, _rc in deferred if holders.get(r) == pid]
        others = [f"{r} (rc {rc}" + (f", holder pid {holders[r]}" if r in holders else "") + ")"
                  for r, rc in deferred if r not in mine]
        if mine:
            lines.append(f"- Region claim for {', '.join(mine)} is held by THIS session's own Claude "
                         f"process (pid {pid}) — it is yours; do not wait on it.")
        if others:
            lines.append(f"- Region claim NOT held: {', '.join(others)} — another writer holds it. "
                         f"Coordinate before writing there; the lock advises, it does not block. "
                         f"Before waiting on a holder, check its pid is not this session's own "
                         f"({pid}).")
    _say(inp, "\n".join(lines))


def _holders(tool: Path, pid: int) -> dict:
    """{region: holder pid} from the helper's `status` table, or {} when it cannot be read."""
    try:
        rc, out = _run_tool(tool, "status", "-", pid, capture=True)
    except Exception:
        return {}
    got = {}
    for line in (out or "").splitlines():
        m = _STATUS_ROW.match(line)
        if m:
            got[m.group(1)] = int(m.group(2))
    return got


# One row of the helper's table: `printf '%-34s %-25s %-8s …' region mode pid …`. Matched by the
# three modes it prints, never by splitting on spaces — `unknown(treated managed)` holds a space,
# and a region longer than 34 characters pushes every later column to the right.
_STATUS_ROW = re.compile(r"^(.+?)\s+(?:interactive|managed|unknown\(treated managed\))\s+(\d+)\s")


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
