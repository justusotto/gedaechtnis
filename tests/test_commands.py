"""Tests for the three slash commands and the status tool they call.

A command file is prose, so what can be tested about it is what a stranger's install depends on:
that it exists, that it is registered, that its frontmatter parses, that it names a tool that is
actually in this tree, and that it does not tell the model to do the one thing the vault's git law
forbids. The status tool gets the real controls — it is the one that reports facts.
"""
from __future__ import annotations
import json, os, re, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
COMMANDS = PLUGIN / "commands"
EXPECTED = {"gedaechtnis-status", "gedaechtnis-recall", "gedaechtnis-debrief",
            "gedaechtnis-cleanup"}


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} has no frontmatter"
    block = text.split("---\n", 2)[1]
    out = {}
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def body(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("---\n", 2)[2]


# ------------------------------------------------------------------ the command files ----
def test_every_command_exists_and_is_registered():
    names = {p.stem for p in COMMANDS.glob("*.md")}
    assert names == EXPECTED, names
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["commands"] == "./commands"
    assert (PLUGIN / manifest["commands"].lstrip("./")).is_dir()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_command_has_a_description_and_a_body(name):
    p = COMMANDS / f"{name}.md"
    fm = frontmatter(p)
    assert fm.get("description"), name
    assert len(fm["description"]) < 200, "a description is one line in a menu, not a paragraph"
    assert len(body(p).strip()) > 200, "a command whose body is a stub teaches nothing"


@pytest.mark.parametrize("name,tool", [("gedaechtnis-status", "tools/status.py"),
                                       ("gedaechtnis-recall", "recall.py"),
                                       ("gedaechtnis-debrief", "tools/status.py")])
def test_each_command_names_a_tool_that_exists(name, tool):
    """The failure this catches is a command renamed on one side only: the file still reads
    perfectly and the command simply fails for the user, with no test having noticed."""
    assert tool in body(COMMANDS / f"{name}.md")
    assert (PLUGIN / tool).is_file()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_command_tells_the_model_to_commit_the_vault_by_hand(name):
    """NEGATIVE control on the git law: committing is the hook's job. A command that told the
    model to stage and commit would undo the touched-set rule from inside the prose."""
    text = body(COMMANDS / f"{name}.md")
    for forbidden in ("git add -A", "git add -a", "git commit -a", "--amend"):
        assert forbidden not in text, forbidden


def test_the_recall_command_passes_the_users_words_through():
    text = body(COMMANDS / "gedaechtnis-recall.md")
    assert "$ARGUMENTS" in text
    assert frontmatter(COMMANDS / "gedaechtnis-recall.md").get("argument-hint")


def test_the_debrief_command_carries_the_four_line_format():
    """The debrief format lives in rules/operating-rules.md; the command must ask for the same
    four lines, or a session gets one shape at boot and another at the end."""
    text = body(COMMANDS / "gedaechtnis-debrief.md")
    rules = (PLUGIN / "rules" / "operating-rules.md").read_text(encoding="utf-8")
    for line in ("Wrote:", "Needs a decision:", "Committing:", "Open:"):
        assert line in text and line in rules, line


# ------------------------------------------------------------------ the status tool ----
@pytest.fixture
def world(tmp_path):
    vault = tmp_path / "vault"
    (vault / "MyProject").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    (vault / "MyProject" / "Position.md").write_text("start\n")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "root"], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".atlas-lane").write_text("lane: MY-PROJECT\npath: MyProject/\n")
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault),
               GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-such-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-such-config.json"))
    return dict(vault=vault, repo=repo, env=env, tmp=tmp_path)


def status(w, *args):
    p = subprocess.run([sys.executable, str(PLUGIN / "tools" / "status.py"),
                        "--cwd", str(w["repo"]), *args],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env=w["env"], timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_status_reports_the_lane_the_vault_and_the_dirt(world):
    (world["vault"] / "MyProject" / "Position.md").write_text("changed\n")
    out = status(world)
    assert "lane:       MY-PROJECT" in out
    assert str(world["vault"]) in out
    assert "dirty paths: 1" in out and "MyProject/Position.md" in out
    assert "partition hook: WARN" in out


def test_status_says_plainly_when_there_is_no_lane(world):
    """NEGATIVE control: no marker must read as "no partition", never as an empty one — the
    difference is whether the reader expects a commit at Stop."""
    bare = world["tmp"] / "unmarked"
    bare.mkdir()
    p = subprocess.run([sys.executable, str(PLUGIN / "tools" / "status.py"), "--cwd", str(bare)],
                       capture_output=True, text=True, env=world["env"], timeout=60)
    assert p.returncode == 0
    assert "lane:       NONE" in p.stdout and "commit nothing" in p.stdout


def test_status_reports_what_the_stop_hook_will_commit(world):
    """The number a reader trusts comes from the code that acts on it: this line is computed by
    commit.py's own functions, so it cannot disagree with what the Stop hook then does."""
    hooks = PLUGIN / "hooks"
    path = world["vault"] / "MyProject" / "Canon.md"
    path.write_text("## decided\n")
    subprocess.run([sys.executable, str(hooks / "chore.py"), "write"],
                   input=json.dumps({"cwd": str(world["repo"]), "session_id": "sX",
                                     "tool_name": "Write",
                                     "tool_input": {"file_path": str(path), "content": "## decided\n"}}),
                   capture_output=True, text=True, env=world["env"], timeout=60, check=True)
    out = status(world, "--session-id", "sX")
    assert "the Stop hook will commit: MyProject/Canon.md" in out

    subprocess.run([sys.executable, str(hooks / "commit.py")],
                   input=json.dumps({"cwd": str(world["repo"]), "session_id": "sX"}),
                   capture_output=True, text=True, env=world["env"], timeout=60, check=True)
    after = status(world, "--session-id", "sX")
    assert "the Stop hook will commit: nothing" in after
    assert "already committed" in after
