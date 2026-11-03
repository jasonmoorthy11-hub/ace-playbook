"""`compile/holdout_report.py`: the pre-registered analysis of the per-session lesson holdout. Everything is invented (session ids, seeds, stamps)."""

import json
import random
from pathlib import Path

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile import holdout_report as hr
from ace_playbook.compile import metrics
from ace_playbook.compile import state as st

SEED, OTHER = "ab" * 32, "cd" * 32
A, B, C = (lid for lid, _ in hr.LESSONS)
DET = dict(hr.LESSONS)
NOW = "2026-12-01T00:00:00Z"
START, END = "2026-11-01T00:00:00Z", "2026-11-30T00:00:00Z"
INSIDE = "2026-11-10T00:00:00Z"
HEADING = "descriptive, not the pre-registered test"


def inj(sid, shown=(), hidden=(), rate=0.5, source="startup", refused="", ts=INSIDE, dropped=(), fp="00000000", entry="cli"):
    row = {"ts": ts, "session_id": sid, "source": source, "shown_ids": list(shown), "holdout_ids": list(hidden), "holdout_rate": rate,
           "seed_fp": fp, "refused": refused, "dropped_cap": list(dropped)}
    return row if entry == "missing" else {**row, "entrypoint": entry}                      # "missing": a legacy row without the field


def srow(sid, start=INSIDE, prompts=2):
    return {"detector": "_session", "tier": "offline", "session": st.session_hash(sid), "ts": "2026-11-20T00:00:00Z", "count": 5, "prompts": prompts,
            "start": start, "eligible": []}


def frow(sid, lid, count, rid, tier="offline"):
    return {"detector": DET[lid], "tier": tier, "session": st.session_hash(sid), "count": count, "id": rid, "key": "", "ts": INSIDE}


class World:
    def __init__(self, tmp_path, seed=SEED):
        self.dir, self.inj, self.ev = tmp_path, tmp_path / "injections.jsonl", tmp_path / "detector_events.jsonl"
        self.conf = tmp_path / "config.json"
        self.conf.write_text(json.dumps({"data_dir": str(tmp_path), "holdout_start": START, "holdout_end": END,
                                         **({"holdout_permutation_seed": seed} if seed else {})}))
        self.irows, self.erows = [], []

    def write(self):
        self.inj.write_text("".join(json.dumps(r) + "\n" for r in self.irows))
        self.ev.write_text("".join(json.dumps(r) + "\n" for r in self.erows))

    def run(self, capsys, *extra, now=NOW, start=START, end=END):
        """The inferential run reads the registered window from the config; only the interim mode is given a --start/--end."""
        self.write()
        window = ["--start", start, "--end", end] if "--interim-descriptive" in extra else []
        rc = hr.main(["--config", str(self.conf), *window, "--json", *extra], now=now)
        cap = capsys.readouterr()
        return rc, cap.out, cap.err

    def result(self, capsys, *extra, **kw):
        rc, out, err = self.run(capsys, *extra, **kw)
        assert rc == 0, err
        return json.loads(out)

    def session(self, sid, arms, start=INSIDE, prompts=2, rate=0.5, fires=None):
        """arms: {lesson id: 'shown' | 'hidden' | None}; fires: {lesson id: count}."""
        shown = [k for k, v in arms.items() if v == "shown"]
        hidden = [k for k, v in arms.items() if v == "hidden"]
        self.irows.append(inj(sid, shown, hidden, rate))
        self.erows.append(srow(sid, start, prompts))
        self.erows += [frow(sid, k, n, f"{sid}-{k}") for k, n in (fires or {}).items() if n]


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def keys(obj):
    """Every dict key at any depth."""
    if isinstance(obj, dict):
        return set(obj) | set().union(*map(keys, obj.values()), set())
    return set().union(*map(keys, obj), set()) if isinstance(obj, list) else set()


def lesson(res, lid):
    return next(x for x in res["lessons"] if x["lesson"] == lid)


# ------------------------------------------------------------------------------------------------ config key
def test_the_config_key_is_optional_hex_and_never_printed():
    assert cfgmod.from_dict({"holdout_permutation_seed": SEED}).holdout_permutation_seed == SEED and cfgmod.from_dict({}).holdout_permutation_seed is None
    for bad in ("short", "G" * 64, SEED.upper(), 7, ""):
        with pytest.raises(ValueError) as err:
            cfgmod.from_dict({"holdout_permutation_seed": bad})
        assert "holdout_permutation_seed" in str(err.value) and (not isinstance(bad, str) or not bad or bad not in str(err.value))
    assert SEED not in repr(cfgmod.from_dict({"holdout_permutation_seed": SEED}))


# ------------------------------------------------------------------------------------------------ the join and the exclusions
def test_the_raw_session_id_is_joined_to_the_detector_rows_by_its_hash(w, capsys):
    w.session("raw-id-1", {A: "shown", B: "hidden", C: "shown"}, fires={A: 2, B: 1})
    w.session("raw-id-2", {A: "hidden", B: "shown", C: "hidden"}, fires={A: 0, C: 3})
    res = w.result(capsys)
    la, lb, lc = (lesson(res, k) for k in (A, B, C))
    assert (la["n"], lb["n"], lc["n"]) == ({"shown": 1, "hidden": 1},) * 3
    assert la["secondary"]["mean"] == {"shown": 2.0, "hidden": 0.0} and lb["secondary"]["mean"] == {"shown": 0.0, "hidden": 1.0}
    assert lc["secondary"]["mean"] == {"shown": 0.0, "hidden": 3.0} and la["primary"]["any_rate"] == {"shown": 1.0, "hidden": 0.0}
    assert res["counts"]["analysed_sessions"] == 2 and w.dir.name not in json.dumps(res)


def test_fire_rows_are_deduplicated_by_id_and_summed_per_session(w, capsys):
    w.session("s1", {A: "shown", B: "shown", C: "shown"}, fires={A: 2})
    w.session("s2", {A: "hidden", B: "hidden", C: "hidden"})
    w.erows += [frow("s1", A, 2, "s1-" + A), frow("s1", A, 1, "extra")]                    # the first repeats an id; the second is a new row
    assert lesson(w.result(capsys), A)["secondary"]["mean"] == {"shown": 3.0, "hidden": 0.0}


def test_every_source_counts_and_a_disagreement_between_rows_excludes_the_session(w, capsys):
    arms = {A: "shown", B: "hidden", C: "shown"}
    w.session("same", arms)
    for src in ("clear", "resume", "compact"):
        w.irows.append(inj("same", [A, C], [B], source=src))
    w.session("differs", arms)
    w.irows.append(inj("differs", [A, B, C], [], source="compact"))                         # B flipped between rows
    res = w.result(capsys)
    assert res["counts"]["assignment_conflict"] == 1 and res["counts"]["analysed_sessions"] == 1


def test_each_exclusion_has_its_own_counter(w, capsys):
    full = {A: "shown", B: "hidden", C: "shown"}
    w.session("ok", full)
    w.session("few-prompts", full, prompts=0)
    w.session("early", full, start="2026-10-31T23:59:59Z")
    w.session("at-end", full, start=END)                                                   # [start, end): the end is out
    w.session("no-start", full, start="")
    w.session("off", full, rate=0.0)
    w.session("odd-rate", full, rate=0.3)
    w.session("refused", full)
    w.irows[-1]["refused"] = "delta_log_failed_verification"
    w.session("partial", {A: "shown", B: None, C: "hidden"})
    w.irows.append(inj("lonely-injection", [A], [B, C]))                                  # no `_session` row, inside the window
    w.erows.append(srow("lonely-session"))                                                # no injection row
    w.erows.append(srow("lonely-old-session", start="2026-01-01T00:00:00Z"))              # outside the window: not counted as unmatched
    res = w.result(capsys)
    c = res["counts"]
    assert {k: c[k] for k in ("below_min_prompts", "outside_window", "no_start", "holdout_off", "rate_not_half", "refused", "unmatched_injection",
                              "unmatched_session", "analysed_sessions", "lesson_not_assigned")} == {
        "below_min_prompts": 1, "outside_window": 2, "no_start": 1, "holdout_off": 1, "rate_not_half": 1, "refused": 1, "unmatched_injection": 1,
        "unmatched_session": 1, "analysed_sessions": 2, "lesson_not_assigned": 1}
    assert lesson(res, B)["n"] == {"shown": 0, "hidden": 1} and lesson(res, B)["not_assigned"] == 1
    assert lesson(res, A)["n"] == {"shown": 2, "hidden": 0}
    assert res["config"]["min_prompts"] == 1 and hr.main(["--config", str(w.conf), "--interim-descriptive", "--min-prompts", "0", "--json"], now=NOW) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["below_min_prompts"] == 0


def test_only_interactive_sessions_are_analysed_and_the_rest_is_counted_not_interactive(w, capsys):
    w.session("cli-1", FULL, fires={A: 2})
    w.session("sdk", FULL)
    w.irows[-1]["entrypoint"] = "sdk-py"
    w.session("mix", FULL)
    w.irows.append(inj("mix", [A, C], [B], entry="sdk-py"))                                 # one cli row, one sdk-py row: out
    w.session("legacy", FULL)
    del w.irows[-1]["entrypoint"]
    w.session("null-entry", FULL)
    w.irows[-1]["entrypoint"] = None
    w.session("bad-entry", FULL)
    w.irows[-1]["entrypoint"] = "invalid"
    w.session("late-sdk", FULL, start="2026-10-01T00:00:00Z")                               # decided after the earlier reasons
    w.irows[-1]["entrypoint"] = "sdk-py"
    for extra in ((), ("--interim-descriptive",)):
        c = w.result(capsys, *extra)["counts"]
        assert c["not_interactive"] == 5 and c["analysed_sessions"] == 1 and c["outside_window"] == 1 and c["injection_sessions"] == 7
        assert hr.COUNTERS[:3] == ("injection_sessions", "null_session_id", "not_interactive")
    assert lesson(w.result(capsys), A)["n"] == {"shown": 1, "hidden": 0}


def test_not_interactive_is_decided_before_the_coin_and_the_other_session_reasons(w, capsys):
    w.session("off-sdk", FULL, rate=0.0)
    w.irows[-1]["entrypoint"] = "sdk-py"
    w.session("odd-sdk", FULL, rate=0.3)
    w.irows[-1]["entrypoint"] = "sdk-py"
    w.session("off-cli", FULL, rate=0.0)
    c = w.result(capsys)["counts"]
    assert (c["not_interactive"], c["holdout_off"], c["rate_not_half"], c["analysed_sessions"]) == (2, 1, 0, 0)


# ------------------------------------------------------------------------------------------------ refusals and the interim mode
def test_before_the_end_the_report_refuses_in_one_line(w, capsys):
    w.session("s1", {A: "shown", B: "hidden", C: "shown"})
    rc, out, err = w.run(capsys, now="2026-11-15T00:00:00Z")
    assert rc == 2 and out == "" and len(err.strip().splitlines()) == 1 and "--interim-descriptive" in err


def test_an_unset_seed_refuses_with_exit_2_even_in_the_interim_mode(tmp_path, capsys):
    w = World(tmp_path, seed=None)
    w.session("s1", {A: "shown", B: "hidden", C: "shown"})
    for extra in ((), ("--interim-descriptive",)):
        rc, out, err = w.run(capsys, *extra)
        assert rc == 2 and out == "" and "holdout_permutation_seed" in err


@pytest.mark.parametrize("args", [["--start", "yesterday", "--end", END], ["--start", END, "--end", START],
                                  ["--start", START, "--end", "2026-13-40T00:00:00Z"]])
def test_bad_dates_are_a_usage_error(w, capsys, args):
    with pytest.raises(SystemExit) as e:
        hr.main(["--config", str(w.conf), "--interim-descriptive", *args], now=NOW)
    assert e.value.code == 2
    capsys.readouterr()


def test_the_interim_mode_prints_per_arm_counts_and_means_but_no_test(w, capsys):
    for i in range(6):
        w.session(f"s{i}", {A: "shown", B: "hidden", C: "shown"} if i % 2 else {A: "hidden", B: "shown", C: "hidden"}, fires={A: i})
    rc, out, err = w.run(capsys, "--interim-descriptive", now="2026-11-15T00:00:00Z")
    res = json.loads(out)
    assert rc == 0 and res["heading"] == HEADING and err == ""
    assert lesson(res, A)["n"] == {"shown": 3, "hidden": 3} and set(lesson(res, A)) == {"lesson", "detector", "n", "not_assigned", "any_rate", "mean"}
    assert not {"p", "ci95", "shift_ci95_null_redraws", "wilson95", "holm_p", "reject", "D", "rate_ratio", "primary", "secondary"} & keys(res)
    assert hr.main(["--config", str(w.conf), "--interim-descriptive"], now="2026-11-15T00:00:00Z") == 0
    text = capsys.readouterr().out
    assert HEADING in text and SEED not in text
    assert not [word for word in ("p =", "p-value", "ci95", "wilson", "holm", "reject") if word in text.lower()]


# ------------------------------------------------------------------------------------------------ the test itself
def planted(w, n=120):
    """A strong effect on lesson A only (hidden fires 60%, shown 5%); B and C fire 30% in both arms. Arms are drawn at random per lesson."""
    rng = random.Random(5)
    for i in range(n):
        arms = {k: rng.choice(("shown", "hidden")) for k in (A, B, C)}
        fires = {A: int(rng.random() < (0.05 if arms[A] == "shown" else 0.6)), B: int(rng.random() < 0.3), C: int(rng.random() < 0.3)}
        w.session(f"p{i}", arms, fires=fires)


def test_a_planted_strong_effect_is_found_and_holm_rejects_only_that_lesson(w, capsys):
    planted(w)
    res = w.result(capsys)
    la, lb, lc = (lesson(res, k) for k in (A, B, C))
    assert la["primary"]["p"] < 0.01 and la["primary"]["reject"] is True and la["primary"]["D"] < 0
    assert lb["primary"]["reject"] is False and lc["primary"]["reject"] is False and lb["primary"]["p"] > 0.05 and lc["primary"]["p"] > 0.05
    assert la["primary"]["holm_p"] == pytest.approx(min(1.0, 3 * la["primary"]["p"]))
    shown_rate = la["primary"]["any_rate"]["shown"]
    assert la["primary"]["wilson95"]["shown"] == metrics.wilson(round(shown_rate * la["n"]["shown"]), la["n"]["shown"])
    assert la["primary"]["rate_ratio"] == pytest.approx(shown_rate / la["primary"]["any_rate"]["hidden"], abs=1e-3)
    assert la["secondary"]["reject"] is True and lb["secondary"]["reject"] is False and res["config"]["permutation_draws"] == 10000


def test_the_same_seed_gives_the_same_report_and_another_seed_another_p(tmp_path, capsys):
    outs = []
    for seed in (SEED, SEED, OTHER):
        w = World(tmp_path, seed)
        planted(w, 40)
        outs.append(w.run(capsys, now=NOW)[1])
    assert outs[0] == outs[1] and outs[0] != outs[2] and SEED not in outs[0] + outs[2] and OTHER not in outs[0] + outs[2]


def test_the_text_report_prints_counts_ids_and_the_tests(w, capsys):
    planted(w, 30)
    w.write()
    assert hr.main(["--config", str(w.conf)], now=NOW) == 0
    text = capsys.readouterr().out
    assert A in text and "primary" in text.lower() and "holm" in text.lower() and HEADING not in text and SEED not in text


# ------------------------------------------------------------------------------------------------ wave 2 (Opus round 1)
FULL = {A: "shown", B: "hidden", C: "shown"}


def test_the_registered_window_keys_are_optional_whole_second_utc_stamps_in_order():
    ok = cfgmod.from_dict({"holdout_start": START, "holdout_end": END})
    assert (ok.holdout_start, ok.holdout_end) == (START, END) and cfgmod.from_dict({}).holdout_start is None
    assert cfgmod.from_dict({"holdout_start": START}).holdout_end is None
    for key, bad in (("holdout_start", "yesterday"), ("holdout_end", "2026-11-30"), ("holdout_start", 7), ("holdout_end", "2026-13-01T00:00:00Z"),
                     ("holdout_start", "2026-11-01T00:00:00+00:00"), ("holdout_start", "")):
        with pytest.raises(ValueError) as err:
            cfgmod.from_dict({key: bad})
        assert key in str(err.value) and (not isinstance(bad, str) or not bad or bad not in str(err.value))
    for pair in ((END, START), (START, START)):
        with pytest.raises(ValueError) as err:
            cfgmod.from_dict({"holdout_start": pair[0], "holdout_end": pair[1]})
        assert "holdout_start" in str(err.value) and "holdout_end" in str(err.value)


@pytest.mark.parametrize("stamp,ok", [("2024-02-29T00:00:00Z", True), ("2000-02-29T00:00:00Z", True), ("2100-02-29T00:00:00Z", False),
                                      ("2026-02-29T00:00:00Z", False), ("2026-02-28T23:59:59Z", True), ("2026-04-31T00:00:00Z", False),
                                      ("2026-11-31T00:00:00Z", False), ("2026-12-31T23:59:59Z", True), ("2026-01-32T00:00:00Z", False),
                                      ("2026-00-10T00:00:00Z", False), ("2026-06-30T24:00:00Z", False), ("2026-06-30T00:00:00", False)])
def test_the_calendar_check_knows_month_lengths_and_the_gregorian_leap_rule(stamp, ok):
    assert cfgmod.utc_stamp_ok(stamp) is ok


def test_loading_a_config_with_the_holdout_keys_imports_neither_datetime_nor_strptime(tmp_path):
    """Run in a fresh interpreter: the hook path loads the config, and the holdout keys must not pull the datetime machinery in."""
    import subprocess
    import sys
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path), "holdout_start": START, "holdout_end": END}))
    code = ("import sys; from ace_playbook import config; before = set(sys.modules); c = config.load(sys.argv[1]); "
            "new = set(sys.modules) - before; print(c.holdout_start, c.holdout_end, sorted(new & {'datetime', '_strptime'}))")
    out = subprocess.run([sys.executable, "-I", "-c", code, str(conf)], capture_output=True, text=True, timeout=60,
                         env={"PYTHONPATH": str(Path(cfgmod.__file__).parent.parent)})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"{START} {END} []"


def test_real_start_stamps_carry_milliseconds_and_the_window_takes_both_forms(w, capsys):
    w.session("ms", FULL, start="2026-11-10T08:30:00.123Z")
    w.session("whole", FULL, start="2026-11-11T08:30:00Z")
    w.session("last-ms", FULL, start="2026-11-29T23:59:59.999Z")
    w.session("end-ms", FULL, start="2026-11-30T00:00:00.000Z")                           # the end itself: out
    w.session("long-frac", FULL, start="2026-11-12T00:00:00.123456Z")
    w.session("junk", FULL, start="2026-11-12 00:00:00")                                    # not a UTC stamp: no start
    c = w.result(capsys)["counts"]
    assert (c["analysed_sessions"], c["no_start"], c["outside_window"]) == (4, 1, 1)


def test_an_interim_window_may_carry_milliseconds_and_injection_stamps_too(w, capsys):
    w.session("a", FULL, start="2026-11-10T00:00:00.500Z")
    w.irows.append(inj("lonely", [A], [B, C], ts="2026-11-10T00:00:00.250Z"))
    res = json.loads(w.run(capsys, "--interim-descriptive", start="2026-11-10T00:00:00.100Z", end="2026-11-10T00:00:01.000Z", now=START)[1])
    assert res["counts"]["analysed_sessions"] == 1 and res["counts"]["unmatched_injection"] == 1


def test_an_empty_arm_has_no_test_and_holm_treats_it_as_one(w, capsys):
    for i in range(6):
        w.session(f"e{i}", {A: "shown", B: "shown" if i % 2 else "hidden", C: "hidden" if i % 2 else "shown"}, fires={B: i % 3})
    la = lesson(w.result(capsys), A)
    for part in ("primary", "secondary"):
        assert la[part]["D"] is None and la[part]["p"] is None and la[part]["holm_p"] == 1.0 and la[part]["reject"] is False
        assert la[part]["rate_ratio"] == metrics.DASH
    assert la["n"] == {"shown": 6, "hidden": 0}


def test_a_zero_hidden_mean_has_no_rate_ratio_but_a_difference(w, capsys):
    for i in range(6):
        w.session(f"z{i}", {A: "shown" if i % 2 else "hidden", B: "hidden", C: "shown"}, fires={A: 2 if i % 2 else 0})
    la = lesson(w.result(capsys), A)
    assert la["secondary"]["mean"]["hidden"] == 0.0 and la["secondary"]["rate_ratio"] == metrics.DASH and la["secondary"]["D"] == 2.0
    assert la["primary"]["rate_ratio"] == metrics.DASH and la["primary"]["D"] == 1.0


def test_a_lesson_in_both_lists_of_one_row_is_an_assignment_conflict(w, capsys):
    w.irows.append(inj("both", [A, B, C], [A]))
    w.erows.append(srow("both"))
    w.session("fine", FULL)
    c = w.result(capsys)["counts"]
    assert c["assignment_conflict"] == 1 and c["analysed_sessions"] == 1


def test_a_session_with_a_rate_zero_row_and_a_live_row_is_excluded_whatever_its_arms(w, capsys):
    w.session("same-arms", FULL)
    w.irows.append(inj("same-arms", [A, B, C], [], rate=0.0, source="resume"))             # holdout off for one start: B is shown there
    w.session("other-arms", FULL)
    w.irows.append(inj("other-arms", [A, C], [B], rate=0.0, source="resume"))              # the arms agree, the rate does not
    w.session("only-live", FULL)
    c = w.result(capsys)["counts"]
    assert (c["mixed_rate"], c["analysed_sessions"], c["assignment_conflict"], c["holdout_off"]) == (2, 1, 0, 0)


def test_a_lesson_dropped_by_the_cap_counts_as_shown_and_is_counted(w, capsys):
    w.session("d1", {A: "shown", B: "hidden", C: None}, fires={C: 1})
    w.irows[-1]["dropped_cap"] = [C]                                                      # the coin said shown; the cap dropped the line
    w.session("d2", {A: "hidden", B: "shown", C: "hidden"})
    w.session("d3", {A: "shown", B: "hidden", C: None})                                   # in neither list and not dropped: not assigned
    res = w.result(capsys)
    assert res["counts"]["dropped_cap"] == 1 and res["counts"]["lesson_not_assigned"] == 1
    assert lesson(res, C)["n"] == {"shown": 1, "hidden": 1} and lesson(res, C)["not_assigned"] == 1


def test_a_dropped_lesson_never_overrides_a_hidden_one(w, capsys):
    w.session("h", {A: "shown", B: "hidden", C: "hidden"})
    w.irows[-1]["dropped_cap"] = [C]
    res = w.result(capsys)
    assert res["counts"]["dropped_cap"] == 0 and lesson(res, C)["n"] == {"shown": 0, "hidden": 1}


def test_only_offline_tier_fire_rows_are_outcomes(w, capsys):
    w.session("t1", {A: "shown", B: "shown", C: "shown"}, fires={A: 2})
    w.session("t2", {A: "hidden", B: "hidden", C: "hidden"})
    w.erows += [frow("t1", A, 5, "live-1", tier="live"), frow("t2", A, 7, "live-2", tier="live"), frow("t2", A, 1, "no-tier", tier=None)]
    assert lesson(w.result(capsys), A)["secondary"]["mean"] == {"shown": 2.0, "hidden": 0.0}


def test_live_rows_that_carry_different_seed_fingerprints_exclude_the_session(w, capsys):
    w.session("fp", FULL)
    w.irows.append(inj("fp", [A, C], [B], source="resume", fp="11111111"))
    w.session("fp-off-row", FULL)
    w.irows.append(inj("fp-off-row", [A, B, C], [], rate=0.0, source="resume", fp="22222222"))   # a rate-0 row: a mixed rate, not a seed finding
    w.session("fp-same", FULL)
    w.irows.append(inj("fp-same", [A, C], [B], source="resume"))
    c = w.result(capsys)["counts"]
    assert (c["seed_fp_mismatch"], c["mixed_rate"], c["analysed_sessions"]) == (1, 1, 1)


def test_rows_without_a_usable_session_id_are_counted(w, capsys):
    w.session("ok", FULL)
    w.irows += [inj(None, [A], [B, C]), inj("", [A], [B, C]), inj(7, [A], [B, C]), {"ts": INSIDE, "shown_ids": [A]}]
    c = w.result(capsys)["counts"]
    assert c["null_session_id"] == 4 and c["analysed_sessions"] == 1 and c["injection_sessions"] == 1


def test_the_primary_has_no_interval_of_its_own_and_the_secondary_names_what_it_is(w, capsys):
    planted(w, 30)
    la = lesson(w.result(capsys), A)
    assert "ci95" not in la["primary"] and "shift_ci95_null_redraws" not in la["primary"]
    assert "ci95" not in la["secondary"] and len(la["secondary"]["shift_ci95_null_redraws"]) == 2


@pytest.mark.parametrize("extra,why", [(("--start", START, "--end", END), "inferential"), (("--start", START), "half"),
                                       (("--interim-descriptive", "--start", START), "half-interim")])
def test_only_the_registered_window_can_be_tested_and_a_cli_window_only_describes(w, capsys, extra, why):
    w.session("s1", FULL)
    w.write()
    rc = hr.main(["--config", str(w.conf), *extra], now=NOW)
    cap = capsys.readouterr()
    assert rc == 2 and cap.out == "" and len(cap.err.strip().splitlines()) == 1, why


def test_an_unset_window_refuses_the_inferential_mode_and_the_interim_mode_without_a_cli_window(tmp_path, capsys):
    w = World(tmp_path)
    w.conf.write_text(json.dumps({"data_dir": str(tmp_path), "holdout_permutation_seed": SEED}))
    w.session("s1", FULL)
    w.write()
    for argv in ([], ["--interim-descriptive"]):
        rc = hr.main(["--config", str(w.conf), *argv], now=NOW)
        cap = capsys.readouterr()
        assert rc == 2 and cap.out == "" and len(cap.err.strip().splitlines()) == 1 and "holdout_start" in cap.err
    assert hr.main(["--config", str(w.conf), "--interim-descriptive", "--start", START, "--end", END], now=NOW) == 0
    capsys.readouterr()


def test_the_interim_mode_without_a_cli_window_describes_the_registered_one(w, capsys):
    w.session("s1", FULL)
    res = json.loads(w.run(capsys, "--interim-descriptive", now="2026-11-15T00:00:00Z", start=START, end=END)[1])
    assert res["config"]["start"] == START and res["heading"] == HEADING
    w.write()
    assert hr.main(["--config", str(w.conf), "--interim-descriptive", "--json"], now="2026-11-15T00:00:00Z") == 0
    assert json.loads(capsys.readouterr().out)["config"] == res["config"]


def test_the_inferential_mode_uses_the_registered_window_and_waits_for_its_end(w, capsys):
    w.session("s1", FULL, start="2026-11-05T00:00:00Z")
    w.conf.write_text(json.dumps({"data_dir": str(w.dir), "holdout_permutation_seed": SEED, "holdout_start": "2026-11-08T00:00:00Z",
                                  "holdout_end": "2026-11-09T00:00:00Z"}))
    w.write()
    assert hr.main(["--config", str(w.conf), "--json"], now="2026-11-08T12:00:00Z") == 2
    assert "has not passed" in capsys.readouterr().err
    assert hr.main(["--config", str(w.conf), "--json"], now="2026-11-09T00:00:00Z") == 0
    res = json.loads(capsys.readouterr().out)
    assert res["config"]["start"] == "2026-11-08T00:00:00Z" and res["counts"]["outside_window"] == 1


# ------------------------------------------------------------------------------------------------ Opus round 2 minors
IMPOSSIBLE = ("2026-11-31T00:00:00Z", "2026-02-30T00:00:00Z")


@pytest.mark.parametrize("key", ("holdout_start", "holdout_end"))
@pytest.mark.parametrize("bad", IMPOSSIBLE)
def test_an_impossible_calendar_date_is_a_config_error(key, bad):
    with pytest.raises(ValueError) as err:
        cfgmod.from_dict({key: bad})
    assert key in str(err.value) and bad not in str(err.value)


@pytest.mark.parametrize("which,bad", (("holdout_end", IMPOSSIBLE[0]), ("holdout_start", IMPOSSIBLE[1])))
def test_the_report_refuses_a_window_stamp_that_is_not_a_real_date(w, capsys, monkeypatch, which, bad):
    """Defence in depth: a config object that got past validation (here set after the fact) still ends in one line, not a traceback."""
    w.session("s1", FULL)
    w.write()
    cfg = cfgmod.from_dict({"data_dir": str(w.dir), "holdout_permutation_seed": SEED, "holdout_start": START, "holdout_end": END})
    setattr(cfg, which, bad)
    monkeypatch.setattr(hr.cfgmod, "load", lambda path=None: cfg)
    for argv in ([], ["--interim-descriptive"]):
        rc = hr.main([*argv, "--json"], now=NOW)
        cap = capsys.readouterr()
        assert rc == 2 and cap.out == "" and len(cap.err.strip().splitlines()) == 1 and which in cap.err and "Traceback" not in cap.err


def test_the_confirmatory_read_refuses_any_min_prompts_but_the_registered_one(w, capsys):
    w.session("s1", FULL)
    rc, out, err = w.run(capsys, "--min-prompts", "3")
    assert rc == 2 and out == "" and len(err.strip().splitlines()) == 1 and "--min-prompts" in err and "1" in err
    assert w.run(capsys, "--min-prompts", "1")[0] == 0
    res = w.result(capsys, "--interim-descriptive", "--min-prompts", "3")
    assert res["config"]["min_prompts"] == 3


def _log(dirpath, *recs, raw=None):
    """A test-made delta log in the config's own data dir (the report reads only lesson_id, ts and fields): recs = (lesson id, ts, flag)."""
    lines = raw if raw is not None else [json.dumps({"op": "PATCH", "lesson_id": lid, "ts": ts, "fields": {"holdout_exempt": flag}}) for lid, ts, flag in recs]
    (dirpath / "deltas.jsonl").write_text("".join(x + "\n" for x in lines))


def _refused(capsys):
    cap = capsys.readouterr()
    assert cap.out == "" and len(cap.err.strip().splitlines()) == 1
    return cap.err


def test_the_confirmatory_read_refuses_when_one_of_the_three_lessons_is_exempt(w, capsys):
    w.session("s1", FULL)
    w.write()
    argv = ["--config", str(w.conf), "--json"]
    assert hr.main(argv, now=NOW) == 0                                                    # no log at all: nothing exempt
    capsys.readouterr()
    _log(w.dir, (B, "2026-10-01T00:00:00Z", True))
    assert hr.main(argv, now=NOW) == 2
    err = _refused(capsys)
    assert B in err and A not in err
    _log(w.dir, ("L-other000", "2026-10-01T00:00:00Z", True), (A, "2026-10-01T00:00:00Z", False))   # exempt, but not one of the three
    assert hr.main(argv, now=NOW) == 0
    capsys.readouterr()


def test_an_exempt_then_unexempt_inside_the_window_still_refuses_and_a_record_from_the_end_on_does_not(w, capsys):
    w.session("s1", FULL)
    w.write()
    argv = ["--config", str(w.conf), "--json"]
    _log(w.dir, (C, "2026-11-10T00:00:00Z", True), (C, "2026-11-12T00:00:00Z", False))
    assert hr.main(argv, now=NOW) == 2 and C in _refused(capsys)                         # exempt for two days of the window
    _log(w.dir, (C, END, True), (A, "2026-12-05T00:00:00Z", True))                       # set only from holdout_end on
    assert hr.main(argv, now=NOW) == 0
    capsys.readouterr()


def test_the_exempt_check_reads_the_log_not_the_view(w, capsys):
    w.session("s1", FULL)
    w.write()
    assert not (w.dir / "playbook.sqlite").exists()                                      # no view file at all
    _log(w.dir, (A, "2026-10-01T00:00:00Z", True))
    assert hr.main(["--config", str(w.conf), "--json"], now=NOW) == 2 and A in _refused(capsys)
    (w.dir / "playbook.sqlite").write_text("not a database")                             # a broken view is not even opened
    _log(w.dir)
    assert hr.main(["--config", str(w.conf), "--json"], now=NOW) == 0
    capsys.readouterr()


@pytest.mark.parametrize("raw", [["not json"], ['{"op": "PATCH"}', "{"], ["[1, 2]"]], ids=["text", "cut", "array"])
def test_an_unreadable_delta_log_refuses_the_test_and_is_named_in_the_interim_mode(w, capsys, raw):
    w.session("s1", FULL)
    w.write()
    _log(w.dir, raw=raw)
    argv = ["--config", str(w.conf), "--json"]
    assert hr.main(argv, now=NOW) == 2
    assert "delta log" in _refused(capsys)
    assert hr.main([*argv, "--interim-descriptive"], now=NOW) == 0
    assert json.loads(capsys.readouterr().out)["lessons_exempt"] == "unreadable"


def test_the_interim_mode_prints_the_exempt_ids_as_a_counter(w, capsys):
    w.session("s1", FULL)
    w.write()
    argv = ["--config", str(w.conf), "--interim-descriptive", "--start", START, "--end", END]
    _log(w.dir, (C, "2026-10-01T00:00:00Z", True), (A, "2026-11-10T00:00:00Z", True), (A, "2026-11-11T00:00:00Z", False))
    assert hr.main([*argv, "--json"], now=NOW) == 0
    assert json.loads(capsys.readouterr().out)["lessons_exempt"] == sorted([A, C])
    assert hr.main(argv, now=NOW) == 0
    assert f"lessons_exempt {', '.join(sorted([A, C]))}" in capsys.readouterr().out
    _log(w.dir)
    assert hr.main(argv, now=NOW) == 0
    assert "lessons_exempt" not in capsys.readouterr().out
