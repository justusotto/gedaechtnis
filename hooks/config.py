"""config.py — every path this plugin touches, resolved in ONE place. stdlib only.

Nothing else in the plugin may name a directory. A hook that hard-codes a path works on one
machine and is a lie everywhere else, so the rule is: read it here or do not read it.

Precedence, per key, highest first:

  1. an environment variable — GEDAECHTNIS_VAULT · GEDAECHTNIS_STATE_DIR ·
     GEDAECHTNIS_FLEET_ROSTER · GEDAECHTNIS_WORKTREES
  2. a JSON object at ~/.claude/gedaechtnis/config.json (point GEDAECHTNIS_CONFIG somewhere
     else to move it; the test suite does exactly that, so no test ever reads or writes the
     real state directory)
  3. a default

Recognised JSON keys, all optional:

  vault               the memory vault's root directory
  state_dir           where the hooks keep their logs, cursors and mode file
  fleet_roster        a markdown file whose `repo:` lines name the repos a SHA may live in
  worktrees_dir       where worktree.py puts its copy-on-write clones
  owner_pages_status  path to a script reporting whether review pages have been answered;
                      when it is absent the session-start hook simply says nothing about them
  python              interpreter used to run that script (default: the one running the hook)

Example ~/.claude/gedaechtnis/config.json:

    {
      "vault": "~/Gedaechtnis",
      "state_dir": "~/.claude/gedaechtnis",
      "worktrees_dir": "~/.claude/worktrees"
    }

Defaults: vault ~/Gedaechtnis · state_dir ~/.claude/gedaechtnis · fleet_roster
<vault>/Global/fleet-roster.md · worktrees_dir ~/.claude/worktrees. ONE deliberate exception:
if nothing names a vault and ~/Atlas exists, ~/Atlas is the vault — the first vault this plugin
was written against is called Atlas, and a machine that already has one must not silently be
given a second, empty one.
"""
from __future__ import annotations
import json, os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))

CONFIG_PATH = Path(os.environ.get("GEDAECHTNIS_CONFIG",
                                  str(HOME / ".claude" / "gedaechtnis" / "config.json"))).expanduser()


def _load() -> dict:
    """The JSON layer. A missing or malformed file is not an error: the plugin falls through to
    its defaults rather than refusing to start — a config bug must never take a session down."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


FILE = _load()


def _path(env: str | None, key: str, default) -> Path:
    raw = os.environ.get(env) if env else None
    if not raw:
        v = FILE.get(key)
        raw = str(v) if v else None
    if not raw:
        raw = str(default() if callable(default) else default)
    return Path(os.path.expanduser(raw))


def _default_vault() -> Path:
    """~/Gedaechtnis, unless an Atlas vault is already on this machine (see the module docstring)."""
    atlas = HOME / "Atlas"
    return atlas if atlas.is_dir() else HOME / "Gedaechtnis"


VAULT = _path("GEDAECHTNIS_VAULT", "vault", _default_vault)
STATE = _path("GEDAECHTNIS_STATE_DIR", "state_dir", HOME / ".claude" / "gedaechtnis")
ROSTER = _path("GEDAECHTNIS_FLEET_ROSTER", "fleet_roster", VAULT / "Global" / "fleet-roster.md")
WORKTREES = _path("GEDAECHTNIS_WORKTREES", "worktrees_dir", HOME / ".claude" / "worktrees")


def owner_pages_status() -> Path | None:
    """The optional answered-pages script, or None when nothing configures one."""
    v = FILE.get("owner_pages_status")
    if not v:
        return None
    p = Path(os.path.expanduser(str(v)))
    return p if p.is_file() else None


def python() -> str:
    """The interpreter used to run the optional script above."""
    v = FILE.get("python")
    if v:
        p = Path(os.path.expanduser(str(v)))
        if p.is_file():
            return str(p)
    import sys
    return sys.executable or "python3"


def no_trash() -> bool:
    """GEDAECHTNIS_NO_TRASH=1 leaves a bundle/clone in place instead of sending it to the Trash.
    Nothing here ever deletes either way; this only removes the Finder round trip."""
    return bool(os.environ.get("GEDAECHTNIS_NO_TRASH"))
