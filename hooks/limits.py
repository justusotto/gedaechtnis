#!/usr/bin/env python3
"""limits.py — the one reader of `rules/limits.json`. No number this package acts on is written twice.

Every threshold the plugin enforces lives in that file, with its reasoning beside it in a `_`-prefixed
sibling key. This module is the only thing that opens it. The rule it exists to make mechanical is the
one the vault's own hooks broke repeatedly: a constant inlined at a call site and a constant in a
config file drift, and the drift is silent because both sides still run.

Three properties, each of which a test pins:

  * **Defaults live HERE, not at the call site.** A malformed or missing `limits.json` must never take
    a session down — the same discipline `config.py` follows for the config file — so a fallback table
    is carried in `DEFAULTS`. It is the SHIPPED file's content; `test_limits.py` asserts the two agree,
    so the fallback cannot quietly become a second, older opinion.
  * **A `_`-prefixed key is documentation.** `get()` never returns one, and the loader does not
    validate them: prose is free to change without a code change.
  * **Nothing is cached across processes.** Each hook invocation is one process and reads once; the
    module-level cache exists so two arms of the same hook do not stat the file twice.
"""
from __future__ import annotations
import json, os
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
# GEDAECHTNIS_LIMITS points the loader at another file. It exists for the same reason every other
# path in this package comes from an env-overridable seam: a suite that had to exercise a threshold
# by actually reaching it would need 301 commits per assertion, and a suite that instead reached
# into the shipped file would be editing the product to test it. The shipped VALUES are pinned
# separately, by `test_limits.py`, against `DEFAULTS`.
LIMITS_PATH = Path(os.path.expanduser(os.environ.get("GEDAECHTNIS_LIMITS")
                                      or str(PLUGIN_ROOT / "rules" / "limits.json")))

# The shipped file's values, carried so a session survives a missing or malformed limits.json.
# Kept in sync BY A TEST, never by memory — see the module docstring.
DEFAULTS = {
    "boot_budget_bytes": 20000,
    "boot_budget_warn_bytes": 20000,
    "boot_budget_warn_lines": 200,
    "compaction_floor_share": 0.4,
    "role_soft_limits_lines": {
        "Map": 100, "Vision": 150, "Position": 250, "Course": 300,
        "Aporia": 200, "Errata": 400, "Annales": 500, "Canon": 800,
    },
    "max_searchable_file_bytes": 2000000,
    "max_memory_file_bytes": 500000,
    "stale_entry_days": 56,
    "cleanup_trigger_days": 14,
    "cleanup_trigger_commits": 300,
    "synthesis_trigger_days": 14,
    "synthesis_trigger_entries": 20,
}

_CACHE: dict | None = None


def _load() -> dict:
    """The file's keys over the defaults. Unreadable or non-object → the defaults alone."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    merged = dict(DEFAULTS)
    try:
        data = json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if not k.startswith("_"):
                    merged[k] = v
    except (OSError, ValueError):
        pass
    _CACHE = merged
    return merged


def get(key: str, default=None):
    """One threshold. A `_`-prefixed key is prose and is never a value: asking for one raises,
    because a call site that read documentation as a number would do so silently forever."""
    if key.startswith("_"):
        raise KeyError(f"{key!r} is a documentation key, not a threshold")
    val = _load().get(key, default)
    if val is None and default is None:
        raise KeyError(f"no such limit: {key!r}")
    return val


def all_limits() -> dict:
    """Every threshold, prose excluded. For a status/report surface that prints the table."""
    return {k: v for k, v in _load().items() if not k.startswith("_")}


def _reset_for_tests() -> None:
    global _CACHE
    _CACHE = None
