#!/usr/bin/env python3
"""session_start.py — what every session should know before its first prompt, as facts.

Writes <state>/session-start.json (session id, cwd, lane, vault HEAD at start, boot bytes) — the
debriefer reads `vault_head` to scope "what changed this session" to the session instead of the
last commit. Prints one short additionalContext block: the lane and its partition, the
partition-hook mode, vault dirt, what this session's @-import chain cost to boot, and — only when
`owner_pages_status` is configured (see config.py) — that script's answered-pages summary, and, where an
`answer_router` is configured, what this boot CARRIED back to the work it belongs to. With no
such script configured the hook says nothing about it at all, rather than guessing where one
might live.

★ THE OFFER, FOR A PROJECT WITH NO MEMORY. Where no marker resolves, the cwd is inside a git repo
and the repo has not been declined, the block tells the model to ask the user ONE line — "This
project has no memory yet — create one? (yes / no / never)" — and names the exact command for each
answer. With no vault on the machine yet (a first install) the question also names the folder a
`yes` creates (PLUGDIR-1 (e)): otherwise a directory install would never be offered anything.
The facts block cannot prompt; the conversation can, and that is the right
surface for the one question. Shown at most once per repo per day (a stamp in the state dir), so a
`no` is never nagged; every other time, and in a non-repo directory, the plain one-line fact.

★ THE OPERATING RULES. A stranger's install has an empty CLAUDE.md and no prose anywhere telling
a session what the six vault files are for. Rather than asking them to write that prose, the hook
injects `rules/operating-rules.md` as additionalContext — the memory-writing discipline itself,
generic, under 3,000 B. It is injected at `startup` ONLY: a `resume` or a `compact` continues a
session that already has it, and re-sending it there would buy nothing and cost tokens every time.
`inject_rules: false` in the config file (or GEDAECHTNIS_INJECT_RULES=0) turns it off for an
install whose own CLAUDE.md already says all of this. And they are injected only where the person
said yes — inside the vault or a repo carrying an `.atlas-lane` marker (PLUGDIR-1): a stranger's
unrelated session is never steered by this plugin's rules.

★ THE BOOT-COST FACT. A session cannot see its own boot: the @-imported files arrive as context
with no size attached, so the one number that would tell a session whether its memory files have
quietly become a document is the one number it never has. This hook measures it — the user-level
CLAUDE.md and this cwd's CLAUDE.md, plus everything they @-import, transitively — and states it
as a fact, in bytes and in files. It is a MEASUREMENT and nothing else: no threshold, no warning,
no advice. What a session or a later pass does with the number is a separate decision, and one a
week of recorded numbers should inform rather than a constant chosen today.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
import names
from common import read_input, context, log, lane_for, git_root, guarded
import common
import procs
# VAULT, STATE deliberately NOT imported by name: a `from` import binds the value
# ONCE, which is the frozen-path defect this package was bitten by. Read through the
# module object (common.VAULT) so PEP 562 re-resolves on every access.

EV = "SessionStart"

# An @-import is a line that is nothing but `@` + an absolute-or-tilde path. Anything else — a
# `@-imported` mention in prose, an email address, a decorator, a relative path — is not one.
IMPORT_LINE_RE = re.compile(r"^@([~/][^\s]*)\s*$")

# Claude Code follows @-imports a handful of hops deep. Bounded so a cyclic chain cannot spin;
# the `seen` set already makes a cycle terminate, and this bounds a pathological deep chain too.
MAX_IMPORT_HOPS = 8


def _readable(raw, base=None) -> Path | None:
    """Resolve one @-import target to an existing file, or None. A missing import is SKIPPED —
    Claude Code does not fail a session over one, and neither may this."""
    try:
        p = Path(os.path.expanduser(str(raw)))
        if base and not p.is_absolute():
            p = Path(base) / p
        p = Path(os.path.realpath(str(p)))
        return p if p.is_file() else None
    except OSError:
        return None


def boot_chain_files(entrypoints, max_hops=MAX_IMPORT_HOPS):
    """-> [(path, size_bytes)] for the closure of `entrypoints` under @-import.

    The walk `boot_chain` sums, exposed so a caller that needs to NAME the chain's members (the
    maintenance hook's byte arm) does not write a second @-import resolver. Two answers to "what
    does a session load" inside one package is the duplicated-fact failure this package records.

    The entrypoints are COUNTED: they are loaded into the session as surely as anything they
    pull in, and a boot cost that omits them is not the boot cost. Deduplicated by realpath, so
    a file reachable from both chains — the common case for a vault file — is counted once, and
    a symlinked or worktree path cannot become a second name for one file.
    """
    seen, frontier = set(), []
    for entry in entrypoints:
        p = _readable(entry)
        if p and p not in seen:
            seen.add(p)
            frontier.append(p)
    for _ in range(max_hops):
        nxt = []
        for path in frontier:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:                      # an unreadable hop is a missing branch,
                continue                         # never a crash
            for line in text.splitlines():
                m = IMPORT_LINE_RE.match(line.strip())
                if not m:
                    continue
                tgt = _readable(m.group(1))
                if tgt and tgt not in seen:
                    seen.add(tgt)
                    nxt.append(tgt)
        if not nxt:
            break
        frontier = nxt
    members = []
    for p in sorted(seen):
        try:
            members.append((p, p.stat().st_size))
        except OSError:                          # vanished between the walk and the stat
            pass
    return members


def boot_chain(entrypoints, max_hops=MAX_IMPORT_HOPS):
    """-> (total_bytes, n_files), the boot-cost fact's own two numbers."""
    members = boot_chain_files(entrypoints, max_hops)
    return sum(size for _, size in members), len(members)


def sh(args, cwd=None, timeout=8):
    try:
        p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 124, "", str(e)


def main() -> None:
    inp = read_input()
    cwd = inp.get("cwd") or os.getcwd()
    sid = inp.get("session_id", "-")
    lane, prefixes, marker = lane_for(cwd)
    rc, head, _ = sh(["git", "-C", str(common.VAULT), "rev-parse", "HEAD"])
    vault_head = head if rc == 0 else None
    import hashlib as _h
    _key = _h.sha1(cwd.encode()).hexdigest()[:10]
    if inp.get("source") == "compact":
        # CONTEXTMSG-1: a compaction opens a new context window. The context notices fire once per
        # WINDOW, so what fired in the old one, and the old window's floor, are cleared here.
        try:
            import context_cap
            context_cap.new_window(sid, inp.get("transcript_path"))
        except Exception:
            import traceback as _tb
            log("hook-errors", "session_start/context_cap: " + _tb.format_exc().replace("\n", " | "))
    if inp.get("source") in ("compact", "resume"):
        try:                                            # the session began earlier: keep ITS start head, do not re-stamp
            prev = json.loads((common.STATE / f"session-start-{_key}.json").read_text(encoding="utf-8"))
            if prev.get("vault_head"):
                vault_head = prev["vault_head"]
        except (OSError, json.JSONDecodeError):
            pass
    rc, dirt, _ = sh(["git", "-C", str(common.VAULT), "status", "--porcelain"])
    n_dirty = len([l for l in dirt.splitlines() if l.strip()]) if rc == 0 else None
    mode = "warn"
    try:
        v = (common.STATE / "partition.mode").read_text(encoding="utf-8").strip().lower()
        mode = v if v in ("warn", "deny") else "warn"
    except OSError:
        pass
    # ONE walk, two consumers: the `boot:` fact needs the totals and the staleness arm below needs
    # the member list. `boot_chain_files`'s own docstring exists because two answers to "what does a
    # session load" inside one package is this package's recorded duplicated-fact failure — and
    # calling both helpers with the same entrypoints re-reads and re-resolves the whole chain.
    _boot_members = boot_chain_files([config.USER_MEMORY, Path(cwd) / "CLAUDE.md"])
    boot_bytes, boot_files = sum(sz for _f, sz in _boot_members), len(_boot_members)
    common.STATE.mkdir(parents=True, exist_ok=True)
    import hashlib
    key = hashlib.sha1(cwd.encode()).hexdigest()[:10]
    # ★ `pid` is what lets a LATER reader tell a live session from a leftover record. The sweep in
    # `wtsweep.py` refuses to remove a worktree that is some session's `cwd`, and without a pid it
    # could only ask "does a record name this directory" — which every record ever written still
    # answers yes to, because these files are per-session and nothing prunes them. A record with no
    # pid (written before this line existed, or by a session whose parent chain could not be read)
    # is treated by that sweep as CANNOT-TELL and protects the worktree anyway; the pid is what
    # lets protection eventually expire, not what creates it.
    # ONE parent-chain walk, read once and used twice. Calling `claude_pid()` again for the name
    # below doubled a `ps` walk per session for a value that cannot have changed between the two
    # lines — and the second call could legitimately answer differently, which would have put a pid
    # and a name from two different processes in one record.
    # PROCSHOST-1: `own_pid`, the session's own process. A host's pid here kept every thread's
    # record alive for as long as the host ran, and gave every thread the host's `--name`.
    claude_pid = procs.own_pid()
    payload = json.dumps({
        "session_id": sid, "cwd": cwd, "lane": lane, "marker": str(marker) if marker else None,
        "pid": claude_pid,
        # ★ The launch `--name`, recorded because NOTHING ELSE ON DISK CARRIES IT. The transcript
        # records the conversation and the payload carries a uuid; the name — the only handle one
        # session has for another — exists solely on the process's command line, and that line is
        # gone the moment the process exits. Both the idle-notify door and the launcher's trailing
        # notify need to find this session's record BY NAME after it has ended.
        "name": procs.session_name(claude_pid),
        "vault_head": vault_head, "partition_mode": mode, "boot_bytes": boot_bytes,
        "boot_files": boot_files, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1)
    sid_file = common.STATE / f"session-start-{common.safe_sid(sid)}.json"
    if not sid_file.exists() or inp.get("source") == "startup":
        common.write_text_atomic(sid_file, payload)                              # per SESSION: two sessions in one repo never collide
    common.write_text_atomic(common.STATE / f"session-start-{key}.json", payload)   # per cwd, for a reader that knows only its cwd
    common.write_text_atomic(common.STATE / "session-start.json", payload)            # the latest
    _sid = common.safe_sid(sid)
    lines = ["Gedächtnis session facts (hook-generated):", f"- Session id {_sid}; this session's start record is ~/.claude/gedaechtnis/session-start-{_sid}.json (pass that path to the debriefer)."]
    offered = False
    if lane:
        lines.append(f"- Lane {lane}, declared by {marker}; vault write partition: {', '.join(prefixes)}.")
        # STOPCOMMIT-2: partition files the last Stop of this lane left uncommitted (a script's or
        # a shell's writes are not in any touched set, so no Stop ever commits them by itself).
        try:
            import commit as _commit
            _pd = _commit.partition_dirty_line(lane)
            if _pd:
                lines.append(_pd)
        except Exception:
            pass
        _held = common.hold_source(marker)
        if _held:
            _where = (f"{Path(marker).parent / common.HOLD_FILE} exists" if _held == common.HOLD_FILE
                      else f"{marker} has a hold: line")
            lines.append(f"- Vault commits are HELD for this lane ({_where}): the vault files you "
                         "write stay uncommitted at Stop until the hold is removed.")
        lines.extend(memory_files_lines(prefixes))
    else:
        _nm = no_memory_line(cwd)
        lines.append(_nm)
        offered = _nm != NO_MARKER
    try:
        import deletedoor as _dd
        if _dd.enabled():
            lines.append(_dd.facts_line())
    except Exception:
        pass
    try:
        import kernelentry as _ke
        if _ke.enabled():
            lines.append(_ke.facts_line())
    except Exception:
        pass
    try:
        import ruledoors as _rd
        lines.append(_rd.facts_line())
    except Exception:
        pass
    lines.append(f"- Partition hook mode: {mode.upper()} (" + ("logs would-be refusals to ~/.claude/gedaechtnis/partition.log, blocks nothing" if mode == "warn" else "writes outside the partition are refused") + ").")
    if vault_head:
        lines.append(f"- Vault {common.VAULT}: HEAD at session start {vault_head[:8]}; dirty paths in the vault right now: {n_dirty}.")
    elif common.VAULT.is_dir():
        lines.append(f"- Vault {common.VAULT}: no commit yet" + ("" if (common.VAULT / ".git").exists() else " (not a git repository)") + ".")
    elif offered:                                      # the offer already names the folder and the command
        lines.append(f"- Vault {common.VAULT} does not exist yet.")
    else:
        lines.append(f"- Vault {common.VAULT} does not exist: run `python3 {Path(__file__).resolve().parent.parent / 'init.py'}` in this repo to create it.")
    if boot_files:
        lines.append(f"- boot: {boot_bytes:,} B across {boot_files} files (@-import chain) — the user-level "
                     "CLAUDE.md, this repo's, and everything they @-import, transitively. A measurement, not a budget.")
    # IDLENOTIFY-1 — sessions this seat ignited that have since reported in, and (separately) a
    # `session_name_pattern` that does not compile. The second is not a detail of the first: a
    # broken pattern means every managed session is silently unmanaged, which looks from here
    # exactly like a quiet fleet.
    try:
        import idlenotify
        _prob = idlenotify.pattern_problem()
        if _prob:
            lines.append(f"- {_prob}.")
        _mail = idlenotify.facts_line()
        if _mail:
            lines.append(_mail)
        # TERMOVERLOAD-1's parked messages are no longer COUNTED here: `deliver.py` shows them
        # (CONTEXTMSG-1), on this same event, and a count beside the text would say it twice.
    except Exception:                                  # a notify bug must not cost a session its facts
        import traceback as _tb
        log("hook-errors", "session_start/idlenotify: " + _tb.format_exc().replace("\n", " | "))
    # What the previous session end computed about upkeep. Read, never recomputed: measuring the
    # vault at session START would delay the first prompt for something that changed at the end of
    # the last one. Says nothing at all when nothing fired — see maintenance.facts_lines.
    try:
        import maintenance
        _m = maintenance.read_state()
        if _m:
            lines.extend(maintenance.facts_lines(_m))
    except Exception:                                  # a maintenance bug must not cost a session its facts
        # ...but it must not vanish either. Every other guarded block in this package logs before
        # it swallows (`common.guarded`); a bare `pass` here would drop a DUE cleanup with nothing
        # anywhere to distinguish that from nothing having fired.
        import traceback as _tb
        log("hook-errors", "session_start/maintenance: " + _tb.format_exc().replace("\n", " | "))
    # The same contract for the boot check: read what the last session end computed, say nothing
    # when nothing failed, and never let this organ's bug cost the session its other facts.
    try:
        import boot_check
        _b = json.loads(boot_check.state_path().read_text(encoding="utf-8"))
        lines.extend(boot_check.facts_lines(_b))
    except FileNotFoundError:
        pass                                           # no session has ended yet; nothing to report
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/boot_check: " + _tb.format_exc().replace("\n", " | "))
    # STALETRUTH M4 — "superseded since your source". The one staleness class a mid-session reader
    # structurally cannot see: by the time the session runs, its sources are loaded and believed.
    # Sources are the @-import chain already resolved above for the `boot:` line, so the input is
    # free; see boot_citations.py for the second condition that keeps this quiet.
    try:
        import boot_citations
        _docs = [f for f, _sz in _boot_members]
        # The row arm is OFF at boot (boot_citations: closed rows are provenance in an always-loaded
        # file, 75% of them here). Its inputs are therefore NOT BUILT: reading the queue tree to
        # hand `stale_citations` a value it provably never looks at cost this vault a 2.4 MB read on
        # every single boot. If the arm is ever switched on here, its input has to be built here too.
        _ledger = config.authority_log()
        _recs, _rx = [], None
        if _ledger is not None:
            try:
                import authority
                _recs = authority.records(_ledger.read_text(encoding="utf-8", errors="replace"))
                _rx = authority.SUPERSEDED_BY
            except (OSError, ImportError):
                _recs, _rx = [], None
        lines.extend(boot_citations.facts_lines(boot_citations.stale_citations(
            _docs, "", [], _recs, _ledger, _rx)))
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/boot_citations: " + _tb.format_exc().replace("\n", " | "))

    # A threshold this vault raised for itself, and anything it MEANT to raise and did not. Silent
    # when the vault configures no limits; a rejected override is never silent, because the whole
    # failure mode of an override layer is looking configured and doing nothing.
    try:
        import limits as _limits
        _ov, _probs = _limits.overridden(), _limits.problems()
        if _ov:
            lines.append("- Vault limits (config.json, over the shipped thresholds): "
                         + ", ".join(f"{k}={v}" for k, v in sorted(_ov.items())) + ".")
        for _why in _probs:
            lines.append("- Vault limits NOT APPLIED: " + _why + ".")
        # Same contract for the vault's SHAPE: a topology key that is present and unusable turns
        # a rule off while reading as configured, which is the one thing this line exists to stop.
        for _why in config.topology_problems():
            lines.append("- Vault topology NOT APPLIED: " + _why)
        # And a rule whose key is not declared at all: OFF, which reads exactly like ON from inside
        # a session. One line naming every such rule, never silence (TOPOLOGY-2).
        _inert = config.inert_topology_doors()
        if _inert:
            lines.append(f"- Vault topology: {len(_inert)} of {len(config.TOPOLOGY_DOORS)} path "
                         "rules are OFF because config.json does not declare their key: "
                         + "; ".join(f"{rule} (`{key}`)" for key, rule in _inert)
                         + ". A vault without such folders needs none of them; one that has them "
                         "names them under `topology`.")
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/limits: " + _tb.format_exc().replace("\n", " | "))
    ops = config.owner_pages_status()                  # None unless config.json names a script
    if ops:
        rc, out, err = sh([config.python(), str(ops), "--json"], timeout=8)
        if rc in (0, 1) and out:                       # the script exits 1 when it has findings; 2 = UNREADABLE
            try:
                j = json.loads(out)
                st = j.get("status") or j.get("verdict") or ""
                counts = {k: v for k, v in j.items() if isinstance(v, int)}
                lines.append(f"- Answered review pages (swept by name): {st or 'see counts'} {json.dumps(counts) if counts else ''}".rstrip())
            except json.JSONDecodeError:
                lines.append(f"- Answered review pages: {ops.name} returned non-JSON; UNMEASURED.")
        elif rc == 2:
            lines.append(f"- Answered review pages: UNREADABLE ({ops.name} exit 2 — a permission-blocked listing looks empty; do not read it as zero).")
        else:
            lines.append(f"- Answered review pages: UNMEASURED ({ops.name} rc={rc}).")
    # ★ NOTICING IS NOT CARRYING. The block above COUNTS answered pages; this one
    # applies the ones there is exactly one place to apply. Silent when this vault configures no
    # router, which is the shipped state — see `config.answer_router`.
    try:
        import answers as _answers
        _router = config.answer_router()
        if _router:
            # The SESSION's own marker, never this package's or the router's repo's: a router
            # invoked on behalf of another session would otherwise inherit the wrong lane and
            # mark, or refuse, the wrong rows.
            _line = _answers.sweep(config.python(), _router, marker)
            if _line:
                lines.append(_line)
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/answers: " + _tb.format_exc().replace("\n", " | "))
    # NIGHTLY-1 — the last nightly invariant run. The runner has appended a record a night for
    # weeks and nothing read it; a check whose result reaches nobody is a cost, not a check.
    # Silent when no log is configured, which is the shipped state.
    try:
        import nightly as _nightly
        _nl = _nightly.facts_line()
        if _nl:
            lines.append(_nl)
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/nightly: " + _tb.format_exc().replace("\n", " | "))
    # KERNELFACET-1 — the facets of this session's region and what loads each, one line apiece, so
    # a session knows they exist without paying for them. Silent for a region with none.
    try:
        import lazy_body as _lazy_body
        lines.extend(_lazy_body.facts_lines(cwd))
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/facets: " + _tb.format_exc().replace("\n", " | "))
    # WTSWEEP-1 — the worktrees the LAST Stop could not remove, and why. Only the kept ones are
    # named: a line announcing each successful removal would be a line that appears every session
    # and says nothing a person must act on, which is how a facts block earns being skimmed.
    _wt = None
    try:
        import wtsweep as _wtsweep
        _wt = _wtsweep.facts_line()
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/wtsweep: " + _tb.format_exc().replace("\n", " | "))
    # STALLBRIEF-1 — the managed sessions that stopped moving, with the worktree line folded in
    # beneath them. Absent when `session_name_pattern` is unset or no managed session is live;
    # the worktree line then prints on its own, exactly as before.
    _block = [_wt] if _wt else []
    try:
        import stallbrief as _stallbrief
        _block = _stallbrief.block(_wt)
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/stallbrief: " + _tb.format_exc().replace("\n", " | "))
    lines.extend(_block)
    if lane:
        # Inbox rows in this lane's regions (paths declared as `<Umbrella>/<Region>` dirs)
        for pre in prefixes:
            if pre.count("/") == 1 and not pre.endswith(".md"):
                ib = common.VAULT / pre / "Inbox.md"
                if ib.is_file():
                    n = sum(1 for l in ib.read_text(encoding="utf-8").splitlines() if l.startswith("- "))
                    if n:
                        lines.append(f"- {pre}/Inbox.md holds {n} unfolded row(s) written by other lanes about work in this region: read and fold them first.")
        led = Path(__file__).resolve().parent.parent / "ledger.py"
        if led.is_file():
            rc, out, _ = sh([sys.executable, str(led), "read", "--to", lane, "--unacked", "--json"], timeout=8)
            if rc == 0 and out.strip():
                try:
                    rows = json.loads(out)
                    if rows:
                        lines.append(f"- Channels ledger: {len(rows)} row(s) addressed to {lane} not yet answered (ids {rows[0]['id']} … {rows[-1]['id']}); they are re-announced every boot until `gedaechtnis/ledger.py ack --from {lane} <id>`.")
                except json.JSONDecodeError:
                    pass
    # AUTODONE-1 — rows of THIS lane another lane's Stop scan found merged but still open.
    try:
        import rowdone
        lines.extend(rowdone.facts_lines(lane))
    except Exception as e:                     # a fact organ never costs the block its other lines
        common.log("session_start", f"rowdone facts failed: {e}")
    envf = os.environ.get("CLAUDE_ENV_FILE")
    if envf:
        try:
            with open(envf, "a", encoding="utf-8") as fh:
                fh.write("export PYTHONDONTWRITEBYTECODE=1\n")
            lines.append("- PYTHONDONTWRITEBYTECODE=1 is set for this session's shells: no __pycache__/.pyc droppings inside arcs.")
        except OSError:
            pass
    # LESSONPUSH-3 — the BOOT arm of the lesson push. Last of the fact organs, because it is the
    # only one that is OPINION rather than fact: everything above is something about this vault
    # that is true, and these lines are a guess at what this session is about to do. Same contract
    # as the rest — it never raises, and a bug in it never costs the block its other lines.
    try:
        import lesson_push
        _bl = lesson_push.boot_notice(sid, lane, prefixes, _boot_members, opener_text(inp))
        if _bl:
            lines.append(_bl)
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/lesson_push: " + _tb.format_exc().replace("\n", " | "))
    log("session", f"{sid}\tlane={lane}\tcwd={cwd}\thead={vault_head}")
    tail = rules_block(inp.get("source"), cwd)
    # BOOTFACTS-1: the block's budget — long-tail lines behind /gedaechtnis-status over the cap, and
    # a preview warning past the harness's inline limit (factsbudget.py). Never costs the block.
    try:
        import factsbudget
        lines = factsbudget.apply(lines, tail, prefixes if lane else [], sid, cwd)
    except Exception:
        import traceback as _tb
        log("hook-errors", "session_start/factsbudget: " + _tb.format_exc().replace("\n", " | "))
    context(EV, "\n".join(lines + tail))


def opener_text(inp: dict) -> str | None:
    """This session's opening prompt, when the transcript already holds one. Usually None.

    ★ MEASURED, NOT ASSUMED. At a cold start the SessionStart hook runs BEFORE the first user
    record exists: over 97 sessions on the machine this was built on, the first user record was
    written after the hook in 96, median +0.86 s (the exception was a resume, whose transcript
    predated the hook by days). So this returns something only on a RESUME or a COMPACT, where the
    transcript is already on disk — which is exactly when it is most useful, because a resumed
    session's subject is settled. When it returns None the open queue rows are the proxy, which is
    what the row that ordered this arm named as the fallback.

    Bounded, and never raises: a transcript is a file another process is appending to."""
    tp = inp.get("transcript_path")
    if not isinstance(tp, str) or not tp:
        return None
    try:
        p = Path(os.path.expanduser(tp))
        if not p.is_file():
            return None
        with p.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 200:                          # the opener is at the top or it is not there
                    return None
                if '"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "user":
                    continue
                msg = rec.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, str):
                    return content[:20_000]
                if isinstance(content, list):
                    parts = [b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text"]
                    if parts:
                        return "\n".join(parts)[:20_000]
    except OSError:
        return None
    return None


def memory_files_lines(prefixes: list) -> list:
    """One fact line per declared partition prefix that is a REGION — a vault directory holding
    role files directly — naming its EXISTING role files as display labels ("Decisions
    (Canon.md)"), in `names.json`'s own row order. A prefix with no role file directly inside it
    (a shared folder's subpaths, a `.md` leaf naming one file) contributes no line: this
    is a fact about what memory the model can read here, not an inventory of the partition."""
    out = []
    for pre in prefixes:
        p = pre.rstrip("/")
        if not p or p.endswith(".md"):
            continue
        d = common.VAULT / p
        if not d.is_dir():
            continue
        present = [s for s in names.ordered() if (d / f"{s}.md").is_file()]
        if not present:
            continue
        out.append(f"- Memory files in {p}: " + " · ".join(names.label(s) for s in present) + ".")
    return out


NO_MARKER = ("- No .atlas-lane marker resolves from this cwd: this session has NO declared vault "
             "write partition (the Stop hook will commit nothing).")


def no_memory_line(cwd: str) -> str:
    """The one line a session with no lane gets — the plain fact, or, ONCE per repo per day, the
    offer to create a memory for it.

    The facts block cannot ask a question; the model can. So the block does not ask — it tells the
    model to ask, once, in one line, at the top of its first reply, and then to drop it. The offer
    appears only where it would be true and welcome: inside a git repo (a memory belongs to a
    project, not to whatever directory a terminal happened to open in), and in a repo the user has
    not already said no to. With no vault on the machine yet the question names the folder a `yes`
    creates — a directory install starts with no vault, and would otherwise never be asked. A stamp file caps it at one
    offer per repo per day, because a `no` that gets asked again at the next boot is a nag, and a
    nagging tool is turned off."""
    repo = git_root(cwd)
    if repo is None:
        return NO_MARKER
    first_install = not common.VAULT.is_dir()          # PLUGDIR-1 (e): a yes also creates the vault
    if str(repo) in config.declined() or str(Path(os.path.realpath(str(repo)))) in config.declined():
        return NO_MARKER
    import hashlib
    stamp = common.STATE / ("offer-" + hashlib.sha1(str(repo).encode()).hexdigest() + ".stamp")
    today = common.today()
    try:
        if stamp.read_text(encoding="utf-8").strip() == today:
            return NO_MARKER                       # already offered today: the fact, not the offer
    except OSError:
        pass
    try:
        common.STATE.mkdir(parents=True, exist_ok=True)
        stamp.write_text(today + "\n", encoding="utf-8")
    except OSError:
        pass
    init_py = Path(__file__).resolve().parent.parent / "init.py"
    if first_install:
        return ('- This project has no memory yet, and this machine has none set up. Ask the user ONCE, '
                'in one line, at the start of your first reply: "This project has no memory yet — '
                f'create one? It would be kept in {common.VAULT}. (yes / no / never)". On yes run '
                f'`python3 {init_py} --yes --repo {repo}` (it creates that folder too); on never run '
                f'`python3 {init_py} --decline --repo {repo}`; on no, do not ask again this session. '
                'Say nothing more about it.')
    return ('- This project has no memory yet. Ask the user ONCE, in one line, at the start of your '
            'first reply: "This project has no memory yet — create one? (yes / no / never)". On yes '
            f'run `python3 {init_py} --yes --repo {repo}`; on never run `python3 {init_py} '
            f'--decline --repo {repo}`; on no, do not ask again this session. Say nothing more '
            'about it.')


ONLY_IF_RE = re.compile(r"<!--only-if:([a-z_]+)-->(.*?)<!--/only-if-->", re.S)
# Any sentinel the pass above did not consume — one that is unclosed, or nested inside a span that
# was kept, since `re.sub` makes ONE pass over the original string and never revisits what it
# emitted. Such a marker would otherwise travel verbatim into the model's context. Stripping it
# KEEPS the surrounding text, which is the same safe direction as an unknown condition name: a
# malformed sentinel leaves a rule in, never takes one out.
LEFTOVER_SENTINEL_RE = re.compile(r"<!--/?only-if(?::[a-z_]+)?-->")


def rules_conditions() -> dict[str, bool]:
    """Which conditional paragraphs of the rules apply to THIS vault.

    A condition is only ever "this paragraph cannot apply here" — never "this user probably does
    not need to be told". The distinction is the whole safety of the mechanism: a session that is
    not told a rule does not follow it, and nothing says so afterwards.

    - `kernel` — the vault has at least one boot file. Telling a session about a file class its
      vault does not contain is describing somebody else's vault.
    - `reviewer` — the `memory-reviewer` agent is installed. A trimmed install that lacks it
      cannot route anything to it, and an instruction to use a tool that is not there is worse
      than silence: it invites the model to invent a substitute."""
    plugin = Path(__file__).resolve().parent.parent
    has_kernel = False
    # ★ THE EXCLUSIONS ARE recall's, not a dot-check of our own. A retired boot file legitimately
    # ends up inside a `Cleanup/` bundle — this package's own rules say nothing is deleted, it is
    # moved there — and a bespoke walk would then report a boot file to a vault that, by every
    # other definition in this package (`maintenance.memory_files()` unions exactly these four
    # sets), no longer has one. `.git` and `.gedaechtnis` were excluded only because they happen
    # to start with a dot, which is coincidence rather than agreement.
    try:
        sys.path.insert(0, str(plugin))
        import recall
        skip = (set(recall.SKIP_DIRS) | set(recall.non_memory_dirs())
                | set(recall.GENERATED_DIRS))
        # ★ `REMOVED_DIRS` is NOT unioned into this set, and that is the fix rather than an
        # omission. It is the one set whose members are PREFIXES: the pass output this package
        # writes is `Cleanup/`, and the convention around it is `Cleanup 2026-09-15 <what>/`. A
        # membership test excludes the bare name and walks straight into every dated bundle — so
        # this function reported a retired boot file to a vault that, by every other definition in
        # this package, no longer has one. Exactly what the comment above says it exists to
        # prevent, asserted in prose and not implemented, for as long as the union was here.
        removed = recall.is_removed_dir
    except Exception:
        skip = {".git", ".gedaechtnis"} | set(config.non_memory_tops())
        # DELIBERATELY BROADER than `recall.is_removed_dir`, not a copy of it. This branch runs only
        # when `recall` will not import at all, and a second implementation of one rule is how two
        # authors reach opposite verdicts on the same path. A bare `startswith` over-excludes here
        # (a region named `Cleanup-of-Records` would go unseen) and that is the safe direction: the
        # cost is a boot file not noticed, against reporting one that the user's own cleanup retired.
        def removed(d):
            return d.startswith("Cleanup") or d.startswith("Synthesis")
    try:
        for dirpath, dirnames, filenames in os.walk(common.VAULT):
            dirnames[:] = [d for d in dirnames
                           if d not in skip and not d.startswith(".") and not removed(d)]
            if "Kernel.md" in filenames:
                has_kernel = True
                break
    except OSError:
        has_kernel = False
    return {"kernel": has_kernel,
            "reviewer": (plugin / "agents" / "memory-reviewer.md").is_file()}


def assemble_rules(text: str, conditions: dict[str, bool]) -> str:
    """The rules with each conditional span kept or dropped, and the seams tidied.

    An UNKNOWN condition name is KEPT, never dropped. A typo in a sentinel would otherwise delete
    a rule from every session silently, which is the one failure this mechanism must not be able
    to cause — and the direction of that default is the only thing protecting it.

    Spans do NOT nest, and a malformed one is survivable rather than fatal. `re.sub` makes one
    pass over the original string, so a span inside a kept span is never reprocessed and an
    unclosed span never matches at all; either way the raw HTML comment would ship into a model's
    context. Both are stripped afterwards, keeping the text. **A consequence worth knowing before
    editing the rules: the sentinel syntax cannot be shown literally in the rules prose**, because
    this strip would eat it. A test lints the shipped file for balance and nesting so a bad edit
    fails at build time rather than reaching a session."""
    def keep(m):
        return m.group(2) if conditions.get(m.group(1), True) else ""
    out = ONLY_IF_RE.sub(keep, text)
    out = LEFTOVER_SENTINEL_RE.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return "\n".join(line.rstrip() for line in out.splitlines()).strip()


def rules_block(source, cwd: str | None = None) -> list[str]:
    """The operating rules, at `startup` only and only in scope, or [] — see the module docstring.

    In scope = `common.in_scope(cwd)`: the vault, or a repo carrying an `.atlas-lane` marker. The
    marker is the person's own yes; outside it the rules would steer a session nobody asked this
    plugin to steer (PLUGDIR-1).

    A source Claude Code did not send (an absent key, a direct invocation) is treated as a
    startup: the failure that matters is a session running with no rules at all, and repeating
    them on a resume costs only tokens.

    The text is ASSEMBLED for the vault in front of it rather than shipped whole — see
    `rules_conditions` for what may vary and, more importantly, for what may not."""
    if (source or "startup") != "startup" or not config.flag("inject_rules", True):
        return []
    if not common.in_scope(cwd):
        return []
    rules = Path(__file__).resolve().parent.parent / "rules" / "operating-rules.md"
    try:
        text = rules.read_text(encoding="utf-8")
    except OSError:                                  # a trimmed install without the rules file
        return []
    return ["", "The operating rules for this vault (from the Gedächtnis plugin; they are how "
                "you write to it):", "", assemble_rules(text, rules_conditions())]


if __name__ == "__main__":
    guarded(main)
