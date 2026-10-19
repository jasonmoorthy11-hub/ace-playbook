"""Step 5c measurement (compile/metrics.py): Wilson intervals, eligible-session rates, friction, cost, agreement, `_session` rows."""

import json
import os

import pytest

from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import metrics, offline, report
from ace_playbook.compile import state as st

from . import compile_support as cs

DET, PID = "rm_rf_nonscratch", "P-0a1b2c3d"
RM = {"tool": "Bash", "input": {"command": cs.RM_PROMPT_CMD}}
RM2 = {"tool": "Bash", "input": {"command": "rm -rf /w/other"}}


def summary(session, calls=10, prompts=5, eligible=(DET,), ts=""):
    return {"detector": "_session", "tier": "offline", "session": session, "count": calls, "prompts": prompts, "eligible": list(eligible),
            "ts": ts}


def fire_row(session, mode="observe", ts="2026-10-18T10:00:00Z", phash="aa", **extra):
    return {"ts": ts, "session": session, "proposal_id": PID, "payload_sha256": phash, "mode": mode, "would_fire": True, **extra}


def one(rows, ev_rows, evasion=(), corpus="private"):
    got = metrics.compute(corpus, rows, {PID: DET}, ev_rows, list(evasion))
    return got["proposals"][PID], got["corpus"]


# ------------------------------------------------------------------ Wilson (z = 1.96, hand-computed with the formula, four decimals)
@pytest.mark.parametrize("k, n, want", [
    (0, 0, "-"),
    (0, 10, [0.0, 0.2775]),         # c = h = 0.19208 / 1.38416 = 0.13877, so lo = 0 and hi = 2 * 0.13877
    (1, 10, [0.0179, 0.4042]),      # c = 0.29208 / 1.38416 = 0.21101, h = 1.96 * sqrt(0.018604) / 1.38416 = 0.19315
    (5, 10, [0.2366, 0.7634]),      # c = 0.5, h = 1.96 * sqrt(0.034604) / 1.38416 = 0.26341
    (10, 10, [0.7225, 1.0]),        # the mirror of 0 of 10, hi capped at 1
])
def test_wilson_matches_hand_computed_values(k, n, want):
    assert metrics.wilson(k, n) == want


# ------------------------------------------------------------------ the eligible-session rule and the rates
def test_only_sessions_with_a_consumed_event_are_eligible_and_fires_elsewhere_do_not_count():
    ev = [summary("s1"), summary("s2", eligible=("rm_rf",)), summary("s3")]
    rows = [fire_row("s1"), fire_row("s2"), fire_row("s4")]          # s2 does not consume DET, s4 has no summary row (not scanned yet)
    got, _ = one(rows, ev)
    assert (got["eligible"], got["sessions_with_fire"], got["fires"], got["fire_rate"]) == (2, 1, 1, 0.5)
    assert got["wilson95"] == metrics.wilson(1, 2)


def test_no_eligible_session_gives_dashes_not_zeros():
    got, _ = one([fire_row("s1")], [])
    assert (got["eligible"], got["fire_rate"], got["wilson95"], got["fires_per_eligible"]) == (0, "-", "-", "-")
    assert got["friction"] == {"messages_per_100_calls": "-", "corrections_per_100_prompts": "-"}


def test_the_newest_summary_row_of_a_session_wins():
    ev = [summary("s1", eligible=()), summary("s1", calls=40, prompts=9)]
    assert metrics.latest_sessions(ev)["s1"]["count"] == 40
    assert one([fire_row("s1")], ev)[0]["eligible"] == 1


def test_repeat_fires_are_the_fires_after_the_first_in_a_session():
    ev = [summary("s1"), summary("s2"), summary("s3")]
    rows = [fire_row("s1"), fire_row("s1", phash="bb"), fire_row("s1", phash="cc"), fire_row("s2")]
    got, _ = one(rows, ev)
    assert (got["fires"], got["sessions_with_fire"], got["repeat_fires"], got["fires_per_eligible"]) == (4, 2, 2, 1.33)


def test_friction_denominators_are_the_tool_calls_and_prompts_of_the_eligible_sessions():
    ev = [summary("s1", calls=50, prompts=20), summary("s3", calls=50, prompts=10), summary("s2", calls=900, prompts=900, eligible=()),
          {"detector": "user_correction", "session": "s1", "count": 3}, {"detector": "user_correction", "session": "s2", "count": 7}]
    rows = [fire_row("s1", "warn"), fire_row("s1", "block", phash="bb"), fire_row("s1", "observe", phash="cc")]
    got, _ = one(rows, ev)
    assert got["friction"] == {"messages_per_100_calls": 2.0,            # 2 warn+block messages over 100 calls
                               "corrections_per_100_prompts": 10.0}     # 3 corrections over 30 prompts (s2 is not eligible)
    assert one(rows, ev, [{"proposal_id": PID}, {"proposal_id": "P-ffffffff"}])[0]["evasion"] == 1


# ------------------------------------------------------------------ cost and agreement
def test_cost_percentiles_use_nearest_rank_and_skip_rows_without_dur_ms():
    rows = [fire_row("s1", dur_ms=d) for d in (5, 1, 9, 3)] + [fire_row("s1"), fire_row("s1", dur_ms=True)]
    cost = one(rows, [summary("s1")])[1]
    assert (cost["cost_p50_ms"], cost["cost_p95_ms"], cost["cost_rows"]) == (3, 9, 4)       # sorted 1 3 5 9: ranks 2 and 4
    old = one([fire_row("s1")], [summary("s1")])[1]
    assert (old["cost_p50_ms"], old["cost_p95_ms"], old["cost_rows"]) == ("-", "-", 0)


def offline_event(session, phash, ts="2026-10-18T10:00:05Z", det=DET):
    return {"detector": det, "tier": "offline", "session": session, "payload_sha256": phash, "ts": ts}


def test_agreement_counts_live_fires_without_an_offline_event_and_offline_events_without_a_live_fire():
    ev = [summary("s1"), offline_event("s1", "aa"), offline_event("s1", "cc"),
          offline_event("s1", "dd", det="rm_rf"),                                  # a detector with no proposal: ignored
          offline_event("s1", "ee", ts="2026-10-01T00:00:00Z")]                    # before the first live row: the hook was not running
    rows = [fire_row("s1", phash="aa"), fire_row("s1", phash="bb"), fire_row("s9", phash="zz")]    # s9 was never scanned offline
    assert one(rows, ev)[1]["agreement"] == {"live_only": 1, "offline_only": 1}                  # bb and cc
    assert one([], ev)[1]["agreement"] == "-"


def test_the_synthetic_corpus_takes_its_sessions_from_its_own_rows():
    rows = [{**fire_row("s1"), "detector": DET}, {**fire_row("s1", phash="bb"), "detector": DET, "would_fire": False},
            {**fire_row("s2"), "would_fire": False, "detector": DET}, {**fire_row("s3"), "detector": "rm_rf"}]
    got, corpus = one(rows, [], corpus="synthetic")
    assert (got["eligible"], got["sessions_with_fire"], corpus["sessions"], corpus["agreement"]) == (2, 1, 3, "-")
    assert got["friction"] == {"messages_per_100_calls": 0.0, "corrections_per_100_prompts": "-"}      # 3 rows over s1, s2: no prompts


# ------------------------------------------------------------------ the `_session` rows of the offline pass
@pytest.fixture
def w(tmp_path):
    return cs.offline_world(tmp_path)


def jsonl(w, name="detector_events.jsonl"):
    p = w.cfg.path(name)
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def test_the_offline_pass_writes_one_session_row_per_session_once(w):
    cs.put_session(w, "s1", [{"human": "tidy"}, RM, RM2, {"human": "again"}])
    cs.put_session(w, "s2", [{"human": "hello"}])
    offline.run(w.cfg, now=cs.NOW)
    rows = {r["session"]: r for r in jsonl(w) if r["detector"] == "_session"}
    assert set(rows) == {st.session_hash("s1"), st.session_hash("s2")}
    a, b = rows[st.session_hash("s1")], rows[st.session_hash("s2")]
    assert (a["count"], a["prompts"], a["tier"], a["v"]) == (2, 2, "offline", 1)
    assert {DET, "rm_rf", "commit_without_push", "user_correction"} <= set(a["eligible"])
    assert (b["count"], b["prompts"], b["eligible"]) == (0, 1, ["preclaim_audit_block", "user_correction"])
    assert sum(r["detector"] == "_session" for r in jsonl(w)) == 2
    offline.run(w.cfg, now=cs.NOW)
    assert sum(r["detector"] == "_session" for r in jsonl(w)) == 2                           # an unchanged pass adds nothing


def test_a_grown_session_adds_a_second_row_and_the_report_uses_the_newer_one(w):
    path = cs.put_session(w, "s1", [{"human": "x"}, RM])
    offline.run(w.cfg, now=cs.NOW)
    more = cs.records([{"human": "x"}, RM, RM2, RM2], "s1")
    path.write_text("".join(json.dumps(r) + "\n" for r in more))
    os.utime(path, (cs.NOW - 7000,) * 2)
    offline.run(w.cfg, now=cs.NOW)
    sessions = [r for r in jsonl(w) if r["detector"] == "_session"]
    assert [r["count"] for r in sessions] == [1, 3] and metrics.latest_sessions(jsonl(w))[st.session_hash("s1")]["count"] == 3


def test_a_dry_run_writes_no_session_row(w):
    cs.put_session(w, "s1", [{"human": "x"}, RM])
    offline.run(w.cfg, dry_run=True, now=cs.NOW)
    assert jsonl(w) == []


# ------------------------------------------------------------------ the report
def test_the_report_prints_a_rates_table_and_a_cost_block_per_corpus_and_json_carries_them(w, capsys, monkeypatch):
    monkeypatch.setattr(report, "now_iso", lambda: "2026-10-19T12:00:00Z")
    cs.put_session(w, "s1", [{"human": "tidy"}, RM])
    cs.put_session(w, "s2", [{"human": "hello"}])
    cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD)
    offline.run(w.cfg, now=cs.NOW)
    cli.main(["--config", w.conf, "report"])
    text = capsys.readouterr().out
    assert text.count("Rates (") == 1 and text.count("Hook cost / agreement") == 1 and "not computed here: arms" in text
    block = text.split("Rates (", 1)[1].split("\n\n", 1)[0].splitlines()
    assert block[1].split()[:5] == ["proposal", "detector", "eligible", "fire_sessions", "fire_rate"]
    assert block[2].split()[:6] == [w.pid, "rm_rf_nonscratch", "1", "1", "1.0", "[0.2065,1.0]"]          # 1 of 1 eligible session
    cli.main(["--config", w.conf, "report", "--json"])
    got = json.loads(capsys.readouterr().out)["rates"]["private"]["proposals"][w.pid]
    assert (got["eligible"], got["sessions_with_fire"], got["fire_rate"], got["wilson95"], got["repeat_fires"]) == (
        1, 1, 1.0, metrics.wilson(1, 1), 0)


# ------------------------------------------------------------------ wave 2
def test_live_fires_in_unscanned_sessions_are_counted_apart_and_leave_every_rate_alone():
    ev = [summary("s1")]
    rows = [fire_row("s1"), fire_row("s9"), fire_row("s9", phash="bb")]
    got, corpus = one(rows, ev)
    assert corpus["awaiting_offline_scan"] == 2 and (got["eligible"], got["fires"], got["fire_rate"]) == (1, 1, 1.0)
    assert one(rows[:1], ev)[1]["awaiting_offline_scan"] == 0


def test_agreement_starts_at_each_detectors_own_first_live_row():
    ev = [summary("s1"), summary("s2"), offline_event("s1", "a1", ts="2026-10-18T10:00:00Z"),
          offline_event("s1", "b0", ts="2026-10-18T10:00:00Z", det="rm_rf"),           # before rm_rf was deployed: ignored
          offline_event("s2", "b1", ts="2026-10-19T10:00:00Z", det="rm_rf"),           # exactly at its first live row: counted
          offline_event("s2", "b2", ts="2026-10-19T11:00:00Z", det="rm_rf"),
          offline_event("s2", "c1", ts="2026-10-19T11:00:00Z", det="unparseable")]    # no proposal at all: ignored
    rows = [fire_row("s1", ts="2026-10-18T10:00:00Z", phash="a1"),
            {**fire_row("s2", ts="2026-10-19T10:00:00Z", phash="b1"), "proposal_id": "P-0a1b2c3e"}]
    got = metrics.compute("private", rows, {PID: DET, "P-0a1b2c3e": "rm_rf"}, ev, [])
    assert got["corpus"]["agreement"] == {"live_only": 0, "offline_only": 1}              # b2 only
    single = metrics.compute("private", rows[:1], {PID: DET}, ev, [])
    assert single["corpus"]["agreement"] == {"live_only": 0, "offline_only": 0}


def test_truncated_fires_are_counted_apart_and_stay_out_of_the_rates_and_the_agreement():
    ev = [summary("s1"), offline_event("s1", "bb")]
    rows = [fire_row("s1", phash="aa"), fire_row("s1", phash="bb", cause="truncated", mode="warn")]
    got, corpus = one(rows, ev)
    assert (got["fires"], got["truncated_fires"], got["sessions_with_fire"], got["repeat_fires"]) == (1, 1, 1, 0)
    assert got["friction"]["messages_per_100_calls"] == 10.0                               # the warning was emitted: 1 over 10 calls
    assert corpus["agreement"] == {"live_only": 1, "offline_only": 1}                       # aa live-only, bb offline-only


def test_stop_rows_and_stop_proposals_are_left_out_of_the_agreement():
    ev = [summary("s1"), offline_event("s1", "aa")]
    rows = [fire_row("s1", phash="zz", event="Stop")]
    assert metrics.compute("private", rows, {PID: DET}, ev, [])["corpus"]["agreement"] == "-"
    rows = [fire_row("s1", phash="aa"), fire_row("s1", phash="zz", event="Stop")]
    assert metrics.compute("private", rows, {PID: DET}, ev, [])["corpus"]["agreement"] == {"live_only": 0, "offline_only": 0}
    assert metrics.compute("private", rows, {PID: DET}, ev, [], {PID})["corpus"]["agreement"] == "-"


def test_the_session_row_id_covers_the_transcript_stamp_so_a_return_to_an_older_state_is_a_new_row():
    consumes = {DET: ("PreToolUse:Bash",)}
    ids = [metrics.session_row([], "s1", "2026-10-18T10:00:00Z", consumes, stamp)["id"] for stamp in ("10:1", "20:2", "10:1", "10:3")]
    assert ids[0] == ids[2] and len({ids[0], ids[1], ids[3]}) == 3                            # the same scan twice is one id
    assert metrics.session_row([], "s1", "t", consumes, "10:3")["id"] != metrics.session_row([], "s1", "t", consumes, "10:4")["id"]


def test_synthetic_tool_calls_are_distinct_payloads_not_event_detector_pairs():
    rows = [{**fire_row("s1", phash="p1"), "detector": DET}, {**fire_row("s1", phash="p1"), "detector": "rm_rf", "proposal_id": "P-0a1b2c3e"},
            {**fire_row("s1", phash="p2"), "detector": DET}]
    got = metrics._sessions("synthetic", rows, [])["s1"]
    assert (got["calls"], got["eligible"]) == (2, {DET, "rm_rf"})
    warn = [{**r, "mode": "warn"} for r in rows]
    assert one(warn, [], corpus="synthetic")[0]["friction"]["messages_per_100_calls"] == 100.0       # 2 warnings of DET over 2 calls


def test_a_session_scanned_before_the_summary_rows_existed_is_scanned_again_once(w):
    cs.put_session(w, "s1", [{"human": "tidy"}, RM])
    offline.run(w.cfg, now=cs.NOW)
    path = w.cfg.path("detector_events.jsonl")
    fires = [ln for ln in path.read_text().splitlines() if json.loads(ln)["detector"] != "_session"]
    path.write_text("".join(ln + "\n" for ln in fires))                                         # the state of a pre-5c pass
    again = offline.run(w.cfg, now=cs.NOW)
    assert (again["transcripts"], again["sessions"], again["detector_events"]) == (1, 1, 0)       # no duplicate fire rows
    assert sum(r["detector"] == "_session" for r in jsonl(w)) == 1 and [r["id"] for r in jsonl(w) if r["detector"] != "_session"] == [
        json.loads(ln)["id"] for ln in fires]
    assert offline.run(w.cfg, now=cs.NOW)["transcripts"] == 0


def test_a_returned_transcript_state_adds_the_newest_session_row(w):
    path = cs.put_session(w, "s1", [{"human": "x"}, RM])
    offline.run(w.cfg, now=cs.NOW)
    first = path.read_text()
    path.write_text("".join(json.dumps(r) + "\n" for r in cs.records([{"human": "x"}, RM, RM2], "s1")))
    os.utime(path, (cs.NOW - 7000,) * 2)
    offline.run(w.cfg, now=cs.NOW)
    path.write_text(first)
    os.utime(path, (cs.NOW - 6000,) * 2)
    offline.run(w.cfg, now=cs.NOW)
    sessions = [r for r in jsonl(w) if r["detector"] == "_session"]
    assert [r["count"] for r in sessions] == [1, 2, 1] and metrics.latest_sessions(jsonl(w))[st.session_hash("s1")]["count"] == 1


# ------------------------------------------------------------------ wave 3
def test_truncated_fires_are_not_awaiting_a_scan():
    rows = [fire_row("s9"), fire_row("s9", phash="bb", cause="truncated")]
    assert one(rows, [summary("s1")])[1]["awaiting_offline_scan"] == 1


def test_a_fire_after_the_sessions_newest_summary_row_waits_for_the_next_scan():
    ev = [summary("s1", ts="2026-10-18T10:00:05.123Z"), offline_event("s1", "aa")]
    same = fire_row("s1", ts="2026-10-18T10:00:05Z", phash="aa")                      # the same second as the last record: covered
    late = fire_row("s1", ts="2026-10-18T10:00:06Z", phash="bb")                      # the session went on after the scan
    got, corpus = one([same, late], ev)
    assert (got["eligible"], got["fires"], got["fire_rate"], corpus["awaiting_offline_scan"]) == (1, 1, 1.0, 1)
    assert corpus["agreement"] == {"live_only": 0, "offline_only": 0}                   # bb is neither live-only nor in a rate
    assert one([late], ev)[0]["sessions_with_fire"] == 0 and one([late], ev)[1]["awaiting_offline_scan"] == 1


def test_a_summary_row_without_a_stamp_covers_every_fire():
    assert one([fire_row("s1", ts="2030-01-01T00:00:00Z")], [summary("s1")])[1]["awaiting_offline_scan"] == 0


def test_repeated_identical_synthetic_payloads_are_separate_calls():
    rows = [{**fire_row("s1", phash="p1"), "detector": DET} for _ in range(3)] + [
        {**fire_row("s1", phash="p1"), "detector": "rm_rf", "proposal_id": "P-0a1b2c3e"}]
    assert metrics._sessions("synthetic", rows, [])["s1"]["calls"] == 3                  # three replays, one of them also fed to rm_rf


def test_a_proposal_id_without_a_proposal_file_has_no_rates_and_is_not_awaiting(w, capsys, monkeypatch):
    monkeypatch.setattr(report, "now_iso", lambda: "2026-10-19T12:00:00Z")
    cs.put_session(w, "s1", [{"human": "tidy"}, RM])
    offline.run(w.cfg, now=cs.NOW)
    cs.put_shadow(w, "s1", cs.RM_PROMPT_CMD, pid="P-deadbeef")
    cli.main(["--config", w.conf, "report", "--json"])
    rates = json.loads(capsys.readouterr().out)["rates"]["private"]
    assert "P-deadbeef" not in rates["proposals"] and rates["corpus"]["awaiting_offline_scan"] == 0
    assert rates["corpus"]["agreement"] == "-"
