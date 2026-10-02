#!/usr/bin/env python3
"""allow_rules.py — merge the plugin's narrow allow and deny rules into each repository's settings.

    python3 tools/allow_rules.py [--dry-run | --apply] [--repo DIR ...] [--only-listed]

(`tools/apply-allow-rules.sh` runs this; it is the one line a person types, once.)

WHAT IT WRITES. The rules come from ONE file, `rules/allow-rules.json`. In every repository the
rule's `{path}` is the plugin's INSTALLED path — the main checkout's absolute path, its `~/`
spelling, and `~/.claude/skills/<name>/…` when that link points at it — never a relative path. A
relative `gedaechtnis/tools/x.py` would name the copy in the session's own working tree, which the
session edits as ordinary work: it could change the tool and then run it through the rule. In a
checkout of the plugin itself the rules go to `.claude/settings.local.json`, because a
`settings.json` there would be published with it.

A rule is written only when its target script exists in the plugin. (A repository script is never
named: its path is relative to the session's tree, which the session edits — review PD1FIX.) THE REPOSITORIES: the `repo:` lines of the fleet roster (`config.roster()`), the
repository the plugin lives in, a checkout of the plugin itself beside that repository, and any
`--repo DIR`. `--only-listed` uses the `--repo` list alone.

HOW IT WRITES. `permissions.allow` and `permissions.deny` gain the rules they lack, in the file's
order, never a duplicate; every other key and every existing rule is kept as it is. Before the
first change of a day it copies the file to `<name>.pre-permdesign-<date>` beside it (an existing
copy is never overwritten), then writes through a temporary file and a rename, and reads the file
back. A file that is not a JSON object is refused and left alone. `--dry-run` (the default) prints
what would change and writes nothing. A second `--apply` finds nothing missing and writes nothing:
the bytes stay the same.
"""
from __future__ import annotations
import argparse, datetime, difflib, json, os, re, shutil, sys, tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
RULES = PLUGIN / "rules" / "allow-rules.json"
sys.path.insert(0, str(PLUGIN / "hooks"))
import config                      # noqa: E402  — hooks/config.py: the roster's location
import rootguard                   # noqa: E402

TAG = "permdesign"


def load_rules(path: Path = RULES) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    for kind in ("allow", "deny"):
        for r in d.get(kind, []):
            if "{path}" not in r["rule"] or not r["rule"].startswith("Bash("):
                raise ValueError(f"{path}: rule {r['rule']!r} names no {{path}}")
    return d


def _git_path(d: Path, *args: str) -> Path | None:
    import subprocess                                               # noqa: PLC0415
    try:
        p = subprocess.run(["git", "-C", str(d), "rev-parse", *args], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 and p.stdout.strip() else None


def is_plugin_root(repo: Path) -> bool:
    return (repo / "hooks" / "mergesha.py").is_file() and (repo / "tools").is_dir()


def host_repo() -> Path | None:
    """The MAIN checkout of the repository the plugin is a folder of (a worktree's too), or None."""
    top = _git_path(PLUGIN, "--show-toplevel")
    common = _git_path(PLUGIN, "--path-format=absolute", "--git-common-dir")
    if not top or top == PLUGIN or not common:
        return None
    return common.parent


def plugin_path() -> Path:
    """This plugin as the host's main checkout carries it; the file itself when there is no host."""
    top, host = _git_path(PLUGIN, "--show-toplevel"), host_repo()
    if top and host:
        main = host / PLUGIN.relative_to(top)
        if main.is_dir():
            return main
    return PLUGIN


def roster_repos() -> list[Path]:
    try:
        txt = config.roster().read_text(encoding="utf-8")
    except OSError:
        return []
    return [config.home() / m.group(1) for m in re.finditer(r"^\s*repo:\s*(\S+)", txt, re.M)]


def repos(extra: list[str], only_listed: bool) -> list[Path]:
    found: list[Path] = [Path(e).expanduser() for e in extra]
    if not only_listed:
        host = host_repo()
        found += roster_repos() + ([host] if host else [])
        if host and (host.parent / PLUGIN.name).is_dir():
            found.append(host.parent / PLUGIN.name)            # a checkout of the plugin itself
    out: list[Path] = []
    for r in found:
        r = r.resolve()
        if r.is_dir() and r not in out:
            out.append(r)
    return out


def install_forms() -> list[str]:
    """How a session outside the plugin's repository names the plugin: absolute, `~/`, skills link."""
    home, plugin = str(Path.home()), plugin_path()
    forms = [str(plugin)]
    if forms[0].startswith(home + os.sep):
        forms.append("~" + forms[0][len(home):])
    skills = Path.home() / ".claude" / "skills"
    try:
        links = sorted(p for p in skills.iterdir() if p.is_symlink())
    except OSError:
        links = []
    for p in links:
        try:
            if p.resolve() == plugin:
                forms.append(f"~/.claude/skills/{p.name}")
        except OSError:
            continue
    return forms


def settings_file(repo: Path) -> Path:
    local = "settings.local.json" if is_plugin_root(repo) else "settings.json"
    return repo / ".claude" / local


def rules_for(repo: Path, doc: dict) -> dict:
    """{"allow": [...], "deny": [...]}: the concrete rules for one repository, in the file's order."""
    plugin = plugin_path()
    base, forms = plugin, [f + "/" for f in install_forms()]
    out: dict = {"allow": [], "deny": []}
    for kind in ("allow", "deny"):
        for r in doc.get(kind, []):
            if r.get("base") == "repo":
                raise ValueError(f"rule {r['rule']!r}: a repository script is session-editable")
            paths = [f + r["target"] for f in forms] if (base / r["target"]).is_file() else []
            for p in paths:
                rule = r["rule"].replace("{path}", p)
                if rule not in out[kind]:
                    out[kind].append(rule)
    return out


def indent_of(text: str) -> int:
    m = re.search(r"\n( +)\S", text)
    return len(m.group(1)) if m else 2


def merged(data: dict, want: dict) -> tuple[dict, dict]:
    """(new data, {"allow": added, "deny": added}); `data` itself is not changed."""
    new = json.loads(json.dumps(data))
    perms = new.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise ValueError("`permissions` is not an object")
    added: dict = {"allow": [], "deny": []}
    for kind in ("allow", "deny"):
        have = perms.get(kind, [])
        if not isinstance(have, list):
            raise ValueError(f"`permissions.{kind}` is not a list")
        miss = [r for r in want[kind] if r not in have]
        if miss:
            perms[kind] = have + miss
            added[kind] = miss
    return new, added


def render(data: dict, like: str) -> str:
    return json.dumps(data, indent=indent_of(like), ensure_ascii=False) + "\n"


def _test_floor(p: Path) -> None:
    """Under pytest a write lands in the test's temporary tree or not at all: these files are real
    repositories' settings, and a test that forgot `--only-listed` must not reach them."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not rootguard.under(p, rootguard._temp_root()):
        raise rootguard.OutsideRoot(f"a test tried to write {p}")


def backup(p: Path) -> Path | None:
    """`<name>.pre-permdesign-<date>` beside the file, once a day; an existing copy is kept."""
    if not p.exists():
        return None
    b = p.with_name(f"{p.name}.pre-{TAG}-{datetime.date.today().isoformat()}")
    _test_floor(b)
    if not b.exists():
        shutil.copy2(p, b)
    return b


def write_atomic(p: Path, text: str) -> None:
    p = p.resolve()                    # a symlinked settings file is written through, not replaced
    _test_floor(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if p.exists():
            shutil.copymode(p, tmp)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def one(repo: Path, doc: dict, apply: bool) -> tuple[int, str]:
    """(rules added, report) for one repository."""
    p = settings_file(repo)
    want = rules_for(repo, doc)
    if p.is_symlink():
        t = p.resolve()
        if not t.is_file() or repo.resolve() not in t.parents:
            return 0, (f"== {repo}\n   refused, left alone: {p} is a symlink to {t}, which is not an "
                       "existing file inside this repository")
    head = f"== {repo}  ({p.relative_to(repo)}{'' if p.exists() else ', new file'})"
    try:
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        data = json.loads(old) if old.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        new, added = merged(data, want)
    except (OSError, ValueError) as e:
        return 0, f"{head}\n   refused, left alone: {p} {e}"
    n = len(added["allow"]) + len(added["deny"])
    lines = [head, f"   rules for this repository: {len(want['allow'])} allow, {len(want['deny'])} deny;"
                   f" missing: {n}"]
    if not n:
        return 0, "\n".join(lines + ["   nothing to change"])
    text = render(new, old or "  ")
    diff = difflib.unified_diff(old.splitlines(), text.splitlines(), str(p), str(p), lineterm="", n=1)
    lines += ["   " + d for d in diff]
    if apply:
        b = backup(p)
        write_atomic(p, text)
        back = json.loads(p.read_text(encoding="utf-8"))
        ok = all(r in back["permissions"][k] for k in ("allow", "deny") for r in want[k])
        lines.append(f"   WRITTEN{' (backup ' + b.name + ')' if b else ''}; read back: "
                     f"{'every rule present' if ok else 'A RULE IS MISSING'}")
        if not ok:
            raise SystemExit(f"{p}: a rule did not come back after the write")
    return n, "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="the default: print, write nothing")
    g.add_argument("--apply", action="store_true", help="write the missing rules")
    ap.add_argument("--repo", action="append", default=[], help="another repository (repeatable)")
    ap.add_argument("--only-listed", action="store_true", help="the --repo list alone")
    ap.add_argument("--rules", default=str(RULES), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    doc = load_rules(Path(a.rules))
    todo = repos(a.repo, a.only_listed)
    if not todo:
        print("no repository found: nothing to do")
        return 1
    total = 0
    for r in todo:
        n, rep = one(r, doc, a.apply)
        total += n
        print(rep)
    verb = "added" if a.apply else "would add (dry run: nothing written; --apply writes)"
    print(f"\n{len(todo)} repositories; {total} rules {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
