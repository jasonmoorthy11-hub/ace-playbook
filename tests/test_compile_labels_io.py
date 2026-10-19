"""`compile label` I/O edges: printable incident preview, regular-file refusal, locked duplicates, strict decode."""

import os
import stat

from ace_playbook.compile import labels

from . import compile_support as cs
from .test_compile_labels import ROW, incident, put_shadow, rows_of, run, tty, world  # noqa: F401  (fixtures)

BAD = "\x1b[31m\x07\x9b"


def test_the_incident_confirmation_prints_no_control_characters_from_the_transcript(world, tty, capsys):
    sess = world.add_session("esc", [{"human": "go"}, {"tool": "Bash", "input": {"command": f"{cs.RM_CMD} {BAD}"}}])
    cs.append_jsonl(world.cfg.injections, {"session_id": sess, "shown_ids": [world.lesson_id], "holdout_ids": []})
    tty.answer = "n"
    code, out, _ = incident(world, capsys, session=sess)
    assert code == 1 and "rm -rf" in out and not any(c in out for c in "\x1b\x07\x9b")


def test_ts_and_tool_name_in_the_incident_confirmation_are_cleaned_too(world, tty, capsys, monkeypatch):
    fake = {"detector": "rm_rf" + BAD, "ts": "2026" + BAD, "ref": "E-00000000", "session": "sid:0123456789abcdef",
            "payload": {"tool_name": "Ba" + BAD + "sh", "tool_input": {"command": "x" + BAD + "y", "n": 1}}}
    monkeypatch.setattr(labels, "resolve_incident", lambda cfg, lid, session: fake)
    tty.answer = "n"
    code, out, _ = incident(world, capsys)
    assert code == 1 and not any(c in out for c in "\x1b\x07\x9b") and "Ba[31msh" in out and "x[31my" in out


def test_a_non_regular_file_swapped_in_after_the_pre_prompt_check_is_refused_untouched(world, tty, capsys, monkeypatch):
    path = world.cfg.path("labels.jsonl")

    def make_fifo(cfg):
        os.mkfifo(path)
        return []

    monkeypatch.setattr(labels, "check_chain", make_fifo)
    for args in (["--tp", put_shadow(world)], ["--incident", "--lesson", world.lesson_id, "--session", world.session]):
        code, _, err = run(world, *args, capsys=capsys)
        assert code == 2 and labels.NOT_A_FILE in err and stat.S_ISFIFO(path.lstat().st_mode)
        monkeypatch.setattr(labels, "check_chain", lambda cfg: [])


def test_two_appends_of_the_same_ref_and_label_leave_one_row_and_return_it(world):
    first = labels.append_row(world.cfg, "fp", "E-00000000", None)
    again = labels.append_row(world.cfg, "fp", "E-00000000", "a different note")
    assert again == first and again[1] == 1 and rows_of(world) == [first[0]]
    assert [labels.append_row(world.cfg, k, "E-00000000")[1] for k in ("tp", "fp")] == [2, 3]


def test_the_locked_read_decodes_strictly_like_the_other_readers(world, tty, capsys, monkeypatch):
    path = world.cfg.path("labels.jsonl")
    path.write_bytes(b'{"ref": "\xff"}\n')
    monkeypatch.setattr(labels, "check_chain", lambda cfg: [])
    code, _, err = run(world, "--tp", put_shadow(world), capsys=capsys)
    assert code == 2
    assert path.read_bytes() == b'{"ref": "\xff"}\n'
