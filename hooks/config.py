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
  claim_tool          the region-claim helper the session-claim hook calls (default:
                      <tool_root>/skills/atlas-region/helpers/region_claim.sh). The package ships
                      no copy of that helper, so on an ordinary install the file is absent and the
                      region claim is simply OFF — a supported state, and a STATED one: the claim
                      hook and `tools/status.py` print which state this install is in rather than
                      falling silent (see `claim_tool_state` below)
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
    """~/Gedaechtnis, unless an Atlas VAULT is already on this machine (see the module docstring).

    The test is the roster FILE, never the directory's name: `~/Atlas` proves it is a vault by
    carrying `Global/fleet-roster.md`. A bare directory called Atlas is somebody's photos."""
    atlas = HOME / "Atlas"
    return atlas if (atlas / "Global" / "fleet-roster.md").is_file() else HOME / "Gedaechtnis"


VAULT = _path("GEDAECHTNIS_VAULT", "vault", _default_vault)
STATE = _path("GEDAECHTNIS_STATE_DIR", "state_dir", HOME / ".claude" / "gedaechtnis")
# The user-level memory file Claude Code loads into EVERY session, whatever the project.
# ~/.claude/CLAUDE.md is the tool's own convention, not one machine's layout, so the default
# is portable — but it is still resolved here rather than named at a call site, because the
# rule this module exists for has no exceptions: read it here or do not read it.
USER_MEMORY = _path("GEDAECHTNIS_USER_MEMORY", "user_memory", HOME / ".claude" / "CLAUDE.md")
ROSTER = _path("GEDAECHTNIS_FLEET_ROSTER", "fleet_roster", VAULT / "Global" / "fleet-roster.md")
WORKTREES = _path("GEDAECHTNIS_WORKTREES", "worktrees_dir", HOME / ".claude" / "worktrees")


TOOL_ROOT = _path("GEDAECHTNIS_TOOL_ROOT", "tool_root", lambda: Path(__file__).resolve().parents[2])


def claim_tool() -> Path | None:
    """The §2.8 region-claim helper, or None when this install has no such tool.

    Derived, never hard-coded: a checkout that carries the helper is found through TOOL_ROOT
    (this file's own grandparent by default), and an install that does not carry one — the
    plugin symlinked on its own into a skills directory — simply has no claim hook. None is
    the ordinary answer, not an error: the caller no-ops silently."""
    return claim_tool_state()[0]


def claim_tool_state() -> tuple[Path | None, str, str]:
    """(tool or None, state, one sentence naming the state) — the SAME answer `claim_tool()` gives,
    plus why, so a session can be TOLD which of these it is in instead of inferring it from silence.

    The helper is a fleet asset, not the package's: this plugin ships no copy of it (copying a
    trip-wired coordination mechanism would fork it), so out of the box, away from the checkout it
    grew in, the region claim is simply OFF. That is a supported state — one writer per region is a
    vault convention, and a stranger with no lanes has no regions to serialize — but it must be a
    STATED one. `hooks/claim.py` prints the line at SessionStart wherever a claim would otherwise
    have been taken, and `tools/status.py` prints it unconditionally.

    States: `ready` · `off-disabled` (auto_claim false) · `off-missing` (a path IS configured and
    is not there — a misconfiguration, not a default) · `off-no-helper` (nothing configured and the
    derived default does not exist: the ordinary stranger case)."""
    raw = os.environ.get("GEDAECHTNIS_CLAIM_TOOL") or FILE.get("claim_tool")
    configured = bool(raw)
    p = (Path(os.path.expanduser(str(raw))) if raw
         else TOOL_ROOT / "skills" / "atlas-region" / "helpers" / "region_claim.sh")
    if not p.is_file():
        if configured:
            return None, "off-missing", (
                f"OFF — the configured claim helper {p} does not exist. Nothing is claimed or "
                f"released this session; fix `claim_tool` (or $GEDAECHTNIS_CLAIM_TOOL) or drop it.")
        return None, "off-no-helper", (
            f"OFF — this install carries no region-claim helper ({p} does not exist), so no region "
            f"is claimed or released. Point `claim_tool` in {CONFIG_PATH} (or "
            f"$GEDAECHTNIS_CLAIM_TOOL) at one to turn it on.")
    if not flag("auto_claim", True):
        return p, "off-disabled", (
            f"OFF — a helper is installed ({p}) but `auto_claim` is false, so nothing is claimed "
            f"or released this session.")
    return p, "ready", f"ON — claims are taken at SessionStart and released at Stop, via {p}."


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
    p = Path(os.path.expanduser(str(path))) if path else CONFIG_PATH
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
