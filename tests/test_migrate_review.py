"""Fixes from the Opus review: secret leaks, paraphrase leads, quarantine flag, promotion, robustness."""

import json
import os

import pytest

from ace_playbook.store import DeltaLog

from .migrate_support import Env
from .test_migrate_apply import T_LEAD, T_PLAIN

SECRET = "AKIA" + "FAKE" + "ABCDEFGH1234"  # AWS-shaped, assembled at runtime, never a real key
PAD = "padding words " * 40


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def all_output(env) -> str:
    return "".join(p.read_text() for p in sorted(env.out.iterdir()))


# ------------------------------------------------------------------ 1. secrets never reach outputs
@pytest.mark.parametrize("mode", ["--dry-run", "--apply"])
def test_secrets_in_too_long_schema_error_and_handoff_rows_are_masked(env, mode):
    env.add_lesson("auto-b0000001", f"Leaked key {SECRET} appears in the notes. {PAD}", "keep_active")
    env.add_lesson("auto-b0000002", f"Never paste === {SECRET} into notes", "keep_active")
    env.add_lesson("auto-b0000003", f"Hand over key {SECRET} to the next owner", "route_to_handoff")
    env.add_lesson("auto-b0000004", "Hand AcmeSecretClient details to the next owner", "route_to_handoff")
    assert env.run(mode) == 0
    ids = env.id_map()
    assert ids["auto-b0000001"]["reason_code"] == "too_long"
    assert ids["auto-b0000002"]["reason_code"] == "schema_error"
    assert SECRET not in all_output(env) and "AcmeSecretClient" not in all_output(env)
    assert "[REDACTED:aws_access_key]" in env.packet_section("too_long")
    assert "[REDACTED:aws_access_key]" in env.packet_section("schema_error")
    rows = {r["id"]: r for r in env.handoff()}
    assert "[REDACTED:aws_access_key]" in rows["auto-b0000003"]["text"]
    assert "[deny-term]" in rows["auto-b0000004"]["text"]
    assert "- handoff rows with masked hits: 2" in env.read("MIGRATION_REPORT.md")


def test_too_long_row_without_hits_keeps_full_text(env):
    env.add_lesson("auto-b0000005", f"Plain long lesson. {PAD}", "keep_active")
    assert env.run("--dry-run") == 0
    assert PAD.strip() in env.packet_section("too_long")


# ------------------------------------------------------------------ 2. paraphrase lead fails a gate
LEAD_FAILS = {
    "too_long": " ".join(["word"] * 120),
    "too_short": "ab",
    "scrub_hit": "Rotate the acme-secret value every quarter",
    "schema_error": "Never paste === into notes",
}


@pytest.mark.parametrize("code", sorted(LEAD_FAILS))
def test_failed_lead_releases_its_members(env, code):
    env.set_overrides([{"id": "auto-a000000a", "action": "keep_dormant", "text": LEAD_FAILS[code]}])
    assert env.run() == 0
    ids = env.id_map()
    assert ids["auto-a000000a"]["reason_code"] == code and ids["auto-a000000a"]["merged_from"] == []
    member = ids["auto-a000000b"]
    assert member["merged_into"] is None and member["action_out"] == "keep_active"
    assert member["status"] == "provisional" and member["lesson_id"] in env.stored()


def test_lead_already_present_still_absorbs_members(env):
    assert env.run() == 0
    assert env.run() == 0
    ids = env.id_map()
    assert ids["auto-a000000a"]["reason_code"] == "already_present"
    assert ids["auto-a000000b"]["merged_into"] == "auto-a000000a"


# ------------------------------------------------------------------ 3. quarantine flag
def test_quarantine_flag_forces_quarantine_without_override(env):
    rows = env.classified_rows()
    next(r for r in rows if r["id"] == "auto-a0000013")["quarantine"] = True
    env.set_classified(rows)
    assert env.run() == 0
    row = env.id_map()["auto-a0000013"]
    assert row["action_in"] == "quarantine" and row["reason_code"] == "quarantine" and row["lesson_id"] is None
    assert not any(les["text"] == T_PLAIN for les in env.stored().values())
    assert "- forced to quarantine by flag: 1" in env.read("MIGRATION_REPORT.md")
    assert T_PLAIN not in env.read("review_packet.md")


def test_override_is_the_release_path_for_a_flagged_record(env):
    rows = env.classified_rows()
    next(r for r in rows if r["id"] == "auto-a0000013")["quarantine"] = True
    env.set_classified(rows)
    env.set_overrides([{"id": "auto-a0000013", "action": "keep_dormant"}])
    assert env.run() == 0
    assert env.by_text(T_PLAIN)["status"] == "dormant"
    assert "- forced to quarantine by flag: 0" in env.read("MIGRATION_REPORT.md")


# ------------------------------------------------------------------ 4. legacy_unverified / promotion
def test_only_a_keep_active_override_yields_active(env):
    env.set_overrides([{"id": "auto-a0000013", "action": "keep_active"},
                       {"id": "auto-a0000011", "action": "keep_dormant"}])
    assert env.run() == 0
    assert env.by_text(T_PLAIN)["status"] == "active"
    assert env.id_map()["auto-a0000013"]["reason_code"] is None
    assert [les["status"] for les in env.stored().values()].count("active") == 1
    assert env.count("active") == 1 and env.count("legacy_unverified") == 5  # 7, minus the promoted one and the dormant one


def test_override_on_a_member_promotes_the_lead(env):
    env.set_overrides([{"id": "auto-a000000b", "action": "keep_active"}])
    assert env.run() == 0
    assert env.by_text(T_LEAD)["status"] == "active"
    assert env.id_map()["auto-a000000b"]["merged_into"] == "auto-a000000a"


def test_promoted_but_inert_stays_provisional_with_inert_reason(env):
    env.set_overrides([{"id": "auto-a0000004", "action": "keep_active"}])
    assert env.run() == 0
    row = env.id_map()["auto-a0000004"]
    assert row["status"] == "provisional" and row["inert_reason"] == "domain" and row["reason_code"] is None
    assert "auto-a0000004 | globex-api | inert: domain" in env.packet_section("provisional")


def test_no_override_means_no_active_lessons_at_all(env):
    assert env.run() == 0
    assert "active" not in {les["status"] for les in env.stored().values()}


# ------------------------------------------------------------------ 5. input checks
def test_duplicate_classified_ids_process_first_and_note_the_rest(env):
    rows = env.classified_rows()
    rows.append(dict(rows[0], proposed_action="retire"))
    env.set_classified(rows)
    assert env.run() == 0
    assert env.id_map()["auto-a0000001"]["action_in"] == "keep_active"
    assert len(env.read("id_map.jsonl").splitlines()) == 20
    rep = env.read("MIGRATION_REPORT.md")
    assert "- duplicate classified ids ignored: 1" in rep and "- classified records: 21" in rep


def test_unmatched_override_is_reported(env):
    env.set_overrides([{"id": "auto-zzzzzzzz", "action": "retire"}])
    assert env.run() == 0
    assert "- overrides matching no record: 1" in env.read("MIGRATION_REPORT.md")
    assert env.count("overrides applied") == 0


def test_legacy_conflicts_and_date_counts(env):
    rows = env.classified_rows()
    next(r for r in rows if r["id"] == "auto-a0000013")["created"] = "31st of July"
    env.set_classified(rows)
    assert env.run("--dry-run") == 0
    rep = env.read("MIGRATION_REPORT.md")
    assert "- legacy ids with differing text: 1" in rep  # a0000002 in the second file
    assert "- records with no usable date: 1" in rep  # a0000012
    assert "- records with a malformed date: 1" in rep  # a0000013 falls back to the legacy date


def test_override_text_rescues_a_missing_join(env):
    env.set_overrides([{"id": "auto-a0000099", "action": "keep_dormant",
                        "text": "Keep the rescued lesson text on this line"}])
    assert env.run() == 0
    assert env.by_text("Keep the rescued lesson text on this line")["status"] == "dormant"
    assert env.count("missing join") == 0 and env.count("rescued by override text") == 1
    assert env.count("joined") == 19


# ------------------------------------------------------------------ 6. robustness
def test_oserror_in_one_batch_is_apply_failed_and_report_is_written(env, monkeypatch):
    real = DeltaLog.append_many

    def flaky(self, drafts, *, ts=None, max_touched=None):
        if ts == "2026-07-03T00:00:00Z":
            raise OSError("disk full")
        return real(self, drafts, ts=ts, max_touched=max_touched)

    monkeypatch.setattr(DeltaLog, "append_many", flaky)
    assert env.run() == 1
    row = env.id_map()["auto-a000000a"]
    assert row["reason_code"] == "apply_failed" and row["lesson_id"] is None and row["status"] is None
    assert env.count("apply_failed") == 1
    assert env.log.verify() == 9


def test_unexpected_error_still_leaves_a_report(env, monkeypatch):
    real = DeltaLog.append_many

    def boom(self, drafts, *, ts=None, max_touched=None):
        if ts == "2026-07-05T00:00:00Z":
            raise RuntimeError("boom")
        return real(self, drafts, ts=ts, max_touched=max_touched)

    monkeypatch.setattr(DeltaLog, "append_many", boom)
    with pytest.raises(RuntimeError):
        env.run()
    assert env.count("apply_failed") == 8  # the failing batch and every later one
    assert env.count("provisional") == 1 and env.count("dormant") == 1  # only what really landed
    assert env.log.verify() == 2


# ------------------------------------------------------------------ 7/8. mode field, file modes
@pytest.mark.parametrize("flag,mode", [("--dry-run", "dry-run"), ("--apply", "apply")])
def test_every_jsonl_row_carries_the_mode(env, flag, mode):
    assert env.run(flag) == 0
    rows = [json.loads(ln) for name in ("id_map.jsonl", "handoff.jsonl")
            for ln in env.read(name).splitlines()]
    assert rows and {r["mode"] for r in rows} == {mode}


def test_outputs_are_created_private(env):
    assert env.run("--dry-run") == 0
    for p in env.out.iterdir():
        assert os.stat(p).st_mode & 0o777 == 0o600, p.name
    assert os.stat(env.out).st_mode & 0o777 == 0o700
