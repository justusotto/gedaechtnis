#!/usr/bin/env python3
"""gate.py — the PreToolUse locked doors of Das Gedächtnis.

    python3 gate.py bash    # vault git law · launch pins model · data integrity · artifact-not-file
    python3 gate.py write   # partition (WARN or DENY per state file) · Concilium stem rule
    python3 gate.py agent   # every Agent call pins a model unless its definition does

A rule is DETERMINISTIC or it is not here: anything needing judgment stays prose (Kernel, Nomos)
and is at most reported by a PostToolUse chore (see chore.py). Each deny cites the vault entry it
enforces so the model learns the why, not just the wall. First deny wins; silence means "no
opinion" and the normal permission flow continues.

Partition mode: `<state>/partition.mode` holds `warn` (default when absent) or `deny`. The intended
adoption path is warn for a week, read the log, then flip the word.
"""
from __future__ import annotations
import re, sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (read_input, deny, ask, log, expand, under, vault_rel, lane_for, path_in_partition,
                    VAULT, HOME, STATE, ROLE_STEMS, guarded, shared_surface, pure_append)
import shlex
import config as _cfg

EV = "PreToolUse"

def _trailing_pathspec(flags: str) -> bool:
    """`git commit -m x Global/Map.md` IS path-limited: git reads a bare trailing token as a pathspec
    with or without `--`. Council 2's dad test (Balthasar, 2026-09-09) found the door refusing that
    form as "bare" for a stranger who never read the vault law. `flags` has quoted text blanked to
    ` Q ` and heredocs to `HEREDOC`; options that take a value consume the next token."""
    takes = {"-m", "-F", "--file", "--author", "--date", "-c", "-C", "--trailer", "--cleanup",
             "--fixup", "--squash", "--reuse-message", "--reedit-message"}
    toks = flags.split(); i = 0
    while i < len(toks):
        t = toks[i]
        if t == "--":
            return True
        if t in takes:
            i += 2; continue
        if t.startswith("-") or t in ("Q", "HEREDOC"):
            i += 1; continue
        return True
    return False


# ---------------------------------------------------------------- bash: segment the command ----

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\n|\|)\s*")
_GIT_C = re.compile(r"(?:^|\s)-C\s+(\S+)")


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


def segments(cmd: str) -> list[str]:
    out = []
    for s in _split_shell(cmd):
        m = re.match(r"^(?:bash|sh|zsh|/bin/(?:ba|z)?sh)\s+(?:-[a-zA-Z]*c\s+)(['\"])(.*)\1\s*$", s, re.S)
        if m:
            out.extend(segments(m.group(2)))      # `bash -c "git … add -A"` is still a git command
            continue
        out.append(s)
    return out


def git_segments(cmd: str, cwd: str | None):
    """Yield (segment, repo_path) for every `git` segment, tracking `cd` across segments."""
    cur = Path(cwd) if cwd else HOME
    for seg in segments(cmd):
        words = seg.split()
        # skip leading env assignments
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        if not words:
            continue
        if words[0] == "cd" and len(words) > 1:
            cur = expand(words[1], str(cur))
            continue
        if words[0] != "git":
            continue
        m = _GIT_C.search(seg)
        repo = expand(m.group(1), str(cur)) if m else cur
        g = re.search(r"--git-dir[= ](\S+)", seg)
        if g:
            repo = expand(g.group(1), str(cur)).parent if expand(g.group(1), str(cur)).name == ".git" else expand(g.group(1), str(cur))
        yield seg, repo


def rule_vault_git(cmd: str, cwd: str | None) -> str | None:
    for seg, repo in git_segments(cmd, cwd):
        if not (repo == VAULT or under(repo, VAULT)):
            continue
        w = seg.split()
        sub = next((x for x in w[1:] if not x.startswith("-") and x not in ("-C",) ), None)
        # the token after -C is the path, not the subcommand
        toks = w[1:]
        i = 0; sub = None
        while i < len(toks):
            if toks[i] in ("-C", "-c"):
                i += 2; continue
            if toks[i].startswith("-"):
                i += 1; continue
            sub = toks[i]; break
        # The subcommand is located as a WHOLE WORD after the git options — `seg.index("commit")`
        # matched inside a PATH containing "commit" (a pytest tmp dir), so the body started mid-path
        # and a path fragment read as a pathspec (found 2026-09-09 by the trailing-pathspec test).
        sub_m = re.search(r"(?:^|\s)" + re.escape(sub or "\x00") + r"(?=\s|$)", seg[len(w[0]):]) if sub else None
        after_sub = seg[len(w[0]) + sub_m.end():] if sub_m else ""
        if sub == "add" and re.search(r"(?:\s|^)(?:-A|--all|-a|-u|--update|\.)(?:\s|$)", after_sub):
            return ("Vault law: never `git add -A`/`-a`/`-u`/`.` in ~/Atlas — a broad add sweeps another lane's "
                    "in-flight work into your commit. Add the exact paths: `git -C ~/Atlas add -- <file>`. "
                    "(Speculum/Kernel 'Standing constraints'; Global/Errata 'A file written before a concurrent "
                    "automated committer runs…')")
        if sub == "commit":
            body = after_sub
            body = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?\n\2\s*$", "HEREDOC", body, flags=re.S | re.M)   # a heredoc body is text, whatever it contains
            flags = re.sub(r"'[^']*'|\"(?:[^\"\\\\]|\\\\.)*\"", " Q ", body)   # quoted text cannot carry a flag
            if re.search(r"(?:\s|^)(?:-[a-zA-Z]*a[a-zA-Z]*|--all)(?:\s|$)", flags):
                return "Vault law: never `git commit -a` in ~/Atlas. Commit path-limited: `git -C ~/Atlas commit -m '<msg>' -- <file>`."
            if "--amend" in flags:
                return ("Vault law: NO-AMEND — never `git commit --amend` on an Atlas commit; forward-fix with a new commit. "
                        "(Speculum/Kernel 'Standing constraints')")
            m = re.search(r'-m\s+"([^"]*)"', body)
            if m and ("`" in m.group(1) or "$(" in m.group(1)) and not re.match(r"^\$\(cat\s*(?:<<|HEREDOC)", m.group(1).strip()):
                # `-m "$(cat <<'EOF' … EOF)"` is the DELIBERATE quoted-heredoc idiom, not an accident
                return ("A backtick or `$(` inside a DOUBLE-quoted `git commit -m` is command-substituted: the word vanishes "
                        "and the commit still succeeds, permanently (NO-AMEND). Use single quotes, or `git commit -F <msgfile>`. "
                        "(Global/Errata 'Backticks inside a DOUBLE-quoted git commit -m…')")
            has_pathspec = re.search(r"\s--(?:\s|$)", flags) is not None or _trailing_pathspec(flags)
            assert_form = "diff --cached --name-only" in cmd
            if has_pathspec and re.search(r"\brm\s+(?:-r\s+)?--cached\b", cmd):
                return ("`git rm --cached` followed by a pathspec commit (`commit … -- <paths>`) commits the WORKING TREE and silently "
                        "DISCARDS the staged deletion — the commit lies about its contents. Use the stage → ASSERT "
                        "(`diff --cached --name-only`) → commit-with-NO-pathspec form in one invocation. (Global/Errata "
                        "'`git commit -- <paths>` commits the WORKING TREE and DISCARDS what you staged…')")
            if not has_pathspec and not assert_form and "-F" not in flags and "--file" not in flags:
                return ("Vault law: never a bare `git commit` in ~/Atlas — the pathspec is the guarantee. Use "
                        "`git -C ~/Atlas commit -m '<msg>' -- <file>`; or, ONLY for a `git rm --cached`, the stage→ASSERT "
                        "(`diff --cached --name-only`)→commit form in one invocation. (Speculum/Kernel 'Standing constraints')")
            if not has_pathspec and not assert_form and ("-F" in flags or "--file" in flags):
                return ("Vault law: `git commit -F <msg>` in ~/Atlas still needs the pathspec: append `-- <file>` "
                        "(everything after `--` is a pathspec), or use the stage→ASSERT→commit form.")
        if sub == "rm" and "--cached" not in seg:
            return ("Defaults never delete: `git rm` removes tracked vault files from the working tree without the Trash. "
                    "Use `git rm --cached` to untrack (the file stays), or move the file to the Trash by hand and commit the "
                    "deletion path-limited. (Global/Nomos §Data integrity)")
        if sub == "push":
            if not re.search(r"\bpush\s+(?:\S*\s+)*backup\b", seg):
                return ("Vault law: never push the vault to a cross-machine SYNC remote; only the bare `backup` remote "
                        "(T7) is allowed: `git -C ~/Atlas push backup`. (Speculum/Kernel 'Standing constraints')")
        if sub == "reset" and "--hard" in seg:
            return ("`git reset --hard` in ~/Atlas destroys another live session's uncommitted work outright. Refused. "
                    "Inspect with `git status`/`git diff`, and forward-fix. (Global/Errata rider: never `checkout --`, "
                    "`stash` or `reset --hard` past a modified-on-disk warning on a file you don't own)")
        if sub == "clean" and re.search(r"\s-[a-zA-Z]*f", seg):
            return "`git clean -f` in ~/Atlas deletes untracked files another session may be writing. Refused; move to Trash by hand if needed."
    return None


_CLAUDE_SUBCMDS = {"plugin","mcp","config","doctor","update","login","logout","setup-token","agents",
                   "install","migrate-installer","--version","-v","--help","-h","auth","upgrade"}


def rule_launch_model(cmd: str) -> str | None:
    for seg in segments(cmd):
        w = seg.split()
        while w and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]):
            w = w[1:]
        if not w or w[0] not in ("claude", "claude.exe"):
            continue
        if len(w) > 1 and w[1] in _CLAUDE_SUBCMDS:
            continue
        if "--model" not in w and not any(x.startswith("--model=") for x in w):
            return ("Every `claude` launch pins `--model` (and `--effort`) explicitly; the settings-file default is "
                    "silent routing authority: a bare launch runs on whatever model the settings file happens to name, "
                    "at whatever price. Add `--model <id> --effort <level>`.")
        if _cfg.flag("require_launch_effort") and "--effort" not in w and not any(x.startswith("--effort=") for x in w):
            return ("This `claude` launch pins `--model` but not `--effort`; the platform default is `high`, and the "
                    "ruled defaults hold ONLY if the launch pins them. Add `--effort <low|medium|high>`. (Global/Map §Models)")
    return None


_PROTECTED = [
    (re.compile(r"_cache\.json"), "mining caches (whisper_cache.json, gemini_cache.json, …) are USER DATA — real money and time"),
    (re.compile(r"anki_mining\.db"), "anki_mining.db is the mining database — user data"),
    (re.compile(r"(?:^|[\s/'\"])media/"), "a `media/` directory holds mined clips — user data"),
    (re.compile(r"(?:~|/Users/[^/\s]+)/Pictures"), "~/Pictures holds his photographs"),
    (re.compile(r"\.Trash"), "the Trash is NEVER emptied — it is his permanent restore net"),
    (re.compile(r"(?:~|/Users/[^/\s]+|\$HOME|\$\{HOME\})/Atlas(?:/|\s|$)"), "the vault and its history"),
]
_DESTROY = re.compile(r"(?<!git )(?:^|\s)(?:rm|unlink|shred|rmdir)\s|\bfind\b.*\s-delete\b|\bgit\s+clean\b|>\s*\S*_cache\.json")


def rule_data_integrity(cmd: str) -> str | None:
    if re.search(r"osascript.*(?:empty\s+(?:the\s+)?trash)", cmd, re.I):
        return "The Trash is never emptied (Global/Nomos §Data integrity — 'so I can always save'). Refused."
    if re.search(r"(?:DROP\s+TABLE|DELETE\s+FROM|TRUNCATE)\b.*anki_mining\.db|anki_mining\.db.*(?:DROP\s+TABLE|DELETE\s+FROM|TRUNCATE)\b", cmd, re.I | re.S):
        return "Destructive SQL against anki_mining.db is refused: it is user data. Back up first (scripts/anki_backup.py) and ask."
    if not _DESTROY.search(cmd):
        return None
    for seg in segments(cmd):
        if not _DESTROY.search(seg):
            continue
        w0 = seg.split()
        if w0 and w0[0] == "git" and not re.search(r"\bgit\s+(?:-C\s+\S+\s+)?clean\b", seg):
            continue                                  # `git rm --cached` etc. are index operations; the vault-git rule owns them
        for rx, why in _PROTECTED:
            if rx.search(seg):
                # allow rm inside the vault's gitignored scratch (.atlas-locks, .pre-* backups) explicitly
                if "vault" in why and re.search(r"Atlas/(?:\.atlas-locks|\.atlas-writer\.lock|[^\s]*\.pre-)", seg):
                    continue
                return (f"Defaults never delete: {why}. Deletions go to the Trash via Finder in one `Cleanup YYYY-MM-DD/` "
                        "bundle with a README, never `rm`; and this class asks the owner first. (Global/Nomos §Data integrity)")
    return None


def rule_artifact_not_file(cmd: str, cwd: str | None) -> str | None:
    for seg in segments(cmd):
        w = seg.split()
        if not w or w[0] != "open":
            continue
        for tok in w[1:]:
            if tok.startswith("-"):
                continue
            t = tok
            if t.startswith("file://"):
                t = t[len("file://"):]
            if not t.lower().endswith(".html"):
                continue
            p = expand(t, cwd)
            try:
                head = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r"""claude\.use\(\s*["'](?:db|artifact|self)["']""", head) or "data-answer-store" in head:
                return ("This page keeps its answers in the ARTIFACT's own store; a `file://` copy has no store, and an "
                        "answer given there strands the work in the reviewer's browser (it happened twice on 2026-09-07). Open the "
                        "artifact URL instead — look it up in ~/Atlas/Pharos/artifacts-index.md, or publish/republish via "
                        "the Artifact tool and `open` that URL.")
    return None


_WRITE_VERBS = {"tee", "cp", "mv", "rm", "touch", "truncate", "install"}


def bash_write_targets(cmd: str, cwd: str | None) -> list[tuple[Path, str]]:
    """Heuristic: vault paths a Bash command writes. (path, how) — how ∈ redirect-append · redirect · sed-i · verb."""
    out = []
    for seg in segments(cmd):
        try:
            w = shlex.split(seg)
        except ValueError:
            w = seg.split()
        for i, tok in enumerate(w):
            # every output-redirect spelling: > >> 1> 2> &> >| >>| 1>> &>> — with the target attached or as the next token
            m = re.match(r"^(?:\d+|&)?(>>|>)\|?(.*)$", tok)
            if not m or tok.startswith(">&") or re.match(r"^\d+>&", tok):
                continue
            how = "redirect-append" if m.group(1) == ">>" else "redirect"
            target = m.group(2)
            if not target and i + 1 < len(w):
                target = w[i + 1]
            if target and target != "|" and not target.startswith("&"):
                out.append((expand(target, cwd), how))
        if not w:
            continue
        j = 0
        while j < len(w) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[j]):
            j += 1
        if j >= len(w):
            continue
        verb = w[j]
        if verb == "sed" and any(x.startswith("-i") for x in w[j + 1:]):
            for x in w[j + 1:]:
                if not x.startswith("-") and not x.startswith("s") and "/" in x:
                    out.append((expand(x, cwd), "sed-i"))
        elif verb in _WRITE_VERBS:
            args = [x for x in w[j + 1:] if not x.startswith("-")]
            if verb in ("cp", "mv", "install") and args:
                out.append((expand(args[-1], cwd), verb))
                if verb == "mv":
                    for src in args[:-1]:
                        out.append((expand(src, cwd), "mv-out"))      # the SOURCE leaves its place: a deletion in disguise
            elif verb in ("tee", "rm", "touch", "truncate"):
                for x in args:
                    out.append((expand(x, cwd), verb))
    return [(p, how) for p, how in out if under(p, VAULT)]


def rule_bash_partition(cmd: str, inp: dict) -> str | None:
    cwd = inp.get("cwd")
    targets = bash_write_targets(cmd, cwd)
    if not targets:
        return None
    lane, prefixes, marker = lane_for(cwd)
    mode = partition_mode()
    sid = inp.get("session_id", "-")
    for p, how in targets:
        if how == "mv-out":
            rel = vault_rel(p) or ""
            return (f"Defaults never delete: `mv` moves `{rel}` OUT of its place in the vault — for every reader that is a deletion "
                    "(a wikilink, an @-import or a lane's partition now points at nothing). Move within the vault with `git mv` and a "
                    "path-limited commit, or ask. (Global/Nomos §Data integrity)")
    if lane is None and cwd and under(expand(cwd), VAULT):
        log("partition", f"ok\tVAULT-CWD\tbash\tsession={sid}")
        return None                                    # a session opened in the vault itself is the owner's own hand
    for p, how in targets:
        rel = vault_rel(p) or ""
        if how == "mv-out":
            return (f"Defaults never delete: `mv` moves `{rel}` OUT of its place in the vault — for every reader that is a deletion "
                    "(a wikilink, an @-import or a lane's partition now points at nothing). Move within the vault with `git mv` and a "
                    "path-limited commit, or ask. (Global/Nomos §Data integrity)")
        if rel.startswith("Concilium/") and p.stem in ROLE_STEMS:
            return f"Concilium STEM RULE: `{rel}` carries a vault role stem; refused (Bash write via {how})."
        kind = shared_surface(rel)
        if lane and path_in_partition(rel, prefixes) and kind not in ("umbrella-shared", "roster"):
            log("partition", f"ok\t{lane}\t{rel}\tbash={how}\tsession={sid}")     # the WARN week's denominator, Bash half
            continue
        if kind and how == "redirect-append":
            # a Bash `>>` cannot be checked for the row grammar and no chore runs on Bash, so it would land unchecked
            # and uncommitted (Caspar, closure round): shared-surface appends go through Edit/Write or ledger.py
            log("partition", f"{mode}\t{lane}\t{rel}\tshared={kind}\tbash-append-refused\tsession={sid}")
            if mode == "deny":
                return (f"`{rel}` is a SHARED surface ({kind}); a Bash `>>` append is not checked against its row grammar and "
                        "is never committed. Append with the Edit/Write tool (the door checks the row and the chore commits it), "
                        "or `gedaechtnis/ledger.py append` for the ledger.")
            continue
        log("partition", f"{mode}\t{lane or 'UNKNOWN-LANE'}\t{rel}\tbash={how}\tsession={sid}")
        if mode == "deny":
            return (f"Bash write ({how}) to `{rel}`, outside lane {lane or 'UNKNOWN'}'s partition. The partition door binds Bash "
                    "writes too: append a keyed row to a shared surface with the Edit/Write tool or `ledger.py`, or relay. (Speculum/Kernel 'Never write another lane's partition')")
    return None


def do_bash(inp: dict) -> None:
    cmd = (inp.get("tool_input") or {}).get("command") or ""
    cwd = inp.get("cwd")
    if not cmd:
        return
    for fn in (lambda: rule_vault_git(cmd, cwd), lambda: rule_launch_model(cmd),
               lambda: rule_data_integrity(cmd), lambda: rule_artifact_not_file(cmd, cwd),
               lambda: rule_bash_partition(cmd, inp)):
        r = fn()
        if r:
            log("deny", f"bash\t{r.split('.')[0][:80]}\t{cmd[:200].replace(chr(10),' ')}")
            if r.startswith(("Defaults never delete", "The Trash is never emptied", "Destructive SQL")):
                ask(EV, r)                            # data-destroying acts REFUSE AND ASK: the owner may still say yes
            else:
                deny(EV, r)
            return


# --------------------------------------------------------------------------- write hooks ----

def partition_mode() -> str:
    try:
        v = (STATE / "partition.mode").read_text(encoding="utf-8").strip().lower()
        return v if v in ("warn", "deny") else "warn"
    except OSError:
        return "warn"


def do_write(inp: dict) -> None:
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("notebook_path") or ""
    if not fp:
        return
    cwd = inp.get("cwd")
    p = expand(fp, cwd)
    if not under(p, VAULT):
        return
    rel = vault_rel(p) or ""
    # Concilium stem rule — a real refusal regardless of mode (Kernel: TRIP-WIRE kind ii)
    if rel.startswith("Concilium/") and p.stem in ROLE_STEMS:
        deny(EV, (f"Concilium STEM RULE: no file under ~/Atlas/Concilium/ may carry a vault role stem (`{p.stem}`) — it would "
                  "leak into kernel_freshness, the Lustrum arm and umbrella discovery. Use the Concilium-native names "
                  "(Fundamentum · Positio · Quaestiones · Vitia · Verba · Lex · Index). (Speculum/Kernel 'Standing constraints')"))
        return
    lane, prefixes, marker = lane_for(cwd)
    mode = partition_mode()
    sid = inp.get("session_id", "-")
    if lane is None and cwd and under(expand(cwd), VAULT):
        log("partition", f"ok\tVAULT-CWD\t{rel}\tsession={sid}")
        return                                   # a session opened in the vault itself is the owner's own hand
    if lane is None:
        log("partition", f"{mode}\tUNKNOWN-LANE\t{rel}\tcwd={cwd}\tsession={sid}")
        if mode == "deny":
            deny(EV, ("No `.atlas-lane` marker resolves from this cwd, so this session has NO declared write partition in "
                      "~/Atlas. Lane identity is DECLARED, never inferred: open the session in the repo that owns this "
                      "region, or relay through your outbox. (Speculum/Kernel 'Never write another lane's partition')"))
        return
    kind = shared_surface(rel)
    if path_in_partition(rel, prefixes) and kind not in ("umbrella-shared", "roster"):
        log("partition", f"ok\t{lane}\t{rel}\tsession={sid}")
        return
    if kind:
        ok, why = pure_append(kind, p, inp.get("tool_name") or "Edit", ti, lane)
        log("partition", f"{mode}\t{lane}\t{rel}\tshared={kind}\tappend={'ok' if ok else 'NO'}\t{why}\tsession={sid}")
        if ok:
            return                                   # the narrow audited exception: a keyed row, appended
        if mode == "deny":
            deny(EV, (f"`{rel}` is a SHARED surface ({kind}): any lane may APPEND a keyed row to it, nothing else — and this "
                      f"write is not a pure append ({why}). Append a row instead (queue: `- [ ] `q:…``; ledger: `gedaechtnis/ledger.py append`; "
                      "inbox: `- YYYY-MM-DD LANE …`)."))
        return
    log("partition", f"{mode}\t{lane}\t{rel}\tmarker={marker}\tsession={sid}")
    if mode == "deny":
        deny(EV, (f"`{rel}` is outside lane {lane}'s declared partition ({marker}). A defect in another lane's paths is a "
                  "briefing, not our edit: append a keyed row to a SHARED surface (its queue file, the Channels ledger, the "
                  "region kernel's Inbox) or write a notice in Channels/{lane}/. (Speculum/Kernel 'Never write another "
                  "lane's partition'; Global/Patterns 'A writer's PERMISSIONS must never decide a record's PLACEMENT')"))


# --------------------------------------------------------------------------- agent hook ----

def agent_definition_has_model(kind: str, cwd: str | None) -> bool:
    if not kind:
        return False
    cands = []
    if cwd:
        d = Path(cwd)
        for _ in range(8):
            cands.append(d / ".claude" / "agents" / f"{kind}.md")
            if d == d.parent or d == HOME:
                break
            d = d.parent
    cands.append(HOME / ".claude" / "agents" / f"{kind}.md")
    for c in cands:
        try:
            txt = c.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = txt.split("---", 2)
        if len(fm) >= 3 and re.search(r"^model:\s*\S+", fm[1], re.M):
            return True
        return False
    return False


def do_agent(inp: dict) -> None:
    ti = inp.get("tool_input") or {}
    kind = ti.get("subagent_type") or ""
    if kind == "fork":
        return                                   # forks always inherit the parent model by design
    if ti.get("model"):
        return
    if agent_definition_has_model(kind, inp.get("cwd")):
        return
    if not _cfg.flag("require_agent_model"):
        return                                   # a stranger's default: never deny a built-in agent on first use
    deny(EV, (f"This Agent call names no `model` and `{kind or 'general-purpose'}` has no `model:` in its definition, so it "
              "would inherit the session model — under Fable that is 2× Opus, silently. Pin `model: \"sonnet\"|\"opus\"|"
              "\"haiku\"` per the task→model table (judge-class = sonnet medium; build = opus medium). (Global/Map §Models)"))


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    inp = read_input()
    {"bash": do_bash, "write": do_write, "agent": do_agent}.get(which, lambda _i: None)(inp)


if __name__ == "__main__":
    guarded(main)
