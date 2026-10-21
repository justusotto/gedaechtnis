"""VAULTSYNC-3 — write homes: on a vault two machines share, a file is written from ONE machine.

`hooks/homes.py` reads one tracked file that names the machines and gives each lane and each
shared path a home; `gate.py` refuses an Edit/Write or a Bash write from the other machine, and
the chores that write a vault file by themselves skip it. The door is off unless the limit is
true AND `topology.homes_file` is set AND the file exists. Every refusal here has the allowed
write beside it, and the gate half drives `gate.py` as a subprocess, the way Claude Code calls it.
The last tests hold the plugin's own writers in `chore.py` to the lock a vault sync moves files under.
"""
from __future__ import annotations
import fcntl, json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
sys.path.insert(0, str(HOOKS))

import homes  # noqa: E402

HOMES = ("# write homes\n"
         "machine: macbook id=AAAA\n"
         "machine: mini id=BBBB\n"
         "default: macbook\n"
         "lane: CURSUS home=macbook\n"
         "lane: MINER home=mini\n"
         "shared: Global/ home=macbook\n"
         "shared: Global/mini-notes.md home=mini\n"
         "shared: Ghost/ home=laptop\n")


@pytest.fixture
def w(tmp_path):
    vault = tmp_path / "Vault"
    for d in ("Global", "Speculum", "Mine", "Ghost", "Queues"):
        (vault / d).mkdir(parents=True)
    (vault / "Speculum" / "Errata.md").write_text("# Errata\n\n- one\n")
    (vault / "Global" / "Patterns.md").write_text("# Patterns\n\n- one\n")
    (vault / "Global" / "mini-notes.md").write_text("# notes\n\n- one\n")
    (vault / "Ghost" / "Position.md").write_text("# Position\n\n- one\n")
    (vault / "Queues" / "own.md").write_text("- [ ] a row | q:X-1")           # no trailing newline
    (vault / "homes.txt").write_text(HOMES)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    home = tmp_path / "home"
    repo = home / "repo"; repo.mkdir(parents=True)
    (repo / ".atlas-lane").write_text("lane: CURSUS\npath: Speculum/\npath: Global/\npath: Ghost/\npath: Queues/\n")
    state = tmp_path / "state"; state.mkdir()
    cfg = tmp_path / "config.json"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user-memory.md"), HOME=str(home),
               GEDAECHTNIS_CONFIG=str(cfg), GEDAECHTNIS_TODAY="2026-10-08", GEDAECHTNIS_MACHINE_ID="AAAA")
    for k in ("GEDAECHTNIS_LIMITS", "CLAUDE_PROJECT_DIR", "GEDAECHTNIS_PROFILE"):
        env.pop(k, None)
    out = dict(vault=vault, repo=repo, state=state, cfg=cfg, env=env)
    configure(out)
    return out


def configure(w, door=True, homes_file="homes.txt", profile=None, partition="warn"):
    topo = {"non_region_tops": ["Global"], "queues_dir": "Queues"}
    if homes_file:
        topo["homes_file"] = homes_file
    cfg = {"topology": topo, "partition": {"mode": partition}}
    if door is not None:
        cfg["limits"] = {"write_homes_door": door}
    if profile:
        cfg["profile"] = profile
    w["cfg"].write_text(json.dumps(cfg))


def hook(w, script, which, tool, tool_input, machine="AAAA"):
    env = dict(w["env"], GEDAECHTNIS_MACHINE_ID=machine)
    p = subprocess.run([sys.executable, str(HOOKS / script), which],
                       input=json.dumps({"cwd": str(w["repo"]), "tool_name": tool, "session_id": "wh",
                                         "tool_input": tool_input}),
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out).get("hookSpecificOutput", {}) if out else {}


def edit(w, rel, machine="AAAA"):
    return hook(w, "gate.py", "write", "Edit",
                {"file_path": str(w["vault"] / rel), "old_string": "- one\n", "new_string": "- one\n- two\n"}, machine)


def refused(res):
    return res.get("permissionDecision") == "deny" and "WRITE HOMES" in res.get("permissionDecisionReason", "")


# ---- the table --------------------------------------------------------------------------------

def test_a_home_is_the_longest_shared_entry_then_the_lane_then_the_default():
    t = homes.parse(HOMES)
    assert t["machines"] == {"macbook": "AAAA", "mini": "BBBB"}
    assert homes.home_of("Global/Patterns.md", "MINER", t) == "macbook"       # shared beats the lane
    assert homes.home_of("Global/mini-notes.md", "CURSUS", t) == "mini"        # the longer entry beats the prefix
    assert homes.home_of("Global/mini-notes.md.bak", "CURSUS", t) == "macbook"  # an exact entry is exact
    assert homes.home_of("Mine/Position.md", "MINER", t) == "mini"            # the lane's line
    assert homes.home_of("Mine/Position.md", "NOBODY", t) == "macbook"        # the default
    assert homes.home_of("Mine/Position.md", None, homes.parse("machine: a id=1\n")) is None


def test_an_indented_or_malformed_line_declares_nothing():
    t = homes.parse(" machine: a id=1\nmachine: b\nlane: X\nshared: Global/\ndefault:\n")
    assert t == {"machines": {}, "default": None, "lanes": {}, "shared": []}


def test_another_spelling_of_a_declared_path_gets_the_same_home():                        # review 2
    import unicodedata
    t = homes.parse(HOMES + "shared: Caf\u00e9/ home=mini\n")
    for rel in ("global/Patterns.md", "GLOBAL/patterns.MD", "global/Brand-new.md"):
        assert homes.home_of(rel, "MINER", t) == "macbook", rel
    assert homes.home_of("global/MINI-notes.md", "CURSUS", t) == "mini"
    assert homes.home_of(unicodedata.normalize("NFD", "Caf\u00e9/x.md"), "CURSUS", t) == "mini"
    assert homes.home_of("Globalx/Patterns.md", "MINER", t) == "mini"          # the control: another folder is another folder


# ---- the door at the gate ------------------------------------------------------------------------

def test_a_write_from_the_home_machine_passes_and_from_the_other_machine_is_refused(w):
    assert not refused(edit(w, "Speculum/Errata.md", "AAAA"))
    res = edit(w, "Speculum/Errata.md", "BBBB")
    assert refused(res)
    why = res["permissionDecisionReason"]
    assert "`Speculum/Errata.md` is written from macbook; this machine is mini." in why
    assert "For-the-vault block" in why


def test_a_shared_path_homed_on_the_other_machine_is_refused_here_and_allowed_there(w):
    assert refused(edit(w, "Global/mini-notes.md", "AAAA"))
    assert not refused(edit(w, "Global/mini-notes.md", "BBBB"))
    assert not refused(edit(w, "Global/Patterns.md", "AAAA"))
    assert refused(edit(w, "Global/Patterns.md", "BBBB"))


def test_a_machine_the_file_does_not_list_is_refused_and_so_is_a_home_it_does_not_list(w):
    res = edit(w, "Speculum/Errata.md", "CCCC")
    assert refused(res) and "this machine is not listed" in res["permissionDecisionReason"]
    res = edit(w, "Speculum/Errata.md", "")
    assert refused(res) and "this machine is not listed" in res["permissionDecisionReason"]
    res = edit(w, "Ghost/Position.md", "AAAA")
    assert refused(res) and "does not list as a machine" in res["permissionDecisionReason"]


def test_a_path_no_line_covers_is_refused_when_there_is_no_default(w):
    (w["vault"] / "homes.txt").write_text(HOMES.replace("default: macbook\n", "").replace("lane: CURSUS home=macbook\n", ""))
    res = edit(w, "Speculum/Errata.md", "AAAA")
    assert refused(res) and "no declared home machine" in res["permissionDecisionReason"]
    assert not refused(edit(w, "Global/Patterns.md", "AAAA"))                 # a covered path still passes


@pytest.mark.parametrize("how", ["limit off", "no homes_file key", "file missing", "public profile"])
def test_the_door_is_silent_until_the_limit_the_key_and_the_file_are_all_there(w, how):
    assert refused(edit(w, "Speculum/Errata.md", "BBBB"))                      # the control: on, it refuses
    if how == "limit off":
        configure(w, door=False)
    elif how == "no homes_file key":
        configure(w, homes_file=None)
    elif how == "file missing":
        (w["vault"] / "homes.txt").unlink()
    else:
        configure(w, door=None, profile="public")
    assert not refused(edit(w, "Speculum/Errata.md", "BBBB"))


def test_the_atlas_profile_turns_the_limit_on_and_a_key_set_by_hand_wins(w):
    configure(w, door=None, profile="atlas")
    assert refused(edit(w, "Speculum/Errata.md", "BBBB"))
    configure(w, door=False, profile="atlas")
    assert not refused(edit(w, "Speculum/Errata.md", "BBBB"))


def test_a_bash_write_meets_the_same_door(w):
    cmd = f"echo x >> {w['vault'] / 'Speculum' / 'Errata.md'}"
    res = hook(w, "gate.py", "bash", "Bash", {"command": cmd}, "BBBB")
    assert refused(res) and "Bash write via" in res["permissionDecisionReason"]
    assert not refused(hook(w, "gate.py", "bash", "Bash", {"command": cmd}, "AAAA"))
    outside = hook(w, "gate.py", "bash", "Bash", {"command": f"echo x >> {w['repo'] / 'notes.txt'}"}, "BBBB")
    assert not refused(outside)                                                # not a vault path: not this door's


def test_the_machine_id_is_probed_once_and_read_from_the_state_folder_after(w, monkeypatch):
    monkeypatch.delenv("GEDAECHTNIS_MACHINE_ID", raising=False)
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(w["state"]))
    calls = []
    monkeypatch.setattr(homes, "_probe_id", lambda: calls.append(1) or "PROBED")
    assert homes.machine_id() == "PROBED" and homes.machine_id() == "PROBED"
    assert calls == [1]
    assert (w["state"] / homes.ID_FILE).read_text().strip() == "PROBED"
    monkeypatch.setenv("GEDAECHTNIS_MACHINE_ID", "SEAM")
    assert homes.machine_id() == "SEAM"


# ---- the plugin's own writers: the lock a vault sync moves files under, and the same door -----

def qlint(w, machine="AAAA"):
    q = w["vault"] / "Queues" / "own.md"
    hook(w, "chore.py", "write", "Edit", {"file_path": str(q), "old_string": "a", "new_string": "a"}, machine)
    return q.read_bytes().endswith(b"\n")


def test_a_chore_writes_nothing_while_a_vault_sync_holds_the_files_and_writes_after(w):
    fh = open(w["state"] / "vault-files.lock", "a+")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)                                    # what vaultsync holds while it moves files
    try:
        assert qlint(w) is False
    finally:
        fh.close()
    assert "a vault sync held the vault's files" in (w["state"] / "chore.log").read_text()
    assert qlint(w) is True                                                    # the control: free, the repair lands


def test_a_chore_on_the_other_machine_leaves_the_file_alone(w):
    assert qlint(w, "BBBB") is False
    assert "WRITE HOMES" in (w["state"] / "chore.log").read_text()
    assert qlint(w, "AAAA") is True


def test_the_four_writers_in_chore_all_go_through_the_one_helper():
    src = (HOOKS / "chore.py").read_text(encoding="utf-8")
    for what in ("artifact-index", "map-row", "qlint-newline", "inbox"):
        assert f'with _vault_files("{what}"' in src, what


def test_a_door_that_is_on_and_cannot_decide_refuses_instead_of_crashing_open(w, monkeypatch, request):   # review, should-fix 1
    for k, v in w["env"].items():
        if k.startswith("GEDAECHTNIS_") or k == "HOME":
            monkeypatch.setenv(k, v)
    monkeypatch.delenv("GEDAECHTNIS_MACHINE_ID")
    monkeypatch.delenv("GEDAECHTNIS_LIMITS", raising=False); monkeypatch.delenv("GEDAECHTNIS_PROFILE", raising=False)
    homes.limits._reset_for_tests()              # limits caches per process: read THIS test's config
    request.addfinalizer(homes.limits._reset_for_tests)
    (w["state"] / homes.ID_FILE).write_bytes(b"\xff\xfe not text")
    monkeypatch.setattr(homes, "_probe_id", lambda: "AAAA")
    assert homes.machine_id() == "AAAA"                                        # an unreadable cache is probed again
    monkeypatch.setattr(homes, "machine_id", lambda: 1 / 0)
    why = homes.refusal("Speculum/Errata.md", "CURSUS")
    assert why and "could not decide" in why and "ZeroDivisionError" in why
    monkeypatch.setattr(homes, "homes_path", lambda: None)
    assert homes.refusal("Speculum/Errata.md", "CURSUS") is None               # the control: off stays silent


def test_a_map_row_that_was_skipped_is_said_and_a_file_with_no_row_coming_takes_no_lock(w):   # review, should-fix 3 and 5
    (w["vault"] / "Speculum" / "Map.md").write_text("# Speculum\n\n## Files in this folder\n\n- [[Errata|Mistakes]] — x\n")
    created = w["state"] / "created"
    def write_new(rel, machine):
        f = w["vault"] / rel
        f.write_text("# new\n")
        env = dict(w["env"], GEDAECHTNIS_MACHINE_ID=machine)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import chore; from pathlib import Path; "
                "print(chore.map_row_for(Path(sys.argv[2]), sys.argv[3], 'CURSUS'))")
        p = subprocess.run([sys.executable, "-B", "-c", code, str(HOOKS), str(f), rel], capture_output=True, text=True, env=env, timeout=60)
        assert p.returncode == 0, p.stderr
        return p.stdout.strip()
    said = write_new("Speculum/Aporia.md", "BBBB")
    assert "was NOT added" in said and "[[Aporia" not in (w["vault"] / "Speculum" / "Map.md").read_text()
    assert "added one row" in write_new("Speculum/Aporia.md", "AAAA")          # the control: at home the row lands
    log_before = (w["state"] / "chore.log").read_text()
    assert write_new("Speculum/notes-of-mine.md", "BBBB") == "None"            # not a role file: nothing asked, nothing logged
    assert (w["state"] / "chore.log").read_text() == log_before
