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


def _source_repo(tmp_path, subject="first commit", leak=False, version=None, changelog=None):
    """A minimal repository with a plugin-shaped subtree in it."""
    src = tmp_path / "src"
    plug = src / "plugin"
    (plug / "tools").mkdir(parents=True)
    shutil.copy(CHECK, plug / "tools" / "publish_check.py")
    shutil.copy(SCRIPT, plug / "tools" / "release_orphan.sh")
    (plug / "README.md").write_text("# a package\n\nnothing private here.\n")
    if leak:
        (plug / "settings.py").write_text('LOG_DIR = "%s"\n' % HOME_PATH)
    if version is not None:
        (plug / ".claude-plugin").mkdir()
        # The fields publish_check's checklist asks of any tree that carries a plugin.json.
        (plug / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "p", "displayName": "P", "version": "%s", "description": "d", '
            '"license": "MIT"}\n' % version)
        (plug / "LICENSE").write_text("MIT License\n")
        (plug / "hooks").mkdir()
        (plug / "hooks" / "noop.py").write_text("pass\n")
        (plug / "hooks" / "hooks.json").write_text(
            '{"hooks": {"Stop": [{"hooks": [{"type": "command", '
            '"command": "python3 -B \\"${CLAUDE_PLUGIN_ROOT}/hooks/noop.py\\""}]}]}}\n')
        (plug / ".gitignore").write_text("__pycache__/\n*.pyc\n.DS_Store\n")
    if changelog is not None:
        (plug / "CHANGELOG.md").write_text(changelog)
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


# THE VERSION RULE: tag = plugin.json version, always. A tag that differs is refused before
# anything is built; the matching one is cut and printed.
_LOG = "# Changelog\n\n## 0.5.0 — 2026-09-27 — something\n\n## 0.4.0 — earlier\n"


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_tag_equal_to_the_version_is_cut_and_printed(tmp_path):
    """NEGATIVE CONTROL for the refusal below: the same fixture with the matching tag builds."""
    src = _source_repo(tmp_path, version="0.5.0", changelog=_LOG)
    out = tmp_path / "build"
    r = _build(src, out, extra=("--tag", "v0.5.0"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(out, "tag", "--list").stdout.split() == ["v0.5.0"]
    assert _git(out, "rev-parse", "v0.5.0^{commit}").stdout == _git(out, "rev-parse", "HEAD").stdout
    assert "push origin v0.5.0" in r.stdout
    assert "Das Gedaechtnis 0.5.0" in _git(out, "log", "-1", "--format=%s").stdout
    # Without --tag the tag is still the version: there is no other tag it could be.
    out2 = tmp_path / "build2"
    assert _build(src, out2).returncode == 0
    assert _git(out2, "tag", "--list").stdout.split() == ["v0.5.0"]


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
@pytest.mark.parametrize("tag", ["v0.5", "v0.6.0", "0.5.0"])
def test_a_tag_that_differs_from_the_version_is_refused_before_anything_is_built(tmp_path, tag):
    """POSITIVE CONTROL. v0.4 shipped under a plugin.json that said 0.2.0; this is that act."""
    src = _source_repo(tmp_path, version="0.5.0", changelog=_LOG)
    out = tmp_path / "build"
    r = _build(src, out, extra=("--tag", tag))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "REFUSED" in r.stderr and "differs from plugin.json's version 0.5.0" in r.stderr
    assert not out.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_tag_on_a_tree_with_no_version_is_refused(tmp_path):
    src = _source_repo(tmp_path)
    out = tmp_path / "build"
    r = _build(src, out, extra=("--tag", "v0.5.0"))
    assert r.returncode == 1 and "carries no version" in r.stderr
    assert not out.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_version_with_no_changelog_heading_is_refused(tmp_path):
    src = _source_repo(tmp_path, version="0.5.1", changelog=_LOG)
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 1 and "no '## 0.5.1 ' heading" in r.stderr
    assert not out.exists()


def test_the_shipped_plugin_json_has_a_changelog_heading():
    """The tree this file ships in obeys the rule it tests."""
    import json
    version = json.loads((PKG / ".claude-plugin" / "plugin.json").read_text())["version"]
    lines = (PKG / "CHANGELOG.md").read_text().splitlines()
    assert any(l.startswith("## %s " % version) for l in lines)


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_version_with_no_changelog_file_at_all_is_refused(tmp_path):
    src = _source_repo(tmp_path, version="0.5.1")
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 1 and "no '## 0.5.1 ' heading" in r.stderr
    assert not out.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_refused_publish_check_cuts_no_tag(tmp_path):
    src = _source_repo(tmp_path, leak=True, version="0.5.0", changelog=_LOG)
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 2, r.stdout + r.stderr
    assert _git(out, "tag", "--list").stdout.strip() == ""


# ---- GEMINI-PUBLIC: the leave-out list ------------------------------------------------------------

def _source_with_list(tmp_path, listed=("tools/dev_only.py", "rules/publish-exclude.json"), raw=None):
    """`_source_repo` plus a development-only file and a leave-out list naming it."""
    src = _source_repo(tmp_path)
    plug = src / "plugin"
    (plug / "rules").mkdir()
    (plug / "tools" / "dev_only.py").write_text("print('development only')\n")
    (plug / "rules" / "publish-exclude.json").write_text(
        raw if raw is not None else '{"exclude": [%s]}\n' % ", ".join('"%s"' % p for p in listed))
    _git(src, "add", "--", "plugin")
    _git(src, "commit", "-q", "-m", "a development-only file and its list")
    return src


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_listed_path_is_left_out_of_the_build_and_the_rest_is_there(tmp_path):
    src = _source_with_list(tmp_path)
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (out / "tools" / "dev_only.py").exists()
    assert not (out / "rules" / "publish-exclude.json").exists()
    assert (out / "tools" / "publish_check.py").is_file() and (out / "README.md").is_file()
    assert "dev_only" not in _git(out, "ls-files").stdout
    assert "left out      : 2 path(s)" in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_CONTROL_with_excluded_publishes_the_listed_path(tmp_path):
    """The same source, one flag: the file is in the build, so the list is what kept it out."""
    src = _source_with_list(tmp_path)
    out = tmp_path / "build"
    r = _build(src, out, extra=("--with-excluded",))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (out / "tools" / "dev_only.py").is_file()
    assert "left out      : nothing" in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_tree_without_a_list_builds_as_before(tmp_path):
    src = _source_repo(tmp_path)
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "left out      : 0 path(s)" in r.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
@pytest.mark.parametrize("raw", ['{"exclude": ["../outside.py"]}', '{"exclude": ["/etc/passwd"]}',
                                 '{"exclude": ["a b.py"]}', '{"exclude": "tools/dev_only.py"}',
                                 '{"skip": []}', 'not json', '{"exclude": ["tools/dev_only.py/"]}',
                                 '{"exclude": ["tools/dev_only.py/."]}', '{"exclude": ["tools//dev_only.py"]}',
                                 '{"exclude": ["tools/*.py"]}', '{"exclude": ["-x"]}', '{"exclude": [5]}'])
def test_a_list_that_cannot_be_read_refuses_the_build(tmp_path, raw):
    src = _source_with_list(tmp_path, raw=raw)
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "publish-exclude.json could not be read" in r.stderr
    assert not (out / ".git").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_listed_path_that_is_not_in_the_tree_refuses_the_build(tmp_path):
    """A name that matches nothing (a typo, a file renamed since) would leave nothing out and still
    be counted. The control is `test_a_listed_path_is_left_out…`: the same list, spelled right."""
    src = _source_with_list(tmp_path, listed=("tools/dev_onyl.py",))
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tools/dev_onyl.py is on the leave-out list" in r.stderr and "not in the tree" in r.stderr
    assert not (out / "tools").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
@pytest.mark.parametrize("subtree", ["plugin/", "plugin//"])
def test_a_subtree_typed_with_a_trailing_slash_still_reads_the_list_and_the_version(tmp_path, subtree):
    src = _source_with_list(tmp_path)
    out = tmp_path / "build"
    env = dict(os.environ, TMPDIR=str(tmp_path))
    r = subprocess.run(["bash", str(src / "plugin" / "tools" / "release_orphan.sh"), "--repo", str(src),
                        "--subtree", subtree, "--sha", "HEAD", "--out", str(out)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "left out      : 2 path(s)" in r.stdout
    assert not (out / "tools" / "dev_only.py").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_the_list_is_read_even_when_python_drops_its_asserts(tmp_path):
    src = _source_with_list(tmp_path, raw='{"exclude": ["a b.py"]}')
    out = tmp_path / "build"
    env = dict(os.environ, TMPDIR=str(tmp_path), PYTHONOPTIMIZE="1")
    r = subprocess.run(["bash", str(src / "plugin" / "tools" / "release_orphan.sh"), "--repo", str(src),
                        "--subtree", "plugin", "--sha", "HEAD", "--out", str(out)], capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "publish-exclude.json could not be read" in r.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_a_listed_directory_is_left_out_with_everything_in_it(tmp_path):
    src = _source_with_list(tmp_path, listed=("tools",))
    # tools/ holds the publish check, so the build stops at "no publish_check.py": the directory is gone.
    out = tmp_path / "build"
    r = _build(src, out)
    assert not (out / "tools").exists()
    assert "carries no tools/publish_check.py" in r.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_the_real_cut_carries_no_gemini_file_and_its_pages_do_not_name_the_tool(tmp_path):
    """The plugin as it stands in this checkout, cut by the real script from a throwaway commit."""
    if not (PKG / "rules" / "publish-exclude.json").is_file():
        pytest.skip("a published tree: it carries no leave-out list and no development-only file")
    src = tmp_path / "src"
    shutil.copytree(PKG, src / "plugin", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
    assert (src / "plugin" / "tools" / "gemini_worker.py").is_file()      # the control: it IS in the source
    _git(src, "init", "-q", ".")
    _git(src, "config", "user.email", "t@local")
    _git(src, "config", "user.name", "t")
    _git(src, "add", "--", "plugin")
    assert _git(src, "commit", "-q", "-m", "the tree").returncode == 0
    out = tmp_path / "build"
    r = _build(src, out)
    assert r.returncode == 0, r.stdout + r.stderr
    names = _git(out, "ls-files").stdout.split("\n")
    assert len(names) > 100
    assert [n for n in names if "gemini" in n.lower()] == []
    for page in [out / "README.md", out / "CHANGELOG.md", out / "PRIVACY.md", *sorted((out / "docs").glob("*.md"))]:
        text = page.read_text(encoding="utf-8")
        for word in ("gemini_worker", "gemini-worker", "`agy`", "Antigravity"):
            assert word not in text, f"{page.name} names {word}"
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "The plugin's own scripts make no network calls; Claude Code itself does." in readme
