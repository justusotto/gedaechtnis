"""DEFERDOOR-1 — a new line that defers work without naming a queue row gets one WARN note.

Positive control: a deferral phrase with no `q:` id is named with its line. Negative controls: the
same phrase WITH an id on the line is silent; the phrase in a test or fixture file is silent; code
(not a comment or docstring) in a `.py` file is silent; an unchanged line of an Edit is silent; the
door is silent while `deferral_door` is off. Everything runs the real PostToolUse chore in a
sandbox: state, config and vault are all under tmp_path.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
QID = "q:CU-2026-09-22-DEFERDOOR-1"


@pytest.fixture
def world(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"deferral_door": True}))
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (tmp_path / "Vault").mkdir()
    env = dict(os.environ, GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"), GEDAECHTNIS_CONFIG=str(cfg),
               GEDAECHTNIS_VAULT=str(tmp_path / "Vault"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"))
    for k in [k for k in env if k.startswith("GEDAECHTNIS_") and k not in (
            "GEDAECHTNIS_STATE_DIR", "GEDAECHTNIS_CONFIG", "GEDAECHTNIS_VAULT", "GEDAECHTNIS_USER_MEMORY")]:
        env.pop(k)
    return dict(repo=repo, cfg=cfg, env=env)


def write(w, rel, content=None, old=None, new=None, final=None):
    """Put `final` (or `content`) on disk, then run the chore with a Write or an Edit payload."""
    p = w["repo"] / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(final if final is not None else content)
    ti = {"file_path": str(p)}
    if content is not None:
        ti["content"] = content
    else:
        ti.update(old_string=old, new_string=new)
    payload = {"session_id": "t", "tool_name": "Write" if content is not None else "Edit",
               "cwd": str(w["repo"]), "tool_input": ti}
    r = subprocess.run([sys.executable, "-B", str(HOOKS / "chore.py"), "write"],
                       input=json.dumps(payload), capture_output=True, text=True, env=w["env"], timeout=30)
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return ""
    return json.loads(r.stdout)["hookSpecificOutput"].get("additionalContext", "")


def test_a_deferral_in_a_REPORT_with_no_row_is_named_with_its_line(world):
    out = write(world, "ROW-X.md", "# Report\n\nDone.\n\nThe examples are filed as their own row.\n")
    assert "Deferral without a queue row" in out, out
    assert "ROW-X.md:5: The examples are filed as their own row." in out, out


def test_the_same_line_WITH_a_queue_id_is_silent(world):
    assert write(world, "ROW-X.md", f"The examples are filed as their own row ({QID}).\n") == ""


def test_a_deferral_in_a_TEST_or_FIXTURE_file_is_silent(world):
    assert write(world, "tests/notes.md", "tracked later\n") == ""
    assert write(world, "fixture_report.md", "tracked later\n") == ""


def test_a_py_COMMENT_and_DOCSTRING_are_read_but_code_is_not(world):
    src = ('"""Module.\n\nThe cache half is deferred.\n"""\n'
           'later = 1  # plain code, only the NAME matches\n'
           'x = 2  # TODO follow-up on the cache\n')
    out = write(world, "mod.py", src)
    assert "mod.py:3: The cache half is deferred." in out, out
    assert "mod.py:6:" in out and "mod.py:5:" not in out, out


def test_an_EDIT_names_only_the_lines_it_added(world):
    old = "Intro.\nThis was deferred.\n"
    new = "Intro.\nThis was deferred.\nThe second half is tracked elsewhere.\n"
    out = write(world, "HANDOFF.md", old=old, new=new, final=new)
    assert "HANDOFF.md:3: The second half is tracked elsewhere." in out, out
    assert "HANDOFF.md:2" not in out, out


def test_a_line_with_no_deferral_phrase_is_silent(world):
    assert write(world, "ROW-X.md", "All four rows merged.\n") == ""


def test_the_door_is_OFF_unless_the_config_turns_it_on(world):
    world["cfg"].write_text("{}")
    assert write(world, "ROW-X.md", "filed as its own row\n") == ""


def test_the_notice_is_capped_and_says_how_many_more(world):
    out = write(world, "ROW-X.md", "".join(f"item {i} deferred\n" for i in range(8)))
    assert "(and 3 more)" in out and out.count("ROW-X.md:") == 5, out


def test_a_phrase_inside_a_WORD_is_not_a_deferral(world):
    """`\\b`: "untracked", "profiled", "collateral" carry no promise."""
    assert write(world, "ROW-X.md", "untracked files; profiled run; collateral\n") == ""


def test_a_VAULT_write_gets_the_same_note(world, tmp_path):
    """Reviewer (DEFERDOOR-1): the vault branch of the chore appends the note too."""
    p = tmp_path / "Vault" / "Speculum" / "Position.md"
    p.parent.mkdir(parents=True)
    p.write_text("The anchor resolver is filed as its own row.\n")
    payload = {"session_id": "t", "tool_name": "Write", "cwd": str(tmp_path / "Vault"),
               "tool_input": {"file_path": str(p), "content": p.read_text()}}
    r = subprocess.run([sys.executable, "-B", str(HOOKS / "chore.py"), "write"],
                       input=json.dumps(payload), capture_output=True, text=True, env=world["env"], timeout=30)
    assert r.returncode == 0, r.stderr
    assert "Position.md:1: The anchor resolver is filed as its own row." in r.stdout, r.stdout


def test_a_LARGE_file_of_findings_is_numbered_in_one_pass():
    """Reviewer (DEFERDOOR-1): 20,000 matching lines took 3.6 s with a scan per finding."""
    import time
    sys.path.insert(0, str(HOOKS))
    import deferral_door
    text = "".join(f"item {i} deferred\n" for i in range(20_000))
    with __import__("tempfile").TemporaryDirectory() as d:
        p = Path(d) / "big.md"
        p.write_text(text)
        t = time.time()
        got = deferral_door.findings(p, {"content": text})
        assert len(got) == 20_000 and got[-1].startswith("big.md:20000:"), got[-1]
        assert time.time() - t < 1.0
