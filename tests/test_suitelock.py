"""PYTESTLOCK-1 — the suite lock: a suite whose vault sentinel is watching holds it, a vault writer
waits on it, and nothing else on the machine blocks a write.

The headline control runs a REAL throwaway pytest under the REAL sentinel fixtures with a decoy HOME
(the `test_vault_sentinel.py` pattern): while that suite runs, a writer is refused and the refusal
names the suite's pid; after it exits, the same writer passes.
"""
from __future__ import annotations
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
_spec = importlib.util.spec_from_file_location("suitelock_under_test", PLUGIN / "tools" / "suitelock.py")
suitelock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(suitelock)

sys.path.insert(0, str(PLUGIN / "hooks"))
import rowdone  # noqa: E402

FOREIGN_LIVE = 1            # launchd / init: alive, never our ancestor (the ancestry walk stops at ppid 1)


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
    p.wait()
    return p.pid


# ---------------------------------------------------------------- the lock itself

def test_acquire_and_release_are_the_pid_file_and_idempotent(tmp_path):
    d = tmp_path / "suite.lock"
    p = suitelock.acquire(d, 4242)
    suitelock.acquire(d, 4242)
    assert p == d / "4242" and sorted(x.name for x in d.iterdir()) == ["4242"]
    suitelock.release(d, 4242)
    suitelock.release(d, 4242)                      # absent = already released, no error
    assert list(d.iterdir()) == []


def test_a_dead_holder_is_reaped_and_does_not_block(tmp_path):
    d = tmp_path / "suite.lock"
    dead = _dead_pid()
    suitelock.acquire(d, dead)
    (d / "notes.txt").write_text("not a pid")       # a foreign file is never touched
    assert suitelock.holders(d, exclude=set()) == []
    assert not (d / str(dead)).exists()
    assert (d / "notes.txt").exists()
    assert suitelock.writer_gate(d, cap_s=0, exclude=set()) is None


def test_the_writers_own_ancestry_never_counts_as_a_holder(tmp_path):
    """A writer a suite runs as a subprocess is that suite's own act."""
    d = tmp_path / "suite.lock"
    suitelock.acquire(d, os.getppid())
    assert os.getppid() in suitelock.ancestry()
    assert suitelock.holders(d) == []
    assert suitelock.holders(d, exclude=set()) == [os.getppid()]


# ---------------------------------------------------------------- the writer gate

def test_no_lock_directory_at_all_is_clear(tmp_path):
    assert suitelock.writer_gate(tmp_path / "never-made", cap_s=0) is None


def test_a_live_holder_refuses_after_the_cap_naming_the_pid(tmp_path):
    d = tmp_path / "suite.lock"
    suitelock.acquire(d, FOREIGN_LIVE)
    why = suitelock.writer_gate(d, cap_s=0)
    assert why and "suite lock held by pid 1" in why and "not writing" in why


def test_the_writer_WAITS_polling_half_a_second_and_passes_when_the_suite_ends(tmp_path):
    d = tmp_path / "suite.lock"
    suitelock.acquire(d, FOREIGN_LIVE)
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s
        if len(slept) == 3:
            suitelock.release(d, FOREIGN_LIVE)      # the suite finishes during the wait

    assert suitelock.writer_gate(d, cap_s=20, sleep=sleep, clock=lambda: t[0]) is None
    assert slept == [0.5, 0.5, 0.5]


def test_the_wait_is_capped(tmp_path):
    d = tmp_path / "suite.lock"
    suitelock.acquire(d, FOREIGN_LIVE)
    t = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s

    why = suitelock.writer_gate(d, cap_s=2, sleep=sleep, clock=lambda: t[0])
    assert why and "waited 2 s" in why
    assert len(slept) == 4


def test_the_cap_reads_its_environment_override(monkeypatch):
    monkeypatch.setenv(suitelock.CAP_ENV, "3.5")
    assert suitelock.cap_default() == 3.5
    monkeypatch.setenv(suitelock.CAP_ENV, "nonsense")
    assert suitelock.cap_default() == suitelock.DEFAULT_CAP_S


def test_a_target_outside_the_vault_is_never_held(tmp_path, monkeypatch):
    """A test's sandbox write has nothing a sentinel could see."""
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "vault"))
    d = tmp_path / "suite.lock"
    suitelock.acquire(d, FOREIGN_LIVE)
    assert suitelock.writer_gate(d, cap_s=0, target=tmp_path / "elsewhere" / "q.md") is None
    assert suitelock.writer_gate(d, cap_s=0, target=tmp_path / "vault" / "q.md")


# ---------------------------------------------------------------- the fallback, and only the fallback

@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 000 directory")
def test_an_UNREADABLE_lock_falls_back_to_the_anchored_count(tmp_path):
    d = tmp_path / "suite.lock"
    d.mkdir()
    d.chmod(0)
    try:
        why = suitelock.writer_gate(d, cap_s=0, ps_lines=["/usr/bin/python3 -m pytest tests"])
        assert why and "cannot be read" in why and "1 pytest" in why
        assert suitelock.writer_gate(d, cap_s=0, ps_lines=["vim notes-about-pytest.md"]) is None
    finally:
        d.chmod(0o755)


def test_a_READABLE_lock_ignores_every_other_pytest_on_the_machine(tmp_path):
    """The whole point: a pytest that is not watching the vault no longer blocks a write."""
    d = tmp_path / "suite.lock"
    d.mkdir()
    busy = ["/usr/bin/python3 -m pytest tests"] * 3
    assert suitelock.writer_gate(d, cap_s=0, ps_lines=busy) is None


# ---------------------------------------------------------------- the real suite holds it

def test_THIS_suite_holds_the_lock_while_its_sentinel_watches():
    """Positive control on the live run: the sentinel's session fixture has taken this process's
    entry in the REAL state dir (resolved with the pristine environment)."""
    import vault_sentinel                                   # the conftest's own instance
    d = vault_sentinel._suite_lock_dir()
    assert (d / str(os.getpid())).is_file(), d


def _decoy_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "Gedaechtnis" / "Region").mkdir(parents=True)
    (home / "Gedaechtnis" / "Region" / "Position.md").write_text("# Status\n", encoding="utf-8")
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("user memory\n", encoding="utf-8")
    return home


def test_a_writer_DURING_a_suite_is_refused_and_one_BETWEEN_suites_passes(tmp_path):
    """The row's control. A real pytest under the real sentinel fixtures, decoy HOME; its one test
    signals it is running and waits for a go-file. The writer (the gate every vault writer calls,
    pointed at that HOME's state dir) is refused while it runs and passes after it exits."""
    home = _decoy_home(tmp_path)
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "from vault_sentinel import (real_files_unchanged_session, real_files_unchanged_module,\n"
        "                            real_files_unchanged_function)\n", encoding="utf-8")
    running, go = tmp_path / "running", tmp_path / "go"
    (suite / "test_probe.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "def test_waits():\n"
        f"    Path({str(running)!r}).write_text('x')\n"
        "    t = time.monotonic()\n"
        f"    while not Path({str(go)!r}).exists() and time.monotonic() - t < 60:\n"
        "        time.sleep(0.05)\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    child = subprocess.Popen([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(suite)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                             cwd=str(tmp_path), stdin=subprocess.DEVNULL)
    d = home / ".claude" / "gedaechtnis" / "suite.lock"
    try:
        t = time.monotonic()
        while not running.exists() and child.poll() is None and time.monotonic() - t < 60:
            time.sleep(0.05)
        assert running.exists(), "the throwaway suite never started its test"
        during = suitelock.writer_gate(d, cap_s=0, exclude=set())
        assert during and f"held by pid {child.pid}" in during, during
    finally:
        go.write_text("go")
        out, _ = child.communicate(timeout=120)
    assert child.returncode == 0, out
    assert suitelock.writer_gate(d, cap_s=0, exclude=set()) is None
    assert list(d.iterdir()) == []                  # released, not merely reaped


# ---------------------------------------------------------------- the writers use it

def test_rowdone_refuses_while_a_suite_holds_the_lock(tmp_path, monkeypatch):
    d = tmp_path / "state" / "suite.lock"
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv(suitelock.CAP_ENV, "0")
    suitelock.acquire(d, FOREIGN_LIVE)
    code, line = rowdone.flip("ME-1", "abcdef1", "b", tmp_path, "ME", [], [])
    assert code == rowdone.PYTEST and "suite lock held by pid 1" in line and "nothing written" in line


def test_rowdone_is_not_held_by_a_pytest_that_holds_no_lock(tmp_path, monkeypatch):
    """Negative control: a machine full of pytest processes, no lock → the write goes past the gate
    (to the next check, which refuses this fake sha for its own reason)."""
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    code, line = rowdone.flip("ME-1", "not-a-sha", "b", tmp_path, "ME", [], [],
                              ps_lines=["/usr/bin/python3 -m pytest tests"] * 3)
    assert code == rowdone.NOT_ON_MAIN, line


def test_the_writers_all_route_through_the_gate():
    """Each named writer calls `writer_gate` — a writer added later without it is caught here."""
    repo = PLUGIN.parent
    for rel in ("gedaechtnis/hooks/rowdone.py",):
        assert "writer_gate(" in (repo / rel).read_text(encoding="utf-8"), rel
    for rel in ("scripts/pharos_usage_fetch.py", "scripts/answer_router.py"):
        p = repo / rel
        if p.exists():                               # absent in the published plugin
            assert "writer_gate(" in p.read_text(encoding="utf-8"), rel


def test_a_multi_row_handoff_spends_ONE_wait_budget_not_one_per_row(tmp_path, monkeypatch):
    """Review should-fix: five rows under a held lock must not wait 5 × the cap inside a 30-s hook."""
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv(suitelock.CAP_ENV, "2")
    monkeypatch.setattr(rowdone, "_WAIT_DEADLINE", None)
    rowdone.suitelock.acquire(rowdone.suitelock.lock_dir(), FOREIGN_LIVE)
    t = [100.0]
    waited = []

    def gate(**kw):
        waited.append(kw["cap_s"])
        t[0] += kw["cap_s"]                          # the gate spends its whole cap, then refuses
        return "held"

    monkeypatch.setattr(rowdone.suitelock, "writer_gate", gate)
    for _ in range(5):
        assert rowdone.suite_busy(clock=lambda: t[0]) == "held"
    assert waited == [2.0, 0.0, 0.0, 0.0, 0.0]
