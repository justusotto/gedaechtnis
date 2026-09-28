"""lesson_push.py — the write-time push, both triggers, and the controls that say when it is quiet.

Every rule has a POSITIVE control (it fires) and a NEGATIVE one (it does not). The negative side
carries the weight here: this arm's failure mode is NOISE, a line printed after every write that
nobody reads, and a test suite that only ever watches it speak cannot see that.

★ DESIGN 2 (LESSONPUSH-2) IS WHAT IS TESTED HERE. The matcher is CITATION, not shared vocabulary:
a lesson is surfaced when the write touches something the lesson's own body cites. So the suite is
organised by TOKEN CLASS — q-id, ruling, wikilinked heading, two-segment path, backticked name,
target filename — each with a control that fires and a control that must not, because a class that
matched everything and a class that matched nothing would both pass a suite organised any other
way. The two triggers (Edit/Write and Bash) each get the same treatment.

The shipped decision gets its own test. `lesson_push_enabled` is FALSE in the shipped limits, on
gates measured TWICE now (design 1's, and design 2's recorded beside the key), and
`test_ships_off_by_default` pins it: the module must stay silent on a write it would otherwise
match exactly. If someone turns it on, that test goes red and they have to say so in the same act.

State, limits, config and the vault are all redirected into tmp_path. The suite never reads the
real vault, the real ~/.claude/gedaechtnis, or the machine's real transcripts.
"""
from __future__ import annotations
import importlib, json, os, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"

# ★ EVERY ENTRY BELOW CITES EXACTLY ONE DISTINCTIVE THING, and which class it cites is the point:
# one lesson per token class, so a test that fires can name WHICH class carried it and a mutation
# that kills one class cannot hide behind another.
GLOBAL_ERRATA = """# Errata (Global)

## A multi-path `git add` with ONE nonexistent path stages NOTHING

Found while closing q:CU-2026-09-01-ALPHA-1: the commit still succeeds and claims files it does
not carry.

## HTTP headers (requests/urllib3) are latin-1 only

Settled by owner-ruling-headers-ascii-2026-08-01. Cyrillic in the path is fine.

## A measured ZERO needs a positive control

The same reasoning as [[Patterns-verification#A control is not a control until a MUTATION has
reddened it]], one instrument over.
"""

SPECULUM_PATTERNS = """# Speculum Patterns

## The apostrophe regime crosses the tokenizer boundary silently

The crossing happens in `lemma/normalize.py` and nowhere else.

## A concurrent automated committer sweeps an unstaged file into its own commit

Seen in `tools/publish_check.py`: write the message file BEFORE the edit.

## Worktree isolation resolves against the parent repository, not the branch

The helper is `record_touched`, and it compares repo roots.

## A lesson whose body cites nothing at all is invisible to this matcher

Pure prose. No path, no id, no backticked name — this entry exists to prove the honest failure
mode is real, and it must never appear in the index.
"""

OTHER_ERRATA = """# Errata (Mnemosyne)

## Stanza lemmatization drops the vocative before proper-noun lists are loaded

The list is loaded by `scripts/load_names.py`, after the first pass.
"""


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A tiny vault, a redirected state dir, and the module freshly imported against them."""
    vault = tmp_path / "Atlas"
    write(vault / "Global" / "Errata.md", GLOBAL_ERRATA)
    write(vault / "Speculum" / "Patterns.md", SPECULUM_PATTERNS)
    write(vault / "Mnemosyne" / "Errata.md", OTHER_ERRATA)
    (vault / "Empty").mkdir(parents=True, exist_ok=True)      # a region with no lesson files
    state = tmp_path / "state"
    limits_file = tmp_path / "limits.json"
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    shipped["lesson_push_enabled"] = True                      # ON, so the behaviour is reachable
    limits_file.write_text(json.dumps(shipped), encoding="utf-8")

    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(vault))
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(state))
    monkeypatch.setenv("GEDAECHTNIS_LIMITS", str(limits_file))
    monkeypatch.setenv("GEDAECHTNIS_CONFIG", str(tmp_path / "no-such-config.json"))
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    if str(PLUGIN) not in sys.path:
        sys.path.insert(0, str(PLUGIN))
    import config, limits, common                              # noqa: PLC0415
    importlib.reload(config)
    importlib.reload(common)
    # ★ `limits.LIMITS_PATH` IS BOUND AT IMPORT, so `_reset_for_tests()` alone clears the cache and
    # keeps reading whatever file the FIRST test in the process pointed it at. Two tests here read
    # the first one's tmp_path before the reload was added, and both failed in the reassuring
    # direction: the module behaved, on a file the test was not writing.
    importlib.reload(limits)
    import lesson_push                                         # noqa: PLC0415
    importlib.reload(lesson_push)
    yield dict(tmp=tmp_path, vault=vault, state=state, limits_file=limits_file, mod=lesson_push)
    # Put the process back on the REAL files. The env vars are monkeypatch's to restore and it does
    # so after this runs, so the vars are cleared here first — a module reloaded while they still
    # point at tmp_path would freeze this test's paths into every module that follows.
    for var in ("GEDAECHTNIS_VAULT", "GEDAECHTNIS_STATE_DIR", "GEDAECHTNIS_LIMITS",
                "GEDAECHTNIS_CONFIG"):
        os.environ.pop(var, None)
    importlib.reload(config)
    importlib.reload(common)
    importlib.reload(limits)
    importlib.reload(lesson_push)


def set_limit(world, **kw):
    data = json.loads(world["limits_file"].read_text(encoding="utf-8"))
    data.update(kw)
    world["limits_file"].write_text(json.dumps(data), encoding="utf-8")
    import limits                                              # noqa: PLC0415
    importlib.reload(limits)


def lines(out: str) -> list:
    return [l for l in (out or "").splitlines() if l.startswith("lesson: ")]


# ------------------------------------------------------------------ one class at a time ----

def test_a_queue_row_id_matches(world):
    """The most specific class there is: an id names exactly one thing."""
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s-q", target, "closing out q:CU-2026-09-01-ALPHA-1 this morning", None)
    assert out is not None, "the positive control did not fire — nothing below it means anything"
    assert "multi-path `git add`" in out


def test_a_ruling_id_matches(world):
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s-r", target, "applying owner-ruling-headers-ascii-2026-08-01 to the header",
                    None)
    assert out is not None and "latin-1" in out


def test_a_wikilinked_heading_matches(world):
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s-h", target,
                    "per [[Patterns-verification#A control is not a control until a MUTATION has "
                    "reddened it]] we mutate first", None)
    assert out is not None and "measured ZERO" in out


def test_a_two_segment_path_matches(world):
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s-p", target, "touching `lemma/normalize.py` again", None)
    assert out is not None and "apostrophe regime" in out


def test_a_backticked_name_matches(world):
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s-n", target, "the call to `record_touched` moved up a line", None)
    assert out is not None and "Worktree isolation" in out


def test_the_file_being_written_is_itself_a_token(world, tmp_path):
    """The target path is the strongest signal a write carries: a session writing
    `publish_check.py` is touching the file a lesson is about, whatever its diff says."""
    lp = world["mod"]
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text(f"@{world['vault']}/Speculum/Kernel.md\n", encoding="utf-8")
    write(world["vault"] / "Speculum" / "Kernel.md", "# k\n")
    target = write(repo / "tools" / "publish_check.py", "x")
    out = lp.notice("s-f", target, "no citations in this text at all", str(repo))
    assert out is not None and "concurrent automated committer" in out


# ------------------------------------------------------------------ the class that must NOT fire ----

def test_shared_vocabulary_alone_is_not_a_match(world):
    """★ THE DEFINING NEGATIVE CONTROL OF DESIGN 2, and the one design 1 would have failed.

    This text is ABOUT the apostrophe lesson in the words of its heading — every term design 1
    scored on — and it cites nothing that lesson cites. Design 1 surfaced exactly this and called
    it a match; design 2 must be silent, or the matcher has not actually been swapped."""
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s-vocab", target,
                    "the apostrophe regime crosses the tokenizer boundary and the crossing is "
                    "silent in both directions", None)
    assert out is None


def test_a_bare_filename_in_prose_is_not_a_token(world):
    """A document naming a file in passing is not touching it — only a QUALIFIED path, or the
    target of the write itself, may carry the weak `file:` class."""
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    assert lp.notice("s-bare", target, "we discussed publish_check.py at length", None) is None
    # ...and the qualified form of the same file DOES match, so the rule is a rule and not a wall
    assert lp.notice("s-bare2", target, "we discussed `tools/publish_check.py` at length", None)


def test_a_role_stem_filename_is_never_a_token(world):
    """Every region has an `Errata.md`. A write to one, and a lesson body naming another, have
    nothing to do with each other — and without this rule they would match in every vault."""
    lp = world["mod"]
    assert lp.norm_path_token("Errata.md") == (None, None)
    assert lp.norm_path_token("Global/Errata.md")[0] == "path:global/errata.md"
    assert lp.norm_path_token("Global/Errata.md")[1] is None


def test_a_citation_inside_a_fence_is_shown_not_cited(world):
    """`citations.quotable_text`'s rule, applied to this matcher's write side: a document that
    REPRODUCES a command or an old row is not making a claim about it. Inline backticks are
    deliberately still citations — that is how a real one is written here."""
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    fenced = "here is the shape:\n\n```\nq:CU-2026-09-01-ALPHA-1\n```\n"
    assert lp.notice("s-fence", target, fenced, None) is None
    assert lp.notice("s-fence2", target, "closing `q:CU-2026-09-01-ALPHA-1`", None) is not None


def test_a_lesson_that_cites_nothing_is_not_in_the_index(world):
    """The honest failure mode, pinned as a fact rather than left as a claim in a docstring: an
    entry of pure prose cannot be reached by this matcher at all."""
    lp = world["mod"]
    headings = [h for _r, h, _t in lp.candidates(world["vault"], "Speculum")]
    assert not any("cites nothing at all" in h for h in headings)
    assert any("apostrophe regime" in h for h in headings)


def test_a_token_cited_by_too_many_entries_is_furniture(world):
    """`MAX_ENTRIES_PER_TOKEN`. A file every arc has one of names nothing; measured on the real
    vault, over 95% of tokens name one or two entries and the tail above eight is `DEVIATIONS.md`,
    `report.md`, `settings.json`."""
    lp = world["mod"]
    body = "\n".join(f"## Lesson number {i} about the shared file\n\nSee `arc/DEVIATIONS.md`.\n"
                     for i in range(lp.MAX_ENTRIES_PER_TOKEN + 1))
    write(world["vault"] / "Speculum" / "Patterns.md", "# Speculum Patterns\n\n" + body)
    idx = lp.build_index(lp.candidates(world["vault"], "Speculum"))
    assert "path:arc/deviations.md" not in idx
    # one fewer entry and the same token IS indexed — the cut is a threshold, not a ban
    body2 = "\n".join(f"## Lesson number {i} about the shared file\n\nSee `arc/DEVIATIONS.md`.\n"
                      for i in range(lp.MAX_ENTRIES_PER_TOKEN))
    write(world["vault"] / "Speculum" / "Patterns.md", "# Speculum Patterns\n\n" + body2)
    idx2 = lp.build_index(lp.candidates(world["vault"], "Speculum"))
    assert "path:arc/deviations.md" in idx2


# ------------------------------------------------------------------ the Bash trigger ----

def test_bash_heredoc_fires_the_push(world, tmp_path):
    """★ THE ROW'S FIRST CHANGE. Design 1 saw 16.9% of this corpus's file-producing tool calls
    because five writes in six are a heredoc or a redirect. The command IS the text: a
    `cat > report.md <<'EOF'` carries every citation the report makes, before the file exists."""
    lp = world["mod"]
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text(f"@{world['vault']}/Speculum/Kernel.md\n", encoding="utf-8")
    write(world["vault"] / "Speculum" / "Kernel.md", "# k\n")
    cmd = (f"cat > {repo}/ROW.md <<'EOF'\n"
           "The row closes q:CU-2026-09-01-ALPHA-1 tonight.\nEOF")
    out = lp.notice_bash("s-bash", cmd, str(repo))
    assert out is not None and "multi-path `git add`" in out


def test_bash_with_no_write_target_is_silent(world, tmp_path):
    """The negative control for the trigger itself: a command that reads is not a write, and a
    Bash arm that fired on every command would be a line after every tool call in the session."""
    lp = world["mod"]
    assert lp.notice_bash("s-bash2", "grep -rn 'q:CU-2026-09-01-ALPHA-1' .", str(tmp_path)) is None
    assert lp.notice_bash("s-bash3", "", str(tmp_path)) is None


def test_the_log_line_names_the_trigger_and_the_matched_token(world):
    """ts, sid, trigger, path, heading, match — SYSTEMYIELD-1's measurement surface, and the two
    fields this row added. A log that cannot say WHICH arm fired cannot answer the question the
    row was ignited for."""
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    assert lp.notice("s-log", target, "closing q:CU-2026-09-01-ALPHA-1", None)
    log = (world["state"] / "lesson_push.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1
    fields = log[0].split("\t")
    assert len(fields) == 6, fields
    ts, sid, trigger, path, heading, match = fields
    assert ts.startswith("20")
    assert sid == "s-log"
    assert trigger == "trigger:edit"
    assert path == "Speculum/Course.md"
    assert "git add" in heading
    assert match == "match:q:CU-2026-09-01-ALPHA-1"


# ------------------------------------------------------------------ the detector itself ----

def test_detector_reads_every_shape_it_claims_to(world, tmp_path):
    # `tmp_path`, not `/tmp`: on macOS `/tmp` is a symlink to `/private/tmp` and `common.expand`
    # resolves it, so a literal `/tmp` expectation fails for a reason that has nothing to do with
    # the detector.
    lp = world["mod"]
    d = tmp_path / "sh"
    d.mkdir()
    got = dict((str(p), how) for p, how in lp.bash_targets(
        f"echo hi > {d}/a.md; echo more >> {d}/b.md; echo x | tee -a {d}/c.md; "
        f"cp {d}/a.md {d}/d.md", str(d)))
    assert got.get(f"{d}/a.md") == "redirect"
    assert got.get(f"{d}/b.md") == "redirect-append"
    assert got.get(f"{d}/c.md") == "tee-append"
    assert got.get(f"{d}/d.md") == "cp"
    py = lp.bash_targets(f"python3 - <<'PY'\nopen(\"{d}/out.json\", \"w\").write(x)\nPY", str(d))
    assert [(str(p), h) for p, h in py] == [(f"{d}/out.json", "python-open")]


def test_detector_tracks_cd_across_segments(world, tmp_path):
    """★ FOUND IN THE 200-CALL AUDIT: nine of nineteen detections were `cd <worktree> && cat >
    <relative path>`. Without this the target resolved against the session's own root and named a
    file in another repo — a confident wrong answer, which for a matcher keyed on file identity is
    worse than silence. `gate.py` has tracked `cd` for the same reason since its door was built."""
    lp = world["mod"]
    got = lp.bash_targets(f"cd {tmp_path}/elsewhere && cat > notes.md <<'EOF'\nx\nEOF", "/tmp/base")
    assert [str(p) for p, _h in got] == [f"{tmp_path}/elsewhere/notes.md"]


def test_detector_skips_a_relative_target_whose_cwd_it_cannot_know(world, tmp_path):
    """After an unresolvable `cd` the cwd is unknown. A relative target is then SKIPPED, never
    guessed — and an absolute one is unaffected, which is what makes this a rule and not a wall."""
    lp = world["mod"]
    assert lp.bash_targets("cd $SOMEWHERE && echo x > notes.md", str(tmp_path)) == []
    assert [str(p) for p, _h in
            lp.bash_targets(f"cd $SOMEWHERE && echo x > {tmp_path}/notes.md",
                            str(tmp_path))] == [f"{tmp_path}/notes.md"]


def test_detector_refuses_a_shell_quoting_artefact(world):
    """★ ALSO FROM THE AUDIT, and it is why targets must LOOK like paths. The segment splitter
    breaks on `|`, which sits inside a quoted string as often as it is a pipe, so
    `grep -c '<<<<<<<\\|>>>>>>>'` arrived at the target slot as `>>>>>>>'` and was read as a
    redirect. Two commands in 200 produced a "target" no file system would accept."""
    lp = world["mod"]
    assert lp.bash_targets(r"grep -c '<<<<<<<\|>>>>>>>' conftest.py", "/tmp") == []
    assert lp.bash_targets("scripts/autorun.py note 'STOP at >=98%.'", "/tmp") == []
    assert lp.bash_targets("cmd > /dev/null 2>&1", "/tmp") == []
    assert lp.bash_targets("echo x > $OUT", "/tmp") == []


def test_a_blockquote_inside_a_heredoc_is_not_a_redirect(world, tmp_path):
    """A heredoc BODY is data. Markdown quotes a line with `>` at its start, and a parser that did
    not know it was inside a body would read that as a redirect to the next word."""
    lp = world["mod"]
    cmd = (f"cat > {tmp_path}/doc.md <<'EOF'\n> quoted line about something\n> another\nEOF")
    assert [str(p) for p, _h in lp.bash_targets(cmd, str(tmp_path))] == [f"{tmp_path}/doc.md"]


# ------------------------------------------------------------------ ordering ----

def test_the_most_specific_token_is_surfaced_first(world):
    """`CLASS_RANK`. When a write matches two lessons, the one matched by a queue-row ID beats the
    one matched by a filename: an id names one thing, a filename names a file many lessons
    mention."""
    lp = world["mod"]
    set_limit(world, lesson_push_max_per_fire=2)
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s-rank", target,
                    "closing q:CU-2026-09-01-ALPHA-1 while touching `lemma/normalize.py`", None)
    got = lines(out)
    assert len(got) == 2
    assert "git add" in got[0], got            # q: before path:
    assert "apostrophe" in got[1], got


# ------------------------------------------------------------------ the shipped decision ----

def test_ships_off_by_default(world, tmp_path, monkeypatch):
    """The SHIPPED limits keep it silent on a write it would otherwise match exactly.

    This is the decision `_lesson_push_enabled` records — now on TWO designs' gates — pinned so it
    cannot be flipped silently."""
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    assert shipped["lesson_push_enabled"] is False
    plain = tmp_path / "shipped-limits.json"
    plain.write_text(json.dumps(shipped), encoding="utf-8")
    monkeypatch.setenv("GEDAECHTNIS_LIMITS", str(plain))
    import limits                                              # noqa: PLC0415
    importlib.reload(limits)
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    assert lp.notice("s-off", target, "closing q:CU-2026-09-01-ALPHA-1", None) is None
    assert lp.notice_bash("s-off", f"cat > {target} <<'EOF'\nq:CU-2026-09-01-ALPHA-1\nEOF",
                          None) is None


# ------------------------------------------------------------------ negative controls ----

def test_region_with_no_lessons_is_silent(world):
    """A vault carrying none of the three lesson files says nothing at all."""
    lp = world["mod"]
    target = write(world["vault"] / "Empty" / "Position.md", "x")
    (world["vault"] / "Global" / "Errata.md").unlink()
    assert lp.notice("s2", target, "closing q:CU-2026-09-01-ALPHA-1", None) is None


def test_same_heading_twice_in_one_session_is_silent(world):
    """The dedupe is per (session, heading), and it survives a second file."""
    lp = world["mod"]
    text = "closing q:CU-2026-09-01-ALPHA-1"
    first = write(world["vault"] / "Speculum" / "Course.md", "x")
    second = write(world["vault"] / "Speculum" / "Aporia.md", "x")
    assert lp.notice("s3", first, text, None) is not None
    assert lp.notice("s3", second, text, None) is None
    # a DIFFERENT session is unaffected — the dedupe is per session, not global
    assert lp.notice("s4", second, text, None) is not None


def test_global_write_sees_global_lessons_only(world):
    """A write inside Global never names another region's lesson."""
    lp = world["mod"]
    target = write(world["vault"] / "Global" / "Nomos.md", "x")
    out = lp.notice("s5", target,
                    "`scripts/load_names.py` ran before q:CU-2026-09-01-ALPHA-1 closed", None)
    assert out is not None
    assert "Mnemosyne" not in out and "Speculum" not in out
    assert "Global/Errata.md#" in out


# ------------------------------------------------------------------ the title rules ----
# Kept from design 1, where they were measured: file titles were 5 of the first 20 surfaced lines.
# The two rules mask each other on an ordinary fixture — the reviewer's finding — so each has a
# fixture only the other cannot refuse.

def test_the_level_one_rule_holds_a_title_the_path_rule_cannot(world):
    """A level-1 heading whose words are NOT in the path — only the depth rule can refuse it."""
    lp = world["mod"]
    write(world["vault"] / "Speculum" / "Patterns.md",
          "# Introduction apostrophe tokenizer boundary overview\n\nSee `lemma/normalize.py`.\n\n"
          "## A real lesson about worktree isolation\n\nThe helper is `record_touched`.\n")
    headings = [h for _rel, h, _t in lp.candidates(world["vault"], "Speculum")]
    assert "Introduction apostrophe tokenizer boundary overview" not in headings
    assert any("worktree isolation" in h for h in headings)


def test_the_path_rule_holds_a_heading_the_level_one_rule_cannot(world):
    """A level-2 heading made only of the file's own path words — only the path rule can refuse
    it. `## Speculum Patterns` is a section header in a real role file, not a lesson."""
    lp = world["mod"]
    write(world["vault"] / "Speculum" / "Patterns.md",
          "# Title\n\n## Speculum Patterns\n\nSee `lemma/normalize.py`.\n\n"
          "## A real lesson about worktree isolation\n\nThe helper is `record_touched`.\n")
    headings = [h for _rel, h, _t in lp.candidates(world["vault"], "Speculum")]
    assert "Speculum Patterns" not in headings
    assert any("worktree isolation" in h for h in headings)


# ------------------------------------------------------------------ tautology suppression ----
# ★ Design 1's dominant noise class — 10 of the first 26 surfaced lines — and it bites HARDER
# under a citation matcher: a document quoting a lesson carries that lesson's own citations by
# construction, so the match would be perfect and the line would say nothing new.

def test_a_lesson_in_the_file_being_written_is_not_surfaced(world):
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Patterns.md", SPECULUM_PATTERNS)
    out = lp.notice("s-taut-1", target, "still about `lemma/normalize.py`", None)
    assert out is None or "Speculum/Patterns.md#" not in out


def test_a_heading_quoted_verbatim_is_not_surfaced(world):
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    quoted = "The apostrophe regime crosses the tokenizer boundary silently"
    out = lp.notice("s-taut-2", target,
                    f"see [[Speculum/Patterns#{quoted}]] — it is about `lemma/normalize.py`", None)
    assert out is None or quoted not in out


def test_a_heading_quoted_ACROSS_A_LINE_BREAK_is_not_surfaced(world):
    """★ Design 1's reviewer finding, reproduced against real vault content: markdown prose
    hard-wraps, and a newline where the heading has a space defeated the plain substring test — so
    the one shape a real citation actually takes was the one shape the suppression missed."""
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    wrapped = ("see [[Speculum/Patterns#The apostrophe regime crosses the tokenizer\n"
               "boundary silently]] — `lemma/normalize.py` again")
    assert "The apostrophe regime crosses the tokenizer boundary silently" not in wrapped, \
        "the fixture must actually contain the wrap, or this test passes for the wrong reason"
    out = lp.notice("s-taut-3", target, wrapped, None)
    assert out is None or "apostrophe regime crosses the tokenizer" not in out


def test_a_NON_tautological_match_is_still_surfaced(world):
    """The negative control for the suppression itself. A rule that silenced everything would pass
    all three tests above and be indistinguishable from the arm being broken."""
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s-taut-4", target, "touching `lemma/normalize.py` for the third time", None)
    assert out is not None and "apostrophe regime" in out


def test_is_tautological_branches_directly(world):
    lp = world["mod"]
    h = "The apostrophe regime crosses the tokenizer boundary silently"
    assert lp.is_tautological("Speculum/Patterns.md", "", "Speculum/Patterns.md", h) is True
    assert lp.is_tautological("Speculum/Course.md", f"x {h} y", "Speculum/Patterns.md", h) is True
    assert lp.is_tautological("Speculum/Course.md", "x The apostrophe regime crosses the\n"
                              "tokenizer boundary silently y", "Speculum/Patterns.md", h) is True
    assert lp.is_tautological("Speculum/Course.md", "nothing like it", "Speculum/Patterns.md",
                              h) is False


# ------------------------------------------------------------------ the per-session index ----

def test_the_index_is_cached_and_the_cache_is_stamped(world):
    """Built once per session, and invalidated BY THE FILES THEMSELVES. A session that edits a
    lesson file invalidates its own cache in the same act, so the arm can never cite a heading
    that has just been renamed away."""
    lp = world["mod"]
    vault = world["vault"]
    first = lp.index_for("s-idx", vault, "Speculum")
    assert "path:lemma/normalize.py" in first
    cache = lp._index_path("s-idx", "Speculum")
    assert cache.is_file(), "the index must reach the state dir, or it is rebuilt every write"
    # a cached read does not touch the lesson files at all
    calls = []
    orig = lp.candidates
    try:
        lp.candidates = lambda *a, **k: calls.append(1) or []
        assert lp.index_for("s-idx", vault, "Speculum") == first
        assert calls == [], "a valid cache must not re-read the corpus"
    finally:
        lp.candidates = orig
    # editing a lesson file invalidates it — the heading is gone from the next index
    write(vault / "Speculum" / "Patterns.md",
          "# Speculum Patterns\n\n## A different lesson\n\nAbout `lemma/other.py`.\n")
    again = lp.index_for("s-idx", vault, "Speculum")
    assert "path:lemma/normalize.py" not in again
    assert "path:lemma/other.py" in again


# ------------------------------------------------------------------ bounds ----

def test_per_fire_cap(world):
    set_limit(world, lesson_push_max_per_fire=1)
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    out = lp.notice("s6", target,
                    "closing q:CU-2026-09-01-ALPHA-1 and touching `lemma/normalize.py` and "
                    "`record_touched`", None)
    assert out is not None
    assert len(lines(out)) == 1


def test_per_session_cap_and_that_it_costs_nothing_once_reached(world, monkeypatch):
    """Past the session budget the arm is silent AND reads no file — the cap is checked first."""
    set_limit(world, lesson_push_max_per_session=2, lesson_push_max_per_fire=1)
    lp = world["mod"]
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    texts = ["closing q:CU-2026-09-01-ALPHA-1", "touching `lemma/normalize.py`",
             "the call to `record_touched`"]
    fired = [lp.notice("s7", target, t, None) for t in texts]
    assert sum(1 for f in fired if f) == 2
    called = []
    monkeypatch.setattr(lp, "index_for", lambda *a, **k: called.append(1) or {})
    assert lp.notice("s7", target, "closing q:CU-2026-09-01-ALPHA-1", None) is None
    assert called == [], "the session cap must be checked before any index is built or read"


def test_an_unwritable_state_dir_does_not_raise(world, monkeypatch):
    """A note must never cost a turn. The dedupe degrades to repeating, never to an exception."""
    lp = world["mod"]
    monkeypatch.setattr(lp, "_update", lambda *a, **k: False)
    target = write(world["vault"] / "Speculum" / "Course.md", "x")
    assert lp.notice("s8", target, "closing q:CU-2026-09-01-ALPHA-1", None)


def test_a_path_in_no_region_and_no_repo_is_silent(world, tmp_path):
    lp = world["mod"]
    stray = write(tmp_path / "elsewhere" / "notes.md", "x")
    assert lp.region_for(stray, None) is None


# ------------------------------------------------------------------ payload reading ----

def test_text_of_reads_all_three_tool_shapes():
    sys.path.insert(0, str(HOOKS))
    import lesson_push as lp                                   # noqa: PLC0415
    assert lp.text_of({"new_string": "a"}) == "a"
    assert lp.text_of({"content": "b"}) == "b"
    got = lp.text_of({"edits": [{"new_string": "c"}, {"new_string": "d"}]})
    assert "c" in got and "d" in got
    assert lp.text_of(None) == ""
    assert lp.text_of({}) == ""


# ------------------------------------------------------------------ limits parity ----

def test_limits_defaults_match_the_shipped_file():
    """The three keys exist in BOTH `rules/limits.json` and `hooks/limits.py`'s DEFAULTS, with the
    same values. `test_limits.py` pins the whole table; this names these three, so a key added to
    one side alone fails with the reason rather than as a dict diff.

    `lesson_push_min_score` is deliberately GONE: design 2's matcher is exact and has no score, so
    a threshold key would be a knob that does nothing — the measurement it carried is folded into
    `_lesson_push_enabled`, where both designs' gates are recorded together."""
    sys.path.insert(0, str(HOOKS))
    import limits                                              # noqa: PLC0415
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    for k in ("lesson_push_enabled", "lesson_push_max_per_fire", "lesson_push_max_per_session"):
        assert k in shipped, f"{k} missing from rules/limits.json"
        assert k in limits.DEFAULTS, f"{k} missing from limits.DEFAULTS"
        assert shipped[k] == limits.DEFAULTS[k], f"{k}: file {shipped[k]} != DEFAULTS {limits.DEFAULTS[k]}"
        assert f"_{k}" in shipped, f"{k} has no reasoning key beside it"
    assert "lesson_push_min_score" not in shipped
    assert "lesson_push_min_score" not in limits.DEFAULTS


def test_the_limits_prose_carries_BOTH_designs_measurements():
    """The row's requirement, pinned: a reader who finds this arm OFF must be able to see what was
    measured, twice, without leaving the file."""
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    prose = shipped["_lesson_push_enabled"]
    assert "LESSONPUSH-1" in prose and "LESSONPUSH-2" in prose
    for number in ("16.9%", "0.358", "50.0%", "0.708"):
        assert number in prose, f"the prose must carry {number}, the measurement it rests on"


# ------------------------------------------------------------------ the chore seam ----

def test_chore_do_write_emits_for_a_NON_vault_path(world, tmp_path, capsys):
    """The wiring's whole point: the re-occurrences LESSONYIELD-1 found were in REPO artifacts, so
    the push must speak for a path outside the vault. That branch returns early for every other
    arm of do_write, which is why it is tested rather than assumed."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text(f"@{world['vault']}/Speculum/Kernel.md\n", encoding="utf-8")
    write(world["vault"] / "Speculum" / "Kernel.md", "# k\n")
    target = write(repo / "notes.md", "x")
    import chore                                               # noqa: PLC0415
    importlib.reload(chore)
    chore.do_write({"session_id": "s9", "cwd": str(repo),
                    "tool_input": {"file_path": str(target),
                                   "new_string": "closing q:CU-2026-09-01-ALPHA-1 today"}})
    out = capsys.readouterr().out
    assert "multi-path `git add`" in out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_chore_do_bash_emits_and_prints_exactly_one_object(world, tmp_path, capsys):
    """★ The second trigger's wiring. `do_bash` had no `additionalContext` arm at all before this
    row; it must speak, and a hook may print exactly ONE JSON object, so the lock-release below it
    must stay silent."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text(f"@{world['vault']}/Speculum/Kernel.md\n", encoding="utf-8")
    write(world["vault"] / "Speculum" / "Kernel.md", "# k\n")
    import chore                                               # noqa: PLC0415
    importlib.reload(chore)
    chore.do_bash({"session_id": "s10", "cwd": str(repo),
                   "tool_input": {"command": f"cat > {repo}/ROW.md <<'EOF'\n"
                                             "closing q:CU-2026-09-01-ALPHA-1\nEOF"}})
    out = capsys.readouterr().out.strip()
    assert "multi-path `git add`" in out
    assert len([l for l in out.splitlines() if l.startswith("{")]) == 1, \
        "a hook may emit exactly one JSON object"
    payload = json.loads(out.splitlines()[-1])
    assert payload["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


def test_chore_do_bash_is_silent_for_a_reading_command(world, tmp_path, capsys):
    """The negative control at the seam, not only in the module: an ordinary read prints nothing."""
    import chore                                               # noqa: PLC0415
    importlib.reload(chore)
    chore.do_bash({"session_id": "s11", "cwd": str(tmp_path),
                   "tool_input": {"command": "grep -rn q:CU-2026-09-01-ALPHA-1 ."}})
    assert capsys.readouterr().out.strip() == ""


# ------------------------------------------------------------------ the reviewer's round ----
# Three findings, each pinned here. Two were controls that could not fail — a test that derives its
# fixture from the constant it is meant to pin, and a sanitizer with nothing exercising it — and
# one was a real defect in the shipped detector that the 200-call audit could not have found
# because the shape never occurred in it.

def test_the_furniture_cut_is_the_MEASURED_value_not_whatever_the_constant_holds(world):
    """★ `test_a_token_cited_by_too_many_entries_is_furniture` builds its fixture FROM
    `MAX_ENTRIES_PER_TOKEN`, so it proves the mechanism at any value and pins none. Raised to 999
    it stays green while the docstring's measured rationale silently becomes false. The constant is
    a MEASUREMENT — over 95% of tokens in three real regions name one or two entries, and the tail
    above eight is arc furniture — so the value is pinned here, beside the evidence for it."""
    lp = world["mod"]
    assert lp.MAX_ENTRIES_PER_TOKEN == 8
    src = (PLUGIN / "hooks" / "lesson_push.py").read_text(encoding="utf-8")
    assert "1:270  2:50" in src, "the measurement the cut rests on must stay beside the constant"


def test_the_index_cache_key_cannot_escape_the_state_dir(world):
    """★ The rebase added a sanitizer to `_index_path` so that the EXEMPT entry in
    `test_mutation_coverage.py` would be true BY CONSTRUCTION — and added no test, so the claim
    was one refactor away from being false again with nothing to say so."""
    lp = world["mod"]
    got = lp._index_path("s/../../etc", "../../../etc/passwd")
    assert got.parent == lp.config.state(), got
    assert ".." not in got.name and "/" not in got.name, got
    # the ordinary case still round-trips to a readable name
    plain = lp._index_path("abc", "Region/Sub")
    assert plain.name == "lesson-index-abc-Region_Sub.json", plain.name


def test_a_cd_inside_a_subshell_does_not_outlive_it(world, tmp_path):
    """★ THE REVIEWER'S REAL DEFECT, and it is the class the `cd` tracking exists to close: a
    `cd` inside `( … )` or `{ … ; }` is scoped to the group, and a flat tracker carried it past
    the closing paren — resolving a later relative write INTO ANOTHER REPO. The 200-call audit
    could not have caught it; no command in that sample used a group."""
    lp = world["mod"]
    base, other = tmp_path / "base", tmp_path / "other"
    base.mkdir(); other.mkdir()
    for cmd in (f"( cd {other} && cat foo ) && echo x > bar.md",
                f"{{ cd {other} ; cat foo ; }} ; echo x > bar.md"):
        got = [str(p) for p, _h in lp.bash_targets(cmd, str(base))]
        assert got == [f"{base}/bar.md"], (cmd, got)
    # the positive control beside it: an UNGROUPED `cd` still moves the cwd, or this test would
    # pass just as well against a tracker that had been deleted
    got = [str(p) for p, _h in lp.bash_targets(f"cd {other} && echo x > bar.md", str(base))]
    assert got == [f"{other}/bar.md"], got


def test_cd_dash_is_unknown_rather_than_a_directory_named_dash(world, tmp_path):
    """`cd -` returns to `$OLDPWD`, which this module does not track. Read literally it produced
    `/tmp/-/out.txt` — a confident wrong answer, the one outcome the detector's own rule forbids."""
    lp = world["mod"]
    assert lp.bash_targets(f"cd {tmp_path} && cd - && echo x > out.txt", str(tmp_path)) == []
    # bare `cd` is HOME, which IS knowable — so the rule is about what cannot be known, not about
    # every unusual `cd`
    got = [str(p) for p, _h in lp.bash_targets("cd && echo x > out.txt", str(tmp_path))]
    assert got and got[0].endswith("/out.txt") and str(tmp_path) not in got[0], got


# ================== DESIGN 3 — THE BOOT ARM (LESSONPUSH-3) ==================================
#
# The write arms are tested by TOKEN CLASS above, because the class is what varies there. The boot
# arm has no write, so what varies is the SELECTOR'S INPUTS — the lane's regions, its queue rows,
# its boot chain, the opener — and each one gets a control that fires and a control that must not.
# A criterion with only a positive control is a criterion no mutation can reach: switch it off and
# the other criteria still supply a line, the suite stays green, and the arm ships a rule that
# does nothing. That is the failure this block is shaped around.

QUEUE_CURSUS = """# cursus-atlas queue

- [ ] `ALPHA-1` — close out the header work | q:CU-2026-09-01-ALPHA-1 | effort:S
  - note: `lemma/normalize.py` is named only in this NOTE, never on the row line.
- [x] `DONE-1` — already finished, and it cites owner-ruling-headers-ascii-2026-08-01
"""

QUEUE_FOREIGN = """# mining-ops queue

- [ ] `BETA-1` — a foreign lane's open row citing `scripts/load_names.py` | q:MO-2026-09-01-BETA-1
"""


def boot_world(world, lane="CURSUS", prefixes=("Speculum", "Global")):
    """Give the tiny vault a queues directory and point config's topology at it."""
    cfg = world["tmp"] / "config.json"
    cfg.write_text(json.dumps({"topology": {"queues_dir": "Pharos/queues/regions"}}),
                   encoding="utf-8")
    os.environ["GEDAECHTNIS_CONFIG"] = str(cfg)
    import config, limits, common                              # noqa: PLC0415
    importlib.reload(config)
    importlib.reload(common)
    importlib.reload(limits)
    importlib.reload(world["mod"])
    q = world["vault"] / "Pharos" / "queues" / "regions"
    write(q / "cursus-atlas.md", QUEUE_CURSUS)
    write(q / "mining-ops.md", QUEUE_FOREIGN)
    set_limit(world, lesson_push_boot_enabled=True)
    return list(prefixes)


def boot_lines(out: str) -> list:
    return [l for l in (out or "").splitlines() if l.startswith("lesson: ")]


def test_boot_lane_regions_takes_regions_and_only_regions(world):
    """POSITIVE: a prefix naming a region with lesson files is a region.
    NEGATIVE: a file prefix, and a directory with no lesson file, are not."""
    lp = world["mod"]
    got = lp.lane_regions(["Speculum", "Global", "Empty", "00-Atlas.md", "Pharos/queues"])
    assert got == ["Global", "Speculum"], got


def test_boot_queue_files_are_this_lanes_only(world):
    """POSITIVE: the lane's own queue file. NEGATIVE: another lane's, in the same directory."""
    lp = world["mod"]
    prefixes = boot_world(world)
    got = [p.name for p in lp.queue_files("CURSUS", prefixes)]
    assert got == ["cursus-atlas.md"], got
    assert "mining-ops.md" not in got
    # and a lane whose name matches nothing takes nothing, rather than taking everything
    assert lp.queue_files("NOSUCHLANE", ["Empty"]) == []


def test_boot_queue_file_count_is_bounded(world):
    """The bound, with a test that can see it: nine matching queue files, eight taken. A bound
    nothing exercises is a line of code that could be deleted without a single test noticing."""
    lp = world["mod"]
    prefixes = boot_world(world)
    q = world["vault"] / "Pharos" / "queues" / "regions"
    for i in range(9):
        write(q / f"cursus-extra{i}.md", "- [ ] `X-{0}` | q:CU-2026-09-01-X-{0}\n".format(i))
    got = lp.queue_files("CURSUS", prefixes)
    assert len(got) == lp.BOOT_MAX_QUEUE_FILES == 8, [p.name for p in got]


def test_boot_open_rows_are_open_rows(world):
    """POSITIVE: the open checkbox line. NEGATIVE: the closed one, and the indented note."""
    lp = world["mod"]
    boot_world(world)
    text = lp.open_rows(world["vault"] / "Pharos" / "queues" / "regions" / "cursus-atlas.md")
    assert "ALPHA-1" in text
    assert "DONE-1" not in text
    assert "normalize.py" not in text                          # notes are not read, by design


def test_boot_fires_on_an_open_row_and_names_the_row_source(world):
    """The positive control for the whole arm: a lesson cited by an OPEN ROW is surfaced."""
    lp = world["mod"]
    prefixes = boot_world(world)
    out = lp.boot_notice("b-1", "CURSUS", prefixes, [], None)
    assert out is not None, "the positive control did not fire"
    assert any("multi-path `git add`" in l for l in boot_lines(out)), out


def test_boot_fires_on_the_boot_chain(world, tmp_path):
    """The second source, alone: no queue file matches, and a chain file carries the citation."""
    lp = world["mod"]
    prefixes = boot_world(world)
    chain = write(tmp_path / "chain" / "Kernel.md",
                  "the rule settled by owner-ruling-headers-ascii-2026-08-01\n")
    out = lp.boot_notice("b-chain", "NOSUCHLANE", ["Speculum", "Global"], [(chain, 60)], None)
    assert out is not None and any("latin-1" in l for l in boot_lines(out)), out


def test_boot_is_silent_with_no_structural_surface(world):
    """NEGATIVE CONTROL for the whole arm: a lane with no queue file and no boot chain has
    nothing to go on, and says nothing. Without this the arm could be firing on the corpus alone
    — which is what an always-on block looks like from the inside."""
    lp = world["mod"]
    boot_world(world)
    assert lp.boot_notice("b-quiet", "NOSUCHLANE", ["Speculum", "Global"], [], None) is None


def test_boot_is_silent_for_a_region_with_no_lessons(world):
    """A region with no lesson files contributes NOTHING OF ITS OWN — Global still applies to
    every region by construction, exactly as it does on the write side. And when Global has none
    either, the arm is silent rather than reaching into somebody else's region: the first assert
    without the second would have passed on a corpus that quietly included `Mnemosyne`."""
    lp = world["mod"]
    prefixes = boot_world(world)
    out = lp.boot_notice("b-empty", "CURSUS", ["Empty"], [], None)
    assert out is not None
    assert all(l.startswith("lesson: Global/") for l in boot_lines(out)), out
    (world["vault"] / "Global" / "Errata.md").unlink()
    assert lp.boot_notice("b-empty-2", "CURSUS", ["Empty"], [], None) is None


def test_boot_speaks_once_per_session(world):
    """NEGATIVE CONTROL: a second SessionStart in the same session — a resume, a compact — is
    silent. The block is paid for by every session; printing it twice is the one harm this arm
    can do to all of them at once.

    ★ THE SECOND BOOT IS GIVEN NEW LINES TO SAY, and without that this test was DECORATIVE:
    replayed at the same cap the dedupe alone silences it, so removing the once-per-session claim
    changed nothing and the suite stayed green. The cap is raised between the two calls, so the
    selector has lines it has not shown and only the claim can stop it."""
    lp = world["mod"]
    prefixes = boot_world(world)
    chain = write(world["tmp"] / "once" / "K.md",
                  "owner-ruling-headers-ascii-2026-08-01 and `lemma/normalize.py` and "
                  "`record_touched` and q:CU-2026-09-01-ALPHA-1 all at once\n")
    set_limit(world, lesson_push_boot_max_lines=1)
    first = lp.boot_notice("b-once", "CURSUS", prefixes, [(chain, 140)], None)
    assert first is not None and len(boot_lines(first)) == 1
    set_limit(world, lesson_push_boot_max_lines=5)
    assert lp.boot_notice("b-once", "CURSUS", prefixes, [(chain, 140)], None) is None


def test_boot_select_skips_what_was_already_shown(world):
    """The dedupe, reached directly. Through `boot_notice` the once-per-session claim gets there
    first, so this rule had no test that could see it — the pair above and this one are each
    other's controls."""
    lp = world["mod"]
    boot_world(world)
    cands = lp.boot_candidates(world["vault"], ["Speculum", "Global"])
    toks = lp.structural_tokens(
        [], [world["vault"] / "Pharos" / "queues" / "regions" / "cursus-atlas.md"], None)
    full = lp.boot_select(cands, toks, [], 10, {})
    assert full, "the positive control did not fire"
    first = full[0][1]
    again = lp.boot_select(cands, toks, [first], 10, {})
    assert first not in [h for _l, h, _t, _s in again]


def test_boot_ships_off(world):
    """The shipped decision, pinned. Its gates refused it: 1 held-out hit of 17 against a bar of
    3. Turning it on must go red here, so the person turning it on has to say so in the same act."""
    lp = world["mod"]
    prefixes = boot_world(world)
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"))
    assert shipped["lesson_push_boot_enabled"] is False
    set_limit(world, lesson_push_boot_enabled=False)
    assert lp.boot_notice("b-off", "CURSUS", prefixes, [], None) is None


def test_boot_respects_its_line_cap(world, tmp_path):
    """★ THE CAP NEEDS MORE CANDIDATES THAN THE CAP, or the assertion has no room and removing
    the cap changes nothing — this test was decorative until the chain below was added, and the
    mutation that deletes the cap came back green."""
    lp = world["mod"]
    prefixes = boot_world(world)
    chain = write(tmp_path / "cap" / "K.md",
                  "owner-ruling-headers-ascii-2026-08-01 and `lemma/normalize.py` and "
                  "`record_touched` and q:CU-2026-09-01-ALPHA-1 all at once\n")
    uncapped = lp.boot_notice("b-uncap", "CURSUS", prefixes, [(chain, 140)], None)
    assert uncapped is not None and len(boot_lines(uncapped)) > 1, uncapped
    set_limit(world, lesson_push_boot_max_lines=1)
    out = lp.boot_notice("b-cap", "CURSUS", prefixes, [(chain, 140)], None)
    assert out is not None and len(boot_lines(out)) == 1
    set_limit(world, lesson_push_boot_max_lines=0)
    assert lp.boot_notice("b-cap0", "CURSUS", prefixes, [(chain, 140)], None) is None


def test_boot_does_not_repeat_what_the_write_arm_already_showed(world):
    """The two arms share one dedupe set: a session shown a lesson after a write is not shown it
    again at its next boot, and the other way round."""
    lp = world["mod"]
    prefixes = boot_world(world)
    first = lp.boot_notice("b-dedupe", "CURSUS", prefixes, [], None)
    heads = [l.split("#", 1)[1] for l in boot_lines(first)]
    assert heads and all(h in lp.shown("b-dedupe") for h in heads)


def test_boot_source_precedence_prefers_the_session_over_the_lane(world, tmp_path):
    """A token in BOTH the boot chain and an open row is credited to the row: the chain is the
    same for every session in the lane, the row is what this session is working on."""
    lp = world["mod"]
    boot_world(world)
    chain = write(tmp_path / "chain2" / "K.md", "q:CU-2026-09-01-ALPHA-1 is in the chain too\n")
    qf = [world["vault"] / "Pharos" / "queues" / "regions" / "cursus-atlas.md"]
    toks = lp.structural_tokens([(chain, 40)], qf, None)
    assert toks.get("q:CU-2026-09-01-ALPHA-1") == "rows", toks.get("q:CU-2026-09-01-ALPHA-1")


def test_boot_opener_is_a_source_of_its_own(world):
    """The third source, which the shipped hook has only on a resume. It must be able to carry a
    selection by itself, or the `--boot-with-opener` arm of the replay measures nothing."""
    lp = world["mod"]
    boot_world(world)
    toks = lp.structural_tokens([], [], "today we re-run `scripts/load_names.py`")
    assert toks.get("name:load_names") == "opener" or any(
        v == "opener" for v in toks.values()), toks


def test_entries_of_keeps_its_shape_and_adds_a_date_on_request(world):
    """`with_meta` is ADDITIVE: two callers unpack three fields and must keep working."""
    lp = world["mod"]
    p = world["vault"] / "Global" / "Errata.md"
    plain = lp.entries_of(p)
    meta = lp.entries_of(p, with_meta=True)
    assert plain and all(len(e) == 3 for e in plain)
    assert meta and all(len(e) == 4 for e in meta)
    assert [e[:3] for e in meta] == plain
    dated = {h: d for _r, h, _t, d in meta}
    assert dated["HTTP headers (requests/urllib3) are latin-1 only"] == "2026-08-01"


def test_neg_date_sorts_newest_first_and_undated_last(world):
    lp = world["mod"]
    assert lp._neg_date("2026-09-01") < lp._neg_date("2026-08-01")
    assert lp._neg_date("2026-08-01") < lp._neg_date("")


def test_reoccurrence_counts_is_empty_without_a_configured_tsv(world):
    lp = world["mod"]
    assert lp.reoccurrence_counts() == {}


def test_reoccurrence_counts_reads_a_configured_tsv(world, tmp_path):
    """POSITIVE: a well-formed census TSV is counted. NEGATIVE: one without the columns this
    reads is {} rather than a wrong number — a tie-break computed from the wrong column would
    never announce itself."""
    lp = world["mod"]
    good = tmp_path / "census.tsv"
    good.write_text("file\theading\tverdict\n"
                    "Global/Errata.md\tA multi-path `git add` with ONE nonexistent path stages "
                    "NOTHING\tRE-OCCURRENCE\n"
                    "Global/Errata.md\tSomething else\tNO SIGNAL\n", encoding="utf-8")
    bad = tmp_path / "bad.tsv"
    bad.write_text("a\tb\tc\nx\ty\tz\n", encoding="utf-8")
    for path, expect in ((good, 1), (bad, 0)):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"lesson_reoccurrence": str(path)}), encoding="utf-8")
        os.environ["GEDAECHTNIS_CONFIG"] = str(cfg)
        import config                                          # noqa: PLC0415
        importlib.reload(config)
        importlib.reload(lp)
        assert len(lp.reoccurrence_counts()) == expect, path


def test_boot_never_raises(world, monkeypatch):
    """A broken organ must cost the facts block nothing. Same contract the write arms carry."""
    lp = world["mod"]
    prefixes = boot_world(world)
    monkeypatch.setattr(lp, "boot_candidates", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert lp.boot_notice("b-raise", "CURSUS", prefixes, [], None) is None


# `session_start.opener_text` lives with the boot arm's tests rather than in a file of its own:
# it exists only to feed this selector, and a reader asking what the opener source is reads these
# two tests beside the ones for the other two sources.

def test_opener_text_is_none_without_a_transcript(world):
    import session_start                                       # noqa: PLC0415
    assert session_start.opener_text({}) is None
    assert session_start.opener_text({"transcript_path": str(world["tmp"] / "nope.jsonl")}) is None


def test_opener_text_reads_the_first_user_message(world, tmp_path):
    """POSITIVE: the first user record's text. NEGATIVE: an assistant record before it is not
    mistaken for one — the transcript's first line is often not the user's."""
    import session_start                                       # noqa: PLC0415
    t = tmp_path / "t.jsonl"
    t.write_text(
        # ★ THE NEGATIVE RECORD CARRIES THE LITERAL BYTES `"user"` IN ANOTHER FIELD — here a
        # `role`, which a transcript record can and does carry beside a `type` that is not
        # `user`. The cheap line filter above the type check skips any line without those exact
        # bytes, so a record that merely contains the WORD user never reaches the type check at
        # all: two earlier versions of this test proved the FILTER, left the type check
        # decorative, and the mutation that deleted it came back green both times.
        json.dumps({"type": "system", "message": {"role": "user", "content": "not the opener"}})
        + "\n"
        + json.dumps({"type": "user", "message": {"content": [
            {"type": "text", "text": "You are the SEVENTEENTH builder"}]}}) + "\n"
        + json.dumps({"type": "user", "message": {"content": "a later one"}}) + "\n",
        encoding="utf-8")
    got = session_start.opener_text({"transcript_path": str(t)})
    assert got == "You are the SEVENTEENTH builder", got


# ---------------------------------------------------------------- the RANKING ----
#
# ★ THE WHOLE TIE-BREAK ORDER WAS UNTESTED, and the reviewer found it by mutation: each of
# `boot_select`'s five sort-key components could be replaced with a constant and all 69 tests
# still passed. It is the one piece of this arm whose correctness a published number depends on —
# gate B2's "selected at rank 6, outside the five-line block" is a statement about this order —
# so each component now gets a fixture that varies IT and holds the rest still.

def _rank_world(world, entries):
    """A corpus of `(file, heading, body)` in one region, and the index it produces.

    The citations here use ids of their own (`q:RK-…`) rather than the ones the class-by-class
    tests above use: Global is in EVERY candidate corpus by construction, so a fixture sharing a
    token with `GLOBAL_ERRATA` silently puts a third entry in the ranking and the assertion is
    then about the wrong pair. Found by the first run of these tests, which is what a positive
    control is for."""
    lp = world["mod"]
    for fname, blocks in entries.items():
        text = "# title\n\n" + "\n".join(f"## {h}\n\n{b}\n" for h, b in blocks)
        write(world["vault"] / "Rank" / fname, text)
    return lp.boot_candidates(world["vault"], ["Rank"])


def test_rank_source_beats_everything(world):
    """A lesson matched from an OPEN ROW outranks one matched from the boot chain, even when the
    chain's token is the more specific class. The session's work beats the lane's furniture."""
    lp = world["mod"]
    cands = _rank_world(world, {"Errata.md": [
        ("From the row", "touching `alpha/beta.py` today"),
        ("From the chain", "settled by q:RK-2026-09-01-RANK-1"),
    ]})
    toks = {"path:alpha/beta.py": "rows", "q:RK-2026-09-01-RANK-1": "boot"}
    got = [h for _l, h, _t, _s in lp.boot_select(cands, toks, [], 5, {})]
    assert got[0] == "From the row", got


def test_rank_class_beats_match_count(world):
    """Within one source, a queue-row id (the most specific class there is) outranks a bare
    filename — even when the filename entry matched on more distinct tokens."""
    lp = world["mod"]
    cands = _rank_world(world, {"Errata.md": [
        ("Specific", "closing q:RK-2026-09-01-RANK-1"),
        ("Vague", "about `notes.md` and `memo.md` and `draft.md`"),
    ]})
    toks = {"q:RK-2026-09-01-RANK-1": "rows", "file:notes.md": "rows",
            "file:memo.md": "rows", "file:draft.md": "rows"}
    got = [h for _l, h, _t, _s in lp.boot_select(cands, toks, [], 5, {})]
    assert got[0] == "Specific", got


def test_rank_match_count_beats_reoccurrence(world):
    """Same source, same class: the entry the session touched in MORE places comes first, and it
    comes first even when the other entry carries the higher re-occurrence count."""
    lp = world["mod"]
    cands = _rank_world(world, {"Errata.md": [
        ("Two hits", "about `alpha/one.py` and `alpha/two.py`"),
        ("One hit", "about `alpha/three.py`"),
    ]})
    toks = {"path:alpha/one.py": "rows", "path:alpha/two.py": "rows",
            "path:alpha/three.py": "rows"}
    reocc = {("Rank/Errata.md", "One hit"): 9}
    got = [h for _l, h, _t, _s in lp.boot_select(cands, toks, [], 5, reocc)]
    assert got[0] == "Two hits", got


def test_rank_reoccurrence_beats_recency(world):
    """Everything above tied: the lesson this vault has measured itself re-learning comes first,
    and it beats the newer entry. A census the installation has not run leaves this uniformly
    zero, which is what the next test holds still."""
    lp = world["mod"]
    cands = _rank_world(world, {"Errata.md": [
        ("Old but re-learned", "about `alpha/one.py`, written 2026-01-01"),
        ("New and never repeated", "about `alpha/two.py`, written 2026-09-01"),
    ]})
    toks = {"path:alpha/one.py": "rows", "path:alpha/two.py": "rows"}
    reocc = {("Rank/Errata.md", "Old but re-learned"): 3}
    got = [h for _l, h, _t, _s in lp.boot_select(cands, toks, [], 5, reocc)]
    assert got[0] == "Old but re-learned", got
    # and with NO census the same pair falls through to recency, the other way round
    got2 = [h for _l, h, _t, _s in lp.boot_select(cands, toks, [], 5, {})]
    assert got2[0] == "New and never repeated", got2


def test_rank_recency_beats_the_alphabet(world):
    """The last tie-break that carries meaning. Without it the order falls to the file and heading
    strings, which is deterministic and arbitrary — so this is the test that separates 'stable'
    from 'right'."""
    lp = world["mod"]
    cands = _rank_world(world, {"Errata.md": [
        ("AAA earlier", "about `alpha/one.py`, written 2026-01-01"),
        ("ZZZ later", "about `alpha/two.py`, written 2026-09-01"),
    ]})
    toks = {"path:alpha/one.py": "rows", "path:alpha/two.py": "rows"}
    got = [h for _l, h, _t, _s in lp.boot_select(cands, toks, [], 5, {})]
    assert got[0] == "ZZZ later", got
    # an UNDATED entry sorts last, not first — `_neg_date`'s sentinel, reached through the sort
    cands2 = _rank_world(world, {"Patterns.md": [
        ("Undated", "about `alpha/three.py`"),
        ("Dated", "about `alpha/four.py`, written 2026-01-01"),
    ]})
    toks2 = {"path:alpha/three.py": "rows", "path:alpha/four.py": "rows"}
    got2 = [h for _l, h, _t, _s in lp.boot_select(cands2, toks2, [], 5, {})]
    assert got2[0] == "Dated", got2


def test_structural_tokens_keeps_the_more_specific_source_whatever_the_order(world):
    """★ The precedence COMPARISON, not the call order that happens to agree with it. The three
    sources are read boot → rows → opener, which already ascends in specificity, so a plain
    'last one wins' would look correct from outside: the reviewer's mutation of the comparison
    came back GREEN. `rows` and `opener` rank EQUAL, so a token in both must stay with the first
    of them — which 'last wins' gets wrong, and which this test can see."""
    lp = world["mod"]
    boot_world(world)
    q = write(world["vault"] / "Pharos" / "queues" / "regions" / "cursus-atlas.md",
              "- [ ] `A` — touching `alpha/beta.py` | q:CU-2026-09-01-ALPHA-1\n")
    toks = lp.structural_tokens([], [q], "also touching `alpha/beta.py` today")
    assert toks.get("path:alpha/beta.py") == "rows", toks.get("path:alpha/beta.py")


def test_opener_read_is_bounded(world, tmp_path):
    """The bound, with a test that can see it: a transcript whose first user record sits past the
    cap returns None rather than reading an arbitrarily long file at boot."""
    import session_start                                       # noqa: PLC0415
    t = tmp_path / "long.jsonl"
    filler = json.dumps({"type": "system", "message": {"role": "user", "content": "x"}})
    t.write_text("\n".join([filler] * 250 + [json.dumps(
        {"type": "user", "message": {"content": "too late"}})]) + "\n", encoding="utf-8")
    assert session_start.opener_text({"transcript_path": str(t)}) is None


# ---------------------------------------------------------------- the boot-order instrument ----
#
# `eval/boot_prompt_order.py` exists because the claim it measures — that the SessionStart hook
# runs before the first prompt is stored, so the boot arm cannot read the opener — decided this
# design and lived only in a paragraph until the row's reviewer could not reproduce it.

def test_boot_prompt_order_reads_both_clocks(world, tmp_path):
    """POSITIVE: a hook row at 12:00:00 local and a first user record one second later is counted
    as 'the prompt did not exist yet'. NEGATIVE: a record a minute EARLIER is counted the other
    way, and a session with no hook row is skipped rather than assumed."""
    sys.path.insert(0, str(PLUGIN / "eval"))
    import boot_prompt_order as bpo                           # noqa: PLC0415
    importlib.reload(bpo)
    from datetime import timedelta, timezone                  # noqa: PLC0415
    log = tmp_path / "session.log"
    log.write_text("2026-09-20T12:00:00\tsid-after\tlane=X\n"
                   "2026-09-20T12:00:00\tsid-before\tlane=X\n", encoding="utf-8")
    hooks = bpo.hook_times(log)
    assert set(hooks) == {"sid-after", "sid-before"}
    # ★ BOTH SIDES AWARE. The log stamps local time with no offset and a transcript stamps UTC;
    # comparing them naively is wrong by the machine's whole UTC offset, which is how a reviewer
    # got a different number for this same claim.
    base = hooks["sid-after"].astimezone(timezone.utc)
    proj = tmp_path / "proj"
    proj.mkdir()
    def rec(ts):
        return json.dumps({"type": "user", "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                           "message": {"content": "hi"}}) + "\n"
    # Arithmetic, not `replace`: `replace(minute=…)` on a time whose minute is already 0 produces
    # the SAME instant, so the "before" arm silently became a second "after" and the test asserted
    # 1 == 2. A fixture that cannot express its own negative case is not a control.
    (proj / "sid-after.jsonl").write_text(rec(base + timedelta(seconds=1)), encoding="utf-8")
    (proj / "sid-before.jsonl").write_text(rec(base - timedelta(minutes=1)), encoding="utf-8")
    (proj / "sid-unknown.jsonl").write_text(rec(base), encoding="utf-8")
    out = bpo.measure(sorted(proj.glob("*.jsonl")), hooks)
    assert out["n_with_both_signals"] == 2, out
    assert out["first_prompt_after_hook"] == 1 and out["first_prompt_before_hook"] == 1, out
    assert out["skipped_no_hook_row"] == 1, out


def test_boot_prompt_order_skips_a_transcript_with_no_user_record(world, tmp_path):
    """UNCHECKED is a third outcome. A transcript the hook logged but that never got a prompt is
    counted as skipped, never as evidence in either direction."""
    sys.path.insert(0, str(PLUGIN / "eval"))
    import boot_prompt_order as bpo                           # noqa: PLC0415
    log = tmp_path / "s.log"
    log.write_text("2026-09-20T12:00:00\tsid-quiet\tlane=X\n", encoding="utf-8")
    proj = tmp_path / "p2"
    proj.mkdir()
    (proj / "sid-quiet.jsonl").write_text(
        json.dumps({"type": "assistant", "message": {"content": []}}) + "\n", encoding="utf-8")
    out = bpo.measure(sorted(proj.glob("*.jsonl")), bpo.hook_times(log))
    assert out["n_with_both_signals"] == 0 and out["skipped_no_user_record"] == 1, out
    assert out["share_after"] is None and out["median_delta_s"] is None, out
