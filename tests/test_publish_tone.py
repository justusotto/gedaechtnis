"""PUBLICTONE-1 — the publish check reads the public pages' wording (`tone()` in publish_check.py).

README.md and docs/*.md may not carry an absolute claim or a superlative. Three lists in
`rules/public-tone.json`: `refused` (never allowed), `needs` (allowed only beside its qualifier),
`counted` (a per-file ceiling). Each has a positive and a negative control on a planted tree, and
the real plugin tree must pass.
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
import pytest

PKG = Path(__file__).resolve().parents[1]
CHECK = PKG / "tools" / "publish_check.py"
sys.path.insert(0, str(PKG / "tools"))

import publish_check as pc  # noqa: E402

TONE = {"refused": ["impossible", "best", "sends nothing"], "needs": {"no network calls": "Claude Code"},
        "counted": {"never": {"README.md": 1}}}


def tree(tmp_path, readme="# p\n\nA plain page.\n", docs=None, tone=TONE, exclude=None):
    """A plugin root that passes every other line of the publish check, so the wording is the
    only thing a run can fail on."""
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "p", "displayName": "P", "version": "0.1.0", "description": "d", "license": "MIT"}\n')
    (root / "LICENSE").write_text("MIT License\n")
    (root / "hooks").mkdir()
    (root / "hooks" / "noop.py").write_text("pass\n")
    (root / "hooks" / "hooks.json").write_text(
        '{"hooks": {"Stop": [{"hooks": [{"type": "command", '
        '"command": "python3 -B \\"${CLAUDE_PLUGIN_ROOT}/hooks/noop.py\\""}]}]}}\n')
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n.DS_Store\n")
    (root / "rules").mkdir()
    (root / "docs").mkdir()
    (root / "README.md").write_text(readme)
    for name, text in (docs or {}).items():
        (root / "docs" / name).write_text(text)
    if tone is not None:
        (root / "rules" / "public-tone.json").write_text(tone if isinstance(tone, str) else json.dumps(tone))
    if exclude is not None:
        (root / "rules" / "publish-exclude.json").write_text(json.dumps({"exclude": exclude}))
    return root


def phrases(root):
    hits, err = pc.tone(root)
    assert err is None
    return [(page, phrase) for page, _line, phrase, _why in hits]


def test_NEGATIVE_a_plain_page_has_no_finding(tmp_path):
    assert phrases(tree(tmp_path)) == []


@pytest.mark.parametrize("sentence, phrase", [("Losing a note is impossible.", "impossible"),
                                              ("The BEST memory there is.", "best"),
                                              ("It sends nothing.", "sends nothing")])
def test_POSITIVE_a_refused_phrase_is_named_with_its_page_and_line(tmp_path, sentence, phrase):
    root = tree(tmp_path, docs={"a.md": "# a\n\nfine\n" + sentence + "\n"})
    hits, _ = pc.tone(root)
    assert [(h[0], h[1], h[2]) for h in hits] == [("docs/a.md", 4, phrase)]


def test_a_refused_word_inside_a_longer_word_is_not_a_finding(tmp_path):
    assert phrases(tree(tmp_path, readme="The bestiary is impossibly long.\n")) == []


def test_no_network_calls_POSITIVE_without_its_qualifier_and_NEGATIVE_with_it(tmp_path):
    bad = tree(tmp_path / "bad", readme="# p\n\nIt makes no network calls. Claude Code is separate.\n")
    assert phrases(bad) == [("README.md", "no network calls")]
    good = tree(tmp_path / "good", readme="# p\n\nIts scripts make no network calls; Claude Code itself does.\n")
    assert phrases(good) == []


def test_never_at_its_ceiling_passes_and_one_more_is_a_finding(tmp_path):
    at = tree(tmp_path / "at", readme="It never pushes.\n")
    assert phrases(at) == []
    over = tree(tmp_path / "over", readme="It never pushes.\nIt NEVER deletes.\n")
    hits, _ = pc.tone(over)
    assert len(hits) == 1 and hits[0][2] == "never" and "2 on this page, the ceiling is 1" in hits[0][3]


def test_a_page_with_no_ceiling_may_not_use_the_counted_word(tmp_path):
    assert phrases(tree(tmp_path, docs={"new.md": "It never asks.\n"})) == [("docs/new.md", "never")]


def test_a_page_on_the_leave_out_list_is_not_read_and_the_same_page_off_it_is(tmp_path):
    docs = {"dev.md": "This is impossible.\n"}
    assert phrases(tree(tmp_path / "a", docs=docs, exclude=["docs/dev.md"])) == []
    assert phrases(tree(tmp_path / "b", docs=docs)) == [("docs/dev.md", "impossible")]


def test_a_root_without_the_tone_file_or_without_a_manifest_is_skipped(tmp_path):
    assert pc.tone(tree(tmp_path / "a", tone=None)) is None
    root = tree(tmp_path / "b")
    (root / ".claude-plugin" / "plugin.json").rename(root / "plugin.json.moved")
    assert pc.tone(root) is None


@pytest.mark.parametrize("raw", ["not json", '{"refused": []}', '{"refused": "x", "needs": {}, "counted": {}}',
                                 '{"refused": [], "needs": {}, "counted": {"never": 3}}', '[]',
                                 '{"refused": [5], "needs": {}, "counted": {}}',
                                 '{"refused": [], "needs": {"a": 1}, "counted": {}}',
                                 '{"refused": [], "needs": {}, "counted": {"never": {"README.md": null}}}',
                                 '{"refused": [], "needs": {}, "counted": {"never": {"README.md": -1}}}',
                                 '{"refused": [], "needs": {}, "counted": {"never": {"README.md": true}}}'])
def test_an_unreadable_tone_file_is_a_failure_not_a_pass(tmp_path, raw):
    hits, err = pc.tone(tree(tmp_path, tone=raw))
    assert hits == [] and err and "could not be read" in err


def run(root):
    return subprocess.run([sys.executable, str(CHECK), "--root", str(root)], capture_output=True, text=True)


def test_WIRING_a_finding_makes_the_publish_check_exit_non_zero_and_a_plain_tree_says_PASS(tmp_path):
    bad = run(tree(tmp_path / "bad", readme="# p\n\nThe fastest and best.\n"))
    assert bad.returncode == 1, bad.stdout
    assert "wording: FAIL  README.md:3: `best`" in bad.stdout and "a wording line FAILED" in bad.stdout
    assert "publish check: clean" not in bad.stdout
    assert "checklist: FAIL" not in bad.stdout          # the wording is the ONLY failing line
    # The control: the same tree with a plain README exits 0, so the exit code above is the wording's.
    good = run(tree(tmp_path / "good"))
    assert good.returncode == 0, good.stdout + good.stderr
    assert "wording: PASS" in good.stdout and "publish check: clean" in good.stdout


@pytest.mark.parametrize("text", ["It sends\nnothing to anyone.\n", "It sends  nothing.\n", "It sends\u00a0nothing.\n",
                                  "It sends *nothing*.\n", "It **sends nothing**.\n", "It `sends nothing`.\n"])
def test_a_refused_phrase_is_found_across_a_line_break_spaces_and_emphasis(tmp_path, text):
    assert phrases(tree(tmp_path, docs={"a.md": "# a\n\n" + text})) == [("docs/a.md", "sends nothing")]


@pytest.mark.parametrize("text, phrase", [("It is _impossible_ to lose a note.\n", "impossible"),
                                          ("It is __impossible__.\n", "impossible"),
                                          ("It _sends nothing_ out.\n", "sends nothing"),
                                          ("This is _the best_ one.\n", "best")])
def test_a_refused_phrase_in_underscore_emphasis_is_found(tmp_path, text, phrase):
    assert phrases(tree(tmp_path, docs={"a.md": "# a\n\n" + text})) == [("docs/a.md", phrase)]


def test_no_network_calls_in_underscore_emphasis_still_needs_its_qualifier(tmp_path):
    assert phrases(tree(tmp_path, readme="# p\n\nIt makes _no network calls_ at all.\n")) == [("README.md", "no network calls")]


def test_a_heading_and_the_paragraph_under_it_are_not_one_phrase(tmp_path):
    assert phrases(tree(tmp_path, docs={"a.md": "## What it sends\n\nNothing leaves the machine by itself.\n"})) == []


def test_a_wrapped_phrase_is_reported_on_the_line_it_starts(tmp_path):
    hits, _ = pc.tone(tree(tmp_path, docs={"a.md": "# a\n\nfine\nIt sends\nnothing.\n"}))
    assert [(h[0], h[1]) for h in hits] == [("docs/a.md", 4)]


def test_no_network_calls_wrapped_over_two_lines_still_needs_its_qualifier(tmp_path):
    bad = tree(tmp_path / "bad", readme="# p\n\nIt makes no network\ncalls at all.\n")
    assert phrases(bad) == [("README.md", "no network calls")]
    good = tree(tmp_path / "good", readme="# p\n\nIts scripts make no network\ncalls; Claude\nCode itself does.\n")
    assert phrases(good) == []


def test_a_listed_word_joined_to_another_is_not_a_finding(tmp_path):
    root = tree(tmp_path, readme="A best-effort check; see `best_match` and `session_close_never`.\n")
    assert phrases(root) == []


def test_the_real_plugin_tree_passes_and_its_ceilings_are_exact():
    hits, err = pc.tone(PKG)
    assert err is None and hits == [], hits
    doc = json.loads((PKG / "rules" / "public-tone.json").read_text(encoding="utf-8"))
    for must in ("impossible", "sends nothing", "fetches nothing"):
        assert must in doc["refused"]
    assert doc["needs"] == {"no network calls": "Claude Code"}
    # A ceiling above the page's real count is room nobody decided to give.
    rx = pc._word_rx("never")
    for rel, ceiling in doc["counted"]["never"].items():
        page = PKG / rel
        if page.is_file():
            assert len(rx.findall(pc._plain(page.read_text(encoding="utf-8")))) == ceiling, rel
