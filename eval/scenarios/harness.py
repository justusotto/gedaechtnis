#!/usr/bin/env python3
"""harness.py — drive the REAL plugin code paths against a generated vault, under a chosen rule.

    world = World(tmp_dir)                    # a vault + the env seam, nothing else
    found = propose(world, limits={"split_min_remaining_entries": 6})
    receipt = apply(world, region, cluster_id, name)

**Never a reimplementation.** Every proposal comes out of `split.py --json` run as a subprocess with
the sandbox env — the same entry point `/gedaechtnis-split` uses — and every apply out of
`split.py --apply`. The harness reads the product's JSON; it computes nothing the product computes.

**The env seam, by PREFIX.** Every `GEDAECHTNIS_*` name inherited from the caller is CLEARED, then the
five the product reads are pointed into the sandbox. This is the rule `eval/simulator/run.py`'s
`Sandbox.env` arrived at after A1's reviewer found an inherited `GEDAECHTNIS_LIMITS` silently changing
what a "sandboxed" run measured; it is carried here as the RULE (clear the prefix), not as the list.

**A rule variant is a limits FILE.** `split.py` reads its thresholds through `limits.py`, so a variant
is a JSON file the run writes — the product is exercised exactly as an owner re-tuning
`rules/limits.json` would exercise it. The product's JSON echoes the thresholds it applied
(`min_entries`, `min_remaining`, and — where the rule under test carries them — `min_remaining_share`,
`min_cohesion`); `propose()` REFUSES to return a result under a variant the product did not echo, so a
knob the shipped code does not know cannot be reported as "tested" with no effect.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
SPLIT = PLUGIN / "split.py"

# The shipped defaults for every key a split scenario can vary. Written in full into the variant's
# limits file so the FILE path is exercised for every key, never the `DEFAULTS` fallback.
BASE_LIMITS = {
    "split_min_entries": 8,
    "split_min_remaining_entries": 4,
}

# Which echoed field proves the product applied which knob.
ECHO = {
    "split_min_entries": "min_entries",
    "split_min_remaining_entries": "min_remaining",
    "split_min_remaining_share": "min_remaining_share",
    "split_min_cohesion": "min_cohesion",
    # SPLITVOCAB-1's vault-wide axis. Echoed, so a rule that does not carry it reports `n/a` for
    # the variant rather than "set it and nothing changed" — the difference between a knob that is
    # off and a knob that is not there.
    "split_boilerplate_region_share": "boilerplate_region_share",
}


class UnsupportedKnob(RuntimeError):
    """The rule under test did not echo a threshold this run set — the knob is not in that code."""


class World:
    def __init__(self, root: Path, vault: Path | None = None):
        self.root = Path(root)
        self.vault = Path(vault) if vault else self.root / "vault"
        self.vault.mkdir(parents=True, exist_ok=True)
        self.state = self.root / "state"
        self.limits_file = self.root / "limits.json"
        self.repo = self.root / "repo"
        self.repo.mkdir(exist_ok=True)
        (self.repo / ".atlas-lane").write_text("lane: SCENARIO\npath: Proj/\n", encoding="utf-8")

    def env(self) -> dict:
        e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
        e.update({
            "GEDAECHTNIS_VAULT": str(self.vault),
            "GEDAECHTNIS_STATE_DIR": str(self.state),
            "GEDAECHTNIS_LIMITS": str(self.limits_file),
            "GEDAECHTNIS_FLEET_ROSTER": str(self.root / "no-such-roster.md"),
            "GEDAECHTNIS_USER_MEMORY": str(self.root / "no-such-user-memory.md"),
            "GEDAECHTNIS_CONFIG": str(self.root / "no-such-config.json"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        return e

    def set_limits(self, limits: dict | None) -> dict:
        merged = dict(BASE_LIMITS)
        merged.update(limits or {})
        self.limits_file.write_text(json.dumps(merged), encoding="utf-8")
        return merged


def _run(world: World, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(SPLIT), *args], capture_output=True,
                          text=True, env=world.env(), timeout=timeout, cwd=str(world.repo),
                          stdin=subprocess.DEVNULL)


def propose(world: World, limits: dict | None = None) -> dict:
    """The product's proposals under `limits`. Raises `UnsupportedKnob` if a knob the run set is not
    echoed back — that is the rule saying "I do not have this threshold", and it must not read as
    "this threshold changed nothing"."""
    merged = world.set_limits(limits)
    p = _run(world, "--json")
    if p.returncode != 0:
        raise RuntimeError(f"split.py --json rc={p.returncode}\n{p.stdout}\n{p.stderr}")
    found = json.loads(p.stdout)
    for key, val in merged.items():
        echo = ECHO.get(key)
        if echo is None:
            continue
        if echo not in found:
            if key in BASE_LIMITS:
                raise RuntimeError(f"product did not echo {echo!r} — the harness's contract broke")
            raise UnsupportedKnob(f"{key}={val!r} set, but the rule under test echoes no {echo!r}")
        if found[echo] != val:
            raise RuntimeError(f"product echoed {echo}={found[echo]!r}, run set {val!r}")
    return found


def apply(world: World, region: str, cluster_id: str, name: str,
          limits: dict | None = None) -> tuple[int, str]:
    """`split.py --apply`. Returns (rc, combined output). rc 0 = applied, 1 = refused, 2 = bad name."""
    world.set_limits(limits)
    p = _run(world, "--apply", region, cluster_id, "--name", name)
    return p.returncode, p.stdout + p.stderr


def render(world: World, limits: dict | None = None) -> str:
    """The human rendering, for the controls that pin what the footer offers."""
    world.set_limits(limits)
    p = _run(world)
    return p.stdout + p.stderr


def region_bytes(vault: Path, region: str) -> dict:
    """Every file's bytes under one region — the conservation snapshot around an apply."""
    rd = vault / region
    return {str(p.relative_to(vault)): p.read_bytes() for p in sorted(rd.rglob("*")) if p.is_file()}
