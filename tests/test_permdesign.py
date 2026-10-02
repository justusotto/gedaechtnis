"""PERMDESIGN-1 — the three pieces that let a narrow allow rule replace a typed line.

  * `tools/reviewed.py` + `tools/run_reviewed.sh`: a script runs only when the sha256 of its bytes
    is in the reviewed ledger; an edited script is refused, and the bytes that run are the bytes
    that were hashed.
  * `tools/allow_rules.py` (+ `apply-allow-rules.sh`): the rules from one file, merged into a
    settings tree idempotently — twice gives the same bytes, other keys untouched, a backup kept.
  * `hooks/mergesha.py ff`: main read and fast-forwarded in one command; refused when main moved.

Everything runs against real files and real git repositories in tmp_path.
"""
from __future__ import annotations
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
TOOLS, HOOKS = PLUGIN / "tools", PLUGIN / "hooks"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(TOOLS))

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    st = tmp_path / "state"
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(st))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(tmp_path / "vault"))
    for v in ("CLAUDE_CODE_SESSION_ID", "GEDAECHTNIS_SEAT"):
        monkeypatch.delenv(v, raising=False)
    import config, reviewed                                             # noqa: PLC0415
    importlib.reload(config); importlib.reload(reviewed)
    return st


def env_for(st: Path) -> dict:
    e = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CODE_SESSION_ID",)}
    e.update(GEDAECHTNIS_STATE_DIR=str(st), GEDAECHTNIS_CONFIG=str(st.parent / "config.json"),
             GEDAECHTNIS_VAULT=str(st.parent / "vault"))
    return e


def run_wrapped(st: Path, script: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(TOOLS / "run_reviewed.sh"), str(script), *args],
                          capture_output=True, text=True, env=env_for(st), timeout=60, cwd=cwd)


# ------------------------------------------------------------------ the reviewed ledger ----

def make_script(tmp_path: Path, body: str = "") -> Path:
    s = tmp_path / "work" / "live.py"
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text("import sys, os\n"
                 "print('RAN', sys.argv[1:], os.path.basename(__file__), os.path.basename(sys.argv[0]))\n"
                 + body, encoding="utf-8")
    return s


def review_record(tmp_path: Path) -> Path:
    r = tmp_path / "VERDICT.md"
    r.write_text("ACCEPT\n", encoding="utf-8")
    return r


def test_a_reviewed_script_runs_with_its_arguments(tmp_path, state):
    import reviewed                                                    # noqa: PLC0415
    s = make_script(tmp_path)
    rc, msg = reviewed.add(str(s), str(review_record(tmp_path)), "opus-review")
    assert rc == 0, msg
    p = run_wrapped(state, s, "--x", "a b")
    assert p.returncode == 0, p.stderr
    assert "RAN ['--x', 'a b'] live.py live.py" in p.stdout
    row = (state / "reviewed.tsv").read_text().splitlines()
    assert row[0].split("\t") == list(reviewed.COLUMNS)
    assert row[1].split("\t")[0] == hashlib.sha256(s.read_bytes()).hexdigest()
    assert "\trun\t" in (state / "reviewed.log").read_text()


def test_an_edited_script_is_refused_with_its_sha_and_the_ledger(tmp_path, state):
    import reviewed                                                    # noqa: PLC0415
    s = make_script(tmp_path)
    assert reviewed.add(str(s), str(review_record(tmp_path)), "opus-review")[0] == 0
    s.write_text(s.read_text() + "print('one more line')\n", encoding="utf-8")
    sha = hashlib.sha256(s.read_bytes()).hexdigest()
    p = run_wrapped(state, s)
    assert p.returncode == 3
    assert "RAN" not in p.stdout
    assert sha in p.stderr and str(state / "reviewed.tsv") in p.stderr
    rc, verdict, _ = reviewed.check(str(s))
    assert rc == 3 and verdict.startswith("NOT REVIEWED")


def test_an_unreviewed_script_is_refused_and_an_empty_ledger_is_no_ledger(tmp_path, state):
    s = make_script(tmp_path)
    p = run_wrapped(state, s)
    assert p.returncode == 3 and "NOT REVIEWED" in p.stderr and "RAN" not in p.stdout


def test_the_runner_refuses_bytes_that_changed_after_the_check(tmp_path, state):
    """The window between the ledger check and the interpreter's start: the runner re-hashes what it
    reads and compiles only that, so a swap in between runs nothing."""
    import reviewed                                                    # noqa: PLC0415
    s = make_script(tmp_path)
    old_sha = hashlib.sha256(s.read_bytes()).hexdigest()
    s.write_text("print('SWAPPED')\n", encoding="utf-8")
    p = subprocess.run([sys.executable, "-c", reviewed.RUNNER, old_sha, str(s)],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 3 and "SWAPPED" not in p.stdout and "changed after it was checked" in p.stderr
    good = subprocess.run([sys.executable, "-c", reviewed.RUNNER,
                           hashlib.sha256(s.read_bytes()).hexdigest(), str(s)],
                          capture_output=True, text=True, timeout=30)
    assert good.returncode == 0 and "SWAPPED" in good.stdout


def test_add_refuses_a_missing_record_and_is_idempotent(tmp_path, state):
    import reviewed                                                    # noqa: PLC0415
    s = make_script(tmp_path)
    assert reviewed.add(str(s), str(tmp_path / "nope.md"), "x")[0] == 3
    assert reviewed.add(str(s), str(review_record(tmp_path)), "a\tb")[0] == 3
    assert not (state / "reviewed.tsv").exists()
    assert reviewed.add(str(s), str(review_record(tmp_path)), "x")[0] == 0
    rc, msg = reviewed.add(str(s), str(review_record(tmp_path)), "x")
    assert rc == 0 and "already reviewed" in msg
    assert len((state / "reviewed.tsv").read_text().splitlines()) == 2


def test_a_module_planted_in_the_working_directory_does_not_run(tmp_path, state):
    """Review MUST-FIX 1: `python -c` put '' first on sys.path, so a `hashlib.py` in the cwd ran
    before the runner's hash check."""
    import reviewed                                                    # noqa: PLC0415
    s = make_script(tmp_path)
    assert reviewed.add(str(s), str(review_record(tmp_path)), "x")[0] == 0
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "hashlib.py").write_text("print('PLANTED')\nimport sys; sys.exit(0)\n")
    p = run_wrapped(state, s, cwd=cwd)
    assert p.returncode == 0, p.stderr
    assert "PLANTED" not in p.stdout and "RAN" in p.stdout


def test_a_planted_venv_interpreter_is_never_used(tmp_path, state):
    """Review MUST-FIX 2: the interpreter came from `<repo>/.venv/bin/python`, which a session writes."""
    import reviewed                                                    # noqa: PLC0415
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV})
    fake = repo / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/sh\necho PLANTED-INTERPRETER\n")
    fake.chmod(0o755)
    s = repo / "live.py"
    s.write_text("print('RAN')\n")
    assert reviewed.add(str(s), str(review_record(tmp_path)), "x")[0] == 0
    assert reviewed.interpreter() != str(fake)
    p = run_wrapped(state, s)
    assert p.returncode == 0 and "RAN" in p.stdout and "PLANTED" not in p.stdout


def test_a_ledger_row_with_a_short_sha_is_ignored(tmp_path, state):
    import reviewed                                                    # noqa: PLC0415
    s = make_script(tmp_path)
    sha = hashlib.sha256(s.read_bytes()).hexdigest()
    state.mkdir(parents=True, exist_ok=True)
    (state / "reviewed.tsv").write_text("\t".join([sha[:63], str(s), "x", "r", "d"]) + "\n"
                                        + "\t".join([sha + "0", str(s), "x", "r", "d"]) + "\n")
    assert reviewed.rows() == []
    assert reviewed.check(str(s))[0] == 3


def test_a_non_python_script_is_refused_even_when_reviewed(tmp_path, state):
    import reviewed                                                    # noqa: PLC0415
    s = tmp_path / "live.sh"
    s.write_text("echo RAN\n", encoding="utf-8")
    assert reviewed.add(str(s), str(review_record(tmp_path)), "x")[0] == 0
    p = run_wrapped(state, s)
    assert p.returncode == 3 and "RAN" not in p.stdout and "not a Python script" in p.stderr


# ------------------------------------------------------------------ the allow rules ----

@pytest.fixture
def ar(state):
    import allow_rules                                                 # noqa: PLC0415
    importlib.reload(allow_rules)
    return allow_rules


def settings_tree(tmp_path: Path) -> dict:
    a = tmp_path / "repos" / "a"
    (a / ".claude").mkdir(parents=True)
    (a / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"defaultMode": "auto", "allow": ["Bash(git log:*)"], "deny": ["Bash(git push*)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}, indent=2) + "\n")
    b = tmp_path / "repos" / "b"                                     # no settings file at all
    b.mkdir(parents=True)
    c = tmp_path / "repos" / "c"                                     # a repo carrying rc_hosts
    (c / "scripts" / "rc_hosts").mkdir(parents=True)
    (c / "scripts" / "rc_hosts" / "rc_hosts.sh").write_text("#!/bin/bash\n")
    (c / ".claude").mkdir()
    (c / ".claude" / "settings.json").write_text('{\n    "permissions": {\n        "allow": []\n    }\n}\n')
    return {"a": a, "b": b, "c": c}


def listed(t: dict) -> list[str]:
    out = ["--only-listed"]
    for r in t.values():
        out += ["--repo", str(r)]
    return out


def snapshot(t: dict) -> dict:
    return {str(p): p.read_bytes() for r in t.values() for p in sorted((r / ".claude").glob("*"))
            if (r / ".claude").is_dir()}


def test_apply_twice_gives_the_same_bytes_and_keeps_other_keys(tmp_path, ar, capsys):
    t = settings_tree(tmp_path)
    before_a = json.loads((t["a"] / ".claude" / "settings.json").read_text())
    assert ar.main(["--apply", *listed(t)]) == 0
    first = snapshot(t)
    assert ar.main(["--apply", *listed(t)]) == 0
    assert snapshot(t) == first, "a second --apply changed bytes"
    out = capsys.readouterr().out
    assert out.count("nothing to change") == 3
    after = json.loads((t["a"] / ".claude" / "settings.json").read_text())
    assert after["hooks"] == before_a["hooks"]
    assert after["permissions"]["defaultMode"] == "auto"
    assert after["permissions"]["allow"][0] == "Bash(git log:*)"
    assert after["permissions"]["deny"][0] == "Bash(git push*)"
    for kind in ("allow", "deny"):
        assert len(after["permissions"][kind]) == len(set(after["permissions"][kind]))
    backups = list((t["a"] / ".claude").glob("settings.json.pre-permdesign-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == before_a
    assert (t["b"] / ".claude" / "settings.json").is_file()
    assert (t["c"] / ".claude" / "settings.json").read_text().startswith('{\n    "permissions"')


def test_dry_run_is_the_default_and_writes_nothing(tmp_path, ar, capsys):
    t = settings_tree(tmp_path)
    before = snapshot(t)
    assert ar.main(listed(t)) == 0
    assert snapshot(t) == before
    assert not (t["b"] / ".claude").exists()
    assert "would add" in capsys.readouterr().out


def test_a_broken_settings_file_is_refused_and_left_alone(tmp_path, ar, capsys):
    t = settings_tree(tmp_path)
    bad = t["a"] / ".claude" / "settings.json"
    bad.write_text("{ not json")
    assert ar.main(["--apply", *listed(t)]) == 0
    assert bad.read_text() == "{ not json"
    assert "refused, left alone" in capsys.readouterr().out


def test_the_rules_are_valid_json_per_repo_and_follow_the_targets(tmp_path, ar):
    t = settings_tree(tmp_path)
    doc = ar.load_rules()
    for name, repo in t.items():
        want = ar.rules_for(repo.resolve(), doc)
        assert json.loads(json.dumps(want)) == want
        for kind in ("allow", "deny"):
            for r in want[kind]:
                assert r.startswith("Bash(") and r.endswith(")") and "{path}" not in r
        assert any(" ff:*)" in r and "hooks/mergesha.py" in r for r in want["allow"])
        assert any("--owner" in r for r in want["deny"])
        for k in ("allow", "deny"):
            for r in want[k]:
                path = r[len("Bash("):].split(" ", 2)[1]
                assert path.startswith(("/", "~")), f"a rule names a session-editable relative path: {r}"
        closes = [r for r in want["allow"] if "sessions.py close" in r]
        assert closes and not any(r.endswith(":*)") for r in closes), "close:* would allow --out <file>"
        assert not any("Bash(python3 gedaechtnis/" in r or "Bash(bash gedaechtnis/" in r
                       for k in ("allow", "deny") for r in want[k]), "a rule names an editable relative path"


def test_the_plugin_checkout_itself_gets_installed_paths_in_its_local_file(tmp_path, ar):
    """Review MUST-FIX 3: a relative rule names the copy the session edits; every repository, the
    plugin's own checkout and the host included, gets the installed path."""
    own = tmp_path / "plugin-clone"
    (own / "hooks").mkdir(parents=True)
    (own / "tools").mkdir()
    (own / "hooks" / "mergesha.py").write_text("")
    want = ar.rules_for(own, ar.load_rules())
    assert f"Bash(python3 {ar.plugin_path()}/hooks/mergesha.py ff:*)" in want["allow"]
    assert "Bash(python3 hooks/mergesha.py ff:*)" not in want["allow"]
    assert ar.settings_file(own).name == "settings.local.json"


def test_the_host_repository_uses_the_installed_path(ar):
    host = ar.host_repo()
    if host is None:
        pytest.skip("the plugin is not a folder of a repository here")
    want = ar.rules_for(host, ar.load_rules())
    assert f"Bash(python3 {ar.plugin_path()}/hooks/mergesha.py ff:*)" in want["allow"]
    assert f"Bash(python3 {ar.plugin_path()}/tools/configure.py *--owner*)" in want["deny"]


def test_a_symlink_out_of_the_repository_is_refused(tmp_path, ar, capsys):
    t = settings_tree(tmp_path)
    out = tmp_path / "elsewhere" / "deep" / "new.json"
    link = t["b"] / ".claude" / "settings.json"
    link.parent.mkdir()
    link.symlink_to(out)
    assert ar.main(["--apply", "--only-listed", "--repo", str(t["b"])]) == 0
    assert not out.exists() and not out.parent.exists()
    assert "is a symlink" in capsys.readouterr().out


def test_a_symlinked_settings_file_is_written_through(tmp_path, ar):
    t = settings_tree(tmp_path)
    real = t["b"] / "real-settings.json"
    real.write_text('{"permissions": {"allow": []}}\n')
    link = t["b"] / ".claude" / "settings.json"
    link.parent.mkdir()
    link.symlink_to(real)
    assert ar.main(["--apply", "--only-listed", "--repo", str(t["b"])]) == 0
    assert link.is_symlink()
    assert "mergesha.py ff:*" in real.read_text()


def test_a_test_cannot_write_outside_its_temp_tree(ar):
    with pytest.raises(Exception):
        ar.write_atomic(Path(os.path.expanduser("~")) / "permdesign-must-not-exist.json", "{}")
    assert not (Path(os.path.expanduser("~")) / "permdesign-must-not-exist.json").exists()


# ------------------------------------------------------------------ mergesha ff ----

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    git(repo, "add", "--", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def land(tmp_path, monkeypatch, state):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    base = commit(repo, "a.txt")
    git(repo, "checkout", "-q", "-b", "sess-X")
    tip = commit(repo, "b.txt")
    git(repo, "checkout", "-q", "main")
    for k, v in GIT_ENV.items():
        monkeypatch.setenv(k, v)
    import common, mergesha, mergewindow                               # noqa: PLC0415
    importlib.reload(common); importlib.reload(mergewindow); importlib.reload(mergesha)
    return {"repo": repo, "base": base, "tip": tip, "ms": mergesha, "mw": mergewindow}


def test_ff_lands_the_tip_and_verifies(land):
    rc, msg = land["ms"].ff("sess-X", land["repo"])
    assert rc == 0, msg
    assert git(land["repo"], "rev-parse", "main") == land["tip"]
    assert "on main" in msg and land["base"][:10] in msg


def test_ff_refuses_when_main_moved_since_the_branch_was_stacked(land):
    moved = commit(land["repo"], "c.txt")
    rc, msg = land["ms"].ff("sess-X", land["repo"])
    assert rc == 3 and "restack" in msg
    assert git(land["repo"], "rev-parse", "main") == moved


def test_ff_refuses_a_stale_expected_main(land):
    rc, msg = land["ms"].ff("sess-X", land["repo"], expect=land["tip"][:8])
    assert rc == 3 and "main moved" in msg
    assert git(land["repo"], "rev-parse", "main") == land["base"]
    rc, msg = land["ms"].ff("sess-X", land["repo"], expect=land["base"][:8])
    assert rc == 0, msg


def test_ff_refuses_outside_the_main_checkout_and_an_option_for_a_branch(land):
    git(land["repo"], "checkout", "-q", "sess-X")
    assert land["ms"].ff("sess-X", land["repo"])[0] == 3
    git(land["repo"], "checkout", "-q", "main")
    assert land["ms"].ff("--help", land["repo"])[0] == 2
    assert git(land["repo"], "rev-parse", "main") == land["base"]


def test_ff_honours_the_merge_window(land):
    (land["repo"] / ".merge-window").write_text("")
    land["mw"].grant("other-session", land["repo"])
    rc, msg = land["ms"].ff("sess-X", land["repo"], sid="me")
    assert rc == 3 and "other-session" in msg
    assert git(land["repo"], "rev-parse", "main") == land["base"]
    land["mw"].release("other-session", land["repo"])
    rc, msg = land["ms"].ff("sess-X", land["repo"], sid="me")
    assert rc == 0, msg
    assert land["mw"].read(land["repo"]) is None, "the window ff took was not given back"


def test_ff_cli_prints_and_exits(land, monkeypatch):
    monkeypatch.chdir(land["repo"])
    assert land["ms"].main(["ff"]) == 2
    assert land["ms"].main(["ff", "sess-X", "--expect-main", land["base"]]) == 0
    assert git(land["repo"], "rev-parse", "main") == land["tip"]


def test_ff_refuses_when_the_suite_gate_is_on_and_no_green_run_covers_the_tip(land, tmp_path, monkeypatch):
    """Review MUST-FIX 5: the gate branch of ff had no test (a mutant disabling it survived)."""
    (tmp_path / "config.json").write_text('{"suite_gate": true}')
    (land["repo"] / "gedaechtnis" / "tests").mkdir(parents=True)
    import config, suitegate                                           # noqa: PLC0415
    importlib.reload(config); importlib.reload(suitegate)
    assert suitegate.enabled()
    monkeypatch.setenv("SUITE_GATE_ALLOW", "1")                        # the env is no escape
    rc, msg = land["ms"].ff("sess-X", land["repo"])
    assert rc == 3 and "full-suite run" in msg
    assert git(land["repo"], "rev-parse", "main") == land["base"]
