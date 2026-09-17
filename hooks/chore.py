#!/usr/bin/env python3
"""chore.py — PostToolUse doors that DO the chore the rule was asking for.

    python3 chore.py artifact   # after an Artifact publish: index row in Pharos/artifacts-index.md, committed
    python3 chore.py write      # after Edit/Write in the vault: RECORD THE TOUCH · APPEND THE
                                #   MEMORY-LOG ROW (src=session) · queue trailing newline ·
                                #   SHA tokens resolve? · change-everywhere lookup

A chore never denies (the act already happened). It repairs what is mechanically repairable and
reports the rest as `additionalContext` — factual statements, never orders.
"""
from __future__ import annotations
import fcntl, json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # logstore/importer, imported LAZILY below
from common import (read_input, context, log, expand, under, vault_rel, fleet_repos, guarded, lane_for, path_in_partition, region_of_repo, repo_root_of, shared_surface, record_touched, record_agent_touched, record_direct_touched, was_created, record_created, release_filelock, ROLE_STEMS)
import common
# VAULT, STATE deliberately NOT imported by name: a `from` import binds the value
# ONCE, which is the frozen-path defect this package was bitten by. Read through the
# module object (common.VAULT) so PEP 562 re-resolves on every access.
import names
import context_economy
import rootguard

EV = "PostToolUse"
UUID = re.compile(r"https://claude\.ai/code/artifact/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
# ★ Derived from the vault, so resolved PER CALL for the same reason the vault is: a constant
# here would re-freeze the path one level down from the fix.

def index_path():
    return common.VAULT / "Pharos" / "artifacts-index.md"


_ACCESSORS = {"INDEX": index_path}


def __getattr__(name: str):
    fn = _ACCESSORS.get(name)
    if fn is not None:
        return fn()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
ANCHOR = "Prefix every id with"


def _git(args: list[str], repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)


def commit_path_limited(repo: Path, rel: str, msg: str) -> str | None:
    """Stage ONE path and commit it with the pathspec, as atlas@local. Retries on index.lock."""
    for attempt in range(4):
        a = _git(["add", "--", rel], repo)
        if a.returncode == 0:
            c = _git(["-c", "user.name=atlas", "-c", "user.email=atlas@local", "commit", "-q", "-m", msg, "--", rel], repo)
            if c.returncode == 0:
                h = _git(["log", "-1", "--format=%h", "--", rel], repo)
                return h.stdout.strip() or "?"
            if "index.lock" not in (c.stderr or ""):
                log("chore", f"commit failed: {c.stderr.strip()[:200]}")
                return None
        elif "index.lock" not in (a.stderr or ""):
            log("chore", f"add failed: {a.stderr.strip()[:200]}")
            return None
        time.sleep(0.2 * (attempt + 1))
    return None


def do_artifact(inp: dict) -> None:
    ti = inp.get("tool_input") or {}
    action = ti.get("action") or "publish"
    if action != "publish":
        return
    blob = json.dumps(inp.get("tool_response", ""), ensure_ascii=False)
    m = UUID.search(blob)
    if not m:
        return
    uid = m.group(1)
    title = ""
    fp = ti.get("file_path")
    if fp:
        try:
            head = expand(fp, inp.get("cwd")).read_text(encoding="utf-8", errors="replace")[:8192]
            t = re.search(r"<title>(.*?)</title>", head, re.S | re.I)
            title = re.sub(r"\s+", " ", t.group(1)).strip() if t else ""
        except OSError:
            pass
    title = title or ti.get("title") or Path(fp or "").stem or "untitled"
    common.STATE.mkdir(parents=True, exist_ok=True)
    lk = open(common.STATE / "artifacts-index.lock", "w")
    fcntl.flock(lk, fcntl.LOCK_EX)                          # read-modify-write under a lock: two publishes never lose a row
    try:
        txt = index_path().read_text(encoding="utf-8")
    except OSError:
        context(EV, f"Artifact {uid} published but ~/Atlas/Pharos/artifacts-index.md is not readable; it is NOT indexed.")
        return
    if uid in txt:
        return
    row = f"| {time.strftime('%Y-%m-%d')} | {title.replace('|', '/')} | `{uid}` |\n"
    if ANCHOR in txt:
        i = txt.index(ANCHOR)
        # insert before the blank line that precedes the anchor paragraph
        j = txt.rfind("\n\n", 0, i)
        txt = txt[:j + 1] + row + txt[j + 1:] if j != -1 else txt[:i] + row + "\n" + txt[i:]
    else:
        txt = txt.rstrip("\n") + "\n" + row
    tmp = index_path().with_suffix(".md.tmp-" + uid[:8])
    rootguard.permit(index_path(), "artifacts index")
    tmp.write_text(txt, encoding="utf-8")
    os.replace(tmp, index_path())                                  # atomic: no reader ever sees a half-written index
    sha = commit_path_limited(common.VAULT, "Pharos/artifacts-index.md", f"artifacts-index: {title} ({uid[:8]})")
    fcntl.flock(lk, fcntl.LOCK_UN); lk.close()
    log("chore", f"artifact-index\t{uid}\t{title}\tcommit={sha}")
    context(EV, f"Artifact {uid} (“{title}”) is now a row in ~/Atlas/Pharos/artifacts-index.md"
                + (f", committed {sha}." if sha else ", written but NOT committed (git refused; see ~/.claude/gedaechtnis/chore.log)."))


# ---- born on first write: a new role file gets its row in the region's Map (DESIGN §3.1) ----
# "Any other role file is born on its first write — nothing asks." Nothing asks, and nothing has to
# remember either: the file appears, and the index that points at it grows by exactly one row.
# NOT a region folder — these carry no Map of their own and their own Map files are hand-kept.
NON_REGION = ("Global", "Pharos", "Channels", "Workflows", "Limen", "Concilium", ".hooks", ".tools")


def _vault_has_head() -> bool:
    """Does the vault have a commit yet? (The same question `init.py` asks before its first
    commit — a path-limited commit onto an unborn branch is not a thing git will do.)"""
    try:
        return _git(["rev-parse", "--verify", "-q", "HEAD"], common.VAULT).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def map_row_for(p: Path, rel: str) -> str | None:
    """Add the Map row for a just-CREATED role file; returns the report line, or None.

    Every arm is a refusal to act: not a role stem, no Map.md beside it, already linked, or the
    file was not created by this write — the row is added on evidence and never on a guess."""
    stem = p.stem
    parts = rel.split("/")
    if p.suffix != ".md" or stem not in ROLE_STEMS or stem == "Map":
        return None                              # Map.md does not index itself
    if len(parts) < 2 or len(parts) > 3 or parts[0] in NON_REGION:
        return None
    map_p = p.parent / "Map.md"
    if not map_p.is_file():
        return None                              # a region with no Map is left exactly as it is
    try:
        txt = map_p.read_text(encoding="utf-8")
    except OSError:
        return None
    if re.search(r"\[\[" + re.escape(stem) + r"(?=[|\]#])", txt):
        return None                              # already indexed, under any display name
    row = f"- [[{stem}|{names.display(stem)}]] — {names.gloss(stem)}\n"
    lines = txt.splitlines(keepends=True)
    at = None
    for i, l in enumerate(lines):
        if l.startswith("- [["):
            at = i + 1                           # after the LAST existing row, before any trailing paragraph
    if at is None:
        for i, l in enumerate(lines):
            if re.match(r"^#{1,6}\s+Files in this folder\s*$", l.strip()):
                at = i + 1
                while at < len(lines) and not lines[at].strip():
                    at += 1
                break
    if at is None:
        lines.append("\n" if lines and lines[-1].strip() else "")
        at = len(lines)
    lines.insert(at, row)
    new = "".join(lines)
    tmp = map_p.with_suffix(".md.tmp-" + stem)
    try:
        rootguard.permit(map_p, "region Map.md index row")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, map_p)                   # atomic: no reader ever sees a half-written index
    except OSError as e:
        log("chore", f"map-row\t{rel}\twrite failed: {e}")
        return None
    map_rel = vault_rel(map_p) or ""
    sha = commit_path_limited(common.VAULT, map_rel, f"{map_rel}: index {stem}.md") if _vault_has_head() else None
    log("chore", f"map-row\t{rel}\trow={stem}\tcommit={sha}")
    return (f"New file {rel}: added one row to {map_rel} — `{row.strip()}`"
            + (f", committed {sha}." if sha else "."))


# ---- the SESSION WRITE PATH: an edit to a live role file becomes a log row in the same act ----
# Council 3 (K-3, 2026-09-10) closed 5–0 on the diagnosis that the shadow's day-0 number measured
# the IMPORTER's round trip and nothing else, because "the session write path is unbuilt". This is
# that path. It is a PostToolUse chore rather than a CLI or a Stop-time sweep for the reason
# commit.py already states about committing: a memory that has to be recorded by hand is a memory
# that is half-written. A CLI would need a session to remember it; a Stop sweep would record the
# edit long after the turn that made it, and would miss the file a later edit in the same session
# rewrote. The chore fires on the write itself, with the file on disk and the parser that the
# importer uses.
#
# TWO REFUSALS, stated because both are load-bearing:
#   * it appends NOTHING when the log does not already exist — the writer follows the importer and
#     never precedes it, so a machine with no shadow does not grow one out of a role-file edit;
#   * it only ever APPENDS. It cannot rewrite or remove a row, so a session that deletes an entry
#     leaves an ORPHAN row, which `views.py` renders and counts rather than hiding.
#
# THE RESIDUAL, named: a role file written by something other than Edit/Write — a shell redirect, a
# script the session ran — has no PostToolUse Edit hook and is not recorded here. It is picked up
# by the next importer sweep as `src=importer`, so it is not lost; it is only not attributed.

def role_log_target(rel: str) -> tuple[str | None, str | None]:
    """(region, stem) when this vault-relative path is a LIVE role file the log covers, else (None, None).

    Same admission rule as `importer.regions()`/`role_files()` — a Map.md beside it, no hidden part,
    not one of the skipped top-level folders, not a `-archive`/`-fixed`/`-resolved` sidecar — but
    decided from the ONE path rather than by walking the vault, because this runs on every write."""
    p = Path(rel)
    if p.suffix != ".md":
        return None, None
    stem = p.stem
    try:
        import logstore                                  # noqa: PLC0415 — lazy: see the note above
    except ImportError:
        return None, None
    if stem not in logstore.STEMS:
        return None, None
    parts = p.parts
    if any(x.startswith(".") for x in parts):
        return None, None                                # `.gedaechtnis/views/…/Position.md` is not live
    if parts and parts[0] in ("Concilium", "Pharos", "Channels", "Workflows", "Limen"):
        return None, None
    d = common.VAULT / p.parent
    if not (d / "Map.md").is_file():
        return None, None                                # a folder with no Map is not a region
    region = str(p.parent) if str(p.parent) != "." else "."
    return region, stem


def log_session_edit(rel: str, p: Path) -> str | None:
    """Append `src=session` rows for the entries of a role file this session just wrote."""
    region, stem = role_log_target(rel)
    if not region:
        return None
    try:
        import logstore, importer                        # noqa: PLC0415,E401 — lazy, see above
        if not logstore.log_files():
            return None                                  # no shadow on this machine: nothing to do
        res = importer.log_one_file(region, stem, p, src="session")
    except Exception as e:                               # a chore never takes a session down
        log("chore", f"session-row\t{rel}\tFAILED {type(e).__name__}: {e}")
        return None
    n = len(res["appended"])
    log("chore", f"session-row\t{rel}\tentries={res['entries']}\tappended={n}")
    if not n:
        return None
    return (f"Memory log: {n} row(s) appended from {rel}, stamped `src=session` "
            f"({res['entries']} entr{'y' if res['entries'] == 1 else 'ies'} in the file; the rest "
            "were already in the log). The log is append-only — a heading you removed or renamed "
            "stays as an ORPHAN row and is counted by `gedaechtnis/views.py`.")


HEX = re.compile(r"`([0-9a-f]{7,12})`")


def sha_resolves(sha: str, repos: list[Path]) -> bool:
    for r in repos:
        if not (r / ".git").exists():
            continue
        try:
            p = _git(["cat-file", "-e", f"{sha}^{{commit}}"], r)
            if p.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, OSError):
            continue
    return False


def do_write(inp: dict) -> None:
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ""
    if not fp:
        return
    p = expand(fp, inp.get("cwd"))
    # context economy: this session wrote `p` — ANY file, not only vault ones (the notice this
    # feeds is about the whole worktree). Recorded before the vault check below, which is about
    # what the Stop hook commits and has nothing to do with what a later Read should warn about.
    context_economy.record_write(inp.get("session_id", "-"), p)
    if not under(p, common.VAULT):
        return
    sid = inp.get("session_id", "-")
    # D2, the release half. FIRST, and before the is_file() guard: the tool call this chore follows
    # is over either way, so the mutex must come off even when the write left no file behind. A
    # foreign session's lock is never touched (release_filelock checks the session id) — a release
    # that ignored ownership would be the same defect as taking somebody else's lock.
    release_filelock(p, sid)
    if not p.is_file():
        return
    rel = vault_rel(p) or ""
    # The TOUCHED SET. Every vault file this session writes is recorded against its session id,
    # because that record is the whole authority for what the Stop hook commits: a hook that
    # instead committed "everything dirty in the partition" would sweep a sibling session's
    # half-written file, which is the one collision class actually measured in this vault.
    record_touched(sid, rel)
    # WHO inside the session wrote it. A subagent's tool call carries `agent_id`; the parent's own
    # calls carry none. Splitting the record here — and only here, where the evidence exists — is
    # what lets SubagentStop commit that subagent's paths without touching the parent's in-flight
    # ones. Both records are additive: `touched` above stays the parent Stop's authority unchanged.
    agent_id = inp.get("agent_id")
    if isinstance(agent_id, str) and agent_id:
        record_agent_touched(sid, agent_id, rel)
    else:
        record_direct_touched(sid, rel)
    notes = []
    row_note = log_session_edit(rel, p)
    if row_note:
        notes.append(row_note)
    if was_created(p):                           # consumes the gate's marker either way
        # The CREATED SET, on the same evidence and in the same breath as the Map row: a file this
        # session made has no sibling's content in it, so D1 lets this session Write it whole.
        # Recorded HERE rather than at the gate because only a PostToolUse can know the write
        # actually happened — a creation the gate denied must never license a later overwrite.
        record_created(sid, rel)
        note = map_row_for(p, rel)
        if note:
            notes.append(note)
    if rel.startswith("Pharos/queues/") and p.suffix == ".md":
        try:
            b = p.read_bytes()
            if b and not b.endswith(b"\n"):
                rootguard.permit(p, "QLINT trailing-newline repair")
                p.write_bytes(b + b"\n")
                notes.append(f"Added the missing trailing newline to {rel} (QLINT-1: a row appended after a missing newline glues onto the previous line and no parser sees it).")
        except OSError:
            pass
    new_text = ti.get("new_string") or ti.get("content") or ""
    if p.suffix == ".md" and new_text:
        shas = sorted(set(HEX.findall(new_text)))
        if shas:
            repos = fleet_repos()
            bad = [s for s in shas if not sha_resolves(s, repos)]
            if bad:
                notes.append(f"SHA tokens in the text just written to {rel} that resolve in NO fleet repo or the vault: "
                             f"{', '.join(bad)}. A SHA is read back from `git log -1 --format=%h`, never written from expectation "
                             "(Global/Errata 'A commit SHA written from expectation…').")
    old_text = ti.get("old_string") or ""
    if p.suffix == ".md" and old_text and new_text:
        gone = vanished_terms(old_text, new_text)
        if gone:
            hits = other_occurrences(gone, exclude=p)
            if hits:
                lines = [f"Change-everywhere lookup: the edit to {rel} removed or renamed {len(gone)} term(s) that still occur elsewhere in the vault:"]
                for term, locs in hits.items():
                    lines.append(f"  `{term}` → " + "; ".join(locs[:8]) + (f"; +{len(locs)-8} more" if len(locs) > 8 else ""))
                lines.append("A renamed heading or id leaves every wikilink/citation to the old name dangling with no error (Global/Patterns 'A correction that does not locate the ORIGIN does not stop the propagation').")
                notes.append("\n".join(lines))
    kind = shared_surface(rel)
    if kind:
        # a permitted append to a SHARED surface by a lane that does not declare it would sit uncommitted forever
        # (the Stop hook stages only declared paths; the pre-commit guard refuses a foreign committer): commit it now
        lane, prefixes, _ = lane_for(inp.get("cwd"))
        if not (lane and path_in_partition(rel, prefixes)) or kind in ("umbrella-shared", "roster"):
            sha = commit_path_limited(common.VAULT, rel, f"{kind}: append by {lane or 'UNKNOWN-LANE'}")
            notes.append(f"Shared surface {rel}: your append is committed as atlas@local ({sha or 'commit refused, see chore.log'}).")
    if notes:
        log("chore", f"write\t{rel}\t{' | '.join(n[:80] for n in notes)}")
        context(EV, "\n".join(notes))


# ---- change-everywhere lookup (his note: "look for all entries of it everywhere so they all get changed") ----
_HEAD = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
_ID = re.compile(r"`(q:[A-Z]+-\d{4}-\d{2}-\d{2}-[A-Z0-9-]+|owner-ruling-[a-z0-9-]+|N-\d{4}-\d{2}-\d{2}-[a-z0-9-]+)`")


def vanished_terms(old: str, new: str) -> list[str]:
    """Headings and ids present in the replaced text but absent from the replacement."""
    terms = set(m.group(1) for m in _HEAD.finditer(old)) | set(_ID.findall(old))
    keep = set(m.group(1) for m in _HEAD.finditer(new)) | set(_ID.findall(new))
    return sorted(t for t in (terms - keep) if len(t) >= 6)


def other_occurrences(terms: list[str], exclude: Path, limit: int = 12) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    # The archive left the vault on 2026-09-10 (council 2 Q-2026-09-09-3), so this prefix
    # now matches nothing. KEPT, not dropped: it costs one string compare and it is what
    # keeps this sweep sane if the one-`mv` restore is ever taken.
    skip = ("Workflows/anthropic-archive", ".git/", "/.tools/")
    for term in terms[:limit]:
        try:
            p = subprocess.run(["grep", "-rIl", "--include=*.md", "-F", "--", term, str(common.VAULT)],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15)
        except (subprocess.TimeoutExpired, OSError):
            continue
        locs = []
        for line in p.stdout.splitlines():
            if any(s in line for s in skip):
                continue
            q = Path(line)
            if q.resolve() == exclude.resolve():
                continue
            locs.append(vault_rel(q) or line)
        if locs:
            out[term] = sorted(locs)
    return out


# ---- foreign-work record: work done outside the session's lane is recorded in THAT region's Inbox.md ----

def _inbox_target(p: Path, cwd: str | None) -> tuple[str | None, str | None]:
    """(region, what) when the written path is outside this session's lane: a vault path in another region, or a
    file inside another lane's repo. Returns (None, None) when the write is within the lane or unattributable."""
    lane, prefixes, _ = lane_for(cwd)
    if under(p, common.VAULT):
        rel = vault_rel(p) or ""
        if lane and path_in_partition(rel, prefixes):
            return None, None
        if shared_surface(rel):
            return None, None                      # an append to a shared surface IS the record
        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] not in ("Global", "Pharos", "Channels", "Workflows", "Limen", "Concilium", ".hooks", ".tools"):
            return "/".join(parts[:2]), f"vault file {rel}"
        if parts[0] == "Speculum":
            return "Speculum", f"vault file {rel}"
        return None, None
    repo = repo_root_of(p)
    if not repo:
        return None, None
    region = region_of_repo(repo)
    if not region:
        return None, None
    own = repo_root_of(Path(cwd)) if cwd else None
    if own and own.resolve() == repo.resolve():
        return None, None
    try:
        rel = str(p.relative_to(repo))
    except ValueError:
        rel = p.name
    return region, f"{repo.name}/{rel}"


def do_inbox(inp: dict) -> None:
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ""
    if not fp:
        return
    cwd = inp.get("cwd"); p = expand(fp, cwd)
    region, what = _inbox_target(p, cwd)
    if not region:
        return
    lane, _, _ = lane_for(cwd)
    lane = lane or "UNKNOWN-LANE"
    sid = inp.get("session_id", "-")
    inbox = common.VAULT / region / "Inbox.md"
    if not (common.VAULT / region).is_dir():
        return
    # `region` came out of another repo's CLAUDE.md, so it is data, not a name this package chose.
    # `is_dir()` above is a check that the target EXISTS, which a traversal satisfies trivially —
    # `~/Desktop` is a directory. The permit is the check that it is OURS. (BLASTRADIUS-1, 2026-09-15.)
    try:
        rootguard.permit(inbox, "inbox row")
    except rootguard.OutsideRoot as e:
        log("hook-errors", f"inbox-refused\t{inbox}\t{e}".replace("\n", " | "))
        return
    # one row per session × region × file; the seen-set lives in the state dir
    seen_f = common.STATE / "inbox-seen.txt"
    key = f"{sid}\t{region}\t{what}"
    try:
        seen = set(seen_f.read_text(encoding="utf-8").splitlines()) if seen_f.is_file() else set()
    except OSError:
        seen = set()
    if key in seen:
        return
    day = time.strftime("%Y-%m-%d")
    row = f"- {day} {lane} wrote `{what}` from `{cwd}` (session {sid[:8]}) — fold or verify at your next boot; the writer's own notes live in its lane, not here.\n"
    new = not inbox.is_file()
    try:
        with open(inbox, "a", encoding="utf-8") as fh:
            if new:
                fh.write(f"# {region} — Inbox\n\nAppend-only. Rows are written by the Gedächtnis hooks when ANOTHER lane works in this region or its repo, so the record lands where the work happened. The owning lane folds each row into its state files at its next boot and deletes it here.\n\n")
            fh.write(row)
        common.STATE.mkdir(parents=True, exist_ok=True)
        with open(seen_f, "a", encoding="utf-8") as fh:
            fh.write(key + "\n")
    except OSError:
        return
    rel = f"{region}/Inbox.md"
    sha = commit_path_limited(common.VAULT, rel, f"{region} Inbox: {lane} wrote {what.split('/')[-1]}")
    log("chore", f"inbox\t{region}\t{lane}\t{what}\tcommit={sha}")
    context(EV, f"Recorded in {rel} that {lane} wrote `{what}` (this region is not this session's lane). The owning lane sees it at its next boot"
                + (f"; committed {sha}." if sha else "; NOT committed (see chore.log)."))


# ------------------------------------------------ D2's Bash half: release what Bash locked ----
# `rule_bash_partition` takes the same per-file mutex for a shell write to a vault `.md` file
# (`>`, `>>`, `tee`, `sed -i`), because a shell redirect is a whole-file overwrite with no anchor
# at all. Until this chore existed there was no PostToolUse on Bash, so every such lock could only
# expire — correct, but it made a sibling wait ten seconds for a write that finished in ten
# milliseconds. This releases them the moment the command returns. It releases ONLY locks this
# session holds, and it repairs nothing else: a chore that started editing files after a shell
# command would be guessing at what the command meant.


def do_bash(inp: dict) -> None:
    cmd = (inp.get("tool_input") or {}).get("command") or ""
    if not cmd:
        return
    sid = inp.get("session_id", "-")
    # context economy: record every file this Bash command read via cat/head/tail/sed -n, so a
    # later read of the same unchanged content gets the RE-READ notice.
    context_economy.record_bash_reads(sid, cmd, inp.get("cwd"))
    try:
        from gate import bash_write_targets           # the same target list the gate locked from
    except ImportError as e:                          # pragma: no cover - the two files ship together
        log("chore", f"bash-release import failed: {e}")
        return
    freed = []
    for p, how in bash_write_targets(cmd, inp.get("cwd")):
        if how in ("redirect", "redirect-append", "sed-i", "tee", "tee-append") and p.suffix == ".md":
            if release_filelock(p, sid):
                freed.append(vault_rel(p) or str(p))
    if freed:
        log("chore", f"bash\treleased={len(freed)}\t{' '.join(freed)}")


def do_read(inp: dict) -> None:
    """PostToolUse Read: the read happened — record its hash so a later re-read can be compared
    against it (context_economy.py). A PreToolUse alone cannot know the read succeeded."""
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ""
    if not fp:
        return
    p = expand(fp, inp.get("cwd"))
    context_economy.record_read(inp.get("session_id", "-"), p)


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    inp = read_input()
    {"artifact": do_artifact, "write": do_write, "inbox": do_inbox,
     "bash": do_bash, "read": do_read}.get(which, lambda _i: None)(inp)


if __name__ == "__main__":
    guarded(main)
