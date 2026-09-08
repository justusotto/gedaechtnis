#!/usr/bin/env python3
"""chore.py — PostToolUse doors that DO the chore the rule was asking for.

    python3 chore.py artifact   # after an Artifact publish: index row in Pharos/artifacts-index.md, committed
    python3 chore.py write      # after Edit/Write in the vault: queue trailing newline · SHA tokens resolve?

A chore never denies (the act already happened). It repairs what is mechanically repairable and
reports the rest as `additionalContext` — factual statements, never orders.
"""
from __future__ import annotations
import json, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import read_input, context, log, expand, under, vault_rel, fleet_repos, VAULT, guarded

EV = "PostToolUse"
UUID = re.compile(r"https://claude\.ai/code/artifact/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
INDEX = VAULT / "Pharos" / "artifacts-index.md"
ANCHOR = "Prefix every id with"


def _git(args: list[str], repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)


def commit_path_limited(repo: Path, rel: str, msg: str) -> str | None:
    """Stage ONE path and commit it with the pathspec, as atlas@local. Retries on index.lock."""
    for attempt in range(4):
        a = _git(["add", "--", rel], repo)
        if a.returncode == 0:
            c = _git(["-c", "user.name=atlas", "-c", "user.email=atlas@local", "commit", "-q", "-m", msg, "--", rel], repo)
            if c.returncode == 0:
                h = _git(["log", "-1", "--format=%h", "--", rel], repo)
                return h.stdout.strip() or "?"
            if "index.lock" not in (c.stderr or ""):
                log("chore", f"commit failed: {c.stderr.strip()[:200]}")
                return None
        elif "index.lock" not in (a.stderr or ""):
            log("chore", f"add failed: {a.stderr.strip()[:200]}")
            return None
        time.sleep(0.2 * (attempt + 1))
    return None


def do_artifact(inp: dict) -> None:
    ti = inp.get("tool_input") or {}
    action = ti.get("action") or "publish"
    if action != "publish":
        return
    blob = json.dumps(inp.get("tool_response", ""), ensure_ascii=False)
    m = UUID.search(blob)
    if not m:
        return
    uid = m.group(1)
    title = ""
    fp = ti.get("file_path")
    if fp:
        try:
            head = expand(fp, inp.get("cwd")).read_text(encoding="utf-8", errors="replace")[:8192]
            t = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
            title = re.sub(r"\s+", " ", t.group(1)).strip() if t else ""
        except OSError:
            pass
    title = title or ti.get("title") or Path(fp or "").stem or "untitled"
    try:
        txt = INDEX.read_text(encoding="utf-8")
    except OSError:
        context(EV, f"Artifact {uid} published but ~/Atlas/Pharos/artifacts-index.md is not readable; it is NOT indexed.")
        return
    if uid in txt:
        return
    row = f"| {time.strftime('%Y-%m-%d')} | {title.replace('|', '/')} | `{uid}` |\n"
    if ANCHOR in txt:
        i = txt.index(ANCHOR)
        # insert before the blank line that precedes the anchor paragraph
        j = txt.rfind("\n\n", 0, i)
        txt = txt[:j + 1] + row + txt[j + 1:] if j != -1 else txt[:i] + row + "\n" + txt[i:]
    else:
        txt = txt.rstrip("\n") + "\n" + row
    INDEX.write_text(txt, encoding="utf-8")
    sha = commit_path_limited(VAULT, "Pharos/artifacts-index.md", f"artifacts-index: {title} ({uid[:8]})")
    log("chore", f"artifact-index\t{uid}\t{title}\tcommit={sha}")
    context(EV, f"Artifact {uid} (“{title}”) is now a row in ~/Atlas/Pharos/artifacts-index.md"
                + (f", committed {sha}." if sha else ", written but NOT committed (git refused; see ~/.claude/gedaechtnis/chore.log)."))


HEX = re.compile(r"`([0-9a-f]{7,12})`")


def sha_resolves(sha: str, repos: list[Path]) -> bool:
    for r in repos:
        if not (r / ".git").exists():
            continue
        try:
            p = _git(["cat-file", "-e", f"{sha}^{{commit}}"], r)
            if p.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            continue
    return False


def do_write(inp: dict) -> None:
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ""
    if not fp:
        return
    p = expand(fp, inp.get("cwd"))
    if not under(p, VAULT) or not p.is_file():
        return
    rel = vault_rel(p) or ""
    notes = []
    if rel.startswith("Pharos/queues/") and p.suffix == ".md":
        try:
            b = p.read_bytes()
            if b and not b.endswith(b"\n"):
                p.write_bytes(b + b"\n")
                notes.append(f"Added the missing trailing newline to {rel} (QLINT-1: a row appended after a missing newline glues onto the previous line and no parser sees it).")
        except OSError:
            pass
    new_text = ti.get("new_string") or ti.get("content") or ""
    if p.suffix == ".md" and new_text:
        shas = sorted(set(HEX.findall(new_text)))
        if shas:
            repos = fleet_repos()
            bad = [s for s in shas if not sha_resolves(s, repos)]
            if bad:
                notes.append(f"SHA tokens in the text just written to {rel} that resolve in NO fleet repo or the vault: "
                             f"{', '.join(bad)}. A SHA is read back from `git log -1 --format=%h`, never written from expectation "
                             "(Global/Errata 'A commit SHA written from expectation…').")
    if notes:
        log("chore", f"write\t{rel}\t{' | '.join(n[:80] for n in notes)}")
        context(EV, " ".join(notes))


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    inp = read_input()
    {"artifact": do_artifact, "write": do_write}.get(which, lambda _i: None)(inp)


if __name__ == "__main__":
    guarded(main)
