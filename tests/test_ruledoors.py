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
    readme = (HOOKS.parent / "README.md").read_text(encoding="utf-8")
    table = readme[readme.index("## The doors"):readme.index("## What this plugin runs")]
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
