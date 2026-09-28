#!/usr/bin/env python3
"""trash.py — move files or folders to this machine's Trash (Recycle Bin on Windows).

    python3 tools/trash.py <path> [<path> …]

The command the doors name where the system has no Trash command of its own (Windows, a Linux
without `gio` or `trash-put`, a macOS without `/usr/bin/trash`). It never deletes: a path it
cannot move stays where it is, the reason is printed, and the exit status is 1. A symbolic link is
moved as the link, never its target — or refused where the only route would follow it.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import common  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    rc = 0
    for a in (argv[1:] if argv[0] == "--" else argv):
        ok, words = common.move_to_trash(Path(a))
        print(words, file=sys.stdout if ok else sys.stderr)
        rc = rc if ok else 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
