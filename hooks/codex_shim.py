#!/usr/bin/env python3
"""Codex payload shim — normalise a Codex hook payload, then run the real door.

WHY A SHIM AND NOT AN EDIT TO gate.py
-------------------------------------
Under Codex the shell tool arrives Claude-identical, but the edit tool does not: it is
`apply_patch`, and its whole payload is one patch envelope in `tool_input.command`, with no
`file_path`. Every door that reads `file_path` would therefore find nothing to object to and
ALLOW the write -- a door that cannot see its subject, failing open and silently.

This shim sits in front of the existing doors instead of changing them: it reads the hook
payload on stdin, hands it to `codex_bridge.normalize_tool_input` (a no-op for every tool
whose payload is already Claude-shaped), and re-invokes the real door with the rewritten
JSON. The doors keep one payload contract and stay unaware of which harness they are in;
this file is the only thing that knows.

Usage (from a generated Codex hooks.json):
    python3 -B <plugin>/hooks/codex_shim.py gate.py write
Exit code, stdout and stderr are passed through unchanged, so `exit 2` still blocks with the
reason the door wrote.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def main() -> int:
    if len(sys.argv) < 2:
        return 0                      # nothing to run: never block on our own misuse
    target = HERE / sys.argv[1]
    if not target.is_file():
        return 0
    raw = sys.stdin.read()
    try:
        inp = json.loads(raw) if raw.strip() else {}
    except ValueError:
        # A payload we cannot parse is not ours to judge; hand it on untouched rather than
        # block a session on a schema surprise.
        inp = None
    if inp is not None:
        try:
            import codex_bridge
            raw = json.dumps(codex_bridge.normalize_tool_input(inp))
        except Exception:
            pass                      # normalisation is best-effort; never block on it
    proc = subprocess.run([sys.executable, "-B", str(target), *sys.argv[2:]],
                          input=raw, text=True, capture_output=True)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
