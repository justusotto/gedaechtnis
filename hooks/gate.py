#!/usr/bin/env python3
"""gate.py — the PreToolUse locked doors of Das Gedächtnis.

    python3 gate.py bash    # vault git law · launch pins model · data integrity · artifact-not-file
    python3 gate.py write   # partition (WARN or DENY per state file) · Concilium stem rule
    python3 gate.py agent   # every Agent call pins a model unless its definition does

A rule is DETERMINISTIC or it is not here: anything needing judgment stays prose (Kernel, Nomos)
and is at most reported by a PostToolUse chore (see chore.py). Each deny cites the vault entry it
enforces so the model learns the why, not just the wall. First deny wins; silence means "no
opinion" and the normal permission flow continues.

Partition mode: `<state>/partition.mode` holds `warn` (default when absent) or `deny`. His ruling
2026-09-08: warn for one week, read the log, then flip the word.
"""
from __future__ import annotations
import re, sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (read_input, deny, log, expand, under, vault_rel, lane_for, path_in_partition,
                    VAULT, HOME, STATE, ROLE_STEMS, guarded, shared_surface, pure_append)

EV = "PreToolUse"

# ---------------------------------------------------------------- bash: segment the command ----

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\n|\|)\s*")
_GIT_C = re.compile(r"(?:^|\s)-C\s+(\S+)")


def segments(cmd: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(cmd) if s.strip()]


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
        if sub == "add" and re.search(r"(?:\s|^)(?:-A|--all|-a|-u|--update|\.)(?:\s|$)", seg[seg.index("add")+3:]):
            return ("Vault law: never `git add -A`/`-a`/`-u`/`.` in ~/Atlas — a broad add sweeps another lane's "
                    "in-flight work into your commit. Add the exact paths: `git -C ~/Atlas add -- <file>`. "
                    "(Speculum/Kernel 'Standing constraints'; Global/Errata 'A file written before a concurrent "
                    "automated committer runs…')")
        if sub == "commit":
            body = seg[seg.index("commit")+6:]
            if re.search(r"(?:\s|^)(?:-a|--all)(?:\s|$)", body):
                return "Vault law: never `git commit -a` in ~/Atlas. Commit path-limited: `git -C ~/Atlas commit -m '<msg>' -- <file>`."
            if "--amend" in body:
                return ("Vault law: NO-AMEND — never `git commit --amend` on an Atlas commit; forward-fix with a new commit. "
                        "(Speculum/Kernel 'Standing constraints')")
            m = re.search(r'-m\s+"([^"]*)"', body)
            if m and ("`" in m.group(1) or "$(" in m.group(1)):
                return ("A backtick or `$(` inside a DOUBLE-quoted `git commit -m` is command-substituted: the word vanishes "
                        "and the commit still succeeds, permanently (NO-AMEND). Use single quotes, or `git commit -F <msgfile>`. "
                        "(Global/Errata 'Backticks inside a DOUBLE-quoted git commit -m…')")
            has_pathspec = re.search(r"\s--(?:\s|$)", body) is not None
            assert_form = "diff --cached --name-only" in cmd
            if not has_pathspec and not assert_form and "-F" not in body and "--file" not in body:
                return ("Vault law: never a bare `git commit` in ~/Atlas — the pathspec is the guarantee. Use "
                        "`git -C ~/Atlas commit -m '<msg>' -- <file>`; or, ONLY for a `git rm --cached`, the stage→ASSERT "
                        "(`diff --cached --name-only`)→commit form in one invocation. (Speculum/Kernel 'Standing constraints')")
            if not has_pathspec and not assert_form and ("-F" in body or "--file" in body):
                return ("Vault law: `git commit -F <msg>` in ~/Atlas still needs the pathspec: append `-- <file>` "
                        "(everything after `--` is a pathspec), or use the stage→ASSERT→commit form.")
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
                    "silent routing authority — a bare launch once routed a seven-worker wave to Fable at 2× cost. "
                    "Add `--model <id> --effort <level>` from the queue row. (Global/Errata 'A settings-file model "
                    "default is silent routing authority…'; Global/Map §Models)")
        if "--effort" not in w and not any(x.startswith("--effort=") for x in w):
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
_DESTROY = re.compile(r"(?:^|\s)(?:rm|unlink|shred|rmdir)\s|\bfind\b.*\s-delete\b|\bgit\s+clean\b|>\s*\S*_cache\.json")


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
        for rx, why in _PROTECTED:
            if rx.search(seg):
                # allow rm inside the vault's gitignored scratch (.atlas-locks, .pre-* backups) explicitly
                if "vault" in why and re.search(r"Atlas/(?:\.atlas-locks|\.atlas-writer\.lock|[^\s]*\.pre-)", seg):
                    continue
                return (f"Defaults never delete: {why}. Deletions go to the Trash via Finder in one `Cleanup YYYY-MM-DD/` "
                        "bundle with a README, never `rm`; and this class asks Justus first. (Global/Nomos §Data integrity)")
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
                        "answer given there strands the work in his browser (it happened twice on 2026-09-07). Open the "
                        "artifact URL instead — look it up in ~/Atlas/Pharos/artifacts-index.md, or publish/republish via "
                        "the Artifact tool and `open` that URL.")
    return None


def do_bash(inp: dict) -> None:
    cmd = (inp.get("tool_input") or {}).get("command") or ""
    cwd = inp.get("cwd")
    if not cmd:
        return
    for fn in (lambda: rule_vault_git(cmd, cwd), lambda: rule_launch_model(cmd),
               lambda: rule_data_integrity(cmd), lambda: rule_artifact_not_file(cmd, cwd)):
        r = fn()
        if r:
            log("deny", f"bash\t{r.split('.')[0][:80]}\t{cmd[:200].replace(chr(10),' ')}")
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
    if lane is None:
        log("partition", f"{mode}\tUNKNOWN-LANE\t{rel}\tcwd={cwd}\tsession={sid}")
        if mode == "deny":
            deny(EV, ("No `.atlas-lane` marker resolves from this cwd, so this session has NO declared write partition in "
                      "~/Atlas. Lane identity is DECLARED, never inferred: open the session in the repo that owns this "
                      "region, or relay through your outbox. (Speculum/Kernel 'Never write another lane's partition')"))
        return
    if path_in_partition(rel, prefixes):
        return
    kind = shared_surface(rel)
    if kind:
        ok, why = pure_append(kind, p, inp.get("tool_name") or "Edit", ti)
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
    deny(EV, (f"This Agent call names no `model` and `{kind or 'general-purpose'}` has no `model:` in its definition, so it "
              "would inherit the session model — under Fable that is 2× Opus, silently. Pin `model: \"sonnet\"|\"opus\"|"
              "\"haiku\"` per the task→model table (judge-class = sonnet medium; build = opus medium). (Global/Map §Models)"))


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    inp = read_input()
    {"bash": do_bash, "write": do_write, "agent": do_agent}.get(which, lambda _i: None)(inp)


if __name__ == "__main__":
    guarded(main)
