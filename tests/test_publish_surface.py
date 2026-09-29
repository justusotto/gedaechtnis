"""PLUGDIR-2 — the README's "What this plugin runs, sends and fetches" section, checked against
the source instead of asserted.

A plugin directory's review looks for behaviour a plugin does not disclose: sending data
elsewhere, running hidden code. So the disclosure is a CLAIM about every shipped file, and this
suite reads every shipped file (tests and evals aside) with the `ast` module:

  * no network library is imported;
  * every program started through `subprocess` / `os.system` is one the README names, and a call
    whose program is decided at run time sits in a file on a known list — a new one reddens here
    until the README says what it runs;
  * every `osascript` call passes its path through `argv`, never inside the script text (the
    worktree sweep interpolated the path until 2026-09-26: a `"` in it ran the rest as AppleScript).

Each rule has a positive control over a planted source string, so a scan that walks nothing, or
a matcher that matches nothing, cannot pass.
"""
from __future__ import annotations
import ast
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
README = (PLUGIN / "README.md").read_text(encoding="utf-8")

NETWORK = {"urllib", "http", "requests", "socket", "ftplib", "smtplib", "ssl", "asyncio",
           "aiohttp", "httpx", "websocket", "xmlrpc", "telnetlib", "poplib", "imaplib"}
CALLS = {"run", "Popen", "check_output", "call", "check_call", "system", "popen"}
# The plugin's own thin wrappers around subprocess.run, called with the argv list as their first
# argument. A scan of `subprocess.*` alone missed the worktree sweep's osascript, which goes
# through `sh(...)` — so the wrappers are read as what they are.
HELPERS = {"sh", "_sh", "_sh2", "_run", "_git", "run_git"}
# The programs the README lists, as the source spells them.
DECLARED = {"'git'", "'ps'", "'lsof'", "'grep'", "'cp'", "'osascript'", "sys.executable",
            "config.python()", "'/bin/bash'",
            "'screen'", "'tmux'", "'sysctl'"}   # TERMOVERLOAD-1: tools/sessions.py + the close check
# Files whose program is decided at run time; each is named in the README (the plugin's own
# scripts, `notify_command`, `answer_router`, `claim_tool`) or runs `git`/`cp` through a helper.
DYNAMIC_OK = {"hooks/answers.py", "hooks/boot_check.py", "hooks/maintenance.py",
              "hooks/session_start.py", "hooks/worktree.py", "init.py", "tools/status.py",
              "hooks/claim.py",
              "hooks/common.py",   # PLATFORM-1: the Trash command of this machine (README "Platforms")
              # TERMOVERLOAD-1: screen or tmux, whichever this machine has (README names both)
              "hooks/fleet.py", "tools/sessions.py"}


def shipped():
    for p in sorted(PLUGIN.rglob("*.py")):
        rel = p.relative_to(PLUGIN)
        if rel.parts[0] in ("tests", "eval") or "__pycache__" in rel.parts:
            continue
        yield str(rel), p.read_text(encoding="utf-8")


def network_imports(src: str) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names if a.name.split(".")[0] in NETWORK]
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in NETWORK:
            out.append(n.module)
    return out


def _fixed_program(fn: ast.FunctionDef) -> str | None:
    """A wrapper that names its program itself (`subprocess.run(["git", *args])`) returns it; a
    pass-through wrapper (`subprocess.run(args)`) returns None."""
    for c in ast.walk(fn):
        if isinstance(c, ast.Call) and ast.unparse(c.func).startswith("subprocess.") and c.args:
            a = c.args[0]
            if isinstance(a, ast.List) and a.elts and isinstance(a.elts[0], ast.Constant):
                return repr(a.elts[0].value)
    return None


def process_calls(src: str):
    """[(program, call node)] for every subprocess/os call that starts a program."""
    out = []
    tree = ast.parse(src)
    fixed = {f.name: _fixed_program(f) for f in ast.walk(tree)
             if isinstance(f, ast.FunctionDef) and f.name in HELPERS}
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = ast.unparse(n.func)
        head, _, last = name.rpartition(".")
        a = n.args[0] if n.args else None
        if not head and name in HELPERS:
            if fixed.get(name):                         # the wrapper names its program itself
                out.append((fixed[name], n))
            elif isinstance(a, ast.List) and a.elts:    # a pass-through wrapper, literal argv
                out.append((ast.unparse(a.elts[0]).replace('"', "'"), n))
            continue                                    # its own body is scanned where it calls subprocess
        if not ((head == "subprocess" and last in CALLS) or (head == "os" and last in ("system", "popen"))
                or name.startswith(("os.exec", "os.spawn"))):
            continue
        if isinstance(a, ast.List) and a.elts:
            out.append((ast.unparse(a.elts[0]).replace('"', "'"), n))
        else:
            out.append(("<dynamic>", n))
    return out


def osascript_problems(src: str) -> list[str]:
    bad = []
    for prog, n in process_calls(src):
        if prog != "'osascript'":
            continue
        elts = n.args[0].elts
        sep = next((i for i, e in enumerate(elts) if isinstance(e, ast.Constant) and e.value == "--"), None)
        if any(not isinstance(e, ast.Constant) for e in elts[:len(elts) if sep is None else sep]):
            bad.append(f"line {n.lineno}: script text is built at run time")
        if sep is None:
            bad.append(f"line {n.lineno}: no `--` before the path argument")
    return bad


# ---------------------------------------------------------------------------- the rules ----

def test_no_shipped_file_imports_a_network_library():
    files = list(shipped())
    assert len(files) > 40, "the scan walked almost nothing"
    hits = {rel: network_imports(src) for rel, src in files}
    assert {k: v for k, v in hits.items() if v} == {}


def test_every_program_started_is_one_the_readme_names():
    undeclared, dynamic = {}, set()
    for rel, src in shipped():
        for prog, _ in process_calls(src):
            if prog == "<dynamic>":
                dynamic.add(rel)
            elif prog not in DECLARED:
                undeclared.setdefault(rel, []).append(prog)
    assert undeclared == {}, f"a program the README does not list: {undeclared}"
    assert dynamic <= DYNAMIC_OK, f"a new run-time-chosen program: {sorted(dynamic - DYNAMIC_OK)}"
    for word in ("`git`", "`ps`", "`lsof`", "`grep`", "`cp`", "`osascript`", "`python3`", "`bash`",
                 "`notify_command`", "`answer_router`", "`claim_tool`", "sends nothing and fetches nothing",
                 "`/usr/bin/trash`", "`gio trash`", "`trash-put`", "`powershell`",
                 "`screen`", "`tmux`", "`sysctl`"):
        assert word in README, f"the README no longer says {word}"


def test_every_osascript_call_passes_its_path_as_an_argument():
    found = 0
    for rel, src in shipped():
        found += sum(1 for p, _ in process_calls(src) if p == "'osascript'")
        assert osascript_problems(src) == [], rel
    # One since PUBLICPOLISH-1: Finder is the last Trash route, for a Mac without /usr/bin/trash
    # (`common._finder_trash`); worktree removal and retention go through the platform seam.
    assert found >= 1, "fewer osascript calls than the README describes — the scan is not reading them"


# ------------------------------------------------------------------ positive controls ----

def test_control_a_network_import_is_seen():
    assert network_imports("import urllib.request\nfrom http import client\n") == ["urllib.request", "http"]
    assert network_imports("import json\n") == []


def test_control_an_undeclared_and_a_dynamic_program_are_seen():
    got = [p for p, _ in process_calls("import subprocess\nsubprocess.run(['curl', 'x'])\nsubprocess.run(cmd)\n"
                                       "sh(['wget', 'y'])\n")]
    assert got == ["'curl'", "<dynamic>", "'wget'"]
    assert "'curl'" not in DECLARED
    wrapped = ("import subprocess\ndef _git(args):\n    return subprocess.run(['git', *args])\n"
               "_git(['log'])\n")
    assert sorted(p for p, _ in process_calls(wrapped)) == ["'git'", "'git'"]


def test_control_an_interpolated_osascript_path_is_seen():
    old = ('import subprocess\nwt="x"\n'
           'subprocess.run(["osascript", "-e", f\'tell application "Finder" to delete POSIX file "{wt}"\'])\n')
    assert len(osascript_problems(old)) == 2
    good = ('import subprocess\nsubprocess.run(["osascript", "-e", "on run argv", "-e", "x", "--", str(p)])\n')
    assert osascript_problems(good) == []


# ----------------------------------------------------------------- PUBLICPOLISH-1 ----

def test_the_doors_reasons_do_not_assume_one_owner():
    """Public code speaks to whoever installed it: no reason a door prints says "his" or "him"."""
    import re, sys
    sys.path.insert(0, str(PLUGIN / "hooks"))
    import gate
    reasons = [why for _rx, why in gate._protected()]
    assert len(reasons) >= 5
    assert [w for w in reasons if re.search(r"\b(his|him|her)\b", w, re.I)] == []


def test_control_an_owner_pronoun_would_be_seen():
    import re
    assert re.search(r"\b(his|him|her)\b", "~/Pictures holds his photographs", re.I)


# Every removal call in the shipped code, and what it removes. The README says nothing calls `rm`
# on a file the person wrote, that the plugin deletes its own bookkeeping, and names the one tree
# delete. A new removal call reddens here until it is listed with its reason (bare review,
# PUBLICPOLISH-1: the first version scanned `rmtree` only).
REMOVALS = {
    ("archive.py", "unlink"): 1,              # its own temp file after an atomic replace
    ("hooks/common.py", "unlink"): 6,         # pre-exists markers, filelocks, a reserved .trashinfo,
                                              # write_text_atomic's own temp file (COMPACTDOOR-2)
    ("tools/compactpoint.py", "unlink"): 1,   # renew's own temp file when the write fails
    ("hooks/mergewindow.py", "unlink"): 1,    # the merge-window lock it granted
    ("init.py", "unlink"): 2,                 # its own stamp and temp file
    ("hooks/worktree.py", "rmtree"): 1,       # a clone it made, holding nothing unique (README)
    ("hooks/fleet.py", "unlink"): 1,          # the mkstemp file read_screen made a moment before
    ("hooks/deliver.py", "unlink"): 3,        # its own claim of a parked file, once read back or restored
    ("tools/sessions.py", "unlink"): 1,       # its own stale launch pid file in the state dir
    ("tools/suitelock.py", "unlink"): 2,      # its own suite-lock entry, and a dead suite's
}
REMOVERS = {"rmtree", "remove", "unlink", "rmdir", "removedirs"}


def removal_calls(src: str) -> dict:
    out = {}
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in REMOVERS:
            out[n.func.attr] = out.get(n.func.attr, 0) + 1
    return out


def test_the_readme_names_every_direct_delete_the_code_makes():
    """The README's "nothing calls `rm`" was false beside worktree.py's `shutil.rmtree` of a clone
    holding nothing unique. Every removal call must be one listed above, and the README says both
    what the plugin deletes of its own and the one tree it deletes."""
    got = {(rel, verb): n for rel, src in shipped() for verb, n in removal_calls(src).items()}
    assert got == REMOVALS, got
    flat = " ".join(README.split())
    assert "One exception: when a worktree clone the plugin made itself is removed" in flat
    assert "The plugin does delete its own bookkeeping" in flat


def test_control_a_removal_call_is_seen():
    assert removal_calls("import os, shutil\nos.remove(p)\nshutil.rmtree(d)\nPath(x).unlink()\n") == \
        {"remove": 1, "rmtree": 1, "unlink": 1}
