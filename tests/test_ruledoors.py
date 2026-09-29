"""DOORS-2 — the rule doors (`hooks/ruledoors.py`): six Boot-file rules a script can decide.

Two halves. The UNIT half pins each door's predicate on its own — one positive and one negative per
door, so a mutation that blanks one predicate reddens the test that names it. The WIRING half drives
`gate.py` as a subprocess, the way Claude Code calls it, and asserts the JSON the harness would act
on: WARN is a note and the call runs; a past `<door>_deny_from` date refuses inside a marked repo
and becomes a note outside one; a clean write says nothing at all.

Every secret below is ASSEMBLED at run time, so this file never carries one for the door (or a
host's secret scanner) to find.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

import ruledoors as rd  # noqa: E402

ANT_KEY = "sk-" + "ant-" + "api03-" + "Q7x" * 12
GH_TOKEN = "gh" + "p_" + "A1b2C3d4E5" * 4
CARD_OK = " ".join(["4111"] + ["1111"] * 3)         # the public Visa test number: Luhn-valid
CARD_BAD = " ".join(["4111"] + ["1111"] * 2 + ["1112"])  # one digit off: Luhn-invalid
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"

PAGE_OK = '<!doctype html>\n<meta charset="utf-8">\n<html><head><title>t</title></head><body>x</body></html>\n'
PAGE_NO_DOCTYPE = '<html>\n<meta charset="utf-8">\n<head></head><body>x</body></html>\n'
PAGE_LATE_META = "<!doctype html>\n<html><head>\n" + "<!-- " + "p" * 1100 + " -->\n" + '<meta charset="utf-8">\n</head></html>\n'
FRAGMENT = '<div class="card">no head of its own</div>\n'

JUDGE_MD = "# Verdict page — pick one per item\n\n- [ ] Option A\n- [ ] Option B\n- [ ] Option C\n"
REPORT_MD = "# Report\n\n- [ ] `q:CU-2026-09-24-DOORS-2a` a queue row | q:CU-2026-09-24-DOORS-2a\n- done\n"


# ---- unit: secret ---------------------------------------------------------------------------------

def test_secret_POSITIVE_an_api_key_a_token_a_pem_and_a_card_are_each_named():
    assert rd.secret_hits(f'KEY = "{ANT_KEY}"') == ["an Anthropic API key"]
    assert "a GitHub token" in rd.secret_hits(f"token: {GH_TOKEN}")
    assert rd.secret_hits(PEM) == ["a private key"]
    assert rd.secret_hits(f"card {CARD_OK}") == ["a payment-card number"]


def test_secret_NEGATIVE_placeholders_prose_and_a_luhn_invalid_number_pass():
    assert rd.secret_hits("API" + "_KEY = " + '"' + "sk-" + "ant-" + "x" * 26 + '"') == []
    assert rd.secret_hits("the key lives in .env and is read with os.environ") == []
    assert rd.secret_hits(f"order {CARD_BAD}") == []
    assert rd.secret_hits("AKIA" + "IOSFODNN7" + "EXAMPLE") == []
    assert rd.secret_hits('SENTINEL = "' + "sk-" + "ant-oat-SENTINEL-TOKEN-DO-NOT-LEAK-0123456789" + '"') == []


def test_secret_only_ADDED_lines_are_judged_and_a_dotenv_file_is_the_right_home(tmp_path):
    p = tmp_path / "app.py"
    have = f'KEY = "{ANT_KEY}"\n'
    assert rd.check_secret(p, have, have + "x = 1\n") is None           # already there: not this write's
    assert rd.check_secret(p, "", have) is not None
    assert rd.check_secret(tmp_path / ".env", "", have) is None
    assert rd.check_secret(tmp_path / "prod.env", "", have) is None


def test_secret_words_never_repeat_the_value(tmp_path):
    w = rd.check_secret(tmp_path / "app.py", "", f'KEY = "{ANT_KEY}"\n')
    assert ANT_KEY not in w and "line 1" in w and "q:CU-2026-09-24-DOORS-2a" in w


# ---- unit: html_head ------------------------------------------------------------------------------

def test_html_head_POSITIVE_missing_doctype_and_late_meta_are_named(tmp_path):
    p = tmp_path / "page.html"
    w = rd.check_html_head(p, "", PAGE_NO_DOCTYPE)
    assert w and "line 1 is not `<!doctype html>`" in w
    w = rd.check_html_head(p, "", PAGE_LATE_META)
    assert w and "past the first 1024" in w and "line 2" in w


def test_html_head_NEGATIVE_a_correct_page_a_fragment_and_a_non_html_file_pass(tmp_path):
    assert rd.check_html_head(tmp_path / "page.html", "", PAGE_OK) is None
    assert rd.check_html_head(tmp_path / "part.html", "", FRAGMENT) is None
    assert rd.check_html_head(tmp_path / "page.txt", "", PAGE_NO_DOCTYPE) is None


def test_html_head_a_test_fixture_and_an_artifact_page_are_not_judged(tmp_path):
    assert rd.check_html_head(tmp_path / "fixtures" / "p.html", "", PAGE_NO_DOCTYPE) is None
    assert rd.check_html_head(tmp_path / "site-v10-artifact.html", "", PAGE_NO_DOCTYPE) is None
    assert rd.check_html_head(tmp_path / "site-v10.html", "", PAGE_NO_DOCTYPE) is not None


# ---- unit: judge_md -------------------------------------------------------------------------------

def test_judge_md_POSITIVE_a_verdict_heading_with_unmarked_choices(tmp_path):
    w = rd.check_judge_md(tmp_path / "notes.md", "Write", JUDGE_MD)
    assert w and "3 unmarked choices" in w


def test_judge_md_POSITIVE_the_name_alone_with_yn_blanks(tmp_path):
    body = "# Round 2\n\n1. Keep the blue? (Y/N)\n2. Drop the legend? (Y/N)\n"
    assert rd.check_judge_md(tmp_path / "owner-verdicts.md", "Write", body)


def test_judge_md_NEGATIVE_queue_rows_one_choice_or_no_judge_word(tmp_path):
    assert rd.check_judge_md(tmp_path / "verdict.md", "Write", REPORT_MD) is None     # queue rows are not ballots
    assert rd.check_judge_md(tmp_path / "verdict.md", "Write", "# Verdict\n\n- [ ] only one\n") is None
    assert rd.check_judge_md(tmp_path / "todo.md", "Write", "# Todo\n\n- [ ] a\n- [ ] b\n") is None
    assert rd.check_judge_md(tmp_path / "verdict.md", "Edit", JUDGE_MD) is None       # an Edit is not a new page


# ---- unit: row_identity ---------------------------------------------------------------------------

def test_row_identity_POSITIVE_anchor_and_field_disagree_or_two_fields():
    assert "anchor says" in rd.row_problem("- [ ] `q:CU-2026-09-24-A-1` text | q:CU-2026-09-24-B-1")
    assert "2 different" in rd.row_problem("- [ ] text | q:CU-2026-09-24-A-1 | q:CU-2026-09-24-B-1")


def test_row_identity_NEGATIVE_agreeing_one_sided_citing_and_non_row_lines():
    assert rd.row_problem("- [ ] `q:CU-2026-09-24-A-1` text | q:CU-2026-09-24-A-1") is None
    assert rd.row_problem("- [x] `q:CU-2026-09-24-A-1` text only anchored") is None
    assert rd.row_problem("- [ ] text cites `q:CU-2026-09-24-B-1` | q:CU-2026-09-24-A-1") is None
    assert rd.row_problem("prose | q:CU-2026-09-24-A-1 and | q:CU-2026-09-24-B-1") is None


# ---- unit: marker_roster --------------------------------------------------------------------------

ROSTER = "# roster\n\n```fleet-roster\nlane: CURSUS\nrepo: repo\npath: Speculum/\npath: Global/\n```\n"


def test_marker_roster_reads_the_fenced_block_only():
    r = rd.parse_roster_text("lane: GHOST\npath: X/\n" + ROSTER)
    assert set(r) == {"CURSUS"} and r["CURSUS"]["paths"] == ["Speculum/", "Global/"]
    assert rd.parse_marker_text("# path: no\nlane: CUR SUS\npath: A/\n") == ("CURSUS", ["A/"])


# ---- wiring: gate.py as a subprocess --------------------------------------------------------------

@pytest.fixture
def w(tmp_path):
    vault = tmp_path / "Vault"
    (vault / "Global").mkdir(parents=True)
    (vault / "Global" / "fleet-roster.md").write_text(ROSTER)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    home = tmp_path / "home"
    repo = home / "repo"; repo.mkdir(parents=True)
    (repo / ".atlas-lane").write_text("lane: CURSUS\npath: Speculum/\npath: Global/\n")
    loose = tmp_path / "loose"; loose.mkdir()                 # no marker: out of scope
    state = tmp_path / "state"; state.mkdir()
    cfg = tmp_path / "config.json"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user-memory.md"), HOME=str(home),
               GEDAECHTNIS_CONFIG=str(cfg), GEDAECHTNIS_TODAY="2026-09-26")
    for k in ("GEDAECHTNIS_LIMITS", "CLAUDE_PROJECT_DIR"):
        env.pop(k, None)
    d = dict(vault=vault, repo=repo, loose=loose, state=state, cfg=cfg, env=env, home=home)
    set_limits(d, {})
    return d


def set_limits(w, lim):
    w["cfg"].write_text(json.dumps({"topology": {"non_region_tops": ["Global"]}, "limits": lim}))


def hook(w, which, tool, tool_input, cwd):
    p = subprocess.run([sys.executable, str(HOOKS / "gate.py"), which],
                       input=json.dumps({"cwd": str(cwd), "tool_name": tool, "session_id": "rd",
                                         "tool_input": tool_input}),
                       capture_output=True, text=True, env=w["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out)["hookSpecificOutput"] if out else {}


def write(w, path, content, cwd=None):
    return hook(w, "write", "Write", {"file_path": str(path), "content": content}, cwd or w["repo"])


def note(res):
    return res.get("additionalContext") or ""


def denied(res):
    return res.get("permissionDecision") == "deny"


def ruledoors_log(w):
    f = w["state"] / "ruledoors.log"
    return f.read_text() if f.is_file() else ""


def test_WIRING_warn_is_a_note_and_the_write_runs(w):
    res = write(w, w["repo"] / "app.py", f'KEY = "{ANT_KEY}"\n')
    assert not denied(res) and "SECRET:" in note(res) and note(res).startswith("WARN")
    assert "warn\tsecret" in ruledoors_log(w)


def test_WIRING_a_past_deny_date_refuses_inside_a_marked_repo(w):
    set_limits(w, {"secret_deny_from": "2026-09-01"})
    res = write(w, w["repo"] / "app.py", f'KEY = "{ANT_KEY}"\n')
    assert denied(res) and "SECRET:" in res.get("permissionDecisionReason", "")
    assert "deny\tsecret" in ruledoors_log(w)


def test_WIRING_a_future_deny_date_still_warns(w):
    set_limits(w, {"secret_deny_from": "2026-12-01"})
    res = write(w, w["repo"] / "app.py", f'KEY = "{ANT_KEY}"\n')
    assert not denied(res) and "SECRET:" in note(res)


def test_WIRING_outside_a_marked_repo_the_refusal_is_a_note(w):
    set_limits(w, {"secret_deny_from": "2026-09-01"})
    res = write(w, w["loose"] / "app.py", f'KEY = "{ANT_KEY}"\n', cwd=w["loose"])
    assert not denied(res) and "not enforced here" in note(res) and "SECRET:" in note(res)


def test_WIRING_NEGATIVE_a_clean_write_says_nothing(w):
    set_limits(w, {d + "_deny_from": "2026-09-01" for d in rd.RULES})
    assert write(w, w["repo"] / "app.py", "x = 1\n") == {}
    assert write(w, w["repo"] / "page.html", PAGE_OK) == {}


def test_WIRING_a_door_switched_off_is_silent(w):
    set_limits(w, {"secret_door": False})
    assert write(w, w["repo"] / "app.py", f'KEY = "{ANT_KEY}"\n') == {}


def test_WIRING_html_head_refuses_in_deny(w):
    set_limits(w, {"html_head_deny_from": "2026-09-01"})
    res = write(w, w["repo"] / "page.html", PAGE_NO_DOCTYPE)
    assert denied(res) and "HTML-HEAD" in res["permissionDecisionReason"]


def test_WIRING_judge_md_warns(w):
    res = write(w, w["repo"] / "choices.md", JUDGE_MD)
    assert "JUDGE-MD" in note(res) and not denied(res)


def test_WIRING_row_identity_refuses_a_vault_queue_row_in_deny(w):
    set_limits(w, {"row_identity_deny_from": "2026-09-01"})
    q = w["vault"] / "Global" / "queue.md"
    res = write(w, q, "- [ ] `q:CU-2026-09-24-A-1` x | q:CU-2026-09-24-B-1\n")
    assert denied(res) and "ROW-IDENTITY" in res["permissionDecisionReason"]


def test_WIRING_marker_roster_is_warn_only_even_with_a_deny_date(w):
    set_limits(w, {"marker_roster_deny_from": "2026-09-01"})
    res = hook(w, "write", "Edit", {"file_path": str(w["repo"] / ".atlas-lane"),
                                    "old_string": "path: Global/\n", "new_string": "path: Global/\npath: Vita/\n"},
               w["repo"])
    assert not denied(res) and "MARKER-ROSTER" in note(res) and "only in the marker: `Vita/`" in note(res)


def test_WIRING_marker_roster_NEGATIVE_a_matching_marker_is_silent(w):
    res = hook(w, "write", "Edit", {"file_path": str(w["repo"] / ".atlas-lane"),
                                    "old_string": "path: Global/\n", "new_string": "path: Global/\n# a comment\n"},
               w["repo"])
    assert "MARKER-ROSTER" not in note(res)


def test_WIRING_a_roster_edit_is_checked_against_the_lanes_marker(w):
    rpath = w["vault"] / "Global" / "fleet-roster.md"
    res = hook(w, "write", "Edit", {"file_path": str(rpath), "old_string": "path: Global/\n",
                                    "new_string": "path: Global/\npath: Limen/\n"}, w["repo"])
    assert "MARKER-ROSTER" in note(res) and "only in the roster: `Limen/`" in note(res)


def bash(w, cmd, cwd=None):
    return hook(w, "bash", "Bash", {"command": cmd}, cwd or w["repo"])


def test_WIRING_launch_pin_POSITIVE_warns_then_refuses(w):
    assert "LAUNCH-PIN" in note(bash(w, 'claude -p "hi" --model claude-opus-5-5'))
    set_limits(w, {"launch_pin_deny_from": "2026-09-01"})
    res = bash(w, 'claude -p "hi" --effort medium')
    assert denied(res) and "`--model`" in res["permissionDecisionReason"]
    assert "owner-ruling-mb1-routing-2026-08-05" in res["permissionDecisionReason"]


def test_WIRING_launch_pin_NEGATIVE_a_pinned_launch_and_a_subcommand_pass(w):
    set_limits(w, {"launch_pin_deny_from": "2026-09-01"})
    assert "LAUNCH-PIN" not in note(bash(w, 'claude -p "hi" --model=claude-opus-5-5 --effort medium'))
    assert not denied(bash(w, "claude plugin list"))
    assert not denied(bash(w, "echo claude is a word here"))


def test_WIRING_launch_pin_outside_scope_is_a_note(w):
    set_limits(w, {"launch_pin_deny_from": "2026-09-01"})
    res = bash(w, 'claude -p "hi"', cwd=w["loose"])
    assert not denied(res) and "not enforced here" in note(res)


def test_every_door_names_its_rule_and_its_id():
    for door, (title, rule, rid) in rd.RULES.items():
        text = rd.words(door, "x")
        assert text.startswith(title + ":") and rule in text and rid in text
        assert rid.startswith(("q:CU-", "owner-ruling-"))


def test_a_malformed_deny_date_is_WARN(w, monkeypatch):
    monkeypatch.setattr(rd.limits, "get", lambda k, d=None: "tomorrow" if k.endswith("_deny_from") else d)
    assert rd.mode("secret") == "warn"


def test_the_facts_line_names_every_door():
    line = rd.facts_line()
    assert all(d in line for d in rd.RULES)


def test_the_README_door_table_has_a_row_for_every_rule_door():
    """DERIVED from RULES: a door added to the module without a README row reddens here."""
    # The door table lives in docs/doors.md since the README was trimmed.
    table = (HOOKS.parent / "docs" / "doors.md").read_text(encoding="utf-8")
    rows = [ln for ln in table.splitlines() if ln.startswith("| ")]
    for door in rd.RULES:
        assert sum(f"`{door}_door" in ln for ln in rows) == 1, door


def test_launch_pin_sees_through_an_env_prefix_and_a_full_path():
    """Review finding: `env FOO=1 claude …` bypassed the door. NEGATIVE: the same launch pinned passes."""
    import shellread
    assert rd.check_launch("env FOO=1 claude -p 'hi' --model x", shellread.segments)
    assert rd.check_launch("env -u HOME FOO=1 /usr/local/bin/claude -p 'hi'", shellread.segments)
    assert rd.check_launch("env FOO=1 claude -p 'hi' --model x --effort low", shellread.segments) is None
    assert rd.check_launch("env FOO=1 python3 x.py", shellread.segments) is None


def test_WIRING_a_delete_door_WARN_keeps_the_launch_pin_note(w):
    """Review finding: the delete door's WARN branch printed its own note and dropped the others."""
    cfg = json.loads(w["cfg"].read_text()); cfg["delete_door"] = True; w["cfg"].write_text(json.dumps(cfg))
    target = w["repo"] / "junk.txt"; target.write_text("x")
    res = bash(w, f"rm {target} && claude -p 'hi' --model x")
    assert not denied(res) and "delete door" in note(res)      # a fresh state dir starts the WARN day
    assert "LAUNCH-PIN" in note(res)


# ---- LAUNCHPINFIX-1: a call that launches nothing passes, a real launch is still caught ----------
# The specimen: `ruledoors.log` 2026-09-27T14:59:50 warned on the line below. Command STRINGS only:
# `claude remote-control --help` hangs on this machine and is never run.
SPECIMEN = "claude --version; claude remote-control --help 2>&1 | head -40"


@pytest.mark.parametrize("cmd", [SPECIMEN, "claude --version", "claude -v", "claude --help", "claude -h",
                                 "claude plugin list", "claude plugins install x", "claude mcp list",
                                 "claude remote-control", "claude remote-control --name seat",
                                 "claude doctor", "command -v claude", "which claude"])
def test_LAUNCHPINFIX_NEGATIVE_a_call_that_launches_nothing_passes(cmd):
    import shellread
    assert rd.check_launch(cmd, shellread.segments) is None


@pytest.mark.parametrize("cmd", ['claude "hi"', 'claude -p "hi"', "claude", 'claude -p "what does --help do"',
                                 "claude -p 'explain claude --version'", 'timeout 600 claude -p "a --help b"',
                                 "nohup claude -p hi", "sudo claude -p hi", "timeout 60 env X=1 claude -p hi",
                                 'claude --version && claude -p "hi"'])
def test_LAUNCHPINFIX_POSITIVE_a_real_unpinned_launch_is_still_caught(cmd):
    import shellread
    assert "LAUNCH-PIN" in rd.check_launch(cmd, shellread.segments)


def test_LAUNCHPINFIX_WIRING_the_specimen_passes_silently_even_past_the_deny_date(w):
    set_limits(w, {"launch_pin_deny_from": "2026-09-01"})
    res = bash(w, SPECIMEN)
    assert not denied(res) and "LAUNCH-PIN" not in note(res)
    assert "launch_pin" not in ruledoors_log(w)
    res = bash(w, 'claude -p "hi"')
    assert denied(res) and "LAUNCH-PIN" in res["permissionDecisionReason"]


def test_LAUNCHPINFIX_WIRING_the_opt_in_flags_read_the_same_launches(w):
    """gate.rule_launch_model reads through ruledoors.launch_words: one reading for both doors."""
    w["env"]["GEDAECHTNIS_REQUIRE_LAUNCH_MODEL"] = "1"
    assert not denied(bash(w, "claude remote-control --help"))
    assert not denied(bash(w, "claude --version"))
    res = bash(w, "nohup claude -p hi")
    assert denied(res) and "pins `--model`" in res["permissionDecisionReason"]


# ---- LAUNCHPINFIX-1 round 2 (xhigh REJECT, 2026-09-28): only claude's OWN words count -------------

@pytest.mark.parametrize("cmd", ["claude -p <<'EOF'\nRun pytest -v --help\nEOF", 'claude -p "task" & git --version',
                                 'claude -p "x" > out.md  # see claude --help', "env --unset HOME claude -p hi",
                                 "env --chdir /tmp claude -p hi", "env - claude -p hi", 'x=$(claude -p "hi")',
                                 "timeout 600 sudo -u me claude -p hi", "if true; then claude -p hi; fi",
                                 "cat <<EOF | claude -p\nhi\nEOF"])
def test_LAUNCHPINFIX_R2_POSITIVE_help_in_other_words_or_behind_a_prefix_is_still_a_launch(cmd):
    import shellread
    assert "LAUNCH-PIN" in rd.check_launch(cmd, shellread.segments)


@pytest.mark.parametrize("cmd", ["claude -p --help", "claude --debug --version", "claude --verbose mcp list",
                                 "command -pv claude", "VAR=1 command -v claude", "builtin command -v claude",
                                 "git commit -m \"$(cat <<'EOF'\nrun claude -p hi\nEOF\n)\"",
                                 "claude -p hi --model x --effort high 2>/dev/null"])
def test_LAUNCHPINFIX_R2_NEGATIVE_claudes_own_help_a_lookup_and_a_pinned_launch_pass(cmd):
    import shellread
    assert rd.check_launch(cmd, shellread.segments) is None


def test_LAUNCHPINFIX_R2_strip_prefix_reads_env_long_options_and_a_bare_dash():
    import shellread
    assert shellread.strip_prefix("env --unset HOME git add -A").startswith("git ")
    assert shellread.strip_prefix("env --chdir /tmp git status").startswith("git ")
    assert shellread.strip_prefix("env - git status").startswith("git ")


def test_LAUNCHPINFIX_R2_a_lookup_after_a_background_job_and_a_redirect_target_named_like_a_flag():
    import shellread
    assert rd.check_launch("true & command -v claude", shellread.segments) is None
    assert "LAUNCH-PIN" in rd.check_launch('claude -p "hi" > --help', shellread.segments)


# ---- LAUNCHPINFIX-1 round 3 (xhigh round-2 REJECT, 2026-09-28) --------------------------------------
# Command STRINGS only; nothing here runs `claude`.

@pytest.mark.parametrize("cmd", ['(claude -p "task")& git --version', '(claude --version)& claude -p "task"',
                                 'command -v claude >/dev/null & claude -p "task"', 'command -v true & claude -p "task"',
                                 'open https://x.test/#a & claude -p "task"', 'echo $# & claude -p "task"',
                                 "time -p claude -p hi", "sudo --user me claude -p hi", "env -S 'claude -p hi'",
                                 "bash <<'EOF'\nclaude -p \"task\"\nEOF", "cat <<'EOF' | bash\nclaude -p \"task\"\nEOF",
                                 "cat > f <<EOF\n$(claude -p hi)\nEOF", "function f { claude -p hi; }"])
def test_LAUNCHPINFIX_R3_POSITIVE_a_glued_amp_a_lookup_a_mid_word_hash_and_a_prefix_hide_no_launch(cmd):
    import shellread
    assert "LAUNCH-PIN" in rd.check_launch(cmd, shellread.segments)


@pytest.mark.parametrize("cmd", ["git commit -F - <<'EOF'\n`claude -p \"hi\"` is still caught.\nEOF",
                                 "git commit -m \"$(cat <<'EOF'\nan unpinned `claude -p \"x\"` here\nEOF\n)\"",
                                 "gh pr create --body \"$(cat <<'EOF'\nsee `claude -p hi`\nEOF\n)\"",
                                 "cat > notes.md <<'EOF'\nRun `claude -p hi` now\nEOF",
                                 "command -p -v claude", "claude --settings s.json mcp list",
                                 "claude --verbose 2>/dev/null mcp list", "cat <<-EOF\n\tfoo; claude -p hi\n\tEOF",
                                 'claude -p "x"# --help'])      # a mid-word `#` is text: claude gets `x#` and `--help`
def test_LAUNCHPINFIX_R3_NEGATIVE_a_quoted_heredoc_body_a_lookup_and_a_settings_file_launch_nothing(cmd):
    import shellread
    assert rd.check_launch(cmd, shellread.segments) is None


def test_LAUNCHPINFIX_R3_a_thousand_deep_substitution_is_read_without_raising():
    import shellread
    deep = "echo " + "$(echo " * 1000 + "x" + ")" * 1000
    assert rd.check_launch(deep, shellread.segments) is None
    assert rd.check_launch("claude -p hi; " + deep, shellread.segments)


def test_LAUNCHPINFIX_R3_the_opt_in_rule_reads_nothing_with_both_flags_off_and_never_raises(monkeypatch):
    import gate
    calls, logged = [], []

    def boom(cmd, segments):
        calls.append(cmd)
        raise RecursionError("maximum recursion depth exceeded")
        yield                                                     # noqa: unreachable — a generator
    monkeypatch.setattr(rd, "launch_words", boom)
    monkeypatch.setattr(gate, "log", lambda kind, row: logged.append((kind, row)))
    for k in ("GEDAECHTNIS_REQUIRE_LAUNCH_MODEL", "GEDAECHTNIS_REQUIRE_LAUNCH_EFFORT"):
        monkeypatch.setenv(k, "0")
    assert gate.rule_launch_model('claude -p "hi"') is None and calls == []
    monkeypatch.setenv("GEDAECHTNIS_REQUIRE_LAUNCH_MODEL", "1")
    assert gate.rule_launch_model('claude -p "hi"') is None and calls
    assert any(k == "ruledoors" and "error" in r and "RecursionError" in r for k, r in logged)


def test_LAUNCHPINFIX_R3_WIRING_a_deep_substitution_costs_no_later_door(w):
    """The reviewer's case: with the opt-in flag on, a 1000-deep `$( )` must not skip the data door."""
    w["env"]["GEDAECHTNIS_REQUIRE_LAUNCH_MODEL"] = "1"
    deep = "echo " + "$(echo " * 1000 + "x" + ")" * 1000
    res = bash(w, 'sqlite3 anki_mining.db "DROP TABLE cards"; ' + deep)
    assert res.get("permissionDecision") == "ask"


# ---- LAUNCHPINFIX-1 round 4 (xhigh round-3 REJECT, 2026-09-28) --------------------------------------
# Command STRINGS only; nothing here runs `claude`. A substitution's parentheses cut no command, and a
# heredoc's body is cut out with the lines after it kept, so pins after `)"` are read.
_PIN = "--model claude-opus-5-5 --effort high"
_HD = "\"$(cat <<'EOF'\nReview the diff.\nEOF\n)\""


@pytest.mark.parametrize("cmd", [f"claude -p {_HD} {_PIN}", f"claude -p {_HD} {_PIN} > run.log 2>&1 &",
                                 f"nohup claude -p {_HD} {_PIN}", f"claude --append-system-prompt {_HD} -p hi {_PIN}",
                                 f"claude -p \"$(cat <<EOF\nReview $x.\nEOF\n)\" {_PIN}",
                                 f"claude --resume $(cat .sid) {_PIN}", f"claude -p $(cat prompt.md) {_PIN}",
                                 f"claude --settings <(echo '{{}}') {_PIN} -p hi",
                                 f"claude --mcp-config <(cat m.json) {_PIN} -p hi",
                                 "claude -p hi --model $(cat .model) --effort high",
                                 f"claude -p hi {_PIN} > >(tee run.log)", f"claude -p `sleep 1 & cat a.md` {_PIN}",
                                 f"claude -p $((1+2)) {_PIN}", "claude -p hi --model=$(cat .m) --effort=`cat .e`"])
def test_LAUNCHPINFIX_R4_NEGATIVE_a_substitution_or_heredoc_prompt_keeps_its_pins(cmd):
    import shellread
    assert rd.check_launch(cmd, shellread.segments) is None


@pytest.mark.parametrize("cmd", ["claude --version & claude -p \"$(cat <<'EOF'\nx\nEOF\n)\"",
                                 "sleep 1 & claude -p \"$(cat <<'EOF'\nx\nEOF\n)\"",
                                 "claude -p $(cat p) & claude --version", "claude -p -- --help"])
def test_LAUNCHPINFIX_R4_POSITIVE_an_unpinned_heredoc_launch_after_amp_and_help_after_dashdash(cmd):
    import shellread
    assert "LAUNCH-PIN" in rd.check_launch(cmd, shellread.segments)


def test_LAUNCHPINFIX_R4_env_split_string_reads_on_from_its_value():
    import shellread, killdoor
    for s in ("env --split-string kill -9 -1", "env -S kill -9 -1"):
        assert shellread.strip_prefix(s) == "kill -9 -1" and killdoor.check(s)
    assert shellread.strip_prefix("env --split-string git -C /v add -A") == "git -C /v add -A"
    assert shellread.strip_prefix("env -u HOME git add -A") == "git add -A"      # control: a value flag
    assert killdoor.check("env --split-string echo -9 -1") is None                # control: no kill


# ---- LAUNCHPINFIX-1 round 5 (xhigh round-4 REJECT, 2026-09-28) --------------------------------------
# Command STRINGS only. A `<<` inside quotes is text, not a heredoc; one that starts on a later line
# (inside `$(`) is still cut; with no end-word line after it, the lines are kept.
@pytest.mark.parametrize("cmd", [f"claude -p \"Fix where std::cout << value prints junk.\nKeep tests green.\" {_PIN}",
                                 f"claude -p \"Use 1 << 20 bytes.\nThen run the tests.\" {_PIN}",
                                 f"claude -p \"$(\ncat <<'EOF'\nDon't stop.\nEOF\n)\" {_PIN}",
                                 f"claude -p '$(' {_PIN}"])
def test_LAUNCHPINFIX_R5_NEGATIVE_a_quoted_shift_a_later_line_heredoc_and_a_quoted_dollar_paren_keep_pins(cmd):
    import shellread
    assert rd.check_launch(cmd, shellread.segments) is None


@pytest.mark.parametrize("cmd", ["claude --version & claude -p \"use x << y\nplease\"", "echo \\$(x & claude -p hi"])
def test_LAUNCHPINFIX_R5_POSITIVE_a_quoted_shift_and_an_escaped_dollar_paren_hide_no_launch(cmd):
    import shellread
    assert "LAUNCH-PIN" in rd.check_launch(cmd, shellread.segments)


def test_LAUNCHPINFIX_R5_strip_prefix_reads_a_quoted_split_string_as_it_does_today():
    import shellread
    assert shellread.strip_prefix("env -S 'kill -9 -1'") == "kill -9 -1'"   # the opening quote is consumed


def test_LAUNCHPINFIX_R5_drop_heredoc_bodies_cuts_only_a_body_the_shell_reads():
    d = rd._drop_heredoc_bodies
    assert d("a <<EOF\nb\nEOF\nc") == "a <<EOF\nc"                         # body and end word cut
    assert d("x\ny <<E\nz\nE") == "x\ny <<E"                                # a heredoc on a later line
    assert d("a <<EOF\nb\nc") == "a <<EOF\nb\nc"                           # no end-word line: kept
    assert d("a \"<<E\nb\nE\"") == "a \"<<E\nb\nE\""                       # inside quotes: text
    assert d("echo \"a << E\"\nclaude -p hi\nE") == "echo \"a << E\"\nclaude -p hi\nE"   # closed quotes too
    assert d("a \"`\ncat <<'E'\nit's\nE\n`\" b") == "a \"`\ncat <<'E'\n`\" b"   # backtick: fresh context
    assert d("x $((1 << 2))\n2\ny") == "x $((1 << 2))\n2\ny"               # arithmetic shift, no heredoc
    assert d("no heredoc\nhere") == "no heredoc\nhere"


def test_LAUNCHPINFIX_R5_an_empty_or_one_word_split_string_keeps_the_head_word():
    # round 4 optional 3, a regression against main in the git doors: `env -S ''` lost the head word
    import shellread, killdoor
    assert shellread.strip_prefix("env -S '' git add -A") == "git add -A"
    assert shellread.strip_prefix('env -S "" git add -A') == "git add -A"
    assert shellread.strip_prefix("env -S 'nohup' kill -9 -1") == "kill -9 -1"
    assert killdoor.check("env -S '' kill -9 -1") and killdoor.check("env -S 'nohup' kill -9 -1")
    assert shellread.strip_prefix("env -S 'echo' hi") == "echo' hi"            # control: not a prefix word
