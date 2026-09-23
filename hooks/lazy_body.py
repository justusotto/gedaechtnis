#!/usr/bin/env python3
"""lazy_body.py — a region's PULL-ONLY bodies, loaded the first time a session touches it.

★ THE GAP THIS CLOSES. A graduated region boots with its Boot file and nothing else: the
Boot file is an INDEX, and the bodies it points at (Status, Decisions, Patterns, Mistakes,
Rules …) are pull-only by design. That is what makes a boot chain affordable. It is also a
hole — a session that spends an hour writing in a region never loads what that region
already learned, and "consult the record before re-deriving" is a sentence in a file
nobody opened.

★ WHY PATH PREFIX AND NOTHING ELSE. Three prior arms in this package tried to choose
memory by CONTENT — term overlap, citation matching, boot-structure ranking — and all
three were refused by their own gates (ROW-LESSONPUSH-1/2/3). Every surveyed system that
does this successfully gates on a PATH: Cursor's Auto Attached globs, Windsurf's Glob
rules. A path prefix is decidable, has no ranking, and cannot be wrong about relevance in
the way a matcher can — if the session is writing in the region, the region's record
applies. This arm makes no judgment at all; it answers "which region is this path in".

Three things it deliberately does NOT do:

  * **It never fires for a region with no Boot file.** Such a region's bodies are still in
    the boot chain, and injecting them again would charge the session twice for what it
    already has.
  * **It never replaces the Boot file.** Every NEVER/ALWAYS rule stays always-loaded, so a
    session that touches nothing still boots with the rules. This arm gates the BODY tier
    only — which is the one mitigation that makes lazy loading safe at all.
  * **It says nothing when it cannot record that it spoke.** A dedupe that fails to write
    its state would inject the same bodies after every tool call. Silence is the correct
    failure, and `_claim` returns False rather than raising.

Read-only toward the vault. No writes outside this package's own state directory.
"""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path

import common
import config
import limits
from common import log

# ★ TWO LISTS, ONE JOB EACH — and the split is not cosmetic. The first draft expressed the
# exclusions TWICE: a SKIP set with the reasons written next to it, and a BODY_ORDER tuple
# that simply left the same three stems out. Mutating the SKIP set came back GREEN, because
# nothing ever reached it — the tuple was the real filter and the documented decision was
# dead code. The candidate set is now DERIVED (every role stem, minus the skips) and
# BODY_ORDER only sorts, so each list is reachable and each is mutation-provable.

# Role stems that ARE candidates and are nonetheless never injected:
#   Kernel  — the Boot file; the session already has it, by definition of this arm.
#   Ethos   — a Global-only role, loaded at boot wherever it exists.
# `Inbox` belongs on this list by intent and is NOT on it: it is not a role stem, so it is
# excluded BY CONSTRUCTION and an entry here would be a second dead guard. The construction
# is what `test_the_inbox_is_never_injected` asserts — if a vault ever adds `Inbox` to
# `ROLE_STEMS`, that control reddens and this comment is where the reader lands.
SKIP_STEMS = frozenset(("Kernel", "Ethos"))

# The order bodies are offered in when the budget cannot hold them all. Not alphabetical
# and not the role taxonomy's own order: this is the order in which a session about to
# WRITE in a region needs them — where the region stands, what is already decided, what
# has already gone wrong, what the rules are, and only then the descriptive material. A
# role stem missing from this tuple is NOT dropped; it sorts to the tail, so a vault that
# adds an on-demand role gets it late rather than never.
BODY_ORDER = ("Position", "Canon", "Errata", "Nomos", "Patterns", "Course", "Aporia",
              "Eidos", "Map", "Vision", "Lexicon", "Apparatus", "Praxis", "Exempla",
              "Annales")


def body_stems() -> list:
    """Every role stem this arm may inject, in BODY_ORDER, unlisted stems at the tail."""
    rank = {s: i for i, s in enumerate(BODY_ORDER)}
    stems = [s for s in common.ROLE_STEMS if s not in SKIP_STEMS]
    return sorted(stems, key=lambda s: (rank.get(s, len(BODY_ORDER)), s))


def enabled() -> bool:
    return bool(limits.get("lazy_bodies_enabled", False))


def budget() -> int:
    try:
        return max(0, int(limits.get("lazy_body_max_bytes", 60000) or 0))
    except (TypeError, ValueError):
        return 0


def has_boot_file(region: str) -> bool:
    """A region is GRADUATED when it carries a Boot file.

    This is the gate that stops the arm charging twice: an ungraduated region's bodies are
    what the repo's own CLAUDE.md still imports, so the session booted with them."""
    if not region:
        return False
    try:
        return (config.vault() / region / "Kernel.md").is_file()
    except OSError:
        return False


def bodies_for(region: str) -> list:
    """The region's pull-only body files, in BODY_ORDER, each as (stem, Path, bytes).

    A file is a body when its stem is a role stem, it is not in SKIP_STEMS, and it is a
    real file — `body_stems()` is the single place that decision is made. Archives (`Position-archive.md`) are NOT bodies: they are pull-only by
    their own rule and are the thing a body points AT when it has been compacted."""
    d = config.vault() / region
    out = []
    for stem in body_stems():
        p = d / f"{stem}.md"
        try:
            if p.is_file():
                out.append((stem, p, p.stat().st_size))
        except OSError:
            continue
    return out


# ------------------------------------------------------------------ per-session state ----
# The shape `lesson_push._claim_boot` uses, with a REGION key instead of a single flag. Its
# own file: a dedupe set has no business adding fields to the session-start record, which
# three other hooks read for keys they own.

def _state_path(sid: str) -> Path:
    return config.state() / f"lazy-body-{common.safe_sid(sid)}.json"


def _load(sid: str) -> dict:
    try:
        doc = json.loads(_state_path(sid).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def shown(sid: str) -> list:
    """The regions already injected for this session. The negative control reads this."""
    got = _load(sid).get("regions")
    return list(got) if isinstance(got, list) else []


def _claim(sid: str, region: str) -> bool:
    """True EXACTLY ONCE per (session, region), and only when the claim reached disk.

    Check-and-take under ONE lock, never a read followed by a write: AGENTFANOUT-1
    measured the two-section form overrunning its cap in 2 of 5 trials at three
    concurrent calls, and PostToolUse arms run concurrently in exactly that way."""
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        lock = config.state() / f"lazy-body-{common.safe_sid(sid)}.lock"
        with open(lock, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                doc = _load(sid)
                got = doc.get("regions")
                got = list(got) if isinstance(got, list) else []
                if region in got:
                    return False
                got.append(region)
                doc["regions"] = got
                _state_path(sid).write_text(json.dumps(doc, indent=1), encoding="utf-8")
                return True
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    except OSError as e:
        # A claim that cannot be recorded must not speak: it would re-inject after every
        # tool call for the rest of the session.
        log("lazy_body", f"claim\t{sid}\t{region}\t{e}")
        return False


# ------------------------------------------------------------------ the injection ----

def _compose(region: str, picked: list, skipped: list) -> str:
    head = [
        f"Region {region} — its pull-only bodies, because this session just touched a path "
        f"under it. Loaded once for this region; later touches are silent.",
        f"The Boot file ({region}/Kernel.md) you booted with is an INDEX. What follows is "
        f"what it points at. Consult it before re-deriving something this region already "
        f"settled.",
    ]
    parts = ["\n".join(head)]
    for stem, p, _n in picked:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:                                   # pragma: no cover - raced deletion
            parts.append(f"--- {region}/{stem}.md — unreadable ({e}) ---")
            continue
        parts.append(f"--- {region}/{stem}.md ---\n{text.rstrip()}")
    if skipped:
        named = ", ".join(f"{region}/{s}.md ({n:,} B)" for s, _p, n in skipped)
        parts.append(
            f"NOT loaded, over the {budget():,} B budget for one region: {named}. "
            f"Read the file directly if the work needs it — it is on disk, not missing.")
    return "\n\n".join(parts)


def notice(sid: str, paths, cwd: str | None) -> str | None:
    """The bodies of every not-yet-injected graduated region these paths fall in.

    `paths` is whatever the calling chore has — one Path, or the list a Bash command
    wrote. Returns None when there is nothing to say, which is the common case: the
    session is working in a region it already loaded."""
    if not enabled():
        return None
    cap = budget()
    if cap <= 0:
        return None
    if isinstance(paths, (str, Path)):
        paths = [paths]
    seen, blocks = [], []
    for p in paths or []:
        try:
            region = _region_of(Path(p), cwd)
        except (OSError, ValueError):
            continue
        # `seen` is an EFFICIENCY guard with NO behavioural control, and that is recorded
        # here rather than covered by a test that could not fail: removing it leaves all 21
        # controls green, because `_claim` already refuses the second visit to a region. What
        # it saves is real but invisible — a Bash command that writes twenty files in one
        # region would otherwise stat every role stem twenty times. Do not write a control
        # for it; a test that cannot distinguish its presence from its absence is decoration.
        if not region or region in seen:
            continue
        seen.append(region)
        if not has_boot_file(region):
            continue
        bodies = bodies_for(region)
        if not bodies:
            continue
        # Claimed BEFORE the text is built, and only when there is something to build:
        # a claim taken for a region with no bodies would silence a later, real touch.
        if not _claim(sid, region):
            continue
        picked, skipped, spent = [], [], 0
        for stem, path, n in bodies:
            if spent + n <= cap:
                picked.append((stem, path, n))
                spent += n
            else:
                skipped.append((stem, path, n))
        if not picked:
            # Every body is individually over the budget. Saying which files exist and how
            # big they are is still worth more than silence.
            skipped = bodies
        blocks.append(_compose(region, picked, skipped))
        log("lazy_body", f"inject\t{sid}\t{region}\tpicked={len(picked)}\t"
                         f"skipped={len(skipped)}\tbytes={spent}")
    return "\n\n".join(blocks) if blocks else None


def _region_of(p: Path, cwd: str | None):
    """The region a touched path belongs to, in a vault of EITHER shape.

    ★ FOUND BY THE DOOR-LEVEL TEST, NOT BY THE MODULE ONE. `lesson_push.region_for` answers
    a vault path with its FIRST SEGMENT — right for a flat vault (`<Region>/Position.md`)
    and wrong for a nested one (`<Umbrella>/<Region>/Position.md`), where it names the
    umbrella. Every region in a nested vault would have resolved to a folder with no Boot
    file, so the arm would have been silent for the whole population it was built for and
    the module suite, whose fixture is flat, was green throughout. A fixture cannot contain
    the conventions of the system it is a fixture for.

    So the vault case is resolved HERE, longest form first, and only a candidate that is
    really a graduated region is accepted. The REPO case still delegates: `region_of_repo`
    already returns `<Umbrella>/<Region>` and is the resolution REGIONWT-1 hardened."""
    rel = common.vault_rel(p)
    if rel:
        parts = rel.split("/")
        tops = config.non_region_tops()
        if parts and (parts[0].startswith(".") or parts[0] in tops):
            return None
        # Two segments before one: an umbrella that is ITSELF a graduated region is legal,
        # but a nested region must win over its umbrella or it can never be reached.
        for n in (2, 1):
            if len(parts) > n:
                cand = "/".join(parts[:n])
                if has_boot_file(cand):
                    return cand
        return None
    import lesson_push
    return lesson_push.region_for(p, cwd)


# ================================================================== facets ====
# KERNELFACET-1. A region's Boot file carries every rule that binds before a session knows its
# task. Some rules bind only when the work touches one surface — a local API, a template folder, a
# database file — and a Boot file that carries them charges every session for the few that need
# them. A FACET is such a rule set, kept in its own file beside the Boot file:
#
#     <Region>/Kernel.md             the Boot file, always loaded
#     <Region>/Kernel-<name>.md      a facet, loaded once, the first time its trigger fires
#
# ★ THE TRIGGER LIVES IN THE FACET FILE, NEVER IN THIS CODE. The file's own frontmatter names what
# loads it, so the person who writes the rules also writes when they apply, and this package ships
# no knowledge of any vault's surfaces:
#
#     ---
#     tools: [mcp__anki__*]              tool-name globs (fnmatch)
#     bash: ['localhost:8765', 'anki_backup\.py']   regexes searched in a Bash command
#     paths: ['templates/**', '*.css']   globs matched against a touched path — vault-relative,
#                                        repo-relative and absolute forms are all tried; `*` crosses `/`
#     rows: ['q:CD-*']                   globs matched against the q-ids a prompt names
#     ---
#
# A prompt that carries `facet:<name>` also loads that facet (the row-tag trigger: a work-queue
# row names the facet it needs, and the opener that cites the row carries the tag).
#
# ★ WHY A TRIGGER AND NOT A POINTER. A rule a session must remember to open is a suggestion: the
# pointer is read, the file is not. Injection happens on the tool call or the prompt, never on the
# session's judgment.
#
# Shared with the body arm above, on purpose: region resolution (`_region_of`), the graduated-region
# gate (`has_boot_file`), the per-session state file and its lock, the log discipline. What is NOT
# shared is the on/off switch — facets are on by default (`facets_enabled`), because a region with
# no facet file injects nothing (one log row per session says so), and a region whose facets have
# all loaded costs one directory listing per tool call.
#
# Every injection is one row in `facets.log`:  <ts> inject <sid> <region>/<facet> <kind>:<detail> <bytes>
# and a candidate region with no facets gets one `none` row per session, so "did it fire" and "was
# there anything to fire" are both a log read. `scripts/facet_measure.py`-style readers depend on
# exactly these columns.

import fnmatch
import re
import shlex

FACET_FILE = re.compile(r"^Kernel-([a-z0-9][a-z0-9_-]{0,39})\.md$")
FACET_TAG = re.compile(r"(?<![\w-])facet:([a-z0-9][a-z0-9_-]{0,39})(?![\w-])")
Q_ID = re.compile(r"q:[A-Z]{2,}-\d{4}-\d{2}-\d{2}-[A-Z0-9-]+")
TRIGGER_KEYS = ("tools", "bash", "paths", "rows")
FACTS_LINE_MAX = 120
PATHY_EXT = re.compile(r"\.[A-Za-z0-9]{1,10}$")
# The most path-shaped tokens one Bash command contributes. A generated command can carry
# thousands; the first 200 are what a person wrote on purpose, and the cost stays bounded.
BASH_PATHS_MAX = 200


def facets_enabled() -> bool:
    return bool(limits.get("facets_enabled", True))


def facet_budget() -> int:
    try:
        return max(0, int(limits.get("facet_max_bytes", 24000) or 0))
    except (TypeError, ValueError):
        return 0


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def _split_flow(inner: str) -> list:
    """Split a flow list's inside on commas that are NOT inside quotes. A regex such as `a{1,3}`
    or a glob with a comma survives when quoted, and backslashes are kept as written."""
    items, cur, q = [], [], None
    for ch in inner:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur.append(ch)
        elif ch == ",":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    return [i.strip() for i in items if i.strip()]


def parse_frontmatter(text: str) -> tuple:
    """(fields, body). `fields` maps each TRIGGER_KEYS key present to a list of strings.

    Not a YAML parser: the four keys take a flow list (`key: [a, 'b']`), a block list
    (`key:` then `  - a` lines) or one scalar. Anything else in the frontmatter is ignored, so a
    vault's own display fields (aliases, tags) can sit beside the triggers. CRLF line endings are
    read as LF: otherwise a file saved by a Windows editor would parse to NO triggers and never fire,
    silently."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    head = text[4:end]
    rest = text[end + 4:]
    body = rest[1:] if rest.startswith("\n") else rest
    fields, key = {}, None
    for line in head.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t") or line.lstrip().startswith("- "):
            item = line.strip()
            if key and item.startswith("- "):
                fields[key].append(_unquote(item[2:]))
            continue
        k, sep, v = line.partition(":")
        k = k.strip()
        key = k if (sep and k in TRIGGER_KEYS) else None
        if key is None:
            continue
        v = v.strip()
        fields[key] = []
        if v.startswith("[") and v.endswith("]"):
            fields[key] = [x for x in (_unquote(i) for i in _split_flow(v[1:-1])) if x]
        elif v:
            fields[key] = [_unquote(v)]
    return fields, body


def facets_of(region: str) -> list:
    """The facet files beside a GRADUATED region's Boot file, each as a dict.

    A region without a Boot file has no facets by definition: a facet is what a Boot file sheds,
    and an ungraduated region's bodies are all still in the boot chain."""
    if not region or not has_boot_file(region):
        return []
    d = config.vault() / region
    out = []
    try:
        names = sorted(p.name for p in d.iterdir())
    except OSError:
        return []
    for n in names:
        m = FACET_FILE.match(n)
        if not m:
            continue
        p = d / n
        try:
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fields, body = parse_frontmatter(text)
        bash = []
        for pat in fields.get("bash", []):
            try:
                bash.append(re.compile(pat))
            except re.error as e:
                log("facets", f"bad-regex\t{region}/{m.group(1)}\t{pat}\t{e}")
        out.append({"region": region, "name": m.group(1), "path": p, "body": body,
                    "tools": fields.get("tools", []), "bash": bash,
                    "paths": fields.get("paths", []), "rows": fields.get("rows", [])})
    return out


def _path_forms(p: Path, cwd) -> list:
    """Every form a path glob may be written against: vault-relative, repo-relative, absolute."""
    forms = []
    rel = common.vault_rel(p)
    if rel:
        forms.append(rel)
    try:
        repo = common.repo_root_of(p)
        if repo:
            forms.append(str(p.resolve().relative_to(repo.resolve())))
    except (OSError, ValueError):
        pass
    forms.append(str(p))
    return forms


def bash_paths(cmd: str, cwd) -> list:
    """Every token of a Bash command that looks like a path, expanded against cwd.

    Wider than `lesson_push.bash_targets` on purpose: that list is what a command WRITES, and a
    facet's surface is also what it reads or opens (`sqlite3 <db>`, `python3 scripts/x.py`)."""
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        toks = cmd.split()
    out, seen = [], set()
    for t in toks:
        if "=" in t and "/" not in t.split("=", 1)[0]:
            t = t.split("=", 1)[1]
        # A token is path-shaped when it has a separator or ends in a file extension — never merely
        # because it contains a dot: a heredoc of prose would otherwise hand every "word." to
        # region resolution, on a hook that runs after every tool call.
        if not t or t.startswith("-") or t in seen or not ("/" in t or PATHY_EXT.search(t)):
            continue
        seen.add(t)
        try:
            out.append(common.expand(t, cwd))
        except (OSError, ValueError):
            continue
        if len(out) >= BASH_PATHS_MAX:
            break
    return out


def _match(f: dict, tool: str, cmd: str, paths: list, cwd) -> "str | None":
    """The trigger that fires facet `f` on this tool call, as `<kind>:<detail>`, or None."""
    for pat in f["tools"]:
        if tool and fnmatch.fnmatchcase(tool, pat):
            return f"tool:{pat}"
    if cmd:
        for rx in f["bash"]:
            if rx.search(cmd):
                return f"bash:{rx.pattern}"
    for p in paths:
        forms = _path_forms(p, cwd)
        for pat in f["paths"]:
            if any(fnmatch.fnmatchcase(s, pat) for s in forms):
                return f"path:{pat}"
    return None


def _match_prompt(f: dict, prompt: str, cwd=None) -> "str | None":
    for m in FACET_TAG.finditer(prompt or ""):
        if m.group(1) == f["name"]:
            return f"row:facet:{f['name']}"
    ids = Q_ID.findall(prompt or "")
    for pat in f["rows"]:
        for q in ids:
            if fnmatch.fnmatchcase(q, pat):
                return f"row:{q}"
    for q in ids:
        if f["name"] in _row_tags(q, cwd):
            return f"row:{q}"
    return None


def _row_tags(qid: str, cwd=None) -> set:
    """The `facet:<name>` tags on the queue row whose anchor is `qid`, when this vault has queues.

    Anchored on the ROW — the checkbox line that carries the id in backticks — never on the id's
    first occurrence: rows cite each other, and the citing row's tags are not the cited row's.

    ★ ONLY THE QUEUE FILES THIS SESSION'S LANE DECLARES. The queue folder is shared by every region,
    and a `facet:<name>` tag on another region's row is that region's business: read from here it
    would load THIS region's same-named facet for work nobody scoped it for (found by the row's
    reviewer). A session with no lane marker declares no queue file, so it reads none — the prompt
    tag and the facet's own `rows:` globs still work for it."""
    qd = config.queues_dir()
    if not qd:
        return set()
    try:
        _lane, prefixes, _m = common.lane_for(cwd)
    except Exception:                                   # noqa: BLE001 - a hook never crashes a session
        prefixes = []
    owned = [p for p in prefixes if p]
    if not owned:
        return set()
    anchor = re.compile(r"^- \[[ xX]\] `" + re.escape(qid) + r"`")
    tags = set()
    try:
        files = sorted(qd.glob("*.md"))
    except OSError:
        return set()
    for f in files:
        rel = common.vault_rel(f) or ""
        if not any(rel == p.rstrip("/") or rel.startswith(p if p.endswith("/") else p + "/")
                   for p in owned):
            continue
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if anchor.match(line):
                    tags.update(FACET_TAG.findall(line))
        except OSError:
            continue
    return tags


def _facet_claimed(sid: str, key: str) -> bool:
    got = _load(sid).get("facets")
    return isinstance(got, list) and key in got


def _claim_facet(sid: str, key: str) -> bool:
    """True EXACTLY ONCE per (session, key). The body arm's lock, a separate list in its file."""
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        lock = config.state() / f"lazy-body-{common.safe_sid(sid)}.lock"
        with open(lock, "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                doc = _load(sid)
                got = doc.get("facets")
                got = list(got) if isinstance(got, list) else []
                if key in got:
                    return False
                got.append(key)
                doc["facets"] = got
                _state_path(sid).write_text(json.dumps(doc, indent=1), encoding="utf-8")
                return True
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    except OSError as e:
        log("lazy_body", f"facet-claim\t{sid}\t{key}\t{e}")
        return False


def _compose_facet(f: dict, trigger: str) -> str:
    body = f["body"].strip()
    head = (f"Facet {f['region']}/{f['name']} — rules for this kind of work, loaded because "
            f"this session matched {trigger}. Loaded once per session; the region's Boot file "
            f"still applies.")
    cap = facet_budget()
    if cap and len(body.encode("utf-8")) > cap:
        return (head + f"\n\nNOT loaded: {f['region']}/Kernel-{f['name']}.md is "
                f"{len(body.encode('utf-8')):,} B, over the {cap:,} B facet budget. Read it "
                f"directly before continuing this work.")
    return f"{head}\n\n--- {f['region']}/Kernel-{f['name']}.md ---\n{body}"


def _session_regions(cwd, paths) -> list:
    """The regions whose facets this call may fire: the cwd's region, then each touched path's."""
    out = []
    # The cwd is a DIRECTORY, and `_region_of` answers for a path INSIDE a region — a cwd that is
    # the region folder itself (`<vault>/Studio/Cards`) has no segment below the region and would
    # resolve to nothing. A child name stands in for "something in this directory".
    cands = ([Path(cwd) / "_"] if cwd else []) + list(paths or [])
    by_dir = {}                    # two paths in one directory are always in one region
    for p in cands:
        d = str(Path(p).parent)
        if d not in by_dir:
            try:
                by_dir[d] = _region_of(Path(p), cwd)
            except (OSError, ValueError):
                by_dir[d] = None
        r = by_dir[d]
        if r and r not in out:
            out.append(r)
    return out


def any_facets() -> bool:
    """Does ANY region of this vault carry a facet file? The fast exit for the common case: a vault
    with none pays two directory listings per tool call and resolves no region at all."""
    v = config.vault()
    for pat in ("*/Kernel-*.md", "*/*/Kernel-*.md"):
        try:
            if any(FACET_FILE.match(p.name) for p in v.glob(pat)):
                return True
        except OSError:
            continue
    return False


def _nothing_to_fire(sid: str) -> None:
    """One `none` row per session for a vault with no facet anywhere, so the measurement still
    counts the session (region `*`)."""
    if not _facet_claimed(sid, "none:*") and _claim_facet(sid, "none:*"):
        log("facets", f"none\t{sid}\t*\t-\t0")


def _field(s: str) -> str:
    """A log column: a tab or newline inside a trigger pattern would split the row, and
    `facet_measure.py` would drop it as malformed — the injection would vanish from every count."""
    return re.sub(r"[\t\r\n]", " ", s)


def _fire(sid: str, regions: list, matcher) -> "str | None":
    blocks = []
    claimed = None
    for region in regions:
        # EFFICIENCY guard, no behavioural control (the claim refuses a second load anyway): once
        # every facet of a region has loaded — the steady state of a session that uses them — a
        # directory listing answers, and no facet file is read, parsed or compiled again.
        try:
            names = [m.group(1) for m in (FACET_FILE.match(n) for n in os.listdir(config.vault() / region)) if m]
        except OSError:
            names = []
        if names:
            if claimed is None:
                got = _load(sid).get("facets")
                claimed = set(got) if isinstance(got, list) else set()
            if all(f"{region}/{n}" in claimed for n in names):
                continue
        facets = facets_of(region)
        if not facets:
            key = f"none:{region}"
            # `_facet_claimed` first is an EFFICIENCY guard with no behavioural control (the claim
            # refuses a second `none` anyway): it spares a lock-and-write on every tool call in a
            # region with no facets. Removing it changes no output; do not write a test for it.
            if not _facet_claimed(sid, key) and _claim_facet(sid, key):
                log("facets", f"none\t{sid}\t{region}\t-\t0")
            continue
        for f in facets:
            key = f"{region}/{f['name']}"
            # No "already claimed?" pre-check here: `_claim_facet` is the ONE dedupe, so a mutation
            # of it reddens the once-per-session control instead of hiding behind a second copy.
            trig = matcher(f)
            if not trig:
                continue
            if not _claim_facet(sid, key):
                continue
            text = _compose_facet(f, trig)
            blocks.append(text)
            log("facets", f"inject\t{_field(sid)}\t{_field(key)}\t{_field(trig)}\t{len(text.encode('utf-8'))}")
    return "\n\n".join(blocks) if blocks else None


def facet_notice(sid: str, tool: str, tool_input: dict, cwd) -> "str | None":
    """The facets a tool call fires, composed, or None. The PostToolUse door calls this."""
    if not facets_enabled():
        return None
    if not any_facets():
        _nothing_to_fire(sid)
        return None
    ti = tool_input if isinstance(tool_input, dict) else {}
    cmd = (ti.get("command") or "") if tool == "Bash" else ""
    paths = []
    fp = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
    if fp:
        try:
            paths.append(common.expand(fp, cwd))
        except (OSError, ValueError):
            pass
    if cmd:
        paths.extend(bash_paths(cmd, cwd))
    regions = _session_regions(cwd, paths)
    return _fire(sid, regions, lambda f: _match(f, tool, cmd, paths, cwd))


def facet_prompt_notice(sid: str, prompt: str, cwd) -> "str | None":
    """The facets a prompt's row tags fire (UserPromptSubmit). Only the cwd's region is a
    candidate: a prompt touches no path."""
    if not facets_enabled():
        return None
    if not any_facets():
        _nothing_to_fire(sid)
        return None
    regions = _session_regions(cwd, [])
    return _fire(sid, regions, lambda f: _match_prompt(f, prompt, cwd))


def describe(f: dict) -> str:
    """One line naming a facet and what loads it, capped at FACTS_LINE_MAX characters."""
    trig = []
    for k in TRIGGER_KEYS:
        vals = [rx.pattern for rx in f[k]] if k == "bash" else f[k]
        if vals:
            trig.append(f"{k} " + ", ".join(vals))
    trig.append(f"tag facet:{f['name']}")
    size = len(f["body"].encode("utf-8"))
    line = f"- Facet {f['region']}/{f['name']} ({size:,} B), loads on: " + "; ".join(trig)
    return line if len(line) <= FACTS_LINE_MAX else line[:FACTS_LINE_MAX - 1] + "…"


def facts_lines(cwd) -> list:
    """The SessionStart lines: one per facet of the session's region, nothing when it has none."""
    if not facets_enabled() or not cwd:
        return []
    return [describe(f) for r in _session_regions(cwd, []) for f in facets_of(r)]
