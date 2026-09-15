#!/usr/bin/env python3
"""worktree.py — copy-on-write clones for isolated builds (APFS clonefile via `cp -c`).

    python3 worktree.py create   # stdin: {name, cwd}; prints the clone path (last line)
    python3 worktree.py remove   # stdin: {worktree_path, cwd}

Why a clone and not `git worktree`: measured 2026-09-08 on a 15 GB working repo (.git 1.1 GB):
`cp -c -R` 14 s at ZERO extra disk, and the clone carries the 371 UNTRACKED arc directories and the
working state a build needs; `git worktree add` 4.5 s but tracked files only. Remove: every commit
the clone made is fetched back into the source repo as `refs/gedaechtnis/<name>` FIRST; then, if the
clone holds nothing unique (clean status, all commits fetched), it is deleted; if it holds
uncommitted work it is moved to the Trash by Finder, never rm'd. Defaults never delete.
"""
from __future__ import annotations
import hashlib, json, os, shutil, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import rootguard

# ★ Resolved PER CALL, never at import. The constants these replace were evaluated once when
# this module was first imported, and on 2026-09-15 that freeze rewrote 263 files of a real
# memory vault: a test set GEDAECHTNIS_VAULT after the module was already in `sys.modules`, so
# the override was read by nobody. PEP 562 `__getattr__` below keeps the old spelling working
# while making every read a fresh resolution — but `from <this module> import VAULT` binds ONCE
# and brings the bug straight back, which is why the importers use attribute access and
# `tests/test_percall_resolution.py` fails if a module-level binding returns.

_ACCESSORS = {"HOME": lambda: config.home(), "ROOT": lambda: config.worktrees()}


def __getattr__(name: str):
    fn = _ACCESSORS.get(name)
    if fn is not None:
        return fn()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_ACCESSORS))


def sh(args, cwd=None, timeout=600):
    return subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=cwd)


def repo_root(cwd: str) -> Path | None:
    p = sh(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
    return Path(p.stdout.strip()) if p.returncode == 0 else None


def dirt_manifest(repo: Path) -> dict:
    """sha256 of every dirty or untracked file (-uall) at this moment — the clone's own baseline."""
    out = {}
    st = sh(["git", "-C", str(repo), "status", "--porcelain", "-uall"])
    for l in st.stdout.splitlines():
        if len(l) <= 3:
            continue
        rel = l[3:].strip().strip('"')
        if rel.startswith(".gedaechtnis-clone-"):
            continue
        p = repo / rel
        try:
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "gone"
        except OSError:
            out[rel] = "unreadable"
    return out


def create(inp: dict) -> int:
    cwd = inp.get("cwd") or os.getcwd()
    name = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (inp.get("name") or "wt")) or "wt"
    src = repo_root(cwd) or Path(cwd)
    config.worktrees().mkdir(parents=True, exist_ok=True)
    dst = config.worktrees() / f"{src.name}--{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    # `name` comes off the hook payload and is already reduced to [A-Za-z0-9-_] above, and
    # `src.name` is a directory basename — but the permit is what SAYS so, and it is the thing that
    # keeps saying so if either sanitiser is ever loosened. This is the directory the whole clone
    # lands in, and `remove()` later deletes it recursively.
    rootguard.permit(dst, "worktree clone root", scratch=config.worktrees())
    p = sh(["cp", "-c", "-R", str(src), str(dst)])            # APFS clonefile
    if p.returncode != 0:
        p = sh(["cp", "-R", "--reflink=auto", str(src), str(dst)])   # Linux reflink (Btrfs/XFS) or plain copy
    if p.returncode != 0:
        print(f"copy-on-write clone unavailable ({p.stderr.strip()[:120]}); falling back to git worktree", file=sys.stderr)
        p = sh(["git", "-C", str(src), "worktree", "add", "--detach", str(dst), "HEAD"])
        if p.returncode != 0:
            print(p.stderr, file=sys.stderr); return 1
        (dst / ".gedaechtnis-clone-of").write_text(str(src) + "\nkind: git-worktree\n", encoding="utf-8")
        print(str(dst)); return 0
    (dst / ".gedaechtnis-clone-of").write_text(str(src) + "\n", encoding="utf-8")
    (dst / ".gedaechtnis-clone-manifest.json").write_text(json.dumps(dirt_manifest(dst)), encoding="utf-8")
    print(f"clonefile copy of {src} → {dst}", file=sys.stderr)
    print(str(dst))
    return 0


def remove(inp: dict) -> int:
    wt = Path(inp.get("worktree_path") or "")
    # ★ FIRST LINE, BEFORE EVERY OTHER CHECK. `worktree_path` is a field of the hook payload, and the
    # function ends in `shutil.rmtree(wt, ignore_errors=True)` — the only recursive delete in this
    # package, and one that reports nothing when it is wrong. The marker-file test below is evidence
    # about what the directory CONTAINS; it is not evidence about WHERE it is, and a directory
    # carrying a stale marker outside the clones root would have been deleted on that evidence.
    # Bounded to the worktrees root, which is where this hook's own `create()` puts every clone.
    # (BLASTRADIUS-1 security pass, 2026-09-15 — defence in depth: no live path to it was proven.)
    try:
        rootguard.permit(wt, "worktree remove", scratch=config.worktrees())
    except rootguard.OutsideRoot as e:
        print(f"refusing to remove a path outside the clones root:\n{e}", file=sys.stderr)
        return 0
    if not wt.is_dir():
        return 0
    marker = wt / ".gedaechtnis-clone-of"
    if not marker.is_file():
        print(f"{wt} is not a gedaechtnis clone; leaving it alone", file=sys.stderr); return 0
    mtxt = marker.read_text(encoding="utf-8")
    src = Path(mtxt.splitlines()[0].strip())
    if "kind: git-worktree" in mtxt:
        sh(["git", "-C", str(src), "worktree", "remove", "--force", str(wt)]); return 0
    name = wt.name.split("--", 1)[-1]
    # ★ THE DEFAULT IS "KEEP IT", NOT "DELETE IT". `unique` used to start False, and the entire
    # assessment that can set it — the fetch-back, the dirt manifest, the stash check — sits inside
    # the `.git` branch below. A clone of a directory that is not a git repo (`create()` falls back
    # to `Path(cwd)` when there is no repo root) therefore skipped every check and fell straight to
    # `shutil.rmtree`, destroying whatever the build wrote, with no Trash round trip — against this
    # module's own promise that defaults never delete. Found by the BLASTRADIUS-1 code review,
    # 2026-09-15. Now the assessment has to RUN and come back negative before anything is removed;
    # "I could not tell" keeps the directory.
    unique = True
    if (wt / ".git").exists() and (src / ".git").exists():
        unique = False
        # every branch the clone made, plus HEAD, comes back — a side branch is not lost because only HEAD was fetched
        f = sh(["git", "-C", str(src), "fetch", "--quiet", str(wt), f"+HEAD:refs/gedaechtnis/{name}/HEAD",
                f"+refs/heads/*:refs/gedaechtnis/{name}/branches/*"])
        if f.returncode != 0:
            print(f"fetch back failed: {f.stderr.strip()}", file=sys.stderr); unique = True
        # unique = a dirty/untracked file whose content differs from what the clone was BORN with — never from the
        # source as it is now (the source's own logs move on; that is not the clone's work)
        try:
            base = json.loads((wt / ".gedaechtnis-clone-manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            base = None
        now = dirt_manifest(wt)
        if base is None:
            unique = unique or bool(now)
        else:
            new_dirt = [r for r, h in now.items() if base.get(r) != h]
            if new_dirt:
                unique = True
                print(f"{len(new_dirt)} uncommitted path(s) unique to the clone — moving it to the Trash, not deleting", file=sys.stderr)
        if sh(["git", "-C", str(wt), "stash", "list"]).stdout.strip():
            unique = True; print("the clone has stashes — keeping it", file=sys.stderr)
    if unique:
        if config.no_trash():
            print(f"left in place (GEDAECHTNIS_NO_TRASH): {wt}", file=sys.stderr); return 0
        sh(["osascript", "-e", f'tell application "Finder" to delete POSIX file "{wt}"'], timeout=300)
        return 0
    shutil.rmtree(wt, ignore_errors=True)
    print(f"removed clone {wt} (commits fetched to refs/gedaechtnis/{name}/…; nothing unique)", file=sys.stderr)
    return 0


def main() -> int:
    try:
        inp = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        inp = {}
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    return create(inp) if which == "create" else remove(inp) if which == "remove" else 0


if __name__ == "__main__":
    sys.exit(main())
