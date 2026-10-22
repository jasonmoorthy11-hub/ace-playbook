"""The tokenizer (compile/shell.py) and what the detectors see through it: speed, evasions, wrappers."""

import time

import pytest

from ace_playbook.compile import catalogue
from ace_playbook.compile import events as ev
from ace_playbook.compile import state as state_mod


def pre(command):
    return {"hook_event_name": "PreToolUse", "session_id": "s-1", "tool_name": "Bash", "tool_use_id": "t",
            "tool_input": {"command": command}}


def fires_for(cmd):
    fires, _, _ = catalogue.evaluate(ev.event_from_hook(pre(cmd)), state_mod.new_state("s-1"), strict=True)
    return {f.detector: f.count for f in fires}


def rm_fires(cmd):
    return fires_for(cmd).get("rm_rf_nonscratch") == 1


TIME_BUDGET = 0.5   # seconds per hostile input: ≈ 0.03 s on a 2024 Mac, 0.12 s seen on the CI runner (≥ 3× headroom over CI, D-147 rule)

SLOW_INPUTS = {
    "unterminated-dq-with-escapes": '"' + '\\"' * 32767,
    "heredoc-32k-delimiter": "<<" + "a" * 32768,
    "heredoc-256-delimiter": ("<<" + "a" * 256 + "\ncat\n") * 3,
    "many-openers-no-terminator": "<<a\n" * 16000,
    "heredoc-repeated": "cat <<EOF\nx\n" * 5000,
    "dq-backslash-newline": '"\\\n' * 20000,
    "single-quotes": "'" * 60000,
    "ansi-quotes": "$'" * 30000,
    "comments": "a #b\n" * 12000,
    "arithmetic-openers": "((1<<a\n" * 9000,
    "brace-flood": "{ " * 32000,
    "bang-flood": "! " * 32000,
    "64kb-padding": "x " * 40000,
}


@pytest.mark.parametrize("name", sorted(SLOW_INPUTS))
def test_evaluate_is_fast_on_hostile_commands(name):
    e = ev.event_from_hook(pre(SLOW_INPUTS[name]))
    t0 = time.monotonic()
    catalogue.evaluate(e, state_mod.new_state("s-1"), strict=True)
    assert time.monotonic() - t0 <= TIME_BUDGET, name


def test_segments_are_computed_once_per_tool_use(monkeypatch):
    calls = []
    real = ev.command_segments
    monkeypatch.setattr(ev, "command_segments", lambda c: (calls.append(c), real(c))[1])
    catalogue.evaluate(ev.event_from_hook(pre("git commit -m m && rm -rf /w/src")), state_mod.new_state("s-1"), strict=True)
    assert len(calls) == 1


@pytest.mark.parametrize("cmd", ["cat <<A\nA\n" * 33 + "rm -rf /w/src", "cat <<A\n" * 33 + "rm -rf /w/src",
                                 "cat <<<x; " * 33 + "rm -rf /w/src"])
def test_the_tail_after_many_openers_still_fires(cmd):
    assert rm_fires(cmd)


def test_cap_marks_truncated_and_emits_unparseable():
    cmd = "x " * (ev.MAX_CMD // 2) + "; rm -rf /w/src"
    e = ev.event_from_hook(pre(cmd))
    assert e.tool_uses[0].truncated is True and len(e.tool_uses[0].input["command"]) == ev.MAX_CMD
    got = fires_for(cmd)
    assert got.get("unparseable") == 1 and "rm_rf_nonscratch" not in got
    assert "unparseable" not in fires_for("ls -la")
    assert (catalogue.CATALOGUE["unparseable"].strength, catalogue.CATALOGUE["unparseable"].compilable) == ("weak", "observe")


def test_heredoc_removal_strips_only_the_body():
    assert rm_fires("cat <<EOF && rm -rf /w/src\nbody\nEOF")
    assert ev.command_segments("cat <<'EOF'\nrm -rf x\nEOF\nls") == [["cat"], ["ls"]]
    assert ev.command_segments("cat <<EOF\nEOF\nls") == [["cat"], ["ls"]]


@pytest.mark.parametrize("cmd", [
    'echo "<<EOF"\nrm -rf /w/src\nEOF',            # opener inside quotes
    "cat <<<EOF\nrm -rf /w/src\nEOF",               # here-string
    "# <<EOF\nrm -rf /w/src\nEOF",                  # inside a comment
    "echo ok # note <<EOF\nrm -rf /w/src\nEOF",     # after a word-initial #
    "echo $((1<<2))\nrm -rf /w/src\n2",             # arithmetic shift
    "(( x = 1 << 2 ))\nrm -rf /w/src\n2",
    "echo x\\<<EOF\nrm -rf /w/src\nEOF",            # escaped
])
def test_a_shift_comment_or_escape_is_not_a_heredoc(cmd):
    assert rm_fires(cmd), cmd


def test_a_real_heredoc_still_hides_its_body():
    assert not rm_fires("cat <<EOF\nrm -rf /w/src\nEOF")
    assert not rm_fires("cat <<-'EOF'\n\trm -rf /w/src\n\tEOF")


def test_comments_are_dropped_only_when_word_initial():
    assert not rm_fires("echo ok # rm -rf /w/src")
    assert not rm_fires("# rm -rf /w/src")
    assert rm_fires("echo a#b; rm -rf /w/src") and rm_fires("echo $#; rm -rf /w/src")
    assert rm_fires("echo 'a # b'\nrm -rf /w/src") and rm_fires('echo "#"; rm -rf /w/src')


@pytest.mark.parametrize("cmd", ["true & rm -rf /w/src", "{ rm -rf /w/src; }", "if true; then rm -rf /w/src; fi",
                                 "! rm -rf /w/src", "while true; do rm -rf /w/src; done", "a || { rm -rf /w/src; }"])
def test_shell_syntax_prefixes_and_background_ampersand_are_split(cmd):
    assert rm_fires(cmd), cmd


def test_redirect_ampersands_are_not_separators():
    assert ev.command_segments("ls 2>&1 | wc") == [["ls", "2>&1"], ["wc"]]
    assert ev.command_segments("ls &> out") == [["ls", "&>", "out"]]
    assert ev.command_segments("a && b") == [["a"], ["b"]]


@pytest.mark.parametrize("cmd", [">/dev/null rm -rf /w/src", "2>/dev/null rm -rf /w/src", "> /dev/null rm -rf /w/src",
                                 "&>/dev/null rm -rf /w/src", ">>log 2>&1 rm -rf /w/src", "<in rm -rf /w/src"])
def test_leading_redirections_are_skipped(cmd):
    assert rm_fires(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "/bin/rm -rf /w/src", "\\rm -rf /w/src", "env rm -rf /w/src", "nice rm -rf /w/src", "xargs rm -rf /w/src",
    "doas rm -rf /w/src", "busybox rm -rf /w/src", "command rm -rf /w/src", "exec rm -rf /w/src",
    "sudo rm -rf /w/src", "env FOO=1 /usr/bin/rm -rf /w/src", "xargs -0 rm -rf /w/src",
    "sudo -u root rm -rf /w/src", "sudo -g wheel -u root rm -rf /w/src", "nice -n 10 rm -rf /w/src",
    "timeout 5 rm -rf /w/src", "timeout -s KILL 5 rm -rf /w/src", "/usr/bin/env rm -rf /w/src",
    "env -u FOO -C /w rm -rf /w/src", "xargs -I {} rm -rf /w/src", "xargs -n 1 -P 4 rm -rf /w/src",
    "doas -u root rm -rf /w/src", "stdbuf -o L rm -rf /w/src", "ionice -c 2 -n 7 rm -rf /w/src",
    "chroot /r rm -rf /w/src", "\\sudo \\nice rm -rf /w/src",
])
def test_rm_wrappers_and_paths_are_seen(cmd):
    assert rm_fires(cmd), cmd


@pytest.mark.parametrize("cmd", ["\\r\\m -rf /w/src", "r\\m -rf /w/src", "$'rm' -rf /w/src", "$'r\\x6d' -rf /w/src",
                                 "$\"rm\" -rf /w/src", "r\"\"m -rf /w/src", "'r'm -rf /w/src"])
def test_obfuscated_command_words_are_seen(cmd):
    assert rm_fires(cmd), cmd


def test_ansi_c_quoting_is_a_quoted_word():
    assert ev.command_segments("echo $'a b' c") == [["echo", "Q", "c"]]
    assert ev.command_segments("echo $'it\\'s' ok") == [["echo", "Q", "ok"]]
    assert ev.command_segments("echo $'unterminated") == [["echo", "$'unterminated"]]


def test_tmpdir_forms_other_than_the_plain_variable_are_not_scratch():
    assert not rm_fires("rm -rf ${TMPDIR}/x") and not rm_fires("rm -rf $TMPDIR/x")
    for target in ("${TMPDIR:-/w/src}/x", "${TMPDIR:=/w}/x", "${TMPDIR#a}/x", "$TMPDIRX/x"):
        assert rm_fires("rm -rf " + target), target


# ------------------------------------------------------------------ escapes: is this character escaped?
def test_an_escaped_separator_does_not_make_a_comment():
    assert rm_fires("echo\\ # a note; rm -rf /w/src")                       # `echo\ #` is one word
    assert ev.command_segments("echo\\ # x") == [["echo\\", "#", "x"]]
    assert not rm_fires("echo ok  # a note; rm -rf /w/src")                 # a real comment still is


def test_two_backslashes_leave_the_separator_unescaped():
    assert not rm_fires("echo \\\\ # a note; rm -rf /w/src")                 # `\\` is a literal backslash: a comment


def test_an_escaped_dollar_is_not_ansi_c_quoting():
    assert ev.command_segments("echo \\$'a b' c") == [["echo", "\\$Q", "c"]]     # \$ literal, then a plain '..'
    assert ev.command_segments("echo \\\\$'a b' c") == [["echo", "\\\\Q", "c"]]  # \\ then a real $'..'
    assert ev.command_segments('echo \\$"a b" c') == [["echo", "\\$Q", "c"]]


def test_a_double_backslash_before_the_opener_is_still_a_heredoc():
    assert not rm_fires("cat \\\\<<EOF\nrm -rf /w/src\nEOF")
    assert rm_fires("cat \\<<EOF\nrm -rf /w/src\nEOF")
    assert rm_fires("cat \\\\\\<<EOF\nrm -rf /w/src\nEOF")                   # three: escaped again


def test_arithmetic_depth_ignores_parentheses_in_quotes_and_resets_at_closing():
    assert not rm_fires('echo "((" ; cat <<EOF\nrm -rf /w/src\nEOF')
    assert not rm_fires("echo $((1<<2)); cat <<EOF\nrm -rf /w/src\nEOF")
    assert rm_fires("echo $((1<<2))\nrm -rf /w/src\n2")


def test_backslash_newline_joins_the_line():
    assert rm_fires("rm -r\\\nf /w/src")
    assert ev.command_segments("echo a \\\nb") == [["echo", "a", "b"]]
    assert ev.command_segments("echo a \\\\\nb") == [["echo", "a", "\\\\"], ["b"]]   # even run: a real newline
    assert ev.command_segments("cat <<EOF \\\nmore\nbody\nEOF\nls") == [["cat", "more"], ["ls"]]


def test_a_comment_ends_at_its_newline_even_after_a_backslash():
    assert rm_fires("# note \\\nrm -rf /w/src")


@pytest.mark.parametrize("name,cmd", [("backslash-run-then-opener", "\\" * 60000 + "<<a\n"),
                                      ("backslash-newlines", "\\\n" * 30000),
                                      ("escaped-hashes", "a\\ #" * 15000),
                                      ("escaped-dollar-quotes", "\\$'" * 20000)])
def test_escape_handling_is_linear(name, cmd):
    t0 = time.monotonic()
    catalogue.evaluate(ev.event_from_hook(pre(cmd)), state_mod.new_state("s-1"), strict=True)
    assert time.monotonic() - t0 <= TIME_BUDGET, name


# ------------------------------------------------------------------ where the opener's command line really ends
def test_a_comment_on_the_opener_line_eats_its_trailing_backslash():
    # bash: `# \` is a comment, so line 1 ends there; the body is empty (line 2 is the terminator) and rm runs
    assert rm_fires("cat <<EOF # \\\nEOF\nrm -rf /w/src\nEOF")
    assert rm_fires("cat <<EOF # note\nEOF\nrm -rf /w/src\nEOF")


def test_a_real_continuation_on_the_opener_line_still_extends_it():
    assert not rm_fires("cat <<EOF \\\n  more\nrm -rf /w/src\nEOF")             # body starts after `more`


def test_a_quoted_continuation_on_the_opener_line_is_a_real_continuation():
    # inside double quotes backslash-newline is removed, so the line ends after `b"`; the next line is the terminator
    assert rm_fires('cat <<EOF "a \\\nb"\nEOF\nrm -rf /w/src\nEOF')
    assert not rm_fires('cat <<EOF "a \\\nb"\nrm -rf /w/src\nEOF')


def test_a_quoted_newline_ends_the_command_line_only_after_the_quote():
    assert not rm_fires('cat <<EOF "a\nb"\nrm -rf /w/src\nEOF')


def test_two_heredocs_on_one_line_take_their_bodies_in_order():
    assert ev.command_segments("cat <<A <<B\nx\nA\ny\nB\nls") == [["cat"], ["ls"]]
    assert rm_fires("cat <<A <<B\nx\nA\ny\nB\nrm -rf /w/src")


def test_an_unterminated_heredoc_hides_nothing():
    assert rm_fires("cat <<EOF\nrm -rf /w/src")


# ------------------------------------------------------------------ arithmetic depth by parenthesis level
def test_parentheses_in_quotes_do_not_open_arithmetic():
    assert not rm_fires('echo "((" ; cat <<EOF\nrm -rf /w/src\nEOF')


def test_nested_parentheses_inside_arithmetic_keep_it_open_until_the_level_returns():
    assert rm_fires("echo $(( (1<<2) ))\nrm -rf /w/src\n2")                 # the shift is inside, not a heredoc
    assert not rm_fires("echo $(( (1) )) ; cat <<EOF\nrm -rf /w/src\nEOF")   # closed again: a real heredoc


def test_a_subshell_is_not_arithmetic():
    assert not rm_fires("( cat <<EOF\nrm -rf /w/src\nEOF\n)")
    assert not rm_fires("( ( cat <<EOF\nrm -rf /w/src\nEOF\n) )")


def test_arithmetic_spanning_lines_stays_open():
    assert rm_fires("echo $((1+\n1<<2))\nrm -rf /w/src\n2")
