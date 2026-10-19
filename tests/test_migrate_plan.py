import json

import pytest

from .migrate_support import Env
from .test_migrate_apply import T_ARROW, T_LEAD, T_PLAIN

QUARANTINED = "Totally harmless looking text about initech-site that Haiku flagged"


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def rec(env, lesson_id):
    return next(r for r in env.classified_rows() if r["id"] == lesson_id)


def test_override_skip_is_listed_and_not_stored(env):
    env.set_overrides([{"id": "auto-a0000013", "action": "skip"}])
    assert env.run() == 0
    row = env.id_map()["auto-a0000013"]
    assert row["action_in"] == "skip" and row["action_out"] == "skip" and row["reason_code"] == "skipped"
    assert env.count("skipped") == 1
    assert not any(les["text"] == T_PLAIN for les in env.stored().values())


def test_override_text_replaces_legacy_text(env):
    env.set_overrides([{"id": "auto-a0000006", "action": "keep_active",
                        "text": "Wait for the index rebuild to finish before the nightly diff step"}])
    assert env.run() == 0
    assert env.by_text("Wait for the index rebuild to finish before the nightly diff step")["status"] == "active"
    assert env.id_map()["auto-a0000006"]["reason_code"] is None


def test_override_text_is_normalized(env):
    env.set_overrides([{"id": "auto-a0000013", "action": "keep_dormant",
                        "text": "Smoke test — run it after each deploy of acme-widgets"}])
    assert env.run() == 0
    assert env.by_text("Smoke test - run it after each deploy of acme-widgets")["status"] == "dormant"


def test_override_releases_quarantined_item(env):
    env.set_overrides([{"id": "auto-a0000010", "action": "keep_active"}])
    assert env.run() == 0
    assert env.by_text(QUARANTINED)["status"] == "active"
    assert env.id_map()["auto-a0000010"]["action_in"] == "keep_active"
    assert env.count("quarantine") == 0


def test_override_can_retire_an_active_item(env):
    env.set_overrides([{"id": "auto-a0000013", "action": "retire"}])
    assert env.run() == 0
    assert env.id_map()["auto-a0000013"]["reason_code"] == "retire"
    assert not any(les["text"] == T_PLAIN for les in env.stored().values())


def test_quarantine_without_override_never_shows_text(env):
    assert env.run("--dry-run") == 0
    blob = "".join(p.read_text() for p in env.out.iterdir())
    assert QUARANTINED not in blob and "harmless looking" not in blob


def test_override_with_scrub_hit_text_is_still_gated(env):
    env.set_overrides([{"id": "auto-a0000010", "action": "keep_active",
                        "text": "Rotate the acme-secret value every quarter"}])
    assert env.run() == 0
    assert env.id_map()["auto-a0000010"]["reason_code"] == "scrub_hit"
    assert "acme-secret" not in env.read("review_packet.md")


def test_unknown_action_is_an_error_row(env):
    rows = env.classified_rows()
    rows[0]["proposed_action"] = "make_it_so"
    env.set_classified(rows)
    assert env.run() == 0
    row = env.id_map()["auto-a0000001"]
    assert row["reason_code"] == "unknown_action" and row["action_out"] == "error"


def test_bad_classified_line_is_reported_not_raised(env):
    env.classified.write_text(env.classified.read_text() + "{not json\n")
    assert env.run("--dry-run") == 0
    assert "classified.jsonl line 21" in env.read("MIGRATION_REPORT.md")


def test_lead_keeps_unranked_action_and_members_stand_alone(env):
    rows = env.classified_rows()
    next(r for r in rows if r["id"] == "auto-a000000a")["proposed_action"] = "quarantine"
    env.set_classified(rows)
    assert env.run() == 0
    ids = env.id_map()
    assert ids["auto-a000000a"]["action_out"] == "quarantine"
    assert ids["auto-a000000b"]["merged_into"] is None and ids["auto-a000000b"]["status"] == "active"
    assert not any(les["text"] == T_LEAD for les in env.stored().values())


def test_member_with_other_action_is_handled_by_its_own_action(env):
    rows = env.classified_rows()
    next(r for r in rows if r["id"] == "auto-a000000b")["proposed_action"] = "route_to_handoff"
    env.set_classified(rows)
    assert env.run() == 0
    assert {r["id"] for r in env.handoff()} == {"auto-a000000b", "auto-a000000c"}
    lead = env.id_map()["auto-a000000a"]
    assert lead["action_out"] == "keep_dormant" and lead["merged_from"] == []


def test_strongest_member_wins_for_lead(env):
    rows = env.classified_rows()
    next(r for r in rows if r["id"] == "auto-a000000a")["proposed_action"] = "mark_compiled"
    next(r for r in rows if r["id"] == "auto-a000000b")["proposed_action"] = "keep_dormant"
    env.set_classified(rows)
    assert env.run() == 0
    assert env.by_text(T_LEAD)["status"] == "dormant" and env.by_text(T_LEAD)["kind"] == "gotcha"


def test_skipped_lead_frees_its_members(env):
    env.set_overrides([{"id": "auto-a000000a", "action": "skip"}])
    assert env.run() == 0
    assert env.id_map()["auto-a000000b"]["merged_into"] is None
    assert env.id_map()["auto-a000000b"]["status"] == "active"


def test_identical_text_in_one_run_is_stored_once(env):
    rows = env.classified_rows()
    twin = dict(rec(env, "auto-a0000013"), id="auto-a0000014")
    rows.append(twin)
    env.set_classified(rows)
    legacy = json.loads((env.legacy_dir / "lessons-a.json").read_text())
    legacy["lessons"].append({"id": "auto-a0000014", "text": T_PLAIN, "created": "2026-07-06"})
    (env.legacy_dir / "lessons-a.json").write_text(json.dumps(legacy))
    assert env.run() == 0
    assert env.count("already_present") == 1
    assert env.id_map()["auto-a0000014"]["reason_code"] == "already_present"
    assert env.log.verify() == 10


def test_classified_created_wins_over_legacy_created(env):
    rows = env.classified_rows()
    next(r for r in rows if r["id"] == "auto-a0000013")["created"] = "2026-06-15"
    env.set_classified(rows)
    assert env.run() == 0
    assert env.by_text(T_PLAIN)["created"] == "2026-06-15T00:00:00Z"


def test_malformed_created_is_treated_as_undated(env):
    rows = env.classified_rows()
    r = next(r for r in rows if r["id"] == "auto-a0000013")
    r["created"] = "last tuesday"
    env.set_classified(rows)
    legacy = json.loads((env.legacy_dir / "lessons-a.json").read_text())
    next(x for x in legacy["lessons"] if x["id"] == "auto-a0000013")["created"] = "also bad"
    (env.legacy_dir / "lessons-a.json").write_text(json.dumps(legacy))
    (env.legacy_dir / "lessons-b.json").unlink()  # its copy of this lesson carries a valid date
    assert env.run() == 0
    assert env.by_text(T_PLAIN)["created"] > "2026-10"


def test_legacy_file_without_lessons_is_reported(env):
    (env.legacy_dir / "lessons-c.json").write_text("{broken")
    assert env.run("--dry-run") == 0
    assert "lessons-c.json" in env.read("MIGRATION_REPORT.md")
    assert env.count("legacy files") == 3


def test_arrow_lesson_roundtrips_normalized(env):
    assert env.run() == 0
    assert env.by_text(T_ARROW)["id"] == env.id_map()["auto-a0000001"]["lesson_id"]
