"""destructive.py — the five rules every automatic chore that moves or removes a user's file follows.

A chore here is anything a hook runs without a person asking: it moves entries out of a file,
removes a directory, or rewrites a file. Each one:

1. ALLOW-LIST — names the classes of file it may touch, and touches nothing else, whatever
   else would have qualified. (Compaction: files named for a memory role. Boot-file roll: Boot
   files that declare a window. Worktree sweep: worktrees under the repository's own
   `.claude/worktrees/`. Cleanup: memory role files, never a sidecar or a file a session loads.)
2. BOUNDED LOSS — one pass never moves more than `max_move_share` of a file, and touches at most
   `max_files_per_pass` files. What it declines for either reason, it says so.
3. PROPOSE BY DEFAULT — a fresh install only proposes: it records what it would do and the next
   session is told. The chore acts only once `<chore>_apply` is set to true for that vault (the
   `limits` object in `config.json`), after the person has seen a proposal.
4. HASH CHECK — a file that changed between the pass reading it and the pass writing it is not
   written.
5. REVERT LINE — every automatic commit lists what it moved and ends with the one command that
   undoes it.

`REGISTRY` is the list of chores that follow these rules. `test_destructive_gate.py` checks every
call of a moving primitive in the hooks sits inside a registered chore, so a new chore that skips
the rules fails the suite rather than shipping.
"""
from __future__ import annotations
import json, time, uuid
from pathlib import Path

import config
import limits
import rootguard

# chore name -> (the `limits` key that switches it from proposing to acting, what it may touch)
REGISTRY = {
    "compaction": ("compaction_apply", "files named for a memory role"),
    "boot-roll": ("boot_roll_apply", "Boot files that declare a window"),
    "worktree-sweep": ("worktree_sweep_apply", "worktrees under the repository's .claude/worktrees/ "
                                               "(removed), and this repository's copy-on-write "
                                               "clones in worktrees_dir (moved to a To-delete folder)"),
    "cleanup": ("cleanup_apply", "memory role files: an entry repeated byte for byte in one file, "
                                 "and files over their line limit"),
}


def applies(chore: str) -> bool:
    """True when this vault has switched the chore from proposing to acting."""
    key = REGISTRY[chore][0]
    try:
        return limits.get(key) is True
    except Exception:
        return False


def max_share() -> float:
    try:
        v = float(limits.get("max_move_share"))
    except Exception:
        return 0.25
    return v if 0 < v <= 1 else 0.25


def max_files() -> int:
    try:
        v = int(limits.get("max_files_per_pass"))
    except Exception:
        return 10
    return v if v > 0 else 10


def pass_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]


def revert_body(pid: str, items: list[dict], repo: Path) -> str:
    """The commit body: what moved, then the one command that undoes the commit.

    The commit cannot name its own sha, so the command finds it by the pass id written on the line
    above it."""
    lines = []
    for it in items:
        heads = it.get("headings") or []
        shown = "; ".join(heads[:8]) + (f"; and {len(heads) - 8} more" if len(heads) > 8 else "")
        lines.append(f"- {it['path']}: {it.get('entries', len(heads))} entr(y/ies) moved to its "
                     f"archive" + (f" — {shown}" if shown else ""))
    lines.append("")
    lines.append(f"gedaechtnis-pass: {pid}")
    lines.append(f"Undo: git -C {repo} revert --no-edit "
                 f"$(git -C {repo} log -1 --format=%H --grep='^gedaechtnis-pass: {pid}$')")
    return "\n".join(lines)


def proposals_path() -> Path:
    return Path(config.state()) / "proposals.json"


def record_proposal(chore: str, items: list[dict]) -> None:
    """Keep what a proposing chore WOULD have done, for the person to read before switching it on."""
    try:
        doc = json.loads(proposals_path().read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            doc = {}
    except (OSError, ValueError):
        doc = {}
    doc[chore] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": items,
                  "switch": REGISTRY[chore][0]}
    try:
        target = rootguard.permit(proposals_path(), why="the proposals file in the state directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    except (OSError, rootguard.OutsideRoot):
        pass


def proposal_line(chore: str, items: list[dict], verb: str) -> str | None:
    """One SessionStart line for a proposing chore, or None when it proposed nothing."""
    if not items:
        return None
    names = ", ".join(i["path"] for i in items[:5])
    more = f", and {len(items) - 5} more" if len(items) > 5 else ""
    return (f"- {chore}: proposing only — {len(items)} item(s) would be {verb}: {names}{more}. "
            f"Details in {proposals_path()}. To let it act, set `{REGISTRY[chore][0]}: true` in "
            f"the `limits` object of config.json.")
