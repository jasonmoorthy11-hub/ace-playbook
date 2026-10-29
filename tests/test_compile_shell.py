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


# ------------------------------------------------------------------ v2: command_stream and canonical_targets (compile/resolve.py)
from ace_playbook.compile import detectors as det, resolve, shell   # noqa: E402

STREAM_CORPUS = [
    "ls", "rm -rf /w/src", "cd /w && rm -rf build; echo done", "echo start\nrm -rf /w/src", "sudo -u root rm -rf /w/src",
    "FOO=1 rm -rf /w/src", "cat <<EOF\nrm -rf /w/src\nEOF\nls", "echo $(( (1+2) << 3 ))\nrm -rf /w/src", "f() { rm -rf /w/src; }; f",
    "a | b & c", "echo 'x' | xargs rm -rf", "rm -rf /w/a /w/b # comment", "if true; then rm -rf /w/x; fi", "x=$'a\\tb'; rm -rf /w/x",
]


@pytest.mark.parametrize("cmd", STREAM_CORPUS)
def test_command_stream_carries_exactly_the_segments_command_segments_returns(cmd):
    events, _quotes = shell.command_stream(cmd)
    assert [f for sep, raw in events if not sep and (f := shell.front(raw))] == shell.command_segments(cmd)


def test_command_stream_masks_exactly_what_command_segments_masks():
    assert shell.command_segments('rm -rf "${D}/x"') == [["rm", "-rf", "Q"]]
    assert shell.command_stream('rm -rf "${D}/x"') == ([("", ["rm", "-rf", "\x000\x00"])], [('"', "${D}/x")])
    assert shell.command_stream('rm -rf "/tmp/x"')[0] == [("", ["rm", "-rf", "/tmp/x"])]


def test_command_stream_events_separators_and_holes():
    events, quotes = shell.command_stream("cd /tmp/x && D='a b' ; echo \"p q\"\nls | wc")
    assert events == [("", ["cd", "/tmp/x"]), ("&&", []), ("", ["D=\x000\x00"]), (";", []), ("", ["echo", "\x001\x00"]),
                      ("\n", []), ("", ["ls"]), ("|", []), ("", ["wc"])]
    assert quotes == [("'", "a b"), ('"', "p q")]


def test_command_stream_keeps_single_quoted_dollar_as_a_hole_and_a_double_quoted_one_inline():
    assert shell.command_stream("echo '$D' \"$D\"") == ([("", ["echo", "\x000\x00", "$D"])], [("'", "$D")])


def test_command_stream_ansi_quoted_words_and_nul_bytes():
    events, quotes = shell.command_stream("echo $'a b'\x00c")
    assert events == [("", ["echo", "\x000\x00", "c"])] and quotes == [("$'", "a b")]
    assert shell.command_stream(None) == ([], []) and shell.command_stream("") == ([], [])


def targets(cmd):
    return [[(p, s) for p, s in g] for g in resolve.canonical_targets(shell.command_stream(cmd), det.SCRATCH)]


@pytest.mark.parametrize("cmd,want", [
    ("rm -rf /w/src", [[("/w/src", False)]]),
    ("rm -rf /tmp/a /w/b", [[("/tmp/a", True), ("/w/b", False)]]),
    ("cd /tmp/w && rm -rf ./*", [[("/tmp/w/*", True)]]),
    ("cd /w; rm -rf ./*", [[("*", False)]]),
    ("cd /w\nrm -rf .", [[(".", False)]]),
    ("cd /tmp/w && cd sub && rm -rf .", [[("/tmp/w/sub", True)]]),
    ("cd /tmp/w && cd /w && rm -rf .", [[("/w", False)]]),
    ("cd /tmp/w && cd .. && rm -rf .", [[("/tmp", True)]]),
    ("cd /tmp/w || rm -rf .", [[(".", False)]]),
    ("pushd /tmp/w && rm -rf .", [[("/tmp/w", True)]]),
    ("cd && rm -rf ./*", [[("$HOME/*", False)]]),
    ("cd /tmp/w && cd - && rm -rf ./*", [[("*", False)]]),
    ("cd /tmp/w && popd && rm -rf x", [[("x", False)]]),
    ("cd -P /tmp/w && rm -rf .", [[("/tmp/w", True)]]),
    ("cd $TMPDIR/w && rm -rf ./*", [[("*", False)]]),
    ("cd $UNSET && rm -rf ./*", [[("*", False)]]),
    ("(cd /tmp/w && rm -rf .)", [[("/tmp/w", True)]]),
    ("(cd /tmp/w); rm -rf .", [[(".", False)]]),
    ("cd /tmp/w | cat; rm -rf .", [[(".", False)]]),
    ("cd /tmp/w & rm -rf .", [[(".", False)]]),
    ("`cd /tmp/w`; rm -rf .", [[(".", False)]]),
    ("D=/tmp/w; rm -rf $D", [[("/tmp/w", True)]]),
    ('D=/tmp/w; rm -rf "${D}/x"', [[("/tmp/w/x", True)]]),
    ("export D=/w && rm -rf $D", [[("/w", False)]]),
    ("declare D=/w; rm -rf $D", [[("/w", False)]]),
    ("A=/tmp; B=$A/x; rm -rf $B", [[("$B", False)]]),
    ("rm -rf $HOME/x $PWD/y ${NOPE}", [[("$HOME/x", False), ("$PWD/y", False), ("${NOPE}", False)]]),
    ("rm -rf $TMPDIR/x ${TMPDIR}/y", [[("$TMPDIR/x", True), ("${TMPDIR}/y", True)]]),
    ("D=/tmp/w; ( D=/w; true ); rm -rf $D", [[("/tmp/w", True)]]),
    ("D=/w; D=$(pwd); rm -rf $D", [[("$D", False)]]),
    ("rm -rf -- /w/src", [[("/w/src", False)]]),
    ("rm -rf '' /w/src", [[("/w/src", False)]]),
    ('rm -rf "a b"', [[("a b", False)]]),
    ("cd /tmp/w && rm -rf node_modules", [[("/tmp/w/node_modules", True)]]),
    ("git worktree add /w/wt b && cd /w/wt && rm -rf .", [[("/w/wt", True)]]),
    ("git worktree add /w/wt b && rm -rf /w/wt/a /w/o", [[("/w/wt/a", True), ("/w/o", False)]]),
])
def test_canonical_targets(cmd, want):
    assert targets(cmd) == want


@pytest.mark.parametrize("cmd,want", [
    ("bash -c 'cd /tmp/w && rm -rf ./*'", [[("/tmp/w/*", True)]]),
    ('sh -c "rm -rf /w/src"', [[("/w/src", False)]]),
    ("eval 'rm -rf /w/src'", [[("/w/src", False)]]),
    ("eval rm -rf /w/src", [[("/w/src", False)]]),
    ("bash -lc 'rm -rf /w/src'", [[("/w/src", False)]]),
    ("bash --norc -o pipefail -c 'rm -rf /w/src'", [[("/w/src", False)]]),
    ("sudo bash -c 'rm -rf /w/src'", [[("/w/src", False)]]),
    ("cd /tmp/w && bash -c 'rm -rf .'", [[("/tmp/w", True)]]),
    ("export D=/tmp/w; bash -c 'rm -rf $D'", [[("/tmp/w", True)]]),
    ("D=/tmp/w; bash -c 'rm -rf $D'", [[("$D", False)]]),          # fix wave 3: a child shell inherits only exported names
    ("cd /tmp/w && bash -c 'cd /w' && rm -rf .", [[("/tmp/w", True)]]),
    ("bash -c \"bash -c 'rm -rf /w/src'\"", []),
    ("bash script.sh -c 'rm -rf /w/src'", []),
    ("bash -c", []),
    ("bash -c ''", []),
    ("ls -d /tmp/w/* | xargs rm -rf", [[("/tmp/w/*", True)]]),
    ("ls -d /w/a /w/b | xargs rm -rf", [[("/w/a", False), ("/w/b", False)]]),
    ("ls | xargs rm -rf", [[("$UNKNOWN", False)]]),
    ("cd /tmp/w && ls | xargs rm -rf", [[("$UNKNOWN", False)]]),
    ("find /w/a /tmp/b -type d | xargs rm -rf", [[("/w/a", False), ("/tmp/b", True)]]),
    ("cat list | xargs rm -rf", []),
    ("ls /w; xargs rm -rf", []),
    ("xargs rm -rf /w/src", [[("/w/src", False)]]),
    ("find /w/src -delete", [[("/w/src", False)]]),
    ("find /tmp/w -name '*.o' -delete", [[("/tmp/w", True)]]),
    ("find -L /w/src -delete", [[("/w/src", False)]]),
    ("find -delete", [[(".", False)]]),
    ("find /w/src -exec rm -rf {} +", [[("/w/src", False)]]),
    ("find /w/src -execdir rm -rf {} \\;", [[("/w/src", False)]]),
    ("find /w/src -exec rm {} +", []),
    ("find /w/src -name x", []),
    ("rm -r /w/src", []),
    ("rm /w/src", []),
])
def test_canonical_targets_wrappers_xargs_and_find(cmd, want):
    assert targets(cmd) == want


def test_a_hostile_quoted_value_is_unresolvable_not_scratch():
    assert targets('rm -rf "/tmp/$(cat /w/x)" "/tmp/`id`"') == [[("Q", False), ("Q", False)]]
    assert targets('D="/tmp/`id`"; rm -rf "$D"') == [[("$D", False)]]


def test_the_environment_is_bounded():
    cmd = "; ".join(f"V{i}=/tmp/{i}" for i in range(resolve.MAX_ENV + 5)) + "; rm -rf $V0 $V%d" % (resolve.MAX_ENV + 1)
    assert targets(cmd) == [[("/tmp/0", True), ("$V%d" % (resolve.MAX_ENV + 1), False)]]
    big = "A=" + "x" * (resolve.MAX_VALUE + 1) + "; rm -rf $A"
    assert targets(big) == [[("$A", False)]]


def test_resolution_is_linear_on_hostile_input():
    for name, cmd in {"cd-flood": "cd /tmp/a && " * 5000 + "rm -rf .", "paren-flood": "( cd /w; " * 5000 + "rm -rf .",
                      "assign-flood": "A=$A$A; " * 5000 + "rm -rf $A", "backtick-flood": "` cd /w " * 5000 + "rm -rf .",
                      "wrapper-flood": "bash -c 'rm -rf /w' ; " * 3000}.items():
        t0 = time.monotonic()
        resolve.canonical_targets(shell.command_stream(cmd), det.SCRATCH)
        assert time.monotonic() - t0 <= TIME_BUDGET, name


# ------------------------------------------------------------------ v2 fails closed (security review of run 1): a non-scratch delete is never read as scratch
def bad(cmd):
    return any(not s for g in targets(cmd) for _p, s in g)


FAIL_CLOSED = [   # (case, command, fires)
    # 1. the cwd carries only across `&&`
    ("cd-or-true-then-rm", "cd /tmp/w || true; rm -rf ./*", True),
    ("cd-or-then-rm", "cd /tmp/w || rm -rf ./*", True),
    ("cd-semicolon-rm", "cd /tmp/w; rm -rf ./*", True),
    ("cd-newline-rm", "cd /tmp/w\nrm -rf ./*", True),
    ("cd-if-then-rm", "if cd /tmp/w; then rm -rf ./*; fi", True),
    ("cd-while-body", "while true; do cd /tmp/w; rm -rf ./*; done", True),
    ("cd-and-rm", "cd /tmp/w && rm -rf ./*", False),
    ("cd-and-cd-and-rm", "cd /tmp/w && cd sub && rm -rf ./*", False),
    ("cd-and-chain-in-a-loop", "for i in 1 2; do cd /tmp/w && rm -rf ./*; done", True),
    ("cd-and-subshell", "cd /tmp/w && (rm -rf ./*)", False),
    ("cd-in-subshell-and", "(cd /tmp/w && rm -rf ./*)", False),
    ("cd-pipe-rm", "cd /tmp/w | cat && rm -rf ./*", True),
    ("cd-and-then-semicolon-chain", "cd /tmp/w && true; rm -rf ./*", True),
    ("bash-c-inherits-the-and-chain", "cd /tmp/w && bash -c 'rm -rf ./*'", False),
    ("bash-c-semicolon-inside", "bash -c 'cd /tmp/w; rm -rf ./*'", True),
    # 2. a binding that later text may change is dropped
    ("plain-binding", 'D=/tmp/w; rm -rf "$D"', False),
    ("unset", 'D=/tmp/w; unset D; rm -rf "$D"', True),
    ("append", 'D=/tmp/w; D+=/../../w; rm -rf "$D"', True),
    ("array-element", 'D=/tmp/w; D[0]=/w; rm -rf "$D"', True),
    ("for-loop", 'D=/tmp/w; for D in /w/a; do rm -rf "$D"; done', True),
    ("read", 'D=/tmp/w; read D; rm -rf "$D"', True),
    ("read-flags", 'D=/tmp/w; read -r -p ask D; rm -rf "$D"', True),
    ("printf-v", 'D=/tmp/w; printf -v D %s /w; rm -rf "$D"', True),
    ("declare-n", 'D=/tmp/w; declare -n D=OTHER; rm -rf "$D"', True),
    ("mapfile", 'D=/tmp/w; mapfile D < f; rm -rf "$D"', True),
    ("getopts", 'D=/tmp/w; getopts ab D; rm -rf "$D"', True),
    ("command-substitution", 'D=/tmp/w; D=$(pwd); rm -rf "$D"', True),
    ("backticks", 'D=/tmp/w; D=`pwd`; rm -rf "$D"', True),
    ("unresolved-rhs", 'D=/tmp/w; D=$OTHER; rm -rf "$D"', True),
    ("default-expansion-rhs", 'D=${X:-/tmp/w}; rm -rf "$D"', True),
    ("eval-rebinds", 'D=/tmp/w; eval "D=/w"; rm -rf "$D"', True),
    ("source-rebinds", 'D=/tmp/w; source ./env.sh; rm -rf "$D"', True),
    ("dot-rebinds", 'D=/tmp/w; . ./env.sh; rm -rf "$D"', True),
    ("rhs-with-a-reference-is-not-a-literal", 'A=/tmp/w; D=$A/x; rm -rf "$D"', True),
    ("bash-c-keeps-the-parent-binding", 'D=/tmp/w; bash -c \'unset D\'; rm -rf "$D"', False),
    # 3. a worktree is scratch only inside the same && chain, and never a wide or odd path
    ("worktree-same-chain", "git worktree add /w/wt b && rm -rf /w/wt", False),
    ("worktree-home", "git worktree add ~/x && rm -rf ~/x", True),
    ("worktree-home-sub", "git worktree add ~/x/y && rm -rf ~/x/y", True),
    ("worktree-semicolon", "git worktree add /w/wt b; rm -rf /w/wt", True),
    ("worktree-or", "git worktree add /w/wt b || rm -rf /w/wt", True),
    ("worktree-newline", "git worktree add /w/wt b\nrm -rf /w/wt", True),
    ("worktree-dotdot", "git worktree add /w/../wt && rm -rf /w/../wt", True),
    ("worktree-dotdot-relative", "git worktree add ../wt && rm -rf ../wt", True),
    ("worktree-dollar", "git worktree add $X/wt && rm -rf $X/wt", True),
    ("worktree-root", "git worktree add / && rm -rf /", True),
    ("worktree-dot", "git worktree add . && rm -rf .", True),
    ("worktree-one-component", "git worktree add /wt && rm -rf /wt", True),
    ("worktree-relative-one-component", "git worktree add wt && rm -rf wt", True),
    ("worktree-the-repo-itself", "cd /w/repo && git worktree add /w/repo b && rm -rf /w/repo", True),
    ("worktree-an-ancestor-of-the-repo", "git -C /w/repo/sub worktree add /w/repo b && rm -rf /w/repo", True),
    ("worktree-under-the-repo", "git -C /w/repo worktree add wt b && rm -rf /w/repo/wt", False),
    ("worktree-then-semicolon-chain", "git worktree add /w/wt b && true; rm -rf /w/wt", True),
    # 4. an `ls` producer resolves only as `ls -d <paths>`
    ("ls-d-glob-scratch", "ls -d /tmp/w/* | xargs rm -rf", False),
    ("ls-d-glob-outside", "ls -d /w/src/* | xargs rm -rf", True),
    ("ls-dir-names", "ls /tmp/w | xargs rm -rf", True),
    ("ls-bare", "cd /tmp/w && ls | xargs rm -rf", True),
    ("ls-l-names", "ls -l /tmp/w | xargs rm -rf", True),
    ("ls-d-without-an-argument", "ls -d | xargs rm -rf", True),
    ("find-keeps-the-root-rule", "find /tmp/w -type f | xargs rm -rf", False),
    ("eval-keeps-the-state", 'eval "cd /tmp/w" && rm -rf ./*', False),
]


@pytest.mark.parametrize("cmd,fires", [(c, f) for _n, c, f in FAIL_CLOSED], ids=[n for n, _c, _f in FAIL_CLOSED])
def test_v2_fails_closed(cmd, fires):
    assert bad(cmd) is fires


# ------------------------------------------------------------------ v2 fails closed, round 2: tainted chains, glued and wrapped rebinds
TAINT_AND_REBINDS = [   # (case, command, fires)
    # a `||` earlier in the chain may skip the cd / the assignment / the worktree add: nothing in the chain is trusted
    ("or-skips-the-cd", "true || cd /tmp/w && rm -rf ./*", True),
    ("cd-then-or-then-rm", "cd /tmp/w && true || rm -rf ./*", True),
    ("or-skips-the-assignment", 'false || D=/tmp/w && rm -rf "$D"', True),
    ("or-skips-the-export", 'false || export D=/tmp/w && rm -rf "$D"', True),
    ("or-skips-the-worktree-add", "false || git worktree add /w/wt b && rm -rf /w/wt", True),
    ("binding-before-the-chain-is-lost-after-or", 'D=/tmp/w; true || echo x; rm -rf "$D"', True),
    ("or-in-a-subshell-chain", "true || (cd /tmp/w && rm -rf ./*)", True),
    ("or-then-semicolon-ends-the-taint", "true || echo x; D=/tmp/w; rm -rf \"$D\"", False),
    ("or-then-newline-ends-the-taint", "true || echo x\nD=/tmp/w\nrm -rf \"$D\"", False),
    ("or-then-background-ends-the-taint", 'true || echo x & D=/tmp/w; rm -rf "$D"', False),
    ("and-chain-without-or", "cd /tmp/w && rm -rf ./*", False),
    ("and-and-chain", "cd /tmp/w && true && rm -rf ./*", False),
    ("or-in-bash-c", "bash -c 'true || cd /tmp/w && rm -rf ./*'", True),
    ("or-outside-bash-c", "true || bash -c 'cd /tmp/w && rm -rf ./*'", False),
    ("or-then-absolute-scratch-target", "true || rm -rf /tmp/w/x", False),
    ("or-then-outside-target", "true || rm -rf /w/src", True),
    # printf -v glued, builtin/command prefixes, unknown shapes of binding builtins, wrapped builtins
    ("printf-v-glued", 'printf -vD /w; rm -rf "$D"', True),
    ("printf-v-glued-after-binding", 'D=/tmp/w; printf -vD /w; rm -rf "$D"', True),
    ("printf-v-spaced", 'D=/tmp/w; printf -v D /w; rm -rf "$D"', True),
    ("builtin-printf-v", 'D=/tmp/w; builtin printf -v D /w; rm -rf "$D"', True),
    ("command-unset", 'D=/tmp/w; command unset D; rm -rf "$D"', True),
    ("builtin-unset", 'D=/tmp/w; builtin unset D; rm -rf "$D"', True),
    ("builtin-cd", "cd /tmp/w && builtin cd /w && rm -rf ./*", True),
    ("command-cd-is-not-plain", "command cd /tmp/w && rm -rf ./*", True),
    ("env-cd-is-not-the-builtin", "env cd /tmp/w && rm -rf ./*", True),
    ("sudo-cd-is-not-the-builtin", "sudo cd /tmp/w && rm -rf ./*", True),
    ("env-export-is-not-the-builtin", 'env export D=/tmp/w && rm -rf "$D"', True),
    ("let", 'D=/tmp/w; let D=1; rm -rf "$D"', True),
    ("let-quoted", 'D=/tmp/w; let "D+=1"; rm -rf "$D"', True),
    ("source-rebinds", 'D=/tmp/w; source x; rm -rf "$D"', True),
    ("read-with-an-unknown-option", 'D=/tmp/w; read -t5 D; rm -rf "$D"', True),
    ("read-plain-name", 'D=/tmp/w; read D; rm -rf "$D"', True),
    ("declare-with-a-plus-option", 'D=/tmp/w; declare +x D; rm -rf "$D"', True),
    ("declare-with-a-long-option", 'D=/tmp/w; declare --weird D; rm -rf "$D"', True),
    ("local-nameref", 'D=/tmp/w; local -n D=X; rm -rf "$D"', True),
    ("export-unknown-option", 'D=/tmp/w; export -Z D; rm -rf "$D"', True),
    ("mapfile-option", 'D=/tmp/w; mapfile -t D < f; rm -rf "$D"', True),
    ("getopts-option", 'D=/tmp/w; getopts -x ab D; rm -rf "$D"', True),
    ("unset-unknown-option", 'D=/tmp/w; unset -q D; rm -rf "$D"', True),
    ("declare-known-options-keep-the-binding", 'D=/tmp/w; declare -r E=1; rm -rf "$D"', False),
    ("export-known-option-keeps-the-binding", 'D=/tmp/w; export -n E; rm -rf "$D"', False),
    ("read-known-options-for-another-name", 'D=/tmp/w; read -r E; rm -rf "$D"', False),
    ("plain-assignment-then-delete", 'D=/tmp/w && rm -rf "$D"', False),
]


@pytest.mark.parametrize("cmd,fires", [(c, f) for _n, c, f in TAINT_AND_REBINDS], ids=[n for n, _c, _f in TAINT_AND_REBINDS])
def test_v2_fails_closed_on_tainted_chains_and_glued_or_wrapped_rebinds(cmd, fires):
    assert bad(cmd) is fires


# ------------------------------------------------------------------ v2 fails closed, round 3: a wrapped builtin resets the state but is still scanned
WRAPPED_BUILTINS = [   # (case, command, fires)
    ("time-eval", 'time eval "rm -rf /w/src"', True),
    ("sudo-eval", 'sudo eval "rm -rf /w/src"', True),
    ("env-eval", 'env eval "rm -rf /w/src"', True),
    ("command-eval", 'command eval "rm -rf /w/src"', True),
    ("env-bash-c", "env bash -c 'rm -rf /w/src'", True),
    ("sudo-bash-c", "sudo bash -c 'rm -rf /w/src'", True),
    ("time-bash-c", "time bash -c 'rm -rf /w/src'", True),
    ("nice-sh-c", "nice sh -c 'rm -rf /w/src'", True),
    ("time-eval-twin", 'time eval "rm -rf /tmp/w/x"', False),
    ("sudo-eval-twin", 'sudo eval "rm -rf /tmp/w/x"', False),
    ("env-eval-twin", 'env eval "rm -rf /tmp/w/x"', False),
    ("command-eval-twin", 'command eval "rm -rf /tmp/w/x"', False),
    ("eval-twin", 'eval "rm -rf /tmp/w/x"', False),
    ("env-bash-c-twin", "env bash -c 'rm -rf /tmp/w/x'", False),
    ("wrapped-eval-forgets-the-cwd", 'cd /tmp/w && sudo eval "rm -rf ./*"', True),
    ("wrapped-eval-forgets-the-binding", 'D=/tmp/w; sudo eval "rm -rf $D"', True),
    ("wrapped-eval-own-cd-still-works", 'sudo eval "cd /tmp/w && rm -rf ./*"', False),
    ("time-cd-is-unknown-and-conservative", "time cd /tmp/w && rm -rf ./*", True),
    ("time-export-is-unknown", 'time export D=/tmp/w && rm -rf "$D"', True),
    ("wrapped-source-forgets-then-deletes", "env source x && rm -rf /w/src", True),
    ("wrapped-builtin-then-target", "env cd /tmp/w && rm -rf /w/src", True),
]


@pytest.mark.parametrize("cmd,fires", [(c, f) for _n, c, f in WRAPPED_BUILTINS], ids=[n for n, _c, _f in WRAPPED_BUILTINS])
def test_v2_a_wrapped_builtin_resets_the_state_but_is_still_scanned(cmd, fires):
    assert bad(cmd) is fires


# ------------------------------------------------------------------ v2 fails closed, round 4: a wrapped eval runs on a copy and leaves the outer state unknown
WRAPPED_EVAL_STATE = [   # (case, command, fires)
    ("env-eval-cd-does-not-leak", 'env eval "cd /tmp/w" && rm -rf ./*', True),
    ("sudo-eval-cd-does-not-leak", 'sudo eval "cd /tmp/w" && rm -rf ./*', True),
    ("time-eval-cd-does-not-leak", 'time eval "cd /tmp/w" && rm -rf ./*', True),
    ("sudo-eval-assignment-does-not-leak", 'sudo eval "D=/tmp/w" && rm -rf "$D"/x', True),
    ("env-eval-assignment-does-not-leak", 'env eval "D=/tmp/w" && rm -rf "$D"', True),
    ("env-eval-worktree-does-not-leak", 'env eval "git worktree add /w/wt b" && rm -rf /w/wt', True),
    ("wrapped-eval-wipes-an-earlier-binding", 'D=/tmp/w; env eval "true"; rm -rf "$D"', True),
    ("env-eval-still-collects-its-deletes", 'env eval "rm -rf /w/src"', True),
    ("env-eval-collects-scratch-as-scratch", 'env eval "rm -rf /tmp/w/x"', False),
    ("env-eval-own-chain-resolves", 'env eval "cd /tmp/w && rm -rf ./*"', False),
    ("plain-eval-cd-runs-in-this-shell", 'eval "cd /tmp/w" && rm -rf ./*', False),
    ("plain-eval-assignment-runs-in-this-shell", 'eval "D=/tmp/w" && rm -rf "$D"/x', False),
    ("plain-eval-delete-scratch", 'eval "rm -rf /tmp/w/x"', False),
]


@pytest.mark.parametrize("cmd,fires", [(c, f) for _n, c, f in WRAPPED_EVAL_STATE], ids=[n for n, _c, _f in WRAPPED_EVAL_STATE])
def test_v2_a_wrapped_eval_runs_on_a_copy_and_leaves_the_outer_state_unknown(cmd, fires):
    assert bad(cmd) is fires


# ------------------------------------------------------------------ fix wave 2: only proven state is trusted (resolve_pre.py)
from ace_playbook.compile import resolve_pre as rp   # noqa: E402

PROVEN = [   # (case, command, fires)
    ("first-in-chain-binding-survives-the-chain", 'D=/tmp/w && true; rm -rf "$D"', False),
    ("binding-after-and-dies-with-the-chain", 'true && D=/tmp/w; rm -rf "$D"', True),
    ("binding-after-and-used-in-the-chain", 'true && D=/tmp/w && rm -rf "$D"', False),
    ("binding-survives-semicolon-and-newline", 'D=/tmp/w\ntrue; rm -rf "$D"/x', False),
    ("binding-in-a-subshell-is-restored", 'D=/tmp/w; (D=/w/src); rm -rf "$D"', False),
    ("worktree-in-a-subshell-is-dropped", "(git worktree add /w/wt b) && rm -rf /w/wt", True),
    ("worktree-in-the-same-group", "(git worktree add /w/wt b && rm -rf /w/wt)", False),
    ("cdpath-assignment", "export CDPATH=/w; cd /tmp/w && cd tmp && rm -rf ./*", True),
    ("cdpath-prefix", "CDPATH=/w cd /tmp/w && rm -rf ./*", True),
    ("backslash-in-a-quoted-target", 'rm -rf "/tmp/w/a\\\nb"', True),
    ("quoted-literal-target-with-a-space", 'rm -rf "/tmp/my dir"', False),
    ("arithmetic-restores-nothing", 'D=/tmp/w; (( D=1 )); rm -rf "$D"', True),
    ("dollar-left-after-a-scratch-prefix", 'D=/tmp/w; rm -rf "$D/$E"', True),
    ("brace-left-after-a-scratch-prefix", 'D=/tmp/w; rm -rf "$D"/{a,b}', True),
    ("literal-token-keeps-v1s-prefix-rule", "rm -rf /tmp/w/$E", False),
    ("literal-token-with-braces-keeps-v1s-prefix-rule", "rm -rf /tmp/w/{a,b}", False),
    ("rm-after-ddash-flag-like-target", "rm -f -r -- -/w/src", True),
    ("rm-ddash-then-scratch", "rm -rf -- /tmp/w/x", False),
    ("an-eval-skipped-by-and-binds-nothing", 'true && eval "D=/tmp/w"; rm -rf "$D"', True),
    ("an-eval-first-in-its-chain-binds-and-resolves", 'eval "D=/tmp/w"; rm -rf "$D"', False),
    # fix wave 3: the leftover check on every target, the exported-only child env, glued redirections
    ("brace-through-dotdot-on-a-trusted-cwd", "cd /tmp/w && rm -rf ./{a,../../w/src}", True),
    ("glob-next-to-dotdot-on-a-trusted-cwd", "cd /tmp/w && rm -rf ../*", True),
    ("plain-glob-on-a-trusted-cwd", "cd /tmp/w && rm -rf ./*", False),
    ("subdir-glob-on-a-trusted-cwd", "cd /tmp/w && rm -rf ./sub/*", False),
    ("dotdot-without-a-glob-still-resolves", "cd /tmp/w && rm -rf ../x", False),
    ("a-literal-token-with-a-brace-keeps-v1s-prefix-rule-on-a-cwd", "cd /tmp/w && rm -rf ./{a,b}", True),
    ("bash-c-child-without-export", 'D=/tmp/w; bash -c \'rm -rf "$D"\'', True),
    ("bash-c-child-with-export", 'export D=/tmp/w; bash -c \'rm -rf "$D"\'', False),
    ("bash-c-child-with-export-after-binding", 'D=/tmp/w; export D; bash -c \'rm -rf "$D"\'', False),
    ("bash-c-child-with-prefix-assignment", 'D=/tmp/w bash -c \'rm -rf "$D"\'', False),
    ("bash-c-child-with-env-prefix", 'env D=/tmp/w bash -c \'rm -rf "$D"\'', False),
    ("sh-c-child-without-export", 'D=/tmp/w; sh -c \'rm -rf "$D"\'', True),
    ("bash-c-child-keeps-the-cwd", "cd /tmp/w && bash -c 'rm -rf ./*'", False),
    ("bash-c-child-keeps-the-worktree-mark", "git worktree add /w/src/wt b && bash -c 'rm -rf /w/src/wt'", False),
    ("bash-c-child-after-sudo-inherits-nothing", 'export D=/tmp/w; sudo bash -c \'rm -rf "$D"\'', True),
    ("bash-c-child-after-unexport", 'export D=/tmp/w; export -n D; bash -c \'rm -rf "$D"\'', True),
    ("bash-c-child-prefix-overrides-with-a-dollar", 'export D=/tmp/w; D=$X bash -c \'rm -rf "$D"\'', True),
    ("export-inside-a-subshell-is-restored", 'D=/tmp/w; (export D); bash -c \'rm -rf "$D"\'', True),
    ("export-before-a-subshell-survives-it", 'export D=/tmp/w; (D=/tmp/v); bash -c \'rm -rf "$D"\'', False),
    ("export-dies-with-its-chain-binding", 'true && export D=/tmp/w; bash -c \'rm -rf "$D"\'', True),
    ("bare-eval-still-shares-the-cwd", "eval 'cd /tmp/w' && rm -rf ./*", False),
    ("glued-redirect-on-cd", "cd /tmp/w>x && rm -rf ./*", True),
    ("glued-redirect-on-an-assignment", 'D=/tmp/w<f; rm -rf "$D"', True),
    ("glued-fd-redirect-on-cd", "cd /tmp/w2>/dev/null && rm -rf ./*", True),
]


@pytest.mark.parametrize("cmd,fires", [(c, f) for _n, c, f in PROVEN], ids=[n for n, _c, _f in PROVEN])
def test_v2_trusts_only_proven_state(cmd, fires):
    assert bad(cmd) is fires


def stream(cmd):
    return shell.command_stream(cmd)


@pytest.mark.parametrize("cmd,untrusted", [
    ("cd /tmp/w && rm -rf ./*", False), ("(cd /tmp/w && rm -rf ./*)", False), ("bash -c 'cd /tmp/w && rm -rf ./*'", False),
    ("if true; then rm -rf x; fi", True), ("while true; do x; done", True), ("for i in a; do x; done", True),
    ("case x in x) y;; esac", True), ("{ a; }", True), ("f() { a; }", True), ("f(){ a; }", True), ("function f { a; }", True),
    ("trap 'x' EXIT", True), ("alias a=b", True), ("IFS=:; a", True), ("read IFS", True), ("CDPATH=x cd y", True),
    ("eval 'cd /tmp/w'", False), ("eval 'cd /tmp/w; rm -rf x'", False), ("eval \"$X\"", True), ("eval 'echo hi'", True),
    ("eval 'f'", True), ("eval 'a && (b)'", True), ("eval 'if true; then cd x; fi'", True), ("echo if then fi", False),
])
def test_untrusted_gate(cmd, untrusted):
    assert rp.untrusted(*stream(cmd)) is untrusted


def test_literal_text_and_plain_segments():
    q = [("'", "a b"), ('"', "x$y")]
    assert rp.literal_text("/tmp/w", q) == "/tmp/w" and rp.literal_text("\x000\x00", q) is None and rp.literal_text("\x001\x00", q) is None
    for bad_ in ("", "a b", "$X", "a*", "a?", "a[1]", "a{b", "a`b", "a\\b"):
        assert rp.literal_text(bad_, q) is None, bad_
    assert rp.plain(["cd", "/x"], ["cd", "/x"]) and rp.plain(["D=1"], []) and rp.plain(["D=1", "E=2"], [])
    assert not rp.plain(["!", "cd", "/x"], ["cd", "/x"]) and not rp.plain(["env", "cd", "/x"], ["cd", "/x"])
    assert not rp.plain(["cd", "/x", ">/dev/null"], ["cd", "/x", ">/dev/null"]) and not rp.plain(["D=1", "env"], [])
    assert not rp.plain(["D=1", "cd", "/x"], ["cd", "/x"]) and not rp.plain(["D=1", ">f"], [])


def test_wave3_a_glued_redirection_is_neither_a_literal_nor_a_plain_segment():
    for glued in ("/tmp/w>x", "/tmp/w<f", "/tmp/w2>/dev/null", "/tmp/w>&2", "/tmp/w;x", "/tmp/w|x", "/tmp/w&x", "/tmp/w#x", "/tmp/w(x", "/tmp/w)"):
        assert rp.literal_text(glued, []) is None, glued
    for cmd in (["cd", "/tmp/w>x"], ["cd", "/tmp/w2>/dev/null"], ["cd", "/tmp/w>&2"], ["pushd", "/tmp/w<f"], ["export", "D=/tmp/w>x"]):
        assert not rp.plain(cmd, cmd), cmd
    assert not rp.plain(["D=/tmp/w<f"], []) and not rp.plain(["D=/tmp/w>x", "E=1"], []) and rp.plain(["D=/tmp/w"], [])
    assert rp.plain(["cd", "/tmp/w"], ["cd", "/tmp/w"]) and rp.literal_text("/tmp/w-x_1.2", []) == "/tmp/w-x_1.2"


def test_wave3_leftover_is_true_for_expansions_and_globs_next_to_dotdot_only():
    for left in ("a/$X", "a/${X}", "./{a,b}", "a/{b,../../c}", "../*", "*/..", "a/../?", "a/[ab]/..", "./sub/../../*"):
        assert rp.leftover(left), left
    for fine in ("/tmp/w", "./*", "./sub/*", "a/../b", "*", "a/b*c/d", "/tmp/w/{a", "{a}"):
        assert not rp.leftover(fine), fine


def test_wave3_child_env_keeps_exported_names_and_literal_prefix_assignments_only():
    q = [("'", "a b")]
    env, exp = {"D": "/tmp/w", "E": "/srv/e", "F": "/tmp/f"}, {"D", "F", "GONE"}
    assert rp.child_env([], env, exp, q) == {"D": "/tmp/w", "F": "/tmp/f"}
    assert rp.child_env(["env"], env, exp, q) == {"D": "/tmp/w", "F": "/tmp/f"}
    assert rp.child_env(["E=/tmp/e", "env", "G=/tmp/g"], env, exp, q) == {"D": "/tmp/w", "F": "/tmp/f", "E": "/tmp/e", "G": "/tmp/g"}
    assert rp.child_env(["D=/tmp/x"], env, exp, q) == {"D": "/tmp/x", "F": "/tmp/f"}
    assert rp.child_env(["D=$X"], env, exp, q) == {"F": "/tmp/f"} and rp.child_env(["D=\x000\x00"], env, exp, q) == {"F": "/tmp/f"}
    for wrapper in (["sudo"], ["nice"], ["command"], ["time"], ["env", "-i"], ["env", "-u", "D"], ["D=/tmp/x", "sudo"], ["builtin"], ["timeout", "5"]):
        assert rp.child_env(wrapper, env, exp, q) == {}, wrapper
    assert env == {"D": "/tmp/w", "E": "/srv/e", "F": "/tmp/f"} and exp == {"D", "F", "GONE"}      # inputs are never mutated


def test_rm_args_after_a_double_dash_everything_is_a_target():
    assert resolve.rm_args(["rm", "-rf", "--", "-x", "y"]) == ["-x", "y"]
    assert resolve.rm_args(["rm", "-r", "-f", "a", "--", "-b"]) == ["a", "-b"]
    assert resolve.rm_args(["rm", "--", "-rf", "x"]) == ["-rf", "x"] and resolve.rm_args(["rm", "-rf", "--"]) == []
