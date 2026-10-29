"""One bounded DNS wait per scan (the wake-up scan): `netguard.resolve_failed` and `scan.collect`. No test sleeps and none reaches a resolver."""

import pytest

from ace_playbook.compile.watch import fetch as fetchmod
from ace_playbook.compile.watch import netguard, scan

from .watch_support import Resp, fixture

NOW = "2026-10-21T12:00:00Z"
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 0))]
LOOPBACK = [(2, 1, 6, "", ("127.0.0.1", 0))]


def sources(*names):
    return [{"id": n, "url": f"https://{n}.example.test/page.md", "kind": "md", "tier": 0, "cadence": "daily"} for n in names]


class Net:
    """A resolver whose answers a test steers, a recording SLEEP and an opener that serves one markdown page."""

    def __init__(self, monkeypatch, answer):
        self.answer, self.resolved, self.slept, self.opened = answer, [], [], []
        monkeypatch.setattr(netguard, "RESOLVE", self.resolve)
        monkeypatch.setattr(scan, "SLEEP", self.sleep)
        monkeypatch.setattr(scan, "DNS_WAIT", 7)

    def resolve(self, host, port, *a, **k):
        self.resolved.append(host)
        out = self.answer(host) if callable(self.answer) else self.answer
        if isinstance(out, BaseException):
            raise out
        return out

    def sleep(self, secs):
        self.slept.append(secs)

    def opener(self, req, timeout=None):
        self.opened.append(req.full_url)
        return Resp(fixture("doc_page.md"), "text/markdown")

    def collect(self, srcs):
        seen = {}
        return (*scan.collect(srcs, seen, NOW, True, self.opener), seen)


# ------------------------------------------------------------------ resolve_failed
@pytest.mark.parametrize("answer, failed", [(OSError("no such host"), True), (ValueError("bad"), True), (UnicodeError("idna"), True), ([], True),
                                            (PUBLIC, False), (LOOPBACK, False)])
def test_resolve_failed_is_true_only_for_an_error_or_no_answer(monkeypatch, answer, failed):
    net = Net(monkeypatch, answer)
    assert netguard.resolve_failed("name.example.test") is failed and net.resolved == ["name.example.test"]


def test_resolve_failed_does_not_resolve_a_literal_or_an_empty_host_and_blocked_address_is_unchanged(monkeypatch):
    net = Net(monkeypatch, OSError("x"))
    assert not any(netguard.resolve_failed(h) for h in ("127.0.0.1", "::1", "[::1]", "", None)) and net.resolved == []
    assert netguard.blocked_address("gone.example.test") is True and net.resolved == ["gone.example.test"]       # an error still reads as blocked
    net.answer = PUBLIC
    assert netguard.blocked_address("ok.example.test") is False and net.resolved == ["gone.example.test", "ok.example.test"]


# ------------------------------------------------------------------ collect
def test_a_failed_resolution_waits_once_refetches_the_source_and_notes_the_recovery(monkeypatch):
    state = {"up": False}
    net = Net(monkeypatch, lambda host: PUBLIC if state["up"] else OSError("not yet"))
    monkeypatch.setattr(scan, "SLEEP", lambda secs: (net.slept.append(secs), state.update(up=True)))
    real, calls = fetchmod.fetch, []
    monkeypatch.setattr(fetchmod, "fetch", lambda url, **k: (calls.append(url), real(url, **k))[1])
    results, notes, counts, seen = net.collect(sources("a", "b"))
    assert net.slept == [7] and calls == ["https://a.example.test/page.md"] * 2 + ["https://b.example.test/page.md"]
    assert counts == {"fetched": 2, "unchanged": 0, "dropped": 0} and [r["id"] for r in results] == ["a", "b"]
    assert notes == ["dns wait 7s: a recovered"] and set(seen["sources"]) == {"a", "b"}


def test_two_failing_sources_wait_exactly_once_and_both_stay_due(monkeypatch):
    net = Net(monkeypatch, OSError("still down"))
    results, notes, counts, seen = net.collect(sources("a", "b"))
    assert net.slept == [7] and counts["dropped"] == 2 and results == []
    assert notes == ["dns wait 7s: a still dropped", "dropped a: address", "dropped b: address"]
    assert net.resolved.count("a.example.test") == 3 and net.resolved.count("b.example.test") == 1       # b never waits and is never re-fetched
    assert "sources" in seen and not seen["sources"]                                                       # no stamp: the next scan fetches both


def test_an_unsafe_answer_never_waits(monkeypatch):
    net = Net(monkeypatch, LOOPBACK)
    results, notes, counts, _ = net.collect(sources("a"))
    assert net.slept == [] and notes == ["dropped a: address"] and counts["dropped"] == 1
    assert net.resolved == ["a.example.test"] * 2          # the fetch's check, then the one question whether the name failed to resolve


def test_a_recovered_source_does_not_stop_a_later_failure_from_being_dropped_without_a_wait(monkeypatch):
    state = {"calls": 0}

    def answer(host):
        if host == "a.example.test":
            state["calls"] += 1
            return OSError("first two lookups") if state["calls"] <= 2 else PUBLIC
        return OSError("always down")

    net = Net(monkeypatch, answer)
    results, notes, counts, _ = net.collect(sources("a", "b"))
    assert net.slept == [7] and notes == ["dns wait 7s: a recovered", "dropped b: address"] and counts == {"fetched": 1, "unchanged": 0, "dropped": 1}


def test_the_default_wait_is_twenty_seconds_and_sleep_is_time_sleep():
    import time
    assert scan.DNS_WAIT == 20.0 and scan.SLEEP is time.sleep
