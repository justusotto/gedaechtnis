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
HOME_NEEDLE = "/" + "Users/someone"          # never spelled literally: this file is scanned too

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
    (copy / "docs").mkdir(exist_ok=True)  # the published tree has its own docs/ since 0.6.0
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


# ---------------------------------------- the four smuggles the security review got past it ----
def test_a_SYMLINK_whose_target_string_is_a_private_path_is_found(copy):
    """★ WHAT GIT PUBLISHES FOR A SYMLINK IS THE TARGET STRING.

    The walker skipped symlinks as "not a file". The skip read as a safety measure — do not follow
    a link out of the tree — and acted as a blind spot: the safe thing is not to READ THROUGH the
    link, which is a different act from not looking at it. a link to an absolute path under the owner's home
    published that absolute path verbatim under a `clean` verdict."""
    (copy / "notes.md").symlink_to(HOME_NEEDLE + "/Atlas/Global/Kernel.md")
    rc, out = run(copy)
    assert rc == 1, out
    assert "notes.md" in out and "home path" in out, out


def test_the_symlink_is_never_READ_THROUGH(copy):
    """The negative control that keeps the fix from becoming the danger it replaced.

    A link is judged on its own TEXT. Following it would make a publish check read arbitrary files
    anywhere on the machine — and would report a leak that this tree does not actually publish,
    since git stores the link, not the target's bytes.

    So: a link whose TEXT is clean and whose TARGET's CONTENT is a needle must come back CLEAN.
    The target is placed in a freshly made temp directory rather than under `tmp_path`, because
    pytest's own directory names embed the account name and the checker would then hit the link
    text for a reason that has nothing to do with this test."""
    import tempfile
    outside = Path(tempfile.mkdtemp())
    secret = outside / "t.txt"
    secret.write_text(HOME_NEEDLE + "/private\n", encoding="utf-8")
    assert not any(n in str(secret) for n in ("Users", "ju" + "stus")), str(secret)
    (copy / "link.md").symlink_to(secret)
    rc, out = run(copy)
    # Since PLUGDIR-2 a symlink in a plugin root is itself a checklist FAIL (the directory refuses
    # symlinks), so rc is 1 — but for THAT line only: no content hit may come from the target.
    fails = [l for l in out.splitlines() if l.startswith("checklist: FAIL")]
    assert [l.split(" — ")[0] for l in fails] == ["checklist: FAIL  no symlinks"], out
    assert "home path" not in out and "hit(s) under" not in out, f"the target's content was read through the link:\n{out}"


def test_a_VENDORED_directory_is_not_a_place_this_tool_promises_nothing_about(copy):
    """`node_modules` was skipped, while the docstring promised nothing is skipped but `.git/` and
    `__pycache__/`. A vendored tree is exactly where a stray absolute path hides."""
    d = copy / "node_modules" / "pkg"
    d.mkdir(parents=True)
    (d / "leak.txt").write_text(HOME_NEEDLE + "/secret\n", encoding="utf-8")
    rc, out = run(copy)
    assert rc == 1, out
    assert "leak.txt" in out, out


def test_a_UTF16_file_hides_nothing(copy):
    """`decode("utf-8", "replace")` turns NUL-interleaved UTF-16 into replacement characters, so
    the needle matched nothing. Any editor that saves UTF-16 produced an invisible leak."""
    (copy / "notes16.txt").write_bytes((HOME_NEEDLE + "/Atlas\n").encode("utf-16-le"))
    rc, out = run(copy)
    assert rc == 1, out
    assert "notes16.txt" in out, out


def test_a_PRIVATE_PATH_IN_A_COMMIT_MESSAGE_is_found(tmp_path):
    """★ A PUSH PUBLISHES THE HISTORY, NOT THE TREE. The scan walked the working tree and said
    publishable. The real mirror carries the owner's absolute path in nineteen auto-generated merge
    subjects — nobody typed them, git wrote them, which is why no amount of care in the tree caught
    them."""
    r = tmp_path / "r"; r.mkdir()
    git("init", "-q", "-b", "main", str(r))
    (r / "a.txt").write_text("clean content\n", encoding="utf-8")
    git("add", "--", "a.txt", cwd=r)
    git("commit", "-q", "-m", "Merge branch 'x' of " + HOME_NEEDLE + "/projects/private", "--", "a.txt", cwd=r)
    rc, out = run(r)
    assert rc == 1, out
    assert "commit message" in out and "home path" in out, out


def test_a_CLEAN_history_is_not_reported(tmp_path):
    """The negative control: a repository whose messages are ordinary must stay silent, or the new
    half cries wolf on every publish and stops being read."""
    r = tmp_path / "r2"; r.mkdir()
    git("init", "-q", "-b", "main", str(r))
    (r / "a.txt").write_text("clean content\n", encoding="utf-8")
    git("add", "--", "a.txt", cwd=r)
    git("commit", "-q", "-m", "add a file", "--", "a.txt", cwd=r)
    rc, out = run(r)
    assert rc == 0, out
    assert "commit message" not in out, out


def test_clean_over_a_NON_GIT_ROOT_says_which_halves_never_ran(copy):
    """★ WHICH QUESTIONS WERE ASKED IS PART OF THE ANSWER. `--root <subdirectory>` is not a git
    root, so the tags, ignore and history halves returned nothing — silently — and the tool printed
    an unqualified `clean`. That is this file's own rule ('a walk of zero files proves nothing')
    broken for its own skipped checks."""
    rc, out = run(copy)
    assert rc == 0, out
    assert "NOT CHECKED" in out and "not a git root" in out, out
    assert "commit messages" in out, out
    assert "Historical blobs" in out and "never scanned" in out, out


def test_a_GIT_ROOT_does_not_print_the_not_checked_note(tmp_path):
    """The negative control for the note itself: a real git root asks all four questions, so
    claiming it skipped three would be a lie in the reassuring direction."""
    r = tmp_path / "r3"; r.mkdir()
    git("init", "-q", "-b", "main", str(r))
    (r / "a.txt").write_text("fine\n", encoding="utf-8")
    git("add", "--", "a.txt", cwd=r)
    git("commit", "-q", "-m", "ok", "--", "a.txt", cwd=r)
    rc, out = run(r)
    assert rc == 0, out
    assert "NOT CHECKED" not in out, out


def test_a_UTF16_hit_reports_the_line_a_PERSON_would_find(copy):
    """A finding nobody can locate is most of the way to a finding nobody acts on.

    The first version of this fix concatenated the decodings, so a UTF-16 hit's line number was
    offset by the whole length of the utf-8 pass: a leak on line 4 was reported as line 9."""
    body = "one\ntwo\nthree\n" + HOME_NEEDLE + "/Atlas\nfive\n"
    (copy / "off.txt").write_bytes(body.encode("utf-16-le"))
    rc, out = run(copy)
    assert rc == 1, out
    line = next(l for l in out.splitlines() if "off.txt" in l)
    assert ":4:" in line, line


def test_a_plain_binary_file_is_not_decoded_three_times(copy):
    """Cheapness is a promise here too: with `node_modules` now correctly walked, a vendored tree
    is the tool's dominant cost. UTF-16 is attempted only when the bytes contain a NUL — which no
    valid UTF-8 text file does — so an ordinary file is read once and decoded once."""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("pc_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pc_mod"] = mod
    spec.loader.exec_module(mod)
    assert len(list(mod._decodings(b"ordinary ascii text\n"))) == 1
    assert len(list(mod._decodings("x".encode("utf-16-le")))) > 1, "a NUL-bearing file must still try UTF-16"


def test_an_ANNOTATED_TAG_message_is_scanned(tmp_path):
    """★ A TAG MESSAGE IS NEITHER A COMMIT NOR A BLOB. `git log` never shows an annotated tag's own
    message, so a leak written with `git tag -a -m` sailed past a `clean` verdict whose printed
    limit mentioned only historical blobs — this file's own rule ("the door allows this" and "the
    door cannot see this" read alike) unapplied to the check it had just gained."""
    r = tmp_path / "t1"; r.mkdir()
    git("init", "-q", "-b", "main", str(r))
    (r / "a.txt").write_text("clean\n", encoding="utf-8")
    git("add", "--", "a.txt", cwd=r)
    git("commit", "-q", "-m", "ordinary subject", "--", "a.txt", cwd=r)
    git("tag", "-a", "v1", "-m", "leak in an annotated tag: " + HOME_NEEDLE + "/secret.md", cwd=r)
    rc, out = run(r)
    assert rc == 1, out
    assert "home path" in out and "v1" in out, out


def test_a_GIT_NOTE_is_scanned(tmp_path):
    r = tmp_path / "t2"; r.mkdir()
    git("init", "-q", "-b", "main", str(r))
    (r / "a.txt").write_text("clean\n", encoding="utf-8")
    git("add", "--", "a.txt", cwd=r)
    git("commit", "-q", "-m", "ordinary subject", "--", "a.txt", cwd=r)
    git("notes", "add", "-m", "leak in a note: " + HOME_NEEDLE + "/secret.md", cwd=r)
    rc, out = run(r)
    assert rc == 1, out
    assert "home path" in out, out


def test_an_ORDINARY_tag_and_note_are_not_reported(tmp_path):
    """The negative control: releases are tagged and notes are used. A half that fires on ordinary
    ones is a half nobody reads."""
    r = tmp_path / "t3"; r.mkdir()
    git("init", "-q", "-b", "main", str(r))
    (r / "a.txt").write_text("clean\n", encoding="utf-8")
    git("add", "--", "a.txt", cwd=r)
    git("commit", "-q", "-m", "ordinary subject", "--", "a.txt", cwd=r)
    git("tag", "-a", "v1", "-m", "release 1", cwd=r)
    git("notes", "add", "-m", "reviewed by two people", cwd=r)
    rc, out = run(r)
    assert rc == 0, out
