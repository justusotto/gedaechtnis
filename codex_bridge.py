#!/usr/bin/env python3
"""Codex bridge — make the memory plugin's doors and boot work under OpenAI's Codex CLI.

WHY THIS EXISTS
---------------
Codex ships a hook system whose wire format is the same shape as Claude Code's
(event -> matcher group -> handlers; `hook_event_name` on stdin; exit 2 blocks with the
reason on stderr). Three things nevertheless stop a Claude plugin from simply working
there, and all three fail SILENTLY -- nothing errors, the session just has no memory and
no doors:

  1. Codex reads `AGENTS.md` but does NOT expand `@/path` imports. A boot file that is a
     chain of imports loads as literal text. Verified by probe, both directions (a nonce
     behind an import is invisible; the same nonce inline is returned) -- including inside
     a TRUSTED project with repo-root-relative imports.
  2. `plugin_hooks` is `removed` in Codex's feature table: a plugin may ship skills, MCP
     servers and apps, but NOT hooks. Hooks must exist as a `hooks.json` config file.
  3. Tool names differ. A matcher of `Bash` never fires under Codex, whose shell tool is
     `unified_exec` and whose edit tool is `apply_patch`. A translated hooks.json with
     Claude matchers is well-formed, loads without error, and matches nothing.

This module is the generic half of the fix: it GENERATES the Codex-side artifacts from the
plugin's own `hooks.json` and the host's own boot chain, so neither is hand-kept and both
pick up changes automatically. It is vendor-neutral and path-agnostic -- every host path is
an argument, nothing here knows about any particular vault, user or repo.

IDEMPOTENCE IS LOad-BEARING, NOT POLITENESS
-------------------------------------------
Codex requires PERSISTED HOOK TRUST: its own UI says "1 hook is new or changed / Hooks need
review / Continue without trusting (hooks won't run)". A generator that rewrites its output
on every run would re-arm that prompt every session and, until someone clicked it, the doors
would be OFF. So `write_if_changed()` compares bytes and does not touch the file when the
content is unchanged. Any future edit to this module must preserve that property; the test
suite asserts it.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# --- 1. events -------------------------------------------------------------------

# Confirmed present in the Codex binary's own hook schema (0.154.0-alpha.6.2).
CODEX_EVENTS = frozenset((
    "PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd",
    "Stop", "SubagentStart", "SubagentStop", "PreCompact", "PostCompact",
    "Interrupt", "PermissionRequest",
))

# Events a Claude plugin may register that Codex has no counterpart for. Dropped with a
# recorded reason rather than silently -- an unexplained absence is how this class of bug
# survives.
UNSUPPORTED_EVENTS = {
    "WorktreeCreate": "Codex has no worktree lifecycle event (`worktrees` is experimental).",
    "WorktreeRemove": "Codex has no worktree lifecycle event (`worktrees` is experimental).",
    "Notification": "Codex has no Notification hook event; `notify` is a separate mechanism.",
}

# --- 2. tool-name matchers -------------------------------------------------------

# Claude tool name -> the Codex tool(s) that do the same job.
#
# MEASURED, NOT INFERRED. The Codex binary's model catalogue calls its shell `unified_exec`,
# which makes `Bash -> unified_exec` the obvious mapping. It is WRONG, and a live probe said
# so: Codex normalises its hook payload to Claude's vocabulary, reporting
#
#     PreToolUse  tool_name='Bash'         tool_input={'command': 'echo hi'}
#
# byte-identical to Claude Code. A `unified_exec` matcher therefore matches nothing and the
# shell door silently never fires -- which is exactly what the first version of this table
# did. `unified_exec` is the INTERNAL shell type, never the hook-facing name.
#
# The edit tool is the opposite case: it really is renamed, AND its payload changes shape.
#
#     PreToolUse  tool_name='apply_patch'  tool_input={'command': '*** Begin Patch ...'}
#
# There is no `file_path`, no `old_string`, no `content` -- the whole edit is one patch
# envelope in a `command` field. Any door that reads `tool_input["file_path"]` sees nothing
# and allows the write. See `parse_apply_patch()` / `normalize_tool_input()` below, which is
# why the mapping is safe to make at all.
TOOL_MAP = {
    "Bash": ["Bash"],                     # identity -- measured, see above
    "Edit": ["apply_patch"],
    "Write": ["apply_patch"],
    "MultiEdit": ["apply_patch"],
    "NotebookEdit": ["apply_patch"],
    "Agent": ["Agent"],
    "Task": ["Task"],
}
UNMAPPABLE_TOOLS = {
    "Artifact": "Codex has no Artifact tool; owner-facing pages stay on the Claude side.",
}

# The only events whose `matcher` names a TOOL. Everywhere else a matcher means something
# else entirely (SessionStart's session source) or nothing at all.
TOOL_MATCHED_EVENTS = frozenset(("PreToolUse", "PostToolUse", "PermissionRequest"))


def translate_matcher(matcher: str) -> tuple[str | None, list[str]]:
    """Translate a Claude tool-name matcher into a Codex one.

    Returns (codex_matcher_or_None, notes). A matcher is an alternation of tool names
    (`"Edit|Write|MultiEdit"`); each alternative is mapped independently, duplicates are
    collapsed, and order is made deterministic so the output is byte-stable.
    """
    notes: list[str] = []
    if not matcher:
        return "", notes  # empty matcher = all tools, same meaning in both harnesses
    out: list[str] = []
    for part in (p.strip() for p in matcher.split("|")):
        if not part:
            continue
        if part in TOOL_MAP:
            out.extend(TOOL_MAP[part])
        elif part in UNMAPPABLE_TOOLS:
            notes.append(f"dropped matcher {part!r}: {UNMAPPABLE_TOOLS[part]}")
        else:
            # An unknown tool name is passed through unchanged: it may be an MCP tool, which
            # is named the same on both sides. Recorded so it is reviewable.
            out.append(part)
            notes.append(f"matcher {part!r} passed through unmapped (unknown tool).")
    if not out:
        return None, notes
    seen: dict[str, None] = {}
    for t in out:
        seen.setdefault(t, None)
    return "|".join(seen), notes


# --- 3. the hooks.json translation ------------------------------------------------

# Two forms occur in the wild: the variable QUOTED (`"${CLAUDE_PLUGIN_ROOT}"/hooks/x.py`,
# which is how the plugin writes it) and bare. The quoted form needs the quotes MOVED rather
# than merely substituted: a naive replace yields `"/root"/hooks/x.py`, which a shell still
# parses correctly but which stops being one quoted token -- so a plugin root containing a
# space would split. Capturing the trailing path run and re-quoting the whole thing keeps it
# a single token, and keeps the output idiomatic enough to read in a diff.
_PLUGIN_ROOT_QUOTED_RE = re.compile(r'"\$\{CLAUDE_PLUGIN_ROOT\}"(\S*)')
_PLUGIN_ROOT_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}|\$CLAUDE_PLUGIN_ROOT")


def _rewrite_command(cmd: str, plugin_root: str) -> str:
    """Codex does not set CLAUDE_PLUGIN_ROOT, so bake the absolute path in."""
    root = plugin_root.rstrip("/")
    cmd = _PLUGIN_ROOT_QUOTED_RE.sub(lambda m: '"' + root + m.group(1) + '"', cmd)
    return _PLUGIN_ROOT_RE.sub(root, cmd)


_HOOK_SCRIPT_RE = re.compile(r'("?)([^"\s]*/hooks/)([A-Za-z0-9_]+\.py)\1')


def _route_through_shim(cmd: str, plugin_root: str) -> str | None:
    """Rewrite `... /hooks/<door>.py <args>` to run via `codex_shim.py <door>.py <args>`.

    Returns None when the command does not look like a plugin door, so the caller can keep
    the original rather than mangle something it did not understand.
    """
    m = _HOOK_SCRIPT_RE.search(cmd)
    if not m or m.group(3) == "codex_shim.py":
        return None
    shim = f'{m.group(1)}{m.group(2)}codex_shim.py{m.group(1)}'
    return cmd[:m.start()] + shim + " " + m.group(3) + cmd[m.end():]


def translate_hooks(hooks_obj: dict, plugin_root: str) -> tuple[dict, list[str]]:
    """Translate a Claude-plugin hooks.json object into a Codex hooks.json object.

    Pure: no I/O, no environment reads. Returns (codex_obj, notes) where `notes` records
    every dropped event and matcher with its reason.
    """
    notes: list[str] = []
    src = (hooks_obj or {}).get("hooks") or {}
    out: dict[str, list] = {}

    for event in sorted(src):                       # sorted => byte-stable output
        groups = src[event] or []
        if event in UNSUPPORTED_EVENTS:
            notes.append(f"dropped event {event!r}: {UNSUPPORTED_EVENTS[event]}")
            continue
        if event not in CODEX_EVENTS:
            notes.append(f"dropped event {event!r}: not in this Codex build's hook schema.")
            continue
        new_groups = []
        for group in groups:
            raw_matcher = group.get("matcher", "")
            if event not in TOOL_MATCHED_EVENTS:
                # A matcher only names a TOOL on PreToolUse/PostToolUse. On SessionStart it
                # names the session source (`startup|resume|clear|compact`); on Stop and the
                # rest it is meaningless. Translating those as tool names would emit a
                # matcher that matches no Codex tool, and the hook would simply never fire --
                # well-formed, silent, and wrong. Drop the matcher so the hook runs on every
                # occurrence of the event, which is what the Claude side means anyway.
                if raw_matcher:
                    notes.append(f"[{event}] dropped non-tool matcher {raw_matcher!r} "
                                 f"(matchers name tools only on PreToolUse/PostToolUse).")
                matcher, mnotes = "", []
            else:
                matcher, mnotes = translate_matcher(raw_matcher)
            notes.extend(f"[{event}] {n}" for n in mnotes)
            if matcher is None:
                continue                            # every alternative was unmappable
            # An apply_patch payload is not Claude-shaped (no `file_path`), so those
            # handlers are routed through the shim, which normalises before the door runs.
            # Measured: without it the door sees no path and ALLOWS the write, logging
            # nothing -- proven with a control pair against the real gate.
            needs_shim = "apply_patch" in (matcher or "")
            handlers = []
            for h in group.get("hooks") or []:
                if h.get("type") != "command":
                    notes.append(f"[{event}] dropped handler of type {h.get('type')!r}.")
                    continue
                nh = dict(h)
                cmd = _rewrite_command(h.get("command", ""), plugin_root)
                if needs_shim:
                    cmd = _route_through_shim(cmd, plugin_root)
                    if cmd is None:
                        notes.append(f"[{event}] handler not routable through the shim; "
                                     f"kept unnormalised: {h.get('command','')!r}")
                        cmd = _rewrite_command(h.get("command", ""), plugin_root)
                nh["command"] = cmd
                handlers.append(nh)
            if not handlers:
                continue
            ng: dict = {}
            if matcher:
                ng["matcher"] = matcher
            ng["hooks"] = handlers
            new_groups.append(ng)
        if new_groups:
            out[event] = new_groups
    return {"hooks": out}, notes


# --- 4. the boot flattener --------------------------------------------------------

_IMPORT_RE = re.compile(r"^@(\S+)\s*$")


def flatten_imports(root: Path, max_bytes: int | None = None) -> tuple[str, dict]:
    """Resolve a CLAUDE.md-style `@`-import chain into one self-contained document.

    Transitive, depth-first, in file order. A file already inlined is not inlined twice
    (a cycle would otherwise hang); the repeat is replaced by a one-line marker so the
    elision is visible rather than silent. A missing target is likewise marked, never
    skipped -- a boot file that quietly lost a section is the exact failure this whole
    module exists to prevent.

    Returns (text, stats) with stats: files, missing, cycles, bytes.
    """
    seen: set[Path] = set()
    stats = {"files": [], "missing": [], "cycles": [], "bytes": 0}

    def resolve(target: str, base: Path) -> Path:
        p = Path(os.path.expanduser(target))
        if not p.is_absolute():
            p = (base.parent / p)
        return Path(os.path.normpath(str(p)))

    def inline(path: Path, depth: int) -> list[str]:
        rp = Path(os.path.normpath(str(path)))
        if rp in seen:
            stats["cycles"].append(str(rp))
            return [f"<!-- gedaechtnis: already inlined above: {rp} -->"]
        if not rp.is_file():
            stats["missing"].append(str(rp))
            return [f"<!-- gedaechtnis: MISSING import target: {rp} -->"]
        seen.add(rp)
        stats["files"].append(str(rp))
        lines: list[str] = [f"<!-- gedaechtnis: begin {rp} -->"]
        for line in rp.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _IMPORT_RE.match(line.strip())
            if m and depth < 12:
                lines.extend(inline(resolve(m.group(1), rp), depth + 1))
            else:
                lines.append(line)
        lines.append(f"<!-- gedaechtnis: end {rp} -->")
        return lines

    text = "\n".join(inline(Path(root), 0)) + "\n"
    stats["bytes"] = len(text.encode("utf-8"))
    if max_bytes is not None and stats["bytes"] > max_bytes:
        stats["over_budget"] = True
    return text, stats


# --- 5. writing, idempotently -----------------------------------------------------

def write_if_changed(path: Path, content: str) -> bool:
    """Write only when the bytes differ. Returns True if the file was written.

    See the module docstring: under Codex a changed hooks file re-arms the trust prompt and
    the doors stay OFF until a human clicks it. Not touching an unchanged file is therefore
    a correctness property of this bridge, not an optimisation.
    """
    path = Path(path)
    new = content.encode("utf-8")
    if path.exists():
        try:
            if path.read_bytes() == new:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(new)
    os.replace(tmp, path)
    return True


# --- 6. the boot facts line -------------------------------------------------------

def boot_facts(agents_md: Path, marker: str = "<!-- gedaechtnis: begin ") -> dict:
    """Describe what a generated boot file actually carries.

    `ok` is False when the file is absent, empty, or carries no inlined section -- the three
    shapes a silently-empty Codex boot takes. The caller is expected to fail LOUD on
    `ok=False`: a boot with no memory that says nothing is how this bug lasted a day.
    """
    p = Path(agents_md)
    if not p.is_file():
        return {"ok": False, "why": "boot file does not exist", "path": str(p),
                "bytes": 0, "sections": 0}
    raw = p.read_bytes()
    n = raw.count(marker.encode("utf-8"))
    if not raw:
        return {"ok": False, "why": "boot file is empty", "path": str(p),
                "bytes": 0, "sections": 0}
    if n == 0:
        return {"ok": False, "why": "boot file carries no inlined sections "
                                    "(imports were not expanded)",
                "path": str(p), "bytes": len(raw), "sections": 0}
    return {"ok": True, "why": "", "path": str(p), "bytes": len(raw), "sections": n}


def facts_line(facts: dict) -> str:
    if facts["ok"]:
        return (f"memory boot: {facts['bytes']:,} B across {facts['sections']} inlined "
                f"section(s) -> {facts['path']}")
    return (f"MEMORY BOOT EMPTY -- {facts['why']} ({facts['path']}). "
            f"This session has NO memory loaded. Regenerate the boot file before trusting "
            f"anything it appears to remember.")


# --- 7. one-call generation -------------------------------------------------------

def generate(plugin_hooks_json: Path, plugin_root: Path, boot_source: Path,
             codex_home: Path) -> dict:
    """Regenerate both Codex-side artifacts. Returns a report dict; writes nothing else."""
    report: dict = {"hooks_written": False, "boot_written": False, "notes": [],
                    "boot": {}, "errors": []}
    try:
        hooks_obj = json.loads(Path(plugin_hooks_json).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        report["errors"].append(f"cannot read plugin hooks.json: {e}")
        return report
    codex_hooks, notes = translate_hooks(hooks_obj, str(plugin_root))
    report["notes"] = notes
    report["hooks_written"] = write_if_changed(
        Path(codex_home) / "hooks.json",
        json.dumps(codex_hooks, indent=2, ensure_ascii=False) + "\n")
    if Path(boot_source).is_file():
        text, stats = flatten_imports(Path(boot_source))
        report["boot_written"] = write_if_changed(Path(codex_home) / "AGENTS.md", text)
        report["boot"] = stats
    else:
        report["errors"].append(f"boot source not found: {boot_source}")
    return report


# --- 8. the apply_patch payload gap ----------------------------------------------
#
# Codex's edit tool does not send the fields a Claude-shaped door parses. Measured:
#
#     tool_name  = 'apply_patch'
#     tool_input = {'command': '*** Begin Patch\n*** Add File: probe_target.txt\n+HELLO\n*** End Patch'}
#
# A door that reads `tool_input["file_path"]` finds nothing. The dangerous part is the
# FAILURE MODE: it does not crash, it finds no path to object to and ALLOWS the write. A
# door that cannot see its subject is worse than an absent one, because the absent one does
# not appear in a report as working. So the envelope is parsed back into the fields the
# doors already understand, before they ever see it.

_PATCH_FILE_RE = re.compile(
    r"^\*\*\*\s+(Add|Update|Delete)\s+File:\s*(.+?)\s*$", re.MULTILINE)
_PATCH_MOVE_RE = re.compile(r"^\*\*\*\s+Move\s+to:\s*(.+?)\s*$", re.MULTILINE)


def parse_apply_patch(command: str) -> list[dict]:
    """Extract the files an apply_patch envelope touches.

    Returns a list of {"op": "Add"|"Update"|"Delete", "path": str} in envelope order, plus
    a synthetic Update entry for any `*** Move to:` destination (a rename writes BOTH paths,
    and a door that only saw the source would miss the one being created).

    Unparseable input yields [] -- and callers must treat [] from a NON-EMPTY command as
    "unknown", never as "touches nothing". `normalize_tool_input` does exactly that.
    """
    out: list[dict] = []
    for op, path in _PATCH_FILE_RE.findall(command or ""):
        out.append({"op": op, "path": path})
    for dest in _PATCH_MOVE_RE.findall(command or ""):
        out.append({"op": "Update", "path": dest})
    return out


def normalize_tool_input(inp: dict) -> dict:
    """Give a Codex hook payload the shape a Claude-shaped door already parses.

    Only `apply_patch` is rewritten; every other tool arrives Claude-identical (measured --
    `Bash` sends `{'command': ...}` on both sides) and is returned untouched, so this is safe
    to run unconditionally.

    The rewritten payload carries `file_path` (the FIRST file, which is what single-file
    doors read) and `_codex_files` (all of them, for a door that wants the full set). It also
    sets `_codex_unparsed` when a non-empty envelope yielded no files, so a door can REFUSE
    on the unknown rather than wave it through -- the whole point of doing this at all.
    """
    if not isinstance(inp, dict):
        return inp
    if inp.get("tool_name") != "apply_patch":
        return inp
    ti = inp.get("tool_input")
    if not isinstance(ti, dict):
        return inp
    cmd = ti.get("command") or ""
    files = parse_apply_patch(cmd)
    new_ti = dict(ti)
    new_ti["_codex_files"] = files
    if files:
        new_ti["file_path"] = files[0]["path"]
    elif cmd.strip():
        new_ti["_codex_unparsed"] = True
    out = dict(inp)
    out["tool_input"] = new_ti
    # Present the tool under a name the existing doors branch on, keeping the original so
    # nothing downstream loses the fact that this came from Codex.
    out["_codex_tool_name"] = "apply_patch"
    out["tool_name"] = "Write" if files and files[0]["op"] == "Add" else "Edit"
    return out


# --- 9. agent definitions ---------------------------------------------------------
#
# Claude restricts a subagent by an ALLOWLIST of tools (`tools: Read, Glob, Grep`). Codex has
# no such concept, and the desktop import wizard therefore drops the key entirely: an agent
# that was read-only BY CONSTRUCTION becomes read-only only in so far as its prompt asks
# nicely. `cursus-adviser`'s translated definition still tells it "You never write... no
# Bash, so you cannot commit" -- a sentence that was true of the harness and is now merely a
# promise.
#
# Codex's agent format does have the right primitive: `sandbox_mode`. This maps the allowlist
# onto it, so the constraint is enforced again rather than requested. `model` is mapped too --
# the wizard dropped that as well, silently collapsing per-agent cost tiering onto whatever the
# session happens to be running.

WRITE_TOOLS = frozenset(("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Agent", "Task"))


def agent_sandbox_mode(tools: list[str] | None) -> str | None:
    """`read-only` when the agent's allowlist contains no tool that can change anything.

    Returns None when there is no allowlist at all -- an agent that never declared one was
    never restricted, and inventing a restriction here would change behaviour rather than
    preserve it.
    """
    if not tools:
        return None
    return "workspace-write" if (set(tools) & WRITE_TOOLS) else "read-only"


def parse_agent_frontmatter(text: str) -> dict:
    """Minimal YAML-frontmatter reader for a Claude agent definition.

    Deliberately not a YAML parser: these files use a flat `key: value` frontmatter, and
    depending on PyYAML would make the plugin's hooks carry a third-party import.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out: dict = {}
    key = None
    for line in text[3:end].splitlines():
        if not line.strip():
            continue
        if line[:1] not in (" ", "\t") and ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            out[key] = val.strip()
        elif key:                                   # folded continuation of the previous key
            out[key] = (out[key] + " " + line.strip()).strip()
    if "tools" in out:
        out["tools"] = [t.strip() for t in out["tools"].split(",") if t.strip()]
    return out


def agent_overlay(agent_md: str) -> dict:
    """The keys a translated Codex agent TOML should carry but is missing.

    Returns only what should be ADDED/CORRECTED -- the caller merges, so a hand-edit to any
    other key in the TOML survives regeneration.
    """
    fm = parse_agent_frontmatter(agent_md)
    out: dict = {}
    sb = agent_sandbox_mode(fm.get("tools"))
    if sb:
        out["sandbox_mode"] = sb
    # `model` is deliberately NOT carried across. A Claude definition names a Claude model
    # ("sonnet", "opus", "fable"); writing that into a Codex agent TOML pins a model id that
    # does not exist on the other vendor. Restoring per-agent tiering needs a deliberate
    # cross-vendor model map, which is a routing decision for the owner, not something this
    # translator should invent. Until that map exists an agent inherits the session model --
    # the same behaviour the import wizard produced, but now for a stated reason.
    if fm.get("model"):
        out["_dropped_model"] = fm["model"]
    return out
