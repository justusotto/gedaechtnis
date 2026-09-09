"""init.py — the one-command install, proven against a temporary HOME.

Every case runs init in a subprocess with `HOME` pointed at tmp_path and every GEDAECHTNIS_* path
redirected there, so the suite never reads or writes a real vault, a real ~/.claude, or a real
config file. The tree comparison hashes every file including `.git/`, because "a second run changes
nothing" has to mean the bytes, not the report.
"""
from __future__ import annotations
import hashlib, json, os, re, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
INIT = PLUGIN / "init.py"
SESSION_START = PLUGIN / "hooks" / "session_start.py"
CORE_SIX = ("Map", "Position", "Canon", "Patterns", "Errata", "Aporia")


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / "my_project").mkdir(parents=True)
    return h


def env_for(home: Path, **extra) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    e["HOME"] = str(home)
    e["GEDAECHTNIS_CONFIG"] = str(home / ".claude" / "gedaechtnis" / "config.json")
    e["GEDAECHTNIS_STATE_DIR"] = str(home / "state")
    # A fake TMPDIR, because the discovery filter drops candidates under the real one and the
    # suite's own tmp_path lives there: without this the filter would be measuring pytest's
    # layout rather than the rule. Nothing is written under it unless a test does so.
    e["TMPDIR"] = str(home / "faketmp")
    e.update(extra)
    return e


def init(home: Path, *args: str, cwd: Path | None = None, **extra_env):
    p = subprocess.run([sys.executable, str(INIT), *args], cwd=str(cwd or home / "my_project"),
                       capture_output=True, text=True, env=env_for(home, **extra_env), stdin=subprocess.DEVNULL, timeout=120)
    return p.returncode, p.stdout, p.stderr


def tree(root: Path) -> dict:
    """{relative path: sha1 of bytes (or the link target)} for everything under root."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = "-> " + os.readlink(p)
        elif p.is_file():
            out[rel] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def report_lines(stdout: str) -> list[tuple[str, str]]:
    """(verb, path) for each report line."""
    rows = []
    for line in stdout.splitlines():
        m = re.match(r"^\s{2}(created|kept|updated|would create|would update|committed|not committed)\s+(\S.*?)(?:\s{2}\(.*\))?$", line)
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


def parse_roster(text: str) -> dict:
    """marker_check-style parse: only the ```fleet-roster fence, `lane:` opens a block."""
    lanes, inside, cur = {}, False, None
    for line in text.splitlines():
        s = line.strip()
        if not inside:
            if s.startswith("```") and s[3:].strip() == "fleet-roster":
                inside = True
            continue
        if s.startswith("```"):
            inside = False
            continue
        m = re.match(r"^\s*lane:(.*)$", line)
        if m:
            cur = lanes.setdefault(re.sub(r"\s+", "", m.group(1)), {"repos": [], "paths": []})
            continue
        m = re.match(r"^\s*repo:(.*)$", line)
        if m and cur is not None:
            cur["repos"].append(m.group(1).strip())
            continue
        m = re.match(r"^\s*path:(.*)$", line)
        if m and cur is not None:
            cur["paths"].append(m.group(1).strip())
    return lanes


# ------------------------------------------------------------- clean install ----
def test_clean_home_gets_everything(home):
    rc, out, err = init(home)
    assert rc == 0, err
    vault = home / "Gedaechtnis"
    for stem in CORE_SIX:
        assert (vault / "my_project" / f"{stem}.md").is_file(), stem
    assert (vault / "Global" / "Kernel.md").is_file()
    assert (vault / "Global" / "fleet-roster.md").is_file()
    assert (vault / ".git").is_dir()
    assert (home / "my_project" / ".atlas-lane").read_text(encoding="utf-8").splitlines()[-3:] == \
        ["lane: MY-PROJECT", "path: my_project/", "path: Global/"]
    claude_md = (home / "my_project" / "CLAUDE.md").read_text(encoding="utf-8")
    assert f"@{vault / 'my_project' / 'Position.md'}" in claude_md.splitlines()
    cfg = json.loads((home / ".claude" / "gedaechtnis" / "config.json").read_text(encoding="utf-8"))
    assert Path(os.path.expanduser(cfg["vault"])).resolve() == vault.resolve()
    link = home / ".claude" / "skills" / "gedaechtnis"
    assert link.is_symlink() and Path(os.readlink(link)) == PLUGIN
    lanes = parse_roster((vault / "Global" / "fleet-roster.md").read_text(encoding="utf-8"))
    assert lanes == {"MY-PROJECT": {"repos": ["my_project"], "paths": ["my_project/", "Global/"]}}
    # the report names every path and the one next step
    verbs = dict((p, v) for v, p in report_lines(out))
    assert verbs[str(vault / "my_project" / "Map.md")] == "created"
    assert verbs[str(link)] == "created"
    assert "Next step: open Claude Code in this repo; the first session prints a facts block naming your vault and lane." in out
    # the vault's first commit carries exactly the created files, nothing else
    log = subprocess.run(["git", "-C", str(vault), "show", "--name-only", "--format=", "HEAD"],
                         capture_output=True, text=True).stdout.split()
    assert sorted(log) == sorted([f"my_project/{s}.md" for s in CORE_SIX] + ["Global/Kernel.md", "Global/fleet-roster.md"])
    status = subprocess.run(["git", "-C", str(vault), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert status.strip() == ""


def test_the_kernel_is_plain_english(home):
    init(home)
    text = (home / "Gedaechtnis" / "Global" / "Kernel.md").read_text(encoding="utf-8")
    assert "memory" in text and "six files" in text and "fleet-roster" in text


def test_an_existing_atlas_directory_is_the_default_vault(home):
    (home / "Atlas").mkdir()
    rc, out, _ = init(home)
    assert rc == 0
    assert (home / "Atlas" / "my_project" / "Map.md").is_file()
    assert not (home / "Gedaechtnis").exists()


# --------------------------------------------------------------- idempotent ----
def test_second_run_changes_nothing_and_reports_kept(home):
    assert init(home)[0] == 0
    before = tree(home)
    rc, out, err = init(home)
    assert rc == 0, err
    assert tree(home) == before
    rows = report_lines(out)
    assert rows and all(v == "kept" for v, _ in rows), out
    assert "committed" not in out


def test_a_pre_existing_region_file_is_untouched(home):
    vault = home / "Gedaechtnis"
    (vault / "my_project").mkdir(parents=True)
    mine = vault / "my_project" / "Canon.md"
    mine.write_bytes(b"# my own canon\n\nwritten before init ran\n")
    rc, out, _ = init(home)
    assert rc == 0
    assert mine.read_bytes() == b"# my own canon\n\nwritten before init ran\n"
    assert dict((p, v) for v, p in report_lines(out))[str(mine)] == "kept"
    assert (vault / "my_project" / "Map.md").is_file()          # the absent siblings were still created


def test_an_existing_marker_is_kept_and_its_lane_adopted(home):
    (home / "my_project" / ".atlas-lane").write_text("lane: MINE\npath: my_project/\npath: Global/\n", encoding="utf-8")
    rc, out, _ = init(home)
    assert rc == 0
    assert any("lane MINE" in l for l in out.splitlines()), out
    lanes = parse_roster((home / "Gedaechtnis" / "Global" / "fleet-roster.md").read_text(encoding="utf-8"))
    assert list(lanes) == ["MINE"]


# ------------------------------------------------------------------- roster ----
def test_existing_roster_with_another_lane_gets_this_lane_appended_inside_the_fence(home):
    vault = home / "Gedaechtnis"
    (vault / "Global").mkdir(parents=True)
    roster = vault / "Global" / "fleet-roster.md"
    original = ("# Roster\n\nprose that must never parse as data:\nlane: NOT-A-LANE\n\n"
                "```fleet-roster\n# a comment\nlane: OTHER\nrepo: elsewhere/other\npath: Other/\npath: Global/\n```\n\nfooter\n")
    roster.write_text(original, encoding="utf-8")
    rc, out, _ = init(home)
    assert rc == 0
    text = roster.read_text(encoding="utf-8")
    assert text.startswith(original.split("```fleet-roster")[0])           # prose above the fence untouched
    assert text.endswith("```\n\nfooter\n")                                 # prose below the fence untouched
    lanes = parse_roster(text)
    assert lanes == {"OTHER": {"repos": ["elsewhere/other"], "paths": ["Other/", "Global/"]},
                     "MY-PROJECT": {"repos": ["my_project"], "paths": ["my_project/", "Global/"]}}
    assert dict((p, v) for v, p in report_lines(out))[str(roster)] == "updated"
    # and the change is ONE insertion of well-formed rows: what the write gate would admit
    a = 0
    while a < min(len(original), len(text)) and original[a] == text[a]:
        a += 1
    b = 0
    while b < min(len(original), len(text)) - a and original[-1 - b] == text[-1 - b]:
        b += 1
    inserted = text[a: len(text) - b]
    assert original[:a] + original[len(original) - b:] == original
    for line in inserted.splitlines():
        assert re.match(r"^\s*(?:#.*|(?:lane|repo|path):\s*\S.*|\s*)$", line), line
    # a third run keeps it
    rc, out, _ = init(home)
    assert dict((p, v) for v, p in report_lines(out))[str(roster)] == "kept"
    assert roster.read_text(encoding="utf-8") == text


def test_existing_claude_md_gains_only_the_import_line(home):
    claude_md = home / "my_project" / "CLAUDE.md"
    claude_md.write_text("# my project\n\nmy own instructions", encoding="utf-8")   # no trailing newline
    rc, out, _ = init(home)
    assert rc == 0
    text = claude_md.read_text(encoding="utf-8")
    assert text.startswith("# my project\n\nmy own instructions\n")
    imp = f"@{home / 'Gedaechtnis' / 'my_project' / 'Position.md'}"
    assert text.count(imp) == 1 and text.endswith(imp + "\n")
    init(home)
    assert claude_md.read_text(encoding="utf-8") == text


# ------------------------------------------------------------------ dry run ----
def test_dry_run_writes_nothing(home):
    before = tree(home)
    rc, out, err = init(home, "--dry-run")
    assert rc == 0, err
    assert tree(home) == before
    assert not (home / "Gedaechtnis").exists() and not (home / ".claude").exists()
    rows = report_lines(out)
    assert rows and all(v == "would create" for v, _ in rows), out
    assert "committed" not in out


# ----------------------------------------------------------------- refusals ----
def test_refuses_when_the_vault_path_is_a_file(home):
    (home / "Gedaechtnis").write_text("not a directory\n", encoding="utf-8")
    rc, out, err = init(home)
    assert rc == 2
    assert "not a directory" in err and err.count("\n") == 1
    assert not (home / "my_project" / ".atlas-lane").exists()


def test_refuses_a_region_that_differs_only_in_case(home):
    (home / "Gedaechtnis" / "myproject").mkdir(parents=True)
    rc, out, err = init(home, "--region", "MyProject")
    assert rc == 2
    assert "only in case" in err and err.count("\n") == 1
    assert not (home / "Gedaechtnis" / "Global").exists()


def test_explicit_flags_are_honoured(home):
    other = home / "elsewhere" / "vault"
    rc, out, _ = init(home, "--vault", str(other), "--lane", "ALPHA", "--region", "Alpha")
    assert rc == 0
    assert (other / "Alpha" / "Map.md").is_file()
    lanes = parse_roster((other / "Global" / "fleet-roster.md").read_text(encoding="utf-8"))
    assert lanes == {"ALPHA": {"repos": ["my_project"], "paths": ["Alpha/", "Global/"]}}
    assert "lane: ALPHA" in (home / "my_project" / ".atlas-lane").read_text(encoding="utf-8")
    cfg = json.loads((home / ".claude" / "gedaechtnis" / "config.json").read_text(encoding="utf-8"))
    assert Path(os.path.expanduser(cfg["vault"])) == other


# --------------------------------------------------------------- end to end ----
def test_after_init_the_session_start_hook_names_the_lane_and_the_vault(home):
    assert init(home)[0] == 0
    repo = home / "my_project"
    p = subprocess.run([sys.executable, str(SESSION_START)],
                       input=json.dumps({"session_id": "t", "cwd": str(repo), "source": "startup"}),
                       capture_output=True, text=True, env=env_for(home), timeout=60)
    assert p.returncode == 0, p.stderr
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Lane MY-PROJECT" in ctx and str(repo / ".atlas-lane") in ctx
    assert "write partition: my_project, Global." in ctx                       # the partition the marker declares
    assert f"Vault {home / 'Gedaechtnis'}" in ctx and "HEAD at session start" in ctx
    assert (home / "state" / "session-start-t.json").is_file()                # state went to the sandbox, not ~/.claude
    assert not (home / ".claude" / "gedaechtnis" / "session-start-t.json").exists()


# --------------------------------------------------------- display layer (WP3, §6) ----
def session_facts(home: Path, sid: str = "t", cwd: Path | None = None, **extra_env) -> str:
    repo = cwd or home / "my_project"
    p = subprocess.run([sys.executable, str(SESSION_START)],
                       input=json.dumps({"session_id": sid, "cwd": str(repo), "source": "startup"}),
                       capture_output=True, text=True, env=env_for(home, **extra_env), timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def test_map_template_has_display_alias_links(home):
    assert init(home)[0] == 0
    text = (home / "Gedaechtnis" / "my_project" / "Map.md").read_text(encoding="utf-8")
    assert "[[Canon|Decisions]]" in text
    assert "[[Position|Status]]" in text
    assert "[[Patterns|Patterns]]" in text
    assert "[[Errata|Mistakes]]" in text
    assert "[[Aporia|Open questions]]" in text


def test_titles_use_the_display_name(home):
    assert init(home)[0] == 0
    vault = home / "Gedaechtnis" / "my_project"
    assert vault.joinpath("Canon.md").read_text(encoding="utf-8").splitlines()[3] == "# my_project — Decisions"
    assert vault.joinpath("Errata.md").read_text(encoding="utf-8").splitlines()[3] == "# my_project — Mistakes"
    assert vault.joinpath("Aporia.md").read_text(encoding="utf-8").splitlines()[3] == "# my_project — Open questions"


def test_core_files_get_exactly_one_alias_at_birth(home):
    assert init(home)[0] == 0
    vault = home / "Gedaechtnis" / "my_project"
    want = {"Map": "Index", "Position": "Status", "Canon": "Decisions", "Patterns": "Patterns",
           "Errata": "Mistakes", "Aporia": "Open questions"}
    for stem, disp in want.items():
        lines = vault.joinpath(f"{stem}.md").read_text(encoding="utf-8").splitlines()
        assert lines[0] == "---"
        assert lines[1] == f"aliases: [{disp}]"
        assert lines[2] == "---"
        # only `aliases` — nothing else in the frontmatter block
        assert lines[3].startswith("# ")


def test_a_second_run_keeps_the_aliases_frontmatter(home):
    assert init(home)[0] == 0
    vault = home / "Gedaechtnis" / "my_project"
    before = {p.name: p.read_bytes() for p in vault.glob("*.md")}
    rc, out, err = init(home)
    assert rc == 0, err
    rows = report_lines(out)
    assert rows and all(v == "kept" for v, _ in rows), out
    after = {p.name: p.read_bytes() for p in vault.glob("*.md")}
    assert before == after


def test_language_de_flips_titles_but_not_the_filenames(home):
    assert init(home, GEDAECHTNIS_LANGUAGE="de")[0] == 0
    vault = home / "Gedaechtnis" / "my_project"
    for stem in CORE_SIX:                                     # the six stems are untouched on disk
        assert vault.joinpath(f"{stem}.md").is_file(), stem
    assert not any((vault / f"{n}.md").exists() for n in
                  ("Entscheidungen", "Fehler", "Muster", "Offene Fragen", "Index", "Stand"))
    canon = vault.joinpath("Canon.md").read_text(encoding="utf-8")
    assert "# my_project — Entscheidungen" in canon
    assert "aliases: [Entscheidungen]" in canon
    errata = vault.joinpath("Errata.md").read_text(encoding="utf-8")
    assert "# my_project — Fehler" in errata


def test_facts_block_language_de_flips_the_memory_files_line(home):
    assert init(home)[0] == 0
    ctx = session_facts(home, GEDAECHTNIS_LANGUAGE="de")
    line = next(l for l in ctx.splitlines() if l.startswith("- Memory files in my_project:"))
    assert "Entscheidungen (Canon.md)" in line
    assert "Fehler (Errata.md)" in line
    assert "Decisions" not in line and "Mistakes" not in line


def test_facts_block_memory_files_line_lists_only_existing_files(home):
    assert init(home)[0] == 0
    (home / "Gedaechtnis" / "my_project" / "Errata.md").unlink()
    ctx = session_facts(home)
    line = next(l for l in ctx.splitlines() if l.startswith("- Memory files in my_project:"))
    assert "Decisions (Canon.md)" in line
    assert "Mistakes (Errata.md)" not in line
    # Global holds only Kernel.md at birth, and gets its own line naming it
    global_line = next((l for l in ctx.splitlines() if l.startswith("- Memory files in Global:")), None)
    assert global_line is not None and "Boot (Kernel.md)" in global_line
