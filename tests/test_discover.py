"""Discovery and the consent screen, proven against a temporary HOME.

Every case builds a whole fake machine under `tmp_path` — a HOME with its own `.claude.json`, its
own `.claude/projects/` transcript directories, its own roots full of git repos — and runs `init.py`
in a subprocess with `HOME`, `TMPDIR` and every `GEDAECHTNIS_*` path pointed into it. Nothing here
reads or writes a real vault, a real `~/.claude.json`, a real config file or the real state dir.

`TMPDIR` matters more than it looks: discovery drops any candidate under the temp directory (a repo
there is an artifact, not a project), and pytest's own `tmp_path` lives under the REAL `$TMPDIR`, so
without a fake one every test would be measuring pytest's layout instead of the rule.
"""
from __future__ import annotations
import hashlib, json, os, pty, re, subprocess, sys, time
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
INIT = PLUGIN / "init.py"
SESSION_START = PLUGIN / "hooks" / "session_start.py"
CORE_SIX = ("Map", "Position", "Canon", "Patterns", "Errata", "Aporia")
DAY = 86400


# ------------------------------------------------------------------ harness ----
@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / "faketmp").mkdir()
    return h


def env_for(home: Path, **extra) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    e["HOME"] = str(home)
    e["TMPDIR"] = str(home / "faketmp")
    e["GEDAECHTNIS_CONFIG"] = str(home / ".claude" / "gedaechtnis" / "config.json")
    e["GEDAECHTNIS_STATE_DIR"] = str(home / "state")
    e["GEDAECHTNIS_ROOTS"] = extra.pop("roots", str(home / "work"))
    e.update(extra)
    return e


def init(home: Path, *args: str, cwd: Path, **env):
    """init with a stdin that is NOT a terminal — the `--yes`-equivalent path."""
    p = subprocess.run([sys.executable, str(INIT), *args], cwd=str(cwd), capture_output=True,
                       text=True, env=env_for(home, **env), timeout=180, stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout, p.stderr


def init_tty(home: Path, *args: str, cwd: Path, answer: str, **env):
    """init with a REAL terminal on stdin, answering the screen with `answer`.

    A pipe would not do: the screen is shown only when `sys.stdin.isatty()`, which is the whole
    interactive contract, so a test that fed a pipe would silently exercise the non-interactive
    path and pass while proving nothing about the prompt."""
    master, slave = pty.openpty()
    p = subprocess.Popen([sys.executable, str(INIT), *args], cwd=str(cwd), env=env_for(home, **env),
                         stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    os.close(slave)
    os.write(master, answer.encode())
    try:
        out, err = p.communicate(timeout=180)
    finally:
        os.close(master)
    return p.returncode, out, err


def git_repo(path: Path, commit: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    if commit:
        (path / "README").write_text("x\n")
        for args in (["add", "README"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
            subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


def claude_json(home: Path, paths) -> None:
    """A ~/.claude.json shaped like the real one: `projects` beside keys we must never read."""
    (home / ".claude.json").write_text(json.dumps({
        "numStartups": 41,
        "userID": "not-read-by-this-plugin",
        "projects": {str(p): {"history": [], "allowedTools": []} for p in paths},
    }, indent=2) + "\n", encoding="utf-8")


def transcript(home: Path, path: Path, days_ago: float) -> Path:
    """A `~/.claude/projects/<mangled>/` directory, back-dated: source (b), the recency signal."""
    d = home / ".claude" / "projects" / str(path).replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    when = time.time() - days_ago * DAY
    os.utime(d, (when, when))
    return d


def rows(stdout: str):
    """(number, project, last session, region, note) for each row of the discovery table."""
    out = []
    for line in stdout.splitlines():
        m = re.match(r"^\s+(\d+)\s+(\S+)\s+(today|unknown|\d+ days? ago)\s+(\S+)"
                     r"(?:\s+\((.*)\))?\s*$", line)
        if m:
            out.append((int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4), m.group(5) or ""))
    return out


def regions(vault: Path):
    return sorted(p.name for p in vault.iterdir()
                  if p.is_dir() and p.name not in (".git", "Global"))


def tree(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = "-> " + os.readlink(p)
        elif p.is_file():
            try:
                out[rel] = hashlib.sha1(p.read_bytes()).hexdigest()
            except OSError:
                out[rel] = "unreadable"
    return out


def cfg(home: Path) -> dict:
    return json.loads((home / ".claude" / "gedaechtnis" / "config.json").read_text(encoding="utf-8"))


@pytest.fixture
def machine(home):
    """Three real git repos in ~/.claude.json, beside four entries that must all be filtered out:
    a repo under $TMPDIR, an existing directory that is not a repo, a path that does not exist,
    HOME itself, and the vault. The screen must show exactly the three."""
    good = [git_repo(home / "work" / n) for n in ("alpha", "beta", "gamma")]
    junk = [git_repo(home / "faketmp" / "scratch-repo"),      # under $TMPDIR
            home / "work" / "not-a-repo",                     # exists, no .git
            home / "work" / "vanished",                       # does not exist
            home,                                             # HOME itself
            home / "Gedaechtnis"]                             # the vault
    (home / "work" / "not-a-repo").mkdir(parents=True, exist_ok=True)
    git_repo(home / "Gedaechtnis")
    claude_json(home, good + junk)
    return {"home": home, "good": good, "vault": home / "Gedaechtnis"}


# ------------------------------------------------- the four sources and the filters ----
def test_the_screen_lists_exactly_the_three_real_repos_and_nothing_else(machine):
    """PROVES: of seven ~/.claude.json entries only the three real, non-temp, non-vault git repos
    are offered — and that the filters, not luck, are what removed the other four."""
    home, good = machine["home"], machine["good"]
    rc, out, err = init(home, "--dry-run", cwd=good[0])
    assert rc == 0, err
    table = rows(out)
    assert sorted(r[3] for r in table) == ["alpha", "beta", "gamma"], out
    assert len(table) == 3, out
    # the four rejected entries are absent from the TABLE (the vault's own name appears further
    # down every report line, so the check has to be against the rows, not the whole output)
    listed = {r[1] for r in table} | {r[3] for r in table}
    for bad in ("scratch-repo", "not-a-repo", "vanished", "Gedaechtnis"):
        assert not any(bad in item for item in listed), bad


def test_yes_creates_exactly_three_regions_in_one_commit(machine):
    """PROVES: the multi-repo install is ONE act — three regions, one first commit, and the commit
    carries exactly the files the run created, nothing swept in beside them."""
    home, good, vault = machine["home"], machine["good"], machine["vault"]
    rc, out, err = init(home, "--yes", cwd=good[0])
    assert rc == 0, err
    assert regions(vault) == ["alpha", "beta", "gamma"]
    for repo in good:
        assert (repo / ".atlas-lane").is_file() and (repo / "CLAUDE.md").is_file()
    log = subprocess.run(["git", "-C", str(vault), "log", "--format=%H"], capture_output=True, text=True).stdout.split()
    assert len(log) == 1, "the three repos must land in ONE commit"
    committed = subprocess.run(["git", "-C", str(vault), "show", "--name-only", "--format=", "HEAD"],
                               capture_output=True, text=True).stdout.split()
    expected = [f"{r}/{s}.md" for r in ("alpha", "beta", "gamma") for s in CORE_SIX] + \
               ["Global/Kernel.md", "Global/fleet-roster.md"]
    assert sorted(committed) == sorted(expected)
    assert subprocess.run(["git", "-C", str(vault), "status", "--porcelain"],
                          capture_output=True, text=True).stdout.strip() == ""


def test_a_second_run_reports_kept_everywhere_and_adds_no_commit(machine):
    """PROVES: idempotence across MANY repos, in bytes and in commits, not only in the report."""
    home, good, vault = machine["home"], machine["good"], machine["vault"]
    assert init(home, "--yes", cwd=good[0])[0] == 0
    before, head = tree(home), subprocess.run(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    rc, out, err = init(home, "--yes", cwd=good[0])
    assert rc == 0, err
    verbs = set(re.findall(r"^\s{2}(created|updated|kept|committed)\s", out, re.M))
    assert verbs == {"kept"}, out
    assert tree(home) == before
    assert subprocess.run(["git", "-C", str(vault), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip() == head


def test_the_fabricated_claude_json_is_byte_identical_afterwards(machine):
    """PROVES: ~/.claude.json is READ and never written — the file belongs to Claude Code."""
    home, good = machine["home"], machine["good"]
    before = (home / ".claude.json").read_bytes()
    assert init(home, "--yes", cwd=good[0])[0] == 0
    assert init(home, "--yes", cwd=good[0])[0] == 0
    assert (home / ".claude.json").read_bytes() == before


def test_a_repo_at_depth_three_under_a_root_is_not_found_and_at_depth_two_is(home):
    """PROVES: the bounded search is bounded — the SAME repo is found at depth 2 and missed at
    depth 3, so the limit is what decides it and not the repo."""
    shallow = git_repo(home / "work" / "one" / "shallow")           # depth 2
    deep = git_repo(home / "work" / "one" / "two" / "deep")         # depth 3
    other = git_repo(home / "elsewhere" / "cwd-repo")
    rc, out, err = init(home, "--dry-run", cwd=other)
    assert rc == 0, err
    found = [r[3] for r in rows(out)]
    assert "shallow" in found and "deep" not in found, out
    assert shallow.is_dir() and deep.is_dir()


def test_a_repo_missing_from_claude_json_but_under_a_root_is_found(home):
    """PROVES: source (d) is not decoration — the repo Claude Code has never been opened in (his
    measured `vita-curriculum` case) is reachable only through the root search."""
    claude_json(home, [])
    unlisted = git_repo(home / "work" / "unlisted")
    rc, out, err = init(home, "--dry-run", cwd=git_repo(home / "elsewhere" / "cwd-repo"))
    assert rc == 0, err
    assert "unlisted" in [r[3] for r in rows(out)], out
    assert unlisted.is_dir()


def test_the_cwd_repo_is_always_offered_and_listed_first(home):
    """PROVES: the repo you are standing in is never a guess — offered even though no source names
    it, and put at the top of the table whatever its age."""
    listed = git_repo(home / "work" / "listed")
    transcript(home, listed, days_ago=0)
    claude_json(home, [listed])
    here = git_repo(home / "elsewhere" / "standing-here")
    rc, out, err = init(home, "--dry-run", cwd=here)
    assert rc == 0, err
    table = rows(out)
    assert [r[3] for r in table] == ["standing-here", "listed"], out


def test_the_footer_names_its_sources_and_says_nothing_else_was_searched(home):
    """PROVES: the screen tells the truth about where it looked, in the user's own terms."""
    rc, out, _ = init(home, "--dry-run", cwd=git_repo(home / "work" / "solo"))
    assert "Found via Claude Code's own project list (~/.claude.json, `projects` key only) and a " \
           "depth-2 look under: ~/work. Nothing else on this disk was searched." in \
           " ".join(out.split()), out


SCREEN = """\
Gedächtnis found 4 projects Claude Code has worked in.

    #  project                          last session   region it would get
    1  work/ledger-api                  today          ledger-api
    2  work/website                     2 days ago     website
    3  work/scratch-notes               31 days ago    scratch-notes
    4  work/old-prototype               210 days ago   old-prototype   (no session in 90 days)

Enter takes every row except #4 (the rows with a note); name a number to include one anyway.
Found via Claude Code's own project list (~/.claude.json, `projects` key only) and a depth-2 look \
under: ~/work. Nothing else on this disk was searched.

"""


def test_the_whole_screen_is_exactly_this(home):
    """PROVES the screen the user actually sees, character for character — the columns line up, the
    dates read as English, the region names are the repo folders verbatim, the stale row is present
    and marked, and the footer names its sources. Pinned in full because this block IS the product's
    first impression: a column that drifts or a sentence that quietly changes is a regression nobody
    else would catch."""
    repos = [git_repo(home / "work" / n) for n in
             ("ledger-api", "website", "scratch-notes", "old-prototype")]
    for repo, days in zip(repos, (0, 2, 31, 210)):
        transcript(home, repo, days_ago=days + 0.5)      # mid-day, so no boundary flake
    claude_json(home, repos)
    rc, out, err = init(home, "--dry-run", cwd=repos[0])
    assert rc == 0, err
    printed = out.split("Gedächtnis init —")[0]
    assert printed == SCREEN, repr(printed)


# --------------------------------------------------------------- recency and staleness ----
def test_a_candidate_older_than_ninety_days_is_listed_but_not_created(home):
    """PROVES: the 90-day default is a TICK, not a filter — the stale row is visible, named as
    stale, and left alone by `--yes`."""
    fresh = git_repo(home / "work" / "fresh")
    stale = git_repo(home / "work" / "stale")
    transcript(home, fresh, days_ago=1)
    transcript(home, stale, days_ago=200)
    claude_json(home, [fresh, stale])
    rc, out, err = init(home, "--yes", cwd=fresh)
    assert rc == 0, err
    table = {r[3]: r for r in rows(out)}
    assert set(table) == {"fresh", "stale"}, out
    assert table["stale"][2] == "200 days ago" and table["stale"][4] == "no session in 90 days"
    assert table["fresh"][2] == "1 day ago" and table["fresh"][4] == ""
    assert regions(home / "Gedaechtnis") == ["fresh"]


def test_a_transcript_mtime_outranks_the_repos_own_head_date(home):
    """PROVES: source (b) is used for recency and source (a) is not re-dated by it — a repo whose
    only commit is from today still reads as a 200-day-old session when that is what the transcript
    directory says."""
    repo = git_repo(home / "work" / "olddesk", commit=True)
    transcript(home, repo, days_ago=200)
    claude_json(home, [repo])
    rc, out, _ = init(home, "--dry-run", cwd=git_repo(home / "elsewhere" / "cwd-repo"))
    table = {r[3]: r for r in rows(out)}
    assert table["olddesk"][2] == "200 days ago", out


# ------------------------------------------------------------------- declining ----
def test_decline_persists_and_the_screen_omits_it_until_offer_declined_or_all(home):
    """PROVES the whole refusal loop: `--decline` writes one key and nothing else, the declined
    repo disappears from the screen, `--offer-declined` brings it back UNTICKED, and `--all`
    ignores the list for one run."""
    keep = git_repo(home / "work" / "keep")
    drop = git_repo(home / "work" / "drop")
    claude_json(home, [keep, drop])
    (home / ".claude" / "gedaechtnis").mkdir(parents=True)
    (home / ".claude" / "gedaechtnis" / "config.json").write_text(
        json.dumps({"vault": str(home / "Gedaechtnis"), "auto_commit": False,
                    "roots": ["~/work"], "python": "/usr/bin/python3"}, indent=2) + "\n", encoding="utf-8")
    rc, out, err = init(home, "--decline", "--repo", str(drop), cwd=keep)
    assert rc == 0, err
    data = cfg(home)
    assert data["declined"] == [str(drop)]
    # every other key survived the write, value for value
    assert data["vault"] == str(home / "Gedaechtnis") and data["auto_commit"] is False
    assert data["roots"] == ["~/work"] and data["python"] == "/usr/bin/python3"
    assert not (drop / ".atlas-lane").exists(), "--decline writes nothing but the config key"

    plain = [r[3] for r in rows(init(home, "--dry-run", cwd=keep)[1])]
    assert plain == ["keep"]
    offered = {r[3]: r[4] for r in rows(init(home, "--dry-run", "--offer-declined", cwd=keep)[1])}
    assert offered.get("drop") == "declined earlier"
    everything = {r[3]: r[4] for r in rows(init(home, "--dry-run", "--all", cwd=keep)[1])}
    assert everything.get("drop") == ""            # --all lists it ticked, note-free


def test_declining_twice_does_not_double_the_entry(home):
    """PROVES: `--decline` is idempotent, like everything else init does."""
    drop = git_repo(home / "work" / "drop")
    claude_json(home, [drop])
    init(home, "--decline", "--repo", str(drop), cwd=drop)
    init(home, "--decline", "--repo", str(drop), cwd=drop)
    assert cfg(home)["declined"] == [str(drop)]


# ------------------------------------------------------------- answering the screen ----
def test_numbers_create_only_the_chosen_rows_and_decline_the_rest(home):
    """PROVES: `1 3` means exactly rows 1 and 3, and that row 2 is REMEMBERED as refused rather
    than simply skipped for this run."""
    repos = [git_repo(home / "work" / n) for n in ("one", "two", "three")]
    for i, r in enumerate(repos):
        transcript(home, r, days_ago=i)              # one newest, three oldest: a stable order
    claude_json(home, repos)
    rc, out, err = init_tty(home, cwd=repos[0], answer="1 3\n")
    assert rc == 0, err
    assert [r[3] for r in rows(out)] == ["one", "two", "three"], out
    assert regions(home / "Gedaechtnis") == ["one", "three"]
    assert cfg(home)["declined"] == [str(repos[1])]        # `two`, the row that was not named


def test_none_and_later_write_nothing_at_all(home):
    """PROVES: the two refusals are honest — `none` and `later` both leave the disk untouched, and
    `later` is truthful in the stronger sense that it records nothing either."""
    repo = git_repo(home / "work" / "solo")
    claude_json(home, [repo])
    for answer in ("none\n", "later\n"):
        before = tree(home)
        rc, out, err = init_tty(home, cwd=repo, answer=answer)
        assert rc == 0, err
        assert "nothing written." in out
        assert tree(home) == before, answer
        assert not (home / "Gedaechtnis").exists() and not (home / ".claude" / "gedaechtnis").exists()


def test_the_prompt_line_is_the_one_the_design_specifies(home):
    rc, out, _ = init_tty(home, cwd=git_repo(home / "work" / "solo"), answer="none\n")
    assert "Give these projects a memory?  [Enter = all]  [numbers, e.g. 1 3 4]  [none]  [later]" in out


def test_enter_takes_the_ninety_day_default(home):
    """PROVES: the interactive default and the `--yes` default are the same rule, not two."""
    fresh = git_repo(home / "work" / "fresh")
    stale = git_repo(home / "work" / "stale")
    transcript(home, fresh, days_ago=2)
    transcript(home, stale, days_ago=400)
    claude_json(home, [fresh, stale])
    rc, out, err = init_tty(home, cwd=fresh, answer="\n")
    assert rc == 0, err
    assert regions(home / "Gedaechtnis") == ["fresh"]
    assert "Enter takes every row except #2" in out or "Enter takes every row except #1" in out, out


# ------------------------------------------------------------------ configuration ----
def test_roots_are_honoured_from_the_environment_and_from_the_config_file(home):
    """PROVES both layers of the `roots` key, and that the environment wins — the config file
    points somewhere with nothing in it, and the repo under the env root is still found."""
    (home / ".claude" / "gedaechtnis").mkdir(parents=True)
    (home / ".claude" / "gedaechtnis" / "config.json").write_text(
        json.dumps({"roots": [str(home / "from-json")]}, indent=2) + "\n", encoding="utf-8")
    in_json = git_repo(home / "from-json" / "json-repo")
    in_env = git_repo(home / "from-env" / "env-repo")
    here = git_repo(home / "elsewhere" / "here")

    found = [r[3] for r in rows(init(home, "--dry-run", cwd=here, roots=str(home / "from-env"))[1])]
    assert "env-repo" in found and "json-repo" not in found, "GEDAECHTNIS_ROOTS wins"

    e = env_for(home)
    e.pop("GEDAECHTNIS_ROOTS")
    p = subprocess.run([sys.executable, str(INIT), "--dry-run"], cwd=str(here), env=e,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    found = [r[3] for r in rows(p.stdout)]
    assert "json-repo" in found and "env-repo" not in found, p.stdout
    assert in_json.is_dir() and in_env.is_dir()


# ----------------------------------------------------------- the negative control ----
def test_a_full_run_touches_nothing_outside_the_vault_repos_config_and_state(machine):
    """NEGATIVE CONTROL: hash every file under the fake HOME before and after a full `--yes` run
    and name every path that moved. Anything outside the five sanctioned surfaces — the vault, the
    chosen repos' `.atlas-lane` and `CLAUDE.md`, the config file, the state dir (and the one
    symlink that registers the plugin with Claude Code), and `.claude/settings.json`, which carries
    the plugin-outage check that must live OUTSIDE the plugin manifest — is a leak, and this is the
    only test that would see it. The settings file was added to this list deliberately, in the act
    that gave init something to write there; growing it silently is what this test exists against."""
    home, good, vault = machine["home"], machine["good"], machine["vault"]
    before = tree(home)
    rc, out, err = init(home, "--yes", cwd=good[0])
    assert rc == 0, err
    after = tree(home)

    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    allowed_files = {str((r / f).relative_to(home)) for r in good for f in (".atlas-lane", "CLAUDE.md")}
    allowed_files.add(".claude/settings.json")
    allowed_prefixes = (str(vault.relative_to(home)) + "/",
                        ".claude/gedaechtnis/", "state/", ".claude/skills/gedaechtnis")
    assert ".claude/settings.json" in changed, \
        "the outage check was not registered — the surface is allowed here only because init uses it"
    leaks = sorted(p for p in changed
                   if p not in allowed_files and not p.startswith(allowed_prefixes))
    assert leaks == [], f"init wrote outside its own surfaces: {leaks}"
    assert changed, "the run must actually have written something, or this control is vacuous"
    assert ".claude.json" not in changed


def test_the_negative_control_would_see_a_stray_write(machine, monkeypatch):
    """PROVES THE CONTROL BITES: the same comparison, with one file planted outside every
    sanctioned surface, reports exactly that file. A hash sweep nobody has watched fail is not
    evidence of anything."""
    home, good = machine["home"], machine["good"]
    before = tree(home)
    assert init(home, "--yes", cwd=good[0])[0] == 0
    (home / "Documents").mkdir(exist_ok=True)
    (home / "Documents" / "stray.txt").write_text("a write init never made\n")   # the plant
    after = tree(home)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    allowed_files = {str((r / f).relative_to(home)) for r in good for f in (".atlas-lane", "CLAUDE.md")}
    allowed_files.add(".claude/settings.json")               # the plugin-outage check's home
    allowed_prefixes = ("Gedaechtnis/", ".claude/gedaechtnis/", "state/", ".claude/skills/gedaechtnis")
    leaks = sorted(p for p in changed if p not in allowed_files and not p.startswith(allowed_prefixes))
    assert leaks == ["Documents/stray.txt"]


# ---------------------------------------------------- the session-start offer ----
def session_start(home: Path, cwd: Path, source: str = "startup") -> str:
    p = subprocess.run([sys.executable, str(SESSION_START)], env=env_for(home), capture_output=True,
                       text=True, timeout=60,
                       input=json.dumps({"session_id": "t", "cwd": str(cwd), "source": source}))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


OFFER = "This project has no memory yet. Ask the user ONCE, in one line"
FACT = "No .atlas-lane marker resolves from this cwd"


def test_the_offer_appears_once_a_day_in_an_undeclared_git_repo(home):
    """PROVES: the offer is made, is made ONCE, and degrades to the plain fact on the next boot the
    same day — a `no` that gets re-asked at every boot is a nag, and a nagging tool is turned off."""
    git_repo(home / "Gedaechtnis")                       # a vault must already exist
    repo = git_repo(home / "work" / "newcomer")
    first = session_start(home, repo)
    assert OFFER in first and FACT not in first
    assert f"--yes --repo {repo}" in first and f"--decline --repo {repo}" in first
    second = session_start(home, repo)
    assert FACT in second and OFFER not in second


def test_a_declined_repo_gets_the_plain_fact(home):
    """PROVES: `never` is remembered by the hook, not only by init."""
    git_repo(home / "Gedaechtnis")
    repo = git_repo(home / "work" / "refused")
    (home / ".claude" / "gedaechtnis").mkdir(parents=True)
    (home / ".claude" / "gedaechtnis" / "config.json").write_text(
        json.dumps({"vault": str(home / "Gedaechtnis"), "declined": [str(repo)]}) + "\n", encoding="utf-8")
    ctx = session_start(home, repo)
    assert FACT in ctx and OFFER not in ctx


def test_a_non_git_directory_never_gets_the_offer(home):
    """PROVES: a memory belongs to a project. A terminal opened in a plain directory is not one,
    and the offer is silent there rather than proposing a region for `~/Downloads`."""
    git_repo(home / "Gedaechtnis")
    plain = home / "just-a-folder"
    plain.mkdir()
    ctx = session_start(home, plain)
    assert FACT in ctx and OFFER not in ctx


def test_no_offer_when_there_is_no_vault_yet(home):
    """PROVES: the offer names a real next step or is not made — with no vault on the machine the
    existing 'run init.py' line is the honest one."""
    repo = git_repo(home / "work" / "newcomer")
    ctx = session_start(home, repo)
    assert FACT in ctx and OFFER not in ctx
    assert "run `python3" in ctx and "init.py" in ctx


def test_a_repo_with_a_marker_is_not_offered_anything(home):
    """NEGATIVE CONTROL for the offer: where a memory already exists neither line appears."""
    git_repo(home / "Gedaechtnis")
    repo = git_repo(home / "work" / "settled")
    assert init(home, "--yes", "--repo", str(repo), cwd=repo)[0] == 0
    ctx = session_start(home, repo)
    assert OFFER not in ctx and FACT not in ctx
    assert "Lane SETTLED" in ctx
