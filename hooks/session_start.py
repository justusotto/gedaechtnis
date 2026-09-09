#!/usr/bin/env python3
"""session_start.py — what every session should know before its first prompt, as facts.

Writes <state>/session-start.json (session id, cwd, lane, vault HEAD at start, boot bytes) — the
debriefer reads `vault_head` to scope "what changed this session" to the session instead of the
last commit. Prints one short additionalContext block: the lane and its partition, the
partition-hook mode, vault dirt, what this session's @-import chain cost to boot, and — only when
`owner_pages_status` is configured (see config.py) — that script's answered-pages summary. With no
such script configured the hook says nothing about it at all, rather than guessing where one
might live.

★ THE BOOT-COST FACT. A session cannot see its own boot: the @-imported files arrive as context
with no size attached, so the one number that would tell a session whether its memory files have
quietly become a document is the one number it never has. This hook measures it — the user-level
CLAUDE.md and this cwd's CLAUDE.md, plus everything they @-import, transitively — and states it
as a fact, in bytes and in files. It is a MEASUREMENT and nothing else: no threshold, no warning,
no advice. What a session or a later pass does with the number is a separate decision, and one a
week of recorded numbers should inform rather than a constant chosen today.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import read_input, context, log, lane_for, VAULT, STATE, guarded

EV = "SessionStart"

# An @-import is a line that is nothing but `@` + an absolute-or-tilde path. Anything else — a
# `@-imported` mention in prose, an email address, a decorator, a relative path — is not one.
IMPORT_LINE_RE = re.compile(r"^@([~/][^\s]*)\s*$")

# Claude Code follows @-imports a handful of hops deep. Bounded so a cyclic chain cannot spin;
# the `seen` set already makes a cycle terminate, and this bounds a pathological deep chain too.
MAX_IMPORT_HOPS = 8


def _readable(raw, base=None) -> Path | None:
    """Resolve one @-import target to an existing file, or None. A missing import is SKIPPED —
    Claude Code does not fail a session over one, and neither may this."""
    try:
        p = Path(os.path.expanduser(str(raw)))
        if base and not p.is_absolute():
            p = Path(base) / p
        p = Path(os.path.realpath(str(p)))
        return p if p.is_file() else None
    except OSError:
        return None


def boot_chain(entrypoints, max_hops=MAX_IMPORT_HOPS):
    """-> (total_bytes, n_files) for the closure of `entrypoints` under @-import.

    The entrypoints are COUNTED: they are loaded into the session as surely as anything they
    pull in, and a boot cost that omits them is not the boot cost. Deduplicated by realpath, so
    a file reachable from both chains — the common case for a vault file — is counted once, and
    a symlinked or worktree path cannot become a second name for one file.
    """
    seen, frontier = set(), []
    for entry in entrypoints:
        p = _readable(entry)
        if p and p not in seen:
            seen.add(p)
            frontier.append(p)
    for _ in range(max_hops):
        nxt = []
        for path in frontier:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:                      # an unreadable hop is a missing branch,
                continue                         # never a crash
            for line in text.splitlines():
                m = IMPORT_LINE_RE.match(line.strip())
                if not m:
                    continue
                tgt = _readable(m.group(1))
                if tgt and tgt not in seen:
                    seen.add(tgt)
                    nxt.append(tgt)
        if not nxt:
            break
        frontier = nxt
    total = 0
    for p in seen:
        try:
            total += p.stat().st_size
        except OSError:                          # vanished between the walk and the stat
            pass
    return total, len(seen)


def sh(args, cwd=None, timeout=8):
    try:
        p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 124, "", str(e)


def main() -> None:
    inp = read_input()
    cwd = inp.get("cwd") or os.getcwd()
    sid = inp.get("session_id", "-")
    lane, prefixes, marker = lane_for(cwd)
    rc, head, _ = sh(["git", "-C", str(VAULT), "rev-parse", "HEAD"])
    vault_head = head if rc == 0 else None
    import hashlib as _h
    _key = _h.sha1(cwd.encode()).hexdigest()[:10]
    if inp.get("source") in ("compact", "resume"):
        try:                                            # the session began earlier: keep ITS start head, do not re-stamp
            prev = json.loads((STATE / f"session-start-{_key}.json").read_text(encoding="utf-8"))
            if prev.get("vault_head"):
                vault_head = prev["vault_head"]
        except (OSError, json.JSONDecodeError):
            pass
    rc, dirt, _ = sh(["git", "-C", str(VAULT), "status", "--porcelain"])
    n_dirty = len([l for l in dirt.splitlines() if l.strip()]) if rc == 0 else None
    mode = "warn"
    try:
        v = (STATE / "partition.mode").read_text(encoding="utf-8").strip().lower()
        mode = v if v in ("warn", "deny") else "warn"
    except OSError:
        pass
    boot_bytes, boot_files = boot_chain([config.USER_MEMORY, Path(cwd) / "CLAUDE.md"])
    STATE.mkdir(parents=True, exist_ok=True)
    import hashlib
    key = hashlib.sha1(cwd.encode()).hexdigest()[:10]
    payload = json.dumps({
        "session_id": sid, "cwd": cwd, "lane": lane, "marker": str(marker) if marker else None,
        "vault_head": vault_head, "partition_mode": mode, "boot_bytes": boot_bytes,
        "boot_files": boot_files, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1)
    sid_file = STATE / f"session-start-{sid}.json"
    if not sid_file.exists() or inp.get("source") == "startup":
        sid_file.write_text(payload, encoding="utf-8")                              # per SESSION: two sessions in one repo never collide
    (STATE / f"session-start-{key}.json").write_text(payload, encoding="utf-8")   # per cwd, for a reader that knows only its cwd
    (STATE / "session-start.json").write_text(payload, encoding="utf-8")            # the latest
    lines = ["Gedächtnis session facts (hook-generated):", f"- Session id {sid}; this session's start record is ~/.claude/gedaechtnis/session-start-{sid}.json (pass that path to the debriefer)."]
    if lane:
        lines.append(f"- Lane {lane}, declared by {marker}; vault write partition: {', '.join(prefixes)}.")
    else:
        lines.append("- No .atlas-lane marker resolves from this cwd: this session has NO declared vault write partition (the Stop hook will commit nothing).")
    lines.append(f"- Partition hook mode: {mode.upper()} (" + ("logs would-be refusals to ~/.claude/gedaechtnis/partition.log, blocks nothing" if mode == "warn" else "writes outside the partition are refused") + ").")
    if vault_head:
        lines.append(f"- Vault {VAULT}: HEAD at session start {vault_head[:8]}; dirty paths in the vault right now: {n_dirty}.")
    elif VAULT.is_dir():
        lines.append(f"- Vault {VAULT}: no commit yet" + ("" if (VAULT / ".git").exists() else " (not a git repository)") + ".")
    else:
        lines.append(f"- Vault {VAULT} does not exist: run `python3 {Path(__file__).resolve().parent.parent / 'init.py'}` in this repo to create it.")
    if boot_files:
        lines.append(f"- boot: {boot_bytes:,} B across {boot_files} files (@-import chain) — the user-level "
                     "CLAUDE.md, this repo's, and everything they @-import, transitively. A measurement, not a budget.")
    ops = config.owner_pages_status()                  # None unless config.json names a script
    if ops:
        rc, out, err = sh([config.python(), str(ops), "--json"], timeout=8)
        if rc in (0, 1) and out:                       # the script exits 1 when it has findings; 2 = UNREADABLE
            try:
                j = json.loads(out)
                st = j.get("status") or j.get("verdict") or ""
                counts = {k: v for k, v in j.items() if isinstance(v, int)}
                lines.append(f"- Answered review pages (swept by name): {st or 'see counts'} {json.dumps(counts) if counts else ''}".rstrip())
            except json.JSONDecodeError:
                lines.append(f"- Answered review pages: {ops.name} returned non-JSON; UNMEASURED.")
        elif rc == 2:
            lines.append(f"- Answered review pages: UNREADABLE ({ops.name} exit 2 — a permission-blocked listing looks empty; do not read it as zero).")
        else:
            lines.append(f"- Answered review pages: UNMEASURED ({ops.name} rc={rc}).")
    if lane:
        # Inbox rows in this lane's regions (paths declared as `<Umbrella>/<Region>` dirs)
        for pre in prefixes:
            if pre.count("/") == 1 and not pre.endswith(".md"):
                ib = VAULT / pre / "Inbox.md"
                if ib.is_file():
                    n = sum(1 for l in ib.read_text(encoding="utf-8").splitlines() if l.startswith("- "))
                    if n:
                        lines.append(f"- {pre}/Inbox.md holds {n} unfolded row(s) written by other lanes about work in this region: read and fold them first.")
        led = Path(__file__).resolve().parent.parent / "ledger.py"
        if led.is_file():
            rc, out, _ = sh([sys.executable, str(led), "read", "--to", lane, "--unacked", "--json"], timeout=8)
            if rc == 0 and out.strip():
                try:
                    rows = json.loads(out)
                    if rows:
                        lines.append(f"- Channels ledger: {len(rows)} row(s) addressed to {lane} not yet answered (ids {rows[0]['id']} … {rows[-1]['id']}); they are re-announced every boot until `gedaechtnis/ledger.py ack --from {lane} <id>`.")
                except json.JSONDecodeError:
                    pass
    envf = os.environ.get("CLAUDE_ENV_FILE")
    if envf:
        try:
            with open(envf, "a", encoding="utf-8") as fh:
                fh.write("export PYTHONDONTWRITEBYTECODE=1\n")
            lines.append("- PYTHONDONTWRITEBYTECODE=1 is set for this session's shells: no __pycache__/.pyc droppings inside arcs.")
        except OSError:
            pass
    log("session", f"{sid}\tlane={lane}\tcwd={cwd}\thead={vault_head}")
    context(EV, "\n".join(lines))


if __name__ == "__main__":
    guarded(main)
