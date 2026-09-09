#!/usr/bin/env python3
"""commit.py — the Stop-time auto-commit: this lane's declared paths, and nothing else.

A memory that has to be committed by hand is a memory that is half-written: the session that
took the trouble to record a decision is the same session that then has to remember the vault's
git law, and the note that never got committed is indistinguishable from the note that was never
made. So the commit is machinery. At Stop, this hook stages the files that changed under the
paths THIS repo's `.atlas-lane` marker declares, and commits exactly those.

    lane, paths  <- the marker found by walking up from the session's cwd (common.lane_for)
    stage        <- `git add -- <file> …`, one explicit file per path, never a directory sweep
    commit       <- `git commit -m "session-end auto-commit: [LANE] DATE" -- <staged files>`

**The fail-safe is the whole design, and it is deliberately narrow.** No marker, an unparsable
marker, a marker with no `lane:` or no `path:`, or a `path:` that is absolute or contains `..` —
any of these means the lane is UNKNOWN, and an unknown lane stages NOTHING, writes one line to
`<state>/commit.log`, and exits cleanly. There is no "sensible default" partition, because the
sensible default is the one that commits another lane's work under this lane's name. A marker is
rejected WHOLE: one bad `path:` line invalidates every other line in it, exactly as the shell
Stop hook this is ported from does it.

**Three things it never does**, each because the vault's git law says so and none of them can be
undone by the next session:

  - never `git add -A` / `-a` / `.` — a scoped `git add` is a request; the commit's pathspec is
    the guarantee, and both are here because a sweep in a repo with concurrent writers commits
    work its author had not finished;
  - never `--amend` — forward-fix only, so history is never rewritten under a reader;
  - never a bare `git commit` — the pathspec is what makes the commit carry only what this lane
    staged, whoever else has something in the shared index.

**What it commits is derived from the index, not from the prefix list.** After staging, the hook
asks git which paths under its prefixes are actually staged and commits those; a prefix that
matches nothing is then simply absent instead of making `git commit -- <prefix>` fail, and
another lane's staged files — which live outside these prefixes — are neither committed nor
unstaged. The one residual, named rather than hidden: inside a prefix BOTH lanes declare
(`Global/`, typically), a sibling's already-staged edit is indistinguishable from this lane's own
and will ride along. Separating those needs an authorship signal git does not keep.

Identity: `Gedächtnis <gedaechtnis@local>`, a fixed machine identity, so `git log` tells a hook
commit from a person's at a glance. `auto_commit: false` in the config file (or
GEDAECHTNIS_AUTO_COMMIT=0) turns the whole hook off; it then stages nothing and says so in the log.
"""
from __future__ import annotations
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import read_input, log, lane_for, path_in_partition, VAULT, guarded

GIT_NAME = "Gedächtnis"
GIT_EMAIL = "gedaechtnis@local"
CHUNK = 200                                   # keep one `git add` argv well under any limit


def git(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """One git call against the vault -> (rc, stdout, stderr).

    stdout is returned RAW: the two readers below parse NUL-separated records, and folding a
    warning from stderr into that stream would invent a path out of a diagnostic. Nothing is
    printed either way — a Stop hook that writes to stdout talks over the session's last
    message."""
    try:
        p = subprocess.run(["git", "-C", str(VAULT), *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "").strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 124, "", str(e)


def changed_paths(prefixes: list[str]) -> list[str]:
    """Vault-relative paths that are modified, deleted or untracked AND inside the partition.

    Read from `git status --porcelain -z`, which quotes nothing and separates records with NUL,
    so a path with a space, a quote or a newline in it survives. A rename record carries two
    paths (new, then old); both are considered, because committing only half of a rename that
    straddles the partition edge would leave the vault in a state nobody wrote."""
    rc, out, err = git(["status", "--porcelain", "-z"])
    if rc != 0:
        log("commit", f"status FAILED rc={rc} detail={err[:200]!r}")
        return []
    recs = out.split("\0") if out else []
    found: list[str] = []
    i = 0
    while i < len(recs):
        rec = recs[i]
        i += 1
        if len(rec) < 4:
            continue
        xy, rel = rec[:2], rec[3:]
        cands = [rel]
        if xy[0] in ("R", "C") and i < len(recs):     # the ORIGINAL path is its own record
            cands.append(recs[i])
            i += 1
        for c in cands:
            c = c.rstrip("/")                          # `?? dir/` — stage the directory itself
            if c and path_in_partition(c, prefixes) and c not in found:
                found.append(c)
    return found


def staged_under(prefixes: list[str]) -> list[str]:
    """What is actually staged under our prefixes — the commit's pathspec, read back from the
    index rather than assumed from the list we asked for."""
    rc, out, err = git(["diff", "--cached", "--name-only", "-z", "--", *prefixes])
    if rc != 0:
        log("commit", f"diff --cached FAILED rc={rc} detail={err[:200]!r}")
        return []
    return [p for p in out.split("\0") if p]


def main() -> None:
    inp = read_input()
    cwd = inp.get("cwd") or os.getcwd()
    sid = inp.get("session_id", "-")
    if not config.flag("auto_commit", True):
        log("commit", f"sid={sid} cwd={cwd} auto_commit=off action=staged-nothing exit=clean")
        return
    lane, prefixes, marker = lane_for(cwd)
    if not lane or not prefixes:
        # The fail-safe. One line, outside the vault, and no commit: an unknown lane has no
        # partition, and a hook that guesses one commits somebody else's work under this name.
        log("commit", f"sid={sid} lane=UNKNOWN cwd={cwd} marker={marker or 'none'} "
                      f"action=staged-nothing exit=clean")
        return
    if not (VAULT / ".git").exists():
        log("commit", f"sid={sid} lane={lane} vault={VAULT} not-a-git-repo action=staged-nothing")
        return
    to_stage = changed_paths(prefixes)
    if not to_stage:
        return                                        # an ordinary no-op session: say nothing
    for i in range(0, len(to_stage), CHUNK):
        rc, _out, err = git(["add", "--", *to_stage[i:i + CHUNK]])
        if rc != 0:
            log("commit", f"sid={sid} lane={lane} stage FAILED rc={rc} detail={err[:200]!r}")
    paths = staged_under(prefixes)
    if not paths:
        log("commit", f"sid={sid} lane={lane} commit SKIPPED (nothing staged under {prefixes}) "
                      f"left-uncommitted={' '.join(to_stage)}")
        return
    subject = f"session-end auto-commit: [{lane}] {time.strftime('%Y-%m-%d')}"
    rc, _out, err = git(["-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}",
                         "commit", "-q", "-m", subject, "--", *paths], timeout=60)
    if rc == 0:
        log("commit", f"sid={sid} lane={lane} committed={len(paths)} subject={subject!r}")
    else:
        # Honest about its own failure: the log is where a reader goes to find out what the hook
        # did, and a swallowed refusal there is worse than the failure it hides.
        log("commit", f"sid={sid} lane={lane} commit FAILED rc={rc} paths={len(paths)} "
                      f"detail={err[:200]!r}")
        log("commit", f"sid={sid} lane={lane} uncommitted-after-failure: {' '.join(paths)}")


if __name__ == "__main__":
    guarded(main)
