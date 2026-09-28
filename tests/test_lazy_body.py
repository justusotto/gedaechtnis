"""lazy_body.py — the region's pull-only bodies, injected once when a path under it is touched.

★ WHAT THIS SUITE IS ORGANISED AROUND. This arm's failure mode is not a wrong answer, it is a
REPEATED one: an injection that fires again after every tool call would spend a region's whole
body set on each of fifty calls and nobody would notice until the bill arrived. So every rule here
carries BOTH controls — the touch that injects AND the touch that must be silent — and the silent
side is the one that decides whether the arm is safe.

The three gates the arm must not cross, each with its own negative control:
  * a region with NO Boot file is never injected (its bodies are already in the boot chain);
  * a second touch of the same region is silent;
  * a claim that cannot reach disk means SILENCE, not a re-fire.

Vault, state, limits and config are redirected into tmp_path. This suite never reads the real
vault, the real ~/.claude/gedaechtnis, or the machine's transcripts.
"""
from __future__ import annotations
import importlib, json, os, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"

POSITION = "# Position\n\nThe region stands at X, decided on 2026-01-02.\n"
CANON = "# Canon\n\n## The decision this session is about to contradict\n\nLocked 2026-01-01.\n"
ERRATA = "# Errata\n\n## A measured ZERO needs a positive control\n\nSeen twice.\n"
ANNALES = "# Annales\n\n## 2026-01-02\n\n- 10:00 — something happened (abc1234)\n"


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two regions: one GRADUATED (a Boot file + bodies), one not (bodies only)."""
    vault = tmp_path / "Atlas"
    # Graduated — this is the one the arm exists for.
    write(vault / "Speculum" / "Kernel.md", "# Speculum Kernel\n\nAn index.\n")
    write(vault / "Speculum" / "Position.md", POSITION)
    write(vault / "Speculum" / "Canon.md", CANON)
    write(vault / "Speculum" / "Errata.md", ERRATA)
    write(vault / "Speculum" / "Annales.md", ANNALES)
    write(vault / "Speculum" / "Inbox.md", "# Inbox\n\n- a hook-written row\n")
    # NOT graduated — its bodies are still in the boot chain, so it must never be injected.
    write(vault / "Mnemosyne" / "Position.md", "# Mnemosyne Position\n\nUngraduated.\n")
    write(vault / "Mnemosyne" / "Canon.md", "# Mnemosyne Canon\n\nUngraduated.\n")

    state = tmp_path / "state"
    limits_file = tmp_path / "limits.json"
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    shipped["lazy_bodies_enabled"] = True                      # ON, so the behaviour is reachable
    limits_file.write_text(json.dumps(shipped), encoding="utf-8")

    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(vault))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(state))
    monkeypatch.setenv("GEDAECHTNIS_LIMITS", str(limits_file))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-such-config.json"))
    for d in (HOOKS, PLUGIN):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
    import config, limits, common                              # noqa: PLC0415
    importlib.reload(config); importlib.reload(common); importlib.reload(limits)
    import lesson_push, lazy_body                              # noqa: PLC0415
    importlib.reload(lesson_push); importlib.reload(lazy_body)
    yield dict(tmp=tmp_path, vault=vault, state=state, limits_file=limits_file, mod=lazy_body)
    for var in ("GEDAECHTNIS_VAULT", "GEDAECHTNIS_STATE_DIR", "GEDAECHTNIS_LIMITS",
                "GEDAECHTNIS_CONFIG"):
        os.environ.pop(var, None)
    importlib.reload(config); importlib.reload(common); importlib.reload(limits)
    importlib.reload(lesson_push); importlib.reload(lazy_body)


def set_limit(world, **kw):
    data = json.loads(world["limits_file"].read_text(encoding="utf-8"))
    data.update(kw)
    world["limits_file"].write_text(json.dumps(data), encoding="utf-8")
    import limits, lazy_body                                   # noqa: PLC0415
    importlib.reload(limits); importlib.reload(lazy_body)
    return lazy_body


# ------------------------------------------------------------------ the injection ----

def test_first_touch_of_a_graduated_region_injects_its_bodies(world):
    """POSITIVE. The content is asserted, not the fact that something was returned: a check that
    only asserted `is not None` would pass on an empty header with no bodies in it."""
    m = world["mod"]
    out = m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    assert out is not None
    assert "Speculum/Position.md" in out
    assert "The region stands at X" in out                     # the FILE'S OWN CONTENT
    assert "The decision this session is about to contradict" in out
    assert "Speculum/Kernel.md" in out                         # named as the index it supplements


def test_the_boot_file_is_never_injected(world):
    """The session already has it — that is the definition of a Boot file."""
    m = world["mod"]
    out = m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    assert "--- Speculum/Kernel.md ---" not in out
    assert "An index." not in out


def test_the_inbox_is_never_injected(world):
    """Hook-written rows for the owning lane to fold. Not memory, and it moves under the session."""
    m = world["mod"]
    out = m.notice("s1", world["vault"] / "Speculum" / "Canon.md", None)
    assert "--- Speculum/Inbox.md ---" not in out
    assert "a hook-written row" not in out


def test_a_second_touch_of_the_same_region_is_silent(world):
    """NEGATIVE, and the one that decides whether the arm is affordable at all."""
    m = world["mod"]
    assert m.notice("s1", world["vault"] / "Speculum" / "Position.md", None) is not None
    assert m.notice("s1", world["vault"] / "Speculum" / "Canon.md", None) is None
    assert m.notice("s1", world["vault"] / "Speculum" / "Errata.md", None) is None


def test_a_different_session_is_not_deduped_by_the_first(world):
    """POSITIVE for the key: once per SESSION per region, not once per region forever."""
    m = world["mod"]
    assert m.notice("s1", world["vault"] / "Speculum" / "Position.md", None) is not None
    assert m.notice("s2", world["vault"] / "Speculum" / "Position.md", None) is not None


def test_an_ungraduated_region_is_never_injected(world):
    """NEGATIVE. Its bodies are still in the boot chain; injecting them charges the session twice.

    The region is REAL and has real bodies — `bodies_for` returns them — so this test cannot pass
    by the region simply being empty, which is the vacuous way it would otherwise go green."""
    m = world["mod"]
    assert m.bodies_for("Mnemosyne"), "fixture broken: the ungraduated region has no bodies"
    assert m.notice("s1", world["vault"] / "Mnemosyne" / "Position.md", None) is None


def test_a_path_in_no_region_injects_nothing(world):
    """NEGATIVE. A write to the machine's temp dir is not work in a region."""
    m = world["mod"]
    assert m.notice("s1", world["tmp"] / "scratch" / "notes.md", None) is None


def test_the_claim_is_not_taken_when_the_region_has_no_bodies(world):
    """A claim burned on an empty region would silence a LATER, real touch of it — so the arm
    must not take one. The control drives that ordering: create the bodies after the first touch."""
    m = world["mod"]
    (world["vault"] / "Fresh").mkdir()
    write(world["vault"] / "Fresh" / "Kernel.md", "# Fresh Kernel\n")
    assert m.notice("s1", world["vault"] / "Fresh" / "Position.md", None) is None
    write(world["vault"] / "Fresh" / "Position.md", "# Fresh Position\n\nNow it exists.\n")
    out = m.notice("s1", world["vault"] / "Fresh" / "Position.md", None)
    assert out is not None and "Now it exists." in out


# ------------------------------------------------------------------ the budget ----

def test_the_budget_cuts_the_tail_and_names_what_it_cut(world):
    """The cap BITES, and a skipped file is NAMED with its size — a silent drop would leave the
    session believing it had the whole record."""
    m = set_limit(world, lazy_body_max_bytes=len(POSITION) + 5)
    out = m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    assert "The region stands at X" in out                     # Position is first in BODY_ORDER
    assert "The decision this session is about to contradict" not in out
    assert "Speculum/Canon.md" in out and " B)" in out         # named, with its size
    assert "on disk, not missing" in out


def test_a_body_that_EXACTLY_fills_the_remaining_budget_is_included(world):
    """★ MUST-FIX 1, from the reviewer: the `spent + n <= cap` boundary had no control. Mutating
    it to `<` left all 21 controls green, because every other budget test leaves margin.

    Inclusive is the intended behaviour — a body that exactly fits is not over budget — and the
    regression it would otherwise ship silently is a body dropped for fitting perfectly."""
    m = set_limit(world, lazy_body_max_bytes=len(POSITION))     # ZERO margin, by construction
    out = m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    assert out is not None
    assert "The region stands at X" in out, "a body that exactly fills the budget was skipped"
    assert "The decision this session is about to contradict" not in out


def test_a_body_ONE_BYTE_over_the_remaining_budget_is_skipped(world):
    """The other side of the same boundary. Without this pair the cap could be off by one in
    either direction and one test alone would still pass."""
    m = set_limit(world, lazy_body_max_bytes=len(POSITION) - 1)
    out = m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    assert "The region stands at X" not in out, "a body over the budget was included"


def test_a_boot_file_that_is_a_DIRECTORY_does_not_graduate_the_region(world):
    """FINDING 3, from the reviewer: `has_boot_file`'s `.is_file()` was indistinguishable from
    `.exists()` in every control. A directory named `Kernel.md` is the case that separates them,
    and `.exists()` would call such a region graduated and then inject its bodies as if the
    session had a Boot file it does not have."""
    m = world["mod"]
    (world["vault"] / "Dir" / "Kernel.md").mkdir(parents=True)
    write(world["vault"] / "Dir" / "Position.md", "# Dir Position\n\nBodies exist here.\n")
    assert (world["vault"] / "Dir" / "Kernel.md").exists(), "fixture broken: nothing to distinguish"
    assert m.has_boot_file("Dir") is False
    assert m.notice("s1", world["vault"] / "Dir" / "Position.md", None) is None


def test_body_order_decides_what_survives_the_cut(world):
    """POSITIVE for the ORDER, not just for the cap. Annales is last in BODY_ORDER, Position
    first; a cap that admits exactly one file must admit Position. A suite that only asserted
    'something was cut' would pass on any order at all, including alphabetical."""
    m = set_limit(world, lazy_body_max_bytes=len(POSITION) + 5)
    out = m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    assert "--- Speculum/Position.md ---" in out
    assert "--- Speculum/Annales.md ---" not in out


def test_every_body_over_the_budget_still_names_them_all(world):
    """A cap smaller than the smallest file leaves `picked` empty. Silence there would be the
    worst outcome: the session learns neither the content nor that there is content."""
    m = set_limit(world, lazy_body_max_bytes=1)
    out = m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    assert out is not None
    assert "Speculum/Position.md" in out and "Speculum/Canon.md" in out
    assert "The region stands at X" not in out


def test_a_zero_budget_switches_the_arm_off(world):
    m = set_limit(world, lazy_body_max_bytes=0)
    assert m.notice("s1", world["vault"] / "Speculum" / "Position.md", None) is None


def test_a_malformed_budget_is_ignored_not_honoured(world):
    """A typo in a vault's override must not silently uncap the injection or crash the hook."""
    m = set_limit(world, lazy_body_max_bytes="sixty thousand")
    assert m.budget() == 0
    assert m.notice("s1", world["vault"] / "Speculum" / "Position.md", None) is None


# ------------------------------------------------------------------ the switch ----

def test_ships_off_by_default(world):
    """The shipped default is FALSE. If someone turns it on, this test goes red and they have to
    say so in the same act — the discipline `lesson_push_enabled` is pinned by."""
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    assert shipped["lazy_bodies_enabled"] is False
    m = set_limit(world, lazy_bodies_enabled=False)
    assert m.notice("s1", world["vault"] / "Speculum" / "Position.md", None) is None


def test_a_claim_that_cannot_reach_disk_stays_silent(world, monkeypatch):
    """NEGATIVE for the failure path. An arm that spoke when its dedupe failed would re-inject
    after every tool call for the rest of the session — the exact runaway the dedupe exists for."""
    m = world["mod"]
    # `_claim` returning False IS the contract for "the claim did not reach disk" — the module
    # converts the OSError itself and logs it. Driving the contract, not the exception, is what
    # keeps this control pointed at `notice`'s behaviour rather than at `_claim`'s internals.
    monkeypatch.setattr(m, "_claim", lambda *_a, **_k: False)
    assert m.notice("s1", world["vault"] / "Speculum" / "Position.md", None) is None


def test_a_read_only_state_dir_produces_a_false_claim_not_an_exception(world, monkeypatch):
    """The other half of the pair above: the real OSError path, so the two are each other's
    controls and neither can go decorative on its own."""
    m = world["mod"]
    import config                                              # noqa: PLC0415
    monkeypatch.setattr(config, "state", lambda: Path("/nonexistent-root/state"))
    assert m._claim("s1", "Speculum") is False
    assert m.notice("s1", world["vault"] / "Speculum" / "Position.md", None) is None


def test_the_claim_records_the_region_on_disk(world):
    """`shown` is the observable the negative control above reads. Assert the STATE, not only the
    absence of a second notice — the two can disagree, and that disagreement is the bug."""
    m = world["mod"]
    m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    assert m.shown("s1") == ["Speculum"]
    assert m.shown("s2") == []


# ------------------------------------------------------------------ several paths ----

def test_a_bash_command_touching_two_regions_injects_each_once(world):
    """The Bash trigger hands in a LIST. Two regions, two blocks, and no region twice."""
    m = world["mod"]
    write(world["vault"] / "Vita" / "Kernel.md", "# Vita Kernel\n")
    write(world["vault"] / "Vita" / "Position.md", "# Vita Position\n\nVita stands here.\n")
    out = m.notice("s1", [world["vault"] / "Speculum" / "Position.md",
                          world["vault"] / "Vita" / "Position.md",
                          world["vault"] / "Speculum" / "Canon.md"], None)
    assert out.count("its pull-only bodies") == 2
    assert "The region stands at X" in out and "Vita stands here." in out


def test_the_arm_is_read_only_toward_the_vault(world):
    """It reads bodies and writes nothing but its own state file. A hook that edited the vault
    while reporting on it would be a different kind of program."""
    m = world["mod"]
    before = {p: p.read_bytes() for p in (world["vault"]).rglob("*.md")}
    m.notice("s1", world["vault"] / "Speculum" / "Position.md", None)
    after = {p: p.read_bytes() for p in (world["vault"]).rglob("*.md")}
    assert before == after


# ------------------------------------------------------------------ the two lists ----

def test_the_skip_set_is_the_only_place_an_exclusion_is_decided(world):
    """★ FOUND BY MUTATION, NOT BY READING. The first draft expressed the exclusions twice — a
    SKIP set carrying the reasons, and an order tuple that simply omitted the same stems — so
    mutating the SKIP set changed nothing and the documented decision was dead code. Every skipped
    stem must now be a ROLE stem the order tuple does NOT also hide, or this pairing rots again."""
    m = world["mod"]
    import common                                              # noqa: PLC0415
    for stem in m.SKIP_STEMS:
        # A skip for something that was never a candidate is a SECOND dead guard — the exact
        # shape this test was written after. `Inbox` was on the list and is not a role stem.
        assert stem in common.ROLE_STEMS, f"{stem} is skipped but was never a candidate"
    assert set(m.body_stems()) == set(common.ROLE_STEMS) - set(m.SKIP_STEMS)
    assert not (set(m.BODY_ORDER) & set(m.SKIP_STEMS)), "an exclusion is expressed in both lists"


def test_a_role_stem_missing_from_the_order_is_offered_at_the_tail_not_dropped(world):
    """A vault that adds an on-demand role must not have it silently vanish. `Praxis` and
    `Exempla` are in the tuple; this drives a stem that is a real role and is NOT."""
    m = world["mod"]
    import common                                              # noqa: PLC0415
    # ★ THE TRIGGER CONDITION DOES NOT OCCUR IN THE REAL DATA — every shipped role stem is in
    # BODY_ORDER today, so a test written against the live set would be VACUOUS and its green
    # would be a fact about the taxonomy, not about the sort. The stem is INTRODUCED here.
    assert not (set(common.ROLE_STEMS) - set(m.BODY_ORDER) - set(m.SKIP_STEMS)), \
        "a role stem is now unlisted for real — this test may stop being the only driver"
    monkey = frozenset(set(common.ROLE_STEMS) | {"Horologium"})
    orig, common.ROLE_STEMS = common.ROLE_STEMS, monkey
    try:
        stems = m.body_stems()
        assert "Horologium" in stems, "an unlisted role stem is dropped entirely"
        assert stems[-1] == "Horologium", f"an unlisted stem must sort to the tail, got {stems[-1]}"
    finally:
        common.ROLE_STEMS = orig
