#!/usr/bin/env python3
"""reviewed.py — run a live script only when its exact bytes were reviewed (PERMDESIGN-1).

    python3 tools/reviewed.py add SCRIPT --review RECORD --by REVIEWER
    python3 tools/reviewed.py check SCRIPT
    python3 tools/reviewed.py run SCRIPT [ARGS ...]        # what tools/run_reviewed.sh calls

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

What it does NOT defend against, named: (1) a session that writes a row into the ledger itself —
the ledger is a plain file; `add` is the sanctioned writer, and the allow rule is given for `run`
only; (2) what the reviewed script IMPORTS — a module planted beside it (a `json.py`) or in the
interpreter's site-packages runs, exactly as under `python SCRIPT`; the review covers one file;
(3) the plugin's own modules (`config.py` here) and the `python3` on PATH that starts this tool are
trusted, not hashed, like every hook of the plugin; (4) a script that needs a virtual
environment's packages runs only when `python` in config.json names that interpreter, and that
interpreter must live outside any tree a session writes; `GEDAECHTNIS_CONFIG` is trusted likewise.
"""
from __future__ import annotations
import argparse, datetime, hashlib, os, sys, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
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


def run(raw: str, args: list[str]) -> int:
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
    py = interpreter()
    log(f"run\t{sha}\t{p}\t{py}\tby {who()}")
    sys.stdout.flush(); sys.stderr.flush()
    os.execv(py, [py, "-I", "-c", RUNNER, sha, str(p), *args])
    return 2                                              # pragma: no cover — execv does not return


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["run"]:
        rest = argv[1:]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        if not rest:
            print("usage: reviewed.py run SCRIPT [ARGS ...]", file=sys.stderr)
            return 2
        return run(rest[0], rest[1:])
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("script")
    a.add_argument("--review", required=True, help="the review record (verdict file) that passed it")
    a.add_argument("--by", required=True, help="who reviewed it")
    c = sub.add_parser("check")
    c.add_argument("script")
    ns = ap.parse_args(argv)
    if ns.cmd == "add":
        rc, msg = add(ns.script, ns.review, ns.by)
    else:
        rc, msg, _ = check(ns.script)
    print(msg, file=sys.stdout if rc == 0 else sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
