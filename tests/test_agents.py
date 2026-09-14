"""Tests for the two shipped agents.

An agent definition is prose; what a stranger's install depends on is that it exists, is
registered, pins a model, declares tools that match what it claims to do, and speaks the
six-file vocabulary rather than one private vault's names. Each of those is checked here, and
each has a way to fail: the name-leak test would catch a definition ported by find-and-replace
that missed a line, which is the realistic way a personal name reaches a public tree.
"""
from __future__ import annotations
import json, re
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
AGENTS = PLUGIN / "agents"
EXPECTED = {"memory-debriefer", "memory-reviewer", "memory-cleaner"}
CORE_SIX = ("Map", "Position", "Canon", "Patterns", "Errata", "Aporia")


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name} has no frontmatter"
    out = {}
    for line in text.split("---\n", 2)[1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def body(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("---\n", 2)[2]


def test_every_agent_exists_and_is_registered():
    assert {p.stem for p in AGENTS.glob("*.md")} == EXPECTED
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    # 2026-09-10: the `agents` manifest key is GONE on purpose — Claude Code's validator rejected
    # it ("agents: Invalid input") and a rejected manifest disables the WHOLE plugin; agents/ is
    # auto-discovered at the plugin root. The pin is now the key's ABSENCE plus the directory.
    assert "agents" not in manifest
    assert AGENTS.is_dir()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_agent_pins_a_model(name):
    """An unpinned agent silently inherits the session's model, which can cost several times what
    was intended — the same rule gate.py enforces on an Agent call."""
    fm = frontmatter(AGENTS / f"{name}.md")
    assert fm.get("model") == "sonnet", fm.get("model")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_agent_declares_a_name_a_description_and_tools(name):
    fm = frontmatter(AGENTS / f"{name}.md")
    assert fm.get("name") == name, "the frontmatter name must match the filename"
    assert fm.get("description") and len(fm["description"]) < 400
    assert fm.get("tools")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_agent_is_under_the_size_budget(name):
    """These are loaded whole when the agent runs; a definition that grows into an essay is a
    cost paid on every invocation."""
    assert len((AGENTS / f"{name}.md").read_bytes()) <= 3000


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_agent_speaks_the_six_file_vocabulary(name):
    text = body(AGENTS / f"{name}.md")
    for stem in CORE_SIX:
        assert f"**{stem}**" in text, stem


def test_the_reviewer_is_read_only_in_its_tools_not_only_in_its_prose():
    """A reviewer that could write would be a reviewer that edits what it was asked to judge.
    The prose says so; this asserts the tool list agrees, which is what actually binds."""
    fm = frontmatter(AGENTS / "memory-reviewer.md")
    tools = {t.strip() for t in fm["tools"].split(",")}
    assert tools == {"Read", "Glob", "Grep"}
    assert "Bash" not in tools and "Write" not in tools and "Edit" not in tools


def test_the_debriefer_can_run_git_because_it_must_read_the_diff():
    fm = frontmatter(AGENTS / "memory-debriefer.md")
    assert "Bash" in {t.strip() for t in fm["tools"].split(",")}
    assert "Write" not in fm["tools"] and "Edit" not in fm["tools"]


def test_the_debriefer_asks_for_the_same_four_lines_as_the_rules():
    text = body(AGENTS / "memory-debriefer.md")
    rules = (PLUGIN / "rules" / "operating-rules.md").read_text(encoding="utf-8")
    for line in ("Wrote:", "Needs a decision:", "Committing:", "Open:"):
        assert line in text and line in rules, line


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_agent_carries_a_private_vault_name(name):
    """These are generic versions of two personal agents. A find-and-replace that missed a line
    is exactly how a private region name reaches a public tree, and it reads perfectly."""
    text = (AGENTS / f"{name}.md").read_text(encoding="utf-8")
    for needle in ("~/Atlas", "Speculum", "Mnemosyne", "atlas-debriefer", "atlas-design-reviewer",
                   "Nomos", "Ethos", "lustrum", "Pharos"):
        assert needle.lower() not in text.lower(), needle
