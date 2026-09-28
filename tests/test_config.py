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
    """POSITIVE control for the documented exception: a machine that already has an Atlas VAULT —
    proved by the roster file, not by the directory's name — keeps using it rather than being
    handed a second, empty one (council 3 K-1, 2026-09-10)."""
    (tmp_path / "Atlas" / "Global").mkdir(parents=True)
    (tmp_path / "Atlas" / "Global" / "fleet-roster.md").write_text("# roster\n", encoding="utf-8")
    r = resolve(tmp_path)
    assert r["vault"] == str(tmp_path / "Atlas")
    assert r["roster"] == str(tmp_path / "Atlas" / "Global" / "fleet-roster.md")


def test_a_vault_under_ANY_name_is_adopted_not_only_one_spelling(tmp_path):
    """The generalisation, and the half a single-candidate test cannot see: the rule is the ROSTER
    FILE, so a vault called anything is adopted. The previous spelling looked for one hard-coded
    directory name, which adopted exactly one machine's vault and no one else's."""
    (tmp_path / "Zettelkasten" / "Global").mkdir(parents=True)
    (tmp_path / "Zettelkasten" / "Global" / "fleet-roster.md").write_text("# roster\n", encoding="utf-8")
    assert resolve(tmp_path)["vault"] == str(tmp_path / "Zettelkasten")


def test_TWO_real_vaults_adopt_NEITHER(tmp_path):
    """The ambiguity arm. Guessing between two real vaults is the one answer that could write a
    session's memory into the wrong person's files, so it is not taken: the fallback is the name
    this package creates, and the user names one in the config file."""
    for name in ("Atlas", "Zettelkasten"):
        (tmp_path / name / "Global").mkdir(parents=True)
        (tmp_path / name / "Global" / "fleet-roster.md").write_text("# roster\n", encoding="utf-8")
    assert resolve(tmp_path)["vault"] == str(tmp_path / "Gedaechtnis")


def test_the_adoption_memo_is_keyed_on_HOME_and_does_not_leak_between_them(tmp_path):
    """★ The memo exists because the scan is a directory listing on a per-call accessor, and a
    process-wide cache would be the FROZEN-PATH defect this module was written to end, one layer
    down. So it is keyed on HOME — asserted here IN PROCESS, because a subprocess per case cannot
    see a cache at all and would pass whatever the keying was."""
    import sys
    sys.path.insert(0, str(HOOKS))
    saved = sys.modules.pop("config", None)
    try:
        import config
        config._ADOPTED.clear()
        a, b = tmp_path / "home-a", tmp_path / "home-b"
        for h, name in ((a, "Atlas"), (b, "Zettelkasten")):
            (h / name / "Global").mkdir(parents=True)
            (h / name / "Global" / "fleet-roster.md").write_text("# roster\n", encoding="utf-8")
        old_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(a)
            assert config.vault() == a / "Atlas"
            os.environ["HOME"] = str(b)
            assert config.vault() == b / "Zettelkasten", "the memo answered for the WRONG home"
            os.environ["HOME"] = str(a)
            assert config.vault() == a / "Atlas", "the memo lost its first home's answer"
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            config._ADOPTED.clear()
    finally:
        sys.modules.pop("config", None)
        if saved is not None:
            sys.modules["config"] = saved


# ---------------------------------------------------------------- the topology layer ----

def _topo(tmp_path, obj):
    """Resolve the topology accessors under a config file carrying `obj`, in a fresh process."""
    cfg = tmp_path / "topo.json"
    cfg.write_text(json.dumps({"topology": obj}), encoding="utf-8")
    code = (
        "import json,sys;sys.path.insert(0,%r);import config;"
        "print(json.dumps({'queues':config.queues_rel(),'idx':config.artifacts_index_rel(),"
        "'ledger':config.ledger_rel(),'channels':config.channels_rel(),"
        "'stem':config.stem_rule_dir(),'tops':sorted(config.non_region_tops()),"
        "'shared':list(config.shared_append_files()),'problems':config.topology_problems()}))"
        % str(HOOKS))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    env["GEDAECHTNIS_CONFIG"] = str(cfg)
    env["HOME"] = str(tmp_path)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         check=True).stdout
    return json.loads(out)


@pytest.mark.parametrize("bad", ["/etc", "../../etc", "a/../../etc", "/"])
def test_a_topology_path_that_ESCAPES_THE_VAULT_is_refused(tmp_path, bad):
    """★ These values are JOINED ONTO THE VAULT. A value that escapes it points every rule built on
    that key at somebody else's files — the queue walker reads them, the newline repair WRITES to
    them. The guard was in the code and in its own docstring with no test: a mutation that deleted
    it reddened nothing in the whole suite (reviewer, 2026-09-20)."""
    r = _topo(tmp_path, {"queues_dir": bad, "artifacts_index": bad + "/x.md", "stem_rule_dir": bad})
    assert r["queues"] is None and r["idx"] is None and r["stem"] is None, r
    # and it is not silent about it
    assert any("not vault-relative" in w for w in r["problems"]), r["problems"]


def test_a_GOOD_topology_is_applied_and_reports_no_problems(tmp_path):
    """The positive control the refusal above needs: the same accessors DO return the value when
    it is an ordinary vault-relative path, so the test above is about the guard and not about the
    accessors being broken."""
    r = _topo(tmp_path, {"queues_dir": "Queues/regions", "artifacts_index": "Queues/index.md",
                         "stem_rule_dir": "Council", "non_region_tops": ["Global", "Queues"],
                         "shared_append_files": ["Umbrella/Canon.md"]})
    assert r["queues"] == "Queues/regions" and r["idx"] == "Queues/index.md"
    assert r["stem"] == "Council" and r["tops"] == ["Global", "Queues"]
    assert r["shared"] == ["Umbrella/Canon.md"]
    assert r["problems"] == []


def test_an_unconfigured_topology_is_the_shipped_shape_and_is_not_a_problem(tmp_path):
    """Absent is the SHIPPED state, not a misconfiguration — and the two folders this package
    CREATES itself (`Global/` via init.py, `Channels/ledger/` via ledger.py) keep their defaults
    while everything else is absent and its rule OFF."""
    r = _topo(tmp_path, {})
    assert r["queues"] is None and r["idx"] is None and r["stem"] is None
    assert r["ledger"] == "Channels/ledger" and r["channels"] == "Channels"
    assert r["tops"] == ["Channels", "Global"] and r["shared"] == []
    assert r["problems"] == []


@pytest.mark.parametrize("obj,needle", [
    ({"queues_dirs": "Queues"}, "not a key this package reads"),
    ({"queues_dir": 7}, "is int, not str"),
    ({"non_region_tops": "Global"}, "is str, not list"),
    ({"shared_append_files": [1]}, "non-string entry"),
])
def test_an_unusable_topology_key_is_NAMED_never_silently_ignored(tmp_path, obj, needle):
    """★ The whole failure mode of an override layer is looking configured and doing nothing. A
    mutation emptying `topology_problems()` reddened nothing in the suite before this (reviewer,
    2026-09-20) — so the sentence that says a bad key is reported was a claim, not a behaviour."""
    r = _topo(tmp_path, obj)
    assert any(needle in w for w in r["problems"]), r["problems"]


def test_a_topology_that_is_not_an_object_is_named_too(tmp_path):
    cfg = tmp_path / "topo.json"
    cfg.write_text(json.dumps({"topology": ["Global"]}), encoding="utf-8")
    code = ("import json,sys;sys.path.insert(0,%r);import config;"
            "print(json.dumps(config.topology_problems()))" % str(HOOKS))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    env["GEDAECHTNIS_CONFIG"] = str(cfg); env["HOME"] = str(tmp_path)
    probs = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True,
                                      text=True, env=env, check=True).stdout)
    assert probs and "is list, not an object" in probs[0], probs


def test_a_directory_merely_named_atlas_is_not_adopted(tmp_path):
    """NEGATIVE control for the same rule: somebody's photo folder called Atlas carries no fleet
    roster, so it is not a vault and every hook must stay out of it."""
    (tmp_path / "Atlas").mkdir()
    (tmp_path / "Atlas" / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    r = resolve(tmp_path)
    assert r["vault"] == str(tmp_path / "Gedaechtnis")
    assert r["roster"] == str(tmp_path / "Gedaechtnis" / "Global" / "fleet-roster.md")


def test_the_vault_env_var_is_the_mechanism(tmp_path):
    """CLONEENV-1's positive control: GEDAECHTNIS_VAULT alone moves the vault, which is what
    `<clone>/.claude/settings.local.json` writes for a session started inside a vault clone."""
    clone = tmp_path / "clone-of-the-vault"
    clone.mkdir()
    r = resolve(tmp_path, GEDAECHTNIS_VAULT=str(clone))
    assert r["vault"] == str(clone)
    assert r["roster"] == str(clone / "Global" / "fleet-roster.md")


def test_config_file_beats_the_default(tmp_path):
    write_config(tmp_path, {"vault": str(tmp_path / "v"), "state_dir": str(tmp_path / "s"),
                            "worktrees_dir": str(tmp_path / "w")})
    (tmp_path / "Atlas" / "Global").mkdir(parents=True)          # even against a REAL Atlas vault
    (tmp_path / "Atlas" / "Global" / "fleet-roster.md").write_text("# roster\n", encoding="utf-8")
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
