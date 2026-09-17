"""Suite-wide guard: the tests must not touch the machine's real files.

The whole guard is `vault_sentinel.py`, including the three autouse fixtures re-exported below.
It lives there and not here so the positive control (`test_vault_sentinel.py`) can install THESE
objects into a throwaway suite and watch them bite — a control that exercises a copy of a guard
proves something about the copy. This file therefore adds nothing, and the control asserts that
it still adds nothing.

Why the guard exists, what it watches, and the two things it cannot see: `vault_sentinel.py`.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vault_sentinel import (        # noqa: E402,F401  — imported for their fixture side effect
    real_files_unchanged_session,
    real_files_unchanged_module,
    real_files_unchanged_function,
)
