"""tools/publish_check.py — the gate that keeps a person out of a public repo.

Two halves, as everywhere else in this suite. The NEGATIVE control is the real plugin tree: it must
pass, or the check is useless because nobody could ever ship. The POSITIVE controls plant one hit of
each class into a temp COPY of the tree and confirm the check goes red — a gate proven only on clean
input has not been proven at all.

Every planted needle is COMPOSED from fragments (`"/" + "Users/someone"`), never written as a
literal, so this test file is not itself a hit. The check has no allowlist and scans its own
directory, so a literal here would make the negative control fail — which is the design working.
"""
from __future__ import annotations
import shutil, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "tools" / "publish_check.py"

# kind reported by the check -> the text planted into a copy of the tree
PLANTS = {
    "home path": "VAULT = " + '"' + "/" + "Users/someone" + "/Atlas" + '"',
    "email": "MAINTAINER = " + '"' + "someone" + "@" + "example.com" + '"',
    "owner name": "# thanks to " + "ju" + "stus" + " for the original vault",
    "private dir": "SRC = " + '"~/' + "Pycharm" + "Projects" + '/thing"',
    "private repo": "GREW_IN = " + '"' + "atlas" + "-system" + '"',
    "api key": "KEY = " + '"' + "sk-ant-" + "api03-" + "A" * 40 + '"',
    "credential": "config = {" + '"api_key": "' + "z" * 24 + '"}',
}


def run(root: Path):
    p = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)],
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def test_the_real_plugin_tree_passes():
    rc, out = run(PLUGIN)
    assert rc == 0, out
    assert "clean" in out


@pytest.fixture
def copy(tmp_path):
    dst = tmp_path / "gedaechtnis"
    shutil.copytree(PLUGIN, dst, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    assert run(dst)[0] == 0, "the copy must start clean, or a planted hit proves nothing"
    return dst


@pytest.mark.parametrize("kind,line", sorted(PLANTS.items()))
def test_a_planted_hit_is_refused(copy, kind, line):
    (copy / "leak.py").write_text(line + "\n", encoding="utf-8")
    rc, out = run(copy)
    assert rc == 1, out
    assert "leak.py:1:" in out and kind in out, out


def test_a_hit_in_a_nested_or_non_python_file_is_found(copy):
    (copy / "docs").mkdir()
    (copy / "docs" / "notes.md").write_text("clone it into " + "~/" + "Pycharm" + "Projects" + "/x\n",
                                            encoding="utf-8")
    rc, out = run(copy)
    assert rc == 1 and "docs/notes.md:1:" in out, out


def test_the_checker_scans_itself(copy):
    """No allowlist means no exemption for the checker's own file — plant a hit inside it."""
    tool = copy / "tools" / "publish_check.py"
    tool.write_text(tool.read_text(encoding="utf-8") + "\n# " + "ju" + "stus" + " was here\n",
                    encoding="utf-8")
    rc, out = run(copy)
    assert rc == 1 and "tools/publish_check.py" in out, out


# ------------------------------------------------------------------ the refs half (MIRRORTAGS-1) ----
# A mirror clone made from the private monorepo INHERITS its tags, and `git push --tags` then
# publishes the private commits they point at. The tree scan above cannot see that: a tag is a ref,
# not a file. Both controls below use a REAL clone, because inheriting-by-clone is the mechanism.

def git(*args, cwd=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          check=True, timeout=120)


@pytest.fixture
def clone(tmp_path):
    """A publish-clean clone of an upstream that carries one non-release tag, as the mirror did."""
    up = tmp_path / "upstream"; up.mkdir()
    git("init", "-q", "-b", "main", str(up))
    (up / "hello.txt").write_text("hello\n", encoding="utf-8")
    git("add", "--", "hello.txt", cwd=up)
    git("commit", "-q", "-m", "root", "--", "hello.txt", cwd=up)
    git("tag", "cursus-phase1-start", cwd=up)
    dst = tmp_path / "mirror"
    git("clone", "-q", str(up), str(dst))
    return dst


def test_an_inherited_tag_is_refused(clone):
    assert git("tag", "-l", cwd=clone).stdout.split() == ["cursus-phase1-start"], "the clone must inherit the tag"
    rc, out = run(clone)
    assert rc == 1, out
    assert "cursus-phase1-start" in out and "--no-tags" in out, out


def test_only_release_tags_passes(clone):
    """Negative control: the same clone, its inherited tag deleted and a real release tagged."""
    git("tag", "-d", "cursus-phase1-start", cwd=clone)
    git("tag", "v0.1", cwd=clone)
    rc, out = run(clone)
    assert rc == 0, out
    assert "clean" in out


def test_a_non_git_root_is_not_judged_on_the_tags_of_the_repo_it_sits_in(copy):
    """The plugin directory lives inside a repo whose tags it will never push: skipped, not refused."""
    assert not (copy / ".git").exists()
    rc, out = run(copy)
    assert rc == 0, out
