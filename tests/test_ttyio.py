"""`ttyio.ask` against a real pseudo-terminal: the prompt reaches the terminal and the typed line comes back.

A single buffered `r+` handle on a tty fails with "File or stream is not seekable" when it turns from writing
to reading; this test opens a pty pair so the write-then-read path is exercised for real, not mocked.
"""

from __future__ import annotations

import os
import pty

import pytest

from ace_playbook import ttyio
from ace_playbook.compile import __main__ as cli
from ace_playbook.store import promote


def _pty():
    master, slave = pty.openpty()
    try:
        name = os.ttyname(slave)
        open(name, "r").close()            # some sandboxes refuse to reopen the slave by name
    except OSError:
        os.close(master)
        os.close(slave)
        pytest.skip("pty slave cannot be reopened by name here")
    return master, slave, name


def test_ask_writes_the_prompt_and_returns_the_typed_line_without_the_newline():
    master, slave, name = _pty()
    try:
        os.write(master, b"yes\n")                     # typed ahead; the pty buffers it until ask() reads
        assert ttyio.ask("Approve? [y/N] ", device=name) == "yes"
        seen = os.read(master, 200)
        assert b"Approve? [y/N] " in seen
    finally:
        os.close(master)
        os.close(slave)


def test_ask_raises_eoferror_when_nothing_can_be_read():
    """/dev/null stands in for an exhausted terminal (a closed pty master blocks forever on macOS)."""
    with pytest.raises(EOFError):
        ttyio.ask("? ", device=os.devnull)


def test_the_cli_and_promote_prompts_go_through_ttyio(monkeypatch):
    calls = []
    monkeypatch.setattr(ttyio, "ask", lambda prompt, device=None: (calls.append(prompt), "  y \n")[1])
    assert cli._ask("Q1 ") == "y"
    assert promote._prompt("Q2 ") == "  y \n"
    assert calls == ["Q1 ", "Q2 "]
