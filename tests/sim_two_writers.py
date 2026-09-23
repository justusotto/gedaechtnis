#!/usr/bin/env python3
"""sim_two_writers.py — two concurrent writers on ONE memory file (DESIGN §5.4, first bullet).

    python3 sim_two_writers.py                 # doors ON, N=120 per worker
    python3 sim_two_writers.py --n 500         # the design's N
    python3 sim_two_writers.py --no-doors      # the discriminating control: entries MUST be lost

No model anywhere. Two worker PROCESSES each perform N Edit-shaped operations against the same
`Errata.md` in a throwaway vault, driving the REAL `gate.py` and the REAL `chore.py` as subprocesses
with the hook JSON Claude Code would send. The question it answers is the one §5 exists for: can
two sessions write one region without losing each other's work?

**The two arms differ ONLY in the doors.** Both read the file, both sleep the same jittered
intervals, both append one entry per operation. The doors arm additionally: asks `gate.py` for
permission (D2 hands it the per-file mutex, D1 would refuse the unanchored shape), re-reads under
the lock and compares the tail against the anchor it planned against (the compare-and-swap), and
calls `chore.py` afterwards (which drops the mutex and records the touch). The control arm skips
the gate and writes the whole file from its STALE read — the `Write` shape D1 exists to refuse.
Holding the timing identical is what makes the comparison a fact about the doors rather than about
how long each arm happened to take.

**The control is not optional and it is not decoration.** If the control ever finishes with nothing
lost, this harness is VACUOUS — it would mean the interleaving never actually overlapped, and every
green from the doors arm would be a fact about the scheduler rather than about D1/D2. The test
asserts `lost > 0` for exactly that reason.

Every path is under a caller-supplied temporary root. Nothing here reads or writes the real vault,
the real state directory or the real ~/.claude.
"""
from __future__ import annotations
import argparse, hashlib, json, os, random, subprocess, sys, time
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
REL = "Notes/Errata.md"
ENTRY = "- w{w} seq {s:04d} — an entry appended by worker {w}"


# ------------------------------------------------------------------ the world both arms share ----

def build_world(root: Path) -> dict:
    """A throwaway vault + a repo whose `.atlas-lane` marker declares the region."""
    vault = root / "Vault"
    (vault / "Notes").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "sim@local"], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "sim"], check=True)
    (vault / "Global").mkdir(exist_ok=True)
    (vault / "Global" / "fleet-roster.md").write_text("```fleet-roster\nlane: SIM\nrepo: repo\n```\n")
    target = vault / REL
    target.write_text("# Errata\n\n- seed entry\n")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-q", "-m", "seed"], check=True)
    repo = root / "repo"
    repo.mkdir(exist_ok=True)
    (repo / ".atlas-lane").write_text("lane: SIM\npath: Notes/\npath: Global/\n")
    state = root / "state"
    env = dict(os.environ,
               GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(root / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(root / "no-such-config.json"))
    return dict(root=root, vault=vault, repo=repo, state=state, env=env, target=target)


def _hook(script: str, which: str, payload: dict, env: dict) -> dict | None:
    p = subprocess.run([sys.executable, "-B", str(HOOKS / script), which], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f"{script} {which} exited {p.returncode}: {p.stderr[:400]}")
    out = p.stdout.strip()
    return json.loads(out) if out else None


def _denied(res) -> bool:
    return (res or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _reason(res) -> str:
    return (res or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def _tail(text: str) -> str:
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1] if lines else ""


def _lock_file(state: Path, path: Path) -> Path:
    return state / "filelocks" / hashlib.sha1(os.path.realpath(str(path)).encode("utf-8")).hexdigest()


# -------------------------------------------------------------------------------- the worker ----

def worker(root: Path, wid: int, n: int, doors: bool, seed: int) -> dict:
    """One writer. Returns its own counters as a dict (the parent reads them off stdout)."""
    rnd = random.Random(seed)
    w = build_world_readonly(root)
    target, env, sid = w["target"], w["env"], f"SIM-{wid}"
    stats = {"worker": wid, "written": 0, "gate_denied": 0, "cas_retry": 0, "d1_denied": 0}

    if doors:
        # One deliberate D1 probe per worker: the whole-file shape the control arm uses for every
        # operation must be REFUSED here, so the two arms are demonstrably running different rules
        # and not merely different code paths.
        # Retried past a D2 refusal: the mutex is checked BEFORE D1, so a probe that happens to
        # land while the sibling holds the lock is answered by D2 and says nothing about D1.
        for _ in range(20):
            res = _hook("gate.py", "write", {"cwd": str(w["repo"]), "tool_name": "Write", "session_id": sid,
                                             "tool_input": {"file_path": str(target),
                                                            "content": target.read_text() + "probe\n"}}, env)
            if _denied(res) and "D2" in _reason(res):
                time.sleep(rnd.uniform(0.005, 0.050))
                continue
            break
        if _denied(res) and "Whole-file Write" in _reason(res):
            stats["d1_denied"] += 1
            stats["d1_reason"] = _reason(res)

    for s in range(n):
        entry = ENTRY.format(w=wid, s=s)
        while True:
            cur = target.read_text()
            anchor = _tail(cur)
            time.sleep(rnd.uniform(0.002, 0.010))     # the read→write window, IDENTICAL in both arms

            if not doors:
                # The control: a whole-file overwrite composed from the STALE read. Everything the
                # sibling appended since that read is gone, silently — the lost update D1 refuses.
                target.write_text(cur.rstrip("\n") + "\n" + entry + "\n")
                stats["written"] += 1
                break

            res = _hook("gate.py", "write", {"cwd": str(w["repo"]), "tool_name": "Edit", "session_id": sid,
                                             "tool_input": {"file_path": str(target), "old_string": anchor,
                                                            "new_string": anchor + "\n" + entry}}, env)
            if _denied(res):
                stats["gate_denied"] += 1
                time.sleep(rnd.uniform(0.005, 0.050))
                continue                               # retry from the read, exactly as the model would

            # Inside the mutex: the compare-and-swap. Re-read, and only write if the file still
            # ends with the anchor this operation was planned against.
            now = target.read_text()
            if _tail(now) != anchor:
                stats["cas_retry"] += 1
                _hook("chore.py", "write", {"cwd": str(w["repo"]), "tool_name": "Edit", "session_id": sid,
                                            "tool_input": {"file_path": str(target)}}, env)
                time.sleep(rnd.uniform(0.005, 0.050))
                continue
            target.write_text(now.rstrip("\n") + "\n" + entry + "\n")
            stats["written"] += 1
            _hook("chore.py", "write", {"cwd": str(w["repo"]), "tool_name": "Edit", "session_id": sid,
                                        "tool_input": {"file_path": str(target),
                                                       "old_string": anchor, "new_string": anchor + "\n" + entry}}, env)
            break
        time.sleep(rnd.uniform(0.0, 0.020))
    return stats


def build_world_readonly(root: Path) -> dict:
    """The same handles as build_world, without creating anything — for a worker process that
    joins a world its parent already built."""
    vault = root / "Vault"
    repo = root / "repo"
    state = root / "state"
    env = dict(os.environ,
               GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(root / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(root / "no-such-config.json"))
    return dict(root=root, vault=vault, repo=repo, state=state, env=env, target=vault / REL)


# ---------------------------------------------------------------------------------- the run ----

def run_sim(root: Path, n: int = 120, doors: bool = True) -> dict:
    """Build the world, run two worker processes, and report what survived.

    Returns: expected · kept · lost · duplicated · out_of_order · per-worker stats · the commit
    result for each session id, when the doors were on."""
    root = Path(root)
    w = build_world(root)
    procs = []
    for wid in (0, 1):
        procs.append(subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", str(wid),
             "--root", str(root), "--n", str(n), "--seed", str(1000 + wid)]
            + ([] if doors else ["--no-doors"]),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    stats, errors = [], []
    for p in procs:
        out, err = p.communicate(timeout=900)
        if p.returncode != 0:
            errors.append(err[-800:])
            continue
        stats.append(json.loads(out.strip().splitlines()[-1]))

    text = w["target"].read_text()
    seen: dict[tuple[int, int], int] = {}
    order: dict[int, list[int]] = {0: [], 1: []}
    for line in text.splitlines():
        if not line.startswith("- w"):
            continue
        parts = line.split()
        if len(parts) < 4 or parts[2] != "seq":
            continue
        try:
            wid, seq = int(parts[1][1:]), int(parts[3])
        except ValueError:
            continue
        seen[(wid, seq)] = seen.get((wid, seq), 0) + 1
        order[wid].append(seq)

    expected = {(wid, s) for wid in (0, 1) for s in range(n)}
    kept = expected & set(seen)
    result = {
        "n_per_worker": n, "doors": doors, "expected": len(expected), "kept": len(kept),
        "lost": len(expected - set(seen)), "duplicated": sum(1 for k, c in seen.items() if c > 1),
        "unexpected": len(set(seen) - expected),
        # a valid interleaving: each worker's own entries appear in the order it wrote them
        "out_of_order": sum(1 for wid in (0, 1) if order[wid] != sorted(order[wid])),
        "workers": stats, "errors": errors, "text_lines": len(text.splitlines()),
    }

    if doors:
        # D3's half: each session's Stop commit, driven by the touched set the chores recorded.
        commits = {}
        for wid in (0, 1):
            sid = f"SIM-{wid}"
            touched = _touched(w["state"], sid)
            commits[sid] = {"touched": touched, "has_file": REL in touched}
            _hook_commit(w, sid)
        head = subprocess.run(["git", "-C", str(w["vault"]), "show", f"HEAD:{REL}"],
                              capture_output=True, text=True).stdout
        result["commits"] = commits
        result["committed_matches_disk"] = (head == text)
        result["committed_lines"] = len(head.splitlines())
    return result


def _touched(state: Path, sid: str) -> list[str]:
    try:
        doc = json.loads((state / f"session-start-{sid}.json").read_text())
    except (OSError, ValueError):
        return []
    t = doc.get("touched")
    return t if isinstance(t, list) else []


def _hook_commit(w: dict, sid: str) -> None:
    subprocess.run([sys.executable, "-B", str(HOOKS / "commit.py")],
                   input=json.dumps({"cwd": str(w["repo"]), "session_id": sid}),
                   capture_output=True, text=True, env=w["env"], timeout=120)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worker", type=int)
    ap.add_argument("--root")
    ap.add_argument("--n", type=int, default=int(os.environ.get("SIM_N", "120")))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-doors", action="store_true")
    a = ap.parse_args()
    if a.worker is not None:
        print(json.dumps(worker(Path(a.root), a.worker, a.n, not a.no_doors, a.seed)))
        return 0
    import tempfile
    root = Path(a.root) if a.root else Path(tempfile.mkdtemp(prefix="sim-two-writers-"))
    t0 = time.time()
    res = run_sim(root, n=a.n, doors=not a.no_doors)
    res["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
