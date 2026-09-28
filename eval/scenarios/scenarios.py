#!/usr/bin/env python3
"""scenarios.py — the split-rule scenario set: shapes, what a sensible reader wants, and how to run each.

Every scenario is (a) a SHAPE or a sequence of shapes, (b) a `want` — what a sensible reader would
expect the pass to propose, written down BEFORE the product was run on it, and (c) a driver that runs
the real `split.py` and summarises its proposals. `want` is judgment, and it is labelled as this arc's:
the owner's page shows the product's proposal beside it and lets the owner overrule the reader.

A `want` of `OWNER` means the case IS the taste question and the harness does not score a delta on it.
A `want` of `no move` is satisfied by `nothing` OR `diffuse` (a finding carried but never applied): the
reader's requirement there is that no entry moves, not that the pass stays silent.

Kinds a summary can carry: `nothing` · `refocus` · `split` · `N x split` · `mixed`. The summary also
keeps the numbers (cluster size, remaining, share, density, coverage) so the tables are not adjectives.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sibling import load as _load  # noqa: E402

def _config():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
    import config
    return config


_vg = _load("vaultgen")
Shape, Topic, generate = _vg.Shape, _vg.Topic, _vg.generate
entries_for, append_entries, from_counts = _vg.entries_for, _vg.append_entries, _vg.from_counts
H = _load("harness")

LOPSIDED_CLUSTER = {"Aporia": 16, "Canon": 40, "Course": 7, "Errata": 34, "Patterns": 66, "Position": 49}
LOPSIDED_REST = {"Aporia": 1, "Canon": 5, "Course": 3, "Errata": 9, "Patterns": 32, "Position": 4}
# Counts only, measured read-only 2026-09-18 with the shipped rule over the region ROW-SPLITRULE-1 §3
# calls the lopsided one (266 entries, cluster 212). No text of that vault is here.

OWNER = "OWNER"


def _summary(found: dict, measured: list[dict] | None = None) -> dict:
    ps = found["proposals"]
    kinds = [p["kind"] for p in ps]
    if not ps:
        label = "nothing"
    elif len(ps) == 1:
        label = kinds[0]
    elif len(set(kinds)) == 1:
        label = f"{len(ps)} x {kinds[0]}"
    else:
        label = "mixed"
    rows = []
    for i, p in enumerate(ps):
        n = p["region_entries"]
        row = {"kind": p["kind"], "size": len(p["entries"]), "n": n, "remaining": p["remaining"],
               "remaining_share": round(p["remaining"] / n, 3) if n else None,
               "name": p["suggested_name"], "id": p["id"]}
        for k in ("cohesion", "min_cohesion", "min_remaining_share"):
            if k in p:
                row[k] = p[k]
        if measured and i < len(measured):
            row["density"] = measured[i]["density"]
            row["coverage"] = measured[i]["coverage"]
            row["files_emptied"] = measured[i]["files_emptied"]
        rows.append(row)
    return {"label": label, "proposals": rows}


def _measure(world: H.World, region: str) -> list[dict]:
    """The instruments, in a child process so the env seam is the sandbox's, never this process's."""
    import json, subprocess, sys
    code = ("import sys, json; sys.path.insert(0, %r); import measure; "
            "print(json.dumps(measure.clusters(__import__('pathlib').Path(%r), %r)))"
            % (str(Path(__file__).resolve().parent), str(world.vault), region))
    p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                       env=world.env(), timeout=300, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return json.loads(p.stdout)


def run_shape(root: Path, shape: Shape, limits: dict) -> dict:
    world = H.World(root)
    manifest = generate(shape, world.vault)
    found = H.propose(world, limits)
    world.set_limits(limits)
    measured = _measure(world, shape.region)
    s = _summary(found, measured)
    s["n"] = manifest["n"]
    s["per_stem"] = manifest["per_stem"]
    return s


def run_steps(root: Path, shapes: list[Shape], limits: dict) -> dict:
    """A region re-generated at each step (prefix-stable, so ids persist). Reports the kind per step,
    whether the SAME cluster id persisted, and how many times the kind FLIPPED."""
    steps, ids, kinds = [], [], []
    for i, shape in enumerate(shapes):
        s = run_shape(root / f"step{i}", shape, limits)
        steps.append(s)
        kinds.append(s["label"])
        ids.append(s["proposals"][0]["id"] if s["proposals"] else None)
    flips = sum(1 for a, b in zip(kinds, kinds[1:]) if a != b)
    return {"label": " > ".join(kinds), "steps": steps, "flips": flips,
            "same_cluster_id_throughout": len({i for i in ids if i}) <= 1}


def run_regrow(root: Path, limits: dict) -> dict:
    """12/8 -> apply the split -> the child is one topic, whole -> grow a SECOND topic inside the child
    to 8 -> the child should now propose exactly that one; the parent (4 noise) proposes nothing, and
    never re-proposes the moved cluster."""
    world = H.World(root)
    base = Shape(topics=[Topic("alpha", 8)], noise=4)
    generate(base, world.vault)
    found = H.propose(world, limits)
    if not found["proposals"] or found["proposals"][0]["kind"] != "split":
        return {"label": f"no split to apply under this rule ({_summary(found)['label']})",
                "applied": False}
    rc, out = H.apply(world, "Proj", found["proposals"][0]["id"], "Alpha", limits)
    after_apply = H.propose(world, limits)
    # Grow the child with a second topic: the words must be fresh, so build a two-topic shape and
    # take only the second topic's entries.
    two = Shape(topics=[Topic("alpha", 8), Topic("beta", 8)], noise=0, region="Proj/Alpha")
    beta = [e for e in entries_for(two) if e.kind == "topic:beta"]
    append_entries(world.vault, "Proj/Alpha", beta)
    regrown = H.propose(world, limits)
    by_region = {}
    for p in regrown["proposals"]:
        by_region.setdefault(p["region"], []).append(
            {"kind": p["kind"], "size": len(p["entries"]), "remaining": p["remaining"]})
    child = by_region.get("Proj/Alpha", [])
    label = ("apply ok > child whole: %s > child regrown: %s; parent: %s" % (
        "nothing" if not after_apply["proposals"] else _summary(after_apply)["label"],
        (child[0]["kind"] + f" {child[0]['size']}->{child[0]['remaining']}") if child else "nothing",
        "nothing" if "Proj" not in by_region else by_region["Proj"]))
    return {"label": label, "applied": rc == 0, "after_apply": _summary(after_apply),
            "regrown": by_region}


def run_apply_all(root: Path, shape: Shape, limits: dict) -> dict:
    """Apply every split the rule offers, one at a time, re-proposing between: what is the parent
    left as, and does the LAST topic ever get a room of its own?"""
    world = H.World(root)
    generate(shape, world.vault)
    trail = []
    for i in range(10):
        found = H.propose(world, limits)
        splits = [p for p in found["proposals"] if p["kind"] == "split"]
        trail.append(_summary(found)["label"])
        if not splits:
            break
        rc, _ = H.apply(world, "Proj", splits[0]["id"], f"Room{i}", limits)
        if rc != 0:
            trail.append(f"apply refused rc={rc}")
            break
    left = _measure(world, "Proj")
    return {"label": " > ".join(trail), "trail": trail, "parent_clusters_left": len(left)}


# ★ THE CONVENTION THE FIXTURE VAULTS WRITE (SPLITVOCAB-1). Three synthetic words standing in for
# `**Scope:** universal`, a NEVER/ALWAYS line and `**Last revisited:**` — the same three in EVERY
# region of the fixture vault, because that is what makes them house style rather than a topic.
# Six letters, so `synthesis.significant_terms` keeps them (it drops anything under five), and
# checked against `recall.STOP` in `vaultgen.make_words`'s taken set rather than assumed.
CONVENTION = ["scopus", "nevera", "revisum"]

# ★ THE SCENARIOS WHOSE WHOLE SUBJECT IS THE VAULT-WIDE AXIS. Declared HERE, beside the place a
# scenario is written, because `run.py` enforces an invariant over them — one of these scoring the
# same with the axis on and with it off is not testing the axis — and a registry kept in the runner
# is a registry the person adding a scenario never sees. It is still a list somebody must extend:
# an axis scenario missing from it is scored like any other, and that is a known hole.
AXIS_SCENARIOS = {"S22", "S23"}


def run_multi(root: Path, shapes: list[Shape], limits: dict, focus: str) -> dict:
    """Several regions in ONE vault; scored on `focus` alone.

    The vault-wide boilerplate axis is a statement about SEVERAL rooms — a term is house style
    because many regions write it — so a one-region fixture cannot exercise it at all, in either
    direction. The siblings are not decoration: they are the evidence the measurement reads."""
    world = H.World(root)
    manifests = {}
    for shape in shapes:
        manifests[shape.region] = generate(shape, world.vault)
    found = H.propose(world, limits)
    mine = dict(found, proposals=[p for p in found["proposals"] if p["region"] == focus])
    s = _summary(mine)
    s["n"] = manifests[focus]["n"]
    s["per_stem"] = manifests[focus]["per_stem"]
    s["regions"] = found["regions"]
    s["boilerplate_carriers"] = manifests[focus]["boilerplate_carriers"]
    # What the product measured as house style, and against what bar — reported so a table can
    # show the axis SILENT (a rule without it echoes nothing) as different from the axis FINDING
    # NOTHING.
    s["boilerplate_bar"] = found.get("boilerplate_bar")
    s["boilerplate_terms"] = found.get("boilerplate_terms")
    s["boilerplate_voters"] = found.get("boilerplate_voters")
    # ★ THE FIXTURE'S OWN PRECONDITION, ASSERTED RATHER THAN DESCRIBED (row reviewer, MUST-FIX 2).
    # Both of these scenarios are about a convention, and if the generator stops writing one they
    # do not fail — they pass, under BOTH variants, and the sweep prints the tally the row was
    # accepted on. That is the same defect the row exists to fix, rebuilt inside the controls added
    # to fix it: a check whose trigger condition never occurs cannot fail, and its green is a fact
    # about the fixture. `boilerplate_carriers` was already being counted from the written text and
    # nothing read it.
    if not s["boilerplate_carriers"]:
        s["error"] = "VACUOUS: the fixture wrote no convention — this scenario tests nothing"
    # ★ The second arm asks the PRODUCT whether the axis was on (`boilerplate_bar` is 0 only when
    # it is off), never the variant dict: a variant that leaves the knob at its DEFAULT sets
    # nothing, so reading the dict diagnosed "the siblings are not writing the convention" on a run
    # where the axis was simply off and the fixture was fine. An arm added to stop a check lying
    # about its fixture was lying about the fixture.
    elif found.get("boilerplate_bar") and not s["boilerplate_terms"]:
        s["error"] = ("VACUOUS: the axis is ON and measured NO house style — the siblings are not "
                      "writing the fixture's convention")
    s["other_regions"] = sorted({p["region"] for p in found["proposals"] if p["region"] != focus})
    # ★ WHAT THE ROOM IS CALLED IS HALF THE DEFECT, and the label a scenario is scored on cannot
    # see it: "split 15->5" and "split 10->10" are both `split`. This is the product's own
    # `shared_terms` — the list the name is built from and the list `apply()` writes into the new
    # region's Map.md — checked against the convention the fixture wrote.
    s["named_from_convention"] = any(t in CONVENTION for p in mine["proposals"]
                                     for t in p["shared_terms"][:2])
    s["names"] = [p["suggested_name"] for p in mine["proposals"]]
    s["terms"] = [p["shared_terms"][:5] for p in mine["proposals"]]
    return s


def _convention_vault(focus: Shape) -> list[Shape]:
    """`focus` plus two sibling regions that write the same convention and nothing else in common.

    Two, because the axis needs three regions before it will answer at all (`BOILERPLATE_MIN_REGIONS`
    in split.py) — a bar expressed as a share of regions would otherwise read `ceil(0.25 * 1) = 1`
    on the one-region vault this package creates by default and call every repeated word furniture.
    Each sibling is one topic plus noise, every entry carrying the convention: they are quiet (a
    region whose every entry is welded into one component is the whole region, which `cluster()`
    refuses as not a sub-region of itself) and they are what makes the convention measurable."""
    return [focus,
            Shape(topics=[Topic("sibone", 9)], noise=6, region="SibOne", seed=41,
                  boilerplate=CONVENTION),
            Shape(topics=[Topic("sibtwo", 9)], noise=6, region="SibTwo", seed=57,
                  boilerplate=CONVENTION)]


def run_real_vault(root: Path, limits: dict, vault: Path | None = None) -> dict:
    """The owner's live vault, READ-ONLY, region names hashed. `propose()` writes nothing; the state
    dir and limits file are the sandbox's. Reported, never scored: there is no `want` for a region the
    harness did not shape."""
    # The real vault this run should read is NAMED, never guessed: SPLITSIM_REAL_VAULT, else the
    # installation's configured vault. A literal path here would be one machine's.
    raw = os.environ.get("SPLITSIM_REAL_VAULT")
    vault = vault or (Path(os.path.expanduser(raw)) if raw else _config().vault())
    if not vault.is_dir():
        return {"label": "UNMEASURED — no vault at that path", "proposals": []}
    world = H.World(root, vault=vault)
    found = H.propose(world, limits)
    rows = []
    for p in found["proposals"]:
        h = hashlib.sha1(p["region"].encode("utf-8")).hexdigest()[:8]
        n = p["region_entries"]
        row = {"region": f"r-{h}", "kind": p["kind"], "size": len(p["entries"]), "n": n,
               "remaining": p["remaining"], "remaining_share": round(p["remaining"] / n, 3)}
        for k in ("cohesion",):
            if k in p:
                row[k] = p[k]
        rows.append(row)
    kinds = [r["kind"] for r in rows]
    return {"label": f"{len(rows)} proposals: {kinds.count('split')} split, "
                     f"{kinds.count('refocus')} refocus", "proposals": rows,
            "regions": found["regions"], "regions_checked": found["regions_checked"]}


def snapshot_shapes() -> list[Shape]:
    """The real vault's 13 candidate clusters as CLIQUE shapes from counts (ROW-SPLITRULE-1 §3 + the
    2026-09-18 read-only measurement), labels hashed. Reproducible in the package without vault text."""
    import json
    data = json.loads((Path(__file__).with_name("real_shape_2026-09-18.json")).read_text("utf-8"))
    return [from_counts(r["label"], r["cluster"], r["rest"]) for r in data["regions"]]


def run_snapshot(root: Path, limits: dict) -> dict:
    rows = []
    for shape in snapshot_shapes():
        s = run_shape(root / shape.region, shape, limits)
        rows.append({"region": shape.region, "label": s["label"],
                     **({k: s["proposals"][0][k] for k in ("size", "remaining", "remaining_share")}
                        if s["proposals"] else {})})
    kinds = [r["label"] for r in rows]
    return {"label": f"{len(rows)} regions: {kinds.count('split')} split, "
                     f"{kinds.count('refocus')} refocus, {kinds.count('nothing')} nothing",
            "regions": rows}


# ----------------------------------------------------------------------------------- the set ----
def _growth_monotone() -> list[Shape]:
    return [Shape(topics=[Topic("alpha", 10 + 2 * i)], noise=6, seed=11) for i in range(8)]


def _growth_flap() -> list[Shape]:
    """A region hovering at a 20% parent share: topic and noise arrive in turns so the share crosses
    0.20 in both directions — 8/38 = .21, 8/40 = .20, 9/40 = .225, 9/45 = .20, 10/45 = .222,
    10/50 = .20, 11/50 = .22, 11/56 = .196, 12/56 = .214, 12/62 = .194."""
    pairs = [(30, 8), (32, 8), (32, 9), (36, 9), (36, 10), (40, 10), (40, 11), (45, 11), (45, 12),
             (50, 12)]
    return [Shape(topics=[Topic("alpha", t)], noise=n, seed=23) for t, n in pairs]


SCENARIOS = [
    # id, title, want, rationale, driver
    ("S01", "tiny region, 5 entries, one topic", "nothing",
     "Five notes are not a room; nothing should be proposed.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 5)]), lim)),
    ("S02", "tiny region, 9 entries, ALL one topic", "nothing",
     "The region IS the topic and has no leftover; there is nothing to move and nothing to rename.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 9)]), lim)),
    ("S03", "tiny region, 6 topic + 3 other", "nothing",
     "No cluster reaches the child bar.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 6)], noise=3), lim)),
    ("S04", "the n-1 case: 8 topic + 1 other", "refocus",
     "ROW-R9 §7. Moving 8 of 9 leaves an orphan; the region is the topic under a better name.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 8)], noise=1), lim)),
    ("S05", "at the floor: 8 topic + 4 other (parent keeps 4 = 33%)", "split",
     "A small topic room and a small parent; splitting is defensible and the floor is met exactly.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 8)], noise=4), lim)),
    ("S06", "one under the floor: 8 topic + 3 other (parent keeps 3 = 27%)", "refocus",
     "Three stray notes are not a region; the reader would rename rather than move.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 8)], noise=3), lim)),
    ("S07", "lopsided, COHESIVE: 212 of 266 on one topic (the lopsided real-region shape, clique)", "refocus",
     "80% of the room is one topic. Moving 212 out and leaving 54 renames the region by the back "
     "door; the honest proposal is that the region IS this topic (and the 54 are the odd ones).",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("lopsided", 212, spread=LOPSIDED_CLUSTER)],
                                             noise=54, noise_spread=LOPSIDED_REST), lim)),
    ("S08", "lopsided, PERCOLATED: 212 of 266 in one component, pair density ~0.13 (the real shape)",
     "no move",
     "Each entry resembles a few neighbours and none resembles the far end. That is not a topic; a "
     "rule that proposes moving 212 entries under a name made of the room's two commonest words "
     "is wrong whatever the floor says.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("lopsided", 212, vocab=16, k=5,
                                                           spread=LOPSIDED_CLUSTER)],
                                             noise=54, noise_spread=LOPSIDED_REST), lim)),
    ("S09", "three equal topics of 10 + 6 other (36)", "3 x split",
     "Three rooms have formed; each should be offered, each leaving a healthy parent.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("a", 10), Topic("b", 10), Topic("c", 10)],
                                             noise=6), lim)),
    ("S10", "three equal topics of 8, NO other (24) — then apply every split offered", "2 x split then nothing",
     "Two topics leave; the third IS the remaining room. A rule that offers the third a room too "
     "would empty the parent.",
     lambda root, lim: run_apply_all(root, Shape(topics=[Topic("a", 8), Topic("b", 8), Topic("c", 8)]),
                                     lim)),
    ("S11", "uneven spread: 12 topic entries ALL in Errata + 10 other across the rest (22)", "split",
     "A topic that lives in one role file is still a topic; the parent keeps 10. Errata.md is left "
     "with zero entries — which is reported, and is not an orphan region.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 12, spread={"Errata": 12})],
                                             noise=10, noise_spread={"Patterns": 4, "Canon": 3,
                                                                     "Position": 2, "Aporia": 1}), lim)),
    ("S12", "small topic in a big room: 8 of 40 (parent keeps 32 = 80%)", "split",
     "The classic case the rule exists for.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 8)], noise=32), lim)),
    ("S13", "count says yes, share says no: 34 of 40 (parent keeps 6 = 15%)", OWNER,
     "Six real entries remain — over any count floor up to 6 — but the room is 85% one topic. This "
     "is the bar question itself, and the owner's page asks it.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 34)], noise=6), lim)),
    ("S14", "growth, monotone: 10+6 then +2 topic entries per step, 8 steps (16 -> 30)", "stable",
     "The parent keeps 6 throughout; its SHARE falls 37% -> 20%. A sensible proposal does not "
     "change kind because the child grew; if a rule flips here it flips on the wrong side.",
     lambda root, lim: run_steps(root, _growth_monotone(), lim)),
    ("S15", "growth, hovering at 20%: topic and other arrive in turns, 10 steps", "stable",
     "The same region a week apart should get the same answer. Flips = how often a reader would "
     "see the proposal change kind with no change in what the region IS.",
     lambda root, lim: run_steps(root, _growth_flap(), lim)),
    ("S16", "an already-split child regrows a second topic", "child: split 8->8; parent: nothing",
     "The child (8, whole) proposes nothing until a second topic forms; then it proposes exactly that "
     "one; the parent never re-proposes what already moved.",
     run_regrow),
    ("S17", "percolation by bridges: 8 + 8 joined by 2 entries that belong to both, + 6 other (24)",
     "2 x split",
     "Two topics, two rooms. The union-find merges them through the bridges into one 18-entry "
     "'topic' named from both vocabularies — never what a reader would call one room.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("a", 8), Topic("b", 8)], noise=6,
                                             bridges=[(0, 1, 2)]), lim)),
    ("S18", "two topics, one under the child bar: 8 + 5 + 10 other (23)", "split",
     "One room has formed (8); the five are not yet one. One proposal, parent keeps 15.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("a", 8), Topic("b", 5)], noise=10), lim)),
    ("S21", "a BROAD-VOCABULARY topic: 40 entries drawing 5 of 12 words each + 10 other (50)", "split",
     "One subject with a wide vocabulary — every entry is about it, few pairs share three words. "
     "The NEGATIVE CONTROL for any cohesion gate: what it costs when it is too tight.",
     lambda root, lim: run_shape(root, Shape(topics=[Topic("alpha", 40, vocab=12, k=5)], noise=10),
                                 lim)),
    ("S22", "boilerplate-only cohesion: two unrelated 6-entry topics + 8 other, EVERY topic entry "
     "carrying the same three convention words the whole vault writes (3 regions)", "no move",
     "The positive control for the vault-wide axis, and the shape that refuted the suite: the two "
     "topics have disjoint vocabularies and nothing to do with each other, and the convention "
     "welds them into one 12-entry 'room' whose core vocabulary IS the convention and whose name "
     "is made of it. Neither topic reaches the child bar on its own, so the honest answer is that "
     "there is nothing here to move. Without this scenario the fix cannot be shown to bite: no "
     "other scenario contains a convention at all.",
     lambda root, lim: run_multi(root, _convention_vault(
         Shape(topics=[Topic("alpha", 6), Topic("beta", 6)], noise=8, region="Proj",
               boilerplate=CONVENTION, boilerplate_share=0.6)), lim, "Proj")),
    ("S23", "a REAL topic of 10 in a 20-entry region, the topic AND five others carrying the same "
     "convention (3 regions)", "split on its own vocabulary",
     "The negative control, and the expensive direction: a vault-wide stoplist set too aggressively "
     "silences real topics that happen to be written in house style, and that failure is SILENT — "
     "the reader is not shown a room that was never proposed. The topic must still be offered, and "
     "named from its OWN vocabulary rather than from the convention it shares with five strangers. "
     "Scored on the NAME as well as the kind: without the axis this scenario is `split` too — of "
     "the topic plus the five strangers, called after the convention — so a control that reads only "
     "the kind cannot fail here and is decoration.",
     lambda root, lim: run_multi(root, _convention_vault(
         Shape(topics=[Topic("gamma", 10)], noise=10, region="Proj",
               boilerplate=CONVENTION, boilerplate_share=0.75)), lim, "Proj")),
    ("S19", "the owner's live vault, READ-ONLY, names hashed (reported, not scored)", None,
     "What the rule says about the real thing today, per variant. Counts only.",
     run_real_vault),
    ("S20", "snapshot of the live vault's 13 candidates as clique shapes from counts (reproducible)",
     None,
     "The same 13 regions rebuilt from counts alone, cohesive; the package can run this without "
     "the vault.",
     run_snapshot),
]
