#!/usr/bin/env python3
"""init.py — give one project, or every project on this machine, a memory. stdlib only.

    python3 init.py [--repo DIR] [--discover] [--decline] [--offer-declined] [--all]
                    [--vault DIR] [--lane NAME] [--region NAME] [--dry-run] [--yes]

Run it from inside a project, or from anywhere at all, and it creates everything the hooks need,
creating ONLY what is absent. An existing file is reported as `kept` and is never touched, so the
command is safe to run again: a second run changes nothing.

★ ONE INSTALL, MANY PROJECTS. With no `--repo`, init DISCOVERS the projects Claude Code has been
opened in and shows one screen listing them — path, when it was last worked in, the region name it
would get — and asks a single question:

    Give these projects a memory?  [Enter = all]  [numbers, e.g. 1 3 4]  [none]  [later]

Enter takes every project with a session in the last 90 days; numbers take exactly those, and the
rows you did not name are recorded as `declined` so they are never offered again (`--offer-declined`
lists them unticked, `--all` ignores the list for one run, `--decline` in a repo records one without
a screen). `none` and `later` write nothing at all — `later` also records nothing. Every chosen
project is set up in one act, and the vault gets ONE first commit.

The disk is never scanned. The four sources are Claude Code's own project list (the `projects` key
of ~/.claude.json, read and never written — no other key of that file is looked at), the mtimes of
~/.claude/projects/<mangled>/ as a recency signal only, `git config --global` maintenance/safe
lists, and a depth-2 look under the `roots` in the config file. Not read, ever: the shell history,
Spotlight, or the home directory at large.

Defaults, when no flag says otherwise:

  repo    the current directory's git toplevel — always offered, whatever discovery found
  lane    the repo's basename upper-cased, non-alphanumerics -> `-`   (my_project -> MY-PROJECT)
          — or, when the repo already has a `.atlas-lane` marker, the lane that marker declares
  region  the repo's basename, VERBATIM                                (my_project -> my_project)
  vault   what hooks/config.py resolves: ~/Gedaechtnis, or ~/Atlas when that directory is an
          Atlas VAULT (it carries Global/fleet-roster.md — a directory that merely has the name
          is not adopted), or the vault named in ~/.claude/gedaechtnis/config.json

What it creates (each line of the report names one of these):

  <vault>/                        the vault directory, `git init`-ed when it is not a git repo
  <vault>/<Region>/{Map,Position,Canon,Patterns,Errata,Aporia}.md
                                  the CORE OF SIX role files (PLAN §2.5/§5.3: a region starts with
                                  six files; every other role is born on its first write, never
                                  asked for. Vision lives at the head of Map as `## Purpose`, the
                                  roadmap at the foot of Position as `## Next`)
  <vault>/Global/Kernel.md        a short plain-English note explaining what the vault is
  <vault>/Global/fleet-roster.md  the lane -> partition declaration (one fenced block per lane;
                                  an existing roster gets this lane's block appended inside the
                                  fence when it has none)
  <repo>/.atlas-lane              the marker: `lane: NAME` + `path: <Region>/` + `path: Global/`
  <repo>/CLAUDE.md                an @-import of <vault>/<Region>/Position.md (appended when the
                                  file exists without it)
  <config>                        ~/.claude/gedaechtnis/config.json naming the vault
  ~/.claude/skills/gedaechtnis    a symlink to this plugin, which is how Claude Code loads it

When the vault had no commit yet, the files this run created are committed as its first commit
(path-limited, exactly the created files, nothing else — the vault's own git law).

Report verbs: `created` · `updated` (an existing roster or CLAUDE.md that gained lines) · `kept`
(untouched) · in `--dry-run`, `would create` / `would update` and nothing is written.

Refuses, exit 2, with one sentence: the vault path exists but is not a directory; a region differs
only in case from a directory already in the vault, or from another chosen repo's region (a
case-insensitive filesystem would silently merge the two); the repo is not a directory; `--region`
or `--lane` was given for a run with more than one project.

`--yes`, and a stdin that is not a terminal, take the default without asking anything.

Every path comes from hooks/config.py — nothing here names a directory of its own — which is also
what lets the test suite run this against a temporary HOME and never touch a real vault.
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN / "hooks"))
import config  # noqa: E402  (every path is resolved there)
import common  # noqa: E402  (git_root: the same bounded walk the hooks do)
import names  # noqa: E402  (display names over the stems this file writes to disk)

CORE_SIX = ("Map", "Position", "Canon", "Patterns", "Errata", "Aporia")
ROSTER_FENCE = "fleet-roster"
GIT_IDENTITY = ["-c", "user.name=atlas", "-c", "user.email=atlas@local"]   # the vault's own identity, as ledger.py uses


# ------------------------------------------------------------------ names ----
def lane_name(basename: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", basename).strip("-").upper()
    return s or "LANE"


def region_name(basename: str) -> str:
    """The repo's own name, VERBATIM — `my_project` stays `my_project`, `ukrainian-card` stays
    `ukrainian-card`. One thing has one name, and the only transformation a stranger never has to
    learn is none. Sanitised only where a character cannot be a directory name (a separator, a
    NUL, a leading or trailing dot or space); it is never re-cased and never re-punctuated."""
    s = re.sub(r"[/\\:\x00]+", "-", basename.strip())
    s = s.strip(". ")
    return s or "Region"


def home_relative(p: Path) -> str:
    """A `repo:` roster value is $HOME-relative; a repo outside HOME is written absolute."""
    try:
        return str(p.relative_to(config.HOME))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------- discovery ----
# Four sources, in this order, all bounded. THE DISK IS NEVER SCANNED: the shell history is not
# read (it carries commands and, routinely, secrets), Spotlight is not asked, `~` is not walked.
# What is read is the list Claude Code already keeps of the directories it has been opened in —
# that list IS the user's working set — plus two levels under a short list of conventional roots.
STALE_DAYS = 90
SEARCH_DEPTH = 2


class Candidate:
    """One repo the install screen may offer: where it is, how it was found, when it was last
    worked in, and whether it is ticked by default."""

    def __init__(self, path: Path, source: str, last_session=None, rank_ts=None):
        self.path = path
        self.sources = [source]
        self.last_session = last_session      # epoch seconds, or None when nothing records one
        self.rank_ts = rank_ts or 0.0         # what it is ORDERED by; never displayed
        self.declined = False

    @property
    def region(self) -> str:
        return region_name(self.path.name)

    def add_source(self, source: str) -> None:
        if source not in self.sources:
            self.sources.append(source)

    def stale(self, now: float) -> bool:
        return self.last_session is not None and (now - self.last_session) > STALE_DAYS * 86400

    def ticked(self, now: float) -> bool:
        """Unknown is not old: a repo nothing has recorded a session for is offered, because the
        alternative is to hide a project from its owner on the strength of a missing file."""
        return not self.declined and not self.stale(now)

    def note(self, now: float) -> str:
        if self.declined:
            return "declined earlier"
        if self.stale(now):
            return f"no session in {STALE_DAYS} days"
        return ""


def _run(args, cwd=None) -> tuple[int, str]:
    try:
        p = subprocess.run(args, cwd=(str(cwd) if cwd else None), capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=10)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _real(p: Path) -> Path:
    try:
        return Path(os.path.realpath(str(p)))
    except OSError:
        return p


def claude_json_projects(home: Path) -> list[Path]:
    """Source (a) — the ONLY key of ~/.claude.json this plugin ever reads: `projects`, whose keys
    are directories Claude Code has been opened in. No other key is read, and the file is never
    written. A missing or malformed file yields nothing at all, silently: a stranger's install
    must not depend on the shape of a file it does not own."""
    try:
        data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return []
    out = []
    for key in projects:
        if isinstance(key, str) and key.strip():
            out.append(Path(os.path.expanduser(key.strip())))
    return out


def _transcript_mtimes(home: Path) -> dict:
    """Source (b) — `~/.claude/projects/<mangled-cwd>/`, read for its mtime and NOTHING else.

    The directory names are a lossy encoding of a path (separators become `-`), so they are not
    decoded back into paths: a name is matched only against a candidate that some other source
    already produced. Recency signal, never a discovery source."""
    out = {}
    try:
        for child in (home / ".claude" / "projects").iterdir():
            if child.is_dir():
                try:
                    out[child.name] = child.stat().st_mtime
                except OSError:
                    pass
    except OSError:
        pass
    return out


def _mangles(path: Path) -> set:
    """The transcript-directory names this path could have produced — encoding forward, which is
    lossless, rather than decoding the name back, which is not."""
    s = str(path)
    return {s.replace("/", "-"), re.sub(r"[^A-Za-z0-9]", "-", s)}


def _git_listed() -> list[Path]:
    """Source (c) — repos git itself already knows about. Usually empty; free when it is not.
    Every failure (no git, no config, an unreadable file) is an empty list, never an error."""
    out = []
    for key in ("maintenance.repo", "safe.directory"):
        rc, txt = _run(["git", "config", "--global", "--get-all", key])
        if rc != 0:
            continue
        for line in txt.splitlines():
            s = line.strip()
            if s and s != "*" and not s.startswith("!"):
                out.append(Path(os.path.expanduser(s)))
    return out


def _search(roots, home: Path, depth: int = SEARCH_DEPTH) -> list[Path]:
    """Source (d) — every directory at depth <= `depth` under each root that contains `.git`.

    Never deeper, never HOME itself, never into a dot-directory (which is what keeps the walk out
    of `~/.claude/worktrees` and every cache), and never below a repo once one is found."""
    found, seen = [], set()
    for root in roots:
        root = Path(root)
        if not root.is_dir() or _real(root) == _real(home):
            continue
        stack = [(root, 0)]
        while stack:
            d, level = stack.pop()
            rd = _real(d)
            if rd in seen:
                continue
            seen.add(rd)
            if (d / ".git").exists():
                found.append(d)
                continue                       # a repo is a leaf: do not walk into its checkout
            if level >= depth:
                continue
            try:
                children = sorted(d.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir() and not child.is_symlink() and not child.name.startswith("."):
                    stack.append((child, level + 1))
    return found


def _tmp_roots() -> list[Path]:
    out = []
    for raw in (os.environ.get("TMPDIR"), "/tmp", "/private/tmp"):
        if raw and str(raw).strip():
            out.append(_real(Path(str(raw).strip())))
    return out


def offerable(path: Path, home: Path, vault: Path | None, tmp_roots=None) -> bool:
    """Is this a repo it would be honest to offer a memory to?

    It must exist, be a git toplevel, and not be one of the places a repo is a temporary artifact
    rather than a project: a temp directory, an agent worktree, a scratchpad, HOME itself, or the
    vault (a vault that offered itself a memory would nest one inside the other)."""
    tmp_roots = _tmp_roots() if tmp_roots is None else tmp_roots
    if not path.is_dir() or not (path / ".git").exists():
        return False
    real = _real(path)
    if real == _real(home):
        return False
    if vault is not None and (real == _real(vault) or _under(real, _real(vault))):
        return False
    s = str(real)
    if "/.claude/worktrees/" in s + "/" or "scratchpad" in s.lower():
        return False
    for t in tmp_roots:
        if real == t or _under(real, t):
            return False
    return True


def discover(home: Path, roots, vault: Path | None = None, cwd: Path | None = None) -> list:
    """-> [Candidate], newest first: every repo the four sources name, filtered and deduplicated.

    `home` and `roots` are passed in rather than read from the environment so this is testable
    against a temporary HOME. `vault` defaults to the configured one. `cwd`'s own repo is always
    included when it is one — you are standing in it, so it is not a guess."""
    if vault is None:
        vault = config.VAULT
    tmp_roots = _tmp_roots()
    found: list[tuple[Path, str]] = []
    project_paths = claude_json_projects(home)
    for p in project_paths:
        found.append((p, "claude.json"))
    for p in _git_listed():
        found.append((p, "git config"))
    search_roots = list(roots) + [p.parent for p in project_paths if p.parent != home]
    for p in _search(search_roots, home):
        found.append((p, "search"))
    if cwd is not None:
        root = Path(cwd) if (Path(cwd) / ".git").exists() else None
        if root is not None:
            found.append((root, "cwd"))

    transcripts = _transcript_mtimes(home)
    by_real: dict = {}
    order: list[Candidate] = []
    for path, source in found:
        if not offerable(path, home, vault, tmp_roots):
            continue
        real = _real(path)
        if real in by_real:
            by_real[real].add_source(source)
            continue
        last = None
        for name in _mangles(path) | _mangles(real):
            if name in transcripts:
                last = max(last or 0.0, transcripts[name])
        if last is None:
            rc, out = _run(["git", "-C", str(path), "log", "-1", "--format=%ct"])
            if rc == 0 and out.strip().isdigit():
                last = float(out.strip())
        try:
            rank = last if last is not None else path.stat().st_mtime
        except OSError:
            rank = 0.0
        c = Candidate(path, source, last_session=last, rank_ts=rank)
        by_real[real] = c
        order.append(c)
    order.sort(key=lambda c: c.rank_ts, reverse=True)
    return order


def humanise(last_session, now: float) -> str:
    if last_session is None:
        return "unknown"
    days = int((now - last_session) // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def display_path(path: Path, home: Path) -> str:
    try:
        return str(path.relative_to(home))
    except ValueError:
        return str(path)


def screen(cands, home: Path, roots, now: float) -> str:
    """The one screen, as text. Rows are numbered from 1; a row that Enter does not include says
    why in its own line rather than in a legend the reader has to hold."""
    head = f"Gedächtnis found {len(cands)} project" + ("" if len(cands) == 1 else "s") + \
           " Claude Code has worked in."
    rows = ["    #  project                          last session   region it would get"]
    for i, c in enumerate(cands, 1):
        note = c.note(now)
        rows.append(f"  {i:3}  {display_path(c.path, home):32} {humanise(c.last_session, now):14} "
                    f"{c.region}" + (f"   ({note})" if note else ""))
    untick = [i for i, c in enumerate(cands, 1) if not c.ticked(now)]
    tail = []
    if untick:
        tail.append("Enter takes every row except " + ", ".join(f"#{i}" for i in untick) +
                    " (the rows with a note); name a number to include one anyway.")
    root_text = ", ".join(display_home(r, home) for r in roots) or "(no roots configured)"
    tail.append("Found via Claude Code's own project list (~/.claude.json, `projects` key only) "
                f"and a depth-{SEARCH_DEPTH} look under: {root_text}. Nothing else on this disk "
                "was searched.")
    return "\n".join([head, ""] + rows + [""] + tail + [""])


def display_home(path: Path, home: Path) -> str:
    try:
        return "~/" + str(Path(path).relative_to(home))
    except ValueError:
        return str(path)


PROMPT = "Give these projects a memory?  [Enter = all]  [numbers, e.g. 1 3 4]  [none]  [later]\n> "


def parse_answer(raw: str, cands, now: float):
    """-> (chosen, unchosen, verb). verb is 'all' | 'numbers' | 'none' | 'later'.

    An answer that is neither empty, a keyword, nor a list of valid numbers is not guessed at:
    the caller re-asks. Only `numbers` records a refusal — `none` and `later` are the same act to
    the disk (nothing written, nothing remembered), which is what makes `later` truthful."""
    s = (raw or "").strip().lower()
    if s in ("none", "n"):
        return [], [], "none"
    if s in ("later", "l"):
        return [], [], "later"
    if s == "":
        ticked = [c for c in cands if c.ticked(now)]
        return ticked, [], "all"
    if s in ("all", "a", "*"):
        return list(cands), [], "all"
    picks = []
    for tok in re.split(r"[\s,]+", s):
        if not tok:
            continue
        if not tok.isdigit() or not (1 <= int(tok) <= len(cands)):
            return None
        picks.append(int(tok))
    if not picks:
        return None
    chosen = [cands[i - 1] for i in sorted(set(picks))]
    unchosen = [c for c in cands if c not in chosen]
    return chosen, unchosen, "numbers"


# ---------------------------------------------------------------- templates ----
# Title lines and the Map's index rows show the DISPLAY name (DESIGN §6); the file on disk keeps
# its stem regardless of `language` — `Canon.md` is never renamed to `Decisions.md`.
def _title(region: str, stem: str) -> str:
    return f"# {region} — {names.display(stem)}"


def _row(stem: str) -> str:
    """One Map index row, as an Obsidian display-alias link: the target is the stem (so the link
    still resolves whatever `language` is set to), the reader sees the display name."""
    return f"- [[{stem}|{names.display(stem)}]] — {names.gloss(stem)}"


def _frontmatter(stem: str) -> str:
    """The one YAML key a role file gets at birth: `aliases: [<display name>]`, so Obsidian's
    own search/link resolution finds the file under its display name too. Nothing else — DESIGN
    §6.2.3 costs this at ~30 B per file, and that is the whole budget."""
    return f"---\naliases: [{names.display(stem)}]\n---\n"


def t_map(region: str) -> str:
    return f"""{_title(region, "Map")}

## Purpose

What this project is for, in a few sentences, and what "done" looks like. (This section is the
project's Vision: it changes rarely; everything below it is an index.)

## Files in this folder

{_row("Position")}
{_row("Canon")}
{_row("Patterns")}
{_row("Errata")}
{_row("Aporia")}

Other files appear here as they are needed; nothing has to be created in advance.
"""


def t_position(region: str) -> str:
    return f"""{_title(region, "Position")}

Current state. Updated as work happens; the newest facts at the top of each section.

## Shipped

(nothing yet)

## In flight

(nothing yet)

## Next

The planned route forward, in priority order.

1. (first step)
"""


def t_canon(region: str) -> str:
    return f"""{_title(region, "Canon")}

Locked decisions. One entry per decision: what was decided, when, and why. Check here before
re-opening a question; supersede an entry in place rather than deleting it.

## (first decision)

**Decided:** YYYY-MM-DD. **Why:** …
"""


def t_patterns(region: str) -> str:
    return f"""{_title(region, "Patterns")}

Approaches that have worked at least twice. Each entry states the rule in a few lines; the story
behind it can go beneath.
"""


def t_errata(region: str) -> str:
    return f"""{_title(region, "Errata")}

Mistakes and bugs, with what went wrong and what to do instead. Every future session reads this,
so an entry here is how the same mistake is not made twice.
"""


def t_aporia(region: str) -> str:
    return f"""{_title(region, "Aporia")}

Open questions. Each carries an urgency (BLOCKING / NEXT / DEFERRED) and the date it was last
looked at; a question answered moves to [[Canon|{names.display("Canon")}]].
"""


TEMPLATES = {"Map": t_map, "Position": t_position, "Canon": t_canon,
             "Patterns": t_patterns, "Errata": t_errata, "Aporia": t_aporia}


def t_kernel(vault: Path) -> str:
    return f"""# What this vault is

This folder is a memory that outlives any single conversation. Claude Code reads parts of it
when a session starts and writes to it as work happens, so the next session — tomorrow, or on
another project — begins knowing what was decided, what went wrong, and what is still open.

- **One folder per project** (a *region*). Each starts with six files, shown under a plain name
  but kept on disk under its short one: **{names.display("Map")}** (`Map.md`, what the project is,
  and an index), **{names.display("Position")}** (`Position.md`, where it stands),
  **{names.display("Canon")}** (`Canon.md`, settled decisions), **{names.display("Patterns")}**
  (`Patterns.md`, what works), **{names.display("Errata")}** (`Errata.md`, what went wrong) and
  **{names.display("Aporia")}** (`Aporia.md`, open questions). Other files are added when there is
  something to put in them.
- **`Global/`** holds what applies to every project.
- **`Global/fleet-roster.md`** says which project may write which folders. A project's repo
  carries a small `.atlas-lane` file naming its *lane* — the writer identity a session uses.

The Gedächtnis plugin enforces the rules that can be enforced (it refuses a command that would
break the vault's git history, for example) and states the facts each session should not have to
ask for. This vault lives at `{vault}` and is an ordinary git repository: `git log` is its history.
"""


def t_roster(block: str) -> str:
    return f"""# Fleet roster — which lane may write which folders

Every lane (a writer identity, declared by a repo's `.atlas-lane` marker) gets one block in the
fenced section below: `lane:` opens the block, `repo:` names the repo (relative to your home
directory), and each `path:` is a vault folder or file that lane may write. `#` comments and blank
lines are ignored. A lane appends its own block; nobody rewrites another lane's.

```{ROSTER_FENCE}
{block}```
"""


def roster_block(lane: str, repo: Path, region: str) -> str:
    return f"lane: {lane}\nrepo: {home_relative(repo)}\npath: {region}/\npath: Global/\n"


def t_marker(lane: str, region: str) -> str:
    return f"""# .atlas-lane — the vault folders sessions opened in this repo may write.
# `lane:` once, then one `path:` per line (vault-relative). Absolute paths and `..` reject the
# whole marker. Comments and blank lines are ignored.
lane: {lane}
path: {region}/
path: Global/
"""


def t_claude_md(region: str, import_line: str) -> str:
    return f"""# {region}

Project instructions for Claude Code. The line below loads this project's current state from the
vault at every session start (an @-import must be an absolute path).

{import_line}
"""


# ---------------------------------------------------------------- parsing ----
def roster_lanes(text: str) -> list[str]:
    """Lane names declared inside the roster's fenced block(s) — the marker_check grammar."""
    lanes, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if not inside:
            if s.startswith("```") and s[3:].strip() == ROSTER_FENCE:
                inside = True
            continue
        if s.startswith("```"):
            inside = False
            continue
        m = re.match(r"^\s*lane:(.*)$", line)
        if m:
            lanes.append(re.sub(r"\s+", "", m.group(1)))
    return lanes


def marker_lane(marker: Path) -> str | None:
    try:
        for line in marker.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("lane:"):
                return s.split(":", 1)[1].strip() or None
    except OSError:
        pass
    return None


# ------------------------------------------------------------------ plan ----
class Step:
    """One path and what init will do to it. `write` runs only when not dry-run."""
    def __init__(self, path: Path, verb: str, write=None, note: str = ""):
        self.path, self.verb, self.write, self.note = path, verb, write, note


def plan(repo: Path, vault: Path, lane: str, region: str, cfg_path: Path, skills_dir: Path) -> list[Step]:
    steps: list[Step] = []

    def file(path: Path, content: str, note: str = ""):
        if path.exists():
            steps.append(Step(path, "kept", note=note))
        else:
            steps.append(Step(path, "created", lambda p=path, c=content: _write(p, c), note))

    # the vault
    if vault.is_dir():
        steps.append(Step(vault, "kept"))
    else:
        steps.append(Step(vault, "created", lambda: vault.mkdir(parents=True)))
    if not (vault / ".git").exists():
        steps.append(Step(vault / ".git", "created", lambda: _git(vault, "init", "-q"), "git init"))
    else:
        steps.append(Step(vault / ".git", "kept"))

    # the region: six core files — an Obsidian display-alias in frontmatter, then the template
    for stem in CORE_SIX:
        file(vault / region / f"{stem}.md", _frontmatter(stem) + TEMPLATES[stem](region))

    # Global
    file(vault / "Global" / "Kernel.md", t_kernel(vault))
    roster = vault / "Global" / "fleet-roster.md"
    block = roster_block(lane, repo, region)
    if roster.is_file():
        text = roster.read_text(encoding="utf-8")
        if lane in roster_lanes(text):
            steps.append(Step(roster, "kept", note=f"already declares lane {lane}"))
        else:
            steps.append(Step(roster, "updated", lambda: _write(roster, _roster_append(text, block)),
                              f"block for lane {lane} appended inside the fence"))
    else:
        steps.append(Step(roster, "created", lambda: _write(roster, t_roster(block))))

    # the repo
    marker = repo / ".atlas-lane"
    if marker.is_file():
        have = marker_lane(marker)
        steps.append(Step(marker, "kept", note="" if have == lane else f"declares lane {have}, not {lane}"))
    else:
        steps.append(Step(marker, "created", lambda: _write(marker, t_marker(lane, region))))
    claude_md = repo / "CLAUDE.md"
    import_line = f"@{vault / region / 'Position.md'}"
    if claude_md.is_file():
        text = claude_md.read_text(encoding="utf-8")
        if any(l.strip() == import_line for l in text.splitlines()):
            steps.append(Step(claude_md, "kept", note="already imports the region's Position.md"))
        else:
            sep = "" if text.endswith("\n") or not text else "\n"
            steps.append(Step(claude_md, "updated", lambda: _write(claude_md, text + sep + "\n" + import_line + "\n"),
                              "@-import line appended"))
    else:
        steps.append(Step(claude_md, "created", lambda: _write(claude_md, t_claude_md(region, import_line))))

    # the plugin's own configuration and registration
    if cfg_path.exists() and _config_names_a_vault(cfg_path):
        note = ""
        if vault != config.VAULT:
            note = f"names {config.VAULT} as the vault, not {vault} — the hooks follow the config file"
        steps.append(Step(cfg_path, "kept", note=note))
    else:
        # MERGED, never replaced: `--decline` may already have written a `declined` list into
        # this file, and a config write that drops another key is a config file that lies.
        verb = "updated" if cfg_path.exists() else "created"
        steps.append(Step(cfg_path, verb, lambda: config.write_keys({"vault": str(vault)}, cfg_path),
                          "names the vault"))
    link = skills_dir / "gedaechtnis"
    if link.exists() or link.is_symlink():
        target = os.readlink(link) if link.is_symlink() else None
        note = "" if target in (None, str(PLUGIN)) else f"symlink points at {target}, not this plugin"
        steps.append(Step(link, "kept", note=note))
    else:
        steps.append(Step(link, "created", lambda: _symlink(link), f"-> {PLUGIN}"))
    return steps


def _config_names_a_vault(cfg_path: Path) -> bool:
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("vault"))


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _symlink(link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(PLUGIN), str(link))


def _git(vault: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(vault), *GIT_IDENTITY, *args], check=True,
                   stdin=subprocess.DEVNULL, capture_output=True, text=True)


def _roster_append(text: str, block: str) -> str:
    """Insert `block` before the LAST closing fence of the fleet-roster section (the shape the
    write gate admits: a new block for the appender's own lane, before the closing fence)."""
    lines = text.splitlines(keepends=True)
    inside, close_at = False, None
    for i, line in enumerate(lines):
        s = line.strip()
        if not inside and s.startswith("```") and s[3:].strip() == ROSTER_FENCE:
            inside = True
        elif inside and s.startswith("```"):
            inside, close_at = False, i
    if close_at is None:                                   # no fence at all: add a whole fenced section at the end
        sep = "" if text.endswith("\n") or not text else "\n"
        return text + sep + f"\n```{ROSTER_FENCE}\n{block}```\n"
    prev = lines[close_at - 1].strip() if close_at > 0 else ""
    lead = "" if prev == "" or prev.startswith("```") else "\n"    # one blank line between blocks
    lines[close_at:close_at] = [lead + block]
    return "".join(lines)


# ------------------------------------------------------------------ main ----
def refuse(sentence: str) -> int:
    print(f"init refused: {sentence}", file=sys.stderr)
    return 2


def _decline(paths, cfg_path: Path, standalone: bool = True) -> int:
    """Record a refusal. The only key touched is `declined`; every other key in the config file
    survives the atomic replace.

    `standalone` is what `--decline` does on its own — it may then truthfully say that nothing else
    was written. Called for the rows a user did not name on the screen it may not, because the rows
    they DID name are about to be created."""
    have = [str(p) for p in config.declined()]
    add = [str(p) for p in paths if str(p) not in have]
    if add:
        config.write_keys({"declined": have + add}, cfg_path)
    for p in paths:
        print(f"  declined      {p}" + ("" if str(p) in add else "  (already declined)"))
    print((f"Recorded in {cfg_path}. Nothing else was written. " if standalone else
           "Recorded as declined; these are not offered again. ") +
          "`init.py --offer-declined` lists them again; `init.py --all` ignores the list once.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Give one project, or every project on this machine, a memory.")
    ap.add_argument("--repo", default=None, help="set up this one repo (default: discover, and include the current directory)")
    ap.add_argument("--vault", default=None, help="the vault directory (default: what hooks/config.py resolves)")
    ap.add_argument("--lane", default=None, help="lane name for --repo (default: the repo's basename, upper-cased)")
    ap.add_argument("--region", default=None, help="region folder name for --repo (default: the repo's basename, verbatim)")
    ap.add_argument("--discover", action="store_true", help="show the discovery screen even when a repo is named")
    ap.add_argument("--decline", action="store_true", help="record that this repo wants no memory, and write nothing else")
    ap.add_argument("--offer-declined", action="store_true", help="list previously declined repos too, unticked")
    ap.add_argument("--all", action="store_true", help="ignore the declined list for this one run")
    ap.add_argument("--dry-run", action="store_true", help="report what would be created; write nothing")
    ap.add_argument("--yes", action="store_true", help="take the default without asking (also implied by a non-terminal stdin)")
    a = ap.parse_args(argv)

    cwd = Path(os.getcwd()).expanduser().resolve()
    cwd_repo = common.git_root(cwd)
    repo = Path(a.repo).expanduser().resolve() if a.repo else (cwd_repo or cwd)
    if not repo.is_dir():
        return refuse(f"{repo} is not a directory, so there is no repo to set up.")
    vault = Path(a.vault).expanduser().resolve() if a.vault else config.VAULT
    if vault.exists() and not vault.is_dir():
        return refuse(f"{vault} exists but is not a directory, so it cannot be the vault.")
    cfg_path = config.CONFIG_PATH
    skills_dir = config.HOME / ".claude" / "skills"
    home = config.HOME
    now = time.time()

    if a.decline:
        return _decline([common.git_root(repo) or repo], cfg_path)

    # ---- which repos this run is about ----
    single = bool(a.repo) and not a.discover
    if single:
        targets = [repo]
    else:
        declined = {str(_real(Path(p))) for p in config.declined()}
        cands = discover(home, config.roots(), vault=vault, cwd=cwd_repo)
        if not any(_real(c.path) == _real(repo) for c in cands):
            # You are standing in it, so it is not a guess — offered even when it is not a git
            # repo, which is the one case discovery itself will not produce.
            cands.insert(0, Candidate(repo, "cwd", last_session=None,
                                      rank_ts=(repo.stat().st_mtime if repo.exists() else 0.0)))
        else:                                     # the cwd's own row goes first, whatever its age
            cands.sort(key=lambda c: 0 if _real(c.path) == _real(repo) else 1)
        if not a.all:
            keep = []
            for c in cands:
                if str(_real(c.path)) in declined:
                    if not a.offer_declined:
                        continue
                    c.declined = True
                keep.append(c)
            cands = keep
        if not cands:
            print("Gedächtnis found no projects to offer a memory to.")
            return 0
        # The screen is printed whether or not anything is asked: on a terminal it is the question,
        # and without one it is the report of what was decided on the user's behalf and why. A run
        # that acts on a list nobody was shown is a run nobody can check.
        print(screen(cands, home, config.roots(), now))
        interactive = sys.stdin.isatty() and not a.yes
        chosen = [c for c in cands if c.ticked(now)]
        if interactive:
            answer = None
            for _ in range(3):
                try:
                    answer = parse_answer(input(PROMPT), cands, now)
                except EOFError:
                    answer = ([c for c in cands if c.ticked(now)], [], "all")
                if answer is not None:
                    break
                print("  Not understood. Press Enter, or type numbers from the table, or `none`, or `later`.")
            if answer is None:
                print("nothing written.")
                return 1
            chosen, unchosen, verb = answer
            if verb in ("none", "later"):
                print("nothing written." + ("" if verb == "later" else
                      "  (`init.py` offers these again next time; `--decline` in a repo records a refusal.)"))
                return 0
            if verb == "numbers" and unchosen and not a.dry_run:
                _decline([c.path for c in unchosen], cfg_path, standalone=False)
        targets = [c.path for c in chosen]
        if not targets:
            print("nothing written.")
            return 0

    # ---- pre-flight refusals, over EVERY target, before anything is written ----
    # `--region` and `--lane` name ONE region and ONE lane, so they are honoured only when this
    # run has one target; with several they would have to be silently applied to the first or
    # ignored, and both are worse than saying so.
    one = len(targets) == 1
    if not one and (a.region or a.lane):
        return refuse(f"--region/--lane name one project, but this run has {len(targets)} of them; "
                      "add --repo to set one up on its own.")
    plans = []
    seen_regions = {}
    for t in targets:
        region = (a.region if one else None) or region_name(t.name)
        if not region or "/" in region or region in (".", ".."):
            return refuse(f"{region!r} is not a usable region name.")
        clash = seen_regions.get(region.lower())
        if clash and clash != region:
            return refuse(f"two chosen repos want the regions {clash!r} and {region!r}, which differ only in case.")
        seen_regions[region.lower()] = region
        if vault.is_dir():
            for name in os.listdir(vault):
                if name != region and name.lower() == region.lower() and (vault / name).is_dir():
                    return refuse(f"the vault already has a directory {name!r}, which differs from {region!r} only in case.")
        marker = t / ".atlas-lane"
        lane = (a.lane if one else None) or (marker.is_file() and marker_lane(marker)) or lane_name(t.name)
        plans.append((t, region, lane))

    # ---- one act: the plan is re-made per repo, so repo 2 sees what repo 1 created ----
    created: list[Path] = []
    for t, region, lane in plans:
        steps = plan(t, vault, lane, region, cfg_path, skills_dir)
        print(f"Gedächtnis init — repo {t}, vault {vault}, lane {lane}, region {region}" + (" (dry run)" if a.dry_run else ""))
        todo = [s for s in steps if s.verb != "kept"]
        if single and todo and not a.dry_run and not a.yes and sys.stdin.isatty():
            try:
                ans = input(f"{len(todo)} path(s) will be created or updated. Proceed? [Y/n] ").strip().lower()
            except EOFError:
                ans = ""
            if ans and ans[0] == "n":
                print("nothing written.")
                return 1
        for s in steps:
            verb = s.verb
            if s.verb != "kept":
                if a.dry_run:
                    verb = "would " + ("create" if s.verb == "created" else "update")
                else:
                    s.write()
                    if s.verb == "created" and s.path.is_file() and _under(s.path, vault):
                        created.append(s.path)
            print(f"  {verb:13} {s.path}" + (f"  ({s.note})" if s.note else ""))

    if created and not a.dry_run and (vault / ".git").is_dir():
        _first_commit(vault, created)

    print("Next step: open Claude Code in this repo; the first session prints a facts block naming your vault and lane.")
    return 0


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _first_commit(vault: Path, created: list[Path]) -> None:
    """The vault's first commit, only when it has none: exactly the files this run created."""
    p = subprocess.run(["git", "-C", str(vault), "rev-parse", "--verify", "-q", "HEAD"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode == 0:
        return                                             # the vault has history; committing is the session's act
    rels = [str(f.relative_to(vault)) for f in created]
    try:
        _git(vault, "add", "--", *rels)
        _git(vault, "commit", "-q", "-m", "Gedächtnis init: the vault's first files", "--", *rels)
        print(f"  committed     {len(rels)} created file(s) as the vault's first commit")
    except subprocess.CalledProcessError as e:
        print(f"  not committed {e.stderr.strip() or e}  (the files are in place; commit them yourself)")


if __name__ == "__main__":
    sys.exit(main())
