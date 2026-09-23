"""test_dadtest.py — ROW-A1 Part A (`eval/dadtest/run.py`), the deterministic dad-test half.

Every claim gets a POSITIVE and a NEGATIVE control, per the row's own instruction: a control that
cannot go red is worse than no control. The unit-level tests drive `wikilink_criterion` and
`evaluate_criteria` directly with synthetic day-rows (fast, deterministic, no subprocesses); a
handful of small real-sandbox tests prove the underlying measurements (`resolved_targets`,
`wikilink_snapshot`) actually detect what they claim to on a genuine installed vault; the slow
end-to-end 90-day run is opt-in only (`GEDAECHTNIS_DADTEST_FULL=1`), because it costs real
subprocess time and this suite otherwise runs on every commit.

Nothing here touches the real vault or the real state directory — every sandbox is a fresh
tmp_path tree wired through the plugin's own env seam, exactly as `test_simulator.py` already
established; `tests/conftest.py`'s vault sentinel watches this suite regardless.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "eval" / "simulator"))
sys.path.insert(0, str(PLUGIN))

import run as sim  # noqa: E402  eval/simulator/run.py — Sandbox, Author, wikilink helpers


def _load(path: Path, name: str):
    """Load a module by file path under a name distinct from `run` — `eval/dadtest/run.py` and
    `eval/simulator/run.py` are literally both named `run.py`; a second `import run` after the
    first is cached under that name returns the WRONG module silently, which is exactly the class
    of bug `hooks/common.py` documents for `from X import Y`."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dadtest = _load(PLUGIN / "eval" / "dadtest" / "run.py", "_test_dadtest_module")

RUN_FULL = os.environ.get("GEDAECHTNIS_DADTEST_FULL") == "1"
slow = pytest.mark.skipif(not RUN_FULL, reason="slow real 90-day hook run — "
                                                "set GEDAECHTNIS_DADTEST_FULL=1 to run it")


@pytest.fixture
def sandbox(tmp_path):
    sb = sim.Sandbox(tmp_path / "box")
    sb.install()
    return sb


def day_row(day, boot_bytes=1000, ok=True, maintenance_ok=True, cleanup_due=False,
           cleanup_ran=False, cleanup_applied=False, extra=None):
    row = {"day": day, "ok": ok, "boot_bytes": boot_bytes, "maintenance_ok": maintenance_ok,
           "cleanup_due": cleanup_due, "cleanup_ran": cleanup_ran,
           "cleanup_applied": cleanup_applied}
    if extra:
        row.update(extra)
    return row


def mk_result(days, budget=20000, horizon=None):
    """A synthetic `evaluate_criteria` input: real `days` rows, dummy `day0`/`day90` (criterion 4
    is exercised separately, directly against `wikilink_criterion`, in its own section above)."""
    return {"boot_budget_bytes": budget, "horizon": horizon or len(days), "days": days,
            "day0": {"day": 1}, "day90": {"day": horizon or len(days)}}


# ============================================================== wikilink_criterion ====

def test_wikilink_criterion_positive_control_detects_a_broken_target():
    """The exact failure this criterion exists to catch: a target that resolved at day 0 and
    does not at day 90."""
    day0 = {("Position", "H1"), ("Position", "H2")}
    day90 = {("Position", "H1")}                       # H2 broke
    c = dadtest.wikilink_criterion(day0, day90, {"day": 1}, {"day": 90})
    assert c["status"] == "FAIL"
    assert "Position#H2" in c["broken_targets"]
    assert "1 of 2" in c["detail"]


def test_wikilink_criterion_negative_control_no_regression_passes():
    """The same check, on a vault where nothing broke: it stays quiet."""
    day0 = {("Position", "H1"), ("Position", "H2")}
    day90 = {("Position", "H1"), ("Position", "H2"), ("Canon", "H3")}   # only grew
    c = dadtest.wikilink_criterion(day0, day90, {"day": 1}, {"day": 90})
    assert c["status"] == "PASS"


def test_wikilink_criterion_empty_day0_is_a_vacuous_pass_named_as_such():
    c = dadtest.wikilink_criterion(set(), set(), {"day": 1}, {"day": 90})
    assert c["status"] == "PASS"
    assert "no wikilinks existed" in c["detail"]


def test_wikilink_criterion_unchecked_when_a_snapshot_is_unreadable():
    """`None` (an unreadable memory file at that snapshot) must never be read as an empty vault —
    the vacuous-pass wording above only applies to a GENUINE empty set."""
    c = dadtest.wikilink_criterion(None, {("Position", "H1")},
                                   {"day": 1, "error": "PermissionError: [Errno 13] denied"},
                                   {"day": 90})
    assert c["status"] == "UNCHECKED"
    assert "day 0" in c["detail"]
    c2 = dadtest.wikilink_criterion({("Position", "H1")}, None, {"day": 1},
                                    {"day": 90, "error": "OSError: boom"})
    assert c2["status"] == "UNCHECKED"
    assert "day 90" in c2["detail"]


# ============================================================ criterion 2: boot bytes ====

def test_criterion2_positive_control_over_budget_fails():
    result = mk_result([day_row(1, boot_bytes=5000), day_row(2, boot_bytes=25000)])
    c = dadtest.evaluate_criteria(result, set(), set())["2_boot_within_budget"]
    assert c["status"] == "FAIL"
    assert c["over_budget_days"] == [2]


def test_criterion2_negative_control_all_under_budget_passes():
    result = mk_result([day_row(1, boot_bytes=5000), day_row(2, boot_bytes=19999)])
    c = dadtest.evaluate_criteria(result, set(), set())["2_boot_within_budget"]
    assert c["status"] == "PASS"


def test_criterion2_all_days_unmeasured_is_unchecked_not_a_zero():
    result = mk_result([day_row(1, ok=False), day_row(2, ok=False)])
    c = dadtest.evaluate_criteria(result, set(), set())["2_boot_within_budget"]
    assert c["status"] == "UNCHECKED"


def test_criterion2_partial_unmeasured_days_stay_unchecked_even_with_no_overage_seen():
    """A day the hook could not measure is not proof that day was fine — the criterion must not
    silently PASS on the days it happened to see."""
    result = mk_result([day_row(1, boot_bytes=1000), day_row(2, ok=False)])
    c = dadtest.evaluate_criteria(result, set(), set())["2_boot_within_budget"]
    assert c["status"] == "UNCHECKED"
    assert c["unmeasured_days"] == [2]


# ======================================================= criterion 3: cleanup fires ====

def test_criterion3_positive_control_applied_cleanup_passes():
    result = mk_result([
        day_row(1),
        day_row(2, cleanup_due=True),
        day_row(3, cleanup_due=True, cleanup_ran=True, cleanup_applied=True,
                extra={"cleanup_receipt": {"folded": [{"path": "Position.md"}]}}),
    ])
    c = dadtest.evaluate_criteria(result, set(), set())["3_cleanup_fires_and_applies"]
    assert c["status"] == "PASS"
    assert c["first_due_day"] == 2
    assert c["first_applied_day"] == 3


def test_criterion3_negative_control_small_vault_never_due_fails_honestly():
    """A vault that stays small reports NO cleanup, and the criterion says so honestly — never a
    silent PASS just because there was nothing to clean up."""
    result = mk_result([day_row(d) for d in range(1, 15)])
    c = dadtest.evaluate_criteria(result, set(), set())["3_cleanup_fires_and_applies"]
    assert c["status"] == "FAIL"
    assert "never became due" in c["detail"]


def test_criterion3_due_but_never_applied_fails_and_names_it():
    result = mk_result([
        day_row(1), day_row(2, cleanup_due=True, cleanup_ran=True, cleanup_applied=False),
        day_row(3, cleanup_due=True, cleanup_ran=True, cleanup_applied=False),
    ])
    c = dadtest.evaluate_criteria(result, set(), set())["3_cleanup_fires_and_applies"]
    assert c["status"] == "FAIL"
    assert "never had anything to apply" in c["detail"]


def test_criterion3_maintenance_never_ran_is_unchecked():
    result = mk_result([day_row(d, maintenance_ok=False) for d in range(1, 4)])
    c = dadtest.evaluate_criteria(result, set(), set())["3_cleanup_fires_and_applies"]
    assert c["status"] == "UNCHECKED"


def test_criterion3_errors_with_no_observed_cleanup_is_unchecked_not_fail():
    """A real cleanup could have been hidden behind a hook failure — that is a DIFFERENT fact from
    a vault that genuinely never needed one, and reporting it as FAIL would say something the run
    never actually established."""
    result = mk_result([
        day_row(1), day_row(2, maintenance_ok=False, extra={"maintenance_error": "boom"}),
        day_row(3),
    ])
    c = dadtest.evaluate_criteria(result, set(), set())["3_cleanup_fires_and_applies"]
    assert c["status"] == "UNCHECKED"
    assert c["error_days"] == [2]


# ===================================================== unrunnable hooks -> UNCHECKED ====

def test_session_boot_unchecked_when_the_hook_raises():
    """A hook that cannot even be measured is UNCHECKED, never a boot_bytes of 0."""
    class BrokenSandbox:
        def hook_boot_bytes(self, day):
            raise RuntimeError("session_start.py failed (rc=1): boom")
    out = dadtest.session_boot(BrokenSandbox(), 3)
    assert out["ok"] is False
    assert "error" in out
    assert "boot_bytes" not in out


def test_session_end_unchecked_when_maintenance_hook_fails(monkeypatch):
    calls = []

    def fake_run_hook(sb, script, payload, cwd=None, extra_args=None, timeout=90):
        calls.append(str(script))
        return 1, "", "boom: maintenance.py crashed"

    monkeypatch.setattr(dadtest, "run_hook", fake_run_hook)
    fake_sb = SimpleNamespace(env={}, state=Path("/nonexistent"), repo=Path("/nonexistent"))
    out = dadtest.session_end(fake_sb, 1, "sid")
    assert out["maintenance_ok"] is False
    assert "boom" in out["maintenance_error"]
    assert out["cleanup_due"] is None
    assert out["cleanup_ran"] is False
    assert len(calls) == 1                      # cleanup.py must never run off a failed read


def test_session_end_reports_cleanup_ran_but_errored(monkeypatch, tmp_path):
    """maintenance says due, cleanup.py itself then fails: `cleanup_ran` is True (it was invoked)
    and `cleanup_applied` stays False with the error attached — never silently absorbed."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "maintenance.json").write_text(
        json.dumps({"cleanup": {"due": True, "arms": {"time": {"fired": True}}},
                    "compacted": []}), encoding="utf-8")

    calls = []

    def fake_run_hook(sb, script, payload, cwd=None, extra_args=None, timeout=90):
        calls.append(str(script))
        if "maintenance.py" in str(script):
            return 0, "", ""
        return 1, "", "cleanup.py crashed"

    monkeypatch.setattr(dadtest, "run_hook", fake_run_hook)
    fake_sb = SimpleNamespace(env={}, state=state_dir, repo=tmp_path / "repo")
    out = dadtest.session_end(fake_sb, 1, "sid")
    assert out["maintenance_ok"] is True
    assert out["cleanup_due"] is True
    assert out["cleanup_ran"] is True
    assert out["cleanup_applied"] is False
    assert "crashed" in out["cleanup_error"]
    assert len(calls) == 2


# ============================================================ real-sandbox controls ====

def test_resolved_targets_positive_control_detects_a_broken_link(sandbox):
    """Write two linked entries with the real Author, then hand-break the target heading (as
    compaction moving it to a differently-named file would) and confirm the specific target drops
    out of `resolved_targets` — not just that the aggregate count changed."""
    author = sim.Author(sandbox, seed=1)
    author.day = 1
    author.entry(sim.BOOT_STEM, 700)               # first entry: no link yet
    author.entry(sim.BOOT_STEM, 700)                # second: links to the first
    before = dadtest.resolved_targets(sandbox)
    assert before, "the second entry must have produced a resolvable link"
    target_stem, target_heading = next(iter(before))

    live = sandbox.role(target_stem)
    text = live.read_text(encoding="utf-8")
    broken_text = text.replace(f"## {target_heading}", f"## {target_heading} RENAMED")
    assert broken_text != text, "the heading text must actually have been rewritten"
    live.write_text(broken_text, encoding="utf-8")

    after = dadtest.resolved_targets(sandbox)
    assert (target_stem, target_heading) not in after


def test_resolved_targets_negative_control_untouched_vault_keeps_every_target(sandbox):
    author = sim.Author(sandbox, seed=1)
    author.day = 1
    for _ in range(5):
        author.entry(sim.BOOT_STEM, 700)
    before = dadtest.resolved_targets(sandbox)
    after = dadtest.resolved_targets(sandbox)        # nothing touched in between
    assert before == after
    assert before                                    # and it is not vacuously empty


def test_wikilink_snapshot_unchecked_on_an_unreadable_file(sandbox):
    author = sim.Author(sandbox, seed=1)
    author.day = 1
    author.entry(sim.BOOT_STEM, 700)
    author.entry(sim.BOOT_STEM, 700)
    live = sandbox.role(sim.BOOT_STEM)
    old_mode = live.stat().st_mode
    try:
        live.chmod(0)
        # A root-run suite would still be able to read a 0-mode file; skip rather than false-pass.
        if os.access(live, os.R_OK):
            pytest.skip("running as a user that can read a 0-mode file (root?)")
        snap = dadtest.wikilink_snapshot(sandbox)
        assert "error" in snap
        assert "health" not in snap
    finally:
        live.chmod(stat.S_IMODE(old_mode))


def test_wikilink_snapshot_positive_control_on_a_healthy_vault(sandbox):
    author = sim.Author(sandbox, seed=1)
    author.day = 1
    author.entry(sim.BOOT_STEM, 700)
    author.entry(sim.BOOT_STEM, 700)
    snap = dadtest.wikilink_snapshot(sandbox)
    assert "error" not in snap
    resolved, total = snap["health"]
    assert total >= 1 and resolved == total


# ==================================================================== small real run ====

def test_small_real_run_none_load_reports_no_cleanup_honestly(tmp_path):
    """Real hooks, real sandbox, genuinely no growth (`load="none"`): the cleanup criterion must
    report FAIL ("never became due"), not PASS-because-nothing-to-do. Kept short (14 days) so it
    runs in the default (non-slow) suite."""
    result = dadtest.run("none", "linear", 14, seed=1, payload_dir=tmp_path / "payloads",
                         sample_days=(1, 14))
    c3 = result["criteria"]["3_cleanup_fires_and_applies"]
    assert c3["status"] == "FAIL"
    assert "never became due" in c3["detail"]
    c2 = result["criteria"]["2_boot_within_budget"]
    assert c2["status"] == "PASS"                    # nothing grew; nothing should cross the budget
    c1 = result["criteria"]["1_questions_to_user"]
    assert c1["status"] == "UNCHECKED"                # Part B's, never decided here
    assert result["criteria"]["overall_verdict"] == "UNCHECKED"


def test_small_real_run_writes_sampled_boot_payloads(tmp_path):
    payload_dir = tmp_path / "payloads"
    result = dadtest.run("low", "linear", 5, seed=2, payload_dir=payload_dir,
                         sample_days=(1, 5))
    assert set(result["sampled_days"]) == {1, 5}
    for day, snap in result["sampled_days"].items():
        p = Path(snap["boot_payload_path"])
        assert p.is_file()
        text = p.read_text(encoding="utf-8")
        assert "@-import chain" in text
        assert "additionalContext" in text


# ===================================================================== slow, opt-in ====

@slow
def test_full_90_day_run_at_medium_load_completes_and_decides_every_criterion(tmp_path):
    """The real end-to-end run this row is FOR: 90 simulated days, the real hooks, at the
    MEASURED `medium` growth rate this file's docstring documents choosing. Not asserting a
    specific PASS/FAIL on criterion 2 (boot budget) — the actual product finding, verified by
    hand while building this script, is that `medium` load genuinely crosses the boot budget on
    most days because `bootfile.roll_window`'s own ceiling (`boot_file_budget_bytes`, 32,000 B) is
    HIGHER than the chain budget this criterion checks (`boot_budget_bytes`, 20,000 B) — a real
    gap between two thresholds in `rules/limits.json`, not a harness bug. What this test pins is
    that every criterion reaches a DECIDED verdict (never UNCHECKED) on a full, error-free run, and
    that the cleanup and wikilink criteria — which this load is specifically chosen to exercise —
    come back PASS."""
    result = dadtest.run("medium", "linear", 90, seed=7, payload_dir=tmp_path / "payloads")
    c = result["criteria"]
    assert all(not d.get("maintenance_error") for d in result["days"])
    assert c["2_boot_within_budget"]["status"] in ("PASS", "FAIL")
    assert c["3_cleanup_fires_and_applies"]["status"] == "PASS"
    assert c["4_wikilinks_resolve_day90"]["status"] == "PASS"
    assert c["overall_verdict"] == "UNCHECKED"        # criterion 1 is Part B's; never PASS here


# ---------------------------------------------- what the stress arm found, pinned ----
def _sandbox_for_protection(tmp_path):
    """A real installed sandbox and its env, for the two protection tests below. Separate from the
    `sandbox` fixture because these drive a SUBPROCESS: `bootfile.always_loaded` reads the chain
    from the environment, and asking it in-process would answer about this test runner's own."""
    sb = sim.Sandbox(tmp_path / "prot")
    sb.install()
    return sb, dict(sb.env)



def test_the_boot_chain_file_is_the_one_cleanup_will_not_touch(tmp_path):
    """★ The A1 stress arm's finding, as a test rather than as a paragraph.

    For a fresh, ungraduated region the repo's `CLAUDE.md` @-imports `Position.md`, so `Position.md`
    IS the boot chain. `maintenance`'s `boot_bytes` arm fires when that chain crosses the budget —
    and `cleanup.propose()` then skips the file, because `bootfile.is_protected` says a file a
    session loads is not tidied by an unattended pass. The trigger names the boot budget; the remedy
    may not touch what breached it. The only thing that compacts a boot file needs a graduated
    `Kernel.md` with a declared window marker, which a fresh install does not have.

    This test does not assert that the gap is WRONG — closing it is a design decision, and the
    author-declared window is already ruled. It asserts that the gap is still THERE, so that the day
    someone closes it, or accidentally widens it, this goes red and says so.
    """
    import subprocess, sys as _s
    code = (
        "import sys, os, pathlib, tempfile\n"
        f"sys.path[:0] = [{str(PLUGIN)!r}, {str(PLUGIN / 'hooks')!r}]\n"
        "import bootfile, config\n"
        "chain = bootfile.always_loaded(os.getcwd())\n"
        "pos = config.vault() / os.environ['REGION'] / 'Position.md'\n"
        "pos.parent.mkdir(parents=True, exist_ok=True)\n"
        "pos.write_text('# Position\\n\\n## x\\n\\n' + 'y' * 90000, encoding='utf-8')\n"
        "print('IN_CHAIN', str(pos.resolve()) in {str(pathlib.Path(c).resolve()) for c in chain})\n"
        "print('PROTECTED', bool(bootfile.is_protected(pos, chain=chain)))\n"
    )
    sb, env = _sandbox_for_protection(tmp_path)
    env["REGION"] = sb.region
    p = subprocess.run([_s.executable, "-B", "-c", code], env=env, cwd=str(sb.repo),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "IN_CHAIN True" in p.stdout, p.stdout
    assert "PROTECTED True" in p.stdout, p.stdout


def test_a_file_OUTSIDE_the_boot_chain_is_not_protected(tmp_path):
    """Negative control, and the one that makes the test above mean something. `is_protected` must
    single out the chain — a version that protected every role file would pass the assertion above
    and would also mean the cleanup pass can never tidy anything at all."""
    import subprocess, sys as _s
    code = (
        "import sys, os\n"
        f"sys.path[:0] = [{str(PLUGIN)!r}, {str(PLUGIN / 'hooks')!r}]\n"
        "import bootfile, config\n"
        "chain = bootfile.always_loaded(os.getcwd())\n"
        "other = config.vault() / os.environ['REGION'] / 'Errata.md'\n"
        "other.parent.mkdir(parents=True, exist_ok=True)\n"
        "other.write_text('# Errata\\n\\n## x\\n\\nbody\\n', encoding='utf-8')\n"
        "print('PROTECTED', bool(bootfile.is_protected(other, chain=chain)))\n"
    )
    sb, env = _sandbox_for_protection(tmp_path)
    env["REGION"] = sb.region
    p = subprocess.run([_s.executable, "-B", "-c", code], env=env, cwd=str(sb.repo),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "PROTECTED False" in p.stdout, p.stdout


# ------------------------------------------- the seam leak the A1 reviewer found ----
def test_the_sandbox_clears_every_seam_it_does_not_redirect():
    """★ `GEDAECHTNIS_LIMITS` was inherited, not redirected, and nothing said so.

    Row A1's reviewer had that variable set on their own machine — their live config points at a
    vault-tuned `boot_budget_bytes` of 100,000 — so a "sandboxed" run measured criterion 2 against
    100,000 B instead of the shipped 20,000, silently. They had to prefix every verification run
    with `env -u GEDAECHTNIS_LIMITS` to reproduce this harness's own published numbers.

    The class is the one this package's own test suite exists to prevent: a harness whose verdict
    depends on the machine it runs on. The fix is that seams are CLEARED by default and redirection
    is the exception, so a seam added to the product later fails loudly here rather than leaking."""
    import os as _os
    dirty = dict(_os.environ)
    dirty["GEDAECHTNIS_LIMITS"] = "/nowhere/limits.json"
    dirty["GEDAECHTNIS_FLEET_ROSTER"] = "/nowhere/roster.md"
    old = dict(_os.environ)
    try:
        _os.environ.update(dirty)
        env = sim.Sandbox(Path("/tmp/does-not-need-to-exist")).env
    finally:
        _os.environ.clear()
        _os.environ.update(old)
    assert "GEDAECHTNIS_LIMITS" not in env, "an ambient seam reached the sandbox"
    assert "GEDAECHTNIS_FLEET_ROSTER" not in env
    assert env["GEDAECHTNIS_VAULT"].endswith("vault"), "redirection still has to happen"


def test_every_seam_the_product_reads_is_listed():
    """Negative control on the list itself, and the reason the leak lasted: a hand-kept enumeration
    drifts from what the product actually reads. This greps the product for `GEDAECHTNIS_*` names
    and fails when one is not accounted for — so adding a seam to `config.py` without adding it to
    `SEAMS` goes red here instead of leaking into a future run's numbers."""
    import re as _re
    found = set()
    for path in sorted((PLUGIN / "hooks").glob("*.py")) + [PLUGIN / "recall.py"]:
        found |= set(_re.findall(r"GEDAECHTNIS_[A-Z_]+", path.read_text(encoding="utf-8")))
    assert found, "the grep found no seams at all — the extractor broke, not the product"
    env = sim.Sandbox(Path("/tmp/does-not-need-to-exist")).env
    leaked = sorted(s for s in found if s in env and s not in sim.Sandbox.REDIRECTED)
    assert not leaked, (f"the product reads {leaked} and the sandbox passes them through; the "
                        f"prefix rule in Sandbox.env is what must cover them")


# ================================ Part B's fixture corpus, and the gate in front of it ==========
CORPUS = PLUGIN / "eval" / "dadtest" / "allotment_corpus.json"


def _corpus():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def test_the_shipped_corpus_passes_its_own_gate():
    """The fixture Part B actually runs over. If this ever goes red, the fixture changed and the
    measurement it feeds is about something else."""
    assert not dadtest.corpus_problems(_corpus())


def test_the_gate_REFUSES_the_load_generated_filler_it_exists_to_replace():
    """★ THE NEGATIVE CONTROL THAT MATTERS. Part B's first run measured the growth simulator's
    filler — `The threshold for the upload is K00001`, the same paragraph three times per entry —
    while the persona talked about an allotment, and three of ten replies said so outright. A
    session asking what the vault is FOR is answering the fixture.

    So the gate is pointed at exactly that filler and must reject it. A gate that only ever sees
    the corpus it was written for has never been shown to refuse anything."""
    filler = [{"day": d, "stem": "Position",
               "heading": f"The threshold for the upload is K{d:05d}",
               "body": f"**Decided:** day {d}. The threshold for the upload is `K{d:05d}`.\n\n"
                       + "This paragraph carries no information and exists to occupy bytes. " * 3}
              for d in range(1, 61)]
    problems = dadtest.corpus_problems(filler)
    assert problems, "the gate accepted the filler this fixture exists to replace"
    assert any("K00001" in p or "filler" in p for p in problems), problems
    assert any("contradiction" in p for p in problems), problems


@pytest.mark.parametrize("break_it,needle", [
    (lambda c: c[:20], "entries"),
    (lambda c: [dict(e, day=min(e["day"], 30)) for e in c], "horizon"),
    (lambda c: [dict(e, day=1) for e in c], "distinct days"),
    (lambda c: [dict(e, stem="Nomos") for e in c], "unknown role stems"),
    (lambda c: [dict(e, body="this is a test fixture") for e in c], "describes itself as artificial"),
    (lambda c: [dict(e, body="nothing happened", heading="nothing happened") for e in c], "contradiction"),
])
def test_each_gate_clause_BITES(break_it, needle):
    """Every clause, driven red on a mutation of the real corpus. A gate is a list of promises and
    an untested clause is not one of them — the row that wrote this gate had already shipped two
    uncontrolled guards in two earlier rows."""
    problems = dadtest.corpus_problems(break_it(_corpus()))
    assert any(needle in p for p in problems), (needle, problems)


def test_the_tells_check_does_NOT_fire_on_a_gardener_testing_soil():
    """★ THE FALSE POSITIVE THAT SENT ME TO REPAIR THE EXTRACTOR. The first version matched the
    WORD `test`, and fired on *"haven't changed anything to test it"* and *"keep meaning to get it
    tested properly"* — ordinary English about soil, in precisely the register the fixture needs.

    A checker that fires on a non-claim is repaired in the extractor, never silenced with an
    exemption, so what it matches now is a note describing its own artificiality. This asserts the
    repair did not go too far the other way: the natural sentences stay clean, and the artificial
    one is still caught."""
    natural = [dict(e, body="Haven't changed anything to test it, just watching. Keep meaning to "
                            "get the soil tested properly and never get round to it.")
               for e in _corpus()]
    assert not any("artificial" in p for p in dadtest.corpus_problems(natural))
    artificial = [dict(e, body="This is a test fixture. Sample data for testing purposes.")
                  for e in _corpus()]
    assert any("artificial" in p for p in dadtest.corpus_problems(artificial))


def test_the_gate_reads_HEADINGS_as_well_as_bodies():
    """The corpus states both of its explicit reversals in HEADINGS — that is where a person puts
    the sharpest thing they have to say. Scanning bodies only reported ONE reversal on a corpus
    carrying three, which would have rejected a good fixture and invited the wrong repair: lowering
    the bar to fit."""
    heads_only = [{"day": e["day"], "stem": e["stem"], "heading": e["heading"],
                   "body": "the weather was ordinary and nothing much happened today"}
                  for e in _corpus()]
    problems = dadtest.corpus_problems(heads_only)
    assert not any("decision reversed" in p for p in problems), problems


def test_PART_A_is_untouched_when_no_corpus_is_passed():
    """Part B's fixture seam may not move Part A's numbers. Part A's three criteria were measured
    at low load over the generated filler and are cited in the README; if `--entries` changed the
    default path, those numbers would quietly become claims about a different run.

    Structural, and it says so: the behavioural proof is a 90-day run of the real hooks, which is
    the `slow` suite. What is asserted here is that the selection is a plain either/or on the
    argument, with `Author` — the simulator's own — on the side where no corpus is given."""
    import ast, inspect, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(dadtest.run)))
    picks = [n for n in ast.walk(tree) if isinstance(n, ast.IfExp)
             and "PersonaAuthor" in ast.unparse(n)]
    assert len(picks) == 1, [ast.unparse(n) for n in picks]
    pick = picks[0]
    assert ast.unparse(pick.body).startswith("PersonaAuthor"), ast.unparse(pick)
    assert ast.unparse(pick.orelse) == "Author(sb, seed)", ast.unparse(pick.orelse)
    assert ast.unparse(pick.test) == "entries", ast.unparse(pick.test)


# ================================================== Part B's two instrument repairs =============
personas = _load(PLUGIN / "eval" / "dadtest" / "personas.py", "_test_personas_module")


def test_a_FILENAME_is_not_a_question():
    """★ THE INSTRUMENT MANUFACTURED THE THING IT COUNTS. The sentence splitter broke on every
    `.`, so a reply mentioning `Position.md` yielded the "question" **``md`)?``** — and three of
    the nine questions in this row's first count were fragments of that kind.

    An instrument that invents findings fails in the direction that LOOKS like a result, which is
    the direction nobody double-checks. A sentence boundary is a stop followed by space; a filename
    is not."""
    reply = "I read the notes (they are in `Position.md`)?\nShall I add it to Canon.md?"
    qs = personas.questions_in(reply)
    assert not any(q.strip().startswith("md") for q in qs), qs
    assert any("Shall I add it" in q for q in qs), qs


def test_a_real_question_after_a_sentence_is_still_counted():
    """The negative control: tightening the boundary must not stop the counter counting. A stop
    followed by a space still ends a sentence, and the question after it is still a question."""
    qs = personas.questions_in("I have written the note. Do you want me to add a second one?")
    assert len(qs) == 1 and "second one" in qs[0], qs


def test_strip_rules_removes_the_RULES_and_keeps_the_VAULT():
    """The control arm's whole validity is this one operation. If it removed the vault content too,
    the arm would measure a session with no memory — a different question — and the subtraction
    would silently be between two unlike things."""
    payload = ("=== @-import chain ===\n## slugs got the lettuce again\nthe usual\n\n"
               "The operating rules for this vault (from the plugin; they are how you write to it):\n"
               "# Operating rules — how a session uses the vault\nA decision becomes settled → Canon.\n")
    stripped, found = personas.strip_rules(payload)
    assert found is True
    assert "Operating rules" not in stripped and "how a session uses the vault" not in stripped
    assert "slugs got the lettuce" in stripped, "the vault content must survive"


def test_strip_rules_REPORTS_when_the_marker_is_missing():
    """If the payload's shape ever changes, the control arm must REFUSE rather than run the same
    input as the real arm and report a subtraction of a thing from itself — a zero that looks like
    a measurement."""
    stripped, found = personas.strip_rules("no marker here at all")
    assert found is False and stripped == "no marker here at all"


def test_each_arm_RECORDS_the_vault_it_read(tmp_path):
    """★ THE SUBTRACTION'S HIDDEN PRECONDITION, MADE CHECKABLE.

    Part B's headline compares two arms, and that is only sound if both read the SAME vault. They
    did — but only because the probes' writes never landed, `acceptEdits` not reaching files
    outside the session's cwd. A harness limitation was doing the work of an isolation guarantee,
    and the day it is fixed the second arm would read the first arm's edits while the result kept
    exactly the same shape.

    So each arm records a fingerprint of the vault it saw. A reader comparing two result files can
    CHECK the precondition instead of inheriting it."""
    vault = tmp_path / "vault" / "region"
    vault.mkdir(parents=True)
    (vault / "Position.md").write_text("# Position\n\n## a note\nbody\n", encoding="utf-8")
    sb = SimpleNamespace(vault=tmp_path / "vault")

    first = personas.sandbox_fingerprint(sb)
    assert first["vault_md_files"] == 1 and len(first["vault_md_sha256"]) == 16
    assert personas.sandbox_fingerprint(sb) == first, "the same vault must fingerprint the same"

    (vault / "Position.md").write_text("# Position\n\n## a note\nEDITED\n", encoding="utf-8")
    assert personas.sandbox_fingerprint(sb) != first, (
        "a vault edited between the arms must be visible in the fingerprint, or the subtraction "
        "is between unlike things and says so nowhere")


def test_the_fingerprint_survives_an_unreadable_vault():
    """It runs at the end of a paid run. A fingerprint that raised would lose ten sessions' worth
    of replies to a bookkeeping error."""
    out = personas.sandbox_fingerprint(SimpleNamespace(vault=Path("/nonexistent-vault-xyz")))
    assert "vault_unreadable" in out or out.get("vault_md_files") == 0, out


# -------------------------------------------- the fixture vault says what it is ----
# ★ TEMPLATESHAPE-1. Part B's fixture is somebody's own notes, and it was being written into a
# region whose role files were born software-project-shaped. `--entries` IS that statement, so it
# carries the shape rather than leaving it to a second flag a future runner will forget.

def test_entries_makes_the_fixture_vault_a_notes_vault_and_part_A_stays_a_project():
    """The rule itself, not a grep of it. Part A (no entries) must not move by a byte."""
    assert dadtest.fixture_shape(None) == "project"
    assert dadtest.fixture_shape([{"day": 2}]) == "notes"
    assert dadtest.fixture_shape([{"day": 2}], "study") == "study", "an explicit flag still wins"
    assert dadtest.fixture_shape(None, "notes") == "notes"


def test_the_sandbox_really_builds_a_vault_in_the_shape_it_was_given(tmp_path):
    """BEHAVIOURAL, because the thing that can go wrong is silent: init's own fallback is
    `project`, so a sandbox that fails to pass the flag still installs successfully and quietly
    stops being the fixture it claims to be. Asserting on the file that is born is the only check
    that can tell those apart — a source grep is satisfied by a comment."""
    notes = sim.Sandbox(tmp_path / "n", region="allotment", shape="notes")
    notes.install()
    born = (notes.vault / "allotment" / "Position.md").read_text(encoding="utf-8")
    assert "## Shipped" not in born and "## Where it stands" in born

    default = sim.Sandbox(tmp_path / "d")
    assert default.shape == "project", "Part A's default must not move"
    default.install()
    assert "## Shipped" in (default.vault / "project" / "Position.md").read_text(encoding="utf-8")
