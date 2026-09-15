"""config.py — every path this plugin touches, resolved in ONE place. stdlib only.

Nothing else in the plugin may name a directory. A hook that hard-codes a path works on one
machine and is a lie everywhere else, so the rule is: read it here or do not read it.

Precedence, per key, highest first:

  1. an environment variable — GEDAECHTNIS_VAULT · GEDAECHTNIS_STATE_DIR ·
     GEDAECHTNIS_FLEET_ROSTER · GEDAECHTNIS_WORKTREES · GEDAECHTNIS_USER_MEMORY
  2. a JSON object at ~/.claude/gedaechtnis/config.json (point GEDAECHTNIS_CONFIG somewhere
     else to move it; the test suite does exactly that, so no test ever reads or writes the
     real state directory)
  3. a default

Recognised JSON keys, all optional:

  vault               the memory vault's root directory
  roots               directories init.py looks under, two levels deep, for git repos to offer
                      a memory to (env GEDAECHTNIS_ROOTS, colon-separated, wins). `~` is
                      expanded. Default: the list in DEFAULT_ROOTS below — the common places
                      people keep checkouts. It is deliberately NOT exhaustive: a JetBrains
                      install, for one, keeps projects in a directory this plugin may not name
                      (`tools/publish_check.py` refuses that literal anywhere in the tree), so a
                      user whose repos live somewhere unusual adds one line here rather than
                      waiting for the list to grow. Nothing outside these roots and Claude
                      Code's own project list is ever looked at.
  declined            absolute repo paths the user said no to. init.py never offers them again
                      (`--offer-declined` lists them unticked, `--all` ignores the list for one
                      run) and the session-start hook stays quiet in them. Appended by
                      `init.py --decline`; nothing else writes it.
  state_dir           where the hooks keep their logs, cursors and mode file
  fleet_roster        a markdown file whose `repo:` lines name the repos a SHA may live in
  worktrees_dir       where worktree.py puts its copy-on-write clones
  user_memory         the user-level CLAUDE.md loaded into every session (default
                      ~/.claude/CLAUDE.md); the session-start hook prices its @-import
                      chain so a session knows what its own boot cost
  owner_pages_status  path to a script reporting whether review pages have been answered;
                      when it is absent the session-start hook simply says nothing about them
  python              interpreter used to run that script (default: the one running the hook)
  tool_root           the checkout the plugin lives in, used to find sibling tools it calls
                      (default: the directory two levels above this file)
  claim_tool          the region-claim helper the session-claim hook calls
                      (default: <tool_root>/skills/atlas-region/helpers/region_claim.sh; when
                      no such file exists the hook does nothing at all)
  auto_claim          claim this session's region at SessionStart and release it at Stop
                      (default: true — see claim.py for why it is opt-out, not opt-in)
  auto_commit         at Stop, stage and commit this lane's declared vault paths (default: true;
                      an unknown lane commits nothing, ever — see commit.py)
  inject_rules        inject rules/operating-rules.md into every session that STARTS, so a
                      stranger's CLAUDE.md can stay empty (default: true; startup only, never
                      on a resume or a compact — see session_start.py)
  language            which display name a role-file stem is shown under (see names.py):
                      `en` (default) — Decisions, Status, Mistakes, …; `de` — Entscheidungen,
                      Stand, Fehler, … (built into names.json, off by default); `latin` — the
                      stem itself, unchanged. The disk keeps the stem either way (`Canon.md`
                      never becomes `Decisions.md`) — this only changes what a session CALLS
                      the file. env GEDAECHTNIS_LANGUAGE wins.
  context_economy     non-blocking PreToolUse notices (see hooks/context_economy.py): re-reading
                      a file whose content has not changed since this session last read it, and
                      reading a large file whole without offset/limit. Both always ALLOW — the
                      notice rides `additionalContext`, nothing is ever refused (default true).
                      env GEDAECHTNIS_CONTEXT_ECONOMY.
  big_read_kb         the size, in KB, above which a whole-file read (no offset/limit) gets the
                      big-read notice (default 24). env GEDAECHTNIS_BIG_READ_KB.

Example ~/.claude/gedaechtnis/config.json:

    {
      "vault": "~/Gedaechtnis",
      "state_dir": "~/.claude/gedaechtnis",
      "worktrees_dir": "~/.claude/worktrees"
    }

Defaults: vault ~/Gedaechtnis · state_dir ~/.claude/gedaechtnis · fleet_roster
<vault>/Global/fleet-roster.md · worktrees_dir ~/.claude/worktrees. ONE deliberate exception:
if nothing names a vault and ~/Atlas is an Atlas VAULT — proved by the file
~/Atlas/Global/fleet-roster.md, not by the directory's name — then ~/Atlas is the vault. The
first vault this plugin was written against is called Atlas, and a machine that already has one
must not silently be given a second, empty one. A directory that merely happens to be called
Atlas (a photo folder, an unrelated project) is NOT adopted: adopting it would point every hook
at a stranger's files (council 3 K-1, 2026-09-10).
"""
from __future__ import annotations
import json, os
from pathlib import Path


def home() -> Path:
    """`~`, resolved now. A test that moves HOME moves every path below it."""
    return Path(os.path.expanduser("~"))


def config_path() -> Path:
    return Path(os.environ.get("GEDAECHTNIS_CONFIG",
                               str(home() / ".claude" / "gedaechtnis" / "config.json"))).expanduser()


def _load() -> dict:
    """The JSON layer. A missing or malformed file is not an error: the plugin falls through to
    its defaults rather than refusing to start — a config bug must never take a session down."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# `FILE` is NOT a cached snapshot any more. It was one, and every accessor that read it returned a
# value frozen at import — the same defect as the frozen paths, one layer down: a user editing the
# config file, or a test writing one, was not seen until the process restarted. It stays as a NAME
# because it is part of this module's published surface, and resolves through the same PEP 562
# `__getattr__` as the paths do, so every read of it re-reads the file.


def _path(env: str | None, key: str, default) -> Path:
    """Resolve ONE path, NOW — environment, then the config file re-read from disk, then a default.

    The file layer is `_load()` and not the module-level `FILE`, because this function is what the
    accessors below call on EVERY access and a cached dict would make the config file's value
    frozen at import even when the path around it is not. `FILE` survives as the name other code
    already reads; nothing here consults it."""
    raw = os.environ.get(env) if env else None
    if not raw:
        v = _load().get(key)
        raw = str(v) if v else None
    if not raw:
        raw = str(default() if callable(default) else default)
    return Path(os.path.expanduser(raw))


def _default_vault() -> Path:
    """~/Gedaechtnis, unless an Atlas VAULT is already on this machine (see the module docstring).

    The test is the roster FILE, never the directory's name: `~/Atlas` proves it is a vault by
    carrying `Global/fleet-roster.md`. A bare directory called Atlas is somebody's photos."""
    h = home()
    atlas = h / "Atlas"
    return atlas if (atlas / "Global" / "fleet-roster.md").is_file() else h / "Gedaechtnis"


# ---------------------------------------------------------------- the paths, resolved PER CALL
#
# ★ These are FUNCTIONS, and the module-level names that used to hold their values are gone.
#
# They were constants: `VAULT = _path(...)`, evaluated once when the module was first imported and
# cached in `sys.modules` for the life of the process. On 2026-09-15 a test set GEDAECHTNIS_VAULT
# and reloaded the module that uses it — but `config` had already been imported by an earlier test,
# so the override was read by nobody and the package compacted 263 files of the user's real vault
# under a 1,500-byte test bound.
#
# The lesson is not "reload harder". A path that can change during a process must be READ when it
# is used, not when the module is loaded — the import order of an unrelated test is not a sensible
# thing for the location of somebody's memory to depend on. The cost is a `_load()` and an
# `expanduser()` per access, measured at roughly 20 µs; the hooks make a few dozen such calls per
# invocation, so it is not a cost anyone can observe.
#
# `__getattr__` below keeps `config.VAULT` working as a spelling (PEP 562), and every read of it
# now goes through `vault()`. `from config import VAULT` still binds once, which is why the
# re-export sites were converted to attribute access — see `tests/test_percall_resolution.py`,
# which fails if a module-level binding comes back.

def vault() -> Path:
    return _path("GEDAECHTNIS_VAULT", "vault", _default_vault)


def state() -> Path:
    return _path("GEDAECHTNIS_STATE_DIR", "state_dir", home() / ".claude" / "gedaechtnis")


def user_memory() -> Path:
    """The user-level memory file Claude Code loads into EVERY session, whatever the project.
    `~/.claude/CLAUDE.md` is the tool's own convention, not one machine's layout, so the default is
    portable — but it is still resolved here rather than named at a call site, because the rule
    this module exists for has no exceptions: read it here or do not read it."""
    return _path("GEDAECHTNIS_USER_MEMORY", "user_memory", home() / ".claude" / "CLAUDE.md")


def roster() -> Path:
    return _path("GEDAECHTNIS_FLEET_ROSTER", "fleet_roster", lambda: vault() / "Global" / "fleet-roster.md")


def worktrees() -> Path:
    return _path("GEDAECHTNIS_WORKTREES", "worktrees_dir", home() / ".claude" / "worktrees")


def tool_root() -> Path:
    return _path("GEDAECHTNIS_TOOL_ROOT", "tool_root", lambda: Path(__file__).resolve().parents[2])


_ACCESSORS = {"FILE": _load, "HOME": home, "VAULT": vault, "STATE": state, "USER_MEMORY": user_memory,
              "ROSTER": roster, "WORKTREES": worktrees, "TOOL_ROOT": tool_root,
              "CONFIG_PATH": config_path}


def __getattr__(name: str):
    """`config.VAULT` -> `vault()`, freshly, on every read (PEP 562).

    This is what lets the old spelling survive without the old defect. It fires only for names this
    module does not define, so removing the assignments above was the load-bearing half: an
    assignment would shadow it and silently restore the cached constant."""
    fn = _ACCESSORS.get(name)
    if fn is not None:
        return fn()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_ACCESSORS))


def claim_tool() -> Path | None:
    """The §2.8 region-claim helper, or None when this install has no such tool.

    Derived, never hard-coded: a checkout that carries the helper is found through TOOL_ROOT
    (this file's own grandparent by default), and an install that does not carry one — the
    plugin symlinked on its own into a skills directory — simply has no claim hook. None is
    the ordinary answer, not an error: the caller no-ops silently."""
    raw = os.environ.get("GEDAECHTNIS_CLAIM_TOOL") or _load().get("claim_tool")
    p = (Path(os.path.expanduser(str(raw))) if raw
         else tool_root() / "skills" / "atlas-region" / "helpers" / "region_claim.sh")
    return p if p.is_file() else None


def owner_pages_status() -> Path | None:
    """The optional answered-pages script, or None when nothing configures one."""
    v = _load().get("owner_pages_status")
    if not v:
        return None
    p = Path(os.path.expanduser(str(v)))
    return p if p.is_file() else None


def python() -> str:
    """The interpreter used to run the optional script above."""
    v = _load().get("python")
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


# The directories init.py looks under for repos to offer a memory to. Two levels deep, `.git`
# present, nothing else. HOME itself is never searched — see the `roots` note in the docstring
# for why this list is short and how a user extends it.
DEFAULT_ROOTS = ["~/Projects", "~/projects", "~/src", "~/code", "~/dev", "~/repos",
                 "~/Developer", "~/IdeaProjects", "~/AndroidStudioProjects",
                 "~/Documents/GitHub", "~/go/src"]


def roots() -> list:
    """-> [Path] the search roots, environment first, then the config file, then DEFAULT_ROOTS.

    GEDAECHTNIS_ROOTS is colon-separated, like PATH. Every entry is `~`-expanded; a path that
    does not exist is kept in the list and simply matches nothing, so the screen can still name
    where it looked."""
    env = os.environ.get("GEDAECHTNIS_ROOTS")
    if env is not None and env.strip():
        raw = [s for s in env.split(":") if s.strip()]
    else:
        v = _load().get("roots")
        raw = [str(x) for x in v if str(x).strip()] if isinstance(v, list) else list(DEFAULT_ROOTS)
    return [Path(os.path.expanduser(s.strip())) for s in raw]


def declined() -> list:
    """-> [str] the absolute repo paths the user has said no to. Re-read from disk on every call:
    `init.py --decline` appends to the file, and a hook running afterwards must see it."""
    v = _load().get("declined")
    return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []


def write_keys(updates: dict, path=None) -> None:
    """Merge `updates` into the config file and replace it atomically, preserving every other key.

    Temp file plus os.replace, because a config file half-written by a crash is a machine with no
    vault: the reader falls through to its defaults and quietly starts a second, empty one. And a
    MERGE rather than a write, because two commands write this file for different reasons — the
    installer names the vault, `--decline` records a refusal — and either one replacing it wholesale
    would silently drop the other's key."""
    p = Path(os.path.expanduser(str(path))) if path else config_path()
    data = {}
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass
    data.update(updates)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(p))


def flag(key: str, default: bool = False) -> bool:
    """A boolean policy from the JSON layer (env GEDAECHTNIS_<KEY>=1/0 wins). Owner-fleet policies such as
    `require_agent_model` and `require_launch_effort` default to False: a stranger's install must not deny
    an Agent call or a launch on first use for a rule that is one owner's cost policy."""
    env = os.environ.get("GEDAECHTNIS_" + key.upper())
    if env is not None:
        return env.strip() not in ("", "0", "false", "no")
    v = _load().get(key)
    return bool(v) if v is not None else default


def big_read_kb() -> int:
    """KB threshold above which a whole-file Read/cat with no offset/limit gets the BIG-READ
    notice (hooks/context_economy.py). Re-read from disk on every call, same reasoning as
    `declined()`/`language()`. GEDAECHTNIS_BIG_READ_KB wins; default 24."""
    env = os.environ.get("GEDAECHTNIS_BIG_READ_KB")
    if env is not None and env.strip():
        try:
            return int(env.strip())
        except ValueError:
            pass
    v = _load().get("big_read_kb")
    try:
        return int(v) if v is not None else 24
    except (TypeError, ValueError):
        return 24


def language() -> str:
    """`en` (default), `de`, or `latin` — which display name names.py shows a stem under.
    Re-read from disk on every call, same reasoning as `declined()`: a config edit must be seen
    by the next hook that asks, not only the next process restart. GEDAECHTNIS_LANGUAGE wins."""
    env = os.environ.get("GEDAECHTNIS_LANGUAGE")
    if env and env.strip():
        return env.strip().lower()
    v = _load().get("language")
    s = str(v).strip().lower() if v else ""
    return s or "en"
