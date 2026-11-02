"""`compile/holdout_report.py`: the pre-registered analysis of the per-session lesson holdout. Everything is invented (session ids, seeds, stamps)."""

import json
import random

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


def inj(sid, shown=(), hidden=(), rate=0.5, source="startup", refused="", ts=INSIDE):
    return {"ts": ts, "session_id": sid, "source": source, "shown_ids": list(shown), "holdout_ids": list(hidden), "holdout_rate": rate,
            "seed_fp": "00000000", "refused": refused}


def srow(sid, start=INSIDE, prompts=2):
    return {"detector": "_session", "tier": "offline", "session": st.session_hash(sid), "ts": "2026-11-20T00:00:00Z", "count": 5, "prompts": prompts,
            "start": start, "eligible": []}


def frow(sid, lid, count, rid):
    return {"detector": DET[lid], "tier": "offline", "session": st.session_hash(sid), "count": count, "id": rid, "key": "", "ts": INSIDE}


class World:
    def __init__(self, tmp_path, seed=SEED):
        self.dir, self.inj, self.ev = tmp_path, tmp_path / "injections.jsonl", tmp_path / "detector_events.jsonl"
        self.conf = tmp_path / "config.json"
        self.conf.write_text(json.dumps({"data_dir": str(tmp_path), **({"holdout_permutation_seed": seed} if seed else {})}))
        self.irows, self.erows = [], []

    def write(self):
        self.inj.write_text("".join(json.dumps(r) + "\n" for r in self.irows))
        self.ev.write_text("".join(json.dumps(r) + "\n" for r in self.erows))

    def run(self, capsys, *extra, now=NOW, start=START, end=END):
        self.write()
        rc = hr.main(["--config", str(self.conf), "--start", start, "--end", end, "--json", *extra], now=now)
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
    assert res["config"]["min_prompts"] == 1 and hr.main(["--config", str(w.conf), "--start", START, "--end", END, "--min-prompts", "0", "--json"],
                                                           now=NOW) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["below_min_prompts"] == 0


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


@pytest.mark.parametrize("args", [["--start", "yesterday", "--end", END], ["--start", END, "--end", START], ["--start", START]])
def test_bad_dates_are_a_usage_error(w, capsys, args):
    with pytest.raises(SystemExit) as e:
        hr.main(["--config", str(w.conf), *args], now=NOW)
    assert e.value.code == 2
    capsys.readouterr()


def test_the_interim_mode_prints_per_arm_counts_and_means_but_no_test(w, capsys):
    for i in range(6):
        w.session(f"s{i}", {A: "shown", B: "hidden", C: "shown"} if i % 2 else {A: "hidden", B: "shown", C: "hidden"}, fires={A: i})
    rc, out, err = w.run(capsys, "--interim-descriptive", now="2026-11-15T00:00:00Z")
    res = json.loads(out)
    assert rc == 0 and res["heading"] == HEADING and err == ""
    assert lesson(res, A)["n"] == {"shown": 3, "hidden": 3} and set(lesson(res, A)) == {"lesson", "detector", "n", "not_assigned", "any_rate", "mean"}
    assert not {"p", "ci95", "wilson95", "holm_p", "reject", "D", "rate_ratio", "primary", "secondary"} & keys(res)
    assert hr.main(["--config", str(w.conf), "--start", START, "--end", END, "--interim-descriptive"], now="2026-11-15T00:00:00Z") == 0
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
    assert hr.main(["--config", str(w.conf), "--start", START, "--end", END], now=NOW) == 0
    text = capsys.readouterr().out
    assert A in text and "primary" in text.lower() and "holm" in text.lower() and HEADING not in text and SEED not in text
