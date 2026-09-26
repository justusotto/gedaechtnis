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


# ---------------------------------------------------------------- the attribution's own controls
# `attribute()` is the only LOOSENING in this guard: it lets some changes through. A loosening needs
# the control that proves it cannot swallow the case the guard exists for — otherwise the honest
# thing would be to not have it.

def _vault(tmp_path):
    import subprocess as sp
    v = tmp_path / "vault"; (v / "Region").mkdir(parents=True)
    (v / "Region" / "Position.md").write_text("# Status\n\noriginal\n", encoding="utf-8")
    sp.run(["git", "-C", str(v), "init", "-q"], check=True, stdin=sp.DEVNULL)
    sp.run(["git", "-C", str(v), "add", "-A"], check=True, stdin=sp.DEVNULL)
    sp.run(["git", "-C", str(v), "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "-m", "root"], check=True, stdin=sp.DEVNULL)
    return v


def test_a_concurrent_COMMITTED_change_is_attributed_away(tmp_path):
    """A background session-end commit: the file moved AND was committed, so the vault's HEAD moved
    and the path is clean. That is somebody else's work, not the suite's."""
    import subprocess as sp
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    v = _vault(tmp_path)
    roots = {"trees": [v], "files": []}
    head_before = vs.head_of(v)
    f = v / "Region" / "Position.md"
    before = vs.stat_fingerprint(roots)
    f.write_text("# Status\n\nthe other writer's entry\n", encoding="utf-8")
    sp.run(["git", "-C", str(v), "add", "-A"], check=True, stdin=sp.DEVNULL)
    sp.run(["git", "-C", str(v), "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "-m", "session-end auto-commit"], check=True, stdin=sp.DEVNULL)
    moved = vs.changed(before, vs.stat_fingerprint(roots))
    assert moved, "the fixture did not actually change anything"
    ours, theirs = vs.attribute(moved, roots, head_before)
    assert [Path(t).name for t in theirs] == ["Position.md"], (ours, theirs)
    assert ours == [], f"a committed, clean change was still blamed on the suite: {ours}"


def test_a_DIRTY_change_is_still_the_suites_even_when_HEAD_moved(tmp_path):
    """★ The control that carries the weight. A concurrent committer is active — HEAD moves — and a
    stray test writes at the same time. The stray write must still fail: it is dirty, because no
    test is going to commit the user's vault. Without this, the attribution would be a hole that
    opens exactly when the vault is busy, which is when the incident happened."""
    import subprocess as sp
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    v = _vault(tmp_path)
    roots = {"trees": [v], "files": []}
    head_before = vs.head_of(v)
    before = vs.stat_fingerprint(roots)

    # the other writer commits something of its own
    (v / "Region" / "Canon.md").write_text("# Decisions\n\ntheirs\n", encoding="utf-8")
    sp.run(["git", "-C", str(v), "add", "-A"], check=True, stdin=sp.DEVNULL)
    sp.run(["git", "-C", str(v), "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "-m", "theirs"], check=True, stdin=sp.DEVNULL)
    # and the suite scribbles on the user's memory, leaving it dirty
    (v / "Region" / "Position.md").write_text("compacted under a test bound\n", encoding="utf-8")

    moved = vs.changed(before, vs.stat_fingerprint(roots))
    ours, theirs = vs.attribute(moved, roots, head_before)
    assert [Path(o).name for o in ours] == ["Position.md"], (ours, theirs)
    assert [Path(t).name for t in theirs] == ["Canon.md"], (ours, theirs)


def test_when_nothing_committed_every_change_is_the_suites(tmp_path):
    """HEAD did not move, so there is no other writer to blame. Unattributable must resolve
    AGAINST the suite, never in its favour."""
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    v = _vault(tmp_path)
    roots = {"trees": [v], "files": []}
    head_before = vs.head_of(v)
    before = vs.stat_fingerprint(roots)
    (v / "Region" / "Position.md").write_text("scribble\n", encoding="utf-8")
    moved = vs.changed(before, vs.stat_fingerprint(roots))
    ours, theirs = vs.attribute(moved, roots, head_before)
    assert [Path(o).name for o in ours] == ["Position.md"]
    assert theirs == []


def test_a_vault_with_no_git_attributes_nothing_away(tmp_path):
    """A stranger's vault need not be a git repository. With no history there is no evidence of
    another writer, so everything stays the suite's."""
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    v = tmp_path / "plain"; (v / "Region").mkdir(parents=True)
    (v / "Region" / "Position.md").write_text("x\n", encoding="utf-8")
    roots = {"trees": [v], "files": []}
    before = vs.stat_fingerprint(roots)
    (v / "Region" / "Position.md").write_text("y\n", encoding="utf-8")
    moved = vs.changed(before, vs.stat_fingerprint(roots))
    ours, theirs = vs.attribute(moved, roots, vs.head_of(v))
    assert ours == moved and theirs == []


def test_the_lock_exclusion_is_narrow(tmp_path):
    """The exclusion list is the one place this guard deliberately looks away, so it gets its own
    pair: the two named lock paths are skipped, and everything else that merely LOOKS temporary —
    a half-written role file, a `.tmp` beside one — is still watched."""
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    v = tmp_path / "vault"
    (v / ".atlas-locks" / "Global.lock.d").mkdir(parents=True)
    (v / "Region").mkdir(parents=True)
    (v / ".atlas-locks" / "Global.lock.d" / "owner").write_text("pid\n", encoding="utf-8")
    (v / ".atlas-writer.lock").write_text("x\n", encoding="utf-8")
    (v / "Region" / "Position.md").write_text("a\n", encoding="utf-8")
    (v / "Region" / "Position.md.tmp-x").write_text("half\n", encoding="utf-8")
    seen = {Path(k).name for k in vs.stat_fingerprint({"trees": [v], "files": []})}
    assert ".atlas-writer.lock" not in seen and "owner" not in seen, f"a lock is being watched: {seen}"
    assert {"Position.md", "Position.md.tmp-x"} <= seen, (
        f"a half-written MEMORY file must still be watched — it is content, not coordination: {seen}")


# ------------------------------------------------------- measured attribution (audit hook)
# The attribution rule above is an INFERENCE. The audit hook is a MEASUREMENT, and the pair below
# is what separates them: it must say CERTAIN when this process did the write, and must not claim
# certainty when it did not.

def test_a_write_by_this_process_is_recorded_as_certain(tmp_path):
    """The half that turns a guess into a fact."""
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    vs.install_audit()
    victim = tmp_path / "written-here.md"
    victim.write_text("this process did it\n", encoding="utf-8")
    assert vs.written_by_us([str(victim)]) == [str(victim)]


def test_a_path_this_process_never_touched_is_NOT_claimed(tmp_path):
    """★ The half that matters after the CR6-FIX false positive. A file another process modified
    must not be reported as ours — the whole failure was a rule asserting authorship it could not
    have known."""
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    vs.install_audit()
    other = tmp_path / "written-by-someone-else.md"
    subprocess.run([sys.executable, "-c",
                    f"open({str(other)!r}, 'w').write('not us')"], check=True, stdin=subprocess.DEVNULL)
    assert other.is_file(), "the fixture did not actually write the file"
    assert vs.written_by_us([str(other)]) == [], \
        "a write made by ANOTHER process was claimed as this one's"


def test_a_read_is_not_recorded_as_a_write(tmp_path):
    """Negative control on the hook itself: it fires on every `open`, so a read must not land in
    the written set or every file the suite looks at becomes evidence against it."""
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    vs.install_audit()
    only_read = tmp_path / "only-read.md"
    only_read.write_bytes(b"x")
    vs._WRITTEN.discard(os.path.realpath(str(only_read)))
    only_read.read_text(encoding="utf-8")
    assert vs.written_by_us([str(only_read)]) == [], "a READ was recorded as a write"


def test_the_message_does_not_claim_authorship_it_cannot_prove(tmp_path):
    """The report's wording is the part a person acts on. When nothing was measured, it must say
    the check cannot tell — not 'So this is the suite', which is what sent a real concurrent
    writer's hook-edit ritual to a reader as a stray test."""
    sys.path.insert(0, str(HERE))
    import vault_sentinel as vs
    roots = {"trees": [tmp_path], "files": []}
    unmeasured = vs.report(["/somewhere/x.md"], "TEST t", roots, theirs=[], certain=[])
    assert "DID NOT WRITE THESE" in unmeasured and "cannot tell" in unmeasured
    assert "So this is the suite" not in unmeasured
    measured = vs.report(["/somewhere/x.md"], "TEST t", roots, theirs=[], certain=["/somewhere/x.md"])
    assert "THIS PROCESS WROTE" in measured and "fact, not an inference" in measured
