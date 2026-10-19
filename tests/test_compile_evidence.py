"""Evidence cards (compile/evidence.py, docs/design/compiler.md 5): admission, legacy path, scrub, ids."""

import hashlib
import json
import os
import stat

import pytest

from ace_playbook.compile import evidence as evm
from ace_playbook.reflect import provenance
from ace_playbook.store import SchemaError

from . import compile_support as cs
from . import fakes


def build(w, **kw):
    return evm.build_cards(w.cfg, w.lesson_id, **kw)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def world(tmp_path, monkeypatch):
    return cs.make_world(tmp_path, monkeypatch)


# ------------------------------------------------------------------ standard path
def test_standard_card_from_a_user_quote_and_a_mapped_fire_in_the_originating_session(world):
    cards, refused = build(world)
    assert refused == [] and len(cards) == 1
    c = cards[0]
    assert (c["provenance"], c["incident_kind"], c["detector"], c["tier"]) == ("standard", "originating", "rm_rf_nonscratch", "offline")
    assert c["origin_session"] == c["incident_session"] == evm.sid(world.sid("origin"))
    assert c["lesson_id"] == world.lesson_id and c["label_ref"] is None and c["self_report"] is True
    assert c["payload_ref"] == {"tool_name": "Bash", "tool_input": {"command": cs.RM_CMD}}
    assert c["replayable"] is True and c["scrub"] == {"profile": "LESSON", "hits": 0}
    assert c["id"] == world.ref("origin")


def test_card_stores_offsets_into_the_user_turn_never_the_quote_text(world):
    (c,), _ = build(world)
    quote = world.fx["lesson"]["quote"]
    (off,) = c["quote_offsets"]
    assert (off["user_turn"], off["start"], off["end"]) == (0, 0, len(provenance.norm(quote)))
    assert off["quote_sha256"] == hashlib.sha256(provenance.norm(quote).encode()).hexdigest()[:16]
    blob = (world.cfg.data_dir / "cards" / f"{c['id']}.json").read_text()
    assert quote not in blob and provenance.norm(quote) not in blob.lower()
    assert "never run" not in blob


def test_session_ids_appear_only_as_sid_hashes(world):
    (c,), _ = build(world)
    blob = json.dumps(c)
    assert world.sid("origin") not in blob
    assert c["incident_session"] == "sid:" + hashlib.sha256(world.sid("origin").encode()).hexdigest()[:16]


def test_quote_found_only_in_tool_output_is_refused(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch, origin="origin_tool_only", sessions=(), shown=())
    cards, refused = build(w)
    assert cards == [] and [r["reason"] for r in refused] == ["quote_not_in_user_message"]


def test_citations_and_exposure_alone_never_admit_a_card(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch, origin="origin_quiet", sessions=("later_clean",), shown=("later_clean",))
    cs.append_jsonl(w.cfg.citations, {"session_id": w.sid("later_clean"), "cited_ids": [w.lesson_id], "unknown_ids": []})
    cards, refused = build(w)
    assert cards == [] and [r["reason"] for r in refused] == ["no_incident"]


def test_exposure_and_agent_citation_are_recorded_but_flagged_self_report(world):
    cs.append_jsonl(world.cfg.injections, {"session_id": world.sid("origin"), "shown_ids": [world.lesson_id], "holdout_ids": []})
    cs.append_jsonl(world.cfg.citations, {"session_id": world.sid("origin"), "cited_ids": [world.lesson_id], "unknown_ids": []})
    (c,), _ = build(world)
    assert (c["lesson_shown_in_incident_session"], c["agent_cited"], c["self_report"]) == (True, True, True)


def test_a_held_out_session_is_eligible_but_not_shown(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch, origin="origin_quiet", sessions=("later",), shown=())
    cs.append_jsonl(w.cfg.injections, {"session_id": w.sid("later"), "shown_ids": [], "holdout_ids": [w.lesson_id]})
    (c,), _ = build(w)
    assert c["incident_kind"] == "later" and c["lesson_shown_in_incident_session"] is False


def test_later_incident_when_the_originating_session_has_no_fire(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch, origin="origin_quiet")
    (c,), _ = build(w)
    assert (c["incident_kind"], c["provenance"]) == ("later", "standard")
    assert c["origin_session"] == evm.sid(w.sid("origin_quiet")) and c["incident_session"] == evm.sid(w.sid("later"))
    assert c["lesson_shown_in_incident_session"] is True and c["quote_offsets"]


def test_a_later_session_where_the_lesson_was_not_eligible_is_ignored(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch, origin="origin_quiet", shown=())
    cards, refused = build(w)
    assert cards == [] and refused[0]["reason"] == "no_incident"


@pytest.mark.parametrize("status", ["dormant", "retired"])
def test_a_lesson_that_is_not_live_gets_no_card(tmp_path, monkeypatch, status):
    w = cs.make_world(tmp_path, monkeypatch, status=status)
    assert build(w) == ([], [{"lesson_id": w.lesson_id, "reason": "lesson_not_live"}])


def test_unknown_lesson_and_missing_transcript_are_refused_by_name(world, tmp_path):
    assert build(world)[0]
    assert evm.build_cards(world.cfg, "L-00000000", write=False)[1][0]["reason"] == "lesson_unknown"
    next(world.cfg.transcripts_root.rglob(world.sid("origin") + ".jsonl")).unlink()
    assert build(world, write=False) == ([], [{"lesson_id": world.lesson_id, "reason": "transcript_missing"}])


def test_a_broken_delta_chain_fails_closed(world):
    lines = world.cfg.deltas.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["fields"]["text"] = "Tampered lesson text goes here"
    world.cfg.deltas.write_text(json.dumps(rec) + "\n")
    with pytest.raises(SchemaError):
        build(world)


def test_quote_normalisation_matches_the_reflector_provenance_gate():
    for s in ("  A  b\nC ", "ÄB  cd", "x"):
        assert evm.norm(s) == provenance.norm(s)


# ------------------------------------------------------------------ legacy path
@pytest.fixture
def legacy(tmp_path, monkeypatch):
    return cs.make_world(tmp_path, monkeypatch, legacy=True)


def test_legacy_lesson_without_a_human_label_gets_no_card_and_reports_the_ref(legacy):
    cards, refused = build(legacy)
    assert cards == []
    assert refused == [{"lesson_id": legacy.lesson_id, "reason": "legacy_unlabelled", "ref": legacy.ref("later")}]


def test_legacy_card_admitted_only_via_a_labelled_incident_in_a_later_eligible_session(legacy):
    cs.write_labels(legacy.cfg, [{"ref": legacy.ref("later"), "label": "incident"}])
    (c,), refused = build(legacy)
    assert refused == []
    assert (c["provenance"], c["incident_kind"], c["origin_session"], c["quote_offsets"]) == ("legacy", "later", None, [])
    assert c["incident_session"] == evm.sid(legacy.sid("later")) and c["id"] == legacy.ref("later")
    row = json.loads(legacy.cfg.path("labels.jsonl").read_text().splitlines()[0])
    assert c["label_ref"] == "lab:" + row["hash"][:8]
    assert evm.verify_card(c) is None


@pytest.mark.parametrize("bad", ["tp", "fp"])
def test_only_an_incident_label_counts_for_the_legacy_path(legacy, bad):
    cs.write_labels(legacy.cfg, [{"ref": legacy.ref("later"), "label": bad}])
    assert build(legacy)[0] == []


def test_legacy_label_for_another_ref_or_with_a_broken_chain_is_refused(legacy):
    cs.write_labels(legacy.cfg, [{"ref": "E-00000000", "label": "incident"}, {"ref": legacy.ref("later"), "label": "incident"}])
    assert [c["id"] for c in build(legacy, write=False)[0]] == [legacy.ref("later")]
    path = legacy.cfg.path("labels.jsonl")
    first, second = path.read_text().splitlines()
    path.write_text(first.replace("E-00000000", "E-00000001") + "\n" + second + "\n")      # edited row: hash no longer matches
    assert build(legacy, write=False)[0] == []


def test_labelled_incident_in_a_session_where_the_lesson_was_not_eligible_is_ignored(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch, legacy=True, shown=())
    cs.write_labels(w.cfg, [{"ref": w.ref("later"), "label": "incident"}])
    assert build(w) == ([], [{"lesson_id": w.lesson_id, "reason": "no_eligible_session"}])


def test_legacy_self_quote_in_a_user_message_never_makes_a_standard_card(legacy):
    legacy.add_session("echo", [{"human": legacy.fx["lesson"]["text"]}, {"tool": "Bash", "input": {"command": cs.RM_CMD}}])
    assert build(legacy)[0] == []


# ------------------------------------------------------------------ scrub, ids, files
def test_scrub_hit_masks_the_payload_and_sets_replayable_false(tmp_path, monkeypatch):
    w = cs.make_world(tmp_path, monkeypatch, origin=None, sessions=("later",), legacy=True)
    token = fakes.anthropic()
    w.add_session("later", [{"human": "go"}, {"tool": "Bash", "input": {"command": f"{cs.RM_CMD} && echo {token}"}}])
    ref = w.ref("later", f"{cs.RM_CMD} && echo {token}")
    cs.write_labels(w.cfg, [{"ref": ref, "label": "incident"}])
    (c,), _ = build(w)
    assert c["replayable"] is False and c["scrub"]["hits"] >= 1 and c["id"] == ref
    blob = (w.cfg.data_dir / "cards" / f"{c['id']}.json").read_text()
    assert token not in blob and "[HIT:" in blob
    assert evm.verify_card(c) is None


def test_clean_payload_hash_matches_the_stored_payload(world):
    (c,), _ = build(world)
    assert c["payload_sha256"] == evm.payload_hash(c["payload_ref"]) and c["replayable"]


def test_card_id_is_sha256_8_of_lesson_incident_session_and_payload_hash(world):
    (c,), _ = build(world)
    want = "E-" + hashlib.sha256(f"{c['lesson_id']}|{c['incident_session']}|{c['payload_sha256']}".encode()).hexdigest()[:8]
    assert c["id"] == want


def test_rebuild_is_idempotent_ids_bytes_and_created(world):
    first, _ = build(world, now="2026-10-20T03:10:00Z")
    path = world.cfg.data_dir / "cards" / f"{first[0]['id']}.json"
    before = path.read_bytes()
    second, _ = build(world, now="2027-01-01T00:00:00Z")
    assert [c["id"] for c in second] == [c["id"] for c in first] and path.read_bytes() == before
    assert second[0]["created"] == "2026-10-20T03:10:00Z"
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_card_files_are_0600_in_a_0700_cards_dir(world):
    (c,), _ = build(world)
    d = world.cfg.data_dir / "cards"
    assert mode(d / f"{c['id']}.json") == 0o600 and mode(d) == 0o700


def test_write_false_touches_nothing(world):
    before = sorted(p.name for p in world.cfg.data_dir.iterdir())
    assert build(world, write=False)[0]
    assert sorted(p.name for p in world.cfg.data_dir.iterdir()) == before


@pytest.mark.parametrize("bad", ["../../etc/passwd", "L-../x", "L-0000000g", "", "L-b3d1fba9/../..", "l-b3d1fba9", "L-b3d1fba9\n"])
def test_crafted_lesson_ids_are_refused_before_any_path_is_built(world, bad):
    with pytest.raises(ValueError):
        evm.build_cards(world.cfg, bad)
    assert not (world.cfg.data_dir / "cards").exists()


def test_a_symlinked_cards_dir_is_refused(world, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    (world.cfg.data_dir / "cards").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        build(world)
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_card_path_rejects_ids_that_are_not_card_ids(world):
    for bad in ("E-../x", "E-123", "../E-12345678", "E-1234567G"):
        with pytest.raises(ValueError):
            evm.card_path(world.cfg, bad)


def test_verify_card_detects_tampering(world):
    (c,), _ = build(world)
    assert evm.verify_card(c) is None
    assert evm.verify_card({**c, "lesson_id": "L-00000000"}) is not None            # id no longer matches
    assert evm.verify_card({**c, "payload_ref": {"tool_name": "Bash", "tool_input": {"command": "ls"}}}) is not None
    assert evm.verify_card({**c, "provenance": "legacy"}) is not None
    assert evm.verify_card({**c, "extra": 1}) is not None
    assert evm.verify_card({**c, "self_report": False}) is not None


def test_private_dir_tightens_a_wider_directory_of_ours_and_never_chmods_a_symlink(tmp_path):
    d = tmp_path / "wide"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    evm.private_dir(d)
    assert mode(d) == 0o700
    target = tmp_path / "target"
    target.mkdir()
    os.chmod(target, 0o755)
    (tmp_path / "link").symlink_to(target)
    evm.private_dir(tmp_path / "link")
    assert mode(target) == 0o755                                    # left alone: the link is not ours to tighten
    evm.private_dir(tmp_path / "new" / "deep")
    assert mode(tmp_path / "new" / "deep") == 0o700
