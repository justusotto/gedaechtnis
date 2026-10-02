#!/usr/bin/env python3
"""mergesha.py — check a claimed merge sha instead of believing it.

A session that manages others is told "merged abc1234" many times a day, and the claim is only as
good as the git call nobody made. This module makes that call and answers in one line:

    abc1234 — on main (repo /path/to/repo)
    abc1234 — NOT on main (repo /path/to/repo)
    abc1234 — unknown object (repo /path/to/repo)

and, when the sha is a commit in the memory vault, the files it touched — so a claim like "the row
mark is in abc1234" can be checked against what the commit actually carries.

Two ways in:

  * `python3 mergesha.py verify-merge <sha> [<repo>]` — one call, one line, exit 0 when the sha is
    on main, 1 when it is not, 2 when git does not know it.
  * a UserPromptSubmit chore (`chore.py mergesha`): when a message from another session says
    `MERGED <sha>` or ``merged `<sha>` ``, the result is added to the session's context. It never
    blocks and never edits anything; a prompt that is not a cross-session message costs one
    substring test, and one with no claim in it one regular expression more.

Read-only, except `ff`: `git merge-base --is-ancestor` and, for a vault commit, `git show --name-only`.

THE LANDING (`ff`, PERMDESIGN-1). `python3 mergesha.py ff <branch> [--expect-main <sha>] [<repo>]`
fast-forwards `main` to the branch tip and verifies it, in ONE command, so a narrow allow rule can
let a builder land without anybody typing the merge line:

  * it runs in the checkout where `main` is checked out, and reads `main` itself, in this command;
  * it refuses when `main` is not an ancestor of the tip (main moved since the branch was stacked:
    restack), when `--expect-main` is given and `main` is not that commit, when another session
    holds the merge window, and when the suite gate is on and no green full run covers the tip
    (with no escape: the door's typed `SUITE_GATE_ALLOW=1` prefix does not reach `ff`);
  * it merges the tip's SHA (`git merge --ff-only`), re-reads `main`, and prints the same
    `verify-merge` line; a window it granted itself, or this session held, is released after.

Exit 0 on main, 3 refused, 2 usage or git failure. It never pushes, rebases or resets.
"""
from __future__ import annotations
import os, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common

ON, NOT_ON, UNKNOWN = "on main", "NOT on main", "unknown object"
SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
# `MERGED 1a2b3c4`, `merged 1a2b3c4d`, ``merged `1a2b3c4` `` — the forms a builder's report uses.
# Word-bounded so "unmerged abc1234" and "merged-by" do not count.
CLAIM = re.compile(r"(?<![\w-])merged:?\s+`?([0-9a-fA-F]{7,40})`?(?![\w-])", re.IGNORECASE)
MAX_CLAIMS = 5
FILES_SHOWN = 8


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return None


def verify(sha: str, repo: Path, ref: str = "main") -> dict:
    """{sha, repo, state, files} for one claimed sha. `files` is filled for vault commits only.

    UNKNOWN when the string is not a sha, when git does not have that object, or when git could
    not be asked. The ancestor test is read from its EXIT CODE: 0 = ancestor, 1 = not; anything
    else (a missing `ref`, a corrupt repo) is not evidence either way and reads as UNKNOWN."""
    out = {"sha": sha, "repo": str(repo), "state": UNKNOWN, "files": None}
    if not SHA.match(sha or ""):
        return out
    # One git call answers all three. `merge-base --is-ancestor` exits 128 for an object git does
    # not have, exactly as for a ref it cannot resolve, so a separate existence check would be a
    # second process for an answer this one already gives.
    p = _git(repo, "merge-base", "--is-ancestor", sha, ref)
    if p is None:
        return out
    if p.returncode == 0:
        out["state"] = ON
    elif p.returncode == 1:
        out["state"] = NOT_ON
    else:
        return out
    if _same(repo, common.VAULT):
        s = _git(repo, "show", "--name-only", "--format=", sha)
        if s is not None and s.returncode == 0:
            out["files"] = [l for l in s.stdout.splitlines() if l.strip()]
    return out


def _same(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def line(r: dict, ref: str = "main") -> str:
    state = r["state"].replace("main", ref)
    s = f"{r['sha']} — {state} (repo {r['repo']})"
    files = r.get("files")
    if files is not None:
        shown = ", ".join(files[:FILES_SHOWN]) or "no files"
        more = f", and {len(files) - FILES_SHOWN} more" if len(files) > FILES_SHOWN else ""
        s += f"; it touched: {shown}{more}"
    return s


def resolve(sha: str, cwd: str | None) -> dict:
    """Verify in the cwd's repository, and — when git there does not know the sha — in the vault.

    A seat is told about repo merges and vault commits in the same breath; which one a bare sha
    names is exactly what the reader does not know, so both are asked, repo first."""
    repo = common.git_root(cwd) if cwd else None
    first = verify(sha, repo) if repo else None
    if first and first["state"] != UNKNOWN:
        return first
    vault = common.VAULT
    if vault and Path(vault).is_dir() and not (repo and _same(repo, vault)):
        second = verify(sha, Path(vault))
        if second["state"] != UNKNOWN:
            return second
    return first or {"sha": sha, "repo": str(repo or cwd or "?"), "state": UNKNOWN, "files": None}


def claims(text: str) -> list[str]:
    """The shas a message CLAIMS were merged, in order, without repeats, at most MAX_CLAIMS."""
    out: list[str] = []
    for m in CLAIM.finditer(text or ""):
        s = m.group(1)
        if s not in out:
            out.append(s)
        if len(out) >= MAX_CLAIMS:
            break
    return out


def from_prompt(inp: dict) -> str | None:
    """The context line for an inbound cross-session message that claims a merge, or None.

    Only a message from ANOTHER SESSION is read — the harness wraps it in a
    `<cross-session-message` element. A person typing "merged abc1234" is telling the session
    something, not reporting a claim to be audited."""
    text = inp.get("prompt") or ""
    if "<cross-session-message" not in text:
        return None
    shas = claims(text)
    if not shas:
        return None
    rows = [line(resolve(s, inp.get("cwd"))) for s in shas]
    return "Merge claims in that message, checked with git: " + " | ".join(rows) + "."


REFUSED = 3


def _one(repo: Path, *args: str) -> str | None:
    p = _git(repo, *args)
    return p.stdout.strip() if p is not None and p.returncode == 0 else None


def ff(branch: str, repo: Path, expect: str | None = None, sid: str | None = None) -> tuple[int, str]:
    """Fast-forward `main` to `branch` in `repo` (the checkout holding `main`), then verify.

    Every precondition is read in this call; nothing is taken from the caller but the names."""
    top = _one(repo, "rev-parse", "--show-toplevel")
    if not top:
        return 2, f"not a git repository: {repo}"
    top = Path(top)
    if _one(top, "symbolic-ref", "--short", "-q", "HEAD") != "main":
        return REFUSED, (f"refused: `main` is not checked out in {top}; run ff from the checkout "
                         "that holds `main`")
    if branch.startswith("-"):
        return 2, f"not a branch or commit: {branch}"
    tip = _one(top, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{branch}^{{commit}}")
    before = _one(top, "rev-parse", "--verify", "--quiet", "refs/heads/main")
    if not tip or not before:
        return 2, f"git does not know `{branch}` (or `main`) in {top}"
    if expect is not None:
        full = _one(top, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{expect}^{{commit}}")
        if not SHA.match(expect) or full != before:
            return REFUSED, (f"refused: main moved: expected {expect}, main is {before[:10]}; "
                             "restack onto main and land again")
    if tip == before:
        return 0, line(verify(tip, top)) + " (already main; nothing merged)"
    anc = _git(top, "merge-base", "--is-ancestor", before, tip)
    if anc is None or anc.returncode != 0:
        return REFUSED, (f"refused: main {before[:10]} is not an ancestor of `{branch}` {tip[:10]}: "
                         "main moved since the branch was stacked; restack and land again")
    import suitegate                                     # noqa: PLC0415 — only a landing pays for it
    # No escape here: the door's `SUITE_GATE_ALLOW=1` must be typed in the command, where the
    # transcript shows it; an allowed `ff` has no such prefix, so a red or missing run refuses.
    if suitegate.enabled() and (top / "gedaechtnis" / "tests").is_dir():
        why = suitegate.check(top, tip)
        if why:
            return REFUSED, "refused: " + why
    import mergewindow                                   # noqa: PLC0415
    sid = sid or os.environ.get("CLAUDE_CODE_SESSION_ID") or f"mergesha-ff-{os.getpid()}"
    mine = False
    if (top / mergewindow.MARKER).is_file():
        cur = mergewindow.read(top)
        if cur and cur.get("holder") != sid:
            return REFUSED, (f"refused: the merge window is held by session {cur['holder']}; wait "
                             "for its verify-merge, then land again")
        rc, msg = mergewindow.grant(sid, top)
        if rc != 0:
            return REFUSED, "refused: " + msg
        mine = True
    try:
        m = _git(top, "merge", "--ff-only", "--quiet", tip)
        after = _one(top, "rev-parse", "--verify", "--quiet", "refs/heads/main")
        if m is None or m.returncode != 0 or after != tip:
            err = ((m.stderr or m.stdout).strip().splitlines() or ["?"])[-1] if m is not None else "git did not answer"
            return 2, f"the fast-forward did not land: main is {(after or '?')[:10]}, tip {tip[:10]}: {err}"
        r = verify(tip, top)
        return (0 if r["state"] == ON else 2), f"main {before[:10]} -> {tip[:10]}; " + line(r)
    finally:
        if mine:
            mergewindow.release(sid, top)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "ff":
        rest, expect = list(argv[1:]), None
        if "--expect-main" in rest:
            i = rest.index("--expect-main")
            if i + 1 >= len(rest):
                rest = []
            else:
                expect = rest[i + 1]
                del rest[i:i + 2]
        if not 1 <= len(rest) <= 2:
            print("usage: mergesha.py ff <branch> [--expect-main <sha>] [<repo>]", file=sys.stderr)
            return 2
        rc, msg = ff(rest[0], Path(rest[1]).expanduser() if len(rest) > 1 else Path.cwd(), expect)
        print(msg, file=sys.stdout if rc == 0 else sys.stderr)
        return rc
    if len(argv) < 2 or argv[0] != "verify-merge":
        print("usage: mergesha.py verify-merge <sha> [<repo>] · mergesha.py ff <branch> "
              "[--expect-main <sha>] [<repo>]", file=sys.stderr)
        return 2
    sha = argv[1]
    if len(argv) > 2:
        r = verify(sha, Path(argv[2]).expanduser())
    else:
        r = resolve(sha, str(Path.cwd()))
    print(line(r))
    return {ON: 0, NOT_ON: 1}.get(r["state"], 2)


if __name__ == "__main__":                                  # pragma: no cover - CLI
    raise SystemExit(main())
