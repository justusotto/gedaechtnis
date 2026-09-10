#!/usr/bin/env python3
"""publish_check.py — refuse to publish anything that carries a person with it. stdlib only.

    python3 gedaechtnis/tools/publish_check.py            # scans the plugin it lives in
    python3 gedaechtnis/tools/publish_check.py --root DIR # scans DIR instead

Exit 0 with `publish check: clean`, or exit 1 listing every hit as `path:line: kind: text`.

What it looks for: a home-directory path (`/Users/<name>`, `/home/<name>`), an email address, the
original author's name, the directory their working repos live in, the private repo this plugin
grew inside, and anything shaped like an API key or a credential assignment.

**There is no allowlist, and the checker scans itself.** That is the whole design: an exemption is
the first thing a leak hides behind, and a checker that skips its own file is one edit away from
being the leak. The needles are therefore written as regexes whose own source text is not a hit —
`j[u]stus` matches the name without the file containing it. That is not evasion (the pattern is
inert data, and the check still fires on any real occurrence anywhere including here); it is what
makes "allowlist nothing" literally true.

Binary and oversized files are read as UTF-8 with undecodable bytes replaced, so a stray secret in
a blob is still found; nothing is skipped except `.git/` and `__pycache__/`.

**The tree is not the only thing a push sends.** When `--root` IS a git root (a mirror clone), this
check also reads its TAGS. A clone made from the private monorepo inherits that monorepo's tags,
and a single `git push --tags` then publishes the private commits those tags point at, with their
whole ancestry — that happened on 2026-09-10. A tag that is not one of this plugin's releases
(`v*`) is therefore a refusal: delete the inherited tag locally, refresh the clone with
`git pull --no-rebase --no-tags`, and publish a release BY NAME (`git push origin v0.1`). A `--root`
that is not a git root has no refs of its own and this half is skipped.
"""
from __future__ import annotations
import argparse, fnmatch, re, subprocess, sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}

# (kind, pattern). See the module docstring for why the literal needles are spelled with a
# one-character class: the checker is scanned by itself and must not be its own hit.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("home path",   re.compile(r"/Users/[A-Za-z0-9._-]+")),
    ("home path",   re.compile(r"/home/[A-Za-z0-9._-]+")),
    ("email",       re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("owner name",  re.compile(r"j[u]stus", re.I)),
    ("private dir", re.compile(r"P[y]charmProjects", re.I)),
    ("private repo", re.compile(r"atlas[-]system", re.I)),
    ("api key",     re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("api key",     re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("api key",     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("api key",     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api key",     re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("api key",     re.compile(r"\bAIza[0-9A-Za-z_-]{20,}")),
    # the key name may itself be quoted (a JSON/TOML config leaks the same way a Python literal does)
    ("credential",  re.compile(r"""(?i)["']?\b(?:api[_-]?key|secret|token|password|passwd)\b["']?\s*[=:]\s*["'][^"'\s]{16,}["']""")),
]


def files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def scan(root: Path) -> list[tuple[Path, int, str, str]]:
    hits = []
    for p in files(root):
        try:
            text = p.read_bytes().decode("utf-8", "replace")
        except OSError as e:
            hits.append((p, 0, "unreadable", str(e)))
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for kind, rx in PATTERNS:
                m = rx.search(line)
                if m:
                    hits.append((p, n, kind, m.group(0)[:120]))
    return hits


RELEASE_TAG = "v*"          # the only tag shape this plugin publishes; see the module docstring


def foreign_tags(root: Path) -> tuple[list[str], str | None]:
    """(the tags on `root` that are not `v*`, error) — ([], None) when `root` is not a git root.

    One `git tag -l`, no network. The `.git` test is what keeps this half from firing on a plugin
    directory that merely SITS INSIDE some larger repo: that repo's tags are not this tree's to
    publish, and no push from here would carry them.
    """
    if not (root / ".git").exists():
        return [], None
    try:
        p = subprocess.run(["git", "-C", str(root), "tag", "-l"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:          # git missing, or wedged
        return [], f"could not read the tags of {root}: {e}"
    if p.returncode != 0:
        return [], f"could not read the tags of {root}: {p.stderr.strip()[:200]}"
    tags = [t.strip() for t in p.stdout.splitlines() if t.strip()]
    return [t for t in tags if not fnmatch.fnmatch(t, RELEASE_TAG)], None


def main() -> int:
    ap = argparse.ArgumentParser(description="Refuse to publish a tree that carries a person with it.")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]),
                    help="directory to scan (default: the plugin this script lives in)")
    args = ap.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"publish check: {root} is not a directory", file=sys.stderr)
        return 1
    bad, err = foreign_tags(root)
    if err:
        print(f"publish check: {err} — an unreadable ref set is not a clean one", file=sys.stderr)
    if bad:
        print(f"publish check: {root} carries {len(bad)} tag(s) that are not this plugin's releases "
              f"({', '.join(bad)}) — a `git push --tags` from here would publish the private commits they "
              f"point at; delete them locally, refresh with `git pull --no-rebase --no-tags`, and push a "
              f"release by name (`git push origin <tag>`).")
    hits = scan(root)
    if not hits and not bad and not err:
        print(f"publish check: clean — {root}")
        return 0
    if hits:
        print(f"publish check: {len(hits)} hit(s) under {root} — this tree is not publishable:")
        for p, n, kind, text in hits:
            print(f"  {p.relative_to(root)}:{n}: {kind}: {text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
