"""Gedächtnis hook tests — every rule gets a POSITIVE control (the violation is denied) and a
NEGATIVE control (the legitimate form is allowed). All state is redirected into tmp_path: the
suite never reads the real vault or writes the real ~/.claude/gedaechtnis (Global/Errata: a suite
that writes the application's real sidecar makes its own verdict depend on the machine's state).
"""
from __future__ import annotations
import json, os, re, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "Atlas"
    (vault / "Global").mkdir(parents=True)
    (vault / "Pharos" / "queues" / "regions").mkdir(parents=True)
    (vault / "Mnemosyne" / "UkrainianCard").mkdir(parents=True)
    (vault / "Speculum").mkdir()
    (vault / "Concilium").mkdir()
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "root"], check=True)
    (vault / "Global" / "fleet-roster.md").write_text("```fleet-roster\nlane: CURSUS\nrepo: src/nope\n```\n")
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: CARD\npath: Mnemosyne/UkrainianCard/\npath: Global/\npath: Pharos/queues/regions/ukrainian-card.md\n")
    state = tmp_path / "state"
    # GEDAECHTNIS_CONFIG points at a file that does not exist, so a real ~/.claude/gedaechtnis/
    # config.json on the host cannot reach the hooks under test (it would otherwise supply
    # `owner_pages_status` and make this suite's verdict depend on the machine).
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, state=state, env=env)


def run(script, which, payload, env):
    p = subprocess.run([sys.executable, str(HOOKS / script), which], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out) if out else None


def decision(res):
    return (res or {}).get("hookSpecificOutput", {}).get("permissionDecision")


def bash(w, cmd, cwd=None):
    return run("gate.py", "bash", {"cwd": cwd or str(w["repo"]), "tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "t"}, w["env"])


# ------------------------------------------------------------------ vault git law ----
def test_add_A_in_vault_denied(world):
    v = world["vault"]
    assert decision(bash(world, f"git -C {v} add -A && git -C {v} commit -m 'x' -- Global/Map.md")) == "deny"

def test_add_path_limited_allowed(world):
    v = world["vault"]
    assert bash(world, f"git -C {v} add -- Global/Map.md && git -C {v} commit -m 'x' -- Global/Map.md") is None

def test_bare_commit_denied_and_assert_form_allowed(world):
    v = world["vault"]
    assert decision(bash(world, f"git -C {v} commit -m 'msg'")) == "deny"
    assert_form = (f"git -C {v} rm --cached -- a.md; S=$(git -C {v} diff --cached --name-only | sort | tr '\\n' '|'); "
                   f"[ \"$S\" = 'a.md|' ] || exit 9; git -C {v} commit -F /tmp/m")
    assert bash(world, assert_form) is None

def test_amend_denied(world):
    v = world["vault"]
    assert decision(bash(world, f"git -C {v} commit --amend --no-edit -- Global/Map.md")) == "deny"

def test_backtick_in_double_quoted_m_denied_single_quotes_allowed(world):
    v = world["vault"]
    assert decision(bash(world, f'git -C {v} commit -m "fix `verdict` field" -- Global/Map.md')) == "deny"
    assert bash(world, f"git -C {v} commit -m 'fix `verdict` field' -- Global/Map.md") is None

def test_cd_then_git_tracks_cwd(world):
    v = world["vault"]
    assert decision(bash(world, f"cd {v} && git add -A")) == "deny"

def test_git_law_does_not_bind_other_repos(world):
    assert bash(world, "git add -A && git commit -m 'repo commit'", cwd=str(world["repo"])) is None

def test_push_backup_allowed_push_origin_denied(world):
    v = world["vault"]
    assert bash(world, f"git -C {v} push backup") is None
    assert decision(bash(world, f"git -C {v} push origin main")) == "deny"

def test_reset_hard_in_vault_denied(world):
    v = world["vault"]
    assert decision(bash(world, f"git -C {v} reset --hard HEAD~1")) == "deny"


# --------------------------------------------------------------- launch pins model ----
def test_claude_launch_without_model_denied(world):
    assert decision(bash(world, "claude -p 'hello' --setting-sources project")) == "deny"

def test_claude_launch_pinned_allowed(world):
    assert bash(world, "claude -p 'hello' --model claude-sonnet-5 --effort medium") is None

def test_claude_launch_model_but_no_effort_denied_only_under_owner_policy(world):
    assert bash(world, "claude -p 'hello' --model claude-sonnet-5") is None
    env = dict(world["env"], GEDAECHTNIS_REQUIRE_LAUNCH_EFFORT="1")
    res = run("gate.py", "bash", {"cwd": str(world["repo"]), "tool_name": "Bash", "tool_input": {"command": "claude -p 'hello' --model claude-sonnet-5"}}, env)
    assert decision(res) == "deny"

def test_claude_subcommands_exempt(world):
    assert bash(world, "claude plugin list") is None
    assert bash(world, "claude --version") is None


# ------------------------------------------------------------------ data integrity ----
# `HOMEDIR` is COMPOSED rather than written as a literal home path: the `_PROTECTED` table matches
# both `~/Pictures` and an absolute `/Users/<name>/Pictures`, so the absolute branch needs a
# positive control — but a literal home path in a source file is exactly what tools/publish_check.py
# refuses, and that check has no allowlist. Composing it keeps both true.
HOMEDIR = "/" + "Users/someone"


@pytest.mark.parametrize("cmd", [
    "rm ~/src/miner/whisper_cache.json",
    f"rm -rf {HOMEDIR}/Pictures/holiday-cull",
    "rm -rf ~/.Trash/*",
    "osascript -e 'tell application \"Finder\" to empty the trash'",
    "rm -rf ~/Atlas",
    "sqlite3 anki_mining.db 'DELETE FROM cards'",
    "find media/ -name '*.mp3' -delete",
])
def test_destructive_on_protected_refuses_and_asks(world, cmd):
    assert decision(bash(world, cmd)) == "ask"          # refuse AND ask: the owner may still say yes

@pytest.mark.parametrize("cmd", [
    "rm -rf /tmp/build",
    "ls ~/Pictures",
    "rm ~/Atlas/.atlas-locks/Speculum.lock",
    "cat whisper_cache.json | head",
    "sqlite3 anki_mining.db 'SELECT count(*) FROM cards'",
])
def test_benign_allowed(world, cmd):
    assert bash(world, cmd) is None


# --------------------------------------------------------- artifact page, not file:// ----
def test_open_answer_store_page_denied_plain_html_allowed(world, tmp_path):
    page = tmp_path / "judge.html"; page.write_text('<title>x</title><script>const db=await claude.use("db")</script>')
    plain = tmp_path / "plain.html"; plain.write_text("<title>x</title><p>hi</p>")
    assert decision(bash(world, f"open {page}")) == "deny"
    assert bash(world, f"open {plain}") is None
    assert decision(bash(world, f"open file://{page}")) == "deny"


# ---------------------------------------------------------------- write partition ----
def write(w, path, mode=None, cwd=None):
    if mode:
        w["state"].mkdir(parents=True, exist_ok=True); (w["state"] / "partition.mode").write_text(mode)
    return run("gate.py", "write", {"cwd": cwd or str(w["repo"]), "tool_name": "Edit", "session_id": "t",
                                    "tool_input": {"file_path": str(path), "old_string": "", "new_string": "x"}}, w["env"])

def test_partition_warn_mode_logs_but_allows(world):
    foreign = world["vault"] / "Speculum" / "Position.md"
    assert write(world, foreign) is None
    log = (world["state"] / "partition.log").read_text()
    assert "warn\tCARD\tSpeculum/Position.md" in log

def test_partition_deny_mode_refuses_foreign_allows_own_and_global(world):
    v = world["vault"]
    assert decision(write(world, v / "Speculum" / "Position.md", mode="deny")) == "deny"
    assert write(world, v / "Mnemosyne" / "UkrainianCard" / "Position.md", mode="deny") is None
    assert write(world, v / "Global" / "Patterns.md", mode="deny") is None
    assert write(world, v / "Pharos" / "queues" / "regions" / "ukrainian-card.md", mode="deny") is None

def test_partition_ignores_non_vault_paths(world, tmp_path):
    assert write(world, tmp_path / "repo" / "x.py", mode="deny") is None

def test_unknown_lane_denied_in_deny_mode(world, tmp_path):
    nolane = tmp_path / "nolane"; nolane.mkdir()
    assert decision(write(world, world["vault"] / "Global" / "Map.md", mode="deny", cwd=str(nolane))) == "deny"

def test_concilium_stem_always_denied(world):
    assert decision(write(world, world["vault"] / "Concilium" / "Position.md")) == "deny"
    assert write(world, world["vault"] / "Concilium" / "Positio.md") is None


# ------------------------------------------------------------------- agent pins model ----
def agent(w, ti):
    return run("gate.py", "agent", {"cwd": str(w["repo"]), "tool_name": "Agent", "tool_input": ti}, w["env"])

def test_agent_without_model_denied_only_under_owner_policy(world):
    assert agent(world, {"subagent_type": "general-purpose", "prompt": "x"}) is None
    world["env"]["GEDAECHTNIS_REQUIRE_AGENT_MODEL"] = "1"
    assert decision(agent(world, {"subagent_type": "general-purpose", "prompt": "x"})) == "deny"

def test_agent_pinned_or_fork_or_defined_allowed(world):
    world["env"]["GEDAECHTNIS_REQUIRE_AGENT_MODEL"] = "1"
    assert agent(world, {"subagent_type": "general-purpose", "prompt": "x", "model": "sonnet"}) is None
    assert agent(world, {"subagent_type": "fork", "prompt": "x"}) is None
    d = world["repo"] / ".claude" / "agents"; d.mkdir(parents=True)
    (d / "mine.md").write_text("---\nname: mine\nmodel: sonnet\n---\nbody\n")
    assert agent(world, {"subagent_type": "mine", "prompt": "x"}) is None
    (d / "unpinned.md").write_text("---\nname: unpinned\n---\nbody\n")
    assert decision(agent(world, {"subagent_type": "unpinned", "prompt": "x"})) == "deny"


# ---------------------------------------------------------------------- chores ----
def test_queue_newline_repaired_and_reported(world):
    q = world["vault"] / "Pharos" / "queues" / "regions" / "ukrainian-card.md"
    q.write_bytes(b"- [ ] `q:X-1` row")
    res = run("chore.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write",
                                    "tool_input": {"file_path": str(q), "content": "- [ ] `q:X-1` row"}}, world["env"])
    assert q.read_bytes().endswith(b"\n")
    assert "trailing newline" in res["hookSpecificOutput"]["additionalContext"]

def test_sha_check_reports_unresolvable_and_accepts_real(world):
    v = world["vault"]; f = v / "Global" / "Map.md"
    real = subprocess.run(["git", "-C", str(v), "log", "-1", "--format=%h"], capture_output=True, text=True).stdout.strip()
    f.write_text(f"see `{real}` and `deadbee`\n")
    res = run("chore.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write",
                                    "tool_input": {"file_path": str(f), "content": f"see `{real}` and `deadbee`"}}, world["env"])
    ctx = res["hookSpecificOutput"]["additionalContext"]
    assert "deadbee" in ctx and real not in ctx

def test_artifact_index_row_appended_and_committed(world):
    v = world["vault"]; idx = v / "Pharos" / "artifacts-index.md"
    idx.write_text("# Artifacts\n\n| date | title | id |\n|---|---|---|\n| 2026-07-23 | Old | `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa` |\n\nPrefix every id with `https://claude.ai/code/artifact/`.\n")
    subprocess.run(["git", "-C", str(v), "add", "--", "Pharos/artifacts-index.md"], check=True)
    subprocess.run(["git", "-C", str(v), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "idx", "--", "Pharos/artifacts-index.md"], check=True)
    page = world["repo"] / "p.html"; page.write_text("<title>My Page</title><p>x</p>")
    uid = "181a58fd-f036-42ce-885e-d51f442b0430"
    res = run("chore.py", "artifact", {"cwd": str(world["repo"]), "tool_name": "Artifact",
              "tool_input": {"file_path": str(page)}, "tool_response": f"Published {page} at https://claude.ai/code/artifact/{uid}"}, world["env"])
    txt = idx.read_text()
    assert f"| My Page | `{uid}` |" in txt and txt.index(uid) < txt.index("Prefix every id")
    log = subprocess.run(["git", "-C", str(v), "log", "-1", "--format=%an %s"], capture_output=True, text=True).stdout
    assert log.startswith("atlas artifacts-index: My Page")
    assert "committed" in res["hookSpecificOutput"]["additionalContext"]
    # idempotent: a second publish of the same id adds nothing
    assert run("chore.py", "artifact", {"cwd": str(world["repo"]), "tool_name": "Artifact", "tool_input": {"file_path": str(page)},
               "tool_response": f"at https://claude.ai/code/artifact/{uid}"}, world["env"]) is None
    assert idx.read_text().count(uid) == 1

def test_session_start_writes_state_and_context(world):
    res = run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s1", "source": "startup"}, world["env"])
    j = json.loads((world["state"] / "session-start.json").read_text())
    assert j["lane"] == "CARD" and len(j["vault_head"]) == 40
    ctx = res["hookSpecificOutput"]["additionalContext"]
    assert "Lane CARD" in ctx and "WARN" in ctx
    assert "Answered review pages" not in ctx          # nothing configured → the hook says nothing

def test_session_start_runs_the_answered_pages_script_only_when_configured(world, tmp_path):
    """The optional `owner_pages_status` seam: absent by default (negative control above), used
    when config.json names a script that exists (positive control here)."""
    script = tmp_path / "pages.py"
    script.write_text('print(\'{"status": "3 uncollected", "uncollected": 3}\')\n', encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"owner_pages_status": str(script)}), encoding="utf-8")
    env = dict(world["env"], GEDAECHTNIS_CONFIG=str(cfg))
    res = run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s3", "source": "startup"}, env)
    ctx = res["hookSpecificOutput"]["additionalContext"]
    assert "Answered review pages (swept by name): 3 uncollected" in ctx and '"uncollected": 3' in ctx

def test_hook_bug_never_crashes_session(world):
    p = subprocess.run([sys.executable, str(HOOKS / "gate.py"), "bash"], input="not json", capture_output=True, text=True, env=world["env"])
    assert p.returncode == 0 and p.stdout.strip() == ""


def test_change_everywhere_lookup_lists_other_occurrences(world):
    v = world["vault"]
    (v / "Speculum" / "Canon.md").write_text("## The old heading name\n\ntext\n")
    (v / "Global" / "Map.md").write_text("see [[../Speculum/Canon#The old heading name]] and `q:CU-2026-09-07-GEDAECHTNIS-1`\n")
    (v / "Global" / "Patterns.md").write_text("cites `q:CU-2026-09-07-GEDAECHTNIS-1` too\n")
    res = run("chore.py", "write", {"cwd": str(world["repo"]), "tool_name": "Edit", "tool_input": {
        "file_path": str(v / "Speculum" / "Canon.md"),
        "old_string": "## The old heading name\n\nsee `q:CU-2026-09-07-GEDAECHTNIS-1`",
        "new_string": "## A new heading name\n\nsee `q:CU-2026-09-07-RENAMED-1`"}}, world["env"])
    ctx = res["hookSpecificOutput"]["additionalContext"]
    assert "The old heading name" in ctx and "Global/Map.md" in ctx
    assert "q:CU-2026-09-07-GEDAECHTNIS-1" in ctx and "Global/Patterns.md" in ctx

def test_change_everywhere_silent_when_nothing_vanished(world):
    v = world["vault"]; f = v / "Speculum" / "Canon.md"; f.write_text("## Kept heading\n\ntext\n")
    res = run("chore.py", "write", {"cwd": str(world["repo"]), "tool_name": "Edit", "tool_input": {
        "file_path": str(f), "old_string": "## Kept heading\n\ntext", "new_string": "## Kept heading\n\nmore text"}}, world["env"])
    assert res is None

def test_session_start_sets_no_bytecode_env(world, tmp_path):
    envf = tmp_path / "env.sh"
    env = dict(world["env"], CLAUDE_ENV_FILE=str(envf))
    run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s2", "source": "startup"}, env)
    assert "PYTHONDONTWRITEBYTECODE=1" in envf.read_text()


# ------------------------------------------------ shared surfaces: any lane may APPEND a keyed row ----
def test_foreign_queue_append_allowed_in_deny_mode_but_rewrite_denied(world):
    q = world["vault"] / "Pharos" / "queues" / "regions" / "mining-ops.md"     # not CARD's queue file
    q.write_text("- [ ] `q:MN-2026-09-01-X-1` existing row | q:MN-2026-09-01-X-1\n")
    world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    append = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {
        "file_path": str(q), "content": q.read_text() + "- [ ] `q:MN-2026-09-08-NEW-1` a row CARD needs MINING-OPS to see | q:MN-2026-09-08-NEW-1\n"}}, world["env"])
    assert append is None
    rewrite = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {
        "file_path": str(q), "content": "- [x] `q:MN-2026-09-01-X-1` I closed your row | q:MN-2026-09-01-X-1\n"}}, world["env"])
    assert decision(rewrite) == "deny" and "SHARED surface" in rewrite["hookSpecificOutput"]["permissionDecisionReason"]
    prose = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {
        "file_path": str(q), "content": q.read_text() + "some prose that is not a row\n"}}, world["env"])
    assert decision(prose) == "deny"

def test_foreign_inbox_and_ledger_append_allowed(world):
    v = world["vault"]; (v / "Speculum").mkdir(exist_ok=True); (v / "Channels" / "ledger").mkdir(parents=True)
    world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    ib = v / "Speculum" / "Inbox.md"; ib.write_text("# Speculum — Inbox\n\n")
    ok = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Edit", "session_id": "t", "tool_input": {
        "file_path": str(ib), "old_string": "# Speculum — Inbox\n\n", "new_string": "# Speculum — Inbox\n\n- 2026-09-08 CARD found a stale line in Kernel.md\n"}}, world["env"])
    assert ok is None
    led = v / "Channels" / "ledger" / "2026-09.tsv"; led.write_text("# hdr\n")
    row = "N-2026-09-08-0001\t2026-09-08T10:00:00\tCARD\tCURSUS\tfact\t-\thello\n"
    assert run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {
        "file_path": str(led), "content": "# hdr\n" + row}}, world["env"]) is None
    bad = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {
        "file_path": str(led), "content": "# hdr\n" + row + "not a row\n"}}, world["env"])
    assert decision(bad) == "deny"


# ------------------------------------------- foreign work is recorded WHERE IT HAPPENED (region Inbox) ----
def test_inbox_row_for_work_in_another_region(world):
    v = world["vault"]; target = v / "Speculum" / "Position.md"; target.write_text("x\n")
    subprocess.run(["git", "-C", str(v), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(v), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"], check=True)
    res = run("chore.py", "inbox", {"cwd": str(world["repo"]), "session_id": "sess1234", "tool_name": "Edit",
                                    "tool_input": {"file_path": str(target), "old_string": "x", "new_string": "y"}}, world["env"])
    ib = v / "Speculum" / "Inbox.md"
    assert ib.is_file() and "CARD wrote `vault file Speculum/Position.md`" in ib.read_text()
    assert "Recorded in Speculum/Inbox.md" in res["hookSpecificOutput"]["additionalContext"]
    log = subprocess.run(["git", "-C", str(v), "log", "-1", "--format=%an %s"], capture_output=True, text=True).stdout
    assert log.startswith("atlas Speculum Inbox: CARD wrote Position.md")
    # same session, same file: no second row
    assert run("chore.py", "inbox", {"cwd": str(world["repo"]), "session_id": "sess1234", "tool_name": "Edit",
                                     "tool_input": {"file_path": str(target), "old_string": "y", "new_string": "z"}}, world["env"]) is None
    assert ib.read_text().count("- 2026-") == 1

def test_inbox_row_for_work_in_another_lanes_repo(world, tmp_path):
    v = world["vault"]; other = tmp_path / "miner"; other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    (other / "CLAUDE.md").write_text(f"@{v}/Global/Map.md\n@{v}/Mnemosyne/AnkiAutoMiner/Kernel.md\n")
    (v / "Mnemosyne" / "AnkiAutoMiner").mkdir(parents=True)
    f = other / "verify" / "x.py"; f.parent.mkdir(); f.write_text("print(1)\n")
    res = run("chore.py", "inbox", {"cwd": str(world["repo"]), "session_id": "s9", "tool_name": "Write",
                                    "tool_input": {"file_path": str(f), "content": "print(1)\n"}}, world["env"])
    ib = v / "Mnemosyne" / "AnkiAutoMiner" / "Inbox.md"
    assert ib.is_file() and "CARD wrote `miner/verify/x.py`" in ib.read_text()
    assert res is not None

def test_no_inbox_row_for_own_lane_or_shared_surface(world):
    v = world["vault"]
    own = v / "Mnemosyne" / "UkrainianCard" / "Position.md"; own.write_text("x\n")
    assert run("chore.py", "inbox", {"cwd": str(world["repo"]), "session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": str(own)}}, world["env"]) is None
    q = v / "Pharos" / "queues" / "regions" / "mining-ops.md"; q.write_text("- [ ] `q:MN-2026-09-08-A-1` r\n")
    assert run("chore.py", "inbox", {"cwd": str(world["repo"]), "session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": str(q)}}, world["env"]) is None
    assert not (v / "Mnemosyne" / "UkrainianCard" / "Inbox.md").exists()

def test_session_start_reports_inbox_and_ledger(world):
    v = world["vault"]; ib = v / "Mnemosyne" / "UkrainianCard" / "Inbox.md"
    ib.write_text("# x\n\n- 2026-09-08 CURSUS wrote `a` …\n- 2026-09-08 VOCAB wrote `b` …\n")
    led = Path(__file__).resolve().parents[1] / "ledger.py"
    subprocess.run([sys.executable, str(led), "append", "--from", "CURSUS", "--to", "CARD", "--kind", "fact", "--ref", "-", "a fact for CARD"], env=world["env"], check=True, capture_output=True)
    res = run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s1", "source": "startup"}, world["env"])
    ctx = res["hookSpecificOutput"]["additionalContext"]
    assert "Inbox.md holds 2 unfolded row(s)" in ctx and "ledger: 1 row(s) addressed to CARD" in ctx
    res2 = run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s2", "source": "startup"}, world["env"])
    assert "ledger: 1 row(s)" in res2["hookSpecificOutput"]["additionalContext"]    # re-announced until ACTED on
    rid = json.loads(subprocess.run([sys.executable, str(led), "read", "--to", "CARD", "--unacked", "--json"], env=world["env"], capture_output=True, text=True).stdout)[0]["id"]
    subprocess.run([sys.executable, str(led), "ack", "--from", "CARD", rid], env=world["env"], check=True, capture_output=True)
    res3 = run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s3", "source": "startup"}, world["env"])
    assert "ledger:" not in res3["hookSpecificOutput"]["additionalContext"]         # the ack is the receipt


# ------------------------------------------------------------------------------ the ledger CLI ----
def test_ledger_append_read_ack_check(world):
    led = Path(__file__).resolve().parents[1] / "ledger.py"; env = world["env"]
    def L(*a): return subprocess.run([sys.executable, str(led), *a], env=env, capture_output=True, text=True)
    r1 = L("append", "--from", "CARD", "--to", "MINING-OPS", "--kind", "request", "--ref", "q:MN-2026-09-08-X-1", "please re-mine   S01E05"); assert r1.returncode == 0, r1.stderr
    rid = r1.stdout.strip(); assert rid.startswith("N-") and rid.endswith("-0001")
    r2 = L("append", "--from", "VOCAB", "--to", "MINING-OPS", "--kind", "fact", "--ref", "-", "second"); assert r2.stdout.strip().endswith("-0002")
    rd = L("read", "--to", "MINING-OPS", "--since-cursor", "--json"); rows = json.loads(rd.stdout); assert [r["id"] for r in rows] == [rid, r2.stdout.strip()]
    assert rows[0]["body"] == "please re-mine S01E05"
    ack = L("ack", "--from", "MINING-OPS", rid); assert ack.returncode == 0 and ack.stdout.strip().endswith("-0003")
    back = json.loads(L("read", "--to", "CARD", "--json").stdout); assert back[0]["kind"] == "read" and back[0]["ref"] == rid
    assert L("ack", "--from", "MINING-OPS", "N-2026-01-01-9999").returncode != 0
    assert L("check").returncode == 0
    f = next((world["vault"] / "Channels" / "ledger").glob("*.tsv")); f.write_text(f.read_text() + "garbage line\n")
    assert L("check").returncode == 1


# ------------------------------------------------------------------ copy-on-write clone worktrees ----
def test_clone_worktree_create_and_clean_remove(tmp_path):
    src = tmp_path / "src"; src.mkdir(); subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "a.txt").write_text("a\n"); (src / "untracked.bin").write_bytes(b"u")
    subprocess.run(["git", "-C", str(src), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one"], check=True)
    env = dict(os.environ, GEDAECHTNIS_WORKTREES=str(tmp_path / "wts"), GEDAECHTNIS_NO_TRASH="1")
    wt = Path(__file__).resolve().parents[1] / "hooks" / "worktree.py"
    p = subprocess.run([sys.executable, str(wt), "create"], input=json.dumps({"cwd": str(src), "name": "feat/x"}), capture_output=True, text=True, env=env)
    clone = Path(p.stdout.strip().splitlines()[-1]); assert clone.is_dir() and (clone / "untracked.bin").exists(), p.stderr
    (clone / "b.txt").write_text("b\n"); subprocess.run(["git", "-C", str(clone), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "two"], check=True)
    p = subprocess.run([sys.executable, str(wt), "remove"], input=json.dumps({"cwd": str(src), "worktree_path": str(clone)}), capture_output=True, text=True, env=env)
    assert p.returncode == 0 and not clone.exists(), p.stderr
    refs = subprocess.run(["git", "-C", str(src), "for-each-ref", "--format=%(refname)", "refs/gedaechtnis/"], capture_output=True, text=True).stdout
    assert "refs/gedaechtnis/feat-x" in refs
    # a clone with NEW uncommitted work is never deleted
    p = subprocess.run([sys.executable, str(wt), "create"], input=json.dumps({"cwd": str(src), "name": "dirty"}), capture_output=True, text=True, env=env)
    clone2 = Path(p.stdout.strip().splitlines()[-1]); (clone2 / "unique.txt").write_text("keep me\n")
    p = subprocess.run([sys.executable, str(wt), "remove"], input=json.dumps({"cwd": str(src), "worktree_path": str(clone2)}), capture_output=True, text=True, env=env)
    assert clone2.exists() and "unique to the clone" in p.stderr


# ------------------------------------------------ council iteration-1 findings (blind seat), fixed ----
def test_region_of_repo_handles_one_segment_region(world, tmp_path):
    from pathlib import Path as P
    import importlib.util, sys as _s
    spec = importlib.util.spec_from_file_location("common_t", str(HOOKS / "common.py")); m = importlib.util.module_from_spec(spec)
    _s.modules["common_t"] = m; os.environ["GEDAECHTNIS_VAULT"] = str(world["vault"]); spec.loader.exec_module(m)
    r = tmp_path / ("atlas-" + "system"); r.mkdir(); (r / "CLAUDE.md").write_text(f"@{world['vault']}/Global/Map.md\n@{world['vault']}/Speculum/Kernel.md\n")
    assert m.region_of_repo(r) == "Speculum"
    r2 = tmp_path / "card"; r2.mkdir(); (r2 / "CLAUDE.md").write_text(f"@{world['vault']}/Mnemosyne/UkrainianCard/Kernel.md\n")
    assert m.region_of_repo(r2) == "Mnemosyne/UkrainianCard"

def test_bash_writes_go_through_the_partition_door(world):
    v = world["vault"]; world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    assert decision(bash(world, f"echo hi >> {v}/Speculum/Position.md")) == "deny"
    assert decision(bash(world, f"sed -i '' 's/a/b/' {v}/Speculum/Position.md")) == "deny"
    assert decision(bash(world, f"cp /tmp/x.md {v}/Speculum/Position.md")) == "deny"
    assert bash(world, f"echo hi >> {v}/Mnemosyne/UkrainianCard/Position.md") is None                     # own lane
    assert bash(world, f"echo '- 2026-09-08 CARD x' >> {v}/Speculum/Inbox.md") is None                     # append to a shared surface
    assert decision(bash(world, f"echo x > {v}/Speculum/Inbox.md")) == "deny"                              # overwrite of a shared surface
    assert decision(bash(world, f"touch {v}/Concilium/Position.md")) == "deny"                             # stem rule via Bash

def test_umbrella_shared_files_are_append_only_even_for_declaring_lanes(world):
    v = world["vault"]; (v / "Mnemosyne" / "Position.md").write_text("# Mnemosyne Position\n\n- old\n")
    marker = world["repo"] / ".atlas-lane"; marker.write_text(marker.read_text() + "path: Mnemosyne/Position.md\n")
    world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    f = v / "Mnemosyne" / "Position.md"
    ok = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Edit", "session_id": "t", "tool_input": {
        "file_path": str(f), "old_string": "- old\n", "new_string": "- old\n- CARD: new line\n"}}, world["env"])
    assert ok is None
    bad = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Edit", "session_id": "t", "tool_input": {
        "file_path": str(f), "old_string": "- old", "new_string": "- rewritten"}}, world["env"])
    assert decision(bad) == "deny"



# ------------------------------------------------ council iteration-1 findings (Caspar, Melchior, Balthasar), fixed ----
def test_quoted_semicolon_in_commit_message_is_not_a_bare_commit(world):
    v = world["vault"]
    assert bash(world, f"git -C {v} commit -m 'fix a; b | c' -- Global/Map.md") is None
    heredoc = "\n".join([f'git -C {v} commit -m "$(cat <<' + "'EOF'", "one; two | three", "EOF", ')" -- Global/Map.md'])
    assert bash(world, heredoc) is None

def test_commit_am_and_bash_c_and_git_dir_are_caught(world):
    v = world["vault"]
    assert decision(bash(world, f'git -C {v} commit -am "x" -- Global/Map.md')) == "deny"
    assert decision(bash(world, f'bash -c "git -C {v} add -A"')) == "deny"
    assert decision(bash(world, f"git --git-dir={v}/.git add -A")) == "deny"

def test_git_rm_cached_is_not_a_data_deletion_but_the_pathspec_commit_trap_is_caught(world):
    v = world["vault"]
    res = bash(world, f"git -C {v} rm --cached -- x.md && git -C {v} commit -m 'untrack' -- x.md")
    assert decision(res) == "deny" and "DISCARDS the staged deletion" in res["hookSpecificOutput"]["permissionDecisionReason"]
    assert bash(world, f"git -C {v} rm --cached -- x.md; S=$(git -C {v} diff --cached --name-only); [ \"$S\" = x.md ] || exit 9; git -C {v} commit -F /tmp/m") is None

def test_vault_cwd_session_is_the_owners_hand(world):
    world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    assert write(world, world["vault"] / "Speculum" / "Position.md", cwd=str(world["vault"])) is None

def test_queue_continuation_must_be_keyed(world):
    q = world["vault"] / "Pharos" / "queues" / "regions" / "mining-ops.md"; q.write_text("- [ ] `q:MN-2026-09-01-X-1` r | q:MN-2026-09-01-X-1\n")
    world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    ok = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {"file_path": str(q), "content": q.read_text() + "  - note: from CARD\n"}}, world["env"])
    assert ok is None
    bad = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {"file_path": str(q), "content": q.read_text() + "  - free prose\n"}}, world["env"])
    assert decision(bad) == "deny"

def test_clone_remove_keeps_side_branches_and_files_in_untracked_dirs(tmp_path):
    src = tmp_path / "src"; src.mkdir(); subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "a.txt").write_text("a\n"); (src / "scratch").mkdir(); (src / "scratch" / "old.txt").write_text("o\n")
    subprocess.run(["git", "-C", str(src), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one"], check=True)
    env = dict(os.environ, GEDAECHTNIS_WORKTREES=str(tmp_path / "wts"), GEDAECHTNIS_NO_TRASH="1")
    wt = Path(__file__).resolve().parents[1] / "hooks" / "worktree.py"
    p = subprocess.run([sys.executable, str(wt), "create"], input=json.dumps({"cwd": str(src), "name": "side"}), capture_output=True, text=True, env=env)
    clone = Path(p.stdout.strip().splitlines()[-1])
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-b", "side-branch"], check=True)
    (clone / "b.txt").write_text("b\n"); subprocess.run(["git", "-C", str(clone), "add", "b.txt"], check=True)
    subprocess.run(["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "side"], check=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-"], check=True)
    p = subprocess.run([sys.executable, str(wt), "remove"], input=json.dumps({"cwd": str(src), "worktree_path": str(clone)}), capture_output=True, text=True, env=env)
    refs = subprocess.run(["git", "-C", str(src), "for-each-ref", "--format=%(refname)", "refs/gedaechtnis/"], capture_output=True, text=True).stdout
    assert re.search(r"refs/gedaechtnis/side-[0-9-]+/branches/side-branch", refs), refs
    p = subprocess.run([sys.executable, str(wt), "create"], input=json.dumps({"cwd": str(src), "name": "dirt"}), capture_output=True, text=True, env=env)
    clone2 = Path(p.stdout.strip().splitlines()[-1]); (clone2 / "scratch" / "new.txt").write_text("unique\n")
    p = subprocess.run([sys.executable, str(wt), "remove"], input=json.dumps({"cwd": str(src), "worktree_path": str(clone2)}), capture_output=True, text=True, env=env)
    assert clone2.exists() and "unique to the clone" in p.stderr


def test_every_redirect_spelling_is_seen_by_the_partition_door(world):
    v = world["vault"]; world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    for form in ("1>", "&>", ">|", "2>", "1>>", "&>>"):
        cmd = f"echo x {form} {v}/Speculum/Position.md"
        assert decision(bash(world, cmd)) == "deny", form
    assert decision(bash(world, f"echo x >|{v}/Speculum/Position.md")) == "deny"
    assert bash(world, f"echo x 2>&1 | grep y") is None                                   # fd dup is not a file write
    assert bash(world, f"echo '- 2026-09-09 CARD x' 1>> {v}/Speculum/Inbox.md") is None    # append to a shared surface, any spelling


# ------------------------------------------------ council iteration-2 findings (Melchior, Caspar), fixed ----
def test_mv_out_of_the_vault_asks(world):
    v = world["vault"]
    assert decision(bash(world, f"mv {v}/Speculum/Canon.md /tmp/")) == "ask"
    assert bash(world, f"mv /tmp/x.md {v}/Mnemosyne/UkrainianCard/notes.md") is None      # into own lane: fine

def test_fleet_roster_is_append_only_for_everyone(world):
    v = world["vault"]; r = v / "Global" / "fleet-roster.md"; r.write_text("```fleet-roster\nlane: CURSUS\nrepo: Projects/x\n```\n")
    world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    ok = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {"file_path": str(r), "content": r.read_text() + "path: Pharos/new\n"}}, world["env"])
    assert ok is None
    bad = run("gate.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {"file_path": str(r), "content": "lane: CARD\nrepo: mine\n"}}, world["env"])
    assert decision(bad) == "deny"                                                            # a rewrite, even though Global/ is declared

def test_git_rm_cached_does_not_ask(world):
    v = world["vault"]
    assert bash(world, f"git -C {v} rm --cached -- x.md; S=$(git -C {v} diff --cached --name-only); [ \"$S\" = x.md ] || exit 9; git -C {v} commit -F /tmp/m") is None

def test_foreign_shared_append_is_committed_by_the_chore(world):
    v = world["vault"]; q = v / "Pharos" / "queues" / "regions" / "mining-ops.md"
    q.write_text("- [ ] `q:MN-2026-09-01-X-1` r | q:MN-2026-09-01-X-1\n")
    subprocess.run(["git", "-C", str(v), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(v), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"], check=True)
    q.write_text(q.read_text() + "- [ ] `q:MN-2026-09-09-NEW-1` from CARD | q:MN-2026-09-09-NEW-1\n")
    res = run("chore.py", "write", {"cwd": str(world["repo"]), "tool_name": "Write", "session_id": "t", "tool_input": {"file_path": str(q), "content": q.read_text()}}, world["env"])
    assert "committed as atlas@local" in res["hookSpecificOutput"]["additionalContext"]
    log = subprocess.run(["git", "-C", str(v), "log", "-1", "--format=%an %s"], capture_output=True, text=True).stdout
    assert log.startswith("atlas queue: append by CARD")

def test_in_partition_bash_write_is_logged_as_ok(world):
    v = world["vault"]; bash(world, f"echo x >> {v}/Mnemosyne/UkrainianCard/Position.md")
    assert "ok\tCARD\tMnemosyne/UkrainianCard/Position.md\tbash=redirect-append" in (world["state"] / "partition.log").read_text()


# ------------------------------------------------ council iteration-2 findings (Balthasar), fixed ----
def test_flags_inside_the_message_are_not_flags(world):
    v = world["vault"]
    assert bash(world, f"git -C {v} commit -m 'never use -a or --amend here' -- Global/Errata.md") is None
    assert bash(world, f'git -C {v} commit -m "the -am trap" -- Global/Errata.md') is None

def test_vault_cwd_bash_write_is_the_owners_hand(world):
    world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    v = world["vault"]
    assert bash(world, f"echo x >> {v}/Speculum/Position.md", cwd=str(v)) is None

def test_compact_does_not_restamp_vault_head(world):
    import hashlib
    run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s1", "source": "startup"}, world["env"])
    key = hashlib.sha1(str(world["repo"]).encode()).hexdigest()[:10]
    f = world["state"] / f"session-start-{key}.json"; j = json.loads(f.read_text()); j["vault_head"] = "0" * 40; f.write_text(json.dumps(j))
    run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s1", "source": "compact"}, world["env"])
    assert json.loads(f.read_text())["vault_head"] == "0" * 40
    run("session_start.py", "", {"cwd": str(world["repo"]), "session_id": "s2", "source": "startup"}, world["env"])
    assert json.loads(f.read_text())["vault_head"] != "0" * 40

def test_clone_is_not_unique_when_only_the_source_moved_on(tmp_path):
    src = tmp_path / "src"; src.mkdir(); subprocess.run(["git", "init", "-q", str(src)], check=True)
    (src / "a.txt").write_text("a\n"); (src / "log.txt").write_text("1\n")
    subprocess.run(["git", "-C", str(src), "add", "a.txt", "log.txt"], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one"], check=True)
    (src / "log.txt").write_text("1\n2\n")                                        # dirty in the source at clone time
    env = dict(os.environ, GEDAECHTNIS_WORKTREES=str(tmp_path / "wts"), GEDAECHTNIS_NO_TRASH="1")
    wt = Path(__file__).resolve().parents[1] / "hooks" / "worktree.py"
    p = subprocess.run([sys.executable, str(wt), "create"], input=json.dumps({"cwd": str(src), "name": "x"}), capture_output=True, text=True, env=env)
    clone = Path(p.stdout.strip().splitlines()[-1])
    (src / "log.txt").write_text("1\n2\n3\n")                                     # the SOURCE moves on after cloning
    p = subprocess.run([sys.executable, str(wt), "remove"], input=json.dumps({"cwd": str(src), "worktree_path": str(clone)}), capture_output=True, text=True, env=env)
    assert not clone.exists(), p.stderr                                            # nothing unique to the clone: removed


# ------------------------------------------------ council closure round (Melchior), fixed ----
def test_git_rm_without_cached_in_the_vault_asks(world):
    v = world["vault"]
    assert decision(bash(world, f"git -C {v} rm -r -- Mnemosyne/UkrainianCard")) == "ask"
    assert bash(world, f"git -C {v} rm --cached -- x.md; S=$(git -C {v} diff --cached --name-only); [ \"$S\" = x.md ] || exit 9; git -C {v} commit -F /tmp/m") is None

def test_mv_out_asks_from_the_vault_cwd_too(world):
    v = world["vault"]
    assert decision(bash(world, f"mv {v}/Speculum/Canon.md /tmp/", cwd=str(v))) == "ask"

def test_roster_rewrite_via_sed_is_denied(world):
    v = world["vault"]; (v / "Global" / "fleet-roster.md").write_text("lane: CURSUS\n")
    world["state"].mkdir(parents=True, exist_ok=True); (world["state"] / "partition.mode").write_text("deny")
    assert decision(bash(world, f"sed -i '' 's/CURSUS/CARD/' {v}/Global/fleet-roster.md")) == "deny"
    assert bash(world, f"echo 'path: Pharos/new' >> {v}/Global/fleet-roster.md") is None
