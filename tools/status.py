#!/usr/bin/env python3
"""status.py — the facts about this install, printed by the code that decides them.

    python3 tools/status.py [--session-id SID] [--cwd DIR]

Every line below is produced by CALLING the hook module that owns the answer — `common.lane_for`
for the lane, `commit.dirty_paths`/`commit.is_dirty` for what the Stop hook would commit,
`session_start.boot_chain` for the boot cost, `config` for every path. Nothing is re-derived
here, because a status page that computes its own version of a value is a second implementation,
and the two drift the moment either changes: the number a reader trusts must come from the code
that acts on it (a report's displayed threshold is not the enforcement mechanism's threshold).

With `--session-id` it also prints exactly what this session's Stop hook would commit right now:
the touched set, intersected with the partition, intersected with what git calls dirty.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
import config                      # noqa: E402
import common                      # noqa: E402
import commit as commit_hook       # noqa: E402
import session_start               # noqa: E402
import context_economy             # noqa: E402


def sh(args, timeout=10):
    try:
        p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=timeout)
        return p.returncode, p.stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 124, str(e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print the facts about this Gedächtnis install.")
    ap.add_argument("--session-id", default=None, help="also print what this session's Stop hook would commit")
    ap.add_argument("--cwd", default=None, help="resolve the lane from this directory (default: the current one)")
    a = ap.parse_args(argv)
    cwd = a.cwd or os.getcwd()
    out = []

    lane, prefixes, marker = common.lane_for(cwd)
    out.append(f"vault:      {config.VAULT}" + ("" if config.VAULT.is_dir() else "  (does not exist — run init.py)"))
    out.append(f"state:      {config.STATE}")
    if lane:
        out.append(f"lane:       {lane}   (declared by {marker})")
        out.append(f"partition:  {', '.join(prefixes)}")
    else:
        out.append("lane:       NONE — no .atlas-lane marker resolves from this directory, so this "
                   "session has no write partition and the Stop hook will commit nothing.")

    mode = "warn"
    try:
        v = (config.STATE / "partition.mode").read_text(encoding="utf-8").strip().lower()
        mode = v if v in ("warn", "deny") else "warn"
    except OSError:
        pass
    out.append(f"partition hook: {mode.upper()} " +
               ("(would-be refusals are logged, nothing is blocked)" if mode == "warn"
                else "(a write outside the partition is refused)"))

    _tool, _claim_state, claim_why = config.claim_tool_state()
    out.append(f"region claim: {claim_why}")

    rc, head = sh(["git", "-C", str(config.VAULT), "rev-parse", "--short", "HEAD"])
    dirty = commit_hook.dirty_paths() if (config.VAULT / ".git").exists() else set()
    out.append(f"vault HEAD: {head if rc == 0 else 'no commit yet'};  dirty paths: {len(dirty)}")
    if dirty:
        mine = sorted(p for p in dirty if lane and common.path_in_partition(p, prefixes))
        others = sorted(p for p in dirty if p not in mine)
        if mine:
            out.append("  in this lane's partition: " + ", ".join(mine[:12]) + (" …" if len(mine) > 12 else ""))
        if others:
            out.append("  outside it (not yours to commit): " + ", ".join(others[:8]) + (" …" if len(others) > 8 else ""))

    boot_bytes, boot_files = session_start.boot_chain([config.USER_MEMORY, Path(cwd) / "CLAUDE.md"])
    out.append(f"boot:       {boot_bytes:,} B across {boot_files} file(s) in the @-import chain "
               "(a measurement, not a budget)")

    led = PLUGIN / "ledger.py"
    if lane and led.is_file():
        rc, txt = sh([sys.executable, str(led), "read", "--to", lane, "--unacked", "--json"])
        if rc == 0 and txt:
            try:
                rows = json.loads(txt)
                out.append(f"ledger:     {len(rows)} row(s) addressed to {lane} not yet answered"
                           + (f" ({rows[0]['id']} … {rows[-1]['id']})" if rows else ""))
            except (json.JSONDecodeError, KeyError, IndexError):
                pass

    if a.session_id:
        ce = context_economy.counts(a.session_id)
        out.append(f"context economy: {ce['reread_notices']} re-read notice(s), "
                   f"{ce['bigread_notices']} big-read notice(s) this session")
        touched = common.touched_paths(a.session_id)
        inside = [p for p in touched if lane and common.path_in_partition(p, prefixes)]
        will = [p for p in inside if commit_hook.is_dirty(p, dirty)]
        out.append("")
        out.append(f"this session ({a.session_id}) wrote {len(touched)} vault file(s); "
                   f"{len(inside)} inside the partition.")
        out.append("the Stop hook will commit: " + (", ".join(will) if will else "nothing"))
        left = [p for p in inside if p not in will]
        if left:
            out.append("already committed, nothing left to stage (an earlier Stop of this "
                       "session, or a session that shares the file stopping first): "
                       + ", ".join(left))
        outside = [p for p in touched if p not in inside]
        if outside:
            out.append("written OUTSIDE the partition, so not committed here (the owning lane was "
                       "told through its region's Inbox): " + ", ".join(outside))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
