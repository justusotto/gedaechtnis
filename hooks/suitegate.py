#!/usr/bin/env python3
"""suitegate.py — a merge is called green only after the full suite ran on the tree being shipped.

Two halves, and the gate never runs a test itself:

  * THE RECORD. `pytest_sessionfinish` below appends one line per pytest run to
    `<git common dir>/gedaechtnis/suite-runs.jsonl`: the commit and tree the run was taken on,
    whether tracked files were modified at the time, whether it was a FULL run, and its counts.
    A repository opts in with a `conftest.py` at its root that loads this file by path and
    re-exports `pytest_sessionfinish` (this repository's own root `conftest.py` is the example).

    The file lives under git's common directory, so every worktree of one repository writes to
    and reads from the same record, and nothing appears in anybody's working tree.

  * THE GATE. `refusal(cmd, cwd)` is called by the Bash door before `git merge <rev>` or
    `git push` runs, in a repository that carries `gedaechtnis/tests`. It looks up the newest
    record for the commit being shipped — or for any commit with the identical TREE, because a
    rebase over unrelated commits changes the sha and not the code — and refuses in words when
    there is none, when the run was partial or on modified files, or when it was red.

OFF unless the config file says `"suite_gate": true`. A command whose segment starts with
`SUITE_GATE_ALLOW=1` passes: the stated escape, visible in the command and in the transcript.

What the gate cannot see: a test that passes for the wrong reason. It checks that the run
HAPPENED on this tree and ENDED green, which is the part people skip, and nothing more.
"""
from __future__ import annotations
import json, re, shlex, subprocess, time
from pathlib import Path

RECORD_NAME = "suite-runs.jsonl"
READ_TAIL = 400                      # records read back; a busy repo appends a few a day
ALLOW = "SUITE_GATE_ALLOW=1"


def enabled() -> bool:
    # Imported here, not at the top: the recorder half is loaded by a repository's root conftest
    # by FILE PATH, and must not need this package's directory on `sys.path` — its `config` and
    # `common` would shadow any same-named module in the suite it is recording.
    import config                                           # noqa: PLC0415
    return config._load().get("suite_gate") is True


def _git(repo: Path | str, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def record_file(repo: Path | str) -> Path | None:
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common) / "gedaechtnis" / RECORD_NAME if common else None


# ------------------------------------------------------------------ the record

def partial_reasons(cfg) -> list[str]:
    """Why a pytest run is NOT the full suite. Empty = full.

    Paths on the command line, `-k`, `-m`, `--deselect`, `--ignore`, `-x`/`--maxfail` and
    `--lf`/`--sw` all narrow what ran. Any one of them makes the run a partial one, however many
    tests it collected."""
    out = []
    try:
        from _pytest.config import Config                    # noqa: PLC0415
        if getattr(cfg, "args_source", None) == Config.ArgsSource.ARGS:
            out.append("paths given")
    except Exception:                                        # noqa: BLE001 — never fail a run
        out.append("pytest version unknown")
    opt = cfg.option
    for attr, why in (("keyword", "-k"), ("markexpr", "-m"), ("deselect", "--deselect"),
                      ("ignore", "--ignore"), ("ignore_glob", "--ignore-glob"),
                      ("lf", "--lf"), ("stepwise", "--sw")):
        if getattr(opt, attr, None):
            out.append(why)
    if getattr(opt, "maxfail", 0):
        out.append("-x/--maxfail")
    return out


def pytest_sessionfinish(session, exitstatus):
    """Append this run's record. Never raises: a recorder that broke the test run it records
    would be the tail wagging the dog."""
    try:
        root = Path(str(session.config.rootpath))
        f = record_file(root)
        sha = _git(root, "rev-parse", "HEAD")
        if not f or not sha:
            return
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        stats = getattr(tr, "stats", {}) if tr else {}
        n = lambda k: len(stats.get(k, []))
        rec = {
            "sha": sha, "tree": _git(root, "rev-parse", "HEAD^{tree}"),
            "dirty": bool(_git(root, "status", "--porcelain", "--untracked-files=no")),
            "partial": partial_reasons(session.config),
            "passed": n("passed"), "failed": n("failed"), "errors": n("error"),
            "skipped": n("skipped"), "nodes": session.testscollected,
            "exit": int(exitstatus), "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cwd": str(root),
        }
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:                                        # noqa: BLE001
        return


def records(repo: Path | str) -> list[dict]:
    f = record_file(repo)
    if not f:
        return []
    try:
        lines = f.read_text(encoding="utf-8").splitlines()[-READ_TAIL:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("sha"):
            out.append(r)
    return out


def green(r: dict) -> bool:
    """A full, clean run in which something ACTUALLY PASSED and nothing failed or errored.

    `passed > 0` and not merely `nodes > 0`: a run where every collected test was skipped — a broken
    fixture, an environment gate that skips the whole suite — has no failures and proves nothing."""
    return (r.get("failed") == 0 and r.get("errors") == 0 and (r.get("passed") or 0) > 0
            and not r.get("partial") and not r.get("dirty"))


# ------------------------------------------------------------------ the gate

def shipped_revs(seg: str) -> list[str] | None:
    """What a `git merge` / `git push` segment ships, or None when it is neither (or cannot tell).

    `git merge <rev>...` ships each named rev. `git push` ships each refspec's source, or HEAD
    when it names none (see `_pushed_revs`). `merge --abort/--continue/--quit` names no rev, so it ships nothing and is not gated."""
    try:
        toks = shlex.split(seg)
    except ValueError:
        return None
    while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
        toks = toks[1:]
    if not toks or toks[0] != "git":
        return None
    i = 1
    while i < len(toks) and toks[i].startswith("-"):
        i += 2 if toks[i] in ("-C", "-c") else 1
    if i >= len(toks):
        return None
    sub, rest = toks[i], toks[i + 1:]
    if sub == "push":
        return _pushed_revs(rest)
    if sub != "merge":
        return None
    revs, j = [], 0
    while j < len(rest):
        t = rest[j]
        if t in ("-m", "-F", "-s", "-X", "--strategy", "--strategy-option", "--file", "--message"):
            j += 2
            continue
        if not t.startswith("-"):
            revs.append(t)
        j += 1
    return revs or None


def _pushed_revs(rest: list[str]) -> list[str] | None:
    """What `git push [<opts>] [<remote> [<refspec>...]]` ships: each refspec's SOURCE.

    `feature:main` ships `feature`, not whatever HEAD is — gating HEAD there would let a red branch
    through while standing on a green one. A leading `+` (force) is stripped. `:dst` (a deletion)
    ships nothing. No refspec at all, or `--all`/`--mirror`, ships from HEAD's side of the repo and
    is gated on HEAD — the branches such a push carries cannot all be named here."""
    with_value = ("-o", "--push-option", "--repo", "--receive-pack", "--exec")
    pos, j = [], 0
    while j < len(rest):
        t = rest[j]
        if t in with_value:
            j += 2
            continue
        if not t.startswith("-"):
            pos.append(t)
        j += 1
    specs = pos[1:]                                   # pos[0] is the remote
    if not specs:
        return ["HEAD"]
    revs = []
    for spec in specs:
        src = spec.lstrip("+").split(":", 1)[0]
        if src:
            revs.append(src)
    return revs or None


def check(repo: Path, rev: str) -> str | None:
    """The refusal for shipping `rev` from `repo`, or None when a green full run covers it."""
    sha = _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if not sha:
        return None                  # git cannot resolve it: git will refuse the merge itself
    tree = _git(repo, "rev-parse", f"{sha}^{{tree}}")
    mine = [r for r in records(repo) if r.get("sha") == sha or (tree and r.get("tree") == tree)]
    what = f"`{rev}` = {sha[:10]} (tree {tree[:10] if tree else '?'})"
    if not mine:
        return (f"No full-suite run is recorded for {what}. Run the whole suite on exactly that "
                "commit — no paths, no -k, no --ignore, nothing modified — then merge. The record "
                "is written by the suite itself; the gate never runs it. To ship anyway, start "
                f"the command with {ALLOW} and say why in the same message.")
    good = [r for r in mine if green(r)]
    if good:
        return None
    r = mine[-1]
    if r.get("dirty"):
        why = "was taken with tracked files modified, so it tested a tree that is not this commit"
    elif r.get("partial"):
        why = "was partial (" + ", ".join(r["partial"]) + ")"
    else:
        why = (f"ended red: {r.get('failed')} failed, {r.get('errors')} errors, "
               f"{r.get('passed')} passed of {r.get('nodes')} collected")
    return (f"The newest suite run for {what}, finished {r.get('finished')}, {why}. A merge is "
            "called green only after the full suite passes on the tree being shipped. To ship "
            f"anyway, start the command with {ALLOW} and say why in the same message.")


def refusal(cmd: str, cwd: str | None, git_segments) -> str | None:
    """The Bash door's entry point. `git_segments` is the gate's own segment walker, passed in so
    `cd` and `-C` are resolved exactly as every other git door resolves them."""
    if not enabled():
        return None
    for seg, repo in git_segments(cmd, cwd):
        if seg.lstrip().startswith(ALLOW):
            continue
        revs = shipped_revs(seg)
        if not revs:
            continue
        top = _git(repo, "rev-parse", "--show-toplevel")
        if not top or not (Path(top) / "gedaechtnis" / "tests").is_dir():
            continue
        for rev in revs:
            r = check(Path(top), rev)
            if r:
                return r
    return None
