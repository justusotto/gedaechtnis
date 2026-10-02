#!/usr/bin/env python3
"""rowdone.py — a finished queue row marks itself (AUTODONE-1).

A queue row stays open after its work lands whenever "mark the row" is a sentence someone has to
remember. On 2026-09-25, 75 of 234 open rows carried a note saying merged / landed / shipped and
were still `[ ]`, and each periodic sweep that flipped them was followed by the same pile growing
back. This module makes the mark part of the act that finishes the row:

    a merge sha that IS on main  +  a handoff whose `## ROWS:` line names `q:<id>` with that sha
        →  that row's checkbox becomes `[x]`,
           `  - done: <sha> <YYYY-MM-DD> by <session>` is appended as its LAST sub-line,
           and the queue file is committed, path-limited, in the vault.

Deterministic throughout: no model reads anything, and every refusal is one printed line.

    python3 rowdone.py apply <sha> <handoff> [--repo R]   every row the handoff names with <sha>
    python3 rowdone.py flip  <q-id> <sha> --by <session> [--repo R]   one row, by hand
    python3 rowdone.py owed                               try the marks a busy vault deferred
    python3 rowdone.py flip  <q-id> --vault-sha <sha> --by <session>  one row finished by a VAULT
                                         commit (no code merge): the sha must be a commit in the vault

Exit codes (one line printed per row either way):
    0  flipped — or already `[x]` (idempotent no-op)
    2  usage, or the repository has no `.rowdone` (the mechanism is off there)
    3  REFUSED: the sha is not on main (with --vault-sha: not a commit in the vault)
    4  FILE-IT <id>: no queue row carries that id
    5  REFUSED: the queue file is outside this lane's rows — a notice is written instead
    6  REFUSED: a test suite is watching the vault (the suite lock, `tools/suitelock.py`) — owed
    7  REFUSED: the row is not uniquely identified (count ≠ 1, or its two id readings disagree)
    8  REFUSED: the queue file has uncommitted changes (a path-limited commit would carry them) — owed
    9  the handoff names no row with that sha

★ WHERE IT RUNS. Two doors in `chore.py`, both calling `apply` here:
  * PostToolUse on Bash — a `mergesha.py verify-merge <sha>` that printed "on main" → the newest
    handoff naming that sha;
  * Stop — every handoff this session wrote (`common.handoff_paths`), so a session that forgot, or
    wrote its handoff after the verify, still lands its marks at exit.

★ A BUSY VAULT IS NOT A LOST MARK (ROWDONEQ-1). When the suite lock (exit 6) or an uncommitted
queue file (exit 8) refuses a flip, the mark is written as an OWED entry — row id, sha, session,
handoff, repository, lane — into `rowdone-owed.json` in the state directory. Every later Stop of a
session of that lane tries the owed entries first-come, without waiting, and the first one that
finds the vault quiet applies them through the same `flip` (every check runs again). An entry
leaves the file when its row is flipped or the refusal is final; one that is still refused after
`OWED_DAYS` is dropped and logged. `rowdone.py owed` applies them by hand.

★ SWITCHED ON PER REPOSITORY by a committed `.rowdone` file at the repository root — the same
shape as `.merge-window`. It says where the queues are (`queues:` — a vault-relative directory,
whose `*.md` are read, or file) and where handoffs live (`handoffs:` — a repo-relative glob). No
file, no mechanism: this package creates no queue folder and does not guess one.

★ A SECOND EVIDENCE SOURCE — MERGE SUBJECTS (AUTODONE-1 scope addition, from the MNEMOSYNE notice
`Channels/MNEMOSYNE/N-2026-09-25-queue-rows-stale-open-after-merge.md`: 26 rows stayed open up to
five days because that seat closed rows "at next boot"). At Stop, every OPEN row of every queue is
matched against the commit SUBJECTS on `main` of every fleet repository (`common.fleet_repos()`,
the vault excluded — a vault subject files rows, it does not finish them) within `SUBJECT_DAYS`:
`Merge <ID>` / `Merge q:<ID>` / `(q:<ID>)`. A row this lane may flip is flipped exactly as above;
another lane's row is NEVER closed — it is recorded in `queue-stale-open.json` under the state dir,
and that lane's next SessionStart prints `QUEUE-STALE-OPEN <id> <repo> <sha>` (plus a notice in
this lane's outbox). A body mention is not evidence: only `%s` is read.

★ WHOSE ROW IT IS. A lane may flip a row only in a queue file inside its declared partition, and a
DIRECTORY grant yields to an EXACT one: when another lane's roster block (`config.roster()`) names
the file itself, the row is that lane's, and this lane writes a notice into its own outbox instead
(`<channels_dir>/<LANE>/N-<date>-rowdone-<id>.md`, facts only). With no readable roster, only a
file this lane names exactly is its own — unknown is refused, never assumed.
"""
from __future__ import annotations
import glob as _glob, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common, config, mergesha, rootguard

MARKER = ".rowdone"
OK, USAGE, NOT_ON_MAIN, ABSENT, FOREIGN, PYTEST, AMBIGUOUS, DIRTY, NO_ROW = 0, 2, 3, 4, 5, 6, 7, 8, 9

# The two readings of a row's identity. ★ The same three expressions as `row_identity()` in the
# repository's `scripts/arc_scope.py`; this package cannot import a repository script, so the
# agreement is pinned by a test that runs both over the live queues (tests/test_rowdone_parity.py).
ROW_ANY = re.compile(r"^\s*-\s*\[[ xX]\]\s")
ROW_OPEN = re.compile(r"^\s*-\s*\[ \]\s")
ANCHORED = re.compile(r"^\s*-\s*\[[ xX]\]\s+`q:([A-Za-z0-9][A-Za-z0-9\-]*)`")
FIELD_QID = re.compile(r"\|\s*q:([A-Za-z0-9][A-Za-z0-9\-]*)\b")
CHECKBOX = re.compile(r"^(\s*-\s*)\[ \]")

ROWS_HEAD = re.compile(r"^##\s+ROWS:?\s*$")
NEXT_HEAD = re.compile(r"^#{1,2}\s")
ROWS_LINE = re.compile(r"^\s*(q:[A-Za-z0-9][A-Za-z0-9\-]*)\t([^\t]*)\t([^\t]*)\t([0-9a-fA-F]{7,40})(?:\t|$)")
SHA_OK = re.compile(r"^[0-9a-fA-F]{7,40}$")
# PYTESTLOCK-1: a write waits on the suite lock (`tools/suitelock.py`) — only a suite whose vault
# sentinel is WATCHING holds it — instead of refusing whenever any pytest runs on the machine. The
# anchored `ps` count below survives as that module's FALLBACK (lock unreadable) and nothing else.
import importlib.util as _ilu
_sl_spec = _ilu.spec_from_file_location(
    "gedaechtnis_suitelock", Path(__file__).resolve().parent.parent / "tools" / "suitelock.py")
suitelock = _ilu.module_from_spec(_sl_spec)
_sl_spec.loader.exec_module(suitelock)
PYTEST = suitelock.PYTEST
SUBJECT_DAYS = 14
SUBJECT_ID = re.compile(r"(?:^|\s)Merge\s+(?:q:)?([A-Z][A-Z0-9]*-\d{4}-\d{2}-\d{2}-[A-Za-z0-9-]*[A-Za-z0-9])"
                        r"|\(q:([A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9])\)")
STALE_FILE = "queue-stale-open.json"
OWED_FILE, OWED_DAYS = "rowdone-owed.json", 7
RETRY = (PYTEST, DIRTY, NOT_ON_MAIN)                 # refusals a later, quieter moment can clear


class Ambiguous(Exception):
    pass


def row_identity(line: str) -> str | None:
    """The id of the row THIS LINE IS — never an id it merely cites. Raises on disagreement."""
    if not ROW_ANY.match(line):
        return None
    a = ANCHORED.match(line)
    aid = a.group(1) if a else None
    fids = set(FIELD_QID.findall(line))
    if len(fids) > 1:
        raise Ambiguous(f"row carries {len(fids)} distinct `| q:` fields {sorted(fids)}")
    fid = fids.pop() if fids else None
    if aid and fid and aid != fid:
        raise Ambiguous(f"anchored id q:{aid} but id field q:{fid} on the same row")
    return aid or fid


# ---- configuration -------------------------------------------------------------------------------

def repo_config(repo: Path | None) -> dict | None:
    """The repository's `.rowdone` as {"queues": [...], "handoffs": [...]}, or None (= off)."""
    if repo is None:
        return None
    try:
        text = (Path(repo) / MARKER).read_text(encoding="utf-8")
    except OSError:
        return None
    out: dict = {"queues": [], "handoffs": []}
    for raw in text.splitlines():
        key, sep, val = raw.strip().partition(":")
        val = val.strip()
        if not sep or key not in out or not val or val.startswith("/") or ".." in val.split("/"):
            continue
        out[key].append(val.rstrip("/"))
    return out


def queue_files(cfg: dict) -> list[Path]:
    vault = config.vault()
    rels = list(cfg.get("queues") or [])
    q = config.queues_rel()
    if q:
        rels.append(q)
    out: list[Path] = []
    for rel in rels:
        p = vault / rel
        found = sorted(p.glob("*.md")) if p.is_dir() else ([p] if p.is_file() else [])
        for f in found:
            if f not in out:
                out.append(f)
    return out


def roster_lanes() -> dict | None:
    """{lane: [paths]} from the roster's `lane:`/`path:` lines, or None when it cannot be read."""
    try:
        text = config.roster().read_text(encoding="utf-8")
    except OSError:
        return None
    out: dict = {}
    cur = None
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("lane:"):
            cur = s.split(":", 1)[1].strip()
            out.setdefault(cur, [])
        elif s.startswith("path:") and cur:
            out[cur].append(s.split(":", 1)[1].strip().rstrip("/"))
    return out or None


def custody(rel: str, lane: str, prefixes: list[str]) -> tuple[bool, str | None]:
    """(may this lane flip rows in `rel`, the owning lane when it may not)."""
    roster = roster_lanes()
    exact_others = [l for l, ps in (roster or {}).items() if l != lane and rel in ps]
    if rel in prefixes:
        return True, None
    if not common.path_in_partition(rel, prefixes):
        return False, (exact_others[0] if exact_others else None)
    if roster is None:
        return False, None                            # a directory grant, and nobody to ask: refuse
    if exact_others:
        return False, exact_others[0]                 # the directory grant yields to the exact one
    return True, None


def pytest_count(ps_lines: list[str] | None = None) -> int:
    return suitelock.pytest_count(ps_lines)           # the fallback count; unreadable = 1


_WAIT_DEADLINE: float | None = None     # one wait budget per PROCESS, however many rows it flips


def suite_busy(ps_lines: list[str] | None = None, clock=time.monotonic) -> str | None:
    """Why the vault must not be written right now, or None. Waits on the suite lock first.

    The cap is spent ONCE per process: a handoff naming five rows calls this five times, and five
    fresh 20-s waits would outlast the 30-s hook that runs them. The first call sets the deadline;
    every later call waits only for what is left of it (zero once it has passed)."""
    global _WAIT_DEADLINE
    now = clock()
    if _WAIT_DEADLINE is None:
        _WAIT_DEADLINE = now + suitelock.cap_default()
    return suitelock.writer_gate(ps_lines=ps_lines, cap_s=max(0.0, _WAIT_DEADLINE - now))


# ---- handoffs ------------------------------------------------------------------------------------

def rows_in_handoff(text: str) -> list[tuple[str, str, str]]:
    """(q-id, session, sha) for every tab-separated line under the `## ROWS:` heading."""
    out, inside = [], False
    for line in text.splitlines():
        if ROWS_HEAD.match(line.strip()):
            inside = True
            continue
        if inside and NEXT_HEAD.match(line):
            inside = False
        if inside:
            m = ROWS_LINE.match(line)
            if m:
                out.append((m.group(1)[2:], m.group(3).strip(), m.group(4).lower()))
    return out


def same_sha(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return len(a) >= 7 and len(b) >= 7 and (a.startswith(b) or b.startswith(a))


# ---- the act -------------------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)


def _commit(rel: str, msg: str) -> str | None:
    vault = config.vault()
    for attempt in range(4):
        a = _git(vault, "add", "--", rel)
        if a.returncode == 0:
            c = _git(vault, "-c", "user.name=atlas", "-c", "user.email=atlas@local",
                     "commit", "-q", "-m", msg, "--", rel)
            if c.returncode == 0:
                return _git(vault, "log", "-1", "--format=%h").stdout.strip() or None
            if "index.lock" not in (c.stderr or ""):
                common.log("rowdone", f"commit failed: {c.stderr.strip()[:200]}")
                return None
        elif "index.lock" not in (a.stderr or ""):
            common.log("rowdone", f"add failed: {a.stderr.strip()[:200]}")
            return None
        time.sleep(0.3 * (attempt + 1))
    return None


def locate(qid: str, files: list[Path]) -> list[tuple[Path, int]]:
    hits = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if row_identity(line) == qid:
                hits.append((f, i))
    return hits


def _notice(lane: str, owner: str, qid: str, sha: str, by: str, rel: str, prefixes: list[str]) -> str | None:
    """Write the foreign-row notice into THIS lane's outbox; its vault-relative path, or None."""
    day = common.today()
    slug = re.sub(r"[^a-z0-9-]+", "-", qid.lower()).strip("-")
    out_rel = f"{config.channels_rel()}/{lane}/N-{day}-rowdone-{slug}.md"
    if not common.path_in_partition(out_rel, prefixes):
        return None
    p = config.vault() / out_rel
    if p.exists():
        return out_rel
    body = (f"# N-{day}-rowdone-{slug}\n"
            f"- from: {lane}\n- to: {owner}\n- kind: fact\n- refs: [{rel}]\n\n"
            f"Row `q:{qid}` in `{rel}` has its merge on main: `{sha}` (checked with "
            f"`git merge-base --is-ancestor {sha} main`), named by the `## ROWS:` line of a handoff "
            f"from session `{by}`. The row is still `[ ]`. That queue file is {owner}'s, so this lane "
            f"did not edit it; the row is yours to flip or to refute.\n")
    try:
        rootguard.permit(p, "rowdone notice in this lane's own outbox")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    except (OSError, rootguard.OutsideRoot):
        return None
    _commit(out_rel, f"rowdone: notice to {owner} — q:{qid} merged {sha}")
    return out_rel


def _owed_update(mutate) -> list:
    """Read-modify-write the owed list under an exclusive lock; returns the list as written.
    Written only when it changed, so a Stop with nothing owed touches nothing."""
    import json
    path = config.state() / OWED_FILE
    try:
        rootguard.permit(path, "rowdone owed list in the state dir")
        rootguard.permit(config.state() / "rowdone-owed.lock", "rowdone owed list's lock in the state dir")
        config.state().mkdir(parents=True, exist_ok=True)
        with open(config.state() / "rowdone-owed.lock", "w") as lk:
            common.lock_file(lk)
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = []
            doc = [e for e in doc if isinstance(e, dict)] if isinstance(doc, list) else []
            new = mutate(list(doc))
            if new != doc:
                common.write_text_atomic(path, json.dumps(new, indent=1) + "\n")
            common.unlock_file(lk)
            return new
    except (OSError, rootguard.OutsideRoot) as e:
        common.log("hook-errors", f"rowdone-owed\t{e}")
        return []


def owe(qid: str, sha: str, by: str, repo: Path, lane: str | None, vault_sha: bool,
        handoff: str | None) -> bool:
    """Record a mark the vault was too busy to take. One entry per (row, sha, lane) — the lane
    is part of the key because only the entry's own lane ever applies it: were another lane's
    entry to stand in for the owner's, the owner would be told "owed" and the row would never flip.
    No lane, no entry: a session without a lane could not flip the row at a quiet vault either."""
    if not lane:
        return False

    def add(doc: list) -> list:
        if not any(e.get("qid") == qid and same_sha(str(e.get("sha")), sha) and e.get("lane") == lane
                   for e in doc):
            doc.append({"qid": qid, "sha": sha, "by": by, "repo": str(repo), "lane": lane,
                        "vault_sha": bool(vault_sha), "handoff": handoff, "at": time.time()})
        return doc
    _owed_update(add)
    return True


def apply_owed(lane: str | None, prefixes: list[str], ps_lines: list[str] | None = None,
               now: float | None = None) -> list[tuple[int, str]]:
    """Try this lane's owed marks, without waiting on the suite lock. A flipped row or a final
    refusal closes its entry; a retryable refusal keeps it until it is `OWED_DAYS` old."""
    global _WAIT_DEADLINE
    mine = [e for e in _owed_update(lambda d: d) if lane and e.get("lane") == lane]
    if not mine:
        return []
    before, _WAIT_DEADLINE = _WAIT_DEADLINE, time.monotonic()   # owed marks never wait on the lock
    try:
        return _apply_owed(mine, lane, prefixes, ps_lines, time.time() if now is None else now)
    finally:
        _WAIT_DEADLINE = before                      # ... and leave the process's own budget as it was


def _apply_owed(mine: list, lane: str, prefixes: list[str], ps_lines: list[str] | None,
                now: float) -> list[tuple[int, str]]:
    results, closed = [], []
    for e in mine:
        qid, sha, repo = str(e.get("qid")), str(e.get("sha")), Path(str(e.get("repo")))
        cfg = repo_config(repo)
        if cfg is None:
            code, line = USAGE, f"q:{qid} — rowdone is off in {repo}"
        else:
            code, line = flip(qid, sha, str(e.get("by") or "unknown-session"), repo, lane, prefixes,
                              queue_files(cfg), ps_lines, vault_sha=bool(e.get("vault_sha")),
                              handoff=e.get("handoff"))
        keep = code in RETRY and now - float(e.get("at") or 0) < OWED_DAYS * 86400
        if not keep:
            closed.append((qid, sha))
        results.append((code, ("owed, kept: " if keep else "owed, closed: ") + line))
        if code == PYTEST:
            break                                    # the vault is busy for every other entry too
    if closed:
        _owed_update(lambda d: [x for x in d if not (
            x.get("lane") == lane and (str(x.get("qid")), str(x.get("sha"))) in closed)])
    return results


def flip(qid: str, sha: str, by: str, repo: Path, lane: str | None, prefixes: list[str],
         files: list[Path], ps_lines: list[str] | None = None,
         vault_sha: bool = False, handoff: str | None = None) -> tuple[int, str]:
    """`vault_sha`: the row was finished by a vault commit, not a code merge (a filing, a doc or
    kernel edit) — `sha` is then verified as a commit IN THE VAULT (`git cat-file -e <sha>^{commit}`)
    instead of as an ancestor of a code repository's main. Everything after the check is the same."""
    def owed() -> str:
        ok = owe(qid, sha, by, repo, lane, vault_sha, handoff)
        return " — owed, applied at the next quiet Stop" if ok else ""
    if not SHA_OK.match(sha or ""):                  # before anything can be owed: never a sha later
        return NOT_ON_MAIN, f"REFUSED q:{qid} — {sha!r} is not a sha"
    busy = suite_busy(ps_lines)
    if busy:
        return PYTEST, f"REFUSED q:{qid} — {busy}; nothing written{owed()}"
    if vault_sha:
        v = _git(config.vault(), "cat-file", "-e", f"{sha}^{{commit}}")
        if v.returncode != 0:
            return NOT_ON_MAIN, f"REFUSED q:{qid} — {sha} is not a commit in the vault {config.vault()}"
    else:
        r = mergesha.verify(sha, repo)
        if r["state"] != mergesha.ON:
            return NOT_ON_MAIN, f"REFUSED q:{qid} — {sha} is {r['state']} in {repo}"
    try:
        hits = locate(qid, files)
    except Ambiguous as e:
        return AMBIGUOUS, f"REFUSED q:{qid} — {e}"
    if not hits:
        return ABSENT, f"FILE-IT {qid} — no queue row carries this id"
    if len(hits) > 1:
        where = ", ".join(f"{common.vault_rel(f) or f}:{i + 1}" for f, i in hits)
        return AMBIGUOUS, f"REFUSED q:{qid} — {len(hits)} rows carry this id ({where})"
    f, i = hits[0]
    rel = common.vault_rel(f) or str(f)
    lines = f.read_text(encoding="utf-8").split("\n")
    if not ROW_OPEN.match(lines[i]):
        return OK, f"q:{qid} already [x] in {rel} — nothing to do"
    if not lane or not prefixes:
        return FOREIGN, f"REFUSED q:{qid} — no declared lane here; {rel} left as it is"
    ok, owner = custody(rel, lane, prefixes)
    if not ok:
        note = _notice(lane, owner, qid, sha, by, rel, prefixes) if owner else None
        tail = f"notice {note}" if note else "no notice (owner unknown or outbox not ours)"
        return FOREIGN, f"REFUSED q:{qid} — {rel} is {owner or 'not this lane'}'s; {tail}"
    st = _git(config.vault(), "status", "--porcelain", "--", rel)
    if st.returncode != 0 or st.stdout.strip():
        return DIRTY, (f"REFUSED q:{qid} — {rel} has uncommitted changes; a path-limited commit "
                       f"would carry them{owed()}")
    before = "\n".join(lines)
    end = i + 1
    while end < len(lines) and lines[end].strip() and lines[end][:1] in (" ", "\t"):
        end += 1
    lines[i] = CHECKBOX.sub(r"\1[x]", lines[i], count=1)
    lines.insert(end, f"  - done: {sha} {common.today()} by {by}")
    after = "\n".join(lines)
    if not after.endswith("\n"):
        after += "\n"
    if f.read_text(encoding="utf-8") != before:                  # somebody wrote it meanwhile
        return DIRTY, f"REFUSED q:{qid} — {rel} changed while it was being edited; nothing written"
    tmp = f.with_name(f".{f.name}.rowdone-tmp")
    rootguard.permit(f, "rowdone row flip in a queue file")
    rootguard.permit(tmp, "rowdone row flip, atomic temp beside the queue file")
    tmp.write_text(after, encoding="utf-8")
    os.replace(tmp, f)
    what = f"vault commit {sha}" if vault_sha else f"merge {sha}"
    got = _commit(rel, f"rowdone: q:{qid} done — {what}{'' if vault_sha else ' on main'} (by {by})")
    if not got:
        return DIRTY, f"q:{qid} flipped in {rel} but the commit FAILED — the file is dirty; see rowdone.log"
    return OK, f"q:{qid} flipped [x] in {rel} (vault {got}; {what})"


def context_for(repo: Path, cwd: str | None, sid: str | None) -> tuple[str | None, list[str]]:
    lane, prefixes, _m = common.lane_for(cwd or str(repo))
    if not lane and sid:
        lane, prefixes, _m, _src = common.session_partition(sid)
    return lane, prefixes


def apply(sha: str, handoff: Path, repo: Path, lane: str | None, prefixes: list[str],
          ps_lines: list[str] | None = None) -> list[tuple[int, str]]:
    cfg = repo_config(repo)
    if cfg is None:
        return [(USAGE, f"rowdone is off in {repo} — no {MARKER} file")]
    try:
        text = Path(handoff).read_text(encoding="utf-8")
    except OSError as e:
        return [(USAGE, f"cannot read handoff {handoff}: {e}")]
    rows = [(q, s, h) for q, s, h in rows_in_handoff(text) if same_sha(h, sha)]
    if not rows:
        return [(NO_ROW, f"{Path(handoff).name} names no row with {sha}")]
    files = queue_files(cfg)
    return [flip(q, sha, s or "unknown-session", repo, lane, prefixes, files, ps_lines,
                 handoff=str(handoff)) for q, s, _h in rows]


def handoff_candidates(repo: Path, cfg: dict, sid: str | None, extra_roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for s in (common.handoff_paths(sid) if sid else []):
        found.append(Path(s))
    for root in [repo, *extra_roots]:
        for g in cfg.get("handoffs") or []:
            found.extend(Path(p) for p in _glob.glob(str(root / g)))
    uniq, seen = [], set()
    for p in found:
        k = str(p)
        if k not in seen and p.is_file():
            seen.add(k)
            uniq.append(p)
    return sorted(uniq, key=lambda p: p.stat().st_mtime, reverse=True)


def after_verify(sid: str, cmd: str, out: str, cwd: str | None) -> str | None:
    """PostToolUse: `verify-merge <sha>` printed "on main" → flip the rows its newest handoff names."""
    if "verify-merge" not in cmd:
        return None
    m = re.search(r"\b([0-9a-fA-F]{7,40}) — on main\b", out or "")
    if not m:
        return None
    sha = m.group(1)
    here = common.git_root(cwd) if cwd else None
    repo = common.main_checkout_of(here) if here else None
    cfg = repo_config(repo)
    if cfg is None:
        return None
    lane, prefixes = context_for(repo, cwd, sid)
    for h in handoff_candidates(repo, cfg, sid, [here] if here and here != repo else []):
        try:
            text = h.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(same_sha(x, sha) for _q, _s, x in rows_in_handoff(text)):
            res = apply(sha, h, repo, lane, prefixes)
            for code, line in res:
                common.log("rowdone", f"verify\t{code}\t{line}")
            return "Row marks for " + sha + " (" + h.name + "): " + " | ".join(l for _c, l in res)
    common.log("rowdone", f"verify\t{NO_ROW}\tno handoff names {sha} yet; the Stop door retries")
    return None


def at_stop(sid: str, cwd: str | None) -> list[tuple[int, str]]:
    """Stop: every handoff this session wrote; each ROWS line whose sha is on main is flipped."""
    results: list[tuple[int, str]] = []
    for s in common.handoff_paths(sid):
        h = Path(s)
        root = common.git_root(h.parent)
        repo = common.main_checkout_of(root) if root else None
        if repo is None or repo_config(repo) is None or not h.is_file():
            continue
        lane, prefixes = context_for(repo, cwd, sid)
        try:
            rows = rows_in_handoff(h.read_text(encoding="utf-8"))
        except OSError:
            continue
        for sha in dict.fromkeys(x for _q, _s, x in rows):
            for code, line in apply(sha, h, repo, lane, prefixes):
                results.append((code, line))
                common.log("rowdone", f"stop\t{code}\t{line}")
    # ROWDONEQ-1: marks an earlier landing could not write, tried now — unless this session's own
    # rows were just refused as busy, which answers the question for the owed ones too.
    if not any(c == PYTEST for c, _l in results):
        lane, prefixes = context_for(Path(cwd or os.getcwd()), cwd, sid)
        for code, line in apply_owed(lane, prefixes):
            results.append((code, line))
            common.log("rowdone", f"owed\t{code}\t{line}")
    return results


def subject_merges(repos: list[Path], days: int = SUBJECT_DAYS) -> dict:
    """{q-id: (repo, sha)} from commit SUBJECTS on `main` of each repo within `days` (newest wins)."""
    out: dict = {}
    for repo in repos:
        if not (Path(repo) / ".git").exists():
            continue
        # ★ `--since-as-filter`, never `--since`: `--since` STOPS the walk at the first commit older
        # than the cutoff, so one back-dated commit on main hides every commit behind it in the walk
        # (the fixture's 2020-dated merge hid two fresh ones). The filter reads every commit.
        p = _git(Path(repo), "log", "main", f"--since-as-filter={days}.days", "--format=%h%x09%s")
        if p.returncode != 0:
            continue
        for line in reversed(p.stdout.splitlines()):          # oldest first, so the newest wins
            sha, _t, subj = line.partition("\t")
            for m in SUBJECT_ID.finditer(subj):
                out[m.group(1) or m.group(2)] = (str(repo), sha)
    return out


def open_row_ids(files: list[Path]) -> list[tuple[str, Path]]:
    out = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                rid = row_identity(line)
            except Ambiguous:
                continue
            if rid and ROW_OPEN.match(line):
                out.append((rid, f))
    return out


def subject_scan(sid: str, repo: Path, lane: str | None, prefixes: list[str],
                 repos: list[Path] | None = None, ps_lines: list[str] | None = None) -> list[tuple[int, str]]:
    """Stop: open rows whose id is in a merge SUBJECT on main → flip ours, record theirs."""
    cfg = repo_config(repo)
    if cfg is None or not lane:
        return []
    busy = suite_busy(ps_lines)
    if busy:
        return [(PYTEST, f"subject scan skipped — {busy}")]
    vault = config.vault()
    if repos is None:
        repos = [repo] + [r for r in common.fleet_repos() if not _same_path(r, vault) and not _same_path(r, repo)]
    merges = subject_merges(repos)
    files = queue_files(cfg)
    results, stale = [], {}
    for qid, f in open_row_ids(files):
        hit = merges.get(qid)
        if not hit:
            continue
        mrepo, sha = hit
        rel = common.vault_rel(f) or str(f)
        ok, owner = custody(rel, lane, prefixes)
        if ok:
            results.append(flip(qid, sha, f"session {sid[:8]} (merge subject)", Path(mrepo), lane,
                                prefixes, files, ps_lines))
        else:
            note = _notice(lane, owner, qid, sha, f"merge subject in {Path(mrepo).name}", rel, prefixes) if owner else None
            stale[qid] = {"owner": owner, "repo": mrepo, "sha": sha, "queue": rel, "notice": note,
                          "seen": common.today()}
            results.append((FOREIGN, f"QUEUE-STALE-OPEN {qid} {mrepo} {sha} — {rel} is {owner or 'not this lane'}'s"))
    _write_stale(stale)
    return results


def _same_path(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def _write_stale(stale: dict) -> None:
    try:
        d = config.state()
        tmp = d / (STALE_FILE + ".tmp")
        rootguard.permit(tmp, "rowdone stale-open record in the state dir")
        rootguard.permit(d / STALE_FILE, "rowdone stale-open record in the state dir")
        d.mkdir(parents=True, exist_ok=True)
        tmp.write_text(__import__("json").dumps(stale, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, d / STALE_FILE)
    except (OSError, rootguard.OutsideRoot) as e:
        common.log("rowdone", f"stale-open write failed: {e}")


def owed_line(lane: str | None) -> list[str]:
    mine = [e for e in _owed_update(lambda d: d) if e.get("lane") == lane]
    if not mine:
        return []
    shown = ", ".join(f"q:{e.get('qid')} ({e.get('sha')})" for e in mine[:6])
    return [f"- Row marks owed by {lane}, written while the vault was busy: {len(mine)} — {shown}"
            + (" …" if len(mine) > 6 else "") + ". They are applied at the next quiet Stop of a "
            f"{lane} session, or now with `rowdone.py owed`."]


def facts_lines(lane: str | None, limit: int = 12) -> list[str]:
    """SessionStart: the stale-open rows recorded for THIS lane by any lane's Stop scan, after
    the marks this lane still owes (ROWDONEQ-1)."""
    if not lane:
        return []
    return owed_line(lane) + _stale_lines(lane, limit)


def _stale_lines(lane: str, limit: int) -> list[str]:
    try:
        data = __import__("json").loads((config.state() / STALE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    mine = sorted((q, v) for q, v in data.items() if isinstance(v, dict) and v.get("owner") == lane)
    if not mine:
        return []
    out = [f"- Queue rows of {lane} still `[ ]` whose id is in a merge subject on main (flip them with a "
           f"`done:` note, or refute): {len(mine)}."]
    for q, v in mine[:limit]:
        out.append(f"  QUEUE-STALE-OPEN {q} {v.get('repo')} {v.get('sha')} ({v.get('queue')}"
                   + (f"; notice {v['notice']}" if v.get("notice") else "") + ")")
    if len(mine) > limit:
        out.append(f"  … and {len(mine) - limit} more in {config.state() / STALE_FILE}")
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    repo_arg = None
    if "--repo" in argv:
        k = argv.index("--repo")
        repo_arg = argv[k + 1] if k + 1 < len(argv) else None
        del argv[k:k + 2]
    by = None
    if "--by" in argv:
        k = argv.index("--by")
        by = argv[k + 1] if k + 1 < len(argv) else None
        del argv[k:k + 2]
    vault_sha = None
    if "--vault-sha" in argv:
        k = argv.index("--vault-sha")
        vault_sha = argv[k + 1] if k + 1 < len(argv) else ""
        del argv[k:k + 2]
    if argv == ["owed"]:
        lane, prefixes, _m = common.lane_for(os.getcwd())
        res = apply_owed(lane, prefixes)
        for _c, line in res:
            print(line)
        print(f"owed entries tried: {len(res)}" + ("" if lane else " (no lane declared here)"))
        return OK
    usage = ("usage: rowdone.py apply <sha> <handoff> [--repo R] · rowdone.py owed · "
             "rowdone.py flip <q-id> <sha> --by <session> [--repo R] · "
             "rowdone.py flip <q-id> --vault-sha <sha> --by <session>")
    if vault_sha is not None:
        if argv[:1] != ["flip"] or len(argv) != 2 or not by or not vault_sha:
            print(usage, file=sys.stderr)
            return USAGE
        argv.append(vault_sha)
    elif len(argv) != 3 or argv[0] not in ("apply", "flip") or (argv[0] == "flip" and not by):
        print(usage, file=sys.stderr)
        return USAGE
    cwd = os.getcwd()
    root = Path(repo_arg).expanduser() if repo_arg else common.git_root(cwd)
    repo = common.main_checkout_of(root) if root else None
    cfg = repo_config(repo)
    if cfg is None:
        print(f"rowdone is off in {repo} — no {MARKER} file")
        return USAGE
    lane, prefixes, _m = common.lane_for(cwd)
    if argv[0] == "apply":
        res = apply(argv[1], Path(argv[2]).expanduser(), repo, lane, prefixes)
    else:
        qid = argv[1][2:] if argv[1].startswith("q:") else argv[1]
        res = [flip(qid, argv[2], by, repo, lane, prefixes, queue_files(cfg),
                    vault_sha=vault_sha is not None)]
    for _c, line in res:
        print(line)
    bad = [c for c, _l in res if c != OK]
    return bad[0] if bad else OK


if __name__ == "__main__":
    raise SystemExit(main())
