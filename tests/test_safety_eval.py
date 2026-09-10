"""Tests for the WP9 destruction eval (`eval/safety/`) — 50 adversarial commands/writes and their
50 legitimate twins, run through the REAL `hooks/gate.py` doors.

**Every case is a real subprocess of `hooks/gate.py`** (or, for a mutant run, the in-process
`mutant_gate.py` wrapper, itself launched as its OWN subprocess by `harness.run_gate` — see that
file's docstring), so this suite proves the same thing `run_safety.py` proves on the command line:
not a description of what the doors are supposed to do, an actual run of them.

`eval/safety/harness.py` is safe to `import` directly into this test process: unlike
`hooks/common.py`/`hooks/config.py`, it reads no environment-derived constants at import time and
opens no real vault or state directory — every sandbox it builds lives under a `tmp_path` this
suite controls, exactly like `test_gate.py`'s `world` fixture. (Global/Errata: "a suite that writes
the application's real sidecar makes its own verdict depend on the machine's state" — the harness
module itself carries no such state to begin with.)
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

import pytest

SAFETY = Path(__file__).resolve().parents[1] / "eval" / "safety"
sys.path.insert(0, str(SAFETY))
import harness  # noqa: E402  (the harness under test)

CASES_PATH = SAFETY / "cases.json"
MUTANTS = ("vault_git", "data_integrity", "bash_partition", "d1_whole_file_write", "write_partition")


@pytest.fixture(scope="module")
def cases():
    return harness.load_cases(CASES_PATH)


# ------------------------------------------------------------------------- the data set itself ----

def test_cases_file_has_fifty_adversarial_and_fifty_legitimate_twins(cases):
    adv = [c for c in cases if c["expect"] != "allow"]
    twins = [c for c in cases if c["expect"] == "allow"]
    assert len(adv) == 50, f"expected 50 adversarial cases, found {len(adv)}"
    assert len(twins) == 50, f"expected 50 legitimate twins, found {len(twins)}"
    assert len(cases) == 100


def test_every_twin_names_the_adversarial_case_it_is_closest_to(cases):
    ids = {c["id"] for c in cases}
    twins = [c for c in cases if c["expect"] == "allow"]
    for t in twins:
        assert t.get("twin_of"), f"{t['id']} has no twin_of"
        assert t["twin_of"] in ids, f"{t['id']} names a twin_of that does not exist: {t['twin_of']!r}"
        origin = next(c for c in cases if c["id"] == t["twin_of"])
        assert origin["expect"] != "allow", f"{t['id']}'s twin_of {origin['id']} is not itself adversarial"


def test_the_twin_set_is_non_empty_per_attack_class(cases):
    """An 'attack class' here is the case-id prefix group (adv-a../twin-a.., adv-b../twin-b.., …) —
    each letter is one of the categories named in the WP9 brief (vault git law, data integrity,
    artifact-not-file, bash partition, write partition, Concilium stem, display-name-as-filename,
    D1 whole-file write, D2 mutex, shared-surface row grammar). A category with adversarial cases
    but ZERO twins would prove only that the doors deny — never that they let the legitimate form
    through, which is half of what this eval exists to show."""
    import re
    classes: dict[str, list[str]] = {}
    for c in cases:
        m = re.match(r"^(?:adv|twin)-([a-z])\d", c["id"])
        assert m, f"case id does not follow the adv-<letter><nn>-... / twin-<letter><nn>-... convention: {c['id']}"
        classes.setdefault(m.group(1), []).append(c["id"])
    assert len(classes) >= 10, f"expected at least 10 attack classes (a..j), found {sorted(classes)}"
    for letter, ids in classes.items():
        twins = [i for i in ids if i.startswith("twin-")]
        advs = [i for i in ids if i.startswith("adv-")]
        assert twins, f"attack class {letter!r} has adversarial cases {advs} but NO legitimate twin"
        assert advs, f"attack class {letter!r} has twins {twins} but no adversarial case they are a twin of"


def test_case_ids_are_unique(cases):
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


# ------------------------------------------------------------------------- the full run is green ----

@pytest.fixture(scope="module")
def baseline_results(cases):
    return harness.run_all(cases)


def test_the_full_run_is_green(baseline_results):
    """The load-bearing assertion: every adversarial case is refused (deny/ask, citing its rule)
    and every legitimate twin is allowed, against the REAL, unmodified `hooks/gate.py`. A red here
    means the plugin's doors do not actually have the safety property WP9 exists to prove — never
    make this test pass by loosening a case's `expect`/`rule`; fix the case (if it was wrong about
    what the code does) or report the gap (if the code is what's wrong)."""
    s = harness.summarize(baseline_results)
    misses = [r for r in baseline_results if not r["ok"]]
    detail = "\n".join(f"  {r['id']}: expected {r['expect']!r}, got {r['decision']!r} — {r['reason'][:160]}"
                       for r in misses)
    assert s["all_ok"], f"{len(misses)} case(s) did not behave as expected:\n{detail}"
    assert s["adversarial"] == 50 and s["twins"] == 50


def test_no_adversarial_case_was_silently_allowed(baseline_results):
    """The sharpest single check: not one of the 50 attacks got a bare allow (decision is None)."""
    allowed_attacks = [r["id"] for r in baseline_results if r["expect"] != "allow" and r["decision"] is None]
    assert not allowed_attacks, f"adversarial case(s) allowed outright, with no deny/ask: {allowed_attacks}"


def test_no_legitimate_twin_was_refused(baseline_results):
    refused_twins = [r["id"] for r in baseline_results if r["expect"] == "allow" and r["decision"] is not None]
    assert not refused_twins, f"legitimate twin(s) refused: {refused_twins}"


# ------------------------------------------------------------------------- mutation testing ----

@pytest.mark.parametrize("mutant", MUTANTS)
def test_each_named_mutant_goes_red(cases, mutant):
    """Disable exactly one rule (see mutant_gate.py) and confirm at least one adversarial case that
    rule alone was protecting now gets through. A mutant that stays green means the rule is not
    actually exercised by anything in cases.json — 'not covered', per the mutation-testing rule in
    Global/Patterns-verification.md ('a check whose trigger condition never occurs in its fixture
    is VACUOUS — mutate the guarded code and confirm the check goes red')."""
    results = harness.run_all(cases, mutant=mutant)
    s = harness.summarize(results)
    assert not s["all_ok"], (
        f"MUTANT DID NOT BITE: disabling {mutant!r} changed no case's outcome — "
        f"either no case in cases.json depends on this rule, or the monkeypatch target is wrong")
    misses = [r for r in results if not r["ok"]]
    # every miss under a mutant must be an ADVERSARIAL case that is now wrongly allowed — a mutant
    # that instead breaks a TWIN (an allow that starts failing) would mean the monkeypatch changed
    # something outside the one rule it names.
    wrong_kind = [r["id"] for r in misses if r["expect"] == "allow"]
    assert not wrong_kind, f"mutant {mutant!r} broke twin(s), not just adversarial cases: {wrong_kind}"


def test_the_five_mutants_are_disjoint_from_a_clean_run(cases):
    """Sanity check on the mutant table itself: MUTANTS here matches mutant_gate.py's own table, so
    a typo in either file is caught immediately rather than silently mutating nothing."""
    mg = SAFETY / "mutant_gate.py"
    txt = mg.read_text(encoding="utf-8")
    for name in MUTANTS:
        assert f'"{name}"' in txt, f"{name!r} is not a key in mutant_gate.py's MUTANTS table"


# ------------------------------------------------------------------------- derived, never typed ----

def test_render_markdown_is_a_pure_function_of_the_results(baseline_results):
    a = harness.render_markdown(baseline_results)
    b = harness.render_markdown(baseline_results)
    assert a == b


def test_json_result_is_regenerated_not_typed(tmp_path, cases):
    """Run `run_safety.py` twice into two different --out dirs and assert the summary section is
    byte-identical — nothing about the reported result is hand-typed or order-dependent."""
    run_py = SAFETY / "run_safety.py"
    outs = []
    for i in range(2):
        out = tmp_path / f"out{i}"
        p = subprocess.run([sys.executable, str(run_py), "--out", str(out)],
                           capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, p.stderr
        doc = json.loads((out / "results.json").read_text(encoding="utf-8"))
        outs.append(doc)
    assert outs[0]["summary"] == outs[1]["summary"]
    ids_a = [r["id"] for r in outs[0]["results"]]
    ids_b = [r["id"] for r in outs[1]["results"]]
    assert ids_a == ids_b
    decisions_a = [(r["id"], r["decision"], r["ok"]) for r in outs[0]["results"]]
    decisions_b = [(r["id"], r["decision"], r["ok"]) for r in outs[1]["results"]]
    assert decisions_a == decisions_b


def test_run_safety_cli_exits_zero_on_a_clean_baseline(tmp_path):
    run_py = SAFETY / "run_safety.py"
    p = subprocess.run([sys.executable, str(run_py), "--out", str(tmp_path)],
                       capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stdout + p.stderr


def test_run_safety_cli_mutant_exits_zero_when_it_bites(tmp_path):
    run_py = SAFETY / "run_safety.py"
    p = subprocess.run([sys.executable, str(run_py), "--mutant", "vault_git", "--out", str(tmp_path)],
                       capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "bit:" in p.stdout


def test_publish_check_stays_clean_for_the_new_files():
    """No personal paths (the plugin is public) — run the plugin's own guard over the whole tree,
    which now includes eval/safety/. (test_publish_check.py already asserts this for the plugin as
    a whole; this is a scoped, explicit re-assertion for WP9's own deliverable.)"""
    plugin = SAFETY.parents[1]
    checker = plugin / "tools" / "publish_check.py"
    p = subprocess.run([sys.executable, str(checker), "--root", str(plugin)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
