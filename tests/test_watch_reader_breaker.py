"""W2 fix wave 3 (D-164): the outage breaker rotates the items that tripped it to the back of the next run and is reported apart from a missing claude.

D-161 (an outage loses nothing: three failed calls and no success end the run, nothing is marked, the tries are not counted) stays. D-164 closes the
denial it left open: three crafted items at the head of the queue (the fake exits 1 whenever the prompt holds $ACE_WATCH_FAKE_POISON) used to trip the
breaker on EVERY scan and hide everything behind them. Fake reader only; the store of every test is a scratch dir under tmp_path.
"""

import pytest  # noqa: F401 - the fixtures below come from test_watch_reader

from ace_playbook.compile.watch import model

from .test_compile_watch_cli import U
from .test_watch_reader import NOW, cfg_of, enable, fake, world  # noqa: F401 - fixtures


def res_of(*titles: str) -> dict:
    entries = [{"id": t, "title": t, "link": f"https://example.test/p/{i}", "updated": "", "summary": f"about {t}"} for i, t in enumerate(titles)]
    return {"id": "feed", "url": "https://example.test/feed.xml", "kind": "rss", "tier": 1, "sha256": "d" * 64, "data": entries, "body": ""}


def feed_xml(*titles: str) -> bytes:
    items = "".join(f"<item><title>{t}</title><link>https://example.test/p/{i}</link><guid>g{i}</guid><description>about it</description></item>"
                    for i, t in enumerate(titles))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>f</title>{items}</channel></rss>'.encode()


def test_three_poisoned_head_items_cost_one_scan_then_retire_after_two_individual_tries(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    res, seen = res_of("POISON a", "POISON b", "POISON c", "good one", "good two"), {}
    keys = [it["key"] for it in model.items_of([res])]
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)                                   # scan 1: the breaker trips
    state = seen["reader"]
    assert out == [] and lines[0] == "reader: 0 read, 0 rejected, 0 leads, 2 unavailable, 3 failed, breaker tripped"
    assert state["breaker"] == keys[:3] and state["done"] == {} and state["fail"] == {}
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)                                   # scan 2: the good items go first
    assert len(out) == 2 and lines[0].startswith("reader: 2 read, 0 rejected, 2 leads, 3 failed") and "breaker" not in lines[0]
    assert "good one" in fake.calls()[3]["argv"][-1] and sorted(state["done"]) == sorted(keys[3:])
    assert sorted(state["fail"].values()) == [1, 1, 1] and state["breaker"] == keys[:3]
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)                                   # scan 3: a second failed try retires each
    assert out == [] and lines[0] == "reader: 0 read, 0 rejected, 0 leads, 3 failed" and state["fail"] == {} and sorted(state["done"]) == sorted(keys)
    assert state["breaker"] == [] and len(fake.calls()) == 11
    assert model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)[1][0] == "reader: 0 read, 0 rejected, 0 leads" and len(fake.calls()) == 11


def test_in_an_outage_nothing_is_marked_or_counted_and_the_head_of_the_queue_rotates(fake, tmp_path):
    """D-161 still holds (done and fail stay empty over three scans) and the streak is a different set of items each scan."""
    fake("exit1")
    res, seen = res_of(*[f"item {i}" for i in range(8)]), {}
    keys = [it["key"] for it in model.items_of([res])]
    heads = []
    for _ in range(3):
        model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
        heads.append(seen["reader"]["breaker"][:3])
        assert seen["reader"]["done"] == {} and seen["reader"]["fail"] == {}
    assert heads[0] == keys[:3] and heads[1] == keys[3:6] and heads[2] != heads[1] and len(fake.calls()) == 9


def test_the_tripped_items_go_behind_every_other_queued_item_not_only_behind_the_first_20(fake, tmp_path, monkeypatch):
    """The queue is ordered before it is cut to MAX_ITEMS: with 3 tripped items and 20 good ones, the next scan reads the 20 good ones."""
    fake("irrelevant")
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    res, seen = res_of("POISON a", "POISON b", "POISON c", *[f"good {i}" for i in range(20)]), {}
    model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert lines[0].startswith("reader: 20 read, 0 rejected, 0 leads") and "failed" not in lines[0] and len(seen["reader"]["done"]) == 20


def test_the_breaker_list_is_pruned_when_an_item_is_read(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    res, seen = res_of("POISON a", "POISON b", "POISON c", "good one"), {}
    model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert len(seen["reader"]["breaker"]) == 3
    monkeypatch.delenv("ACE_WATCH_FAKE_POISON")                                                             # the model can read them now
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert len(out) == 4 and seen["reader"]["breaker"] == [] and "breaker" not in lines[0]


def test_the_scan_line_and_watch_status_say_breaker_tripped_not_claude_unavailable(world, fake, capsys, monkeypatch):
    monkeypatch.setenv("ACE_WATCH_FAKE_POISON", "POISON")
    world.pages[U["feed"]] = (feed_xml("POISON a", "POISON b", "POISON c", "good one"), "application/rss+xml")
    enable(world)
    code, out, err = world.run(capsys, "scan")
    assert code == 0 and "reader: 0 read, 0 rejected, 0 leads, 1 unavailable, 3 failed, breaker tripped" in out
    assert "claude is unavailable" not in out and "reader: breaker tripped (3 failed calls, no success); 1 item(s) not read" in out
    assert "breaker tripped" in world.run(capsys, "status")[1]


def struck(res: dict, n: int) -> tuple[dict, list[str]]:
    """A `seen` in which the first `n` items of `res` already carry one failed try (a strike), and the keys of all its items."""
    keys = [it["key"] for it in model.items_of([res])]
    return {"reader": {"fail": {k: 1 for k in keys[:n]}}}, keys


def test_prior_strike_items_are_not_evidence_of_an_outage_and_lose_nothing_in_one(fake, tmp_path):
    """D-164 clause 2: 3 items with a strike + 5 fresh ones, every call fails: the fresh ones trip the breaker, the strikes stay at 1, nothing is marked."""
    fake("exit1")
    res = res_of(*[f"item {i}" for i in range(8)])
    seen, keys = struck(res, 3)
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert out == [] and "breaker tripped" in lines[0] and len(fake.calls()) == 6
    assert seen["reader"]["fail"] == {k: 1 for k in keys[:3]} and seen["reader"]["done"] == {}


def test_a_run_of_only_prior_strike_items_that_all_fail_retires_them(fake, tmp_path):
    """Accepted residual: an outage hitting a run with nothing but prior-strike items retires them (no fresh item can trip the breaker)."""
    fake("exit1")
    res = res_of("a", "b", "c")
    seen, keys = struck(res, 3)
    out, lines = model.stage(cfg_of(), [res], tmp_path / "wd", seen, NOW)
    assert lines[0] == "reader: 0 read, 0 rejected, 0 leads, 3 failed" and seen["reader"]["fail"] == {} and sorted(seen["reader"]["done"]) == sorted(keys)


def test_a_strike_items_try_commits_at_the_first_success_of_the_run(fake, monkeypatch):
    """strike item fails, a fresh one fails, a fresh one succeeds: the strike item is retired, the fresh failure keeps one strike."""
    monkeypatch.setenv("ACE_WATCH_FAKE_SEQ", "exit1,exit1,irrelevant")
    items = model.items_of([res_of("a", "b", "c")])
    done, fail = {}, {items[0]["key"]: 1}
    model.pipeline(cfg_of(), items, NOW, model.new_stats(), done, fail)
    assert fail == {items[1]["key"]: 1} and sorted(done) == sorted([items[0]["key"], items[2]["key"]])
