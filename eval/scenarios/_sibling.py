"""How the scenario modules find each other, and why it is not `import harness`.

`eval/scenarios/` and `eval/safety/` each contain a file called `harness.py`, and `eval/simulator/`
and this directory each contain a `run.py`. A bare `import harness` binds whichever of the two the
interpreter loaded FIRST — `sys.path` is consulted only when `sys.modules` has no entry — so in a
process that has already touched the safety bench, `harness.UnsupportedKnob` raises AttributeError
and the scenario sweep dies inside its own error handler. That is not hypothetical: the full suite
failed exactly this way (`test_scenarios.py` 3 failed, `test_safety_eval.py` 1 error, in the same
run), while every scenario test passed in isolation. The producing session met the `run` half of
this collision and fixed it in the TEST loader only, one level above where the bare imports live.

So every sibling import in this directory goes through `load()`, which binds the module under the
unique name `splitsim_<stem>` regardless of what else is in the process. `test_scenarios.py` uses
the same names, so the test suite and the CLI share one module identity rather than two copies.
Nothing here ever registers a bare `harness`, `scenarios`, `vaultgen` or `run` — which is also what
keeps `eval/safety/run_safety.py`'s own `import harness` correct.

This file is imported by path by its own callers for the same reason; `_sibling` is a name nothing
else in the tree uses, so it is safe as a bare import once this directory is on `sys.path`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIX = "splitsim_"


def load(stem: str):
    """Import `eval/scenarios/<stem>.py` as `splitsim_<stem>`, once per process."""
    alias = PREFIX + stem
    existing = sys.modules.get(alias)
    if existing is not None:
        return existing
    path = HERE / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:        # raise, never assert: a silent None here would
        raise ImportError(f"cannot load {path}")   # surface as a missing attribute much later
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod                       # before exec, so a sibling cycle resolves
    spec.loader.exec_module(mod)
    return mod
