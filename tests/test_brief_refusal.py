"""The brief's last refusal reason: `brief refusal <reason>` ledger rows, the fail() paths of hooks/brief.sh, and the `last refusal:` line of `brief status`."""

import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ace_playbook.compile import brief, brief_audit as ba

from . import brief_support as bs
from .test_brief_sh import World

ROOT = Path(__file__).resolve().parent.parent
FMT = "%Y-%m-%dT%H:%M:%SZ"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(bs.config(tmp_path)))
    return tmp_path


def run(capsys, *argv):
    code = brief.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def ledger(tmp_path):
    path = tmp_path / "brief" / "brief_ledger.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime(FMT)


def put_rows(tmp_path, *rows):
    d = tmp_path / "brief"
    d.mkdir(exist_ok=True)
    (d / "brief_ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def refusal_line(capsys):
    return next(ln for ln in run(capsys, "status")[1].splitlines() if ln.startswith("last refusal:"))


def test_the_verb_appends_one_private_row_with_the_reason(env, capsys):
    assert run(capsys, "refusal", "digest refused")[0] == 0
    [row] = ledger(env)
    assert set(row) == {"ts", "stage", "refused"} and row["stage"] == "run" and row["refused"] == "digest refused" and row["ts"].endswith("Z")
    path = env / "brief" / "brief_ledger.jsonl"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600 and len(path.read_text().splitlines()) == 1


def test_a_reason_outside_the_fixed_set_is_other_and_never_free_text(env, capsys):
    for text in ("/" + "Users/someone/secret", "FAKE-MAIL-BODY", "", "stage 9 failed", "digest refused; rm"):
        assert run(capsys, "refusal", text)[0] == 0
    assert [r["refused"] for r in ledger(env)] == ["other"] * 5
    assert "FAKE" not in (env / "brief" / "brief_ledger.jsonl").read_text()


def test_the_exit_code_in_a_stage_failed_reason_is_dropped(env, capsys):
    run(capsys, "refusal", "stage 2 failed (exit 7)")
    assert [r["refused"] for r in ledger(env)] == ["stage 2 failed"]


def test_every_fail_literal_of_the_scripts_is_in_the_fixed_set():
    import re
    texts = "".join((ROOT / "hooks" / f).read_text() for f in ("brief.sh", "brief_lib.sh"))
    literals = set(re.findall(r'fail "([^"$]+?)(?: \(exit[^"]*)?"', texts))
    assert literals >= {"config", "lock", "digest refused", "brief refused", "state file", "tool audit failed", "due check failed"}
    assert {x for x in literals if x not in ba.REASONS} <= {"stage 1 timed out", "stage 2 timed out"}      # their rows are the timeout rows


def test_status_prints_none_when_there_is_no_refusal_row(env, capsys):
    out = run(capsys, "status")[1].splitlines()
    assert out[:3] == ["last brief: never", "needs you: 0", "ledger rows: 0"] and out[3:] == ["last refusal: none", "last stage costs: none"]
    put_rows(env, {"ts": ago(hours=1), "stage": "stage1", "cost_usd": 0.1})
    assert refusal_line(capsys) == "last refusal: none"


def test_status_renders_the_last_of_mixed_drift_timeout_and_refused_rows_with_the_age(env, capsys):
    drift = {"ts": ago(days=3), "stage": "stage1", "drift": ["X"], "unknown_tools": 0}
    timeout = {"ts": ago(hours=5), "stage": "stage2", "timeout": "stage2"}
    refused = {"ts": ago(minutes=7), "stage": "run", "refused": "brief refused"}
    cost = {"ts": ago(seconds=5), "stage": "stage1", "cost_usd": 0.2}
    put_rows(env, drift)
    assert refusal_line(capsys) == "last refusal: tool drift 3d"
    put_rows(env, drift, timeout)
    assert refusal_line(capsys) == "last refusal: stage2 timeout 5h"
    put_rows(env, drift, timeout, refused, cost)
    assert refusal_line(capsys) == "last refusal: brief refused 7m"
    lines = run(capsys, "status")[1].splitlines()
    assert lines[2:] == ["ledger rows: 4", "last refusal: brief refused 7m", "last stage costs: stage1 $0.2"]


def test_status_shows_a_forged_reason_or_stage_as_other_and_survives_a_bad_stamp(env, capsys):
    put_rows(env, {"ts": "not a time", "stage": "run", "refused": "/" + "Users/x/secret"})
    assert refusal_line(capsys) == "last refusal: other ?"
    put_rows(env, {"ts": ago(hours=1), "stage": ["x"], "timeout": "x"})
    assert refusal_line(capsys).startswith("last refusal: ") and "[" not in refusal_line(capsys)


# ------------------------------------------------------------------ hooks/brief.sh
def test_a_failing_stage_through_the_fake_claude_leaves_a_refusal_row_and_status_says_so(tmp_path):
    w = World(tmp_path)
    p = w.run("--force", ACE_FAKE_FAIL="stage1")
    assert p.returncode == 1 and p.stderr == "brief: stage 1 failed (exit 1)\n"
    rows = w.ledger()
    assert [r["stage"] for r in rows] == ["stage1", "run"] and rows[-1]["refused"] == "stage 1 failed"
    done = subprocess.run([str(ROOT / "hooks" / "brief.sh"), "--force"], capture_output=True, text=True, env={**w.env, "ACE_FAKE_FAIL": "stage2"},
                          timeout=120, cwd=tmp_path)
    assert done.returncode == 1 and w.ledger()[-1]["refused"] == "stage 2 failed"


def test_an_unparseable_digest_is_a_digest_refused_row(tmp_path):
    w = World(tmp_path)
    w.set_answers(digest="not json at all")
    p = w.run("--force")
    assert p.returncode == 1 and w.ledger()[-1]["refused"] in ("digest refused", "stage 1 gave no result")


def test_a_tool_drift_run_leaves_only_its_drift_row_and_status_says_tool_drift(tmp_path, monkeypatch, capsys):
    w = World(tmp_path)
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", str(w.conf))
    p = w.run("--force", ACE_FAKE_INIT_TOOLS="Bash,ToolSearch")
    assert p.returncode == 2
    assert [r["stage"] for r in w.ledger() if "refused" in r] == [] and any("drift" in r for r in w.ledger())
    assert refusal_line(capsys).startswith("last refusal: tool drift ")


def test_a_timeout_leaves_the_timeout_row_and_no_second_refusal_row(tmp_path):
    w = World(tmp_path)
    p = w.run("--force", ACE_FAKE_SLEEP="30", ACE_BRIEF_STAGE_TIMEOUT="1", ACE_BRIEF_KILL_GRACE="1")
    assert p.returncode == 1 and p.stderr.strip().endswith("stage 1 timed out")
    assert [("timeout" in r, "refused" in r) for r in w.ledger()] == [(True, False)]


def test_the_quiet_exits_leave_no_refusal_row(tmp_path):
    w = World(tmp_path)
    assert w.run("--force").returncode == 0
    before = w.ledger()
    p = w.run()                                                                     # inside the dedupe window: rc 3, quiet exit 0
    assert (p.returncode, p.stdout, p.stderr) == (0, "", "") and w.ledger() == before


def test_a_run_whose_config_fails_leaves_stderr_only(tmp_path):
    w = World(tmp_path)
    (tmp_path / "bad.json").write_text("{")
    p = w.run("--force", ACE_PLAYBOOK_CONFIG=str(tmp_path / "bad.json"))
    assert p.returncode == 1 and p.stderr.endswith("brief: config\n") and not (w.dir / "brief_ledger.jsonl").exists()
