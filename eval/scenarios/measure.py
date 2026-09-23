#!/usr/bin/env python3
"""measure.py — instruments over a region, using the PRODUCT's own functions to see what it sees.

An instrument may label; it never decides (the resume wall). Every number here is computed from
`split.region_entries` + `split.cluster` + the `rare` term sets the product itself attaches — no second
tokenizer, no second clustering. What it adds is what the product does not report about a cluster:

  density      share of member PAIRS that share >= MIN_SHARED_TERMS terms. 1.0 = a clique (every entry
               is about the same thing); a percolated component — a chain where each entry resembles a
               neighbour and none resembles the far end — sits low.
  coverage     for the cluster's top-3 weighted shared terms, the share of members carrying at least
               two of them. A topic has a vocabulary most of its members use; a percolated component's
               "shared terms" are the two commonest words in the room.
  per_stem     how the members fall across role files, and what each file would be LEFT with.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "hooks"))


def _product():
    import split, synthesis  # noqa: E402  — imported late so the env seam is set by the caller
    return split, synthesis


def clusters(vault: Path, region: str) -> list[dict]:
    """Every cluster the product finds in `vault/region`, with the three instruments attached.
    Read-only. Uses `split.cluster` directly, so the kinds it reports are the product's under the
    limits the CURRENT process sees (`GEDAECHTNIS_LIMITS`) — with ONE exception, named rather than
    left to be discovered: it passes no boilerplate set, so `split_boilerplate_region_share` is
    the one knob this instrument cannot honour. It reads ONE region and that axis is measured
    across the vault, so there is nothing here to measure it from. Everything it feeds
    (`density`, `coverage`, `parent_clusters_left`) is reported beside the product's own JSON and
    never scored against a `want`, and the scenario that uses it is single-region, where the axis
    is inert anyway — but a number from here is the pre-axis rule's number, and a caller that
    starts scoring on it must supply the set instead."""
    split, synthesis = _product()
    entries, unreadable = split.region_entries(vault / region, vault)
    out = []
    # `cluster()` returns (rooms, chains) since SPLITCLUSTER-1; the chains are not rooms and
    # have nothing to measure here.
    rooms, _chains = split.cluster(entries)
    for c in rooms:
        members = [entries[i] for i in c["members"]]
        k = len(members)
        pairs = sum(1 for i in range(k) for j in range(i + 1, k)
                    if len(members[i]["rare"] & members[j]["rare"]) >= synthesis.MIN_SHARED_TERMS)
        top = c["shared_terms"][:3]
        cover = sum(1 for m in members if sum(1 for t in top if t in m["rare"]) >= 2)
        per_stem: dict[str, dict] = {}
        for e in entries:
            d = per_stem.setdefault(e["stem"], {"total": 0, "moving": 0})
            d["total"] += 1
        for m in members:
            per_stem[m["stem"]]["moving"] += 1
        for d in per_stem.values():
            d["left"] = d["total"] - d["moving"]
        out.append({
            "size": k, "region_entries": len(entries), "remaining": c["remaining"],
            "kind": c["kind"], "top_terms": top,
            "density": round(pairs / (k * (k - 1) / 2), 3) if k > 1 else 1.0,
            "coverage": round(cover / k, 3),
            "per_stem": per_stem,
            "files_emptied": sorted(s for s, d in per_stem.items() if d["total"] and d["left"] == 0),
            "unreadable": unreadable,
        })
    return out
