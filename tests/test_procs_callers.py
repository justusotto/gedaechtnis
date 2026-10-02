"""PROCSHOST-1 — which process each caller of `procs` means, on a faked process tree.

Before: `procs.is_claude` asked "is any argument's basename `claude`", which a Project thread
(`claude.exe --print --sdk-url …`) does not answer, so every thread's walk ran past its own process
and `claude_pid` named the Remote Control HOST. Six call sites then took the host for "this
session": its pid (which never dies) and its `--name` (shared by every thread).

Decision, one per caller, each pinned below with a thread tree (positive) and a plain session or a
bare host (negative):

  session_start  the start record's pid and name   → the session's own process
  idlenotify     the Stop notice's sender name     → the session's own process
  idlenotify     the mail line's reader name       → the session's own process
  fleet          `session_name_of`'s fallback      → the session's own process
  fleet          the close pass's "is this me"     → the session's own process
  stallbrief     "not myself" in the managed list  → the session's own process

Not changed, and why: `suitelock.py` holds the pytest process's own pid (`os.getpid()`), never a
walked one; `wtsweep.py` reads the pids `session_start` records (fixed at the writer);
`resume_gate.py` reads the pid of the CLI's own registry record. No real process is looked at.
"""
from __future__ import annotations
import importlib, json, os, subprocess, sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

HOST = "claude remote-control --name demo host --spawn same-dir --capacity 4"
THREAD = ("/opt/x/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe "
          "--print --sdk-url https://example.invalid/sessions/cse_A --session-id cse_A")
PLAIN = "claude --model m --name s-builder-R1"
PATTERN = r"^(?P<seat>[a-z0-9-]+?)-(?P<lane>builder|audit)-(?P<qid>[A-Za-z0-9-]+)$"

UNDER_THREAD = ({9000: (1, HOST), 9001: (9000, THREAD)}, 9001)
UNDER_PLAIN = ({9100: (1, PLAIN)}, 9100)
UNDER_HOST = ({9000: (1, HOST)}, 9000)


@pytest.fixture
def box(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"session_name_pattern": PATTERN}))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(cfg))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    import config, procs, idlenotify, stallbrief, fleet                   # noqa: PLC0415
    for m in (config, procs, idlenotify, stallbrief, fleet):
        importlib.reload(m)

    def under(tree):
        rows, parent = tree
        me = os.getpid()
        monkeypatch.setattr(procs, "ps_info",
                            lambda pid: (parent, "python3 hook.py") if pid == me else rows.get(pid))

    return {"procs": procs, "idlenotify": idlenotify, "stallbrief": stallbrief, "fleet": fleet,
            "under": under, "tmp": tmp_path}


# ------------------------------------------------------------------ procs itself ----
def test_a_thread_is_its_own_process_and_a_host_is_nobodys(box):
    p = box["procs"]
    box["under"](UNDER_THREAD)
    assert p.claude_pid() == 9001 and p.own_pid() == 9001      # was 9000, the host
    box["under"](UNDER_PLAIN)
    assert p.claude_pid() == 9100 and p.own_pid() == 9100      # a plain session: as before
    box["under"](UNDER_HOST)
    assert p.claude_pid() == 9000 and p.own_pid() is None      # the nearest claude; never "mine"


def test_is_claude_is_the_executable_test(box):
    is_claude = box["procs"].is_claude
    for cmd in ("claude --resume x", "/usr/local/bin/claude", "/bin/sh /tmp/bin/claude a b",
                THREAD, "/opt/x/bin/claude.exe --resume x", HOST):
        assert is_claude(cmd), cmd
    for cmd in ("login -pflq user /bin/bash -c cd /w && exec caffeinate -s claude remote-control",
                "/usr/bin/caffeinate -s claude remote-control --name demo host",
                "/bin/zsh -c claude --model m", "python3 /opt/u/.claude/plugins/x/hook.py",
                "/usr/bin/vim claude.md"):
        assert not is_claude(cmd), cmd


def test_a_wrapper_that_only_names_claude_is_walked_past(box):
    """`caffeinate -s claude remote-control` sits between a host and its `login`; a session
    started through such a wrapper must be found at its own process, not at the wrapper."""
    box["under"](({9200: (1, "/usr/bin/caffeinate -s claude --name s-builder-R1"),
                   9201: (9200, PLAIN)}, 9201))
    assert box["procs"].own_pid() == 9201
    box["under"](({9200: (1, "/usr/bin/caffeinate -s claude --name s-builder-R1")}, 9200))
    assert box["procs"].own_pid() is None and box["procs"].claude_pid() is None


# ------------------------------------------------------------------ the callers ----
def test_the_mail_line_is_read_under_the_sessions_own_name_never_the_hosts(box, monkeypatch):
    idle = box["idlenotify"]
    seen = []
    monkeypatch.setattr(idle, "unread", lambda name, since=None: seen.append(name) or [])
    box["under"](UNDER_THREAD)
    assert idle.facts_line() is None and seen == []            # a thread carries no --name: no mail
    box["under"](UNDER_HOST)
    assert idle.facts_line() is None and seen == []            # and never "demo", the host's
    box["under"](UNDER_PLAIN)
    idle.facts_line()
    assert seen == ["s-builder-R1"]                            # a plain session: its own name


def test_the_stop_notice_names_the_session_never_the_host(box, monkeypatch):
    idle = box["idlenotify"]
    asked = []
    monkeypatch.setattr(idle, "match_name", lambda name: asked.append(name))    # None: stop there
    box["under"](UNDER_THREAD)
    assert idle.notify("S1", None, None) is None
    box["under"](UNDER_PLAIN)
    assert idle.notify("S1", None, None) is None
    assert asked == [None, "s-builder-R1"]                     # was ["demo", …] under a host


def test_the_name_fallback_is_the_sessions_own(box, monkeypatch):
    fleet = box["fleet"]
    monkeypatch.setattr(fleet, "registry_records", lambda: [])
    box["under"](UNDER_THREAD)
    assert fleet.session_name_of("S1") is None                 # was "demo"
    box["under"](UNDER_PLAIN)
    assert fleet.session_name_of("S1") == "s-builder-R1"


def test_the_close_pass_knows_itself_by_its_own_pid(box, monkeypatch):
    fleet = box["fleet"]
    seen = []

    def gather(r, mux, me):
        seen.append(me)
        return {"alive": False, "is_self": False}

    row = {"name": "s-builder-R1", "pid": 9001}
    box["under"](UNDER_THREAD)
    fleet.sweep(False, now=1_800_000_000.0, gather_fn=gather, rows=[row], log_proposals=False)
    box["under"](UNDER_PLAIN)
    fleet.sweep(False, now=1_800_000_000.0, gather_fn=gather, rows=[row], log_proposals=False)
    box["under"](UNDER_HOST)                                   # right under a host: the host itself,
    fleet.sweep(False, now=1_800_000_000.0, gather_fn=gather, rows=[row], log_proposals=False)
    assert seen == [9001, 9100, 9000]                          # so its own row keeps `is_self`
    assert fleet.gather.__code__.co_varnames[:3] == ("r", "mux", "me")     # the seam is the real one


def test_the_stall_block_leaves_out_this_session_not_its_host(box):
    sb = box["stallbrief"]
    root = box["tmp"] / "projects"
    root.mkdir()
    sessions = [{"pid": 9100, "name": "s-builder-R1", "model": "m", "age": 60},
                {"pid": 9000, "name": "s-builder-R2", "model": "m", "age": 60}]
    listed = lambda: [l for l in sb.facts_lines(now=1_800_000_000.0, sessions=sessions, root=root)
                      if "s-builder-R" in l and "Managed sessions" not in l]
    box["under"](UNDER_PLAIN)
    assert len(listed()) == 1 and "s-builder-R2" in listed()[0]     # 9100 is this session
    box["under"](UNDER_THREAD)
    assert len(listed()) == 2                                       # was 1: pid 9000 was "me"


# ------------------------------------------------------------------ the start record, end to end ----
def _fake_ps(tmp: Path, rows: dict, parent: int) -> str:
    d = tmp / f"ps{len(list(tmp.glob('ps*')))}"
    d.mkdir()
    table = d / "table.txt"
    table.write_text("".join(f"{pid}\t{ppid} {cmd}\n" for pid, (ppid, cmd) in rows.items()))
    (d / "ps").write_text(
        '#!/bin/sh\nfor a in "$@"; do p="$a"; done\n'
        f'line=$(awk -F "\\t" -v p="$p" \'$1==p {{print $2}}\' "{table}")\n'
        f'if [ -n "$line" ]; then echo "$line"; else echo "{parent} /bin/sh -c python3 hook.py"; fi\n')
    (d / "ps").chmod(0o755)
    return str(d)


@pytest.mark.parametrize("tree,pid,name", [
    (UNDER_THREAD, 9001, None),              # was 9000 and "demo": the host's
    (UNDER_PLAIN, 9100, "s-builder-R1"),     # a plain session: as before
    (UNDER_HOST, None, None),                # the host's own first session: cannot tell
])
def test_the_start_record_carries_the_sessions_own_pid_and_name(tmp_path, tree, pid, name):
    vault, repo, state = tmp_path / "vault", tmp_path / "repo", tmp_path / "state"
    (vault / "MyProject").mkdir(parents=True)
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: MY-PROJECT\npath: MyProject/\n")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"))
    env["PATH"] = _fake_ps(tmp_path, *tree) + os.pathsep + env.get("PATH", "")
    p = subprocess.run([sys.executable, str(HOOKS / "session_start.py")],
                       input=json.dumps({"cwd": str(repo), "session_id": "s1", "source": "startup"}),
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    doc = json.loads((state / "session-start-s1.json").read_text())
    assert (doc["pid"], doc["name"]) == (pid, name)
