"""Every mutating call site in the package is either guarded by `rootguard.permit()` or listed here
with a reason. A new one that is neither fails this test.

## Why this file exists, and what it is fixing about the last one

`rootguard.py`'s docstring said: *"which is why `tests/test_rootguard.py` asserts the coverage of the
call sites by reading the source rather than trusting that they were wired."* **That test did not
exist.** Two independent reviews of the BLASTRADIUS-1 work found the same thing on the same day: the
guard built for the 2026-09-15 incident had zero production call sites, and the file describing it
asserted a check that was never written. A guard nobody calls is a comment; a docstring that claims
a check nobody wrote is worse, because it stops the next person looking.

So the claim is made true here, and made true in the shape that survives: not a list of the sites
that exist today, but a rule about any site that ever exists.

## The rule

A *mutating call site* is a call that can change the filesystem: `open()` in a writing mode, the
mutating `os.*` and `shutil.*` functions, the mutating `Path` methods, and a `subprocess` call
carrying a tree-changing `git` subcommand. For each one, in the product tree, one of these must hold:

  1. `rootguard.permit(...)` is called somewhere in the same function; or
  2. the site is in `EXEMPT` below, with a reason a person wrote.

Rule 1 is deliberately coarse — same function, not "on every path to this line". A dataflow-exact
version would be more precise and would not survive contact with the next refactor; this one is
mechanical, and the exemption list is where judgment goes, in writing, where a reviewer can argue
with it.

## What this does NOT prove

That the permit is on the *right* path, or that it runs before the write. It proves that somebody
considered the question at this site and recorded the answer. The behavioural half is
`test_rootguard.py`; this is the coverage half. Neither substitutes for the other.
"""
from __future__ import annotations
import ast
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]

MUT_ATTRS = {("os", "replace"), ("os", "rename"), ("os", "remove"), ("os", "unlink"),
             ("os", "rmdir"), ("os", "removedirs"), ("os", "renames"), ("os", "truncate"),
             ("os", "chmod"), ("os", "symlink"), ("os", "link"), ("os", "mkdir"),
             ("os", "makedirs"), ("os", "utime"),
             ("shutil", "move"), ("shutil", "rmtree"), ("shutil", "copy"), ("shutil", "copy2"),
             ("shutil", "copyfile"), ("shutil", "copytree")}
PATH_MUT = {"write_text", "write_bytes", "unlink", "mkdir", "rmdir", "touch", "chmod",
            "symlink_to", "hardlink_to"}
NOT_PATHS = {"os", "shutil", "sys", "json", "re", "time", "subprocess", "hashlib", "argparse"}
GIT_MUT = {"add", "commit", "rm", "mv", "checkout", "reset", "clean", "restore", "stash",
           "worktree", "init", "apply", "merge", "rebase", "revert", "tag", "branch", "switch"}

# ---------------------------------------------------------------------------------------------
# Every exemption is a claim somebody has to stand behind. Keyed by "<relpath>::<function>".
EXEMPT = {
    # --- the installer. It runs BEFORE the roots exist; creating the vault is its entire job, and
    # every path it writes was either displayed to the user in a plan or typed by them.
    "init.py::plan": "the installer CREATES the vault the user named; there is no root yet to be inside of",
    "init.py::_write": "installer, writing files the plan displayed and the user approved before it ran",
    "init.py::_write_settings": "installer, writing the user's own ~/.claude/settings.json, shown in the plan",
    "init.py::_symlink": "installer, linking the plugin into the skills dir; the target is the fixed PLUGIN constant",
    "init.py::_remove_outage": "installer, removing the outage stamp file it wrote itself",

    # --- CLI output paths. The user typed them. Refusing these would be wrong, not safe: a report
    # is not memory, and a tool that cannot write where it was told is broken.
    "retention.py::inventory": "writes the report path the caller passed on the command line",
    "retention.py::main": "--json / --md write where the user pointed; a report is not memory",
    "shadow_score.py::main": "--out writes the report directory the caller named on the command line",

    # --- the config file's own writer, which IS one of the roots.
    "hooks/config.py::write_keys": "writes the config file (itself a root), or the explicit path a caller passed",

    # --- the state directory, reached only through config.state(); no argument steers the path.
    "hooks/common.py::log": "state dir; the path is config.state() joined to a literal name",
    # COMPACTDOOR-2 round 2 (finding A): the session record written in one rename, never half.
    "hooks/common.py::write_text_atomic": "replaces a file its callers name — every caller passes "
                                          "the state dir (config.state()/common.STATE) joined to a "
                                          "literal name, carrying at most a session id sanitized by "
                                          "safe_sid() or a sha1 of the cwd — through a hidden sibling temp file of its own, "
                                          "chmod to the old file's mode, then os.replace; the temp file "
                                          "is unlinked on any failure, and on Windows a failed rename "
                                          "falls back to the plain write_text it replaced",
    # --- PLATFORM-1: the freedesktop Trash, used instead of a delete on a Linux with no Trash command.
    "hooks/common.py::_freedesktop_trash": "moves a path the caller asked to trash INTO the user's own "
                                            "Trash ($XDG_DATA_HOME or ~/.local/share/Trash, the spec's fixed "
                                            "place), writes its restore record there, and on a failed move "
                                            "removes only the record it created a moment before",
    # TERMOVERLOAD-1 — the launch ledger, close log, proposals, rate-limit stamp, parked messages
    # and launch lock: each is config.state() joined to a literal name (parked: a name sanitized by
    # safe_sid()); read_screen removes only the mkstemp file it created in the same call.
    "hooks/fleet.py::append_launch": "state dir; config.state() joined to a literal name",
    "hooks/fleet.py::close_log": "state dir; config.state() joined to a literal name",
    "hooks/fleet.py::safe_close_log": "state dir; config.state() joined to the literal close.log (HANDLINES-1)",
    "hooks/fleet.py::owner_go_log": "state dir; config.state() joined to the literal owner-go.log (LAUNCHGATE-1)",
    # HANDLINES-1 — tools/configure.py writes only the package's own config file and its state log.
    "tools/configure.py::backup": "copies config.config_path() to config.json.pre-<key>-<date> beside it; <key> was checked against the shipped schema first, and an existing backup is never overwritten",
    "tools/configure.py::write_atomic": "config.config_path(), the package's own config file, through a mkstemp temp in the same directory; on failure removes only that temp",
    "tools/configure.py::log": "state dir; config.state() joined to the literal config.log",
    # PERMDESIGN-1 — the allow-rules merge writes the settings file of a repository the person
    # listed (the roster, or --repo), only with --apply, after a dry run printed the diff.
    "tools/allow_rules.py::backup": "copies a repository's .claude/settings(.local).json to <name>.pre-permdesign-<date> beside it; an existing copy is never overwritten; under pytest a path outside the test's temp tree is refused",
    "tools/allow_rules.py::write_atomic": "a repository's .claude/settings(.local).json, through a mkstemp temp in the same directory; on failure removes only that temp; under pytest a path outside the test's temp tree is refused",
    # PERMDESIGN-1 — the reviewed ledger and its log, in the state dir only.
    # GEMINISETUP-1 — tools/gemini_worker.py writes the answer file its caller named, and its own run log.
    "tools/gemini_worker.py::cmd_run": "its two files come from _create(); the one unlink removes the empty <OUT>.partial this call made a moment before, when <OUT>.stderr could not be created; the working folder is a new mkdtemp folder, filled by _copy_in() and removed by _clear()",
    "tools/gemini_worker.py::_clear": "removes by IDENTITY, never by a path alone: a copy is unlinked through the open handle of the mkdtemp folder this run created (dir_fd), and only when its device and inode are the ones _copy_in() recorded when it wrote that copy; the rmdir runs only when the path still is that same folder (device and inode compared), and only removes an empty folder — a folder the tool moved, or a link it planted at the old path, is left and named",
    "tools/gemini_worker.py::_create": "opens <OUT>.partial or <OUT>.stderr beside the --out path the caller named with mode xb: an existing file, or a symlink, is refused, never written",
    "tools/gemini_worker.py::_place": "os.link gives the .partial its final name (<OUT> or <OUT>.failed) and fails rather than replace a file that is there; the unlink removes only that .partial after the link succeeded",
    "tools/gemini_worker.py::ledger_append": "state dir; appends one row to config.state() joined to the literal gemini-runs.tsv, never rewrites it",
    "tools/reviewed.py::log": "state dir; config.state() joined to the literal reviewed.log",
    "tools/reviewed.py::add": "state dir; appends one row to config.state()/reviewed.tsv, never rewrites it",
    "hooks/fleet.py::record_proposals": "state dir; config.state() joined to a literal name",
    "hooks/fleet.py::due": "state dir; config.state() joined to literal names",
    "hooks/fleet.py::park": "state dir; config.state() joined to a name sanitized by safe_sid()",
    "hooks/deliver.py::_claim": "state dir; renames fleet.parked_path() (safe_sid name) to a claim beside it, same directory",
    "hooks/deliver.py::_restore": "state dir; puts its own claim files back as fleet.parked_path(), same directory",
    "hooks/deliver.py::deliver": "state dir; removes only claim files _claim() created beside fleet.parked_path()",
    "hooks/fleet.py::read_screen": "removes only the hardcopy file and the mkdtemp folder this function created a moment before",
    "tools/sessions.py::cmd_launch": "state dir; its lock and a pid file named by safe_sid() of the session name",
    "tools/sessions.py::cmd_replay": "the --out file the person running the replay names on the command line, and its folder; nothing else is written",
    "hooks/compact_door.py::_record_pointer": "state dir; config.state() joined to a literal name and a "
                                              "session id sanitized by safe_sid()",
    "tools/compactpoint.py::write": "appends to the state file the caller named on the command line, as "
                                    "/compact-ready tells it to; a compact point is the session's own note",
    "tools/compactpoint.py::renew": "rewrites the caller-named state file in place — a symlink's target, "
                                    "never the link — through a sibling temp file given the file's own "
                                    "mode, then os.replace (the temp file is unlinked if the write fails); "
                                    "only when the file is UTF-8 and already holds a compact-point heading, "
                                    "and the bytes are copied as read except that one heading's text",
    "hooks/wtsweep.py::record": "state dir; the path is config.state() joined to the literal name "
                               "'worktree-sweep.json' by state_file(), with nothing caller-supplied "
                               "in it. The DESTRUCTIVE half of this module is the `git worktree "
                               "remove` in sweep(), which is a subprocess and not a write this "
                               "checker sees — it is guarded by classify()'s four conditions (merged, "
                               "clean, no live session, no live process) and "
                               "by git's own refusal, both under test in test_wtsweep.py",
    "hooks/mergewindow.py::grant": "git's common directory of the repository named, joined to the "
                                   "literal 'gedaechtnis/merge-window.json' by lock_path(); one small "
                                   "JSON file this module owns, written via a tmp file and os.replace. "
                                   "Nothing caller-supplied in the path but the repository itself, which "
                                   "git resolves; test_mergewindow.py is its subject",
    "hooks/mergewindow.py::release": "the same file as grant(), removed only when the caller's session "
                                     "holds it (exit 5 otherwise); it is this module's own state, never "
                                     "user data",
    "tools/suitelock.py::acquire": "the suite lock directory the caller passes, which every caller "
                                   "derives as the plugin's state directory joined to the literal "
                                   "'suite.lock' (the vault sentinel's session fixture); one "
                                   "<pid> file of the package's own bookkeeping, never user data; "
                                   "test_suitelock.py is its subject",
    "tools/suitelock.py::release": "the same directory as acquire(); removes only the entry named "
                                   "by the caller's own pid, absent = already released",
    "tools/suitelock.py::holders": "the same directory as acquire(); removes only an entry whose name "
                                   "is an integer pid that is no longer alive (a suite killed with "
                                   "-9), never a file whose name is not a pid",
    "hooks/suitegate.py::pytest_sessionfinish": "git's common directory of the repository the "
                                                "suite ran in, joined to the literal "
                                                "'gedaechtnis/suite-runs.jsonl' by record_file(); "
                                                "append-only, one JSON line per pytest run, nothing "
                                                "caller-supplied in the path. It records; the gate "
                                                "that READS it is test_suitegate.py's subject. What "
                                                "this does NOT cover: a record file edited by hand, "
                                                "which the gate would believe",
    "hooks/idlenotify.py::deliver": "state dir; the path is config.state() joined to the literal "
                                    "'notify' and a filename that is the seat put through "
                                    "common.safe_sid() — the seat arrives from a config pattern and "
                                    "a process command line, so it is sanitised rather than trusted, "
                                    "and test_a_seat_name_can_never_escape_the_state_directory is "
                                    "what says so. The OTHER thing this function does is run the "
                                    "configured transport, which is a subprocess this checker does "
                                    "not see: it is given a timeout, its failure is logged and "
                                    "never raised, and the mailbox is written BEFORE it runs, so "
                                    "nothing it does can lose a message",
    "hooks/common.py::update_session_state": "state dir via session_state_path(); the sid is sanitized by safe_sid()",
    "hooks/common.py::record_cwd": "state dir via session_state_path(); only the TIMES of this session's own record are touched (os.utime), the sid is sanitized by safe_sid()",
    "hooks/common.py::note_pre_exists": "state dir; the filename is a hash of the target, never the target itself",
    "hooks/common.py::clear_pre_exists": "state dir; the filename is a hash of the target, never the target itself",
    "hooks/common.py::was_created": "state dir; the filename is a hash of the target, never the target itself",
    "hooks/common.py::_filelock_mutex": "state dir; the lock filename is a sha1 of the real target path",
    "hooks/common.py::take_filelock": "state dir; the lock filename is a sha1 of the real target path",
    "hooks/common.py::release_filelock": "state dir; the lock filename is a sha1 of the real target path",
    "hooks/claim.py::_write_claims": "state dir, via config.state() and a session id sanitized by safe_sid()",
    "hooks/claim.py::_say": "state dir, via common.STATE and a session id sanitized by safe_sid()",
    "hooks/context_economy.py::_update": "state dir, via config.state() and a sanitized session id",
    "hooks/lesson_push.py::_update": "state dir only: both writes are config.state() joined to a literal name plus a session id sanitized by safe_sid(); no caller-supplied value, and no vault path, reaches either",
    "hooks/lesson_push.py::index_for": "state dir only: the cache path is config.state() joined to a literal prefix, a session id sanitized by safe_sid() and a region key sanitized to [A-Za-z0-9_-] by _index_path(); neither the written path nor the vault path being indexed can reach it",
    "hooks/lazy_body.py::_claim_facet": "state dir only: the same two paths as `_claim` (config.state() joined to a literal prefix plus a session id sanitized by safe_sid()). The FACET key it records is a value inside the JSON document, never a path segment",
    "hooks/lazy_body.py::_claim": "state dir only: both writes are config.state() joined to a literal prefix plus a session id sanitized by safe_sid(). The REGION the claim records is a value inside the JSON document, never a path segment — which is the distinction the context_cap entry below was once wrong about, so it is stated rather than assumed",
    "hooks/context_cap.py::_update": "state dir only: all three writes are config.state() joined to a literal name plus a session id sanitized by safe_sid(); no caller-supplied value reaches any of the three paths",
    # ★ WAS: "every path is config.state() joined to a literal" — and it was not. Two of them were
    # joined to the RAW `session_id` payload field, which `safe_sid()` exists to sanitize and which
    # every neighbouring entry in this table names. The exemption did not merely miss the case; it
    # told a reviewer the question had been answered. An exemption's REASON is a claim, and a false
    # one is worse than no exemption at all, because it stops the next reader looking.
    "hooks/session_start.py::main": "state dir only; every path is config.state() joined to a "
                                    "literal or to a session id sanitized by safe_sid()",
    "hooks/session_start.py::no_memory_line": "state dir; a dated stamp file with a literal name",
    "hooks/boot_check.py::main": "state dir, via state_path(), which is config.state() plus a literal",
    "hooks/maintenance.py::main": "state dir, via state_path(), which is config.state() plus a literal",
    "hooks/maintenance.py::record_pass": "the same state file as ::main, by the same state_path(); the only value it sets is a date, and the only path it builds is config.state() plus a literal",
    "outage_check.py::log": "a standalone check writing its own log into the state dir it was handed",
    "ledger.py::read": "state dir cursor file, via cursor_path(); the lane name is validated by the row grammar",

    # --- the temp-file-beside-destination shape. As bounded as the destination, which is permitted
    # separately, so permitting the temp adds nothing.
    "codex_bridge.py::write_if_changed": "generic writer; every caller passes a path inside the plugin's own tree",

    # --- git, scoped by -C to a resolved root, with a pathspec the caller derived by relative_to().
    "ledger.py::_commit": "git -C the vault; the pathspec is built via relative_to(vault), which raises on escape",
    "hooks/gate.py::non_release_tags": "`git tag -l` is READ-ONLY; it is here only because the matcher sees the word `tag`",
    "tools/publish_check.py::foreign_tags": "`git tag -l` is READ-ONLY; same reason",

}
# ---------------------------------------------------------------------------------------------


def product_modules():
    for p in sorted(PLUGIN.rglob("*.py")):
        if "__pycache__" in p.parts or "/tests/" in str(p) or "/eval/" in str(p):
            continue
        yield p


def _mutating_calls(tree):
    """-> [(lineno, description)] for every filesystem-mutating call in the tree."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
           and (f.value.id, f.attr) in MUT_ATTRS:
            out.append((n.lineno, f"{f.value.id}.{f.attr}"))
        elif isinstance(f, ast.Attribute) and f.attr in PATH_MUT \
                and not (isinstance(f.value, ast.Name) and f.value.id in NOT_PATHS):
            out.append((n.lineno, f"Path.{f.attr}"))
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = ""
            if len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                mode = str(n.args[1].value)
            for kw in n.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if any(c in mode for c in "wax+"):
                out.append((n.lineno, f"open({mode})"))
        else:
            subs = {q.value for q in ast.walk(n)
                    if isinstance(q, ast.Constant) and isinstance(q.value, str) and q.value in GIT_MUT}
            if subs and any(isinstance(q, ast.Constant) and q.value == "git" for q in ast.walk(n)):
                out.append((n.lineno, "git " + ",".join(sorted(subs))))
    return out


def _calls_permit(fn) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            u = ast.unparse(n.func)
            if u.endswith("permit") or u.endswith("rootguard.permit"):
                return True
    return False


def scan(modules):
    """-> (unguarded, used_exemptions). `unguarded` is the list that must be empty."""
    unguarded, used = [], set()
    for p in modules:
        try:
            rel = str(p.relative_to(PLUGIN))
        except ValueError:
            rel = p.name
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            muts = _mutating_calls(fn)
            if not muts:
                continue
            key = f"{rel}::{fn.name}"
            if key in EXEMPT:
                used.add(key)
                continue
            if _calls_permit(fn):
                continue
            for ln, what in muts:
                unguarded.append(f"{rel}:{ln}  {fn.name}()  {what}")
    return unguarded, used


def test_every_mutating_call_site_is_guarded_or_exempted():
    """The claim `rootguard.py` makes about itself, made true."""
    unguarded, _ = scan(list(product_modules()))
    assert not unguarded, (
        "Mutating call site(s) with neither a rootguard.permit() in the same function nor an entry "
        "in EXEMPT:\n  " + "\n  ".join(unguarded)
        + "\n\nAdd the permit, or add an EXEMPT entry saying in words why this write cannot leave "
          "the package's roots. An exemption is a claim somebody has to stand behind — write the "
          "reason, not 'it's fine'.")


def test_the_scan_is_actually_reading_the_package():
    """The failure mode that reads as a pass: an empty module list finds no unguarded sites and
    reports green forever."""
    mods = list(product_modules())
    assert len(mods) >= 20, f"only {len(mods)} modules scanned — the walk is not reading the package"
    total = sum(len(_mutating_calls(ast.parse(m.read_text(encoding="utf-8")))) for m in mods)
    assert total >= 60, f"only {total} mutating sites found across {len(mods)} modules — the matcher is broken"


def test_the_coverage_scan_bites(tmp_path):
    """Positive control: plant an unguarded write and watch the scan name it."""
    bad = tmp_path / "relapse.py"
    bad.write_text("from pathlib import Path\n"
                   "def writes_anywhere(p):\n"
                   "    Path(p).write_text('x')\n", encoding="utf-8")
    unguarded, _ = scan([bad])
    assert unguarded and "writes_anywhere" in unguarded[0], unguarded


def test_the_coverage_scan_stays_quiet_on_a_guarded_write(tmp_path):
    """Negative control: the sanctioned shape must not trip it, or the test is noise."""
    good = tmp_path / "fine.py"
    good.write_text("import rootguard\nfrom pathlib import Path\n"
                    "def writes_carefully(p):\n"
                    "    rootguard.permit(p, 'why')\n"
                    "    Path(p).write_text('x')\n", encoding="utf-8")
    unguarded, _ = scan([good])
    assert not unguarded, unguarded


def test_no_exemption_is_stale():
    """An exemption for a function that no longer mutates anything is a claim nobody is checking.
    It also hides the next site that takes the same name."""
    _, used = scan(list(product_modules()))
    # GEMINI-PUBLIC: a published tree leaves tools/gemini_worker.py out (rules/publish-exclude.json);
    # its entries are not stale there, the file is absent. Where the file is, they are checked.
    root = Path(__file__).resolve().parents[1]
    absent = {k for k in EXEMPT if k.startswith("tools/gemini_worker.py::")
              and not (root / "tools" / "gemini_worker.py").is_file()}
    stale = sorted(set(EXEMPT) - used - absent)
    assert not stale, (
        "EXEMPT entries that match no mutating function any more:\n  " + "\n  ".join(stale)
        + "\n\nDelete them. A stale exemption silently pre-approves whatever is written there next.")


def test_every_exemption_carries_a_real_reason():
    """'ok' is not a reason."""
    thin = [k for k, v in EXEMPT.items() if len(v.split()) < 5]
    assert not thin, f"exemptions with no real justification: {thin}"
