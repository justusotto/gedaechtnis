#!/usr/bin/env python3
"""publish_check.py — refuse to publish anything that carries a person with it. stdlib only.

    python3 gedaechtnis/tools/publish_check.py            # scans the plugin it lives in
    python3 gedaechtnis/tools/publish_check.py --root DIR # scans DIR instead — ANY repo, not just
                                                           # this plugin (SWEEPROOT-1, 2026-09-18)

Exit 0 with `publish check: clean`, or exit 1 listing every hit as `path:line: kind: excerpt`. If
the walk finds no files at all — an empty or nonexistent tree — that is never reported as clean:
it prints `publish check: UNCHECKED` and exits 1, because a walk of zero files proves nothing.

What it looks for, as separate PATTERN CLASSES (never one catch-all regex, so a gap in one class
never hides behind another passing): a home-directory path (`/Users/<name>`, `/home/<name>`), an
email address, the original author's name, the directory their working repos live in, the private
repo this plugin grew inside, a phone-number shape, an IBAN shape, anything shaped like an API key
or a credential assignment, and any `.env`-style file (its first non-comment line is the finding —
an env file is a secret by what it IS, not only by what regex its contents happen to match). When
`--root` is a git working tree, it also flags any file `git` would have ignored by `.gitignore` but
that is tracked anyway (force-added past the ignore, the classic way a `.env` or a credentials dump
survives a "we .gitignore secrets" policy).

Every reported excerpt is REDACTED — the matched text itself is never printed, only enough of its
boundary to identify the class. A leak in a report about leaks is still a leak.

**There is no allowlist, and the checker scans itself.** That is the whole design: an exemption is
the first thing a leak hides behind, and a checker that skips its own file is one edit away from
being the leak. The needles are therefore written as regexes whose own source text is not a hit —
`j[u]stus` matches the name without the file containing it. That is not evasion (the pattern is
inert data, and the check still fires on any real occurrence anywhere including here); it is what
makes "allowlist nothing" literally true. `--root` runs the exact same PATTERNS and the exact same
functions over any directory — a foreign repo gets no separate, weaker code path.

Binary and oversized files are read as UTF-8 with undecodable bytes replaced, so a stray secret in
a blob is still found; nothing is skipped except `.git/` and `__pycache__/`.

**The tree is not the only thing a push sends.** When `--root` IS a git root (a mirror clone, or any
other repo), this check also reads its TAGS. A clone made from a private monorepo inherits that
monorepo's tags, and a single `git push --tags` then publishes the private commits those tags point
at, with their whole ancestry — that happened on 2026-09-10. A tag that is not one of this plugin's
releases (`v*`) is therefore a refusal: delete the inherited tag locally, refresh the clone with
`git pull --no-rebase --no-tags`, and publish a release BY NAME (`git push origin v0.1`). A `--root`
that is not a git root has no refs of its own and this half is skipped. **This `v*` shape is this
plugin's own release convention — a foreign repo (e.g. a private push of some other project) will
usually carry non-`v*` tags of its own that are not inherited leaks; that half of the report is
informational there and the operator judges it, same as any other finding.**
"""
from __future__ import annotations
import argparse, fnmatch, os, re, subprocess, sys
from pathlib import Path

# ★ `node_modules` was in here and is NOT. A skip list is a list of places this tool PROMISES
# nothing about, and the docstring promised the opposite ("nothing is skipped except `.git/` and
# `__pycache__/`"). A vendored tree is exactly where a stray absolute path hides, and a checker
# that says `clean` over a directory it declined to open has answered a different question than
# the one it was asked. `.git` stays because its refs are scanned separately, by `history()`.
SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache"}

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
    # personal-data shapes (SWEEPROOT-1): a phone number needs a leading + to be distinguishable
    # from an ordinary digit run (version numbers, line counts, ...); an IBAN is a 2-letter country
    # code + 2 check digits + up to 30 alnum, which is specific enough not to fire on normal code.
    ("phone",       re.compile(r"\+\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{2,4}\b")),
    ("iban",        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
]

# .env-shaped files are a secret by what they ARE, not only by what regex their contents happen to
# match — an untouched `KEY=value` line with a short value would slip every pattern above. The
# example/sample/template siblings are the conventional "safe to commit" variants and are excluded.
ENV_SAFE_SUFFIXES = (".example", ".sample", ".template")


def is_env_file(p: Path) -> bool:
    name = p.name
    if name == ".env":
        return True
    return name.startswith(".env.") and not name.endswith(ENV_SAFE_SUFFIXES)


def redact(text: str) -> str:
    """Enough of the match to identify it, never enough to reuse it — no full secret ever prints."""
    text = text.strip()
    if len(text) <= 6:
        return "*" * len(text) if text else "(empty)"
    return f"{text[:3]}…{text[-3:]}"


def _decodings(raw: bytes):
    """Each encoding a needle could hide in, yielded SEPARATELY with its own line numbering.

    `decode("utf-8", "replace")` turns NUL-interleaved UTF-16 into replacement characters, so a
    private path written by any editor that saves UTF-16 matched nothing at all.

    Two things the first version of this fix got wrong, both worth the extra lines:

    * It CONCATENATED the decodings, so every reported line number for a UTF-16 hit was offset by
      the length of the utf-8 pass — a hit on line 4 was reported as line 9. A finding a person
      cannot locate is most of the way to a finding they ignore.
    * It decoded every file three times. Measured on one 50 MB file: 7.14 s and 397 MB RSS. Now
      that `node_modules` is correctly walked, that is the tool's dominant cost on a vendored tree
      — and a check slow enough to be skipped protects nothing. UTF-16 is only attempted when the
      bytes actually contain a NUL, which no valid UTF-8 text file does."""
    yield raw.decode("utf-8", "replace")
    if b"\x00" not in raw:
        return
    for enc in ("utf-16-le", "utf-16-be"):
        try:
            yield raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            pass


def files(root: Path):
    """Every file this tree would publish — INCLUDING symlinks, whose published content is the
    link text itself.

    A symlink was skipped as "not a file". What git stores for a symlink is its TARGET STRING, so
    `ln -s /Users/<somebody>/<vault>/Global/Kernel.md notes.md` publishes that absolute path verbatim
    while this walker looked straight past it. The skip read as a safety measure (do not follow
    links out of the tree) and acted as a blind spot: the safe thing is not to READ THROUGH the
    link, which is different from not looking at it."""
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_symlink() or p.is_file():
            yield p


def scan(root: Path) -> tuple[list[tuple[Path, int, str, str]], int]:
    """(hits, walked) — `walked` is the file count actually read, so the caller can tell an empty
    tree (nothing to be clean ABOUT) apart from a tree that was scanned and found clean."""
    hits = []
    walked = 0
    for p in files(root):
        walked += 1
        try:
            if p.is_symlink():
                # The published bytes ARE the link text; never read through it.
                texts = [os.readlink(p)]
            else:
                texts = list(_decodings(p.read_bytes()))
        except OSError as e:
            hits.append((p, 0, "unreadable", str(e)))
            continue
        if is_env_file(p):
            for n, line in enumerate(texts[0].splitlines(), 1):
                s = line.strip()
                if s and not s.startswith("#"):
                    hits.append((p, n, "env file", redact(s)))
                    break
            else:
                hits.append((p, 1, "env file", "(empty)"))
        # Each decoding is scanned with its OWN line numbering, so a reported line is a line a
        # person can open the file and find. `seen` keeps one file's hit from being reported twice
        # when two decodings both contain it.
        seen = set()
        for text in texts:
            for n, line in enumerate(text.splitlines(), 1):
                for kind, rx in PATTERNS:
                    m = rx.search(line)
                    if m and (n, kind) not in seen:
                        seen.add((n, kind))
                        hits.append((p, n, kind, redact(m.group(0)[:120])))
    return hits, walked


_SEP = "@@gedaechtnis-field@@"     # see `history()`

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


def history(root: Path) -> tuple[list[tuple[str, str, str]], str | None]:
    """(hits in COMMIT MESSAGES and branch names, error) — ([], None) when `root` is not a git root.

    ★ The scan walked the WORKING TREE and called the result publishable. A push publishes the
    HISTORY. A path scrubbed from HEAD still sits in the commit that removed it, and — the case
    that actually exists here — an auto-generated merge subject carries the owner's absolute path
    in twenty-one commits, as measured on the mirror: `Merge branch '<b>' of /Users/<owner>/<the owner's projects dir>/...`.
    Nobody typed those; git wrote them, which is exactly why no amount of care in the tree caught
    them.

    Commit messages, ref NAMES, and the bodies of annotated tags and notes. Historical BLOBS are
    deliberately NOT scanned here: that is a
    `--all --objects` walk of the whole object database, slow enough that it would not be run, and
    a tool that is skipped is worth less than one that answers a narrower question every time.
    That limit is printed rather than implied — see `main`."""
    if not (root / ".git").exists():
        return [], None
    out = []
    try:
        # `%N` is the commit's NOTES. A note lives in `refs/notes/*` as a blob hanging off a tree,
        # so `for-each-ref`'s `%(contents)` on that ref gives the notes COMMIT's message, not the
        # note text — the note is reachable here and only here.
        msgs = subprocess.run(["git", "-C", str(root), "log", "--all", "--format=%H%x00%B%N%x00"],
                              capture_output=True, text=True, timeout=120)
        refs = subprocess.run(["git", "-C", str(root), "for-each-ref",
                               "--format=%(refname)"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"could not read the history of {root}: {e}"
    if msgs.returncode != 0:
        return [], f"could not read the history of {root}: {msgs.stderr.strip()[:200]}"
    for record in msgs.stdout.split("\x00\n"):
        if "\x00" not in record:
            continue
        sha, body = record.split("\x00", 1)
        for kind, rx in PATTERNS:
            if kind == "email":
                continue          # a commit author is an email BY DESIGN; the noreply form is correct
            m = rx.search(body)
            if m:
                out.append((sha.strip()[:8], kind, redact(m.group(0))))
                break
    # ★ A TAG MESSAGE IS NOT A COMMIT AND NOT A BLOB. `git log` never shows an annotated tag's own
    # message, and `refs/notes` blobs are not reachable from the commits either — so a leak written
    # into `git tag -a -m "…"` or `git notes add -m "…"` sailed past a `clean` verdict whose printed
    # limit said only that historical BLOBS are unscanned. That is this file's own rule — "the door
    # allows this" and "the door cannot see this" read alike from outside — unapplied to the check
    # it had just gained. `%(contents)` gives the object's own body for a tag and the note text for
    # a note.
    try:
        bodies = subprocess.run(["git", "-C", str(root), "for-each-ref",
                                 # NOT `%x00`: that is a `git log` escape and `for-each-ref`
                                 # prints it LITERALLY, which silently produced one unsplittable
                                 # record and a `clean` verdict. A literal sentinel is passed
                                 # through unchanged by both.
                                 f"--format=%(refname){_SEP}%(contents){_SEP}",
                                 "refs/tags"],
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return out, f"could not read the tag bodies of {root}: {e}"
    if bodies.returncode == 0:
        for record in bodies.stdout.split(_SEP + "\n"):
            if _SEP not in record:
                continue
            name, body = record.split(_SEP, 1)
            for kind, rx in PATTERNS:
                if kind == "email":
                    continue
                m = rx.search(body)
                if m:
                    out.append((name.strip(), kind, redact(m.group(0))))
                    break
    if refs.returncode == 0:
        for name in refs.stdout.splitlines():
            for kind, rx in PATTERNS:
                if kind == "email":
                    continue
                m = rx.search(name)
                if m:
                    out.append((name.strip(), kind, redact(m.group(0))))
                    break
    return out, None


def tracked_but_ignored(root: Path) -> tuple[list[str], str | None]:
    """(tracked paths that `.gitignore` would exclude, error) — ([], None) when `root` is not a
    git root. This is the "force-added past the ignore" leak: a `.env` or credentials file gets
    `.gitignore`d for safety, then a `git add -f` (or an add that predates the ignore rule) puts it
    under version control anyway, where the ignore rule can no longer protect it. `git ls-files -c
    -i --exclude-standard` is exactly this intersection — cached (tracked) AND ignored.
    """
    if not (root / ".git").exists():
        return [], None
    try:
        p = subprocess.run(["git", "-C", str(root), "ls-files", "-c", "-i", "--exclude-standard"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"could not read the tracked/ignored files of {root}: {e}"
    if p.returncode != 0:
        return [], f"could not read the tracked/ignored files of {root}: {p.stderr.strip()[:200]}"
    return [l.strip() for l in p.stdout.splitlines() if l.strip()], None


# ------------------------------------------------ the directory-submission checklist (PLUGDIR-2) ----
# The plugin directory's pre-submission checklist, as lines a machine can decide. Run only when the
# root IS a plugin (it carries `.claude-plugin/plugin.json`) — a foreign repo scanned with `--root`
# is not being submitted anywhere, and a file-count limit means nothing for it. Each line prints
# PASS or FAIL with the evidence, so a reader sees WHICH lines were asked, not a bare verdict.

MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 512
HOOK_CMD_RX = re.compile(r'^python3 -B "\$\{CLAUDE_PLUGIN_ROOT\}/[^"$]+\.py"(?: [A-Za-z0-9_-]+)*$')


def published_set(root: Path) -> tuple[list[str], str]:
    """(relative paths, how) — what a publish of `root` would carry. Inside a git work tree that is
    the tracked files plus the untracked ones `.gitignore` does not exclude (what `git add` would
    take); in a plain directory, every file, `__pycache__` INCLUDED — nothing is skipped here,
    because the question is what would ship, not what is worth reading."""
    try:
        p = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                            "--exclude-standard"], capture_output=True, text=True, timeout=30)
        if p.returncode == 0:
            names = sorted({n for n in p.stdout.split("\0") if n})
            return [n for n in names if (root / n).exists() or (root / n).is_symlink()], "git"
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for q in sorted(root.rglob("*")):
        if ".git" in q.relative_to(root).parts[:1]:
            continue
        if q.is_symlink() or q.is_file():
            out.append(str(q.relative_to(root)))
    return out, "walk"


def checklist(root: Path) -> list[tuple[str, bool, str]] | None:
    """[(line, ok, evidence)] for a plugin root, or None when `root` is not a plugin."""
    import json
    manifest = root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return None
    names, how = published_set(root)
    out: list[tuple[str, bool, str]] = []

    def line(text, bad, what):
        out.append((text, not bad, (f"{len(bad)} {what}: " + ", ".join(bad[:5])) if bad else f"none ({len(names)} files, {how})"))

    line("no __pycache__/ or .pyc", [n for n in names if "__pycache__/" in n or n.endswith(".pyc")], "compiled file(s)")
    line("no .DS_Store", [n for n in names if n.rsplit("/", 1)[-1] == ".DS_Store"], "Finder file(s)")
    line("no symlinks", [n for n in names if (root / n).is_symlink()], "symlink(s)")
    big = []
    for n in names:
        try:
            if (root / n).lstat().st_size > MAX_FILE_BYTES:
                big.append(f"{n} ({(root / n).lstat().st_size // 1024} KiB)")
        except OSError:
            pass
    line(f"no file over {MAX_FILE_BYTES // 1024} KiB", big, "oversized file(s)")
    out.append((f"at most {MAX_FILES} files", len(names) <= MAX_FILES, f"{len(names)} files ({how})"))
    try:
        m = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        out.append(("plugin.json parses", False, str(e)))
        return out
    out.append(("plugin.json has no `hooks` key (hooks/hooks.json is found by convention)",
                "hooks" not in m, "present" if "hooks" in m else "absent"))
    missing = [k for k in ("name", "displayName", "version", "description", "license") if not m.get(k)]
    out.append(("plugin.json names name, displayName, version, description, license", not missing,
                f"missing: {', '.join(missing)}" if missing else "all present"))
    lic = root / "LICENSE"
    head = lic.read_text(encoding="utf-8").split("\n", 1)[0] if lic.is_file() else ""
    out.append(("`license` matches LICENSE", bool(m.get("license")) and str(m.get("license")) in head,
                f"license={m.get('license')!r}, LICENSE starts {head[:30]!r}"))
    hooks = root / "hooks" / "hooks.json"
    cmds = []
    try:
        for ents in json.loads(hooks.read_text(encoding="utf-8")).get("hooks", {}).values():
            for e in ents:
                cmds += [h.get("command", "") for h in e.get("hooks", [])]
    except (OSError, ValueError) as e:
        out.append(("hooks/hooks.json parses", False, str(e)))
        return out
    odd = [c for c in cmds if not HOOK_CMD_RX.match(c)]
    out.append(('every hook command is `python3 -B "${CLAUDE_PLUGIN_ROOT}/<path>.py" [args]`',
                bool(cmds) and not odd, f"{len(odd)} of {len(cmds)} differ: {odd[:2]}" if odd else f"{len(cmds)} commands"))
    inline = [c for c in cmds if re.search(r"python3?\s+(?:-\w+\s+)*-c\b", c)]
    out.append(("no inline `python3 -c` in hooks.json", not inline, f"{len(inline)} inline" if inline else "none"))
    gi = root / ".gitignore"
    gtext = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    want = [w for w in ("__pycache__", "*.pyc", ".DS_Store") if w not in gtext]
    out.append((".gitignore excludes __pycache__, *.pyc, .DS_Store", not want,
                f"missing: {', '.join(want)}" if want else "all three"))
    return out


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
    gi, gi_err = tracked_but_ignored(root)
    if gi_err:
        print(f"publish check: {gi_err} — an unreadable ignore set is not a clean one", file=sys.stderr)
    hist, hist_err = history(root)
    if hist_err:
        print(f"publish check: {hist_err} — an unreadable history is not a clean one", file=sys.stderr)
    if hist:
        print(f"publish check: {len(hist)} commit message(s) or ref name(s) under {root} carry "
              f"private text — a push publishes the HISTORY, not the tree:")
        for where, kind, text in hist:
            print(f"  {where}: {kind}: {text}")
    cl = checklist(root)
    cl_failed = False
    if cl is None:
        print("checklist: skipped — no .claude-plugin/plugin.json at this root, so it is not a plugin")
    else:
        for text, ok, why in cl:
            print(f"checklist: {'PASS' if ok else 'FAIL'}  {text} — {why}")
            cl_failed = cl_failed or not ok
    hits, walked = scan(root)
    for name in gi:
        hits.append((root / name, 0, "gitignore-tracked", redact(name)))
    # A walk that touched nothing proves nothing: an empty or nonexistent tree is UNCHECKED, never
    # a false "clean" — the whole point of this tool is that "no finding" must mean "we looked".
    if walked == 0:
        print(f"publish check: UNCHECKED — {root} — the walk found 0 files, which is not a clean "
              f"tree, it is an unproven one", file=sys.stderr)
        return 1
    # ★ WHICH HALVES RAN IS PART OF THE ANSWER. `--root <a subdirectory>` is not a git root, so the
    # tags, ignore and history halves all returned ([], None) — silently — and the tool printed an
    # unqualified `clean`. That is this file's own rule ("a walk of zero files proves nothing")
    # broken for its own skipped checks: three of the four questions were never asked, and the
    # report said nothing was wrong with any of them.
    skipped = [] if (root / ".git").exists() else ["foreign tags", "tracked-but-ignored",
                                                  "commit messages and ref names"]
    if not hits and not bad and not err and not gi_err and not hist and not hist_err and not cl_failed:
        note = ""
        if skipped:
            note = (f"; NOT CHECKED (not a git root): {', '.join(skipped)} — run this against the "
                    f"repository root to check them")
        print(f"publish check: clean — {root} ({walked} file(s) walked{note}). "
              f"Historical blobs and the contents of past versions of files are never scanned "
              f"by this tool.")
        return 0
    if cl_failed:
        print(f"publish check: a checklist line FAILED under {root} (above) — this tree is not publishable")
    if hits:
        print(f"publish check: {len(hits)} hit(s) under {root} — this tree is not publishable:")
        for p, n, kind, text in hits:
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = p
            print(f"  {rel}:{n}: {kind}: {text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
