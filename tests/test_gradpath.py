"""test_gradpath.py — GRADPATH-1: the over-budget boot chain gets a way out, in words.

## The loop this row closes (row A1 §3, measured)

`maintenance`'s `boot_bytes` arm fires when the @-import chain crosses its budget, so a cleanup is
due every session. `cleanup.propose()` then SKIPS the file that grew, because `bootfile.is_protected`
says a file a session LOADS is not tidied by an unattended pass. And `bootfile.roll_window`, the one
thing that compacts a boot file, needs a `Kernel.md` with a declared window — which a fresh,
ungraduated install has neither of. At `medium` load the A1 harness measured 80 of 90 days over the
budget, peaking at 191,029 B, with nothing able to act on any of them.

**Nothing here compacts a boot-chain body.** That is the 00:43 incident class and the row forbids it
outright. What ships is a PROPOSAL and a sentence: the pass names the region whose Boot file would
end the loop, and the facts line at the next session start says the same thing in the same breath as
the trigger it cannot satisfy.

## The controls

Every test below runs against a REAL installed sandbox and drives the REAL modules in a subprocess,
because `bootfile.always_loaded` reads the chain from the environment and asking it in this test
runner's own process answers about the wrong machine. The negative controls are the point:

- a region that already HAS a Boot file gets no proposal and no sentence — **while the chain is
  still over budget**, which is what makes it a control of the Boot-file condition rather than of
  the budget condition (asserted explicitly, so it cannot pass by never reaching the branch);
- a chain UNDER budget gets no proposal from a region with no Boot file — the other condition,
  bracketed the same way;
- `apply()` never moves one, and the vault is byte-identical afterwards.

Every one of them was mutation-checked: see `ROW-GRADPATH-1.md` for which rule was neutered for
which test and what went red.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "eval" / "simulator"))
sys.path.insert(0, str(PLUGIN))

import run as sim  # noqa: E402  eval/simulator/run.py — the installed Sandbox

# Comfortably past `boot_budget_bytes` (20,000) without reading the number here: the test asserts
# the module's own answer to "is it over budget", never a threshold of its own.
BIG = 90_000


def _sandbox(tmp_path, *, size: int, boot_file: bool, through_symlink: bool = False):
    """A real installed sandbox whose region's `Position.md` is `size` bytes.

    `through_symlink` spells every path through a symlinked parent — the `/tmp` vs `/private/tmp`
    case on macOS, and the one under which a resolved-vault / unresolved-region mismatch prints a
    `../../…` traversal where a region name belongs. pytest's own `tmp_path` is already resolved,
    so without this the branch is never reached and any assertion about it is green about nothing."""
    root = tmp_path / "box"
    if through_symlink:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real, target_is_directory=True)
        root = link / "box"
    sb = sim.Sandbox(root)
    sb.install()
    pos = sb.role("Position")
    pos.parent.mkdir(parents=True, exist_ok=True)
    pos.write_text("# Position\n\n## state\n\n" + "y" * size + "\n", encoding="utf-8")
    if boot_file:
        (sb.vault / sb.region / "Kernel.md").write_text(
            "# Boot\n\n<!-- gedaechtnis:window -->\n\n## resume\n\nnow\n"
            "<!-- gedaechtnis:/window -->\n", encoding="utf-8")
    return sb


PROBE = """
found = cleanup.propose()
grad = [q for q in found["proposals"] if q["kind"] == "graduate"]
doc = maintenance.compute(maintenance.first_run_state("2026-01-01"), os.getcwd(), "2026-06-01")
lines = maintenance.facts_lines(doc)
cg = doc["boot_file"]["chain_graduation"]
print(json.dumps({
    "graduate": grad,
    "over_budget": cg["over_budget"],
    "chain_bytes": cg["chain_bytes"],
    "chain_regions": cg["regions"],
    "payload_regions": [g["region"] for g in doc["boot_file"]["graduation"]],
    "boot_bytes_arm_fired": doc["cleanup"]["arms"]["boot_bytes"]["fired"],
    "cleanup_due": doc["cleanup"]["due"],
    "cleanup_lines": [l for l in lines if l.startswith("- Maintenance: a cleanup pass is due")],
    "boot_lines": [l for l in lines if l.startswith("- Boot file:")],
    "region": os.environ["REGION"],
}))
"""


PRELUDE = ("import json, os, sys\n"
           f"sys.path[:0] = [{str(PLUGIN)!r}, {str(PLUGIN / 'hooks')!r}]\n"
           "import bootfile, cleanup, maintenance, config\n")


def _probe(sb, code: str) -> dict:
    """Run `code` inside the sandbox's own environment; read the JSON on its last stdout line.

    A SUBPROCESS, never an in-process call: `bootfile.always_loaded` resolves the @-import chain
    from the environment, so asking it here would answer about this test runner's own machine."""
    p = subprocess.run([sys.executable, "-B", "-c", PRELUDE + code],
                       env={**sb.env, "REGION": sb.region}, cwd=str(sb.repo),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


# ----------------------------------------------------------------- positive ----
def test_an_over_budget_chain_with_no_boot_file_is_PROPOSED_a_graduation(tmp_path):
    """POSITIVE CONTROL. A fresh install whose `Position.md` has grown past the chain budget: the
    cleanup pass proposes graduation for that region, and the facts line names the remedy."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False)
    out = _probe(sb, PROBE)

    assert out["over_budget"] is True, out
    assert out["boot_bytes_arm_fired"] is True, out
    assert len(out["graduate"]) == 1, out["graduate"]
    g = out["graduate"][0]
    assert g["region"] == out["region"]
    assert g["action"] == "report"
    assert g["has_boot_file"] is False
    assert any(f.endswith("Position.md") for f in g["files"]), g
    assert "Kernel.md with a declared window" in g["remedy"]
    assert "@-import" in g["remedy"], "a Kernel.md nothing imports is not a remedy"

    assert len(out["cleanup_lines"]) == 1, out["cleanup_lines"]
    line = out["cleanup_lines"][0]
    assert "cannot bring that down" in line, line
    # EQUALITY, not a substring of the line: `region in line` stays true when the region is
    # printed as `../../…/vault/project`, which is exactly the defect this pins.
    assert f"{out['region']} is in the chain" in line, line
    assert "@-import" in line, line


def test_the_region_name_survives_a_SYMLINKED_vault(tmp_path):
    """★ `/tmp` against `/private/tmp`. `regions()` hands back paths spelled as the module's own
    VAULT; resolving the vault on one side only sends `_rel` down its `os.path.relpath` fallback
    and prints `../../…/vault/project` as the region's NAME — in the facts line, the proposal and
    the receipt, beside a second facts line naming the same region correctly."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False, through_symlink=True)
    out = _probe(sb, PROBE)

    assert out["over_budget"] is True
    assert out["graduate"], out
    assert out["graduate"][0]["region"] == out["region"], out["graduate"][0]["region"]
    assert ".." not in out["cleanup_lines"][0], out["cleanup_lines"][0]
    # The pre-existing payload arm is the second instrument: the two must agree on the name.
    assert out["payload_regions"] == [] or out["payload_regions"] == [out["region"]], out


def test_one_proposal_per_region_not_one_per_protected_file(tmp_path):
    """A region with several protected files in the chain is one region to graduate, not three
    findings. Without the dedupe the pass would print the same remedy once per file."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False)
    claude = sb.repo / "CLAUDE.md"
    extra = sb.vault / sb.region / "Course.md"
    extra.write_text("# Course\n\n## plan\n\n" + "z" * 5_000 + "\n", encoding="utf-8")
    claude.write_text(claude.read_text(encoding="utf-8") + f"\n@{extra}\n", encoding="utf-8")
    out = _probe(sb, PROBE)
    assert out["over_budget"] is True
    assert len(out["graduate"]) == 1, out["graduate"]
    assert sorted(f.rsplit("/", 1)[-1] for f in out["graduate"][0]["files"]) == \
        ["Course.md", "Position.md"], out["graduate"][0]["files"]


# ---------------------------------------------- a Kernel.md alone is not the remedy ----
def test_a_boot_file_that_nothing_IMPORTS_does_not_silence_the_finding(tmp_path):
    """★ THE STATE THAT LOOKS FIXED AND IS NOT. Take the remedy's advice — write the `Kernel.md` —
    and the chain does not move by a byte, because the repo's `CLAUDE.md` still @-imports the
    bodies and nothing in this package rewrites an import line.

    The first version of this row EXCLUDED such a region, on the reasoning that it "has the
    remedy". The arm then goes on firing every session with the sentence explaining it GONE, which
    is worse than the defect the row set out to fix: a silenced loop rather than a loud one. So the
    finding stays, and the Boot file's presence changes only WHICH HALF of the remedy is left."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=True)
    out = _probe(sb, PROBE)

    assert out["over_budget"] is True, "the test is vacuous unless the chain is still over budget"
    assert out["boot_bytes_arm_fired"] is True, out
    assert len(out["graduate"]) == 1, out["graduate"]
    g = out["graduate"][0]
    assert g["has_boot_file"] is True
    assert "already exists" in g["remedy"], g["remedy"]
    assert "point the @-import" in g["remedy"], g["remedy"]
    assert "cannot bring that down" in out["cleanup_lines"][0]


def test_the_remedy_NAMES_THE_COMMAND_that_performs_it_in_both_states(tmp_path):
    """A remedy that describes work no shipped command can do is where this loop started.

    GRADPATH-1 made the pass propose graduation; GRADPATH-2 shipped the path that performs it. Until
    the sentence a person actually reads says `/gedaechtnis-graduate`, the two halves are connected
    only in the head of whoever built them — and the failure mode is silent: the finding fires every
    session, correctly, and nobody acts because nothing tells them what to run.

    Asserted in BOTH states, because they are two different sentences and only one of them was ever
    going to be checked by accident."""
    for boot_file in (False, True):
        sb = _sandbox(tmp_path / f"bf-{boot_file}", size=BIG, boot_file=boot_file)
        out = _probe(sb, PROBE)
        assert out["graduate"], (boot_file, out)
        remedy = out["graduate"][0]["remedy"]
        assert "/gedaechtnis-graduate" in remedy, (boot_file, remedy)


def test_the_remedy_does_not_claim_the_package_graduates_a_region_ON_ITS_OWN(tmp_path):
    """The negative control for the sentence above, and the one that matters more.

    Naming a command is one edit away from promising it runs by itself. Nothing in this package
    writes a Boot file or rewrites an import line unprompted, and the remedy must keep saying so —
    a user who believes the vault is graduating itself will read the same finding for ninety days
    and conclude the tool is broken."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False)
    remedy = _probe(sb, PROBE)["graduate"][0]["remedy"]
    assert "until you say so" in remedy or "when you apply it" in remedy, remedy
    for promise in ("has been written for you", "will be written", "automatically", "has graduated"):
        assert promise not in remedy, (promise, remedy)


# ----------------------------------------------------------------- negative ----
def test_a_region_whose_files_are_NOT_in_the_chain_gets_NO_proposal(tmp_path):
    """NEGATIVE CONTROL on membership, and the one that makes the mechanism mean something. A
    SECOND region sits in the vault, bigger than the first and with no Boot file — and nothing
    @-imports it. The chain is over budget (asserted), the first region is named, and the second
    is not: what is costing a session context is what it LOADS, never what is on disk near it.

    Without this, a version that named every region with no Boot file would pass every other test
    in this file."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False)
    quiet = sb.vault / "Quiet"
    quiet.mkdir(parents=True, exist_ok=True)
    (quiet / "Map.md").write_text("# Quiet\n", encoding="utf-8")
    (quiet / "Position.md").write_text("# Position\n\n## q\n\n" + "q" * (BIG * 2) + "\n",
                                       encoding="utf-8")
    out = _probe(sb, PROBE)

    assert out["over_budget"] is True, out["chain_bytes"]
    assert [g["region"] for g in out["graduate"]] == [out["region"]], out["graduate"]
    assert [r["region"] for r in out["chain_regions"]] == [out["region"]], out["chain_regions"]
    assert "Quiet" not in out["cleanup_lines"][0], out["cleanup_lines"][0]


def test_a_chain_under_budget_gets_NO_proposal_even_with_no_boot_file(tmp_path):
    """NEGATIVE CONTROL on the budget condition. A region with no Boot file is an ordinary vault,
    not a finding: it becomes one only once the chain it sits in has crossed the budget."""
    sb = _sandbox(tmp_path, size=100, boot_file=False)
    out = _probe(sb, PROBE)

    assert out["over_budget"] is False, out["chain_bytes"]
    assert out["graduate"] == [], out["graduate"]
    assert out["cleanup_lines"] == [] or "cannot bring that down" not in out["cleanup_lines"][0]


# ------------------------------------------------------------- nested regions ----
def test_a_nested_region_is_charged_to_the_NEAREST_region_only(tmp_path):
    """Vaults nest: a region can hold a sub-region, and both carry a `Map.md` — the vault this grew
    from has several. Charging a chain member to every region above it double-counts the bytes (the region
    totals then exceed the chain they came from) and names an ancestor whose Boot file would not
    replace that import at all. It also made the facts line and the receipt disagree about how many
    regions there are."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False)
    sub = sb.vault / sb.region / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "Map.md").write_text("# Sub\n", encoding="utf-8")
    (sub / "Position.md").write_text("# Position\n\n## s\n\n" + "q" * 30_000 + "\n",
                                     encoding="utf-8")
    claude = sb.repo / "CLAUDE.md"
    claude.write_text(claude.read_text(encoding="utf-8") + f"\n@{sub / 'Position.md'}\n",
                      encoding="utf-8")
    out = _probe(sb, PROBE)

    regions = {r["region"]: r for r in out["chain_regions"]}
    assert set(regions) == {out["region"], f"{out['region']}/sub"}, regions
    assert sum(r["bytes"] for r in regions.values()) <= out["chain_bytes"], regions
    assert regions[f"{out['region']}/sub"]["files"] == [f"{out['region']}/sub/Position.md"]
    # and the receipt agrees with the facts line: one proposal per named region
    assert sorted(g["region"] for g in out["graduate"]) == sorted(regions), out["graduate"]


# ------------------------------------------------------- not said twice ----
def test_the_payload_arm_does_not_repeat_a_region_the_cleanup_line_named(tmp_path):
    """Two arms, two questions — a region's own payload, and the chain. For a region in both they
    printed the same region, the same remedy and the same closing clause one line apart. One
    carrier per fact: the chain line stays, because it is the one attached to a trigger that fires
    every session."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False)
    out = _probe(sb, PROBE)

    assert out["over_budget"] is True
    # The payload arm still COMPUTES the region — it is a true fact about the region's own bytes,
    # and suppressing the measurement would be a different and worse fix. What is suppressed is the
    # second LINE saying it, so the reader meets one carrier for one fact.
    assert out["payload_regions"] == [out["region"]], out["payload_regions"]
    repeated = [l for l in out["boot_lines"]
                if l.startswith(f"- Boot file: {out['region']} carries")]
    assert repeated == [], repeated
    assert "cannot bring that down" in out["cleanup_lines"][0]


# -------------------------------------------------------------- never applied ----
APPLY_PROBE = """
import hashlib
pos = config.vault() / os.environ["REGION"] / "Position.md"
before = hashlib.sha256(pos.read_bytes()).hexdigest()
found = cleanup.propose()
receipt = cleanup.apply(found)
after = hashlib.sha256(pos.read_bytes()).hexdigest()
dry = [q for q in found["proposals"] if q["kind"] not in cleanup.REPORT_ONLY_KINDS]
print(json.dumps({
    "graduation": receipt.get("graduation"),
    "moved": [m.get("path") for m in receipt.get("moved") or []],
    "folded": [f.get("path") for f in receipt.get("folded") or []],
    "would_change": [q["kind"] for q in dry],
    "unchanged": before == after,
    "region": os.environ["REGION"],
}))
"""


def test_apply_never_moves_a_graduation_and_leaves_the_boot_chain_byte_identical(tmp_path):
    """The proposal is a REPORT. `apply()` reaches no path that could act on it, the file that
    breached the budget is byte-identical afterwards, and a dry run does not count it as a change
    that would be applied."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False)
    out = _probe(sb, APPLY_PROBE)

    assert len(out["graduation"] or []) == 1, out
    assert out["unchanged"] is True, "a graduation proposal must never rewrite the file it names"
    assert out["region"] not in (out["moved"] or []) + (out["folded"] or [])
    assert "graduate" not in out["would_change"], out["would_change"]


# ------------------------------------------------------------------ the receipt ----
README_PROBE = """
rel = os.environ["REGION"]
found = cleanup.propose()
receipt = cleanup.apply(found)
page = ""
if receipt.get("bundle"):
    f = config.vault() / receipt["bundle"] / cleanup.README_NAME
    page = f.read_text(encoding="utf-8") if f.is_file() else ""
print(json.dumps({
    "graduation": [g["region"] for g in receipt.get("graduation") or []],
    "page_has_section": "The boot chain, and what this pass could not do about it" in page,
    "page_claims_over_budget": "is over its budget" in page,
    "page": page[:0],
}))
"""


def test_the_receipt_page_carries_the_finding_when_there_IS_one(tmp_path):
    """POSITIVE CONTROL on the page a person actually opens."""
    sb = _sandbox(tmp_path, size=BIG, boot_file=False)
    out = _probe(sb, README_PROBE)
    assert out["graduation"], out
    assert out["page_has_section"] is True, out
    assert out["page_claims_over_budget"] is True, out


def test_the_receipt_page_does_NOT_claim_an_over_budget_chain_when_there_is_none(tmp_path):
    """★ NEGATIVE CONTROL, and the defect it pins was shipped: the section's paragraph asserted
    *"The @-import chain a session boots with is over its budget"* unconditionally, so every
    ordinary cleanup receipt — on a vault whose chain was 573 B under a 20,000 B budget — told its
    reader a false thing about their vault. A section with nothing in it does not get to assert the
    condition that would have filled it."""
    sb = _sandbox(tmp_path, size=100, boot_file=False)
    # something for the pass to actually do, so a bundle and its page exist at all
    dup = sb.vault / sb.region / "Errata.md"
    dup.write_text("# Errata\n\n## a\n\nbody\n\n## a\n\nbody\n", encoding="utf-8")
    out = _probe(sb, README_PROBE)

    assert out["graduation"] == [], out
    assert out["page_has_section"] is False, out
    assert out["page_claims_over_budget"] is False, out
