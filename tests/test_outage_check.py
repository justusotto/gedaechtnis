"""outage_check.py — the guard against the failure that disables every other guard.

2026-09-09: `"agents": "./agents"` in the plugin manifest failed Claude Code's validator, a rejected
manifest disables the WHOLE plugin, and for fifteen hours every session ran with no doors and no
error anywhere. Council 3 K-2 (owner-picked 2026-09-10, "block-then-downgrade"): a UserPromptSubmit
hook in the USER settings, outside the plugin, exits 2 when the plugin's per-session SessionStart
artifact is absent.

THE CONTROLS, and which is real:

* POSITIVE — REAL where the `claude` CLI exists. A scratch copy of this plugin with the `agents`
  key restored is handed to `claude plugin validate`, the actual validator that rejected it in
  September, and the test asserts its non-zero verdict. Claude Code runs NO hook from a rejected
  manifest, so the SessionStart hook never runs and the artifact is never written — which is what
  the check then meets. The absence is SIMULATED (by not running the hook); the rejection that
  causes it is REAL. Without the CLI, only the simulated half runs, and
  `test_absent_artifact_blocks` is the whole positive control.
* NEGATIVE — REAL throughout. The artifact is written by running the plugin's own
  `hooks/session_start.py` as a subprocess, never by the test writing a file it guessed the name
  of: if the writer's path and the checker's path ever diverge, this test fails.

Every case redirects HOME and every GEDAECHTNIS_* path into tmp_path, so the suite never reads or
writes a real vault, a real ~/.claude, or the real state directory.

The second half of the file covers the PREDATES-INSTALL exemption, added 2026-09-10 after the
install refused the installing session's own next eight prompts. Its controls are paired the same
way: the exemption case and the block case differ in the STAMP alone (birth time cannot be set, so
the file is fixed and the stamp moves around it), and every unreadable stamp, unreadable transcript
and absent stamp is asserted to block exactly as the code did before the branch existed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
CHECK = PLUGIN / "outage_check.py"
SESSION_START = PLUGIN / "hooks" / "session_start.py"
INIT = PLUGIN / "init.py"


# ------------------------------------------------------------------ world ----
@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    state = tmp_path / "state"
    vault = tmp_path / "vault"
    (vault / "Global").mkdir(parents=True)
    (vault / "Global" / "fleet-roster.md").write_text("```fleet-roster\nlane: TEST\nrepo: repo\n```\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: TEST\npath: Test/\npath: Global/\n")
    cfg = tmp_path / "config.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    env.update(HOME=str(home), GEDAECHTNIS_STATE_DIR=str(state), GEDAECHTNIS_CONFIG=str(cfg),
               GEDAECHTNIS_VAULT=str(vault),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"))
    return dict(home=home, state=state, vault=vault, repo=repo, cfg=cfg, env=env, tmp=tmp_path)


def check(w, payload, script: Path = CHECK, **extra_env):
    env = dict(w["env"], **extra_env)
    return subprocess.run([sys.executable, "-B", str(script)],
                          input=json.dumps(payload) if payload is not None else "",
                          capture_output=True, text=True, env=env, timeout=30)


def raw_check(w, raw: str, **extra_env):
    env = dict(w["env"], **extra_env)
    return subprocess.run([sys.executable, "-B", str(CHECK)], input=raw,
                          capture_output=True, text=True, env=env, timeout=30)


def session_start(w, sid: str, source: str = "startup"):
    """Run the PLUGIN's own SessionStart hook — the writer of the artifact under test."""
    p = subprocess.run([sys.executable, "-B", str(SESSION_START)],
                       input=json.dumps({"session_id": sid, "cwd": str(w["repo"]), "source": source}),
                       capture_output=True, text=True, env=w["env"], timeout=60)
    assert p.returncode == 0, p.stderr
    return p


def log_rows(w) -> list[str]:
    f = w["state"] / "outage-check.log"
    return [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.is_file() else []


def write_config(w, **keys):
    w["cfg"].write_text(json.dumps(keys, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------- the predates-install exemption ----
# A hook in the USER settings is read on EVERY prompt, not only in sessions started after it was
# installed — which is how, on 2026-09-10, the session that installed this check refused its own
# next eight prompts. The stamp says when the guard went up; a transcript older than the stamp
# means the session was already running, and a running session cannot restart itself.
STAMP = "outage-check-installed.json"
HAS_BIRTHTIME = hasattr(os.stat(__file__), "st_birthtime")
needs_birthtime = pytest.mark.skipif(not HAS_BIRTHTIME,
                                     reason="st_birthtime is macOS/BSD; without it nothing is exempt")


def transcript(w, name: str = "sess.jsonl") -> Path:
    """A stand-in for `~/.claude/projects/<mangled>/<sid>.jsonl`, whose BIRTH time is the signal.
    Birth time cannot be set, so every case varies the STAMP against this file's real one."""
    p = w["tmp"] / "transcripts" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"type":"summary"}\n', encoding="utf-8")
    return p


def birth(p: Path) -> float:
    info = os.stat(p)
    return float(getattr(info, "st_birthtime", info.st_mtime))


def write_stamp(w, at, iso="2026-09-10T17:12:00"):
    """`at=None` writes a stamp with no usable `installed_at`; a str writes raw bytes."""
    w["state"].mkdir(parents=True, exist_ok=True)
    f = w["state"] / STAMP
    if isinstance(at, str):
        f.write_text(at, encoding="utf-8")
    else:
        body = {"iso": iso} if at is None else {"installed_at": at, "iso": iso}
        f.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return f


@needs_birthtime
def test_a_session_older_than_the_install_is_exempt_not_refused(world):
    """THE SPECIMEN: sid 2d87c590 started 2026-09-08, the check was installed 2026-09-10 17:12,
    and eight prompts were refused. Its transcript predates the stamp, so it proceeds."""
    t = transcript(world)
    write_stamp(world, birth(t) + 10)
    r = check(world, {"session_id": "SID-OLD", "transcript_path": str(t), "prompt": "hello"})
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "predates" in r.stdout, r.stdout
    rows = log_rows(world)
    assert len(rows) == 1 and "predates-install\tsid=SID-OLD" in rows[0], rows
    assert not any("\tblock\t" in row or row.endswith("block") for row in rows), rows


@needs_birthtime
def test_the_exemption_is_the_stamp_and_nothing_else(world):
    """Positive/negative pair on the ONE comparison: same session, same transcript, two stamps."""
    t = transcript(world)
    stamp = write_stamp(world, birth(t) + 10)
    assert check(world, {"session_id": "SID-X", "transcript_path": str(t)}).returncode == 0
    stamp.unlink()
    write_stamp(world, birth(t) - 10)
    assert check(world, {"session_id": "SID-X", "transcript_path": str(t)}).returncode == 2


def test_a_session_started_after_the_install_still_blocks(world):
    t = transcript(world)
    write_stamp(world, birth(t) - 10)                  # the guard went up BEFORE this session
    r = check(world, {"session_id": "SID-NEW", "transcript_path": str(t)})
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    rows = log_rows(world)
    assert len(rows) == 1 and "block\tsid=SID-NEW" in rows[0], rows


def test_no_stamp_blocks_exactly_as_before(world):
    """The pre-fix behaviour, unchanged: with nothing saying when the guard went up, nobody is
    exempt — an old transcript alone must never open the door."""
    t = transcript(world)
    assert not (world["state"] / STAMP).exists()
    r = check(world, {"session_id": "SID-DEAD", "transcript_path": str(t)})
    assert r.returncode == 2, (r.returncode, r.stdout)
    assert len(log_rows(world)) == 1 and "block" in log_rows(world)[0]


@needs_birthtime
@pytest.mark.parametrize("payload", [
    {},                                                        # no transcript_path at all
    {"transcript_path": ""},
    {"transcript_path": "   "},
    {"transcript_path": None},
    {"transcript_path": 17},                                   # a schema change, not a path
    {"transcript_path": "/no/such/transcript-4f2a.jsonl"},     # names a file that is not there
])
def test_without_a_readable_transcript_the_stamp_exempts_nobody(world, payload):
    t = transcript(world)
    write_stamp(world, birth(t) + 10)                          # old enough, if it could be read
    r = check(world, dict({"session_id": "SID-DEAD"}, **payload))
    assert r.returncode == 2, (payload, r.returncode, r.stdout)
    assert "block" in "".join(log_rows(world))


@needs_birthtime
@pytest.mark.parametrize("stamp", ["{ not json", "[]", '"2026-09-10"', '{"installed_at": "soon"}',
                                   '{"installed_at": null}', '{"installed_at": true}', '{"iso": "x"}'])
def test_a_stamp_it_cannot_read_blocks(world, stamp):
    t = transcript(world)
    write_stamp(world, stamp)
    assert check(world, {"session_id": "SID-DEAD", "transcript_path": str(t)}).returncode == 2, stamp


@needs_birthtime
def test_the_happy_path_is_untouched_by_the_exemption(world):
    """Artifact present: exit 0, nothing printed, nothing logged — even with a stamp and an old
    transcript sitting right there. The one stat stays one stat."""
    session_start(world, "SID-OK")
    t = transcript(world)
    write_stamp(world, birth(t) + 10)
    r = check(world, {"session_id": "SID-OK", "transcript_path": str(t)})
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    assert log_rows(world) == []


@needs_birthtime
def test_message_mode_is_untouched(world):
    """The downgrade path never consults the stamp: it warns, as it did before."""
    write_config(world, outage_check="message")
    t = transcript(world)
    write_stamp(world, birth(t) + 10)
    r = check(world, {"session_id": "SID-DEAD", "transcript_path": str(t)})
    assert r.returncode == 0
    rows = log_rows(world)
    assert len(rows) == 1 and "message\tsid=SID-DEAD" in rows[0], rows


# -------------------------------------------------------- negative control ----
def test_negative_control_artifact_written_by_the_real_hook_is_silent(world):
    """The plugin loaded: its SessionStart hook wrote the record, and the check says nothing."""
    session_start(world, "SID-OK")
    assert (world["state"] / "session-start-SID-OK.json").is_file(), \
        "the hook did not write the artifact this check keys off — writer and checker have diverged"
    r = check(world, {"session_id": "SID-OK", "prompt": "hello"})
    assert r.returncode == 0
    assert r.stdout == "" and r.stderr == ""
    assert log_rows(world) == []


def test_negative_control_holds_for_a_resumed_session(world):
    session_start(world, "SID-RES", source="resume")
    assert check(world, {"session_id": "SID-RES"}).returncode == 0


# -------------------------------------------------------- positive control ----
def test_absent_artifact_blocks(world):
    """No SessionStart record for this session id -> exit 2. This is what a rejected manifest
    looks like from the prompt's side: the hook never ran, so the file was never written."""
    r = check(world, {"session_id": "SID-DEAD", "prompt": "hello"})
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert r.stdout == "", "a blocking check must not also inject context"


def test_the_block_message_names_the_failure_the_cause_and_the_off_switch(world):
    err = check(world, {"session_id": "SID-DEAD"}).stderr
    assert "did not load this session" in err                      # (a) what happened
    assert "session-start-SID-DEAD.json" in err                    #     and the evidence
    assert "claude plugin list" in err and "manifest" in err       # (b) the likely cause
    assert "GEDAECHTNIS_OUTAGE_CHECK=off" in err                   # (c) one session
    assert '"outage_check": "off"' in err                          #     permanently
    assert "--remove-outage-check" in err                          #     and uninstall
    assert str(world["cfg"]) in err, "the off-switch must name the config file THIS install reads"


def test_a_block_leaves_one_log_row_so_the_falsifier_can_count_it(world):
    check(world, {"session_id": "SID-DEAD"})
    rows = log_rows(world)
    assert len(rows) == 1 and "block\tsid=SID-DEAD" in rows[0], rows


def test_the_check_runs_with_no_plugin_around_it(world, tmp_path):
    """Copied to a bare directory, with no hooks/, no config.py, no plugin: it must still work.
    The failure it guards can be an UNINSTALL as easily as a bad key."""
    lonely = tmp_path / "elsewhere" / "outage_check.py"
    lonely.parent.mkdir(parents=True)
    shutil.copy2(CHECK, lonely)
    assert check(world, {"session_id": "SID-DEAD"}, script=lonely).returncode == 2
    session_start(world, "SID-OK")
    assert check(world, {"session_id": "SID-OK"}, script=lonely).returncode == 0


# ------------------------------------- the real validator, where it exists ----
def copy_plugin(dest: Path, agents_key: bool) -> Path:
    shutil.copytree(PLUGIN, dest, ignore=shutil.ignore_patterns("tests", "eval", ".git", "__pycache__"))
    m = dest / ".claude-plugin" / "plugin.json"
    d = json.loads(m.read_text(encoding="utf-8"))
    if agents_key:
        d["agents"] = "./agents"                 # the exact 2026-09-09 defect
    m.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    return dest


def validate(path: Path):
    return subprocess.run(["claude", "plugin", "validate", str(path)],
                          capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_positive_control_the_real_validator_rejects_the_agents_key(world, tmp_path):
    """REAL half of the positive control: Claude Code's own validator, on the exact defect."""
    bad = copy_plugin(tmp_path / "plugin-bad", agents_key=True)
    v = validate(bad)
    assert v.returncode != 0, (v.returncode, v.stdout, v.stderr)
    assert "agents" in (v.stdout + v.stderr)
    # A rejected manifest runs NO hook, so nothing writes session-start-<sid>.json. That absence
    # is what the check meets on the first prompt:
    assert check(world, {"session_id": "SID-BAD"}).returncode == 2


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_paired_control_the_shipped_manifest_validates(tmp_path):
    """The other arm of the same instrument: the manifest we ship passes, so the branch the
    positive control takes is decided by the validator, not by the test's assumption."""
    good = copy_plugin(tmp_path / "plugin-good", agents_key=False)
    v = validate(good)
    assert v.returncode == 0, (v.returncode, v.stdout, v.stderr)


# ------------------------------------------- a schema break must not block ----
def test_missing_session_id_exits_zero_and_leaves_exactly_one_row(world):
    r = check(world, {"prompt": "hello", "cwd": str(world["repo"])})
    assert r.returncode == 0 and r.stderr == ""
    rows = log_rows(world)
    assert len(rows) == 1 and "no-session-id" in rows[0] and "prompt" in rows[0], rows


@pytest.mark.parametrize("raw", ["", "   ", "not json at all", "[1,2,3]", '{"session_id": ""}',
                                 '{"session_id": null}'])
def test_no_usable_session_id_never_blocks(world, raw):
    r = raw_check(world, raw)
    assert r.returncode == 0, (raw, r.returncode, r.stderr)
    assert len(log_rows(world)) == 1


# ------------------------------------------------ the downgrade, built now ----
def test_message_mode_from_the_config_file_does_not_block(world):
    write_config(world, outage_check="message")
    r = check(world, {"session_id": "SID-DEAD"})
    assert r.returncode == 0
    assert "did not load this session" in r.stderr
    assert "did not load this session" in r.stdout, "message mode must reach the model, not only the transcript"
    rows = log_rows(world)
    assert len(rows) == 1 and "message\tsid=SID-DEAD" in rows[0], rows


def test_message_mode_from_the_environment_wins_over_the_config_file(world):
    write_config(world, outage_check="block")
    assert check(world, {"session_id": "SID-DEAD"}, GEDAECHTNIS_OUTAGE_CHECK="message").returncode == 0


def test_off_switches_it_off_entirely_and_silently(world):
    for env in ({"GEDAECHTNIS_OUTAGE_CHECK": "off"}, {"GEDAECHTNIS_OUTAGE_CHECK": "0"}):
        r = check(world, {"session_id": "SID-DEAD"}, **env)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", ""), env
    assert log_rows(world) == []
    write_config(world, outage_check=False)
    assert check(world, {"session_id": "SID-DEAD"}).returncode == 0


def test_an_unknown_mode_value_blocks_rather_than_failing_open(world):
    write_config(world, outage_check="yes-please")
    assert check(world, {"session_id": "SID-DEAD"}).returncode == 2


def test_a_malformed_config_file_does_not_take_the_check_down(world):
    world["cfg"].write_text("{not json", encoding="utf-8")
    assert check(world, {"session_id": "SID-DEAD"}).returncode == 2
    session_start(world, "SID-OK")
    assert check(world, {"session_id": "SID-OK"}).returncode == 0


# ------------------------------------------------------- resolution parity ----
def test_the_state_dir_it_derives_is_the_one_config_py_resolves(world, tmp_path):
    """The check re-derives the state directory instead of importing config.py, so that a broken
    plugin cannot take it down. This pins the two against each other; without it they drift and
    the check stats a path nothing writes."""
    cases = [
        dict(),                                                          # env (the fixture's)
        dict(GEDAECHTNIS_STATE_DIR=str(tmp_path / "env-state")),
    ]
    write_config(world, state_dir=str(tmp_path / "cfg-state"))
    cases.append(dict(GEDAECHTNIS_STATE_DIR=""))                          # falls through to the file
    for extra in cases:
        env = dict(world["env"], **extra)
        if extra.get("GEDAECHTNIS_STATE_DIR") == "":
            env.pop("GEDAECHTNIS_STATE_DIR")
        theirs = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(PLUGIN / 'hooks')!r}); import config; print(config.STATE)"],
            capture_output=True, text=True, env=env, timeout=30)
        ours = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(PLUGIN)!r}); import outage_check as o;"
             " print(o.state_dir(o.config()))"],
            capture_output=True, text=True, env=env, timeout=30)
        assert theirs.returncode == 0 and ours.returncode == 0, (theirs.stderr, ours.stderr)
        assert ours.stdout.strip() == theirs.stdout.strip(), (extra, ours.stdout, theirs.stdout)


def test_it_imports_nothing_but_the_standard_library(world):
    """It runs on every prompt: no plugin import, no third-party import, no subprocess. Read from
    the AST, not from the text — its own docstring says the word `subprocess`, and a grep that a
    comment can satisfy is not a check."""
    import ast
    imported = set()
    for node in ast.walk(ast.parse(CHECK.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"json", "os", "sys", "time", "pathlib", "__future__"}, imported
    p = subprocess.run([sys.executable, "-c",
                        f"import sys; sys.path.insert(0, {str(PLUGIN)!r});"
                        " before=set(sys.modules); import outage_check;"
                        " print(sorted(set(sys.modules)-before))"],
                       capture_output=True, text=True, env=world["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    for mod in json.loads(p.stdout.replace("'", '"')):
        assert mod.split(".")[0] in sys.stdlib_module_names | {"outage_check"}, mod


# ------------------------------------------------------------- the install ----
def run_init(w, *args, script: Path = INIT, cwd: Path | None = None):
    return subprocess.run([sys.executable, str(script), *args], cwd=str(cwd or w["repo"]),
                          capture_output=True, text=True, env=w["env"], stdin=subprocess.DEVNULL,
                          timeout=180)


def settings_path(w) -> Path:
    return w["home"] / ".claude" / "settings.json"


def installed(w) -> list[dict]:
    data = json.loads(settings_path(w).read_text(encoding="utf-8"))
    out = []
    for group in data.get("hooks", {}).get("UserPromptSubmit", []):
        out += [s for s in group.get("hooks", []) if "outage_check.py" in s.get("command", "")]
    return out


def test_init_installs_it_once_and_a_second_run_changes_nothing(world):
    p = run_init(world, "--repo", str(world["repo"]), "--yes")
    assert p.returncode == 0, p.stderr
    assert len(installed(world)) == 1, settings_path(world).read_text()
    first = settings_path(world).read_bytes()
    p = run_init(world, "--repo", str(world["repo"]), "--yes")
    assert p.returncode == 0, p.stderr
    assert settings_path(world).read_bytes() == first, "the second run rewrote settings.json"
    assert len(installed(world)) == 1
    assert "already runs the plugin-outage check" in p.stdout


def test_the_installed_command_guards_against_its_own_absence(world, tmp_path):
    """`python3 <missing file>` exits 2, and exit 2 BLOCKS the prompt — so a moved or uninstalled
    plugin would wedge every session. The command must exit 0 when the script is gone, and still
    pass a real exit 2 through when it is there."""
    run_init(world, "--repo", str(world["repo"]), "--yes")
    cmd = installed(world)[0]["command"]
    gone = subprocess.run(cmd.replace(str(CHECK), str(tmp_path / "no-such-outage_check.py")),
                          shell=True, capture_output=True, text=True, env=world["env"], timeout=30)
    assert gone.returncode == 0, (gone.returncode, gone.stderr)
    live = subprocess.run(cmd, shell=True, input=json.dumps({"session_id": "SID-DEAD"}),
                          capture_output=True, text=True, env=world["env"], timeout=30)
    assert live.returncode == 2, (live.returncode, live.stderr)


def test_install_preserves_a_foreign_hook_and_removal_leaves_it_standing(world):
    foreign = {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "echo somebody-elses-hook"}]}]},
        "model": "claude-opus-5"}
    settings_path(world).write_text(json.dumps(foreign, indent=2), encoding="utf-8")
    run_init(world, "--repo", str(world["repo"]), "--yes")
    data = json.loads(settings_path(world).read_text(encoding="utf-8"))
    cmds = [s["command"] for g in data["hooks"]["UserPromptSubmit"] for s in g["hooks"]]
    assert "echo somebody-elses-hook" in cmds and any("outage_check.py" in c for c in cmds)
    assert data["model"] == "claude-opus-5", "an unrelated settings key was dropped"

    p = run_init(world, "--remove-outage-check")
    assert p.returncode == 0, p.stderr
    data = json.loads(settings_path(world).read_text(encoding="utf-8"))
    cmds = [s["command"] for g in data["hooks"]["UserPromptSubmit"] for s in g["hooks"]]
    assert cmds == ["echo somebody-elses-hook"] and data["model"] == "claude-opus-5"


def test_removal_is_idempotent_and_says_so(world):
    run_init(world, "--repo", str(world["repo"]), "--yes")
    assert run_init(world, "--remove-outage-check").returncode == 0
    assert installed(world) == []
    p = run_init(world, "--remove-outage-check")
    assert p.returncode == 0 and "no plugin-outage check was installed" in p.stdout


def test_no_outage_check_skips_the_install_entirely(world):
    p = run_init(world, "--repo", str(world["repo"]), "--yes", "--no-outage-check")
    assert p.returncode == 0, p.stderr
    assert "outage_check.py" not in p.stdout
    assert not settings_path(world).exists() or installed(world) == []


def test_a_settings_file_it_cannot_parse_is_left_alone_and_reported(world):
    settings_path(world).write_text("{ this is not json\n", encoding="utf-8")
    before = settings_path(world).read_bytes()
    p = run_init(world, "--repo", str(world["repo"]), "--yes")
    assert p.returncode == 0, p.stderr                    # the rest of the install still happens
    assert settings_path(world).read_bytes() == before, "a malformed settings.json was overwritten"
    assert "could not be read as JSON" in p.stdout and "was NOT installed" in p.stdout
    p = run_init(world, "--remove-outage-check")
    assert p.returncode == 2 and settings_path(world).read_bytes() == before


def test_a_moved_plugin_rewrites_its_entry_instead_of_duplicating_it(world, tmp_path):
    run_init(world, "--repo", str(world["repo"]), "--yes")
    moved = tmp_path / "moved-plugin"
    copy_plugin(moved, agents_key=False)
    p = run_init(world, "--repo", str(world["repo"]), "--yes", script=moved / "init.py")
    assert p.returncode == 0, p.stderr
    specs = installed(world)
    assert len(specs) == 1, specs
    assert str(moved / "outage_check.py") in specs[0]["command"]


def tree(root: Path) -> dict:
    import hashlib
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            out[str(p.relative_to(root))] = "-> " + os.readlink(p)
        elif p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def test_install_outage_check_alone_writes_nothing_else(world):
    """The command a machine that is ALREADY set up runs. A full `init.py` run there would resolve
    a region from the repo's basename and create it in the vault — this must touch one file."""
    before = tree(world["home"]) | {"vault:" + k: v for k, v in tree(world["vault"]).items()}
    p = run_init(world, "--install-outage-check")
    assert p.returncode == 0, p.stderr
    after = tree(world["home"]) | {"vault:" + k: v for k, v in tree(world["vault"]).items()}
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    assert changed == [".claude/settings.json"], changed
    assert len(installed(world)) == 1
    assert not (world["repo"] / "CLAUDE.md").exists(), "a standalone install touched the repo"


def test_install_outage_check_alone_is_idempotent_and_dry_runnable(world):
    assert run_init(world, "--install-outage-check", "--dry-run").returncode == 0
    assert not settings_path(world).exists()
    run_init(world, "--install-outage-check")
    first = settings_path(world).read_bytes()
    p = run_init(world, "--install-outage-check")
    assert p.returncode == 0 and "already runs" in p.stdout
    assert settings_path(world).read_bytes() == first


def test_install_outage_check_alone_refuses_a_settings_file_it_cannot_parse(world):
    settings_path(world).write_text("{ not json\n", encoding="utf-8")
    before = settings_path(world).read_bytes()
    p = run_init(world, "--install-outage-check")
    assert p.returncode == 2 and "could not be read as JSON" in p.stderr
    assert settings_path(world).read_bytes() == before


def test_dry_run_writes_no_settings(world):
    p = run_init(world, "--repo", str(world["repo"]), "--yes", "--dry-run")
    assert p.returncode == 0, p.stderr
    assert not settings_path(world).exists()
    assert "would update" in p.stdout or "would create" in p.stdout


# ------------------------------------------------------- the install stamp ----
def stamp_path(w) -> Path:
    return w["state"] / STAMP


def stamp(w) -> dict:
    return json.loads(stamp_path(w).read_text(encoding="utf-8"))


def test_init_writes_the_stamp_once_and_never_rewrites_it(world):
    """Its value is the moment the check FIRST began guarding; a rewrite would re-lock out every
    session older than the rewrite — which is the whole bug."""
    p = run_init(world, "--repo", str(world["repo"]), "--yes")
    assert p.returncode == 0, p.stderr
    assert isinstance(stamp(world)["installed_at"], float), stamp(world)
    assert stamp(world)["iso"][:2] == "20"
    first = stamp_path(world).read_bytes()
    p = run_init(world, "--repo", str(world["repo"]), "--yes")
    assert p.returncode == 0, p.stderr
    assert stamp_path(world).read_bytes() == first, "the second run rewrote the install stamp"


def test_a_kept_install_with_no_stamp_gets_one(world):
    """The live situation on 2026-09-10: the hook was installed, and nothing recorded when."""
    run_init(world, "--repo", str(world["repo"]), "--yes")
    stamp_path(world).unlink()
    p = run_init(world, "--repo", str(world["repo"]), "--yes")
    assert p.returncode == 0, p.stderr
    assert "already runs the plugin-outage check" in p.stdout, p.stdout
    assert stamp_path(world).is_file(), p.stdout


def test_install_outage_check_alone_writes_the_stamp_and_a_second_run_keeps_it(world):
    p = run_init(world, "--install-outage-check")
    assert p.returncode == 0, p.stderr
    first = stamp_path(world).read_bytes()
    p = run_init(world, "--install-outage-check")
    assert p.returncode == 0 and "already runs" in p.stdout
    assert stamp_path(world).read_bytes() == first


def test_install_outage_check_alone_stamps_an_install_that_predates_the_stamp(world):
    run_init(world, "--install-outage-check")
    stamp_path(world).unlink()
    p = run_init(world, "--install-outage-check")
    assert p.returncode == 0 and stamp_path(world).is_file(), p.stdout


def test_removal_removes_the_stamp(world):
    run_init(world, "--install-outage-check")
    assert stamp_path(world).is_file()
    p = run_init(world, "--remove-outage-check")
    assert p.returncode == 0, p.stderr
    assert not stamp_path(world).exists()
    assert run_init(world, "--remove-outage-check").returncode == 0      # still idempotent


@pytest.mark.parametrize("args", [("--repo", "REPO", "--yes", "--dry-run"),
                                  ("--install-outage-check", "--dry-run")])
def test_dry_run_writes_no_stamp(world, args):
    args = tuple(str(world["repo"]) if a == "REPO" else a for a in args)
    p = run_init(world, *args)
    assert p.returncode == 0, p.stderr
    assert not stamp_path(world).exists()


def test_no_outage_check_writes_no_stamp(world):
    p = run_init(world, "--repo", str(world["repo"]), "--yes", "--no-outage-check")
    assert p.returncode == 0, p.stderr
    assert not stamp_path(world).exists()


def test_a_settings_file_it_cannot_parse_leaves_no_stamp(world):
    """The check was NOT installed, so nothing began guarding and there is nothing to stamp."""
    settings_path(world).write_text("{ this is not json\n", encoding="utf-8")
    p = run_init(world, "--repo", str(world["repo"]), "--yes")
    assert p.returncode == 0, p.stderr
    assert not stamp_path(world).exists()


@needs_birthtime
def test_end_to_end_the_installer_and_the_check_agree_on_the_stamp(world):
    """The writer and the reader, joined: init writes the stamp, and a transcript created AFTER
    that write blocks while one created before it is exempt. Neither side guesses the path."""
    old = transcript(world, "old.jsonl")
    run_init(world, "--install-outage-check")
    new = transcript(world, "new.jsonl")
    assert stamp(world)["installed_at"] > birth(old)
    assert check(world, {"session_id": "SID-OLD", "transcript_path": str(old)}).returncode == 0
    assert check(world, {"session_id": "SID-NEW", "transcript_path": str(new)}).returncode == 2
