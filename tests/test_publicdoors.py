"""PUBLICDOORS-1 — four doors written for one working style ship OFF; a profile turns them on.

The four: `open` of a local copy of an artifact-store page (`artifact_open_door`), a Markdown
verdict page (`judge_md_door`), a queue row whose two ids disagree (`row_identity_door`) and a
marker that differs from its roster row (`marker_roster_door`). `"profile": "atlas"` in
config.json turns all four on; a key set by hand in the `limits` object wins over the profile in
both directions. Each behaviour has a positive and a negative control, and the wiring half drives
`gate.py` as a subprocess, the way Claude Code calls it.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
sys.path.insert(0, str(HOOKS))

import limits  # noqa: E402

FOUR = ("artifact_open_door", "judge_md_door", "row_identity_door", "marker_roster_door")
JUDGE_MD = "# Verdict page — pick one per item\n\n- [ ] Option A\n- [ ] Option B\n- [ ] Option C\n"
STORE_PAGE = '<!doctype html>\n<meta charset="utf-8">\n<body data-answer-store="x">page</body>\n'


@pytest.fixture
def w(tmp_path):
    vault = tmp_path / "Vault"
    (vault / "Global").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    home = tmp_path / "home"
    repo = home / "repo"; repo.mkdir(parents=True)
    (repo / ".atlas-lane").write_text("lane: CURSUS\npath: Speculum/\npath: Global/\n")
    state = tmp_path / "state"; state.mkdir()
    cfg = tmp_path / "config.json"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user-memory.md"), HOME=str(home),
               GEDAECHTNIS_CONFIG=str(cfg), GEDAECHTNIS_TODAY="2026-10-01")
    for k in ("GEDAECHTNIS_LIMITS", "CLAUDE_PROJECT_DIR", "GEDAECHTNIS_PROFILE"):
        env.pop(k, None)
    return dict(vault=vault, repo=repo, state=state, cfg=cfg, env=env)


def configure(w, **keys):
    w["cfg"].write_text(json.dumps(dict({"topology": {"non_region_tops": ["Global"]}}, **keys)))


def read_limits(w, extra_env=None):
    """What a fresh hook process reads: the four door values, the profile, the problems."""
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import limits; "
            "print(json.dumps([{k: limits.get(k) for k in limits.PROFILE_DOORS}, limits.profile(), limits.problems()]))")
    p = subprocess.run([sys.executable, "-c", code, str(HOOKS)], capture_output=True, text=True,
                       env=dict(w["env"], **(extra_env or {})), timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def hook(w, which, tool, tool_input):
    p = subprocess.run([sys.executable, str(HOOKS / "gate.py"), which],
                       input=json.dumps({"cwd": str(w["repo"]), "tool_name": tool, "session_id": "pd",
                                         "tool_input": tool_input}),
                       capture_output=True, text=True, env=w["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    return json.loads(out)["hookSpecificOutput"] if out else {}


# ---- the shipped values ---------------------------------------------------------------------------

def test_the_four_doors_ship_off_in_the_file_and_in_the_fallback_table():
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    assert limits.PROFILE_DOORS == FOUR
    for k in FOUR:
        assert shipped[k] is False, k
        assert limits.DEFAULTS[k] is False, k
        assert shipped["_key_class"][k] == "live", k
    assert limits.PROFILES["public"] == {}
    assert limits.PROFILES["atlas"] == {k: True for k in FOUR}


def test_the_other_rule_doors_still_ship_on():
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    for k in ("secret_door", "html_head_door", "launch_pin_door", "kill_door", "kernel_entry_door"):
        assert shipped[k] is True, k


# ---- the profile layer ----------------------------------------------------------------------------

def test_NEGATIVE_no_profile_named_is_public_and_the_four_are_off(w):
    configure(w)
    doors, name, problems = read_limits(w)
    assert name == "public" and doors == {k: False for k in FOUR} and problems == []


def test_NEGATIVE_no_config_file_at_all_is_public(w):
    doors, name, _ = read_limits(w)
    assert name == "public" and not any(doors.values())


def test_POSITIVE_profile_atlas_turns_all_four_on_in_one_line(w):
    configure(w, profile="atlas")
    doors, name, problems = read_limits(w)
    assert name == "atlas" and doors == {k: True for k in FOUR} and problems == []


def test_a_key_set_by_hand_wins_over_the_profile_in_both_directions(w):
    configure(w, profile="atlas", limits={"judge_md_door": False})
    doors, _, _ = read_limits(w)
    assert doors["judge_md_door"] is False and doors["row_identity_door"] is True
    configure(w, limits={"row_identity_door": True})
    doors, name, _ = read_limits(w)
    assert name == "public" and doors["row_identity_door"] is True and doors["judge_md_door"] is False


def test_an_unknown_profile_is_public_and_is_named(w):
    configure(w, profile="atlsa")
    doors, name, problems = read_limits(w)
    assert name == "public" and not any(doors.values())
    assert len(problems) == 1 and "atlsa" in problems[0] and "public" in problems[0]


def test_the_environment_variable_wins_over_the_file(w):
    configure(w)
    doors, name, _ = read_limits(w, {"GEDAECHTNIS_PROFILE": "Atlas"})
    assert name == "atlas" and all(doors.values())
    configure(w, profile="atlas")
    doors, name, _ = read_limits(w, {"GEDAECHTNIS_PROFILE": "public"})
    assert name == "public" and not any(doors.values())


def test_a_deny_date_on_a_door_that_is_off_is_reported_and_silent_once_the_door_is_on(w):
    configure(w, limits={"judge_md_deny_from": "2026-09-30"})
    _, _, problems = read_limits(w)
    assert len(problems) == 1 and "judge_md_deny_from" in problems[0] and '"profile": "atlas"' in problems[0]
    assert "under the `public` profile" in problems[0]
    configure(w, profile="atlas", limits={"judge_md_deny_from": "2026-09-30"})
    assert read_limits(w)[2] == []
    configure(w, limits={"judge_md_deny_from": ""})
    assert read_limits(w)[2] == []


def test_the_line_for_a_door_switched_off_by_hand_under_atlas_names_the_key_not_the_profile(w):
    configure(w, profile="atlas", limits={"judge_md_door": False, "judge_md_deny_from": "2026-09-30"})
    _, name, problems = read_limits(w)
    assert name == "atlas" and len(problems) == 1
    assert "`judge_md_door` is false" in problems[0] and "under the" not in problems[0]
    assert "remove that key or set it to true" in problems[0]
    # Under public, removing the key would leave the door off: only "set it to true" is offered.
    configure(w, limits={"judge_md_door": False, "judge_md_deny_from": "2026-09-30"})
    _, name, problems = read_limits(w)
    assert name == "public" and len(problems) == 1
    assert "set it to true" in problems[0] and "remove that key" not in problems[0]


def test_the_line_names_the_profile_in_force_when_the_environment_masks_the_file(w):
    configure(w, profile="atlas", limits={"row_identity_deny_from": "2026-09-30"})
    _, name, problems = read_limits(w, {"GEDAECHTNIS_PROFILE": "public"})
    assert name == "public" and len(problems) == 1 and "GEDAECHTNIS_PROFILE" in problems[0]


@pytest.mark.parametrize("value", [0, False, [], "", "  ", 1, True, ["atlas"], {"a": 1}, None])
def test_a_profile_key_that_is_there_and_unusable_is_public_and_named(w, value):
    configure(w, profile=value)
    doors, name, problems = read_limits(w)
    assert name == "public" and not any(doors.values())
    assert len(problems) == 1 and "`profile` is" in problems[0], problems


def test_configure_show_names_the_profile_as_the_source_and_shipped_without_one(w):
    tool = str(PLUGIN / "tools" / "configure.py")
    def show():
        p = subprocess.run([sys.executable, tool, "show"], capture_output=True, text=True, env=w["env"], timeout=30)
        assert p.returncode == 0, p.stderr
        return {ln.split(" = ")[0]: ln for ln in p.stdout.splitlines() if " = " in ln}
    configure(w, profile="atlas", limits={"row_identity_door": False})
    rows = show()
    assert "judge_md_door = true  [profile:atlas;" in rows["judge_md_door"]
    assert "row_identity_door = false  [config;" in rows["row_identity_door"]
    assert "secret_door = true  [shipped;" in rows["secret_door"]
    configure(w)
    assert "judge_md_door = false  [shipped;" in show()["judge_md_door"]


# ---- wiring: gate.py ------------------------------------------------------------------------------

def test_WIRING_NEGATIVE_public_a_markdown_verdict_page_is_written_without_a_word(w):
    configure(w, limits={"judge_md_deny_from": "2026-09-30"})
    res = hook(w, "write", "Write", {"file_path": str(w["repo"] / "verdict.md"), "content": JUDGE_MD})
    assert res.get("permissionDecision") != "deny"
    assert "JUDGE-MD" not in (res.get("additionalContext") or "")


def test_WIRING_POSITIVE_atlas_the_same_write_is_refused_past_its_date(w):
    configure(w, profile="atlas", limits={"judge_md_deny_from": "2026-09-30"})
    res = hook(w, "write", "Write", {"file_path": str(w["repo"] / "verdict.md"), "content": JUDGE_MD})
    assert res.get("permissionDecision") == "deny" and "JUDGE-MD" in res.get("permissionDecisionReason", "")


def test_WIRING_NEGATIVE_public_open_of_a_store_page_runs(w):
    configure(w)
    page = w["repo"] / "page.html"; page.write_text(STORE_PAGE)
    res = hook(w, "bash", "Bash", {"command": f"open {page}"})
    assert res.get("permissionDecision") != "deny"
    assert "ARTIFACT" not in (res.get("additionalContext") or "")


def test_WIRING_POSITIVE_atlas_open_of_a_store_page_is_refused(w):
    configure(w, profile="atlas")
    page = w["repo"] / "page.html"; page.write_text(STORE_PAGE)
    res = hook(w, "bash", "Bash", {"command": f"open {page}"})
    assert res.get("permissionDecision") == "deny" and "ARTIFACT" in res.get("permissionDecisionReason", "")


def test_WIRING_the_session_facts_line_lists_the_three_rule_doors_as_off_under_public(w):
    configure(w)
    code = "import sys; sys.path.insert(0, sys.argv[1]); import ruledoors; print(ruledoors.facts_line())"
    p = subprocess.run([sys.executable, "-c", code, str(HOOKS)], capture_output=True, text=True, env=w["env"], timeout=30)
    assert p.returncode == 0, p.stderr
    off = p.stdout.split("off ", 1)[1]
    for d in ("judge_md", "row_identity", "marker_roster"):
        assert d in off
    assert "secret" not in off
    assert "Profile `public`; artifact-open door off." in p.stdout
    configure(w, profile="atlas")
    p = subprocess.run([sys.executable, "-c", code, str(HOOKS)], capture_output=True, text=True, env=w["env"], timeout=30)
    assert "Profile `atlas`; artifact-open door on." in p.stdout and "off " not in p.stdout
