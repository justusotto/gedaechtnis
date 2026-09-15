"""The control pair for the sentinel: it BITES on a write to the user's files, and it STAYS QUIET
on a write to a test's own sandbox.

A gate proven only to fire is half-proven. The false positives of a guard that watches the user's
entire memory vault would be paid for by everyone who ever runs this suite, so the negative control
below is not a formality — it is the half that says the guard is usable.

Both controls run a REAL pytest in a subprocess whose HOME is a temporary directory holding a decoy
vault, so "the user's real files" for that run are files this test created. Nothing here can touch
the machine's actual vault, and the assertion is about the OUTER RUN'S EXIT CODE — the thing a
person would see — rather than about the fixture's internals.
"""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _decoy_home(tmp_path: Path) -> Path:
    """A temporary HOME whose `Gedaechtnis/` is what `config._default_vault()` will resolve to."""
    home = tmp_path / "home"
    vault = home / "Gedaechtnis" / "Region"
    vault.mkdir(parents=True)
    (vault / "Position.md").write_text("# Status\n\nthe user's own memory\n", encoding="utf-8")
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("user memory\n", encoding="utf-8")
    return home


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    """Run one throwaway test under the REAL sentinel fixtures, with a decoy HOME."""
    home = _decoy_home(tmp_path)
    suite = tmp_path / "suite"
    suite.mkdir()
    # The same two-line wiring the shipped conftest does — asserted identical by
    # `test_the_shipped_conftest_still_installs_all_three` below.
    (suite / "conftest.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "from vault_sentinel import (real_files_unchanged_session, real_files_unchanged_module,\n"
        "                            real_files_unchanged_function)\n",
        encoding="utf-8")
    (suite / "test_probe.py").write_text(body, encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(suite)],
                          capture_output=True, text=True, env=env, cwd=str(tmp_path),
                          stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------- positive control

def test_the_sentinel_fails_the_run_when_a_test_writes_the_users_vault(tmp_path):
    """This is the incident, in miniature: a test writes a file in the resolved vault. Before this
    guard, that run went green."""
    p = _run(tmp_path, (
        "import os\n"
        "from pathlib import Path\n"
        "def test_writes_the_real_vault():\n"
        "    v = Path(os.environ['HOME']) / 'Gedaechtnis' / 'Region' / 'Position.md'\n"
        "    v.write_text('compacted under a test bound\\n', encoding='utf-8')\n"
        "    assert v.is_file()\n"))
    out = p.stdout + p.stderr
    assert p.returncode != 0, f"THE GUARD DID NOT BITE — the run passed.\n{out}"
    assert "THE USER'S REAL FILES CHANGED" in out, out
    assert "Position.md" in out, out
    # It must name the TEST, not merely the suite: naming the suite is what made the real incident
    # unreadable for hours.
    assert "TEST " in out, f"the failure did not name the test that did it\n{out}"


def test_the_sentinel_sees_a_file_created_in_the_vault(tmp_path):
    """Creation, not only modification — an archive segment opening is a CREATE, and 1,749 of them
    is what the real incident actually produced."""
    p = _run(tmp_path, (
        "import os\n"
        "from pathlib import Path\n"
        "def test_creates_in_the_real_vault():\n"
        "    (Path(os.environ['HOME']) / 'Gedaechtnis' / 'Region' / 'Position-archive-2.md'"
        ").write_text('x\\n', encoding='utf-8')\n"))
    out = p.stdout + p.stderr
    assert p.returncode != 0, f"a CREATED file went unseen\n{out}"
    assert "Position-archive-2.md" in out, out


def test_the_sentinel_sees_the_config_file_being_rewritten(tmp_path):
    """The quieter target: rewriting the install's config redirects every later session on the
    machine, and it is one file rather than 263."""
    p = _run(tmp_path, (
        "import os, json\n"
        "from pathlib import Path\n"
        "def test_rewrites_user_memory():\n"
        "    (Path(os.environ['HOME']) / '.claude' / 'CLAUDE.md'"
        ").write_text('clobbered\\n', encoding='utf-8')\n"))
    out = p.stdout + p.stderr
    assert p.returncode != 0, f"a write to the user-level memory file went unseen\n{out}"
    assert "CLAUDE.md" in out, out


# ---------------------------------------------------------------- negative control

def test_the_sentinel_stays_quiet_when_a_test_uses_its_own_sandbox(tmp_path):
    """The half that makes the guard usable. A test that writes only its own tmp_path — which is
    what every well-behaved test in this suite does — must pass, and must pass while writing a
    LOT, so the control discriminates rather than merely running."""
    p = _run(tmp_path, (
        "from pathlib import Path\n"
        "def test_writes_only_its_own_sandbox(tmp_path):\n"
        "    for i in range(50):\n"
        "        (tmp_path / f'file{i}.md').write_text('## entry\\n' * 100, encoding='utf-8')\n"
        "    (tmp_path / 'sub').mkdir()\n"
        "    (tmp_path / 'sub' / 'Position.md').write_text('x', encoding='utf-8')\n"
        "    assert len(list(tmp_path.iterdir())) == 51\n"))
    out = p.stdout + p.stderr
    assert p.returncode == 0, f"THE GUARD FIRED ON A CLEAN TEST — it is unusable as written.\n{out}"


def test_the_sentinel_refuses_rather_than_watching_nothing(tmp_path):
    """The failure mode that reads as a pass. If the resolver cannot run, the suite is UNGUARDED —
    and an unguarded run is indistinguishable from a clean one. So the resolver raises instead of
    returning empty roots, and this drives the refusal branch directly by making the subprocess
    exit non-zero."""
    sys.path.insert(0, str(HERE))
    import pytest as _pytest
    import vault_sentinel

    saved = vault_sentinel._RESOLVER
    try:
        vault_sentinel._RESOLVER = "import sys; sys.path.insert(0, %r); sys.exit(3)"
        with _pytest.raises(RuntimeError, match="UNGUARDED"):
            vault_sentinel.resolved_roots()
        # And the other half of "could not look": a resolver that succeeds but returns the wrong
        # number of paths must not be silently accepted as a partial answer.
        vault_sentinel._RESOLVER = "import sys; sys.path.insert(0, %r); print('/tmp')"
        with _pytest.raises(RuntimeError, match="expected 4 resolved paths"):
            vault_sentinel.resolved_roots()
    finally:
        vault_sentinel._RESOLVER = saved


# ---------------------------------------------------------------- the wiring itself

def test_the_shipped_conftest_still_installs_all_three(tmp_path):
    """The controls above install the fixtures by hand. This is the line that says the SHIPPED
    conftest installs the same three — without it, someone could delete a fixture from conftest.py
    and every control above would still pass."""
    src = (HERE / "conftest.py").read_text(encoding="utf-8")
    for name in ("real_files_unchanged_session", "real_files_unchanged_module",
                 "real_files_unchanged_function"):
        assert name in src, f"conftest.py no longer installs {name}"
