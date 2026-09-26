"""Tests for the shipped agents.

An agent definition is prose; what a stranger's install depends on is that it exists, is
registered, pins a model, declares tools that match what it claims to do, and speaks the
six-file vocabulary rather than one private vault's names. Each of those is checked here, and
each has a way to fail: the name-leak test would catch a definition ported by find-and-replace
that missed a line, which is the realistic way a personal name reaches a public tree.
"""
from __future__ import annotations
import json, pathlib, re, subprocess
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
AGENTS = PLUGIN / "agents"
EXPECTED = {"memory-debriefer", "memory-reviewer", "memory-cleaner", "memory-synthesizer",
            "memory-distiller"}
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


# ---------------------------------------------------------------- the private-name sweep ----
#
# ★ THE WHOLE SHIPPED TREE, not only the agents.
#
# The words below are the vault this package grew in: its umbrellas, its regions, its lanes, its
# non-tier folders, the two personal agents these five are generic versions of. None of them is a
# concept the package has — a stranger's vault has no `Pharos` — so a shipped file that names one
# is wrong about the reader as well as private about the author. Where the package genuinely needs
# one of these SHAPES (a queue folder, an outbox, a folder with reserved names, a file several
# lanes append to), it reads the name from `config.topology()`; where it needs prose, it says "the
# vault", "a region", "the queue".
#
# `tests/` is deliberately NOT swept: a fixture may name whatever it likes, and several do — the
# point of a fixture is to be a concrete vault. What ships is what is swept.

# The patterns are CASE-SENSITIVE and bounded, and the reason is the two lowercase names the
# package genuinely ships: the `.atlas-lane` marker file and the `atlas-region` claim-helper path,
# both part of its published contract and neither a vault's name. A case-folding substring check
# fires on both — and on "fabrication", "reconcile" and every other honest English word that
# happens to contain one of these. The lowercase SLUGS below are matched case-insensitively
# because they are only ever names.
PRIVATE_NAME_RE = re.compile(
    r"\bMnemosyne|\bSpeculum|\bSinica|\bFabrica\b|\bConcilium|\bLimen\b|\bPharos"
    r"|\bUkrain|\bUkran|\bAnkiAutoMiner|\bChongju|\bChineseDecks|\bChineseCard|\bLiBai"
    r"|\bMyTracker|\bMelchior|\bAtlas\b|\bCURSUS\b|\bMINING-OPS\b|\bVita\b|\bCurriculum\b")
PRIVATE_SLUG_RE = re.compile(
    r"ukrainian-card|ukrainian-vocab|cursus-adviser|concilium"
    r"|atlas-debriefer|atlas-design-reviewer|lustrum|mathesis", re.I)

# What the sweep must NOT fire on. Both are shipped, published names of this package's own, and a
# rule that cannot tell them from a vault's name would be rewritten to pass rather than obeyed.
ALLOWED_LITERALS = (".atlas-lane", "atlas-region")

# `.claude-plugin/` carries the manifest — as shipped as anything else here, and it was
# outside the first spelling of this sweep entirely.
SWEPT_DIRS = ("hooks", "eval", "commands", "agents", "rules", "tools", ".claude-plugin")
SWEPT_FILES = ("README.md", "CHANGELOG.md", "LICENSE", "names.json")
SWEPT_SUFFIXES = {".py", ".md", ".json", ".sh", ".txt"}


def shipped_files() -> list:
    """Every file a publish would carry, outside `tests/`. Top-level modules are included: they
    are shipped code, and the first pass of this rule missed them by sweeping directories only.

    ★ The population is TRACKED files, from git — not a filesystem walk. A walk counted two
    gitignored eval OUTPUT files (`eval/safety/results/*`) that exist only on a machine that has
    run the eval, so the same commit had 112 swept files here and 109 on a reviewer's checkout of
    it. A test whose population depends on what the machine happens to have run is a test whose
    verdict does; and those files are not published, so they were never this rule's business.
    Where git cannot answer (an exported tree with no repo), the walk is the fallback and the
    coverage control below still holds it to a floor."""
    tracked = _tracked()
    if tracked is not None:
        return tracked
    out = []
    for d in SWEPT_DIRS:
        for p in sorted((PLUGIN / d).rglob("*")):
            if p.is_file() and p.suffix in SWEPT_SUFFIXES and "__pycache__" not in p.parts:
                out.append(p)
    for name in SWEPT_FILES:
        if (PLUGIN / name).is_file():
            out.append(PLUGIN / name)
    out += [p for p in sorted(PLUGIN.glob("*.py")) if p.is_file()]
    return out


def _tracked() -> "list | None":
    """The tracked files this rule sweeps, or None where git cannot say."""
    try:
        r = subprocess.run(["git", "-C", str(PLUGIN), "ls-files", "-z", "--", "."],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = []
    for rel in r.stdout.split("\0"):
        if not rel:
            continue
        p = PLUGIN / rel
        parts = pathlib.PurePosixPath(rel).parts
        if parts[0] == "tests" or p.suffix not in SWEPT_SUFFIXES or not p.is_file():
            continue
        if len(parts) == 1 or parts[0] in SWEPT_DIRS or rel in SWEPT_FILES:
            out.append(p)
    return sorted(out)


def test_the_sweep_does_not_fire_on_this_package_s_own_names():
    """The NEGATIVE control. `.atlas-lane` and `atlas-region` are shipped names, and an earlier
    spelling of this rule — a case-folded substring check — refused both, plus the word
    "fabrication". A sweep that cannot stay quiet is a sweep somebody edits the tree to satisfy."""
    for lit in ALLOWED_LITERALS:
        assert not PRIVATE_NAME_RE.search(lit), lit
        assert not PRIVATE_SLUG_RE.search(lit), lit
    for innocent in ("a fabrication with a dollar sign", "reconcile the two", "vital signs",
                     "the curricular argument", "atlas-region helper", "a .atlas-lane marker"):
        assert not PRIVATE_NAME_RE.search(innocent), innocent
        assert not PRIVATE_SLUG_RE.search(innocent), innocent


def test_the_sweep_bites():
    """The POSITIVE control, planted in-process rather than in the tree: each class of pattern
    must actually fire, or a typo in the regex would make every file below pass."""
    for planted in ("a row in Mnemosyne/UkrainianCard", "look it up in ~/Atlas", "lane CURSUS",
                    "the Pharos queue", "Speculum/Kernel", "under Concilium/", "cursus-adviser",
                    "scripts/concilium.py", "concilium convene", "the Lustrum pass",
                    "atlas-debriefer"):
        assert PRIVATE_NAME_RE.search(planted) or PRIVATE_SLUG_RE.search(planted), planted


def test_the_sweep_actually_reaches_the_tree():
    """The control this rule needs most. A glob that matches nothing passes every assertion below
    it, silently — "nothing found" and "nowhere looked" print the same. These floors are what a
    green sweep is allowed to mean."""
    files = shipped_files()
    assert len(files) >= 100, f"the sweep found only {len(files)} shipped files"
    tops = {p.relative_to(PLUGIN).parts[0] for p in files}
    for d in SWEPT_DIRS:
        assert d in tops, f"the sweep reached no file under {d}/"
    assert any(p.parent == PLUGIN and p.suffix == ".py" for p in files), "no top-level module swept"


@pytest.mark.parametrize("rel", sorted(str(p.relative_to(PLUGIN)) for p in shipped_files()))
def test_no_shipped_file_carries_a_private_vault_name(rel):
    """A find-and-replace that missed a line is exactly how a private region name reaches a public
    tree, and it reads perfectly on the way out."""
    text = (PLUGIN / rel).read_text(encoding="utf-8", errors="replace")
    hits = [m.group(0) for m in PRIVATE_NAME_RE.finditer(text)]
    hits += [m.group(0) for m in PRIVATE_SLUG_RE.finditer(text)]
    assert not hits, f"{rel} carries {sorted(set(hits))}"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_agent_carries_a_private_vault_name(name):
    """The agents get the sweep above plus two words that are legitimate elsewhere in the package
    — `Nomos` and `Ethos` are role stems a vault may genuinely have — but never in an agent's
    prose, which speaks the display vocabulary."""
    text = (AGENTS / f"{name}.md").read_text(encoding="utf-8")
    assert not PRIVATE_NAME_RE.search(text) and not PRIVATE_SLUG_RE.search(text), name
    for needle in ("Nomos", "Ethos"):
        assert needle.lower() not in text.lower(), needle
