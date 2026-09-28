#!/usr/bin/env python3
"""mergewindow.py — one session moves `main` at a time.

When two sessions merge into one repository's `main` in the same hour, the second one's full-suite
run was taken on a tree that is no longer the tree it ships: main moved under it, and it must
restack and run the suite again (2026-09-23: main moved under one builder twice, and each move cost
a fresh ~35-minute suite). The merge window is a claim on the right to move `main`:

    python3 mergewindow.py grant <session-id> [<repo>]    # the window, for that session
    python3 mergewindow.py release <session-id> [<repo>]  # give it back (exit 5: held by another)
    python3 mergewindow.py status [<repo>]

A session may grant the window to itself when it is free; a managing session may grant it to the
session it wants to merge next. The session id is on the session's own start facts line, and the
door's refusal prints it.

THE DOOR (`refusal`, called by gate.py's Bash door): in a repository whose root carries the file
`.merge-window`, a `git merge`, `git rebase` or `git pull` run while `main` is the checked-out
branch is refused unless this session holds a live window. `--abort`, `--continue`, `--quit` and
`--skip` are never refused: they finish or undo an operation already started.

THE RELEASE: after `mergesha.py verify-merge <sha>` prints "<sha> — on main", the PostToolUse Bash
chore releases the window this session holds in that repository. The command is read with the
doors' own `shellread.segments`, so `verify-merge <sha>; echo rc=$?` names the sha and the repo it
names (MERGEWINDOW-2: a regex read `37cce85a;` as the sha and `echo` as the repository, and the
window stayed held until it expired). A window whose holder never verified
expires after `TTL_MIN` minutes, so a session that died holding it cannot block the others.

The commands that move `main` WITHOUT checking it out are under the same door, whatever is checked
out (MERGEWINDOW-2): `git push . x:main`, `git fetch . x:main`, `git branch -f main x` (and `-M`/`-C`
onto `main`), `git update-ref refs/heads/main …`, `git checkout -B main x` / `git switch -C main x`.
Still NOT seen, named rather than guessed at: `git reset --hard|--soft <x>` while `main` is checked
out moves it too; `reset` is mostly an index or worktree operation here, so gating it would refuse
far more than it protects.

The window lives in `<git common dir>/gedaechtnis/merge-window.json`, so every worktree of one
repository reads the same one and nothing appears in anybody's working tree.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path

MARKER = ".merge-window"
TTL_MIN = 90                          # a full suite here is ~35 minutes; two of them, and some slack
BRANCH = "main"
_FINISH = {"--abort", "--continue", "--quit", "--skip"}
_VERIFIED = re.compile(r"\b([0-9a-fA-F]{7,40}) — on main\b")


def _git(repo: Path | str, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def lock_path(repo: Path | str) -> Path | None:
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) / "gedaechtnis" / "merge-window.json" if common else None


def read(repo: Path | str, now: float | None = None) -> dict | None:
    """The live window, or None when there is none or it has expired."""
    p = lock_path(repo)
    if not p or not p.is_file():
        return None
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if (now or time.time()) >= float(d.get("expires", 0)):
        return None
    return d


def grant(sid: str, repo: Path | str, now: float | None = None) -> tuple[int, str]:
    now = now or time.time()
    p = lock_path(repo)
    if not p:
        return 2, f"not a git repository: {repo}"
    cur = read(repo, now)
    if cur and cur.get("holder") != sid:
        left = int((float(cur["expires"]) - now) // 60)
        return 3, f"the merge window is held by session {cur['holder']} (expires in {left} min)"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"holder": sid, "granted": now, "expires": now + TTL_MIN * 60}))
    os.replace(tmp, p)
    return 0, f"merge window granted to session {sid} for {TTL_MIN} min"


def release(sid: str, repo: Path | str) -> tuple[int, str]:
    p = lock_path(repo)
    cur = read(repo)
    if not p or not cur:
        return 0, "no merge window is held"
    if cur.get("holder") != sid:
        return 5, f"the merge window is held by session {cur['holder']}, not {sid}; not released"
    p.unlink()                          # the window is this module's own state file, never user data
    return 0, f"merge window released by session {sid}"


def _sub(seg: str) -> tuple[str | None, list[str]]:
    toks = seg.split()[1:]
    i = 0
    while i < len(toks):
        if toks[i] in ("-C", "-c"):
            i += 2; continue
        if toks[i].startswith("-"):
            i += 1; continue
        return toks[i], toks[i + 1:]
    return None, []


_MAIN_REFS = {BRANCH, f"refs/heads/{BRANCH}"}


def moves_main_directly(sub: str | None, rest: list[str]) -> bool:
    """True for the commands that move `main` without it being checked out (MERGEWINDOW-2)."""
    pos = [x.strip("'\"") for x in rest if not x.startswith("-")]     # `'main'` is main (reviewer)
    if sub in ("push", "fetch"):
        return bool(pos) and pos[0] == "." and any(
            x.rsplit(":", 1)[-1] in _MAIN_REFS for x in pos[1:] if ":" in x)
    if sub == "branch":
        # the branch SET is the first name for `-f <name> <start>`, the second for `-M/-C <old> <new>`
        # (the only one when `-M <new>` renames the current branch) — never the start point
        if any(re.fullmatch(r"-[a-zA-Z]*[MC][a-zA-Z]*", x) for x in rest):
            return bool(pos) and pos[min(1, len(pos) - 1)] == BRANCH
        forced = any(x == "--force" or re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", x) for x in rest)
        return forced and bool(pos) and pos[0] == BRANCH
    if sub in ("checkout", "switch"):
        # `checkout -B main x` / `switch -C main x` reset main to x (reviewer, MERGEWINDOW-2)
        return any(re.fullmatch(r"-[a-zA-Z]*[BC][a-zA-Z]*|--force-create", x) for x in rest) \
            and bool(pos) and pos[0] == BRANCH
    if sub == "update-ref":
        return bool(pos) and pos[0] == f"refs/heads/{BRANCH}"
    return False


def refusal(cmd: str, cwd: str | None, sid: str, git_segments) -> str | None:
    """The Bash door's entry point. `git_segments` is the gate's own walker (prefixes stripped,
    `cd` and `-C` resolved), passed in so this module reads commands exactly as the other doors do."""
    for seg, repo in git_segments(cmd, cwd):
        sub, rest = _sub(seg)
        direct = moves_main_directly(sub, rest)
        if not direct and (sub not in ("merge", "rebase", "pull") or _FINISH & set(rest)):
            continue
        top = _git(repo, "rev-parse", "--show-toplevel")
        if not top or not (Path(top) / MARKER).is_file():
            continue
        if not direct and _git(repo, "symbolic-ref", "--short", "-q", "HEAD") != BRANCH:
            continue
        cur = read(repo)
        if cur and cur.get("holder") == sid:
            continue
        here = Path(__file__).resolve()
        claim = f"python3 {here} grant {sid} {top}"
        if cur:
            return (f"Only one session moves `{BRANCH}` at a time, and the merge window is held by session "
                    f"{cur['holder']}. Wait for its `mergesha.py verify-merge`, which releases it, then "
                    f"`{claim}`; after the wait, read `git log -1 --format=%h {BRANCH}` again and restack if it moved.")
        return (f"Only one session moves `{BRANCH}` at a time. Take the merge window first: `{claim}` — "
                f"then read `git log -1 --format=%h {BRANCH}` in the same command as the merge. "
                f"`mergesha.py verify-merge <sha>` releases it.")
    return None


_REDIRECT = re.compile(r"^\d*(?:>>?|<|>&|&>)(.*)$")


def verify_calls(cmd: str, cwd: str | None) -> list[tuple[str, Path]]:
    """(sha, repo) for every `verify-merge <sha> [<repo>]` the command runs. Read with the doors'
    own `shellread.segments` and `shlex`, `cd` followed across segments, redirections skipped — so
    `verify-merge 37cce85a; echo rc=$?` and `… 2>&1 | tail -5` name the sha and the cwd's repo."""
    import shlex                                             # noqa: PLC0415
    import shellread                                         # noqa: PLC0415
    cur = Path(cwd or ".")
    out = []
    for seg in shellread.segments(cmd):
        try:
            words = shlex.split(shellread.strip_prefix(seg))
        except ValueError:
            continue
        if not words:
            continue
        if words[0] == "cd" and len(words) > 1:
            cur = cur / os.path.expanduser(words[1])
            continue
        if "verify-merge" not in words:
            continue
        i = words.index("verify-merge")
        if i + 1 >= len(words):
            continue
        args, j = [], i + 2
        while j < len(words):
            m = _REDIRECT.match(words[j])
            if m:
                j += 1 if m.group(1) else 2                  # `>f` carries its target; `> f` does not
                continue
            args.append(words[j]); j += 1
        out.append((words[i + 1], cur / os.path.expanduser(args[0]) if args else cur))
    return out


def release_after_verify(sid: str, cmd: str, out: str, cwd: str | None) -> str | None:
    """PostToolUse: `verify-merge <sha>` printed "<sha> — on main" → release this session's window
    in that repo. A window another session holds is never touched and draws no notice (W7)."""
    if "verify-merge" not in cmd:
        return None
    on_main = {m.group(1).lower() for m in _VERIFIED.finditer(out or "")}
    if not on_main:
        return None
    notes = []
    for sha, repo in verify_calls(cmd, cwd):
        if sha.lower() not in on_main:
            continue
        cur = read(repo)
        if not cur or cur.get("holder") != sid:
            continue
        notes.append(release(sid, repo)[1])
    return "; ".join(notes) or None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("grant", "release", "status") or (argv[0] != "status" and len(argv) < 2):
        print("usage: mergewindow.py grant|release <session-id> [<repo>] · mergewindow.py status [<repo>]",
              file=sys.stderr)
        return 2
    if argv[0] == "status":
        repo = argv[1] if len(argv) > 1 else "."
        cur = read(repo)
        print(f"held by session {cur['holder']} until {time.strftime('%H:%M', time.localtime(cur['expires']))}"
              if cur else "free")
        return 0
    repo = argv[2] if len(argv) > 2 else "."
    rc, msg = (grant if argv[0] == "grant" else release)(argv[1], repo)
    print(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
