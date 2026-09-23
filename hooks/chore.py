#!/usr/bin/env python3
"""chore.py — PostToolUse doors that DO the chore the rule was asking for.

    python3 chore.py artifact   # after an Artifact publish: a row in the vault's artifact index, committed
    python3 chore.py write      # after Edit/Write in the vault: RECORD THE TOUCH · APPEND THE
                                #   MEMORY-LOG ROW (src=session) · queue trailing newline ·
                                #   SHA tokens resolve? · change-everywhere lookup
    python3 chore.py answers    # carry every unambiguously-placeable answered review export back
                                #   to the work it belongs to (silent with no router configured)

A chore never denies (the act already happened). It repairs what is mechanically repairable and
reports the rest as `additionalContext` — factual statements, never orders.
"""
from __future__ import annotations
import fcntl, json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # logstore/importer, imported LAZILY below
from common import (read_input, context, log, expand, under, vault_rel, fleet_repos, guarded, lane_for, path_in_partition, region_of_repo, repo_root_of, main_checkout_of, lane_of_repo, declared_lane_of_session, shared_surface, record_touched, record_handoff, record_agent_touched, record_direct_touched, was_created, record_created, release_filelock, record_cwd, ROLE_STEMS)
import common
# VAULT, STATE deliberately NOT imported by name: a `from` import binds the value
# ONCE, which is the frozen-path defect this package was bitten by. Read through the
# module object (common.VAULT) so PEP 562 re-resolves on every access.
import names
import context_economy
import rootguard
import citations
import config
import lesson_push
import lazy_body
import deferral_door

EV = "PostToolUse"
UUID = re.compile(r"https://claude\.ai/code/artifact/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
# ★ Derived from the vault, so resolved PER CALL for the same reason the vault is: a constant
# here would re-freeze the path one level down from the fix.

def index_path():
    """The vault's artifact index, or None when this installation configures none.

    An index of published artifacts is a convention of the vault, not of the memory system, so
    the path comes from `config.topology()["artifacts_index"]`. With nothing configured the
    artifact chore has nowhere to write and says so, rather than inventing a folder."""
    return config.artifacts_index()


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
    idx = index_path()
    if idx is None:
        context(EV, f"Artifact {uid} (“{title}”) published. This vault configures no artifact index "
                    f"(`topology.artifacts_index` in {config.config_path()}), so no row was written.")
        return
    common.STATE.mkdir(parents=True, exist_ok=True)
    lk = open(common.STATE / "artifacts-index.lock", "w")
    fcntl.flock(lk, fcntl.LOCK_EX)                          # read-modify-write under a lock: two publishes never lose a row
    try:
        txt = idx.read_text(encoding="utf-8")
    except OSError:
        context(EV, f"Artifact {uid} published but the artifact index {idx} is not readable; it is NOT indexed.")
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
    tmp = idx.with_suffix(".md.tmp-" + uid[:8])
    rootguard.permit(idx, "artifacts index")
    tmp.write_text(txt, encoding="utf-8")
    os.replace(tmp, idx)                                           # atomic: no reader ever sees a half-written index
    sha = commit_path_limited(common.VAULT, common.vault_rel(idx) or str(idx), f"artifacts-index: {title} ({uid[:8]})")
    fcntl.flock(lk, fcntl.LOCK_UN); lk.close()
    log("chore", f"artifact-index\t{uid}\t{title}\tcommit={sha}")
    context(EV, f"Artifact {uid} (“{title}”) is now a row in {common.vault_rel(idx) or idx}"
                + (f", committed {sha}." if sha else ", written but NOT committed (git refused; see ~/.claude/gedaechtnis/chore.log)."))


# ---- born on first write: a new role file gets its row in the region's Map (DESIGN §3.1) ----
# "Any other role file is born on its first write — nothing asks." Nothing asks, and nothing has to
# remember either: the file appears, and the index that points at it grows by exactly one row.
# NOT a region folder — these carry no Map of their own and their own Map files are hand-kept.
# The set comes from `config.non_region_tops()` — one seam, so a vault states its shape once
# instead of four modules each carrying a copy of it.


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
    if len(parts) < 2 or len(parts) > 3 or parts[0].startswith(".") or parts[0] in config.non_region_tops():
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
    if parts and parts[0] in config.non_region_tops():
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


def citation_notes(new_text: str) -> list[str]:
    """The write-time citation resolution. Never raises: a note that cost a turn would be a bad bargain."""
    try:
        qs, rs, capped = citations.tokens(new_text)
        if not qs and not rs:
            return []
        qstates = {}
        if qs:
            queue_text = ""
            qdir = config.queues_dir()
            if qdir and qdir.is_dir():
                parts, total = [], 0
                for f in sorted(qdir.rglob("*.md")):
                    try:
                        t = f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    total += len(t)
                    if total > citations.MAX_QUEUE_BYTES:
                        # Past the ceiling the answer would be slow rather than wrong. Go quiet:
                        # a partial read would report present rows as ABSENT, which is worse than
                        # saying nothing (`citations.py`, "what bounds the cost").
                        parts = []
                        break
                    parts.append(t)
                queue_text = "\n".join(parts)
            if queue_text:
                qstates = {q: citations.row_state(q, queue_text) for q in qs}
            # No queue files at all: every id would read as ABSENT, which is a fact about this
            # installation and not about the citation. Say nothing rather than flag them all.
        rstates = {}
        if rs:
            ledger = config.authority_log()
            if ledger is not None:
                try:
                    import authority
                    recs = authority.records(ledger.read_text(encoding="utf-8", errors="replace"))
                    rstates = {r: citations.ruling_state(r, recs, authority.SUPERSEDED_BY) for r in rs}
                except (OSError, ImportError):
                    rstates = {}                     # named but unreadable: still not the citation's fault
            # No ledger configured at all: the same reasoning as the queues above — unresolvable is
            # not wrong, and reporting every ruling as ABSENT would be a fact about this
            # installation rather than about the text. An `is_file()` guard here would add nothing:
            # the OSError arm already owns the missing-file case and returns the same empty answer.
        return citations.notes(qstates, rstates, capped)
    except Exception as exc:                         # pragma: no cover - guarded like every chore
        log("chore", f"citations\t{exc}")
        return []


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
    # IDLENOTIFY-1 — a handoff is the one artifact whose existence means a row finished, and it is
    # recorded HERE, above the vault check, for the same reason the lesson arm below is: a handoff
    # lives in a REPO, so an arm wired to vault writes would never see one.
    record_handoff(inp.get("session_id", "-"), p)
    # LESSONPUSH-1/2 — the lessons this vault already holds about what was just written, named
    # here because this is the moment they apply. ABOVE the vault check on purpose: the measurement
    # that bought this arm (LESSONYIELD-1) found the re-occurrences in REPO artifacts — handoffs,
    # row reports, deviation logs — not in vault files, so a push that only fired on vault writes
    # would be silent for exactly the population it was built for. `do_bash` carries the second
    # trigger (LESSONPUSH-2): five writes in six here are a heredoc or a redirect, not an Edit.
    lesson = lesson_push.notice(inp.get("session_id", "-"), p, lesson_push.text_of(ti),
                                inp.get("cwd"))
    # LAZYBOOT-1 item 2 — the region's pull-only bodies, once, because the session is now
    # working in it. Beside the lesson arm and for the same reason it sits above the vault
    # check: a session writing a row report or a handoff in a REPO is working in that
    # repo's region just as much as one editing the region's own files.
    lazy = lazy_body.notice(inp.get("session_id", "-"), p, inp.get("cwd"))
    # DEFERDOOR-1 — a new line that defers work and names no queue row. Above the vault check:
    # the specimen's promises sat in a docstring, a report and a notice — two of three in a repo.
    deferral = deferral_door.notice(p, ti)
    if not under(p, common.VAULT):
        # The only `additionalContext` this arm emits for a non-vault write — and it must be
        # emitted HERE, because everything below returns without speaking for such a path.
        both = [t for t in (lesson, lazy, deferral) if t]
        if both:
            context(EV, "\n\n".join(both))
        return
    sid = inp.get("session_id", "-")
    # D2, the release half. FIRST, and before the is_file() guard: the tool call this chore follows
    # is over either way, so the mutex must come off even when the write left no file behind. A
    # foreign session's lock is never touched (release_filelock checks the session id) — a release
    # that ignored ownership would be the same defect as taking somebody else's lock.
    release_filelock(p, sid)
    if not p.is_file():
        return                      # gone before this chore ran: no note of any kind, deferral's included
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
    if lesson:
        notes.append(lesson)
    if lazy:
        notes.append(lazy)
    if deferral:
        notes.append(deferral)
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
    qrel = config.queues_rel()
    if qrel and rel.startswith(qrel + "/") and p.suffix == ".md":
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
    # STALEWRITE-1 — resolve the CITATION TOKENS in what was just written, beside the SHA check
    # above and for the same reason: a citation goes stale in silence. Bounded, and it only ever
    # speaks when an answer is worth acting on. `citations.py` carries the rules, including why a
    # token inside a fence or a blockquote is not a citation.
    if p.suffix == ".md" and new_text:
        notes.extend(citation_notes(new_text))

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

def _rel_in(p: Path, repo: Path) -> str:
    try:
        return str(p.relative_to(repo))
    except ValueError:
        return p.name


def _inbox_target(p: Path, cwd: str | None, sid: str = "-") -> tuple[str | None, str | None]:
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
        tops = config.non_region_tops()
        if len(parts) >= 3 and not parts[0].startswith(".") and parts[0] not in tops:
            return "/".join(parts[:2]), f"vault file {rel}"
        if len(parts) == 2 and not parts[0].startswith(".") and parts[0] not in tops:
            # A FLAT vault's region — `<Region>/<role>.md`, the shape init.py creates. This used
            # to be reachable only through one hard-coded region name, so in a flat vault every
            # other region's foreign write went unattributed.
            return parts[0], f"vault file {rel}"
        return None, None
    repo = repo_root_of(p)
    if not repo:
        return None, None
    region = region_of_repo(repo)
    if not region:
        return None, None
    # WHOSE WRITE IS THIS? By the LANE each side DECLARES, never by comparing root paths. A git
    # worktree's root is a different directory from its main checkout's, so root equality called a
    # lane's own build worktrees another lane's — thirty-two of one lane's own reports landed in
    # another region's Inbox in one morning — and a session standing in the VAULT, where no marker
    # lives, had no identity at all, so seven more of its own writes were filed under an unknown
    # lane. Both are the same misread.
    #
    # The session's side is its cwd marker, and failing that the lane it DECLARED AT BOOT, which is
    # what survives a `cd` into the vault. The write's side is the marker of the checkout that owns
    # the repository it went into. Only when BOTH are known can this say "another lane": with the
    # session's own identity unknown, an honest reader cannot tell a foreign write from an own one,
    # and the record exists to say WHO worked here.
    session_lane = lane or declared_lane_of_session(sid) or common.session_partition(sid)[0]
    write_lane = lane_of_repo(p)
    if write_lane and session_lane:
        if write_lane == session_lane:
            return None, None                        # our own repo — a worktree of it included
        return region, f"{repo.name}/{_rel_in(p, repo)}"
    # One side undeclared. Fall back to identity by REPOSITORY, with worktrees resolved to the
    # checkout that owns them, so the unmarked case is at least not wrong about worktrees.
    own = main_checkout_of(Path(cwd)) if cwd else None
    mine = main_checkout_of(p)
    if own and mine and own == mine:
        return None, None
    return region, f"{repo.name}/{_rel_in(p, repo)}"


def do_inbox(inp: dict) -> None:
    ti = inp.get("tool_input") or {}
    fp = ti.get("file_path") or ""
    if not fp:
        return
    cwd = inp.get("cwd"); p = expand(fp, cwd)
    sid = inp.get("session_id", "-")
    region, what = _inbox_target(p, cwd, sid)
    if not region:
        return
    lane, _, _ = lane_for(cwd)
    lane = (lane or declared_lane_of_session(sid) or common.session_partition(sid)[0]
            or "UNKNOWN-LANE")
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
    # WTSWEEP-1 — where this session is working NOW. The Stop-time worktree sweep will not delete a
    # directory a live session occupies, and the SessionStart record cannot answer that question:
    # it holds where the session STARTED, and every builder here starts in the repo root and then
    # works inside a worktree. Written only when the value changes.
    record_cwd(sid, inp.get("cwd"))
    # context economy: record every file this Bash command read via cat/head/tail/sed -n, so a
    # later read of the same unchanged content gets the RE-READ notice.
    context_economy.record_bash_reads(sid, cmd, inp.get("cwd"))
    # LESSONPUSH-2 — the SECOND trigger. Design 1 rode on Edit/Write alone and measured its own
    # blindness: 16.9% of this population's file-producing tool calls. A Bash command that
    # literally produces a file is a write, and the command text (a heredoc body included) is what
    # was written. Emitted BEFORE the lock release below, which has an early `return` of its own
    # on an import failure — a notice placed after it would be silently lost in exactly the case
    # where something is already wrong. Nothing below this prints (the release speaks only to the
    # log), so this process still emits at most one JSON object.
    lesson = lesson_push.notice_bash(sid, cmd, inp.get("cwd"))
    # LAZYBOOT-1 item 2 — the same path-scoped injection, off the Bash trigger. Five writes
    # in six in this population are a heredoc or a redirect, so an arm wired to Edit/Write
    # alone would be blind to most of the work it exists for (LESSONPUSH-2 measured that
    # blindness at 16.9% coverage before the Bash arm took it to 50.0%).
    bash_written = [t for t, _how in lesson_push.bash_targets(cmd, inp.get("cwd"))]
    # IDLENOTIFY-1 — the handoff door's SECOND trigger, and the one that carries most of the
    # traffic: this fleet writes its handoffs with a heredoc, which the Edit/Write chore never sees.
    for t in bash_written:
        record_handoff(sid, t)
    lazy = lazy_body.notice(sid, bash_written, inp.get("cwd"))
    both = [t for t in (lesson, lazy) if t]
    if both:
        context(EV, "\n\n".join(both))
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
    sid = inp.get("session_id", "-")
    context_economy.record_read(sid, p)
    # LAZYBOOT-1 item 2 — a READ is a touch. A session that opens a region's file to find
    # out where things stand is exactly the session the bodies were kept on disk for, and
    # it is the touch that happens FIRST: reading precedes writing in most of this
    # population's work.
    lazy = lazy_body.notice(sid, p, inp.get("cwd"))
    if lazy:
        context(EV, lazy)


def do_answers(inp: dict) -> None:
    """`chore.py answers` — carry every unambiguously-placeable answered review export back to the
    work it belongs to, and report what happened as context.

    A chore, in this package's sense exactly: it never denies (the person already answered the
    page), it repairs what is mechanically repairable, and it states the rest as facts rather than
    orders. Silent when this vault configures no `answer_router`.

    The same function the SessionStart facts line calls, reachable on demand so a session that
    has just downloaded an answer does not have to wait for its next boot.
    """
    router = config.answer_router()
    if not router:
        return
    try:
        import answers
    except ImportError as e:                      # pragma: no cover - ships alongside
        log("chore", f"answers import failed: {e}")
        return
    marker = None
    try:
        _lane, _pre, marker = lane_for(inp.get("cwd"))
    except Exception:                             # noqa: BLE001 - a chore never crashes a session
        marker = None
    line = answers.sweep(config.python(), router, marker)
    if line:
        context(EV, line.lstrip("- "))


def do_wtsweep(inp: dict) -> None:
    """Stop-time GIT-worktree sweep. Removes the finished ones; records the rest for the next boot.

    ★ `git worktree`, not the APFS clones in `worktree.py` — see `wtsweep.py`'s docstring, which
    carries the whole rule. This function is only the door: find the repo, ask, record. It prints
    nothing. A Stop hook that printed would be speaking to a session that has already stopped, and
    what a person needs to see is the state at their NEXT boot, which is what `record` is for.

    Off by default is NOT the shape here — `worktree_sweep` ships true — so the guard that matters
    is that every destructive decision is made in `wtsweep.classify`, under test, and this door
    contributes no condition of its own."""
    import wtsweep                                            # noqa: PLC0415 — Stop-only cost
    if not wtsweep.enabled():
        return
    cwd = inp.get("cwd") or os.getcwd()
    try:
        repo = repo_root_of(Path(cwd))
    except OSError:
        return
    if repo is None or not (repo / ".git").exists():
        return
    result = wtsweep.sweep(repo, apply=wtsweep.applies())
    wtsweep.record(repo, result)
    for path in result["removed"]:
        log("worktrees", f"removed\t{path}")
    for path, reason in result["kept"]:
        log("worktrees", f"kept\t{path}\t{reason}")
    for path in result.get("proposed", []):
        log("worktrees", f"proposed\t{path}")


def do_idlenotify(inp: dict) -> None:
    """Stop-time: tell the seat that ignited this session that it finished or hit its cap.

    ★ `idlenotify.py` carries the whole rule, including why this is off unless a vault names a
    `session_name_pattern` and why the mailbox is written before the transport runs. This function
    is only the door: hand over what the payload knows and print nothing. A Stop hook that printed
    would be speaking to a session that has already stopped — the reader here is a DIFFERENT
    session, which is the point of the module."""
    import idlenotify                                       # noqa: PLC0415 — Stop-only cost
    import session_start                                    # noqa: PLC0415 — for `opener_text`
    # The opener is read through `session_start.opener_text` rather than re-parsed here: it is the
    # one place in this package that knows what a first user record looks like, and a second
    # implementation would be the duplicated fact this package refuses everywhere else. At STOP the
    # transcript always holds the opener — the "usually None" in that function's docstring is about
    # SessionStart, where the hook runs before the first user record exists.
    #
    # ★ PASSED AS A CALLABLE, NOT AS A VALUE. `opener=session_start.opener_text(inp)` reads the
    # transcript BEFORE `notify` runs — Python evaluates keyword arguments eagerly — so every Stop
    # of every turn on every machine paid a transcript open and up to 200 JSON parses to reach a
    # door that was off. The lambda is not a style choice; it is what makes "off means nothing is
    # read" true.
    idlenotify.notify(inp.get("session_id", "-"), inp.get("cwd"), inp.get("transcript_path"),
                      opener=lambda: session_start.opener_text(inp))


def do_mergesha(inp: dict) -> None:
    """UserPromptSubmit: when a message from another session claims a merge, check it with git and
    add the answer to this session's context. `mergesha.py` carries the rule; this is only the
    door. Never blocks — a claim that does not verify is information for the reader, not a wall."""
    import mergesha                                         # noqa: PLC0415 — prompt-time cost
    text = mergesha.from_prompt(inp)
    if text:
        context("UserPromptSubmit", text)


def do_facet(inp: dict) -> None:
    """PostToolUse, every tool: the region's facets whose trigger this call matched (KERNELFACET-1).

    ONE arm for every tool, not a line in each of `do_bash`/`do_read`/`do_write`: a tool-name
    trigger has to see tools none of those arms is registered for, and one decision split over four
    call sites is four places for it to drift. The engine is `lazy_body`'s, so region resolution,
    the per-session claim and the log are the body arm's own."""
    text = lazy_body.facet_notice(inp.get("session_id", "-"), inp.get("tool_name") or "",
                                  inp.get("tool_input") or {}, inp.get("cwd"))
    if text:
        context(EV, text)


def do_prompt(inp: dict) -> None:
    """UserPromptSubmit: a prompt that carries `facet:<name>`, or names a queue row that does."""
    text = lazy_body.facet_prompt_notice(inp.get("session_id", "-"), inp.get("prompt") or "",
                                         inp.get("cwd"))
    if text:
        context("UserPromptSubmit", text)


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    inp = read_input()
    {"artifact": do_artifact, "write": do_write, "inbox": do_inbox,
     "bash": do_bash, "read": do_read, "answers": do_answers,
     "wtsweep": do_wtsweep, "idlenotify": do_idlenotify,
     "mergesha": do_mergesha, "facet": do_facet, "prompt": do_prompt}.get(which, lambda _i: None)(inp)


if __name__ == "__main__":
    guarded(main)
