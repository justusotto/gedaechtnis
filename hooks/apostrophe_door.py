#!/usr/bin/env python3
"""apostrophe_door.py — one apostrophe codepoint per declared layer (APOSTROPHEGATE-1).

Some languages written in Cyrillic put an apostrophe inside a word (м'ясо, п'ять), and two
codepoints are in common use for it: U+02BC MODIFIER LETTER APOSTROPHE and U+0027 APOSTROPHE. A
project that keeps both usually keeps them apart BY LAYER — its source corpus in one, its
persisted vocabulary in the other — with one converter at the boundary between them. A mixed
layer breaks joins in silence: the two spellings of one word are two keys, and nothing errors.
This door checks that each declared layer holds one of them only.

THE DECLARATION IS THE OWNER'S, NEVER GUESSED. A repo opts in with one file at its root,
`.gedaechtnis-apostrophe`, written by whoever owns the repo:

    # which regime each part of this repo is in; first matching `layer:` wins
    boundary: tools/xlsx_ingest.py          # exempt: the converter reads one regime, writes the other
    layer: U+02BC src/*.py data/*.txt
    layer: U+0027 vocab/*.json

`boundary:` is checked BEFORE any `layer:` (exempt wins outright, so a boundary file may also
match a layer glob). Globs are repo-relative, matched with `fnmatch` on the posix path, where `*`
also crosses `/`. A regime other than U+02BC / U+0027, or an absolute or `..` glob, rejects the
WHOLE file: the door then judges nothing in that repo and says so in its log — a half-read
declaration would judge files against a layer nobody declared. No declaration file = the door
is silent there; a repo without one has made no claim to hold.

WHAT COUNTS AS A UKRAINIAN APOSTROPHE. An apostrophe-like codepoint (U+0027 ' · U+02BC ʼ ·
U+2019 ’ · U+2018 ‘ · U+0060 ` · U+02B9 ʹ · U+2032 ′) with a Cyrillic letter on both sides —
immediately, or with only combining marks (a stress accent) between. A quote that touches one
Cyrillic letter only — a string delimiter, `'м'` — is not judged; nor is any apostrophe between
Latin letters. Its codepoint must equal the layer's. Every Cyrillic word in a layer is judged
the same way: a name borrowed from another language (`Д'Артаньян`) is named like any other.

WHAT IS JUDGED. On an Edit / MultiEdit / Write (PreToolUse, from `gate.py`, before the write
lands; a NotebookEdit is not read, and an authority-ledger file is judged by its own door first) only the text the call ADDS — `new_string`, each edit's `new_string`, a Write's
`content` — so a violation already in the file does not fire on every unrelated edit. An Edit's
new text is judged WITH the file's letters on either side of it (`_post_edit`), so an edit that
begins or ends at the apostrophe itself is still in a word. The walk
CLI judges whole files: `python3 apostrophe_door.py walk <repo>` lists every mismatch in the
declared file set as `path:line:col found U+XXXX, layer is U+YYYY` and exits 1 (0 = clean,
2 = no usable declaration); a layer file it cannot read as UTF-8 is named `unreadable` and
also exits 1 — never a silent skip. Bash writes (a heredoc, `sed -i`) carry no text for a door to read
and are NOT seen; the walk CLI is what a lane's pre-commit runs to close that.

WARN, THEN DENY — the flip is a DATE in the config. `limits.apostrophe_deny_from: "YYYY-MM-DD"`:
from that day a mismatching write is refused; before it, or with the key empty or malformed, the
write runs and the model is told what the door saw. `limits.apostrophe_door: false` turns it
off. Outside the vault and every marked repo the door only advises (`common.in_scope`). Every
mismatch it names and every rejected declaration is one line in `<state>/apostrophe.log`; a clean
write logs nothing. A WARN on a file INSIDE the vault is held until the vault's own checks have run
and printed with the kernel-entry note, so a refusal from those checks is never hidden by it.
"""
from __future__ import annotations

import datetime
import fnmatch
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common  # noqa: E402
import limits  # noqa: E402

DECL_NAME = ".gedaechtnis-apostrophe"
REGIMES = {"U+02BC": "ʼ", "U+0027": "'"}
APOS = "'ʼ’‘`ʹ′"
# Cyrillic, Cyrillic Supplement, Extended-A/B/C. A combining mark (a stress accent, U+0301, is
# normal in learner material) may sit on either side of the apostrophe: a stressed word is still judged.
_CYR = "Ѐ-ԯᲀ-᲏ⷠ-ⷿꙀ-ꚟ"
_MARK = "̀-ͯ"
IN_WORD = re.compile(rf"[{_CYR}][{_MARK}]*([{APOS}])[{_MARK}]*(?=[{_CYR}])")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def cp(ch: str) -> str:
    return f"U+{ord(ch):04X}"


# ---------------------------------------------------------------- the declaration ----
class Declaration:
    def __init__(self, root: Path, layers: list, boundary: list):
        self.root, self.layers, self.boundary = root, layers, boundary

    def regime_for(self, rel: str) -> str | None:
        """The codepoint `rel` must use, or None (exempt / in no layer)."""
        if any(fnmatch.fnmatchcase(rel, g) for g in self.boundary):
            return None
        for regime, globs in self.layers:
            if any(fnmatch.fnmatchcase(rel, g) for g in globs):
                return regime
        return None


def _bad_glob(g: str) -> bool:
    return g.startswith("/") or ".." in Path(g).parts


def parse_declaration(path: Path) -> tuple[Declaration | None, str]:
    """-> (declaration, "") or (None, why it was rejected). All-or-nothing, like parse_marker."""
    layers, boundary = [], []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"unreadable: {e.strerror}"
    for n, line in enumerate(text.splitlines(), 1):
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        key, _, rest = s.partition(":")
        words = rest.split()
        if key == "boundary":
            if not words or any(_bad_glob(g) for g in words):
                return None, f"line {n}: `boundary:` needs relative globs"
            boundary += words
        elif key == "layer":
            if len(words) < 2 or words[0] not in REGIMES:
                return None, f"line {n}: `layer:` needs a regime ({' or '.join(REGIMES)}) and globs"
            if any(_bad_glob(g) for g in words[1:]):
                return None, f"line {n}: `layer:` globs must be relative, without `..`"
            layers.append((REGIMES[words[0]], words[1:]))
        else:
            return None, f"line {n}: unknown key {key!r} (only `layer:` and `boundary:`)"
    if not layers:
        return None, "no `layer:` line"
    return Declaration(path.parent, layers, boundary), ""


def declaration_for(p: Path) -> tuple[Declaration | None, str, str]:
    """-> (declaration, repo-relative path, rejection reason) for the repo holding `p`."""
    root = common.git_root(p.parent if not p.is_dir() else p)
    if root is None:
        return None, "", ""
    decl = root / DECL_NAME
    if not decl.is_file():
        return None, "", ""
    d, why = parse_declaration(decl)
    try:
        rel = p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = ""
    return d, rel, why


# ---------------------------------------------------------------- the judgment ----
def mismatches(text: str, regime: str) -> list[tuple[int, int, str]]:
    """(line, col, found char) for every in-word apostrophe whose codepoint is not `regime`."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in IN_WORD.finditer(line):
            if m.group(1) != regime:
                out.append((n, m.start(1) + 1, m.group(1)))
    return out


def added_text(tool: str, ti: dict) -> str:
    if tool == "MultiEdit":
        return "\n".join(e.get("new_string") or "" for e in (ti.get("edits") or []))
    return ti.get("new_string") or ti.get("content") or ""


# How much of the file on either side of an edit is judged with it: enough for a letter plus the
# combining marks a stressed word can carry between it and the apostrophe.
EDGE = 16


def _bad_at(text: str, regime: str) -> list[tuple[int, str]]:
    """(offset, found char) for every in-word apostrophe in `text` whose codepoint is not `regime`."""
    return [(m.start(1), m.group(1)) for m in IN_WORD.finditer(text) if m.group(1) != regime]


def _post_edit(p: Path, tool: str, ti: dict, regime: str) -> list[tuple[str, int, str]] | None:
    """REVIEWFINDS-1 X2.1 — judge an Edit / MultiEdit on the POST-EDIT span, not `new_string` alone.

    An apostrophe at the edge of `new_string` has the file's letter, not the new text's, on that
    side; judged alone it is never "in a word", and swapping `ʼ` for `'` passed unseen. Each
    replacement is judged as `left + new + right` (EDGE characters of the file either side). An
    offender inside the new text counts; one in the context counts only if the same place was not
    already an offender before the edit — a violation the edit did not make is not blamed on it.
    -> [(the post-edit span, offset in it, found char)], or None when the file cannot be read (a
    new file: `new_string` is all there is, and the caller judges it alone)."""
    try:
        cur = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    edits = (ti.get("edits") or []) if tool == "MultiEdit" else [ti]
    out = []
    for e in edits:
        old, new = e.get("old_string") or "", e.get("new_string") or ""
        if not old or old not in cur:
            out += [(new, o, ch) for o, ch in _bad_at(new, regime)]
            continue
        idx, at = [], cur.find(old)
        while at >= 0:
            idx.append(at)
            if not e.get("replace_all"):
                break
            at = cur.find(old, at + len(old))
        for i in idx:
            left, right = cur[max(0, i - EDGE):i], cur[i + len(old):i + len(old) + EDGE]
            after = left + new + right
            before = {("L", o) if o < len(left) else ("R", o - len(left) - len(old))
                      for o, _ in _bad_at(left + old + right, regime)
                      if o < len(left) or o >= len(left) + len(old)}
            for o, ch in _bad_at(after, regime):
                if o < len(left):
                    key = ("L", o)
                elif o >= len(left) + len(new):
                    key = ("R", o - len(left) - len(new))
                else:
                    key = None
                if key is None or key not in before:
                    out.append((after, o, ch))
        cur = cur.replace(old, new) if e.get("replace_all") else cur.replace(old, new, 1)
    return out


def _word_at(line: str, col: int) -> str:
    i = j = col - 1
    while i > 0 and not line[i - 1].isspace():
        i -= 1
    while j < len(line) and not line[j].isspace():
        j += 1
    return line[i:j][:40]


def check(p: Path, tool: str, ti: dict) -> str | None:
    """The refusal/notice text for a write, or None. Never raises on a missing declaration."""
    if not enabled():
        return None
    d, rel, why = declaration_for(p)
    if why:
        common.log("apostrophe", f"inert\t{p}\tdeclaration rejected: {why}")
        return None
    if d is None or not rel:
        return None
    regime = d.regime_for(rel)
    if regime is None:
        return None
    edge = _post_edit(p, tool, ti, regime) if tool in ("Edit", "MultiEdit") else None
    if edge is None:
        text = added_text(tool, ti)
        lines = text.splitlines()
        bad = mismatches(text, regime)
        shown = [f"  line {n} of the added text, col {c}: {cp(ch)} in `{_word_at(lines[n - 1], c)}`"
                 for n, c, ch in bad[:8]]
    else:
        bad = edge
        shown = []
        for span, o, ch in bad[:8]:
            ls = span.rfind("\n", 0, o) + 1
            le = span.find("\n", o)
            line = span[ls:le if le >= 0 else len(span)]
            shown.append(f"  in the edited text: {cp(ch)} in `{_word_at(line, o - ls + 1)}`")
    if not bad:
        return None
    more = f"\n  … and {len(bad) - 8} more" if len(bad) > 8 else ""
    return (f"APOSTROPHE REGIME: `{rel}` is in a {cp(regime)} layer (declared in "
            f"{DECL_NAME} at {d.root}), and this write adds {len(bad)} in-word apostrophe(s) in "
            f"another codepoint:\n" + "\n".join(shown) + more +
            f"\nWrite {cp(regime)} ({regime!r}) there. Converting between regimes happens only at "
            f"the declared boundary file; never mix them inside one layer.")


# ---------------------------------------------------------------- config ----
def enabled() -> bool:
    return bool(limits.get("apostrophe_door", True))


def deny_from() -> str:
    v = limits.get("apostrophe_deny_from", "")
    return v.strip() if isinstance(v, str) else ""


def mode() -> str:
    """`deny` from the configured date on; `warn` before it, without it, or with a malformed one."""
    d = deny_from()
    if not _ISO.match(d):
        return "warn"
    try:
        datetime.date.fromisoformat(d)
    except ValueError:
        return "warn"
    return "deny" if common.today() >= d else "warn"


# ---------------------------------------------------------------- the walker ----
def walk(root: Path) -> tuple[int, list[str]]:
    """Every mismatch in the declared file set of `root`. -> (exit code, lines)."""
    decl = root / DECL_NAME
    if not decl.is_file():
        return 2, [f"no {DECL_NAME} at {root}: nothing is declared, nothing is judged"]
    d, why = parse_declaration(decl)
    if d is None:
        return 2, [f"{decl} rejected — {why}"]
    out, judged, found, unreadable = [], 0, 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [x for x in dirnames if x != ".git"]
        for f in filenames:
            full = Path(dirpath) / f
            rel = full.relative_to(root).as_posix()
            regime = d.regime_for(rel)
            if regime is None:
                continue
            try:
                text = full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                # REVIEWFINDS-1 X2.2: a layer file the walk cannot read was skipped and the walk
                # exited 0 — a clean verdict on a file nobody judged. It is named, and it fails.
                unreadable += 1
                out.append(f"{rel}: unreadable ({type(e).__name__}), not judged — layer is {cp(regime)}")
                continue
            judged += 1
            for n, c, ch in mismatches(text, regime):
                found += 1
                out.append(f"{rel}:{n}:{c} found {cp(ch)}, layer is {cp(regime)}")
    out.append(f"# {judged} file(s) judged, {found} mismatch(es)"
               + (f", {unreadable} unreadable" if unreadable else ""))
    return (1 if found or unreadable else 0), out


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "walk":
        print("usage: apostrophe_door.py walk <repo>", file=sys.stderr)
        return 2
    rc, lines = walk(Path(argv[1]).expanduser().resolve())
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
