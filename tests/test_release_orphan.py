"""`tools/release_orphan.sh` — the release build, exercised by RUNNING it.

The script's whole claim is negative: the published branch carries the tree and NOT the history it
was cut from, and it refuses rather than publishing a tree that carries a person. Both halves need
a control. The positive control plants a home path in the tree and the build must refuse; the
history control puts one in the SOURCE's commit subject and the published branch must come out
clean — with a third assertion that the check which calls it clean can see commit messages at all,
because a clean verdict from a blind instrument is the failure this whole file exists to catch.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
SCRIPT = PKG / "tools" / "release_orphan.sh"
CHECK = PKG / "tools" / "publish_check.py"
HOME_PATH = "/Users/" + "someone" + "/notes"   # split so this file is not itself a finding


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _source_repo(tmp_path, subject="first commit", leak=False):
    """A minimal repository with a plugin-shaped subtree in it."""
    src = tmp_path / "src"
    plug = src / "plugin"
    (plug / "tools").mkdir(parents=True)
    shutil.copy(CHECK, plug / "tools" / "publish_check.py")
    shutil.copy(SCRIPT, plug / "tools" / "release_orphan.sh")
    (plug / "README.md").write_text("# a package\n\nnothing private here.\n")
    if leak:
        (plug / "settings.py").write_text('LOG_DIR = "%s"\n' % HOME_PATH)
    _git(src, "init", "-q", ".")
    _git(src, "config", "user.email", "t@local")
    _git(src, "config", "user.name", "t")
    _git(src, "add", "--", "plugin")
    _git(src, "commit", "-q", "-m", subject)
    return src


def _build(src, out, extra=()):
    env = dict(os.environ)
    env["TMPDIR"] = str(out.parent)
    return subprocess.run(
        ["bash", str(src / "plugin" / "tools" / "release_orphan.sh"),
         "--repo", str(src), "--subtree", "plugin", "--sha", "HEAD", "--out", str(out), *extra],
        capture_output=True, text=True, env=env)


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_clean_tree_builds_one_commit_with_no_ancestry(tmp_path):
    src = _source_repo(tmp_path)
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "publish check: clean" in r.stdout
    assert _git(out, "rev-list", "--all", "--count").stdout.strip() == "1"
    assert _git(out, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip().startswith("public-")
    # NOTHING is pushed, and no remote is even configured: a release is a person's act.
    assert _git(out, "remote").stdout.strip() == ""
    assert "NOTHING HAS BEEN PUSHED" in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_private_name_in_the_tree_refuses_the_build(tmp_path):
    """POSITIVE CONTROL. Same script, same source, one planted line."""
    src = _source_repo(tmp_path, leak=True)
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "REFUSED" in r.stdout + r.stderr
    assert "settings.py" in r.stdout
    # The commit still exists in the throwaway build, which is deliberate: it is left to be read.
    # What must not exist is a remote to push it to.
    assert _git(out, "remote").stdout.strip() == ""


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_home_path_in_the_sources_history_does_not_travel(tmp_path):
    subject = "Merge branch 'x' of " + HOME_PATH
    src = _source_repo(tmp_path, subject=subject)
    # The instrument is not blind: run over the SOURCE, the same check reports that subject.
    on_source = subprocess.run([sys.executable, str(CHECK), "--root", str(src)],
                               capture_output=True, text=True)
    assert on_source.returncode == 1
    assert "commit message" in on_source.stdout
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "publish check: clean" in r.stdout
    published = _git(out, "log", "--all", "--format=%s%n%b").stdout
    assert "/Users/" not in published
    assert "Merge branch" not in published


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_the_release_message_names_no_path(tmp_path):
    src = _source_repo(tmp_path)
    out = tmp_path / "build"
    assert _build(src, out).returncode == 0
    body = _git(out, "log", "-1", "--format=%B").stdout
    assert "/Users/" not in body and str(src) not in body and "plugin/" not in body


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_it_refuses_to_build_over_a_directory_that_has_something_in_it(tmp_path):
    src = _source_repo(tmp_path)
    out = tmp_path / "build"
    out.mkdir()
    (out / "someone-elses-work.txt").write_text("hello\n")
    r = _build(src, out)
    assert r.returncode == 1
    assert "not empty" in r.stderr
    assert (out / "someone-elses-work.txt").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_missing_sha_is_refused_rather_than_defaulted(tmp_path):
    src = _source_repo(tmp_path)
    env = dict(os.environ)
    r = subprocess.run(["bash", str(src / "plugin" / "tools" / "release_orphan.sh"),
                        "--repo", str(src), "--subtree", "plugin"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert "--sha is required" in r.stderr
