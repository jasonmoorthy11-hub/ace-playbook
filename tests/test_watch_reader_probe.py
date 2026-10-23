"""W2 fix wave 5 (D-165): a code-owned health probe, never a feed item, decides "outage"; plus D-166-adjacent pipeline facts and the flagged set.

A failed call is held (nothing counted, nothing marked). After PROBE_AFTER failed calls with no success, or when a run ends with failures and no
success, ONE call is made on the fixed built-in `reader.PROBE`: a parsed envelope means the Reader is healthy (the held tries count, the run goes
on), anything else is an outage (every held try is dropped, the rest of the run is `unavailable`, nothing is marked). The fake exits 1 whenever the
prompt holds $ACE_WATCH_FAKE_POISON (a crafted item the model cannot read). Fake reader only; every store is a scratch dir under tmp_path.
"""

import logging
import subprocess

import pytest

from ace_playbook.compile.watch import model, reader, validate

from .test_compile_watch_cli import U
from .test_watch_reader import NOW, cfg_of, enable, fake, verb, world  # noqa: F401 - fixtures


def res_of(*titles: str) -> dict:
    entries = [{"id": t, "title": t, "link": f"https://example.test/p/{i}", "updated": "", "summary": f"about {t}"} for i, t in enumerate(titles)]
    return {"id": "feed", "url": "https://example.test/feed.xml", "kind": "rss", "tier": 1, "sha256": "d" * 64, "data": entries, "body": ""}


def feed_xml(*titles: str) -> bytes:
    items = "".join(f"<item><title>{t}</title><link>https://example.test/p/{i}</link><guid>g{i}</guid><description>about it</description></item>"
                    for i, t in enumerate(titles))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>{items}</channel></rss>'.encode()


def struck(res: dict, n: int) -> tuple[dict, list[str]]:
    """A `seen` in which the first `n` items of `res` already carry one failed try (a strike), and the keys of all its items."""
    keys = [it["key"] for it in model.items_of([res])]
    return {"reader": {"fail": {k: 1 for k in keys[:n]}}}, keys


def test_the_probe_is_a_fixed_benign_item_and_only_a_parsed_envelope_is_healthy(fake):
    assert reader.PROBE["source"] == "probe" and reader.PROBE["url"] == "about:probe" and "health check" in reader.PROBE["text"]
    for name, want in (("good", True), ("irrelevant", True), ("not_json", True), ("model_pin", True),
                       ("iserror", False), ("exit1", False), ("garbage", False), ("empty", False), ("error", False)):
        fake(name)
        assert reader.probe(cfg_of()) is want, name
    argv = fake.calls()[-1]["argv"]
    assert "Reader health check" in argv[-1] and "carries no news" in argv[-1] and argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv and argv[argv.index("--setting-sources") + 1] == ""


def test_a_probe_with_no_claude_is_not_healthy(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("ACE_WATCH_CLAUDE", str(tmp_path / "no-such-claude"))
    assert reader.probe(cfg_of()) is False


@pytest.mark.parametrize("sizes", [(1, 2, 3), (2, 4, 6), (20, 20, 20)], ids=["W1", "W2", "FLOOD"])
@pytest.mark.parametrize("name", ["exit1", "iserror"])
def test_an_outage_loses_nothing_over_three_scans_the_probe_costs_one_call(fake, tmp_path, name, sizes):
    """W1 (1 new item a scan), W2 (2 a scan), FLOOD (20 queued): every call fails, the probe too; done and fail stay empty, no item is retired."""
    fake(name)
    seen = {}
    for scan, size in enumerate(sizes, 1):
        before = len(fake.calls())
        out, lines = model.stage(cfg_of(), [res_of(*[f"item {j}" for j in range(size)])], tmp_path / "wd", seen, NOW)
        failed = min(size, 3)
        assert out == [] and lines[0] == f"reader: 0 read, 0 rejected, 0 leads, {size} unavailable, {failed} failed, outage"
        assert lines[1] == f"reader: outage (the health probe failed after {failed} failed call(s)); {size} item(s) not read, nothing marked"
        assert seen["reader"]["done"] == {} and seen["reader"]["fail"] == {} and seen["reader"]["last"]["outage"] == 1
        assert len(fake.calls()) - before == failed + 1


def test_prior_strike_items_are_no_evidence_either_way_four_strikes_and_two_fresh_in_an_outage(fake, tmp_path):
    """W3: the streak/strike distinction is gone. 4 items with a strike + 2 fresh ones, every call fails: 3 calls and the probe, nothing retired."""
    fake("exit1")
    res = res_of(*[f"item {i}" for i in range(6)])
    seen, keys = struck(res, 4)
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert out == [] and lines[0].endswith(", outage") and len(fake.calls()) == 4
    assert seen["reader"]["fail"] == {k: 1 for k in keys[:4]} and seen["reader"]["done"] == {}


def test_a_low_volume_run_of_strike_items_in_an_outage_retires_nothing(fake, tmp_path):
    """LOWVOL (flips the wave-4 residual): 2 strike items only, every call fails: the run end makes the probe, the probe fails, no try counts."""
    fake("exit1")
    res = res_of("a", "b")
    seen, keys = struck(res, 2)
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert lines[0] == "reader: 0 read, 0 rejected, 0 leads, 2 unavailable, 2 failed, outage" and len(fake.calls()) == 3
    assert seen["reader"]["fail"] == {k: 1 for k in keys} and seen["reader"]["done"] == {}


def test_three_poisoned_head_items_are_retired_on_their_own_and_the_good_items_behind_them_are_read(fake, tmp_path, monkeypatch, caplog):
    """POISON: the probe is fine, so the three failures are the items' own: scan 1 reads both good items, scan 2 retires the crafted ones."""
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    caplog.set_level(logging.INFO, logger="ace_playbook.watch")
    res, seen = res_of("POISON a", "POISON b", "POISON c", "good one", "good two"), {}
    keys = [it["key"] for it in model.items_of([res])]
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    state = seen["reader"]
    assert len(out) == 2 and lines[0].startswith("reader: 2 read, 0 rejected, 2 leads, 3 failed") and "outage" not in lines[0] and len(lines) == 1
    assert "Reader health check" in fake.calls()[3]["argv"][-1] and len(fake.calls()) == 6
    assert "reader: health probe ok after 3 failed call(s)" in caplog.messages
    assert state["fail"] == {k: 1 for k in keys[:3]} and sorted(state["done"]) == sorted(keys[3:])
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert out == [] and lines[0] == "reader: 0 read, 0 rejected, 0 leads, 3 failed" and len(fake.calls()) == 10
    assert state["fail"] == {} and sorted(state["done"]) == sorted(keys)
    assert model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)[1][0] == "reader: 0 read, 0 rejected, 0 leads" and len(fake.calls()) == 10


def test_a_short_run_of_bad_items_ends_with_a_probe_and_counts_their_tries(fake, tmp_path, monkeypatch):
    """Two crafted items and nothing else: fewer than three failures, the run end makes the probe, it is healthy, so each item has one strike."""
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    res, seen = res_of("POISON a", "POISON b"), {}
    keys = [it["key"] for it in model.items_of([res])]
    model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert seen["reader"]["fail"] == {k: 1 for k in keys} and seen["reader"]["done"] == {} and len(fake.calls()) == 3
    model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert seen["reader"]["fail"] == {} and sorted(seen["reader"]["done"]) == sorted(keys) and len(fake.calls()) == 6


def test_the_probe_is_made_at_most_once_per_run_later_failures_count_at_once(fake, tmp_path, monkeypatch):
    """Six crafted items: 3 failed calls, ONE probe (healthy), then 3 more failed calls that count at once and need no second probe: 7 calls."""
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    res, seen = res_of(*[f"POISON {i}" for i in range(6)]), {}
    model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert len(fake.calls()) == 7 and sum(1 for c in fake.calls() if "Reader health check" in c["argv"][-1]) == 1
    assert sorted(seen["reader"]["fail"].values()) == [1] * 6


def test_three_new_crafted_items_before_every_scan_never_stop_the_good_item_being_read(fake, tmp_path, monkeypatch):
    """STREAM: the denial of the old breaker (a fresh crafted trio ahead of the good item on every scan) is gone: the good item is read each scan."""
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    seen = {}
    for scan in range(3):
        res = res_of(f"POISON a{scan}", f"POISON b{scan}", f"POISON c{scan}", f"good {scan}")
        out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
        assert len(out) == 1 and lines[0].startswith("reader: 1 read, 0 rejected, 1 leads, 3 failed") and "outage" not in lines[0]
    assert len(fake.calls()) == 15


def test_an_unavailable_claude_after_failed_calls_drops_the_held_tries_and_stops_at_once(fake, tmp_path, monkeypatch):
    """Design minor 3: two failed calls, then a failed spawn: nothing is counted or marked, the rest is `unavailable`, no spawn is tried again."""
    real, spawns = subprocess.Popen, []

    def popen(*a, **k):
        spawns.append(1)
        if len(spawns) > 2:
            raise OSError("no spawn")
        return real(*a, **k)

    fake("exit1")
    monkeypatch.setattr(subprocess, "Popen", popen)
    items = model.items_of([res_of("a", "b", "c", "d")])
    s, done, fail = model.new_stats(), {}, {}
    assert model.pipeline(cfg_of(), items, NOW, s, done, fail) == []
    assert (s["failed"], s["unavailable"], s["outage"]) == (2, 2, 0) and done == {} and fail == {} and len(spawns) == 3


def test_the_probe_item_is_never_stored_composed_or_counted_read(world, fake, capsys, monkeypatch):
    """One crafted entry in the feed, a Reader that answers `good`: the probe is answered `good` too, yet no lead, no read, no key for it exists."""
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    world.pages[U["feed"]] = (feed_xml("POISON quiet"), "application/rss+xml")
    enable(world)
    code, out, err = world.run(capsys, "scan")
    assert code == 0 and "reader: 0 read, 0 rejected, 0 leads, 1 failed" in out and "outage" not in out and len(fake.calls()) == 2
    assert all(p["title"] != "Fake reader lead" for p, _ in world.proposals())
    s = model.new_stats()
    model.pipeline(cfg_of(), model.items_of([res_of("POISON a")]), NOW, s, {}, {})
    assert (s["read"], s["rejected"], s["leads"], s["probe"]) == (0, 0, 0, "ok")


def test_the_old_breaker_list_in_seen_json_is_deleted_on_load(fake, tmp_path):
    seen = {"reader": {"breaker": ["a", "b"]}}
    model.stage(cfg_of(), [res_of("x")], tmp_path / "wd", seen, NOW)
    assert "breaker" not in seen["reader"] and "breaker" not in seen["reader"]["last"]


def test_the_entries_the_rules_can_flag_are_never_read_even_past_the_w1_cap_of_ten(world, fake, capsys):
    """design minor 2: 12 keyword entries, W1 keeps 10 as leads; the Reader reads NONE of the 12 (0 calls), so no entry ever has two leads."""
    fake("good_idx1")
    world.pages[U["feed"]] = (feed_xml(*[f"subagent post {i}" for i in range(12)]), "application/rss+xml")
    enable(world)
    code, out, err = world.run(capsys, "scan")
    leads = [p for p, _ in world.proposals() if p["class"] == "lead"]
    assert code == 0 and fake.calls() == [] and len(leads) == 10 and len({p["evidence"][0]["quote"] for p in leads}) == 10
    assert "reader: 0 read, 0 rejected, 0 leads, 12 dup" in out


def test_a_model_title_loses_lone_surrogates_and_noncharacters(fake):
    """canary m4: Cs and the noncharacters (U+FFFE, U+FFFF, U+FDD0-U+FDEF, the plane-end pairs) are dropped, never joined into a space."""
    item = {"source": "feed", "url": "u", "sha256": "a" * 64, "text": "A post\nabout it"}
    out = {"relevant": True, "class": "lead", "title": "A\ud800B\ufffeC\uffffD\ufdd0E\ufdefF\U0001fffeG\U0010ffffH", "quote_line_index": 0}
    assert validate.validate(out, item) == {"class": "lead", "title": "ABCDEFGH", "quote": "A post"}


def test_the_canary_verb_exits_2_unless_every_planted_item_was_read_and_exits_0_only_when_none_obeyed(fake, tmp_path, monkeypatch, capsys):
    """D-166: one failed call among four leaves 3 of 4 items read: the check proved a part, so exit 2 (all irrelevant and all read is exit 0)."""
    monkeypatch.setenv("ACE_WATCH_FAKE_SEQ", "iserror,irrelevant,irrelevant,irrelevant")
    assert verb(tmp_path, monkeypatch, capsys) == (2, "canary: 3 of 4 items read\ncanary: 0 proposals\n", "canary: 1 failed\n")
    monkeypatch.delenv("ACE_WATCH_FAKE_SEQ")
    fake("irrelevant")
    assert verb(tmp_path, monkeypatch, capsys) == (0, "canary: 0 proposals\n", "")
