"""hooks/names.py — the display-name layer (DESIGN §6). Every case runs `names.py` in a
subprocess so a monkeypatched `NAMES_PATH` (for the corruption test) can never leak into another
test, and so importing `config` never touches a real vault (HOME is redirected to tmp_path exactly
as every other hook test does).
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
PLUGIN = Path(__file__).resolve().parents[1]
RULES = PLUGIN / "rules" / "operating-rules.md"


def env_for(home: Path, **extra) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GEDAECHTNIS_")}
    e["HOME"] = str(home)
    e["GEDAECHTNIS_CONFIG"] = str(home / ".claude" / "gedaechtnis" / "config.json")
    e["GEDAECHTNIS_STATE_DIR"] = str(home / "state")
    e.update(extra)
    return e


def probe(code: str, home: Path, **env) -> str:
    setup = (f"import sys; sys.path.insert(0, {str(HOOKS)!r}); from pathlib import Path;"
             " import names, config\n")
    p = subprocess.run([sys.executable, "-c", setup + code], capture_output=True, text=True,
                       env=env_for(home, **env), stdin=subprocess.DEVNULL, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


# ---------------------------------------------------------------------- display ----
def test_display_default_english(tmp_path):
    assert probe("print(names.display('Errata'))", tmp_path) == "Mistakes"


def test_display_german_explicit(tmp_path):
    assert probe("print(names.display('Errata', 'de'))", tmp_path) == "Fehler"


def test_display_latin_returns_the_stem(tmp_path):
    assert probe("print(names.display('Errata', 'latin'))", tmp_path) == "Errata"


def test_display_language_from_config_env(tmp_path):
    assert probe("print(names.display('Canon'))", tmp_path, GEDAECHTNIS_LANGUAGE="de") == "Entscheidungen"


def test_display_unknown_stem_echoes(tmp_path):
    assert probe("print(names.display('Notes'))", tmp_path) == "Notes"
    assert probe("print(names.display('Notes', 'de'))", tmp_path) == "Notes"


def test_gloss_known_and_unknown(tmp_path):
    out = probe("print(names.gloss('Canon')); print(repr(names.gloss('Notes')))", tmp_path)
    lines = out.splitlines()
    assert lines[0] == "settled, with reasons"
    assert lines[1] == "''"


# ------------------------------------------------------------------------ label ----
def test_label_display_first_stem_once(tmp_path):
    assert probe("print(names.label('Canon'))", tmp_path) == "Decisions (Canon.md)"


def test_label_respects_explicit_language(tmp_path):
    assert probe("print(names.label('Canon', 'de'))", tmp_path) == "Entscheidungen (Canon.md)"


# ------------------------------------------------------------ all_display_names ----
def test_all_display_names_spans_every_language_column(tmp_path):
    out = probe(
        "names_ = sorted(names.all_display_names());"
        "print('Mistakes' in names_, 'Fehler' in names_, 'Entscheidungen' in names_)", tmp_path)
    assert out == "True True True"


def test_all_display_names_is_language_independent(tmp_path):
    """Set to `de`, the door still needs to catch an EN filename too — all_display_names() must
    not shrink to the active column."""
    out = probe("print('Mistakes' in names.all_display_names())", tmp_path, GEDAECHTNIS_LANGUAGE="de")
    assert out == "True"


# --------------------------------------------------------------------------- stems ----
def test_stems_contains_the_eighteen_rows(tmp_path):
    out = probe("print(sorted(names.stems()))", tmp_path)
    got = eval(out)
    for s in ("Map", "Vision", "Position", "Course", "Canon", "Patterns", "Aporia", "Eidos",
              "Errata", "Apparatus", "Annales", "Nomos", "Lexicon", "Ethos", "Praxis",
              "Exempla", "Kernel", "Inbox"):
        assert s in got, s
    assert len(got) == 18


# ---------------------------------------------------------------- corrupted table ----
def test_corrupted_names_json_falls_back_to_stems_without_raising(tmp_path):
    """A missing/malformed names.json must never break a hook (module docstring). Monkeypatch
    NAMES_PATH to a broken file inside the SAME probe process — never the real names.json."""
    bad = tmp_path / "bad-names.json"
    bad.write_text("{ not json at all", encoding="utf-8")
    code = (
        f"names.NAMES_PATH = Path({str(bad)!r})\n"
        "print(names.display('Canon'))\n"
        "print(names.display('Canon', 'de'))\n"
        "print(names.gloss('Canon'))\n"
        "print(sorted(names.stems()))\n"
        "print(sorted(names.all_display_names()))\n"
    )
    out = probe(code, tmp_path)
    lines = out.splitlines()
    assert lines[0] == "Canon"                 # echoed stem, not invented
    assert lines[1] == "Canon"
    assert lines[2] == ""                      # gloss(): empty, not a crash
    assert lines[3] == "[]"
    assert lines[4] == "[]"


def test_missing_names_json_falls_back_to_stems_without_raising(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    code = f"names.NAMES_PATH = Path({str(missing)!r})\nprint(names.display('Position'))\n"
    assert probe(code, tmp_path) == "Position"


# ---------------------------------------------------------- rules file size + naming ----
def test_rules_file_under_4500_bytes():
    assert RULES.stat().st_size < 4500, RULES.stat().st_size


def test_rules_file_names_every_core_file_as_display_and_stem():
    text = RULES.read_text(encoding="utf-8")
    for stem, disp in (("Canon", "Decisions"), ("Errata", "Mistakes"), ("Patterns", "Patterns"),
                       ("Aporia", "Open questions"), ("Position", "Status"), ("Map", "Index")):
        assert disp in text, disp
        assert f"{stem}.md" in text or f"`{stem}`" in text or f"**{stem}**" in text, stem


# ------------------------------------------------------- README table matches names.json ----
def test_readme_table_matches_names_json():
    """A duplicated fact does not stay duplicated (Global/Patterns); pin the README's hand-written
    table against names.json so a future row change is caught here, not discovered stale."""
    import json as _json
    table = _json.loads((PLUGIN / "names.json").read_text(encoding="utf-8"))
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    for stem, row in table.items():
        assert f"`{stem}.md` | {row['en']} | {row['gloss']} |" in text, stem
