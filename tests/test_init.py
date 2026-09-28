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


def test_an_existing_atlas_vault_is_the_default_vault(home):
    """POSITIVE control: what makes ~/Atlas the vault is the ROSTER FILE, not the folder's name."""
    (home / "Atlas" / "Global").mkdir(parents=True)
    (home / "Atlas" / "Global" / "fleet-roster.md").write_text("# roster\n", encoding="utf-8")
    rc, out, _ = init(home)
    assert rc == 0
    assert (home / "Atlas" / "my_project" / "Map.md").is_file()
    assert not (home / "Gedaechtnis").exists()


def test_a_directory_merely_named_atlas_is_not_adopted_as_the_vault(home):
    """NEGATIVE control (council 3 K-1): a photo folder called Atlas is not a vault — init must
    build ~/Gedaechtnis and leave the stranger's directory untouched."""
    (home / "Atlas").mkdir()
    (home / "Atlas" / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    rc, out, _ = init(home)
    assert rc == 0
    assert (home / "Gedaechtnis" / "my_project" / "Map.md").is_file()
    assert sorted(p.name for p in (home / "Atlas").iterdir()) == ["photo.jpg"]


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


def test_existing_claude_md_gains_only_the_import_BLOCK(home):
    """The user's own text is untouched and exactly one import line is added — now inside the
    marked block, which is what tells `graduate.py` these lines are its business. Anything the user
    writes outside the markers is theirs, and the marker pair is what makes that a rule rather than
    a hope."""
    claude_md = home / "my_project" / "CLAUDE.md"
    claude_md.write_text("# my project\n\nmy own instructions", encoding="utf-8")   # no trailing newline
    rc, out, _ = init(home)
    assert rc == 0
    text = claude_md.read_text(encoding="utf-8")
    assert text.startswith("# my project\n\nmy own instructions\n")
    imp = f"@{home / 'Gedaechtnis' / 'my_project' / 'Position.md'}"
    assert text.count(imp) == 1
    assert text.count("<!-- gedaechtnis:imports -->") == 1
    assert text.count("<!-- gedaechtnis:/imports -->") == 1
    # the import sits INSIDE the pair, not merely somewhere in the file
    a = text.index("<!-- gedaechtnis:imports -->")
    b = text.index("<!-- gedaechtnis:/imports -->")
    assert a < text.index(imp) < b
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


# ---------------------------- the most consequential file this package rewrites ----
def test_an_existing_settings_json_is_BACKED_UP_before_it_is_rewritten(home):
    """★ THE FILE WHOSE `hooks` ENTRIES THIS MACHINE EXECUTES, REWRITTEN IN PLACE WITH NO COPY.

    `settings.json` was the only rewrite in the package with neither a backup nor an fsync — while
    `graduate.py` takes a dated backup of a repo's `CLAUDE.md`, which is prose. A corrupt
    `settings.json` is a Claude Code that will not start, and the file a user needs in that moment
    is exactly the one this function used to overwrite.

    The backup is once per day, so an install run twice does not eat the original with the copy it
    made a minute earlier."""
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({"mine": "keep me", "hooks": {"Stop": []}}, indent=2) + "\n"
    settings.write_text(original, encoding="utf-8")

    rc, out, err = init(home, "--outage-check")
    assert rc == 0, err
    backups = sorted(settings.parent.glob("settings.json.pre-gedaechtnis-*"))
    assert len(backups) == 1, [b.name for b in backups]
    assert backups[0].read_text(encoding="utf-8") == original, "the backup is not the original"
    assert json.loads(settings.read_text(encoding="utf-8"))["mine"] == "keep me"

    # ★ HAND-EDIT BETWEEN TWO SAME-DAY INSTALLS. With `if not backup.exists()`, the second run
    # found today's backup, left it alone, and replaced the file — so the only surviving copy was
    # the state before install #1 and the edit in between was in NO backup at all. `graduate.py`
    # answers this by comparing content and suffixing; the higher-consequence file must not answer
    # it more weakly.
    before = backups[0].read_text(encoding="utf-8")
    edited = json.dumps({"mine": "keep me", "SECOND": "hand edit"}, indent=2) + "\n"
    settings.write_text(edited, encoding="utf-8")
    rc, out, err = init(home, "--outage-check")
    assert rc == 0, err
    saved = {b.read_text(encoding="utf-8")
             for b in settings.parent.glob("settings.json.pre-gedaechtnis-*")}
    assert before in saved, "the first backup was lost"
    assert edited in saved, "the hand edit between the two installs is in no backup at all"

    # And a run that changes nothing adds nothing: a third install on the same day, with the file
    # already matching a backup, must not litter the directory with identical copies.
    n_before = len(list(settings.parent.glob("settings.json.pre-gedaechtnis-*")))
    rc, out, err = init(home, "--outage-check")
    assert rc == 0, err
    assert len(list(settings.parent.glob("settings.json.pre-gedaechtnis-*"))) == n_before


def test_no_settings_json_means_no_backup_file(home):
    """The negative control: a clean install has nothing to back up, and a stray
    `settings.json.pre-gedaechtnis-*` beside a fresh install would be a puzzle for its owner."""
    rc, out, err = init(home)
    assert rc == 0, err
    assert not list((home / ".claude").glob("settings.json.pre-gedaechtnis-*"))


def test_the_settings_write_leaves_no_tmp_file_behind(home):
    """`mkstemp` replaces a fixed `settings.json.tmp`, which two concurrent installs raced for.
    Whatever it creates must be gone once the replace has happened."""
    rc, out, err = init(home)
    assert rc == 0, err
    assert not list((home / ".claude").glob("settings.json.*.tmp")), \
        [p.name for p in (home / ".claude").glob("*")]


# ------------------------------------------------ the shape of the role files ----
# ★ TEMPLATESHAPE-1. The dad test's day-90 probes called the shipped vault "a garden log wearing a
# project's file structure" and `Shipped / In flight / Next` "empty boilerplate that doesn't fit a
# garden log" — in BOTH arms, so the finding is about the package, not about the model. These tests
# hold the two halves of the fix apart: the new shapes SAY something different, and the old one is
# not touched by a byte.

def _position(home: Path, vault: Path) -> str:
    return (vault / "my_project" / "Position.md").read_text(encoding="utf-8")


def test_the_project_shape_is_byte_identical_to_what_shipped_before_the_question(home, tmp_path):
    """NEGATIVE CONTROL, and the one the row names: a software project must get exactly today's
    file. Run against the templates as `git show` has them at the row's base, so this cannot be
    satisfied by the templates drifting together — it is pinned to the shipped bytes."""
    import subprocess as sp
    base = sp.run(["git", "show", "28f72cd3:gedaechtnis/init.py"], cwd=str(PLUGIN),
                  capture_output=True, text=True)
    if base.returncode != 0:                      # a shallow/exported tree has no such object
        pytest.skip("the base revision is not in this checkout")
    # `__file__` and `__name__` are supplied because the module resolves its own plugin root
    # from them; everything else it touches is import-time only.
    shipped: dict = {"__file__": str(INIT), "__name__": "init_at_base"}
    exec(compile(base.stdout, "init.py(base)", "exec"), shipped)
    import importlib.util
    spec = importlib.util.spec_from_file_location("init_under_test", INIT)
    now = importlib.util.module_from_spec(spec)
    sys.modules["init_under_test"] = now
    spec.loader.exec_module(now)
    for stem in CORE_SIX:
        assert now.TEMPLATES[stem]("Proj", "project") == shipped["TEMPLATES"][stem]("Proj"), stem


def test_a_notes_vault_is_not_born_wearing_a_projects_headings(home, tmp_path):
    """POSITIVE CONTROL: the shape the row exists for. The three software scaffolds are gone and
    the file says something a person keeping a log can answer."""
    vault = tmp_path / "vault"
    rc, out, err = init(home, "--repo", str(home / "my_project"), "--vault", str(vault),
                        "--yes", "--no-outage-check", "--shape", "notes")
    assert rc == 0, err
    text = _position(home, vault)
    for gone in ("## Shipped", "## In flight", "The planned route forward"):
        assert gone not in text, gone
    assert "## Where it stands" in text and "## What is next" in text
    assert "shape notes" in out
    purpose = (vault / "my_project" / "Map.md").read_text(encoding="utf-8")
    assert "What this project is for" not in purpose


def test_every_shape_produces_a_file_and_only_project_keeps_the_software_headings(home, tmp_path):
    """The whole set, so a shape added without a template entry fails here rather than at a
    stranger's install. `study` ships un-probed (no fixture measures it) and is asserted only for
    the property this row is about."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("init_shapes", INIT)
    m = importlib.util.module_from_spec(spec); sys.modules["init_shapes"] = m
    spec.loader.exec_module(m)
    for shape in m.SHAPES:
        for stem in CORE_SIX:
            body = m.TEMPLATES[stem]("Proj", shape)
            assert body.strip() and body.endswith("\n"), (shape, stem)
        pos = m.t_position("Proj", shape)
        assert ("## Shipped" in pos) is (shape == "project"), shape


def test_an_unanswered_shape_falls_back_to_project_and_says_so(home, tmp_path):
    """The fallback must not be silent: whatever a person did not choose is printed with the flag
    that changes it. A default nobody was told about is how the software headings reached a garden
    log in the first place."""
    vault = tmp_path / "vault"
    rc, out, err = init(home, "--repo", str(home / "my_project"), "--vault", str(vault),
                        "--yes", "--no-outage-check")
    assert rc == 0, err
    assert "## Shipped" in _position(home, vault)
    assert "--shape" in out and "nothing asked" in out


def test_a_named_shape_short_circuits_the_prompt_and_one_call_asks_once(home, tmp_path):
    """RENAMED by the row's reviewer, which was right: this exercises `ask_shape` in
    isolation and never runs `main` over two repos, so it never tested the once-per-RUN
    property its old name asserted. That property has a test of its own below."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("init_ask", INIT)
    m = importlib.util.module_from_spec(spec); sys.modules["init_ask"] = m
    spec.loader.exec_module(m)
    asked = []
    said = []
    got = m.ask_shape(None, True, ask=lambda p: (asked.append(p), "2")[1], say=said.append)
    assert got == "notes" and len(asked) == 1
    assert m.ask_shape("study", True, ask=lambda p: asked.append(p) or "1", say=said.append) \
        == "study", "--shape must answer it without asking"
    assert len(asked) == 1, "a named shape still asked"


def test_three_non_answers_give_up_rather_than_loop(home, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("init_ask3", INIT)
    m = importlib.util.module_from_spec(spec); sys.modules["init_ask3"] = m
    spec.loader.exec_module(m)
    said: list = []
    n = []
    got = m.ask_shape(None, True, ask=lambda p: n.append(1) or "banana", say=said.append)
    assert got == m.DEFAULT_SHAPE and len(n) == 3
    assert any("--shape" in s for s in said)
    assert m.parse_shape("") is None and m.parse_shape("9") is None
    assert m.parse_shape("2") == "notes" and m.parse_shape("NOTES") == "notes"


def test_a_rerun_over_an_existing_region_does_not_ask_a_question_that_changes_nothing(home, tmp_path):
    """The shape only decides files at BIRTH. A second run creates none, so asking there teaches a
    person that this question is noise."""
    vault = tmp_path / "vault"
    rc, out, _ = init(home, "--repo", str(home / "my_project"), "--vault", str(vault),
                      "--yes", "--no-outage-check", "--shape", "notes")
    assert rc == 0
    before = _position(home, vault)
    rc, out2, _ = init(home, "--repo", str(home / "my_project"), "--vault", str(vault),
                       "--yes", "--no-outage-check")
    assert rc == 0
    assert "nothing asked" not in out2, "the fallback line was printed on a run that births nothing"
    assert _position(home, vault) == before, "a re-run rewrote the role file under another shape"


def test_a_dry_run_neither_asks_nor_announces_a_shape(home, tmp_path):
    """A dry run is a REPORT. It writes nothing, so the question would decide nothing and a
    fallback line is noise — and the discovery screen is pinned character for character elsewhere,
    which an extra line breaks."""
    vault = tmp_path / "vault"
    rc, out, err = init(home, "--repo", str(home / "my_project"), "--vault", str(vault),
                        "--dry-run", "--no-outage-check")
    assert rc == 0, err
    assert "nothing asked" not in out and "nothing chosen" not in out
    assert "shape project" in out, "the report line must still say which shape it would use"
    assert not (vault / "my_project").exists()


def test_an_interactive_birth_really_asks_at_a_terminal_and_takes_the_answer(home, tmp_path):
    """POSITIVE CONTROL through a REAL pty, because the failure mode is a HANG, not a wrong answer:
    `input()` on an open terminal waits forever, so a run that asks a question nobody answers dies
    on a timeout rather than failing. Three `test_discover.py` cases did exactly that when this
    question was added. A pipe cannot exercise this at all — it is not a terminal, so nothing is
    asked."""
    import pty
    vault = tmp_path / "vault"
    master, slave = pty.openpty()
    p = subprocess.Popen([sys.executable, str(INIT), "--repo", str(home / "my_project"),
                          "--vault", str(vault), "--no-outage-check"],
                         cwd=str(home / "my_project"), env=env_for(home), stdin=slave,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    os.close(slave)
    # ORDER MATTERS and it is the shape first: `plan()` needs the shape to build the steps the
    # "Proceed?" line then counts. Answering them the other way round hangs.
    os.write(master, b"2\n")          # the shape: notes
    os.write(master, b"y\n")          # "N path(s) will be created or updated. Proceed? [Y/n]"
    try:
        out, err = p.communicate(timeout=60)
    finally:
        os.close(master)
    assert p.returncode == 0, err
    assert "What is this memory mostly for?" in out, out
    assert "shape notes" in out
    text = (vault / "my_project" / "Position.md").read_text(encoding="utf-8")
    assert "## Where it stands" in text and "## Shipped" not in text


def test_a_reborn_shape_varying_file_is_NOT_silently_given_the_project_shape(home, tmp_path):
    """★ MUST-FIX from the row's reviewer, reproduced as a test.

    The birth guard was keyed on `Position.md` — the file the defect was found in, and therefore
    the only example in hand. `Map.md` varies by shape too, so a region that answered `notes` and
    then lost its `Map.md` got a project-shaped one back with NO question and NO announcement: a
    notes vault quietly acquiring software prose, which is exactly what this question exists to
    prevent. Seventy-eight tests were green with the guard keyed on the wrong file."""
    vault = tmp_path / "vault"
    rc, _out, err = init(home, "--repo", str(home / "my_project"), "--vault", str(vault),
                         "--yes", "--no-outage-check", "--shape", "notes")
    assert rc == 0, err
    m = vault / "my_project" / "Map.md"
    assert "What this project is for" not in m.read_text(encoding="utf-8")
    m.unlink()

    rc, out, err = init(home, "--repo", str(home / "my_project"), "--vault", str(vault),
                        "--yes", "--no-outage-check")
    assert rc == 0, err
    reborn = m.read_text(encoding="utf-8")
    assert "What this project is for" not in reborn, "a notes region was handed project prose"
    assert "What this is for" in reborn, reborn[:200]
    assert "shape notes" in out, "the region's own answer should not need asking for twice"
    assert "nothing asked" not in out, "there was nothing to ask: the region had already answered"


def test_a_region_edited_past_recognition_is_asked_again_rather_than_guessed_at(home, tmp_path):
    """NEGATIVE CONTROL for the recogniser. It reads the user's own files, and they edit them — so
    on ANY doubt it must return None and let the question be asked, never pick the likeliest. A
    wrong guess here writes prose into somebody's notes."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("init_region_shape", INIT)
    m = importlib.util.module_from_spec(spec); sys.modules["init_region_shape"] = m
    spec.loader.exec_module(m)
    vault = tmp_path / "v"
    (vault / "r").mkdir(parents=True)
    assert m.region_shape(vault, "r") is None, "an empty region has not answered anything"

    (vault / "r" / "Position.md").write_text(m.t_position("r", "notes"), encoding="utf-8")
    assert m.region_shape(vault, "r") == "notes"
    (vault / "r" / "Map.md").write_text(m.t_map("r", "notes"), encoding="utf-8")
    assert m.region_shape(vault, "r") == "notes", "two agreeing files are still one answer"

    # the two files disagree — do not pick a side
    (vault / "r" / "Map.md").write_text(m.t_map("r", "project"), encoding="utf-8")
    assert m.region_shape(vault, "r") is None

    # edited past recognition
    (vault / "r" / "Map.md").write_text("# r\n\nmy own words\n", encoding="utf-8")
    assert m.region_shape(vault, "r") is None

    # study is recognised too, and `other` renders as `notes` so a tie between them is not doubt
    (vault / "s").mkdir()
    (vault / "s" / "Position.md").write_text(m.t_position("s", "study"), encoding="utf-8")
    assert m.region_shape(vault, "s") == "study"
    (vault / "o").mkdir()
    (vault / "o" / "Position.md").write_text(m.t_position("o", "other"), encoding="utf-8")
    assert m.region_shape(vault, "o") == "notes", "notes and other render identically"


def test_the_shape_varying_set_is_derived_from_the_tables_not_listed_beside_them():
    """A template that starts varying by shape must join the guard by EXISTING. If this ever has to
    be edited by hand when a table grows, the guard is one more place to forget."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("init_varying", INIT)
    m = importlib.util.module_from_spec(spec); sys.modules["init_varying"] = m
    spec.loader.exec_module(m)
    varying = {stem for stem in CORE_SIX
               if len({m.TEMPLATES[stem]("Proj", s) for s in m.SHAPES}) > 1}
    assert varying == set(m.SHAPE_VARYING), \
        f"the templates vary by shape for {sorted(varying)}, the guard watches {sorted(m.SHAPE_VARYING)}"
