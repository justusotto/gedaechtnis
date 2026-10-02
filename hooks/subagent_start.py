#!/usr/bin/env python3
"""subagent_start.py — a sub-agent started: its reservation in the fan-out cap becomes a running
entry under its own id (`fanout.record_started`; AGENTCAP-1).

Payload (measured 2026-10-01, Claude Code 2.1.284): `session_id` (the PARENT's), `agent_id`,
`agent_type`. Prints nothing and never fails: bookkeeping must not stop a sub-agent from starting.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fanout
from common import read_input, log, guarded


def main() -> None:
    inp = read_input()
    if not isinstance(inp, dict):                    # a payload that is no object: nothing to record
        return
    sid, aid = inp.get("session_id") or "-", inp.get("agent_id")
    if not isinstance(aid, str) or not aid:
        return
    try:
        fanout.record_started(sid, aid)
    except Exception as e:                           # noqa: BLE001 — see the docstring
        log("hook-errors", f"fanout-start\t{sid}\t{e}".replace("\n", " | "))


if __name__ == "__main__":
    guarded(main)
