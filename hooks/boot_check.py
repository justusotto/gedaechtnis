#!/usr/bin/env python3
"""boot_check.py — at session end, check whether the files a session BOOTS with still tell the truth.

An always-loaded file is read by every session and acted on without being questioned. When one of
its claims stops being true — a path that has moved, a commit that was never pushed, a wikilink to
a file somebody renamed, an `@`-import whose target is gone — nothing anywhere disagrees with it.
The session simply believes it. That is the failure this hook exists to make loud, and it is the
same shape as every other failure this package hunts: **a wrong state that produces no signal.**

## Why it is a BOOT check and not an audit

The vault this grew from checks its whole tree. This checks the `@`-import closure a session
actually loads — the entrypoints and everything they pull in. Two reasons, and the second is the
important one:

1. It is cheap enough to run at every session end, so it is never "the check we should run
   sometime".
2. **An untrue claim costs in proportion to how often it is READ.** A stale line in a file nobody
   opens is a tidiness problem. The same line in the boot chain is in every session's context,
   shaping every answer, for as long as it stands.

## What it does NOT do

It never repairs anything. It does not judge whether a SHA is the RIGHT commit, only whether it
exists; nor whether a path holds what the text claims, only that it is there. A checker firing on
something that is not a claim is fixed IN THE EXTRACTOR, never silenced with a skip entry — a skip
list is where a real detector goes to die quietly.

## UNCHECKED is not PASSING

No git, an unreadable file, an extension that raised: each is reported in that word. An instrument
that reports its own blindness as health is the failure it was built to catch, with extra steps.

## The `extra_checks` seam

A vault with its own rules adds its own detectors through config `extra_checks` — a list of module
paths, each exposing `run(vault) -> [finding]`. They run AFTER the built-ins and cannot suppress
them; one that raises is UNCHECKED, named, and costs nothing else. This is how the fleet-specific
detectors of the vault this grew from (umbrella divergence, dirty-orphan deltas, a private naming
rule) live outside the package instead of inside it, where no other user would want them.
"""
from __future__ import annotations
import importlib.util, json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import session_start
from common import read_input, log, guarded, VAULT, STATE

STATE_FILE = "boot_check.json"
SCHEMA_VERSION = 1
EVIDENCE_CAP = 8

# A path a person would write: absolute, or `~`-rooted, at least two segments deep. NOT a bare
# relative path — those are ambiguous about their base, and the vault's checker this is reduced from
# learned to report them as a class it cannot resolve rather than to guess a root.
#
# ★ The first segment is NOT a whitelist, and that is a correction rather than a preference. The
# first version matched only `/Users`, `/home`, `/opt`, `/srv`, `/var` — the roots this fleet's own
# machine happens to use — and silently missed every claim about a path anywhere else. Its own test
# fixture caught it: macOS puts temporary directories under `/private/var`, so the fixture's live
# path was never extracted and the test that counts claims went red. A detector whose reach depends
# on which machine wrote the note is a detector that reports health for the wrong reason.
# `>` is in the lookbehind because of a real false positive, measured rather than imagined: the
# fleet's own boot chain writes `<project>/.claude/agents/` as a PLACEHOLDER, and without it the
# checker reported `/.claude/agents` as a path that does not exist — which is true, and not a claim
# anybody made.
PATH_RE = re.compile(r"(?<![\w@:.>])(?:~|/[A-Za-z0-9_.+\-]+)(?:/[A-Za-z0-9_.+\-]+)+/?")
SHA_BACKTICK_RE = re.compile(r"`([0-9a-f]{7,40})`")
SHA_KEYWORD_RE = re.compile(r"\b(?:commit|HEAD)\s+`?([0-9a-f]{7,40})\b", re.I)
WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|[^\]]*)?\]\]")
IMPORT_RE = re.compile(r"^\s*@((?:/|~)\S+)\s*$")
# A hex run with no letter in it is a number — a count, a year, a version. The vault's checker
# learned this from its own false positives; carried here as a rule rather than as a skip list.
HAS_LETTER_RE = re.compile(r"[a-f]")


def state_path() -> Path:
    return STATE / STATE_FILE


def boot_files(cwd: str) -> list[Path]:
    """The @-import closure this session boots with — `session_start`'s own walk, not a second one."""
    return [p for p, _size in session_start.boot_chain_files(
        [config.USER_MEMORY, Path(cwd) / "CLAUDE.md"])]


def extract_claims(text: str) -> list[dict]:
    """Every checkable claim in one file, with the line it sits on.

    Order is the file's own, so a report reads top to bottom."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = IMPORT_RE.match(line)
        if m:
            out.append({"kind": "import", "claim": m.group(1), "line": n})
            continue                      # an @-import line is not also a path claim
        for m in PATH_RE.finditer(line):
            out.append({"kind": "path", "claim": m.group(0).rstrip("/") or "/", "line": n})
        for m in WIKILINK_RE.finditer(line):
            out.append({"kind": "wikilink", "claim": m.group(1).strip(), "line": n})
        for rx in (SHA_BACKTICK_RE, SHA_KEYWORD_RE):
            for m in rx.finditer(line):
                tok = m.group(1)
                if HAS_LETTER_RE.search(tok):
                    out.append({"kind": "sha", "claim": tok, "line": n})
    # One claim may be found by two SHA patterns (backticked AND after the word `commit`).
    seen, uniq = set(), []
    for c in out:
        key = (c["kind"], c["claim"], c["line"])
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def basename_index(vault: Path) -> dict[str, int]:
    idx: dict[str, int] = {}
    try:
        for dirpath, dirnames, filenames in os.walk(vault):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.endswith(".md"):
                    idx[name[:-3]] = idx.get(name[:-3], 0) + 1
    except OSError:
        pass
    return idx


def git_ok(path: Path) -> bool:
    return _sh(["git", "-C", str(path), "rev-parse", "--git-dir"]) is not None


def candidate_repos(cwd: str, files) -> list[Path]:
    """Every repository a SHA in these files could reasonably name: the vault, the repo the session
    is working in, and the repo each boot file itself lives in.

    ★ Measured, not assumed. Checking only the vault reported five live commits as dead on the
    fleet's own boot chain — every one of them a commit in the CODE repo whose `CLAUDE.md` names it,
    which is exactly where a note about code belongs. A checker that calls those dead teaches its
    reader to ignore it, and an ignored checker is worse than none: it is the same silence with a
    maintenance cost.

    The closure is derived — the session's cwd and the files' own locations — rather than read from
    any roster, so it needs no configuration and cannot go stale."""
    out, seen = [], set()
    for start in [VAULT, Path(cwd)] + [Path(f).parent for f in files]:
        root = _sh(["git", "-C", str(start), "rev-parse", "--show-toplevel"])
        if root and root not in seen:
            seen.add(root)
            out.append(Path(root))
    return out


def _sh(args, timeout=15):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def check_claim(claim: dict, vault: Path, index: dict, repos: list) -> str | None:
    """None when the claim holds, a reason when it does not, "UNCHECKED: …" when it could not be
    decided. Three outcomes, never two: the middle one is what the whole hook is about."""
    kind, value = claim["kind"], claim["claim"]
    if kind in ("path", "import"):
        try:
            return None if Path(os.path.expanduser(value)).exists() else "target does not exist"
        except OSError as e:
            return f"UNCHECKED: {e.__class__.__name__}"
    if kind == "wikilink":
        target = value.split("/")[-1].strip()
        return None if index.get(target) else "no file of that name in the vault"
    if kind == "sha":
        if not repos:
            return ("UNCHECKED: no git repository among the vault, this session's directory or the "
                    "boot files' own locations, so no commit can be resolved")
        for repo in repos:
            if _sh(["git", "-C", str(repo), "cat-file", "-t", value]) == "commit":
                return None
        where = ", ".join(r.name for r in repos)
        return f"no such commit in {where}"
    return None


def load_extension(path: str):
    spec = importlib.util.spec_from_file_location(f"gedaechtnis_extra_{abs(hash(path))}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"not importable: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "run"):
        raise AttributeError("module has no run(vault) function")
    return mod


def run_extensions(vault: Path) -> tuple[list[dict], list[str]]:
    """(findings, unchecked). An extension that raises costs its own findings and nothing else."""
    raw = config._load().get("extra_checks")
    paths = [str(x) for x in raw] if isinstance(raw, list) else []
    findings, unchecked = [], []
    for path in paths:
        try:
            out = load_extension(os.path.expanduser(path)).run(vault)
            if not isinstance(out, list):
                raise TypeError(f"run(vault) returned {type(out).__name__}, expected list")
            for f in out:
                if not isinstance(f, dict):
                    raise TypeError("a finding must be a dict")
                findings.append({"kind": str(f.get("kind", "extra")), "claim": str(f.get("claim", "")),
                                 "file": str(f.get("file", path)), "line": f.get("line"),
                                 "detail": str(f.get("detail", "")), "source": path})
        except Exception as e:
            unchecked.append(f"{path} ({e.__class__.__name__}: {e})")
    return findings, unchecked


def compute(cwd: str) -> dict:
    files = boot_files(cwd)
    index = basename_index(VAULT)
    repos = candidate_repos(cwd, files)
    failures, unreadable, n_claims = [], [], 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            unreadable.append(f"{path} ({e.__class__.__name__})")
            continue
        for claim in extract_claims(text):
            n_claims += 1
            why = check_claim(claim, VAULT, index, repos)
            if why:
                failures.append({**claim, "file": str(path), "detail": why})
    extra, ext_unchecked = run_extensions(VAULT)
    unchecked = [f for f in failures if f["detail"].startswith("UNCHECKED")]
    real = [f for f in failures if not f["detail"].startswith("UNCHECKED")]
    return {"version": SCHEMA_VERSION, "computed": time.strftime("%Y-%m-%d"),
            "n_boot_files": len(files), "n_claims": n_claims,
            "repos": [str(r) for r in repos],
            "failures": real[:EVIDENCE_CAP], "n_failures": len(real),
            "unchecked": unchecked[:EVIDENCE_CAP], "n_unchecked": len(unchecked),
            "extra": extra[:EVIDENCE_CAP], "n_extra": len(extra),
            "extra_unchecked": ext_unchecked, "unreadable": unreadable}


def facts_lines(doc: dict) -> list[str]:
    """Only about something that actually failed. A line printed every session is a line every
    session learns to skip, and the one time it matters it reads like the other hundred."""
    out = []
    def name(f):
        try:
            where = Path(f["file"]).name
        except Exception:
            where = f.get("file", "?")
        line = f" line {f['line']}" if f.get("line") else ""
        return f"{f['kind']} `{f['claim']}` in {where}{line} — {f['detail']}"
    if doc.get("n_failures"):
        shown = "; ".join(name(f) for f in doc.get("failures") or [])
        more = doc["n_failures"] - len(doc.get("failures") or [])
        out.append(f"- Boot check: {doc['n_failures']} claim(s) in the files this session boots "
                   f"with no longer hold: {shown}" + (f", and {more} more" if more > 0 else "") + ".")
    if doc.get("n_extra"):
        shown = "; ".join(f"{f['kind']} {f['claim']} — {f['detail']}" for f in doc.get("extra") or [])
        out.append(f"- Boot check ({len(set(f['source'] for f in doc.get('extra') or []))} "
                   f"extension(s)): {doc['n_extra']} finding(s): {shown}.")
    if doc.get("n_unchecked"):
        out.append(f"- Boot check: {doc['n_unchecked']} claim(s) could NOT be checked — read them "
                   f"as UNCHECKED, never as sound: {doc['unchecked'][0]['detail']}.")
    for u in doc.get("extra_unchecked") or []:
        out.append(f"- Boot check: an extra_checks module did NOT run, so whatever it looks for is "
                   f"UNCHECKED this session: {u}.")
    for u in doc.get("unreadable") or []:
        out.append(f"- Boot check: a boot file could NOT be read, so its claims are UNCHECKED: {u}.")
    if out:
        out.append(f"- Boot check state: {state_path()} (computed at the last session end).")
    return out


def main() -> None:
    inp = read_input()
    cwd = inp.get("cwd") or os.getcwd()
    if not VAULT.is_dir():
        log("boot_check", f"no vault at {VAULT}; nothing checked")
        return
    STATE.mkdir(parents=True, exist_ok=True)
    doc = compute(cwd)
    new = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    old = None
    try:
        old = state_path().read_text(encoding="utf-8")
    except OSError:
        pass
    def sans_stamp(text):
        try:
            d = json.loads(text)
            d.pop("computed", None)
            return json.dumps(d, indent=1, sort_keys=True)
        except (ValueError, AttributeError):
            return text
    if old is None or sans_stamp(old) != sans_stamp(new):
        state_path().write_text(new, encoding="utf-8")
    log("boot_check", f"claims={doc['n_claims']} failures={doc['n_failures']} "
                      f"unchecked={doc['n_unchecked']} extra={doc['n_extra']}")


if __name__ == "__main__":
    guarded(main)
