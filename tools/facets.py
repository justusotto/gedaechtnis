#!/usr/bin/env python3
"""facets.py — list a region's facets and what loads each.

    python3 tools/facets.py <Region>          e.g.  python3 tools/facets.py Studio/Cards

A facet is a `Kernel-<name>.md` file beside the region's Boot file; its frontmatter names the
tools, Bash patterns, paths and row ids that load it (see `hooks/lazy_body.py`). This prints the
same lines the SessionStart facts block shows, from the same function, so the two cannot differ.
Exit 0 with one line per facet; exit 1 with a reason when the region has none.
"""
from __future__ import annotations
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
import lazy_body  # noqa: E402


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    region = args[0].strip("/")
    if not lazy_body.has_boot_file(region):
        print(f"{region}: no Boot file (Kernel.md), so no facets.")
        return 1
    facets = lazy_body.facets_of(region)
    if not facets:
        print(f"{region}: no facet files (Kernel-<name>.md) beside its Boot file.")
        return 1
    for f in facets:
        print(lazy_body.describe(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
