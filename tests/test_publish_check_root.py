"""tests/test_publish_check_root.py — `--root` sweeping an ARBITRARY repo (SWEEPROOT-1).

`test_publish_check.py` covers the plugin's own tree (the default scan target) and the tags half.
This file covers the other ruled requirement: `--root DIR` must run the exact same sweep — reused
classes, never a duplicate rule — over ANY repository, so it can serve the private push of a
completely different project (the ukrainian-card repo, per the ruling this row implements).

Every fixture below is a small synthetic git repo built fresh in `tmp_path`, never a real project.
No owner name or real personal fact appears anywhere in this file — every planted value is an
obviously fake stand-in (an invalid-TLD email, a made-up phone digit string, a textbook IBAN
example, a made-up API key shape). Every one of them is COMPOSED FROM FRAGMENTS, never written as
a contiguous literal — same convention as `test_publish_check.py` and for the same reason: this
checker scans its own test suite too, so a literal needle here would make this file its own hit.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "tools" / "publish_check.py"

sys.path.insert(0, str(PLUGIN / "tools"))
import publish_check as pc  # noqa: E402  (needs the sys.path insert above)

# Fragment-composed fake needles, reused across tests below — never a contiguous literal anywhere
# in this file (see module docstring).
FAKE_EMAIL = "nobody" + "@" + "example.invalid"
FAKE_PHONE = "+00" + " 000" + " 000" + " 0000"
FAKE_IBAN = "GB29" + "NWBK" + "60161331926819"   # textbook IBAN check-digit example, not a real account


def run(root: Path):
    p = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)],
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=str(cwd), capture_output=True, text=True, check=True, timeout=120)


@pytest.fixture
def bare_repo(tmp_path):
    """A minimal, unrelated git repo standing in for a foreign project like ukrainian-card."""
    repo = tmp_path / "some-other-repo"
    repo.mkdir()
    git("init", "-q", "-b", "main", str(repo), cwd=tmp_path)
    (repo / "README.md").write_text("a project with nothing to do with this plugin\n", encoding="utf-8")
    git("add", "--", "README.md", cwd=repo)
    git("commit", "-q", "-m", "root", "--", "README.md", cwd=repo)
    return repo


# ------------------------------------------------------------------------------- clean / empty ----

def test_a_clean_foreign_repo_passes(bare_repo):
    rc, out = run(bare_repo)
    assert rc == 0, out
    assert "clean" in out


def test_an_empty_root_is_unchecked_not_clean(tmp_path):
    """A walked-count of zero must never read as clean — that would be a false negative by design.
    (The UNCHECKED sentence itself uses the word "clean" in prose — "not a clean tree" — so this
    checks for the actual success line `publish check: clean —`, not a bare substring.)"""
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    rc, out = run(empty)
    assert rc != 0, out
    assert "UNCHECKED" in out
    assert "publish check: clean" not in out


def test_a_nonexistent_root_is_refused(tmp_path):
    rc, out = run(tmp_path / "does-not-exist")
    assert rc != 0, out


# --------------------------------------------------------------------- positive controls: secrets ----

def test_a_planted_dotenv_file_is_found(bare_repo):
    (bare_repo / ".env").write_text("API_KEY=" + "x" * 10 + "\n", encoding="utf-8")
    rc, out = run(bare_repo)
    assert rc == 1, out
    assert ".env:1:" in out and "env file" in out
    # the value itself must never appear in the report
    assert ("x" * 10) not in out


def test_a_dotenv_example_sibling_is_not_flagged_as_an_env_file(bare_repo):
    (bare_repo / ".env.example").write_text("API_KEY=replace_me\n", encoding="utf-8")
    rc, out = run(bare_repo)
    assert rc == 0, out
    assert "env file" not in out


def test_a_planted_owner_name_is_found(bare_repo):
    fake_name = "n" + "e" + " " + "m" + "o"  # obviously fake, composed so this file is not a hit
    (bare_repo / "notes.txt").write_text("thanks to " + "ju" + "stus" + " (aka " + fake_name + ")\n",
                                         encoding="utf-8")
    rc, out = run(bare_repo)
    assert rc == 1, out
    assert "notes.txt:1:" in out and "owner name" in out


def test_a_planted_email_is_found(bare_repo):
    (bare_repo / "contact.txt").write_text(FAKE_EMAIL + "\n", encoding="utf-8")
    rc, out = run(bare_repo)
    assert rc == 1, out
    assert "contact.txt:1:" in out and "email" in out
    assert FAKE_EMAIL not in out  # redacted, not printed whole


def test_a_planted_phone_number_is_found(bare_repo):
    (bare_repo / "contact.txt").write_text("call " + FAKE_PHONE + "\n", encoding="utf-8")
    rc, out = run(bare_repo)
    assert rc == 1, out
    assert "contact.txt:1:" in out and "phone" in out


def test_a_planted_iban_shape_is_found(bare_repo):
    # textbook example IBAN shape (not a real account) from the IBAN Wikipedia example set
    (bare_repo / "contact.txt").write_text(FAKE_IBAN + "\n", encoding="utf-8")
    rc, out = run(bare_repo)
    assert rc == 1, out
    assert "contact.txt:1:" in out and "iban" in out


def test_a_planted_api_key_is_found(bare_repo):
    (bare_repo / "config.py").write_text('KEY = "sk-ant-api03-' + "A" * 40 + '"\n', encoding="utf-8")
    rc, out = run(bare_repo)
    assert rc == 1, out
    assert "config.py:1:" in out and "api key" in out
    assert ("A" * 40) not in out  # redacted


# ----------------------------------------------------------------- positive control: gitignore ----

def test_a_gitignored_but_tracked_file_is_found(bare_repo):
    (bare_repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    git("add", "--", ".gitignore", cwd=bare_repo)
    git("commit", "-q", "-m", "ignore env", "--", ".gitignore", cwd=bare_repo)
    secret = bare_repo / ".env"
    secret.write_text("TOKEN=" + "y" * 20 + "\n", encoding="utf-8")
    git("add", "-f", "--", ".env", cwd=bare_repo)   # force past the ignore, the real-world mistake
    git("commit", "-q", "-m", "oops committed anyway", "--", ".env", cwd=bare_repo)
    rc, out = run(bare_repo)
    assert rc == 1, out
    assert "gitignore-tracked" in out
    assert ".env" in out


def test_an_ignored_but_untracked_file_is_not_a_gitignore_finding(bare_repo):
    """The negative control for the gitignore-tracked class: ignored AND untracked is fine. Uses a
    non-`.env`-named file with content that trips no OTHER class, so this isolates the
    gitignore-tracked check specifically rather than being masked by the env-file class."""
    (bare_repo / ".gitignore").write_text("local.secrets\n", encoding="utf-8")
    git("add", "--", ".gitignore", cwd=bare_repo)
    git("commit", "-q", "-m", "ignore local secrets file", "--", ".gitignore", cwd=bare_repo)
    (bare_repo / "local.secrets").write_text("just a local scratch file, nothing planted\n",
                                              encoding="utf-8")  # never git-added
    rc, out = run(bare_repo)
    assert rc == 0, out
    assert "gitignore-tracked" not in out


# ---------------------------------------------------------------------------------- redaction ----

def test_redact_never_returns_the_input_text_for_long_secrets():
    secret = "sk-ant-api03-" + "B" * 40
    red = pc.redact(secret)
    assert red != secret
    assert secret not in red
    assert len(red) < len(secret)


def test_redact_of_short_text_is_fully_masked():
    assert pc.redact("abcdef") == "******"
    assert pc.redact("") == "(empty)"


# ------------------------------------------------------- mutation checks, one per class (SWEEPROOT-1) ----
# Each test builds a REAL mutated COPY of publish_check.py on disk (source-text surgery, run as its
# own subprocess — a full black-box rerun, not an in-process patch) with exactly one detection rule
# neutered, runs it against a fixture that plants that class's finding, and asserts the mutated
# script goes GREEN (rc 0 / "clean") where the unmutated script goes RED — i.e. the positive-control
# test for that class would itself go red (fail) if this were the shipped code. The `run_script`
# helper is `run()` above generalised to take an arbitrary script path instead of always SCRIPT.

def run_script(script: Path, root: Path):
    p = subprocess.run([sys.executable, str(script), "--root", str(root)],
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def mutated_copy(tmp_path, *, drop_pattern_kind: str | None = None,
                  neuter_env_file: bool = False,
                  neuter_gitignore_tracked: bool = False,
                  neuter_walked_zero_guard: bool = False) -> Path:
    src = SCRIPT.read_text(encoding="utf-8")
    if drop_pattern_kind is not None:
        before = src
        src = "\n".join(
            line for line in src.splitlines()
            if not line.strip().startswith(f'("{drop_pattern_kind}"')
        ) + "\n"
        assert src != before, f"no PATTERNS line found for kind={drop_pattern_kind!r} — mutation is a no-op"
    if neuter_env_file:
        anchor = "def is_env_file(p: Path) -> bool:\n    name = p.name"
        replacement = "def is_env_file(p: Path) -> bool:\n    return False  # MUTATED\n    name = p.name"
        assert anchor in src, "is_env_file anchor text not found — mutation is a no-op"
        src = src.replace(anchor, replacement)
    if neuter_gitignore_tracked:
        anchor = "    if not (root / \".git\").exists():\n        return [], None\n    try:\n        p = subprocess.run([\"git\", \"-C\", str(root), \"ls-files\""
        assert anchor in src, "tracked_but_ignored anchor text not found — mutation is a no-op"
        src = src.replace(
            anchor,
            "    return [], None  # MUTATED\n" + anchor,
        )
    if neuter_walked_zero_guard:
        anchor = (
            "    if walked == 0:\n"
            "        print(f\"publish check: UNCHECKED — {root} — the walk found 0 files, which is not a clean \"\n"
            "              f\"tree, it is an unproven one\", file=sys.stderr)\n"
            "        return 1\n"
        )
        assert anchor in src, "walked==0 guard anchor text not found — mutation is a no-op"
        src = src.replace(anchor, "")
    mutant = tmp_path / "publish_check_mutant.py"
    mutant.write_text(src, encoding="utf-8")
    return mutant


def test_mutation_removing_env_file_detection_goes_green_where_original_goes_red(bare_repo, tmp_path):
    (bare_repo / ".env").write_text("API_KEY=" + "x" * 10 + "\n", encoding="utf-8")
    rc_orig, out_orig = run(bare_repo)
    assert rc_orig == 1 and "env file" in out_orig
    mutant = mutated_copy(tmp_path, neuter_env_file=True)
    rc_mut, out_mut = run_script(mutant, bare_repo)
    assert rc_mut == 0, out_mut
    assert "publish check: clean" in out_mut
    assert ": env file:" not in out_mut


def test_mutation_removing_owner_name_pattern_goes_green_where_original_goes_red(bare_repo, tmp_path):
    fake_name = "n" + "e" + " " + "m" + "o"
    (bare_repo / "notes.txt").write_text("thanks to " + "ju" + "stus" + " (aka " + fake_name + ")\n",
                                         encoding="utf-8")
    rc_orig, out_orig = run(bare_repo)
    assert rc_orig == 1 and "owner name" in out_orig
    mutant = mutated_copy(tmp_path, drop_pattern_kind="owner name")
    rc_mut, out_mut = run_script(mutant, bare_repo)
    assert rc_mut == 0, out_mut
    assert "publish check: clean" in out_mut
    assert ": owner name:" not in out_mut


def test_mutation_removing_email_pattern_goes_green_where_original_goes_red(bare_repo, tmp_path):
    (bare_repo / "contact.txt").write_text(FAKE_EMAIL + "\n", encoding="utf-8")
    rc_orig, out_orig = run(bare_repo)
    assert rc_orig == 1 and "email" in out_orig
    mutant = mutated_copy(tmp_path, drop_pattern_kind="email")
    rc_mut, out_mut = run_script(mutant, bare_repo)
    assert rc_mut == 0, out_mut
    assert "publish check: clean" in out_mut
    assert ": email:" not in out_mut


def test_mutation_removing_phone_pattern_goes_green_where_original_goes_red(bare_repo, tmp_path):
    (bare_repo / "contact.txt").write_text("call " + FAKE_PHONE + "\n", encoding="utf-8")
    rc_orig, out_orig = run(bare_repo)
    assert rc_orig == 1 and "phone" in out_orig
    mutant = mutated_copy(tmp_path, drop_pattern_kind="phone")
    rc_mut, out_mut = run_script(mutant, bare_repo)
    assert rc_mut == 0, out_mut
    assert "publish check: clean" in out_mut
    assert ": phone:" not in out_mut


def test_mutation_removing_iban_pattern_goes_green_where_original_goes_red(bare_repo, tmp_path):
    (bare_repo / "contact.txt").write_text(FAKE_IBAN + "\n", encoding="utf-8")
    rc_orig, out_orig = run(bare_repo)
    assert rc_orig == 1 and "iban" in out_orig
    mutant = mutated_copy(tmp_path, drop_pattern_kind="iban")
    rc_mut, out_mut = run_script(mutant, bare_repo)
    assert rc_mut == 0, out_mut
    assert "publish check: clean" in out_mut
    assert ": iban:" not in out_mut


def test_mutation_removing_api_key_patterns_goes_green_where_original_goes_red(bare_repo, tmp_path):
    (bare_repo / "config.py").write_text('KEY = "sk-ant-api03-' + "A" * 40 + '"\n', encoding="utf-8")
    rc_orig, out_orig = run(bare_repo)
    assert rc_orig == 1 and "api key" in out_orig
    mutant = mutated_copy(tmp_path, drop_pattern_kind="api key")
    rc_mut, out_mut = run_script(mutant, bare_repo)
    assert rc_mut == 0, out_mut
    assert "publish check: clean" in out_mut
    assert ": api key:" not in out_mut


def test_mutation_disabling_gitignore_tracked_check_goes_green_where_original_goes_red(bare_repo, tmp_path):
    (bare_repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    git("add", "--", ".gitignore", cwd=bare_repo)
    git("commit", "-q", "-m", "ignore env", "--", ".gitignore", cwd=bare_repo)
    secret = bare_repo / ".env"
    secret.write_text("TOKEN=" + "y" * 20 + "\n", encoding="utf-8")
    git("add", "-f", "--", ".env", cwd=bare_repo)
    git("commit", "-q", "-m", "oops committed anyway", "--", ".env", cwd=bare_repo)
    rc_orig, out_orig = run(bare_repo)
    assert rc_orig == 1 and "gitignore-tracked" in out_orig
    mutant = mutated_copy(tmp_path, neuter_gitignore_tracked=True)
    rc_mut, out_mut = run_script(mutant, bare_repo)
    # the .env content itself would still be caught by the env-file/credential classes on this
    # fixture, so assert the SPECIFIC class the mutation targets is gone, not overall greenness.
    assert "gitignore-tracked" not in out_mut, out_mut


def test_mutation_removing_walked_zero_guard_goes_green_where_original_goes_red(tmp_path):
    empty = tmp_path / "nothing-here-2"
    empty.mkdir()
    rc_orig, out_orig = run(empty)
    assert rc_orig != 0 and "UNCHECKED" in out_orig
    mutant = mutated_copy(tmp_path, neuter_walked_zero_guard=True)
    rc_mut, out_mut = run_script(mutant, empty)
    assert rc_mut == 0, out_mut
    assert "clean" in out_mut, \
        "removing the walked==0 guard must make the empty root falsely read as clean"
