#!/usr/bin/env python3
"""limits.py — the one reader of `rules/limits.json`. No number this package acts on is written twice.

Every threshold the plugin enforces lives in that file, with its reasoning beside it in a `_`-prefixed
sibling key. This module is the only thing that opens it. The rule it exists to make mechanical is the
one the vault's own hooks broke repeatedly: a constant inlined at a call site and a constant in a
config file drift, and the drift is silent because both sides still run.

Three properties, each of which a test pins:

  * **Defaults live HERE, not at the call site.** A malformed or missing `limits.json` must never take
    a session down — the same discipline `config.py` follows for the config file — so a fallback table
    is carried in `DEFAULTS`. It is the SHIPPED file's content; `test_limits.py` asserts the two agree,
    so the fallback cannot quietly become a second, older opinion.
  * **A `_`-prefixed key is documentation.** `get()` never returns one, and the loader does not
    validate them: prose is free to change without a code change.
  * **Nothing is cached across processes.** Each hook invocation is one process and reads once; the
    module-level cache exists so two arms of the same hook do not stat the file twice.
  * **A vault may raise its own ceilings, and a typo in doing so is REPORTED.** `config.json`'s
    `limits` object is merged last, over the file and the defaults, because a threshold is a
    property of the vault and not of the machine the vault sits on: a fleet whose ruled boot budget
    is larger than the package default had, until this seam, only `GEDAECHTNIS_LIMITS` — a
    MACHINE-WIDE env var pointing at a whole replacement file, which then applies to every other
    vault on that machine and to every sandbox that forgot to clear it. The override layer is
    VALIDATED rather than trusted: an unknown key, a `_`-prefixed key, a wrong-typed value or a
    non-object `limits` is not applied AND is named by `problems()`, which `tools/status.py` and
    the session-start facts print. A misconfiguration that silently does nothing is the failure
    this layer exists to avoid, not one it may introduce.
"""
from __future__ import annotations
import json, os
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
# GEDAECHTNIS_LIMITS points the loader at another file. It exists for the same reason every other
# path in this package comes from an env-overridable seam: a suite that had to exercise a threshold
# by actually reaching it would need 301 commits per assertion, and a suite that instead reached
# into the shipped file would be editing the product to test it. The shipped VALUES are pinned
# separately, by `test_limits.py`, against `DEFAULTS`.
LIMITS_PATH = Path(os.path.expanduser(os.environ.get("GEDAECHTNIS_LIMITS")
                                      or str(PLUGIN_ROOT / "rules" / "limits.json")))

# The shipped file's values, carried so a session survives a missing or malformed limits.json.
# Kept in sync BY A TEST, never by memory — see the module docstring.
DEFAULTS = {
    "boot_budget_bytes": 20000,
    "boot_budget_warn_bytes": 20000,
    "boot_budget_warn_lines": 200,
    "boot_file_budget_bytes": 32000,
    "boot_file_warn_bytes": 28000,
    "compaction_floor_share": 0.4,
    "role_soft_limits_lines": {
        "Map": 100, "Vision": 150, "Position": 250, "Course": 300,
        "Aporia": 200, "Errata": 400, "Annales": 500, "Canon": 800,
        "Ethos": 150, "Eidos": 400,
    },
    "max_searchable_file_bytes": 2000000,
    "max_memory_file_bytes": 500000,
    "stale_entry_days": 56,
    "context_hard_cap_tokens": 700000,
    "context_warn_over_floor_tokens": 400000,
    # RESUMEGATE-1: a message that would wake an idle session is refused when it is cold and large,
    # short of headroom, or a Fable session (hooks/resume_gate.py).
    "resume_cold_cap": 200000,
    "resume_cold_s": 300,
    "resume_window": 420000,
    "resume_min_headroom": 70000,
    # CONTEXTMSG-1: a message of at most `resume_short_chars` characters to a WARM target passes the
    # headroom rule below `resume_short_window`. Both 0 = off, the shipped behaviour.
    "resume_short_chars": 0,
    "resume_short_window": 0,
    "split_min_entries": 8,
    "split_min_remaining_entries": 4,
    "split_min_remaining_share": 0.0,
    "split_min_cohesion": 0.0,
    "split_core_term_share": 0.5,
    "split_max_bridge_entries": 3,
    "split_boilerplate_region_share": 0.41,
    "split_auto_apply": False,
    "split_auto_min_remaining_share": 0.25,
    "lesson_push_enabled": False,
    # `lesson_push_min_score` was removed with design 2 (LESSONPUSH-2): the citation matcher is
    # EXACT and has no score, so a threshold key would be a knob that does nothing. Its
    # measurement is folded into `_lesson_push_enabled`, where both designs' gates are recorded.
    "agent_max_concurrent": 8,
    "agent_open_stale_seconds": 21600,
    "lesson_push_max_per_fire": 2,
    "lesson_push_max_per_session": 10,
    # LESSONPUSH-3, the BOOT arm. A separate switch from `lesson_push_enabled` on purpose: the
    # write arms were refused by their own gates and are closed, and one key for both would make
    # turning the boot arm on re-open a design two measurements have already refused.
    "lesson_push_boot_enabled": False,
    "lesson_push_boot_max_lines": 5,
    "worktree_sweep": True,
    # DESTRUCTIVEGATE-1: every automatic chore that moves or removes a user file proposes until
    # the vault switches it on, and is bounded per pass. See hooks/destructive.py.
    "compaction_apply": False,
    "boot_roll_apply": False,
    "worktree_sweep_apply": False,
    "cleanup_apply": False,
    "max_move_share": 0.25,
    "max_files_per_pass": 10,
    "lazy_bodies_enabled": False,
    "lazy_body_max_bytes": 60000,
    "facets_enabled": True,
    "facet_max_bytes": 24000,
    "cleanup_trigger_days": 14,
    "cleanup_trigger_commits": 300,
    "synthesis_trigger_days": 14,
    "synthesis_trigger_entries": 20,
    # KERNELENTRY-1: a Boot-file bullet with a tool signature goes to a facet (hooks/kernelentry.py).
    "kernel_entry_door": True,
    "kernel_entry_deny_from": "",
    # APOSTROPHEGATE-1: one apostrophe codepoint per declared layer (hooks/apostrophe_door.py).
    "apostrophe_door": True,
    "apostrophe_deny_from": "",
    "secret_door": True,
    "secret_deny_from": "",
    "html_head_door": True,
    "html_head_deny_from": "",
    "launch_pin_door": True,
    "launch_pin_deny_from": "",
    "judge_md_door": True,
    "judge_md_deny_from": "",
    "row_identity_door": True,
    "row_identity_deny_from": "",
    "marker_roster_door": True,
    # KILLDOOR-1: a kill whose target the shell computes (hooks/killdoor.py).
    "kill_door": True,
    "kill_deny_from": "",
    # BOOTFACTS-1: the SessionStart facts block's budget (hooks/factsbudget.py). 0 = no cap.
    "facts_max_bytes": 0,
    "facts_inline_limit_chars": 10000,
    # TERMOVERLOAD-1: the REACH notice, the launch cap, the session-close sweep and its door.
    "context_reach_margin_tokens": 30000,
    "session_cap": 0,
    "session_swap_cap_share": 0,
    "session_close_apply": False,
    "session_close_idle_minutes": 180,
    "session_close_every_s": 300,
    "session_close_exempt": [],
    "session_cap_per_seat": 0,
    "session_launch_door": "warn",
}

# ★ ONE tuple, not three globals. `_CACHE`, `_PROBLEMS` and `_OVERRIDDEN` were kept in step only
# by the two functions that assigned them together, and `_load()`'s early return keyed off the
# first alone — so anything that installed a cache directly (a test does, via monkeypatch) left the
# other two at None, and `problems()` and `overridden()` then answered "nothing configured, nothing
# wrong" for a vault that had configured plenty. Three facts produced by one read belong in one
# value; there is no longer a state in which some of them are set.
_STATE: tuple | None = None


def _vault_overrides() -> tuple[dict, list]:
    """(the applicable `limits` keys from config.json, the problems found) — the per-vault layer.

    Imported lazily and defensively: `limits` is loaded by hooks that must not fail, `config` lives
    beside this module rather than on the path of every caller, and a config layer that cannot be
    read is exactly the "no override" case, not an error.

    Every rejection is RETURNED, never swallowed. A key this package has no default for is the
    likeliest real mistake — `boot_budget` for `boot_budget_bytes` reads correctly, applies
    nothing, and would leave a vault sitting on the shipped ceiling believing it had raised it."""
    base = _file_layer()          # what `get()` will actually return — see below
    where = "the `limits` object in config.json"
    problems = []
    try:
        import config                                   # noqa: PLC0415 — see the docstring
        cfg = config._load()
    except ImportError as e:
        # ★ SILENCE AND "NOTHING CONFIGURED" WERE THE SAME ANSWER. `import config` resolves only
        # when `hooks/` is on `sys.path` — true for every shipped caller, because `import limits`
        # needs it too and `config` sits in the same directory. But a caller that loads this module
        # BY PATH (`spec_from_file_location`) gets neither, and the bare `except` then returned
        # "this vault configures no limits": measured, a vault whose config.json says 100,000
        # reported 20,000 with `problems()` empty. That is the sentence this function's own
        # docstring uses to justify the validation — a vault sitting on the shipped ceiling
        # believing it raised it — arriving through the one door nobody was watching.
        #
        # The VALUE still falls through to the defaults, because a hook that cannot read a config
        # must not die; what changes is that the surfaces now SAY the layer was never consulted.
        return {}, [f"the `config` module could not be imported ({e}), so config.json was never "
                    f"read: the limits below are the shipped defaults, whatever this vault "
                    f"configured. `limits.py` needs its own directory on sys.path"]
    except Exception as e:
        return {}, [f"config.json could not be consulted ({type(e).__name__}: {e}), so the limits "
                    f"below are the shipped defaults, whatever this vault configured"]
    if config.unreadable():
        # ★ THE THIRD STATE. `config._load()` swallows a malformed config.json and hands back an
        # empty dict, so "this vault configures no limits" and "this vault's config could not be
        # read" arrived here identical — and the surfaces printed the reassuring one: `shipped
        # defaults; config.json overrides none`, a clean bill of health for a vault that is
        # configured and applying nothing. That is the precise failure this layer was built to
        # give a voice to, arriving through the one door nobody checked.
        return {}, ["config.json exists but could not be read as a JSON object — no limit was "
                    "overridden, and the `limits` object in it (if any) was never seen"]
    raw = cfg.get("limits")
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [f"{where} is a {type(raw).__name__}, not an object — no limit was overridden"]
    ok = {}
    for k, v in raw.items():
        if k.startswith("_"):
            # Documentation, exactly as the FILE layer treats it and as this module's own docstring
            # promises — skipped, never validated, never reported. It was reported once, and the
            # cost landed on the most natural way to write a config: `rules/limits.json` ships
            # sixteen `_`-prefixed reasoning keys beside its sixteen thresholds, so a user who
            # copied it got SIXTEEN permanent "NOT APPLIED" lines — in `status`, and in every
            # session's boot facts — for a config that was entirely correct.
            #
            # ONE case is still named, and it is the one that silence costs something: the user
            # wrote `_boot_budget_bytes` and NOT `boot_budget_bytes`. Reading a file where each
            # threshold sits beside its `_`-prefixed twin, editing the wrong one of a pair is the
            # available mistake — and the result is a vault believing it raised a ceiling it is
            # still sitting under. When the real twin IS present, the `_` key is what it claims to
            # be: prose beside a setting, and it stays quiet.
            twin = k.lstrip("_")
            if twin in base and twin not in raw:
                problems.append(f"{where}: `{k}` is a documentation key and was not applied — did "
                                f"you mean `{twin}`? (nothing here sets `{twin}`)")
            continue
        if k not in base:
            problems.append(f"{where}: `{k}` is not a limit this package reads — not applied "
                            f"(the names are: {', '.join(sorted(base))})")
            continue
        # NAMES come from the effective table (the file may ADD a limit); the TYPE comes from the
        # SHIPPED definition whenever there is one. Two different questions, two different
        # authorities: the file layer is unvalidated, so letting it define the type lets a
        # corrupted value there reject a perfectly good vault override — a list in the file made a
        # per-role dict "a dict, not a list", and the vault sat on the list.
        shipped = DEFAULTS.get(k, base[k])
        want = type(shipped)
        # bool is an int to Python and never a threshold to anyone else — so a bool is refused
        # for every NUMERIC limit. ★ BUT A LIMIT MAY ITSELF BE A BOOL (`split_auto_apply` is the
        # first), and this branch rejected those too, BEFORE the type test could accept them: the
        # message read "`split_auto_apply` is a bool, not a bool — not applied", which is the tell
        # that the branch had never run on one. It failed safe only by accident — the ON path was
        # equally unreachable — and it meant a vault could not turn a bool limit on OR off through
        # the seam that exists for exactly that.
        if isinstance(v, bool) and want is not bool:
            problems.append(f"{where}: `{k}` is a bool, not a {want.__name__} — not applied")
            continue
        if not isinstance(v, want if want is not float else (int, float)):
            problems.append(f"{where}: `{k}` is a {type(v).__name__}, not a "
                            f"{want.__name__} — not applied")
            continue
        if want is dict:
            # ★ A DICT LIMIT IS MERGED PER SUB-KEY, NEVER REPLACED. `role_soft_limits_lines` is a
            # TABLE of independent thresholds, and the whole promise of this layer is that raising
            # one ceiling leaves the others alone. Assigning the object wholesale keeps that promise
            # for every scalar limit and breaks it for the one dict: a vault that writes
            # `{"role_soft_limits_lines": {"Position": 999}}` — the obvious spelling, and the row's
            # own headline use case — would DELETE the seven roles it did not mention. Both
            # consumers read the table with `.get(stem)` and treat a miss as "no limit", so the
            # oversize trigger simply stops firing for those roles: no error, no `problems()` entry,
            # nothing on any surface. A silent loss of an existing protection is worse than the
            # no-op this validation was built to catch, because there is no moment at which anyone
            # could notice.
            # Same split one level down, and the same reason.
            sub_known = dict(shipped)
            if isinstance(base.get(k), dict):
                sub_known.update(base[k])
            sub_ok, sub_bad = {}, False
            for sk, sv in v.items():
                if sk not in sub_known:
                    problems.append(f"{where}: `{k}.{sk}` is not a name this package reads — not "
                                    f"applied (the names are: {', '.join(sorted(sub_known))})")
                    sub_bad = True
                    continue
                sub_want = type(sub_known[sk])
                if isinstance(sv, bool) or not isinstance(
                        sv, sub_want if sub_want is not float else (int, float)):
                    problems.append(f"{where}: `{k}.{sk}` is a {type(sv).__name__}, not a "
                                    f"{sub_want.__name__} — not applied")
                    sub_bad = True
                    continue
                sub_ok[sk] = sv
            if sub_ok:
                ok[k] = sub_ok      # the SUBSET that was accepted; _load merges it per key
            elif not sub_bad:
                problems.append(f"{where}: `{k}` is empty — nothing to override")
            continue
        ok[k] = v
    return ok, problems


def _apply(merged: dict, k, v) -> None:
    """Put one override in, at the right GRAIN — and the grain is a property of the KEY's type,
    never of which layer the value came from.

    A scalar limit is one number and replaces the one below it. `role_soft_limits_lines` is a
    TABLE of eight independent thresholds, and assigning it wholesale DELETES every role the
    override did not mention — silently, because both consumers read it with `.get(stem)` and treat
    a miss as "this role has no limit", so the oversize trigger simply stops firing for them.

    This lived at BOTH layers and was fixed at one. The vault layer got the merge because that is
    where the defect was found; the `GEDAECHTNIS_LIMITS` FILE layer kept the wholesale assignment,
    so the identical silent loss was still reachable through a seam that is live on this machine
    right now — and the fix's own comment claimed the guarantee for the TYPE, unqualified, while
    the code kept it for one door. **A rule stated about a value and enforced at one of its two
    entrances is not enforced.** One function now owns the grain, and both layers call it.

    This does not re-open what the FILE layer replaces: it still replaces the base table key by
    key, exactly as the docstring says. What it may not do is reach INSIDE a key and drop entries
    nobody named.

    **The grain is read off DEFAULTS, not off what happens to be in `merged`.** Asking
    `isinstance(merged.get(k), dict)` reads the layer BELOW — which the unvalidated FILE layer can
    have set to anything. A file saying `{"role_soft_limits_lines": ["Position"]}` made the base a
    LIST, so a vault's per-role override fell to the `else` branch and replaced the whole table
    again: seven roles gone, `problems()` empty. The silent loss survived its own fix by coming
    back through the one layer nobody validates. A key's grain is a property of the SHIPPED
    definition and of nothing else, so that is where it is read."""
    # ★ THE FOURTH ENTRANCE. Reading the grain from `DEFAULTS` alone was right for every key
    # DEFAULTS knows — and `DEFAULTS.get(k)` is `None` for a key the FILE layer INTRODUCED, which
    # was fix 1's whole purpose. So the validator accepted a partial override of a file-only dict
    # key (it consults the effective shape) and this function then took the scalar-replace branch:
    # sub-keys the override did not name were dropped, `problems()` empty. Same rule, fourth place,
    # and each of the first three fixes was correct where it stood.
    #
    # The authority is now: the SHIPPED definition when there is one, else the shape the layer
    # below actually holds. One question — "what shape is this key?" — asked in one place.
    shipped = DEFAULTS.get(k)
    under = merged.get(k)
    if shipped is None and isinstance(under, dict):
        shipped = under
    if isinstance(shipped, dict) and isinstance(v, dict):
        merged[k] = {**(under if isinstance(under, dict) else shipped), **v}
    else:
        merged[k] = v


def _file_layer() -> dict:
    """DEFAULTS with the limits FILE applied — the table a vault override is actually validated
    against, and the table `get()` would return if no vault configured anything.

    Split out because the validator used to check names and types against `DEFAULTS` while `get()`
    answered from DEFAULTS+FILE. A limit that exists only in `rules/limits.json` was therefore LIVE
    and un-overridable: the vault's attempt came back `is not a limit this package reads` — a
    sentence that is false, blames the user, and leaves them on the old value. Adding a key to the
    shipped file alone still passes the suite, so nothing would have caught it later either.
    Whatever the effective table is, that is what an override is measured against."""
    merged = dict(DEFAULTS)
    try:
        data = json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if not k.startswith("_"):
                    _apply(merged, k, v)
    except (OSError, ValueError):
        pass
    return merged


def _load() -> dict:
    """The defaults, then the file's keys, then the vault's `limits` object. Each layer over the
    last; an unreadable or non-object file or config layer leaves the layers below it standing."""
    global _STATE
    if _STATE is not None:
        return _STATE[0]
    merged = _file_layer()
    overrides, problems = _vault_overrides()
    for k, v in overrides.items():
        _apply(merged, k, v)
    _STATE = (merged, problems, overrides)
    return merged


def problems() -> list:
    """Every part of this vault's `limits` object that was NOT applied, each saying why.

    Empty on an install that configures no limits and on one that configures them correctly —
    which are the same output and opposite facts, so the surfaces that print this say which."""
    _load()
    return list(_STATE[1] if _STATE else [])


def overridden() -> dict:
    """The keys this vault's config.json actually changed, and to what. The positive control for
    `problems()`: a surface printing only the failures cannot show that the layer works.

    Goes through `_load()` like everything else. It used to call `_vault_overrides()` directly,
    which re-read and re-parsed `config.json` a second time in every process that prints both this
    and `problems()` — both surfaces do — and quietly contradicted this module's own stated
    guarantee that each hook invocation reads once."""
    _load()
    return dict(_STATE[2] if _STATE else {})


def get(key: str, default=None):
    """One threshold. A `_`-prefixed key is prose and is never a value: asking for one raises,
    because a call site that read documentation as a number would do so silently forever."""
    if key.startswith("_"):
        raise KeyError(f"{key!r} is a documentation key, not a threshold")
    val = _load().get(key, default)
    if val is None and default is None:
        raise KeyError(f"no such limit: {key!r}")
    return val


def all_limits() -> dict:
    """Every threshold, prose excluded. For a status/report surface that prints the table."""
    return {k: v for k, v in _load().items() if not k.startswith("_")}


def _reset_for_tests() -> None:
    global _STATE
    _STATE = None
