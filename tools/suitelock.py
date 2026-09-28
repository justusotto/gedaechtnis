#!/usr/bin/env python3
"""suitelock.py — a test suite says it is watching the vault; a vault writer waits for it to finish.

    python3 tools/suitelock.py status          # who holds it (stale entries are reaped first)

★ WHY. The suite's vault sentinel (`tests/vault_sentinel.py`) fails a run when the user's real
files move while it watches, and reports "THIS PROCESS DID NOT WRITE THESE" when another program
did it. So the programs that write the vault on their own — the row-done door, the queue appenders,
the usage fetcher — used to refuse whenever ANY pytest process ran on the machine. On a machine where
several suites overlap that count never rests: on 2026-09-26 every writer waited 2 h 20 min for a
zero that never came, and most of those pytest processes were not watching the vault at all.

★ WHAT. A suite whose sentinel is watching holds `<state dir>/suite.lock/<pid>` for exactly as long
as it watches (the sentinel's session fixture takes it before its baseline and drops it after its
last check). A writer calls `writer_gate()`: no live holder → write; a holder → poll every 0.5 s up
to a cap, then REFUSE in words naming the holders. Nothing else on the machine blocks a write.

  * An entry whose pid is dead is reaped (removed) on sight — a suite killed with -9 leaves one.
  * The writer's own process ancestry never counts as a holder: a writer a suite runs as a SUBPROCESS
    is that suite's own act, pointed at whatever sandbox the test set up.
  * FALLBACK ONLY: when the lock directory exists but cannot be read, the writer falls back to the
    old anchored `ps` count, because "cannot tell" must never read as "clear".

What it cannot see: a suite on a checkout older than this file takes no lock. Until every live
worktree is past it, such a suite is invisible here, exactly as it would be to any other writer.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path

LOCK_NAME = "suite.lock"
POLL_S = 0.5
DEFAULT_CAP_S = 20.0             # fits inside the 30-s PostToolUse hook budget; the Stop door retries
CAP_ENV = "GEDAECHTNIS_SUITE_WAIT_S"
# The fallback's anchored count: the interpreter's EXECUTABLE is the first word. `pgrep -x pytest`
# is 0 while `python -m pytest` runs, and an unanchored grep counts the grep itself.
PYTEST = re.compile(r"^[^ ]*[Pp]ython[^ ]* .*pytest")


def _hooks():
    h = str(Path(__file__).resolve().parent.parent / "hooks")
    if h not in sys.path:
        sys.path.append(h)                                  # appended: never shadows a caller's module


def lock_dir(state: Path | str | None = None) -> Path:
    """`<state dir>/suite.lock`. The state dir comes from the package's own resolver unless given."""
    if state is None:
        _hooks()
        import config                                       # noqa: PLC0415
        state = config.state()
    return Path(state) / LOCK_NAME


def _alive(pid: int) -> bool:
    _hooks()
    import procs                                            # noqa: PLC0415
    return procs.pid_alive(pid)


def acquire(d: Path, pid: int | None = None) -> Path:
    """Take this process's entry. Idempotent: a second call rewrites the same file."""
    pid = os.getpid() if pid is None else pid
    d.mkdir(parents=True, exist_ok=True)
    p = d / str(pid)
    p.write_text(json.dumps({"pid": pid, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                             "cwd": os.getcwd()}) + "\n", encoding="utf-8")
    return p


def release(d: Path, pid: int | None = None) -> None:
    """Drop this process's entry (the package's own bookkeeping). Absent = already released."""
    pid = os.getpid() if pid is None else pid
    try:
        (d / str(pid)).unlink()
    except FileNotFoundError:
        pass


def ancestry(pid: int | None = None, max_hops: int = 12) -> set[int]:
    """This process and its parents. A writer a suite spawned is part of that suite."""
    _hooks()
    import procs                                            # noqa: PLC0415
    cur = os.getpid() if pid is None else pid
    out = {cur}
    for _ in range(max_hops):
        info = procs.ps_info(cur)
        if not info or info[0] <= 1 or info[0] in out:
            break
        cur = info[0]
        out.add(cur)
    return out


def holders(d: Path, exclude: set[int] | None = None, alive=_alive) -> list[int]:
    """Live pids holding the lock, minus `exclude`. Dead entries are reaped. Raises OSError when
    the directory exists and cannot be listed — the caller's cue for the fallback."""
    if not d.exists():
        return []
    live = []
    for e in sorted(d.iterdir()):
        try:
            pid = int(e.name)
        except ValueError:
            continue                                        # not ours; never touched
        if not alive(pid):
            try:
                e.unlink()                                  # reap: a suite killed with -9
            except OSError:
                pass
            continue
        live.append(pid)
    if live and exclude is None:
        exclude = ancestry()
    return [p for p in live if p not in (exclude or set())]


def pytest_count(ps_lines: list[str] | None = None) -> int:
    """The FALLBACK: anchored count of pytest processes on this machine. Unreadable = 1."""
    if ps_lines is None:
        try:
            p = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=5)
            ps_lines = p.stdout.splitlines()
        except (subprocess.TimeoutExpired, OSError):
            return 1
    return sum(1 for l in ps_lines if PYTEST.match(l))


def cap_default() -> float:
    try:
        return float(os.environ.get(CAP_ENV, DEFAULT_CAP_S))
    except ValueError:
        return DEFAULT_CAP_S


def in_real_vault(target: Path | str) -> bool:
    """Whether `target` lies inside the vault this package resolves. Unresolvable = True (gate it)."""
    try:
        _hooks()
        import config                                       # noqa: PLC0415
        vault = Path(config.vault()).expanduser().resolve()
        t = Path(target).expanduser().resolve()
    except Exception:                                       # noqa: BLE001 — cannot tell = gate
        return True
    return t == vault or vault in t.parents


def writer_gate(d: Path | None = None, cap_s: float | None = None, poll_s: float = POLL_S,
                exclude: set[int] | None = None, ps_lines: list[str] | None = None,
                target: Path | str | None = None,
                alive=_alive, sleep=time.sleep, clock=time.monotonic) -> str | None:
    """None = clear to write the vault. Otherwise one sentence saying why not.

    Waits while a suite holds the lock, polling every `poll_s`, for at most `cap_s`. With a
    `target` outside the vault (a test's sandbox), there is nothing for a sentinel to see: clear."""
    if target is not None and not in_real_vault(target):
        return None
    d = lock_dir() if d is None else d
    cap_s = cap_default() if cap_s is None else cap_s
    start = clock()
    while True:
        try:
            h = holders(d, exclude, alive)
        except OSError as e:
            n = pytest_count(ps_lines)
            if n:
                return (f"the suite lock {d} cannot be read ({e.__class__.__name__}), and the fallback "
                        f"count sees {n} pytest process(es) running — not writing")
            return None
        if not h:
            return None
        if clock() - start >= cap_s:
            return (f"a test suite is watching the vault (suite lock held by pid "
                    f"{', '.join(map(str, h))}); waited {cap_s:g} s — not writing")
        sleep(poll_s)


def main(argv: list[str]) -> int:
    if argv[:1] != ["status"]:
        print(__doc__.strip())
        return 0 if argv[:1] in (["-h"], ["--help"]) else 2
    d = lock_dir()
    try:
        h = holders(d, exclude=set())
    except OSError as e:
        print(f"{d}: cannot be read ({e})")
        return 1
    print(f"{d}: " + (f"held by {', '.join(map(str, h))}" if h else "free"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
