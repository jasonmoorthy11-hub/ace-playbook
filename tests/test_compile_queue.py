"""`compile label --queue` (docs/design/compiler.md 7.3 "Label queue"): the blind queue of unlabelled fires, TTY only."""

import fcntl
import json
import os
import pty
import select
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from ace_playbook.compile import labels, queue, report

from . import compile_support as cs

ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-10-20T12:00:00Z"
CMDS = ("rm -rf $V/a", "rm -rf $V/b", "rm -rf $V/c", "rm -rf $V/d", "rm -rf $V/e")
EMAIL = "jane.doe" + "@" + "invented-corp.io"                # assembled so no address literal sits in the source


@pytest.fixture
def w(tmp_path):
    return cs.offline_world(tmp_path)


def fire(w, n: int, *, ts: str | None = None) -> dict:
    """Shadow row number `n` (its own command, so its own row id); a larger n is later by default."""
    return cs.put_shadow(w, f"sess-q-{n}", CMDS[n], ts=ts or f"2026-10-20T0{n + 1}:00:00Z")


class Script:
    """A scripted `ask`: answers in order, records the prompts, fails loudly when the script runs dry."""

    def __init__(self, *answers: str) -> None:
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        assert self.answers, f"unexpected prompt {prompt!r}"
        return self.answers.pop(0)


def no_tty(what: str) -> bool:
    print(f"error: {what} needs an interactive terminal (stdin is not a TTY)")
    return False


def run(w, ask, limit: int = 50, need_tty=lambda what: True) -> int:
    return queue.run(w.cfg, limit, ask, need_tty)


def chain(w) -> list[dict]:
    rows, broken = labels.read_chain(w.cfg)
    assert broken is None
    return rows


# ---------------------------------------------------------------- pending
def test_pending_is_oldest_first_and_limited(w):
    rows = [fire(w, n) for n in (3, 0, 2, 1)]                      # written out of order
    oldest = sorted(rows, key=lambda r: r["ts"])
    items = queue.pending(w.cfg, 3, NOW)
    assert [i["ts"] for i in items] == [r["ts"] for r in oldest[:3]]
    assert [i["id"] for i in items] == [labels.row_id(r) for r in oldest[:3]]
    assert len(queue.pending(w.cfg, 50, NOW)) == 4


def test_item_fields(w):
    row = fire(w, 0, ts="2026-10-20T09:00:00Z")
    (item,) = queue.pending(w.cfg, 50, NOW)
    approved = json.loads((w.data / "proposals" / "approved" / (w.pid + ".json")).read_text())
    assert item == {"id": labels.row_id(row), "ts": row["ts"], "age": "3h", "proposal_id": w.pid, "detector": "rm_rf_nonscratch",
                    "hash8": approved["proposal_sha256"][:8], "mode": "observe", "event": "PreToolUse", "cause": "detector",
                    "session8": row["session"][:8], "payload_sha256": row["payload_sha256"],
                    "tally": {"n": 1, "tp": 0, "fp": 0, "needed": 5}, "chain_ok": True}


def test_age_units():
    stamps = ("2026-10-20T11:59:30Z", "2026-10-20T11:30:00Z", "2026-10-20T07:00:00Z", "2026-10-17T12:00:00Z",
              "2027-01-01T00:00:00Z", "junk")
    assert [queue.age(t, NOW) for t in stamps] == ["0m", "30m", "5h", "3d", "0m", "-"]


def test_pending_is_blind_and_skips_labelled_rows(w):
    rows = [fire(w, n) for n in range(3)]
    labels.append_row(w.cfg, "fp", labels.row_id(rows[0]), "SECRET-NOTE-TEXT")
    labels.append_row(w.cfg, "tp", labels.row_id(rows[1]))
    items = queue.pending(w.cfg, 50, NOW)
    assert [i["id"] for i in items] == [labels.row_id(rows[2])]
    assert "label" not in items[0] and "SECRET-NOTE-TEXT" not in json.dumps(items)
    assert items[0]["tally"] == {"n": 3, "tp": 1, "fp": 1, "needed": 4}


def test_pending_deduplicates_by_row_id(w):
    row = fire(w, 0)
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), row)                # the same row, twice more
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), row)
    assert [i["id"] for i in queue.pending(w.cfg, 50, NOW)] == [labels.row_id(row)]


def test_pending_skips_unfired_and_synthetic_rows(w):
    row = fire(w, 0)
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), {**fire(w, 1), "would_fire": False})
    cs.append_jsonl(w.cfg.path("shadow.synthetic.jsonl"), {**row, "corpus": "synthetic", "proposal_id": "P-0a1b2c3d", "ts": "2026-10-01T00:00:00Z"})
    items = queue.pending(w.cfg, 50, NOW)
    assert {i["proposal_id"] for i in items} == {w.pid}
    assert [i["ts"] for i in items][0] == row["ts"]


def test_tally_equals_report_collect(w):
    rows = [fire(w, n) for n in range(5)]
    labels.append_row(w.cfg, "tp", labels.row_id(rows[0]))
    labels.append_row(w.cfg, "tp", labels.row_id(rows[1]))
    labels.append_row(w.cfg, "fp", labels.row_id(rows[2]), "scratch")
    prop = report.collect(w.cfg, 14, NOW)["private"]["proposals"][w.pid]
    t = queue.pending(w.cfg, 50, NOW)[0]["tally"]
    assert (t["tp"], t["fp"]) == (prop["tp"], prop["fp"]) == (2, 1)
    assert t["n"] == prop["rows"] == 5 and t["needed"] == 3


def test_pending_without_proposal_files_uses_dashes(w):
    fire(w, 0)
    for f in (w.data / "proposals" / "approved").glob("*.json"):
        f.unlink()
    (item,) = queue.pending(w.cfg, 50, NOW)
    assert item["hash8"] == "-" and item["mode"] == "-"


def test_a_tp_candidate_row_covers_nothing_so_the_row_stays_pending(w, capsys):
    row = fire(w, 0)
    cs.write_labels(w.cfg, [{"ref": labels.row_id(row), "label": "tp_candidate", "reason_code": "auto_tp_hook_block"}])
    assert [i["id"] for i in queue.pending(w.cfg, 50, NOW)] == [labels.row_id(row)]
    assert queue.LABELLED == ("tp", "fp")


def revoke(w):
    from ace_playbook.compile import approvals
    approvals.append(w.data, {"proposal_id": w.pid, "proposal_sha256": "-", "mode": "revoked"})


def test_rows_of_a_retired_proposal_are_left_out_unless_all(w):
    row = fire(w, 0)
    revoke(w)
    assert queue.pending(w.cfg, 50, NOW) == []
    (item,) = queue.pending(w.cfg, 50, NOW, include_retired=True)
    assert item["id"] == labels.row_id(row)


def test_run_skips_retired_by_default_and_labels_them_with_all(w, capsys):
    row = fire(w, 0)
    revoke(w)
    assert run(w, Script()) == 0 and capsys.readouterr().out.strip() == "queue empty"
    assert queue.run(w.cfg, 50, Script("1", ""), lambda what: True, include_retired=True) == 0
    (lab,) = chain(w)
    assert (lab["ref"], lab["label"], lab["reason_code"]) == (labels.row_id(row), "tp", "human")


# ---------------------------------------------------------------- run
def test_scripted_session_writes_the_expected_rows(w, capsys):
    rows = [fire(w, n) for n in range(5)]
    ask = Script("1", "", "2", "", "", "2", "cleanup under scratch", "3", "q")
    assert run(w, ask) == 0
    got = [(r["ref"], r["label"], r["reason_code"], r.get("note")) for r in chain(w)]
    assert got == [(labels.row_id(rows[0]), "tp", "human", None), (labels.row_id(rows[2]), "fp", "human", "cleanup under scratch")]
    assert ask.prompts.count("fp note (required): ") == 3 and ask.prompts.count("note (optional): ") == 1
    out = capsys.readouterr().out
    assert out.splitlines()[-1] == "labelled 1 tp, 1 fp, skipped 2, chain ok"
    assert out.count("1 tp / 2 fp / 3 skip / q quit") == 5


def test_card_shows_the_six_groups_and_no_command(w, capsys):
    fire(w, 0)
    assert run(w, Script("q")) == 0
    out = capsys.readouterr().out
    for part in ("rm_rf_nonscratch@", "mode observe", "age ", "payloads are hashes only", "session ", "proposal " + w.pid,
                 "event PreToolUse", "1 tp / 2 fp / 3 skip / q quit", "tally:", "needed 5", "chain ok", "rubric = design §7.3"):
        assert part in out, part
    assert "rm -rf" not in out


def test_later_cards_show_the_labels_given_so_far(w, capsys):
    for n in range(2):
        fire(w, n)
    assert run(w, Script("1", "", "q")) == 0
    cards = capsys.readouterr().out.split("[2/2]")
    assert "tp 0  fp 0  needed 5" in cards[0] and "tp 1  fp 0  needed 4" in cards[1]


def test_unknown_key_is_reasked_three_times_then_skips(w):
    fire(w, 0)
    ask = Script("x", "yy", "0")                                      # three bad keys skip the row; nothing more is asked
    assert run(w, ask) == 0 and ask.prompts.count("> ") == 3 and chain(w) == []


def test_scrubbed_note_is_reasked_once_then_the_row_is_skipped(w, capsys):
    rows = [fire(w, n) for n in range(2)]
    assert run(w, Script("2", EMAIL, EMAIL, "1", "ok note")) == 0
    assert [(r["ref"], r["label"], r["note"]) for r in chain(w)] == [(labels.row_id(rows[1]), "tp", "ok note")]
    out = capsys.readouterr()
    assert "refused by the scrubber" in out.err + out.out and EMAIL not in out.out + out.err


def test_eof_stops_cleanly(w, capsys):
    for n in range(2):
        fire(w, n)

    def dry(prompt):
        raise EOFError
    assert run(w, dry) == 0 and chain(w) == []
    assert "labelled 0 tp, 0 fp, skipped 0, chain ok" in capsys.readouterr().out


def test_empty_queue(w, capsys):
    assert run(w, Script()) == 0 and capsys.readouterr().out.strip() == "queue empty"


def test_non_tty_refuses_before_asking(w, capsys):
    fire(w, 0)
    assert run(w, Script(), need_tty=no_tty) == 2
    assert "label --queue needs an interactive terminal" in capsys.readouterr().out


def test_broken_labels_chain_refuses_before_asking(w, capsys):
    rows = [fire(w, n) for n in range(2)]
    labels.append_row(w.cfg, "tp", labels.row_id(rows[0]))
    path = w.cfg.path("labels.jsonl")
    path.write_text(path.read_text().replace('"tp"', '"fp"'))
    assert run(w, Script()) == 1                                      # an empty script fails the test if anything is asked
    assert "chain broken at row 1" in capsys.readouterr().err


def test_limit_below_one_is_a_usage_error(w, capsys):
    assert run(w, Script(), limit=0) == 2 and "--limit" in capsys.readouterr().err


# ---------------------------------------------------------------- the CLI
def _env(w, tmp_path) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)                                         # the child's HOME: its `~` never reaches the operator's key dir
    return {**os.environ, "ACE_PLAYBOOK_CONFIG": w.conf, "HOME": str(home), "PYTHONPATH": str(ROOT)}


def _cli(w, tmp_path, *argv, **kw):
    return subprocess.Popen([sys.executable, "-m", "ace_playbook.compile", *argv], cwd=ROOT, env=_env(w, tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def _drive(master: int, proc: subprocess.Popen, script: list[tuple[str, bytes]], timeout: float = 60) -> bytes:
    """Answer each prompt of `script` when it shows on the terminal, then keep draining the master until the child exits (a session
    leader whose terminal output is unread is not reaped on macOS)."""
    seen, deadline, sent, used = b"", time.monotonic() + timeout, 0, 0
    while proc.poll() is None and time.monotonic() < deadline:
        if select.select([master], [], [], 0.1)[0]:
            seen += os.read(master, 4096)
        if sent < len(script) and script[sent][0].encode() in seen[used:]:
            os.write(master, script[sent][1])
            sent, used = sent + 1, len(seen)
    assert sent == len(script), seen
    return seen


def test_queue_on_a_real_pty(w, tmp_path):
    rows = [fire(w, n) for n in range(2)]
    master, slave = pty.openpty()
    try:
        proc = _cli(w, tmp_path, "label", "--queue", "--limit", "1", stdin=slave, start_new_session=True,
                    preexec_fn=lambda: (fcntl.ioctl(0, termios.TIOCSCTTY, 0), os.tcsetpgrp(0, os.getpgrp())))   # the pty is its terminal
        shown = _drive(master, proc, [("> ", b"1\n"), ("note (optional): ", b"\n")])      # key 1 (tp), then an empty optional note
        out, err = proc.communicate(timeout=30)
    finally:
        os.close(master)
        os.close(slave)
    assert proc.returncode == 0, err
    (row,) = chain(w)
    assert (row["ref"], row["label"], row["reason_code"]) == (labels.row_id(rows[0]), "tp", "human") and "note" not in row
    assert out.splitlines()[-1] == "labelled 1 tp, 0 fp, skipped 0, chain ok" and b"> " in shown


def test_queue_refuses_without_a_terminal_on_the_cli(w, tmp_path):
    fire(w, 0)
    proc = _cli(w, tmp_path, "label", "--queue", stdin=subprocess.DEVNULL)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 2 and "needs an interactive terminal" in err
    assert chain(w) == []


@pytest.mark.parametrize("extra, text", ((["--rows"], "not allowed with argument"), (["--tp", "abcd1234"], "not allowed with argument"),
                                         (["--note", "x"], "--note do not apply to this action")))
def test_queue_does_not_combine_with_other_actions(w, tmp_path, extra, text):
    proc = _cli(w, tmp_path, "label", "--queue", *extra, stdin=subprocess.DEVNULL)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 2 and text in err and "needs an interactive terminal" not in err       # refused for the combination, not the TTY


def test_all_belongs_to_the_queue_only(w, tmp_path):
    proc = _cli(w, tmp_path, "label", "--rows", "--all", stdin=subprocess.DEVNULL)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 2 and "--all do not apply to this action" in err
    proc = _cli(w, tmp_path, "label", "--queue", "--all", stdin=subprocess.DEVNULL)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 2 and "needs an interactive terminal" in err


def test_queue_is_off_the_hook_path():
    code = "import sys, ace_playbook.compile.shadow; sys.exit('ace_playbook.compile.queue' in sys.modules)"
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
