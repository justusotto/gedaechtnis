#!/usr/bin/env python3
"""killdoor.py — a kill whose target the shell computes is refused (KILLDOOR-1, 2026-09-28).

On 2026-09-28 15:18:42 a session ran

    pkill -P $(pgrep -o -f "nonexistent-xyz" 2>/dev/null || echo 1)

The search found nothing, the fallback printed `1`, and `pkill -P 1` ended every child of launchd:
Terminal, the Claude app, the managing session and six hosts. Nobody typed a `1`; the shell
computed it. The door reads the COMMAND STRING and refuses the shapes whose target nobody wrote:

    shape                                             why
    kill / pkill / killall with a target from         the target is whatever the substitution
    `$( )`, backticks, `<( )` or a `$variable`        prints, including a fallback nobody reads
    `… | xargs kill` (or pkill / killall)             the targets are whatever the pipe carries
    `pkill -P` / `--parent`                           every child of a parent: `-P 1` is every app
    `pkill -f` / `--full`, `pgrep -f`                 matches the whole command line, the session's
                                                      own included (standing NEVER, prose until now)
    pid 0, pid 1, a negative pid                      this process group, launchd, a whole group;
    (`kill`, and `pkill -g` / `-s`)                   `kill -9 -1` signals every process you own
    a `kill` target that is not literal digits        only a number someone typed is a known target

`kill 12345` with the digits written out passes, and so does `kill %1` (a job of the same shell),
`kill -l`, `pkill -x name` and `killall name`. `kill -0 <any target>` passes too, a variable
included: signal 0 sends nothing and only asks whether the pid is alive (seat's decision,
2026-09-28); `pkill -0` and `killall -0` still select by pattern and keep the rule. The door reads
what the shell RUNS: every segment `shellread.segments` finds (a `case` arm included), the inside of
every `$( )` / backtick / `<( )`, a shell heredoc's body, and an UNQUOTED heredoc's substitutions;
a quoted heredoc (`<<'EOF'`) is text the shell never runs, so a commit message that quotes a kill
in backticks passes. A kill made from Python (`os.kill`, `subprocess.run(["pkill", …])`) is not read.

Mode, facts line, log and scope are the DOORS-2 ones (`ruledoors.py`, door name `kill`): WARN until
`limits.kill_deny_from`, then DENY inside the vault or a marked repo, a note elsewhere.
"""
from __future__ import annotations
import re
from pathlib import Path

import shellread

KILLERS = ("kill", "pkill", "killall")
_SIGNALS = {"HUP", "INT", "QUIT", "ILL", "TRAP", "ABRT", "IOT", "EMT", "FPE", "KILL", "BUS", "SEGV", "SYS",
            "PIPE", "ALRM", "TERM", "URG", "STOP", "TSTP", "CONT", "CHLD", "CLD", "TTIN", "TTOU", "IO",
            "POLL", "XCPU", "XFSZ", "VTALRM", "PROF", "WINCH", "INFO", "USR1", "USR2", "PWR", "STKFLT"}
# pkill / pgrep options that take a value (BSD and procps together); `-P`, `-g`, `-s` are judged.
# `-c` (procps: count) and `-L` (BSD: pidfile lock) take none; `-d` is pgrep's delimiter.
_PK_VALUE = set("FgGPstuUdJ")
_PK_LONG_VALUE = {"--parent", "--pgroup", "--session", "--group", "--euid", "--uid", "--terminal",
                  "--pidfile", "--signal", "--ns", "--nslist", "--runstates", "--cgroup", "--env"}
_XARGS_VALUE = {"-I", "-J", "-L", "-n", "-P", "-R", "-s", "-S", "-E", "-a", "-d",
                "--arg-file", "--delimiter", "--max-args", "--max-procs", "--max-chars", "--process-slot-var"}
# GNU `-e`/`-i`/`-l` take their value glued (`-i{}`), never the next word; `--eof`/`--replace`/`--max-lines` use `=`
_TIMEOUTS = ("timeout", "gtimeout")
_REDIR = re.compile(r"^(?:\d*|&)(?:>>?|<<?<?|>&|<&|&>>?)")

HOWTO = ("Instead: stop a run by the PID you captured when you LAUNCHED it (`cmd & echo $! > run.pid`, "
         "then read that file and type the number: `kill 12345`), or let the run finish, or hand the "
         "line to the owner to run by hand. Never search for a process to end it.")


# ---- reading the command -------------------------------------------------------------------------

def words(seg: str) -> list[str]:
    """Split `seg` on unquoted whitespace, keeping quotes, `$( )`, `${ }`, `<( )` and backticks
    inside one word, and dropping redirections with their targets (`2>/dev/null`, `> f`)."""
    out, cur, i, n = [], [], 0, len(seg)
    q, depth, tick = None, 0, False
    while i < n:
        c = seg[i]
        if q:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(seg[i + 1]); i += 2; continue
            if c == q:
                q = None
            i += 1; continue
        if tick:
            cur.append(c)
            if c == "`":
                tick = False
            i += 1; continue
        if c in ("'", '"') and not depth:
            q = c; cur.append(c); i += 1; continue
        if c == "`" and not depth:
            tick = True; cur.append(c); i += 1; continue
        if seg.startswith(("$(", "<(", ">(", "${"), i):
            depth += 1; cur.append(seg[i:i + 2]); i += 2; continue
        if c in ")}" and depth:
            depth -= 1; cur.append(c); i += 1; continue
        if c.isspace() and not depth:
            if cur:
                out.append("".join(cur)); cur = []
            i += 1; continue
        if c == "#" and not cur and not depth:
            break                                   # `kill 12345 # stop server`: the rest is a comment
        cur.append(c); i += 1
    if cur:
        out.append("".join(cur))
    kept, skip = [], False
    for w in out:
        if skip:
            skip = False; continue
        m = _REDIR.match(w)
        if m and not w.startswith(("<(", ">(")):
            if m.end() == len(w):
                skip = True                     # `2> /dev/null`: the target is the next word
            continue
        kept.append(w)
    return kept


def substitutions(text: str) -> list[str]:
    """The bodies of every `$( )`, `<( )`, `>( )` and backtick pair in `text`, outermost first; a
    single-quoted stretch is literal and is skipped."""
    out, i, n, q, body = [], 0, len(text), None, False
    while i < n:
        c = text[i]
        if c == "\n" and q is None:
            body = True                           # past the first line: a heredoc body, where `#` hides nothing
        if q == "'":
            if c == "'":
                q = None
            i += 1; continue
        if c == "'" and q is None and not body:  # in an unquoted heredoc body a quote is plain text
            q = "'"; i += 1; continue
        if c == '"' and not body:
            q = None if q == '"' else '"'
            i += 1; continue
        if c == "\\" and i + 1 < n:
            i += 2; continue
        if c == "#" and q is None and not body and (i == 0 or text[i - 1] in " \t;&|("):
            j = text.find("\n", i)                # `echo hi # $(pkill -f x)`: a comment runs nothing
            i = n if j == -1 else j
            continue
        if text.startswith(("$(", "<(", ">("), i) and not text.startswith("$((", i):
            depth, j, qq = 1, i + 2, None
            while j < n and depth:
                d = text[j]
                if qq:
                    if d == qq:
                        qq = None
                elif d in ("'", '"'):
                    qq = d
                elif text.startswith(("$(", "<(", ">("), j):
                    depth += 1; j += 1
                elif d == ")":
                    depth -= 1
                j += 1
            out.append(text[i + 2:j - 1] if depth == 0 else text[i + 2:])
            i = j; continue
        if c == "`":
            j = text.find("`", i + 1)
            out.append(text[i + 1:j] if j != -1 else text[i + 1:])
            i = (j + 1) if j != -1 else n; continue
        i += 1
    return out


def _lit(w: str) -> str:
    """A word the shell does not compute, as the command receives it: quotes, backslashes and a
    `$'…'` wrapper removed (`"-P"`, `\\-P`, `$'-P'` all reach pkill as `-P`)."""
    w = re.sub(r"\$'((?:[^'\\]|\\.)*)'", r"\1", w)
    return re.sub(r"\\(.)", r"\1", re.sub(r"[\'\"]", "", w))


def _unquote(w: str) -> str:
    if len(w) >= 2 and w[0] == w[-1] and w[0] in ("'", '"'):
        return w[1:-1]
    return w


def _computed(w: str) -> bool:
    """A word whose value the shell makes: a substitution, backticks or a parameter expansion.
    Inside single quotes nothing expands, so `'$x'` is literal text."""
    if w.startswith("'") and w.endswith("'") and len(w) >= 2:
        return False
    return "`" in w or bool(re.search(r"\$[({A-Za-z_0-9$!?#@*-]|<\(", w))


def _base(w: str) -> str:
    """The command name as the shell and the disk resolve it: `\\kill`, `pk''ill` and `/bin/kill` are
    `kill`, and `PKILL` is `pkill` too — macOS's disk is case-insensitive, so it finds `/usr/bin/pkill`."""
    return Path(re.sub(r"[\\'\"]", "", w)).name.lower()


# ---- the shapes ----------------------------------------------------------------------------------

def _is_signal(opt: str) -> bool:
    s = opt.lstrip("-")
    if s.isdigit():
        return True
    s = s.upper()
    return (s[3:] if s.startswith("SIG") else s) in _SIGNALS


def _pid_problem(t: str) -> str | None:
    """What is wrong with a pid a person wrote, or None when it is an ordinary literal pid."""
    v = _unquote(t)
    if _computed(t):
        return f"the target `{t}` is computed by the shell, not written"
    if v.startswith("-"):
        return f"`{v}` is a NEGATIVE pid: it signals a whole process group (`-1`: every process you own)"
    if re.fullmatch(r"[0-9]+", v):
        if not v.strip("0"):
            return "pid `0` is this process group: the session's own shell and everything it started"
        if v.lstrip("0") == "1":
            return "pid `1` is launchd: the parent of every app on this machine"
        return None
    if re.fullmatch(r"%[0-9]+|%[%+-]?", v):
        return None                              # a job of this same shell
    return f"the target `{v}` is not a pid written out in digits"


def _nothing_after_signal(rest: list[str]) -> bool:
    """True when no second signal option follows signal 0: bash's `kill` reads EVERY `-s`/`-n`/
    `-SIG` before the first pid, so `kill -0 -s 9 -1` sends SIGKILL to every process you own."""
    return not (rest and rest[0].startswith("-") and rest[0] != "--")


def _check_kill(args: list[str]) -> str | None:
    i = 0
    if args and args[0] in ("-l", "-L", "--list", "--table"):
        return None                              # lists signals, signals nothing
    if args and args[0] in ("-s", "-n", "--signal"):
        if len(args) > 1 and _unquote(args[1]) == "0" and _nothing_after_signal(args[2:]):
            return None                          # `kill -s 0 $PID`: signal 0 only asks if the pid lives
        i = 2
    elif args and args[0].startswith("-") and args[0] != "--" and _is_signal(args[0]):
        if args[0] == "-0" and _nothing_after_signal(args[1:]):
            return None                          # `kill -0 $PID`: sends nothing (seat's decision 2026-09-28)
        i = 1
    if i < len(args) and args[i] == "--":
        i += 1
    targets = args[i:]
    if not targets:
        return None
    for t in targets:
        why = _pid_problem(t)
        if why:
            return f"`kill`: {why}."
    return None


_MATCH_ALL = re.compile(r"^\^?\.?[*+]?\$?$|^\.\*?$")


def _matches_every_name(p: str) -> bool:
    """`.`, `.*`, `^$`… and any pattern that matches the empty string (`a*`, `.*a*`) match every
    process name."""
    v = _unquote(p)
    if _MATCH_ALL.match(v):
        return True
    try:
        return re.search(v, "") is not None
    except (re.error, OverflowError, RecursionError):
        return False


def _check_pkill(verb: str, args: list[str]) -> str | None:
    """`pkill` and `pgrep` share their options; `pgrep` only ever errs on `-f`."""
    i, pattern, pidfile = 0, [], False
    while i < len(args):
        a = args[i]
        if a == "--":
            pattern.extend(args[i + 1:]); break
        if a.startswith("--"):
            name, _, val = a.partition("=")
            if name in ("--full",):
                return f"`{verb} --full` matches the whole command line, this session's own included."
            if name == "--parent" and verb == "pkill":
                return "`pkill --parent` signals every child of a parent: `--parent 1` is every app on this machine."
            if name in ("--pgroup", "--session") and verb == "pkill":
                v = val or (args[i + 1] if i + 1 < len(args) else "")
                for x in v.split(","):
                    why = _pid_problem(x) if x else None
                    if why and "not a pid" not in why:
                        return f"`pkill {name}`: {why}."
            if name == "--inverse" and verb == "pkill":
                return "`pkill --inverse` signals every process that does NOT match: all of them but a few."
            if name in _PK_LONG_VALUE and not val:
                i += 1
            i += 1; continue
        if a.startswith("-") and len(a) > 1:
            if verb == "pkill" and _is_signal(a):
                i += 1; continue                 # `-9`, `-HUP`, `-KILL`: a signal, not a flag cluster
            letters = a[1:]
            for k, ch in enumerate(letters):
                if ch == "f":
                    return (f"`{verb} -f` matches the whole command line, so the search finds this session's "
                            "own command and whatever else happens to mention the word.")
                if ch == "v" and verb == "pkill":
                    return "`pkill -v` signals every process that does NOT match: all of them but a few."
                if ch == "F":
                    pidfile = True
                if ch == "P" and verb == "pkill":
                    return ("`pkill -P` signals every child of a parent: `-P 1` is every app on this machine "
                            "(2026-09-28 15:18, when a search that found nothing fell back to `1`).")
                if ch in _PK_VALUE:
                    val = letters[k + 1:] or (args[i + 1] if i + 1 < len(args) else "")
                    if verb == "pkill" and ch in "gs":
                        for v in val.split(","):
                            why = _pid_problem(v) if v else None
                            if why and "not a pid" not in why:
                                return f"`pkill -{ch}`: {why}."
                    elif verb == "pkill" and _computed(val):
                        return f"`pkill -{ch}`: the value `{val}` is computed by the shell, not written."
                    if not letters[k + 1:]:
                        i += 1
                    break
            i += 1; continue
        pattern.append(a); i += 1
    if verb == "pkill":
        for p in pattern:
            if _computed(p):
                return f"`pkill`: the pattern `{p}` is computed by the shell, not written."
            if _matches_every_name(p):
                return f"`pkill`: the pattern `{p}` matches every process name."
        if not pattern and not pidfile:
            return ("`pkill` with no pattern signals every process its options select — `pkill -u <you>` "
                    "is every process you own.")
    return None


def _check_killall(args: list[str]) -> str | None:
    names, i, regex, scoped = [], 0, False, False
    while i < len(args):
        a = args[i]
        if _computed(a):
            return f"`killall`: the target `{a}` is computed by the shell, not written."
        if a in ("-u", "-t", "-c"):
            scoped = scoped or a in ("-u", "-t")
            if i + 1 < len(args) and _computed(args[i + 1]):
                return f"`killall {a}`: the value `{args[i + 1]}` is computed by the shell, not written."
            i += 2; continue
        if a == "-m":
            regex = True
        elif not a.startswith("-"):
            names.append(a)
        i += 1
    if scoped and not names:
        return ("`killall -u`/`-t` with no process name signals every process of that user or terminal.")
    if regex and any(_matches_every_name(n) for n in names):
        return "`killall -m` with a pattern that matches every process name."
    return None


_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "time", "coproc"}
_CASE = re.compile(r"""^case\s+(?:"[^"]*"|'[^']*'|\$\([^)]*\)|\S+)\s+in(?:\s+|$)""")
# a case arm's pattern: `stop)`, `(stop)`, `*)`, `"a b")` — the command after it runs
_CASE_ARM = re.compile(r"""^\(?(?:"[^"]*"|'[^']*'|[^\s()`'"|])+\s*\)\s*""")
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}


def _head(seg: str) -> str:
    """`seg` from the command it runs: shell keywords (`do`, `then`, `if` …), `!`, an opening `(` or
    `{`, a function header `name()` and the `strip_prefix` words are skipped; an unmatched closing
    `)` or `}` at the end is dropped. `for p in …; do kill $p` reads as `kill $p`."""
    s = seg.strip()
    for _ in range(12):
        before = s
        if s.startswith("(") and not s.startswith("(("):
            s = s[1:].lstrip()
        m = re.match(r"^(?:function\s+[A-Za-z_][\w.-]*(?:\s*\(\s*\))?|[A-Za-z_][\w.-]*\s*\(\s*\))\s*", s)
        if m:
            s = s[m.end():]
        m = _CASE.match(s)
        if m:
            s = s[m.end():]
        m = _CASE_ARM.match(s)
        if m:
            s = s[m.end():]
        w = s.split(None, 1)
        if w and w[0] in _KEYWORDS:
            s = w[1] if len(w) > 1 else ""
        s = shellread.strip_prefix(s) if s else s
        if s == before:
            break
    while s.endswith((")", "}")) and s.count(s[-1]) > s.count("(" if s[-1] == ")" else "{"):
        s = s[:-1].rstrip()
    return s


def _verb_and_args(seg: str) -> tuple[str, list[str], str | None]:
    """(head command's basename, its arguments, the command `xargs` runs or None)."""
    w = words(_head(seg))
    while w and _base(w[0]) in _TIMEOUTS:        # `timeout 5 kill …`
        w = w[1:]
        while w and w[0].startswith("-"):
            w = w[2:] if w[0] in ("-s", "-k", "--signal", "--kill-after") else w[1:]
        w = w[1:]                                 # the duration
    if not w:
        return "", [], None
    # an option spelled with quotes or a backslash is still the option
    w = [w[0]] + [(_lit(x) if not _computed(x) and _lit(x).startswith("-") else x) for x in w[1:]]
    verb = _base(w[0])
    if verb == "xargs":
        k = 1
        while k < len(w) and w[k].startswith("-"):
            k += 2 if w[k] in _XARGS_VALUE else 1
        prefix = None                             # `xargs sudo -u me kill`, `xargs env X=1 kill`
        while k < len(w):
            b = _base(w[k])
            if b in shellread._PREFIXES or b in _TIMEOUTS:
                prefix = b; k += 1
                if b in _TIMEOUTS:
                    while k < len(w) and w[k].startswith("-"):
                        k += 2 if w[k] in ("-s", "-k", "--signal", "--kill-after") else 1
                    k += 1                        # the duration
                continue
            if shellread._ASSIGN.match(w[k]):
                k += 1; continue
            if prefix and w[k].startswith("-"):
                k += 2 if w[k] in shellread._PREFIXES.get(prefix, ()) else 1
                continue
            break
        return verb, w[1:], (_base(w[k]) if k < len(w) else None)
    return verb, w[1:], None


_OPENS_HEREDOC = re.compile(r"(?<!<)<<-?\s*['\"]?[A-Za-z_]")


def amp_split(seg: str) -> list[str]:
    """`seg` cut at every unquoted single `&` (a background job), which `shellread` keeps inside one
    segment: `sleep 5 & pkill -P 1` is two commands, and the second one is the kill."""
    nl = seg.find("\n")
    if nl != -1 and _OPENS_HEREDOC.search(seg[:nl]):
        parts = amp_split(seg[:nl])                # a heredoc body is text: only its opener's line is cut
        k = max((j for j, x in enumerate(parts) if _OPENS_HEREDOC.search(x)), default=len(parts) - 1)
        if parts:
            parts[k] += seg[nl:]
        return parts
    out, cur, i, n = [], [], 0, len(seg)
    q, depth = None, 0
    while i < n:
        c = seg[i]
        if q:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(seg[i + 1]); i += 2; continue
            if c == q:
                q = None
            i += 1; continue
        if c == "\\" and i + 1 < n:                 # `it\'s`: an escaped quote opens nothing
            cur.append(seg[i:i + 2]); i += 2; continue
        if c in ("'", '"', "`"):
            q = c; cur.append(c); i += 1; continue
        if seg.startswith(("$(", "<(", ">("), i):
            depth += 1; cur.append(seg[i:i + 2]); i += 2; continue
        if c == ")" and depth:
            depth -= 1; cur.append(c); i += 1; continue
        if (c == "&" and not depth and not seg.startswith("&>", i) and not seg.startswith("&&", i)
                and (i == 0 or seg[i - 1] not in "<>&|")):
            out.append("".join(cur)); cur = []; i += 1; continue
        cur.append(c); i += 1
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def check_segment(seg: str, _depth: int = 0) -> str | None:
    parts = amp_split(seg)
    if len(parts) > 1:
        for part in parts:
            why = check(part, _depth=_depth + 1)  # the whole reading: `sh -c '…' &` is unwrapped too
            if why:
                return why
        return None
    seg = parts[0] if parts else seg
    verb, args, xcmd = _verb_and_args(seg)
    if verb == "eval":
        return check(" ".join(_unquote(a) for a in args), _depth=_depth + 1)
    if verb in _SHELLS:
        k = 0                                     # `bash -l -c`, `bash -o pipefail -c`, `bash --login -c`
        while k < len(args) and not re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", args[k]):
            if args[k] in ("-o", "+o", "-O", "+O"):
                k += 2
            elif args[k].startswith(("-", "+")) and args[k] != "-":
                k += 1
            else:
                break
        if k < len(args) and re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", args[k]) and k + 1 < len(args):
            return check(_unquote(args[k + 1]), _depth=_depth + 1)  # `bash -c '…' arg0` too
        if "<<<" in args:                         # `bash <<< 'pkill -f x'`: the here-string is the script
            k = args.index("<<<")
            return check(_unquote(args[k + 1]), _depth=_depth + 1) if k + 1 < len(args) else None
        m = re.search(r"<<<\s*(\S.*)$", seg, re.S)
        if m:
            return check(_unquote(m.group(1).strip()), _depth=_depth + 1)
        return None
    if verb == "xargs":
        if xcmd in KILLERS:
            return (f"`xargs {xcmd}` signals whatever the pipe hands it: the targets are computed, and an "
                    "empty or wrong search upstream becomes a wrong kill.")
        return None
    if verb == "kill":
        return _check_kill(args)
    if verb in ("pkill", "pgrep"):
        return _check_pkill(verb, args)
    if verb == "killall":
        return _check_killall(args)
    return None


_QUOTED_HEREDOC = re.compile(r"""<<-?\s*(?:'(\w+)'|"(\w+)"|\\(\w+))""")


def _expanded(seg: str) -> str:
    """`seg` with the body of every heredoc whose end word is QUOTED (`<<'EOF'`, `<<"EOF"`,
    `<<\\EOF`) cut out: that body is literal text the shell never expands, so a commit message
    quoting `pkill -f x` in backticks runs nothing. The lines around it stay (`"$(cat <<'EOF' … EOF
    )" "$(pkill -f y)"` still shows its second substitution). An UNQUOTED body (`cat > f <<EOF`
    holding `$(pkill -f x)`) really runs, so it stays."""
    lines, out, i = seg.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        ends = [next(g for g in m.groups() if g) for m in _QUOTED_HEREDOC.finditer(line)]
        i += 1
        for word in ends:                       # bodies follow in the order their `<<` appear
            while i < len(lines) and lines[i].strip() != word:
                i += 1
            i += 1                              # the end-word line itself
    return "\n".join(out)


# `<<\EOF` quotes its end word as `<<'EOF'` does. shellread (shared with the delete and git doors)
# reads only the quote spellings, so the kill door rewrites this one before it splits the command.
_BACKSLASH_HEREDOC = re.compile(r"(?<!<)(<<-?\s*)\\([A-Za-z_][A-Za-z0-9_]*)")   # shellread's end words: ASCII
_HEREDOC_OP = re.compile(r"""(?<!<)<<-?\s*(?:'\w+'|"\w+"|\\?\w+)""")


def check(cmd: str, segments=shellread.segments, _depth: int = 0) -> str | None:
    """The first problem this command's kills carry, in words, or None."""
    if not re.search(r"(?i)kill|pgrep", re.sub(r"[\\'\"]", "", cmd)):
        return None
    if _depth > 6:
        return ("a kill nested more than six shells, evals or substitutions deep: the door cannot read "
                "what it would signal.")
    cmd = cmd.replace("\\\n", "")                  # a backslash-newline joins two lines into one
    cmd = _BACKSLASH_HEREDOC.sub(r"\1'\2'", cmd)    # `<<\EOF` is `<<'EOF'`: the body is literal text
    for seg in segments(cmd):
        try:
            why = _check_one(seg, segments, _depth)
        except Exception:                         # noqa: BLE001 — one unreadable part costs only itself
            why = None
        if why:
            return why
    return None


def _check_one(seg: str, segments, _depth: int) -> str | None:
    why = check_segment(seg, _depth)
    if why:
        return why
    for body in substitutions(_expanded(seg)):
        why = check(body, segments, _depth + 1)
        if why:
            return why
    if "\n" in seg and _HEREDOC_OP.search(seg.split("\n", 1)[0]):
        # `cat > f <<'EOF' && pkill -P 1`: the rest of the opener's line runs; shellread keeps it
        # inside the heredoc segment, so it is read again with the heredoc operator blanked out
        line = _HEREDOC_OP.sub(" ", seg.split("\n", 1)[0])
        why = check(line, segments, _depth + 1)
        if why:
            return why
    if shellread._heredoc_reader(seg) == "shell":
        why = check(shellread._heredoc_body(seg), segments, _depth + 1)
        if why:
            return why
    return None


def refusal(finding: str) -> str:
    return finding + "\n" + HOWTO
