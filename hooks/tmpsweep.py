#!/usr/bin/env python3
"""tmpsweep.py — the session temp folder: scratch folders of sessions that are gone, and the
processes they left running (MACFIX-1).

Claude Code gives every session a folder `<root>/<project slug>/<session id>/` (`scratchpad/`,
`tasks/`) under `/private/tmp/claude-<uid>`. Nothing empties it before a reboot: 225 GB in 4.9 days
on 2026-10-06, and three headless Chrome processes three days old whose sessions had ended.

Two chores, one pass, in this order (a leftover process keeps its session's folder "in use"):

  LEFTOVERS  a browser (by its executable) whose command line carries `--user-data-dir=<a session's folder>/…`, whose
             session is not alive, with no live session among its ancestors, started more than
             `leftover_procs_min_age_s` ago, is sent SIGTERM (never SIGKILL: Chrome then removes
             its own code-sign copy). Its start time and command line are read again right before.
  SCRATCH    a session folder whose session is not alive is
               · STRIPPED after `tmp_sweep_strip_days`: files of `tmp_sweep_big_file_mb` or more,
                 copied repositories (a directory holding `.git`), browser profiles (a
                 directory holding `Local State` and `Default/`) and dependency folders are
                 removed and listed in `<folder>/SWEPT.txt`; notes, logs and scripts stay;
               · REMOVED whole after `tmp_sweep_days`.
             Age is the time since anything last happened in the folder or in the session's
             transcript. Measured 2026-10-06: the strip at 2 days returns 76 % of the space.

What is never touched: a folder that is not `<root>/-<slug>/<uuid>` (allow-list); a folder whose
session is alive, or that a live process stands in or names on its command line; any folder when
the list of live sessions (one unreadable session file is enough), the process list or the
processes' working directories could not be read; a directory on another volume inside a folder; a path with a symbolic link among its components. Liveness is read again for each folder
right before it is acted on. A symbolic link inside a folder is unlinked, never followed.

Proposes until `tmp_sweep_apply` / `leftover_procs_apply` is on (hooks/destructive.py).
`python3 tmpsweep.py run [--apply] [--all]` runs one pass by hand; the Stop hook starts the same
pass detached, at most once per `tmp_sweep_every_s` machine-wide.
"""
from __future__ import annotations
import glob, json, os, re, signal, subprocess, sys, time
from pathlib import Path

import common
import config
import destructive
import limits
import procs

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LEDGER = "SWEPT.txt"
HEAVY_DIRS = frozenset(("node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache"))
KEPT_LIVE = "its session is alive"
KEPT_PROCESS = "a live process stands in it or names it"
KEPT_UNREAD = "the live sessions or the process list could not be read"
KEPT_YOUNG = "younger than the limit"
KEPT_LINK = "a symbolic link is among its path components"
KEPT_BOUND = "this pass reached tmp_sweep_max_per_pass; left for the next pass"


def _num(key: str, default: float) -> float:
    try:
        v = float(limits.get(key, default))
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


def root() -> Path:
    v = limits.get("tmp_sweep_root", "")
    if isinstance(v, str) and v.strip():
        return Path(v.strip())
    for base in ("/private/tmp", "/tmp"):
        p = Path(base) / f"claude-{os.getuid()}"
        if p.is_dir():
            return p
    return Path("/private/tmp") / f"claude-{os.getuid()}"


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def link_free(p: Path) -> bool:
    """No component of `p` is a symbolic link (so `/tmp/…`, a link on macOS, is refused)."""
    cur = Path(p.anchor)
    for part in p.parts[1:]:
        cur = cur / part
        try:
            if os.path.islink(cur):
                return False
        except OSError:
            return False
    return True


def units(r: Path | None = None) -> list[tuple[Path, str]]:
    """(folder, session id) for every `<root>/-<slug>/<uuid>` directory. Nothing else is a unit."""
    r = r or root()
    out = []
    try:
        slugs = sorted(os.listdir(r))
    except OSError:
        return out
    for slug in slugs:
        sp = r / slug
        if not slug.startswith("-") or os.path.islink(sp) or not sp.is_dir():
            continue
        try:
            kids = sorted(os.listdir(sp))
        except OSError:
            continue
        for k in kids:
            kp = sp / k
            if UUID.match(k) and not os.path.islink(kp) and kp.is_dir():
                out.append((kp, k))
    return out


# ---- liveness ------------------------------------------------------------------------------------

class Snapshot:
    """What is alive right now: session ids, session pids, every process's command line and
    working directory. `ok` is False when any of the three could not be read; then nothing is acted on."""

    def __init__(self) -> None:
        self.sids: set[str] = set()
        self.session_pids: set[int] = set()
        self.cmds: dict[int, tuple[int, str, str]] = {}      # pid -> (ppid, lstart, command)
        self.cwds: list[tuple[str, int]] = []
        self.comm: dict[int, str] = {}                       # pid -> its executable (`ps -o comm=`)
        self.ok = self._sessions() and self._cwds() and self._ps()   # ps last: it is what is acted on

    def _sessions(self) -> bool:
        seen = False
        for f in glob.glob(str(claude_home() / "sessions" / "*.json")):
            try:
                d = json.loads(Path(f).read_text(encoding="utf-8"))
                pid, sid = int(d.get("pid")), d.get("sessionId")
            except FileNotFoundError:
                continue                                      # it ended between the listing and the read
            except (OSError, ValueError, TypeError, AttributeError):
                common.log("tmpsweep", f"unread\tsession file {f} could not be read: nothing is touched this pass")
                return False                                  # a session nobody can name may be alive
            if procs.pid_alive(pid):
                if not isinstance(sid, str) or not UUID.match(sid):
                    return False                              # alive, and its folder cannot be told
                seen = True
                self.session_pids.add(pid)
                self.sids.add(sid)
        for f in glob.glob(str(config.state() / "session-start-*.json")):
            try:
                d = json.loads(Path(f).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict):
                continue
            pids = [d.get("pid")] + [c.get("pid") for c in (d.get("claims") or []) if isinstance(c, dict)]
            if any(isinstance(p, int) and procs.pid_alive(p) for p in pids):
                self.sids.add(Path(f).name[len("session-start-"):-len(".json")])
        return seen                      # this pass runs under a session, so none alive = unreadable

    def _ps(self) -> bool:
        try:
            env = {**os.environ, "LC_ALL": "C"}
            c = subprocess.run(["ps", "-axwwo", "pid=,comm="], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=20, env=env)
            p = subprocess.run(["ps", "-axwwo", "pid=,ppid=,lstart=,command="], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=20, env=env)
        except (subprocess.TimeoutExpired, OSError):
            return False
        if c.returncode != 0 or p.returncode != 0:
            return False
        for line in (c.stdout or "").splitlines():
            bits = line.strip().split(None, 1)
            if len(bits) == 2 and bits[0].isdigit():
                self.comm[int(bits[0])] = bits[1]
        for line in (p.stdout or "").splitlines():
            m = re.match(r"\s*(\d+)\s+(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+[\d:]+\s+\d{4})\s+(.*)$", line)
            if m:
                self.cmds[int(m.group(1))] = (int(m.group(2)), m.group(3), m.group(4))
        return bool(self.cmds)

    def _cwds(self) -> bool:
        import wtsweep                                        # noqa: PLC0415 — one reading of lsof
        c = wtsweep.process_cwds()
        self.cwds = c or []
        return c is not None

    def why_alive(self, path: Path, sid: str) -> str | None:
        if not self.ok:
            return KEPT_UNREAD
        if sid in self.sids:
            return KEPT_LIVE
        base = str(path)
        for _ppid, _start, cmd in self.cmds.values():
            if sid in cmd or base + "/" in cmd or cmd.endswith(base):
                return KEPT_PROCESS
        for cwd, _pid in self.cwds:
            if cwd == base or cwd.startswith(base + "/"):
                return KEPT_PROCESS
        return None

    def under_live_session(self, pid: int) -> bool:
        """Whether a live session is among `pid`'s ancestors."""
        for _ in range(40):
            if pid in self.session_pids:
                return True
            nxt = self.cmds.get(pid, (0, "", ""))[0]
            if nxt <= 1 or nxt == pid:
                return False
            pid = nxt
        return True                                           # a chain this deep: cannot tell = keep


# ---- age -----------------------------------------------------------------------------------------

def last_activity(path: Path, sid: str) -> float:
    """The newest of: the session's transcript, the folder and everything in it, at every depth
    (a session resumed under another id may still write deep into this one). Once
    the folder has a ledger, the ledger's recorded time stands in for everything not newer than
    the ledger itself (a strip changes directory times; that is not the session's activity)."""
    floor, recorded = 0.0, None
    led = path / LEDGER
    try:
        first = led.read_text(encoding="utf-8").split("\n", 1)[0]
        recorded = float(first.split("last-activity:", 1)[1])
        floor = os.lstat(led).st_mtime + 60
    except (OSError, ValueError, IndexError):
        recorded = None
    newest = recorded or 0.0

    def see(p: str) -> None:
        nonlocal newest
        try:
            m = os.lstat(p).st_mtime
        except OSError:
            return
        if recorded is None or m > floor:
            newest = max(newest, m)

    see(str(path))
    dev = _dev(str(path))
    for dp, dn, fn in os.walk(path, followlinks=False):
        dn[:] = [d for d in dn if _own(os.path.join(dp, d), dev)]
        for k in dn + fn:
            if not (dp == str(path) and k == LEDGER):
                see(os.path.join(dp, k))
    for f in glob.glob(str(claude_home() / "projects" / "*" / f"{sid}.jsonl")):
        try:
            newest = max(newest, os.lstat(f).st_mtime)
        except OSError:
            pass
    return newest


# ---- removal -------------------------------------------------------------------------------------

def _blocks(st) -> int:
    return getattr(st, "st_blocks", 0) * 512


def _dev(p: str) -> int | None:
    try:
        return os.lstat(p).st_dev
    except OSError:
        return None


def _own(sub: str, dev: int | None) -> bool:
    """A real directory on the same volume as its session folder: the only kind that is entered.
    A link is not, and neither is a mount point (a disk image, a network volume)."""
    try:
        st = os.lstat(sub)
    except OSError:
        return False
    return dev is not None and st.st_dev == dev and not os.path.islink(sub) and not os.path.ismount(sub)


def _unlink(fp: str) -> int:
    try:
        size = _blocks(os.lstat(fp)); os.unlink(fp)
    except OSError:
        return 0
    return size


def remove_tree(p: str) -> int:
    """Remove `p` and everything in it; return the bytes of what was really removed. A link is
    unlinked, never followed; a directory on another volume is left where it is, with its parents."""
    if os.path.islink(p) or not os.path.isdir(p):
        return _unlink(p)
    freed, dirs, dev = 0, [], _dev(p)
    try:
        os.chmod(p, 0o700)                                    # a read-only item is still ours to empty
    except OSError:
        pass
    for dp, dn, fn in os.walk(p, topdown=True, followlinks=False):
        for f in fn:
            freed += _unlink(os.path.join(dp, f))
        keep = []
        for d in dn:
            sub = os.path.join(dp, d)
            if os.path.islink(sub):
                _unlink(sub)
            elif _own(sub, dev):
                try:
                    os.chmod(sub, 0o700)                      # a read-only directory is still ours to empty
                except OSError:
                    pass
                keep.append(d); dirs.append(sub)
        dn[:] = keep
    for sub in dirs[::-1] + [p]:                              # deepest first, the folder itself last
        try:
            os.rmdir(sub)
        except OSError:
            pass
    return freed


def tree_bytes(p: str) -> int:
    t, dev = 0, _dev(p)
    for dp, dn, fn in os.walk(p, followlinks=False):
        dn[:] = [d for d in dn if _own(os.path.join(dp, d), dev)]
        for f in fn:
            try:
                t += _blocks(os.lstat(os.path.join(dp, f)))
            except OSError:
                pass
    return t


def _head_of(repo: str) -> str:
    try:
        return Path(repo, ".git", "HEAD").read_text(encoding="utf-8").strip()[:80]
    except OSError:
        return "-"


def heavy(path: Path, big: int) -> list[tuple[str, str, int, str]]:
    """(kind, path, bytes, note) for each heavy thing in the folder: `repo`, `profile`, `deps`, `file`."""
    out, dev = [], _dev(str(path))
    top = (str(path), str(path / "scratchpad"), str(path / "tasks"))   # the session's own folders: never one item
    for dp, dn, fn in os.walk(path, followlinks=False):
        dn[:] = [d for d in dn if os.path.islink(os.path.join(dp, d)) or _own(os.path.join(dp, d), dev)]
        if dp not in top and (".git" in dn or ".git" in fn):
            out.append(("repo", dp, tree_bytes(dp), _head_of(dp)))
            dn[:] = []
            continue
        if dp not in top and "Local State" in fn and "Default" in dn:      # a Chromium user-data-dir
            out.append(("profile", dp, tree_bytes(dp), ""))
            dn[:] = []
            continue
        for d in list(dn):
            sub = os.path.join(dp, d)
            if os.path.islink(sub):
                dn.remove(d)
            elif d in HEAVY_DIRS and (d not in ("venv", ".venv") or os.path.isfile(os.path.join(sub, "pyvenv.cfg"))):
                out.append(("deps", sub, tree_bytes(sub), "")); dn.remove(d)
        for f in fn:
            fp = os.path.join(dp, f)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if not os.path.islink(fp) and os.path.isfile(fp) and st.st_size >= big and f != LEDGER:
                out.append(("file", fp, _blocks(st), ""))
    return out


def strip(path: Path, items: list, activity: float) -> int:
    freed, lines = 0, []
    for kind, p, _size, note in items:
        got = remove_tree(p)
        freed += got
        lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{kind}\t{os.path.relpath(p, path)}\t{got}\t{note}")
    led = path / LEDGER
    try:
        try:
            body = led.read_text(encoding="utf-8").split("\n", 1)[1] if led.exists() and not led.is_symlink() else ""
        except (OSError, IndexError):
            body = ""
        body = body or ("# removed by the temp sweep (gedaechtnis tmpsweep.py): time, kind, path, bytes, note. "
                        "Files this large and copied repositories go after tmp_sweep_strip_days; the folder "
                        "goes after tmp_sweep_days.\n")
        if led.is_symlink():
            os.unlink(led)
        # the first line is the activity THIS strip was judged on: work between two strips counts
        led.write_text(f"last-activity: {activity}\n{body}" + "\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return freed


# ---- the two chores ------------------------------------------------------------------------------

_UDD = re.compile(r"--user-data-dir=(\S+)")
# The executable itself (`ps -o comm=`), never a word on a command line: `ssh host chrome …`,
# a Python driver or an editor that merely NAME a profile folder are not browsers.
BROWSER_COMM = re.compile(r"^(Google Chrome( for Testing)?( Helper.*)?|Chromium( Helper.*)?|chrome|"
                          r"chrome-headless-shell|headless_shell|chrome_crashpad_handler)$")


def is_browser_comm(comm: str | None) -> bool:
    return bool(comm) and comm.startswith("/") and bool(BROWSER_COMM.match(os.path.basename(comm)))


def leftovers(snap: Snapshot, r: Path, now: float) -> list[dict]:
    """Processes started with a dead session's folder as their `--user-data-dir`."""
    out, min_age = [], _num("leftover_procs_min_age_s", 86400)
    base = str(r) + "/"
    for pid, (ppid, start, cmd) in sorted(snap.cmds.items()):
        m = _UDD.search(cmd)
        if not m or not is_browser_comm(snap.comm.get(pid)):
            continue
        udd = m.group(1)
        if udd.startswith("/tmp/") and base.startswith("/private/tmp/"):
            udd = "/private" + udd                            # the link spelling of the same folder
        if not udd.startswith(base):
            continue
        parts = udd[len(base):].split("/")
        if len(parts) < 2 or not parts[0].startswith("-") or not UUID.match(parts[1]):
            continue
        try:
            age = now - time.mktime(time.strptime(start, "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            continue
        if parts[1] in snap.sids or age < min_age or snap.under_live_session(pid):
            continue
        out.append({"pid": pid, "ppid": ppid, "start": start, "cmd": cmd, "sid": parts[1],
                    "path": f"pid {pid} ({Path(cmd.split(' --')[0]).name}, session {parts[1][:8]})"})
    ids = {i["pid"] for i in out}
    return sorted(out, key=lambda i: i["ppid"] in ids)       # a browser before its own helpers


def _term(pid: int) -> bool:
    """SIGTERM to one pid read from the process list a moment ago. Never SIGKILL, never a group."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def end_leftovers(r: Path, apply: bool) -> dict:
    res = {"ended": [], "proposed": [], "kept": []}
    snap = Snapshot()
    if not snap.ok:
        return res
    for it in leftovers(snap, r, time.time()):
        if not apply:
            res["proposed"].append(it); continue
        now = Snapshot()                                      # read again at the moment of acting
        cur = now.cmds.get(it["pid"])
        if cur is None:
            continue                                          # gone with its parent
        if (not now.ok or cur[1] != it["start"] or cur[2] != it["cmd"] or it["sid"] in now.sids
                or not is_browser_comm(now.comm.get(it["pid"])) or now.under_live_session(it["pid"])):
            res["kept"].append(it); continue
        if not _term(it["pid"]):
            res["kept"].append(it); continue
        res["ended"].append(it)
        common.log("leftovers", f"ended\tpid={it['pid']}\tstarted={it['start']}\tsession={it['sid']}\t{it['cmd'][:300]}")
        time.sleep(0.2)
    return res


def classify(path: Path, sid: str, snap: Snapshot, now: float) -> tuple[str, str, float]:
    """(action, reason, last activity): action is `remove`, `strip` or `keep`."""
    if not link_free(path) or os.path.realpath(path) != str(path):
        return "keep", KEPT_LINK, 0.0
    why = snap.why_alive(path, sid)
    if why:
        return "keep", why, 0.0
    act = last_activity(path, sid)
    age = (now - act) / 86400.0
    if age >= _num("tmp_sweep_days", 7):
        return "remove", "", act
    if age >= _num("tmp_sweep_strip_days", 2):
        return "strip", "", act
    return "keep", KEPT_YOUNG, act


def sweep(apply: bool, r: Path | None = None, everything: bool = False) -> dict:
    r = r or root()
    res = {"removed": [], "stripped": [], "proposed": [], "kept": [], "freed": 0}
    if not link_free(r) or not r.is_dir():
        return res
    big = int(_num("tmp_sweep_big_file_mb", 100) * 1048576)
    n_max = int(_num("tmp_sweep_max_per_pass", 60))
    snap, done = Snapshot(), 0
    for path, sid in units(r):
        action, why, act = classify(path, sid, snap, time.time())
        if action == "keep":
            res["kept"].append((str(path), why)); continue
        items = heavy(path, big) if action == "strip" else []
        if action == "strip" and not items:
            continue                                          # stripped before, or nothing heavy in it
        if not apply:
            size = tree_bytes(str(path)) if action == "remove" else sum(i[2] for i in items)
            res["proposed"].append({"path": str(path), "action": action, "bytes": size}); continue
        if not everything and done >= n_max:
            res["kept"].append((str(path), KEPT_BOUND)); continue
        again, why, act2 = classify(path, sid, Snapshot(), time.time())   # at the moment of acting
        if again != action or act2 != act:
            res["kept"].append((str(path), why or "it changed after it was checked")); continue
        freed = remove_tree(str(path)) if action == "remove" else strip(path, items, act)
        done += 1
        res["freed"] += freed
        if action == "remove" and os.path.lexists(path):
            action = "remove-partial"                         # something in it could not be removed
            res["kept"].append((str(path), "part of it could not be removed"))
        else:
            res["removed" if action == "remove" else "stripped"].append(str(path))
        common.log("tmpsweep", f"{action}\t{path}\tbytes={freed}\tidle_days={(time.time() - act) / 86400:.1f}"
                               + (f"\titems={len(items)}" if items else ""))
    return res


# ---- wiring --------------------------------------------------------------------------------------

def enabled() -> bool:
    return bool(limits.get("tmp_sweep", False)) or bool(limits.get("leftover_procs", False))


def _stamp() -> Path:
    return Path(config.state()) / "tmpsweep.stamp"


def due(now: float | None = None) -> bool:
    """True at most once per `tmp_sweep_every_s` on this machine; taking it writes the stamp."""
    now = now or time.time()
    try:
        _stamp().parent.mkdir(parents=True, exist_ok=True)
        with open(str(_stamp()) + ".lock", "w") as lk:        # two Stops in one instant: one takes it
            common.lock_file(lk)
            try:
                if 0 <= now - float(_stamp().read_text(encoding="utf-8")) < _num("tmp_sweep_every_s", 21600):
                    return False
            except (OSError, ValueError):
                pass
            _stamp().write_text(str(now), encoding="utf-8")
    except OSError:
        return False
    return True


def run_pass(apply_scratch: bool, apply_procs: bool, everything: bool = False) -> dict:
    r = root()
    out = {"leftovers": {"ended": [], "proposed": [], "kept": []},
           "scratch": {"removed": [], "stripped": [], "proposed": [], "kept": [], "freed": 0}}
    if os.name != "posix" or not common.pid_probe_safe():
        return out
    if limits.get("leftover_procs", False):
        out["leftovers"] = end_leftovers(r, apply_procs)
        if out["leftovers"]["ended"]:
            time.sleep(3)                                     # let them exit before folders are judged
    if limits.get("tmp_sweep", False):
        out["scratch"] = sweep(apply_scratch, r, everything)
    lo, sc = out["leftovers"], out["scratch"]
    destructive.record_proposal("leftover-procs", [{"path": i["path"]} for i in lo["proposed"]])
    destructive.record_proposal("scratch-sweep", [{"path": f"{i['path']} ({i['action']}, {i['bytes'] / 2**30:.2f} GB)"}
                                                  for i in sc["proposed"]])
    common.log("tmpsweep", f"pass\tremoved={len(sc['removed'])}\tstripped={len(sc['stripped'])}\t"
                           f"proposed={len(sc['proposed'])}\tkept={len(sc['kept'])}\tfreed_bytes={sc['freed']}\t"
                           f"procs_ended={len(lo['ended'])}\tprocs_proposed={len(lo['proposed'])}")
    return out


def facts_line() -> str | None:
    """The SessionStart lines of the two chores while they only propose; None once they act."""
    try:
        doc = json.loads(destructive.proposals_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    lines = []
    for chore, verb in (("scratch-sweep", "stripped or removed"), ("leftover-procs", "ended")):
        if not destructive.applies(chore):
            line = destructive.proposal_line(chore, (doc.get(chore) or {}).get("items") or [], verb)
            if line:
                lines.append(line)
    return "\n".join(lines) or None


def start_detached() -> None:
    """The Stop hook's part: one pass in its own process, so a Stop never waits on a removal."""
    subprocess.Popen([config.python(), "-B", str(Path(__file__).resolve()), "run", "--configured"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd="/")


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "run":
        print(__doc__)
        return 2
    configured = "--configured" in argv
    apply_s = destructive.applies("scratch-sweep") if configured else "--apply" in argv
    apply_p = destructive.applies("leftover-procs") if configured else "--apply" in argv
    out = run_pass(apply_s, apply_p, everything="--all" in argv)
    if not configured:
        sc, lo = out["scratch"], out["leftovers"]
        print(json.dumps({"root": str(root()), "removed": len(sc["removed"]), "stripped": len(sc["stripped"]),
                          "proposed": len(sc["proposed"]),
                          "proposed_gb": round(sum(i["bytes"] for i in sc["proposed"]) / 2**30, 1),
                          "kept": len(sc["kept"]), "freed_gb": round(sc["freed"] / 2**30, 2),
                          "procs_ended": [i["pid"] for i in lo["ended"]],
                          "procs_proposed": [i["pid"] for i in lo["proposed"]],
                          "procs_kept": [i["pid"] for i in lo["kept"]]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
