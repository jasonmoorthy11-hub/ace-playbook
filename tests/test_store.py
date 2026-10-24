import json
import os

import pytest

from ace_playbook.scrub import QuarantineRequired
from ace_playbook.store import DeltaLog, SchemaError, make_lesson_id, rebuild, search, view
from ace_playbook.store import rank
from ace_playbook.store.schema import ORIGINS, USER_ORIGINS

from . import fakes
from .helpers import GLOBAL, PROJECT, add_draft, evidence


@pytest.fixture
def log(tmp_path):
    return DeltaLog(tmp_path / "deltas.jsonl")


def test_add_and_fold(log):
    d = add_draft()
    rec = log.append(d, ts="2026-01-01T00:00:00Z")
    assert rec["delta_id"].startswith("D-") and rec["prev_hash"] == "0" * 64
    st = log.state()
    les = st[d["lesson_id"]]
    assert les["status"] == "provisional" and les["shown"] == 0 and les["scope_key"] == "demo"
    assert log.verify() == 1


def test_hash_chain_detects_tampering(log):
    log.append(add_draft(), ts="2026-01-01T00:00:00Z")
    log.append(add_draft("Second distinct lesson text here"), ts="2026-01-02T00:00:00Z")
    lines = log.path.read_text().splitlines()
    lines[0] = lines[0].replace("python3", "pythonX")
    log.path.write_text("\n".join(lines) + "\n")
    with pytest.raises(SchemaError):
        log.verify()


@pytest.mark.parametrize("mutate,expect", [
    (lambda d: d["fields"].pop("kind"), "missing"),
    (lambda d: d["fields"].update(kind="nonsense"), "kind"),
    (lambda d: d["fields"].update(text="short"), "text length"),
    (lambda d: d["fields"].update(extra=1), "unknown fields"),
    (lambda d: d.update(op="DROP"), "op must"),
    (lambda d: d.update(counters={"shown": 1}), "ADD may not"),
    (lambda d: d["fields"].update(scope={"type": "project"}), "normalized key"),
])
def test_schema_rejects(log, mutate, expect):
    d = add_draft()
    mutate(d)
    with pytest.raises(SchemaError, match=expect):
        log.append(d)
    assert not log.path.exists()


def test_live_status_requires_user_provenance(log):
    d = add_draft(evidence=evidence("tool_output"))
    with pytest.raises(SchemaError, match="user_message or user_file"):
        log.append(d)
    q = add_draft(evidence=evidence("tool_output"), status="quarantined")
    log.append(q)


@pytest.mark.parametrize("origin", ["agent_file", "shell_output"])
def test_agent_and_shell_origins_validate_but_cannot_carry_a_live_lesson(log, origin):
    assert origin in ORIGINS and origin not in USER_ORIGINS
    d = add_draft(evidence=evidence(origin))
    with pytest.raises(SchemaError, match="user_message or user_file"):
        log.append(d)                                                  # provisional is a live status
    d["fields"]["status"] = "quarantined"
    log.append(d)
    assert log.state()[d["lesson_id"]]["evidence"][0]["origin"] == origin
    with pytest.raises(SchemaError, match="user-origin"):
        log.append({"op": "PATCH", "lesson_id": d["lesson_id"], "fields": {"status": "active"}})


def test_patch_to_active_needs_user_evidence(log):
    d = add_draft(evidence=evidence("tool_output"), status="quarantined")
    log.append(d)
    with pytest.raises(SchemaError, match="user-origin"):
        log.append({"op": "PATCH", "lesson_id": d["lesson_id"], "fields": {"status": "active"}})


def test_duplicate_add_and_unknown_target_rejected(log):
    d = add_draft()
    log.append(d)
    with pytest.raises(SchemaError, match="already exists"):
        log.append(d)
    with pytest.raises(SchemaError, match="unknown lesson"):
        log.append({"op": "RETIRE", "lesson_id": "L-00000000", "fields": {"reason": "gone"}})


def test_add_id_must_match_content(log):
    d = add_draft()
    d["lesson_id"] = "L-deadbeef"
    with pytest.raises(SchemaError, match="make_lesson_id"):
        log.append(d)


def test_all_ops_roundtrip(log):
    a = add_draft("Alpha lesson about environment variables", GLOBAL)
    b = add_draft("Alpha lesson about env vars on Vercel", GLOBAL)
    log.append_many([a, b], ts="2026-01-01T00:00:00Z")
    la, lb = a["lesson_id"], b["lesson_id"]
    log.append({"op": "PATCH", "lesson_id": la, "fields": {"pinned": True, "status": "active"}})
    log.append({"op": "NARROW", "lesson_id": la,
                "fields": {"applies_when": "only in vercel deploys", "scope": PROJECT}})
    log.append({"op": "MERGE", "lesson_id": la, "fields": {"merged_from": [lb]}})
    log.append({"op": "NOOP", "lesson_id": la, "counters": {"shown": 2, "cited": 1}})
    st = log.state()
    assert st[la]["pinned"] and st[la]["status"] == "active"
    assert st[la]["scope_type"] == "project" and st[la]["scope_key"] == "demo"
    assert st[lb]["status"] == "superseded" and st[lb]["superseded_by"] == la
    assert st[la]["shown"] == 2 and st[la]["cited"] == 1 and st[la]["merged_from"] == [lb]
    log.append({"op": "RETIRE", "lesson_id": la, "fields": {"reason": "obsolete", "to_status": "compiled"}})
    assert log.state()[la]["status"] == "compiled"
    log.append({"op": "RETIRE", "lesson_id": la, "fields": {"reason": "obsolete"}})
    with pytest.raises(SchemaError, match="cannot be modified"):
        log.append({"op": "PATCH", "lesson_id": la, "fields": {"pinned": False}})
    log.append({"op": "NOOP", "lesson_id": la, "counters": {"shown": 1}})  # counters still allowed
    assert log.verify() == 9


def test_append_many_is_atomic(log):
    good, bad = add_draft(), add_draft("Another good lesson text")
    bad["fields"]["kind"] = "nope"
    with pytest.raises(SchemaError):
        log.append_many([good, bad])
    assert not log.path.exists()


def test_touched_budget(log):
    drafts = [add_draft(f"Distinct lesson number {i} text") for i in range(6)]
    with pytest.raises(SchemaError, match="touches 6"):
        log.append_many(drafts, max_touched=5)
    assert len(log.append_many(drafts[:5], max_touched=5)) == 5


def test_writer_refuses_secret_text(log):
    d = add_draft(f"Deploy with {fakes.anthropic()} token")
    with pytest.raises(QuarantineRequired):
        log.append(d)
    d2 = add_draft(evidence=evidence(quote="my key is " + fakes.github()))
    with pytest.raises(QuarantineRequired):
        log.append(d2)
    assert not log.path.exists()


def test_no_temp_file_left_behind(log):
    log.append(add_draft())
    assert sorted(p.name for p in log.path.parent.iterdir()) == ["deltas.jsonl", "deltas.jsonl.lock"]


def test_sqlite_view_rebuild_is_deterministic_and_searchable(log, tmp_path):
    for i, t in enumerate(["Vercel env vars need redeploy to apply",
                           "Railway CLI fails in non-interactive mode",
                           "Prefer uv over pip for python projects"]):
        log.append(add_draft(t), ts=f"2026-01-0{i + 1}T00:00:00Z")
    db = tmp_path / "view.sqlite"
    state1 = log.rebuild_view(db)
    con = view.open_view(db)
    rows = search(con, "redeploy environment")
    assert [r["text"] for r in rows] == ["Vercel env vars need redeploy to apply"]
    assert view.view_head(db) == log.head_hash()
    con.close()
    db.unlink()
    assert log.rebuild_view(db) == state1


def test_view_is_rebuilt_from_log_not_authoritative(log, tmp_path):
    log.append(add_draft())
    db = tmp_path / "v.sqlite"
    log.rebuild_view(db)
    log.append(add_draft("A later lesson that changes the head"))
    assert view.view_head(db) != log.head_hash()


def test_fts_query_injection_is_inert(log, tmp_path):
    log.append(add_draft())
    db = tmp_path / "v.sqlite"
    log.rebuild_view(db)
    con = view.open_view(db)
    assert search(con, '" OR text:* NOT "') is not None
    assert search(con, "***") == []


def test_decay_affects_rank_only(log):
    d = add_draft()
    log.append(d, ts="2026-01-01T00:00:00Z")
    les = log.state()[d["lesson_id"]]
    now = rank.parse_ts("2026-01-01T00:00:00Z")
    fresh = rank.score(les, now)
    old = rank.score(les, now + 180 * 86400)
    assert old < fresh and abs(old - fresh * 0.25) < 1e-9
    assert log.state()[d["lesson_id"]] == les  # decay never mutates state


def test_wilson_is_conservative():
    assert rank.wilson_lower(0, 0) == 0
    assert rank.wilson_lower(5, 5) < 1.0
    assert rank.wilson_lower(50, 50) > rank.wilson_lower(5, 5)


def test_make_lesson_id_stable_and_scope_sensitive():
    assert make_lesson_id(PROJECT, "x y z") == make_lesson_id(PROJECT, " x y z ")
    assert make_lesson_id(PROJECT, "x y z") != make_lesson_id(GLOBAL, "x y z")


def test_corrupt_line_reports_position(log):
    log.path.write_text('{"ok":1}\nnot json\n')
    with pytest.raises(SchemaError, match="deltas.jsonl:2"):
        log.read()
    assert json.loads('{"a":1}')


# ------------------------------------------------------------ file modes (0600)
def _mode(path):
    return path.stat().st_mode & 0o777


def test_append_creates_deltas_0600_even_under_umask_000(log):
    old = os.umask(0)
    try:
        log.append(add_draft(), ts="2026-01-01T00:00:00Z")
    finally:
        os.umask(old)
    assert _mode(log.path) == 0o600


def test_append_tightens_a_preexisting_0644_log(log):
    log.append(add_draft(), ts="2026-01-01T00:00:00Z")
    os.chmod(log.path, 0o644)
    log.append(add_draft("Second distinct lesson text here"), ts="2026-01-02T00:00:00Z")
    assert _mode(log.path) == 0o600
    assert log.verify() == 2


def test_pin_keeps_deltas_0600(log):
    d = add_draft()
    log.append(d, ts="2026-01-01T00:00:00Z")
    os.chmod(log.path, 0o644)
    old = os.umask(0)
    try:
        log.append({"op": "PATCH", "lesson_id": d["lesson_id"], "fields": {"pinned": True}},
                   ts="2026-01-02T00:00:00Z")
    finally:
        os.umask(old)
    assert _mode(log.path) == 0o600


def test_failed_replace_removes_tmp_and_keeps_old_log(log, monkeypatch):
    log.append(add_draft(), ts="2026-01-01T00:00:00Z")
    before = log.path.read_text()

    def boom(*_a, **_k):
        raise OSError("replace failed")

    with monkeypatch.context() as m:
        m.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            log.append(add_draft("Second distinct lesson text here"), ts="2026-01-02T00:00:00Z")
    assert not log.path.with_name("deltas.jsonl.tmp").exists()
    assert log.path.read_text() == before


def test_tmp_symlink_is_not_followed(log, tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    log.path.with_name("deltas.jsonl.tmp").symlink_to(victim)
    with pytest.raises(OSError):
        log.append(add_draft(), ts="2026-01-01T00:00:00Z")
    assert victim.read_text() == "keep"
    assert not log.path.exists()


def test_rebuilt_view_db_is_0600_under_umask_000(tmp_path):
    old = os.umask(0)
    try:
        rebuild([], tmp_path / "view.sqlite")
    finally:
        os.umask(old)
    assert _mode(tmp_path / "view.sqlite") == 0o600


def test_failed_view_rebuild_leaves_no_tmp_and_the_old_db_untouched(tmp_path, monkeypatch):
    db = tmp_path / "view.sqlite"
    rebuild([], db)
    before = db.read_bytes()
    monkeypatch.setattr(view, "DDL", "THIS IS NOT SQL;")
    with pytest.raises(Exception):
        rebuild([], db)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["view.sqlite"]
    assert db.read_bytes() == before
