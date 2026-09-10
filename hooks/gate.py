#!/usr/bin/env python3
"""gate.py — the PreToolUse locked doors of Das Gedächtnis.

    python3 gate.py bash    # vault git law · launch pins model · data integrity · artifact-not-file ·
                            # no whole-file overwrite (D1's bash half + shared-surface append-only)
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
from common import (read_input, deny, ask, allow, log, expand, under, vault_rel, lane_for, path_in_partition,
                    VAULT, HOME, STATE, ROLE_STEMS, guarded, shared_surface, pure_append,
                    note_pre_exists, clear_pre_exists, created_paths, take_filelock, release_filelock)
import fnmatch
import shlex
import config as _cfg
import names
import context_economy

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
            return "--" not in toks[i + 1:] and _breadth(toks[i + 1:]) or True
        if t in takes:
            i += 2; continue
        if t.startswith("-") or t == "HEREDOC":
            i += 1; continue
        return _breadth(toks[i:])          # a bare token — or a QUOTED path (`Q`) — is a pathspec
    return False


BREADTH = "BREADTH"


def _breadth(pathspecs):
    """`commit -m x .` (or `*`, `:/`) is path-limited only in name: `.` is every tracked modification,
    the exact breadth `add .` is refused for (council 2, Balthasar iteration 2). Returns BREADTH for
    that, True for a real pathspec."""
    for t in pathspecs:
        if t in (".", "*", ":/", "./"):
            return BREADTH
    return True


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
            has_pathspec = _trailing_pathspec(flags)
            if has_pathspec == BREADTH:
                return ("Vault law: `git commit … .` is every tracked change, the breadth `add .` is refused for. "
                        "Name the files: `git -C ~/Atlas commit -m '<msg>' -- <file>`.")
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
        if _cfg.flag("require_launch_model") and "--model" not in w and not any(x.startswith("--model=") for x in w):
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
    (re.compile(r"(?:~|/Users/[^/\s]+|\$HOME|\$\{HOME\})/" + re.escape(VAULT.name) + r"(?:/|\s|$)"
                + r"|" + re.escape(str(VAULT)) + r"(?:/|\s|$)"), "the vault and its history"),
]   # the vault is whatever config names — council 2 (Balthasar, closure) found the literal `/Atlas` here,
    # which left a stranger's ~/Gedaechtnis unprotected by the one rule that must hold everywhere
_VAULT_RX = re.escape(str(VAULT)) + r"(?:/|\s|$)"   # scope for the owner-class protections when protect_everywhere is off
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
        everywhere = _cfg.flag("protect_everywhere")
        for rx, why in _PROTECTED:
            if not everywhere and "vault" not in why and "Trash" not in why and not re.search(_VAULT_RX, seg):
                continue                              # a stranger's own `media/` or `*_cache.json` outside the vault is his to delete
            if rx.search(seg):
                # allow rm inside the vault's gitignored scratch (.atlas-locks, .pre-* backups) explicitly
                # the vault's name, not the literal `Atlas` — the same correction council 2 made to
                # _PROTECTED above: a stranger whose vault is `~/Gedaechtnis` gets its own scratch
                # carve-out, instead of being asked about every lock file it cleans up
                if "vault" in why and re.search(re.escape(VAULT.name) + r"/(?:\.atlas-locks|\.atlas-writer\.lock|[^\s]*\.pre-)", seg):
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


# --------------------------------------------------- the `# GENERATED` door (council 2 §1) ----
# A view under `.gedaechtnis/views/` is rebuilt from the log every time it is generated, so an edit
# made IN the view is gone at the next generation with no error anywhere — the exact silent-loss
# shape the log-and-views design exists to remove. The door turns that edit into a row instead.
#
# It is keyed on the FILE'S OWN FIRST LINE, never on a directory: a generated file says so about
# itself, so the rule needs no allowlist and cannot go stale when the views move (their final home
# is a later ruling). During the shadow nothing outside `.gedaechtnis/views/` carries the line, so
# nothing outside it can be bitten — which is what makes an additive shadow additive.
#
# Balthasar's sign-off names this door the most likely thing to be reverted; the signal he asked for
# is more than 3 refusals in the shadow's first week. Every refusal is logged as `generated-view`.

GENERATED_PREFIX = "# GENERATED"


def is_generated(p: Path) -> bool:
    """True when the file exists and its FIRST LINE marks it as machine-written."""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readline().startswith(GENERATED_PREFIX)
    except OSError:
        return False


def generated_refusal(rel: str, how: str) -> str:
    return (f"`{rel}` is a GENERATED view: its first line is `{GENERATED_PREFIX} sha256:…`, and it is rebuilt from the "
            f"memory log every time `gedaechtnis/views.py` runs — this {how} would be gone at the next generation, with "
            "no error anywhere. Append a row instead:\n"
            "    python3 gedaechtnis/logstore.py append --region <Region> --kind <decision|lesson|state|question|note> "
            "--stem <Canon|Errata|Patterns|Position|Aporia> --heading '## …' --body '…'\n"
            "then re-run `python3 gedaechtnis/views.py`. To change an entry the log already carries, append the corrected "
            "row — the log is append-only and the newer row wins. (Council 2 closure §1: a hand edit becomes a row, never "
            "a lost edit.)")


_WRITE_VERBS = {"tee", "cp", "mv", "rm", "touch", "truncate", "install"}
_CLOBBER = {"redirect": "shell redirect", "redirect-append": "shell append", "sed-i": "`sed -i`",
            "tee": "`tee`", "cp": "`cp` over it", "mv": "`mv` over it", "install": "`install` over it",
            "truncate": "`truncate`"}


def rule_generated_view_bash(cmd: str, cwd: str | None) -> str | None:
    for p, how in bash_write_targets(cmd, cwd):
        if how in _CLOBBER and is_generated(p):
            return generated_refusal(vault_rel(p) or str(p), _CLOBBER[how])
    return None


def bash_write_targets(cmd: str, cwd: str | None) -> list[tuple[Path, str]]:
    """Heuristic: vault paths a Bash command writes. (path, how) — how in redirect-append · redirect ·
    sed-i · tee · tee-append · cp · mv · mv-out · rm · touch · truncate · install · dd.

    `tee` is split into `tee`/`tee-append` here (rather than left as one "verb" bucket) because the
    two need OPPOSITE treatment downstream: `tee -a` is a pure append like `>>`, plain `tee` truncates
    like `>` — a caller that cannot tell them apart cannot refuse the second without also refusing
    the first. `dd of=` is handled on its own because its target is a `key=value` argument, not a
    trailing bare token or a `>`-spelled redirect."""
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
        elif verb == "dd":
            for x in w[j + 1:]:
                if x.startswith("of="):
                    out.append((expand(x[len("of="):], cwd), "dd"))
        elif verb in _WRITE_VERBS:
            args = [x for x in w[j + 1:] if not x.startswith("-")]
            if verb in ("cp", "mv", "install") and args:
                out.append((expand(args[-1], cwd), verb))
                if verb == "mv":
                    for src in args[:-1]:
                        out.append((expand(src, cwd), "mv-out"))      # the SOURCE leaves its place: a deletion in disguise
            elif verb == "tee":
                how = "tee-append" if any(x in ("-a", "--append") for x in w[j + 1:]) else "tee"
                for x in args:
                    out.append((expand(x, cwd), how))
            elif verb in ("rm", "touch", "truncate"):
                for x in args:
                    out.append((expand(x, cwd), verb))
    return [(p, how) for p, how in out if under(p, VAULT)]


# ------------------------------------------ D1's bash half: no whole-file overwrite (WP9 gap 1) ----
# `Write` has an anchor-free sibling in Bash: a truncating redirect (`>`, not `>>`), `tee` without
# `-a`, `cp`/`mv`/`install` ONTO an existing target, `truncate`, and `dd of=` all replace a file's
# entire content with NO compare-and-swap at all — worse than `Write`, which at least carries the
# session's own belief about what it is replacing (Write still gets D1's own check; this is the
# same guard for the shapes Write cannot reach). So this binds every vault `.md` file — own lane or
# not — and is MODE-INDEPENDENT exactly like D1 itself (Design §5.2 D1): it is a data-loss guard,
# not a partition rule, and `partition.mode` governs who may write WHERE, never whether a write may
# erase what is already there. Same exemptions as D1: a file that does not exist, an empty file, a
# non-.md file, anything under `Cleanup */`, and a file THIS session created (the identical
# created-set D1 reads — no second bookkeeping channel).
#
# A shared-surface `.md` file (a region's queue, the fleet roster, an Inbox, `artifacts-index.md`,
# the umbrella-shared Canon/Position pair) gets a DIFFERENT wording naming the append-only rule,
# because that is what a model needs to hear even when it is the file's OWN declaring lane (WP9 gap
# 2): `path_in_partition` says "yours", but a single-FILE partition entry among a directory-prefix
# entry means "yours to APPEND to", not "yours to replace" — Edit/Write already draw this line via
# `pure_append`; this is the same line for Bash, which `pure_append` never sees.
#
# `sed -i` is deliberately NOT in the truncating set below: unlike `>`, it does not replace the
# whole file by construction — an ordinary `s/a/b/` or line-targeted edit leaves the rest of the
# file untouched, and a syntactic guess at "does this script empty the file" would either miss real
# wipes or refuse ordinary edits that happen to match the guess. `sed -i` stays covered by D2's
# mutex only, exactly as before this rule existed — a narrower, deliberate scope call, not an
# oversight (see the report for this change).

_BASH_TRUNCATING_HOWS = {"redirect", "tee", "cp", "mv", "install", "truncate", "dd"}


def rule_bash_no_whole_file_write(cmd: str, cwd: str | None, sid: str) -> str | None:
    for p, how in bash_write_targets(cmd, cwd):
        if how not in _BASH_TRUNCATING_HOWS or p.suffix != ".md":
            continue
        try:
            if not p.is_file() or p.stat().st_size == 0:
                continue
        except OSError:
            continue
        rel = vault_rel(p) or ""
        if not rel or _under_cleanup(rel) or rel in created_paths(sid):
            continue
        kind = shared_surface(rel)
        if kind:
            return (f"`{rel}` is a SHARED surface ({kind}): rows are atomic single-Edit appends, never a "
                     "read-modify-write (Speculum/Kernel 'Queue-file custody'; QCUSTODY-1 for a region queue). "
                    f"Bash may only APPEND to it (`>>`) — `{how}` truncates the whole file with no anchor. Use "
                     "Edit/Write for a checked keyed-row append instead.")
        return (f"Bash write ({how}) truncates `{rel}`, an existing non-empty vault file, with no anchor: another "
                "session's content since your last read is gone with no record. Use Edit — its anchor is checked "
                "against the file as it is now. Whole-file overwrite is never allowed unless this session created "
                "the file. (Design §5.2 D1; new, empty, non-.md and Cleanup files are exempt; `>>` is unaffected.)")
    return None


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
        if how not in ("mv-out", "rm"):
            r = rule_display_name_filename(p)      # deterministic, like the stem rule: every mode, every lane
            if r:
                return r + f" (Bash write via {how}.)"
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
    # D2's Bash half. A shell redirect is a whole-file overwrite with no anchor, so it belongs
    # inside the same per-file mutex as Edit/Write; `chore.py bash` drops these when the command
    # returns, and a lock older than LOCK_TTL is taken over exactly as it is on the Edit path.
    for p, how in targets:
        if how not in ("redirect", "redirect-append", "sed-i", "tee", "tee-append") or p.suffix != ".md":
            continue
        ok, age, holder = take_filelock(p, sid)
        if not ok:
            log("deny", f"bash\tfilelock\t{vault_rel(p)}\theld-by={holder}\tage={age:.1f}s\tsession={sid}")
            return (f"Another session is editing `{vault_rel(p)}` right now (lock {int(age)}s old); retry the same edit in a "
                    "moment — it will re-read the file. (Design §5.2 D2)")
    return None


def do_bash(inp: dict) -> None:
    cmd = (inp.get("tool_input") or {}).get("command") or ""
    cwd = inp.get("cwd")
    if not cmd:
        return
    sid = inp.get("session_id", "-")
    for fn in (lambda: rule_vault_git(cmd, cwd), lambda: rule_launch_model(cmd),
               lambda: rule_data_integrity(cmd), lambda: rule_artifact_not_file(cmd, cwd),
               lambda: rule_generated_view_bash(cmd, cwd),
               lambda: rule_bash_no_whole_file_write(cmd, cwd, sid),
               lambda: rule_bash_partition(cmd, inp)):
        r = fn()
        if r:
            log("deny", f"bash\t{r.split('.')[0][:80]}\t{cmd[:200].replace(chr(10),' ')}")
            if r.startswith(("Defaults never delete", "The Trash is never emptied", "Destructive SQL")):
                ask(EV, r)                            # data-destroying acts REFUSE AND ASK: the owner may still say yes
            else:
                deny(EV, r)
            return
    # No door fired: a non-blocking context-economy notice, if this command reads a single file
    # via cat/head/tail/sed -n (context_economy.py). Always ALLOW — see that module's docstring.
    note = context_economy.bash_notice(inp)
    if note:
        allow(EV, note)


# ------------------------------------------------- the display-name-as-filename door (§6.3) ----
# The display layer shows `Canon.md` as "Decisions". A model that reads "write it to Decisions"
# may create `Decisions.md`, and then the region has two files for one role, neither of which any
# consumer of ROLE_STEMS can see. The door is deterministic, so it refuses in every partition mode
# — like the Concilium stem rule — and only for a file that does not exist yet: an existing
# `Decisions.md` is somebody's data, and this door never touches data.


def _fold(s: str) -> str:
    """`open-questions` · `Open_Questions` · `OPEN QUESTIONS` all fold to `open questions`."""
    return re.sub(r"\s+", " ", s.replace("_", " ").replace("-", " ")).strip().casefold()


_DISPLAY_TO_STEM: dict | None = None


def display_to_stem() -> dict:
    """{folded display name (every language column) → the stem it names}."""
    global _DISPLAY_TO_STEM
    if _DISPLAY_TO_STEM is None:
        m = {}
        for stem in names.stems():
            for lang in ("en", "de"):
                d = names.display(stem, lang)
                if d:
                    m.setdefault(_fold(d), stem)
        _DISPLAY_TO_STEM = m
    return _DISPLAY_TO_STEM


def rule_display_name_filename(p: Path) -> str | None:
    """Deny reason for creating a vault `.md` file named after a display name, else None."""
    if p.suffix != ".md" or p.exists():
        return None
    # Concilium is exempt: its files are named by the Concilium-native table, and `Index` — the
    # native name of `Concilium/<Entity>/Index.md` — is also Map's display name. Refusing it here
    # would deny the correct file name in the one place the vault requires it. The Concilium STEM
    # rule still refuses `Concilium/<Entity>/Map.md`, so the two doors do not overlap.
    if (vault_rel(p) or "").startswith("Concilium/"):
        return None
    stem = p.stem
    # a real role stem is never denied, whatever the display table says: `Patterns` is both a stem
    # and its own display name, and `Inbox` is a stem the chores create.
    if stem.casefold() in {s.casefold() for s in (set(ROLE_STEMS) | set(names.stems()))}:
        return None
    folded = _fold(stem)
    if folded not in {_fold(d) for d in names.all_display_names()}:
        return None
    target = display_to_stem().get(folded)
    if not target:
        return None                              # a display name we cannot resolve names no file to point at
    rel_dir = vault_rel(p.parent)
    where = f"{rel_dir}/{target}.md" if rel_dir and rel_dir != "." else f"{target}.md"
    return (f'The file is `{target}.md` (shown as "{names.display(target)}"); display names are never file names — '
            f"every consumer of the vault's role stems looks for `{target}.md` and would never see `{p.name}`. "
            f"Write to `{where}`.")


# --------------------------------------------------------------------------- write hooks ----

def partition_mode() -> str:
    try:
        v = (STATE / "partition.mode").read_text(encoding="utf-8").strip().lower()
        return v if v in ("warn", "deny") else "warn"
    except OSError:
        return "warn"


# ------------------------------------------------------ D1: no whole-file Write (DESIGN §5.2) ----
# `Edit` is a compare-and-swap: its anchor is matched against the file as it is at the moment of
# the edit, so an edit against a paragraph another session changed FAILS instead of clobbering.
# A whole-file `Write` has no anchor at all — it replaces the file from the session's stale
# reading, and every line a sibling added since that read is gone with no error anywhere. So the
# door refuses that one shape, and the refusal names the tool that does the same job safely.
#
# It binds prose memory files and NOTHING else. Exempt, each for its own reason:
#   · a file that does not exist     — nothing to lose
#   · an empty file                  — same
#   · a file that is not `.md`       — a generator writing an HTML page or a verdict JSON into the
#                                      vault is not editing memory and is never refused ([R3])
#   · anything under `Cleanup */`    — a cleanup bundle is written whole, by construction ([R3])
#   · a file THIS session created    — there is no other session's content in it to lose
#   · a verified pure append to a SHARED surface — the append rule re-reads the file HERE and
#     refuses unless the write extends what is on disk NOW, which is the same guarantee D1 asks
#     of Edit. Without this the door would silently retract the keyed-row affordance §5.2 keeps.


def _under_cleanup(rel: str) -> bool:
    """Any ancestor directory named `Cleanup *` — the bundle convention, at any depth."""
    return any(fnmatch.fnmatch(part, "Cleanup *") for part in Path(rel).parts[:-1])


def rule_no_whole_file_write(tool: str, ti: dict, p: Path, rel: str, sid: str, lane: str | None) -> str | None:
    if tool != "Write":
        return None                                  # Edit / MultiEdit / NotebookEdit are anchored
    if p.suffix != ".md" or not p.is_file():
        return None
    try:
        if p.stat().st_size == 0:
            return None
    except OSError:
        return None
    if _under_cleanup(rel) or rel in created_paths(sid):
        return None
    kind = shared_surface(rel)
    if kind:
        ok, _why = pure_append(kind, p, "Write", ti, lane)
        if ok:
            return None
    return (f"Whole-file Write to `{rel}` refused: another session may have changed this file since you read it. "
            "Use Edit — its anchor is checked against the file as it is now. "
            "(Design §5.2 D1; new, empty, non-.md and Cleanup files are exempt.)")


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
    note_pre_exists(p)      # the only moment anything can still tell a CREATION from an edit (chore.py reads this back)
    # Concilium stem rule — a real refusal regardless of mode (Kernel: TRIP-WIRE kind ii)
    if rel.startswith("Concilium/") and p.stem in ROLE_STEMS:
        clear_pre_exists(p)                      # a refused write leaves no record of itself
        deny(EV, (f"Concilium STEM RULE: no file under ~/Atlas/Concilium/ may carry a vault role stem (`{p.stem}`) — it would "
                  "leak into kernel_freshness, the Lustrum arm and umbrella discovery. Use the Concilium-native names "
                  "(Fundamentum · Positio · Quaestiones · Vitia · Verba · Lex · Index). (Speculum/Kernel 'Standing constraints')"))
        return
    # the `# GENERATED` door — deterministic, so it refuses in every partition mode, like the stem rule
    if is_generated(p):
        log("deny", f"write\tgenerated-view\t{rel}\tsession={inp.get('session_id', '-')}")
        clear_pre_exists(p)                      # a refused write leaves no record of itself
        deny(EV, generated_refusal(rel, f"`{inp.get('tool_name') or 'Edit'}`"))
        return
    r = rule_display_name_filename(p)
    if r:
        log("deny", f"write\tdisplay-name-filename\t{rel}")
        clear_pre_exists(p)                      # a refused write leaves no record of itself
        deny(EV, r)
        return
    lane, prefixes, marker = lane_for(cwd)
    mode = partition_mode()
    sid = inp.get("session_id", "-")

    # D2 — take the per-file mutex, then D1. Every refusal from here on RELEASES the lock first:
    # the tool call is not going to happen, so holding the file for the next ten seconds would
    # block a sibling for a write that never occurred. `NotebookEdit` is the one tool whose lock
    # the chore cannot release (hooks.json has no PostToolUse for it, and it carries
    # `notebook_path` rather than `file_path`), so a notebook's lock ages out at LOCK_TTL instead —
    # the same fail-safe that covers a hook that dies, and the reason there has to be one.
    ok, age, holder = take_filelock(p, sid)
    if not ok:
        log("deny", f"write\tfilelock\t{rel}\theld-by={holder}\tage={age:.1f}s\tsession={sid}")
        clear_pre_exists(p)                      # a refused write leaves no record of itself
        deny(EV, (f"Another session is editing `{rel}` right now (lock {int(age)}s old); retry the same edit in a moment — "
                  "it will re-read the file. (Design §5.2 D2)"))
        return

    def refuse(reason: str) -> None:
        # A refused write leaves nothing behind: not the mutex (a sibling would wait ten seconds
        # for a write that never happened) and not the pre-exists marker (a later chore would read
        # it as a creation by this session and let D1 wave the overwrite through).
        release_filelock(p, sid)
        clear_pre_exists(p)
        deny(EV, reason)

    d1 = rule_no_whole_file_write(inp.get("tool_name") or "", ti, p, rel, sid, lane)
    if d1:
        log("deny", f"write\twhole-file-write\t{rel}\tsession={sid}")
        refuse(d1)
        return

    if lane is None and cwd and under(expand(cwd), VAULT):
        log("partition", f"ok\tVAULT-CWD\t{rel}\tsession={sid}")
        return                                   # a session opened in the vault itself is the owner's own hand
    if lane is None:
        log("partition", f"{mode}\tUNKNOWN-LANE\t{rel}\tcwd={cwd}\tsession={sid}")
        if mode == "deny":
            refuse(("No `.atlas-lane` marker resolves from this cwd, so this session has NO declared write partition in "
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
            refuse((f"`{rel}` is a SHARED surface ({kind}): any lane may APPEND a keyed row to it, nothing else — and this "
                      f"write is not a pure append ({why}). Append a row instead (queue: `- [ ] `q:…``; ledger: `gedaechtnis/ledger.py append`; "
                      "inbox: `- YYYY-MM-DD LANE …`)."))
        return
    log("partition", f"{mode}\t{lane}\t{rel}\tmarker={marker}\tsession={sid}")
    if mode == "deny":
        refuse((f"`{rel}` is outside lane {lane}'s declared partition ({marker}). A defect in another lane's paths is a "
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


def do_read(inp: dict) -> None:
    """PreToolUse Read: a non-blocking context-economy notice, or nothing (context_economy.py)."""
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ""
    if not fp:
        return
    p = expand(fp, inp.get("cwd"))
    partial = ti.get("offset") is not None or ti.get("limit") is not None
    note = context_economy.check_read(inp.get("session_id", "-"), p, partial=partial)
    if note:
        allow(EV, note)


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    inp = read_input()
    {"bash": do_bash, "write": do_write, "agent": do_agent, "read": do_read}.get(which, lambda _i: None)(inp)


if __name__ == "__main__":
    guarded(main)
