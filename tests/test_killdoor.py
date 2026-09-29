"""KILLDOOR-1 — a kill whose target the shell computes is refused (`hooks/killdoor.py`).

Every case is a command STRING handed to the door; nothing here runs a kill. One POSITIVE (the door
speaks) and one NEGATIVE (the same verb, written safely, passes) per shape, so a mutation that blanks
one shape reddens the test that names it. The WIRING half drives `gate.py` as a subprocess the way
Claude Code calls it: WARN is a note and the call runs, a past `kill_deny_from` refuses inside a
marked repo and is a note outside one.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import killdoor as kd  # noqa: E402
import ruledoors as rd  # noqa: E402
from test_ruledoors import w, set_limits, note, denied, bash, ruledoors_log  # noqa: E402,F401

INCIDENT = 'pkill -P $(pgrep -o -f "nonexistent-xyz" 2>/dev/null || echo 1)'


def refuses(cmd: str) -> bool:
    return kd.check(cmd) is not None


# ---- one positive and one negative per shape ------------------------------------------------------

def test_the_incident_of_2026_09_28_is_refused():
    why = kd.check(INCIDENT)
    assert why and "pkill -P" in why


@pytest.mark.parametrize("cmd", ["kill $(cat run.pid)", "kill `cat run.pid`", 'kill "$PID"', "kill $PID",
                                 "kill -9 ${PIDS[0]}", "kill $!", "pkill $(echo name)", 'killall "$APP"',
                                 "kill <(echo 1)"])
def test_POSITIVE_a_computed_target_is_refused(cmd):
    assert "computed" in kd.check(cmd)


@pytest.mark.parametrize("cmd", ["kill 12345", "kill -9 12345", "kill -s TERM 12345 2>/dev/null",
                                 "kill -TERM 12345 67890", "/bin/kill -9 99999", "kill '12345'", "kill %1",
                                 "kill -l", "killall Preview", "pkill -x Preview"])
def test_NEGATIVE_a_literal_target_passes(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["pgrep foo | xargs kill", "pgrep foo | xargs -n1 kill -9",
                                 "ps -ax | awk '{print $1}' | xargs -I{} pkill {}", "echo x | xargs killall"])
def test_POSITIVE_xargs_into_a_killer_is_refused(cmd):
    assert "xargs" in kd.check(cmd)


def test_NEGATIVE_xargs_into_anything_else_passes():
    assert kd.check("ls | xargs echo") is None
    assert kd.check("find . -name x | xargs -P 4 grep kill") is None


@pytest.mark.parametrize("cmd", ["pkill -P 4242", "pkill -P4242 foo", "pkill -lP 4242", "pkill --parent 42",
                                 "pkill -9 -P 4242"])
def test_POSITIVE_pkill_P_is_refused_whatever_the_parent(cmd):
    assert "pkill -P" in kd.check(cmd) or "--parent" in kd.check(cmd)


def test_NEGATIVE_a_signal_name_with_a_P_in_it_is_not_pkill_P():
    for cmd in ("pkill -PIPE -x foo", "pkill -HUP -x foo", "pkill -SIGPROF -x foo", "pkill -9 foo"):
        assert kd.check(cmd) is None, cmd


@pytest.mark.parametrize("cmd", ["pkill -f pytest", "pkill -fl pytest", "pkill -9 -f 'python -m x'",
                                 "pkill --full x", "pgrep -f pytest", "pgrep -lf x", "pgrep -o -f x",
                                 "pgrep --full x"])
def test_POSITIVE_a_full_command_line_match_is_refused(cmd):
    assert "whole command line" in kd.check(cmd)


def test_NEGATIVE_pgrep_without_f_and_pkill_on_a_name_pass():
    for cmd in ("pgrep -x pytest", "pgrep -l Python", "pkill -x Preview", "pgrep -P 4242"):
        assert kd.check(cmd) is None, cmd


@pytest.mark.parametrize("cmd,word", [("kill 1", "launchd"), ("kill -9 0", "process group"),
                                      ("kill -9 -1", "NEGATIVE"), ("kill -- -4242", "NEGATIVE"),
                                      ("kill 4242 1", "launchd"), ("pkill -g 0 foo", "process group"),
                                      ("pkill -s 1", "launchd"), ("pkill -g -4 foo", "NEGATIVE")])
def test_POSITIVE_pid_0_pid_1_and_a_negative_pid_are_refused(cmd, word):
    assert word in kd.check(cmd)


def test_NEGATIVE_an_ordinary_pid_and_a_signal_number_pass():
    assert kd.check("kill -1 4242") is None        # `-1` here is SIGHUP, the target is 4242
    assert kd.check("kill -9 4242") is None
    assert kd.check("pkill -g 4242 foo") is None


@pytest.mark.parametrize("cmd", ["kill foo", "kill -9 run.pid"])
def test_POSITIVE_a_kill_target_that_is_not_digits_is_refused(cmd):
    assert "not a pid" in kd.check(cmd)


# ---- where the door reads -------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["sleep 5 & pkill -P 1", "echo $(pkill -f x)", "bash -c 'pkill -f foo'",
                                 'eval "pkill -f foo"', "sudo kill 1", "nohup kill -9 0", "timeout 5 kill $X",
                                 "true && kill $(cat p)", "bash <<EOF\npkill -f foo\nEOF",
                                 "x=$(pgrep -f foo); echo $x"])
def test_POSITIVE_a_kill_inside_a_job_substitution_prefix_or_heredoc_is_read(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["echo kill is a word", 'echo "pkill -f x"', "grep -n 'pgrep -f' notes.md",
                                 "cat > notes.md <<EOF\npkill -f foo\nEOF", "cmd & echo $! > run.pid",
                                 "cmd >/dev/null 2>&1 & kill 4242", "kill -9 99999 &", "git log --grep=kill"])
def test_NEGATIVE_a_kill_that_is_only_text_or_a_literal_pid_passes(cmd):
    assert kd.check(cmd) is None


def test_the_refusal_says_what_to_do_instead():
    text = kd.refusal(kd.check(INCIDENT))
    assert "captured when you LAUNCHED" in text and "let the run finish" in text and "owner" in text


# ---- the DOORS-2 machinery ------------------------------------------------------------------------

def test_the_kill_door_is_a_rule_door_and_the_facts_line_names_it():
    assert "kill" in rd.RULES and rd.RULES["kill"][2] == "q:CU-2026-09-28-KILLDOOR-1"
    assert "kill" in rd.facts_line()


def test_WIRING_kill_door_WARN_is_a_note_and_the_command_runs(w):
    res = bash(w, INCIDENT)
    assert not denied(res) and "KILL:" in note(res) and "kill_deny_from" in note(res)
    assert "warn\tkill" in ruledoors_log(w)


def test_WIRING_kill_door_a_past_date_refuses_inside_a_marked_repo(w):
    set_limits(w, {"kill_deny_from": "2026-09-01"})
    res = bash(w, INCIDENT)
    assert denied(res) and "KILL:" in res["permissionDecisionReason"]
    assert "q:CU-2026-09-28-KILLDOOR-1" in res["permissionDecisionReason"]
    assert "deny\tkill" in ruledoors_log(w)


def test_WIRING_kill_door_NEGATIVE_a_literal_pid_passes_silently(w):
    set_limits(w, {"kill_deny_from": "2026-09-01"})
    res = bash(w, "kill 12345")
    assert not denied(res) and "KILL:" not in note(res)


def test_WIRING_kill_door_outside_scope_is_a_note(w):
    set_limits(w, {"kill_deny_from": "2026-09-01"})
    res = bash(w, "pgrep -f foo", cwd=w["loose"])
    assert not denied(res) and "not enforced here" in note(res)


def test_WIRING_kill_door_off_says_nothing(w):
    set_limits(w, {"kill_deny_from": "2026-09-01", "kill_door": False})
    res = bash(w, INCIDENT)
    assert not denied(res) and "KILL:" not in note(res)


# ---- review round 1 (Opus 5.5 xhigh, REJECT): the shapes it found passing ------------------------

@pytest.mark.parametrize("cmd", ["for p in $(pgrep foo); do kill $p; done",
                                 "if pgrep -x foo >/dev/null; then pkill -P 1; fi", "(pkill -P 1)",
                                 "{ pkill -f foo; }", "! kill $PID", 'k() { kill "$1"; }; k 1', "time (kill $X)",
                                 "while read p; do kill $p; done < pids", "function k { kill $1; }"])
def test_R1_POSITIVE_a_kill_behind_a_keyword_bracket_or_function_header_is_read(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["(kill 12345)", "{ kill 12345; }", "if true; then echo kill; fi",
                                 "for f in *.md; do echo $f; done"])
def test_R1_NEGATIVE_the_same_brackets_around_a_literal_pid_pass(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ['sh -c "pkill -f x" &', "nohup sh -c 'pkill -f x' &", "bash -c 'pkill -f foo' _",
                                 "bash <<< 'pkill -f foo'"])
def test_R1_POSITIVE_a_shell_string_in_a_background_job_or_with_args_or_a_here_string_is_read(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["pkill -u someuser", "pkill -U 501", "pkill -v -x Finder", "pkill .", "pkill '.*'",
                                 "killall -u someuser", "killall -m .", "pkill --pgroup 0 foo", "pkill --session=1",
                                 "pkill --inverse -x Finder"])
def test_R1_POSITIVE_a_kill_that_selects_everything_is_refused(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["pkill -u someuser -x Preview", "killall -u someuser Preview", "pkill -F run.pid",
                                 "killall -m '^Preview$'"])
def test_R1_NEGATIVE_the_same_options_with_a_name_pass(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["\\kill $PID", "pk''ill -P 1", "PKILL -P 1", '"kill" $PID'])
def test_R1_POSITIVE_a_respelled_command_name_is_read(cmd):
    assert refuses(cmd)


def test_R1_a_door_error_while_wording_costs_no_other_door(w, monkeypatch):
    """The words and the mode are built inside the try: a raising refusal() logs and lets the call run."""
    import gate
    monkeypatch.setattr(kd, "refusal", lambda f: (_ for _ in ()).throw(RuntimeError("boom")))
    assert kd.check(INCIDENT)                           # the finding itself still stands
    src = (HOOKS / "gate.py").read_text()
    body = src[src.index("# The kill door (KILLDOOR-1)"):src.index("# The delete door (DELETEPOLICY-1)")]
    tried = body[body.index("try:"):body.index("except Exception")]
    assert "killdoor.refusal" in tried and "ruledoors.mode" in tried and "in_scope" in tried


# ---- round 2 (xhigh REJECT, 2026-09-28) and the seat's signal-0 decision ---------------------------

@pytest.mark.parametrize("cmd", ["git commit -m \"$(cat <<'EOF'\nThe door refuses `pkill -f x` now.\nEOF\n)\"",
                                 "gh pr create --body \"$(cat <<'EOF'\nsee `pkill -P 1`\nEOF\n)\"",
                                 "cat > notes.md <<'EOF'\nnever run `pkill -f x`\nEOF",
                                 'cat > notes.md <<"EOF"\nnever run $(pkill -f x)\nEOF'])
def test_R2_NEGATIVE_a_quoted_heredoc_body_is_text_the_shell_never_runs(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["cat > f <<EOF\n$(pkill -f x)\nEOF",
                                 "git commit -m \"$(cat <<EOF\nnow `pkill -P 1`\nEOF\n)\"",
                                 "x \"$(cat <<'EOF'\nhi\nEOF\n)\" \"$(pkill -f y)\"",
                                 "bash <<'EOF'\npkill -f x\nEOF"])
def test_R2_POSITIVE_an_unquoted_body_a_later_substitution_and_a_shell_heredoc_are_still_read(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ['case "$1" in\n  stop) kill $(cat run.pid) ;;\nesac',
                                 "case $a in x|stop) pkill -P 1;; esac", "case $a in stop) pkill -P 1;; esac",
                                 "case $a in (stop) kill $PID;; esac", "case $a in *) pkill -f x;; esac",
                                 "bash <<'EOF'\ncase $a in stop) pkill -P 1;; esac\nEOF"])
def test_R2_POSITIVE_a_kill_in_a_case_arm_is_read(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ['case "$1" in\n  stop) kill 12345 ;;\nesac', "case $a in *) echo kill;; esac",
                                 "(cd x; kill 12345)"])
def test_R2_NEGATIVE_a_case_arm_with_a_literal_pid_passes(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["kill -0 $PID", 'kill -0 "$pid"', "kill -s 0 $PID", "kill -n 0 $PID",
                                 "kill -0 $(cat run.pid)", "kill -0 -- $PID"])
def test_SEAT_NEGATIVE_signal_0_passes_with_any_target(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["kill -0 $PID; kill $PID", "kill -0 $PID && kill -9 $PID", "kill -s 9 $PID",
                                 "kill -n 15 $PID", "kill -9 $PID", "pkill -0 -f x", "pkill -0 -P 1",
                                 "killall -0 $APP"])
def test_SEAT_POSITIVE_any_other_signal_and_pkill_killall_0_keep_the_rule(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["bash -l -c 'pkill -f x'", "bash -e -c 'kill $PID'", "sh -x -c 'pkill -f x'",
                                 "bash --login -c 'pkill -f x'", "bash -o pipefail -c 'pkill -P 1'"])
def test_R2_POSITIVE_shell_options_before_c_are_stepped_over(cmd):
    assert refuses(cmd)


def test_R2_a_backslash_newline_joins_the_line():
    assert refuses("pkill -x foo \\\n-P 1")
    assert refuses('pkill -9 \\\n-f "x"')
    assert kd.check("kill -9 \\\n12345") is None


def test_R2_NEGATIVE_a_trailing_comment_is_not_a_target():
    assert kd.check("kill 12345 # stop server") is None
    assert refuses("kill $PID # stop server")


@pytest.mark.parametrize("cmd", ["pkill 'a*'", "pkill '.*a*'", "killall -m 'x*'"])
def test_R2_POSITIVE_a_pattern_matching_the_empty_name_matches_every_name(cmd):
    assert "every process" in kd.check(cmd)


def test_R2_c_and_L_take_no_value_d_does_and_killall_c_is_judged():
    assert refuses("pgrep -cf x")
    assert refuses("pkill -c -f x")
    assert refuses("pkill -L -f x")
    assert kd.check("pgrep -d , foo") is None
    assert refuses("killall -c $NAME")
    assert kd.check("killall -c staff Preview") is None


def test_R2_nesting_costs_linear_time_not_exponential():
    import time
    cmd = "kill 12345"
    for _ in range(16):
        cmd = "true & echo $(" + cmd + ")"
    t = time.monotonic()
    kd.check(cmd)
    assert time.monotonic() - t < 1.0


# ---- round 3 (xhigh REJECT, 2026-09-28): the controls for the fixes in a2dab9ce --------------------
# Every string below is handed to the door from Python; none is ever run in a shell.

@pytest.mark.parametrize("cmd", ["kill -0 -s 9 -1", "kill -s 0 -s KILL -1", "kill -0 -sKILL $PID",
                                 "bash -c 'kill -0 -s 9 -1'"])
def test_R3_POSITIVE_signal_0_with_a_second_signal_option_keeps_the_rule(cmd):
    """bash's `kill` reads every `-s`/`-n`/`-SIG` before the first pid: `kill -0 -s 9 -1` is SIGKILL
    to every process you own."""
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["kill -0 $PID", 'kill -0 "$pid"', "kill -s 0 $PID", "kill -n 0 $PID",
                                 "kill -0 -- $PID", "kill -s 0 -- $PID"])
def test_R3_NEGATIVE_signal_0_with_no_second_signal_option_passes(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["cat > f <<'EOF' && pkill -P 1\nx\nEOF", "cat > run.sh <<EOF; kill $(cat p)\nx\nEOF"])
def test_R3_POSITIVE_the_rest_of_a_heredoc_openers_line_is_read(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["cat > f <<'EOF' && echo done\nkill $PID\nEOF",
                                 "cat > f <<'EOF' && kill 12345\npkill -f x\nEOF", "cat > f <<'EOF'\npkill -f x\nEOF"])
def test_R3_NEGATIVE_a_quiet_opener_line_over_a_quoted_body_passes(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["grep x <<<'abc'; pkill -P 1", "wc -c <<<foo && pkill -P 1"])
def test_R3_POSITIVE_a_here_string_is_not_a_heredoc_so_the_next_command_is_read(cmd):
    assert refuses(cmd)


def test_R3_NEGATIVE_a_here_string_is_text_and_shellread_splits_after_it():
    import shellread
    assert kd.check("grep x <<<'kill'") is None
    assert kd.check("grep x <<<'abc'; kill 12345") is None
    assert shellread.segments("grep x <<<'abc'; rm -rf y") == ["grep x <<<'abc'", "rm -rf y"]
    assert shellread._heredoc_reader("bash <<<'EOF'\nx\nEOF") is None    # `<<<` never opens a body


@pytest.mark.parametrize("cmd", ['pkill "-P" 1', "pkill '-f' foo", "pkill \\-P 1", "pkill $'-P' 1",
                                 'pkill "--parent" 1', 'pgrep "-f" x', 'killall "-u" someuser', "killall \"-m\" '.*'"])
def test_R3_POSITIVE_a_quoted_or_escaped_option_is_the_option(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ['kill "-9" 12345', 'pkill "-x" Preview', "pkill $'-x' Preview", "pkill \\-x Preview",
                                 'killall "-u" someuser Preview'])
def test_R3_NEGATIVE_a_quoted_harmless_option_passes(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["case $a in stop ) kill $PID;; esac", "case $a in\n  stop ) pkill -P 1 ;;\nesac",
                                 "case $a in $s) kill $PID;; esac", "case $(cat f) in stop) kill $PID;; esac"])
def test_R3_POSITIVE_a_spaced_arm_a_variable_arm_and_a_substituted_subject_are_read(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["case $a in stop) kill 12345;; esac", "case $a in stop ) kill 12345;; esac",
                                 "case $(cat f) in stop) kill 12345;; esac"])
def test_R3_NEGATIVE_the_same_case_shapes_with_a_literal_pid_pass(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["kill ²; pkill -P 1", "kill " + "9" * 4301 + "; pkill -P 1",
                                 "pkill 'a{99999999999}'; pkill -P 1"])
def test_R3_POSITIVE_an_odd_first_part_does_not_silence_the_second(cmd):
    assert refuses(cmd)


def test_R3_digits_are_ascii_digits_and_no_pid_word_raises():
    assert "not a pid" in kd._pid_problem("²")          # `isdigit` is true for `²`; `int()` is not
    assert kd._pid_problem("9" * 4301) is None               # past int()'s 4300-digit limit, no ValueError
    assert "process group" in kd._pid_problem("00")
    assert "launchd" in kd._pid_problem("01")
    assert kd._matches_every_name("a{99999999999}") is False  # OverflowError in re, caught
    assert kd.check("kill " + "9" * 4301) is None
    assert kd.check("pkill 'a{99999999999}'") is None


def test_R3_one_unreadable_part_costs_only_itself(monkeypatch):
    real = kd.check_segment

    def boom(seg, _depth=0):
        if "boom" in seg:
            raise RuntimeError("boom")
        return real(seg, _depth)
    monkeypatch.setattr(kd, "check_segment", boom)
    assert refuses("echo boom; pkill -P 1")
    assert kd.check("echo boom; kill 12345") is None


@pytest.mark.parametrize("cmd", ["pgrep foo | xargs sudo kill -9", "pgrep foo | xargs env X=1 kill",
                                 "pgrep foo | xargs timeout 5 kill", "pgrep foo | xargs nohup kill",
                                 "pgrep foo | xargs sudo -u me kill"])
def test_R3_POSITIVE_xargs_through_a_prefix_into_a_killer_is_refused(cmd):
    assert "xargs" in kd.check(cmd)


@pytest.mark.parametrize("cmd", ["pgrep foo | xargs echo", "pgrep foo | xargs sudo echo", "ls | xargs env X=1 grep kill",
                                 "ls | xargs timeout 5 grep kill"])
def test_R3_NEGATIVE_xargs_through_a_prefix_into_anything_else_passes(cmd):
    assert kd.check(cmd) is None


def _nest(inner: str, times: int) -> str:
    for _ in range(times):
        inner = "echo $(" + inner + ")"
    return inner


def test_R3_POSITIVE_a_kill_nested_past_six_deep_is_refused():
    assert "nested more than six" in kd.check(_nest("pkill -P 1", 8))
    assert "nested more than six" in kd.check(_nest("pkill -P 1", 7))
    assert "pkill -P" in kd.check(_nest("pkill -P 1", 6))    # six deep is still read


def test_R3_NEGATIVE_deep_nesting_that_names_no_kill_passes():
    assert kd.check(_nest("echo hi", 8)) is None


def test_R3_NEGATIVE_a_comment_is_not_searched_for_substitutions():
    assert kd.check("echo hi # $(pkill -f x)") is None
    assert kd.check("kill 12345 # $(pkill -f x)") is None


@pytest.mark.parametrize("cmd", ["echo hi; pkill -f x # c", "echo a#$(pkill -f x)", "echo '# x' $(pkill -f x)"])
def test_R3_POSITIVE_a_hash_inside_a_word_or_quotes_is_not_a_comment(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["kill -0 -s 9 -1", "case $a in\n  stop ) pkill -P 1 ;;\nesac",
                                 "cat > f <<'EOF' && pkill -P 1\nx\nEOF", 'pkill "-P" 1'])
def test_WIRING_R3_the_round_3_shapes_are_refused_in_DENY_mode(w, cmd):
    set_limits(w, {"kill_deny_from": "2026-09-01"})
    res = bash(w, cmd)
    assert denied(res) and "KILL:" in res["permissionDecisionReason"]


# --- round 4 (xhigh REJECT, 1 must-fix + optional 4, 5, 6) ---------------------------------------

@pytest.mark.parametrize("cmd", ["cat > f.md <<EOF\n# $(pkill -P 1)\nEOF",
                                 "bash <<EOF\n# cleanup $(pkill -P 1)\necho done\nEOF",
                                 "cat > f <<EOF\nnote # $(kill $PID)\nEOF",
                                 "cat > r.md <<EOF\n## pids: $(pgrep -f server)\nEOF"])
def test_R4_POSITIVE_a_hash_in_an_unquoted_heredoc_body_hides_no_substitution(cmd):
    assert refuses(cmd)                          # bash expands `$( )` in the body whatever `#` stands before it


@pytest.mark.parametrize("cmd", ["cat > f.md <<'EOF'\n# $(pkill -P 1)\nEOF",
                                 "bash <<'EOF'\n# $(pkill -P 1)\nEOF",
                                 "echo hi # $(pkill -f x)",
                                 "cat > f <<EOF # a note $(pkill -f x)\nplain text\nEOF"])
def test_R4_NEGATIVE_a_quoted_body_and_a_first_line_comment_still_pass(cmd):
    assert kd.check(cmd) is None


@pytest.mark.parametrize("cmd", ["bash <<< \"echo hi\npkill -P 1\"", "sh <<< 'cd /tmp\nkill $PID'",
                                 "bash <<-EOF\n\tpkill -P 1\n\tEOF"])
def test_R4_POSITIVE_a_multi_line_here_string_and_a_tab_indented_heredoc_are_read(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["pgrep foo | xargs -i kill {}", "pgrep foo | xargs -l kill"])
def test_R4_POSITIVE_gnu_xargs_i_and_l_take_no_next_word(cmd):
    assert refuses(cmd)


def test_R4_NEGATIVE_a_plain_heredoc_keeps_a_tab_indented_line_as_body():
    import shellread
    assert kd.check("cat > f <<EOF\n\tEOF is not the end here\nEOF") is None
    assert shellread._heredoc_body("cat <<-EOF\n\tline\n\tEOF") == "\tline"
    assert shellread._heredoc_body("cat <<EOF\n\tEOF\nline\nEOF") == "\tEOF\nline"


# --- round 5 (xhigh REJECT, 3 must-fix + optional 7) ---------------------------------------------

@pytest.mark.parametrize("cmd", ["cat > notes.md <<EOF\nDon't forget\n$(pkill -P 1)\nEOF",
                                 "cat > f <<EOF\nit's $(kill $PID) time\nEOF",
                                 "cat > f <<EOF\nit's `pkill -P 1`\nEOF"])
def test_R5_POSITIVE_an_apostrophe_in_an_unquoted_heredoc_body_hides_nothing(cmd):
    assert refuses(cmd)


@pytest.mark.parametrize("cmd", ["git commit -m 'don'\\''t panic'; pkill -P 1", "echo it\\'s done; pkill -P 1",
                                 "echo \\\" && pkill -f x"])
def test_R5_POSITIVE_an_escaped_quote_outside_quotes_opens_nothing(cmd):
    assert refuses(cmd)


def test_R5_POSITIVE_amp_split_reads_an_escaped_quote_as_text():
    assert kd.amp_split("echo it\\'s & pkill -P 1") == ["echo it\\'s", "pkill -P 1"]
    assert refuses("echo it\\'s & pkill -P 1")


@pytest.mark.parametrize("cmd", ["git commit -F - <<'EOF'\nFix A & B\nRefuse `pkill -f x`.\nEOF",
                                 "cat > f.md <<'EOF'\nR&D note: `kill $PID` is refused\nEOF",
                                 "cat > f.md <<'EOF'\nDon't $(pkill -P 1)\nEOF",
                                 "echo it\\'s done; kill 12345",
                                 'echo "a\nb" # $(pkill -f x)'])
def test_R5_NEGATIVE_a_quoted_body_with_an_ampersand_and_escaped_quotes_pass(cmd):
    assert kd.check(cmd) is None


def test_R5_POSITIVE_a_heredoc_opener_line_is_still_cut_at_its_ampersand():
    assert refuses("cat > f <<'EOF' & pkill -P 1\nplain\nEOF")
    assert kd.amp_split("cat > f <<'EOF' & sleep 1\nA & B\nEOF") == ["cat > f <<'EOF'\nA & B\nEOF", "sleep 1"]


def test_R6_NEGATIVE_a_backslash_quoted_heredoc_body_is_text():
    assert kd.check("cat > f <<\\EOF\nRefuse `pkill -f x`\nEOF") is None


def test_R6_POSITIVE_a_backslash_heredoc_fed_to_a_shell_is_read():
    assert refuses("bash <<\\EOF\npkill -P 1\nEOF")


def test_R6_line_continuation_keeps_the_next_command_whole():
    import shellread
    assert shellread.segments("make build && \\\n  rm -rf dist") == ["make build", "rm -rf dist"]
    assert shellread.segments("cd d && \\\n  git push --force") == ["cd d", "git push --force"]


@pytest.mark.parametrize("cmd", ["cat > f <<\\EOF && pkill -P 1\nbody\nEOF", "cat > f <<\\EOF; kill $(cat p)\nbody\nEOF",
                                 "cat <<\\'EOF' x'; pkill -P 1", "cat <<\\EOF | bash\npkill -f x\nEOF"])
def test_R7_POSITIVE_a_kill_around_a_backslash_heredoc_is_read(cmd):
    assert refuses(cmd)


def test_R7_NEGATIVE_a_backslash_heredoc_body_with_an_ampersand_is_text():
    assert kd.check("cat > notes.md <<\\EOF\nmake & pkill -P 1 broke it\nEOF") is None


@pytest.mark.parametrize("cmd", ["cat > f <<-\\EOF\n\tRefuse `pkill -f x`\n\tEOF", "cat > f <<-\\EOF\n\tsleep 1 & pkill -P 1\n\tEOF",
                                 "cat > f <<\\EOFé\n`pkill -f x`\nEOFé"])
def test_R8_NEGATIVE_a_tab_or_non_ascii_backslash_heredoc_body_is_text(cmd):
    assert kd.check(cmd) is None
