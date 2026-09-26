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
  answer_router       path to a command that routes an answered review-page export back to the
                      work it belongs to (see answers.py for the four-key JSON contract);
  owner_pages_status  path to a script reporting whether review pages have been answered;
                      when it is absent the session-start hook simply says nothing about them
  session_name_pattern  a regex matching the `--name` of sessions this installation MANAGES, e.g.
                      `^(?P<seat>[a-z0-9-]+)-(?P<lane>builder|audit)-(?P<qid>[A-Za-z0-9-]+)$`.
                      UNSET = the shipped state = the idle-notify door is entirely off and reads
                      nothing. A `seat` group names who a finished session reports to; an opener's
                      own `seat:` line outranks it
  notify_command      path to a command that DELIVERS one cross-session message: it is run as
                      `<command> <seat>` with the message row as JSON on stdin. Optional — the
                      seat's mailbox under the state dir is the durable record, written before
                      this runs, so an absent or failing transport loses nothing
  suite_gate          `true` holds `git merge` / `git push` in a repository carrying `gedaechtnis/tests`
                      until a full, green test run is recorded for the tree being shipped (see
                      hooks/suitegate.py). Off unless exactly `true`
  stall_minutes       with `session_name_pattern` set, how long a managed session may go without an
                      assistant record before session start lists it as STALLED (default 20)
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
                      reading a large file whole without offset/limit. Both are notes — the text
                      rides `additionalContext` alone, nothing is ever refused and no permission
                      prompt is answered for you (default true).
                      env GEDAECHTNIS_CONTEXT_ECONOMY.
  big_read_kb         the size, in KB, above which a whole-file read (no offset/limit) gets the
                      big-read notice (default 24). env GEDAECHTNIS_BIG_READ_KB.
  topology            an object describing THIS VAULT's own shape where it has grown past the
                      one init.py creates — `non_region_tops`, `queues_dir`, `artifacts_index`,
                      `ledger_dir`, `channels_dir`, `shared_append_files`, `stem_rule_dir`. Every
                      key is absent by default and the rule built on it is then simply OFF; a key
                      that is present and unusable is named by `topology_problems()`. See the
                      TOPOLOGY section at the foot of this file.
  limits              an object of THIS VAULT's threshold overrides, merged over rules/limits.json
                      by hooks/limits.py — e.g. {"boot_budget_bytes": 100000} for a vault whose
                      ruled boot chain is larger than the package default. Only names the package
                      already reads are applied; an unknown key, a `_`-prefixed key, a wrong-typed
                      value or a non-object `limits` is NOT applied and is named by
                      `limits.problems()`, which tools/status.py and the session-start facts print.
                      This module never interprets the object — it only carries it.

Example ~/.claude/gedaechtnis/config.json:

    {
      "vault": "~/Gedaechtnis",
      "state_dir": "~/.claude/gedaechtnis",
      "worktrees_dir": "~/.claude/worktrees"
    }

Defaults: vault ~/Gedaechtnis · state_dir ~/.claude/gedaechtnis · fleet_roster
<vault>/Global/fleet-roster.md · worktrees_dir ~/.claude/worktrees. ONE deliberate exception:
if nothing names a vault and ~/Gedaechtnis does not exist, a directory one level under home that
PROVES itself a vault — by carrying Global/fleet-roster.md — is adopted instead. A machine that
already has a vault must not silently be given a second, empty one. The proof is the roster FILE
and never a directory's NAME: a folder that merely happens to be named after somebody's vault (a
photo folder, an unrelated project) is not adopted, because adopting it would point every hook at
a stranger's files (council 3 K-1, 2026-09-10). Where TWO directories prove themselves, none is
adopted — the machine has to say which (`vault` in the config file), and guessing between two
real vaults is the one answer that could write memory into the wrong one.
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


def unreadable() -> bool:
    """True when a config file EXISTS and cannot be read as a JSON object.

    `_load()` returns `{}` for a missing file and for a broken one, deliberately — a config bug
    must never take a session down. But the two are opposite facts, and a caller that reports on
    what the user configured has to tell them apart: with only `_load()`, a trailing comma in
    `config.json` produced `shipped defaults; config.json overrides none` — a clean bill of health
    for a vault that is configured and applying none of it. The swallow stays; this says whether
    there was something to swallow."""
    p = config_path()
    if not p.is_file():
        return False
    try:
        return not isinstance(json.loads(p.read_text(encoding="utf-8")), dict)
    except (OSError, ValueError):
        return True


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


_ADOPTED: dict = {}


def _default_vault() -> Path:
    """~/Gedaechtnis, unless a vault is already on this machine (see the module docstring).

    The test is the roster FILE, never the directory's NAME — a package that looked for one
    particular directory name would adopt somebody's photo folder that happens to share it, and
    would miss every vault called anything else. `~/Gedaechtnis` wins when it exists, because that
    is the name this package creates; otherwise the immediate children of home are asked, once,
    and adopted only when EXACTLY ONE of them answers.

    The scan is memoised PER HOME rather than per process: it is a directory listing, and this
    function is called on every path access. Keying it on `home()` is what keeps a test that moves
    HOME from reading the real machine's answer — the frozen-path defect this module exists to
    avoid, one layer down."""
    h = home()
    mine = h / "Gedaechtnis"
    if mine.is_dir():
        return mine
    key = str(h)
    if key not in _ADOPTED:
        found = []
        try:
            for child in sorted(h.iterdir()):
                if child.name.startswith(".") or not child.is_dir():
                    continue
                if (child / "Global" / "fleet-roster.md").is_file():
                    found.append(child)
                    if len(found) > 1:
                        break                 # ambiguous: stop looking, adopt neither
        except OSError:
            found = []
        _ADOPTED[key] = found[0] if len(found) == 1 else None
    return _ADOPTED[key] or mine


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
    raw = os.environ.get("GEDAECHTNIS_CLAIM_TOOL") or _load().get("claim_tool")
    configured = bool(raw)
    p = (Path(os.path.expanduser(str(raw))) if raw
         else tool_root() / "skills" / "atlas-region" / "helpers" / "region_claim.sh")
    if not p.is_file():
        if configured:
            return None, "off-missing", (
                f"OFF — the configured claim helper {p} does not exist. Nothing is claimed or "
                f"released this session; fix `claim_tool` (or $GEDAECHTNIS_CLAIM_TOOL) or drop it.")
        return None, "off-no-helper", (
            f"OFF — this install carries no region-claim helper ({p} does not exist), so no region "
            f"is claimed or released. Point `claim_tool` in {config_path()} (or "
            f"$GEDAECHTNIS_CLAIM_TOOL) at one to turn it on.")
    if not flag("auto_claim", True):
        return p, "off-disabled", (
            f"OFF — a helper is installed ({p}) but `auto_claim` is false, so nothing is claimed "
            f"or released this session.")
    return p, "ready", f"ON — claims are taken at SessionStart and released at Stop, via {p}."


def owner_pages_status() -> Path | None:
    """The optional answered-pages script, or None when nothing configures one."""
    v = _load().get("owner_pages_status")
    if not v:
        return None
    p = Path(os.path.expanduser(str(v)))
    return p if p.is_file() else None


def answer_router() -> "Path | None":
    """The optional answer-routing command, or None when nothing configures one.

    Named at the TOP LEVEL beside `owner_pages_status`, not inside `topology`, and the reason is
    the topology contract itself: every `topology` value is VAULT-RELATIVE by construction
    (`_topo_rel` refuses an absolute path or a `..` segment, because those values are joined onto
    the vault). A router is a SCRIPT, it lives in a repo rather than in the vault, and its path is
    absolute — putting it under `topology` would mean breaking the invariant that makes every
    other topology key safe. `owner_pages_status` is the same kind of thing and sits in the same
    place; this follows it.

    Unset is the shipped state and costs nothing: a vault with no review pages has no answers to
    route. The contract a configured command must meet is in `answers.py`'s docstring.
    """
    v = _load().get("answer_router")
    if not v:
        return None
    p = Path(os.path.expanduser(str(v)))
    return p if p.is_file() else None


def session_name_pattern() -> "str | None":
    """The regular expression that says which session names this installation MANAGES, or None.

    OFF BY DEFAULT, and that is the whole safety story for the idle-notify door: with no pattern
    configured, no session is a managed session, nothing is ever read about one, and nothing is
    ever sent. A fleet that launches its sessions with `--name` opts in by naming the shape it
    uses, e.g. `^(?P<seat>[a-z0-9-]+)-(?P<lane>[a-z]+)-(?P<qid>[A-Z0-9-]+)$`.

    Top level beside `answer_router` and `invariants_log`, and for the same stated reason: every
    `topology` value is vault-RELATIVE by construction, and this is neither a path nor vault
    content. A pattern that does not COMPILE is not applied, and is NAMED at session start
    (`idlenotify.pattern_problem`) rather than raising — a hook that died on a typo in a config
    file would take the session with it, and a pattern that silently matched nothing would leave
    a fleet believing its sessions report when none of them do."""
    v = _load().get("session_name_pattern")
    if not v or not isinstance(v, str):
        return None
    return v


def notify_command() -> "Path | None":
    """The optional command that DELIVERS a cross-session message, or None.

    This package can decide WHAT to say to a seat and WHEN; it cannot know HOW this installation
    passes a message between two live sessions — that is a property of the harness and of the
    fleet's own tooling, and a plugin that hardcoded one transport would be carrying somebody
    else's fleet around. So delivery is a seam, and it is not the durable record: the message is
    appended to the seat's mailbox FIRST and the command runs after, so a transport that is
    missing, wedged or slow loses nothing.
    """
    v = _load().get("notify_command")
    if not v:
        return None
    p = Path(os.path.expanduser(str(v)))
    return p if p.is_file() else None


def invariants_log() -> "Path | None":
    """The optional nightly invariant-run log, or None when nothing configures one.

    Top level rather than a `topology` key, for the reason `answer_router` above states in full:
    every `topology` value is VAULT-RELATIVE by construction, and this is a log inside a REPO, so
    its path is absolute. Putting it under `topology` would break the invariant that makes every
    other topology key safe.

    Unset is the shipped state. A vault whose fleet runs no nightly has no line to report, and the
    session-start arm then says nothing — the correct output, and not the same as a nightly that
    ran and failed."""
    v = _load().get("invariants_log")
    if not v:
        return None
    p = Path(os.path.expanduser(str(v)))
    return p if p.is_file() else None


def lesson_reoccurrence() -> "Path | None":
    """The optional re-occurrence census TSV, or None when nothing configures one.

    Named in the config file (`lesson_reoccurrence`) for the reason `authority_log` is: how often
    a lesson has been re-learned is the output of a census somebody ran over their own sessions,
    not a fact this package can compute. Unset is the shipped state and costs only a tie-break."""
    v = _load().get("lesson_reoccurrence")
    if not v:
        return None
    p = Path(os.path.expanduser(str(v)))
    return p if p.is_file() else None


def authority_log() -> "Path | None":
    """The installation's append-only authority ledger, or None when nothing configures one.

    Named in the config file (`authority_log`) rather than known by this package, because an
    authority ledger is a convention of the installation and not of the memory system: a plugin
    that hardcoded one repo's path would be carrying somebody else's vault around. Unset is the
    shipped state, and every door built on this is inert until a vault names its own.
    """
    v = _load().get("authority_log")
    if not v:
        return None
    return Path(os.path.expanduser(str(v)))


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


# ---------------------------------------------------------------- this vault's own TOPOLOGY
#
# ★ A vault's SHAPE is the installation's, not the package's.
#
# The shipped shape is the one `init.py` creates and nothing more: `<vault>/Global/` for what
# applies everywhere, `<vault>/<Region>/` (or `<vault>/<Umbrella>/<Region>/`) for memory. A vault
# that has grown a work-queue folder, per-lane outboxes, a cross-lane ledger, an artifact index,
# umbrella files several lanes append to, or a folder whose file names are reserved has grown
# CONVENTIONS — and a package that carried one installation's conventions as constants would be
# carrying somebody else's vault around, and would be wrong about every other one.
#
# So each of them is a KEY here. Whether it has a DEFAULT is decided by one question — does this
# package CREATE that folder itself? Three do: `init.py` makes `Global/` and `ledger.py` makes
# `Channels/ledger/`, so `non_region_tops` (`Global`, `Channels`), `ledger_dir` and `channels_dir`
# default. The other four — `queues_dir`, `artifacts_index`, `shared_append_files`,
# `stem_rule_dir` — name folders no part of this package creates, so they are ABSENT by default
# and the rule built on each is then simply OFF. That is a supported state and a STATED one:
# `topology_problems()` names a key that is present and unusable, and `tools/status.py` and the
# session-start facts print it.
#
# Example, for a vault that has grown all of them:
#
#     "topology": {
#       "non_region_tops":     ["Global", "Queues", "Channels", "Workflows", "Council"],
#       "queues_dir":          "Queues/regions",
#       "artifacts_index":     "Queues/artifacts-index.md",
#       "ledger_dir":          "Channels/ledger",
#       "channels_dir":        "Channels",
#       "shared_append_files": ["Umbrella/Decisions.md", "Umbrella/Status.md"],
#       "stem_rule_dir":       "Council"
#     }
#
# A hidden top-level directory (`.git`, `.obsidian`, anything dot-prefixed) is never a region and
# never needs listing — that is a rule about the shape of a name, not about one vault's layout.

# `Global/` and `Channels/` are the package's OWN: `init.py` creates the first and `ledger.py`
# creates the second. Everything else a vault keeps at top level is that vault's and is named in
# the config file.
DEFAULT_NON_REGION_TOPS = ("Global", "Channels")
DEFAULT_LEDGER_DIR = "Channels/ledger"
DEFAULT_CHANNELS_DIR = "Channels"

_TOPOLOGY_KEYS = {
    "non_region_tops": list,
    "queues_dir": str,
    "artifacts_index": str,
    "ledger_dir": str,
    "channels_dir": str,
    "shared_append_files": list,
    "stem_rule_dir": str,
}


def topology() -> dict:
    """The raw `topology` object from the config file, or `{}`. Never interpreted here beyond
    being a dict — the accessors below do the reading, one key each."""
    v = _load().get("topology")
    return v if isinstance(v, dict) else {}


def topology_problems() -> list:
    """-> [str] one sentence per topology entry that is PRESENT and will not be applied.

    Same contract as `limits.problems()`, and for the same reason: a key that is silently ignored
    reads exactly like a key that is working. An unknown name, a wrong type, and an absolute or
    `..`-bearing path are all named. An entirely absent `topology` is not a problem — it is the
    shipped state."""
    raw = _load().get("topology")
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return [f"`topology` in {config_path()} is {type(raw).__name__}, not an object; ignored."]
    out = []
    for k, v in raw.items():
        want = _TOPOLOGY_KEYS.get(k)
        if want is None:
            out.append(f"`topology.{k}` is not a key this package reads; ignored.")
            continue
        if not isinstance(v, want):
            out.append(f"`topology.{k}` is {type(v).__name__}, not {want.__name__}; ignored.")
            continue
        vals = v if want is list else [v]
        for s in vals:
            if not isinstance(s, str) or not s.strip():
                out.append(f"`topology.{k}` carries a non-string entry; ignored.")
            elif s.strip().startswith("/") or ".." in s.strip().split("/"):
                out.append(f"`topology.{k}` entry {s!r} is not vault-relative; ignored.")
    return out


def _topo_rel(key: str) -> "str | None":
    """One vault-RELATIVE path from the topology object, or None. Rejects an absolute path and
    any `..` segment: these values are joined onto the vault, so a value that escapes it would
    point every rule built on this key at a stranger's files."""
    v = topology().get(key)
    if not isinstance(v, str) or not v.strip():
        return None
    raw = v.strip()
    # ★ The ABSOLUTE test is on the RAW value, BEFORE the trailing-slash tidy. The first spelling
    # stripped both ends first, so `/etc` arrived here as `etc` and `startswith("/")` could never
    # fire: an absolute path was not refused, it was silently REWRITTEN into a vault-relative one
    # and every rule on that key pointed at `<vault>/etc`. Found by the test below, which exists
    # because a mutation removing this guard reddened nothing (reviewer, 2026-09-20).
    if raw.startswith("/") or ".." in raw.split("/"):
        return None
    s = raw.strip("/")
    return s or None


def _topo_list(key: str) -> list:
    v = topology().get(key)
    if not isinstance(v, list):
        return []
    # Same order as `_topo_rel`: absolute and `..` are judged on the RAW value.
    out = []
    for s in v:
        if not isinstance(s, str) or not s.strip():
            continue
        raw = s.strip()
        if raw.startswith("/") or ".." in raw.split("/"):
            continue
        if raw.strip("/"):
            out.append(raw.strip("/"))
    return out


def non_region_tops() -> frozenset:
    """Top-level vault directories that hold no region. Default: the shipped `Global/` alone.

    A configured list REPLACES the default rather than adding to it — a vault that renames its
    shared folder must be able to say so — so a config naming its own set should name `Global`
    too if it still has one."""
    v = _topo_list("non_region_tops")
    return frozenset(v) if v else frozenset(DEFAULT_NON_REGION_TOPS)


def queues_dir() -> "Path | None":
    """The directory of per-region work-queue files, or None when this vault has none."""
    rel = _topo_rel("queues_dir")
    return (vault() / rel) if rel else None


def queues_rel() -> "str | None":
    """`queues_dir` as a vault-relative string (the form the path rules compare against)."""
    return _topo_rel("queues_dir")


def artifacts_index() -> "Path | None":
    """The vault file published artifacts are indexed in, or None when this vault indexes none."""
    rel = _topo_rel("artifacts_index")
    return (vault() / rel) if rel else None


def artifacts_index_rel() -> "str | None":
    return _topo_rel("artifacts_index")


def ledger_rel() -> "str | None":
    """The cross-lane ledger directory, vault-relative. Unlike the keys above this HAS a default:
    `ledger.py` ships with the package and creates `Channels/ledger/` itself, so it is the
    package's own convention and not one vault's."""
    return _topo_rel("ledger_dir") or DEFAULT_LEDGER_DIR


def channels_rel() -> "str | None":
    """The per-lane outbox directory, vault-relative. Defaults with the ledger above, and for the
    same reason: the folder is the package's own."""
    return _topo_rel("channels_dir") or DEFAULT_CHANNELS_DIR


def shared_append_files() -> tuple:
    """Vault-relative files that any lane may APPEND a row to and nobody may rewrite. Empty by
    default: a vault with no shared surfaces of this kind has none, and inventing one would open
    a partition the installation never declared."""
    return tuple(_topo_list("shared_append_files"))


def stem_rule_dir() -> "str | None":
    """A vault-relative directory in which a file may NOT carry a memory-role stem, or None.

    Some vaults keep a folder whose files are named by their own table and whose names would
    otherwise be picked up by every rule that walks for role files. Where an installation names
    one, the write door refuses a role stem inside it; where none is named, there is no such
    rule."""
    return _topo_rel("stem_rule_dir")


# The four rules that act only on paths a vault's `topology` names. Each is OFF — not failing, not
# warning, simply absent — until its key is declared, so an undeclared key and a working one look
# the same from inside a session. `inert_topology_doors` is what lets the boot say which are off.
TOPOLOGY_DOORS = (
    ("queues_dir", "work-queue files (append a row, never rewrite; kept out of recall)"),
    ("artifacts_index", "the artifact index (append a row, never rewrite)"),
    ("shared_append_files", "shared files any lane may append to"),
    ("stem_rule_dir", "the reserved-name folder (no memory-role file names)"),
)


def inert_topology_doors() -> list:
    """-> [(key, rule)] for each topology-dependent rule whose key this vault does not declare
    (absent, empty, or rejected by `topology_problems`). Empty when all four are declared."""
    declared = {"queues_dir": queues_rel(), "artifacts_index": artifacts_index_rel(),
                "shared_append_files": shared_append_files(), "stem_rule_dir": stem_rule_dir()}
    return [(k, rule) for k, rule in TOPOLOGY_DOORS if not declared.get(k)]


def non_memory_tops() -> frozenset:
    """Top-level directories that are OPERATIONAL rather than memory — work queues and lane
    notice outboxes — derived from the first segment of `queues_dir` and `channels_dir`.

    Derived rather than listed, so a vault says where its queues are ONCE. `Global/` is never in
    here: it is shared memory and the shipped vault's own, which is exactly why it is excluded
    from this set while appearing in `non_region_tops()`."""
    out = set()
    for rel in (queues_rel(), channels_rel(), ledger_rel()):
        if rel:
            out.add(rel.split("/")[0])
    return frozenset(out)


def non_role_tops() -> frozenset:
    """Top-level directories that carry no memory ROLE files — every non-region top except the
    shared `Global/`, which does. This is what a vault WALKER skips."""
    return frozenset(non_region_tops() - {"Global"})
