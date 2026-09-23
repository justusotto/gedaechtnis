"""test_scenarios.py — the split-rule scenario harness (`eval/scenarios/`).

The harness makes three kinds of claim and each gets a POSITIVE and a NEGATIVE control:

  1. the GENERATOR writes the shape it was asked for — the product's own proposal names exactly the
     entries the shape called a topic, noise never clusters, bridges merge and their absence does not;
  2. the RUNNER exercises the product and nothing else — a knob the product does not echo is REFUSED,
     a knob it does echo comes back with the value the run set, and the `no_topics` mutant turns every
     proposal off (if a table still showed a split under it, it was not read from the product);
  3. the SCORER says WRONG when the product disagrees with the scenario's want — and `ok` when it
     agrees — on both a split and a growth (flips) scenario.

Nothing here touches the real vault: every world is a tmp_path; `harness.World.env()` clears every
inherited `GEDAECHTNIS_*` name.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

import importlib.util

PLUGIN = Path(__file__).resolve().parents[1]
SCEN = PLUGIN / "eval" / "scenarios"


def _load(name: str, alias: str):
    """Load `eval/scenarios/<name>.py` under a UNIQUE module name. `test_simulator.py` already imports
    the growth simulator's `run.py` as `run`, so a plain `import run` here would silently get THAT
    module — 14 tests failed that way on the first full-suite run (`module 'run' has no attribute
    'score'`). The scenario modules import each other by bare name, so the path is inserted first."""
    if str(SCEN) not in sys.path:
        sys.path.insert(0, str(SCEN))
    spec = importlib.util.spec_from_file_location(alias, SCEN / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


V = _load("vaultgen", "splitsim_vaultgen")
H = _load("harness", "splitsim_harness")
SC = _load("scenarios", "splitsim_scenarios")
R = _load("run", "splitsim_run")


def _world(tmp_path, shape):
    w = H.World(tmp_path)
    m = V.generate(shape, w.vault)
    return w, m


# ------------------------------------------------------------- 1. the generator ----
def test_the_product_proposes_exactly_the_entries_the_shape_called_a_topic(tmp_path):
    w, m = _world(tmp_path, V.Shape(topics=[V.Topic("alpha", 8)], noise=4))
    found = H.propose(w)
    assert len(found["proposals"]) == 1
    got = {e["heading"] for e in found["proposals"][0]["entries"]}
    assert got == set(m["headings"]["topic:alpha"])
    assert found["proposals"][0]["remaining"] == 4


def test_noise_never_clusters(tmp_path):
    w, _ = _world(tmp_path, V.Shape(noise=40))
    assert H.propose(w)["proposals"] == []


def test_two_topics_stay_two_proposals_even_when_a_few_entries_belong_to_BOTH(tmp_path):
    """★ THIS TEST USED TO ASSERT THE DEFECT, and inverting it is what SPLITCLUSTER-1 delivers.

    Its previous form ended `== [18]` — two 8-entry topics joined by 2 shared entries were welded
    by the pairwise rule into one 18-entry "topic", and the test pinned that as correct because it
    was what the code did. It is not what a reader would call one room, and the name such a cluster
    gets is made of both vocabularies. Scenario S17 scores the same shape independently.

    The two shared entries appear in BOTH proposals, which is the honest answer: they belong to
    both topics, `apply()` recomputes the region from disk, and whichever room the reader creates
    first is the one that takes them."""
    w, _ = _world(tmp_path / "apart", V.Shape(topics=[V.Topic("a", 8), V.Topic("b", 8)], noise=6))
    assert [len(p["entries"]) for p in H.propose(w)["proposals"]] == [8, 8]
    w2, _ = _world(tmp_path / "bridged", V.Shape(topics=[V.Topic("a", 8), V.Topic("b", 8)], noise=6,
                                                 bridges=[(0, 1, 2)]))
    sizes = sorted(len(p["entries"]) for p in H.propose(w2)["proposals"])
    assert sizes == [10, 10], f"the bridged pair came back as {sizes}, not two rooms"
    # and the bridges really are the overlap, not two arbitrary rooms of the right size
    a, b = (set(e["heading"] for e in p["entries"]) for p in H.propose(w2)["proposals"])
    assert len(a & b) == 2, sorted(a & b)


def test_a_pair_bridged_by_MORE_than_the_knob_allows_is_still_reported_as_one(tmp_path):
    """The blind spot, asserted rather than left for someone to discover.

    The cut search looks for a separation of at most `split_max_bridge_entries` entries. At the
    shipped 3, a pair welded by 4 is invisible and comes back as one cluster — measured, and the
    limits prose says so. Raising the knob to 4 finds it, which is the proof that the limit is the
    reason and not some other property of the shape."""
    shape = V.Shape(topics=[V.Topic("a", 8), V.Topic("b", 8)], noise=6, bridges=[(0, 1, 4)])
    w, _ = _world(tmp_path / "wide", shape)
    assert [len(p["entries"]) for p in H.propose(w)["proposals"]] == [20]
    w2, _ = _world(tmp_path / "wide2", shape)
    sizes = sorted(len(p["entries"])
                   for p in H.propose(w2, {"split_max_bridge_entries": 4})["proposals"])
    assert sizes == [12, 12], sizes


def test_the_cohesion_dial_moves_the_products_own_measurement(tmp_path):
    """`vocab == k` is a clique; `vocab > k` is sparser. Read off the product's `cohesion` field."""
    # ★ THE SPARSE ARM MOVED, and why: it used to be `vocab=16, k=5`, which SPLITCLUSTER-1 now
    # refuses outright — at that density not one term is carried by half the entries, so there is
    # no core vocabulary and no cluster to read a cohesion off. That is the row's point, not a
    # regression, and scenario S08 scores it. The dial itself is unchanged, so the test keeps its
    # question and moves to a sparse topic that still IS a topic: `vocab=12, k=5` (scenario S21,
    # the negative control for any cohesion gate) measures 0.45-0.55 against a clique's 1.0.
    w1, _ = _world(tmp_path / "clique", V.Shape(topics=[V.Topic("a", 40)], noise=10))
    w2, _ = _world(tmp_path / "sparse", V.Shape(topics=[V.Topic("a", 40, vocab=12, k=5)], noise=10))
    c1 = H.propose(w1)["proposals"][0]["cohesion"]
    c2 = H.propose(w2)["proposals"][0]["cohesion"]
    assert c1 == 1.0 and c2 < 0.8, (c1, c2)
    assert c2 > 0.0, "the sparse arm must still BE a cluster, or the dial is reading nothing"


def test_a_component_with_NO_core_vocabulary_is_not_proposed_at_all(tmp_path):
    """★ The 212-entry case that made SPLITCLUSTER-1 exist.

    At `vocab=16, k=5` each entry resembles a few neighbours and nothing is common to the whole:
    measured over 10 seeds, ZERO terms are carried by half the component. The old rule offered to
    move all 212 entries under a name built from the room's two commonest words. No count, share
    or cohesion floor fixes that — they only silence it — which is why the test is that NOTHING is
    proposed, and why the positive control below matters more than usual."""
    w, _ = _world(tmp_path / "chain", V.Shape(topics=[V.Topic("a", 212, vocab=16, k=5)], noise=54))
    assert H.propose(w)["proposals"] == []
    # POSITIVE CONTROL: the same size and the same generator, one subject — must still propose.
    w2, _ = _world(tmp_path / "real", V.Shape(topics=[V.Topic("a", 212)], noise=54))
    assert [len(p["entries"]) for p in H.propose(w2)["proposals"]] == [212]


def test_an_exact_spread_lands_exactly(tmp_path):
    shape = V.Shape(topics=[V.Topic("a", 12, spread={"Errata": 12})], noise=10,
                    noise_spread={"Patterns": 4, "Canon": 3, "Position": 2, "Aporia": 1})
    _, m = _world(tmp_path, shape)
    assert m["per_stem"] == {"Errata": 12, "Patterns": 4, "Canon": 3, "Position": 2, "Aporia": 1}


def test_a_spread_that_does_not_sum_is_refused():
    with pytest.raises(ValueError):
        V.entries_for(V.Shape(topics=[V.Topic("a", 5, spread={"Errata": 4})]))


def test_growing_a_topic_keeps_its_earlier_entries_byte_identical():
    """Prefix stability: the same region N entries later carries the same earlier entries, so a
    growth scenario compares like with like."""
    a = [e for e in V.entries_for(V.Shape(topics=[V.Topic("a", 10)], noise=6, seed=3))
         if e.kind == "topic:a"]
    b = [e for e in V.entries_for(V.Shape(topics=[V.Topic("a", 14)], noise=6, seed=3))
         if e.kind == "topic:a"]
    # Order-independent: the generator interleaves topics with noise on purpose.
    ta, tb = sorted(e.text() for e in a), sorted(e.text() for e in b)
    assert len(ta) == 10 and len(tb) == 14
    assert set(ta) <= set(tb)


def test_the_snapshot_carries_counts_only():
    data = json.loads((PLUGIN / "eval" / "scenarios" / "real_shape_2026-09-18.json").read_text("utf-8"))
    assert len(data["regions"]) == 13
    for r in data["regions"]:
        assert re.fullmatch(r"r-[0-9a-f]{8}", r["label"]), r["label"]
        assert set(r["cluster"]) <= set(V.STEMS) and set(r["rest"]) <= set(V.STEMS)
        assert all(isinstance(v, int) for v in list(r["cluster"].values()) + list(r["rest"].values()))
    shapes = SC.snapshot_shapes()
    assert [sum(t.n for t in s.topics) + s.noise for s in shapes] == [r["n"] for r in data["regions"]]


def test_from_counts_reproduces_the_measured_shape(tmp_path):
    shape = V.from_counts("r-test", {"Errata": 5, "Canon": 3}, {"Patterns": 2, "Position": 2})
    w, m = _world(tmp_path, shape)
    assert m["per_stem"] == {"Errata": 5, "Canon": 3, "Patterns": 2, "Position": 2}
    p = H.propose(w)["proposals"][0]
    assert (len(p["entries"]), p["remaining"]) == (8, 4)


# ---------------------------------------------------------------- 2. the runner ----
def test_a_knob_the_product_does_not_echo_is_refused(tmp_path):
    w, _ = _world(tmp_path, V.Shape(topics=[V.Topic("a", 8)], noise=4))
    H.ECHO["split_no_such_knob"] = "no_such_echo"
    try:
        with pytest.raises(H.UnsupportedKnob):
            H.propose(w, {"split_no_such_knob": 1})
    finally:
        del H.ECHO["split_no_such_knob"]


def test_a_knob_the_product_echoes_comes_back_with_the_value_set(tmp_path):
    w, _ = _world(tmp_path, V.Shape(topics=[V.Topic("a", 8)], noise=1))
    assert H.propose(w, {"split_min_remaining_entries": 1})["min_remaining"] == 1
    assert H.propose(w, {"split_min_remaining_entries": 1})["proposals"][0]["kind"] == "split"
    assert H.propose(w, {"split_min_remaining_entries": 4})["proposals"][0]["kind"] == "refocus"


def test_the_env_seam_clears_every_inherited_plugin_name(tmp_path, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_SOMETHING_NEW", "leak")
    monkeypatch.setenv("GEDAECHTNIS_LIMITS", "/nowhere/limits.json")
    e = H.World(tmp_path).env()
    assert "GEDAECHTNIS_SOMETHING_NEW" not in e
    assert e["GEDAECHTNIS_LIMITS"] == str(tmp_path / "limits.json")


def test_the_no_topics_mutant_silences_every_proposal(tmp_path, monkeypatch):
    """The harness's own bite check: under the mutant the generator writes no shared vocabulary, so
    the n-1 scenario must come back `nothing`. Without it, `refocus`."""
    root = tmp_path / "plain"
    assert SC.run_shape(root, V.Shape(topics=[V.Topic("a", 8)], noise=1),
                        {"split_min_remaining_entries": 4})["label"] == "refocus"
    monkeypatch.setenv(R.MUTANTS["no_topics"], "1")
    root2 = tmp_path / "mutant"
    assert SC.run_shape(root2, V.Shape(topics=[V.Topic("a", 8)], noise=1),
                        {"split_min_remaining_entries": 4})["label"] == "nothing"


def test_the_regrow_driver_applies_a_real_split_and_sees_the_child_as_a_region(tmp_path):
    out = SC.run_regrow(tmp_path, {"split_min_remaining_entries": 4})
    assert out["applied"] is True
    assert out["after_apply"]["label"] == "nothing"
    assert "child regrown: split 8->8" in out["label"] and "parent: nothing" in out["label"]
    assert (tmp_path / "vault" / "Proj" / "Alpha" / "SPLIT-RECEIPT.md").is_file()


def test_the_real_vault_driver_reports_unmeasured_when_there_is_no_vault(tmp_path):
    out = SC.run_real_vault(tmp_path, {"split_min_remaining_entries": 4},
                            vault=tmp_path / "no-such-vault")
    assert out["label"].startswith("UNMEASURED")


# ---------------------------------------------------------------- 3. the scorer ----
def test_the_scorer_says_wrong_when_the_product_disagrees_and_ok_when_it_agrees():
    assert R.score("refocus", {"label": "split", "proposals": []}) == "WRONG"
    assert R.score("refocus", {"label": "refocus", "proposals": []}) == "ok"
    assert R.score("no move", {"label": "diffuse", "proposals": []}) == "ok"
    assert R.score("no move", {"label": "split", "proposals": []}) == "WRONG"
    assert R.score("stable", {"label": "x", "flips": 0}) == "ok"
    assert R.score("stable", {"label": "x", "flips": 3}).startswith("WRONG")
    assert R.score(SC.OWNER, {"label": "split"}) == "owner"
    assert R.score(None, {"label": "split"}) == "-"
    assert R.score("split", None) == "n/a"


def test_every_scenario_has_a_want_or_is_declared_unscored():
    for sid, title, want, why, driver in SC.SCENARIOS:
        assert sid and title and why and callable(driver)
        assert want is None or isinstance(want, str)
    assert len(SC.SCENARIOS) >= 12


def test_the_sweep_end_to_end_on_two_scenarios(tmp_path):
    res = R.run_all({"S04", "S05"}, ["floor0", "floor4"], tmp_path, None)
    assert res["cells"]["S04"]["floor0"]["label"] == "split"
    assert res["cells"]["S04"]["floor4"]["label"] == "refocus"
    assert res["cells"]["S05"]["floor4"]["label"] == "split"
    md = R.render(res)
    assert "| S04 |" in md and "WRONG" in md and "ok" in md


# ------------------------------------------- 5. the module-identity collision ----
# `eval/safety/harness.py` and `eval/scenarios/harness.py` both answer to the bare name `harness`,
# and `sys.path` is consulted only when `sys.modules` has no entry. Before `_sibling.load()`, a
# process that touched the safety bench first handed the scenario sweep the WRONG harness, and it
# died inside its own error handler (`module 'harness' has no attribute 'UnsupportedKnob'`) —
# 3 failures in the full suite, 0 when `test_scenarios.py` ran alone. These two tests run in a
# SUBPROCESS because the collision is a property of a process's import table, and this process has
# already resolved it. Reverting either import line in `run.py`/`scenarios.py` turns both red.

_COLLIDE = r"""
import sys
from pathlib import Path
PLUGIN = Path(%r)
# 1. poison the well exactly as the full suite does: safety's harness claims the bare name first.
sys.path.insert(0, str(PLUGIN / "eval" / "safety"))
import harness as SAFETY
assert not hasattr(SAFETY, "UnsupportedKnob"), "fixture wrong: that IS the scenario harness"
# 2. now load the scenario sweep the way the tests and the CLI both do.
sys.path.insert(0, str(PLUGIN / "eval" / "scenarios"))
from _sibling import load
R = load("run")
print("HAS_KNOB", hasattr(R.H, "UnsupportedKnob"))
print("DISTINCT", R.H is not SAFETY)
print("BARE_IS_SAFETY", sys.modules["harness"] is SAFETY)
print("SC_HARNESS_SAME", load("scenarios").H is R.H)
# the invariant, not the instance: NOTHING this directory loads may claim a bare stem.
stolen = {"run", "scenarios", "vaultgen", "measure"} & set(sys.modules)
print("NO_BARE_STEMS", not stolen, sorted(stolen))
"""


def _collide(tmp_path):
    import subprocess
    p = subprocess.run([sys.executable, "-c", _COLLIDE % str(PLUGIN)],
                       capture_output=True, text=True, cwd=str(tmp_path))
    return p


def test_the_scenario_sweep_survives_the_safety_benchs_harness_claiming_the_bare_name(tmp_path):
    p = _collide(tmp_path)
    assert p.returncode == 0, f"stdout={p.stdout}\nstderr={p.stderr}"
    assert "HAS_KNOB True" in p.stdout, p.stdout
    assert "DISTINCT True" in p.stdout, p.stdout
    assert "SC_HARNESS_SAME True" in p.stdout, p.stdout
    assert "NO_BARE_STEMS True" in p.stdout, p.stdout


def test_nothing_in_scenarios_steals_the_bare_harness_name_from_the_safety_bench(tmp_path):
    # The other direction, and the reason `eval/safety/run_safety.py` errored in the same run:
    # whoever loses the race gets the other's module. After the fix the scenario side registers
    # only `splitsim_*` names, so the bare one still belongs to whoever legitimately imported it.
    p = _collide(tmp_path)
    assert p.returncode == 0, f"stdout={p.stdout}\nstderr={p.stderr}"
    assert "BARE_IS_SAFETY True" in p.stdout, p.stdout


def test_the_harness_refuses_a_knob_the_product_echoes_with_the_WRONG_VALUE(tmp_path):
    """The other half of the echo contract, and it had no control.

    `test_a_knob_the_product_does_not_echo_is_refused` proves the harness sees a MISSING echo.
    Disabling the value-comparison arm was green — so the instrument could detect "knob absent"
    and could not detect "knob echoed with a value the run never set", which is the failure that
    would make a whole sweep a measurement of the wrong configuration."""
    w, _ = _world(tmp_path, V.Shape(topics=[V.Topic("alpha", 8)], noise=4))
    H.ECHO["split_min_entries"] = "min_entries"
    real = H.propose(w, {"split_min_entries": 8})
    assert real["min_entries"] == 8, real
    # the product will echo 8; tell the harness the run asked for 9 and it must refuse, loudly
    original = H.World.set_limits

    def lying_set_limits(self, limits=None):
        merged = original(self, limits)
        return dict(merged, split_min_entries=9)

    H.World.set_limits = lying_set_limits
    try:
        with pytest.raises(RuntimeError, match="echoed"):
            H.propose(w, {"split_min_entries": 8})
    finally:
        H.World.set_limits = original


def test_no_two_modules_under_gedaechtnis_share_a_bare_stem_without_an_aliasing_loader():
    """Enforce, don't remember. `harness` collided because two directories each ship one and every
    consumer imported it by bare name; `sys.path` is consulted only when `sys.modules` has no
    entry, so the loser silently receives the winner's module. The same trap is DORMANT for `run`,
    a stem claimed by six files under `eval/`.

    This does not forbid a duplicated stem — it forbids a BARE import of one, which is the part
    that actually breaks. Each known-and-accepted offender is listed by name with its reason, so a
    NEW one fails here instead of in whichever unrelated test collects first."""
    import collections
    src = [f for f in PLUGIN.rglob("*.py") if "tests" not in f.parts]
    by_stem = collections.defaultdict(list)
    for f in src:
        by_stem[f.stem].append(f)
    duplicated = {stem for stem, fs in by_stem.items() if len(fs) > 1}
    assert duplicated, "no duplicated stems at all — this guard would be vacuous; check the glob"
    assert "run" in duplicated, f"expected `run` to still be duplicated, got {sorted(duplicated)}"

    offenders = []
    for f in src:
        text = f.read_text(encoding="utf-8")
        for stem in sorted(duplicated):
            if not re.search(rf"^\s*(?:import {stem}\b|from {stem} import)", text, re.M):
                continue
            offenders.append(f"{f.relative_to(PLUGIN)} imports bare {stem!r} "
                             f"({len(by_stem[stem])} files claim it)")

    accepted = {
        # Its own directory-mate, and the scenarios side no longer registers the bare name, so
        # nothing can outrace it. Recorded rather than silently allowed.
        "eval/safety/run_safety.py imports bare 'harness' (2 files claim it)",
        # Imports the GROWTH simulator's run.py from beside it; the scenario runner is reached
        # only through `_sibling.load`, so this one cannot be served the wrong module either.
        "eval/simulator/run.py imports bare 'recall_bench' (1 files claim it)",
    }
    unknown = sorted(set(offenders) - accepted)
    assert not unknown, (
        "a bare import of a duplicated module stem — route it through an aliasing loader "
        "(see eval/scenarios/_sibling.py) or add it to `accepted` with the reason it is safe:\n  "
        + "\n  ".join(unknown))


def test_the_cut_candidates_are_ranked_by_NEIGHBOURS_KNOWING_EACH_OTHER_not_by_index(tmp_path):
    """★ The ranking had no test, and the obvious fixtures cannot give it one.

    `_unbridge` searches only the `_CUT_CANDIDATES` least-cohesive entries, because the exhaustive
    search is C(n, K) connectivity passes. Replacing that ranking with `sorted(members)` left every
    test green — not because the ranking does not matter, but because in an 18- or 20-entry
    component the first 16 INDICES happen to include the bridges anyway. A control that cannot fail
    is not a control.

    This component is large enough that the two bridges fall outside the first 16 indices, so only
    a ranking that actually finds bridges can separate it. Bridges rank lowest because their
    neighbours are two sets of strangers (measured 0.52 against a median of 1.0); by DEGREE they
    rank near the TOP, which is why degree would be the wrong dial."""
    w, _ = _world(tmp_path / "late", V.Shape(topics=[V.Topic("a", 20), V.Topic("b", 20)], noise=8,
                                             bridges=[(0, 1, 2)]))
    found = H.propose(w)["proposals"]
    sizes = sorted(len(p["entries"]) for p in found)
    assert sizes == [22, 22], f"the bridged pair came back as {sizes}"
    a, b = (set(e["heading"] for e in p["entries"]) for p in found)
    assert len(a & b) == 2, sorted(a & b)


def test_a_pair_joined_by_a_SINGLE_entry_is_still_two_rooms(tmp_path):
    """K=1 had no test, and the obvious fixture cannot give it one.

    With topics of 10 and 12 the K-starts-at-2 mutant returns the SAME two rooms: it removes the
    bridge plus one ordinary entry, both pieces are still over the floor, and the ordinary entry is
    re-attached to its own room. Equivalent mutant, not a gap — so the fixture has to sit where a
    2-cut cannot succeed. At topics of EXACTLY the child floor, removing one extra entry leaves a
    7-entry piece that is too small to be a room, so only a cut of exactly 1 can separate them."""
    w, _ = _world(tmp_path / "one", V.Shape(topics=[V.Topic("a", 8), V.Topic("b", 8)], noise=6,
                                            bridges=[(0, 1, 1)]))
    found = H.propose(w)["proposals"]
    assert sorted(len(p["entries"]) for p in found) == [9, 9], \
        f"one shared entry did not separate two floor-sized rooms: {[len(p['entries']) for p in found]}"
    a, b = (set(e["heading"] for e in found[0]["entries"]),
            set(e["heading"] for e in found[1]["entries"]))
    assert len(a & b) == 1, sorted(a & b)


def test_each_room_is_NAMED_FROM_ITS_OWN_vocabulary_not_the_regions(tmp_path):
    """★ The defect the clustering change left standing: right entries, wrong name.

    The shared-term list was ranked by how many pairs share a term ANYWHERE IN THE REGION, so a
    bridging entry imported the other topic's words into this room's list. Measured before the fix:
    a 12-entry Beta room was proposed as `AlphaoneAlphathree2` — the Alpha name with a digit — and
    applying it wrote Alpha's words into the new region's Map.md. Its reported cohesion was a false
    0.167 for the same reason, which would have had a live cohesion gate refuse a perfect room.

    The assertion is the property, not the string: every term a room is NAMED from must be carried
    by more than half of THAT room's own entries. Under the defect the Beta room's top terms were
    carried by 2 of 12 — the bridges alone."""
    w, _ = _world(tmp_path / "named", V.Shape(topics=[V.Topic("alpha", 10), V.Topic("beta", 10)],
                                              noise=10, bridges=[(0, 1, 2)]))
    found = H.propose(w)["proposals"]
    assert len(found) == 2, [len(p["entries"]) for p in found]
    for p in found:
        own = p["entries"]
        for term in p["shared_terms"][:2]:
            carried = sum(1 for e in own if term in e["heading"])
            assert carried > len(own) / 2, (
                f"{p['suggested_name']} is named from {term!r}, carried by only "
                f"{carried} of its {len(own)} entries")
    assert found[0]["suggested_name"].rstrip("0123456789") != \
        found[1]["suggested_name"].rstrip("0123456789"), \
        f"the two rooms got the same name base: {[p['suggested_name'] for p in found]}"
    assert all(p["cohesion"] == 1.0 for p in found), [p["cohesion"] for p in found]


def test_a_group_with_no_subject_is_REPORTED_as_a_chain_not_dropped(tmp_path):
    """★ This module's own rule, written where REFOCUS is defined: dropping a finding answers "is
    there anything here" with "no" when the honest answer is "yes, and it is a chain".

    212 entries that resemble their neighbours and share no subject are not a room — there is
    nothing to move and nothing to name — but they are not nothing either. They come back in
    `chains`, one compact line, rather than as 212-entry proposals (which is what the old rule
    offered, and what this row removed: the live vault went from 13 proposals to 5)."""
    w, _ = _world(tmp_path / "chain", V.Shape(topics=[V.Topic("a", 212, vocab=16, k=5)], noise=54))
    found = H.propose(w)
    assert found["proposals"] == [], [len(p["entries"]) for p in found["proposals"]]
    assert found["chains"], "212 entries vanished with no output at all"
    assert found["chains"][0]["entries"] > 100, found["chains"]
    # ★ AND IT MUST SAY WHERE. A count with no region is rumoured, not reported: on the real vault
    # this line carries EIGHT groups across 28 regions, and bare numbers cannot be acted on.
    assert found["chains"][0]["region"] == "Proj", found["chains"]
    line = [ln for ln in H.render(w).splitlines() if "share no subject" in ln]
    assert line, H.render(w)
    body = H.render(w)
    assert "Proj: 212 of 266 entries" in body, body
    # POSITIVE CONTROL: one subject, same size and generator — a room, not a chain.
    w2, _ = _world(tmp_path / "room", V.Shape(topics=[V.Topic("a", 212)], noise=54))
    got = H.propose(w2)
    assert [len(p["entries"]) for p in got["proposals"]] == [212]
    assert got["chains"] == [], got["chains"]


def test_BOTH_new_rules_can_actually_be_TURNED_OFF_from_a_limits_file(tmp_path):
    """★ Zero is the one value that switches a rule off, and `or` made it unreachable.

    `float(x or 0.5)` is copied from the knobs whose fallback IS 0.0, where it is harmless. Here it
    silently mapped 0 — off — to the value that turns the rule fully ON. Both rules are tested from
    a limits FILE, the way an operator would set them, not by patching a function."""
    chain = V.Shape(topics=[V.Topic("a", 212, vocab=16, k=5)], noise=54)
    w, _ = _world(tmp_path / "coreoff", chain)
    assert H.propose(w)["proposals"] == []
    w2, _ = _world(tmp_path / "coreoff2", chain)
    off = H.propose(w2, {"split_core_term_share": 0})["proposals"]
    assert [len(p["entries"]) for p in off] == [212], \
        "core_term_share=0 did not switch the rule off"

    bridged = V.Shape(topics=[V.Topic("a", 8), V.Topic("b", 8)], noise=6, bridges=[(0, 1, 2)])
    w3, _ = _world(tmp_path / "bridgeon", bridged)
    assert sorted(len(p["entries"]) for p in H.propose(w3)["proposals"]) == [10, 10]
    w4, _ = _world(tmp_path / "bridgeoff", bridged)
    fused = H.propose(w4, {"split_max_bridge_entries": 0})["proposals"]
    assert [len(p["entries"]) for p in fused] == [18], \
        "max_bridge_entries=0 did not switch the unbridging off"


# ------------------------------------- 6. the cross-variant invariant itself ----
# ★ `run.py` states the diagnostic in its own comment — "a scenario that scores `ok` under both
# variants is not testing the axis" — and for two reviewer rounds NOTHING EXERCISED IT. The
# invariant lives in the eval harness, which the suite does not otherwise run, so the next edit
# could un-enforce it silently: the sweep would print a clean tally and the tally is what a row is
# accepted on. That is the same shape as the defect SPLITVOCAB-1 exists to fix.
#
# The break is by DEGREE, not by absence, because absence is already covered: `run_multi` refuses a
# fixture that wrote NO convention (`boilerplate_carriers == 0`). A fixture that writes it too
# THINLY passes both of those preconditions and still tests nothing — the weld never forms, so the
# scenario scores the same with the axis on and with it off. Only the cross-variant delta can see
# that, because only it holds both scores at once.

def _axis_scenarios_at_share(share: float) -> list:
    """`SC.SCENARIOS` with S22/S23's FOCUS region writing the convention on `share` of its entries.

    The siblings are untouched: they still carry it on every entry, so the axis still MEASURES house
    style (`boilerplate_terms` non-empty) and the second precondition stays quiet too. The only
    thing that changes is whether the convention is dense enough inside the focus region to weld its
    entries into a room — which is the whole thing these two scenarios exist to show."""
    focus = {
        "S22": SC.Shape(topics=[SC.Topic("alpha", 6), SC.Topic("beta", 6)], noise=8, region="Proj",
                        boilerplate=SC.CONVENTION, boilerplate_share=share),
        "S23": SC.Shape(topics=[SC.Topic("gamma", 10)], noise=10, region="Proj",
                        boilerplate=SC.CONVENTION, boilerplate_share=share),
    }
    out = []
    for sid, title, want, why, driver in SC.SCENARIOS:
        if sid in focus:
            driver = (lambda root, lim, _s=focus[sid]:
                      SC.run_multi(root, SC._convention_vault(_s), lim, "Proj"))
        out.append((sid, title, want, why, driver))
    return out


def _axis_sweep(tmp_path, scenarios, variants):
    """Run the two axis scenarios and return {sid: delta score}, using the runner's own scorer."""
    old = SC.SCENARIOS
    SC.SCENARIOS = scenarios
    try:
        res = R.run_all(set(SC.AXIS_SCENARIOS), variants, tmp_path, None)
    finally:
        SC.SCENARIOS = old
    wants = {s[0]: s[2] for s in scenarios}
    return res, {sid: R.score(wants[sid], res["cells"][sid]["floor4"])
                 for sid in SC.AXIS_SCENARIOS}


def test_an_axis_scenario_gone_quiet_BY_DEGREE_is_reported_VACUOUS_not_ok(tmp_path):
    """POSITIVE CONTROL for the invariant: thin the convention and the sweep must refuse to score.

    At share 0.1 two of the focus region's twenty entries carry the convention, which is above both
    existing preconditions (something was written; the axis measured house style) and far below what
    it takes to weld a room. Before the invariant existed this run printed `ok` for both scenarios
    under every variant — the reading the row was accepted on."""
    res, scores = _axis_sweep(tmp_path, _axis_scenarios_at_share(0.1),
                              ["floor4", R.AXIS_OFF_VARIANT])
    for sid in sorted(SC.AXIS_SCENARIOS):
        cell = res["cells"][sid]["floor4"]
        assert "error" not in cell, f"{sid}: a precondition fired, so this is not the DEGREE case"
        assert cell["boilerplate_carriers"], f"{sid}: the fixture wrote no convention at all"
        assert cell["boilerplate_terms"], f"{sid}: the axis measured no house style"
        assert cell.get("axis_delta_missing") is True, f"{sid}: the invariant did not fire"
        assert scores[sid].startswith("VACUOUS"), f"{sid} scored {scores[sid]!r}"
    assert "VACUOUS" in R.render(res)


def test_the_shipped_axis_fixtures_are_NOT_flagged_vacuous(tmp_path):
    """NEGATIVE CONTROL: the fixtures as written score DIFFERENTLY with the axis off, so the same
    invariant must stay silent on them. Without this, a guard that flagged everything would pass
    the test above and turn the whole sweep into `VACUOUS`."""
    res, scores = _axis_sweep(tmp_path, list(SC.SCENARIOS), ["floor4", R.AXIS_OFF_VARIANT])
    for sid in sorted(SC.AXIS_SCENARIOS):
        assert res["cells"][sid]["floor4"].get("axis_delta_missing") is not True, \
            f"{sid}: the shipped fixture was called vacuous"
        assert scores[sid] == "ok", f"{sid} scored {scores[sid]!r} with the axis on"
        off = R.score(next(s[2] for s in SC.SCENARIOS if s[0] == sid),
                      res["cells"][sid][R.AXIS_OFF_VARIANT])
        assert off.startswith("WRONG"), f"{sid} scored {off!r} with the axis OFF"


def test_a_run_without_the_axis_off_variant_says_so_instead_of_scoring(tmp_path):
    """The guard's OWN ABSENCE, asserted. `--variants floor4` is how anyone pokes at these two, and
    there is no delta to take in that run — so `ok` there would mean only "it scored what the reader
    wanted", which the quiet fixture above also does."""
    res, _ = _axis_sweep(tmp_path, _axis_scenarios_at_share(0.1), ["floor4"])
    assert res["axis_unchecked"] is True
    for sid in sorted(SC.AXIS_SCENARIOS):
        assert res["cells"][sid]["floor4"].get("axis_unchecked") is True
        assert R.score(next(s[2] for s in SC.SCENARIOS if s[0] == sid),
                       res["cells"][sid]["floor4"]) == "n/a (no axis-off variant in this run)"
    assert "cannot check the vault-wide axis" in R.render(res)
