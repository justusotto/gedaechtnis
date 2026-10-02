#!/usr/bin/env python3
"""sessions.py — launch a Claude session detached, see every session on one page, close the finished.

    python3 tools/sessions.py launch --name NAME --cwd DIR [--row Q-ID] [--worktree DIR]
                                     [--handoff GLOB] [--owner-go "HIS WORDS"] LAUNCHER.sh
    python3 tools/sessions.py ls
    python3 tools/sessions.py projects [--ids FILE | --ids-text 'cse_… cse_…']
    python3 tools/sessions.py close [--dry-run] [--when-needed | --all-finished]
    python3 tools/sessions.py close --replay DAYS [--out FILE.md]

THE LAUNCH RULE (TERMOVERLOAD-1). One session = one detached `screen` (or `tmux`) session, never a
Terminal window: a GUI that hangs at ~90 windows queues launches and replays them later as
duplicates. The launcher is a script whose last line is `exec claude …`; `launch` runs it as

    env -u <fleet.SCRUB_VARS> screen -dmS NAME zsh -c 'echo $$ > PIDFILE; exec zsh LAUNCHER'

so the pid written to PIDFILE IS the claude process (both `exec`s keep it) — the pid a later close
kills is the one captured here, never one found by searching the process table. Before launching,
under one lock, it REFUSES a second live session for the same row, worktree or name, and a launch
over `session_cap` WORKING claude sessions (idle ones do not count — SESSCLOSE-2), or over the
launching seat's `session_cap_per_seat`; each refusal prints its reason and exits non-zero.

THE MEMORY TEST (LAUNCHGATE-1) is the kernel's own pressure reading, never a share of swap: a launch
passes when `kern.memorystatus_vm_pressure_level` is 1 (normal) AND `kern.memorystatus_level` (the
percentage of memory free) is at least `session_launch_memory_floor`; warn (2) and critical (4)
refuse, with the numbers and what would make it pass. Swap in use is not a reason: macOS leaves
stale pages swapped after the pressure has ended. `--owner-go "<the owner's words>"` launches past
the memory test only — never past the duplicate check or a cap — and writes who, when, the words
and the readings to `owner-go.log` in the state directory.

`ls` is the read-only page: per session its state (PROMPT / WORKING / IDLE / BLIND), context,
handoff, parked messages, and the line to type to answer it — `screen -r NAME` (detach again with
Ctrl-a d). Nothing here types into a session. Beside the launch ledger it reads the CLI's own
registry (`~/.claude/sessions/*.json`) and the process table (SESSLSBLIND-1): a Remote Control host
is shown as HOST, a session it spawned as THREAD of that host, a daemon background job (`kind: bg`)
as FORK with the session it was copied from, and a session resumed by hand by its record. Every
such line leads with the SESSION ID — a host restarts an idle thread under a new name.

`projects` answers which live THREADs are real Project threads (PROJECTDESK-1): a thread whose
remote id (`bridgeSessionId` `session_01X…`, or `--session-id` / `--sdk-url` `cse_01X…` on its
command line — same suffix) is in the Project's thread list is PROJECT; one that started within
`fleet.HOST_OWN_SECONDS` of its host is the host's own session (HOST-OWN); anything else is
UNCONFIRMED. The thread list is what a Project session's `list_thread_sessions` returns, saved or
pasted as text; every `cse_…` in it counts. Read-only; the name is never used to decide.

`close` runs the close sweep once — over the launch ledger AND every other live interactive
session the CLI has a record of — and closes the finished ones while `session_close_apply` is on
(shipped on since HANDLINES-1). `--dry-run` prints exactly what would close and why, and for every
finished session it would NOT close, the safety fact that refuses it. A close needs every one of:
the ledger row this tool wrote (pid AND start time, still matching the live process) — or, for a
session started by hand (CLOSETUNE-2), its registry record `~/.claude/sessions/<pid>.json` naming
that pid with a `procStart` within 1 s of the live start, entrypoint `cli` (never Desktop), inside a
screen window, and never one the owner typed into after its first prompt — a claude
process whose parent chain reaches the launcher's screen/tmux session through shells only, not a
Remote Control host, not attended (`CLAUDE_CODE_SESSION_ATTENDED`), no name on
`session_close_never`. The close quits that screen/tmux session — no signal to a pid, never
`kill -9` — and is written to `close.log` with who closed it and the line that reopens it.

FINISHED is a fact about time (CLOSETUNE-1, `fleet.classify`): the last turn ended and is
`session_close_idle_minutes` old (or its handoff says done/landed/stopped with no turn since its
commit, or it reported to its seat 30 minutes ago); its last words are printed, never obeyed.
What cannot be told (git, its screen, an old background task) keeps it only up to 4 × that age.
`close --when-needed` is what the Remote Control hosts' 5-minute login item runs: while the machine
is over its memory line (swap in use ≥ `session_close_swap_used_mb` MB, or the memory-pressure level
< `session_close_memory_floor`) it closes the oldest finished sessions one by one, re-reading both
figures after each, and stops as soon as the machine is under; under the line it reads the two
figures and nothing else. `close --all-finished` closes every finished one, oldest first.

`close --replay DAYS` is the pilot (SESSCLOSE-2; the live rule since CLOSETUNE-2 of 2026-10-02): it
runs the close rule's transcript half over the last DAYS of real transcripts and prints, per
session, when the rule would first have closed it and what came AFTER that moment. A turn the
session then did on its own is a WRONG close; a message or a resume by someone else is a reopen.
It closes nothing; it reads transcripts, the launch ledger (for each session's seat) and, per
handoff a session wrote, one `git log`. It exits 1 when any row is wrong. It runs twice: the table
keeps the doubt about an old unreported task (as the live rule does when it cannot read the process
table); one sentence states the bound with that doubt dropped, which the exit code ignores.
"""
from __future__ import annotations
import argparse, os, re, shlex, subprocess, sys, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
import closerules                  # noqa: E402
import common                      # noqa: E402
import config                      # noqa: E402
import context_cap                 # noqa: E402
import fleet                       # noqa: E402
import limits                      # noqa: E402

EXEC_CLAUDE = re.compile(r"^\s*exec\s+(?:\S*/)?claude\b", re.M)
PID_WAIT_S = 10.0


def _launcher_problem(launcher: Path) -> str | None:
    try:
        text = launcher.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"cannot read the launcher: {e}"
    if not EXEC_CLAUDE.search(text):
        return ("the launcher has no `exec claude …` line — without `exec` the recorded pid is a "
                "shell, and closing it would leave claude running")
    return None


def cmd_launch(a) -> int:
    name = a.name
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", name):
        print(f"refused: {name!r} is not a usable session name (letters, digits, . _ -)")
        return 2
    launcher = Path(a.launcher).expanduser().resolve()
    prob = _launcher_problem(launcher)
    if prob:
        print(f"refused: {prob}")
        return 2
    mux = fleet.multiplexer()
    if mux is None:
        print("refused: neither screen nor tmux is available on this machine, so there is no way to "
              "run a session detached. Install one (macOS ships screen).")
        return 4
    config.state().mkdir(parents=True, exist_ok=True)
    with open(config.state() / "launches.lock", "w") as lk:
        common.lock_file(lk)
        procs_now = fleet.claude_processes()
        live_names = {p["name"] for p in (procs_now or []) if p.get("name")}
        dup = fleet.duplicate(fleet.ledger(), name, a.row, a.worktree, live_names)
        if dup:
            print(f"refused (duplicate): {dup}")
            return 3
        count = len(procs_now) if procs_now is not None else None
        busy = fleet.working_count(procs_now)
        seat = a.seat or fleet.launching_seat()
        over = fleet.over_cap(busy, fleet.cap()) or fleet.over_seat(seat, fleet.ledger(), fleet.seat_cap())
        readings = fleet.launch_readings()
        floor = fleet.launch_level_floor()
        swap_floor = fleet.launch_swap_free_floor_mb()
        mem, note = fleet.launch_memory(readings, floor, swap_floor)
        go = fleet.owner_go_words(a.owner_go)
        if a.owner_go is not None and not go:
            print("refused: --owner-go needs the owner's words, with at least one letter or digit; "
                  "an empty go is not a go.")
            return 2
        if over or (mem and not go):
            idle = [i["name"] for i in fleet.sweep(apply=False, rows=fleet.current_launches())
                    if not i["keep"]]
            print((f"refused (cap): {over}." if over else f"refused (memory): {mem}.")
                  + (f" Finished and closeable: {', '.join(idle)}." if idle else "")
                  + ("" if over else " " + fleet.launch_memory_help(readings, floor, swap_floor))
                  + (" --owner-go passes the memory test only, never a cap." if over and go else ""))
            return 3
        if mem:
            if not fleet.owner_go_log(name, go, mem, readings, fleet.closer()):
                print(f"refused (memory): {mem}. --owner-go was given, but owner-go.log could not "
                      f"be written, and a go that leaves no record does not apply.")
                return 3
            print(f"owner-go: launching past the memory test ({mem}). Logged to "
                  f"{config.state() / 'owner-go.log'} with the words given"
                  + (f" (the first {fleet.OWNER_GO_CHARS} characters)"
                     if len(go) > fleet.OWNER_GO_CHARS else "") + ".")
        elif note:
            print(f"note: {note}.")
        pidfile = config.state() / f"launch-{common.safe_sid(name)}.pid"
        try:
            pidfile.unlink()
        except OSError:
            pass
        inner = f"echo $$ > {shlex.quote(str(pidfile))}; exec /bin/zsh {shlex.quote(str(launcher))}"
        argv = (["screen", "-dmS", name, "/bin/zsh", "-c", inner] if mux == "screen"
                else ["tmux", "new-session", "-d", "-s", name, "/bin/zsh -c " + shlex.quote(inner)])
        p = subprocess.run(argv, cwd=a.cwd, env=fleet.clean_env(), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=15)
        if p.returncode != 0:
            print(f"failed: {' '.join(argv[:3])} exited {p.returncode}: {p.stderr.strip()}")
            return 1
        deadline = time.time() + PID_WAIT_S
        pid = None
        while time.time() < deadline:
            try:
                pid = int(pidfile.read_text().strip())
                break
            except (OSError, ValueError):
                time.sleep(0.2)
        row = {"name": name, "row": a.row, "worktree": a.worktree, "handoff": a.handoff,
               "cwd": os.path.realpath(a.cwd), "launcher": str(launcher), "pid": pid,
               "pid_start": fleet.pid_start(pid), "mux": mux, "mux_session": name,
               "launched_at": time.time(), "seat": seat}
        fleet.append_launch(row)
    print(f"launched {name} under {mux} (pid {pid if pid else 'NOT RECORDED — the pidfile never appeared'}).")
    print(f"  attach: {fleet.attach_line(mux, name)}")
    if count is not None:
        print(f"  claude processes before this launch: {count}, working: {busy}"
              + (f" (session_cap {fleet.cap()})" if fleet.cap() else " (no session_cap set)")
              + (f" · seat {seat}" if seat else ""))
    if fleet.launcher_lacks_inbound_accept(str(launcher)):
        print("  warning: the launcher does not pass --settings '{\"crossSessionInbound\":\"accept\"}';"
              " messages from other sessions may be held for an approval nobody can give.")
    return 0 if pid else 1


def _state_word(f: dict) -> str:
    screen = f.get("screen")
    if screen and fleet.PROMPT_TEXT.search(screen):
        return "PROMPT"
    if not f.get("scanned") and f.get("status") is None:
        return "BLIND"
    if (f.get("status") not in (None, "idle")) or (f.get("scanned") and f.get("end") is None):
        return "WORKING"
    return "IDLE"


def cmd_ls(_a) -> int:
    mux = fleet.multiplexer()
    rows = fleet.current_launches()
    procs_now = fleet.claude_processes() or []
    window = int(limits.get("resume_window", 420000) or 0)
    unreach = window - int(limits.get("resume_min_headroom", 70000) or 0) if window else None
    seen = set()
    sw, source = fleet.memory_share()
    print(f"{len(procs_now)} claude processes ({fleet.working_count(procs_now)} working) · "
          f"load {fleet.load_average()} · "
          f"{source} {'' if sw is None else f'{sw:.0%} used'} · "
          f"session_cap {fleet.cap() or 'unset'} · multiplexer {mux or 'none'}")
    view = fleet.live_view(fleet.registry_records(), fleet.process_table())
    kinds = [e["kind"] for e in view]
    print(f"registry and process table: {kinds.count('HOST')} host(s), {kinds.count('THREAD')} "
          f"thread(s), {kinds.count('SESSION')} other session(s), {kinds.count('FORK')} fork(s), "
          f"{kinds.count('BLIND')} blind")
    for r in rows:
        f = fleet.gather(r, mux, None)
        if not f["alive"]:
            continue
        seen.add(r.get("pid"))
        tp = fleet.transcript_for(r.get("pid"), r["name"], r.get("launched_at"))
        ctx = context_cap.current_context(tp) if tp else None
        bits = [r["name"], f"pid {r.get('pid')}", _state_word(f),
                f"context {ctx:,}" if ctx else "context unknown",
                "handoff yes" if f["handoff"] else "handoff no"]
        if ctx and unreach and ctx >= unreach:
            bits.append(f"UNREACHABLE once cold (context ≥ {unreach:,}; while warm it still "
                        f"receives messages below resume_window {window:,})")
        n = len(fleet.parked(r["name"]))
        if n:
            bits.append(f"{n} parked message(s)")
        print("- " + " · ".join(bits))
        print(f"    answer it: {fleet.attach_line(r.get('mux') or mux, r.get('mux_session') or r['name'])}")
    # SESSLSBLIND-1: the CLI's registry is the second source. Hosts, Project threads, forks and
    # sessions resumed by hand are seen there (or, for a host, in the process table) — keyed by
    # pid and session id, never by name.
    for e in view:
        if e["pid"] in seen:
            continue
        print(_view_line(e))
        if e["kind"] in ("SESSION", "BLIND", "HOST"):
            sty = fleet.screen_of(e["pid"])
            screen = fleet.read_screen("screen", sty) if sty and e["kind"] != "HOST" else None
            if screen and fleet.PROMPT_TEXT.search(screen):
                print("    PROMPT on its screen")
            print(f"    answer it: {fleet.attach_line('screen', sty)}" if sty else
                  "    not in a screen session — its Terminal window or app, if any, is the only way in")
    launched = {r["name"] for r in rows if r.get("pid") in seen}
    for name, n in fleet.all_parked().items():
        where = f"{n} message(s) in {fleet.parked_path(name)}"
        h = fleet.holders(view, name)
        if len(h) == 1:
            print(f"- parked for {name} (live, session {h[0].get('session_id') or 'unknown'}: "
                  f"deliver them): {where}")
        elif len(h) > 1:
            ids = ", ".join(e.get("session_id") or f"pid {e['pid']}" for e in h)
            print(f"- parked for {name} ({len(h)} live sessions carry this name: {ids}; deliver by "
                  f"session id, never by the name): {where}")
        elif name in launched or name in {common.safe_sid(x) for x in launched}:
            print(f"- parked for {name} (live, launched by this tool): {where}")
        else:
            print(f"- parked for {name} (no live session): {where}")
    return 0


_STATUS_WORD = {"busy": "WORKING", "idle": "IDLE"}


def _view_line(e: dict) -> str:
    """One line per live entry of `fleet.live_view`. The session id leads: a name is not stable."""
    k = e["kind"]
    if k == "HOST":
        return (f"- HOST {e.get('name') or '(unnamed)'} · pid {e['pid']} · "
                f"{e['threads']} thread session(s) running · a Remote Control host, not a session")
    if k == "BLIND":
        return (f"- {e['name']} · pid {e['pid']} · BLIND (no session record: no transcript, "
                f"not resumable) · (not in the launch ledger)")
    status = _STATUS_WORD.get(e.get("status"), e.get("status") or "status unknown")
    bits = [f"session {e.get('session_id') or 'unknown'}", f"pid {e['pid']}", status,
            f"name {e.get('name') or '(none)'}"]
    if k == "THREAD":
        head = f"- THREAD of host {e.get('host') or e['host_pid']}"
        if e.get("remote"):
            bits.append(f"remote {e['remote']}")
        bits.append("the name is not stable: address it by session id")
    elif k == "FORK":
        head = "- FORK"
        bits.append(f"copied from {e['source']}" if e.get("source")
                    else "copied from: not recorded")
    else:
        head = "-"
        bits.append("(not in the launch ledger)")
    return f"{head} " + " · ".join(bits)


def cmd_projects(a) -> int:
    text = a.ids_text or ""
    if a.ids:
        try:
            text += "\n" + Path(a.ids).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"cannot read {a.ids}: {exc}", file=sys.stderr)
            return 2
    ids = fleet.thread_ids(text)
    table = fleet.process_table()
    if table is None:
        print("process table unreadable: no thread can be tied to its host", file=sys.stderr)
        return 2
    rows = fleet.project_threads(fleet.live_view(fleet.registry_records(), table), table, ids)
    print(f"{len(rows)} live thread(s) under a Remote Control host · "
          f"{len(ids)} Project thread id(s) given")
    for e in rows:
        print(f"- {e['verdict']} session {e.get('session_id') or 'unknown'} · pid {e['pid']} · "
              f"host {e.get('host') or e.get('host_pid')} · remote {', '.join(e['ids']) or 'none'} · "
              f"name {e.get('name') or '(none)'}")
    if not ids:
        print("no Project thread list given: PROJECT cannot be told; pass --ids with the output of "
              "list_thread_sessions from a Project session")
    return 0


def cmd_replay(a) -> int:
    now = time.time()
    since = now - float(a.replay) * 86400
    idle = fleet.idle_minutes()
    files = closerules.transcripts(Path(os.environ.get("CLAUDE_PROJECTS_DIR")
                                        or Path.home() / ".claude" / "projects"), since)
    facts = replay_facts()
    rows = closerules.replay(files, now, since, idle, facts_fn=facts)
    bound = closerules.replay(files, now, since, idle, facts_fn=facts, nothing_runs=True)
    text = replay_table(rows, now, since, idle, len(files), closerules.reuse_gaps(files, since), bound)
    print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    return 1 if any(r["wrong"] for r in rows) else 0


def replay_facts():
    """What the replay needs beside a transcript: the seat the launch ledger recorded for that name,
    and the commit time of a done/landed/stopped handoff the session wrote (one git read per file)."""
    seats = {r.get("name"): r.get("seat") for r in fleet.ledger()}
    seen: dict[tuple, float | None] = {}

    def facts(sc: dict, end: dict):
        hs = tuple(fleet.own_handoffs(end))
        if hs and hs not in seen:
            seen[hs] = fleet.handoff_done(None, sc.get("cwd"), hs)
        return seen.get(hs), seats.get(sc.get("name"))
    return facts


def _bound_line(bound) -> str:
    """The second run, with every old-task doubt dropped: what the live process read has to cover."""
    closed = [r for r in bound if r["closed_at"]]
    wrong = [r["name"] for r in closed if r["wrong"]]
    return ("An old background task that never reported keeps a session here for 12 h, as it does "
            "live when the process table cannot be read. The live pass reads it: with the registry "
            "idle and no process of its own, such a task does not keep. Replayed as if NOTHING ran "
            f"in any of them, the rule closes {len(closed)} and {len(wrong)} of those did another "
            "turn on their own" + (f" ({', '.join(wrong)})" if wrong else "") + " — those are the "
            "sessions the live read of their processes must keep, and the dry run shows whether it does.")


def replay_table(rows, now, since, idle, nfiles, gaps=(), bound=None) -> str:
    closed = [r for r in rows if r["closed_at"]]
    wrong = [r for r in closed if r["wrong"]]
    reused = [r for r in closed if r.get("reused")]
    ft = closerules.fmt_time
    out = [f"# Close-rule replay — {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}", "",
           f"Window {ft(since)} → {ft(now)} · {nfiles} transcripts · {len(rows)} sessions (grouped by "
           f"name; a resume under the same name counts as the same session) · idle threshold "
           f"{idle:g} min.", "",
           f"**Would have closed: {len(closed)}. WRONG closes (the session did another turn on its "
           f"own after the close moment): {len(wrong)}. Messaged or resumed by someone after it "
           f"(reopened with the `claude -r` line the close log prints): {len(reused)}.**", "",
           "The rule replayed is the live one (`closerules.finished`): the last turn ended and is "
           f"{idle:g} min old, or its own handoff says done/landed/stopped and was committed in "
           "that turn, or it reported to its seat; either one and 30 min of quiet. Not replayed — "
           "live-only facts that can only KEEP a session, so leaving them out can only ADD "
           "closes: the screen read (a prompt, an attached screen), unsaved work, a live process "
           "of its own, the registry status, a terminal window, the never list. One thing is read "
           "as it is TODAY, not as it was: a handoff's State line and its last commit.", "",
           *([] if bound is None else [_bound_line(bound), ""]),
           "## Would have closed", "",
           "| session | closed at | then | its last words (tail) |", "|---|---|---|---|"]
    for r in sorted(closed, key=lambda r: r["closed_at"]):
        then = (f"**WRONG** — {r['after_kind']} at {ft(r['after'])}" if r["wrong"] else
                f"reopened — {r['after_kind']} at {ft(r['after'])}" if r.get("reused") else
                "nothing after")
        words = r["end_text"].replace("|", "/").replace("\n", " ")[-110:]
        out.append(f"| {r['name']} | {ft(r['closed_at'])} | {then} | {words} |")
    out += ["", f"## Finished turns that were used again later (the margin the {idle:g}-minute "
            "threshold must clear)", "",
            "Every turn end the transcript rules call finished (idle aside) after which the session "
            "was still messaged or resumed, gap ≥ 10 min, longest first.", "",
            "| session | ended | used again after | its last words (tail) |", "|---|---|---|---|"]
    for g in gaps:
        out.append(f"| {g['name']} | {ft(g['ended'])} | {g['gap_min']:.0f} min | "
                   f"{g['text'].replace('|', '/')} |")
    if not gaps:
        out.append("| — | | | |")
    out += ["", "## Kept (first reason at the last turn)", "", "| session | reason |", "|---|---|"]
    for r in rows:
        if not r["closed_at"]:
            out.append(f"| {r['name']} | {r['reason'].replace('|', '/')[:150]} |")
    return "\n".join(out)


def _print_items(items, idle) -> None:
    for i in items:
        extra = "".join(f"\n    note: {n}" for n in i.get("notes") or [])
        if i["closed"]:
            print(f"closed  {i['name']} (pid {i['pid']}) — reopen with: {i['resume']}{extra}")
        elif i["keep"]:
            print(f"keep  {i['name']}: " + "; ".join(i["keep"]))
        elif i.get("refused"):
            print(f"refuse  {i['name']} (pid {i['pid']}): finished, but " + "; ".join(i["refused"]))
        else:
            unc = f"   # {i['uncommitted']}" if i.get("uncommitted") else ""
            how = (i.get("admission") or fleet.REGISTRY_ADMITTED if i.get("outside") else
                   f"launched by this tool, same pid and start time, inside its own "
                   f"{i['attach'].split()[0]} session")
            print(f"would close  {i['name']} (pid {i['pid']}): finished; {how}, not "
                  f"attended, not on session_close_never — reopen with: {i['resume']}{unc}{extra}")


def cmd_close(a) -> int:
    if a.replay is not None:
        return cmd_replay(a)
    on = fleet.applies()
    apply = on and not a.dry_run
    by = f"sessions.py close by {fleet.closer()}"
    if a.when_needed or a.all_finished:
        res = fleet.close_when_needed(apply=apply, by=by, all_finished=a.all_finished)
        head = "all finished" if a.all_finished else "when needed"
        if res["stopped"] == "under the line":
            print(f"under the line ({fleet.figures(res['before'])}; line: swap < "
                  f"{res['target']:.0f} MB and memory level ≥ {res['floor']:g}): nothing to do.")
            return 0
        if not res["candidates"] and res["over"] is None and not a.all_finished:
            print(f"{head}: {res['stopped']}.")
            return 0
        print(f"{head}: before {fleet.figures(res['before'])}"
              + (f" — over the line: {', '.join(res['over'])}" if res["over"] else ""))
        if a.dry_run:
            print("dry run: nothing is closed.")
        elif not on:
            print("session_close_apply is off for this install: proposing only.")
        _print_items(res.get("items") or [], fleet.idle_minutes())
        if res["candidates"] and not res["closed"]:
            print("oldest first: " + ", ".join(i["name"] for i in res["candidates"]))
        print(f"{head}: closed {len(res['closed'])}"
              + (f" ({', '.join(i['name'] for i in res['closed'])})" if res["closed"] else "")
              + f"; after {fleet.figures(res['after'])}; {res['stopped']}.")
        return 0
    if a.dry_run:
        print("dry run: nothing is closed.")
    elif not on:
        print("session_close_apply is off for this install: proposing only.")
    items = fleet.sweep(apply=apply, by=by)
    _print_items(items, fleet.idle_minutes())
    fleet.record_proposals(items)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    la = sub.add_parser("launch")
    la.add_argument("--name", required=True)
    la.add_argument("--cwd", required=True)
    la.add_argument("--row")
    la.add_argument("--worktree")
    la.add_argument("--handoff", help="glob of the handoff file this session will write")
    la.add_argument("--seat", help="the orchestrator this session counts against "
                                   "(default: the launching session's name)")
    la.add_argument("--owner-go", metavar="WORDS",
                    help="the owner's own words giving the go: launch past the MEMORY test only "
                         "(never the duplicate check or a cap); logged to owner-go.log")
    la.add_argument("launcher")
    sub.add_parser("ls")
    pj = sub.add_parser("projects")
    pj.add_argument("--ids", help="a file holding the Project's thread list (any text; every "
                                  "cse_… in it counts)")
    pj.add_argument("--ids-text", help="the same, given inline")
    cl = sub.add_parser("close")
    cl.add_argument("--dry-run", action="store_true",
                    help="print what would close and why each, and close nothing")
    cl.add_argument("--apply", action="store_true",
                    help="(kept for old command lines) `close` applies whenever session_close_apply is on")
    cl.add_argument("--when-needed", action="store_true",
                    help="only while the machine is over its memory line (session_close_swap_used_mb, "
                         "session_close_memory_floor): close the oldest finished sessions until it is "
                         "under; under the line it reads two figures and does nothing else")
    cl.add_argument("--all-finished", action="store_true",
                    help="close every finished session this tool launched, oldest first, whatever "
                         "the memory line (the seat's manual sweep)")
    cl.add_argument("--replay", type=float, metavar="DAYS",
                    help="replay the close rules over the last DAYS of transcripts (the pilot)")
    cl.add_argument("--out", help="with --replay: also write the table to this file")
    a = ap.parse_args(argv)
    return {"launch": cmd_launch, "ls": cmd_ls, "projects": cmd_projects,
            "close": cmd_close}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
