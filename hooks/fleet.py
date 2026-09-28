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
  * `sweep()` — gathers the facts and, only when `session_close_apply` is on, closes with `kill -9` of
    the recorded pid after re-reading every rule in the same pass. Off, it proposes.
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


def read_screen(mux: str | None, name: str) -> str | None:
    """The text on a session's screen, or None when it cannot be read. A READ: no keystroke."""
    if mux is None:
        return None
    try:
        if mux == "tmux":
            p = subprocess.run(["tmux", "capture-pane", "-p", "-t", name], capture_output=True,
                               text=True, stdin=subprocess.DEVNULL, timeout=5)
            return p.stdout if p.returncode == 0 else None
        fd, tmp = tempfile.mkstemp(prefix="gd-screen-", suffix=".txt")
        os.close(fd)
        try:
            p = subprocess.run(["screen", "-S", name, "-p", "0", "-X", "hardcopy", "-h", tmp],
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5)
            if p.returncode != 0:
                return None
            data = Path(tmp).read_bytes().decode("utf-8", "replace")
            return data if data.strip() else None
        finally:
            try:
                os.unlink(tmp)
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
        return worktree
    m = re.match(r"^(.*/\.claude/worktrees/[^/]+)(?:/|$)", _real(cwd) or "")
    return m.group(1) if m else None


def _toplevel(d: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def session_unsaved(cwd: str | None, worktree: str | None, writes) -> list[str] | None:
    """Uncommitted work that is THIS SESSION'S (SESSCLOSE-2), [] when none, None when git cannot say.

    The first sweep counted every dirty path of the checkout the session ran in; a shared checkout
    carries ~100 standing dirty paths that belong to nobody running (read 2026-09-27), so a finished
    builder there could never be closed. Now: (1) the session's own worktree — declared, or its cwd
    under `.claude/worktrees/` — counts whole, whatever the age of its dirt; (2) every file its
    transcript WROTE (Write/Edit/MultiEdit/NotebookEdit) that sits in a git repo and is still
    uncommitted there counts. A file outside any repo is saved on disk and does not count.
    Residual, stated: a file written only by a shell command (`>`, `sed -i`) is not in the transcript
    as a write; a builder's own worktree catches that case, a shared checkout does not."""
    out: list[str] = []
    own = own_worktree(cwd, worktree)
    if own:
        dirt = unsaved_work(None, own, None)
        if dirt is None:
            return None
        out.extend(dirt)
    by_repo: dict[str, list[str]] = {}
    for fp in sorted(writes or ()):
        full = _real(fp if os.path.isabs(os.path.expanduser(fp)) else os.path.join(cwd or "/", fp))
        if not full or (own and full.startswith(_real(own) + os.sep)):
            continue
        d = full if os.path.isdir(full) else os.path.dirname(full)
        while d and not os.path.isdir(d):
            d = os.path.dirname(d)
        top = _toplevel(d) if d else None
        if top:
            by_repo.setdefault(top, []).append(os.path.relpath(full, _real(top)))
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
                out.append(os.path.join(top, e[3:]))
                if e[0] in "RC":
                    i += 1
            i += 1
    return out


# ------------------------------------------------------------------------------ the decision ----

def idle_minutes() -> float:
    try:
        v = float(limits.get("session_close_idle_minutes", 180) or 180)
    except (TypeError, ValueError):
        v = 180.0
    return v if v > 0 else 180.0


def exempt_patterns() -> list[str]:
    try:
        v = limits.get("session_close_exempt", [])
    except KeyError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def classify(f: dict, now: float, idle_minutes: float) -> list[str]:
    """The close rules that do NOT hold, as KEEP reasons. [] means closeable. Pure.

    `f` keys: alive, is_self, handoff (a ledger-named handoff file exists), end (the transcript's
    final turn end from `closerules.scan`, None when the session is mid-turn), scanned (a
    transcript was found), status (registry status or None), screen (text or None), attached,
    unsaved (list or None), outside (not in the launch ledger), mux (None = no screen/tmux),
    exempt (a `session_close_exempt` pattern matched its name)."""
    keep = []
    if f.get("is_self"):
        keep.append("launched: it is the session running this pass")
    if not f.get("alive"):
        keep.append("launched: its recorded pid is gone or now belongs to another process")
    if f.get("exempt"):
        keep.append("exempt: its name matches session_close_exempt")
    if not f.get("scanned"):
        keep.append("transcript: no transcript to read its last turn from")
    else:
        keep.extend(closerules.verdict(f.get("end"), now, idle_minutes, handoff=bool(f.get("handoff"))))
    status = f.get("status")
    if status is not None and status != "idle":
        keep.append(f"idle: the session registry says {status!r}")
    if f.get("outside") and not f.get("mux"):
        keep.append("window: it runs in a terminal window, not a screen — closing it is the owner's")
    screen = f.get("screen")
    if screen is None:
        keep.append("no-prompt: its screen could not be read")
    elif PROMPT_TEXT.search(screen):
        keep.append("no-prompt: a permission prompt is on its screen")
    if f.get("attached"):
        keep.append("attached: someone has its screen open")
    unsaved = f.get("unsaved")
    if unsaved is None:
        keep.append("no-unsaved-work: git could not say whether its work is committed")
    elif unsaved:
        keep.append(f"no-unsaved-work: {len(unsaved)} uncommitted path(s) it wrote, e.g. {unsaved[0]}")
    return keep


def handoff_written(pattern: str | None, since: float | None) -> bool:
    if not pattern:
        return False
    import glob                                               # noqa: PLC0415
    for p in glob.glob(os.path.expanduser(pattern)):
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
    tp = transcript_for(pid, r.get("name"), at)
    try:
        big = tp is not None and tp.stat().st_size > SCAN_MAX_BYTES
    except OSError:
        big = True
    sc = closerules.scan(tp) if tp and not big else None
    # A write whose timestamp did not parse (0) counts: unknown age is never "before the launch".
    since_launch = {k: v for k, v in (sc["ends"][-1]["writes"] if sc and sc["ends"] else {}).items()
                    if not at or not v or v >= at - 60}
    return {
        "is_self": bool(me) and pid == me,
        "alive": same_process(pid, r.get("pid_start")),
        "exempt": any(fnmatch.fnmatchcase(r["name"], pat) for pat in exempt_patterns()),
        "handoff": handoff_written(r.get("handoff"), at),
        "status": registry_status(pid),
        "scanned": sc is not None,
        "end": sc["last"] if sc else None,
        "screen": read_screen(m, session) if m else None,
        "attached": (screen_attached(session) if m == "screen"
                     else tmux_attached(session) if m == "tmux" else False),
        "unsaved": session_unsaved(r.get("cwd"), r.get("worktree"), since_launch),
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
            pid = int(doc.get("pid"))
        except (OSError, ValueError, TypeError):
            continue
        if pid in known or doc.get("kind") not in (None, "interactive"):
            continue
        sty = screen_of(pid)
        out.append({"name": doc.get("name") or f"(unnamed {str(doc.get('sessionId'))[:8]})",
                    "pid": pid, "pid_start": registry_start_local(doc.get("procStart")),
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


def sweep(apply: bool, now: float | None = None, gather_fn=gather, kill=None,
          budget_s: float = SWEEP_BUDGET_S, rows: list[dict] | None = None) -> list[dict]:
    """One pass over the live sessions — the launch ledger's, and (SESSCLOSE-2) every other live
    interactive session the CLI has a record of. Returns one item per live session with its verdict.

    `apply` False: nothing is killed; closeable sessions are returned as proposals. True: each
    closeable session's facts are GATHERED AGAIN and re-classified immediately before it is closed —
    a session that started a turn in between is kept. Every proposal and every close is logged with
    the line that reopens it (`claude -r <name>`), so a wrong close costs one command."""
    clock = (lambda: now) if now is not None else time.time
    now = clock()
    mux = multiplexer()
    me = procs.claude_pid()
    idle = idle_minutes()
    kill = kill or (lambda pid: os.kill(int(pid), 9))
    items = []
    started = time.time()
    cands = current_launches() if rows is None else rows
    if rows is None:
        cands = cands + outside_sessions(cands)
    for r in cands:
        if time.time() - started > budget_s:
            close_log(f"deferred\t{r['name']}\tpass budget {budget_s:.0f}s spent")
            continue
        f = gather_fn(r, mux, me)
        if not f.get("alive") and not f.get("is_self"):
            continue                                          # gone: nothing to close or report
        keep = classify(f, now, idle)
        item = {"name": r["name"], "pid": r.get("pid"), "keep": keep, "closed": False,
                "outside": bool(r.get("outside")), "resume": resume_line(r),
                "attach": attach_line(r.get("mux") or mux, r.get("mux_session") or r["name"])}
        if not keep and apply:
            again = classify(gather_fn(r, mux, me), clock(), idle)
            if again:
                item["keep"] = ["changed during the pass: " + again[0]]
            else:
                how = close_one(r, r.get("mux") or mux, kill)
                if how:
                    item["closed"] = True
                    close_log(f"closed\t{r['name']}\tpid {r['pid']}\t{how}\treopen: {item['resume']}")
                else:
                    item["keep"] = ["the close did not end the recorded pid"]
        elif not keep:
            close_log(f"proposed\t{r['name']}\tpid {r['pid']}\t(session_close_apply is off)"
                      f"\treopen: {item['resume']}")
        items.append(item)
    return items


def close_one(r: dict, mux: str | None, kill, quit_fn=None, alive_fn=None,
              wait_s: float = CLOSE_WAIT_S) -> str | None:
    """End one launched session; returns how it ended, or None if the recorded pid survived.

    First `screen -X quit` / `tmux kill-session`: the claude in it ends CLEANLY on the hang-up
    (measured by the seat 2026-09-26 — SIGTERM does nothing, the quit does). Only if the recorded
    pid, with its recorded start time, is still there after `wait_s` does `kill -9` follow — of that
    pid, never of anything found by search."""
    quit_fn = quit_fn or mux_quit
    alive_fn = alive_fn or (lambda: same_process(r.get("pid"), r.get("pid_start")))
    quit_fn(r.get("mux") or mux, r.get("mux_session") or r["name"])
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not alive_fn():
            return "screen quit"
        time.sleep(0.25)
    if not alive_fn():
        return "screen quit"
    try:
        kill(r["pid"])
    except OSError:
        return None
    return "kill -9 after screen quit"


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


def attach_line(mux: str | None, name: str) -> str:
    if mux == "tmux":
        return f"tmux attach -t {name}"
    if mux == "screen":
        return f"screen -r {name}"
    return "(no multiplexer)"


def due(now: float | None = None) -> bool:
    """Machine-wide rate limit for the Stop chore: True at most once per `session_close_every_s`."""
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
            st.write_text(str(now))
            return True
    except OSError:
        return False


# -------------------------------------------------------------------------- parked messages ----

def parked_path(name: str) -> Path:
    return config.state() / f"parked-{common.safe_sid(name)}.jsonl"


def park(to: str, sender: str | None, reason: str, text: str) -> Path | None:
    """Keep a message the resume gate refused, where the recipient's successor will be shown it."""
    row = {"schema": PARKED_SCHEMA, "kind": "refused-wake", "to": to, "from": sender or "?",
           "reason": reason, "text": (text or "")[:PARKED_TEXT_CHARS],
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        path = parked_path(to)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except OSError as e:
        common.log("hook-errors", f"fleet\tpark\t{to}\t{e}")
        return None


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
