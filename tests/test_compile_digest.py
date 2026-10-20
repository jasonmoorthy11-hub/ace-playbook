"""`compile digest` (docs/design/compiler.md 8.x): six plain-text blocks rendered from status.json, one log line per run."""

import json
import os
import stat
import subprocess
import sys

import pytest

from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import digest, status

from . import compile_support as cs
from .test_compile_status import NOW, ROOT, build, fire, labelled_world, pid_of, w  # noqa: F401 - w is a fixture
from .test_hooks import HOOKS, env  # noqa: F401 - env is a fixture

HEADINGS = ["status", "needs you", "detectors", "health", "changes", "next"]
LABEL_CMD = "uv run python -m ace_playbook.compile label --queue"


def block(text: str, name: str) -> list[str]:
    """The lines under heading `name` (up to the next blank line)."""
    lines = text.splitlines()
    return lines[lines.index(name) + 1:][:next((i for i, ln in enumerate(lines[lines.index(name) + 1:]) if not ln), None)]


def headings(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln in HEADINGS]


def spiky(w, today: int, others: list[int], days: int = 14) -> dict:
    """The real status of `w` with an invented rows-per-day series for its first proposal (the last entry is today, 2026-10-20)."""
    s = build(w, days=days)
    pid = pid_of(w)
    series = [(f"2026-10-{20 - len(others) + i:02d}", n) for i, n in enumerate(others)] + [("2026-10-20", today)]
    s["per_day"] = [{"date": d, "proposal_id": pid, "rows": n} for d, n in series if n]
    return s


def test_six_headings_appear_once_and_in_order(w):
    labelled_world(w)
    text = digest.render(build(w), None)
    assert headings(text) == HEADINGS and all(text.splitlines().count(h) == 1 for h in HEADINGS)


def test_status_block_names_the_protocol_the_report_hash_the_day_keyed_and_chains(w):
    s = build(w)
    lines = block(digest.render(s, None), "status")
    assert "shadow-v1" in lines[0] and s["report_sha256"][:8] in lines[0] and "day 6 of 7" in lines[0]
    assert "keyed no" in lines[0] and "chains ok" in lines[0]


def test_needs_you_has_the_queue_count_the_oldest_age_and_the_exact_label_command(w):
    labelled_world(w)
    lines = block(digest.render(build(w), None), "needs you")
    assert lines[0] == "3 unlabelled fires (oldest 2d)" and lines[1] == "run: " + LABEL_CMD
    empty = block(digest.render({**build(w), "queue": [], "queue_total": 0}, None), "needs you")
    assert empty == ["nothing to label"]


def test_the_detector_table_is_the_status_table(w):
    labelled_world(w)
    s = build(w)
    lines = block(digest.render(s, None), "detectors")
    assert lines == status.table(s) and lines[0].split()[:3] == ["proposal", "detector", "mode"]


def test_health_flags_silent_detectors_stale_heartbeats_and_chain_notes(w):
    s = build(w)                                                        # no fires, never a nightly run, no offline_state.json
    lines = block(digest.render(s, None), "health")
    assert "silent: rm_rf_nonscratch" in lines and "silent: rm_rf" in lines
    assert any(ln.startswith("stale: hook never") for ln in lines) and any(ln.startswith("stale: nightly never") for ln in lines)
    s["heartbeat"] = {**s["heartbeat"], "last_hook_row": "2026-10-18T23:00:00Z", "last_nightly_ok": "2026-10-20T03:00:00Z",
                      "last_offline_scan": "2026-10-20T03:00:00Z", "notes": ["chain_key:missing"]}
    s["measurement"]["chains"]["approvals"] = "broken:3"
    lines = block(digest.render(s, None), "health")
    assert "stale: hook 37h" in lines and not any(ln.startswith("stale: nightly") for ln in lines)       # 37 h is over 36
    assert "note: chain_key:missing" in lines and "chain approvals broken:3" in lines
    s["heartbeat"]["last_hook_row"] = "2026-10-19T00:30:00Z"                                         # 35.5 h: fresh enough
    assert not any(ln.startswith("stale: hook") for ln in block(digest.render(s, None), "health"))


def test_a_quiet_system_says_all_clear(w):
    s = build(w)
    s["heartbeat"] = {**s["heartbeat"], "last_hook_row": NOW, "last_nightly_ok": NOW, "last_offline_scan": NOW, "notes": []}
    assert block(digest.render(s, None), "health") == ["all clear"]


@pytest.mark.parametrize("today, others, spike", [
    (4, [1] * 13, True),          # 4 today against a 17 / 14 = 1.2 mean: more than 3x
    (3, [0] * 13, True),          # the floor of three fires
    (2, [0] * 13, False),         # fewer than three fires is never a spike
    (4, [2] * 13, False),         # 4 against a 30 / 14 = 2.1 mean: under 3x
    (7, [2] * 13, False),         # 7 against 33 / 14 = 2.4: 7.1 is the line
    (8, [2] * 13, True),          # 8 against 2.4: over 3x
])
def test_spike_rule_is_three_times_the_daily_mean_of_the_window_with_at_least_three_fires(w, today, others, spike):
    lines = block(digest.render(spiky(w, today, others), None), "health")
    assert any(ln.startswith("spike: " + pid_of(w)) for ln in lines) is spike


def test_changes_lists_approval_and_demotion_rows_stamped_after_since(w):
    s = build(w)
    s["demotions"] = [{"proposal_id": "P-0a1b2c3d", "to_mode": "observe", "cause": "evasion_pair", "ts": "2026-10-19T05:00:00Z",
                       "review_required": True, "lifted": False},
                      {"proposal_id": "P-0a1b2c3d", "to_mode": "warn", "cause": "fp_rate", "ts": "2026-10-10T05:00:00Z",
                       "review_required": False, "lifted": True}]
    rows = [{"ts": "2026-10-19T08:00:00Z", "proposal_id": "P-0a1b2c3d", "mode": "warn"},
            {"ts": "2026-10-01T08:00:00Z", "proposal_id": "P-0a1b2c3d", "mode": "observe"}]
    lines = block(digest.render(s, "2026-10-15T00:00:00Z", rows), "changes")
    assert lines == ["2026-10-19T05:00:00Z demotion P-0a1b2c3d observe evasion_pair", "2026-10-19T08:00:00Z approval P-0a1b2c3d warn"]
    assert block(digest.render(s, "2026-10-20T00:00:00Z", rows), "changes") == ["none"]
    assert len(block(digest.render(s, "2026-09-01T00:00:00Z", rows), "changes")) == 4


def test_the_default_since_is_the_last_24_hours(w):
    s = build(w)
    rows = [{"ts": "2026-10-19T13:00:00Z", "proposal_id": "P-0a1b2c3d", "mode": "warn"},
            {"ts": "2026-10-19T11:00:00Z", "proposal_id": "P-0a1b2c3d", "mode": "observe"}]
    assert block(digest.render(s, None, rows), "changes") == ["2026-10-19T13:00:00Z approval P-0a1b2c3d warn"]


def test_next_has_the_week_one_date_and_each_proposals_earliest_warn_date(w):
    lines = block(digest.render(build(w), None), "next")
    assert lines[0] == "week 1 due 2026-10-22T00:00:00Z"
    assert f"{pid_of(w, 'rm_rf')} rm_rf: warn earliest 2026-10-29" in lines
    assert not any(pid_of(w) in ln for ln in lines)                       # approved 2026-10-01: the 14 days are over


def test_next_says_week_one_has_not_started_before_any_approval(tmp_path):
    empty = cs.real_world(tmp_path, approve=False)
    assert block(digest.render(build(empty), None), "next") == ["week 1 not started"]


# ------------------------------------------------------------------ the verb and the log
def run(w, *args, capsys):
    code = cli.main(["--config", w.conf, "digest", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def log_lines(w) -> list[str]:
    p = w.cfg.path("digest.log")
    return p.read_text().splitlines() if p.exists() else []


def test_each_run_prints_the_digest_and_appends_exactly_one_log_line_0600(w, capsys):
    os.umask(0o022)
    labelled_world(w)
    for n in (1, 2):
        code, out, err = run(w, capsys=capsys)
        assert (code, err) == (0, "") and headings(out) == HEADINGS
        assert len(log_lines(w)) == n
    row = json.loads(log_lines(w)[-1])
    assert set(row) == {"ts", "report_sha256", "queue", "notes"} and row["queue"] == 3 and isinstance(row["notes"], list)
    assert len(row["report_sha256"]) == 64 and stat.S_IMODE(w.cfg.path("digest.log").stat().st_mode) == 0o600


def test_since_and_days_are_validated_and_a_bad_run_logs_nothing(w, capsys):
    for args in (("--since", "yesterday"), ("--since", "2026-13-45"), ("--days", "0")):
        code, out, err = run(w, *args, capsys=capsys)
        assert (code, out) == (2, "") and err.startswith("error: ")
    assert log_lines(w) == []
    code, out, _ = run(w, "--since", "2026-10-01", capsys=capsys)             # a bare date is midnight
    assert code == 0 and "approval" in out


def test_run_quiet_logs_and_returns_the_queue_and_notes_without_printing(w, capsys):
    labelled_world(w)
    got = digest.run_quiet(w.cfg)
    assert capsys.readouterr().out == "" and set(got) == {"queue", "notes"} and got["queue"] == 3 and isinstance(got["notes"], list)
    assert len(log_lines(w)) == 1
    assert digest.run_quiet(w.cfg, write=False)["queue"] == 3 and len(log_lines(w)) == 1


def test_the_nightly_entry_point_exits_zero_and_reports_the_digest(env):  # noqa: F811
    e, cfg = env
    p = subprocess.run([sys.executable, str(HOOKS / "nightly_sweep.py")], capture_output=True, text=True, env=e)
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["digest"] == {"queue": 0, "notes": []}
    assert len((cfg.data_dir / "digest.log").read_text().splitlines()) == 1


def test_an_offline_dry_run_leaves_digest_log_alone(env):  # noqa: F811
    e, cfg = env
    p = subprocess.run([sys.executable, str(HOOKS / "nightly_sweep.py"), "--offline-dry-run"], capture_output=True, text=True, env=e)
    assert p.returncode == 0 and "digest" in json.loads(p.stdout) and not (cfg.data_dir / "digest.log").exists()


def test_a_failing_digest_is_reported_by_type_and_the_sweep_still_exits_zero(env):  # noqa: F811
    e, cfg = env
    (cfg.data_dir / "digest.log").mkdir(parents=True)                  # the log path is a directory: the append fails
    p = subprocess.run([sys.executable, str(HOOKS / "nightly_sweep.py")], capture_output=True, text=True, env=e)
    assert p.returncode == 0 and json.loads(p.stdout)["digest"] == {"error": "IsADirectoryError"} and "sweep" in json.loads(p.stdout)


def test_digest_stays_within_its_line_budget():
    assert len((ROOT / "ace_playbook" / "compile" / "digest.py").read_text().splitlines()) <= 200
