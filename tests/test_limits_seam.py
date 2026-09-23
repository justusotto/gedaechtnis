"""Tests for the PER-VAULT limits seam — `config.json`'s `limits` object over `rules/limits.json`.

Every test here runs the loader in a SUBPROCESS with an env built from scratch, because the thing
under test is a module-level cache over two files whose locations come from the environment: a
test that imported `limits` in this process would measure whichever test imported it first, which
is the exact defect `config.py` was rewritten for (a path frozen at import).

The env in every case is scrubbed of every `GEDAECHTNIS_*` variable by PREFIX and then rebuilt with
only what the case needs. This machine runs with a `GEDAECHTNIS_LIMITS` pointing at a fleet vault's
own file; a test that inherited it would read that vault's thresholds and pass or fail for reasons
that have nothing to do with the code.

The positive/negative pairing throughout: an override that APPLIES, beside a rejection that is
NAMED. An override layer's characteristic failure is not crashing — it is looking configured and
doing nothing, and `problems()` exists so that state has a voice. An empty `problems()` therefore
needs the applied-override case beside it or it is the "nothing missing / nothing to look for"
output: identical for a correct config and for a config nothing read.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"

DEFAULTS_ROLES = ("Map", "Vision", "Position", "Course", "Aporia", "Errata", "Annales",
                  "Canon")

PROBE = (
    "import sys, json; sys.path.insert(0, {hooks!r}); import limits;"
    " print(json.dumps({{'budget': limits.get('boot_budget_bytes'),"
    " 'commits': limits.get('cleanup_trigger_commits'),"
    " 'overridden': limits.overridden(), 'problems': limits.problems()}}))"
)


def clean_env(**extra) -> dict:
    """os.environ with every GEDAECHTNIS_* variable removed by prefix, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    env.update({k: str(v) for k, v in extra.items()})
    return env


def probe(tmp_path: Path, limits_obj, *, with_file: bool = False, raw_config: str | None = None):
    """Run the loader with `limits_obj` in a throwaway config.json; return its parsed answer."""
    cfg = tmp_path / "config.json"
    if raw_config is not None:
        cfg.write_text(raw_config, encoding="utf-8")
    else:
        body = {"vault": str(tmp_path / "vault")}
        if limits_obj is not None:
            body["limits"] = limits_obj
        cfg.write_text(json.dumps(body), encoding="utf-8")
    env = clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path)
    if with_file:
        f = tmp_path / "file-limits.json"
        f.write_text(json.dumps({"boot_budget_bytes": 55555, "cleanup_trigger_commits": 7}),
                     encoding="utf-8")
        env["GEDAECHTNIS_LIMITS"] = str(f)
    p = subprocess.run([sys.executable, "-B", "-c", PROBE.format(hooks=str(HOOKS))],
                       capture_output=True, text=True, env=env, timeout=30,
                       stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


# ------------------------------------------------------------------ positive ----
def test_a_vault_raises_its_own_ceiling(tmp_path):
    """THE ROW'S REASON TO EXIST: this vault's ruled boot chain is larger than the package default,
    and until now the only way to say so was a machine-wide env var pointing at a whole file."""
    r = probe(tmp_path, {"boot_budget_bytes": 100000})
    assert r["budget"] == 100000
    assert r["overridden"] == {"boot_budget_bytes": 100000}
    assert r["problems"] == []


def test_an_unconfigured_vault_is_unchanged(tmp_path):
    """The negative half of the above: no `limits` object → the shipped numbers, and `overridden`
    empty. Without this, the applied case could be reading a default that happened to match."""
    r = probe(tmp_path, None)
    assert r["budget"] == 20000 and r["commits"] == 300
    assert r["overridden"] == {} and r["problems"] == []


def test_only_the_named_keys_move(tmp_path):
    r = probe(tmp_path, {"boot_budget_bytes": 100000})
    assert r["commits"] == 300, "an override of one key must not disturb another"


def test_the_vault_layer_is_merged_over_the_file_layer(tmp_path):
    """Precedence, stated: `GEDAECHTNIS_LIMITS` REPLACES the shipped table, the vault's `limits`
    object is merged over whatever that table is. The file's other keys survive."""
    r = probe(tmp_path, {"boot_budget_bytes": 100000}, with_file=True)
    assert r["budget"] == 100000, "config.json's limits must win over the file"
    assert r["commits"] == 7, "and the file's untouched keys must still be read"


# ------------------------------------------------------------------ rejected ----
@pytest.mark.parametrize("obj,needle", [
    ({"boot_budget": 100000}, "not a limit this package reads"),
    ({"boot_budget_bytes": "100000"}, "is a str, not a int"),
    ({"boot_budget_bytes": True}, "is a bool, not a int"),
    ({"role_soft_limits_lines": 300}, "is a int, not a dict"),
])
def test_a_bad_override_is_named_and_not_applied(tmp_path, obj, needle):
    """Each of these reads as if it configured something. None of them may CHANGE anything, and
    none of them may be silent — a typo that quietly does nothing leaves a vault believing it
    raised a ceiling it is still sitting under."""
    r = probe(tmp_path, obj)
    assert r["budget"] == 20000, "a rejected override must not reach the value"
    assert r["overridden"] == {}
    assert len(r["problems"]) == 1 and needle in r["problems"][0]
    assert list(obj)[0] in r["problems"][0], "the problem must name the key the user wrote"


def test_a_non_object_limits_is_named(tmp_path):
    r = probe(tmp_path, [{"boot_budget_bytes": 100000}])
    assert r["budget"] == 20000 and r["overridden"] == {}
    assert len(r["problems"]) == 1 and "not an object" in r["problems"][0]


def test_good_and_bad_keys_in_one_object_are_separated(tmp_path):
    """A mistake in one key may not cost the user the keys they got right, and getting four keys
    right may not hide the fifth."""
    r = probe(tmp_path, {"boot_budget_bytes": 100000, "boot_budget": 1})
    assert r["budget"] == 100000 and r["overridden"] == {"boot_budget_bytes": 100000}
    assert len(r["problems"]) == 1 and "boot_budget`" in r["problems"][0]


# ------------------------------------------------- the one DICT-valued limit ----
ROLES_PROBE = (
    "import sys, json; sys.path.insert(0, {hooks!r}); import limits;"
    " print(json.dumps({{'roles': limits.get('role_soft_limits_lines'),"
    " 'overridden': limits.overridden(), 'problems': limits.problems()}}))"
)


def roles_probe(tmp_path: Path, limits_obj):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"vault": str(tmp_path / "vault"), "limits": limits_obj}),
                   encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", "-c", ROLES_PROBE.format(hooks=str(HOOKS))],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                       env=clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_a_dict_limit_is_merged_PER_ROLE_and_the_roles_not_named_SURVIVE(tmp_path):
    """★ THE FAILURE THAT REPORTS NOTHING. `role_soft_limits_lines` is the one dict-valued limit,
    and it is a TABLE of independent thresholds. A vault writing the obvious thing —
    `{"role_soft_limits_lines": {"Position": 999}}`, which is this layer's headline use case — had
    the whole table REPLACED: the seven roles it did not mention vanished.

    Nothing anywhere said so. `problems()` was empty, because the value was a dict and a dict is
    what the key wants. Both consumers read the table with `.get(stem)` and treat a miss as "this
    role has no limit", so the oversize trigger simply stopped firing for Map, Vision, Course,
    Aporia, Errata, Annales and Canon — no error, no line on any surface, and no moment at which
    anyone could notice a protection had been turned off.

    The row's own promise is that raising ONE ceiling leaves the others alone. It held for every
    scalar limit and broke for the only dict."""
    r = roles_probe(tmp_path, {"role_soft_limits_lines": {"Position": 999}})
    assert r["problems"] == [], r["problems"]
    assert r["roles"]["Position"] == 999, r["roles"]
    for role, shipped in (("Map", 100), ("Vision", 150), ("Course", 300), ("Aporia", 200),
                          ("Errata", 400), ("Annales", 500), ("Canon", 800)):
        assert r["roles"][role] == shipped, (role, r["roles"])
    assert r["overridden"] == {"role_soft_limits_lines": {"Position": 999}}


def test_an_UNKNOWN_ROLE_inside_the_dict_is_named_and_the_good_ones_still_apply(tmp_path):
    """The negative control, and the reason a per-key merge is not just a `dict.update`.

    Sub-keys need the same validation the top level got, or the layer accepts
    `{"Postition": 999}` in silence — a typo that reads as configured, does nothing, and leaves
    `Position` sitting at the shipped 250 while its author believes otherwise. The correctly
    spelled sibling in the same object must still apply: one mistake may not cost the user the
    keys they got right."""
    r = roles_probe(tmp_path, {"role_soft_limits_lines": {"Postition": 999, "Canon": 900}})
    assert r["roles"]["Canon"] == 900, r["roles"]
    assert r["roles"]["Position"] == 250, r["roles"]
    assert "Postition" not in r["roles"]
    assert len(r["problems"]) == 1 and "role_soft_limits_lines.Postition" in r["problems"][0]


def test_a_WRONG_TYPED_role_value_is_named_and_not_applied(tmp_path):
    r = roles_probe(tmp_path, {"role_soft_limits_lines": {"Canon": "900"}})
    assert r["roles"]["Canon"] == 800, r["roles"]
    assert r["overridden"] == {}
    assert len(r["problems"]) == 1 and "is a str, not a int" in r["problems"][0]


def test_an_EMPTY_dict_override_is_named_rather_than_counted_as_configured(tmp_path):
    """`{"role_soft_limits_lines": {}}` changes nothing and is not a mistake the type check can
    see. Silence here is the "looks configured and does nothing" state this whole layer exists to
    give a voice to."""
    r = roles_probe(tmp_path, {"role_soft_limits_lines": {}})
    assert r["roles"]["Canon"] == 800
    assert r["overridden"] == {}
    assert len(r["problems"]) == 1 and "nothing to override" in r["problems"][0]


def test_overridden_and_problems_read_the_config_ONCE_between_them(tmp_path):
    """Both surfaces print both, and `overridden()` used to re-read and re-parse `config.json`
    behind the cache — contradicting this module's own stated guarantee that a hook invocation
    reads once. Counted by making the file unreadable AFTER the first call: a second read would
    come back empty and disagree with the first."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"vault": str(tmp_path / "vault"),
                               "limits": {"boot_budget_bytes": 100000}}), encoding="utf-8")
    prog = (
        "import sys, json, os; sys.path.insert(0, {hooks!r}); import limits;"
        " first = limits.overridden();"
        " os.remove({cfg!r});"
        " print(json.dumps({{'first': first, 'second': limits.overridden(),"
        " 'problems': limits.problems(), 'budget': limits.get('boot_budget_bytes')}}))"
    ).format(hooks=str(HOOKS), cfg=str(cfg))
    p = subprocess.run([sys.executable, "-B", "-c", prog], capture_output=True, text=True,
                       timeout=30, stdin=subprocess.DEVNULL,
                       env=clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path))
    assert p.returncode == 0, p.stderr
    r = json.loads(p.stdout)
    assert r["first"] == r["second"] == {"boot_budget_bytes": 100000}, r
    assert r["budget"] == 100000 and r["problems"] == []


def test_a_BOOL_role_value_is_rejected_like_a_bool_anywhere_else(tmp_path):
    """`True` is an `int` to Python and never a threshold to anyone else. The top level has said so
    since the row shipped; the sub-keys got the same clause and no test, and a mutation removing it
    left all twenty-four green — correct code with nothing holding it there."""
    r = roles_probe(tmp_path, {"role_soft_limits_lines": {"Canon": True}})
    assert r["roles"]["Canon"] == 800, r["roles"]
    assert r["overridden"] == {}
    assert len(r["problems"]) == 1 and "is a bool, not a int" in r["problems"][0]


def file_roles_probe(tmp_path: Path, file_limits, config_limits=None):
    """The same question asked through the GEDAECHTNIS_LIMITS FILE layer instead of config.json."""
    f = tmp_path / "file-limits.json"
    f.write_text(json.dumps(file_limits), encoding="utf-8")
    cfg = tmp_path / "config.json"
    body = {"vault": str(tmp_path / "vault")}
    if config_limits is not None:
        body["limits"] = config_limits
    cfg.write_text(json.dumps(body), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", "-c", ROLES_PROBE.format(hooks=str(HOOKS))],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                       env=clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path,
                                     GEDAECHTNIS_LIMITS=f))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_the_FILE_layer_merges_a_dict_limit_per_role_too(tmp_path):
    """★ THE SAME DEFECT THROUGH THE OTHER DOOR. The per-role merge was added where the defect was
    FOUND — the vault's `config.json` — and the `GEDAECHTNIS_LIMITS` file layer kept assigning the
    table wholesale. That seam is not hypothetical machinery: it is live on this machine as the
    interim boot-budget override, and one day's edit adding a partial role table would have
    reproduced the identical silent loss, with the fix's own comment claiming the guarantee for the
    TYPE, unqualified.

    A rule stated about a value and enforced at one of its two entrances is not enforced."""
    r = file_roles_probe(tmp_path, {"role_soft_limits_lines": {"Position": 999}})
    assert r["roles"]["Position"] == 999, r["roles"]
    for role, shipped in (("Map", 100), ("Canon", 800), ("Errata", 400)):
        assert r["roles"][role] == shipped, (role, r["roles"])


def test_the_FILE_layer_still_REPLACES_a_SCALAR_limit(tmp_path):
    """The negative control, and the one that keeps the fix from quietly changing the declared
    precedence. The file layer replaces the base table key by key — that is what the docstring
    says and what two eval harnesses depend on. What it may not do is reach INSIDE a key and drop
    entries nobody named. Grain follows the KEY's type, never the layer."""
    r = file_roles_probe(tmp_path, {"boot_budget_bytes": 55555,
                                    "role_soft_limits_lines": {"Position": 999}})
    assert r["roles"]["Position"] == 999 and r["roles"]["Canon"] == 800
    probe_budget = subprocess.run(
        [sys.executable, "-B", "-c", PROBE.format(hooks=str(HOOKS))], capture_output=True,
        text=True, timeout=30, stdin=subprocess.DEVNULL,
        env=clean_env(GEDAECHTNIS_CONFIG=tmp_path / "config.json", HOME=tmp_path,
                      GEDAECHTNIS_LIMITS=tmp_path / "file-limits.json"))
    assert probe_budget.returncode == 0, probe_budget.stderr
    assert json.loads(probe_budget.stdout)["budget"] == 55555


def test_the_vault_layer_still_wins_OVER_the_file_layer_per_role(tmp_path):
    """Three layers, one table: defaults under the file under the vault, per role. The role only
    the file names keeps the file's number; the role both name takes the vault's; the roles neither
    names keep the shipped ones."""
    r = file_roles_probe(tmp_path, {"role_soft_limits_lines": {"Position": 999, "Canon": 111}},
                         config_limits={"role_soft_limits_lines": {"Canon": 222}})
    assert r["roles"]["Position"] == 999, r["roles"]
    assert r["roles"]["Canon"] == 222, r["roles"]
    assert r["roles"]["Map"] == 100, r["roles"]


def test_a_float_limit_accepts_an_int(tmp_path):
    """`compaction_floor_share` ships as 0.4. JSON has one number type and a user writing `1`
    means 1.0; refusing that would be the layer inventing a rule of its own."""
    r = probe(tmp_path, {"compaction_floor_share": 1})
    assert r["problems"] == [] and r["overridden"] == {"compaction_floor_share": 1}


# ------------------------------- the table an override is MEASURED against ----
def test_a_limit_that_exists_only_in_the_FILE_can_be_overridden_by_the_vault(tmp_path):
    """★ THE VALIDATOR MUST ASK THE TABLE THAT ANSWERS, NOT THE ONE THAT SHIPS.

    Names and types were checked against `DEFAULTS` while `get()` answered from DEFAULTS+FILE. So a
    limit present only in `rules/limits.json` (or in a `GEDAECHTNIS_LIMITS` file) was LIVE and
    un-overridable: the vault's attempt came back *`X` is not a limit this package reads* — a
    sentence that is false, that blames the user for the package's bookkeeping, and that leaves
    them sitting on the old value believing they raised it.

    Nothing would have caught it later either: the suite pins DEFAULTS ⊆ shipped file, so ADDING a
    key to the shipped file alone stays green and creates this state silently."""
    r = file_roles_probe(tmp_path, {"newkey_bytes": 5}, config_limits={"newkey_bytes": 9})
    assert r["problems"] == [], r["problems"]
    probe_new = subprocess.run(
        [sys.executable, "-B", "-c",
         ("import sys, json; sys.path.insert(0, {hooks!r}); import limits;"
          " print(json.dumps({{'v': limits.get('newkey_bytes'), 'p': limits.problems()}}))"
          ).format(hooks=str(HOOKS))],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        env=clean_env(GEDAECHTNIS_CONFIG=tmp_path / "config.json", HOME=tmp_path,
                      GEDAECHTNIS_LIMITS=tmp_path / "file-limits.json"))
    assert probe_new.returncode == 0, probe_new.stderr
    out = json.loads(probe_new.stdout)
    assert out["v"] == 9, out
    assert out["p"] == [], out


def test_a_name_in_NEITHER_table_is_still_named(tmp_path):
    """The negative control: widening the whitelist from DEFAULTS to DEFAULTS+FILE may not widen it
    to everything. A key no layer defines is the typo the validation exists for."""
    r = file_roles_probe(tmp_path, {"newkey_bytes": 5}, config_limits={"nosuchkey": 9})
    assert len(r["problems"]) == 1 and "not a limit this package reads" in r["problems"][0]


def test_a_ROLE_added_only_by_the_FILE_can_be_overridden_too(tmp_path):
    """The same defect one level down, in the dict limit's sub-keys: a role the file layer adds is
    live in the table every consumer reads, and was rejected as a name this package does not
    know."""
    r = file_roles_probe(tmp_path, {"role_soft_limits_lines": {"Eidos": 400}},
                         config_limits={"role_soft_limits_lines": {"Eidos": 600}})
    assert r["roles"]["Eidos"] == 600, r["roles"]
    assert r["problems"] == [], r["problems"]


def test_a_NON_DICT_in_the_file_layer_cannot_turn_a_per_role_override_into_a_REPLACE(tmp_path):
    """★ THE SILENT LOSS, SURVIVING ITS OWN FIX BY COMING BACK THROUGH THE UNVALIDATED LAYER.

    `_apply` merged only when BOTH sides were dicts, and the FILE layer is validated by nobody. A
    file saying `{"role_soft_limits_lines": ["Position"]}` made the base a LIST, so the vault's
    per-role override fell through to the replace branch and deleted the seven roles it did not
    name — `problems()` empty, exactly the state the merge was written to prevent.

    The grain is now read off the SHIPPED definition, which no layer can rewrite."""
    r = file_roles_probe(tmp_path, {"role_soft_limits_lines": ["Position"]},
                         config_limits={"role_soft_limits_lines": {"Position": 999}})
    assert r["roles"]["Position"] == 999, r["roles"]
    for role, shipped in (("Map", 100), ("Canon", 800), ("Errata", 400), ("Annales", 500)):
        assert r["roles"][role] == shipped, (role, r["roles"])


def test_the_three_answers_come_from_ONE_read_and_cannot_disagree(tmp_path):
    """`problems()` and `overridden()` used to early-return off a cache keyed on ONE of three
    globals. Anything that installed a table directly — a test does — left the other two at None,
    and both accessors then reported "nothing configured, nothing wrong" for a vault that had
    configured plenty. Three facts produced by one read are now one value, so there is no state in
    which some of them are set."""
    prog = (
        "import sys, json; sys.path.insert(0, {hooks!r}); import limits;"
        " limits._STATE = ({{'boot_budget_bytes': 7}}, ['a problem'], {{'boot_budget_bytes': 7}});"
        " print(json.dumps({{'v': limits.get('boot_budget_bytes'), 'p': limits.problems(),"
        " 'o': limits.overridden()}}))"
    ).format(hooks=str(HOOKS))
    p_ = subprocess.run([sys.executable, "-B", "-c", prog], capture_output=True, text=True,
                        timeout=30, stdin=subprocess.DEVNULL,
                        env=clean_env(GEDAECHTNIS_CONFIG=tmp_path / "absent.json", HOME=tmp_path))
    assert p_.returncode == 0, p_.stderr
    r = json.loads(p_.stdout)
    assert r["v"] == 7 and r["p"] == ["a problem"] and r["o"] == {"boot_budget_bytes": 7}, r


# ------------------------------------------------------- survivability ----
def test_a_malformed_config_file_leaves_the_defaults_standing_AND_SAYS_SO(tmp_path):
    """The same contract `config.py` already keeps — a config bug never takes a session down — but
    NOT in silence.

    ★ The third state. `config._load()` swallows a broken file and returns `{}`, which reaches this
    layer identical to "this vault configures nothing", and the surfaces then printed the
    reassuring one: `shipped defaults; config.json overrides none`. A clean bill of health for a
    vault whose config is configured and applying none of it — the exact failure this layer exists
    to give a voice to, arriving through the one door nobody had checked. A trailing comma is
    enough to produce it."""
    r = probe(tmp_path, None, raw_config='{"vault": "/tmp/v", "limits": {"boot_budget_bytes": 100000},}')
    assert r["budget"] == 20000, "the session must stand on the shipped numbers"
    assert len(r["problems"]) == 1 and "could not be read as a JSON object" in r["problems"][0]
    assert r["overridden"] == {}


def test_an_ABSENT_config_file_is_not_reported_as_a_broken_one(tmp_path):
    """The negative control, and the reason the check asks whether the file EXISTS. An install
    that never wrote a config is the common case and is not a misconfiguration; reporting it would
    put a permanent problem line in every session's boot facts for a vault doing nothing wrong."""
    env = clean_env(GEDAECHTNIS_CONFIG=tmp_path / "absent.json", HOME=tmp_path)
    p_ = subprocess.run([sys.executable, "-B", "-c", PROBE.format(hooks=str(HOOKS))],
                        capture_output=True, text=True, env=env, timeout=30,
                        stdin=subprocess.DEVNULL)
    assert p_.returncode == 0, p_.stderr
    r = json.loads(p_.stdout)
    assert r["budget"] == 20000 and r["problems"] == [] and r["overridden"] == {}


def test_a_documentation_key_in_the_limits_object_is_SKIPPED_not_reported(tmp_path):
    """`_`-prefixed keys are documentation — the FILE layer skips them and this module's own
    docstring promises the loader does not validate them. This layer reported them instead, and the
    cost landed on the most natural way to write a config: `rules/limits.json` ships sixteen
    `_`-prefixed reasoning keys beside its sixteen thresholds, so a user who copied it as a
    starting point got SIXTEEN permanent NOT-APPLIED lines — in `status`, and in the boot facts of
    every session — for a configuration that was entirely correct. A surface that cries wolf
    sixteen times on a correct config is not a surface anyone reads on the seventeenth."""
    r = probe(tmp_path, {"_boot_budget_bytes": 100000, "boot_budget_bytes": 100000})
    assert r["budget"] == 100000, r
    assert r["problems"] == [], r["problems"]
    assert r["overridden"] == {"boot_budget_bytes": 100000}


def test_an_unknown_key_WITHOUT_the_underscore_is_still_named(tmp_path):
    """The negative control for the one above: skipping documentation may not become skipping
    typos. `boot_budget` for `boot_budget_bytes` reads correctly and does nothing, which is the
    mistake this validation was built for."""
    r = probe(tmp_path, {"boot_budget": 100000})
    assert r["budget"] == 20000
    assert len(r["problems"]) == 1 and "not a limit this package reads" in r["problems"][0]


def test_the_seam_does_not_need_a_config_file_at_all(tmp_path):
    env = clean_env(GEDAECHTNIS_CONFIG=tmp_path / "absent.json", HOME=tmp_path)
    p = subprocess.run([sys.executable, "-B", "-c", PROBE.format(hooks=str(HOOKS))],
                       capture_output=True, text=True, env=env, timeout=30,
                       stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["budget"] == 20000


# ------------------------------------------------------------- the surfaces ----
def test_status_prints_the_override_and_the_rejection(tmp_path):
    """A problem nobody prints is a problem nobody has. Both directions in one run: the applied
    key is shown, and the rejected key is shown as NOT APPLIED."""
    vault = tmp_path / "vault"
    (vault / "Global").mkdir(parents=True)
    (vault / "Global" / "fleet-roster.md").write_text("repo: none\n", encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"vault": str(vault), "state_dir": str(tmp_path / "state"),
                               "limits": {"boot_budget_bytes": 100000, "boot_budget": 1}}),
                   encoding="utf-8")
    (tmp_path / "state").mkdir()
    p = subprocess.run([sys.executable, "-B", str(PLUGIN / "tools" / "status.py"),
                        "--cwd", str(tmp_path)], capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL,
                       env=clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path))
    assert p.returncode == 0, p.stderr
    assert "boot_budget_bytes=100000" in p.stdout
    assert "NOT APPLIED" in p.stdout and "boot_budget`" in p.stdout


def test_status_says_so_when_nothing_is_overridden(tmp_path):
    """The negative control for the line above — otherwise a blank line and a correct config are
    the same output."""
    vault = tmp_path / "vault"
    (vault / "Global").mkdir(parents=True)
    (vault / "Global" / "fleet-roster.md").write_text("repo: none\n", encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"vault": str(vault), "state_dir": str(tmp_path / "state")}),
                   encoding="utf-8")
    (tmp_path / "state").mkdir()
    p = subprocess.run([sys.executable, "-B", str(PLUGIN / "tools" / "status.py"),
                        "--cwd", str(tmp_path)], capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL,
                       env=clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path))
    assert p.returncode == 0, p.stderr
    assert "config.json overrides none" in p.stdout
    assert "NOT APPLIED" not in p.stdout


# ------------------------------------------------------------ the documentation ----
def test_the_key_is_documented_where_the_other_config_keys_are():
    """`config.py`'s docstring is the published list of what config.json may carry. A key readable
    only from the code that reads it is a key nobody configures."""
    doc = (HOOKS / "config.py").read_text(encoding="utf-8")
    head = doc.split('"""')[1]
    assert "\n  limits " in head, "the `limits` key must be listed in config.py's key table"


# ------------------------------------------------------ the session-start facts ----
def _facts_world(tmp_path, limits_obj):
    """A throwaway vault, state dir and repo, and a config.json carrying `limits_obj`."""
    vault = tmp_path / "vault"
    (vault / "Proj").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    (vault / "Proj" / "Map.md").write_text("# Proj\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(vault), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(vault), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "root"], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# project\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    cfg = tmp_path / "config.json"
    body = {"vault": str(vault), "state_dir": str(state)}
    if limits_obj is not None:
        body["limits"] = limits_obj
    cfg.write_text(json.dumps(body), encoding="utf-8")
    env = clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path,
                    GEDAECHTNIS_USER_MEMORY=tmp_path / "no-such-user-memory.md",
                    GEDAECHTNIS_FLEET_ROSTER=tmp_path / "no-such-roster.md")
    p = subprocess.run([sys.executable, "-B", str(HOOKS / "session_start.py")],
                       input=json.dumps({"session_id": "s1", "cwd": str(repo),
                                         "source": "startup"}),
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def test_the_facts_name_a_raised_ceiling_and_a_rejected_one(tmp_path):
    """The facts block is what a session actually reads. A threshold this vault raised for itself
    belongs there, and so does one it MEANT to raise: a session acting on the shipped 20,000 while
    its config says 100,000 has no way to notice from anywhere else."""
    ctx = _facts_world(tmp_path, {"boot_budget_bytes": 100000, "boot_budget": 1})
    assert "boot_budget_bytes=100000" in ctx
    assert "Vault limits NOT APPLIED" in ctx and "boot_budget`" in ctx


def test_the_facts_are_silent_when_the_vault_configures_no_limits(tmp_path):
    """The negative control: an ordinary install must not gain a line about a seam it does not use.
    Without this, the line above is satisfied by a hook that prints unconditionally."""
    ctx = _facts_world(tmp_path, None)
    assert "Vault limits" not in ctx


# ------------------------------------- the fixtures' own isolation, pinned ----
def test_every_fixture_role_table_NAMES_ALL_EIGHT_ROLES():
    """★ A FIXTURE'S ISOLATION MUST BE WRITTEN DOWN, NOT INHERITED FROM A LAYER'S SEMANTICS.

    Six suites set a limits FILE with a one- or two-role table, and that WAS their isolation: the
    file replaced the table, so a role they did not name had no limit and could not produce a
    finding. When dict limits began merging per sub-key — a fix for a silent protection loss — an
    unnamed role started inheriting the SHIPPED default instead. Every one of those suites stayed
    green while quietly no longer isolating what it was written to isolate, which is the worst
    available outcome: the tests still pass and no longer mean what their author meant.

    So the tables name every role, and this asks them to. A suite that wants a role out of the way
    gives it a ceiling nothing in that suite can reach — the same statement the short table used to
    make, made out loud, where the next reader and this check can both see it."""
    import ast
    roles = set(DEFAULTS_ROLES)
    checked, missing = [], []
    # This module is exempt, and only this one: partial tables are its SUBJECT — half its cases
    # exist to prove what happens when a layer names one role and not the others. An exemption
    # that swallowed any other file would swallow the defect.
    for py in sorted((PLUGIN / "tests").glob("test_*.py")):
        if py.name == "test_limits_seam.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, val in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "role_soft_limits_lines"):
                    continue
                if not isinstance(val, ast.Dict):
                    continue
                named = {k.value for k in val.keys if isinstance(k, ast.Constant)}
                checked.append(py.name)
                if roles - named:
                    missing.append(f"{py.name}:{val.lineno} omits {sorted(roles - named)}")
    assert checked, "the sweep found no role table at all — it is blind, not satisfied"
    assert not missing, (
        "a fixture's role table leaves roles unnamed, so those roles silently take the SHIPPED "
        "default and the suite no longer isolates what it was written to isolate:\n  "
        + "\n  ".join(missing))


# ------------------------------------------- the layer that could not say it was absent ----
def test_a_BY_PATH_import_SAYS_the_config_layer_was_never_consulted(tmp_path):
    """★ "NOTHING CONFIGURED" AND "I COULD NOT LOOK" WERE THE SAME ANSWER.

    `import config` resolves only when `hooks/` is on `sys.path` — true for every shipped caller,
    because `import limits` needs it too and `config` sits in the same directory. A caller that
    loads this module BY PATH (`spec_from_file_location`) gets neither, and the bare
    `except Exception: return {}, []` then reported exactly what an unconfigured vault reports.

    Measured before the fix: a vault whose `config.json` says 100,000 came back **20,000 with
    `problems()` empty** — a vault sitting on the shipped ceiling believing it had raised it, which
    is the sentence this function's own docstring uses to justify its validation.

    The VALUE still falls through to the defaults: a hook that cannot read a config must not die.
    What changes is that the surfaces say so."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"vault": str(tmp_path / "vault"),
                               "limits": {"boot_budget_bytes": 100000}}), encoding="utf-8")
    prog = (
        "import importlib.util, json, sys\n"
        "spec = importlib.util.spec_from_file_location('limits', {limits!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print(json.dumps({{'v': m.get('boot_budget_bytes'), 'p': m.problems()}}))\n"
    ).format(limits=str(HOOKS / "limits.py"))
    p = subprocess.run([sys.executable, "-B", "-c", prog], capture_output=True, text=True,
                       timeout=30, stdin=subprocess.DEVNULL,
                       env=clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path))
    assert p.returncode == 0, p.stderr
    r = json.loads(p.stdout)
    assert r["v"] == 20000, "the value must still fall through to the shipped default"
    assert len(r["p"]) == 1, r["p"]
    assert "could not be imported" in r["p"][0] and "sys.path" in r["p"][0], r["p"]


def test_an_ORDINARY_import_stays_SILENT(tmp_path):
    """The negative control, and the one that matters: every shipped caller imports this module by
    name, and a problem line printed on every correct install is a line nobody reads."""
    r = probe(tmp_path, {"boot_budget_bytes": 100000})
    assert r["problems"] == [], r["problems"]
    assert r["budget"] == 100000


def test_a_FILE_ONLY_dict_key_keeps_the_sub_keys_the_override_did_not_name(tmp_path):
    """★ THE SAME RULE'S FOURTH ENTRANCE (E1 reviewer finding F1).

    `_apply` read the grain from `DEFAULTS` — which is `None` for a key the FILE layer INTRODUCED,
    and letting the file add a limit was fix 1's whole purpose. So the validator accepted a partial
    override of a file-only dict key and `_apply` then took the scalar-replace branch: the sub-keys
    nobody named were dropped, `problems()` empty.

    Three earlier fixes, each correct where it stood. The rule never had an owner — only a sequence
    of correct local answers."""
    r = file_roles_probe(tmp_path, {"newdict_limits": {"a": 1, "b": 2, "c": 3}},
                         config_limits={"newdict_limits": {"a": 999}})
    assert r["problems"] == [], r["problems"]
    probe_new = subprocess.run(
        [sys.executable, "-B", "-c",
         ("import sys, json; sys.path.insert(0, {hooks!r}); import limits;"
          " print(json.dumps(limits.get('newdict_limits')))").format(hooks=str(HOOKS))],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
        env=clean_env(GEDAECHTNIS_CONFIG=tmp_path / "config.json", HOME=tmp_path,
                      GEDAECHTNIS_LIMITS=tmp_path / "file-limits.json"))
    assert probe_new.returncode == 0, probe_new.stderr
    assert json.loads(probe_new.stdout) == {"a": 999, "b": 2, "c": 3}, probe_new.stdout


def test_a_documentation_key_whose_REAL_TWIN_IS_MISSING_is_named(tmp_path):
    """The `_`-key silence keeps ONE case (E1 reviewer finding F2): the user wrote
    `_boot_budget_bytes` and not `boot_budget_bytes`. Reading a file where every threshold sits
    beside its `_`-prefixed twin, editing the wrong one of a pair is the available mistake — and
    its result is a vault believing it raised a ceiling it is still sitting under."""
    r = probe(tmp_path, {"_boot_budget_bytes": 100000})
    assert r["budget"] == 20000
    assert len(r["problems"]) == 1 and "did you mean `boot_budget_bytes`" in r["problems"][0]


def test_a_documentation_key_BESIDE_its_real_twin_stays_quiet(tmp_path):
    """The negative control, and the reason the silence was introduced: `rules/limits.json` ships
    sixteen `_`-prefixed reasoning keys BESIDE their sixteen thresholds. A user who copies it as a
    starting point must not get sixteen NOT-APPLIED lines in every session's boot facts for a
    configuration that is entirely correct."""
    r = probe(tmp_path, {"_boot_budget_bytes": "why we raised it", "boot_budget_bytes": 100000})
    assert r["budget"] == 100000
    assert r["problems"] == [], r["problems"]


def test_a_config_module_that_RAISES_is_reported_too(tmp_path):
    """The ImportError arm's sibling, and it needed its own control: a mutation that silenced the
    GENERIC arm left every test green, because nothing in the suite could make `config._load()`
    raise something other than an ImportError.

    So a stub `config` is put ahead of the real one on `sys.path` and told to raise. The value must
    still fall through to the shipped defaults — a hook that cannot read a config must not take the
    session down — and the surfaces must say the layer was never consulted."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "config.py").write_text(
        "def _load():\n"
        "    raise RuntimeError('the config layer is wedged')\n"
        "def unreadable():\n"
        "    return False\n"
        "def state():\n"
        "    import pathlib; return pathlib.Path(%r)\n"
        "def vault():\n"
        "    import pathlib; return pathlib.Path(%r)\n" % (str(tmp_path / "state"), str(tmp_path / "vault")),
        encoding="utf-8")
    prog = (
        "import sys, json\n"
        "sys.path.insert(0, {hooks!r})\n"
        "sys.path.insert(0, {stub!r})\n"
        "import limits\n"
        "print(json.dumps({{'v': limits.get('boot_budget_bytes'), 'p': limits.problems()}}))\n"
    ).format(hooks=str(HOOKS), stub=str(stub))
    p = subprocess.run([sys.executable, "-B", "-c", prog], capture_output=True, text=True,
                       timeout=30, stdin=subprocess.DEVNULL,
                       env=clean_env(GEDAECHTNIS_CONFIG=tmp_path / "config.json", HOME=tmp_path))
    assert p.returncode == 0, p.stderr
    r = json.loads(p.stdout)
    assert r["v"] == 20000, r
    assert len(r["p"]) == 1 and "could not be consulted" in r["p"][0], r["p"]
    assert "RuntimeError" in r["p"][0], r["p"]


def test_a_documentation_key_with_NO_REAL_TWIN_invents_no_suggestion(tmp_path):
    """The other half of the twin guard, and the reviewer found it uncontrolled.

    `twin in base` is what stops the hint being FABRICATED. Without it, `_totallyMadeUp` produces
    *"did you mean `totallyMadeUp`?"* — a suggestion for a name that is not a setting at all, which
    sends a confused user looking for a key that has never existed. Documentation is free to say
    anything; a `_` key whose lstripped name is not a real limit is prose, and prose stays silent.

    The shipped behaviour was already right. Nothing was holding it there — which is the state
    this row's own report calls out, arriving in this row's own new code."""
    r = probe(tmp_path, {"_totallyMadeUp": 100000, "boot_budget_bytes": 100000})
    assert r["budget"] == 100000
    assert r["problems"] == [], r["problems"]


@pytest.mark.parametrize("key", ["__dunder__", "_", "___", "_.", "_-"])
def test_odd_underscore_keys_stay_silent_and_never_crash(tmp_path, key):
    """The shapes the reviewer probed by hand, pinned. `"__dunder__".lstrip("_")` is `"dunder__"`
    (lstrip strips only the left), `"_".lstrip("_")` is `""` — none of them names a limit, so all
    of them are prose. What matters as much as the silence is that none of them raises: this runs
    inside a hook, and a hook that dies is a session that dies."""
    r = probe(tmp_path, {key: "some prose", "boot_budget_bytes": 100000})
    assert r["budget"] == 100000, r
    assert r["problems"] == [], r["problems"]


# ------------------------------------------------------- a BOOL limit (SPLITAUTO-1 reviewer) ----
BOOL_PROBE = (
    "import sys, json; sys.path.insert(0, {hooks!r}); import limits;"
    " print(json.dumps({{'auto': limits.get('split_auto_apply'),"
    " 'overridden': limits.overridden(), 'problems': limits.problems()}}))"
)


def bool_probe(tmp_path: Path, limits_obj):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"vault": str(tmp_path / "vault"), "limits": limits_obj}),
                   encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", "-c", BOOL_PROBE.format(hooks=str(HOOKS))],
                       capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                       env=clean_env(GEDAECHTNIS_CONFIG=cfg, HOME=tmp_path))
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_a_vault_can_set_a_BOOL_limit_in_BOTH_directions(tmp_path):
    """★ `split_auto_apply` is the package's first bool limit, and this seam could not set it.

    The type check refused every bool BEFORE the type test could accept one, so the message read
    "`split_auto_apply` is a bool, not a bool — not applied" — a sentence that is its own proof the
    branch had never run on a bool limit. It failed safe only by accident (ON was equally
    unreachable), and it meant the one knob standing between a person's vault and an automatic
    file move could not be set through the seam that exists for exactly that.

    Both directions, because a seam that can only turn something off is not a seam."""
    off = bool_probe(tmp_path, {"split_auto_apply": False})
    assert off["auto"] is False and off["problems"] == [], off
    assert off["overridden"] == {"split_auto_apply": False}, off
    on = bool_probe(tmp_path, {"split_auto_apply": True})
    assert on["auto"] is True and on["problems"] == [], on
    assert on["overridden"] == {"split_auto_apply": True}, on


def test_a_bool_is_still_refused_for_a_NUMERIC_limit(tmp_path):
    """The rule the old branch was written for, and it must survive the fix: `True` is `1` to
    Python and a threshold to nobody. Without this, widening the bool case could quietly let
    `{"boot_budget_bytes": true}` through as a ceiling of 1."""
    r = bool_probe(tmp_path, {"boot_budget_bytes": True})
    assert r["overridden"] == {}, r
    assert any("not a int" in p for p in r["problems"]), r["problems"]
