#!/usr/bin/env python3
"""commit.py — the Stop-time auto-commit: this lane's declared paths, and nothing else.

A memory that has to be committed by hand is a memory that is half-written: the session that
took the trouble to record a decision is the same session that then has to remember the vault's
git law, and the note that never got committed is indistinguishable from the note that was never
made. So the commit is machinery. At Stop, this hook stages the vault files THIS SESSION wrote,
inside the paths THIS repo's `.atlas-lane` marker declares, and commits exactly those.

    lane, paths  <- the marker found by walking up from the session's cwd (common.lane_for)
    touched      <- the vault paths this session wrote, recorded per session id by chore.py on
                    every PostToolUse Edit/Write (`touched: [...]` in session-start-<sid>.json)
    stage        <- `git add -- <file> …`, one explicit file, never a directory sweep
    commit       <- `git commit -m "session-end auto-commit: [LANE] DATE" -- <staged files>`

**The unit is the TOUCHED SET, not "everything dirty in the partition", and the difference is the
whole point.** The only collision this vault has actually suffered is a session committing a
SIBLING session's half-written file: same lane, same declared prefix, two live sessions, and the
one that stopped first swept an edit its author had not finished — four instances in a single
night. A partition tells you what a lane MAY write; it cannot tell you which of two sessions in
that lane wrote a given line. The session's own record can, so that is what is committed.

Two consequences, stated rather than discovered: a file BOTH sessions edited is committed by
whichever stops first, carrying both edits (git has no way to split them, and leaving it
uncommitted would be worse); and the second session's Stop then finds nothing left for that file
and logs one line saying so. A session that wrote nothing to the vault commits nothing, silently.

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

**What it commits is read back from the index, and only for its own paths.** After staging, the
hook asks git which of ITS files are actually staged and commits those by name; a file that
turned out to have nothing to stage is simply absent instead of making `git commit -- <path>`
fail, and anything another session has staged — inside this partition or outside it — is neither
committed nor unstaged. Asking git about the PREFIX instead is precisely how a sibling's staged
file would ride along, which is why the pathspec here is the touched list.

**The residual, named rather than hidden.** A vault file written by something other than the Edit
or Write tool — a shell command, a script the session ran — is not in the touched set and is not
committed here. That is the deliberate trade: an unrecorded write stays in the working tree for
its author to commit, which is recoverable, while a sweep of a sibling's half-written file is not.

Identity: `Gedächtnis <gedaechtnis@local>`, a fixed machine identity, so `git log` tells a hook
commit from a person's at a glance. `auto_commit: false` in the config file (or
GEDAECHTNIS_AUTO_COMMIT=0) turns the whole hook off; it then stages nothing and says so in the log.
"""
from __future__ import annotations
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
from common import read_input, log, lane_for, path_in_partition, touched_paths, VAULT, guarded

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


def dirty_paths() -> set[str]:
    """Every vault-relative path git reports as changed: modified, deleted, staged or untracked.

    Read from `git status --porcelain -z`, which quotes nothing and separates records with NUL,
    so a path with a space, a quote or a newline in it survives. A rename record carries two
    paths (new, then old); both count as dirty. This set is an INTERSECTION filter, never a
    source of paths: what to commit comes from the session's touched set."""
    rc, out, err = git(["status", "--porcelain", "-z"])
    if rc != 0:
        log("commit", f"status FAILED rc={rc} detail={err[:200]!r}")
        return set()
    recs = out.split("\0") if out else []
    found: set[str] = set()
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
            c = c.rstrip("/")
            if c:
                found.add(c)
    return found


def is_dirty(rel: str, dirty: set[str]) -> bool:
    """Is this path one git would commit something for?

    Its own entry, or an ANCESTOR's: git reports a wholly untracked directory as one `?? dir/`
    record and never names the files inside it, so a session's first write into a brand-new
    region would otherwise look clean and be dropped."""
    if rel in dirty:
        return True
    parts = rel.split("/")
    return any("/".join(parts[:n]) in dirty for n in range(1, len(parts)))


def staged_of(paths: list[str]) -> list[str]:
    """Which of OUR paths are actually staged — the commit's pathspec, read back from the index
    rather than assumed from the list we asked for.

    Asked about our own paths, never about the prefixes: a sibling session's staged file inside
    the same declared prefix must be neither committed nor unstaged, and asking git about the
    prefix is exactly how it would end up in this commit."""
    if not paths:
        return []
    rc, out, err = git(["diff", "--cached", "--name-only", "-z", "--", *paths])
    if rc != 0:
        log("commit", f"diff --cached FAILED rc={rc} detail={err[:200]!r}")
        return []
    return [p for p in out.split("\0") if p]


def auto_commit(inp: dict, select, subject_for, tag: str = "") -> None:
    """The whole commit, parameterised by WHICH of the session's paths this invocation owns.

    `select(sid, prefixes)` returns the vault-relative candidate paths; everything after it —
    the off switch, the lane fail-safe, the dirty intersection, the explicit `git add --`, the
    read-back pathspec, the logging — is identical for every caller, and deliberately so. The
    Stop hook passes the session's touched set; `subagent_stop.py` passes one subagent's paths
    minus the parent's own. There is exactly one implementation of the vault's git law here, and
    a second entry point that copied it would be a second thing to keep correct.

    `tag` is an extra field for the log line (e.g. the agent id), so `commit.log` stays the one
    place a reader goes to find out what was committed and on whose behalf."""
    cwd = inp.get("cwd") or os.getcwd()
    sid = inp.get("session_id", "-")
    pre = f"sid={sid}" + (f" {tag}" if tag else "")
    if not config.flag("auto_commit", True):
        log("commit", f"{pre} cwd={cwd} auto_commit=off action=staged-nothing exit=clean")
        return
    lane, prefixes, marker = lane_for(cwd)
    if not lane or not prefixes:
        # The fail-safe. One line, outside the vault, and no commit: an unknown lane has no
        # partition, and a hook that guesses one commits somebody else's work under this name.
        log("commit", f"{pre} lane=UNKNOWN cwd={cwd} marker={marker or 'none'} "
                      f"action=staged-nothing exit=clean")
        return
    if not (VAULT / ".git").exists():
        log("commit", f"{pre} lane={lane} vault={VAULT} not-a-git-repo action=staged-nothing")
        return
    mine = [p for p in select(sid, prefixes) if path_in_partition(p, prefixes)]
    if not mine:
        return                                        # nothing of ours here: say nothing
    dirty = dirty_paths()
    to_stage = [p for p in mine if is_dirty(p, dirty)]
    if not to_stage:
        # Everything this session wrote is already in history — typically because a sibling
        # session that edited the same file stopped first and carried both sets of edits. That
        # is the designed outcome, not a failure, but it is logged so a reader can tell it from
        # a session whose work vanished.
        log("commit", f"{pre} lane={lane} nothing-left-to-commit "
                      f"(already committed elsewhere): {' '.join(mine)}")
        return
    for i in range(0, len(to_stage), CHUNK):
        rc, _out, err = git(["add", "--", *to_stage[i:i + CHUNK]])
        if rc != 0:
            log("commit", f"{pre} lane={lane} stage FAILED rc={rc} detail={err[:200]!r}")
    paths = staged_of(to_stage)
    if not paths:
        log("commit", f"{pre} lane={lane} commit SKIPPED (nothing staged) "
                      f"left-uncommitted={' '.join(to_stage)}")
        return
    subject = subject_for(lane)
    rc, _out, err = git(["-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}",
                         "commit", "-q", "-m", subject, "--", *paths], timeout=60)
    if rc == 0:
        log("commit", f"{pre} lane={lane} committed={len(paths)} subject={subject!r}")
    else:
        # Honest about its own failure: the log is where a reader goes to find out what the hook
        # did, and a swallowed refusal there is worse than the failure it hides.
        log("commit", f"{pre} lane={lane} commit FAILED rc={rc} paths={len(paths)} "
                      f"detail={err[:200]!r}")
        log("commit", f"{pre} lane={lane} uncommitted-after-failure: {' '.join(paths)}")


def main() -> None:
    auto_commit(read_input(),
                lambda sid, _prefixes: touched_paths(sid),
                lambda lane: f"session-end auto-commit: [{lane}] {time.strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    guarded(main)
