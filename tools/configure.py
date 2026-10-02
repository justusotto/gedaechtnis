#!/usr/bin/env python3
"""configure.py — read and write this install's `limits` without a hand-typed JSON line (HANDLINES-1).

    python3 tools/configure.py show
    python3 tools/configure.py get KEY
    python3 tools/configure.py set KEY VALUE [--owner]
    python3 tools/configure.py unset KEY [--owner]

`config.json`'s `limits` object is the per-install layer over `rules/limits.json` (see
`hooks/limits.py`). Before this tool a changed threshold reached the owner as a `python3 -c "…"`
line to paste; the tool replaces that line, so a session changes a key itself.

THE POLICY it enforces, read from the shipped file's `_key_class` table:
  * `live` — the key ships ON, or at its live value, with an off-switch. Any session may set it.
  * `owner:<money|irreversible|learner-data|routing|ruled>` — the owner's switch. `set` and `unset`
    refuse it unless `--owner` is passed, and refuse `--owner` inside a Claude session (its
    environment carries CLAUDECODE / CLAUDE_CODE_SESSION_ID): the flag is for the owner's own
    terminal.

What every write does: validates the key against the shipped schema (an unknown key is refused)
and the value against the shipped TYPE (the same rule `limits.py` applies when it reads), keeps a
timestamped backup `config.json.pre-<key>-<date>` (once per key per day: never clobbered), writes
atomically (temporary file in the same folder, then rename), re-reads the file and checks the key
came back with the value, and appends one line to `config.log` in the plugin's state folder:
when, key, old, new, by whom.

VALUE is JSON when it parses as JSON (`20000`, `true`, `["a","b"]`, `0.5`), otherwise a string for
a string key. The file's other keys, `_`-prefixed notes included, are kept as they are.
"""
from __future__ import annotations
import argparse, datetime, json, os, shutil, sys, tempfile, time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
import config as paths             # noqa: E402  — hooks/config.py: where config.json and state live
import limits                      # noqa: E402

SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")


def shipped() -> dict:
    """The shipped file, `_` keys included (the class table is one of them)."""
    return json.loads(limits.LIMITS_PATH.read_text(encoding="utf-8"))


def schema() -> dict:
    """Every settable key and its shipped value: the fallback table with the shipped file over it."""
    return {k: v for k, v in limits._file_layer().items() if not k.startswith("_")}


def key_class(key: str, doc: dict | None = None) -> str:
    """`live` or `owner:<why>`. A key missing from the table is the owner's: unknown is never live."""
    table = (doc if doc is not None else shipped()).get("_key_class") or {}
    return str(table.get(key) or "owner:unclassified")


def parse_value(key: str, raw: str, base: dict):
    """The typed value, or raise ValueError saying why. Same type rule as `limits._vault_overrides`."""
    want = type(base[key])
    try:
        v = json.loads(raw)
    except ValueError:
        v = raw
    if want is str and not isinstance(v, str):
        v = raw
    if isinstance(v, bool) and want is not bool:
        raise ValueError(f"`{key}` is a {want.__name__}; `{raw}` is a bool")
    ok = want if want is not float else (int, float)
    if not isinstance(v, ok):
        raise ValueError(f"`{key}` is a {want.__name__}; `{raw}` reads as a {type(v).__name__}")
    if want is float and key.endswith("_share") and not (0 <= v <= 1):
        raise ValueError(f"`{key}` is a share between 0 and 1, not `{raw}`")   # limits.py's rule
    if want is dict:
        for sk, sv in v.items():
            if sk not in base[key]:
                raise ValueError(f"`{key}.{sk}` is not a name this package reads "
                                 f"(the names are: {', '.join(sorted(base[key]))})")
            st = type(base[key][sk])
            if isinstance(sv, bool) or not isinstance(sv, st if st is not float else (int, float)):
                raise ValueError(f"`{key}.{sk}` is a {st.__name__}")
    return v


def in_session(env=None) -> bool:
    env = os.environ if env is None else env
    return any(env.get(v) for v in SESSION_VARS)


def who(env=None) -> str:
    env = os.environ if env is None else env
    return env.get("GEDAECHTNIS_SEAT") or env.get("CLAUDE_CODE_SESSION_ID") or (
        "session" if in_session(env) else "terminal")


def read_config(p: Path) -> dict:
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))       # a broken file is refused, not replaced
    if not isinstance(data, dict):
        raise ValueError(f"{p} is not a JSON object")
    return data


def backup(p: Path, key: str) -> Path | None:
    """`config.json.pre-<key>-<date>`, once per key per day; an existing one is never overwritten."""
    if not p.exists():
        return None
    b = p.with_name(f"{p.name}.pre-{key}-{datetime.date.today().isoformat()}")
    if not b.exists():
        shutil.copy2(p, b)
    return b


def write_atomic(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        if p.exists():
            shutil.copymode(p, tmp)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def log(line: str) -> None:
    try:
        paths.state().mkdir(parents=True, exist_ok=True)
        with (paths.state() / "config.log").open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\t" + line + "\n")
    except OSError:
        pass


def _gate(key: str, owner: bool, doc: dict) -> str | None:
    """Why this write is refused, or None."""
    if key.startswith("_"):
        return f"`{key}` is a documentation key, not a setting"
    if key not in schema():
        return f"`{key}` is not a key this package reads (see `configure.py show`)"
    cls = key_class(key, doc)
    if cls != "live" and not owner:
        return (f"`{key}` is the owner's switch ({cls}); it is set from the owner's own terminal "
                f"with --owner")
    if owner and in_session():
        return ("--owner is refused inside a Claude session: it is for the owner's own terminal")
    return None


def _below(key: str) -> str:
    """Where a key's value comes from when config.json's `limits` does not set it (PUBLICDOORS-1)."""
    name = limits.profile()
    return f"profile:{name}" if key in limits.PROFILES.get(name, {}) else "shipped"


def cmd_show(_a) -> int:
    doc = shipped()
    over = read_config(paths.config_path()).get("limits") or {}
    eff = limits.all_limits()
    for k in sorted(schema()):
        src = "config" if k in over else _below(k)
        print(f"{k} = {json.dumps(eff.get(k))}  [{src}; {key_class(k, doc)}]")
    for p in limits.problems():
        print(f"NOT APPLIED: {p}")
    return 0


def cmd_get(a) -> int:
    if a.key not in schema():
        print(f"refused: `{a.key}` is not a key this package reads")
        return 2
    print(json.dumps(limits.get(a.key)))
    return 0


def _write(a, unset: bool) -> int:
    doc = shipped()
    why = _gate(a.key, a.owner, doc)
    if why:
        print(f"refused: {why}")
        log(f"refused\t{a.key}\t{why}\tby {who()}")
        return 3
    base = schema()
    value = None
    if not unset:
        try:
            value = parse_value(a.key, a.value, base)
        except ValueError as e:
            print(f"refused: {e}")
            return 2
    p = paths.config_path()
    try:
        data = read_config(p)
    except (OSError, ValueError) as e:
        print(f"refused: {p} could not be read as a JSON object ({e}); nothing written")
        return 4
    lim = data.get("limits")
    if lim is not None and not isinstance(lim, dict):
        print(f"refused: the `limits` in {p} is not an object; nothing written")
        return 4
    lim = dict(lim or {})
    old = lim.get(a.key, "(shipped)")
    if unset:
        if a.key not in lim:
            print(f"{a.key}: not set in {p}; the {_below(a.key)} value applies")
            return 0
        lim.pop(a.key)
    else:
        if a.key in lim and lim[a.key] == value:
            print(f"{a.key} = {json.dumps(value)} already; nothing written")
            return 0
        lim[a.key] = value
    data["limits"] = lim
    b = backup(p, a.key)
    write_atomic(p, data)
    back = read_config(p).get("limits") or {}
    if (unset and a.key in back) or (not unset and back.get(a.key) != value):
        print(f"FAILED: {p} did not read back with the change")
        return 5
    new = "(shipped)" if unset else value
    log(f"{'unset' if unset else 'set'}\t{a.key}\t{json.dumps(old)} -> {json.dumps(new)}\t"
        f"by {who()}{' (owner)' if a.owner else ''}\tbackup {b or 'none: no file before'}")
    print(f"{a.key}: {json.dumps(old)} -> {json.dumps(new)} in {p}"
          + (f" (backup {b.name})" if b else ""))
    return 0


def main(argv=None) -> int:
    # allow_abbrev=False: `--own` must not stand for `--owner`, or a deny rule on `*--owner*` misses it.
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", allow_abbrev=False)
    g = sub.add_parser("get", allow_abbrev=False)
    g.add_argument("key")
    s = sub.add_parser("set", allow_abbrev=False)
    s.add_argument("key")
    s.add_argument("value")
    s.add_argument("--owner", action="store_true")
    u = sub.add_parser("unset", allow_abbrev=False)
    u.add_argument("key")
    u.add_argument("--owner", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "show":
        return cmd_show(a)
    if a.cmd == "get":
        return cmd_get(a)
    return _write(a, unset=(a.cmd == "unset"))


if __name__ == "__main__":
    sys.exit(main())
