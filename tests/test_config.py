"""hooks/config.py — the one place a path is resolved, so the one place worth proving.

Every case runs in a subprocess with `HOME` pointed at tmp_path, because `~` is read from the
environment: that is what lets this file test the real defaults (including the `~/Atlas` fallback)
without reading or writing the real home directory.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"

PROBE = (
    "import json, sys; sys.path.insert(0, %r); import config;"
    "print(json.dumps({'vault': str(config.VAULT), 'state': str(config.STATE),"
    " 'roster': str(config.ROSTER), 'worktrees': str(config.WORKTREES),"
    " 'ops': str(config.owner_pages_status() or ''), 'python': config.python()}))" % str(HOOKS)
)


def resolve(home: Path, **env) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    e["HOME"] = str(home)
    e.update(env)
    p = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, env=e, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def write_config(home: Path, obj: dict) -> Path:
    d = home / ".claude" / "gedaechtnis"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "config.json"
    f.write_text(json.dumps(obj), encoding="utf-8")
    return f


def test_defaults_when_nothing_is_configured(tmp_path):
    r = resolve(tmp_path)
    assert r["vault"] == str(tmp_path / "Gedaechtnis")
    assert r["state"] == str(tmp_path / ".claude" / "gedaechtnis")
    assert r["roster"] == str(tmp_path / "Gedaechtnis" / "Global" / "fleet-roster.md")
    assert r["worktrees"] == str(tmp_path / ".claude" / "worktrees")


def test_an_existing_atlas_vault_is_the_default(tmp_path):
    """The documented exception: a machine that already has an Atlas vault keeps using it rather
    than being handed a second, empty one."""
    (tmp_path / "Atlas").mkdir()
    r = resolve(tmp_path)
    assert r["vault"] == str(tmp_path / "Atlas")
    assert r["roster"] == str(tmp_path / "Atlas" / "Global" / "fleet-roster.md")


def test_config_file_beats_the_default(tmp_path):
    write_config(tmp_path, {"vault": str(tmp_path / "v"), "state_dir": str(tmp_path / "s"),
                            "worktrees_dir": str(tmp_path / "w")})
    (tmp_path / "Atlas").mkdir()                     # even against the Atlas fallback
    r = resolve(tmp_path)
    assert r["vault"] == str(tmp_path / "v") and r["state"] == str(tmp_path / "s")
    assert r["worktrees"] == str(tmp_path / "w")
    assert r["roster"] == str(tmp_path / "v" / "Global" / "fleet-roster.md")


def test_environment_beats_the_config_file(tmp_path):
    write_config(tmp_path, {"vault": str(tmp_path / "from-file")})
    r = resolve(tmp_path, GEDAECHTNIS_VAULT=str(tmp_path / "from-env"))
    assert r["vault"] == str(tmp_path / "from-env")


def test_config_location_is_itself_overridable(tmp_path):
    write_config(tmp_path, {"vault": str(tmp_path / "default-location")})
    other = tmp_path / "elsewhere.json"
    other.write_text(json.dumps({"vault": str(tmp_path / "elsewhere")}), encoding="utf-8")
    r = resolve(tmp_path, GEDAECHTNIS_CONFIG=str(other))
    assert r["vault"] == str(tmp_path / "elsewhere")


def test_a_broken_config_falls_through_instead_of_crashing(tmp_path):
    """A config bug must never take a session down — the hooks fall back to defaults."""
    write_config(tmp_path, {})
    (tmp_path / ".claude" / "gedaechtnis" / "config.json").write_text("{not json", encoding="utf-8")
    r = resolve(tmp_path)
    assert r["vault"] == str(tmp_path / "Gedaechtnis")


def test_tildes_in_the_config_are_expanded(tmp_path):
    write_config(tmp_path, {"vault": "~/tilde-vault"})
    assert resolve(tmp_path)["vault"] == str(tmp_path / "tilde-vault")


def test_owner_pages_status_is_absent_unless_configured_and_present(tmp_path):
    assert resolve(tmp_path)["ops"] == ""                       # nothing configured
    write_config(tmp_path, {"owner_pages_status": str(tmp_path / "gone.py")})
    assert resolve(tmp_path)["ops"] == ""                       # configured but not on disk
    real = tmp_path / "status.py"; real.write_text("print('{}')\n", encoding="utf-8")
    write_config(tmp_path, {"owner_pages_status": str(real)})
    assert resolve(tmp_path)["ops"] == str(real)


def test_python_defaults_to_the_running_interpreter_and_is_overridable(tmp_path):
    assert resolve(tmp_path)["python"] == sys.executable
    write_config(tmp_path, {"python": str(tmp_path / "nope")})
    assert resolve(tmp_path)["python"] == sys.executable        # a missing interpreter is ignored
    fake = tmp_path / "py"; fake.write_text("#!/bin/sh\n", encoding="utf-8")
    write_config(tmp_path, {"python": str(fake)})
    assert resolve(tmp_path)["python"] == str(fake)


def test_region_of_repo_accepts_a_flat_vault_region(tmp_path, monkeypatch):
    """A stranger's vault is flat: <vault>/<Region>/Position.md with no umbrella above it.
    An Atlas umbrella (children carrying Position.md) must still NOT read as a region."""
    import importlib, os, sys
    vault = tmp_path / "vault"; (vault / "Flat").mkdir(parents=True); (vault / "Flat" / "Position.md").write_text("x")
    (vault / "Umb" / "Child").mkdir(parents=True); (vault / "Umb" / "Position.md").write_text("x"); (vault / "Umb" / "Child" / "Position.md").write_text("x")
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(vault)); monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "none.json"))
    saved = {m: sys.modules.pop(m, None) for m in ("config", "common")}   # module-level VAULT is read at import
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
    try:
        common = importlib.import_module("common")
        repo = tmp_path / "repo"; repo.mkdir()
        (repo / "CLAUDE.md").write_text(f"@{vault}/Flat/Position.md\n")
        assert common.region_of_repo(repo) == "Flat"
        (repo / "CLAUDE.md").write_text(f"@{vault}/Umb/Position.md\n")
        assert common.region_of_repo(repo) is None
        (repo / "CLAUDE.md").write_text(f"@{vault}/Umb/Child/Position.md\n")
        assert common.region_of_repo(repo) == "Umb/Child"
    finally:   # never leave a tmp-vault config cached for the next test (found the hard way)
        for m in ("config", "common"):
            sys.modules.pop(m, None)
            if saved[m] is not None:
                sys.modules[m] = saved[m]
