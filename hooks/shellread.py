#!/usr/bin/env python3
"""shellread.py — how the doors read a Bash command line: which text the shell RUNS.

One module, imported by `gate.py` (the vault-git and data-integrity doors) and `deletedoor.py` (the
delete door), so both read a command the same way (DELETEDOOR-3). Before, the delete door read only
segments whose own verb was `rm`/`rmdir`/`unlink`, and a removal inside `bash <<EOF`, a Python
heredoc, `python3 -c "…"` or `( … )` ran unseen; the vault door had learned to read those at
DELETEDOOR-2. The reading lives here once: a copied pattern copies its bug.

  segments(cmd)        the command split on `&& || ; | newline` outside quotes, `$( )` and heredoc
                       bodies, with `bash -c '…'` unwrapped into its own segments
  strip_prefix(seg)    the segment from its head word on (`sudo`, `env X=1`, `time` … skipped)
  _heredoc_reader(seg) who reads the segment's heredoc: "writer" (`cat > f <<EOF`, never run),
                       "python", "shell", "other", or None
  _heredoc_body(seg)   that body
  _python_code(body)   a Python source with comments dropped and string literals blanked, except a
                       string that IS a removal command (kept as ` rm `)
  python_removals(src) the removals a Python source makes, with their targets where written out
"""
from __future__ import annotations
import re


def _split_shell(cmd: str) -> list[str]:
    """Split on && || ; | and newlines OUTSIDE quotes, $( ), and heredoc bodies. Conservative: on any doubt
    the text stays in one segment (an under-split can only make a rule miss; an over-split made it refuse
    legitimate commits — Balthasar, council 2026-09-08)."""
    out, cur, i, n = [], [], 0, len(cmd)
    q = None; depth = 0; heredoc = None
    while i < n:
        c = cmd[i]
        if heredoc is not None:
            cur.append(c)
            if c == "\n":
                j = cmd.find("\n", i + 1); line = cmd[i + 1: j if j != -1 else n]
                if line.strip() == heredoc:
                    cur.append(line); i = (j if j != -1 else n); heredoc = None
                    continue
            i += 1; continue
        if q:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(cmd[i + 1]); i += 2; continue
            if c == q:
                q = None
            i += 1; continue
        if c in ("'", '"'):
            q = c; cur.append(c); i += 1; continue
        if cmd.startswith("$(", i):
            depth += 1; cur.append("$("); i += 2; continue
        if c == ")" and depth:
            depth -= 1; cur.append(c); i += 1; continue
        if depth:
            cur.append(c); i += 1; continue
        m = re.match(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", cmd[i:])
        if m:
            heredoc = m.group(2); cur.append(m.group(0)); i += len(m.group(0)); continue
        if cmd.startswith("&&", i) or cmd.startswith("||", i):
            out.append("".join(cur)); cur = []; i += 2; continue
        if c == "|" and "".join(cur).rstrip().endswith(">"):
            cur.append(c); i += 1; continue           # `>|` / `>>|` clobber-redirects are not pipes
        if c in ";|\n":
            out.append("".join(cur)); cur = []; i += 1; continue
        cur.append(c); i += 1
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


# The words that run the command after them (GITPREFIX-1): `sudo git …`, `env X=1 git …`,
# `time git …` reach git as surely as `git …` does. A closed set, each with the options that take a
# value, so `sudo -u me git` and `nice -n 5 git` are read too. `VAR=val` assignments go the same way.
_PREFIXES = {"env": {"-u", "-C", "-S", "-P"}, "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"},
             "nohup": set(), "time": set(), "command": set(), "exec": {"-a"}, "nice": {"-n"},
             "caffeinate": {"-t", "-w"}, "builtin": set()}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def strip_prefix(seg: str) -> str:
    """`seg` from its head word on: leading `VAR=val` assignments and the `_PREFIXES` words, with
    their options, are skipped. The rest of the segment is returned as written — quotes, heredoc
    and spacing untouched — because the rules after this read offsets into it."""
    pos, prefix = 0, None
    while True:
        m = re.compile(r"\s*(\S+)").match(seg, pos)
        if not m:
            return ""
        w = m.group(1)
        base = w.rsplit("/", 1)[-1]                  # `/usr/bin/env git` is `env git`
        if _ASSIGN.match(w) or base in _PREFIXES:
            prefix = base if base in _PREFIXES else prefix
            pos = m.end(); continue
        if prefix and w.startswith("-") and len(w) > 1:
            pos = m.end()
            if w in _PREFIXES[prefix]:                 # an option that takes a value: skip the value too
                v = re.compile(r"\s*\S+").match(seg, pos)
                pos = v.end() if v else pos
            continue
        return seg[m.start(1):]


def segments(cmd: str) -> list[str]:
    out = []
    for s in _split_shell(cmd):
        m = re.match(r"^(?:bash|sh|zsh|/bin/(?:ba|z)?sh)\s+(?:-[a-zA-Z]*c\s+)(['\"])(.*)\1\s*$", strip_prefix(s), re.S)
        if m:
            out.extend(segments(m.group(2)))      # `bash -c "git … add -A"` is still a git command
            continue
        out.append(s)
    return out



_HEREDOC_BODY = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\2\s*$", re.S | re.M)

_WRITERS = ("cat", "tee")
_SHELLS = ("bash", "sh", "zsh", "dash", "ksh")
_PYTHON = re.compile(r"^python(?:\d+(?:\.\d+)?)?$")
_PY_DESTROY = re.compile(r"\b(?:remove|unlink|rmdir|removedirs|rmtree)\s*\(")
_RM_STRING = re.compile(r"^\s*(?:/bin/)?(?:rm|unlink|rmdir|shred)(?:\s|$)")


def _head_word(stage: str) -> str:
    w = strip_prefix(stage).split()
    return w[0].rsplit("/", 1)[-1] if w else ""


def _heredoc_reader(seg: str) -> str | None:
    """Who reads this segment's heredoc: "writer" (`cat`/`tee` writing it to a file — the `-F`
    message-body idiom), "python", "shell", "other", or None when the segment has no heredoc.
    The command line is split on `|`: the stage holding `<<` reads the body, unless it is a writer
    with a later stage, which then does (`cat <<EOF | bash` feeds a shell)."""
    head = seg.split("\n", 1)[0]
    if "<<" not in head or not _HEREDOC_BODY.search(seg):
        return None
    stages = head.split("|")
    i = next(k for k, st in enumerate(stages) if "<<" in st)
    word = _head_word(stages[i])
    if word in _WRITERS:
        if i + 1 == len(stages):
            return "writer"
        word = _head_word(stages[i + 1])
    if _PYTHON.match(word):
        return "python"
    if word in _SHELLS or word == "eval":
        return "shell"
    return "other"


def _writer_heredoc(seg: str) -> bool:
    """True when the segment's command only WRITES its heredoc somewhere (`cat > msg <<EOF`)."""
    return _heredoc_reader(seg) == "writer"


def _python_code(body: str) -> str | None:
    """A Python heredoc body with its comments dropped and its string literals blanked — except a
    string that IS a removal command (`"rm"`, `'rm -rf x'`), which `subprocess`/`os.system` run,
    kept as ` rm `. None when the body does not tokenize: the caller then reads it raw."""
    import io, tokenize                                     # noqa: PLC0415 — only for a Python heredoc
    blank = {tokenize.STRING} | {getattr(tokenize, n) for n in ("FSTRING_MIDDLE", "TSTRING_MIDDLE")
                                 if hasattr(tokenize, n)}
    out = []
    try:
        for t in tokenize.generate_tokens(io.StringIO(body).readline):
            if t.type == tokenize.COMMENT:
                continue
            if t.type in blank:
                lit = re.sub(r"^[A-Za-z]*(['\"]{1,3})|(['\"]{1,3})$", "", t.string)
                out.append(" rm " if _RM_STRING.match(lit) else " S ")
            else:
                out.append(" " + t.string)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return None
    return " ".join(out)


def _heredoc_body(seg: str) -> str:
    m = _HEREDOC_BODY.search(seg)
    return seg[seg.index("\n", m.start()) + 1: m.end()].rsplit("\n", 1)[0] if m else ""



_PY_RM_FUNCS = {"remove": False, "unlink": False, "rmdir": False, "removedirs": True, "rmtree": True}
# a string that starts a removal COMMAND: the rm family, or `find` (read by the door for -delete/-exec)
_PY_CMD_STRING = re.compile(r"^\s*(?:/bin/|/usr/bin/)?(?:rm|unlink|rmdir|shred|find)(?:\s|$)")


def python_removals(src: str) -> list[tuple[str, list[str], bool]] | None:
    """(verb, targets, recursive) for every removal a Python source makes; None when it does not
    tokenize. A removal CALL (`os.remove(…)`, `shutil.rmtree(…)`, `Path(…).unlink()`) yields its
    target when that is one string literal, else `<unseeable: …>`. A string that IS a removal
    command (the rm family, or `find`, whose -delete/-exec the caller judges) yields
    ("sh", [that command], False) for the caller to read as a shell line: a list
    (`['rm', '-f', 'x']`) is joined when every element is a string literal, else unseeable.
    Comments and every other string are prose and yield nothing. A prose string that BEGINS with
    such a command (`print('rm the old files')`) is read as one: the door cannot tell a print from a
    call by the string alone, and it errs toward refusing (the vault door reads it the same way)."""
    import io, tokenize                                     # noqa: PLC0415 — only for Python source
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                                  tokenize.DEDENT, tokenize.ENDMARKER)]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return None

    def lit(t) -> str | None:
        if t.type != tokenize.STRING or re.match(r"^[A-Za-z]*[fFtT]", t.string):
            return None                                    # an f-string's value is not on the page
        m = re.match(r"^[A-Za-z]*('''|\"\"\"|'|\")(.*)\1$", t.string, re.S)
        return m.group(2) if m else None

    out, n = [], len(toks)
    funcs = dict(_PY_RM_FUNCS)
    for i in range(n - 2):                                  # `from os import remove as r` (reviewer)
        if toks[i].string in _PY_RM_FUNCS and toks[i + 1].string == "as" and toks[i + 2].type == tokenize.NAME:
            funcs[toks[i + 2].string] = _PY_RM_FUNCS[toks[i].string]
    for i, t in enumerate(toks):
        if t.type == tokenize.NAME and t.string in funcs and i + 1 < n and toks[i + 1].string == "(":
            rec = funcs[t.string]
            if i + 3 < n and lit(toks[i + 2]) is not None and toks[i + 3].string in (")", ","):
                out.append((t.string, [lit(toks[i + 2])], rec))           # os.remove("x")
            elif (i >= 5 and toks[i - 1].string == "." and toks[i - 2].string == ")"
                  and lit(toks[i - 3]) is not None and toks[i - 4].string == "("):
                out.append((t.string, [lit(toks[i - 3])], rec))           # Path("x").unlink()
            else:
                out.append((t.string, [f"<unseeable: {t.string}(…) in Python>"], True))
        elif t.type == tokenize.STRING and lit(t) is not None and _PY_CMD_STRING.match(lit(t)):
            if i > 0 and toks[i - 1].string in ("[", "(") and i + 1 < n and toks[i + 1].string == ",":
                words, j = [lit(t)], i + 1                              # ['rm', '-f', 'x']
                while j < n and toks[j].string == ",":
                    if j + 1 < n and lit(toks[j + 1]) is not None:
                        words.append(lit(toks[j + 1])); j += 2
                    elif j + 1 < n and toks[j + 1].string in ("]", ")"):
                        j += 1                                          # a trailing comma
                        break
                    else:
                        words = None
                        break
                if words is None:
                    out.append(("sh", [f"<unseeable: {lit(t)} with arguments not written out>"], True))
                else:
                    import shlex                                        # noqa: PLC0415
                    out.append(("sh", [" ".join(shlex.quote(x) for x in words)], False))
            else:
                out.append(("sh", [lit(t)], False))                     # os.system("rm -rf x")
    return out
