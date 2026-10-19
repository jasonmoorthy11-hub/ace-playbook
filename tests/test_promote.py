import json
import sys

import pytest

from ace_playbook.store import DeltaLog, SchemaError, view
from ace_playbook.store import __main__ as cli
from ace_playbook.store import promote as promote_mod

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


LEGACY = [{"quote": "Use python3, not python, for scripts", "origin": "user_file",
           "session_id": "legacy:abc123"}]


def _legacy_lesson(log, status=None, **extra):
    d = add_draft(scope=GLOBAL, evidence=LEGACY, **({"status": status} if status else {}), **extra)
    log.append(d, ts="2026-01-01T00:00:00Z")
    return d["lesson_id"]


def test_bare_active_patch_is_refused_when_only_legacy_evidence(tmp_path):
    # Migrated `legacy:` evidence is self-quoted, so it cannot justify a live status on a PATCH.
    log = DeltaLog(tmp_path / "deltas.jsonl")
    lid = _legacy_lesson(log)
    bare = {"op": "PATCH", "lesson_id": lid, "fields": {"status": "active"}}
    with pytest.raises(SchemaError, match="live status requires user-origin evidence"):
        log.append(bare)
    assert log.state()[lid]["status"] == "provisional" and log.verify() == 1


def test_patch_active_allowed_with_nonlegacy_user_evidence(tmp_path):
    log = DeltaLog(tmp_path / "deltas.jsonl")
    lid = _legacy_lesson(log)
    log.append(_patch(lid, evidence("user_message", "I typed this one myself"), status="active"))
    assert log.state()[lid]["status"] == "active"
    # a non-legacy item already on the lesson also suffices for a later bare PATCH
    d = add_draft("Second lesson with typed evidence", GLOBAL)
    log.append(d)
    log.append({"op": "PATCH", "lesson_id": d["lesson_id"], "fields": {"status": "active"}})
    assert log.state()[d["lesson_id"]]["status"] == "active" and log.verify() == 4


def test_existing_log_with_add_active_legacy_still_rebuilds_and_verifies(tmp_path):
    log = DeltaLog(tmp_path / "deltas.jsonl")
    lid = _legacy_lesson(log, status="active")  # ADD active on legacy evidence: origin-only rule
    other = _legacy_lesson_text(log, "Another migrated lesson stays provisional")
    log.append({"op": "PATCH", "lesson_id": lid, "fields": {"pinned": True}})
    log.append({"op": "PATCH", "lesson_id": lid, "fields": {"text": "Use python3 for scripts, always"}})
    log.append({"op": "NOOP", "lesson_id": lid, "counters": {"shown": 1}})
    assert log.verify() == 5
    db = tmp_path / "view.sqlite"
    state = log.rebuild_view(db)
    assert state[lid]["status"] == "active" and state[lid]["pinned"]
    assert state[other]["status"] == "provisional"
    assert view.view_head(db) == log.head_hash()


def _legacy_lesson_text(log, text):
    d = add_draft(text, GLOBAL, evidence=[{**LEGACY[0], "quote": text, "session_id": "legacy:def456"}])
    log.append(d, ts="2026-01-02T00:00:00Z")
    return d["lesson_id"]


def test_promote_cli_still_succeeds_on_a_legacy_only_lesson(env, capsys):
    conf, log = env
    lid = _legacy_lesson(log)
    assert run(conf, "promote", lid, "--evidence-quote", QUOTE) == 0
    assert log.state()[lid]["status"] == "active" and log.verify() == 2


def _full_lesson(log):
    ev = [{"quote": f"Typed quote number {i} here", "origin": "user_message"} for i in range(5)]
    d = add_draft("Lesson that already holds five evidence items", GLOBAL, evidence=ev)
    log.append(d, ts="2026-01-01T00:00:00Z")
    return d["lesson_id"]


def test_append_time_refusal_evidence_full_leaves_log_untouched(env, capsys):
    conf, log = env
    lid = _full_lesson(log)
    before = log.path.read_bytes()
    refused(capsys, run(conf, "promote", lid, "--evidence-quote", QUOTE), "evidence full")
    assert log.path.read_bytes() == before and log.verify() == 1


def test_dry_run_simulates_the_fold(env, capsys):
    conf, log = env
    lid = _full_lesson(log)
    before = log.path.read_bytes()
    refused(capsys, run(conf, "promote", lid, "--evidence-quote", QUOTE, "--dry-run"),
            "evidence full")
    assert log.path.read_bytes() == before


def test_patch_adding_legacy_tagged_evidence_cannot_justify_active(tmp_path):
    log = DeltaLog(tmp_path / "deltas.jsonl")
    lid = _legacy_lesson(log)
    fake = [{"quote": "Looks typed but is tagged legacy", "origin": "user_file",
             "session_id": "legacy:zzz999"}]
    with pytest.raises(SchemaError, match="live status requires user-origin evidence"):
        log.append(_patch(lid, fake, status="active"))
    assert log.state()[lid]["status"] == "provisional" and log.verify() == 1


def test_malformed_lesson_id_is_refused_before_lookup(env, capsys):
    conf, log = env
    seed(log)
    refused(capsys, run(conf, "promote", "not-an-id; rm", "--evidence-quote", QUOTE), "L-xxxxxxxx")


def _answers(monkeypatch, *answers):
    asked = []
    queue = list(answers)

    def fake(text):
        asked.append(text)
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr(promote_mod, "_prompt", fake)
    return asked


def test_interactive_prompts_for_quote_then_confirms(env, monkeypatch, capsys):
    conf, log = env
    lid = seed(log)
    asked = _answers(monkeypatch, QUOTE, "y")
    assert run(conf, "promote", lid) == 0
    assert asked[0].startswith("Evidence quote (your own words, 12-400 chars): ")
    assert asked[1] == f"Promote {lid}? [y/N] "
    assert log.read()[-1]["fields"]["evidence"][0]["quote"] == QUOTE
    assert log.state()[lid]["status"] == "active"


def test_interactive_default_answer_does_not_promote(env, monkeypatch, capsys):
    conf, log = env
    lid = seed(log)
    before = log.path.read_bytes()
    _answers(monkeypatch, QUOTE, "")
    refused_rc = run(conf, "promote", lid)
    assert refused_rc == 2 and "not confirmed" in capsys.readouterr().err.splitlines()[-1]
    assert log.path.read_bytes() == before


def test_interactive_without_a_readable_terminal_refuses(env, monkeypatch, capsys):
    conf, log = env
    lid = seed(log)
    before = log.path.read_bytes()
    _answers(monkeypatch)  # first prompt raises EOFError
    assert run(conf, "promote", lid) == 2
    assert capsys.readouterr().err.splitlines()[-1] == "error: could not read from the terminal"
    assert log.path.read_bytes() == before


def test_interactive_quote_is_still_scrubbed(env, monkeypatch, capsys):
    conf, log = env
    lid = seed(log)
    _answers(monkeypatch, f"Yes that is right for {DENY} work", "y")
    assert run(conf, "promote", lid) == 2
    assert "scrub" in capsys.readouterr().err.splitlines()[-1]


def test_view_rebuild_failure_after_promote_is_a_clean_message(env, monkeypatch, capsys):
    conf, log = env
    lid = seed(log)

    def boom(*_a, **_k):
        raise RuntimeError("/secret/path exploded")

    monkeypatch.setattr(DeltaLog, "rebuild_view", boom)
    assert run(conf, "promote", lid, "--evidence-quote", QUOTE) == 0
    out = capsys.readouterr()
    assert "promoted; view rebuild failed, run `rebuild`" in out.out
    assert "secret" not in out.out + out.err and log.state()[lid]["status"] == "active"
