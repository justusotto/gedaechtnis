#!/usr/bin/env python3
"""fleet.py — the machine facts behind launching a session, seeing it, and closing it (TERMOVERLOAD-1).

THE SPECIMENS (2026-09-26). Terminal.app hung at ~90 windows and REPLAYED its queued launches
minutes later, igniting duplicate builders into the same worktrees; 52 finished sessions had to be
force-stopped by hand to bring the load down from ~90; a session stuck on a permission prompt could
be answered only in its own window. Design, options and the ratification:
`.orchestration/review/termoverload-2026-09-26/DESIGN.md`.

★ AND ONE FINDING THAT COMES FIRST. A `claude` started from inside another Claude session's shell
inherits `CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`, the PARENT's `CLAUDE_CODE_SESSION_ID` and
`CLAUDE_PID`. With `CLAUDE_CODE_CHILD_SESSION` set, an interactive session treats itself as nested
and turns session persistence OFF: no transcript, no `~/.claude/sessions/<pid>.json`. Every session
the seats launched with `screen -dmS` on 2026-09-26 was in that state — the context warn line, the
resume gate, the stall brief and `claude --resume` were all blind to it, and nothing said so.
`clean_env` is the remedy; `SCRUB_VARS` is the list, and a launch that skips it is what the Bash door
in `gate.py` names.

WHAT THIS MODULE DECIDES, and what it never does:
  * `duplicate()` — whether a launch would be a second live session for the same row, worktree or
    name. Liveness is the pid recorded AT LAUNCH plus that pid's start time, so a recycled pid cannot
    read as the old session. Never a process-table search by pattern.
  * `over_cap()` — whether the machine already runs `session_cap` claude processes.
  * `classify()` — whether a launched session is FINISHED: every rule in `CLOSE_RULES` must hold, and
    each one that does not is named. Pure: it reads a facts dict, so every rule has a test.
  * `sweep()` — gathers the facts and, only when `session_close_apply` is on, closes by quitting the
    screen/tmux session the launcher created, after re-reading every rule AND every safety fact
    (`close_refusals`) in the same pass. Never a signal to a pid, never `kill -9`. Off, it proposes.
It never types into a session. Reading a session's screen (`screen -X hardcopy`, `tmux
capture-pane`) is a read; answering it is the owner's hand (`screen -r <name>`).

No vault writes: everything here lives in the plugin's state directory.
"""
from __future__ import annotations
import fnmatch, json, os, re, shlex, shutil, subprocess, tempfile, time
from pathlib import Path

import closerules
import common
import config
import limits
import procs

# The variables a Claude session's shell exports that make a child `claude` believe it is nested
# (CLI 2.1.280: `vxe()` builds them; `DHe()` reads CHILD_SESSION). Scrubbed at every launch.
SCRUB_VARS = (
    "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_EFFORT", "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH", "AI_AGENT",
)

# The CLI's permission dialog, as it paints it. Matched against a READ of the screen.
PROMPT_TEXT = re.compile(r"Do you want to|Enter to confirm|❯\s*1\.\s*Yes|Esc to cancel", re.I)
# The fleet's wording for a turn that ended while WAITING (reviewer rev. 1): idle, and not done.
# One pattern, one place: `closerules` owns it (SESSCLOSE-2 widened it, and added ASK_TEXT).
WAIT_TEXT = closerules.WAIT_TEXT
PARKED_SCHEMA = "parked/1"
PARKED_TEXT_CHARS = 2000
INBOUND_ACCEPT = re.compile(r"crossSessionInbound[\"']?\s*:\s*[\"']accept")

CLOSE_WAIT_S = 10.0
GIT_TIMEOUT_S = 8          # a git that cannot answer in this long KEEPS the session (None, never [])
SWEEP_BUDGET_S = 35.0      # the Stop entry has 60 s; sessions past the budget wait for the next pass
SWEEP_CAP_S = 35.0         # ... counted from the pass's START there, as it always was: the Stop
#                            entry gets no later than before (CLOSEBUDGET-1 review)
STOP_CURSOR = "session-close.cursor"            # where the Stop pass resumes
NEEDED_CURSOR = "session-close-needed.cursor"   # ... and the login item's / hand-run pass: one each,
#                            or a pass that reaches the end resets the other's place
NEEDED_BUDGET_S = 240.0    # the login item's pass and the hand-run ones (no hook limit above them)
BUDGET_STRETCH = 3         # over the memory line a pass that has found nothing closable yet may run
#                            this many budgets before it gives up (CLOSEBUDGET-1)
SCAN_MAX_BYTES = 200 * 1024 * 1024   # a transcript past this is not read (KEPT): one file must not
                                     # spend the pass. Largest seen 2026-09-27: 57 MB, read in <1 s.

CLOSE_RULES = ("launched", "finished", "idle", "no-prompt", "no-unsaved-work", "transcript",
               "not-waiting", "background", "conversation", "attached", "window", "exempt")


# ------------------------------------------------------------------------------ environment ----

def clean_env(env: dict | None = None) -> dict:
    """A copy of `env` (default: this process's) without the nested-session markers."""
    src = dict(os.environ if env is None else env)
    for k in SCRUB_VARS:
        src.pop(k, None)
    return src


def multiplexer() -> str | None:
    """'screen' | 'tmux' | None. Windows has neither and gets None: `launch` refuses with a reason,
    which is no worse than today (there is no launcher there at all). `GEDAECHTNIS_MUX` overrides."""
    forced = (os.environ.get("GEDAECHTNIS_MUX") or "").strip().lower()
    if forced in ("screen", "tmux", "none"):
        return None if forced == "none" else forced
    if common.platform() == "windows":
        return None
    for m in ("screen", "tmux"):
        if common._have(m):
            return m
    return None


def mux_launch_argv(mux: str, name: str, launcher: str) -> list[str]:
    if mux == "screen":
        return ["screen", "-dmS", name, "/bin/zsh" if Path("/bin/zsh").exists() else "sh", launcher]
    return ["tmux", "new-session", "-d", "-s", name, launcher]


def mux_names(mux: str | None) -> set[str] | None:
    """Names of the multiplexer's live sessions, or None when they cannot be read."""
    if mux is None:
        return None
    argv = ["screen", "-ls"] if mux == "screen" else ["tmux", "list-sessions", "-F", "#S"]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = set()
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if mux == "screen":
            m = re.match(r"^\d+\.(\S+)\s", line + " ")
            if m and "(" in line:
                out.add(m.group(1))
        elif line:
            out.add(line)
    return out


SCREEN_READ_WAIT_S = 2.0   # how long a hardcopy may take to appear (screen writes it afterwards)
SCREEN_STABLE_S = 0.2      # the file must have stopped changing for this long: a hardcopy cut
#                            short would miss the prompt lines at its end


def read_screen(mux: str | None, name: str, wait_s: float = SCREEN_READ_WAIT_S) -> str | None:
    """The text on a session's screen, or None when it cannot be read. A READ: no keystroke.

    CLOSETUNE-2 (2026-10-02), two measured causes of "could not be read":
      - `screen -S <name>` matches by PREFIX, so `X-A` beside `X-A2` is "several suitable screens".
        The screen is named by `<pid>.<name>`; with none or several of exactly that name it is
        unreadable, never a prefix match that could be another session's screen.
      - `-X hardcopy` only ASKS the screen to write the file; a screen that was swapped out writes
        it after this function had read an empty file and removed it (99 late files were found in
        the temp folder). The file is read until it has text that stopped changing, up to `wait_s`,
        inside a folder of its own that is removed, so a later write lands nowhere."""
    if mux is None:
        return None
    try:
        if mux == "tmux":
            p = subprocess.run(["tmux", "capture-pane", "-p", "-t", name], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=5)
            return p.stdout if p.returncode == 0 else None
        pids = mux_session_pids("screen", name)
        if not pids or len(pids) != 1:
            return None           # none, several, or unreadable: never another session's screen
        target = f"{pids[0]}.{name}"
        box = tempfile.mkdtemp(prefix="gd-screen-")
        tmp = os.path.join(box, "screen.txt")
        try:
            p = subprocess.run(["screen", "-S", target, "-p", "0", "-X", "hardcopy", "-h", tmp],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5)
            if p.returncode != 0:
                return None
            deadline, seen, since = time.time() + wait_s, None, 0.0
            while True:
                try:
                    data = Path(tmp).read_bytes()
                except OSError:
                    data = b""
                t = time.time()
                if data != seen:
                    seen, since = data, t
                elif data.strip(b"\x00 \t\r\n") and t - since >= SCREEN_STABLE_S:
                    return data.decode("utf-8", "replace")
                if t >= deadline:
                    return None
                time.sleep(0.05)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            try:
                os.rmdir(box)
            except OSError:
                pass
    except (OSError, subprocess.TimeoutExpired):
        return None


def screen_of(pid) -> str | None:
    """The screen session a process runs in, from its own `STY` (`<pid>.<name>`), or None.

    Read from `ps eww`, which prints a process's environment for the user's own processes; used
    only to name the attach line for a session that was launched before this tool existed."""
    try:
        p = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(int(pid))], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return None
    m = re.search(r"(?:^|\s)STY=\d+\.(\S+)", p.stdout or "")
    return m.group(1) if m else None


def mux_quit(mux: str | None, name: str) -> None:
    if mux is None:
        return
    argv = (["screen", "-S", name, "-X", "quit"] if mux == "screen"
            else ["tmux", "kill-session", "-t", name])
    try:
        subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


# -------------------------------------------------------------------------------- processes ----

def pid_start(pid: int | None) -> str | None:
    """The process's start time as `ps` prints it — the half of identity a pid alone lacks."""
    if not pid:
        return None
    try:
        p = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    s = " ".join((p.stdout or "").split())
    return s or None


def same_process(pid, start) -> bool:
    """The recorded session is still the process with that pid: alive AND the same start time."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    return bool(start) and procs.pid_alive(pid) and _same_start(pid_start(pid), start)


def _same_start(a: str | None, b: str | None) -> bool:
    """Two start-time stamps name the same moment. `ps -o lstart` prints "Sun 27 Sep 15:18:13 2026"
    in one locale and the CLI's registry `procStart` "Sun Sep 27 11:58:44 2026": the same tokens
    in another order. Compared as token multisets — every token must match, only order may differ."""
    return bool(a) and bool(b) and sorted(str(a).split()) == sorted(str(b).split())


def claude_processes() -> list[dict] | None:
    """Every process whose IMAGE is claude: [{pid, name}], name None when unnamed. None if unread."""
    import stallbrief                                         # noqa: PLC0415 — one rule, one place
    try:
        p = subprocess.run(["ps", "-Ao", "pid=,command="], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    out = []
    for line in (p.stdout or "").splitlines():
        bits = line.split(None, 1)
        if len(bits) < 2:
            continue
        toks = bits[1].split()
        if not stallbrief._runs_claude(toks):
            continue
        try:
            out.append({"pid": int(bits[0]), "name": stallbrief._flag_value(toks, "--name")})
        except ValueError:
            continue
    return out


# ------------------------------------------------- the CLI's own registry: a second source ----
# SESSLSBLIND-1. The launch ledger knows only what `sessions.py launch` started, and the process
# table knows only a `--name` typed on the command line — so a session the owner resumed by hand
# (`claude --resume <id>`, its name only in the registry) read as "no live session", a Remote
# Control host read as a BLIND session, and a Project thread (image `claude.exe`, no `--name`)
# did not appear at all. `~/.claude/sessions/<pid>.json` is the CLI's own record of every live
# session; it is read here, and nothing else: never a transcript.
#
# A session is identified by its SESSION ID, never by its name: a host starts an idle thread
# again under a new derived name (`demo-repo-57` came back as `demo-repo-1d`, 2026-09-28).

def _image_is_claude(toks: list[str]) -> bool:
    """`claude`, `node … claude`, or the native `claude.exe` a Remote Control host spawns."""
    if not toks:
        return False
    first = os.path.basename(toks[0])
    if first in ("claude", "claude.exe"):
        return True
    return (first.startswith("node") and len(toks) > 1
            and os.path.basename(toks[1]) in ("claude", "claude.exe"))


def is_host(toks: list[str]) -> bool:
    """A Remote Control host: `claude remote-control …` — it serves threads, it is not one."""
    if not _image_is_claude(toks):
        return False
    rest = toks[2:] if os.path.basename(toks[0]).startswith("node") else toks[1:]
    return bool(rest) and rest[0] == "remote-control"


def host_name(toks: list[str]) -> str | None:
    """The host's `--name`, whole. `ps` loses the quoting, so `--name "demo-repo host"` comes
    back as two tokens; the name runs to the next `--flag`."""
    for i, tok in enumerate(toks):
        if tok.startswith("--name="):
            return tok[len("--name="):].strip("'\"") or None
        if tok == "--name":
            words = []
            for w in toks[i + 1:]:
                if w.startswith("--"):
                    break
                words.append(w)
            return " ".join(words).strip("'\"") or None
    return None


def process_table() -> dict[int, dict] | None:
    """Every claude process (sessions AND hosts): pid -> {ppid, start, toks}. None if unread."""
    try:
        p = subprocess.run(["ps", "-Ao", "pid=,ppid=,lstart=,command="], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    return parse_process_table(p.stdout or "")


def parse_process_table(text: str) -> dict[int, dict]:
    """`pid ppid <lstart: 5 tokens> command…` lines -> {pid: {ppid, start, toks}}, claude only."""
    out = {}
    for line in text.splitlines():
        bits = line.split()
        if len(bits) < 8:
            continue
        toks = bits[7:]
        if not _image_is_claude(toks):
            continue
        try:
            out[int(bits[0])] = {"ppid": int(bits[1]), "start": " ".join(bits[2:7]), "toks": toks}
        except ValueError:
            continue
    return out


def registry_records() -> list[dict]:
    """Every `<pid>.json` in the CLI's session registry, parsed; unreadable files are skipped."""
    import resume_gate                                        # noqa: PLC0415 — one reader, one place
    return resume_gate._registry()


FORK_SOURCE_KEYS = ("forkParentSessionId", "forkedFromSessionId", "forkSessionId")


def _fork_source(rec: dict, toks: list[str] | None) -> str | None:
    for k in FORK_SOURCE_KEYS:
        if rec.get(k):
            return str(rec[k])
    if toks and "--fork-session" in toks:
        for flag in ("--resume", "-r", "--session-id"):
            v = _flag(toks, flag)
            if v and not v.startswith("-"):
                return v
    return None


def _flag(toks: list[str], flag: str) -> str | None:
    import stallbrief                                         # noqa: PLC0415 — one rule, one place
    return stallbrief._flag_value(toks, flag)


def _str(v) -> str | None:
    """A registry field as a string, or None: the records are another program's files, so a list
    or a number where a name belongs must not crash the page (review of SESSLSBLIND-1)."""
    return v if isinstance(v, str) and v else None


def _pid_of(rec: dict) -> int | None:
    v = rec.get("pid")
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isascii() and v.isdigit():   # "²" isdigit, int() raises (CT2 fix review)
        return int(v)
    return None


def _start_epoch(lstart: str) -> float | None:
    """`ps -o lstart` (local time, either token order) as epoch seconds, or None."""
    toks = " ".join(str(lstart).split())
    for fmt in ("%a %d %b %H:%M:%S %Y", "%a %b %d %H:%M:%S %Y"):
        try:
            return time.mktime(time.strptime(toks, fmt))
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def _same_process(rec: dict, proc: dict) -> bool:
    """The record describes THIS process, not an earlier one whose pid was reused. `procStart`
    (UTC) must match the start `ps` prints; without it, `startedAt` (epoch ms, written when the
    session began) must not be older than the process. With neither, the record cannot be tied to
    the process and is not trusted."""
    if _str(rec.get("procStart")):
        return _same_start(registry_start_local(rec["procStart"]), proc["start"])
    began = rec.get("startedAt")
    t0 = _start_epoch(proc["start"])
    if isinstance(began, (int, float)) and not isinstance(began, bool) and t0 is not None:
        return began / 1000.0 >= t0 - 5
    return False


def live_view(records: list[dict], table: dict[int, dict] | None) -> list[dict]:
    """Classify every live claude session and host, from the registry and the process table.

    Returns one dict per entry, `kind` one of:
      HOST    a `claude remote-control` process (a host has no registry record of its own; if it
              ever has one, it is still shown once, as HOST); `threads` counts the live sessions
              it spawned.
      THREAD  a session whose parent process is a host: a Project / Remote Control thread.
      FORK    a background job of the CLI's daemon (`kind: bg`) — FORK even under a host — with the
              session it was copied from when the record or its command line names it (`source`).
      SESSION any other live session with a registry record.
      BLIND   a claude process with a `--name` and NO registry record (no transcript, not resumable).
    A record whose pid is dead, or that cannot be tied to the live process (`_same_process`: the
    pid was reused), is not live and is left out; one pid is listed once. With no process table
    (`ps` unreadable), liveness falls back to `procs.pid_alive` and no HOST or parent can be told.
    """
    out, recorded = [], set()
    hosts = {pid: p for pid, p in (table or {}).items() if is_host(p["toks"])}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        pid = _pid_of(rec)
        if pid is None or pid in recorded or pid in hosts:
            continue
        if table is not None:
            proc = table.get(pid)
            if proc is None or not _same_process(rec, proc):
                continue
        else:
            if not procs.pid_alive(pid):
                continue
            proc = None
        recorded.add(pid)
        e = {"pid": pid, "session_id": _str(rec.get("sessionId")), "name": _str(rec.get("name")),
             "status": _str(rec.get("status")), "cwd": _str(rec.get("cwd")),
             "remote": _str(rec.get("bridgeSessionId")), "rec_kind": _str(rec.get("kind"))}
        if rec.get("kind") == "bg":
            e["kind"] = "FORK"
            e["source"] = _fork_source(rec, proc["toks"] if proc else None)
        elif proc and proc["ppid"] in hosts:
            e["kind"] = "THREAD"
            e["host_pid"] = proc["ppid"]
            e["host"] = host_name(hosts[proc["ppid"]]["toks"])
        else:
            e["kind"] = "SESSION"
        out.append(e)
    for pid, p in sorted(hosts.items()):
        out.append({"kind": "HOST", "pid": pid, "name": host_name(p["toks"]),
                    "threads": sum(1 for e in out if e.get("host_pid") == pid)})
    for pid, p in sorted((table or {}).items()):
        if pid in recorded or pid in hosts:
            continue
        name = _flag(p["toks"], "--name")
        if name:
            out.append({"kind": "BLIND", "pid": pid, "name": name})
    return out


# A parked message can be delivered only to a session the resume gate can find by name: one with a
# registry record (not BLIND, not a HOST) and the ONLY live one carrying that name.
DELIVERABLE_KINDS = ("SESSION", "THREAD", "FORK")


def live_names(view: list[dict]) -> set[str]:
    """Every name at least one live session with a record carries (for the parked-message check).
    A host's name and a BLIND session's name are left out: neither can take a message."""
    return {e["name"] for e in view if e.get("name") and e["kind"] in DELIVERABLE_KINDS}


def holders(view: list[dict], parked_name: str) -> list[dict]:
    """The live sessions a parked file's name belongs to. The file name is `safe_sid(name)`, so
    the live names are compared in that form too."""
    return [e for e in view if e.get("name") and e["kind"] in DELIVERABLE_KINDS
            and (e["name"] == parked_name or common.safe_sid(e["name"]) == parked_name)]


# PROJECTDESK-1: which live THREADs are real Project threads. A host also spawns sessions for
# itself within seconds of its own start (measured 2026-09-28: the six language-deck hosts' children
# and the demo-repo host's, each within seconds of the host).
HOST_OWN_SECONDS = 30
_THREAD_ID = re.compile(r"\b(?:cse|session)_([0-9][A-Za-z0-9]+)")


def thread_ids(text: str) -> set[str]:
    """The id suffixes in any text: a Project's thread list, pasted or saved, carries `cse_01X…`;
    the registry's `bridgeSessionId` carries `session_01X…` with the SAME suffix."""
    return set(_THREAD_ID.findall(text or ""))


def _ids_of(e: dict, proc: dict | None) -> set[str]:
    """Both readings of a thread's remote id: its record's `bridgeSessionId` and its command
    line's `--session-id` / `--sdk-url`."""
    seen = thread_ids(e.get("remote") or "")
    if proc:
        for flag in ("--session-id", "--sdk-url"):
            seen |= thread_ids(_flag(proc["toks"], flag) or "")
    return seen


def project_threads(view: list[dict], table: dict[int, dict] | None,
                    project_ids: set[str] | None, window: float = HOST_OWN_SECONDS) -> list[dict]:
    """Each live THREAD with a `verdict`:
      PROJECT      its remote id is in the Project's thread list (`project_ids`, suffixes);
      HOST-OWN     not in the list, and it started within `window` seconds of its host;
      UNCONFIRMED  neither — or no list was given and it did not start with its host.
    The id decides, never the name (a thread's name changes when the host restarts it)."""
    out = []
    for e in view:
        if e["kind"] != "THREAD":
            continue
        proc = (table or {}).get(e["pid"])
        host = (table or {}).get(e.get("host_pid"))
        ids = _ids_of(e, proc)
        t0 = _start_epoch(proc["start"]) if proc else None
        h0 = _start_epoch(host["start"]) if host else None
        if project_ids and ids & project_ids:
            verdict = "PROJECT"
        elif t0 is not None and h0 is not None and 0 <= t0 - h0 <= window:
            verdict = "HOST-OWN"
        else:
            verdict = "UNCONFIRMED"
        out.append(dict(e, verdict=verdict, ids=sorted(ids)))
    return out


def load_average() -> str:
    try:
        return " ".join(f"{x:.1f}" for x in os.getloadavg())
    except (OSError, AttributeError):
        return "unknown"


# ----------------------------------------------------------------------------------- ledger ----

def ledger_path() -> Path:
    return config.state() / "launches.jsonl"


def ledger() -> list[dict]:
    """Launch rows, oldest first; the LAST row per name is that name's current launch."""
    try:
        text = ledger_path().read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("name"):
            rows.append(r)
    return rows


def current_launches(rows: list[dict] | None = None) -> list[dict]:
    latest: dict[str, dict] = {}
    for r in (ledger() if rows is None else rows):
        latest[r["name"]] = r
    return list(latest.values())


def append_launch(row: dict) -> None:
    config.state().mkdir(parents=True, exist_ok=True)
    with ledger_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _real(p: str | None) -> str | None:
    return os.path.realpath(os.path.expanduser(p)) if p else None


def duplicate(rows: list[dict], name: str, row: str | None, worktree: str | None,
              live_names: set[str], alive=same_process) -> str | None:
    """Why this launch would be a second live session, or None. `alive(pid, start)` is injectable."""
    if name in live_names:
        return f"a live claude process is already named {name!r}"
    wt = _real(worktree)
    for r in current_launches(rows):
        if not alive(r.get("pid"), r.get("pid_start")):
            continue
        if r["name"] == name:
            return f"{name!r} is already running (pid {r.get('pid')})"
        if row and r.get("row") == row:
            return f"row {row} already has a live session: {r['name']} (pid {r.get('pid')})"
        if wt and _real(r.get("worktree")) == wt:
            return f"worktree {worktree} already has a live session: {r['name']} (pid {r.get('pid')})"
    return None


def cap() -> int:
    try:
        v = int(limits.get("session_cap", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


def swap_share() -> float | None:
    """Swap in use as a share of swap total, or None when it cannot be read (or there is no swap).

    The seat's measurement 2026-09-26: 16 sessions, load 51, swap 2.4 of 4 GB — memory, not the
    process count, was the line the machine was on."""
    try:
        if common.platform() == "macos":
            out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True,
                                 stdin=subprocess.DEVNULL, timeout=5).stdout
            tot = re.search(r"total\s*=\s*([\d.]+)M", out)
            used = re.search(r"used\s*=\s*([\d.]+)M", out)
            if not (tot and used):
                return None
            t, u = float(tot.group(1)), float(used.group(1))
        else:
            info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines()
                        if ":" in l)
            t = float(info["SwapTotal"].split()[0])
            u = t - float(info["SwapFree"].split()[0])
    except (OSError, subprocess.TimeoutExpired, KeyError, ValueError):
        return None
    return (u / t) if t > 0 else None


def swap_cap() -> float:
    try:
        v = float(limits.get("session_swap_cap_share", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return v if 0 < v <= 1 else 0.0


def memory_share() -> tuple[float | None, str]:
    """(share of memory in use, what was read). Swap when the machine has swap; when swap total is 0
    (read 2026-09-27 after the restart: `vm.swapusage` total 0.00M, so the swap gate saw nothing),
    memory pressure instead — macOS `kern.memorystatus_level` is the percentage of memory free, the
    number `memory_pressure` prints; Linux `MemAvailable`. (None, "unknown") when neither reads."""
    sw = swap_share()
    if sw is not None:
        return sw, "swap"
    try:
        if common.platform() == "macos":
            out = subprocess.run(["sysctl", "-n", "kern.memorystatus_level"], capture_output=True,
                                 text=True, stdin=subprocess.DEVNULL, timeout=5).stdout.strip()
            level = float(out)
            if 0 <= level <= 100:
                return 1 - level / 100, "memory pressure"
        else:
            info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines()
                        if ":" in l)
            t = float(info["MemTotal"].split()[0])
            if t > 0:
                return 1 - float(info["MemAvailable"].split()[0]) / t, "memory pressure"
    except (OSError, subprocess.TimeoutExpired, KeyError, ValueError):
        pass
    return None, "unknown"


def over_swap(share: float | None, limit: float, source: str = "swap") -> str | None:
    """Why a launch is refused for memory, or None. An unreadable reading never refuses."""
    if not limit or share is None or share < limit:
        return None
    return f"{source} is {share:.0%} used and session_swap_cap_share is {limit:.0%}"


# ------------------------------------------------- the launch memory test (LAUNCHGATE-1) ----
# The launch used to be refused at a SHARE of swap (`session_swap_cap_share`). Measured 2026-10-01:
# three launches refused at swap 79-82% of total with `kern.memorystatus_vm_pressure_level` 1
# (normal), `kern.memorystatus_level` 39 and nothing closeable. macOS leaves stale pages in swap
# after the pressure has ended and resizes the total, so neither a share nor an absolute MB of swap
# in use says whether a new session fits. The kernel's own pressure level does.

PRESSURE_NORMAL = 1
PRESSURE_WORDS = {1: "normal", 2: "warn", 4: "critical"}


def launch_level_floor() -> float:
    """`session_launch_memory_floor`: the memory level (macOS `kern.memorystatus_level`, percentage
    of memory free) below which a launch is refused. Not a number in [0, 100] → the shipped 20."""
    try:
        raw = limits.get("session_launch_memory_floor", 20)
        if isinstance(raw, bool):
            return 20.0
        v = float(raw)
    except (TypeError, ValueError, KeyError):
        return 20.0
    return v if 0 <= v <= 100 else 20.0


def launch_swap_free_floor_mb() -> float:
    """`session_launch_swap_free_mb`: swap FREE, in MB, under which the swap backstop applies.
    0 = no backstop. Not a finite number ≥ 0 → the shipped 512."""
    try:
        raw = limits.get("session_launch_swap_free_mb", 512)
        if isinstance(raw, bool):
            return 512.0
        v = float(raw)
    except (TypeError, ValueError, KeyError):
        return 512.0
    return v if v >= 0 and v != float("inf") else 512.0          # NaN fails `v >= 0`


def _sysctl(name: str) -> str | None:
    try:
        p = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def launch_readings() -> dict:
    """{pressure: the kernel's memory-pressure level (1 normal, 2 warn, 4 critical) or None,
    level: percentage of memory free 0-100 or None, swap_free_mb, swap_total_mb: MB or None}.
    macOS: `kern.memorystatus_vm_pressure_level`, `kern.memorystatus_level`, `vm.swapusage`.
    Linux has no such pressure level: `pressure` stays None, the level is MemAvailable/MemTotal."""
    out = {"pressure": None, "level": None, "swap_free_mb": None, "swap_total_mb": None}
    if common.platform() == "macos":
        try:
            out["pressure"] = int(_sysctl("kern.memorystatus_vm_pressure_level") or "")
        except ValueError:
            pass
        try:
            level = float(_sysctl("kern.memorystatus_level") or "")
            if 0 <= level <= 100:
                out["level"] = level
        except ValueError:
            pass
        sw = _sysctl("vm.swapusage") or ""
        tot = re.search(r"total\s*=\s*([\d.]+)M", sw)
        free = re.search(r"free\s*=\s*([\d.]+)M", sw)
        try:
            if tot and free:
                out["swap_total_mb"], out["swap_free_mb"] = (float(tot.group(1)),
                                                             float(free.group(1)))
        except ValueError:
            out["swap_total_mb"] = out["swap_free_mb"] = None
        return out
    try:
        info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines()
                    if ":" in l)
    except OSError:
        return out
    try:
        mt = float(info["MemTotal"].split()[0])
        level = 100 * float(info["MemAvailable"].split()[0]) / mt if mt > 0 else float("nan")
        if level <= 100:                                      # NaN and MemAvailable > MemTotal fail
            out["level"] = max(0.0, level)                    # under 0 is 0: it refuses, never "unread"
    except (KeyError, ValueError, IndexError):
        pass
    try:
        t, f = float(info["SwapTotal"].split()[0]) / 1024, float(info["SwapFree"].split()[0]) / 1024
        if t >= 0 and f == f and t != float("inf"):           # NaN fails both
            out["swap_total_mb"], out["swap_free_mb"] = t, min(max(0.0, f), t)
    except (KeyError, ValueError, IndexError):
        pass
    return out


def _level(lv: float) -> str:
    """A level as printed: whole when it is whole, else one decimal ROUNDED DOWN — 19.96 under a
    floor of 20 prints 19.9, never 20."""
    return f"{lv:.0f}" if float(lv).is_integer() else f"{int(lv * 10) / 10:.1f}"


def launch_figures(m: dict) -> str:
    p, lv, free = m.get("pressure"), m.get("level"), m.get("swap_free_mb")
    return ("pressure level " + ("unreadable" if p is None
                                 else f"{p} ({PRESSURE_WORDS.get(p, 'not a known level')})")
            + ", memory level " + ("unreadable" if lv is None else _level(lv))
            + ", swap free " + ("unreadable" if free is None else f"{free:.0f} MB"))


def launch_memory(m: dict, level_floor: float, swap_free_floor: float) -> tuple[str | None, str]:
    """(why a launch is refused for memory, or None; a note to print when a reading was missing).

    The rule: a launch passes when the pressure level is normal (1) AND the memory level is at or
    above `level_floor`. Any other pressure level refuses, with the numbers. Swap is never a share
    and never a reason while the pressure level reads normal; it is a backstop — swap free under
    `swap_free_floor` is named beside a pressure that is not normal, and decides alone only when
    neither the pressure level nor the memory level could be read.

    A missing reading is never passed over silently: the note says which one was missing and what
    the launch was judged by instead."""
    p, lv = m.get("pressure"), m.get("level")
    free, total = m.get("swap_free_mb"), m.get("swap_total_mb")
    swap_low = bool(swap_free_floor > 0 and free is not None and total and free < swap_free_floor)
    figs = launch_figures(m)
    low = (f"memory level {_level(lv)} is under session_launch_memory_floor {level_floor:g}"
           if lv is not None and lv < level_floor else None)
    if p is not None and p != PRESSURE_NORMAL:
        why = f"memory pressure is {PRESSURE_WORDS.get(p, 'not normal')} ({figs})"
        if low:
            why += f"; {low}"
        if swap_low:
            why += f"; swap free is under session_launch_swap_free_mb {swap_free_floor:g} MB"
        return why, ""
    if p == PRESSURE_NORMAL:
        if low:
            return f"{low} ({figs})", ""
        if lv is None:
            return None, (f"the memory level could not be read ({figs}): judged by the pressure "
                          f"level alone")
        return None, ""
    if lv is not None:                                      # no pressure level on this machine
        note = f"the pressure level could not be read ({figs}): judged by the memory level alone"
        return (f"{low} ({figs})", "") if low else (None, note)
    if swap_low:
        return (f"neither the pressure level nor the memory level could be read, and swap free is "
                f"under session_launch_swap_free_mb {swap_free_floor:g} MB ({figs})"), ""
    return None, (f"neither the pressure level nor the memory level could be read ({figs}): "
                  f"launched without a memory test")


def launch_memory_help(m: dict, level_floor: float, swap_free_floor: float) -> str:
    """What would make this memory refusal pass, for the refusal message — worded by the readings
    the refusal was judged by (the same three branches as `launch_memory`)."""
    if m.get("pressure") is not None:
        cond = (f"the pressure level is 1 (normal) and the memory level is at least "
                f"{level_floor:g} (session_launch_memory_floor)")
    elif m.get("level") is not None:
        cond = f"the memory level is at least {level_floor:g} (session_launch_memory_floor)"
    else:
        cond = (f"swap free is at least {swap_free_floor:g} MB (session_launch_swap_free_mb); "
                f"once the pressure level or the memory level can be read again, those decide")
    return (f"It passes when {cond}; `sessions.py close --dry-run` shows what could be closed. "
            f"With the owner's go, repeat the launch with --owner-go \"<his words>\": it launches "
            f"past the memory test only, and is logged.")


OWNER_GO_CHARS = 500


def one_line(text) -> str:
    """Text for one log column: every character that is not printable (tab, newline, ESC, a
    zero-width space, a lone surrogate) becomes a space, and runs of spaces become one."""
    return " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())


def owner_go_words(text) -> str:
    """The owner's words as they will be logged, or "" when they say nothing: a go needs at least
    one letter or digit — spaces, control characters and invisible ones are not a go."""
    words = one_line(text or "")
    return words if any(c.isalnum() for c in words) else ""


def owner_go_log(name: str, words: str, why: str, m: dict, by: str) -> bool:
    """`owner-go.log`: when, name, by whom, the readings, the refusal it passed, the owner's words —
    one line per launch that went past the memory test. Words past `OWNER_GO_CHARS` are cut, with
    the number cut. False when the line could not be written: an override that leaves no record
    does not apply."""
    words = one_line(words)
    if len(words) > OWNER_GO_CHARS:
        words = f"{words[:OWNER_GO_CHARS].rstrip()} … [+{len(words) - OWNER_GO_CHARS} chars]"
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with (config.state() / "owner-go.log").open("a", encoding="utf-8") as fh:
            fh.write("\t".join([time.strftime("%Y-%m-%dT%H:%M:%S"), "launch", one_line(name),
                                f"by {one_line(by)}", launch_figures(m), f"past: {one_line(why)}",
                                f"words: {words}"]) + "\n")
    except (OSError, ValueError):                             # ValueError: an unencodable character
        return False
    return True


def working(pid, status_fn=None) -> bool:
    """Whether a live claude process counts against the cap (SESSCLOSE-2: the cap counts WORKING
    sessions, not every process). Idle = its registry record says `idle`; no record (a session
    launched blind, or not interactive) counts as working — an unknown is never free capacity."""
    st = (status_fn or registry_status)(pid)
    return st != "idle"


def working_count(procs_now: list[dict] | None, status_fn=None) -> int | None:
    if procs_now is None:
        return None
    return sum(1 for p in procs_now if working(p["pid"], status_fn))


def seat_cap() -> int:
    try:
        v = int(limits.get("session_cap_per_seat", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


def launching_seat(env: dict | None = None) -> str | None:
    """The orchestrator launching this session: the name in the registry record of the claude
    process whose shell runs `launch` (its `CLAUDE_PID`), or `GEDAECHTNIS_SEAT`. None = no seat."""
    env = os.environ if env is None else env
    if env.get("GEDAECHTNIS_SEAT"):
        return env["GEDAECHTNIS_SEAT"]
    import resume_gate                                        # noqa: PLC0415
    try:
        doc = json.loads((resume_gate.sessions_dir() / f"{int(env.get('CLAUDE_PID') or 0)}.json").read_text())
    except (OSError, ValueError, TypeError):
        return None
    n = doc.get("name") if isinstance(doc, dict) else None
    return n if isinstance(n, str) and n else None


def over_seat(seat: str | None, rows: list[dict], limit: int, alive=same_process,
              status_fn=None) -> str | None:
    """Why a launch is refused for its seat's share, or None: `limit` working sessions launched by
    the same seat are already live. A launch with no seat, or no limit, is never refused here."""
    if not seat or not limit:
        return None
    mine = [r for r in current_launches(rows) if r.get("seat") == seat
            and alive(r.get("pid"), r.get("pid_start")) and working(r.get("pid"), status_fn)]
    if len(mine) < limit:
        return None
    return (f"seat {seat} already has {len(mine)} working session(s) and session_cap_per_seat is "
            f"{limit} ({', '.join(r['name'] for r in mine[:6])})")


def over_cap(count: int | None, limit: int) -> str | None:
    """Why a launch is refused for load, or None. An unreadable count never refuses. `count` is the
    number of WORKING sessions (`working_count`) since SESSCLOSE-2 — idle ones do not use the CPU."""
    if not limit or count is None or count < limit:
        return None
    return (f"{count} claude sessions are working and session_cap is {limit} "
            f"(load average {load_average()})")


# ------------------------------------------------------------------------ transcript facts ----

def transcript_for(pid, name: str | None, launched_at: float | None) -> Path | None:
    """The session's transcript: via its registry record's session id, else by its launch name."""
    import context_cap, stallbrief, resume_gate               # noqa: PLC0415
    try:
        doc = json.loads((resume_gate.sessions_dir() / f"{int(pid)}.json").read_text())
        sid = doc.get("sessionId") if isinstance(doc, dict) else None
    except (OSError, ValueError, TypeError):
        sid = None
    if sid:
        t = context_cap.locate_transcript(sid, None)
        if t:
            return t
    if name:
        return stallbrief.find_transcript(name, (launched_at or 0) - 60)
    return None


def registry_status(pid) -> str | None:
    import resume_gate                                        # noqa: PLC0415
    try:
        doc = json.loads((resume_gate.sessions_dir() / f"{int(pid)}.json").read_text())
    except (OSError, ValueError, TypeError):
        return None
    s = doc.get("status") if isinstance(doc, dict) else None
    return s if isinstance(s, str) else None


def last_turn(transcript: Path | None) -> dict | None:
    """{state, text, ts} from the last main-chain conversation record, or None.

    state: 'ended' — the last record is an assistant turn with `stop_reason` `end_turn`;
           'pending' — anything else (a tool call without its result, a user message not yet
           answered, a streaming record) — the session is working or waiting on a prompt."""
    import context_cap, stallbrief                            # noqa: PLC0415
    if transcript is None:
        return None
    try:
        lines = context_cap._read_tail(transcript, stallbrief.TAIL_BYTES)
    except OSError:
        return None
    last, text = None, ""
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("isSidechain") or rec.get("isMeta"):
            continue
        if rec.get("type") not in ("assistant", "user"):
            continue
        last = rec
        if rec.get("type") == "assistant":
            content = (rec.get("message") or {}).get("content")
            t = content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in (content or []) if isinstance(b, dict)
                and b.get("type") == "text")
            if t.strip():
                text = " ".join(t.split())
    if last is None:
        return None
    msg = last.get("message") or {}
    ended = last.get("type") == "assistant" and msg.get("stop_reason") == "end_turn"
    return {"state": "ended" if ended else "pending", "text": text,
            "ts": stallbrief._ts(last.get("timestamp"))}


def unsaved_work(cwd: str | None, worktree: str | None, since: float | None) -> list[str] | None:
    """Uncommitted paths that may be this session's, [] when none, None when git cannot say.

    A declared worktree is the session's own: any dirt in it counts. Without one, the cwd's checkout
    may be shared, so only paths modified since the launch count — and each of those KEEPS."""
    where = worktree or cwd
    if not where:
        return None
    try:
        p = subprocess.run(["git", "--no-optional-locks", "-C", where, "status", "--porcelain", "-z", "-uall"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    try:
        top = subprocess.run(["git", "-C", where, "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, stdin=subprocess.DEVNULL, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    paths, fields = [], (p.stdout or "").split("\0")
    i = 0
    while i < len(fields):
        e = fields[i]
        if len(e) > 3:
            paths.append(e[3:])
            if e[0] in "RC":                                  # a rename/copy: next field is its OLD path
                i += 1
        i += 1
    if worktree:
        return paths
    out = []
    for rel in paths:
        try:
            if os.lstat(os.path.join(top, rel)).st_mtime >= (since or 0):
                out.append(rel)
        except OSError:
            out.append(rel)                                   # a deleted file is dirt too
    return out


def own_worktree(cwd: str | None, worktree: str | None) -> str | None:
    """The checkout that is the session's own: its declared worktree, or a cwd inside a
    `.claude/worktrees/<ROW>` folder (the house place for one build's worktree). None otherwise."""
    if worktree:
        return _in(cwd, worktree)
    m = re.match(r"^(.*/\.claude/worktrees/[^/]+)(?:/|$)", _real(cwd) or "")
    return m.group(1) if m else None


def _in(cwd: str | None, p: str) -> str:
    """`p` as the session sees it: a relative path is under the session's cwd, never under the cwd
    of the process running the sweep (CLOSETUNE-1: a ledger worktree `.claude/worktrees/X` was read
    against the sweep's own folder, and git's failure read as "unknown")."""
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) or not cwd else os.path.join(cwd, p)


def _toplevel(d: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def session_unsaved(cwd: str | None, worktree: str | None, writes,
                    ended: float | None = None) -> list[str] | None:
    """Uncommitted work that is THIS SESSION'S (SESSCLOSE-2), [] when none, None when git cannot say.

    The first sweep counted every dirty path of the checkout the session ran in; a shared checkout
    carries ~100 standing dirty paths that belong to nobody running (read 2026-09-27), so a finished
    builder there could never be closed. Now: (1) the session's own worktree — declared, or its cwd
    under `.claude/worktrees/` — counts whole, whatever the age of its dirt; (2) every file its
    transcript WROTE (Write/Edit/MultiEdit/NotebookEdit) that sits in a git repo and is still
    uncommitted there counts. A file outside any repo is saved on disk and does not count.
    Residual, stated: a file written only by a shell command (`>`, `sed -i`) is not in the transcript
    as a write; a builder's own worktree catches that case, a shared checkout does not.

    SESSCLOSE-3 — committed = saved. `writes` is `{path: when the session last wrote it}`. A TRACKED
    path that git lists as modified is saved when its last commit is at or after that write AND
    the file on disk changed after `ended`, the end of the session's last turn: a commit carried
    the path after the write, and the change git still lists was made when the session had
    stopped acting, so it is not this session's (a commit time alone does not show that: an
    index committed after a second edit, a shell edit after the commit, an amend of other paths
    all leave the session's own change dirty under a later commit). `ended` unknown = unsaved.
    (Measured 2026-10-04: a handoff committed at 23:13 and edited by its seat at
    00:37 kept its builder for 12.1 h). Still unsaved: an untracked path, one added and never
    committed, a deleted one, a write whose time is unknown, and any path git cannot date.
    A path under the machine's temp folder whose repository is also there is not work to save (a throwaway checkout in a
    scratchpad) — unless the session itself runs there."""
    out: list[str] = []
    own = own_worktree(cwd, worktree)
    times = writes if isinstance(writes, dict) else {}
    roots = temp_roots()
    in_temp = _under(_real(cwd) if cwd else None, roots)
    if own and not os.path.lexists(own):
        # A removed worktree holds no file: `git worktree remove` refuses a dirty tree. Its commits
        # are on its branch. A fact, not a failed read (CLOSETUNE-1).
        pass
    elif own:
        dirt = unsaved_work(None, own, None)
        if dirt is None:
            return None
        out.extend(dirt)
    by_repo: dict[str, list[str]] = {}
    wrote_at: dict[tuple[str, str], float | None] = {}
    for fp in sorted(writes or ()):
        full = _real(fp if os.path.isabs(os.path.expanduser(fp)) else os.path.join(cwd or "/", fp))
        if not full or (own and full.startswith(_real(own) + os.sep)):
            continue
        d = full if os.path.isdir(full) else os.path.dirname(full)
        while d and not os.path.isdir(d):
            d = os.path.dirname(d)
        top = _toplevel(d) if d else None
        # A throwaway checkout: the path AND the repository it belongs to are both under temp.
        # A worktree in temp of a repository that lives elsewhere is real work (its branch is).
        if top and not in_temp and _under(full, roots) and _under(_common_dir(top), roots):
            continue
        if top:
            rel = os.path.relpath(full, _real(top))
            by_repo.setdefault(top, []).append(rel)
            wrote_at[(top, rel)] = times.get(fp)
    for top, rels in by_repo.items():
        try:
            p = subprocess.run(["git", "--no-optional-locks", "-C", top, "status", "--porcelain", "-z", "-uall", "--", *rels],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               timeout=GIT_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if p.returncode != 0:
            return None
        fields, i = (p.stdout or "").split("\0"), 0
        while i < len(fields):
            e = fields[i]
            if len(e) > 3:
                if not _committed_since(top, e[3:], e[:2], wrote_at.get((top, e[3:])), ended):
                    out.append(os.path.join(top, e[3:]))
                if e[0] in "RC":
                    i += 1
            i += 1
    return out


def temp_roots() -> list[str]:
    """The machine's temp folders, by real path: `/tmp`, `/private/tmp`, `$TMPDIR`. Never
    `tempfile.gettempdir()`: with no writable temp folder it answers the CURRENT folder, which
    would make a real checkout "temp"."""
    out = []
    for d in ("/tmp", "/private/tmp", os.environ.get("TMPDIR")):
        r = _real(d) if d and os.path.isabs(d) else None
        if r and r != os.sep and r not in out:
            out.append(r)
    return out


def _common_dir(top: str) -> str | None:
    """The real path of the repository a checkout belongs to (`--git-common-dir`); None when
    git cannot say — which `_under` reads as not under temp, so the path is counted."""
    try:
        p = subprocess.run(["git", "--no-optional-locks", "-C", top, "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = p.stdout.strip() if p.returncode == 0 else ""
    return _real(out if os.path.isabs(out) else os.path.join(top, out)) if out else None


def _under(path: str | None, roots: list[str]) -> bool:
    return bool(path) and any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _committed_since(top: str, rel: str, xy: str, wrote: float | None,
                     ended: float | None = None) -> bool:
    """Whether a path git lists as dirty is nonetheless SAVED for the session that wrote it at
    `wrote`: it is tracked and only modified (never untracked, added, deleted, renamed or
    unmerged), its last commit on this checkout's HEAD is at or after that write, and the file
    on disk was last changed AFTER `ended` (the end of the session's last turn). Anything that
    cannot be read is False: the path counts as unsaved."""
    if not wrote or not ended or xy not in (" M", "M ", "MM"):
        return False
    try:
        if os.stat(os.path.join(top, rel)).st_mtime <= float(ended):
            return False
        p = subprocess.run(["git", "--no-optional-locks", "-C", top, "log", "-1", "--format=%ct",
                            "--", rel], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=GIT_TIMEOUT_S)
        return p.returncode == 0 and float(p.stdout.strip()) >= float(wrote)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return False


# ------------------------------------------------------------------------------ the decision ----

def idle_minutes() -> float:
    try:
        v = float(limits.get("session_close_idle_minutes", 180) or 180)
    except (TypeError, ValueError):
        v = 180.0
    return v if v > 0 else 180.0


def _minutes(key: str, default: float) -> float:
    """A limit that is a number ≥ 0 (0 = off where the key says so); anything else → `default`."""
    try:
        raw = limits.get(key, default)
        if isinstance(raw, bool):
            return float(default)
        v = float(raw)
    except (TypeError, ValueError, KeyError):
        return float(default)
    return v if v >= 0 and v != float("inf") else float(default)      # NaN fails `v >= 0`


def report_minutes() -> float:
    """`session_close_report_minutes`: the quiet a session needs after it reported to its seat or
    committed a done handoff (SESSCLOSE-3; before, the constant 30)."""
    return _minutes("session_close_report_minutes", closerules.REPORT_IDLE_MIN)


def stale_shell_minutes() -> float:
    """`session_close_stale_shell_minutes`: how old the last turn of a session that REPORTED must
    be before a background wait loop it left behind stops keeping it. 0 = such a loop keeps until
    `session_close_abandoned_minutes`."""
    return _minutes("session_close_stale_shell_minutes", 60)


def abandoned_minutes() -> float:
    """`session_close_abandoned_minutes`: how old the last turn must be before a session with
    nothing unsaved and nothing live but idle shells or shell + `sleep` wait loops stops being
    kept by them, reported or not. 0 = off."""
    return _minutes("session_close_abandoned_minutes", 1440)


def launch_wait_s() -> float:
    """`session_launch_wait_s`: how long a launch the memory test refused waits, after the closer
    ran, for the test to pass. 0 = the launch closes nothing and is refused at once."""
    return _minutes("session_launch_wait_s", 30)


def prompt_flag_minutes() -> float:
    """`session_prompt_flag_minutes`: a prompt on a session's screen for longer than this is
    FLAGGED in `sessions.py ls` and the health digest."""
    return _minutes("session_prompt_flag_minutes", 5)


def seat_addresses(seat: str | None, records: list[dict] | None = None) -> list[str]:
    """The socket addresses a session may have sent its report to instead of its seat's name:
    `uds:<messagingSocketPath>` of every registry record named like the seat (SESSCLOSE-3). The
    path carries the seat's pid, and a record is rewritten by whatever session next runs under
    that pid, so a record that still names the seat is the seat's address."""
    if not seat:
        return []
    out = []
    for rec in (registry_records() if records is None else records):
        if not isinstance(rec, dict):
            continue
        sock = rec.get("messagingSocketPath")
        if rec.get("name") == seat and isinstance(sock, str) and sock and f"uds:{sock}" not in out:
            out.append(f"uds:{sock}")
    return out


def exempt_patterns() -> list[str]:
    try:
        v = limits.get("session_close_exempt", [])
    except KeyError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def classify(f: dict, now: float, idle_minutes: float) -> list[str]:
    """The close rules that do NOT hold, as KEEP reasons. [] means closeable. Pure.

    CLOSETUNE-1: FINISHED is a fact about time (`closerules.finished`): the last turn ended, and it
    is `idle_minutes` old — or its handoff says done/landed/stopped with no turn since its commit,
    or it reported to its seat `session_close_report_minutes` ago. Last words that ask or wait no longer keep a session
    that is finished; they go to `notes(f, …)`. What cannot be told for certain — git could not
    answer, git lists uncommitted paths it wrote, its screen could not be read, an old background
    task never reported, the owner typed into it — keeps it only while its last turn is younger
    than `idle_minutes` × `closerules.UNKNOWN_FACTOR` (12 h at 3 h); past that it is closed, and its
    uncommitted paths go into the close log and the reopen line (`claude -r` brings it back whole).

    `f` keys: alive, is_self, handoff_done (commit time of a done/landed/stopped handoff, or None),
    seat, end (the transcript's final turn end from `closerules.scan`, None when mid-turn), scanned,
    status (registry status or None), screen (text or None), attached, unsaved (list or None),
    outside, mux (None = no screen/tmux), exempt, children (`own_processes`: the live processes it
    started for its work; None = the process table could not be read).

    CLOSETUNE-2 (2026-10-02): what "cannot be told" about an old background task is now READ. A live
    process of its own keeps the session, named. With the registry saying idle and no such process
    (`_nothing_runs`), an old task that never reported no longer keeps it, and an unreadable screen
    does not either when the transcript shows the last turn ended with no task open: a prompt
    needs a tool call or a task to ask for it.

    SESSCLOSE-3 (2026-10-06): `f` also carries seat_addrs (the seat's socket addresses),
    report_min, stale_shell_min and work (`own_work`: per live process of its own, whether it only
    WAITS). A session that reported and left only wait loops behind (`stale_loops`) is not kept
    by them, by the registry's `shell`, or by the old task that started them; each loop is a note."""
    keep = []
    stale = stale_loops(f, now)
    report_min = f.get("report_min", closerules.REPORT_IDLE_MIN)
    if f.get("is_self"):
        keep.append("launched: it is the session running this pass")
    if not f.get("alive"):
        keep.append("launched: its recorded pid is gone or now belongs to another process")
    if f.get("exempt"):
        keep.append("exempt: its name matches session_close_exempt")
    soft: list[str] = []
    age = None
    if not f.get("scanned"):
        keep.append("transcript: no transcript to read its last turn from")
    else:
        v = closerules.finished(f.get("end"), now, idle_minutes, f.get("handoff_done"), f.get("seat"),
                                nothing_runs=_nothing_runs(f) or bool(stale),
                                report_min=report_min, seat_addrs=f.get("seat_addrs") or ())
        keep.extend(v["hard"])
        soft.extend(v["soft"])
        age = v["age_s"]
        if f.get("outside"):
            # CLOSETUNE-2: a session NOT launched by this tool that the owner typed into is his,
            # however old — "never one you have typed into". A soft keep only for ledger rows.
            typed = [s for s in soft if s.startswith("conversation:")]
            soft = [s for s in soft if not s.startswith("conversation:")]
            keep.extend(s + " — a hand-started session he typed into is his to close" for s in typed)
    status = f.get("status")
    if status is not None and status != "idle" and not (stale and status == "shell"):
        keep.append(f"idle: the session registry says {status!r}")
    if f.get("outside") and not f.get("mux"):
        keep.append("window: it runs in a terminal window, not a screen — closing it is the owner's")
    kids = f.get("children")
    if kids and not stale:
        keep.append(f"background: {len(kids)} process(es) it started are still running "
                    f"({', '.join(kids[:3])}{', …' if len(kids) > 3 else ''})")
    screen = f.get("screen")
    if screen is None:
        if not _no_prompt_possible(f):
            soft.append("no-prompt: its screen could not be read")
    elif PROMPT_TEXT.search(screen):
        keep.append("no-prompt: a permission prompt is on its screen")
    if f.get("attached"):
        keep.append("attached: someone has its screen open")
    unsaved = f.get("unsaved")
    if unsaved is None:
        soft.append("no-unsaved-work: git could not say whether its work is committed (unknown)")
    elif unsaved:
        soft.append(f"no-unsaved-work: {len(unsaved)} uncommitted path(s) it wrote, e.g. {unsaved[0]}")
    line = unknown_line_s(idle_minutes)
    if soft and (age is None or age < line):
        keep.extend(f"{k} — keeps until its last turn is {line / 3600:g} h old" for k in soft)
    return keep


def stale_loops(f: dict, now: float) -> list[str]:
    """SESSCLOSE-3: the wait loops a FINISHED session left behind, as labels; [] = none, or not
    provably so. Pure. All of these must hold:
      - `stale_shell_min` > 0 and the last turn ended at least that long ago;
      - the session SAID it is finished in that turn (`closerules.said`: a report to its seat, or
        a done handoff committed) — a session that only went quiet is waiting for its loop;
      - the registry was read and says `idle` or `shell`, never `busy`;
      - EVERY live process of its own only waits (`own_work`: a shell whose command is a loop
        with `sleep N` as its whole body, and nothing but its own forks and `sleep` under it).
        One process that is anything else — a suite, a Python job, a browser, a script between
        two of its commands — and every child keeps as before;
      - every one of them, and every background task still open, was started BEFORE that
        report: a loop started after it is what the session said it waits for.
    THE DAY RULE — or, whatever it said: its last turn ended `abandoned_min`
    (`session_close_abandoned_minutes`, a day) ago, git READ its work and nothing it wrote is
    unsaved, the registry says `idle` or `shell`, and every live process of its own is `idle`
    (`own_work`: a bare shell with nothing under it, or a shell whose command has a loop with
    `sleep N` as its whole body and only its own forks and `sleep` under it) or has stood still
    for `child_still_min` (`still_since`, MACFIX-1B: a hung command, a browser nobody drives).
    A shell running anything else keeps. What such a loop would have done after its wait is given up: after a day
    nobody is there to read it, and the close log carries the line that reopens the session.
    Measured 2026-10-06: six sessions were kept 61–111 h by one `until …; do sleep N; done` each;
    the four that had reported are these, the two that had not are real waiters."""
    mins = f.get("stale_shell_min") or 0
    end, work, kids = f.get("end"), f.get("work"), f.get("children")
    if not f.get("scanned") or end is None or not work or kids is None or len(kids) != len(work):
        return []
    if f.get("status") not in ("idle", "shell"):
        return []
    ts = end.get("ts")
    if ts is None:
        return []
    # The day rule (owner, 2026-10-06: "wont do this from hand anymore"): reported or not.
    day = f.get("abandoned_min") or 0
    still, need = f.get("still") or {}, (f.get("child_still_min") or 0) * 60

    def quiet(w: dict) -> bool:
        # MACFIX-1B: or it has stood still (`still_since`) for `child_still_min`: the same
        # processes, no processor time. One that computed in that window keeps the session.
        since = still.get(str(w.get("label")))
        return bool(w.get("idle")) or (need > 0 and isinstance(since, (int, float))
                                       and 0 <= since <= now - need)
    if (day > 0 and now - ts >= day * 60 and f.get("unsaved") == []
            and all(quiet(w) for w in work)):
        return [str(w.get("label")) for w in work]
    if mins <= 0 or now - ts < mins * 60 or not all(w.get("waits") for w in work):
        return []
    said_at = closerules.said_at(end, f.get("handoff_done"), f.get("seat"), f.get("seat_addrs") or ())
    if not said_at:
        return []
    # What it started AFTER it reported is what it waits on ("I report when they finish").
    started = [w.get("started") for w in work] + list((end.get("open_bg_ts") or {}).values())
    # a process start is whole seconds: one in the report's own second counts as after it
    if any(not isinstance(s, (int, float)) or not s or s + 1 > said_at for s in started):
        return []
    return [str(w.get("label")) for w in work]


def _nothing_runs(f: dict) -> bool:
    """Both were READ and both say nothing of this session's is running: the CLI's registry says
    idle (it says `busy` while an agent of its own works and `shell` while a background shell
    runs), and it has no live process of its own. Either one unread = False."""
    return f.get("status") == "idle" and f.get("children") == []


def _no_prompt_possible(f: dict) -> bool:
    """With the screen unreadable, the transcript and the process table answer instead: the last
    turn ended (no tool call without its result), no task it started is still open, and nothing of
    its own runs. A permission prompt is asked by a tool call or by a task; neither exists."""
    end = f.get("end")
    return bool(f.get("scanned")) and end is not None and not end.get("open_bg") and _nothing_runs(f)


HELPER_BOOT_S = 120.0      # a non-shell child started this soon after the session is a helper
HELPER_NAMES = ("caffeinate",)   # the CLI's own keep-awake (`caffeinate -i -t 300`), never work
MORE_SHELLS = ("dash", "ksh", "fish", "tcsh", "csh", "-dash", "-ksh", "-fish", "-tcsh", "-csh")


def own_processes(pid, table: dict[int, dict] | None) -> list[str] | None:
    """The live processes a session started for its WORK, as `pid N (name)`: its direct children,
    minus its boot-time helpers. None when the table or the session's own row could not be read.
    Pure: `table` is `full_process_table()`.

    A helper is a child that is NOT a shell and started within HELPER_BOOT_S of the session itself
    (an MCP server; measured 2026-10-02: every one of 31 finished sessions had exactly one, started
    1 s after it). The CLI runs every command, background task and Monitor through a shell, so a
    shell child is always work, whenever it started; so is anything that started later, except
    the keep-awake the CLI starts itself while it works (HELPER_NAMES)."""
    work = own_work(pid, table)
    return None if work is None else [w["label"] for w in work]


WAIT_ONLY = ("sleep",)     # beside a shell, the one program that only waits
# The whole loop body is one `sleep N`: `until <check>; do sleep 10; done`.
WAIT_LOOP = re.compile(r";\s*do\s+sleep\s+[0-9.]+[smhd]?\s*;\s*done\b")
# What may follow `done`: commands that only print (the usual form is "wait, then show the result").
AFTER_LOOP = ("echo", "date", "true", ":", "printf", "cat", "tail", "head", "grep", "ls", "wc")
# The CLI runs a command as `<shell> -c "<its own head> && eval '<the command>' < /dev/null && pwd -P >| <file>"`.
CLI_FORM = re.compile(r"&& eval '(.*)' \\?< /dev/null && pwd -P >\| \S+\s*$", re.S)
LOOP_HEAD = re.compile(r"^\s*(?:[A-Za-z_]\w*=[^;&|]*;\s*)*(?:until|while)\s")


def wait_loop_text(text: str) -> bool:
    """Whether a shell's command line is ONLY a wait loop. All of: the command (the CLI's own
    wrapper taken off, else what follows `-c`) BEGINS with the loop — plain assignments, then
    `until` / `while`; it has exactly ONE loop, whose whole body is `sleep N`; nothing runs in the
    background (`&`); and after `done` there is nothing but commands that print (`AFTER_LOOP`:
    echo, date, cat, grep, tail, …; pipes only between them; no redirect, no substitution,
    nothing remote). So `while :; do rsync …; sleep 600; done`, `sleep 3600 && ./deploy.sh`,
    `./a.sh; until …`, `( … ) & until …`, `trap … EXIT; until …` are all jobs; a shell with no
    loop cannot be told. Work in the loop's CONDITION is accepted: it is a check. Pure."""
    text = (text or "").replace("\\012", "\n")
    m = CLI_FORM.search(text)
    if m:
        cmd = m.group(1).replace("'\"'\"'", "'")
    elif " -c " in text:
        cmd = text.split(" -c ", 1)[1]
    else:
        return False
    found = list(WAIT_LOOP.finditer(cmd))
    if len(found) != 1 or not LOOP_HEAD.match(cmd):
        return False
    if len(re.findall(r"\bdo\b", cmd)) != 1 or len(re.findall(r"\bdone\b", cmd)) != 1:
        return False                                   # a second loop, or loop text inside a string
    if re.search(r"(?<![&>])&(?!&)", cmd):
        return False                                   # something was sent to the background
    for part in re.split(r";|&&|\n", cmd[found[0].end():]):
        part = part.strip()
        if not part:
            continue
        if re.search(r"[`<>&]|\$\(|\|\|", part):
            return False
        for stage in part.split("|"):                 # a pipe between two printers still prints
            if not stage.split() or stage.split()[0] not in AFTER_LOOP:
                return False
    return True


def own_work(pid, table: dict[int, dict] | None) -> list[dict] | None:
    """`own_processes` with three more facts per process: [{label, waits, started, idle}]
    (`idle`: see the inner function; the day rule of `stale_loops` reads it). `waits`
    (SESSCLOSE-3): the process is a shell whose command is only a wait loop (`wait_loop_text`:
    `until …; do sleep 10; done`), and everything under it is `sleep` or a fork of that same
    shell (same command). Decided by the command, not by the instant: a job sampled between two
    of its commands, a shell running a script, a bare shell blocked in `read` or `wait` are all
    work. Anything else anywhere under it (a Python job, a suite, `ssh`, a browser) is work, and
    so is a process whose command cannot be read. `started`: its start, epoch seconds, or None.
    None when the table or the session's own row could not be read. Pure."""
    try:
        me = table.get(int(pid)) if table is not None else None
    except (TypeError, ValueError):
        me = None
    if me is None:
        return None
    born = _start_epoch(me.get("start") or "")
    under: dict[int, list[int]] = {}
    for c, proc in table.items():
        under.setdefault(proc.get("ppid"), []).append(c)

    def base_of(proc: dict) -> str:
        return os.path.basename(proc["toks"][0]) if proc.get("toks") else "?"

    def waits(c: int) -> bool:
        mine = table[c].get("toks") or []
        if base_of(table[c]) not in SHELLS + MORE_SHELLS or not wait_loop_text(" ".join(mine)):
            return False
        seen, todo = set(), [c]
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            b = base_of(table[x])
            if b not in SHELLS + MORE_SHELLS + WAIT_ONLY:
                return False
            if b not in WAIT_ONLY and (table[x].get("toks") or [])[1:] != mine[1:]:
                return False                      # another shell command: a script, not a fork
            todo.extend(under.get(x, []))
        return True

    def idle(c: int) -> bool:
        """The day rule's reading: a bare shell (no command) with nothing under it, or a shell
        whose command has a loop with `sleep N` as its whole body and nothing under it but its
        own forks and `sleep`. Unlike `waits`: what stands before or after the loop is not read."""
        if base_of(table[c]) not in SHELLS + MORE_SHELLS:
            return False
        mine = table[c].get("toks") or []
        seen, todo = set(), list(under.get(c, []))
        if not todo and all(a.startswith("-") for a in mine[1:]):
            return True                           # a bare shell: no command, nothing under it
        text = " ".join(mine)
        if not WAIT_LOOP.search(text):
            return False
        # loop keywords stand after a `;` — the word "done" inside a quoted pattern is not one
        if (len(re.findall(r";\s*do\b", text)) != 1 or len(re.findall(r";\s*done\b", text)) != 1
                or re.search(r"(?<![&>])&(?!&)", text)):
            return False                          # a second loop, or a job in the background
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            b = base_of(table[x])
            if b not in SHELLS + MORE_SHELLS + WAIT_ONLY:
                return False
            if b not in WAIT_ONLY and (table[x].get("toks") or [])[1:] != mine[1:]:
                return False                      # another shell command: a script, not a fork
            todo.extend(under.get(x, []))
        return True

    out = []
    for c in sorted(table):
        proc = table[c]
        if proc.get("ppid") != int(pid):
            continue
        base = base_of(proc)
        st = _start_epoch(proc.get("start") or "")
        # `caffeinate` with only switches and numbers is the keep-awake; `caffeinate <job>` is work.
        awake = base in HELPER_NAMES and all(
            a.startswith("-") or a.isdigit() for a in proc["toks"][1:])
        # A start time that cannot be read is never "at boot": the child counts as work.
        helper = awake or (base not in SHELLS + MORE_SHELLS + HELPER_NAMES and born is not None
                           and st is not None and 0 <= st - born <= HELPER_BOOT_S)
        if not helper:
            out.append({"label": f"pid {c} ({base})", "waits": waits(c), "started": st,
                        "idle": idle(c)})
    return out


CHILD_STILL = "child-still.json"
# The executable's own name (`ps -o comm=`), never a word on a command line.
BROWSER_COMM = re.compile(r"^(Google Chrome( for Testing)?( Helper.*)?|Chromium( Helper.*)?|chrome|"
                          r"chrome-headless-shell|headless_shell|chrome_crashpad_handler)$")
# A shell whose command can wait for something outside it with no process to show for it.
# (as a COMMAND word: at the start, or after ; & | ( { or do/then/else/while/until/if/eval — the
# word "read" inside a path or an argument is not one).
# Words that may stand before it are skipped: `IFS= read`, `! read`, `command read`, `time read`.
SHELL_WAITS = re.compile(r"/dev/(tcp|udp)/|(?:^|[;&|({\n]|\b(?:do|then|else|while|until|if|eval)\s)\s*['\"]?"
                         r"(?:(?:[A-Za-z_]\w*=\S*|!|command|builtin|time|exec|noglob)\s+)*"
                         r"(?:read|wait|select|zselect|mkfifo|nc|coproc|vared|ztcp|zpty|sysread|suspend)\b")
STILL_READINGS = 3         # a baseline counts after this many readings: one clock step is not a day


def child_still_minutes() -> float:
    """`session_close_child_still_minutes`: how long a process a session started must have stood
    still (`still_since`) before it stops keeping a session whose last turn is a day old. 0 = off."""
    return _minutes("session_close_child_still_minutes", 1440)


def browser_idle_cpu_s() -> float:
    """`session_close_browser_idle_cpu_seconds`: the processor seconds per day a browser may use
    and still count as standing still (an idle headless Chrome ticks; measured 2026-10-06: about
    44 s a day for a browser and its helpers three days after its session's last turn)."""
    return _minutes("session_close_browser_idle_cpu_seconds", 120)


def parse_cpu(s: str) -> float | None:
    """`ps -o time=` ([[D-]H:]M:S[.cc]) as seconds; None when it is not that."""
    try:
        days, _, rest = (s or "").strip().rpartition("-")
        v = 0.0
        for x in rest.split(":"):
            v = v * 60 + float(x)
        return v + (int(days) * 86400 if days else 0) if v >= 0 else None
    except ValueError:
        return None


def cpu_table() -> tuple[dict[int, float], dict[int, str]] | None:
    """(pid -> processor seconds used since it started, pid -> its executable). None if unread."""
    try:
        p = subprocess.run(["ps", "-Awwo", "pid=,time=,comm="], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=5, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    cpu, comm = {}, {}
    for line in (p.stdout or "").splitlines():
        bits = line.strip().split(None, 2)
        v = parse_cpu(bits[1]) if len(bits) == 3 else None
        if v is not None and bits[0].isdigit():
            cpu[int(bits[0])], comm[int(bits[0])] = v, bits[2]
    return (cpu, comm) if cpu else None


def is_browser(comm: str | None) -> bool:
    """A browser or one of its helpers, by its executable alone: `ssh host chrome`, a Python
    driver or `tail -f Chromium.log` name one and are not one."""
    return bool(comm) and comm.startswith("/") and bool(BROWSER_COMM.match(os.path.basename(comm)))


# Beside a shell and a browser, the programs that have nothing outside the machine to wait for:
# one of these with no processor time for a day is hung (reading a terminal nobody types into).
# NOT here, on purpose: `sleep` (a timer: the wait-loop rules judge it), `ssh`, `curl`, `tail`
# (it follows a log), Python, anything else — a remote job or a long timer also uses no
# processor time, so a tree holding one of those is never "standing still" and keeps as before.
# Also not here: `cat`, `head`, `tee` (the usual readers of a named pipe: `cat gate && ./deploy.sh`).
# A shell counts only when its command has no word that waits without a process (SHELL_WAITS).
# What is left and accepted, as under the day rule's sleep loops: a command that would have gone
# on after a filter that hung for a day is given up; the close log carries the line that reopens.
LOCAL_FILTERS = ("grep", "egrep", "fgrep", "ugrep", "rg", "tr", "sed", "awk", "wc", "sort", "uniq", "cut", "jq")


def tree_reading(root: int, table: dict[int, dict] | None, cpu: dict[int, float] | None,
                 comm: dict[int, str] | None = None) -> dict | None:
    """{`pid|start`: [processor seconds, is a browser]} for `root` and everything under it.
    None when the table, a start time or one process's seconds could not be read, or when one
    process is something that may be waiting for the outside (not a shell, a browser or one of
    LOCAL_FILTERS). Pure."""
    if table is None or cpu is None or comm is None or root not in table:
        return None
    under: dict[int, list[int]] = {}
    for c, proc in table.items():
        under.setdefault(proc.get("ppid"), []).append(c)
    out, todo, seen = {}, [root], set()
    while todo:
        x = todo.pop()
        if x in seen:
            continue
        seen.add(x)
        if x not in cpu or not table[x].get("start"):
            return None
        toks, browser = table[x].get("toks") or [], is_browser(comm.get(x))
        base = os.path.basename(comm.get(x) or "") or "?"
        if not browser and base not in SHELLS + MORE_SHELLS + LOCAL_FILTERS:
            return None
        if base in SHELLS + MORE_SHELLS:
            args = toks[1:]
            # a shell running a script FILE cannot be read (the waiting word is in the file): keep.
            # Readable: a bare shell (switches only) and `-c <command>`.
            if "-c" not in args and any(not a.startswith("-") for a in args):
                return None
            if SHELL_WAITS.search(" ".join(a for a in args if not a.startswith("-"))):
                return None
        out[f"{x}|{table[x]['start']}"] = [cpu[x], browser]
        todo.extend(under.get(x, []))
    return out


def still_step(old: dict | None, cur: dict, now: float, browser_s_per_day: float) -> dict:
    """One reading against the last one: the entry to keep ({since, at, n, procs}). Pure. `since`
    stands only when the tree is the SAME processes (pid and start), no process that is not a
    browser used any processor time since the last reading, and the browsers together used no
    more than their idle allowance for the time since that reading. Anything else — a process came or went (a loop that starts `ssh` then
    `sleep` changes at every turn), a job computed — starts a new baseline at `now`."""
    b_now = sum(sec for sec, browser in cur.values() if browser)
    fresh = {"since": now, "at": now, "n": 1, "b0": b_now, "procs": cur}
    if not isinstance(old, dict) or not isinstance(old.get("procs"), dict):
        return fresh
    was, since, at, n, b0 = old["procs"], old.get("since"), old.get("at"), old.get("n"), old.get("b0")
    if (not isinstance(since, (int, float)) or not isinstance(at, (int, float)) or not isinstance(n, int)
            or not isinstance(b0, (int, float))
            or not since <= at <= now or set(was) != set(cur)):
        return fresh
    used = 0.0
    for k, (sec, browser) in cur.items():
        try:
            d = sec - float(was[k][0])
        except (TypeError, ValueError, IndexError):
            return fresh
        if d < 0 or (d > 0 and not browser):
            return fresh
        used += d
    # Two lines, both must hold: since the LAST reading (five idle days do not buy a busy hour),
    # and since the baseline (many short readings do not add up to more than the day's allowance).
    if (used > browser_s_per_day * max(now - at, 3600.0) / 86400.0
            or b_now - b0 > browser_s_per_day * max(now - since, 3600.0) / 86400.0):
        return fresh
    return {"since": since, "at": now, "n": n + 1, "b0": b0, "procs": cur}


def still_since(work: list[dict] | None, table: dict[int, dict] | None, now: float,
                cpu: dict[int, float] | None = None, comm: dict[int, str] | None = None) -> dict[str, float]:
    """{label: since when that process of a session's own has stood still} for `own_work`'s
    processes (MACFIX-1B). Reads `cpu_table()` unless given, and keeps the baselines in
    `child-still.json` under one lock; a process whose tree could not be read as still gets no
    entry AND loses its baseline, one read fewer than STILL_READINGS times gets none yet, and
    none gets one when the file cannot be written. Entries of processes that are gone are
    dropped on each write."""
    if not work or table is None or child_still_minutes() <= 0:
        return {}
    if cpu is None:
        cpu, comm = cpu_table() or (None, None)
    cur, unread = {}, []
    for w in work:
        m = re.match(r"pid (\d+) ", str(w.get("label")))
        if not m or int(m.group(1)) not in table:
            continue
        key = f"{m.group(1)}|{table[int(m.group(1))].get('start')}"
        t = tree_reading(int(m.group(1)), table, cpu, comm)
        if t is None:
            unread.append(key)                                # not still NOW: its baseline goes
        else:
            cur[str(w["label"])] = (key, t)
    alive = {f"{pid}|{proc.get('start')}" for pid, proc in table.items()}
    out = {}
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with open(config.state() / "child-still.lock", "w") as lk:
            common.lock_file(lk)
            try:
                doc = json.loads((config.state() / CHILD_STILL).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                doc = {}
            doc = doc if isinstance(doc, dict) else {}
            had = json.dumps(doc, sort_keys=True)
            for label, (key, t) in cur.items():
                doc[key] = still_step(doc.get(key), t, now, browser_idle_cpu_s())
                if doc[key]["n"] >= STILL_READINGS:
                    out[label] = doc[key]["since"]
            doc = {k: v for k, v in doc.items() if k in alive and k not in unread}
            if json.dumps(doc, sort_keys=True) != had:
                tmp = config.state() / (CHILD_STILL + ".tmp")
                tmp.write_text(json.dumps(doc) + "\n", encoding="utf-8")
                os.replace(tmp, config.state() / CHILD_STILL)
    except OSError:
        return {}
    return out


def unknown_line_s(idle_minutes: float) -> float:
    """The age past which an UNKNOWN no longer keeps a finished session (CLOSETUNE-1)."""
    return idle_minutes * 60 * closerules.UNKNOWN_FACTOR


STALE_NOTE = "stale wait loop not counted"


def notes(f: dict, now: float, idle_minutes: float) -> list[str]:
    """What is printed beside a close and never keeps it: the last words, uncommitted paths."""
    out = []
    if f.get("scanned") and f.get("end") is not None:
        stale = stale_loops(f, now)
        v = closerules.finished(f.get("end"), now, idle_minutes, f.get("handoff_done"), f.get("seat"),
                                nothing_runs=_nothing_runs(f) or bool(stale),
                                report_min=f.get("report_min", closerules.REPORT_IDLE_MIN),
                                seat_addrs=f.get("seat_addrs") or ())
        out += v["notes"]
        if v["how"]:
            out.insert(0, "finished: " + v["how"])
        said = closerules.said(f.get("end"), f.get("handoff_done"), f.get("seat"),
                               f.get("seat_addrs") or ())
        out += [f"{STALE_NOTE}: {label} — " + ("it reported, " if said else "it did not report, ")
                + f"its last turn is {(v['age_s'] or 0) / 60:.0f} min old, and only an idle shell, "
                "a sleep loop or a process that has stood still is left under it" for label in stale]
        if f.get("screen") is None and _no_prompt_possible(f):
            out.append("its screen could not be read; its last turn ended with no tool call or "
                       "task open, so no prompt can be up")
    return out


def uncommitted_note(f: dict) -> str | None:
    """The line a close carries about work not in git: the paths, or that git could not say."""
    u = f.get("unsaved")
    if u is None:
        wrote = sorted(f.get("wrote") or [])
        return ("uncommitted: unknown (git could not answer); it wrote "
                + (", ".join(wrote[:8]) + (" …" if len(wrote) > 8 else "") if wrote else "nothing"))
    if u:
        return "uncommitted: " + ", ".join(u[:8]) + (" …" if len(u) > 8 else "")
    return None


STATE_LINE = re.compile(r"^\s*(?:[#>*_-]+\s*)*State\b[*_]*(.*)$")
NOT_STATE = re.compile(r"^\s*[A-Za-z]+\s*:")          # `State machine: …` is not the State
STATE_DONE = re.compile(r"^(DONE|LANDED|STOPPED)\b")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def handoff_state_done(text: str) -> bool:
    """Whether a handoff's State says DONE / LANDED / STOPPED (capitals, as the house writes them).
    The State line is the FIRST line outside a fenced block that starts `State` (after heading,
    quote, list or bold marks: `## State`, `## State: X`, `- State: X`, `**State** X`,
    `State — X`), but not `State <word>:`; its value is the rest of that line, or the next
    non-empty line when the rest is empty. That line decides, whatever it says. The value must
    START with one of the words, so `NOT LANDED` and `IN PROGRESS — DONE after review` never count
    (CLOSETUNE-1 review MF1 and its fix review)."""
    lines, fence = text.splitlines(), None
    for i, l in enumerate(lines):
        f = FENCE.match(l)
        if fence is None and f:
            fence = f.group(1)
            continue
        if fence is not None:
            if f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence) \
                    and not l.strip()[len(f.group(1)):].strip():
                fence = None
            continue
        m = STATE_LINE.match(l)
        if not m or NOT_STATE.match(m.group(1)):
            continue
        rest = m.group(1).lstrip(" \t*_:`>—–-")
        if not rest.strip():
            rest = next((x for x in lines[i + 1:i + 6] if x.strip()), "")
        return bool(STATE_DONE.match(rest.lstrip(" \t*_`>:—–-")))
    return False


WORKTREE_SEG = re.compile(r"/\.claude/worktrees/[^/]+/")
OWN_HANDOFFS_MAX = 5


def own_handoffs(end: dict | None) -> list[str]:
    """The handoff files a session wrote (Write/Edit on a `HANDOFF*.md`) IN ITS FINAL TURN, newest
    last. One it touched in an earlier turn is not its last act: a later commit of that file by
    someone else must not read as this session finishing (review optional 1)."""
    w = (end or {}).get("writes") or {}
    began = (end or {}).get("began")
    if began is None:
        return []
    return [p for p in sorted(w, key=lambda k: w[k] or 0.0)
            if closerules.HANDOFF_NAME.search(p) and (w[p] or 0.0) >= began][-OWN_HANDOFFS_MAX:]


def handoff_done(pattern: str | None, cwd: str | None, paths=()) -> float | None:
    """Criterion (a): the AUTHOR time (`%at`: a rebase, amend or cherry-pick resets the committer
    time, never this) of the last commit of a handoff whose State says done, landed or stopped.
    None when there is no such file, it is not committed, or git cannot say.

    The handoff is one the launch ledger named (`pattern`) or, CLOSETUNE-2 (2026-10-02), one the
    session WROTE (`paths`, from `own_handoffs`): no launch of 49 named a handoff, so the rule saw
    none. A path inside a build worktree that is gone is read at the same place in the main
    checkout, where the merge put it (the commit, and so its author time, is the same one)."""
    import glob                                               # noqa: PLC0415
    cands = glob.glob(_in(cwd, pattern)) if pattern else []
    for q in paths or ():
        q = _in(cwd, q)
        cands.append(q if os.path.exists(q) else WORKTREE_SEG.sub("/", q, count=1))
    best = None
    for p in cands:
        try:
            if not handoff_state_done(Path(p).read_text(encoding="utf-8", errors="replace")):
                continue
            g = subprocess.run(["git", "--no-optional-locks", "-C", os.path.dirname(p) or ".", "log",
                                "-1", "--format=%at", "--", os.path.basename(p)], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
        t = (g.stdout or "").strip()
        if g.returncode == 0 and t.isdigit():
            best = max(best or 0.0, float(t))
    return best


def handoff_written(pattern: str | None, since: float | None, cwd: str | None = None) -> bool:
    if not pattern:
        return False
    import glob                                               # noqa: PLC0415
    for p in glob.glob(_in(cwd, pattern)):
        try:
            if os.stat(p).st_mtime >= (since or 0):
                return True
        except OSError:
            continue
    return False


def tmux_attached(name: str) -> bool:
    """Whether a tmux session has a client attached. False when unreadable (as `screen_attached`)."""
    try:
        p = subprocess.run(["tmux", "list-clients", "-t", name], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and bool((p.stdout or "").strip())


def screen_attached(name: str) -> bool:
    """Whether a screen session is attached (someone is looking at it). False when unreadable:
    the screen READ that follows decides; an attached screen is extra caution, not the only one."""
    try:
        p = subprocess.run(["screen", "-ls"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    for line in (p.stdout or "").splitlines():
        m = re.match(r"^\s*\d+\.(\S+)\s.*\((Attached|Detached)\)", line)
        if m and m.group(1) == name:
            return m.group(2) == "Attached"
    return False


def gather(r: dict, mux: str | None, me: int | None) -> dict:
    pid = r.get("pid")
    at = r.get("launched_at")
    m = r.get("mux") or (mux if not r.get("outside") else None)
    session = r.get("mux_session") or r["name"]
    if r.get("outside"):
        # CLOSETUNE-2 review MF1: a hand-started session is read ONLY by the session id its registry
        # record names — never the by-name fallback, which can find ANOTHER transcript with the same
        # title (and so lose the fact that the owner typed into this one).
        import context_cap                                    # noqa: PLC0415
        tp = context_cap.locate_transcript(r["sid"], None) if _str(r.get("sid")) else None
    else:
        tp = transcript_for(pid, r.get("name"), at)
    try:
        big = tp is not None and tp.stat().st_size > SCAN_MAX_BYTES
    except OSError:
        big = True
    sc = closerules.scan(tp) if tp and not big else None
    if sc is not None and r.get("outside") and sc.get("sid") != r.get("sid"):
        sc = None                                             # not provably its transcript: keep
    # A write whose timestamp did not parse (0) counts: unknown age is never "before the launch".
    last_end = sc["ends"][-1] if sc and sc["ends"] else None
    since_launch = {k: v for k, v in (last_end["writes"] if last_end else {}).items()
                    if not at or not v or v >= at - 60}
    table = full_process_table()
    work = own_work(pid, table)
    return {
        "still": still_since(work, table, time.time()),
        "child_still_min": child_still_minutes(),
        "is_self": bool(me) and pid == me,
        "alive": same_process(pid, r.get("pid_start")),
        "exempt": any(fnmatch.fnmatchcase(r["name"], pat) for pat in exempt_patterns()),
        "handoff": handoff_written(r.get("handoff"), at, r.get("cwd")),
        "handoff_done": handoff_done(r.get("handoff"), r.get("cwd"), own_handoffs(last_end)),
        "children": None if work is None else [w["label"] for w in work],
        "work": work,
        "seat": r.get("seat"),
        "seat_addrs": seat_addresses(r.get("seat")),
        "report_min": report_minutes(),
        "stale_shell_min": stale_shell_minutes(),
        "abandoned_min": abandoned_minutes(),
        "wrote": sorted(since_launch),
        "status": registry_status(pid),
        "scanned": sc is not None,
        "end": sc["last"] if sc else None,
        "screen": read_screen(m, session) if m else None,
        "attached": (screen_attached(session) if m == "screen"
                     else tmux_attached(session) if m == "tmux" else False),
        "unsaved": session_unsaved(r.get("cwd"), r.get("worktree"), since_launch,
                                   (last_end or {}).get("ts")),
        "outside": bool(r.get("outside")),
        "mux": m,
    }


def outside_sessions(rows: list[dict] | None = None) -> list[dict]:
    """Live sessions NOT launched by `sessions.py launch`, from the CLI's own registry records
    (`~/.claude/sessions/<pid>.json`), as launch-shaped rows. Identity is the record's pid plus its
    `procStart`, compared with the live process's start time — never a pattern search."""
    import resume_gate                                        # noqa: PLC0415
    known = {r.get("pid") for r in current_launches(rows)}
    out = []
    try:
        recs = sorted(resume_gate.sessions_dir().glob("*.json"))
    except OSError:
        return out
    for f in recs:
        try:
            doc = json.loads(f.read_text())
            # CLOSETUNE-2 review MF2: the strict reader — an int or an ASCII digit string, never a
            # bool or float; inside the try, so one malformed record never stops the pass.
            pid = _pid_of(doc) if isinstance(doc, dict) else None
        except (OSError, ValueError, TypeError):
            continue
        if pid is None:
            continue
        if pid in known or doc.get("kind") not in (None, "interactive"):
            continue
        sty = screen_of(pid)
        reg_start = _str(doc.get("procStart"))
        # CLOSETUNE-2: when the live process's start matches the registry's (within the tolerance),
        # the identity every later check compares against is the start `ps` printed, so a pid reused
        # after this read is another process to `same_process` and to `close_refusals`.
        live = pid_start(pid)
        start = live if registry_start_matches(reg_start, live) else registry_start_local(reg_start)
        out.append({"name": doc.get("name") or f"(unnamed {str(doc.get('sessionId'))[:8]})",
                    "pid": pid, "pid_start": start, "reg_start": reg_start, "reg_pid": doc.get("pid"),
                    "reg_file_pid": int(f.stem) if f.stem.isdigit() else None,
                    "entrypoint": _str(doc.get("entrypoint")),
                    "cwd": doc.get("cwd"),
                    "sid": doc.get("sessionId"), "launched_at": (doc.get("startedAt") or 0) / 1000,
                    "mux": "screen" if sty else None, "mux_session": sty, "outside": True,
                    "named": bool(doc.get("name"))})
    return out


def registry_start_local(s: str | None) -> str | None:
    """The registry's `procStart` is UTC ("Sun Sep 27 11:58:44 2026" for a process `ps` says started
    "Sun 27 Sep 13:58:44 2026" in CEST — measured 2026-09-27); convert it to local time in `ps`
    tokens so `_same_start` compares like with like. None when it does not parse."""
    import calendar                                           # noqa: PLC0415
    try:
        t = calendar.timegm(time.strptime(" ".join(str(s).split()), "%a %b %d %H:%M:%S %Y"))
    except (ValueError, TypeError):
        return None
    lt = time.localtime(t)
    return time.strftime(f"%a {lt.tm_mday} %b %H:%M:%S %Y", lt)


def resume_line(r: dict) -> str:
    """The one command that brings a closed session back (the owner's own resume habit)."""
    who = r["name"] if (not r.get("outside") or r.get("named")) else (r.get("sid") or r["name"])
    cwd = r.get("cwd")
    return (f"cd {shlex.quote(cwd)} && " if cwd else "") + f"claude -r {shlex.quote(str(who))}"


def close_log(line: str) -> None:
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with (config.state() / "session-close.log").open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\t" + line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ the safe close (HANDLINES-1) ----
# On 2026-09-28 a computed kill target ended the owner's own session, Terminal and every host
# (KILLDOOR-1). The close below is the only path that ends a session, and it may end one ONLY when
# every fact in `close_refusals` holds, each read fresh from the process table at the moment of the
# close: the ledger row this tool wrote at launch (pid AND start time — a pid alone is reused) — or,
# since CLOSETUNE-2, the CLI's registry record naming that pid and start time (`registry_refusals`) — a
# claude process whose parent chain reaches the screen/tmux session the launcher created with only
# shells in between, not a host, not attended, not on `session_close_never`. The close itself is
# `screen -S <screenpid>.<name> -X quit` (or `tmux kill-session -t =<name>`): never a signal to a
# pid, never `kill -9`. A session that outlives the quit is reported, and stays.

CHAIN_MAX_HOPS = 6
SHELLS = ("zsh", "bash", "sh", "-zsh", "-bash", "-sh")
ATTENDED_VAR = "CLAUDE_CODE_SESSION_ATTENDED"


def close_never() -> list[str]:
    """`session_close_never`: name patterns (fnmatch) that are never closed, whatever else holds."""
    try:
        v = limits.get("session_close_never", [])
    except KeyError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def full_process_table() -> dict[int, dict] | None:
    """EVERY process: pid -> {ppid, start, toks}. The parent-chain walk needs the shells and the
    screen between a session and its launcher, which `process_table()` (claude only) leaves out."""
    try:
        p = subprocess.run(["ps", "-Ao", "pid=,ppid=,lstart=,command="], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    out = {}
    for line in (p.stdout or "").splitlines():
        bits = line.split()
        if len(bits) < 8:
            continue
        try:
            out[int(bits[0])] = {"ppid": int(bits[1]), "start": " ".join(bits[2:7]), "toks": bits[7:]}
        except ValueError:
            continue
    return out


def mux_session_pids(mux: str | None, name: str) -> list[int] | None:
    """The pids a session named EXACTLY `name` hangs under: screen's own pid (`<pid>.<name>`), or
    tmux's pane pids. [] = no such session; None = the multiplexer could not be read."""
    try:
        if mux == "screen":
            p = subprocess.run(["screen", "-ls"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=5)
            out = []
            for line in (p.stdout or "").splitlines():
                m = re.match(r"^\s*(\d+)\.(\S+)\s", line)
                if m and m.group(2) == name:
                    out.append(int(m.group(1)))
            if not out and "No Sockets found" not in (p.stdout or "") and "There " not in (p.stdout or ""):
                return None
            return out
        if mux == "tmux":
            p = subprocess.run(["tmux", "list-panes", "-s", "-t", f"={name}", "-F", "#{pane_pid}"],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5)
            if p.returncode != 0:
                return []
            return [int(x) for x in (p.stdout or "").split() if x.isdigit()]
    except (OSError, subprocess.TimeoutExpired):
        return None
    return []


def process_env(pid) -> str | None:
    """The process's environment as `ps eww` prints it (own-user processes only), or None."""
    try:
        p = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(int(pid))], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return None
    return p.stdout if p.returncode == 0 else None


def attended(env_text: str | None) -> bool | None:
    """True when the session says a person is at it; None when its environment could not be read."""
    if env_text is None:
        return None
    m = re.search(r"(?:^|\s)" + ATTENDED_VAR + r"=(\S*)", env_text)
    return bool(m) and m.group(1).lower() not in ("", "0", "false", "no")


def chain_to(pid: int, table: dict[int, dict], anchors: list[int]) -> tuple[int | None, str | None]:
    """(the anchor the parent chain of `pid` reaches, None) or (None, why not). Only shells, and the
    `login` screen itself starts each window with, may sit between the session and its screen: a
    Terminal, a host or anything else in between means the process is not the one the launcher put
    there. (A tmux pane pid IS the session.)"""
    if pid in anchors:
        return pid, None
    cur = table.get(pid, {}).get("ppid")
    for _ in range(CHAIN_MAX_HOPS):
        if cur is None or cur <= 1:
            break
        if cur in anchors:
            return cur, None
        proc = table.get(cur)
        if proc is None:
            return None, f"its parent chain breaks at pid {cur}"
        base = os.path.basename(proc["toks"][0]) if proc["toks"] else ""
        # screen on macOS starts each window as `login -pflq <user> <cmd>` (measured HANDLINES-1):
        # a `login` whose parent IS the anchor is screen's own; a Terminal's `login` is not.
        if base == "login" and proc.get("ppid") in anchors:
            cur = proc.get("ppid")
            continue
        if base not in SHELLS:
            return None, (f"pid {cur} ({base or '?'}) sits between it and its screen: only a "
                          f"shell may")
        cur = proc.get("ppid")
    return None, "its parent chain does not reach the screen/tmux session the launcher created"


def mux_strangers(pid: int, table: dict[int, dict], anchors: list[int], mux: str | None) -> list[int]:
    """The processes a quit would end that are NOT this session: every process under the screen the
    chain reaches (tmux: under every pane of the session, and the other panes themselves), minus
    the session, its own descendants and the shells/`login` between it and its screen.
    `screen -X quit` and `tmux kill-session` end EVERY window and pane, so a second window he opened
    in that screen (a dev server, an editor) is one of these (HANDLINES-1 review MF1)."""
    kids: dict[int, list[int]] = {}
    for p, proc in table.items():
        kids.setdefault(proc.get("ppid"), []).append(p)

    def below(root: int) -> set[int]:
        out, todo = set(), [root]
        while todo:
            for c in kids.get(todo.pop(), []):
                if c not in out:
                    out.add(c)
                    todo.append(c)
        return out

    reached, _ = chain_to(pid, table, anchors)
    if reached is None:
        return []
    own = {pid, reached} | below(pid)
    cur = table.get(pid, {}).get("ppid")
    for _ in range(CHAIN_MAX_HOPS):
        if cur is None or cur == reached or cur <= 1:
            break
        own.add(cur)
        cur = table.get(cur, {}).get("ppid")
    roots = list(anchors) if mux == "tmux" else [reached]
    seen: set[int] = set()
    for a in roots:
        seen |= {a} | below(a)
    return sorted(seen - own)


def close_refusals(r: dict, table: dict[int, dict] | None, anchors: list[int] | None,
                   env_text: str | None, never: list[str] | None = None) -> list[str]:
    """Why this session may NOT be closed, each reason named. [] = every safety fact holds. Pure:
    the process table, the multiplexer's session pids and the process environment are passed in."""
    never = close_never() if never is None else never
    out = []
    if r.get("outside"):
        # CLOSETUNE-2: the second admission. A hand-started session is admissible only on the
        # registry's own pid + start time and its own screen window; every fact below still holds.
        why = registry_refusals(r, table)
        if why:
            out.append("ledger: not launched by `sessions.py launch` — closing it is the owner's")
            out.extend(why)
    pid, start = r.get("pid"), r.get("pid_start")
    names = [n for n in (r.get("name"), r.get("mux_session")) if n]
    if not pid or not start:
        out.append("ledger: no pid and start time were recorded at launch")
    if table is None:
        out.append("process: the process table could not be read")
        return out
    proc = table.get(int(pid)) if pid else None
    if pid and proc is None:
        out.append(f"process: pid {pid} is gone")
    elif proc is not None:
        if not _same_start(proc["start"], start):
            out.append(f"identity: pid {pid} started {proc['start']!r}, the ledger recorded "
                       f"{start!r} — a reused pid is another process")
        toks = proc["toks"]
        if not _image_is_claude(toks):
            out.append(f"process: pid {pid} is not a claude process ({(toks or ['?'])[0]})")
        if is_host(toks):
            out.append("host: it is a Remote Control host")
        n = host_name(toks)
        if n:
            names.append(n)
    for n in names:
        hit = next((p for p in never if fnmatch.fnmatchcase(n, p)), None)
        if hit:
            out.append(f"never: {n!r} matches session_close_never pattern {hit!r}")
            break
    if (r.get("mux") not in ("screen", "tmux")) or not r.get("mux_session"):
        out.append("screen: the ledger names no screen/tmux session to end")
    elif anchors is None:
        out.append("screen: the multiplexer could not be read")
    elif not anchors:
        out.append(f"screen: no {r.get('mux')} session named exactly {r.get('mux_session')!r}")
    elif proc is not None:
        _, why = chain_to(int(pid), table, anchors)
        if why:
            out.append("chain: " + why)
        else:
            others = mux_strangers(int(pid), table, anchors, r.get("mux"))
            if others:
                shown = ", ".join(f"{p} ({os.path.basename((table.get(p, {}).get('toks') or ['?'])[0])})"
                                  for p in others[:5])
                out.append(f"screen: it holds other programs a quit would end too ({shown}"
                           f"{', …' if len(others) > 5 else ''}) — closing it is the owner's")
    a = attended(env_text)
    if a is None:
        out.append("attended: its environment could not be read")
    elif a:
        out.append(f"attended: {ATTENDED_VAR} is set — a person is at it")
    return out


# ---------------------------------------- the second admission: the CLI's registry (CLOSETUNE-2) ----
# The owner, 2026-09-30: "Extend the close to hand-started sessions inside a screen window that the
# session registry knows by pid + start time, same time rules, still never a terminal-window or
# Desktop session, never a host, never one you have typed into."
#
# The registry file `~/.claude/sessions/<pid>.json` is the CLI's own record: `pid`, and `procStart`,
# the process start time in UTC at whole-second resolution. Measured 2026-09-30 on 27 live records:
# `procStart` equals `ps -o lstart` to the second in 27 of 27 (difference 0 s), while `startedAt`
# (epoch ms, written when the session began) runs 0.7-4.2 s later, so only `procStart` is used.
# Tolerance 1 s: both are the kernel's one start time printed at 1-second resolution, and one
# writer truncating where the other rounds is at most 1 s apart. A pid the kernel reuses within
# the same second is not a real case (the pid space is 99,999 and wraps in minutes, not a second).

REGISTRY_START_TOLERANCE_S = 1.0
REGISTRY_ADMITTED = "registry: pid + start time match, screen window"


def registry_start_epoch(s: str | None) -> float | None:
    """The registry's `procStart` (UTC, "Sun Sep 27 11:58:44 2026") as epoch seconds, or None."""
    import calendar                                           # noqa: PLC0415
    try:
        return float(calendar.timegm(time.strptime(" ".join(str(s).split()), "%a %b %d %H:%M:%S %Y")))
    except (ValueError, TypeError, OverflowError):
        return None


def registry_start_matches(reg_start: str | None, ps_start: str | None) -> bool:
    """The registry's `procStart` and the start `ps` prints name the same moment, within
    REGISTRY_START_TOLERANCE_S. Unparseable on either side = no match."""
    a, b = registry_start_epoch(reg_start), _start_epoch(ps_start) if ps_start else None
    return a is not None and b is not None and abs(a - b) <= REGISTRY_START_TOLERANCE_S


def registry_refusals(r: dict, table: dict[int, dict] | None) -> list[str]:
    """Why a session NOT in the launch ledger is not admissible by the registry ([] = admissible).
    Pure. `r` carries what `outside_sessions` read from the registry file: `reg_file_pid` (the pid
    in the file's name), `pid` (the pid the record names), `reg_start` (its `procStart`),
    `entrypoint`, and `mux`/`mux_session` from the process's own STY. The screen window, host,
    never-list, attended and chain facts are `close_refusals`' own and are not repeated here."""
    out = []
    pid = r.get("pid")
    ep = r.get("entrypoint")
    if ep == "claude-desktop":
        out.append("desktop: a Claude Desktop session — closing it is the owner's")
    elif ep != "cli":
        out.append(f"registry: entrypoint {ep!r} is not a session started by hand with `claude`")
    raw = r.get("reg_pid", pid)
    if (not isinstance(pid, int) or isinstance(pid, bool) or _pid_of({"pid": raw}) != pid
            or r.get("reg_file_pid") != pid):
        out.append(f"registry: the record {r.get('reg_file_pid')}.json names pid {raw!r}, not its own")
    if not r.get("reg_start"):
        out.append("registry: the record carries no procStart")
    elif table is not None:
        proc = table.get(pid) if isinstance(pid, int) else None
        if proc is not None and not registry_start_matches(r.get("reg_start"), proc.get("start")):
            out.append(f"registry: pid {pid} started {proc.get('start')!r}, the registry recorded "
                       f"{r.get('reg_start')!r} UTC — more than {REGISTRY_START_TOLERANCE_S:g} s apart")
    if r.get("mux") != "screen" or not r.get("mux_session"):
        out.append("window: it runs in a terminal window, not a screen — closing it is the owner's")
    return out


def live_refusals(r: dict) -> tuple[list[str], int | None]:
    """`close_refusals` over facts read NOW; also the screen pid the chain reached (the exact
    session to quit)."""
    table = full_process_table()
    mux = r.get("mux")
    anchors = mux_session_pids(mux, r.get("mux_session") or "") if mux else None
    env_text = process_env(r.get("pid")) if r.get("pid") else None
    why = close_refusals(r, table, anchors, env_text)
    anchor = None
    if not why and table is not None and anchors:
        anchor, _ = chain_to(int(r["pid"]), table, anchors)
    return why, anchor


def closer() -> str:
    """Who is closing, for the log: the seat's name, else this session's id, else the terminal."""
    return (launching_seat() or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or ("terminal" if os.isatty(0) else "unknown"))


def safe_close_log(action: str, r: dict, why: str, by: str) -> None:
    """`close.log`: when, what, name, pid, start, why, by whom — one line per close or refusal."""
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with (config.state() / "close.log").open("a", encoding="utf-8") as fh:
            fh.write("\t".join([time.strftime("%Y-%m-%dT%H:%M:%S"), action, str(r.get("name")),
                                f"pid {r.get('pid')}", f"start {r.get('pid_start')}", why,
                                f"by {by}"]) + "\n")
    except OSError:
        pass


def cursor_path(name: str = STOP_CURSOR) -> Path:
    return config.state() / name


def read_cursor(name: str = STOP_CURSOR) -> dict | None:
    """Where the last pass that ran out of budget stopped: {name, pid, launched_at}, or None. A file
    that cannot be read as that — whatever is in it — is no cursor; it never stops a pass."""
    try:
        doc = json.loads(cursor_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("name"), str):
        return None
    return {"name": doc["name"], "pid": doc.get("pid"), "launched_at": _launched(doc)}


def write_cursor(r: dict | None, name: str = STOP_CURSOR) -> None:
    """Remember the last session judged (None: the pass reached the end, the next starts at the
    oldest). Written whole beside the file and renamed into place; never removed, so an empty
    document is "none". One fixed temp name: a failed rename leaves at most one file behind."""
    doc = {} if r is None else {"name": str(r.get("name")), "pid": r.get("pid"),
                                "launched_at": _launched(r),
                                "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        tmp = cursor_path(name).with_name(name + ".tmp")
        tmp.write_text(json.dumps(doc) + "\n", encoding="utf-8")
        os.replace(tmp, cursor_path(name))
    except (OSError, TypeError, ValueError):
        pass


def _launched(r: dict) -> float:
    """A row's launch time as a finite number; anything else (missing, a bool, NaN, infinite, an
    integer too large for a float) is 0 — the row sorts first, it never raises."""
    at = r.get("launched_at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return 0.0
    try:
        v = float(at)
    except OverflowError:
        return 0.0
    return v if v == v and v not in (float("inf"), float("-inf")) else 0.0


def pass_order(rows: list[dict], cursor: dict | None) -> list[dict]:
    """The rows oldest launch first, turned so the pass begins right AFTER the cursor's session and
    ends with the ones the last pass already judged. Every row stays in the list exactly once. A
    cursor naming a session no longer here resumes at the first one launched after it. The
    cursor's session is the row with its name AND pid: two live sessions can carry one name (a
    session resumed by hand while the first still runs), and matching the name alone sent every
    pass back to the first of them."""
    rows = sorted(rows, key=_launched)                        # stable: equal ages keep their order
    if not cursor or not rows:
        return rows
    at = next((n + 1 for n, r in enumerate(rows)
               if r.get("name") == cursor["name"] and r.get("pid") == cursor.get("pid")), None)
    if at is None:
        at = next((n for n, r in enumerate(rows) if _launched(r) > cursor["launched_at"]), 0)
    at %= len(rows)
    return rows[at:] + rows[:at]


def sweep(apply: bool, now: float | None = None, gather_fn=None, refusals_fn=None,
          budget_s: float = SWEEP_BUDGET_S, rows: list[dict] | None = None,
          by: str | None = None, log_proposals: bool = True, resume: bool = False,
          cap_s: float | None = None, until_found: int = 0, stats: dict | None = None,
          timer=time.time, alive_fn=None, cursor: str = STOP_CURSOR) -> list[dict]:
    """One pass over the live sessions — the launch ledger's, and (SESSCLOSE-2) every other live
    interactive session the CLI has a record of. Returns one item per live session with its verdict.

    A session is closeable when `classify` finds it finished AND `close_refusals` finds every safety
    fact holding. `apply` False: nothing is closed; closeable sessions come back as proposals, and
    each refused one with its reasons. True: each closeable session is closed by `apply_item`
    (facts gathered AGAIN, both checks re-run, only then its screen quit). Every close and refusal
    goes to close.log with the line that reopens it (`claude -r <name>`), so a wrong close costs one
    command. Each item carries `last_ts` (its last turn's end) so a caller can close oldest first.

    The budget (CLOSEBUDGET-1). `budget_s` counts from the first session JUDGED, not from the
    listing; `cap_s` (the Stop entry) bounds the whole pass from its start. Every row is tested
    for a live process (`alive_fn`, as shipped `same_process`) BEFORE any fact is gathered:
    gathering the facts of ~180 ended ledger rows (a 2 s screen read each) spent the whole budget
    before the first live session (measured 2026-10-02: 8,160 "deferred" lines, `judged: 0`). A
    caller that injects its own `gather_fn` decides `alive` there, unless it gives `alive_fn` too.
    `resume`: the pass runs oldest
    launch first, begins after the session the last budget-cut pass stopped at (`pass_order`, the
    file `cursor`), and records where it stops, so every session is judged within a few passes. `until_found`: the
    budget does not end the pass before that many closable sessions are found (up to
    `BUDGET_STRETCH` budgets; never past `cap_s`). What the budget leaves unjudged is ONE log line
    with a count. `stats`, when given, receives {live, judged, deferred, found}."""
    t_pass = timer()
    clock = (lambda: now) if now is not None else time.time
    now = clock()
    mux = multiplexer()
    # The NEAREST claude process: the thread's or session's own pid (since `procs.is_claude`
    # tests the executable, PROCSHOST-1), and for a pass run right under a host the HOST, whose
    # row then keeps `is_self`. `own_pid` is None there and the host's row would lose that keep.
    me = procs.claude_pid()
    idle = idle_minutes()
    refusals_fn = refusals_fn or live_refusals
    by = by or closer()
    items = []
    cands = current_launches() if rows is None else rows
    if rows is None:
        cands = cands + outside_sessions(cands)
    if alive_fn is None and gather_fn is None:
        alive_fn = same_process
    gather_fn = gather_fn or gather
    if alive_fn is not None:
        # an ended row is nothing to close or report: drop it on the one cheap read, before `gather`.
        # Inside the cap: with a slow `ps` the rows not yet read stay in and are left for later.
        kept = []
        for n, r in enumerate(cands):
            if cap_s is not None and timer() - t_pass > cap_s:
                kept.extend(cands[n:])
                break
            if (bool(me) and r.get("pid") == me) or alive_fn(r.get("pid"), r.get("pid_start")):
                kept.append(r)
        cands = kept
    if resume:
        cands = pass_order(cands, read_cursor(cursor))
    started = timer()
    judged = found = 0
    last = None
    for n, r in enumerate(cands):
        t = timer()
        spent = t - started > budget_s and (found >= until_found
                                            or t - started > budget_s * BUDGET_STRETCH)
        if spent or (cap_s is not None and t - t_pass > cap_s):
            left = len(cands) - n
            close_log(f"deferred\t{left} session(s)\tpass budget {budget_s:.0f}s spent after "
                      f"{judged} judged" + (f"; the next pass resumes after {last['name']}"
                                            if resume and last else ""))    # none judged: as before
            break
        last = r
        judged += 1
        f = gather_fn(r, mux, me)
        if not f.get("alive") and not f.get("is_self"):
            continue                                          # gone: nothing to close or report
        keep = classify(f, now, idle)
        refused, _anchor = ([], None) if keep else refusals_fn(r)
        end = f.get("end") or {}
        observe_prompt(r, screen_prompt(f), time.time(),
                       attach_line(r.get("mux") or mux, r.get("mux_session") or r["name"]),
                       since=None if f.get("scanned") else r.get("launched_at"))
        item = {"name": r["name"], "pid": r.get("pid"), "keep": keep, "refused": refused,
                "closed": False, "outside": bool(r.get("outside")), "resume": resume_line(r),
                "admission": REGISTRY_ADMITTED if r.get("outside") else "ledger",
                "attach": attach_line(r.get("mux") or mux, r.get("mux_session") or r["name"]),
                "last_ts": end.get("ts"), "notes": [] if keep else notes(f, now, idle),
                "uncommitted": uncommitted_note(f), "row": r}
        if not keep and not refused:
            found += 1
        if not keep and not refused and apply:
            apply_item(item, mux=mux, me=me, clock=clock, gather_fn=gather_fn,
                       refusals_fn=refusals_fn, by=by)
        elif not keep and refused and apply and not r.get("outside"):
            # a registry-only session is refused on every pass by design: logging it would write a
            # line per session per 5-minute Stop pass (the builder's measurement); ledger rows only
            safe_close_log("refused", r, "; ".join(refused), by)
        elif not keep and not refused and log_proposals:
            close_log(f"proposed\t{r['name']}\tpid {r['pid']}\t(dry run or session_close_apply off)"
                      f"\treopen: {item['resume']}")
        items.append(item)
    if resume and judged:                    # a pass that judged nobody keeps the place it had
        write_cursor(last if judged < len(cands) else None, cursor)
    if stats is not None:
        stats.update(live=len(cands), judged=judged, deferred=len(cands) - judged, found=found)
    return items


def apply_item(item: dict, mux: str | None = None, me: int | None = None, clock=time.time,
               gather_fn=gather, refusals_fn=None, by: str = "unknown", close_fn=None) -> bool:
    """Close ONE item `sweep` found closeable: gather its facts again, re-run `classify` and
    `close_refusals`, and only when both still pass quit its screen. Sets item closed/keep/refused;
    returns whether it closed. The close log and the reopen line carry its uncommitted paths."""
    r = item["row"]
    mux = mux if mux is not None else multiplexer()
    refusals_fn = refusals_fn or live_refusals
    idle = idle_minutes()
    f = gather_fn(r, mux, me)
    again = classify(f, clock(), idle)
    refused2, anchor = ([], None) if again else refusals_fn(r)
    if again:
        item["keep"] = ["changed during the pass: " + again[0]]
        return False
    if refused2:
        item["refused"] = refused2
        safe_close_log("refused", r, "; ".join(refused2), by)
        return False
    unc = uncommitted_note(f)
    item["uncommitted"] = unc
    reopen = item["resume"] + (f"   # {unc}" if unc else "")
    how = (close_fn or close_one)(r, r.get("mux") or mux, anchor)
    if how:
        item["closed"] = True
        item["resume"] = reopen
        ns = notes(f, clock(), idle)
        why = ("; ".join(ns[:1] + [n for n in ns[1:] if n.startswith(STALE_NOTE)])
               or f"finished and idle ≥{idle:g} min")
        close_log(f"closed\t{r['name']}\tpid {r['pid']}\t{how}\treopen: {reopen}")
        safe_close_log("closed", r, f"{why}; {how}; reopen: {reopen}", by)
        return True
    item["keep"] = ["the screen quit did not end the recorded pid; it stays"]
    safe_close_log("not-ended", r, "the screen quit did not end it; nothing else was sent", by)
    return False


# --------------------------------------------------------------- the memory line (CLOSETUNE-1) ----
# The owner, 2026-09-30: "automate it … so i dont need to free space by hand in the future myself".
# `close_when_needed` closes the oldest FINISHED sessions (every safety fact above holding) only
# while the machine is over its memory line, and stops the moment it is back under.

def swap_used_limit_mb() -> float:
    """`session_close_swap_used_mb`: swap in use, in MB, at or above which the machine is over the
    line. ABSOLUTE, never a share: macOS shrinks the swap TOTAL as swap drains (a
    measurement 2026-09-30 00:18→00:47: total 9.2 → 7.2 → 5.1 GB while used fell 8.1 → 4.2 GB), so a
    ratio stays "over" for hours on an idle machine with most of its memory free. Not a positive
    number → the shipped 4096."""
    try:
        raw = limits.get("session_close_swap_used_mb", 4096)
        if isinstance(raw, bool):
            return 4096.0                                     # `true` is not a number of MB
        v = float(raw)
    except (TypeError, ValueError, KeyError):
        return 4096.0
    return v if v > 0 and v == v and v != float("inf") else 4096.0


def memory_floor() -> float:
    """`session_close_memory_floor`: the memory-pressure level (macOS `kern.memorystatus_level`, the
    percentage of memory free) below which the machine is over the line. Out of [0, 100] → 30."""
    try:
        v = float(limits.get("session_close_memory_floor", 30))
    except (TypeError, ValueError, KeyError):
        return 30.0
    return v if 0 <= v <= 100 else 30.0


def memory_readings() -> dict:
    """{swap: share of swap in use (0.0 when the machine has no swap) or None, swap_mb: used MB or
    None, level: memory-pressure level 0-100 (percentage free) or None}. Two reads, no more."""
    out = {"swap": None, "swap_mb": None, "level": None}
    try:
        if common.platform() == "macos":
            sw = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=5).stdout
            tot = re.search(r"total\s*=\s*([\d.]+)M", sw)
            used = re.search(r"used\s*=\s*([\d.]+)M", sw)
            if tot and used:
                t, u = float(tot.group(1)), float(used.group(1))
                out["swap"], out["swap_mb"] = (u / t if t > 0 else 0.0), u
            lv = subprocess.run(["sysctl", "-n", "kern.memorystatus_level"], capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=5).stdout.strip()
            level = float(lv)
            if 0 <= level <= 100:
                out["level"] = level
        else:
            info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines()
                        if ":" in l)
            t = float(info["SwapTotal"].split()[0])
            u = t - float(info["SwapFree"].split()[0])
            out["swap"], out["swap_mb"] = (u / t if t > 0 else 0.0), u / 1024
            mt = float(info["MemTotal"].split()[0])
            if mt > 0:
                out["level"] = 100 * float(info["MemAvailable"].split()[0]) / mt
    except (OSError, subprocess.TimeoutExpired, KeyError, ValueError):
        pass
    return out


def over_line(m: dict, limit_mb: float, floor: float) -> list[str] | None:
    """Why the machine is over its memory line ([] = under it), or None when neither figure could be
    read — an unreadable machine is never a reason to close anything. Swap is judged by the MB in
    use (`swap_mb`), never by its share of a total that shrinks as it drains."""
    if m.get("swap_mb") is None and m.get("level") is None:
        return None
    why = []
    if m.get("swap_mb") is not None and m["swap_mb"] >= limit_mb:
        why.append(f"swap {m['swap_mb']:.0f} MB used ≥ {limit_mb:.0f} MB")
    if m.get("level") is not None and m["level"] < floor:
        why.append(f"memory level {m['level']:.0f} < {floor:g}")
    return why


def figures(m: dict) -> str:
    sw = "unreadable" if m.get("swap_mb") is None else f"{m['swap_mb']:.0f} MB used"
    lv = "unreadable" if m.get("level") is None else f"{m['level']:.0f}"
    return f"swap {sw}, memory level {lv}"


def launch_guard() -> str | None:
    """Why `sessions.py launch` would refuse a launch for memory right now, or None."""
    return launch_memory(launch_readings(), launch_level_floor(), launch_swap_free_floor_mb())[0]


GUARD_OVER = "the launch guard refuses"


def close_when_needed(apply: bool, by: str, readings_fn=None, sweep_fn=None,
                      apply_fn=None, settle_s: float = 2.0, all_finished: bool = False,
                      rows: list[dict] | None = None, guard_fn=None) -> dict:
    """Close the oldest finished sessions first, only while the machine is over its memory line
    (`all_finished`: every finished session, whatever the line). Returns {before, after, over,
    closed: [item], candidates: [item], stopped}. A machine under the line costs the readings
    and nothing else: no sweep runs. Ledger rows, and (CLOSETUNE-2) the hand-started sessions the
    registry admits (`registry_refusals`): every other session is refused by `close_refusals`.

    SESSCLOSE-3 — one signal. The machine is ALSO over the line whenever the launch guard would
    refuse a launch (`guard_fn`, as shipped `launch_guard`): measured 2026-10-03, the guard
    refused at pressure level 2 / memory level 40 while this pass, which read only swap and the
    level, printed "under the line". Both are read again before every close. A caller that
    injects its own `readings_fn` and no `guard_fn` gets no guard reading (a test's figures are
    its own; the machine's are not)."""
    if guard_fn is None:
        guard_fn = launch_guard if readings_fn is None else (lambda: None)
    readings_fn = readings_fn or memory_readings
    target, floor = swap_used_limit_mb(), memory_floor()

    def over(m: dict) -> list[str] | None:
        w, g = over_line(m, target, floor), guard_fn()
        return (w or []) + [f"{GUARD_OVER}: {g}"] if g else w

    before = readings_fn()
    why = over(before)
    res = {"before": before, "after": before, "over": why, "closed": [], "candidates": [],
           "stopped": "", "target": target, "floor": floor}
    if not all_finished:
        if why is None:
            res["stopped"] = "neither memory figure could be read: nothing closed"
            return res
        if not why:
            res["stopped"] = "under the line"
            return res
    if rows is None:
        rows = current_launches()
        rows = rows + outside_sessions(rows)
    live = [r for r in rows if same_process(r.get("pid"), r.get("pid_start"))]
    stats: dict = {}
    # Over the line the pass does not stop on its budget before it has found ONE session to close
    # (CLOSEBUDGET-1); a pass cut short resumes after the last session judged.
    items = (sweep_fn or sweep)(apply=False, rows=live, by=by, log_proposals=False,
                                budget_s=NEEDED_BUDGET_S, resume=True, stats=stats,
                                cursor=NEEDED_CURSOR,
                                until_found=0 if all_finished else 1)
    res["stats"] = stats
    cands = sorted((i for i in items if not i["keep"] and not i["refused"]),
                   key=lambda i: (i.get("last_ts") is None, i.get("last_ts") or 0))
    res["candidates"] = cands
    res["items"] = items
    if not apply:
        res["stopped"] = "dry run or session_close_apply off: nothing closed"
        return res
    now_m = before
    for i in cands:
        if not all_finished:
            now_m = readings_fn()            # fresh before EVERY close: the sweep can take minutes
            w = over(now_m)
            if not w:
                res["stopped"] = "back under the line" if w == [] else "the figures became unreadable"
                break
        try:
            done = (apply_fn or apply_item)(i, by=by)
        except Exception as exc:                              # noqa: BLE001 — stop, keep the summary
            res["stopped"] = f"stopped: {i['name']} raised {type(exc).__name__}: {exc}"
            close_log(f"error\t{i['name']}\t{type(exc).__name__}: {exc}")
            break
        if done:
            res["closed"].append(i)
            if settle_s:
                time.sleep(settle_s)
    else:
        res["stopped"] = res["stopped"] or ("no finished session left to close" if cands else
                                            "no finished session to close")
    res["after"] = readings_fn() if res["closed"] else (now_m if cands and not all_finished else before)
    if why:                                  # over the line: one line per pass, whatever it did
        close_log(pass_line("when needed", stats, len(items), len(res["closed"]))
                  + f"\t{figures(before)} -> {figures(res['after'])}\t{res['stopped']}")
    return res


def pass_line(what: str, stats: dict, judged: int, closed: int) -> str:
    """`pass` line of session-close.log: how many live sessions, judged, closed, left for later."""
    return (f"pass\t{what}\tlive {stats.get('live', judged)}, judged {stats.get('judged', judged)}, "
            f"closed {closed}, deferred {stats.get('deferred', 0)}")


def mux_quit_exact(mux: str | None, name: str, anchor: int | None) -> None:
    """End exactly one multiplexer session: screen by `<pid>.<name>`, tmux by `=<name>`."""
    if mux == "screen":
        argv = ["screen", "-S", f"{anchor}.{name}" if anchor else name, "-X", "quit"]
    elif mux == "tmux":
        argv = ["tmux", "kill-session", "-t", f"={name}"]
    else:
        return
    try:
        subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def close_one(r: dict, mux: str | None, anchor: int | None = None, quit_fn=None, alive_fn=None,
              wait_s: float = CLOSE_WAIT_S) -> str | None:
    """End one launched session by quitting the screen/tmux session the launcher created; returns
    how it ended, or None if the recorded pid (with its recorded start time) is still there after
    `wait_s`. The claude in it ends CLEANLY on the hang-up (measured by the seat 2026-09-26). Nothing
    else is sent: no signal to any pid, never `kill -9` (HANDLINES-1)."""
    quit_fn = quit_fn or (lambda m, n: mux_quit_exact(m, n, anchor))
    alive_fn = alive_fn or (lambda: same_process(r.get("pid"), r.get("pid_start")))
    quit_fn(r.get("mux") or mux, r.get("mux_session") or r["name"])
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not alive_fn():
            return "screen quit"
        time.sleep(0.25)
    return None if alive_fn() else "screen quit"


def applies() -> bool:
    """True only when the owner has switched `session_close_apply` on for this install.

    Not in `destructive.REGISTRY`: that registry is for chores that move FILES, and its own test
    pins it to exactly those. The rule is the same one — propose until switched on."""
    try:
        return limits.get("session_close_apply") is True
    except Exception:                                         # noqa: BLE001
        return False


def record_proposals(items: list[dict]) -> None:
    """What the last pass would have closed, for `sessions.py ls` and the owner to read."""
    doc = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "switch": "session_close_apply",
           "judged": len(items), "kept": sum(1 for i in items if i["keep"]),
           "items": [{"name": i["name"], "pid": i["pid"], "outside": bool(i.get("outside")),
                      "reopen": i.get("resume")} for i in items
                     if not i["keep"] and not i["closed"]]}
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        (config.state() / "session-close-proposals.json").write_text(json.dumps(doc, indent=1) + "\n",
                                                                     encoding="utf-8")
    except OSError:
        pass


def claude_config_path() -> Path:
    """The CLI's own settings file (`~/.claude.json`; under `CLAUDE_CONFIG_DIR` when that is set)."""
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(os.path.expanduser(d)) if d else Path.home()) / ".claude.json"


def folder_trust(cwd: str | None, path: Path | None = None) -> bool | None:
    """Whether the CLI will start in `cwd` without its "do you trust this folder" dialog
    (SESSCLOSE-3: a session launched detached into an untrusted folder sat at that dialog for good).
    Read from `projects[<path>].hasTrustDialogAccepted` in the CLI's settings file. True: the
    folder or a folder above it is accepted (the CLI lets a parent vouch — measured 2026-10-06:
    one of ~80 worktrees under an accepted repo has an entry of its own). False: the folder has
    an entry that says not accepted, and nothing above it is accepted. None: it cannot be told —
    the file or its `projects` could not be read, or the folder has no entry at all."""
    try:
        doc = json.loads((path or claude_config_path()).read_text(encoding="utf-8"))
        projects = doc.get("projects") if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None
    here = _real(cwd) if cwd else None
    if not isinstance(projects, dict) or not here:
        return None
    by_real = {}
    for k, v in projects.items():
        if isinstance(k, str) and isinstance(v, dict):
            real = _real(k) or k
            if real == k or real not in by_real:         # an alias only fills a gap
                by_real[real] = v.get("hasTrustDialogAccepted")
    d = here
    while True:
        if by_real.get(d) is True:
            return True
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return False if by_real.get(here) is False else None


PROMPT_SEEN = "prompt-seen.json"


def read_prompt_seen() -> dict:
    """{`name|pid`: {since, name, pid, pid_start, attach}} — every session a pass or `ls` last
    saw with a prompt on its screen, and since when. {} when unread."""
    try:
        doc = json.loads((config.state() / PROMPT_SEEN).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in doc.items() if isinstance(v, dict)} if isinstance(doc, dict) else {}


def observe_prompt(r: dict, prompt: bool | None, now: float, attach: str = "",
                   since: float | None = None, alive_fn=None) -> float | None:
    """Record what one read of a session's screen showed, and return since when it has shown a
    prompt (None: no prompt). `prompt` True: its entry is kept, or begun at `since` (default
    `now`; a session with no transcript counts from its launch). False: its entry is dropped.
    None (the screen could not be read): nothing changes. Entries whose process is gone are
    dropped on every write. One small file under one lock; a failed write loses only the age."""
    key = f"{r.get('name')}|{r.get('pid')}"
    if prompt is None:
        return (read_prompt_seen().get(key) or {}).get("since")
    if not prompt and key not in read_prompt_seen():
        return None                                           # the common read: no lock, no write
    alive_fn = alive_fn or same_process
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with open(config.state() / "prompt-seen.lock", "w") as lk:
            common.lock_file(lk)
            doc = read_prompt_seen()
            had = dict(doc)
            if prompt:
                doc.setdefault(key, {"since": float(since if since else now), "name": r.get("name"),
                                     "pid": r.get("pid"), "pid_start": r.get("pid_start"),
                                     "attach": attach})
            else:
                doc.pop(key, None)
            doc = {k: v for k, v in doc.items()
                   if k == key or alive_fn(v.get("pid"), v.get("pid_start"))}
            if doc != had:
                tmp = config.state() / (PROMPT_SEEN + ".tmp")
                tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
                os.replace(tmp, config.state() / PROMPT_SEEN)
            return (doc.get(key) or {}).get("since") if prompt else None
    except OSError:
        return now if prompt else None


def prompt_words(since: float | None, now: float, flag_min: float | None = None) -> str:
    """`PROMPT 23 min — FLAGGED` past `session_prompt_flag_minutes`, else `PROMPT 2 min`."""
    flag_min = prompt_flag_minutes() if flag_min is None else flag_min
    mins = max(0.0, (now - since) / 60) if isinstance(since, (int, float)) and since else 0.0
    return f"PROMPT {mins:.0f} min" + (" — FLAGGED" if flag_min > 0 and mins >= flag_min else "")


def screen_prompt(f: dict) -> bool | None:
    """True / False: a prompt is / is not on the screen `gather` read; None: no screen was read."""
    screen = f.get("screen")
    return None if screen is None else bool(PROMPT_TEXT.search(screen))


def attach_line(mux: str | None, name: str) -> str:
    if mux == "tmux":
        return f"tmux attach -t {name}"
    if mux == "screen":
        return f"screen -r {name}"
    return "(no multiplexer)"


def due(now: float | None = None, consume: bool = True) -> bool:
    """Machine-wide rate limit for the Stop chore: True at most once per `session_close_every_s`.
    `consume=False` only asks (a dry run must not use up the next real pass's turn)."""
    now = time.time() if now is None else now
    every = int(limits.get("session_close_every_s", 300) or 300)
    st = config.state() / "session-close.last"
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with open(config.state() / "session-close.lock", "w") as lk:
            common.lock_file(lk)
            try:
                last = float(st.read_text().strip() or 0)
            except (OSError, ValueError):
                last = 0.0
            if now - last < every:
                return False
            if consume:
                st.write_text(str(now))
            return True
    except OSError:
        return False


# -------------------------------------------------------------------------- parked messages ----

def parked_path(name: str) -> Path:
    return config.state() / f"parked-{common.safe_sid(name)}.jsonl"


def delivered_path(name: str) -> Path:
    """Where `deliver.py` moves a parked line once it is shown to its recipient (CONTEXTMSG-1)."""
    return config.state() / f"parked-{common.safe_sid(name)}.delivered.jsonl"


def session_name_of(sid: str | None) -> str | None:
    """The NAME of the session with this id: the registry record's `name` first, the process's
    `--name` second, else None.

    ONE key for writer and reader (CONTEXTMSG-1). The resume gate addresses a target by its
    registry name, but the park writer named the SENDER and the facts line named the RECIPIENT by
    `--name` alone, which a resumed session (`claude --resume <id>`) does not carry: the recipient
    was never shown its parked lines and the sender showed as "?"."""
    if sid and sid != "-":
        for rec in registry_records():
            if str(rec.get("sessionId") or "") == sid and rec.get("name"):
                return str(rec["name"])
    return procs.session_name(procs.own_pid())     # never a host's name (PROCSHOST-1)


def park(to: str, sender: str | None, reason: str, text: str) -> Path | None:
    """Keep a message the resume gate refused, where the recipient's successor will be shown it."""
    row = {"schema": PARKED_SCHEMA, "kind": "refused-wake", "to": to, "from": sender or "?",
           "reason": reason, "text": (text or "")[:PARKED_TEXT_CHARS],
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        path = parked_path(to)
        # Locked, and written only into the file that is STILL at the path (CONTEXTMSG-1 review):
        # `deliver.py` claims the parked file by renaming it and then takes this same lock before
        # reading. A park that opened the file just before the rename would otherwise write into
        # the claim after it was read, and the line would be deleted with it.
        for _ in range(20):
            with path.open("a", encoding="utf-8") as fh:
                common.lock_file(fh)
                try:
                    same = os.fstat(fh.fileno()).st_ino == os.stat(path).st_ino
                except FileNotFoundError:
                    same = False
                if same:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                    common.unlock_file(fh)
                    return path
                common.unlock_file(fh)
        raise OSError("the parked file kept moving while it was written")
    except OSError as e:
        common.log("hook-errors", f"fleet\tpark\t{to}\t{e}")
        return None


def append_lines(path: Path, text: str) -> None:
    """Append to a parked file under the same lock and same-file check as `park` (CONTEXTMSG-1)."""
    for _ in range(20):
        with path.open("a", encoding="utf-8") as fh:
            common.lock_file(fh)
            try:
                same = os.fstat(fh.fileno()).st_ino == os.stat(path).st_ino
            except FileNotFoundError:
                same = False
            if same:
                fh.write(text)
                fh.flush()
                common.unlock_file(fh)
                return
            common.unlock_file(fh)
    raise OSError(f"{path} kept moving while it was written")


def parked(name: str) -> list[dict]:
    try:
        text = parked_path(name).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("schema") == PARKED_SCHEMA:
            out.append(r)
    return out


def parked_line(name: str | None) -> str | None:
    """One facts line when messages to this session's NAME were parked, or None."""
    if not name:
        return None
    rows = parked(name)
    if not rows:
        return None
    senders = sorted({r.get("from") or "?" for r in rows})
    return (f"- {len(rows)} message(s) to {name} were refused by the resume gate and parked "
            f"(from {', '.join(senders)}; newest {rows[-1].get('at')}). "
            f"Read them in {parked_path(name)}.")


def all_parked() -> dict[str, int]:
    out = {}
    try:
        files = sorted(config.state().glob("parked-*.jsonl"))
    except OSError:
        return out
    for f in files:
        name = f.name[len("parked-"):-len(".jsonl")]
        if name.endswith(".delivered"):                   # already shown to its recipient
            continue
        n = len(parked(name))
        if n:
            out[name] = n
    return out


# ------------------------------------------------------------------------------ the launch door ----

# A Terminal window that RUNS a session: `open -a Terminal <script>.sh|.command`, or an osascript
# `do script` whose text starts claude or a launcher script. Opening Terminal on a note is not one.
_TERMINAL_LAUNCH = re.compile(
    r"\bopen\s+(?:-\S+\s+)*-a\s+['\"]?Terminal['\"]?(?:\s+-\S+)*\s+\S*\.(?:sh|command|zsh)\b|"
    r"\bosascript\b[^\n]*\bdo script\b[^\n]*(?:\bclaude\b|\.sh\b|\.command\b)", re.I)
_MUX_LAUNCH = re.compile(r"\bscreen\s+(?:-\S*\s+)*-d?m\S*|\bscreen\s+-dmS\b|"
                         r"\btmux\s+new(?:-session)?\b[^\n]*\s-d\b")
PREFILTER = ("Terminal", "osascript", "screen", "tmux")


def launch_door_mode() -> str:
    v = str(limits.get("session_launch_door", "warn") or "off").strip().lower()
    return v if v in ("off", "warn", "deny") else "warn"


def launch_outside_rule(cmd: str) -> str | None:
    """Why this command starts a session outside the launch rule, or None (TERMOVERLOAD-1 §3.6).

    Two shapes: a Terminal window per session (`open -a Terminal`, an osascript `do script`) — the
    shape that hung Terminal at ~90 windows and replayed its queue as duplicate builders; and a
    detached screen/tmux launch that does not scrub `CLAUDE_CODE_CHILD_SESSION` — the shape that
    writes no transcript. `sessions.py launch` does both right, and is never matched here."""
    if not any(k in cmd for k in PREFILTER) or "sessions.py" in cmd:
        return None
    how = "python3 gedaechtnis/tools/sessions.py launch --name <n> --cwd <dir> [--row <q>] <launcher>"
    if _TERMINAL_LAUNCH.search(cmd):
        return ("This opens a Terminal window, and one window per session is what hung Terminal at "
                "~90 windows and replayed its queued launches as duplicate builders (2026-09-26). "
                f"Launch detached instead: {how}.")
    if _MUX_LAUNCH.search(cmd) and "CLAUDE_CODE_CHILD_SESSION" not in cmd:
        return ("This starts a detached screen/tmux session from inside a Claude session without "
                "scrubbing CLAUDE_CODE_CHILD_SESSION, so a claude started in it writes NO transcript "
                "and NO registry record (the context warn, the resume gate and `claude --resume` are "
                f"then blind to it). Use {how} — it scrubs the environment and records the pid.")
    return None


def launcher_lacks_inbound_accept(launcher: str) -> bool:
    try:
        return not INBOUND_ACCEPT.search(Path(launcher).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
