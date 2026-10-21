#!/usr/bin/env python3
"""Write homes (VAULTSYNC-3): on a vault two machines share, each lane and each shared path is
written from ONE machine, named in one tracked file. A vault write from the other machine is
refused, and the entry goes into that session's handoff instead.

Why a door and not the merge: `vaultsync.py` joins two sides only when both ADDED lines at the
same place; everything else waits for a person. One writing machine per file keeps that case rare.

The file (`topology.homes_file`, vault-relative), one entry per line at column 0:

    machine: macbook id=<machine id>
    machine: mini id=<machine id>
    default: macbook
    lane: WRITER home=macbook
    shared: Global/ home=macbook
    shared: Notes/Canon.md home=macbook

A file's home is its longest matching `shared:` entry, else the writing lane's `lane:` line, else
`default:`. The home is DECLARED, never inferred: no home, a home naming a machine the file does
not list, or a machine the file does not list all refuse.

OFF unless `limits.write_homes_door` is true AND `topology.homes_file` is set AND that file
exists; any one missing and `refusal()` returns None for every path.
"""
from __future__ import annotations
import os, re, subprocess, unicodedata
from pathlib import Path

import config
import limits

_MACHINE = re.compile(r"^machine:\s*(\S+)\s+id=(\S+)\s*$")
_DEFAULT = re.compile(r"^default:\s*(\S+)\s*$")
_LANE = re.compile(r"^lane:\s*(\S+)\s+home=(\S+)\s*$")
_SHARED = re.compile(r"^shared:\s*(.+?)\s+home=(\S+)\s*$")
ID_FILE = "machine-id"


def homes_path() -> "Path | None":
    """The homes file when the door is on and the file is there, else None."""
    if not limits.get("write_homes_door"):
        return None
    rel = config._topo_rel("homes_file")
    if not rel:
        return None
    p = config.vault() / rel
    return p if p.is_file() else None


def parse(text: str) -> dict:
    out = {"machines": {}, "default": None, "lanes": {}, "shared": []}
    for line in text.splitlines():
        m = _MACHINE.match(line)
        if m:
            out["machines"][m.group(1)] = m.group(2)
            continue
        m = _DEFAULT.match(line)
        if m:
            out["default"] = m.group(1)
            continue
        m = _LANE.match(line)
        if m:
            out["lanes"][m.group(1)] = m.group(2)
            continue
        m = _SHARED.match(line)
        if m:
            out["shared"].append((m.group(1), m.group(2)))
    return out


def _probe_id() -> str:
    try:
        r = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True,
                           text=True, timeout=5)
        m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', r.stdout or "")
        if m:
            return m.group(1)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        return Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def machine_id() -> str:
    """This machine's id, or "" when none can be read. `GEDAECHTNIS_MACHINE_ID` is the test seam;
    the probed value is cached in the state folder, so the door costs one file read per write."""
    env = os.environ.get("GEDAECHTNIS_MACHINE_ID")
    if env is not None:
        return env.strip()
    cache = config.state() / ID_FILE
    try:
        got = cache.read_text(encoding="utf-8").strip()
        if got:
            return got
    except (OSError, ValueError):                # unreadable or not text: probe again
        pass
    got = _probe_id()
    if got:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(got + "\n", encoding="utf-8")
        except OSError:
            pass
    return got


def _fold(s: str) -> str:
    """One spelling per file: `global/patterns.md` and a decomposed `é` name the same file as the
    declared entry on this disk, so they get its home. On a case-sensitive disk this refuses a
    little too much, which is the safe side for this door (review 3-2)."""
    return unicodedata.normalize("NFC", s).casefold()


def home_of(rel: str, lane: "str | None", table: dict) -> "str | None":
    best, rel = None, _fold(rel)
    for pat, name in table["shared"]:
        hit = rel.startswith(_fold(pat)) if pat.endswith("/") else rel == _fold(pat)
        if hit and (best is None or len(pat) > len(best[0])):
            best = (pat, name)
    if best:
        return best[1]
    if lane and lane in table["lanes"]:
        return table["lanes"][lane]
    return table["default"]


def refusal(rel: str, lane: "str | None") -> "str | None":
    """One line when `rel` must not be written from this machine, else None. A door that is ON
    and cannot decide REFUSES and says why: under the hooks' catch-all a crash here would exit 0,
    which is every write allowed on the machine that must not write (review 3, should-fix 1)."""
    p = homes_path()
    if p is None or not rel:
        return None
    try:
        return _refusal(p, rel, lane)
    except Exception as e:                       # noqa: BLE001
        return (f"WRITE HOMES: the door could not decide for `{rel}` ({e.__class__.__name__}: {e}); "
                f"nothing is written from a machine it cannot name. Fix `{p.name}` by hand.")[:400]


def _refusal(p: Path, rel: str, lane: "str | None") -> "str | None":
    try:
        table = parse(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return (f"WRITE HOMES: the homes file `{p.name}` could not be read, so no machine is the declared "
                f"home of `{rel}`. Fix `{p.name}` by hand; put the entry in your handoff's For-the-vault block.")
    mid = machine_id()
    me = next((n for n, i in table["machines"].items() if mid and i == mid), None)
    home = home_of(rel, lane, table)
    tail = "Put the entry in your handoff's For-the-vault block."
    if home is None:
        return f"WRITE HOMES: `{rel}` has no declared home machine in `{p.name}` (no `shared:`, `lane:` or `default:` line covers it). {tail}"
    if home not in table["machines"]:
        return f"WRITE HOMES: `{rel}` is homed on `{home}`, which `{p.name}` does not list as a machine. {tail}"
    if me is None:
        return f"WRITE HOMES: `{rel}` is written from {home}; this machine is not listed in `{p.name}`. {tail}"
    if me != home:
        return f"WRITE HOMES: `{rel}` is written from {home}; this machine is {me}. {tail}"
    return None
