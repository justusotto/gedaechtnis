#!/usr/bin/env python3
"""session_start.py — what every session should know before its first prompt, as facts.

Writes <state>/session-start.json (session id, cwd, lane, vault HEAD at start) — the debriefer
reads `vault_head` to scope "what changed this session" to the session instead of the last commit.
Prints one short additionalContext block: the lane and its partition, the partition-hook mode,
vault dirt, and the owner-answers summary (~/Downloads swept BY NAME — an empty result is
UNREADABLE, never zero; the script that knows this is owner_pages_status.py).
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import read_input, context, log, lane_for, VAULT, STATE, HOME, guarded

EV = "SessionStart"
OPS = HOME / "PycharmProjects" / "atlas-system" / "scripts" / "owner_pages_status.py"
PY = HOME / "PycharmProjects" / "atlas-system" / ".venv" / "bin" / "python"


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
    rc, dirt, _ = sh(["git", "-C", str(VAULT), "status", "--porcelain"])
    n_dirty = len([l for l in dirt.splitlines() if l.strip()]) if rc == 0 else None
    mode = "warn"
    try:
        v = (STATE / "partition.mode").read_text(encoding="utf-8").strip().lower()
        mode = v if v in ("warn", "deny") else "warn"
    except OSError:
        pass
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "session-start.json").write_text(json.dumps({
        "session_id": sid, "cwd": cwd, "lane": lane, "marker": str(marker) if marker else None,
        "vault_head": vault_head, "partition_mode": mode, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1), encoding="utf-8")
    lines = ["Gedächtnis session facts (hook-generated):"]
    if lane:
        lines.append(f"- Lane {lane}, declared by {marker}; vault write partition: {', '.join(prefixes)}.")
    else:
        lines.append("- No .atlas-lane marker resolves from this cwd: this session has NO declared vault write partition (the Stop hook will commit nothing).")
    lines.append(f"- Partition hook mode: {mode.upper()} (" + ("logs would-be refusals to ~/.claude/gedaechtnis/partition.log, blocks nothing" if mode == "warn" else "writes outside the partition are refused") + ").")
    if vault_head:
        lines.append(f"- Vault HEAD at session start: {vault_head[:8]}; dirty paths in the vault right now: {n_dirty}.")
    if OPS.is_file():
        py = str(PY) if PY.is_file() else "python3"
        rc, out, err = sh([py, str(OPS), "--json"], timeout=8)
        if rc == 0 and out:
            try:
                j = json.loads(out)
                st = j.get("status") or j.get("verdict") or ""
                counts = {k: v for k, v in j.items() if isinstance(v, int)}
                lines.append(f"- Owner answers in ~/Downloads (by name): {st or 'see counts'} {json.dumps(counts) if counts else ''}".rstrip())
            except json.JSONDecodeError:
                lines.append("- Owner answers in ~/Downloads: owner_pages_status.py returned non-JSON; UNMEASURED.")
        elif rc == 2:
            lines.append("- Owner answers in ~/Downloads: UNREADABLE (owner_pages_status.py exit 2 — a TCC-blocked listing looks empty; do not read as zero).")
        else:
            lines.append(f"- Owner answers in ~/Downloads: UNMEASURED (owner_pages_status.py rc={rc}).")
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
            rc, out, _ = sh([sys.executable, str(led), "read", "--to", lane, "--since-cursor", "--json"], timeout=8)
            if rc == 0 and out.strip():
                try:
                    rows = json.loads(out)
                    if rows:
                        lines.append(f"- Channels ledger: {len(rows)} new row(s) addressed to {lane} since the last boot (ids {rows[0]['id']} … {rows[-1]['id']}); reply with `gedaechtnis/ledger.py ack --from {lane} <id>`.")
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
