import json
import re
from collections import Counter

import pytest

from .migrate_support import Env

T_ARROW = 'Use python3 - never plain python to the "python" alias is missing in acme-widgets'
T_BTICK_ACTIVE = "Quote the path with `cp` when the folder name has spaces in it"
T_BTICK_DORMANT = "Wrap the `find` call in quotes when the pattern has a wildcard"
T_DOMAIN = "Edit config.yaml before restarting the worker process"
T_ANGLE = "Replace <tenant> with the real tenant id in the route pattern"
T_LEAD = "Regenerate the seed fixtures after any schema change in globex-api"
T_COMPILED = "Always run the linter before staging files for a commit"
T_GLOBAL = "Commit messages use the conventional commits format"
T_UNKNOWN = "Use a fresh virtualenv per project to avoid package clashes"
T_PLAIN = "Run the smoke test after every deploy of acme-widgets"


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture
def applied(env):
    assert env.run("--apply") == 0
    return env


def test_apply_stores_expected_counts_by_status_and_kind(applied):
    st = applied.stored()
    assert Counter(les["status"] for les in st.values()) == {"provisional": 7, "dormant": 3}
    assert Counter(les["kind"] for les in st.values()) == {
        "gotcha": 7, "rule": 1, "fact": 1, "hook_candidate": 1}
    assert applied.count("active") == 0 and applied.count("provisional") == 7
    assert applied.count("legacy_unverified") == 7
    assert applied.count("dormant") == 3 and applied.count("hook_candidate") == 1


def test_store_chain_verifies(applied):
    assert applied.log.verify() == 10


def test_second_apply_adds_nothing(applied):
    head = applied.log.head_hash()
    assert applied.run("--apply") == 0
    assert applied.log.head_hash() == head and applied.log.verify() == 10
    assert applied.count("already_present") == 10


def test_dry_run_never_creates_the_store_and_writes_all_outputs(env):
    assert env.run("--dry-run") == 0
    assert not env.data_dir.exists()
    for name in ("MIGRATION_REPORT.md", "review_packet.md", "id_map.jsonl", "handoff.jsonl"):
        assert (env.out / name).exists(), name
    assert "DRY RUN" in env.read("MIGRATION_REPORT.md").splitlines()[0]
    assert "DRY RUN" in env.read("review_packet.md").splitlines()[0]
    assert env.count("active") == 0 and env.count("provisional") == 7


def test_dry_run_leaves_an_existing_store_untouched(applied):
    before = applied.cfg.deltas.read_bytes()
    assert applied.run("--dry-run") == 0
    assert applied.cfg.deltas.read_bytes() == before
    assert applied.count("already_present") == 10


def test_apply_header_and_head_hashes(applied):
    assert "APPLY" in applied.read("MIGRATION_REPORT.md").splitlines()[0]
    rep = applied.read("MIGRATION_REPORT.md")
    assert applied.log.head_hash() in rep
    assert "record count delta: 10" in rep


def test_too_long_goes_to_packet_not_store(applied):
    row = applied.id_map()["auto-a0000006"]
    assert row["reason_code"] == "too_long" and row["lesson_id"] is None
    sec = applied.packet_section("too_long")
    assert "auto-a0000006" in sec and "false result" in sec  # full normalized text, never truncated
    assert not any(les["text"].startswith("When the acme-widgets nightly") for les in applied.stored().values())


def test_too_short_and_emoji_only_not_stored(applied):
    ids = applied.id_map()
    assert ids["auto-a0000007"]["reason_code"] == "too_short"
    assert ids["auto-a0000009"]["reason_code"] == "too_short"
    assert applied.count("too_short") == 2


def test_deny_term_is_scrub_hit_and_masked_in_packet(applied):
    assert applied.id_map()["auto-a0000008"]["reason_code"] == "scrub_hit"
    sec = applied.packet_section("scrub_hit")
    assert "auto-a0000008" in sec and "[deny-term]" in sec and "deny_term" in sec
    assert "AcmeSecretClient" not in applied.read("review_packet.md")
    assert "AcmeSecretClient" not in "".join(p.read_text() for p in applied.out.iterdir())


def test_backtick_active_is_provisional_dormant_stays_dormant(applied):
    ids = applied.id_map()
    prov, dorm = applied.by_text(T_BTICK_ACTIVE), applied.by_text(T_BTICK_DORMANT)
    assert prov["status"] == "provisional" and dorm["status"] == "dormant"
    assert ids["auto-a0000002"]["inert_reason"] == "code_fence_or_backtick"
    assert ids["auto-a0000003"]["inert_reason"] == "code_fence_or_backtick"
    sec = applied.packet_section("provisional")
    assert "auto-a0000002" in sec and "auto-a0000003" not in sec


def test_inert_kinds_bare_domain_angle_and_arrow(applied):
    ids = applied.id_map()
    assert ids["auto-a0000004"]["inert_reason"] == "domain"
    assert ids["auto-a0000005"]["inert_reason"] == "angle_brackets"
    assert ids["auto-a0000001"]["inert_reason"] is None  # arrows become words, never "->"
    assert applied.by_text(T_DOMAIN)["status"] == "provisional"
    assert applied.by_text(T_ANGLE)["status"] == "provisional"
    assert applied.by_text(T_ARROW)["status"] == "provisional"  # legacy_unverified, not inert
    assert applied.count("angle_brackets") == 1 and applied.count("domain") == 1


def test_normalized_text_is_what_is_stored(applied):
    les = applied.by_text(T_ARROW)
    assert les["evidence"] == [{"quote": T_ARROW, "origin": "user_file", "session_id": "legacy:auto-a0000001"}]


def test_paraphrase_member_merged_into_lead_with_strongest_action(applied):
    ids = applied.id_map()
    lead, member = ids["auto-a000000a"], ids["auto-a000000b"]
    assert member["merged_into"] == "auto-a000000a" and member["lesson_id"] is None
    assert member["reason_code"] == "merged_member" and member["action_out"] == "merged"
    assert lead["merged_from"] == ["auto-a000000b"] and lead["merged_into"] is None
    assert lead["action_in"] == "keep_dormant" and lead["action_out"] == "keep_active"
    assert applied.by_text(T_LEAD)["status"] == "provisional"
    assert applied.count("groups") == 1 and applied.count("members merged") == 1


def test_created_preserves_legacy_date(applied):
    assert applied.by_text(T_LEAD)["created"] == "2026-07-03T00:00:00Z"
    assert applied.by_text(T_PLAIN)["created"] == "2026-07-06T00:00:00Z"
    assert applied.by_text(T_BTICK_DORMANT)["created"] == "2026-07-01T00:00:00Z"
    recs = applied.log.read()
    stamps = [r["ts"] for r in recs]
    assert stamps[:-1] == sorted(stamps[:-1])  # dated batches ascending, undated batch last


def test_undated_record_is_stamped_now(applied):
    ts = applied.by_text(T_UNKNOWN)["created"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", ts) and ts > "2026-10"
    assert applied.log.read()[-1]["ts"] == ts


def test_handoff_has_exactly_the_route_ids(applied):
    rows = applied.handoff()
    assert [r["id"] for r in rows] == ["auto-a000000c"]
    assert rows[0] == {"mode": "apply", "id": "auto-a000000c", "key": "initech-site", "created": "2026-07-05",
                       "text": "Ask for the staging notes in the handoff file before deploying initech-site"}
    assert applied.count("handoff records") == 1
    assert applied.id_map()["auto-a000000c"]["lesson_id"] is None


def test_retire_drop_junk_quarantine_counted_not_stored(applied):
    ids = applied.id_map()
    for i, code in (("auto-a000000d", "retire"), ("auto-a000000e", "drop_junk"), ("auto-a0000010", "quarantine")):
        assert ids[i]["reason_code"] == code and ids[i]["lesson_id"] is None
    assert applied.count("retire") == 1 and applied.count("drop_junk") == 1 and applied.count("quarantine") == 1


def test_quarantine_row_is_masked(applied):
    sec = applied.packet_section("quarantine")
    assert "auto-a0000010" in sec and "<quarantined; patterns: aws_access_key, email>" in sec
    assert "harmless looking" not in applied.read("review_packet.md")


def test_mark_compiled_is_hook_candidate_dormant(applied):
    les = applied.by_text(T_COMPILED)
    assert les["kind"] == "hook_candidate" and les["status"] == "dormant"


def test_global_key_gives_global_scope_and_rule_kind(applied):
    les = applied.by_text(T_GLOBAL)
    assert les["scope_type"] == "global" and les["scope_key"] == "" and les["kind"] == "rule"


def test_key_unknown_scope_and_fact_kind(applied):
    les = applied.by_text(T_UNKNOWN)
    assert (les["scope_type"], les["scope_key"], les["kind"]) == ("project", "key_unknown", "fact")


def test_applies_when_from_reason_or_literal_fallback(applied):
    assert applied.by_text(T_PLAIN)["applies_when"] == "after deploying the service"
    assert applied.by_text(T_GLOBAL)["applies_when"] == "migrated legacy lesson (global)"  # backtick in reason
    assert applied.by_text(T_UNKNOWN)["applies_when"] == "migrated legacy lesson (key_unknown)"  # reason too short


def test_first_legacy_occurrence_wins(applied):
    assert applied.by_text(T_BTICK_ACTIVE)["created"] == "2026-07-06T00:00:00Z"
    assert not any("must lose" in les["text"] for les in applied.stored().values())


def test_missing_join_is_reported_not_raised(applied):
    row = applied.id_map()["auto-a0000099"]
    assert row["reason_code"] == "missing_join" and row["action_out"] == "error"
    rep = applied.read("MIGRATION_REPORT.md")
    assert "- missing join: 1" in rep and "auto-a0000099" in rep


def test_id_map_has_one_line_per_record_and_no_text(applied):
    lines = applied.read("id_map.jsonl").splitlines()
    assert len(lines) == 20
    keys = {"mode", "id", "key", "action_in", "action_out", "lesson_id", "status", "kind", "inert_reason",
            "reason_code", "merged_into", "merged_from"}
    assert all(set(json.loads(ln)) == keys for ln in lines)
    assert {json.loads(ln)["mode"] for ln in lines} == {"apply"}
    blob = applied.read("id_map.jsonl") + applied.read("MIGRATION_REPORT.md")
    for les in applied.stored().values():
        assert les["text"] not in blob


def test_report_counts_only(applied):
    rep = applied.read("MIGRATION_REPORT.md")
    assert "- legacy files: 2" in rep and "- classified records: 20" in rep and "- joined: 19" in rep
    assert "- keep_active: in 12, out 11" in rep  # member merged away, dormant lead promoted
    assert "- keep_dormant: in 3, out 2" in rep
    for t in (T_PLAIN, T_LEAD, "nightly export"):
        assert t not in rep


def test_stored_ids_match_id_map(applied):
    mapped = {r["lesson_id"] for r in applied.id_map().values() if r["lesson_id"] and r["status"]}
    assert mapped == set(applied.stored())


def test_keep_active_lands_provisional_legacy_unverified(applied):
    ids = applied.id_map()
    for lid in ("auto-a0000001", "auto-a0000011", "auto-a0000013", "auto-a000000a"):
        assert ids[lid]["status"] == "provisional" and ids[lid]["reason_code"] == "legacy_unverified"
        assert ids[lid]["inert_reason"] is None
    assert ids["auto-a0000002"]["reason_code"] == "legacy_unverified"  # inert too; both recorded
    assert ids["auto-a0000003"]["reason_code"] is None and ids["auto-a0000003"]["status"] == "dormant"
    assert not any(les["status"] == "active" for les in applied.stored().values())
    sec = applied.packet_section("provisional")
    assert "auto-a0000013 | acme-widgets | legacy_unverified" in sec
    assert "auto-a0000002 | acme-widgets | legacy_unverified; inert: code_fence_or_backtick" in sec


def test_report_counts_legacy_unverified_under_provisional(applied):
    rep = applied.read("MIGRATION_REPORT.md")
    assert "- provisional: 7\n  - legacy_unverified: 7\n" in rep
