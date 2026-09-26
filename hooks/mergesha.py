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

Read-only throughout: `git merge-base --is-ancestor` and, for a vault commit, `git show --name-only`.
"""
from __future__ import annotations
import re, subprocess, sys
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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] != "verify-merge":
        print("usage: mergesha.py verify-merge <sha> [<repo>]", file=sys.stderr)
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
