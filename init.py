#!/usr/bin/env python3
"""init.py — stand up a vault, a region and a lane for one repo, asking nothing. stdlib only.

    python3 init.py [--repo DIR] [--vault DIR] [--lane NAME] [--region NAME] [--dry-run] [--yes]

Run from inside a project (or point `--repo` at one) and it creates everything the hooks need,
creating ONLY what is absent. An existing file is reported as `kept` and is never touched, so the
command is safe to run again: a second run changes nothing.

Defaults, when no flag says otherwise:

  repo    the current directory
  lane    the repo's basename upper-cased, non-alphanumerics -> `-`   (my_project -> MY-PROJECT)
          — or, when the repo already has a `.atlas-lane` marker, the lane that marker declares
  region  the repo's basename in CamelCase                             (my_project -> MyProject)
  vault   what hooks/config.py resolves: ~/Gedaechtnis, or an existing ~/Atlas, or the vault
          named in ~/.claude/gedaechtnis/config.json

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

Refuses, exit 2, with one sentence: the vault path exists but is not a directory; `--region`
differs only in case from a directory already in the vault (a case-insensitive filesystem would
silently merge the two); the repo is not a directory.

`--yes` skips the confirmation that is asked only when stdin is a terminal.

Every path comes from hooks/config.py — nothing here names a directory of its own — which is also
what lets the test suite run this against a temporary HOME and never touch a real vault.
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN / "hooks"))
import config  # noqa: E402  (every path is resolved there)

CORE_SIX = ("Map", "Position", "Canon", "Patterns", "Errata", "Aporia")
ROSTER_FENCE = "fleet-roster"
GIT_IDENTITY = ["-c", "user.name=atlas", "-c", "user.email=atlas@local"]   # the vault's own identity, as ledger.py uses


# ------------------------------------------------------------------ names ----
def lane_name(basename: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", basename).strip("-").upper()
    return s or "LANE"


def region_name(basename: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", basename) if p]
    return "".join(p[0].upper() + p[1:] for p in parts) or "Region"


def home_relative(p: Path) -> str:
    """A `repo:` roster value is $HOME-relative; a repo outside HOME is written absolute."""
    try:
        return str(p.relative_to(config.HOME))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------- templates ----
def t_map(region: str) -> str:
    return f"""# {region} — Map

## Purpose

What this project is for, in a few sentences, and what "done" looks like. (This section is the
project's Vision: it changes rarely; everything below it is an index.)

## Files in this folder

- [[Position]] — where the project stands right now: shipped, in flight, deferred, and what comes next
- [[Canon]] — decisions that are settled, each with the reason, so nobody re-argues them
- [[Patterns]] — approaches that worked more than once
- [[Errata]] — mistakes made and how they were fixed, so they are not made twice
- [[Aporia]] — open questions nobody has answered yet

Other files appear here as they are needed; nothing has to be created in advance.
"""


def t_position(region: str) -> str:
    return f"""# {region} — Position

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
    return f"""# {region} — Canon

Locked decisions. One entry per decision: what was decided, when, and why. Check here before
re-opening a question; supersede an entry in place rather than deleting it.

## (first decision)

**Decided:** YYYY-MM-DD. **Why:** …
"""


def t_patterns(region: str) -> str:
    return f"""# {region} — Patterns

Approaches that have worked at least twice. Each entry states the rule in a few lines; the story
behind it can go beneath.
"""


def t_errata(region: str) -> str:
    return f"""# {region} — Errata

Mistakes and bugs, with what went wrong and what to do instead. Every future session reads this,
so an entry here is how the same mistake is not made twice.
"""


def t_aporia(region: str) -> str:
    return f"""# {region} — Aporia

Open questions. Each carries an urgency (BLOCKING / NEXT / DEFERRED) and the date it was last
looked at; a question answered moves to Canon.
"""


TEMPLATES = {"Map": t_map, "Position": t_position, "Canon": t_canon,
             "Patterns": t_patterns, "Errata": t_errata, "Aporia": t_aporia}


def t_kernel(vault: Path) -> str:
    return f"""# What this vault is

This folder is a memory that outlives any single conversation. Claude Code reads parts of it
when a session starts and writes to it as work happens, so the next session — tomorrow, or on
another project — begins knowing what was decided, what went wrong, and what is still open.

- **One folder per project** (a *region*). Each starts with six files: `Map` (what the project
  is, and an index), `Position` (where it stands), `Canon` (settled decisions), `Patterns`
  (what works), `Errata` (what went wrong) and `Aporia` (open questions). Other files are added
  when there is something to put in them.
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

    # the region: six core files
    for stem in CORE_SIX:
        file(vault / region / f"{stem}.md", TEMPLATES[stem](region))

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
    if cfg_path.exists():
        note = ""
        if vault != config.VAULT:
            note = f"names {config.VAULT} as the vault, not {vault} — the hooks follow the config file"
        steps.append(Step(cfg_path, "kept", note=note))
    else:
        steps.append(Step(cfg_path, "created", lambda: _write(cfg_path, json.dumps({"vault": str(vault)}, indent=2) + "\n"),
                          "names the vault"))
    link = skills_dir / "gedaechtnis"
    if link.exists() or link.is_symlink():
        target = os.readlink(link) if link.is_symlink() else None
        note = "" if target in (None, str(PLUGIN)) else f"symlink points at {target}, not this plugin"
        steps.append(Step(link, "kept", note=note))
    else:
        steps.append(Step(link, "created", lambda: _symlink(link), f"-> {PLUGIN}"))
    return steps


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stand up a vault, a region and a lane for one repo, asking nothing.")
    ap.add_argument("--repo", default=None, help="the project repo (default: the current directory)")
    ap.add_argument("--vault", default=None, help="the vault directory (default: what hooks/config.py resolves)")
    ap.add_argument("--lane", default=None, help="lane name (default: the repo's basename, upper-cased)")
    ap.add_argument("--region", default=None, help="region folder name (default: the repo's basename in CamelCase)")
    ap.add_argument("--dry-run", action="store_true", help="report what would be created; write nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation on a terminal")
    a = ap.parse_args(argv)

    repo = Path(a.repo or os.getcwd()).expanduser().resolve()
    if not repo.is_dir():
        return refuse(f"{repo} is not a directory, so there is no repo to set up.")
    vault = Path(a.vault).expanduser().resolve() if a.vault else config.VAULT
    if vault.exists() and not vault.is_dir():
        return refuse(f"{vault} exists but is not a directory, so it cannot be the vault.")
    region = a.region or region_name(repo.name)
    if not region or "/" in region or region in (".", ".."):
        return refuse(f"{region!r} is not a usable region name.")
    if vault.is_dir():
        for name in os.listdir(vault):
            if name != region and name.lower() == region.lower() and (vault / name).is_dir():
                return refuse(f"the vault already has a directory {name!r}, which differs from {region!r} only in case.")
    marker = repo / ".atlas-lane"
    lane = a.lane or (marker.is_file() and marker_lane(marker)) or lane_name(repo.name)

    cfg_path = config.CONFIG_PATH
    skills_dir = config.HOME / ".claude" / "skills"
    steps = plan(repo, vault, lane, region, cfg_path, skills_dir)

    print(f"Gedächtnis init — repo {repo}, vault {vault}, lane {lane}, region {region}" + (" (dry run)" if a.dry_run else ""))
    todo = [s for s in steps if s.verb != "kept"]
    if todo and not a.dry_run and not a.yes and sys.stdin.isatty():
        try:
            ans = input(f"{len(todo)} path(s) will be created or updated. Proceed? [Y/n] ").strip().lower()
        except EOFError:
            ans = ""
        if ans and ans[0] == "n":
            print("nothing written.")
            return 1

    created: list[Path] = []
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
