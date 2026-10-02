#!/usr/bin/env python3
"""wtsweep.py — GIT worktrees: removed when they are finished, reported when they are not.

★ THIS IS `git worktree`, NOT the APFS clone mechanism in `worktree.py`. The two are different
things that share an English word, and conflating them would point a DELETE at the wrong tree.
`worktree.py` makes copy-on-write clones under `config.worktrees()` (`~/.claude/worktrees`) for
isolated builds; this module sweeps real git worktrees under `<repo>/.claude/worktrees/`, which is
where the fleet's placement rule requires them.

CLONESWEEP-1 (2026-10-01): the clones are swept here too, by their OWN rule (`classify_clone`) and
their own action. `worktree.py remove` is the only other thing that takes a clone away, and it runs
only when the WorktreeRemove event fires; for ten clones (44 GB) it never did. A finished clone is
MOVED to `~/Downloads/To delete YYYY-MM-DD/` with a ledger line — never deleted, never the Trash:
the person empties that folder by hand. The git-worktree loop below never looks in the clones
folder, and the clone loop never runs `git worktree remove`.

Why it exists: COMPLETE-1 left 45 worktree folders scattered beside the repo in
the projects directory, beside the repo, because removing one was a thing every builder had to REMEMBER after verifying
its merge. A rule nobody can forget is a rule a script decides.

    removable  =  HEAD is an ancestor of main  AND  status is clean  AND  no live session's cwd
                  AND  no live process's cwd  (WTPROC-1 — see `process_cwds`)
    everything else is KEPT and REPORTED, with the reason it was kept

The three conditions are an AND on purpose, and each one exists because the other two cannot see
what it sees:

  * **ancestor of main** — the work is on main, so deleting the checkout loses nothing.
  * **clean status** — uncommitted work is not on main by definition, and is the only thing here
    that cannot be recovered from git.
  * **★ no live session's cwd** — and this is the condition the row's first draft did not have.
    A worktree an agent is ACTIVELY WORKING IN looks exactly like a finished one: a disciplined
    reviewer restores every mutation it makes, so its `status --porcelain` is empty, and if its
    branch already merged its HEAD is an ancestor of main. On 2026-09-22 a reviewer's checkout was
    in precisely that state while the reviewer was still running in it. Cleanliness measures the
    FILES; it says nothing about whether anyone is standing there.

★ AND ONE CASE THAT IS NEVER REMOVED AND MUST NOT BE REPORTED AS "UNMERGED": a detached worktree
whose HEAD was orphaned by a rebase. Its commits were rebased onto main under new shas, so the old
sha is an ancestor of nothing and `merge-base --is-ancestor` will refuse it forever. Reporting it
alongside genuinely unmerged work would put a permanent line in every SessionStart — and a report
that always says the same thing is a report people stop reading. It gets its own reason. It is NOT
removed on patch-id equivalence: "these commits look like those commits" is too clever a basis for
deleting a directory, and the cost of being wrong is somebody's only copy.

Nothing here is destructive on its own: `git worktree remove` refuses a dirty tree itself, and this
module never calls `rm`, never uses `--force`, and never removes the main worktree.
"""
from __future__ import annotations
import fnmatch, hashlib, json, os, re, shlex, subprocess, time
from pathlib import Path

import common
import config
import limits
import procs
import destructive
import rootguard

# Reasons a worktree was kept. The strings are the report's wording, so they live in one place and
# the tests assert against these names rather than against prose typed twice.
KEPT_DIRTY = "uncommitted work"
KEPT_LIVE = "a live session is working in it"
KEPT_UNMERGED = "commits not on main"
KEPT_ORPHANED = "detached at a sha no longer reachable — most likely rebase leftovers"
KEPT_LOCKED = "locked"
KEPT_UNKNOWN_SESSION = "a session record names it but has no readable pid"
KEPT_OUTSIDE = "outside .claude/worktrees/, the only place this sweep removes from"
KEPT_BOUND = "this pass already removed max_files_per_pass worktrees; left for the next pass"
KEPT_CHANGED = "its HEAD moved after it was checked"
KEPT_PROCESS = "a live process has its working directory in it"
KEPT_PROCS_UNREAD = "the machine's process working directories could not be read"
KEPT_YOUNG = "a clone younger than worktree_clone_min_age_seconds"
KEPT_CLONE_GIT = "a clone whose git state could not be read"
KEPT_CLONE_STASH = "a clone with a stash made after it was cloned"
KEPT_CLONE_LINKED = "a clone in which a git worktree was added after it was cloned"
MAX_CLONE_TIPS = 50                      # more tips than this to judge one by one: kept, not judged
GENERATED = ["*.pyc", "__pycache__/*", "*/__pycache__/*", ".DS_Store", "*/.DS_Store", "node_modules",
             "*/node_modules", "*.log", "*-log.tsv", ".claude/scheduled_tasks.lock"]


def _git(args: list[str], cwd: Path | None = None, alt: Path | None = None,
         timeout: int = 30) -> subprocess.CompletedProcess:
    """`alt` lets this one command also read another repository's objects, without fetching."""
    env = {**os.environ, "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(alt)} if alt else None
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout, env=env,
                          cwd=str(cwd) if cwd else None)


def list_worktrees(repo: Path) -> list[dict]:
    """Every worktree of `repo`, MAIN FIRST (git's own order), parsed from `--porcelain`.

    The porcelain format is a blank-line-separated record per worktree with `worktree <path>`,
    then `HEAD <sha>`, then either `branch <ref>` or `detached`, plus optional `locked` and
    `prunable`. Parsed rather than pattern-matched so a new key added by a future git does not
    silently shift a field."""
    p = _git(["worktree", "list", "--porcelain"], cwd=repo)
    if p.returncode != 0:
        return []
    out, cur = [], {}
    for line in (p.stdout or "").splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": val, "head": None, "branch": None, "detached": False, "locked": False}
        elif key == "HEAD":
            cur["head"] = val
        elif key == "branch":
            cur["branch"] = val
        elif key == "detached":
            cur["detached"] = True
        elif key == "locked":
            cur["locked"] = True
    if cur:
        out.append(cur)
    return out


def session_cwds() -> list[tuple[str, int | None, bool]]:
    """Every directory a session is or was working in, with that session's pid.

    Delegates to `common.session_locations`, which yields BOTH the record's `cwd` (where the
    session started) and its `cwd_last` (where its last Bash door saw it). The second is the one
    that matters here: every builder and reviewer in this fleet starts in the repo root and then
    works inside a worktree, so a check that knew only the starting directory would find no session
    in any worktree and happily delete the one somebody is standing in.

    CLONESWEEP-1: a record with NO pid field stops counting once its file is older than
    `worktree_session_stale_seconds`. Such a record can never be shown dead, so it protected a
    worktree for ever (6 of 17 on 2026-10-01). The file is rewritten each time its session moves
    directory and touched hourly while it keeps running Bash commands in one (`common.record_cwd`,
    called by the Bash door only), so its age is time since that session's last Bash call there,
    to the hour. `process_cwds` is a second
    guard only while one of the session's processes has its working directory in the worktree."""
    try:
        stale = float(limits.get("worktree_session_stale_seconds", 86400))
    except (TypeError, ValueError):
        stale = 86400.0
    return common.session_locations(stale_no_pid=stale)


def process_cwds() -> list[tuple[str, int]] | None:
    """(working directory, pid) for every process on the machine, or None when it cannot be read.

    ★ WHY THIS EXISTS BESIDE THE SESSION RECORDS (WTPROC-1). On 2026-09-22 at 22:01:45 the sweep
    removed BASE-22 while a pytest run was still working in it. Session records know where a SESSION
    has been; they know nothing about the background process that session started — a suite, a
    server, a `sleep` — which keeps running in the tree after the session's own cwd has moved on.
    The kernel is the only party that knows where every process is standing, so it is asked.

    `lsof -d cwd -Fpn` prints `p<pid>` then `n<path>` per process. Its exit status is NOT read:
    lsof exits 1 for reasons unrelated to cwd (a process that vanished mid-scan). What is read is
    whether it named any process at all — it always names at least the one that ran it, so an empty
    answer means the scan failed, and None tells the caller to keep everything."""
    try:
        p = subprocess.run(["lsof", "-nP", "-d", "cwd", "-Fpn"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        return None
    out, pid = [], None
    for line in (p.stdout or "").splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith("n") and pid is not None:
            out.append((line[1:], pid))
    return out or None


def _process_in(wt: Path, cwds: list[tuple[str, int]]) -> bool:
    """Whether any live process's working directory is `wt` or below it (`Path.parents`, as below)."""
    for cwd, pid in cwds:
        try:
            c = Path(cwd).resolve()
        except OSError:
            continue
        if (c == wt or wt in c.parents) and procs.pid_alive(pid):
            return True
    return False


def _occupied_by(wt: Path, records: list[tuple[str, int | None, bool]]) -> str | None:
    """The reason `wt` is occupied, or None if no session record reaches into it.

    A session whose cwd is BELOW the worktree root counts: an agent working in
    `<wt>/gedaechtnis/tests` is as much in that worktree as one sitting at its root.

    Containment is `Path.parents`, never a string prefix — `/a/bc` must not occupy `/a/b`. The
    worktree path itself is not resolved here because `git worktree list --porcelain` already
    reports a canonical path; that is git's guarantee, not this module's, and is stated so a reader
    does not delete the `.resolve()` on the session side believing the two are symmetric."""
    unknown = False
    for cwd, pid, has_pid in records:
        try:
            c = Path(cwd).resolve()
        except OSError:
            continue
        if c != wt and wt not in c.parents:
            continue
        if not has_pid:
            unknown = True                       # keep looking: a live pid outranks a stale record
            continue
        if procs.pid_alive(pid):
            return KEPT_LIVE
    return KEPT_UNKNOWN_SESSION if unknown else None


def _is_ancestor(repo: Path, sha: str, ref: str = "main") -> bool:
    return _git(["merge-base", "--is-ancestor", sha, ref], cwd=repo).returncode == 0


def _sha_is_reachable(repo: Path, sha: str) -> bool:
    """Whether any ref in the repository still reaches `sha`.

    This is what separates "unmerged work somebody still owns" from "a rebase left this behind".
    `git branch/tag --contains` over all refs answers it; an orphaned sha is contained by nothing.
    A sha git cannot even parse is treated as unreachable rather than raising."""
    p = _git(["for-each-ref", "--contains", sha, "--format=%(refname)"], cwd=repo)
    if p.returncode != 0:
        return False
    return bool((p.stdout or "").strip())


def in_allowed_dir(repo: Path, path: Path) -> bool:
    try:
        base = (repo / ".claude" / "worktrees").resolve()
        return path.resolve().parent == base
    except OSError:
        return False


def applies() -> bool:
    """Does this vault let the sweep REMOVE, or only report what it would remove?"""
    return destructive.applies("worktree-sweep")


def classify(repo: Path, wt: dict, records: list[tuple[str, int | None, bool]],
             cwds: list[tuple[str, int]] | None = None) -> tuple[bool, str]:
    """(removable, reason). `reason` is empty when removable.

    `cwds` is `process_cwds()`'s answer. None means it could not be read, and then nothing is
    removable: occupancy is the one condition whose failure destroys work that is not in git.

    ORDER IS DELIBERATE. The occupancy check runs FIRST, before anything about git state, because
    it is the only condition whose failure mode is destroying work that is not in git at all. The
    cheap checks going first would be the usual advice; here the expensive-but-decisive one does."""
    path = Path(wt["path"])
    # ALLOW-LIST (destructive.py clause 1): only the worktrees this workflow creates, under the
    # repository's own `.claude/worktrees/`. A worktree anywhere else was made by someone for a
    # reason this sweep cannot see, however finished it looks.
    if not in_allowed_dir(repo, path):
        return False, KEPT_OUTSIDE
    if wt.get("locked"):
        return False, KEPT_LOCKED
    occupied = _occupied_by(path, records)
    if occupied:
        return False, occupied
    if cwds is None:
        return False, KEPT_PROCS_UNREAD
    if _process_in(path, cwds):
        return False, KEPT_PROCESS
    st = _git(["status", "--porcelain"], cwd=path)
    if st.returncode != 0 or (st.stdout or "").strip():
        return False, KEPT_DIRTY
    head = wt.get("head") or ""
    if not head:
        return False, KEPT_UNMERGED
    if _is_ancestor(repo, head):
        return True, ""
    if wt.get("detached") and not _sha_is_reachable(repo, head):
        return False, KEPT_ORPHANED
    return False, KEPT_UNMERGED


def sweep(repo: Path, apply: bool = True) -> dict:
    """Sweep `repo`'s git worktrees. Returns {"removed": [...], "kept": [(path, reason), ...]}.

    `apply=False` classifies and removes nothing — the shape every destructive routine in this
    package carries, so a caller can show what WOULD happen without a dry run being a different
    code path from the real one. What it would remove goes to `proposed`. The Stop hook passes
    `applies()`, which is False until the vault switches `worktree_sweep_apply` on."""
    result = {"removed": [], "kept": [], "proposed": [], "undo": []}
    n_max = destructive.max_files()
    wts = list_worktrees(repo)
    if not wts:
        return result
    records = session_cwds()
    cwds = process_cwds()
    for wt in wts[1:]:                           # [0] is the main worktree; never a candidate
        path = wt.get("path") or ""
        if not path or not Path(path).exists():
            continue
        removable, reason = classify(repo, wt, records, cwds)
        if not removable:
            result["kept"].append((path, reason))
            continue
        if not apply:
            result["proposed"].append(path)
            continue
        if len(result["removed"]) >= n_max:
            result["kept"].append((path, KEPT_BOUND))
            continue
        # HASH CHECK (clause 4): read HEAD again right before removing. A commit between
        # `classify` and here means the decision was about another state. Uncommitted edits are
        # NOT re-checked here on purpose: git's own refusal below covers them, and a check here
        # would hide whether that refusal still works (see the --force comment).
        head_now = _git(["rev-parse", "HEAD"], cwd=Path(path))
        if head_now.returncode != 0 or head_now.stdout.strip() != (wt.get("head") or ""):
            result["kept"].append((path, KEPT_CHANGED))
            continue
        # No --force, ever: git's own refusal on a dirty tree is a second opinion about the one
        # condition whose loss is unrecoverable, and overriding it would remove that opinion.
        # ★ NEVER --force. git refuses to remove a dirty or locked worktree, and that refusal is a
        # SECOND OPINION on the one condition whose loss is unrecoverable. `classify` above already
        # refuses both, so this line changes nothing on any input the tests produce — which is
        # exactly why a mutation to `--force` came back green until a control was written that
        # makes `classify` wrong on purpose. The two guards agree everywhere except where the first
        # one is broken, and that is the case worth paying for.
        #
        # Two sweeps racing (two sessions stopping at once) both report the same path as removed.
        # git decides that race and the directory goes exactly once; the inaccuracy is in the
        # REPORT's attribution, never in what happens on disk.
        p = _git(["worktree", "remove", path], cwd=repo)
        if p.returncode == 0:
            result["removed"].append(path)
            # REVERT LINE (clause 5): the one command that brings it back. Every commit is on main
            # (that is why it was removable), so re-adding the worktree at its sha loses nothing.
            result["undo"].append(f"git -C {repo} worktree add --detach {path} {wt.get('head')}")
        else:
            result["kept"].append((path, f"git refused to remove it: {(p.stderr or '').strip()[:120]}"))
    if result["removed"]:
        _git(["worktree", "prune"], cwd=repo)
    # CLONESWEEP-1: this repo's copy-on-write clones. Counted against the same `n_max`. Nothing
    # here may raise: the worktrees removed above are only recorded once this function returns.
    try:
        _sweep_clones(repo, apply, records, cwds, result, n_max)
    except Exception as e:                           # noqa: BLE001 — see the comment above
        result["kept"].append((str(config.worktrees()), f"the clone sweep stopped: {e!r}"[:160]))
    return result


def _sweep_clones(repo: Path, apply: bool, records, cwds, result: dict, n_max: int) -> None:
    for clone in clones_of(repo):
        ok, reason = classify_clone(repo, clone, records, cwds)
        if not ok:
            result["kept"].append((str(clone), reason))
        elif not apply:
            result["proposed"].append(str(clone))
        elif len(result["removed"]) >= n_max:
            result["kept"].append((str(clone), KEPT_BOUND))
        else:
            dest, words = move_clone(clone, CLONE_WHY)
            if dest is None:
                result["kept"].append((str(clone), words))
            else:
                result["removed"].append(str(clone))
                result["undo"].append(undo_mv(dest, clone))


# ---------------------------------------------------------------- the clones (CLONESWEEP-1) ----

def _generated(rel: str) -> bool:
    pats = limits.get("worktree_generated", GENERATED)
    return any(isinstance(p, str) and fnmatch.fnmatch(rel, p)
               for p in (pats if isinstance(pats, list) else GENERATED))


def clone_dirt(wt: Path, born: float) -> list[str] | None:
    """Uncommitted paths that are the clone's OWN work, or None when that cannot be told.

    A clone is born with the source's uncommitted files (`.gedaechtnis-clone-manifest.json` lists
    them, each with its sha256 at birth). Its own work is a dirty path the manifest does not list,
    a listed file whose content no longer has that sha256, a directory (a nested repository: its
    inside is not read), or a path missing now that was there at birth. A listed path with no hash
    (born "gone" or "unreadable") is judged by time: written or replaced after `born`, reading the
    modification time AND the inode change time, since a copy that keeps an old modification time
    (`cp -p`, `rsync -a`, `mv`) still moves the second.
    Generated files (`worktree_generated`) are never work. Files git ignores are not seen."""
    try:
        base = json.loads((wt / ".gedaechtnis-clone-manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    st = _git(["status", "--porcelain", "-z", "-uall"], cwd=wt, timeout=40)
    if st.returncode != 0 or not isinstance(base, dict):
        return None
    out = []
    for _xy, rel in common.porcelain_z(st.stdout):
        if rel.startswith(".gedaechtnis-clone-") or _generated(rel):
            continue
        want = base.get(rel)
        try:
            s = (wt / rel).lstat()
        except OSError:
            changed = want != "gone"                     # absent now: the clone's act unless born so
        else:
            if (wt / rel).is_dir():
                changed = True
            elif isinstance(want, str) and len(want) == 64:
                try:                                     # the content itself: no clock can hide it
                    changed = hashlib.sha256((wt / rel).read_bytes()).hexdigest() != want
                except OSError:
                    changed = True
            else:
                changed = max(s.st_mtime, s.st_ctime) > born
        if rel not in base or changed:
            out.append(rel)
    return out


def clone_worktree_added(wt: Path, born: float) -> bool:
    """Whether a git worktree was added IN the clone after its birth. A clone is born with a copy
    of the source's `.git/worktrees/` entries; those are the source's. An entry made or changed
    after `born` is the clone's own linked worktree, wherever its checkout lies: its uncommitted
    files are in no status this sweep reads, so the clone is kept. Unreadable = True (kept)."""
    try:
        return any(max(e.lstat().st_mtime, e.lstat().st_ctime) > born
                   for e in (wt / ".git" / "worktrees").iterdir())
    except FileNotFoundError:
        return False
    except OSError:
        return True


def clone_commits_kept_elsewhere(src: Path, wt: Path, born: float = 0.0) -> bool | None:
    """Whether every commit the clone holds is also in `src`. The clone's tips are EVERY ref it
    has (branches, tags, anything under `refs/`), HEAD, the HEAD of every linked worktree
    (`for-each-ref` does not list those), and every reflog entry written after `born` (a commit
    left behind by `reset --hard` or by leaving a detached HEAD is held by the reflog alone).
    More than `MAX_CLONE_TIPS` tips the source does not have: "not held", unjudged.
    A commit counts as held by `src` when
    one of its refs or reflogs reaches it, or when `git cherry` marks it patch-equivalent to a
    commit on main. A merge commit is never patch-equivalent: one only the clone has keeps it.
    The clone's objects are read in place (an alternate object directory); nothing is fetched.
    None when git cannot read either side; a failing `git cherry` is "not held", never "held"."""
    tips = _git(["for-each-ref", "--format=%(objectname)"], cwd=wt)
    head = _git(["rev-parse", "HEAD"], cwd=wt)
    have = _git(["for-each-ref", "--format=%(objectname)"], cwd=src)
    linked = _git(["worktree", "list", "--porcelain"], cwd=wt)
    logs = _git(["log", "-g", "--all", "--date=unix", "--format=%H %gd"], cwd=wt, timeout=40)
    if (tips.returncode != 0 or head.returncode != 0 or have.returncode != 0
            or linked.returncode != 0 or logs.returncode != 0):
        return None
    extra = {l[5:].strip() for l in linked.stdout.splitlines() if l.startswith("HEAD ")}
    for line in logs.stdout.splitlines():
        sha, _, sel = line.partition(" ")
        m = re.search(r"@\{(\d+)\}$", sel)
        # reflog times are whole seconds and `born` is not: an entry from the second the clone was
        # born in counts as after it. An unreadable time counts as after birth too.
        if m is None or float(m.group(1)) + 1 > born:
            extra.add(sha)
    alt = wt / ".git" / "objects"
    mine = sorted((set(tips.stdout.split()) | {head.stdout.strip()} | extra) - set(have.stdout.split()))
    if not mine:
        return True
    if len(mine) > MAX_CLONE_TIPS:
        return False
    only = _git(["rev-list", *mine, "--not", "--all", "--reflog"], cwd=src, alt=alt, timeout=40)
    if only.returncode != 0:
        return None
    unique, same = set(only.stdout.split()), set()
    for sha in mine if unique else []:
        c = _git(["cherry", "main", sha], cwd=src, alt=alt, timeout=40)
        if c.returncode != 0:
            return False
        same |= {l[2:].strip() for l in c.stdout.splitlines() if l.startswith("- ")}
    return unique <= same


def classify_clone(repo: Path, wt: Path, records, cwds, now: float | None = None) -> tuple[bool, str]:
    """(sweepable, reason) for one copy-on-write clone of `repo`. Same order as `classify`:
    age and occupancy first, git state last; "could not tell" keeps it."""
    try:
        born = (wt / ".gedaechtnis-clone-of").stat().st_mtime
        min_age = float(limits.get("worktree_clone_min_age_seconds", 86400))
    except (OSError, TypeError, ValueError):
        return False, KEPT_CLONE_GIT
    if (now if now is not None else time.time()) - born < min_age:
        return False, KEPT_YOUNG
    occupied = _occupied_by(wt, records)
    if occupied:
        return False, occupied
    if cwds is None:
        return False, KEPT_PROCS_UNREAD
    if _process_in(wt, cwds):
        return False, KEPT_PROCESS
    try:
        stash = _git(["stash", "list", "--format=%ct"], cwd=wt)
        if stash.returncode != 0:
            return False, KEPT_CLONE_GIT
        if any(float(t) + 1 > born for t in stash.stdout.split()):   # whole seconds, as the reflog
            return False, KEPT_CLONE_STASH
        if clone_worktree_added(wt, born):
            return False, KEPT_CLONE_LINKED
        kept = clone_commits_kept_elsewhere(repo, wt, born)
        if kept is None:
            return False, KEPT_CLONE_GIT
        if not kept:
            return False, KEPT_UNMERGED
        dirt = clone_dirt(wt, born)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return False, KEPT_CLONE_GIT
    if dirt is None:
        return False, KEPT_CLONE_GIT
    return (False, KEPT_DIRTY) if dirt else (True, "")


def to_delete_dir(now: float | None = None) -> Path:
    """`~/Downloads/To delete YYYY-MM-DD/` — where a cleanup puts what it takes away (owner ruling
    2026-10-01: never the Trash; the person moves that folder to the Trash by hand)."""
    return config.home() / "Downloads" / time.strftime("To delete %Y-%m-%d", time.localtime(now))


def clones_of(repo: Path) -> list[Path]:
    """The copy-on-write clones of `repo` in the clones folder: a real directory directly in it
    whose marker's first line names `repo`. A `kind: git-worktree` fallback is a git worktree, is
    listed by `list_worktrees`, and is not a clone."""
    out = []
    try:
        entries = sorted(config.worktrees().resolve().iterdir())
    except OSError:
        return out
    for d in entries:
        try:
            lines = (d / ".gedaechtnis-clone-of").read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):                    # ValueError: a marker that is not UTF-8
            continue
        if d.is_symlink() or not lines or not lines[0].strip() or "kind: git-worktree" in lines[1:]:
            continue
        try:
            if Path(lines[0].strip()).resolve() == repo.resolve():
                out.append(d)
        except (OSError, ValueError):                    # ValueError: a NUL in the marker's path
            continue
    return out


def move_clone(wt: Path, why: str) -> tuple[Path | None, str]:
    """Rename `wt` into today's To-delete folder and add a ledger line. (new path, words). A plain
    rename: same volume only, nothing copied, nothing removed; any failure leaves it in place."""
    folder = to_delete_dir()
    dest = folder / wt.name
    try:
        rootguard.permit(wt, "clone sweep: the clone")
        rootguard.permit(dest, "clone sweep: the To-delete folder", scratch=folder)
        folder.mkdir(parents=True, exist_ok=True)
        if dest.exists() or dest.is_symlink():
            return None, f"{dest} already exists"
        readme = folder / "README-what-went-where.html"
        if not readme.exists():
            readme.write_text(TO_DELETE_README, encoding="utf-8")
        os.rename(wt, dest)
        with open(folder / "ledger.tsv", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{wt}\t{dest}\t{why}\t{undo_mv(dest, wt)}\n")
    except (OSError, rootguard.OutsideRoot) as e:
        if dest.exists() and not wt.exists():
            return dest, f"moved; the ledger line was not written ({e})"
        return None, f"could not be moved to {folder}: {str(e)[:120]}"
    return dest, "moved"


TO_DELETE_README = """<!doctype html>
<meta charset="utf-8">
<title>To delete: what went where</title>
<h1>To delete: what went where</h1>
<p>The worktree sweep moved finished build copies here. Nothing was deleted. Every commit in each
copy is also in its source repository, and it held no uncommitted file of its own.</p>
<p><code>ledger.tsv</code> in this folder has one line per item: when, where it came from, where it
is now, why it was safe to move, and the <code>mv</code> command that puts it back.</p>
<p>When you no longer need them, move this folder to the Trash yourself.</p>
"""

def undo_mv(dest: Path, wt: Path) -> str:
    return f"mv {shlex.quote(str(dest))} {shlex.quote(str(wt))}"


CLONE_WHY = ("copy-on-write clone; every commit is in the source repository, no stash and no "
             "uncommitted file of its own (generated files aside)")


def state_file() -> Path:
    return Path(config.state()) / "worktree-sweep.json"


def record(repo: Path, result: dict) -> None:
    """Persist what was kept, for the NEXT session's facts line to report.

    Written even when nothing was kept: an empty list is the only thing that lets the reader
    distinguish "swept, nothing outstanding" from "never swept"."""
    try:
        state_file().write_text(json.dumps({
            "repo": str(repo), "removed": result["removed"],
            "proposed": result.get("proposed", []), "undo": result.get("undo", []),
            "kept": [{"path": p, "reason": r} for p, r in result["kept"]],
        }, indent=1), encoding="utf-8")
    except OSError:
        pass


def facts_line() -> str | None:
    """The SessionStart line, or None when there is nothing to say.

    Only KEPT worktrees are reported. A removal is not news — it is the sweep doing its job, and a
    line announcing it every session is how a report earns being skimmed."""
    try:
        d = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    kept = d.get("kept") or []
    proposed = [{"path": p} for p in (d.get("proposed") or [])]
    pline = destructive.proposal_line("worktree-sweep", proposed, "removed")
    if not kept:
        return pline
    parts = [f"{Path(k['path']).name} ({k['reason']})" for k in kept[:6]]
    more = f", and {len(kept) - 6} more" if len(kept) > 6 else ""
    line = ("- Worktrees still open in this repo: " + "; ".join(parts) + more
            + ". Clean, merged and unoccupied ones are removed automatically; these were not.")
    return line + ("\n" + pline if pline else "")


def enabled() -> bool:
    return bool(limits.get("worktree_sweep"))
