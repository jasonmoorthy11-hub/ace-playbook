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
from ace_playbook.compile import approvals, demotions, labels, metrics, offline, proposal, queue, report, seal, shadow, status
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


def hum(ref, label):
    """A label row the ladder counts: reason_code human."""
    return {"ref": ref, "label": label, "reason_code": "human"}


def pid_of(w, det="rm_rf_nonscratch"):
    return w.props[det]["id"]


def fire(w, det, sid, n, ts="2026-10-18T09:00:{:02d}Z", mode="observe"):
    """`n` distinct rows (one per second) of proposal `det` in session `sid`, stamped before the session's last transcript record
    (a later fire waits for the next offline scan), written in hook mode `mode`; returns their row ids."""
    return [labels.row_id(cs.put_shadow(w, sid, cs.RM_PROMPT_CMD, mode=mode, ts=ts.format(i), pid=pid_of(w, det))) for i in range(n)]


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
    cs.write_labels(w.cfg, [hum(r, "tp") for r in a[:3]] + [hum(a[3], "fp")])
    w.labels = [hum(r, "tp") for r in a[:3]] + [hum(a[3], "fp")]
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
    corpus = d["rates"]["private"]["corpus"]
    ag = corpus["agreement"] if isinstance(corpus["agreement"], dict) else {"live_only": None, "offline_only": None}
    assert s["agreement"] == {**ag, "awaiting_scan": corpus["awaiting_offline_scan"]} and s["demotions"] == d["demotions"]["rows"]


def test_precision_uses_metrics_wilson_and_is_insufficient_below_five_labels(w):
    labelled_world(w)
    a = build(w)["proposals"][pid_of(w)]                         # 3 tp + 1 fp = 4 labels
    assert a["precision"] == "INSUFFICIENT" and a["fp_upper_95"] is None
    fire(w, "rm_rf_nonscratch", "s2", 3, ts="2026-10-18T09:30:{:02d}Z")
    rows = [labels.row_id(r) for r in cs.shadow_rows(w.data) if r["ts"].startswith("2026-10-18T09:30")]
    cs.write_labels(w.cfg, [*every_label(w), *(hum(r, "tp") for r in rows)])         # 6 tp + 1 fp
    a = build(w)["proposals"][pid_of(w)]
    assert (a["tp"], a["fp"]) == (6, 1) and a["precision"] == metrics.wilson(6, 7) and a["fp_upper_95"] is None


def test_zero_false_positives_give_the_upper_bound_of_the_rule_of_three_family(w):
    ids = fire(w, "rm_rf_nonscratch", "s1", 10)
    cs.write_labels(w.cfg, [hum(r, "tp") for r in ids])
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
    base = {"mode": "observe", "revoked": False, "cap": None, "cap_note": "approve --mode observe restarts",
            "approvals_mode": "observe", "backing": "ok", "approved_at": "2026-10-01T00:00:00Z",
            "unlabelled": 0, "to_label": 0, "eligible": 4, "strength": "strong",
            "window_start": "2026-10-01T00:00:00Z", "tp_window": 5, "fp_window": 0, "compilable": "block", "event": "PreToolUse"}
    return {"approved_mode": over.get("mode", "observe"), **base, **over}


@pytest.mark.parametrize("over, want", [
    ({"mode": "disabled"}, "retired"),
    ({"mode": "revoked"}, "retired"),
    ({"approved_mode": "disabled"}, "retired"),
    ({"revoked": True}, "retired"),
    ({"mode": "disabled", "to_label": 3}, "retired"),                                # retired wins over labelling
    ({"to_label": 2}, "label 2 fires"),
    ({"to_label": 2, "eligible": 0}, "label 2 fires"),
    ({"to_label": 3, "unlabelled": 1}, "label 3 fires"),                             # N is to_label, not the report's unlabelled
    ({"to_label": 0, "unlabelled": 4}, "warn eligible"),
    ({"eligible": 0}, "silent: eligible 0"),
    ({"eligible": None}, "silent: eligible 0"),
    ({"window_start": "2026-10-10T08:00:00Z"}, "warn earliest 2026-10-24"),
    ({"window_start": "2026-10-10T08:00:00Z", "tp_window": 0}, "warn earliest 2026-10-24"),
    ({"window_start": "2026-10-06T12:00:01Z"}, "warn earliest 2026-10-20"),          # 13 d 23 h 59 m 59 s: one second short
    ({"window_start": "2026-10-06T12:00:00Z"}, "warn eligible"),                      # exactly 14 days: the window is complete
    ({"window_start": None}, "warn eligible"),                                        # no window known: the clock cannot hold it back
    ({"tp_window": 4}, "needs 5 human TPs (has 4)"),
    ({"tp_window": 5, "fp_window": 6}, "noisy: add an exception or retire"),
    ({"tp_window": 3, "fp_window": 4}, "needs 5 human TPs (has 3)"),                 # too few TPs is named before noise
    ({"tp_window": 5, "fp_window": 5}, "not eligible: 5 FPs (warn needs 0)"),         # exactly half is not noisy, but warn needs 0 FPs
    ({"tp_window": 5, "fp_window": 1}, "not eligible: 1 FPs (warn needs 0)"),
    ({"tp_window": 5, "fp_window": 0, "strength": "weak"}, "observe only: weak detector"),
    ({"strength": None}, "observe only: weak detector"),
    ({}, "warn eligible"),
    # the ladder's rungs: observe -> warn -> block, and nothing above block
    ({"compilable": "observe"}, "observe only: detector allows observe"),               # a strong detector that may not reach warn
    ({"compilable": "none"}, "observe only: detector allows none"),
    ({"compilable": "warn"}, "warn eligible"),
    ({"mode": "warn", "approved_mode": "warn"}, "block eligible"),
    ({"mode": "warn", "window_start": "2026-10-10T08:00:00Z"}, "block earliest 2026-10-24"),
    ({"mode": "warn", "tp_window": 4}, "needs 5 human TPs (has 4)"),
    ({"mode": "warn", "tp_window": 5, "fp_window": 6}, "noisy: add an exception or retire"),
    ({"mode": "warn", "fp_window": 1}, "not eligible: 1 FPs (block needs 0)"),
    ({"mode": "warn", "compilable": "warn"}, "warn only: detector allows warn"),         # block needs a detector that allows block
    ({"mode": "warn", "compilable": "observe"}, "warn only: detector allows observe"),
    ({"mode": "warn", "event": "Stop"}, "warn only: block is for PreToolUse"),
    ({"mode": "warn", "event": "-"}, "warn only: block is for PreToolUse"),              # an unknown event is never PreToolUse
    ({"mode": "warn", "compilable": "warn", "event": "Stop"}, "warn only: detector allows warn"),
    ({"mode": "warn", "strength": "weak"}, "warn only: weak detector"),
    ({"mode": "warn", "to_label": 1}, "label 1 fires"),
    ({"mode": "warn", "eligible": 0}, "silent: eligible 0"),
    ({"mode": "block"}, "block (top rung)"),
    # a copy the verdict must not trust: capped by a demotion, or not backed by the newest approvals row
    ({"cap": "observe"}, "demoted:observe (approve --mode observe restarts)"),
    ({"mode": "warn", "cap": "warn"}, "demoted:warn (approve --mode observe restarts)"),
    ({"cap": "observe", "cap_note": "demotions chain broken"}, "demoted:observe (demotions chain broken)"),        # a re-approval does not lift this one
    ({"mode": "warn", "approved_mode": "block", "cap": "warn"}, "demoted:warn (approve --mode observe restarts)"),    # a block copy capped to warn
    ({"mode": "warn", "approved_mode": "warn", "cap": "block"}, "block eligible"),                              # a cap at or above the next rung holds nothing
    ({"approved_mode": "warn", "approvals_mode": "observe", "backing": "row"}, "not backed: newest approvals row is observe"),   # an unbacked warn copy
    ({"backing": "row"}, "not backed: newest approvals row is observe"),                                     # an unbacked OBSERVE copy is refused too
    ({"backing": "row", "approvals_mode": "none"}, "not backed: no approvals row (approve --mode observe records it)"),
    ({"backing": "proposal"}, "not backed: proposal file differs from the approved copy"),
    ({"backing": "chain", "approvals_mode": "none"}, "not backed: approvals chain broken"),                # a broken chain is named, never a row
    ({"backing": "chain", "approvals_mode": "observe", "cap": "observe"}, "not backed: approvals chain broken"),
    ({"backing": "chain", "approved_at": "2026-12-01T00:00:00Z"}, "not backed: approvals chain broken"),
    ({"mode": "warn", "approved_at": "2026-12-01T00:00:00Z"}, "not backed: newest approvals row is dated in the future"),
    ({"approved_at": "2026-12-01T00:00:00Z", "cap": "observe"}, "not backed: newest approvals row is dated in the future"),
    ({"mode": "-", "approved_mode": "-", "approvals_mode": "none"}, "not backed: no approved copy"),
    ({"cap": "observe", "to_label": 2}, "label 2 fires"),                                                                # labelling is named first
    ({"cap": "observe", "eligible": 0}, "silent: eligible 0"),
    ({"mode": "block", "event": "Stop", "fp_window": 3}, "block (top rung)"),
    ({"mode": "block", "approved_mode": "block", "cap": "block"}, "block (top rung)"),             # nothing is left to promote to
    ({"mode": "block", "to_label": 2}, "label 2 fires"),
    ({"mode": "block", "revoked": True}, "retired"),
])
def test_verdict_rules_in_order(over, want):
    assert status.verdict(row(**over), NOW) == want


def test_auto_fps_add_a_suffix_naming_the_oldest_row_to_overturn_and_retired_adds_none():
    suffix = "; 2 auto FPs (label --tp abcd1234 overturns)"
    got = status.verdict(row(tp_window=5, fp_window=2, auto_fp=2, auto_fp_oldest="abcd1234"), NOW)
    assert got == "not eligible: 2 FPs (warn needs 0)" + suffix
    assert status.verdict(row(auto_fp=2, auto_fp_oldest="abcd1234"), NOW) == "warn eligible" + suffix
    assert status.verdict(row(mode="block", auto_fp=2, auto_fp_oldest="abcd1234"), NOW) == "block (top rung)" + suffix
    assert status.verdict(row(revoked=True, auto_fp=2, auto_fp_oldest="abcd1234"), NOW) == "retired"
    assert status.verdict(row(auto_fp=0, auto_fp_oldest=None), NOW) == "warn eligible"


@pytest.mark.parametrize("tp, fp, shown", [(3, 1, False), (4, 0, False), (5, 0, True), (4, 1, True), (2, 3, True)])
def test_precision_is_a_wilson_interval_from_five_labels_on(tp, fp, shown):
    p = {"detector": "rm_rf", "mode": "observe", "approved_mode": "observe", "rows": 0, "tp": tp, "fp": fp, "unlabelled": 0}
    got = status._proposal(p, {"eligible": 3, "fires": 5}, None, None, (0, 0), NOW)
    assert got["precision"] == (metrics.wilson(tp, tp + fp) if shown else "INSUFFICIENT")


def test_a_revoked_approval_row_retires_the_proposal_even_without_a_copy(w):
    fire(w, "rm_rf", "s1", 1)
    approvals.append(w.data, {"proposal_id": pid_of(w, "rm_rf"), "proposal_sha256": "-", "mode": "revoked", "ts": "2026-10-17T00:00:00Z"})
    (w.data / "proposals" / "approved" / (pid_of(w, "rm_rf") + ".json")).unlink()
    assert build(w)["proposals"][pid_of(w, "rm_rf")]["verdict"] == "retired"


def test_a_new_approval_shows_the_next_earliest_date_in_json_and_verdict(w):
    p = build(w)["proposals"][pid_of(w, "rm_rf")]
    assert p["approved_at"] == "2026-10-15T00:00:00Z" and p["next_earliest"] == "2026-10-29T00:00:00Z"
    assert (p["next_rung"], p["verdict"], p["strength"]) == ("warn", "warn earliest 2026-10-29", "weak")
    assert build(w)["proposals"][pid_of(w)]["strength"] == "strong"


# ------------------------------------------------------------------ measurement
def revoke(w, det, ts):
    approvals.append(w.data, {"proposal_id": pid_of(w, det), "proposal_sha256": "-", "mode": "revoked", "ts": ts})


def test_measurement_start_is_the_newest_observe_row_of_the_proposals_still_in_play(tmp_path):
    w = cs.real_world(tmp_path, detectors=DETS, approve=False)
    assert build(w)["measurement"] == {"start": None, "day": None, "week1_due": None, "keyed": False,
                                       "chains": {"approvals": "ok", "demotions": "ok", "labels": "ok"}}
    approve(w, "rm_rf_nonscratch", "2026-10-18T16:10:26Z")
    approve(w, "rm_rf", "2026-10-19T08:00:00Z")
    assert build(w)["measurement"]["start"] == "2026-10-19T08:00:00Z"
    revoke(w, "rm_rf", "2026-10-19T20:00:00Z")                                           # a revoked proposal no longer sets the start
    m = build(w)["measurement"]
    assert (m["start"], m["day"], m["week1_due"]) == ("2026-10-18T16:10:26Z", 3, "2026-10-25T16:10:26Z")
    assert build(w, now="2026-10-18T20:00:00Z")["measurement"]["day"] == 1
    assert build(w)["proposals"][pid_of(w)]["approved_at"] == "2026-10-18T16:10:26Z"


def test_a_warn_promotion_does_not_move_the_measurement_start_and_seal_agrees(tmp_path):
    w = cs.real_world(tmp_path, detectors=DETS, approve=False)
    approve(w, "rm_rf_nonscratch", "2026-10-18T16:10:26Z")
    approve(w, "rm_rf", "2026-10-19T08:00:00Z")
    revoke(w, "rm_rf", "2026-10-19T20:00:00Z")
    approve(w, "rm_rf", "2026-10-19T21:00:00Z")                                          # approved again after the revocation
    seal_start = lambda: seal._proposals(w.cfg)[1]                                      # noqa: E731 - seal --dry-run's measurement_start
    assert build(w)["measurement"]["start"] == seal_start() == "2026-10-19T21:00:00Z"
    approve(w, "rm_rf_nonscratch", "2026-10-25T00:00:00Z", "warn")                       # the first promotion
    assert build(w)["measurement"]["start"] == "2026-10-19T21:00:00Z"                    # the ladder restart point stays
    assert seal_start() == "2026-10-19T21:00:00Z"                                        # seal shares the rule (measure.start_of)


def test_a_demotion_disabled_proposal_still_counts_so_a_nightly_demotion_cannot_move_the_start_back(w):
    s = build(w)
    p = s["proposals"][pid_of(w, "rm_rf")]
    assert (p["mode"], s["measurement"]["start"]) == ("observe", "2026-10-15T00:00:00Z")
    demotions.append(w.data, {"proposal_id": pid_of(w, "rm_rf"), "from_mode": "observe", "to_mode": "disabled", "cause": "label_fp",
                              "ref": "0123abcd", "review_required": False, "ts": "2026-10-16T00:00:00Z"})
    s = build(w)
    assert s["proposals"][pid_of(w, "rm_rf")]["mode"] == "disabled" and s["proposals"][pid_of(w, "rm_rf")]["verdict"] == "retired"
    assert s["measurement"]["start"] == seal._proposals(w.cfg)[1] == "2026-10-15T00:00:00Z"          # equals seal --dry-run (measure.start_of)
    revoke(w, "rm_rf", "2026-10-17T00:00:00Z")                                                       # only a revocation takes it out
    assert build(w)["measurement"]["start"] == seal._proposals(w.cfg)[1] == "2026-10-01T00:00:00Z"


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


# ------------------------------------------------------------------ the ladder's clock and counts (item 5)
def test_the_clock_is_the_resolved_window_start_so_a_demotion_restarts_it(w):
    demotions.append(w.data, {"proposal_id": pid_of(w), "from_mode": "warn", "to_mode": "observe", "cause": "label_fp",
                              "ref": "0123abcd", "review_required": False, "ts": "2026-10-18T00:00:00Z"})
    p = build(w)["proposals"][pid_of(w)]
    assert p["approved_at"] == "2026-10-01T00:00:00Z" and p["window_start"] == "2026-10-18T00:00:00Z"
    assert p["next_earliest"] == "2026-11-01T00:00:00Z" and p["cap"] == "observe"
    assert p["verdict"] == "demoted:observe (approve --mode observe restarts)"                   # the demotion caps the copy until it is re-approved


def check_of(w, det, mode, now):
    """`ladder.check` for `det` going up to `mode`, from the world's own files (the lesson is taken as active: the verdict does not look)."""
    from ace_playbook.compile import catalogue, ladder, modes, proposal
    pid = pid_of(w, det)
    p, rec = proposal.load(proposal.proposal_path(w.cfg, pid)), proposal.load(shadow.approved_path(w.cfg, pid))
    copy = rec if shadow.pending_body(rec)[0] == p else None
    arows, drows = approvals.read_chain(w.data, None)[0], modes.read_demotions(w.data, None)
    resolved = modes.resolve(pid, copy, arows, None, drows[0], drows[1], now)
    return ladder.check(p, copy, labels.shadow_rows(w.cfg), labels.read_chain(w.cfg)[0], arows, catalogue.CATALOGUE[det], "active", now, resolved, mode=mode)


def demote(w, frm, to, at, cause="label_fp"):
    demotions.append(w.data, {"proposal_id": pid_of(w), "from_mode": frm, "to_mode": to, "cause": cause, "ref": "0123abcd",
                              "review_required": False, "ts": at})


LATER = "2026-10-30T00:00:00Z"


def five_tps(w, mode):
    ids = fire(w, "rm_rf_nonscratch", "s1", 5, mode=mode)
    cs.write_labels(w.cfg, [hum(r, "tp") for r in ids])
    return ids


def world_block_copy_demoted_to_warn(w):
    approve(w, "rm_rf_nonscratch", "2026-10-05T00:00:00Z", "warn")
    approve(w, "rm_rf_nonscratch", "2026-10-10T00:00:00Z", "block")
    demote(w, "block", "warn", "2026-10-12T00:00:00Z")
    five_tps(w, "warn")                                                                          # five human TPs on warn-mode fires after the demotion


def world_observe_copy_capped_at_observe(w):
    demote(w, "observe", "observe", "2026-10-12T00:00:00Z", "label_fp_pair")
    five_tps(w, "observe")


def world_warn_copy_demoted_to_observe(w):
    approve(w, "rm_rf_nonscratch", "2026-10-05T00:00:00Z", "warn")
    demote(w, "warn", "observe", "2026-10-12T00:00:00Z")
    five_tps(w, "observe")


def world_prechain_observe_copy_no_row(w):                                                       # A1
    w.cfg.path("approvals.jsonl").unlink()                                                       # a copy from before the chain: no approvals row at all
    five_tps(w, "observe")


def world_warn_row_copy_edited_down_to_observe(w):                                               # A2
    approve(w, "rm_rf_nonscratch", "2026-10-05T00:00:00Z", "warn")
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "observe")                               # the copy now says observe, the newest row says warn
    five_tps(w, "observe")


def world_newest_observe_row_names_another_hash(w):                                              # A3
    approvals.append(w.data, {"proposal_id": pid_of(w), "proposal_sha256": "e" * 64, "mode": "observe", "ts": "2026-10-03T00:00:00Z"})
    five_tps(w, "observe")


def world_warn_row_dated_after_now(w):                                                           # A4
    approve(w, "rm_rf_nonscratch", "2026-11-05T00:00:00Z", "warn")
    five_tps(w, "warn")


def world_proposal_file_rewritten_after_approval(w):                                             # B4
    five_tps(w, "observe")
    path = proposal.proposal_path(w.cfg, pid_of(w))
    raw = json.loads(path.read_text())
    raw["created"] = "2026-10-02T00:00:00Z" if raw.get("created") != "2026-10-02T00:00:00Z" else "2026-10-03T00:00:00Z"
    path.write_text(json.dumps(raw, indent=1, sort_keys=True) + "\n")                           # only `created` changed: proposal.load accepts it


def break_approvals(w, index, ts="2026-09-30T00:00:00Z"):
    """Edit one approvals row in place (its hash no longer matches), so the chain is broken at that row."""
    path = w.data / "approvals.jsonl"
    lines = path.read_text().splitlines()
    row = json.loads(lines[index])
    lines[index] = json.dumps({**row, "ts": ts})
    path.write_text("\n".join(lines) + "\n")


def world_chain_broken_at_row_1(w):                                                              # B1
    five_tps(w, "observe")
    break_approvals(w, 0)


def world_chain_broken_after_this_proposals_row(w):                                              # B1b
    five_tps(w, "observe")
    break_approvals(w, -1)                                                                       # rm_rf's row: this proposal's own row still verifies


def world_demotions_chain_broken(w):                                                             # C3
    five_tps(w, "observe")
    demotions.append(w.data, {"proposal_id": pid_of(w, "rm_rf"), "from_mode": "observe", "to_mode": "observe", "cause": "label_fp_pair",
                              "ref": "feedbeef", "review_required": True, "ts": "2026-10-16T00:00:00Z"})
    path = w.data / "demotions.jsonl"
    row = json.loads(path.read_text().splitlines()[0])
    path.write_text(json.dumps({**row, "ts": "2026-10-16T00:00:01Z"}) + "\n")                    # edited in place: the chain breaks at row 1


def world_unbacked_warn_copy(w):
    cs.approve_file(w.cfg, w.props["rm_rf_nonscratch"], "warn")                                  # the copy says warn, the chain only says observe
    five_tps(w, "observe")


@pytest.mark.parametrize("world, want, check_says", [
    (world_block_copy_demoted_to_warn, "demoted:warn (approve --mode observe restarts)", "demoted:warn"),
    (world_observe_copy_capped_at_observe, "demoted:observe (approve --mode observe restarts)", "demoted:observe"),
    (world_warn_copy_demoted_to_observe, "demoted:observe (approve --mode observe restarts)", "demoted:observe"),
    (world_unbacked_warn_copy, "not backed: newest approvals row is observe", "not in observe"),
    (world_prechain_observe_copy_no_row, "not backed: no approvals row (approve --mode observe records it)", "copy not backed by the approvals chain"),
    (world_warn_row_copy_edited_down_to_observe, "not backed: newest approvals row is warn", "copy not backed by the approvals chain"),
    (world_newest_observe_row_names_another_hash, "not backed: newest approvals row is observe", "copy not backed by the approvals chain"),
    (world_warn_row_dated_after_now, "not backed: newest approvals row is dated in the future", "dated in the future"),
    (world_proposal_file_rewritten_after_approval, "not backed: proposal file differs from the approved copy", "copy not backed by the approvals chain"),
    (world_demotions_chain_broken, "demoted:observe (demotions chain broken)", "demoted:observe"),                   # the broken chain caps at observe
])
def test_a_demoted_or_unbacked_copy_is_refused_by_the_verdict_exactly_where_ladder_check_refuses(w, world, want, check_says):
    world(w)
    p = build(w, now=LATER)["proposals"][pid_of(w)]
    assert p["verdict"] == want
    nxt = p["next_rung"]
    res = check_of(w, "rm_rf_nonscratch", nxt, LATER)
    assert not res.ok and any(check_says in r for r in res.reasons)


@pytest.mark.parametrize("world", [world_chain_broken_at_row_1, world_chain_broken_after_this_proposals_row])
def test_a_broken_approvals_chain_is_named_not_a_row(w, world):
    world(w)
    s = build(w, now=LATER)
    assert s["measurement"]["chains"]["approvals"].startswith("broken:")
    p = s["proposals"][pid_of(w)]
    assert p["backing"] == "chain" and p["verdict"] == "not backed: approvals chain broken"      # ladder.run refuses a broken chain outright


def test_a_broken_demotions_chain_caps_with_its_own_remedy_and_a_re_approval_does_not_lift_it(w):
    world_demotions_chain_broken(w)
    p = build(w, now=LATER)["proposals"][pid_of(w)]
    assert (p["cap"], p["cap_note"], p["verdict"]) == ("observe", "demotions chain broken", "demoted:observe (demotions chain broken)")
    approve(w, "rm_rf_nonscratch", "2026-10-19T00:00:00Z")
    assert build(w, now=LATER)["proposals"][pid_of(w)]["verdict"] == "demoted:observe (demotions chain broken)"
    assert build(w, now=LATER)["proposals"][pid_of(w, "rm_rf")]["cap_note"] == "demotions chain broken"


def test_known_gap_the_verdict_does_not_replay_proposal_validate_so_missing_evidence_cards_still_read_eligible(w):
    """GAP, pinned on purpose: `ladder.run` runs `proposal.validate` before `check` and refuses on a failure; the read-only verdict does not.
    A change that closes this gap must update this test (design 8.x, "Known gap")."""
    five_tps(w, "observe")
    for card in (w.data / "cards").glob("*.json"):
        card.unlink()
    src = proposal.proposal_path(w.cfg, pid_of(w))
    assert not proposal.validate(proposal.load(src), w.cfg, path=src).ok                          # ladder.run would refuse here
    assert build(w, now=LATER)["proposals"][pid_of(w)]["verdict"] == "warn eligible"
    assert check_of(w, "rm_rf_nonscratch", "warn", LATER).ok                                      # and `check` alone accepts


def test_a_block_copy_demoted_to_warn_no_longer_reads_block_eligible(w):
    world_block_copy_demoted_to_warn(w)
    p = build(w, now=LATER)["proposals"][pid_of(w)]
    assert (p["mode"], p["approved_mode"], p["cap"], p["next_rung"]) == ("warn", "block", "warn", "block")
    assert (p["tp_window"], p["verdict"]) == (5, "demoted:warn (approve --mode observe restarts)")
    assert {"not in warn", "demoted:warn"} <= {r for r in check_of(w, "rm_rf_nonscratch", "block", LATER).reasons if r in ("not in warn", "demoted:warn")}


def test_a_re_approval_lifts_the_cap_and_the_verdict_follows(w):
    world_observe_copy_capped_at_observe(w)
    approve(w, "rm_rf_nonscratch", "2026-10-13T00:00:00Z")                  # approve --mode observe restarts: strictly newer than the demotion
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert p["cap"] is None and p["window_start"] == "2026-10-13T00:00:00Z"


@pytest.mark.parametrize("world", [None, world_block_copy_demoted_to_warn, world_observe_copy_capped_at_observe, world_warn_copy_demoted_to_observe,
                                   world_unbacked_warn_copy, world_prechain_observe_copy_no_row, world_warn_row_copy_edited_down_to_observe,
                                   world_newest_observe_row_names_another_hash, world_warn_row_dated_after_now,
                                   world_proposal_file_rewritten_after_approval, world_demotions_chain_broken, world_chain_broken_at_row_1,
                                   world_chain_broken_after_this_proposals_row,
                                   lambda w: (approve(w, "rm_rf_nonscratch", "2026-10-05T00:00:00Z", "warn"), five_tps(w, "warn")),
                                   lambda w: (approve(w, "rm_rf_nonscratch", "2026-10-05T00:00:00Z", "warn"), five_tps(w, "observe")),
                                   lambda w: five_tps(w, "observe")])
def test_the_verdict_is_never_looser_than_ladder_check(w, world):
    """For each synthetic world: an `<next> eligible` verdict implies `ladder.check` accepts the promotion to that rung."""
    world and world(w)
    for det in ("rm_rf_nonscratch", "rm_rf"):
        p = build(w, now=LATER)["proposals"][pid_of(w, det)]
        if p["verdict"].endswith(" eligible") and p["verdict"].split()[0] == p["next_rung"]:
            assert check_of(w, det, p["next_rung"], LATER).ok, (det, p["verdict"], check_of(w, det, p["next_rung"], LATER).reasons)


def test_the_eligible_worlds_are_really_eligible(w):
    seen = five_tps(w, "observe")
    assert build(w, now=LATER)["proposals"][pid_of(w)]["verdict"] == "warn eligible" and check_of(w, "rm_rf_nonscratch", "warn", LATER).ok
    approve(w, "rm_rf_nonscratch", "2026-10-05T00:00:00Z", "warn")
    w.cfg.path("labels.jsonl").unlink()
    cs.write_labels(w.cfg, [hum(r, "tp") for r in seen + fire(w, "rm_rf_nonscratch", "s2", 5, mode="warn", ts="2026-10-18T10:00:{:02d}Z")])
    assert build(w, now=LATER)["proposals"][pid_of(w)]["verdict"] == "block eligible" and check_of(w, "rm_rf_nonscratch", "block", LATER).ok


def test_next_rung_and_next_earliest_are_null_at_block_and_follow_the_mode_below_it(w):
    s = build(w)["proposals"]
    assert (s[pid_of(w)]["next_rung"], s[pid_of(w)]["next_earliest"]) == ("warn", "2026-10-15T00:00:00Z")
    approve(w, "rm_rf_nonscratch", "2026-10-05T00:00:00Z", "warn")
    approve(w, "rm_rf_nonscratch", "2026-10-10T00:00:00Z", "block")
    p = build(w)["proposals"][pid_of(w)]
    assert (p["mode"], p["next_rung"], p["next_earliest"], p["verdict"]) == ("block", None, None, "block (top rung)")


def test_a_proposal_without_a_backed_copy_has_no_window(w):
    fire(w, "rm_rf", "s1", 1)
    (w.data / "proposals" / "approved" / (pid_of(w, "rm_rf") + ".json")).unlink()
    p = build(w)["proposals"][pid_of(w, "rm_rf")]
    assert p["window_start"] is None and p["next_earliest"] is None and p["verdict"] == "label 1 fires"


def test_verdict_counts_tps_and_fps_in_the_ladders_window_only(w):
    ids = fire(w, "rm_rf_nonscratch", "s1", 7)
    early = labels.row_id(cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD, ts="2026-09-30T09:00:00Z", pid=pid_of(w)))     # before the approval
    cs.write_labels(w.cfg, [hum(early, "fp"), *(hum(r, "tp") for r in ids[:5]), hum(ids[5], "fp"), hum(ids[6], "tp")])
    scan(w)
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert (p["tp"], p["fp"]) == (6, 2) and (p["tp_window"], p["fp_window"]) == (6, 1)       # all-time equals the report, window the ladder
    assert p["verdict"] == "not eligible: 1 FPs (warn needs 0)"


def test_a_label_without_the_human_reason_code_is_no_ladder_tp(w):
    ids = fire(w, "rm_rf_nonscratch", "s1", 5)
    cs.write_labels(w.cfg, [{"ref": r, "label": "tp"} for r in ids])                        # reason_code "real": the report counts it
    scan(w)
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert p["tp"] == 5 and p["tp_window"] == 0 and p["verdict"] == "needs 5 human TPs (has 0)"


def test_a_weak_detector_never_reaches_warn_eligible(w):
    ids = fire(w, "rm_rf", "s1", 5)
    cs.write_labels(w.cfg, [hum(r, "tp") for r in ids])
    scan(w)
    assert build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w, "rm_rf")]["verdict"] == "observe only: weak detector"
    assert build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]["verdict"] != "observe only: weak detector"


def promote(w, det, mode, at):
    """Move `det` up a rung: the approved copy and its approvals row, both stamped `at`."""
    approve(w, det, at, mode)


def test_a_warn_proposal_counts_only_its_warn_mode_fires_and_the_next_rung_is_block(w):
    promote(w, "rm_rf_nonscratch", "warn", "2026-10-05T00:00:00Z")
    old = fire(w, "rm_rf_nonscratch", "s1", 5, ts="2026-10-18T09:01:{:02d}Z")                      # fired while still in observe
    ids = fire(w, "rm_rf_nonscratch", "s2", 5, mode="warn")
    cs.write_labels(w.cfg, [hum(r, "tp") for r in old + ids])
    scan(w)
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert (p["mode"], p["tp"], p["tp_window"]) == ("warn", 10, 5)                               # the report counts both, the ladder only warn
    assert p["window_start"] == "2026-10-05T00:00:00Z" and p["verdict"] == "block eligible"


def test_four_warn_mode_tps_are_short_of_block_however_many_observe_mode_tps_there_are(w):
    promote(w, "rm_rf_nonscratch", "warn", "2026-10-05T00:00:00Z")
    old = fire(w, "rm_rf_nonscratch", "s1", 5, ts="2026-10-18T09:01:{:02d}Z")
    cs.write_labels(w.cfg, [hum(r, "tp") for r in old + fire(w, "rm_rf_nonscratch", "s2", 4, mode="warn")])
    scan(w)
    assert build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]["verdict"] == "needs 5 human TPs (has 4)"


def test_observe_mode_labels_alone_do_not_make_a_warn_proposal_eligible(w):
    promote(w, "rm_rf_nonscratch", "warn", "2026-10-05T00:00:00Z")
    cs.write_labels(w.cfg, [hum(r, "tp") for r in fire(w, "rm_rf_nonscratch", "s1", 5)])        # all five were fired in observe mode
    scan(w)
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert (p["tp"], p["tp_window"], p["verdict"]) == (5, 0, "needs 5 human TPs (has 0)")


def test_a_warn_proposal_with_an_fp_in_its_warn_window_is_not_eligible_for_block(w):
    promote(w, "rm_rf_nonscratch", "warn", "2026-10-05T00:00:00Z")
    ids = fire(w, "rm_rf_nonscratch", "s1", 6, mode="warn")
    cs.write_labels(w.cfg, [*(hum(r, "tp") for r in ids[:5]), hum(ids[5], "fp")])                 # no offline scan: it would demote on the FP
    assert build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]["verdict"] == "not eligible: 1 FPs (block needs 0)"


def test_a_block_mode_proposal_is_at_the_top_rung(w):
    promote(w, "rm_rf_nonscratch", "warn", "2026-10-05T00:00:00Z")
    promote(w, "rm_rf_nonscratch", "block", "2026-10-10T00:00:00Z")
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert (p["mode"], p["verdict"]) == ("block", "block (top rung)")


def test_the_event_and_compilable_ceiling_of_each_proposal_reach_the_verdict(w):
    p = build(w)["proposals"]
    assert (p[pid_of(w)]["event"], p[pid_of(w)]["compilable"]) == ("PreToolUse", "block")
    assert p[pid_of(w, "rm_rf")]["compilable"] == "observe"


# ------------------------------------------------------------------ the queue and its covering rule (items 1, 2, 8)
def auto_row(ref, label="tp_candidate", reason="auto_tp_hook_block"):
    return {"ref": ref, "label": label, "reason_code": reason}


def test_a_tp_candidate_row_is_no_label_so_the_row_stays_pending(w):
    (rid,) = fire(w, "rm_rf_nonscratch", "s1", 1)
    scan(w)
    before = build(w)
    cs.write_labels(w.cfg, [auto_row(rid)])
    after = build(w)
    assert after["queue_total"] == before["queue_total"] == 1 and [x["id"] for x in after["queue"]] == [rid]
    assert [x["id"] for x in queue.pending(w.cfg, now=NOW)] == [rid]
    assert after["proposals"][pid_of(w)]["tp"] == 0                                           # and it is no TP either


def test_a_tp_candidate_row_counts_in_to_label_and_the_verdict_but_not_in_unlabelled(w):
    a, b = fire(w, "rm_rf_nonscratch", "s1", 2)
    cs.write_labels(w.cfg, [auto_row(a), hum(b, "tp")])                                       # a is only a candidate, b is a human tp
    scan(w)
    p = build(w)["proposals"][pid_of(w)]
    assert (p["to_label"], p["unlabelled"], p["verdict"]) == (1, 0, "label 1 fires")          # unlabelled stays the report's count
    assert p["unlabelled"] == report.collect(w.cfg, 14, NOW)["private"]["proposals"][pid_of(w)]["unlabelled"]


def test_to_label_is_the_queue_rows_of_each_proposal_and_sums_to_the_queue_and_the_hidden_rows(w):
    labelled_world(w)                                                                         # 2 pending on rm_rf_nonscratch, 1 on rm_rf
    s = build(w)
    assert {pid: p["to_label"] for pid, p in s["proposals"].items()} == {pid_of(w): 2, pid_of(w, "rm_rf"): 1}
    assert sum(p["to_label"] for p in s["proposals"].values()) == s["queue_total"] + s["queue_retired"] == 3
    assert s["proposals"][pid_of(w)]["verdict"] == "label 2 fires" and s["proposals"][pid_of(w, "rm_rf")]["to_label"] == 1


def test_a_covering_label_removes_a_row_from_to_label_and_an_unfired_proposal_has_zero(w):
    ids = fire(w, "rm_rf_nonscratch", "s1", 3)
    cs.write_labels(w.cfg, [hum(ids[0], "tp"), auto_row(ids[1], "fp", "auto_fp_correction")])     # an auto fp covers a row too
    p = build(w)["proposals"]
    assert (p[pid_of(w)]["to_label"], p[pid_of(w, "rm_rf")]["to_label"]) == (1, 0)


def auto_fp_world(w):
    """rm_rf_nonscratch: five human tps and one fire (the oldest, `ids[0]`) whose only label is an automatic fp; returns the six ids."""
    ids = fire(w, "rm_rf_nonscratch", "s1", 6)
    cs.write_labels(w.cfg, [auto_row(ids[0], "fp", "auto_fp_correction"), *(hum(r, "tp") for r in ids[1:])])
    return ids


def test_an_auto_fp_only_fire_is_counted_and_named_in_the_verdict_and_changes_no_other_count(w):
    rid = auto_fp_world(w)[0]
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert (p["auto_fp"], p["auto_fp_oldest"]) == (1, rid)
    assert (p["unlabelled"], p["to_label"], p["tp_window"]) == (0, 0, 5)                      # it covers the row: nothing to label
    assert p["verdict"] == f"not eligible: 1 FPs (warn needs 0); 1 auto FPs (label --tp {rid} overturns)"   # the ladder counts the fp itself
    assert build(w, now="2026-10-30T00:00:00Z")["queue_total"] == 0


def test_a_human_tp_label_on_the_same_row_overturns_the_auto_fp(w):
    ids = auto_fp_world(w)
    rid = ids[0]
    w.cfg.path("labels.jsonl").unlink()
    cs.write_labels(w.cfg, [auto_row(rid, "fp", "auto_fp_correction"), *(hum(r, "tp") for r in ids[1:]), hum(rid, "tp")])      # the human row is newer
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert (p["auto_fp"], p["auto_fp_oldest"], p["tp_window"], p["fp_window"]) == (0, None, 6, 0)
    assert p["verdict"] == "warn eligible"


def test_a_human_fp_is_no_auto_fp_and_the_auto_fp_count_is_per_window_and_per_mode(w):
    ids = fire(w, "rm_rf_nonscratch", "s1", 3)
    old = labels.row_id(cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD, ts="2026-09-30T09:00:00Z", pid=pid_of(w)))      # before the window
    cs.write_labels(w.cfg, [hum(ids[0], "fp"), auto_row(ids[1], "fp", "auto_fp_shell_rerun"), auto_row(ids[2], "fp", "auto_fp_prompt_target"),
                            auto_row(old, "fp", "auto_fp_correction")])
    p = build(w, now="2026-10-30T00:00:00Z")["proposals"][pid_of(w)]
    assert (p["auto_fp"], p["auto_fp_oldest"], p["fp_window"]) == (2, ids[1], 3)


def test_an_auto_fp_and_a_human_row_do_cover_a_row(w):
    a, b = fire(w, "rm_rf_nonscratch", "s1", 2)
    cs.write_labels(w.cfg, [auto_row(a, "fp", "auto_fp_correction"), hum(b, "tp")])
    assert build(w)["queue_total"] == 0 and queue.pending(w.cfg, now=NOW) == []


def test_queue_total_equals_the_length_of_the_label_queue(w):
    labelled_world(w)
    fire(w, "rm_rf", "s2", 2, ts="2026-10-18T09:40:{:02d}Z")
    ids = [x["id"] for x in queue.pending(w.cfg, limit=1000, now=NOW)]
    s = build(w)
    assert s["queue_total"] == len(ids) == 5 and [x["id"] for x in s["queue"]] == ids


def test_the_fire_predicate_is_shared(w):
    assert status.fired({"would_fire": True}) and status.fired({}) and not status.fired({"would_fire": False})
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), {"ts": "2026-10-18T09:00:09Z", "session": "a" * 16, "proposal_id": pid_of(w), "payload_sha256": "cd" * 32})
    assert build(w)["queue_total"] == len(queue.pending(w.cfg, limit=1000, now=NOW)) == 1


def retire(w, det="rm_rf"):
    revoke(w, det, "2026-10-17T00:00:00Z")


def test_rows_of_retired_proposals_are_left_out_of_the_queue_and_counted(w):
    labelled_world(w)                                             # 2 pending on rm_rf_nonscratch, 1 on rm_rf
    retire(w)
    s = build(w)
    assert s["queue_total"] == 2 and s["queue_retired"] == 1 and {x["proposal_id"] for x in s["queue"]} == {pid_of(w)}
    assert s["proposals"][pid_of(w, "rm_rf")]["unlabelled"] == 1 and s["proposals"][pid_of(w, "rm_rf")]["verdict"] == "retired"
    assert s["proposals"][pid_of(w, "rm_rf")]["to_label"] == 1                           # counted although the default queue hides it
    assert s["proposals"][pid_of(w, "rm_rf")]["revoked"] is True
    assert [x["proposal_id"] for x in queue.pending(w.cfg, now=NOW)] == [pid_of(w)] * 2
    both = queue.pending(w.cfg, include_retired=True, now=NOW)
    assert len(both) == 3 and {x["proposal_id"] for x in both} == {pid_of(w), pid_of(w, "rm_rf")}


def test_retired_covers_a_disabled_mode_and_a_revoked_copy_mode():
    assert status.retired({"mode": "disabled", "approved_mode": "-", "revoked": False})
    assert status.retired({"mode": "-", "approved_mode": "revoked", "revoked": False})
    assert status.retired({"mode": "observe", "approved_mode": "observe", "revoked": True})
    assert not status.retired({"mode": "observe", "approved_mode": "warn", "revoked": False})


def test_queue_retired_is_zero_when_nothing_is_retired(w):
    labelled_world(w)
    assert build(w)["queue_retired"] == 0


# ------------------------------------------------------------------ silence is no rows at all; awaiting the scan (item 3)
def test_a_row_that_is_not_a_scanned_fire_is_never_silence(w):
    cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD, ts="2026-10-20T11:00:00Z", pid=pid_of(w))         # after the session's last scan stamp
    s = build(w)
    p = s["proposals"][pid_of(w)]
    assert (p["rows"], p["fires"], p["eligible"]) == (1, 0, 2)
    assert "silent:rm_rf_nonscratch" not in s["heartbeat"]["notes"] and "silent:rm_rf" in s["heartbeat"]["notes"]
    assert s["agreement"]["awaiting_scan"] == 1


def test_a_truncated_row_is_not_silence_either(w):
    cs.append_jsonl(w.cfg.path("shadow.jsonl"), {**cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD, ts="2026-10-18T09:00:00Z", pid=pid_of(w)), "cause": "truncated",
                                                  "payload_sha256": "ee" * 32})
    assert "silent:rm_rf_nonscratch" not in build(w)["heartbeat"]["notes"]


def test_agreement_is_always_a_dict_with_awaiting_scan_and_the_text_shows_it(w):
    s = build(w)
    assert s["agreement"] == {"live_only": None, "offline_only": None, "awaiting_scan": 0}            # "-" (no live rows) becomes nulls
    cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD, ts="2026-10-20T11:00:00Z", pid=pid_of(w))
    s = build(w)
    assert "awaiting scan 1" in status.render(s)


# ------------------------------------------------------------------ the shared line caps (item 9)
def test_the_new_modules_and_main_keep_every_line_within_160_characters():
    for name in ("status.py", "digest.py", "queue.py", "dashboard.py", "dashboard_html.py", "__main__.py"):
        for n, line in enumerate((ROOT / "ace_playbook" / "compile" / name).read_text().splitlines(), 1):
            assert len(line) <= 160, f"{name}:{n} is {len(line)} characters"
