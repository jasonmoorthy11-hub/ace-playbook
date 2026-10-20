"""`compile status` (docs/design/compiler.md 8.x): status.json is built from `report.collect` and nothing else, so every number matches."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ace_playbook.atomic import write_private
from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import approvals, labels, metrics, offline, report, shadow, status
from ace_playbook.compile import state as st

from . import compile_support as cs

ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-10-20T12:00:00Z"
DETS = ("rm_rf_nonscratch", "rm_rf")
RM = {"tool": "Bash", "input": {"command": cs.RM_PROMPT_CMD}}


def approve(w, det, at, mode="observe"):
    """The approved copy plus its approvals row, both stamped `at`."""
    rec = shadow.approved_record(w.props[det], mode, at)
    path = w.data / "proposals" / "approved" / (w.props[det]["id"] + ".json")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(path, json.dumps(rec, indent=1, sort_keys=True) + "\n")
    approvals.append(w.data, {"proposal_id": w.props[det]["id"], "proposal_sha256": rec["proposal_sha256"], "mode": mode, "ts": at})


def pid_of(w, det="rm_rf_nonscratch"):
    return w.props[det]["id"]


def fire(w, det, sid, n, ts="2026-10-18T09:00:{:02d}Z"):
    """`n` distinct rows (one per second) of proposal `det` in session `sid`, stamped before the session's last transcript record
    (a later fire waits for the next offline scan); returns their row ids."""
    return [labels.row_id(cs.put_shadow(w, sid, cs.RM_PROMPT_CMD, ts=ts.format(i), pid=pid_of(w, det))) for i in range(n)]


@pytest.fixture
def w(tmp_path):
    world = cs.real_world(tmp_path, detectors=DETS, approve=False)
    world.cfg.transcripts_root = tmp_path / "projects"
    approve(world, "rm_rf_nonscratch", "2026-10-01T00:00:00Z")
    approve(world, "rm_rf", "2026-10-15T00:00:00Z")
    for sid in ("s1", "s2"):
        cs.put_session(world, sid, [{"human": "tidy"}, RM])
    offline.run(world.cfg, now=cs.NOW)
    return world


def scan(w):
    offline.run(w.cfg, now=cs.NOW)


def build(w, **kw):
    return status.build(w.cfg, now=kw.pop("now", NOW), **kw)


def labelled_world(w):
    """rm_rf_nonscratch: 3 tp, 1 fp, 2 unlabelled; rm_rf: 1 unlabelled."""
    a = fire(w, "rm_rf_nonscratch", "s1", 6)
    fire(w, "rm_rf", "s2", 1)
    cs.write_labels(w.cfg, [{"ref": r, "label": "tp"} for r in a[:3]] + [{"ref": a[3], "label": "fp"}])
    w.labels = [{"ref": r, "label": "tp"} for r in a[:3]] + [{"ref": a[3], "label": "fp"}]
    scan(w)
    return a


def every_label(w):
    """`cs.write_labels` always starts a chain at GENESIS, so a test that adds labels rewrites the file with all of them."""
    w.cfg.path("labels.jsonl").unlink()
    return w.labels


# ------------------------------------------------------------------ one source of truth
def test_every_proposal_number_equals_the_report_collect_number(w):
    labelled_world(w)
    s, d = build(w), report.collect(w.cfg, 14, NOW)
    assert s["report_sha256"] == d["report_sha256"] and s["schema"] == 1 and s["generated_at"] == NOW and s["days"] == 14
    priv, rates = d["private"]["proposals"], d["rates"]["private"]["proposals"]
    assert set(s["proposals"]) == set(priv) == {pid_of(w), pid_of(w, "rm_rf")}
    for pid, got in s["proposals"].items():
        for k in ("detector", "mode", "approved_mode", "rows", "tp", "fp", "unlabelled"):
            assert got[k] == priv[pid][k], (pid, k)
        for k in ("fires", "eligible", "fires_per_eligible"):
            assert got[k] == rates[pid][k], (pid, k)
    a = s["proposals"][pid_of(w)]
    assert (a["tp"], a["fp"], a["unlabelled"], a["eligible"]) == (3, 1, 2, 2) and a["fires"] == 6
    assert s["cost"] == {"p50_ms": d["rates"]["private"]["corpus"]["cost_p50_ms"], "p95_ms": d["rates"]["private"]["corpus"]["cost_p95_ms"],
                         "rows": d["rates"]["private"]["corpus"]["cost_rows"], "threshold_ms": 200}
    assert s["agreement"] == d["rates"]["private"]["corpus"]["agreement"] and s["demotions"] == d["demotions"]["rows"]


def test_precision_uses_metrics_wilson_and_is_insufficient_below_five_labels(w):
    labelled_world(w)
    a = build(w)["proposals"][pid_of(w)]                         # 3 tp + 1 fp = 4 labels
    assert a["precision"] == "INSUFFICIENT" and a["fp_upper_95"] is None
    fire(w, "rm_rf_nonscratch", "s2", 3, ts="2026-10-18T09:30:{:02d}Z")
    rows = [labels.row_id(r) for r in cs.shadow_rows(w.data) if r["ts"].startswith("2026-10-18T09:30")]
    cs.write_labels(w.cfg, [*every_label(w), *({"ref": r, "label": "tp"} for r in rows)])         # 6 tp + 1 fp
    a = build(w)["proposals"][pid_of(w)]
    assert (a["tp"], a["fp"]) == (6, 1) and a["precision"] == metrics.wilson(6, 7) and a["fp_upper_95"] is None


def test_zero_false_positives_give_the_upper_bound_of_the_rule_of_three_family(w):
    ids = fire(w, "rm_rf_nonscratch", "s1", 10)
    cs.write_labels(w.cfg, [{"ref": r, "label": "tp"} for r in ids])
    scan(w)
    a = build(w)["proposals"][pid_of(w)]
    assert (a["tp"], a["fp"]) == (10, 0) and a["fp_upper_95"] == 0.259 and a["precision"] == metrics.wilson(10, 10)
    assert build(w)["proposals"][pid_of(w, "rm_rf")]["fp_upper_95"] is None            # n = 0: no bound


def test_queue_lists_unlabelled_fires_oldest_first_without_session_ids(w):
    a = labelled_world(w)
    s = build(w)
    q = s["queue"]
    assert [x["id"] for x in q][1:] == a[4:6] and len(q) == 3 and s["queue_total"] == 3         # the rm_rf row is the oldest
    assert [x["ts"] for x in q] == sorted(x["ts"] for x in q)
    assert set(q[0]) == {"id", "ts", "proposal_id", "detector", "session8", "event", "cause"}
    assert (q[0]["detector"], q[0]["session8"]) == ("rm_rf", st.session_hash("s2")[:8])
    assert (q[1]["detector"], q[1]["session8"], q[1]["event"], q[1]["cause"]) == ("rm_rf_nonscratch", st.session_hash("s1")[:8], "PreToolUse", "detector")
    assert s["proposals"][pid_of(w)]["unlabelled"] + s["proposals"][pid_of(w, "rm_rf")]["unlabelled"] == 3


def test_queue_is_capped_at_fifty_but_the_total_is_not(w):
    fire(w, "rm_rf_nonscratch", "s1", 55, ts="2026-10-18T09:{:02d}:00Z")
    scan(w)
    s = build(w)
    assert len(s["queue"]) == 50 and s["queue_total"] == 55


# ------------------------------------------------------------------ verdicts
def row(**over):
    base = {"mode": "observe", "approved_mode": "observe", "unlabelled": 0, "eligible": 4, "tp": 5, "fp": 0,
            "approved_at": "2026-10-01T00:00:00Z"}
    return {**base, **over}


@pytest.mark.parametrize("over, want", [
    ({"mode": "disabled"}, "retired"),
    ({"mode": "revoked"}, "retired"),
    ({"mode": "disabled", "unlabelled": 3}, "retired"),                              # retired wins over labelling
    ({"unlabelled": 2}, "label 2 fires"),
    ({"unlabelled": 2, "eligible": 0}, "label 2 fires"),
    ({"eligible": 0}, "silent: eligible 0"),
    ({"eligible": None}, "silent: eligible 0"),
    ({"approved_at": "2026-10-10T08:00:00Z"}, "warn earliest 2026-10-24"),
    ({"approved_at": "2026-10-10T08:00:00Z", "tp": 0}, "warn earliest 2026-10-24"),
    ({"tp": 4}, "needs 5 human TPs (has 4)"),
    ({"tp": 5, "fp": 6}, "noisy: add an exception or retire"),
    ({"tp": 3, "fp": 4}, "needs 5 human TPs (has 3)"),                               # too few TPs is named before noise
    ({"tp": 5, "fp": 5}, "warn eligible"),                                           # exactly half is not above half
    ({}, "warn eligible"),
    ({"approved_at": "2026-10-06T12:00:00Z"}, "warn eligible"),                      # exactly 14 days: the window is complete
])
def test_verdict_rules_in_order(over, want):
    assert status.verdict(row(**over), NOW) == want


def test_a_revoked_approval_row_retires_the_proposal_even_without_a_copy(w):
    fire(w, "rm_rf", "s1", 1)
    approvals.append(w.data, {"proposal_id": pid_of(w, "rm_rf"), "proposal_sha256": "-", "mode": "revoked", "ts": "2026-10-17T00:00:00Z"})
    (w.data / "proposals" / "approved" / (pid_of(w, "rm_rf") + ".json")).unlink()
    assert build(w)["proposals"][pid_of(w, "rm_rf")]["verdict"] == "retired"


def test_a_new_approval_shows_the_warn_earliest_date_in_json_and_verdict(w):
    p = build(w)["proposals"][pid_of(w, "rm_rf")]
    assert p["approved_at"] == "2026-10-15T00:00:00Z" and p["warn_earliest"] == "2026-10-29T00:00:00Z"
    assert p["verdict"] == "warn earliest 2026-10-29" and p["strength"] == "weak"
    assert build(w)["proposals"][pid_of(w)]["strength"] == "strong"


# ------------------------------------------------------------------ measurement
def test_measurement_start_is_the_newest_observe_warn_or_block_row_and_skips_revoked(tmp_path):
    w = cs.real_world(tmp_path, detectors=DETS, approve=False)
    assert build(w)["measurement"] == {"start": None, "day": None, "week1_due": None, "keyed": False,
                                       "chains": {"approvals": "ok", "demotions": "ok", "labels": "ok"}}
    approve(w, "rm_rf_nonscratch", "2026-10-18T16:10:26Z")
    approve(w, "rm_rf", "2026-10-19T08:00:00Z")
    approvals.append(w.data, {"proposal_id": pid_of(w, "rm_rf"), "proposal_sha256": "-", "mode": "revoked", "ts": "2026-10-19T20:00:00Z"})
    m = build(w)["measurement"]
    assert (m["start"], m["day"], m["week1_due"]) == ("2026-10-19T08:00:00Z", 2, "2026-10-26T08:00:00Z")
    assert build(w, now="2026-10-19T09:00:00Z")["measurement"]["day"] == 1
    assert build(w)["proposals"][pid_of(w)]["approved_at"] == "2026-10-18T16:10:26Z"


def test_a_broken_labels_chain_and_a_broken_approvals_chain_are_named(w):
    labelled_world(w)
    with open(w.cfg.path("labels.jsonl"), "a") as fh:
        fh.write('{"junk": 1}\n')
    with open(w.cfg.path("approvals.jsonl"), "a") as fh:
        fh.write('{"junk": 1}\n')
    s = build(w)
    assert s["measurement"]["chains"]["labels"] == "broken:5" and s["measurement"]["chains"]["approvals"] == "broken:3"
    assert "approvals:broken:3" in s["heartbeat"]["notes"] and "labels:broken:5" in s["heartbeat"]["notes"]
    assert "chains BROKEN" in status.render(s).splitlines()[0]


# ------------------------------------------------------------------ heartbeat (2c)
def test_a_world_without_rows_has_null_heartbeats_and_the_text_says_never(tmp_path):
    w = cs.real_world(tmp_path, approve=False)
    h = build(w)["heartbeat"]
    assert (h["last_hook_row"], h["last_live_fire"], h["last_offline_scan"], h["last_nightly_ok"]) == (None,) * 4
    text = status.render(build(w))
    assert "hook never" in text and "live fire never" in text and "offline scan never" in text


def test_heartbeat_ages_come_from_row_stamps_and_the_offline_state_mtime(w):
    cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD, ts="2026-10-20T09:00:00Z", pid=pid_of(w))
    cs.put_shadow(w, "s1", "ls", ts="2026-10-20T10:00:00Z", pid=pid_of(w))
    rows = cs.shadow_rows(w.data)
    rows[1]["would_fire"] = False
    w.cfg.path("shadow.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    state = w.cfg.path("offline_state.json")
    state.write_text("{}")
    os.utime(state, (cs.NOW - 3 * 86400, cs.NOW - 3 * 86400))                       # 2026-10-16T12:00:00Z
    h = build(w)["heartbeat"]
    assert (h["last_hook_row"], h["last_live_fire"], h["last_offline_scan"]) == (
        "2026-10-20T10:00:00Z", "2026-10-20T09:00:00Z", "2026-10-16T12:00:00Z")
    text = status.render(build(w))
    assert "hook 2h" in text and "live fire 3h" in text and "offline scan 4d" in text


def test_the_nightly_health_record_feeds_last_nightly_ok_and_a_failure_is_a_note(w):
    w.cfg.path("health.json").write_text(json.dumps({"last_run": "2026-10-20T03:00:00Z", "ok": True, "consecutive_failures": 0, "last_error": ""}))
    assert build(w)["heartbeat"]["last_nightly_ok"] == "2026-10-20T03:00:00Z"
    w.cfg.path("health.json").write_text(json.dumps({"last_run": "2026-10-20T03:00:00Z", "ok": False, "consecutive_failures": 1, "last_error": "x"}))
    h = build(w)["heartbeat"]
    assert h["last_nightly_ok"] is None and "nightly:failed" in h["notes"]


def test_chain_key_missing_is_noted_on_a_keyed_install_and_absent_otherwise(w, monkeypatch):
    s = build(w)
    assert s["measurement"]["keyed"] is False and "chain_key:missing" not in s["heartbeat"]["notes"]
    monkeypatch.setenv("ACE_PLAYBOOK_KEYED", "1")                                     # keyed by the marker, no key file
    s = build(w)
    assert s["measurement"]["keyed"] is True and "chain_key:missing" in s["heartbeat"]["notes"]
    assert s["measurement"]["chains"]["approvals"].startswith("broken:") and s["measurement"]["chains"]["demotions"] == "broken:key_missing"


def test_a_detector_with_eligible_sessions_and_no_fires_is_silent(w):
    scan(w)
    notes = build(w)["heartbeat"]["notes"]
    assert "silent:rm_rf_nonscratch" in notes and "silent:rm_rf" in notes
    fire(w, "rm_rf", "s1", 1)
    scan(w)
    notes = build(w)["heartbeat"]["notes"]
    assert "silent:rm_rf" not in notes and "silent:rm_rf_nonscratch" in notes


# ------------------------------------------------------------------ the verb
def run(w, *args, capsys):
    code = cli.main(["--config", w.conf, "status", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_text_screen_has_the_header_one_row_per_proposal_the_queue_and_the_cost(w, capsys):
    labelled_world(w)
    code, out, err = run(w, capsys=capsys)
    lines = out.splitlines()
    assert (code, err) == (0, "") and lines[0].startswith("ace status  day ") and "of 7  keyed no  chains ok  report " in lines[0]
    assert lines[0].endswith(report.source_sha256()[:8])
    assert sum(ln.startswith("P-") for ln in lines) == 2 and "label 2 fires" in out and "INSUFFICIENT" in out
    assert "queue: 3 unlabelled (oldest " in out and "cost p95 " in out and " ms vs 200" in out


def test_json_is_the_status_json_and_the_file_is_written_0600_without_a_tmp(w, capsys):
    os.umask(0o022)
    code, out, _ = run(w, "--json", capsys=capsys)
    path = w.cfg.path("status.json")
    got = json.loads(path.read_text())
    assert code == 0 and json.loads(out) == got
    assert got["schema"] == 1 and set(got) >= {"measurement", "heartbeat", "proposals", "queue", "cost", "agreement", "demotions"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and not list(w.data.glob("status.json.tmp"))


def test_out_may_name_a_file_outside_the_data_dir(w, capsys, tmp_path):
    target = tmp_path / "elsewhere" / "s.json"
    code, out, _ = run(w, "--out", str(target), capsys=capsys)
    assert code == 0 and json.loads(target.read_text())["schema"] == 1 and "ace status" in out
    assert not w.cfg.path("status.json").exists() and stat.S_IMODE(target.stat().st_mode) == 0o600


def test_days_is_validated_like_report(w, capsys):
    code, out, err = run(w, "--days", "0", capsys=capsys)
    assert (code, out) == (2, "") and err.startswith("error: --days must be 1..")
    assert not w.cfg.path("status.json").exists()


def test_a_corpus_mixup_refuses_with_the_report_exit_code(w, capsys):
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), {"ts": NOW, "session": "a", "proposal_id": "P-0a1b2c3d", "payload_sha256": "b", "corpus": "synthetic"})
    code, _, err = run(w, capsys=capsys)
    assert code == 2 and err.startswith("error: ")


def test_status_json_holds_no_session_hash_lesson_text_or_data_path(w, capsys):
    labelled_world(w)
    _, out, _ = run(w, "--json", capsys=capsys)
    assert st.session_hash("s1") not in out and str(w.data) not in out and "/" + "Users/" not in out      # split: CI greps the source


def test_the_hook_path_never_imports_status_or_digest():
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import ace_playbook.compile.shadow; "
            "bad = [m for m in ('ace_playbook.compile.status', 'ace_playbook.compile.digest') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-I", "-c", code, str(ROOT)], cwd=ROOT).returncode == 0


def test_status_stays_within_its_line_budget():
    assert len((ROOT / "ace_playbook" / "compile" / "status.py").read_text().splitlines()) <= 300
