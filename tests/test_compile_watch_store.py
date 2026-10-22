"""`compile/watch/store.py` and `summary.py`: the private proposal store, the decisions log, the cap, the kill file, the status fields."""

import json
import os
import stat

import pytest

from ace_playbook import config as cfgmod
from ace_playbook.compile.watch import store, summary

NOW = "2026-10-21T12:00:00Z"
LATER = "2026-12-31T00:00:00Z"


def rec(n: int, cls: str = "model_pin", *, created: str = "2026-10-20T00:00:00Z", title: str | None = None, expires: str = "2026-11-19T00:00:00Z"):
    return {"id": f"W-{n:08x}", "created": created, "class": cls, "elevated": False, "title": title or f"title {n}",
            "evidence": [{"url": "https://example.test/x", "sha256": "0" * 64, "quote": "q", "tier": 0}], "inventory_hits": [],
            "change": {"target": "t", "diff": None, "diff_sha256": None}, "verify": "true", "expires": expires, "status": "open"}


@pytest.fixture
def wd(tmp_path):
    return store.ensure(tmp_path / "watch")


def test_config_keys_default_to_none_and_expand_and_unknown_keys_still_fail(tmp_path):
    c = cfgmod.from_dict({})
    assert (c.watch_dir, c.watch_sources, c.watch_inventory_map) == (None, None, None)
    c = cfgmod.from_dict({"watch_dir": "~/w", "watch_sources": "~/s.json", "watch_inventory_map": "~/m.json"})
    assert c.watch_dir == cfgmod.Path(os.path.expanduser("~/w")) and c.watch_sources.name == "s.json" and c.watch_inventory_map.name == "m.json"
    with pytest.raises(ValueError, match="unknown config keys"):
        cfgmod.from_dict({"watch_dirr": "x"})


def test_watch_dir_defaults_under_the_data_dir(tmp_path):
    assert store.watch_dir(cfgmod.Config(data_dir=tmp_path)) == tmp_path / "watch"
    assert store.watch_dir(cfgmod.Config(data_dir=tmp_path, watch_dir=tmp_path / "elsewhere")) == tmp_path / "elsewhere"


def test_ensure_makes_private_directories(tmp_path):
    wd = store.ensure(tmp_path / "w")
    assert stat.S_IMODE(wd.stat().st_mode) == 0o700 and stat.S_IMODE((wd / "proposals").stat().st_mode) == 0o700


def test_a_proposal_is_written_once_0600_and_never_replaced(wd):
    assert store.write_proposal(wd, rec(1)) is True
    path = wd / "proposals" / "W-00000001.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.write_proposal(wd, {**rec(1), "title": "changed"}) is False
    assert json.loads(path.read_text())["title"] == "title 1"
    with pytest.raises(ValueError):
        store.write_proposal(wd, {**rec(2), "id": "../../etc/x"})
    assert sorted(p.name for p in wd.iterdir()) == ["proposals"]


def test_decisions_append_0600_and_reject_unknown_values(wd):
    store.append_decision(wd, "W-00000001", "later", "", now=NOW)
    store.append_decision(wd, "W-00000001", "dismissed", "not mine\x1b[31m", now=NOW)
    path = wd / "decisions.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rows = store.read_decisions(wd)
    assert [r["decision"] for r in rows] == ["later", "dismissed"] and rows[1]["note"] == "not mine[31m"
    assert set(rows[0]) == {"ts", "id", "decision", "note"}
    with pytest.raises(ValueError):
        store.append_decision(wd, "W-00000001", "applied", "", now=NOW)
    with pytest.raises(ValueError):
        store.append_decision(wd, "nope", "later", "", now=NOW)


def test_a_cut_last_line_does_not_swallow_the_next_row(wd):
    (wd / "decisions.jsonl").write_text('{"ts": "2026-10-21T12:00:00Z", "id": "W-0000')
    store.append_decision(wd, "W-00000002", "later", "", now=NOW)
    assert [r["id"] for r in store.read_decisions(wd)] == ["W-00000002"]


def test_status_is_the_last_decision_else_the_file_and_expiry_applies(wd):
    p = rec(1)
    assert store.status_of(p, [], NOW) == "open"
    assert store.status_of(p, [], LATER) == "expired"
    d = lambda x: [{"id": p["id"], "decision": x}]  # noqa: E731
    assert [store.status_of(p, d(x), NOW) for x in ("accepted", "dismissed", "closed", "later", "promoted")] == [
        "accepted", "dismissed", "auto-closed", "open", "open"]
    assert store.status_of(p, d("dismissed"), LATER) == "dismissed"
    assert store.status_of({**p, "status": "queued"}, [], NOW) == "queued"
    assert store.status_of(p, [{"id": "W-ffffffff", "decision": "dismissed"}], NOW) == "open"


def test_rebalance_promotes_queued_in_ranking_order_when_a_slot_frees(wd):
    for n in range(1, 6):
        store.write_proposal(wd, rec(n, "docs"))
    store.write_proposal(wd, {**rec(6, "docs"), "status": "queued", "created": "2026-10-19T00:00:00Z"})
    store.write_proposal(wd, {**rec(7, "model_pin"), "status": "queued", "created": "2026-10-20T01:00:00Z"})
    store.write_proposal(wd, {**rec(8, "version"), "status": "queued", "created": "2026-10-20T00:00:00Z"})
    assert store.rebalance(wd, NOW) == []
    store.append_decision(wd, "W-00000001", "dismissed", "n", now=NOW)
    assert store.rebalance(wd, NOW) == ["W-00000007"]                      # model_pin outranks version and docs
    store.append_decision(wd, "W-00000002", "accepted", "", now=NOW)
    store.append_decision(wd, "W-00000003", "accepted", "", now=NOW)
    assert store.rebalance(wd, NOW) == ["W-00000008", "W-00000006"]       # version, then docs
    assert store.rebalance(wd, NOW) == []


def test_leads_never_count_against_the_cap(wd):
    for n in range(1, 4):
        store.write_proposal(wd, rec(n, "lead"))
    store.write_proposal(wd, {**rec(9, "docs"), "status": "queued"})
    assert store.rebalance(wd, NOW) == ["W-00000009"]


def test_seen_round_trips_0600_and_tolerates_garbage(wd):
    assert store.read_seen(wd) == {}
    store.save_seen(wd, {"last_scan": NOW})
    assert store.read_seen(wd) == {"last_scan": NOW} and stat.S_IMODE((wd / "seen.json").stat().st_mode) == 0o600
    (wd / "seen.json").write_text("[1]")
    assert store.read_seen(wd) == {}
    (wd / "seen.json").write_text("{nope")
    assert store.read_seen(wd) == {}


def test_the_kill_file_is_seen(wd):
    assert store.disabled(wd) is False
    (wd / "DISABLED").write_text("")
    assert store.disabled(wd) is True


def test_unreadable_proposal_files_are_skipped(wd):
    store.write_proposal(wd, rec(1))
    (wd / "proposals" / "W-00000002.json").write_text("{nope")
    (wd / "proposals" / "W-00000003.json").write_text(json.dumps(rec(9)))        # the id inside does not match the file name
    (wd / "proposals" / "notes.txt").write_text("x")
    assert [p["id"] for p in store.load_proposals(wd)] == ["W-00000001"]


# ------------------------------------------------------------------ summary
def test_fields_of_a_missing_watch_dir_are_all_zero(tmp_path):
    cfg = cfgmod.Config(data_dir=tmp_path)
    assert summary.fields(cfg, NOW) == {"watch_open": 0, "watch_leads": 0, "watch_oldest": None, "watch_top": None, "watch_titles": [],
                                        "watch_disabled": False, "watch_last_scan": None, "watch_corrupt": 0}
    assert not (tmp_path / "watch").exists()                              # a pure read creates nothing


def test_fields_count_open_non_leads_leads_oldest_top_and_last_scan(tmp_path, wd):
    cfg = cfgmod.Config(data_dir=tmp_path, watch_dir=wd)
    store.write_proposal(wd, rec(1, "docs", created="2026-10-10T00:00:00Z", title="docs one"))
    store.write_proposal(wd, rec(2, "model_pin", created="2026-10-15T00:00:00Z", title="pin\x1b[2Jtwo"))
    store.write_proposal(wd, rec(3, "lead", created="2026-10-01T00:00:00Z"))
    store.write_proposal(wd, rec(4, "docs", created="2026-10-11T00:00:00Z", title="x" * 300))
    store.write_proposal(wd, {**rec(5), "status": "queued"})
    store.write_proposal(wd, rec(6, "docs"))
    store.append_decision(wd, "W-00000006", "dismissed", "n", now=NOW)
    store.save_seen(wd, {"last_scan": "2026-10-21T06:00:00Z"})
    f = summary.fields(cfg, NOW)
    assert (f["watch_open"], f["watch_leads"], f["watch_oldest"]) == (3, 1, "2026-10-10T00:00:00Z")
    assert f["watch_top"] == "pin[2Jtwo" and f["watch_titles"][:2] == ["pin[2Jtwo", "docs one"] and len(f["watch_titles"]) == 3
    assert all(len(t) <= 120 for t in f["watch_titles"]) and f["watch_last_scan"] == "2026-10-21T06:00:00Z"
    assert f["watch_disabled"] is False
    (wd / "DISABLED").write_text("")
    assert summary.fields(cfg, NOW)["watch_disabled"] is True


def test_the_block_format(tmp_path):
    age = lambda ts, now: "1h" if ts else "never"  # noqa: E731
    base = {"watch_open": 2, "watch_leads": 1, "watch_oldest": "x", "watch_last_scan": "y", "watch_titles": ["a", "b"], "watch_disabled": False}
    assert summary.block(base, NOW, age) == ["open 2  leads 1  oldest 1h  last scan 1h", "- a", "- b"]
    assert summary.block({**base, "watch_open": 0, "watch_leads": 0, "watch_titles": []}, NOW, age) == ["none"]
    assert summary.block({**base, "watch_disabled": True}, NOW, age) == ["disabled"]
    assert summary.block({}, NOW, age) == ["none"]
    assert summary.block({**base, "watch_open": "x"}, NOW, age)[0].startswith("open 0  ")


# ------------------------------------------------------------------ wave 4: corrupt stores never raise, ensure() is careful
def test_deep_json_in_a_proposal_a_decision_row_or_the_seen_file_is_skipped_not_raised(tmp_path, wd):
    store.write_proposal(wd, rec(1))
    (wd / "proposals" / "W-0000dead.json").write_text("[" * 200000)
    (wd / "decisions.jsonl").write_text("[" * 200000 + "\n" + json.dumps({"ts": NOW, "id": "W-00000001", "decision": "later", "note": ""}) + "\n")
    (wd / "seen.json").write_text('{"a":' * 200000)
    assert store.read_seen(wd) == {}
    assert [p["id"] for p in store.load_proposals(wd)] == ["W-00000001"]
    assert [d["decision"] for d in store.read_decisions(wd)] == ["later"]
    f = summary.fields(cfgmod.Config(data_dir=tmp_path, watch_dir=wd), NOW)
    assert (f["watch_open"], f["watch_corrupt"], f["watch_last_scan"]) == (1, 1, None)


@pytest.mark.parametrize("bad", [
    {"id": "W-0000cafe", "created": [1], "class": {"a": 1}, "expires": 5, "status": "open", "title": ["x"]},
    {"id": "W-0000cafe", "created": NOW, "class": ["x"], "title": "t", "evidence": [], "change": {}},
    {"id": "W-0000cafe", "created": NOW, "class": "docs", "title": 7, "evidence": [{}], "change": {"target": "t"}},
    {"id": "W-0000cafe", "created": NOW, "class": "docs", "title": "t", "evidence": "x", "change": {"target": "t"}},
    {"id": "W-0000cafe", "created": NOW, "class": "docs", "title": "t", "evidence": [{"quote": "q"}], "change": []},
    {"id": "W-0000cafe", "created": None, "class": "docs", "title": "t"}, [1, 2], "text", 5])
def test_a_proposal_of_the_wrong_shape_is_skipped_and_counted_and_fields_never_raise(tmp_path, wd, bad):
    store.write_proposal(wd, rec(1))
    (wd / "proposals" / "W-0000cafe.json").write_text(json.dumps(bad))
    (wd / "decisions.jsonl").write_text(json.dumps({"id": "W-0000cafe", "decision": "reopened", "note": None}) + "\n"
                                        + json.dumps({"id": "W-00000001", "decision": ["x"], "note": {}}) + "\n"
                                        + json.dumps({"id": ["W-00000001"], "decision": "later"}) + "\n")
    assert [p["id"] for p in store.load_proposals(wd)] == ["W-00000001"]
    assert store.read_decisions(wd) == [{"id": "W-0000cafe", "decision": "reopened", "note": None}]
    f = summary.fields(cfgmod.Config(data_dir=tmp_path, watch_dir=wd), NOW)
    assert (f["watch_open"], f["watch_corrupt"]) == (1, 1)
    assert summary.block(f, NOW, lambda ts, now: "1h")[0].endswith("corrupt 1")


def test_a_symlinked_proposal_file_and_a_proposals_dir_that_is_a_file_count_as_corrupt(tmp_path, wd):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps(rec(2)))
    (wd / "proposals" / "W-00000002.json").symlink_to(target)
    assert store.load_proposals(wd) == [] and store.scan_proposals(wd)[1] == 1


def test_fields_fall_back_to_zeros_when_the_store_raises_anything(tmp_path, wd, monkeypatch):
    def boom(*a, **k):
        raise TypeError("unhashable")

    monkeypatch.setattr(store, "scan_proposals", boom)
    f = summary.fields(cfgmod.Config(data_dir=tmp_path, watch_dir=wd), NOW)
    assert (f["watch_open"], f["watch_leads"], f["watch_titles"], f["watch_corrupt"]) == (0, 0, [], 0)
    monkeypatch.undo()
    monkeypatch.setattr(store, "read_decisions", lambda wd: 1 / 0)
    assert summary.fields(cfgmod.Config(data_dir=tmp_path, watch_dir=wd), NOW)["watch_open"] == 0


def test_status_of_and_rank_key_tolerate_odd_types():
    odd = {"id": "W-00000001", "class": {"a": 1}, "created": [1]}
    assert store.rank_key(odd)[0] == 9
    assert store.status_of({"id": "W-00000001", "expires": 5, "status": ["x"]}, [], NOW) == "open"


def test_ensure_leaves_an_existing_directory_mode_alone_and_creates_a_missing_proposals_dir_0700(tmp_path):
    wd = tmp_path / "w"
    wd.mkdir(mode=0o755)
    wd.chmod(0o755)
    store.ensure(wd)
    assert stat.S_IMODE(wd.stat().st_mode) == 0o755 and stat.S_IMODE((wd / "proposals").stat().st_mode) == 0o700


def test_ensure_refuses_a_symlinked_proposals_dir(tmp_path):
    wd = tmp_path / "w"
    wd.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (wd / "proposals").symlink_to(other)
    with pytest.raises(store.UnsafeDir) as exc:
        store.ensure(wd)
    assert str(tmp_path) not in str(exc.value) and list(other.iterdir()) == []
    (wd / "proposals").unlink()
    (wd / "proposals").write_text("a file")
    with pytest.raises(store.UnsafeDir):
        store.ensure(wd)


# ------------------------------------------------------------------ wave 5: odd store entries count as corrupt, a symlinked watch_dir is refused
def test_a_proposals_path_that_is_a_file_and_a_decisions_log_that_is_a_directory_count_as_corrupt(tmp_path):
    wd = tmp_path / "w"
    wd.mkdir()
    (wd / "proposals").write_text("a file")
    cfg = cfgmod.Config(data_dir=tmp_path, watch_dir=wd)
    f = summary.fields(cfg, NOW)
    assert f["watch_corrupt"] == 1 and summary.block(f, NOW, lambda ts, now: "1h") == ["none  corrupt 1"]
    (wd / "proposals").unlink()
    (wd / "proposals").mkdir()
    (wd / "decisions.jsonl").mkdir()
    assert summary.fields(cfg, NOW)["watch_corrupt"] == 1
    (wd / "decisions.jsonl").rmdir()
    (wd / "decisions.jsonl").symlink_to(tmp_path / "nowhere")
    assert summary.fields(cfg, NOW)["watch_corrupt"] == 1
    (wd / "decisions.jsonl").unlink()
    assert summary.fields(cfg, NOW)["watch_corrupt"] == 0


def test_ensure_refuses_a_watch_dir_that_is_a_symlink_without_naming_a_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(store.UnsafeDir) as exc:
        store.ensure(link)
    assert str(tmp_path) not in str(exc.value) and list(real.iterdir()) == []
    link.unlink()
    link.symlink_to(tmp_path / "dangling")
    with pytest.raises(store.UnsafeDir):
        store.ensure(link)
    assert not (tmp_path / "dangling").exists()
