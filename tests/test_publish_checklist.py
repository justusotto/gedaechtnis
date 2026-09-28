"""PLUGDIR-2 — the directory-submission checklist half of `tools/publish_check.py`.

The shipped plugin must PASS every line (the positive case, read off the real tree). Each line
also has a planted failure in a copy of a minimal plugin, which must FAIL exactly that line — a
checklist line nobody has watched fail is not evidence.
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "tools"))
import publish_check  # noqa: E402

CMD = 'python3 -B "${CLAUDE_PLUGIN_ROOT}/hooks/gate.py" bash'


@pytest.fixture
def mini(tmp_path):
    """A minimal plugin that passes every line: a plain directory, so the 'walk' set is used."""
    r = tmp_path / "plug"
    (r / ".claude-plugin").mkdir(parents=True)
    (r / "hooks").mkdir()
    (r / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": "p", "displayName": "P", "version": "0.2.0", "description": "d", "license": "MIT"}))
    (r / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": CMD}]}]}}))
    (r / "LICENSE").write_text("MIT License\n\nCopyright\n")
    (r / ".gitignore").write_text("__pycache__/\n*.pyc\n.DS_Store\n")
    return r


def failed(root: Path) -> list[str]:
    return [line for line, ok, _ in publish_check.checklist(root) if not ok]


def test_the_shipped_plugin_passes_every_line():
    cl = publish_check.checklist(PLUGIN)
    assert cl is not None and len(cl) >= 11
    assert [line for line, ok, why in cl if not ok] == []


def test_the_minimal_plugin_passes_every_line(mini):
    assert failed(mini) == []


def test_a_non_plugin_root_is_skipped_and_says_so(tmp_path):
    (tmp_path / "a.txt").write_text("a\n")
    assert publish_check.checklist(tmp_path) is None
    p = subprocess.run([sys.executable, str(PLUGIN / "tools" / "publish_check.py"), "--root", str(tmp_path)],
                       capture_output=True, text=True, timeout=60)
    assert "checklist: skipped" in p.stdout


@pytest.mark.parametrize("plant,line", [
    (lambda r: (r / "__pycache__").mkdir() or (r / "__pycache__" / "x.cpython-39.pyc").write_bytes(b"\0"),
     "no __pycache__/ or .pyc"),
    (lambda r: (r / ".DS_Store").write_bytes(b"\0"), "no .DS_Store"),
    (lambda r: os.symlink("/etc/hosts", r / "link"), "no symlinks"),
    (lambda r: (r / "big.txt").write_bytes(b"x" * (256 * 1024 + 1)), "no file over 256 KiB"),
    (lambda r: [(r / f"f{i}.txt").write_text("x") for i in range(513)], "at most 512 files"),
])
def test_each_file_line_fails_on_its_own_plant(mini, plant, line):
    plant(mini)
    assert failed(mini) == [line]


def _manifest(r, **change):
    p = r / ".claude-plugin" / "plugin.json"
    d = json.loads(p.read_text())
    for k, v in change.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    p.write_text(json.dumps(d))


def test_a_hooks_key_fails_its_line(mini):
    _manifest(mini, hooks="./hooks/hooks.json")
    assert failed(mini) == ["plugin.json has no `hooks` key (hooks/hooks.json is found by convention)"]


def test_a_missing_display_name_fails_its_line(mini):
    _manifest(mini, displayName=None)
    assert failed(mini) == ["plugin.json names name, displayName, version, description, license"]


def test_a_license_that_disagrees_with_the_file_fails(mini):
    _manifest(mini, license="Apache-2.0")
    assert failed(mini) == ["`license` matches LICENSE"]


def _hook(r, cmd):
    (r / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": cmd}]}]}}))


def test_the_split_quote_form_fails_the_command_line(mini):
    _hook(mini, 'python3 -B "${CLAUDE_PLUGIN_ROOT}"/hooks/gate.py bash')
    assert failed(mini) == ['every hook command is `python3 -B "${CLAUDE_PLUGIN_ROOT}/<path>.py" [args]`']


def test_an_inline_python_c_fails_two_lines(mini):
    _hook(mini, 'python3 -c "print(1)"')
    assert set(failed(mini)) == {'every hook command is `python3 -B "${CLAUDE_PLUGIN_ROOT}/<path>.py" [args]`',
                                 "no inline `python3 -c` in hooks.json"}


def test_a_gitignore_missing_ds_store_fails(mini):
    (mini / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    assert failed(mini) == [".gitignore excludes __pycache__, *.pyc, .DS_Store"]


def test_inside_git_only_the_published_set_counts(mini):
    """In a git work tree an ignored `__pycache__` is not published, so it does not fail the line;
    a force-added one is, and does."""
    subprocess.run(["git", "init", "-q", str(mini)], check=True)
    (mini / "__pycache__").mkdir()
    (mini / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert failed(mini) == []
    subprocess.run(["git", "-C", str(mini), "add", "-f", "--", "__pycache__/x.pyc"], check=True)
    assert failed(mini) == ["no __pycache__/ or .pyc"]


def test_a_failed_line_makes_the_whole_check_exit_nonzero(mini):
    (mini / ".DS_Store").write_bytes(b"\0")
    p = subprocess.run([sys.executable, str(PLUGIN / "tools" / "publish_check.py"), "--root", str(mini)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 1 and "checklist: FAIL  no .DS_Store" in p.stdout, p.stdout
