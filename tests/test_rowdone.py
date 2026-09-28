"""AUTODONE-1 — a finished queue row marks itself. Every guard in `rowdone.flip` has its positive
control (the row flips, the note lands as the LAST sub-line, the commit carries the queue file and
nothing else) and a negative control of its own: a sha not on main, an id no row carries, a row in
another lane's queue, a running pytest, an already-flipped row, a dirty queue file, an id on two
rows. The vault, repository, roster and lane marker are scratch copies under tmp_path; config is
redirected through the environment, which this package re-reads on every call.
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
import rowdone  # noqa: E402


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


OWN = """# own queue

- [ ] `q:ME-1` effort:S — the row this lane finishes
  - note: first note
  - note: second note
- [ ] `q:ME-2` effort:S — the next row, untouched
- [x] `q:ME-3` effort:S — already closed
  - done: 1234567 2026-09-01 by earlier
"""
THEIRS = """# their queue

- [ ] `q:THEM-1` effort:S — another lane's row
"""


@pytest.fixture
def w(tmp_path, monkeypatch):
    vault = tmp_path / "Vault"
    (vault / "Queues" / "regions").mkdir(parents=True)
    (vault / "Channels" / "ME").mkdir(parents=True)
    (vault / "Queues" / "regions" / "own.md").write_text(OWN)
    (vault / "Queues" / "regions" / "theirs.md").write_text(THEIRS)
    (vault / "Channels" / "ME" / ".keep").write_text("")
    git(vault, "init", "-q", "-b", "main"); git(vault, "add", "-A"); git(vault, "commit", "-q", "-m", "vault root")
    roster = tmp_path / "roster.md"
    roster.write_text("lane: ME\npath: Queues/\npath: Channels/ME/\n\n"
                      "lane: THEM\npath: Queues/regions/theirs.md\npath: Channels/THEM/\n")
    repo = tmp_path / "repo"; repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / ".rowdone").write_text("rows mark themselves\n\nqueues: Queues/regions\nhandoffs: review/HANDOFF*.md\n")
    (repo / ".atlas-lane").write_text("lane: ME\npath: Queues/\npath: Channels/ME/\n")
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "root")
    merged = git(repo, "rev-parse", "--short=8", "HEAD")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "f.txt").write_text("unmerged\n"); git(repo, "add", "f.txt"); git(repo, "commit", "-q", "-m", "wip")
    unmerged = git(repo, "rev-parse", "--short=8", "HEAD")
    git(repo, "checkout", "-q", "main")
    (repo / "review").mkdir()
    cfg = tmp_path / "config.json"; cfg.write_text("{}")
    for k, v in dict(GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(tmp_path / "state"),
                     GEDAECHTNIS_CONFIG=str(cfg), GEDAECHTNIS_FLEET_ROSTER=str(roster),
                     GEDAECHTNIS_USER_MEMORY=str(tmp_path / "none.md")).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(rowdone, "pytest_count", lambda ps_lines=None: 0)
    return dict(vault=vault, repo=repo, merged=merged, unmerged=unmerged, roster=roster)


def handoff(w, rows, name="HANDOFF-x.md"):
    body = "# handoff\n\n## ROWS:\n\n```\n" + "".join(
        f"q:{q}\tME\tbuilder-9\t{sha}\tACCEPT\t0\t0\t0\tnote\n" for q, sha in rows) + "```\n\n## GUARDIAN-ACTS:\n\n(none)\n"
    p = w["repo"] / "review" / name
    p.write_text(body)
    return p


def run(w, rows, sha=None):
    h = handoff(w, rows)
    return rowdone.apply(sha or rows[0][1], h, w["repo"], "ME", ["Queues", "Channels/ME"])


def own(w):
    return (w["vault"] / "Queues" / "regions" / "own.md").read_text()


def head_files(w):
    return git(w["vault"], "show", "--name-only", "--format=", "HEAD").splitlines()


# ---- the positive control ----------------------------------------------------------------------

def test_a_merged_row_flips_and_its_done_line_is_the_LAST_sub_line(w):
    res = run(w, [("ME-1", w["merged"])])
    assert res[0][0] == rowdone.OK, res
    lines = own(w).splitlines()
    i = lines.index("- [x] `q:ME-1` effort:S — the row this lane finishes")
    assert lines[i + 1:i + 4] == ["  - note: first note", "  - note: second note",
                                  f"  - done: {w['merged']} {rowdone.common.today()} by builder-9"]
    assert lines[i + 4].startswith("- [ ] `q:ME-2`")          # the next row is untouched
    assert own(w).endswith("\n")
    assert head_files(w) == ["Queues/regions/own.md"]          # path-limited: that file, nothing else
    assert git(w["vault"], "status", "--porcelain") == ""


# ---- one negative control per guard ------------------------------------------------------------

def test_a_sha_NOT_on_main_is_refused_and_nothing_is_written(w):
    before = own(w)
    code, line = run(w, [("ME-1", w["unmerged"])])[0]
    assert code == rowdone.NOT_ON_MAIN and "NOT on main" in line
    assert own(w) == before


def test_an_id_no_row_carries_prints_FILE_IT(w):
    code, line = run(w, [("ME-99", w["merged"])])[0]
    assert code == rowdone.ABSENT and line.startswith("FILE-IT ME-99")


def test_a_row_in_ANOTHER_lanes_queue_is_not_touched_and_a_notice_is_written(w):
    before = (w["vault"] / "Queues" / "regions" / "theirs.md").read_text()
    code, line = run(w, [("THEM-1", w["merged"])])[0]
    assert code == rowdone.FOREIGN and "THEM" in line
    assert (w["vault"] / "Queues" / "regions" / "theirs.md").read_text() == before
    notes = list((w["vault"] / "Channels" / "ME").glob("N-*-rowdone-them-1.md"))
    assert len(notes) == 1
    body = notes[0].read_text()
    assert "- to: THEM" in body and "- kind: fact" in body and w["merged"] in body
    assert head_files(w) == [str(notes[0].relative_to(w["vault"]))]


def test_a_queue_file_OUTSIDE_the_partition_is_refused(w):
    before = own(w)
    code, line = rowdone.apply(w["merged"], handoff(w, [("ME-1", w["merged"])]), w["repo"], "ME",
                               ["Channels/ME"])[0]
    assert code == rowdone.FOREIGN and "no notice" in line
    assert own(w) == before


def test_a_directory_grant_with_NO_readable_roster_is_refused(w, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_FLEET_ROSTER", str(w["roster"]) + ".missing")
    before = own(w)
    code, line = run(w, [("ME-1", w["merged"])])[0]
    assert code == rowdone.FOREIGN
    assert own(w) == before


def test_an_EXACT_file_grant_needs_no_roster(w, monkeypatch):
    monkeypatch.setenv("GEDAECHTNIS_FLEET_ROSTER", str(w["roster"]) + ".missing")
    code, _line = rowdone.apply(w["merged"], handoff(w, [("ME-1", w["merged"])]), w["repo"], "ME",
                                ["Queues/regions/own.md"])[0]
    assert code == rowdone.OK
    assert "- [x] `q:ME-1`" in own(w)


def test_a_suite_holding_the_lock_refuses_before_anything_is_read(w, monkeypatch, tmp_path):
    """PYTESTLOCK-1: the refusal is the suite lock's (a live foreign holder, pid 1), no longer the
    machine-wide pytest count."""
    monkeypatch.setenv("GEDAECHTNIS_SUITE_WAIT_S", "0")
    rowdone.suitelock.acquire(rowdone.suitelock.lock_dir(), 1)
    before = own(w)
    code, line = run(w, [("ME-1", w["merged"])])[0]
    assert code == rowdone.PYTEST and "suite lock held by pid 1" in line
    assert own(w) == before


def test_an_already_flipped_row_is_a_no_op_exit_0(w):
    head = git(w["vault"], "rev-parse", "HEAD")
    before = own(w)
    code, line = run(w, [("ME-3", w["merged"])])[0]
    assert code == rowdone.OK and "already [x]" in line
    assert own(w) == before and git(w["vault"], "rev-parse", "HEAD") == head


def test_a_queue_file_with_uncommitted_changes_is_refused(w):
    p = w["vault"] / "Queues" / "regions" / "own.md"
    p.write_text(OWN + "- [ ] `q:ME-4` effort:S — somebody's uncommitted row\n")
    code, line = run(w, [("ME-1", w["merged"])])[0]
    assert code == rowdone.DIRTY and "uncommitted" in line
    assert "- [ ] `q:ME-1`" in p.read_text()


def test_an_id_on_TWO_rows_is_refused_rather_than_choosing(w):
    p = w["vault"] / "Queues" / "regions" / "own.md"
    p.write_text(OWN + "- [ ] `q:ME-1` effort:S — the same id filed twice\n")
    git(w["vault"], "commit", "-q", "-am", "dup")
    code, line = run(w, [("ME-1", w["merged"])])[0]
    assert code == rowdone.AMBIGUOUS and "2 rows" in line


def test_a_row_whose_two_id_readings_disagree_is_refused(w):
    p = w["vault"] / "Queues" / "regions" / "own.md"
    p.write_text(OWN.replace("the next row, untouched", "untouched | q:ME-1"))
    git(w["vault"], "commit", "-q", "-am", "disagree")
    code, line = run(w, [("ME-2", w["merged"])])[0]
    assert code == rowdone.AMBIGUOUS and "anchored id" in line


def test_a_row_with_TWO_id_fields_is_refused(w):
    p = w["vault"] / "Queues" / "regions" / "own.md"
    p.write_text(OWN.replace("- [ ] `q:ME-2` effort:S — the next row, untouched",
                             "- [ ] legacy row | q:ME-2 | q:ME-9"))
    git(w["vault"], "commit", "-q", "-am", "two fields")
    code, line = run(w, [("ME-2", w["merged"])])[0]
    assert code == rowdone.AMBIGUOUS and "distinct" in line


# ---- the handoff reading -----------------------------------------------------------------------

def test_only_rows_with_THAT_sha_are_applied_and_a_handoff_without_one_says_so(w):
    h = handoff(w, [("ME-1", w["merged"]), ("ME-2", w["unmerged"])])
    res = rowdone.apply(w["merged"], h, w["repo"], "ME", ["Queues", "Channels/ME"])
    assert [c for c, _l in res] == [rowdone.OK]
    assert "- [ ] `q:ME-2`" in own(w)
    res = rowdone.apply("abcdef1", h, w["repo"], "ME", ["Queues", "Channels/ME"])
    assert res[0][0] == rowdone.NO_ROW


def test_a_repository_without_the_switch_file_is_off(w):
    (w["repo"] / ".rowdone").unlink()
    h = handoff(w, [("ME-1", w["merged"])])
    res = rowdone.apply(w["merged"], h, w["repo"], "ME", ["Queues", "Channels/ME"])
    assert res[0][0] == rowdone.USAGE and "off" in res[0][1]
    assert "- [ ] `q:ME-1`" in own(w)


def test_rows_lines_outside_the_ROWS_section_are_not_read():
    text = ("## Notes\n\nq:X-1\tME\ts\tabcdef12\tACCEPT\n\n## ROWS:\n\n    q:X-2\tME\ts\tabcdef12\tACCEPT\n"
            "\n## GUARDIAN-ACTS:\n\nq:X-3\tME\ts\tabcdef12\tACCEPT\n")
    assert [q for q, _s, _h in rowdone.rows_in_handoff(text)] == ["X-2"]


# ---- the anchored pytest count -----------------------------------------------------------------

def test_the_pytest_count_is_anchored_on_the_interpreter():
    rx = rowdone.PYTEST
    lines = ["/opt/homebrew/bin/python3 -m pytest tests -q",
             "/usr/bin/Python -m pytest x",
             "grep -E pytest",                                    # the grep that looks for it
             "/bin/zsh -c ps -axo command= | grep pytest",
             "vim notes-about-pytest.md"]
    assert sum(1 for l in lines if rx.match(l)) == 2
    assert rowdone.pytest_count(lines) == 2


def test_the_real_count_sees_THIS_pytest():
    """Positive control on the live machine: this test runs under pytest, so the count is ≥ 1."""
    assert rowdone.pytest_count() >= 1


# ---- the doors ---------------------------------------------------------------------------------

def test_the_verify_merge_door_flips_the_rows_of_the_newest_handoff(w):
    handoff(w, [("ME-1", w["merged"])])
    out = f"{w['merged']} — on main (repo {w['repo']})"
    text = rowdone.after_verify("sid-x", f"python3 mergesha.py verify-merge {w['merged']}", out, str(w["repo"]))
    assert text and "flipped [x]" in text
    assert "- [x] `q:ME-1`" in own(w)


def test_the_verify_merge_door_is_silent_when_the_sha_is_not_on_main(w):
    handoff(w, [("ME-1", w["merged"])])
    out = f"{w['merged']} — NOT on main (repo {w['repo']})"
    assert rowdone.after_verify("sid-x", f"python3 mergesha.py verify-merge {w['merged']}", out, str(w["repo"])) is None
    assert "- [ ] `q:ME-1`" in own(w)


def test_the_stop_door_reads_every_handoff_the_session_wrote(w):
    h1 = handoff(w, [("ME-1", w["merged"])], name="HANDOFF-1.md")
    h2 = handoff(w, [("ME-2", w["merged"])], name="HANDOFF-2.md")
    for h in (h1, h2):
        rowdone.common.record_handoff("sid-stop", h)
    res = rowdone.at_stop("sid-stop", str(w["repo"]))
    assert [c for c, _l in res] == [rowdone.OK, rowdone.OK]
    assert "- [x] `q:ME-1`" in own(w) and "- [x] `q:ME-2`" in own(w)


def test_the_stop_door_ignores_handoffs_this_session_did_not_write(w):
    handoff(w, [("ME-1", w["merged"])])
    assert rowdone.at_stop("sid-other", str(w["repo"])) == []
    assert "- [ ] `q:ME-1`" in own(w)


def test_the_chore_wiring_reaches_rowdone_end_to_end(w, tmp_path):
    """The PostToolUse Bash door as the harness runs it: a subprocess, a real ps — and this very
    pytest is running, so the answer that proves the door fired is the pytest refusal."""
    handoff(w, [("ME-1", w["merged"])])
    payload = {"cwd": str(w["repo"]), "tool_name": "Bash", "session_id": "sid-e2e",
               "tool_input": {"command": f"python3 mergesha.py verify-merge {w['merged']}"},
               "tool_response": {"stdout": f"{w['merged']} — on main (repo {w['repo']})"}}
    p = subprocess.run([sys.executable, str(HOOKS / "chore.py"), "bash"], input=json.dumps(payload),
                       capture_output=True, text=True, env=dict(os.environ), timeout=60)
    assert p.returncode == 0, p.stderr
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert f"Row marks for {w['merged']} (HANDOFF-x.md): " in ctx and "q:ME-1" in ctx


def test_hooks_json_runs_the_stop_door_AFTER_the_commit_door():
    hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]["Stop"][0]["hooks"]
    cmds = [h["command"] for h in hooks]
    i_commit = next(i for i, c in enumerate(cmds) if c.rstrip("\"").endswith("/hooks/commit.py"))
    i_rowdone = next(i for i, c in enumerate(cmds) if c.replace("\"", "").endswith("chore.py rowdone"))
    assert i_rowdone > i_commit


# ---- the second evidence source: merge SUBJECTS on main (MNEMOSYNE notice 2026-09-25) --------

DATED = """# own queue

- [ ] `q:ME-2026-09-20-A-1` effort:S — merged under a `Merge` subject
- [ ] `q:ME-2026-09-20-B-1` effort:S — merged under a `(q:...)` subject
- [ ] `q:ME-2026-09-20-C-1` effort:S — named only in a commit BODY
- [ ] `q:ME-2026-09-20-D-1` effort:S — merged 30 days ago
"""


def subject_fixture(w):
    (w["vault"] / "Queues" / "regions" / "own.md").write_text(DATED)
    (w["vault"] / "Queues" / "regions" / "theirs.md").write_text(
        "# theirs\n\n- [ ] `q:TH-2026-09-20-E-1` effort:S — their merged row\n")
    git(w["vault"], "commit", "-q", "-am", "dated rows")
    r = w["repo"]

    def commit(msg, date=None):
        env = dict(os.environ)
        if date:
            env.update(GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date)
        subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                        "-q", "--allow-empty", "-m", msg], check=True, env=env)
    commit("Merge ME-2026-09-20-A-1: the A row")
    commit("feature work (q:ME-2026-09-20-B-1)")
    commit("unrelated subject\n\nMerge q:ME-2026-09-20-C-1 is only mentioned here (q:ME-2026-09-20-C-1)")
    commit("Merge q:ME-2026-09-20-D-1", date="2020-01-01T00:00:00")
    commit("Merge q:TH-2026-09-20-E-1")


def test_merge_subjects_flip_our_rows_and_nothing_else(w):
    subject_fixture(w)
    res = rowdone.subject_scan("sid-subj", w["repo"], "ME", ["Queues", "Channels/ME"], repos=[w["repo"]])
    text = own(w)
    assert "- [x] `q:ME-2026-09-20-A-1`" in text, res
    assert "- [x] `q:ME-2026-09-20-B-1`" in text
    assert "- [ ] `q:ME-2026-09-20-C-1`" in text          # a BODY mention is not evidence
    assert "- [ ] `q:ME-2026-09-20-D-1`" in text          # older than SUBJECT_DAYS
    assert sum(1 for c, _l in res if c == rowdone.OK) == 2


def test_a_foreign_rows_merge_subject_is_RECORDED_never_closed(w):
    subject_fixture(w)
    theirs_before = (w["vault"] / "Queues" / "regions" / "theirs.md").read_text()
    res = rowdone.subject_scan("sid-subj", w["repo"], "ME", ["Queues", "Channels/ME"], repos=[w["repo"]])
    assert (w["vault"] / "Queues" / "regions" / "theirs.md").read_text() == theirs_before
    assert any(l.startswith("QUEUE-STALE-OPEN TH-2026-09-20-E-1 ") for _c, l in res)
    facts = rowdone.facts_lines("THEM")
    assert len(facts) == 2 and "QUEUE-STALE-OPEN TH-2026-09-20-E-1" in facts[1] and "notice Channels/ME/" in facts[1]
    assert rowdone.facts_lines("ME") == []                 # printed to the OWNING lane only


def test_the_subject_scan_is_off_without_the_switch_file(w):
    subject_fixture(w)
    (w["repo"] / ".rowdone").unlink()
    assert rowdone.subject_scan("sid-subj", w["repo"], "ME", ["Queues", "Channels/ME"], repos=[w["repo"]]) == []
    assert "- [ ] `q:ME-2026-09-20-A-1`" in own(w)


# ---- flip --vault-sha: a row finished by a vault commit, not a code merge ----------------------

def _flip(w, sha, vault_sha):
    files = [w["vault"] / "Queues" / "regions" / "own.md", w["vault"] / "Queues" / "regions" / "theirs.md"]
    return rowdone.flip("ME-1", sha, "builder-9", w["repo"], "ME", ["Queues", "Channels/ME"], files,
                        vault_sha=vault_sha)


def test_vault_sha_positive_a_vault_commit_flips_the_row(w):
    vsha = git(w["vault"], "rev-parse", "--short=8", "HEAD")
    code, line = _flip(w, vsha, True)
    assert code == rowdone.OK and f"vault commit {vsha}" in line, line
    assert f"  - done: {vsha} {rowdone.common.today()} by builder-9" in own(w)
    assert "- [x] `q:ME-1`" in own(w) and head_files(w) == ["Queues/regions/own.md"]


def test_vault_sha_negative_a_repo_sha_is_not_a_vault_commit(w):
    before = own(w)
    code, line = _flip(w, w["merged"], True)
    assert code == rowdone.NOT_ON_MAIN and "not a commit in the vault" in line, line
    assert own(w) == before


def test_vault_sha_negative_without_the_flag_a_vault_sha_is_still_refused(w):
    """The flag is what licenses a vault sha: the default path still asks the code repo's main."""
    vsha = git(w["vault"], "rev-parse", "--short=8", "HEAD")
    before = own(w)
    assert _flip(w, vsha, False)[0] == rowdone.NOT_ON_MAIN and own(w) == before


def test_vault_sha_through_the_cli(w, monkeypatch, capsys):
    vsha = git(w["vault"], "rev-parse", "--short=8", "HEAD")
    monkeypatch.chdir(w["repo"])
    assert rowdone.main(["flip", "q:ME-1", "--vault-sha", vsha]) == rowdone.USAGE     # --by is required
    assert rowdone.main(["flip", "q:ME-1", "--vault-sha", "deadbeef", "--by", "b"]) == rowdone.NOT_ON_MAIN
    assert rowdone.main(["flip", "q:ME-1", "--vault-sha", vsha, "--by", "b", "--repo", str(w["repo"])]) == rowdone.OK
    assert f"  - done: {vsha} " in own(w)
