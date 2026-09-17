"""Every path this package writes is resolved WHEN IT IS USED, not when the module was imported.

## The bug this is the fence around

`config.VAULT = _path(...)` ran once, at first import, and the value was cached in `sys.modules` for
the life of the process. On 2026-09-15 a test set `GEDAECHTNIS_VAULT` and reloaded the module it was
testing — but `config` had been imported minutes earlier by an unrelated test, so the override was
read by nobody. The package compacted 263 files of the machine's real memory vault under a
1,500-byte test bound and opened 1,749 archive segments. Nothing was wrong with WHAT it wrote.

So the two tests below are different in kind, and both are needed:

  * `test_*_follows_the_environment_mid_process` is the BEHAVIOURAL proof — change the variable in a
    live process and watch every derived path move. It would have failed before the fix.
  * `test_no_module_level_resolved_path_constants` is the FENCE. The behavioural test passes for the
    modules it names; this one reads every module in the package and fails if a new constant appears
    anywhere, including in a file written next year by someone who never heard of the incident.
"""
from __future__ import annotations
import ast
import os
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "hooks"))


@pytest.fixture
def moved_vault(tmp_path, monkeypatch):
    v = tmp_path / "decoy-vault"
    (v / "Region").mkdir(parents=True)
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(v))
    return v


def test_config_follows_the_environment_mid_process(moved_vault):
    """The root. Both spellings — the accessor and the legacy constant name — must move."""
    import config
    assert config.vault() == moved_vault
    assert Path(config.VAULT) == moved_vault, "config.VAULT is still the value it had at import"


def test_common_follows_the_environment_mid_process(moved_vault):
    """The module every hook reads through. This is the exact name that was frozen."""
    import common
    assert Path(common.VAULT) == moved_vault
    assert Path(common.ROSTER) == moved_vault / "Global" / "fleet-roster.md", \
        "ROSTER is derived from the vault and must move with it"


def test_derived_paths_follow_too(moved_vault):
    """A path built FROM the vault at module level re-freezes the fix one level down, which is the
    quieter way this defect comes back."""
    import chore, logstore, views, ledger
    assert Path(chore.INDEX) == moved_vault / "Pharos" / "artifacts-index.md"
    assert Path(logstore.LOG_DIR) == moved_vault / ".gedaechtnis" / "log"
    assert Path(views.COLD_DIR) == moved_vault / ".gedaechtnis" / "views-cold"
    assert Path(ledger.LEDGER_DIR) == moved_vault / "Channels" / "ledger"


def test_home_move_reaches_every_path(tmp_path, monkeypatch):
    """A relocated HOME is what the canary run does, and the whole canary is worthless if the
    package quietly keeps resolving against the real one."""
    monkeypatch.delenv("GEDAECHTNIS_VAULT", raising=False)
    monkeypatch.delenv("GEDAECHTNIS_STATE_DIR", raising=False)
    monkeypatch.delenv("GEDAECHTNIS_CONFIG", raising=False)
    home = tmp_path / "home"
    (home / "Gedaechtnis").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    import config
    assert Path(config.VAULT) == home / "Gedaechtnis"
    assert Path(config.STATE) == home / ".claude" / "gedaechtnis"
    assert Path(config.CONFIG_PATH) == home / ".claude" / "gedaechtnis" / "config.json"
    assert Path(config.WORKTREES) == home / ".claude" / "worktrees"


# ---------------------------------------------------------------- the fence

# A module-level assignment whose value mentions any of these is a resolved path being frozen.
_PATHY = ("config.vault", "config.state", "config.home", "config.roster", "config.worktrees",
          "config.VAULT", "config.STATE", "config.HOME", "config.ROSTER", "config.WORKTREES",
          "common.VAULT", "common.STATE", "common.HOME", "common.ROSTER",
          "logstore.VAULT", "logstore.VIEW_DIR", "logstore.LOG_DIR")

# A path that genuinely cannot move during a process: it is derived from this file's own location
# on disk, not from anything a user or a test can point somewhere else.
_STATIC_OK = ("__file__",)


def _product_modules():
    for p in sorted(PLUGIN.rglob("*.py")):
        if "__pycache__" in p.parts or "/tests/" in str(p) or "/eval/" in str(p):
            continue
        yield p


def test_no_module_level_resolved_path_constants():
    """No module in this package may bind a resolved vault/state/home path at module level.

    This is the check that survives the people who read the incident report. `__getattr__` is the
    sanctioned spelling; an assignment shadows it and silently restores the cached constant, which
    is why the assignment itself — not the value — is what is refused."""
    offenders = []
    for p in _product_modules():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in tree.body:
            if not isinstance(n, (ast.Assign, ast.AnnAssign)):
                continue
            if n.value is None:
                continue
            # A LAMBDA is the sanctioned shape — `_ACCESSORS = {"VAULT": lambda: config.vault()}`
            # stores the resolver, not the resolution, and calling it is what makes the access
            # per-call. So the scan looks only at path expressions OUTSIDE any lambda body.
            outside = ast.Module(body=[ast.Expr(value=n.value)], type_ignores=[])
            for sub in ast.walk(outside):
                if isinstance(sub, ast.Lambda):
                    sub.body = ast.Constant(value=None)
            src = ast.unparse(outside)
            if any(k in src for k in _PATHY) and not any(k in src for k in _STATIC_OK):
                tgts = [n.target] if isinstance(n, ast.AnnAssign) else n.targets
                for t in tgts:
                    if isinstance(t, ast.Name):
                        offenders.append(f"{p.relative_to(PLUGIN)}:{n.lineno}  {t.id} = {src[:70]}")
    assert not offenders, (
        "A resolved path is bound at MODULE level again — the 2026-09-15 defect, exactly:\n  "
        + "\n  ".join(offenders)
        + "\n\nMake it a function and expose the old name through a PEP 562 `__getattr__`, as "
          "config.py / common.py / logstore.py do. A module-level assignment shadows `__getattr__`.")


def test_no_from_import_of_a_resolved_path():
    """`from common import VAULT` binds the value ONCE and reintroduces the bug even though
    `common.VAULT` itself is now per-call. The fence has to cover the spelling too."""
    frozen = {"VAULT", "STATE", "HOME", "ROSTER", "LOG_DIR", "VIEW_DIR", "START_FILE", "LEDGER_DIR",
              "USER_MEMORY", "WORKTREES", "CONFIG_PATH", "TOOL_ROOT", "INDEX", "COLD_DIR"}
    offenders = []
    for p in _product_modules():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        # MODULE level only. The same statement inside a function body re-executes on every call,
        # which is per-call resolution by another spelling — `bootfile.py` does exactly that and is
        # correct. Flagging it would train people to ignore this test.
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.col_offset == 0 \
               and n.module in ("config", "common", "logstore"):
                for a in n.names:
                    if a.name in frozen:
                        offenders.append(f"{p.relative_to(PLUGIN)}:{n.lineno}  from {n.module} import {a.name}")
    assert not offenders, (
        "A resolved path is imported BY NAME, which binds it once at import:\n  "
        + "\n  ".join(offenders)
        + "\n\nImport the module and read the attribute (`common.VAULT`) so PEP 562 re-resolves.")


# ---------------------------------------------------------------- the fence's own controls
# The two fences above pass. A fence that passes proves nothing until it has been shown to fail on
# the thing it is for — both of them are "assert not offenders", which is the exact shape that
# passes silently when the scan is broken and finds nothing at all.

def _scan_constants(paths):
    offenders = []
    for p in paths:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in tree.body:
            if not isinstance(n, (ast.Assign, ast.AnnAssign)) or n.value is None:
                continue
            outside = ast.Module(body=[ast.Expr(value=n.value)], type_ignores=[])
            for sub in ast.walk(outside):
                if isinstance(sub, ast.Lambda):
                    sub.body = ast.Constant(value=None)
            src = ast.unparse(outside)
            if any(k in src for k in _PATHY) and not any(k in src for k in _STATIC_OK):
                offenders.append(f"{p.name}:{n.lineno}")
    return offenders


def test_the_constant_fence_bites(tmp_path):
    """Plant the 2026-09-15 defect and watch the scan name it."""
    bad = tmp_path / "relapse.py"
    bad.write_text("import config\nVAULT = config.vault()\n", encoding="utf-8")
    assert _scan_constants([bad]), "the constant fence did not fire on a planted frozen constant"


def test_the_constant_fence_stays_quiet_on_the_sanctioned_shape(tmp_path):
    """And the negative half: the accessor table this package actually uses must NOT trip it, or
    the fence is noise and gets deleted by the first person it inconveniences."""
    good = tmp_path / "fine.py"
    good.write_text("import config\n_ACCESSORS = {'VAULT': lambda: config.vault()}\n"
                    "def vault():\n    return config.vault()\n", encoding="utf-8")
    assert not _scan_constants([good]), "the fence fired on the sanctioned per-call shape"


def test_the_fence_is_actually_reading_the_package():
    """The failure mode that reads as a pass: a scan whose file list is empty finds no offenders and
    reports green forever."""
    mods = list(_product_modules())
    assert len(mods) >= 20, f"the fence is scanning only {len(mods)} modules — it is not reading the package"
    assert any(m.name == "config.py" for m in mods)
    assert any(m.name == "common.py" for m in mods)
