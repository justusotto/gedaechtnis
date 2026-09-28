"""names.py — the display-name layer over the vault's role-file stems. stdlib only.

DESIGN §6.1: the disk keeps the stems (`Canon.md` never becomes `Decisions.md`); every surface a
person or model READS shows a display name instead. The mapping lives in one file, `names.json`
(alongside `init.py`, one directory above this one — stdlib 3.9 has no `tomllib`, so it is JSON,
not TOML), keyed by stem: `{"en": ..., "de": ..., "gloss": ...}`. `config.language()` picks the
column — `en` (default), `de` (built in, off by default), or `latin` (the stem itself).

Loading is lazy (nothing is read until the first call) and defensive: a missing or corrupted
names.json is not an error anywhere in this module — every function falls back to the bare stem,
because a display-name bug must never break a hook (the guarded-body rule every hook in this
plugin follows).

    display("Canon")            -> "Decisions"
    display("Canon", "de")      -> "Entscheidungen"
    display("Canon", "latin")   -> "Canon"
    display("Notes")            -> "Notes"              (unknown stem: echoed, never invented)
    gloss("Canon")               -> "settled, with reasons"
    label("Canon")               -> "Decisions (Canon.md)"
    all_display_names()          -> {"Index", "Purpose", ..., "Entscheidungen", "Fehler", ...}
    stems()                       -> frozenset of every stem the table names
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

NAMES_PATH = Path(__file__).resolve().parent.parent / "names.json"

_cache: dict | None = None


def _table() -> dict:
    """The parsed names.json, cached after the first successful read. A missing or malformed
    file yields {} — never raises — so every lookup below falls through to the stem itself."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        data = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
        _cache = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        _cache = {}
    return _cache


def display(stem: str, lang: str | None = None) -> str:
    """The name a person or model should call `stem`, in `lang` (default: config.language()).

    `latin` returns the stem itself, whatever the table says. A stem the table does not know —
    a stranger's own file, or a role added after this table was written — is echoed unchanged:
    this module never invents a display name."""
    lang = (lang or config.language()).strip().lower()
    if lang == "latin":
        return stem
    row = _table().get(stem)
    if not isinstance(row, dict):
        return stem
    name = row.get(lang) or row.get("en")
    return str(name) if name else stem


def gloss(stem: str) -> str:
    """The one-line description of what `stem` holds, or "" for a stem the table does not know."""
    row = _table().get(stem)
    return str(row.get("gloss", "")) if isinstance(row, dict) else ""


def label(stem: str, lang: str | None = None) -> str:
    """"Decisions (Canon.md)" — the display name first, the stem once in parentheses, so a
    reader and the model map to the same file whatever the display name is."""
    return f"{display(stem, lang)} ({stem}.md)"


def all_display_names() -> set:
    """Every display name across EVERY language column, regardless of the configured language.

    The filename door (part B) denies creating a vault file whose stem is any display name in
    ANY language — `Fehler.md` is denied under `en` exactly as `Mistakes.md` is (DESIGN §6.3,
    [R4]) — so it needs the whole table, not just the active column."""
    out = set()
    for row in _table().values():
        if isinstance(row, dict):
            for key in ("en", "de"):
                v = row.get(key)
                if v:
                    out.add(str(v))
    return out


def stems() -> frozenset:
    """Every stem names.json carries, as a frozenset."""
    t = _table()
    return frozenset(k for k, v in t.items() if isinstance(v, dict))


def ordered() -> list:
    """Every stem names.json carries, in the FILE's own row order (JSON objects preserve
    insertion order in Python) — the order a surface listing several role files (the facts
    block, a README table) should use, rather than a frozenset's arbitrary one."""
    t = _table()
    return [k for k, v in t.items() if isinstance(v, dict)]
