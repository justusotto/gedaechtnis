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
    (vault / "Studio" / "Cards").mkdir(parents=True)
    (vault / "Toolkit").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()                            # repo_root_of stops at a .git
    (repo / ".atlas-lane").write_text(
        "lane: CARD\npath: Studio/Cards/\npath: Global/\n"
        "path: Queues/regions/cards.md\n")
    state = tmp_path / "state"

    argv_log = tmp_path / "tool-argv.txt"
    rc_file = tmp_path / "tool-rc.txt"
    tool = tmp_path / "fake_region_claim.sh"
    tool.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{argv_log}"\n'
        f'printf "ATLAS=%s\\n" "$ATLAS" >> "{argv_log}"\n'
        f'[ "$1" = status ] && [ -f "{rc_file}.status" ] && {{ cat "{rc_file}.status"; exit 0; }}\n'
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
    cfg.write_text(json.dumps({"claim_tool": str(tool),
                           "topology": {"non_region_tops": ["Global", "Queues", "Channels"],
                                        "queues_dir": "Queues/regions"}}))

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
    assert calls(w) == [f"claim-interactive Studio/Cards {pid}"]
    assert claims(w) == [{"region": "Studio/Cards", "pid": int(pid)}]

    hook(w, "stop")
    assert calls(w)[1] == f"release-interactive Studio/Cards {pid}"
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
    (w["repo"] / ".atlas-lane").write_text("lane: CURSUS\npath: Global/\npath: Queues/\n")
    hook(w, "start")
    assert calls(w) == []


def test_tool_absent_no_call_no_crash(world):
    w = world
    w["cfg"].write_text(json.dumps({"claim_tool": str(w["tmp"] / "nope.sh")}))
    p = hook(w, "start")
    assert calls(w) == []
    # …and the session is TOLD it is unclaimed rather than left to read silence as a claim
    assert "Region claim" in p.stdout and "OFF" in p.stdout
    hook(w, "stop")                                 # and the stop half survives it too
    assert calls(w) == []


def test_auto_claim_false_no_call(world):
    w = world
    w["cfg"].write_text(json.dumps({"claim_tool": str(w["tool"]), "auto_claim": False}))
    p = hook(w, "start")
    assert calls(w) == []
    assert "auto_claim" in p.stdout, "a disabled feature still says so"


# ------------------------------------------------- the facts line for an OFF claim ----
# The package ships no claim helper, so OFF is the ordinary state of a published copy. What must
# never happen is OFF looking like ON. Each case below pairs with its negative control: the
# claiming run, which prints "claim held" and no OFF line.
def test_no_helper_configured_says_so_and_names_the_setting(world):
    w = world
    # `tool_root` is pointed at an empty directory, which is what a published copy of the plugin
    # looks like: no `claim_tool` key, and no helper where the default would derive one.
    w["cfg"].write_text(json.dumps({"tool_root": str(w["tmp"] / "empty-root")}))
    p = hook(w, "start")
    assert calls(w) == []
    assert "Region claim" in p.stdout and "OFF" in p.stdout
    assert "claim_tool" in p.stdout, "the line says how to turn it on"
    assert "Studio/Cards" in p.stdout, "and which region went unclaimed"


def test_the_off_line_is_absent_when_the_claim_is_actually_held(world):
    """Negative control: with a working helper there is no OFF line — so the line above is proof of
    a state, not a string this hook always prints."""
    w = world
    p = hook(w, "start")
    assert len(calls(w)) == 1
    assert "Region claim held" in p.stdout
    assert "OFF" not in p.stdout


def test_no_region_means_no_line_at_all(world):
    """Outside a region there is nothing to claim, so the facts line would be noise. Silence here
    is the correct answer and is not the silence the line exists to prevent."""
    w = world
    w["cfg"].write_text(json.dumps({"tool_root": str(w["tmp"] / "empty-root")}))
    (w["repo"] / ".atlas-lane").write_text("lane: CURSUS\npath: Global/\npath: Queues/\n")
    p = hook(w, "start")
    assert calls(w) == [] and p.stdout.strip() == ""


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
    subs = [str(c).split()[0] for c in calls(w) if not str(c).startswith("status")]
    assert subs == ["claim-interactive", "reap"], subs   # rc 1 → one reap attempt (refused here, rc 1); stop released nothing it does not own


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
    assert claims(w) == [{"region": "Studio/Cards", "pid": pid}]
    assert "SURVIVES" in logtext(w, "hook-errors")


# ------------------------------------------------------------------ re-issue ----
def test_resume_reissues_the_claim_without_duplicating_the_record(world):
    """A resume/compact re-runs SessionStart against the same live Claude process. The helper
    treats a re-claim by the holder as idempotent, and the record must not grow a second row —
    or Stop would release the same lock twice and the second refusal would look like a defect."""
    w = world
    hook(w, "start", under_claude=False, pinned_ps=True)
    hook(w, "start", source="resume", under_claude=False, pinned_ps=True)
    assert calls(w) == ["claim-interactive Studio/Cards 4242"] * 2
    assert claims(w) == [{"region": "Studio/Cards", "pid": 4242}]
    hook(w, "stop", under_claude=False, pinned_ps=True)
    assert calls(w).count("release-interactive Studio/Cards 4242") == 1


def test_stop_without_a_start_does_nothing(world):
    hook(world, "stop", sid="never-started")
    assert calls(world) == []


# --------------------------------------------------- region resolution sources ----
def test_region_comes_from_the_repo_s_own_claude_md_when_the_marker_names_none(world):
    """A single-segment partition (`Toolkit/`) is not a region path, but the repo's @-imports
    name the region it is for."""
    w = world
    (w["repo"] / ".atlas-lane").write_text("lane: CURSUS\npath: Toolkit/\npath: Global/\n")
    (w["repo"] / "CLAUDE.md").write_text(f"@{w['vault']}/Toolkit/Kernel.md\n")
    # A one-segment directory PROVES it is a region by carrying a role file. It used to be
    # reachable through a hard-coded region name instead, so every other flat vault's region
    # failed this path silently.
    (w["vault"] / "Toolkit" / "Kernel.md").write_text("# Toolkit\n")
    hook(w, "start")
    pid = w["pidfile"].read_text().strip()
    assert calls(w) == [f"claim-interactive Toolkit {pid}"]


def test_a_region_missing_from_the_vault_is_not_claimed(world):
    w = world
    (w["repo"] / ".atlas-lane").write_text("lane: X\npath: Studio/NoSuchRegion/\n")
    hook(w, "start")
    assert calls(w) == []


def test_user_prompt_submit_reclaims_with_a_promptless_payload(world):
    """Stop fires at the end of EVERY turn, so hooks.json re-runs `start` on UserPromptSubmit,
    whose payload carries no `source`. It must claim exactly like a startup does."""
    hj = json.loads((HOOKS / "hooks.json").read_text())
    assert "UserPromptSubmit" in hj["hooks"], "the per-turn re-claim must be wired"
    assert "claim.py start" in hj["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"].replace('"', "")
    hook(world, "start", source=None)
    assert any(str(c).startswith("claim-interactive") for c in calls(world)), calls(world)


def _counting_tool(world, first_claim_rc, reap_rc):
    """A fake helper: the first claim-interactive returns `first_claim_rc`, reap returns `reap_rc`,
    every later call returns 0. Records argv like the fixture's tool."""
    count = world["tool"].parent / "count.txt"
    world["tool"].write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{world["argv_log"]}"\n'
        f'n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{count}"\n'
        f'case "$1" in claim-interactive) [ "$n" -eq 1 ] && exit {first_claim_rc}; exit 0;; '
        f'reap) exit {reap_rc};; esac\nexit 0\n')


def test_held_by_a_dead_writer_is_reaped_once_and_reclaimed(world):
    """First live run (2026-09-09): a 12-day-dead interactive lock returned rc 1 and nothing
    reaped it. On rc 1 the hook asks the helper's reaper once and retries once."""
    _counting_tool(world, first_claim_rc=1, reap_rc=0)
    hook(world, "start")
    subs = [str(c).split()[0] for c in calls(world)]
    assert subs == ["claim-interactive", "reap", "claim-interactive"], subs
    assert claims(world), "the retried claim is recorded"


def test_reap_refused_means_no_retry(world):
    _counting_tool(world, first_claim_rc=1, reap_rc=1)
    hook(world, "start")
    subs = [str(c).split()[0] for c in calls(world) if not str(c).startswith("status")]
    assert subs == ["claim-interactive", "reap"], subs
    assert not claims(world)


# --------------------------------------------- GUARDSILENT-1: never wait on your own claim ----
def _status_table(w, region, holder, mode="interactive"):
    """The helper's real layout: `printf '%-34s %-25s %-8s %-16s %-6s %-8s %-12s %s\\n'`."""
    row = "%-34s %-25s %-8s %-16s %-6s %-8s %-12s %s\n"
    (w["rc_file"].parent / (w["rc_file"].name + ".status")).write_text(
        row % ("REGION", "MODE", "PID", "HOST", "HELD", "STARTED", "LIVENESS", "REAPABLE-NOW")
        + row % (region, mode, holder, "h", "1m", "1m", "alive", "no"))


def test_a_holder_that_is_THIS_session_is_named_as_yours(world):
    """CARDKERNEL-2 waited on a holder that was itself. The pinned `ps` makes our pid 4242."""
    w = world
    w["rc_file"].write_text("1")
    _status_table(w, "Studio/Cards", 4242)
    p = hook(w, "start", under_claude=False, pinned_ps=True)
    out = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "held by THIS session's own Claude process (pid 4242)" in out, out
    assert "another writer holds it" not in out


@pytest.mark.parametrize("region,mode", [
    ("Studio/Cards", "unknown(treated managed)"),     # the helper's only mode with a space in it
    ("Studio/Cards", "managed"),
    ("Studio/" + "a" * 40, "interactive"),           # longer than the 34-wide column
])
def test_own_holder_is_recognised_in_every_mode_the_helper_prints(world, region, mode):
    w = world
    (w["vault"] / region).mkdir(parents=True, exist_ok=True)
    w["repo"].joinpath(".atlas-lane").write_text(f"lane: CARD\npath: {region}/\n")
    w["rc_file"].write_text("1")
    _status_table(w, region, 4242, mode)
    p = hook(w, "start", under_claude=False, pinned_ps=True)
    out = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "held by THIS session's own Claude process (pid 4242)" in out, out


def test_a_holder_that_is_ANOTHER_process_is_named_by_pid(world):
    w = world
    w["rc_file"].write_text("1")
    _status_table(w, "Studio/Cards", 777)
    p = hook(w, "start", under_claude=False, pinned_ps=True)
    out = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "another writer holds it" in out and "holder pid 777" in out, out
    assert "not this session's own (4242)" in out


# ------------------------------------- PROJECTCLAIM-1: the event name, and threads of a host ----
def _run(w, payload, env_extra=None):
    env = dict(w["env"], **(env_extra or {}))
    p = subprocess.run([sys.executable, str(HOOKS / "claim.py"), "start"], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    return p


def _tree(w, rows, default_parent):
    """A PATH whose `ps` answers from a FAKED process tree: `rows` maps pid -> (ppid, command).
    Any pid not in the table (the hook's own, which is real) is reported as a child of
    `default_parent` run by a plain shell. No real process is looked at, none is signalled."""
    d = w["tmp"] / f"tree{len(list(w['tmp'].glob('tree*')))}"
    d.mkdir()
    table = d / "table.txt"
    table.write_text("".join(f"{pid}\t{ppid} {cmd}\n" for pid, (ppid, cmd) in rows.items()))
    (d / "ps").write_text(
        '#!/bin/sh\nfor a in "$@"; do p="$a"; done\n'
        f'line=$(awk -F "\\t" -v p="$p" \'$1==p {{print $2}}\' "{table}")\n'
        f'if [ -n "$line" ]; then echo "$line"; else echo "{default_parent} /bin/sh -c python3 claim.py start"; fi\n')
    (d / "ps").chmod(0o755)
    return {"PATH": str(d) + os.pathsep + w["env"].get("PATH", "")}


HOST = "claude remote-control --name demo host --spawn same-dir --capacity 4"
THREAD = ("/opt/x/.nvm/versions/node/v24/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe "
          "--print --sdk-url https://api.anthropic.com/v1/code/sessions/cse_{0} --session-id cse_{0}")


def _payload(w, event, sid="S1"):
    d = {"cwd": str(w["repo"]), "session_id": sid}
    if event:
        d["hook_event_name"] = event
    return d


def _event_out(p):
    return json.loads(p.stdout)["hookSpecificOutput"]["hookEventName"] if p.stdout.strip() else None


def test_the_output_names_the_event_it_was_called_for(world):
    """Defect 2: Claude Code refuses a hook whose output names another event than its own. A
    UserPromptSubmit run must answer UserPromptSubmit (positive), a SessionStart run SessionStart
    and an input naming no event the old answer (negatives: nothing changed there)."""
    w = world
    env = _tree(w, {9001: (1, "/opt/bin/claude --model m")}, 9001)
    assert _event_out(_run(w, _payload(w, "UserPromptSubmit"), env)) == "UserPromptSubmit"
    assert _event_out(_run(w, _payload(w, "SessionStart"), env)) == "SessionStart"
    assert _event_out(_run(w, _payload(w, None), env)) == "SessionStart"
    bad = dict(_payload(w, None), hook_event_name=123)       # not a string: the old answer
    assert _event_out(_run(w, bad, env)) == "SessionStart"


def test_the_off_line_names_its_event_too(world):
    w = world
    w["cfg"].write_text(json.dumps({"claim_tool": str(w["tmp"] / "absent.sh")}))
    p = _run(w, _payload(w, "UserPromptSubmit"))
    assert _event_out(p) == "UserPromptSubmit", p.stdout


def test_a_thread_of_a_host_claims_with_its_OWN_pid_never_the_host_s(world):
    w = world
    env = _tree(w, {9000: (1, HOST), 9001: (9000, THREAD.format("A"))}, 9001)
    p = _run(w, _payload(w, "UserPromptSubmit"), env)
    assert calls(w) == ["claim-interactive Studio/Cards 9001"]
    assert claims(w) == [{"region": "Studio/Cards", "pid": 9001}]
    assert "pid 9001" in p.stdout and "9000" not in p.stdout


def test_two_threads_under_one_host_are_two_holders(world):
    w = world
    rows = {9000: (1, HOST), 9001: (9000, THREAD.format("A")), 9002: (9000, THREAD.format("B"))}
    _run(w, _payload(w, "UserPromptSubmit", sid="A"), _tree(w, rows, 9001))
    _run(w, _payload(w, "UserPromptSubmit", sid="B"), _tree(w, rows, 9002))
    assert claims(w, "A") == [{"region": "Studio/Cards", "pid": 9001}]
    assert claims(w, "B") == [{"region": "Studio/Cards", "pid": 9002}]


def test_a_thread_claims_nothing_at_start_up_only_at_its_first_prompt(world):
    """The host's own first session never has a turn and never leaves; it must hold nothing."""
    w = world
    env = _tree(w, {9000: (1, HOST), 9001: (9000, THREAD.format("A"))}, 9001)
    p = _run(w, _payload(w, "SessionStart"), env)
    assert calls(w) == [] and claims(w) is None and p.stdout == ""
    assert "skipped at SessionStart" in logtext(w, "claim")
    _run(w, _payload(w, "UserPromptSubmit"), env)
    assert calls(w) == ["claim-interactive Studio/Cards 9001"]


def test_a_hook_directly_under_a_host_claims_nothing(world):
    """At start-up that is the host's own session: logged, quiet. At a PROMPT it is a thread that
    was not recognised: said to the session and to hook-errors."""
    w = world
    env = _tree(w, {9000: (1, HOST)}, 9000)
    p = _run(w, _payload(w, "SessionStart"), env)
    assert calls(w) == [] and claims(w) is None and p.stdout == ""
    assert "Remote Control host" in logtext(w, "claim")
    assert logtext(w, "hook-errors") == ""
    p = _run(w, _payload(w, "UserPromptSubmit"), env)
    assert calls(w) == [] and claims(w) is None
    assert "NOT taken" in p.stdout and _event_out(p) == "UserPromptSubmit"
    assert "no recognised thread process" in logtext(w, "hook-errors")


@pytest.mark.parametrize("host", [
    "claude --permission-mode auto remote-control --name h",
    "/opt/x/bin/claude.exe remote-control --name h",
    # a flag whose value has spaces, before the subcommand (`ps` prints no quotes)
    "claude --name Atlas host remote-control",
    "claude --debug-file /data/My Logs/h.log remote-control",
    'claude --settings {"a": 1, "b": 2} remote-control',
    "claude --add-dir /a /b remote-control --spawn same-dir",
    "claude --name=Atlas host remote-control",
])
def test_a_host_spelled_otherwise_is_still_never_a_holder(world, host):
    """Review finding 1: a flag with a value before the subcommand, and a `claude.exe` host, were
    read as a plain session / walked past. Above the host sits a plain `claude` (8000) that must
    not be picked up either."""
    w = world
    env = _tree(w, {8000: (1, "claude --model m"), 9000: (8000, host)}, 9000)
    _run(w, _payload(w, "SessionStart"), env)
    assert calls(w) == [] and claims(w) is None
    # the prompt path too (CLAIMHOST-2): a hook right under such a host takes nothing and says so
    p = _run(w, _payload(w, "UserPromptSubmit"), env)
    assert calls(w) == [] and claims(w) is None
    assert "NOT taken" in p.stdout


@pytest.mark.parametrize("plain", [
    "claude fix the remote-control bug",                  # a prompt that mentions the word
    "claude --name remote-control",                       # a session NAMED remote-control
    "claude --model m --name remote-control --resume x",
    "/opt/x/bin/claude.exe --verbose look at remote-control hosts",
])
def test_a_plain_session_that_only_mentions_remote_control_claims(world, plain):
    """CLAIMHOST-2 positive: these were read as hosts and never claimed. Negative control is the
    test above: a real host, spelled either way, still takes nothing at start-up or at a prompt."""
    w = world
    env = _tree(w, {9001: (1, plain)}, 9001)
    p = _run(w, _payload(w, "UserPromptSubmit"), env)
    assert calls(w) == ["claim-interactive Studio/Cards 9001"]
    assert "NOT taken" not in p.stdout and "Remote Control host" not in p.stdout


@pytest.mark.parametrize("oneshot", [
    "claude --print=json remote-control",
    "claude remote-control --print=text",
    "claude -p=x remote-control",
])
def test_a_print_run_spelled_with_an_equals_sign_is_no_host(oneshot):
    """CLAIMHOST-2: `--print=…` counts like `--print`. Negative control: without it, a host."""
    sys.path.insert(0, str(HOOKS))
    import procs
    assert not procs.is_host(oneshot)
    assert procs.is_host("claude remote-control --name h")
    assert procs.is_host("claude --printer x remote-control")      # no print flag by prefix alone


def test_a_plain_claude_session_claims_at_start_up_as_before(world):
    w = world
    env = _tree(w, {9001: (1, "claude --resume 2de0e225")}, 9001)
    _run(w, _payload(w, "SessionStart"), env)
    assert calls(w) == ["claim-interactive Studio/Cards 9001"]


@pytest.mark.parametrize("depth,found", [(6, True), (7, False)])
def test_a_session_six_hops_deep_is_found_as_before_and_seven_is_not(world, depth, found):
    """The hop limit is unchanged: `claude_pid` and the claim agree at the edge."""
    w = world
    rows = {8000 + i: (8000 + i + 1, "/bin/sh -c wrapper") for i in range(1, depth)}
    rows[8000 + depth] = (1, "claude --model m")
    _run(w, _payload(w, "SessionStart"), _tree(w, rows, 8001))
    assert calls(w) == ([f"claim-interactive Studio/Cards {8000 + depth}"] if found else [])


def test_what_counts_as_a_thread_and_as_a_host():
    sys.path.insert(0, str(HOOKS))
    import procs
    assert procs.is_thread(THREAD.format("A"))
    assert procs.is_thread("/bin/sh /tmp/bin/claude.exe --print --sdk-url=https://a")
    assert procs.is_thread("/x/claude.exe --sdk-url https://a")          # the flag right after the exe
    assert not procs.is_thread("claude --resume x")
    assert not procs.is_thread(HOST)
    # a SHELL that merely carries the text of a thread command in its -c string is no thread
    assert not procs.is_thread("/bin/zsh -c echo " + THREAD.format("A"))
    assert procs.is_host(HOST)
    assert procs.is_host("/usr/local/bin/claude --debug remote-control")
    assert not procs.is_host("/bin/zsh -c claude remote-control")
    assert not procs.is_host("claude -p fix the host")
    assert not procs.is_host("claude -p fix the remote-control host")    # a one-shot run is no host
    assert procs.is_host("claude --permission-mode auto remote-control")
    assert not procs.is_host(THREAD.format("A"))
    # CLAIMHOST-2: the subcommand is the first argument that is no flag and no flag's value
    assert not procs.is_host("claude fix the remote-control bug")
    assert not procs.is_host("claude --name remote-control")
    assert not procs.is_host("claude --name=remote-control --model m")
    assert not procs.is_host("claude --verbose fix the remote-control bug")
    assert procs.is_host("claude --name remote-control remote-control")       # named so, AND a host
    assert procs.is_host("claude --model m --permission-mode auto remote-control --name h")
    assert procs.is_host("claude --some-new-flag remote-control")             # unknown flag: host side
    assert procs.is_host("claude -- remote-control")
    assert not procs.is_host("claude --debug-file /tmp/remote-control")
    # a value with spaces: its first token is the value, the rest runs to the next flag
    assert procs._SPACED_VALUE_FLAGS <= procs._VALUED_FLAGS
    assert procs.is_host("claude --name Atlas host remote-control")
    assert procs.is_host("claude --name=Atlas host remote-control")           # the `=` spelling too
    assert not procs.is_host("claude --name=remote-control")
    assert not procs.is_host("claude --model=m x remote-control")             # `=` on a one-word flag
    assert procs.is_host("claude --model m --add-dir /a /b remote-control --name h")
    assert not procs.is_host("claude --name remote-control --model m")        # the value itself
    assert not procs.is_host("claude --name Atlas host --model m fix the remote-control bug")
    assert not procs.is_host("claude --name Atlas host")
    assert not procs.is_host("claude --name Atlas host -p remote-control")
    assert not procs.is_host("claude --model m x remote-control")             # one-word value: no tail
    assert procs.is_host("claude --name x fix the remote-control bug")        # the named cost


# ---- CONTEXTMSG-1: the claim line at a prompt is printed only when it changes ----------------

def test_the_same_claim_line_is_not_repeated_on_the_next_prompt(world):
    w = world
    w["cfg"].write_text(json.dumps({"claim_tool": str(w["tmp"] / "absent.sh")}))
    first = _run(w, _payload(w, "UserPromptSubmit"))
    assert "Region claim" in first.stdout                          # positive: said once
    again = _run(w, _payload(w, "UserPromptSubmit"))
    assert again.returncode == 0 and again.stdout == ""            # the same line: not again


def test_a_changed_claim_line_and_every_session_start_are_printed(world):
    w = world
    working = w["cfg"].read_text()                                 # the fixture's own claim tool
    w["cfg"].write_text(json.dumps({"claim_tool": str(w["tmp"] / "absent.sh")}))
    _run(w, _payload(w, "UserPromptSubmit"))
    assert "Region claim" in _run(w, _payload(w, "SessionStart")).stdout   # a new window hears it
    env = _tree(w, {9001: (1, "/opt/bin/claude --model m")}, 9001)
    w["cfg"].write_text(working)                                   # the claim tool is back: news
    p = _run(w, _payload(w, "UserPromptSubmit"), env)
    assert "Region claim held" in p.stdout, p.stdout
    assert _run(w, _payload(w, "UserPromptSubmit"), env).stdout == ""
    assert "Region claim" in _run(w, _payload(w, "UserPromptSubmit", sid="S2"), env).stdout
