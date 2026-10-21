#!/usr/bin/env python3
"""reviewed.py — run a live script only when its exact bytes were reviewed (PERMDESIGN-1).

    python3 tools/reviewed.py add SCRIPT --review RECORD --by REVIEWER
    python3 tools/reviewed.py check SCRIPT
    python3 tools/reviewed.py run [--python PY] SCRIPT [ARGS ...]   # what tools/run_reviewed.sh calls
    python3 tools/reviewed.py allow-interpreter REPO PY    # the owner, from his own terminal
    python3 tools/reviewed.py disallow-interpreter REPO    # likewise

THE LEDGER is `reviewed.tsv` in the plugin's state folder, one row per reviewed script:
sha256, path, reviewer, review record, date. `add` appends a row and nothing else ever changes one;
each add, refusal and run also goes to `reviewed.log` there. A row is keyed on the SHA-256 of the
file's bytes, so any edit to the script — one character — makes a different sha, and the edited
script is not reviewed until someone adds it again. The path is recorded for the reader; the sha
alone decides.

`run` refuses, printing the sha and the ledger path, when the sha is not in the ledger. It runs
Python scripts only, and the script file that runs is the file it hashed: the interpreter is started
isolated (`-I`: no current directory on `sys.path`, no PYTHON* variables, no user site) with a small
runner that reads the file again, hashes what it read, refuses if that differs, and compiles those
same bytes with `__file__`, `sys.argv[0]` and the script's folder first on `sys.path`, as
`python SCRIPT` would. So a write between the check and the start cannot slip in unreviewed code.
The interpreter is the plugin's configured `python` (config.json), else the Python running this
tool, never one found in the tree the script lives in: a session can write a `.venv/bin/python`.

ONE exception, the owner's (RUNREVIEWED-2): `run --python PY` uses PY when config.json's
`reviewed_interpreters` lists PY for a repository folder the script lives under, e.g.
`{"~/code/miner": "~/code/miner/.venv/bin/python"}`. Any other `--python`
is refused and nothing runs; without `--python` nothing changes. Listing an in-tree interpreter is
the owner accepting, for that one repository, that a session can rewrite it: the review still covers
the script's exact bytes, and still nothing else. The key is written by `allow-interpreter`, which
refuses inside a Claude session (its environment carries CLAUDECODE / CLAUDE_CODE_SESSION_ID), and
by no other tool: `configure.py` writes `limits` only.

What it does NOT defend against, named: (1) a session that writes a row into the ledger itself —
the ledger is a plain file; `add` is the sanctioned writer, and the allow rule is given for `run`
only; (2) what the reviewed script IMPORTS — a module planted beside it (a `json.py`) or in the
interpreter's site-packages runs, exactly as under `python SCRIPT`; the review covers one file;
(3) the plugin's own modules (`config.py` here) and the `python3` on PATH that starts this tool are
trusted, not hashed, like every hook of the plugin; (4) a script that needs a virtual
environment's packages runs only when `python` in config.json names that interpreter, and that
interpreter must live outside any tree a session writes, or be listed in `reviewed_interpreters`;
`GEDAECHTNIS_CONFIG` is trusted likewise; (5) a session that edits config.json directly, or runs
`allow-interpreter` with the session variables unset: the refusal is a guard against the ordinary
path, not a lock on the file.
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, sys, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "tools"))                 # `-I` drops the script's own folder
import config as paths             # noqa: E402  — hooks/config.py: the state folder

COLUMNS = ("sha256", "path", "reviewer", "review", "date")
REFUSED = 3

# Runs inside the chosen interpreter: re-read, re-hash, refuse on any difference, then compile the
# bytes that were hashed. `sys.argv` here is [-c, sha, path, *args].
RUNNER = r"""
import hashlib, os, sys
sha, path = sys.argv[1], sys.argv[2]
with open(path, "rb") as fh:
    src = fh.read()
if hashlib.sha256(src).hexdigest() != sha:
    sys.stderr.write("refused: %s changed after it was checked; nothing ran\n" % path)
    sys.exit(3)
sys.argv = [path] + sys.argv[3:]
sys.path.insert(0, os.path.dirname(path))
g = {"__name__": "__main__", "__file__": path, "__builtins__": __builtins__}
exec(compile(src, path, "exec"), g)
"""


def ledger() -> Path:
    return paths.state() / "reviewed.tsv"


def log(line: str) -> None:
    try:
        paths.state().mkdir(parents=True, exist_ok=True)
        with (paths.state() / "reviewed.log").open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\t" + line + "\n")
    except OSError:
        pass


def who() -> str:
    e = os.environ
    return e.get("GEDAECHTNIS_SEAT") or e.get("CLAUDE_CODE_SESSION_ID") or "terminal"


def sha_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rows() -> list[dict]:
    try:
        text = ledger().read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for ln in text.splitlines():
        f = ln.split("\t")
        if len(f) == len(COLUMNS) and len(f[0]) == 64 and f[0] != COLUMNS[0]:
            out.append(dict(zip(COLUMNS, f)))
    return out


def find(sha: str) -> dict | None:
    return next((r for r in rows() if r["sha256"] == sha), None)


def script_path(raw: str) -> Path:
    p = Path(raw).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"not a file: {raw}")
    return p


def _clean(v: str, what: str) -> str:
    v = (v or "").strip()
    if not v or any(c in v for c in "\t\r\n"):
        raise ValueError(f"{what} is empty or carries a tab or line break")
    return v


def add(raw: str, review: str, by: str) -> tuple[int, str]:
    try:
        p = script_path(raw)
        rec = Path(review).expanduser().resolve()
        if not rec.is_file():
            raise ValueError(f"the review record is not a file: {review}")
        by = _clean(by, "--by")
        fields = [sha_of(p.read_bytes()), _clean(str(p), "the path"), by, _clean(str(rec), "--review"),
                  datetime.date.today().isoformat()]
    except (OSError, ValueError) as e:
        log(f"add-refused\t{raw}\t{e}\tby {who()}")
        return REFUSED, f"refused: {e}"
    old = find(fields[0])
    if old:
        return 0, f"{fields[0]} is already reviewed ({old['path']}, by {old['reviewer']}, {old['date']})"
    L = ledger()
    L.parent.mkdir(parents=True, exist_ok=True)
    with L.open("a", encoding="utf-8") as fh:
        if fh.tell() == 0:
            fh.write("\t".join(COLUMNS) + "\n")
        fh.write("\t".join(fields) + "\n")
    log(f"add\t{fields[0]}\t{fields[1]}\treviewer {by}\treview {fields[3]}\tby {who()}")
    return 0, f"reviewed: {fields[0]} {fields[1]} (ledger {L})"


def check(raw: str) -> tuple[int, str, str | None]:
    """(rc, verdict line, sha or None)."""
    try:
        p = script_path(raw)
        sha = sha_of(p.read_bytes())
    except (OSError, ValueError) as e:
        return REFUSED, f"refused: {e}", None
    r = find(sha)
    if not r:
        return REFUSED, (f"NOT REVIEWED: {p} sha256 {sha} is not in {ledger()}; a review adds it with "
                         f"`reviewed.py add {p} --review <record> --by <reviewer>`"), sha
    return 0, f"REVIEWED: {p} sha256 {sha} by {r['reviewer']} on {r['date']} ({r['review']})", sha


def interpreter() -> str:
    """The plugin's configured `python`, else ours. Never the script's tree: a session writes there."""
    return paths.python()


def _abs(py: str) -> str:
    """Absolute and normalised, symlinks kept: a venv's `bin/python` IS a symlink."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(py)))


def allowed_interpreter(script: Path, py: str) -> tuple[bool, str]:
    """(True, path) when config.json's `reviewed_interpreters` lists PY for a repository folder
    holding SCRIPT; else (False, why)."""
    want = _abs(py)
    table = paths.reviewed_interpreters()
    repos = [r for r in table if script == Path(r) or Path(r) in script.parents]
    if not repos:
        return False, (f"no repository holding {script} is in `reviewed_interpreters` "
                       f"({paths.config_path()}); the owner adds one with "
                       f"`python3 {Path(__file__).resolve()} allow-interpreter <repo> <python>`")
    if not any(want in table[r] for r in repos):
        return False, (f"{want} is not listed for {', '.join(repos)} in `reviewed_interpreters` "
                       f"(listed: {', '.join(p for r in repos for p in table[r])})")
    if not (os.path.isfile(want) and os.access(want, os.X_OK)):
        return False, f"{want} is listed but is not an executable file"
    return True, want


def run(raw: str, args: list[str], python: str | None = None) -> int:
    rc, verdict, sha = check(raw)
    if rc != 0 or sha is None:
        print(verdict, file=sys.stderr)
        log(f"run-refused\t{sha or '-'}\t{raw}\tby {who()}")
        return rc
    p = script_path(raw)
    if p.suffix != ".py":
        print(f"refused: {p} is not a Python script; run_reviewed runs only the bytes it hashed, "
              "which it can do for Python", file=sys.stderr)
        log(f"run-refused\t{sha}\t{p}\tnot python\tby {who()}")
        return REFUSED
    if python is None:
        py = interpreter()
    else:
        ok, py = allowed_interpreter(p, python)
        if not ok:
            print(f"refused: --python {python}: {py}; nothing ran", file=sys.stderr)
            log(f"run-refused\t{sha}\t{p}\tinterpreter {_abs(python)} not listed\tby {who()}")
            return REFUSED
    log(f"run\t{sha}\t{p}\t{py}\tby {who()}")
    sys.stdout.flush(); sys.stderr.flush()
    os.execv(py, [py, "-I", "-c", RUNNER, sha, str(p), *args])
    return 2                                              # pragma: no cover — execv does not return


def _same_folder(key: str, r: Path) -> bool:
    """KEY names the folder R: by resolved path, or (a case-insensitive disk spells one folder two
    ways and `resolve()` keeps the spelling) by being the same directory on disk."""
    k = Path(os.path.expanduser(key))
    if not k.is_absolute():
        # The reader ignores a relative key. Resolving one here would read it against whatever
        # folder the owner's terminal is in, and carry its DEAD values over to the absolute key —
        # making an interpreter runnable that no entry ever allowed (review 2026-10-06).
        return False
    try:
        return k.resolve() == r or (k.exists() and r.exists() and os.path.samefile(k, r))
    except OSError:
        return False


def set_interpreter(repo: str, py: str | None) -> tuple[int, str]:
    """The owner's writer for `reviewed_interpreters`: add PY for REPO, or (PY None) drop REPO."""
    import configure                                     # noqa: PLC0415 — tools/configure.py
    if configure.in_session():
        log(f"allow-refused\t{repo}\t{py or '-'}\tin a session\tby {who()}")
        return REFUSED, ("refused: `reviewed_interpreters` is the owner's key; set it from your own "
                         "terminal, not inside a Claude session")
    r = Path(repo).expanduser().resolve()
    if py is not None:
        if not r.is_dir():
            return REFUSED, f"refused: not a folder: {repo}"
        if r == paths.home() or r in paths.home().parents:
            return REFUSED, f"refused: {r} is not one repository; name the repository folder"
        py = _abs(py)
        if not (os.path.isfile(py) and os.access(py, os.X_OK)):
            return REFUSED, f"refused: not an executable file: {py}"
    p = paths.config_path()
    try:
        data = configure.read_config(p)
    except (OSError, ValueError) as e:
        return REFUSED, f"refused: {p} cannot be read as a JSON object ({e}); fix it by hand first"
    table = data.get("reviewed_interpreters")
    table = dict(table) if isinstance(table, dict) else {}
    # Every spelling of this folder a hand edit may have left (`~/x`, a trailing slash): the reader
    # honours them all, so the writer must find them all, or a revoke leaves one standing.
    same = [k for k in table if isinstance(k, str) and k.strip() and _same_folder(k, r)]
    cur = []
    for k in same:
        v = table[k]
        for x in ([v] if isinstance(v, str) else v if isinstance(v, list) else []):
            # Only what the reader honours (an absolute path): a relative value was dead in the
            # file, and making it absolute against this terminal's folder would bring it to life.
            if isinstance(x, str) and os.path.isabs(os.path.expanduser(x)):
                x = os.path.normpath(os.path.expanduser(x))
                if x not in cur:
                    cur.append(x)
    old = {k: table[k] for k in same}
    if py is None:
        if not same:
            return 0, f"{r} is not in `reviewed_interpreters`; nothing to do"
    elif py in cur and same == [str(r)]:
        return 0, f"{py} is already listed for {r}"
    for k in same:
        table.pop(k)
    if py is not None:
        table[str(r)] = cur + ([] if py in cur else [py])
    b = configure.backup(p, "reviewed_interpreters")
    if table:
        data["reviewed_interpreters"] = table
    else:
        data.pop("reviewed_interpreters", None)
    configure.write_atomic(p, data)
    log(f"interpreters\t{r}\t{json.dumps(old)} -> {json.dumps(table.get(str(r)))}\tby {who()}"
        f"\tbackup {b or 'none'}")
    return 0, f"reviewed_interpreters[{r}] = {json.dumps(table.get(str(r)))} ({p})"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["run"]:
        rest = argv[1:]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        python = None
        if rest[:1] == ["--python"] and len(rest) >= 2:
            python, rest = rest[1], rest[2:]
        elif rest[:1] and rest[0].startswith("--python="):
            python, rest = rest[0].split("=", 1)[1], rest[1:]
        if not rest or (python is not None and not python):
            print("usage: reviewed.py run [--python PY] SCRIPT [ARGS ...]", file=sys.stderr)
            return 2
        return run(rest[0], rest[1:], python)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("script")
    a.add_argument("--review", required=True, help="the review record (verdict file) that passed it")
    a.add_argument("--by", required=True, help="who reviewed it")
    c = sub.add_parser("check")
    c.add_argument("script")
    ai = sub.add_parser("allow-interpreter", help="the owner's: let `run --python PY` use PY for REPO")
    ai.add_argument("repo")
    ai.add_argument("python")
    di = sub.add_parser("disallow-interpreter", help="the owner's: drop REPO's interpreters")
    di.add_argument("repo")
    ns = ap.parse_args(argv)
    if ns.cmd == "add":
        rc, msg = add(ns.script, ns.review, ns.by)
    elif ns.cmd == "allow-interpreter":
        rc, msg = set_interpreter(ns.repo, ns.python)
    elif ns.cmd == "disallow-interpreter":
        rc, msg = set_interpreter(ns.repo, None)
    else:
        rc, msg, _ = check(ns.script)
    print(msg, file=sys.stdout if rc == 0 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
