#!/usr/bin/env python3
"""sessions.py — launch a Claude session detached, see every session on one page, close the finished.

    python3 tools/sessions.py launch --name NAME --cwd DIR [--row Q-ID] [--worktree DIR]
                                     [--handoff GLOB] LAUNCHER.sh
    python3 tools/sessions.py ls
    python3 tools/sessions.py projects [--ids FILE | --ids-text 'cse_… cse_…']
    python3 tools/sessions.py close [--apply]
    python3 tools/sessions.py close --replay DAYS [--out FILE.md]

THE LAUNCH RULE (TERMOVERLOAD-1). One session = one detached `screen` (or `tmux`) session, never a
Terminal window: a GUI that hangs at ~90 windows queues launches and replays them later as
duplicates. The launcher is a script whose last line is `exec claude …`; `launch` runs it as

    env -u <fleet.SCRUB_VARS> screen -dmS NAME zsh -c 'echo $$ > PIDFILE; exec zsh LAUNCHER'

so the pid written to PIDFILE IS the claude process (both `exec`s keep it) — the pid a later close
kills is the one captured here, never one found by searching the process table. Before launching,
under one lock, it REFUSES a second live session for the same row, worktree or name, and a launch
over `session_cap` WORKING claude sessions (idle ones do not count — SESSCLOSE-2), over the
launching seat's `session_cap_per_seat`, or over `session_swap_cap_share` of memory (swap, or memory
pressure when the machine has no swap); each refusal prints its reason and exits non-zero.

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
session the CLI has a record of. Without `--apply`, or while `session_close_apply` is off for this
install, it only proposes. The flag lives in the vault's config, not here: a command-line switch
cannot turn on what the owner has not. Each proposal prints the line that reopens the session.

`close --replay DAYS` is the pilot (SESSCLOSE-2): it runs the transcript rules over the last DAYS of
real transcripts and prints, per session, when the rules would first have closed it and whether it
was messaged, resumed or wrote anything AFTER that moment — each such row is a would-be WRONG
close. It reads transcripts only and exits 1 when any row is wrong.
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
        share, source = fleet.memory_share()
        over = (fleet.over_cap(busy, fleet.cap()) or fleet.over_seat(seat, fleet.ledger(), fleet.seat_cap())
                or fleet.over_swap(share, fleet.swap_cap(), source))
        if over:
            idle = [i["name"] for i in fleet.sweep(apply=False, rows=fleet.current_launches())
                    if not i["keep"]]
            print(f"refused (cap): {over}."
                  + (f" Finished and closeable: {', '.join(idle)}." if idle else ""))
            return 3
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
            bits.append(f"UNREACHABLE (context ≥ {unreach:,})")
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
    rows = closerules.replay(files, now, since, idle)
    text = replay_table(rows, now, since, idle, len(files), closerules.reuse_gaps(files, since))
    print(text)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    return 1 if any(r["wrong"] for r in rows) else 0


def replay_table(rows, now, since, idle, nfiles, gaps=()) -> str:
    closed = [r for r in rows if r["closed_at"]]
    wrong = [r for r in closed if r["wrong"]]
    ft = closerules.fmt_time
    out = [f"# Close-rule replay — {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}", "",
           f"Window {ft(since)} → {ft(now)} · {nfiles} transcripts · {len(rows)} sessions (grouped by "
           f"name; a resume under the same name counts as the same session) · idle threshold "
           f"{idle:g} min.", "",
           f"**Would have closed: {len(closed)}. Would-be WRONG closes (messaged, resumed or wrote "
           f"after the close moment): {len(wrong)}.**", "",
           "Not replayed — live-only facts that can only KEEP a session, so leaving them out can only "
           "ADD closes and the wrong count is an upper bound: the screen read (a prompt, an attached "
           "screen), unsaved work, the process being alive, the registry status, a terminal window, "
           "the exempt list. Also not replayed, in the other direction: a handoff file named by the "
           "launch ledger (a handoff the session wrote with its own Write/Edit IS replayed).", "",
           "## Would have closed", "",
           "| session | closed at | then | its last words (tail) |", "|---|---|---|---|"]
    for r in sorted(closed, key=lambda r: r["closed_at"]):
        then = f"**WRONG** — {r['after_kind']} at {ft(r['after'])}" if r["wrong"] else "nothing after"
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


def cmd_close(a) -> int:
    if a.replay is not None:
        return cmd_replay(a)
    on = fleet.applies()
    apply = bool(a.apply) and on
    if a.apply and not on:
        print("session_close_apply is off for this install: proposing only.")
    items = fleet.sweep(apply=apply)
    for i in items:
        if i["closed"]:
            print(f"closed  {i['name']} (pid {i['pid']})")
        elif not i["keep"]:
            print(f"would close  {i['name']} (pid {i['pid']})"
                  + (" [not launched by this tool]" if i.get("outside") else "")
                  + f" — reopen with: {i['resume']}")
        else:
            print(f"keep  {i['name']}: " + "; ".join(i["keep"]))
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
    la.add_argument("launcher")
    sub.add_parser("ls")
    pj = sub.add_parser("projects")
    pj.add_argument("--ids", help="a file holding the Project's thread list (any text; every "
                                  "cse_… in it counts)")
    pj.add_argument("--ids-text", help="the same, given inline")
    cl = sub.add_parser("close")
    cl.add_argument("--apply", action="store_true")
    cl.add_argument("--replay", type=float, metavar="DAYS",
                    help="replay the close rules over the last DAYS of transcripts (the pilot)")
    cl.add_argument("--out", help="with --replay: also write the table to this file")
    a = ap.parse_args(argv)
    return {"launch": cmd_launch, "ls": cmd_ls, "projects": cmd_projects,
            "close": cmd_close}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
