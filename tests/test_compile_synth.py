"""Step 5c: `compile replay-synthetic`, the frozen shadow corpus, and the row schema of the hook (`v`, `dur_ms`)."""

import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

from ace_playbook.compile import __main__ as cli
from ace_playbook.compile import catalogue, evidence, labels, report, shadow, synth
from ace_playbook.fixtures.generate import generate

from . import compile_support as cs

ROOT = Path(__file__).resolve().parent.parent
FROZEN = ROOT / "ace_playbook" / "fixtures" / "frozen"
SYN_FILE = "shadow.synthetic.jsonl"
HOOK_KEYS = {"v", "ts", "proposal_id", "lesson_id", "mode", "event", "tool_name", "session", "payload_sha256", "would_fire", "cause",
             "tier", "dur_ms"}


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = cs.real_world(tmp_path)
    monkeypatch.setenv("ACE_PLAYBOOK_CONFIG", world.conf)
    return world


def replay(w, *args, capsys):
    code = cli.main(["--config", w.conf, "replay-synthetic", *args])
    out = capsys.readouterr()
    return code, out.out, out.err


def syn_rows(w):
    return [json.loads(line) for line in w.cfg.path(SYN_FILE).read_text().splitlines()]


def fire_live(w, monkeypatch, cmd=cs.RM_CMD, sid="sess-live-1"):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(cs.pre_payload(cmd, sid)).encode())))
    assert shadow.main(["pre"], w.cfg) == 0


def corpus_copy(tmp_path, flip=None):
    data = json.loads((FROZEN / "shadow_corpus.json").read_text())
    if flip is not None:
        event = data["sessions"][flip[0]]["events"][flip[1]]
        event["expect"] = "no_fire" if event["expect"] == "fire" else "fire"
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(data))
    return path


# ------------------------------------------------------------------ the frozen corpus
def test_the_frozen_corpus_has_at_least_three_sessions_of_six_events_over_the_two_rm_detectors():
    data = json.loads((FROZEN / "shadow_corpus.json").read_text())
    assert data["v"] == 1 and len(data["sessions"]) >= 3 and all(len(s["events"]) >= 6 for s in data["sessions"])
    events = [e for s in data["sessions"] for e in s["events"]]
    assert {e["detector"] for e in events} == {"rm_rf_nonscratch", "rm_rf"} and {e["expect"] for e in events} == {"fire", "no_fire"}
    assert all(set(e) == {"tool_name", "tool_input", "expect", "detector"} for e in events)
    text = (FROZEN / "shadow_corpus.json").read_text()
    assert "/Use" + "rs/" not in text and "@" not in text


def test_the_frozen_manifest_covers_the_corpus_and_the_generator_writes_the_same_bytes(tmp_path):
    man = json.loads((FROZEN / "MANIFEST.json").read_text())["sha256"]
    assert man["shadow_corpus.json"] == hashlib.sha256((FROZEN / "shadow_corpus.json").read_bytes()).hexdigest()
    fresh = generate(1, tmp_path)
    assert fresh["sha256"]["shadow_corpus.json"] == man["shadow_corpus.json"]
    assert (tmp_path / "shadow_corpus.json").read_bytes() == (FROZEN / "shadow_corpus.json").read_bytes()


# ------------------------------------------------------------------ the replay
def test_the_replay_matches_every_expectation_and_prints_counts_only(w, capsys):
    code, out, err = replay(w, capsys=capsys)
    assert (code, err) == (0, "")
    counts = dict(line.split(": ") for line in out.splitlines())
    corpus = json.loads((FROZEN / "shadow_corpus.json").read_text())["sessions"]
    examples = json.loads(cs.EXAMPLES.read_text())["detectors"]
    want = sum(len(s["events"]) for s in corpus) + sum(len(d["no_fire"]) + 1 for d in examples.values())
    assert counts == {"sessions": str(len(corpus) + len(examples)), "events": str(want), "fired": counts["fired"],
                      "disagreements": "0", "errors": "0", "rows": str(want)}
    assert int(counts["fired"]) >= len(examples) + 4
    assert "/w/" not in out and "synthetic-" not in out


def test_every_payload_of_examples_json_is_fed_and_the_rows_say_which_detector_fired(w, capsys):
    replay(w, capsys=capsys)
    rows = syn_rows(w)
    examples = json.loads(cs.EXAMPLES.read_text())["detectors"]
    for det, ex in examples.items():
        hashes = {r["payload_sha256"]: r["would_fire"] for r in rows if r["detector"] == det}
        for payload in ex["no_fire"]:
            assert hashes[evidence.payload_hash(payload)] is False
        assert hashes[evidence.payload_hash(ex["fire"])] is True
    assert {r["detector"] for r in rows} <= set(catalogue.CATALOGUE)


def test_rows_are_synthetic_in_their_own_file_and_the_private_file_never_gains_a_corpus_key(w, capsys, monkeypatch):
    replay(w, capsys=capsys)
    assert not w.cfg.path("shadow.jsonl").exists()                         # the replay never creates the private file
    rows = syn_rows(w)
    assert rows and {r["corpus"] for r in rows} == {"synthetic"} and all(HOOK_KEYS <= set(r) for r in rows)
    fire_live(w, monkeypatch)
    live = cs.shadow_rows(w.data)
    assert len(live) == 1 and "corpus" not in live[0] and set(live[0]) == HOOK_KEYS
    assert oct(w.cfg.path(SYN_FILE).stat().st_mode & 0o777) == "0o600"


def test_a_second_replay_replaces_the_file_instead_of_appending(w, capsys):
    replay(w, capsys=capsys)
    first = len(syn_rows(w))
    replay(w, capsys=capsys)
    assert len(syn_rows(w)) == first


def test_the_report_keeps_the_synthetic_rows_apart_from_the_private_ones(w, capsys, monkeypatch):
    fire_live(w, monkeypatch)
    replay(w, capsys=capsys)
    assert cli.main(["--config", w.conf, "report", "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert set(obj["private"]["proposals"]) == {w.props["rm_rf_nonscratch"]["id"]}
    assert set(obj["private"]["proposals"]).isdisjoint(obj["synthetic"]["proposals"])
    assert obj["rates"]["synthetic"]["corpus"]["sessions"] > 0 and obj["rates"]["private"]["corpus"]["sessions"] == 0
    assert all(p["detector"] in catalogue.CATALOGUE for p in obj["synthetic"]["proposals"].values())
    rows = sum(p["rows"] for p in obj["synthetic"]["proposals"].values())
    assert rows == sum(1 for r in syn_rows(w) if r["would_fire"])               # only fires are rows of the table
    with open(w.cfg.path(SYN_FILE), "a") as fh:                                 # the existing guard: a private row in the synthetic file
        fh.write(json.dumps({**syn_rows(w)[0], "corpus": "private"}) + "\n")
    assert cli.main(["--config", w.conf, "report"]) == 2


def test_the_synthetic_rates_use_every_corpus_session_as_eligible(w, capsys):
    replay(w, capsys=capsys)
    cli.main(["--config", w.conf, "report", "--json"])
    rates = json.loads(capsys.readouterr().out)["rates"]["synthetic"]["proposals"]
    by = {p["detector"]: p for p in rates.values()}
    assert by["rm_rf_nonscratch"]["eligible"] == 4 and by["rm_rf_nonscratch"]["sessions_with_fire"] == 3     # ex-01, 01, 04 fire; 02 does not
    assert by["rm_rf"]["eligible"] == 3 and by["rm_rf"]["sessions_with_fire"] == 3
    assert by["rm_rf_nonscratch"]["fire_rate"] == 0.75 and by["rm_rf_nonscratch"]["wilson95"] == [0.3006, 0.9544]


def test_a_disagreeing_corpus_exits_1_and_still_writes_the_rows(w, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(synth, "CORPUS", corpus_copy(tmp_path, flip=(0, 0)))
    code, out, err = replay(w, capsys=capsys)
    assert (code, err) == (1, "") and "disagreements: 1" in out.splitlines()
    assert len(syn_rows(w)) == int(dict(line.split(": ") for line in out.splitlines())["rows"])


def test_an_unknown_detector_or_a_tool_the_detector_does_not_consume_is_a_refusal_with_exit_2(w, capsys, monkeypatch, tmp_path):
    bad = json.loads((FROZEN / "shadow_corpus.json").read_text())
    bad["sessions"][0]["events"][0]["detector"] = "no_such_detector"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad))
    monkeypatch.setattr(synth, "CORPUS", path)
    code, out, err = replay(w, capsys=capsys)
    assert (code, out) == (2, "") and err.startswith("error:") and not w.cfg.path(SYN_FILE).exists()
    bad["sessions"][0]["events"][0].update(detector="rm_rf", tool_name="Read")
    path.write_text(json.dumps(bad))
    assert replay(w, capsys=capsys)[0] == 2
    path.write_text("{not json")
    assert replay(w, capsys=capsys)[0] == 2


def test_out_redirects_the_rows(w, capsys, tmp_path):
    target = tmp_path / "elsewhere" / "rows.jsonl"
    assert replay(w, "--out", str(target), capsys=capsys)[0] == 0
    assert target.exists() and not w.cfg.path(SYN_FILE).exists()


def test_the_replay_uses_a_scratch_state_directory(w, capsys, tmp_path):
    replay(w, capsys=capsys)
    assert not (w.data / "shadow_state").exists()


# ------------------------------------------------------------------ the hook row
def test_the_hook_row_has_the_exact_key_set_and_types_and_a_row_id_that_ignores_the_new_keys(w, monkeypatch):
    fire_live(w, monkeypatch)
    (row,) = cs.shadow_rows(w.data)
    assert set(row) == HOOK_KEYS and row["v"] == 1 and type(row["dur_ms"]) is int and row["dur_ms"] >= 0
    assert all(type(row[k]) is str for k in HOOK_KEYS - {"v", "would_fire", "dur_ms"}) and row["would_fire"] is True
    assert labels.row_id(row) == hashlib.sha256("|".join([row["ts"], row["session"], row["proposal_id"], row["payload_sha256"]]).encode()
                                               ).hexdigest()[:8]


def test_dur_ms_is_the_monotonic_time_from_the_start_of_the_call_to_the_row(w, monkeypatch):
    ticks = iter([100.0])
    monkeypatch.setattr(shadow.time, "monotonic", lambda: next(ticks, 100.25))
    fire_live(w, monkeypatch)
    assert cs.shadow_rows(w.data)[0]["dur_ms"] == 250


def test_the_report_text_prints_cost_for_rows_with_dur_ms(w, capsys, monkeypatch):
    fire_live(w, monkeypatch)
    cli.main(["--config", w.conf, "report"])
    assert "dur_ms p50 " in capsys.readouterr().out and report.metrics.DASH == "-"
