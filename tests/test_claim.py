"""Tests for the SessionStart/Stop region claim.

The real claim helper is NEVER run: every test points `claim_tool` at a fake shell script in
tmp_path that records its own argv and exits with a code the test chooses. That keeps the suite
away from the real vault's lock directory entirely — a suite that writes the application's real
state makes its own verdict depend on the machine (Global/Errata), and here the state in question
is a coordination lock other live sessions obey.

The Claude Code process is faked the same way: a script named `claude` records its pid and then
runs the hook, so the hook's parent-chain walk has something real to find. That is also the
control for the rule that matters most — the pid handed to the lock is never the hook's own.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "Atlas"
    (vault / "Global").mkdir(parents=True)
    (vault / "Mnemosyne" / "UkrainianCard").mkdir(parents=True)
    (vault / "Speculum").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()                            # repo_root_of stops at a .git
    (repo / ".atlas-lane").write_text(
        "lane: CARD\npath: Mnemosyne/UkrainianCard/\npath: Global/\n"
        "path: Pharos/queues/regions/ukrainian-card.md\n")
    state = tmp_path / "state"

    argv_log = tmp_path / "tool-argv.txt"
    rc_file = tmp_path / "tool-rc.txt"
    tool = tmp_path / "fake_region_claim.sh"
    tool.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{argv_log}"\n'
        f'printf "ATLAS=%s\\n" "$ATLAS" >> "{argv_log}"\n'
        f'[ -f "{rc_file}" ] && exit "$(cat "{rc_file}")"\n'
        "exit 0\n")
    tool.chmod(0o755)

    pidfile = tmp_path / "claude.pid"
    fake_claude = tmp_path / "bin" / "claude"          # basename `claude` — what _is_claude matches
    fake_claude.parent.mkdir()
    fake_claude.write_text(f'#!/bin/sh\nprintf "%s" "$$" > "{pidfile}"\n"$@"\n')  # no exec: it stays the parent
    fake_claude.chmod(0o755)

    # A PATH whose `ps` reports every process as a child of init: the parent-chain walk then
    # finds nothing, which is the only way to test the no-claude-ancestor branch from inside a
    # suite that is itself running under a real Claude Code process.
    blind = tmp_path / "blind"
    blind.mkdir()
    (blind / "ps").write_text('#!/bin/sh\necho "1 /usr/sbin/nothing"\n')
    (blind / "ps").chmod(0o755)

    # A PATH whose `ps` reports a FIXED claude ancestor (pid 4242), so two runs of the hook see
    # the one process a resume really does share. The wrapper above cannot: it is a new shell,
    # with a new pid, every time it runs.
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    (pinned / "ps").write_text(
        '#!/bin/sh\nfor a in "$@"; do p="$a"; done\n'
        'if [ "$p" = "4242" ]; then echo "1 /opt/bin/claude --model m"; else echo "4242 /bin/sh"; fi\n')
    (pinned / "ps").chmod(0o755)

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"claim_tool": str(tool)}))

    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_CONFIG=str(cfg))
    env.pop("GEDAECHTNIS_AUTO_CLAIM", None)
    env.pop("GEDAECHTNIS_CLAIM_TOOL", None)
    return dict(vault=vault, repo=repo, state=state, env=env, tmp=tmp_path, cfg=cfg,
                argv_log=argv_log, rc_file=rc_file, tool=tool, fake_claude=fake_claude,
                pidfile=pidfile, blind=blind, pinned=pinned)


def hook(w, which, *, cwd=None, sid="S1", source="startup", under_claude=True, blind_ps=False,
         pinned_ps=False):
    """Run claim.py, optionally beneath a process whose basename is `claude`."""
    argv = [sys.executable, str(HOOKS / "claim.py"), which]
    if under_claude:
        argv = [str(w["fake_claude"])] + argv
    env = dict(w["env"])
    if blind_ps or pinned_ps:
        env["PATH"] = str(w["blind" if blind_ps else "pinned"]) + os.pathsep + env.get("PATH", "")
    payload = {"cwd": cwd or str(w["repo"]), "session_id": sid, "source": source}
    p = subprocess.run(argv, input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    return p


def claude_pid(w):
    """The pid the fake `claude` wrapper reported for the MOST RECENT hook run."""
    return w["pidfile"].read_text().strip()


def calls(w):
    """The subcommand lines the fake tool recorded (ATLAS echo lines dropped)."""
    if not w["argv_log"].exists():
        return []
    return [l for l in w["argv_log"].read_text().splitlines() if l and not l.startswith("ATLAS=")]


def claims(w, sid="S1"):
    f = w["state"] / f"session-start-{sid}.json"
    return json.loads(f.read_text())["claims"] if f.exists() else None


def logtext(w, name):
    f = w["state"] / f"{name}.log"
    return f.read_text() if f.exists() else ""


# ---------------------------------------------------------------- the pair works ----
def test_start_claims_the_region_and_stop_releases_exactly_it(world):
    w = world
    hook(w, "start")
    pid = w["pidfile"].read_text().strip()
    assert calls(w) == [f"claim-interactive Mnemosyne/UkrainianCard {pid}"]
    assert claims(w) == [{"region": "Mnemosyne/UkrainianCard", "pid": int(pid)}]

    hook(w, "stop")
    assert calls(w)[1] == f"release-interactive Mnemosyne/UkrainianCard {pid}"
    assert claims(w) == []                         # nothing left to release twice


def test_the_tool_is_told_which_vault_to_use(world):
    """The helper reads ATLAS; a hook must not act on a different vault than the plugin's."""
    hook(world, "start")
    assert f"ATLAS={world['vault']}" in world["argv_log"].read_text()


# ------------------------------------------------------------- the pid handed over ----
def test_pid_is_the_claude_process_never_the_hook_s_own(world):
    w = world
    hook(w, "start")
    claude_pid = int(w["pidfile"].read_text().strip())
    assert claims(w)[0]["pid"] == claude_pid
    line = [l for l in logtext(w, "claim").splitlines() if "start" in l][0]
    self_pid = int(line.split("self=")[1].split("\t")[0])
    assert self_pid != claude_pid                  # the walk moved off the hook process
    assert f"claude={claude_pid}" in line


def test_no_claude_ancestor_claims_nothing_and_says_why(world):
    w = world
    hook(w, "start", under_claude=False, blind_ps=True)     # a parent chain with no claude in it
    assert calls(w) == []
    assert claims(w) is None
    assert "no claude process found" in logtext(w, "hook-errors")


# ------------------------------------------------------------------- the no-ops ----
def test_unknown_lane_no_call(world):
    w = world
    elsewhere = w["tmp"] / "unmarked"
    elsewhere.mkdir()
    hook(w, "start", cwd=str(elsewhere))
    assert calls(w) == []


def test_zero_regions_no_call(world):
    """A marker that declares only shared and non-tier prefixes names no region to claim."""
    w = world
    (w["repo"] / ".atlas-lane").write_text("lane: CURSUS\npath: Global/\npath: Pharos/queues/\n")
    hook(w, "start")
    assert calls(w) == []


def test_tool_absent_no_call_no_crash(world):
    w = world
    w["cfg"].write_text(json.dumps({"claim_tool": str(w["tmp"] / "nope.sh")}))
    p = hook(w, "start")
    assert calls(w) == [] and p.stdout.strip() == ""
    hook(w, "stop")                                 # and the stop half survives it too
    assert calls(w) == []


def test_auto_claim_false_no_call(world):
    w = world
    w["cfg"].write_text(json.dumps({"claim_tool": str(w["tool"]), "auto_claim": False}))
    hook(w, "start")
    assert calls(w) == []


def test_auto_claim_defaults_true_with_no_config_key(world):
    w = world
    w["cfg"].write_text(json.dumps({"claim_tool": str(w["tool"])}))   # no auto_claim key at all
    hook(w, "start")
    assert len(calls(w)) == 1


# --------------------------------------------------------- the helper's exit codes ----
def test_held_by_another_writer_is_not_recorded(world):
    w = world
    w["rc_file"].write_text("1")                    # 1 = held-by-other
    p = hook(w, "start")
    assert claims(w) == []
    assert "NOT held" in p.stdout
    hook(w, "stop")
    assert len(calls(w)) == 1                       # stop released nothing it does not own


def test_raced_lost_is_not_recorded(world):
    w = world
    w["rc_file"].write_text("4")                    # 4 = won the mkdir, holding NOTHING
    hook(w, "start")
    assert claims(w) == []


def test_release_exit_5_keeps_the_claim_and_logs_it(world):
    """5 means the lock SURVIVED. It is not a release, and must not be recorded as one."""
    w = world
    hook(w, "start")
    pid = int(claude_pid(w))
    w["rc_file"].write_text("5")
    hook(w, "stop")
    assert claims(w) == [{"region": "Mnemosyne/UkrainianCard", "pid": pid}]
    assert "SURVIVES" in logtext(w, "hook-errors")


# ------------------------------------------------------------------ re-issue ----
def test_resume_reissues_the_claim_without_duplicating_the_record(world):
    """A resume/compact re-runs SessionStart against the same live Claude process. The helper
    treats a re-claim by the holder as idempotent, and the record must not grow a second row —
    or Stop would release the same lock twice and the second refusal would look like a defect."""
    w = world
    hook(w, "start", under_claude=False, pinned_ps=True)
    hook(w, "start", source="resume", under_claude=False, pinned_ps=True)
    assert calls(w) == ["claim-interactive Mnemosyne/UkrainianCard 4242"] * 2
    assert claims(w) == [{"region": "Mnemosyne/UkrainianCard", "pid": 4242}]
    hook(w, "stop", under_claude=False, pinned_ps=True)
    assert calls(w).count("release-interactive Mnemosyne/UkrainianCard 4242") == 1


def test_stop_without_a_start_does_nothing(world):
    hook(world, "stop", sid="never-started")
    assert calls(world) == []


# --------------------------------------------------- region resolution sources ----
def test_region_comes_from_the_repo_s_own_claude_md_when_the_marker_names_none(world):
    """A single-segment partition (`Speculum/`) is not a region path, but the repo's @-imports
    name the region it is for."""
    w = world
    (w["repo"] / ".atlas-lane").write_text("lane: CURSUS\npath: Speculum/\npath: Global/\n")
    (w["repo"] / "CLAUDE.md").write_text(f"@{w['vault']}/Speculum/Kernel.md\n")
    hook(w, "start")
    pid = w["pidfile"].read_text().strip()
    assert calls(w) == [f"claim-interactive Speculum {pid}"]


def test_a_region_missing_from_the_vault_is_not_claimed(world):
    w = world
    (w["repo"] / ".atlas-lane").write_text("lane: X\npath: Mnemosyne/NoSuchRegion/\n")
    hook(w, "start")
    assert calls(w) == []
