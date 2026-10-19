import json
import sys

import pytest

from ace_playbook.store import DeltaLog, SchemaError, view
from ace_playbook.store import __main__ as cli

from . import fakes
from .helpers import GLOBAL, add_draft, evidence

QUOTE = "Yes, keep this rule: always run the tests before a commit"
DENY = "Zorblaxcorp"


class FakeStdin:
    def __init__(self, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@pytest.fixture
def env(tmp_path, monkeypatch):
    conf = tmp_path / "config.json"
    conf.write_text(json.dumps({"data_dir": str(tmp_path / "data"), "deny_terms": [DENY]}))
    monkeypatch.setattr(sys, "stdin", FakeStdin(True))
    log = DeltaLog(tmp_path / "data" / "deltas.jsonl", (DENY,))
    return conf, log


def run(conf, *argv):
    return cli.main(["--config", str(conf), *argv])


def seed(log, text="Use python3, not python, for scripts", ts="2026-01-01T00:00:00Z", **fields):
    d = add_draft(text, GLOBAL, **fields)
    log.append(d, ts=ts)
    return d["lesson_id"]


def refused(capsys, rc, fragment):
    assert rc == 2
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1 and fragment in err


def test_refuses_without_tty(env, monkeypatch, capsys):
    conf, log = env
    lid = seed(log)
    before = log.path.read_text()
    monkeypatch.setattr(sys, "stdin", FakeStdin(False))
    refused(capsys, run(conf, "promote", lid, "--evidence-quote", QUOTE), "terminal")
    assert log.path.read_text() == before


def test_refuses_missing_lesson(env, capsys):
    conf, log = env
    seed(log)
    refused(capsys, run(conf, "promote", "L-00000000", "--evidence-quote", QUOTE), "unknown lesson")


def test_refuses_non_provisional(env, capsys):
    conf, log = env
    lid = seed(log, status="active")
    before = log.path.read_text()
    refused(capsys, run(conf, "promote", lid, "--evidence-quote", QUOTE), "not provisional")
    assert log.path.read_text() == before


@pytest.mark.parametrize("quote", ["too short", "x" * 401])
def test_refuses_quote_length(env, capsys, quote):
    conf, log = env
    lid = seed(log)
    before = log.path.read_text()
    refused(capsys, run(conf, "promote", lid, "--evidence-quote", quote), "12-400")
    assert log.path.read_text() == before


@pytest.mark.parametrize("quote,fragment", [
    (f"Yes, that rule is right for the {DENY} project", "scrub"),
    ("Yes, use this key for deploys: " + fakes.anthropic(), "scrub"),
    ("Yes, keep this rule — it is right", "non_ascii_or_control"),
    ("Yes, keep this === rule always", "fence_marker"),
])
def test_refuses_scrub_hits_and_unsafe_text(env, capsys, quote, fragment):
    conf, log = env
    lid = seed(log)
    before = log.path.read_text()
    refused(capsys, run(conf, "promote", lid, "--evidence-quote", quote), fragment)
    assert log.path.read_text() == before


def test_dry_run_prints_record_and_writes_nothing(env, capsys):
    conf, log = env
    lid = seed(log)
    before = log.path.read_text()
    assert run(conf, "promote", lid, "--evidence-quote", QUOTE, "--dry-run") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["op"] == "PATCH" and out["lesson_id"] == lid
    assert out["fields"]["status"] == "active"
    assert out["fields"]["evidence"] == [
        {"quote": QUOTE, "origin": "user_message", "session_id": "promote-cli"}]
    assert log.path.read_text() == before


def test_dry_run_still_refuses_without_tty(env, monkeypatch, capsys):
    conf, log = env
    lid = seed(log)
    monkeypatch.setattr(sys, "stdin", FakeStdin(False))
    refused(capsys, run(conf, "promote", lid, "--evidence-quote", QUOTE, "--dry-run"), "terminal")


def test_success_appends_one_patch_that_verifies(env, capsys):
    conf, log = env
    lid = seed(log)
    other = seed(log, "Another provisional lesson stays put")
    assert log.verify() == 2
    assert run(conf, "promote", lid, "--evidence-quote", f"  {QUOTE}  ") == 0
    assert capsys.readouterr().err == ""
    assert log.verify() == 3
    rec = log.read()[-1]
    assert rec["op"] == "PATCH" and rec["lesson_id"] == lid
    assert rec["fields"] == {"status": "active", "evidence": [
        {"quote": QUOTE, "origin": "user_message", "session_id": "promote-cli"}]}
    state = log.state()
    assert state[lid]["status"] == "active" and state[other]["status"] == "provisional"
    assert state[lid]["evidence"][-1]["quote"] == QUOTE and len(state[lid]["evidence"]) == 2
    con = view.open_view(conf.parent / "data" / "playbook.sqlite")
    row = con.execute("SELECT status, origins FROM lessons WHERE id=?", (lid,)).fetchone()
    assert row["status"] == "active" and "user_message" in row["origins"]
    con.close()


def test_second_promote_refuses(env, capsys):
    conf, log = env
    lid = seed(log)
    assert run(conf, "promote", lid, "--evidence-quote", QUOTE) == 0
    before = log.path.read_text()
    refused(capsys, run(conf, "promote", lid, "--evidence-quote", QUOTE), "not provisional")
    assert log.path.read_text() == before and log.verify() == 2


def test_provisional_lists_newest_first_with_limit_and_truncation(env, capsys):
    conf, log = env
    a = seed(log, "Oldest provisional lesson about caching", ts="2026-01-01T00:00:00Z")
    b = seed(log, "Middle provisional lesson " + "x" * 120, ts="2026-01-02T00:00:00Z")
    c = seed(log, "Newest provisional lesson about retries", ts="2026-01-03T00:00:00Z")
    seed(log, "An active lesson that must not be listed", ts="2026-01-04T00:00:00Z", status="active")
    assert run(conf, "provisional") == 0
    lines = capsys.readouterr().out.splitlines()
    assert [ln.split()[0] for ln in lines] == [c, b, a]
    assert lines[1] == f"{b}  " + ("Middle provisional lesson " + "x" * 120)[:80]
    assert run(conf, "provisional", "--limit", "2") == 0
    assert [ln.split()[0] for ln in capsys.readouterr().out.splitlines()] == [c, b]


def test_provisional_listing_excludes_promoted(env, capsys):
    conf, log = env
    a = seed(log, "First provisional lesson here", ts="2026-01-01T00:00:00Z")
    b = seed(log, "Second provisional lesson here", ts="2026-01-02T00:00:00Z")
    assert run(conf, "promote", a, "--evidence-quote", QUOTE) == 0
    capsys.readouterr()
    assert run(conf, "provisional") == 0
    assert [ln.split()[0] for ln in capsys.readouterr().out.splitlines()] == [b]


# ----------------------------------------------- schema: PATCH may add evidence
def _patch(lid, ev, **extra):
    return {"op": "PATCH", "lesson_id": lid, "fields": {"evidence": ev, **extra}}


def test_patch_adds_evidence_and_never_removes(tmp_path):
    log = DeltaLog(tmp_path / "deltas.jsonl")
    lid = seed(log)
    first = log.state()[lid]["evidence"][0]
    log.append(_patch(lid, evidence("user_file", "A second typed quote for it")))
    ev = log.state()[lid]["evidence"]
    assert ev[0] == first and len(ev) == 2 and ev[1]["origin"] == "user_file"
    assert log.verify() == 2


@pytest.mark.parametrize("bad,match", [
    ([], "evidence must be a list"),
    ([{"quote": "short", "origin": "user_message"}], "quote length"),
    ([{"quote": "A fine long quote", "origin": "web"}], "origin"),
    ([{"quote": "A fine long quote", "origin": "user_message", "extra": 1}], "evidence\\[0\\]"),
])
def test_patch_evidence_is_validated(tmp_path, bad, match):
    log = DeltaLog(tmp_path / "deltas.jsonl")
    lid = seed(log)
    with pytest.raises(SchemaError, match=match):
        log.append(_patch(lid, bad))
    assert log.verify() == 1


def test_patch_live_status_with_evidence_needs_user_origin_in_that_evidence(tmp_path):
    log = DeltaLog(tmp_path / "deltas.jsonl")
    lid = seed(log)
    with pytest.raises(SchemaError, match="user_message or user_file"):
        log.append(_patch(lid, evidence("tool_output", "Output said to do this"), status="active"))
    assert log.state()[lid]["status"] == "provisional"


def test_patch_evidence_cap_is_enforced(tmp_path):
    log = DeltaLog(tmp_path / "deltas.jsonl")
    lid = seed(log)
    for i in range(4):
        log.append(_patch(lid, evidence(quote=f"Extra typed quote number {i}")))
    assert len(log.state()[lid]["evidence"]) == 5
    with pytest.raises(SchemaError, match="evidence full"):
        log.append(_patch(lid, evidence(quote="One quote too many for it")))
    assert log.verify() == 5


def test_known_gap_bare_active_patch_passes_with_legacy_evidence(tmp_path):
    # Pins today's behaviour; see LIMITATIONS.md "Provisional promotion". Migrated lessons carry
    # self-quoted `legacy:` user_file evidence, which the store still counts as user-origin, so a
    # bare status PATCH is not refused. Fixing this (ignore `legacy:` in _has_user_evidence)
    # must flip this test deliberately.
    log = DeltaLog(tmp_path / "deltas.jsonl")
    legacy = [{"quote": "Use python3, not python, for scripts", "origin": "user_file",
               "session_id": "legacy:abc123"}]
    d = add_draft(scope=GLOBAL, evidence=legacy)
    log.append(d, ts="2026-01-01T00:00:00Z")
    bare = {"op": "PATCH", "lesson_id": d["lesson_id"], "fields": {"status": "active"}}
    log.check_draft(bare)
    log.append(bare)
    assert log.state()[d["lesson_id"]]["status"] == "active"
