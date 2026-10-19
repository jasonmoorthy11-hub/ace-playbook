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
    assert time.monotonic() - t0 <= 0.1, name


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
