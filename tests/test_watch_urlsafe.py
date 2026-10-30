"""A bracketed URL host that is no IPv6 literal (`https://[REDACTED]/x`) must never raise out of the watcher: it is dropped as malformed.
Python 3.11.4+ `urlsplit` raises ValueError on it. No test reaches the network (fake openers, the conftest resolver)."""

import email.message
import io
import json
import urllib.request
import urllib.response
from types import SimpleNamespace
from urllib.parse import SplitResult

import pytest

from ace_playbook.compile.watch import fetch as fetchmod
from ace_playbook.compile.watch import netguard, scan, urlsafe

from .watch_support import Opener, Resp, fixture

NOW = "2026-10-21T12:00:00Z"
BAD = "https://[REDACTED]/x"
GOOD = "https://example.test/feed"
RSS = ("application/rss+xml",)


def get(url, **kw):
    return fetchmod.fetch(url, expect=RSS, store={}, now=NOW, kind="rss", **kw)


# (a) the helper
@pytest.mark.parametrize("url", [BAD, "http://[abc", "https://host]/x", None, 5, b"http://x/"])
def test_split_is_none_for_a_rejected_url_or_a_non_string(url):
    assert urlsafe.split(url) is None


@pytest.mark.parametrize("url, host", [("https://example.com/a", "example.com"), ("http://[::1]/a", "::1")])
def test_split_returns_the_result_for_a_good_url(url, host):
    out = urlsafe.split(url)
    assert isinstance(out, SplitResult) and out.hostname == host


# (b) the sources file
def test_a_bracketed_host_in_the_sources_file_is_the_one_bad_entry_error(tmp_path):
    path = tmp_path / "watch_sources.json"
    path.write_text(json.dumps([{"id": "a", "url": BAD, "kind": "rss", "tier": 0}]), encoding="utf-8")
    with pytest.raises(ValueError, match="watch_sources has a bad entry"):
        fetchmod.load_sources(path)
    path.write_text(json.dumps([{"id": "a", "url": GOOD, "kind": "rss", "tier": 0}]), encoding="utf-8")
    assert [s["url"] for s in fetchmod.load_sources(path)] == [GOOD]


# (c) the fetch of a source URL
def test_fetching_a_bracketed_host_is_a_dropped_record_and_makes_no_request():
    op = Opener({})
    assert get(BAD, opener=op) == {"url": BAD, "fetched_at": NOW, "dropped": "url"} and op.requests == []


# (d) the redirect handler
class Reply(urllib.response.addinfourl):
    def __init__(self, url, code, headers, body=b""):
        super().__init__(io.BytesIO(body), headers, url, code)
        self.msg = "x"


class Fake(urllib.request.BaseHandler):
    handler_order = 100

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def http_open(self, req):
        self.asked.append(req.full_url)
        code, location, body = self.routes[req.full_url]
        headers = email.message.Message()
        headers["Content-Type"] = "application/rss+xml"
        if location:
            headers["Location"] = location
        return Reply(req.full_url, code, headers, body)

    https_open = http_open


def chain(routes):
    fake = Fake(routes)
    opener = urllib.request.build_opener(netguard.SameHost, fake)
    return fake, lambda req, timeout=None: opener.open(req, timeout=timeout)


def test_a_redirect_to_a_bracketed_host_is_dropped_and_a_same_host_redirect_still_works():
    fake, op = chain({GOOD: (302, BAD, b"")})
    out = get(GOOD, opener=op)
    assert out["dropped"] == "network ValueError" and fake.asked == [GOOD]
    fake, op = chain({GOOD: (302, "https://example.test/feed2", b""), "https://example.test/feed2": (200, None, fixture("rss_ok.xml"))})
    assert len(get(GOOD, opener=op)["data"]) == 2 and fake.asked == [GOOD, "https://example.test/feed2"]


@pytest.mark.parametrize("old, new", [(GOOD, BAD), (BAD, GOOD), (BAD, BAD), (GOOD, "http://[abc")])
def test_redirect_request_refuses_a_rejected_url_with_the_refusal_not_a_value_error(old, new):
    req = SimpleNamespace(full_url=old)                  # `Request(BAD)` itself raises, so only a stand-in can carry such a URL
    with pytest.raises(netguard.RedirectRefused):
        netguard.SameHost().redirect_request(req, None, 302, "x", {}, new)


# (e) a scan over a bad and a good source
def test_a_scan_drops_the_bracketed_source_with_a_note_and_never_waits_for_dns(monkeypatch):
    slept = []
    monkeypatch.setattr(scan, "SLEEP", slept.append)
    srcs = [{"id": "bad", "url": BAD, "kind": "rss", "tier": 0, "cadence": "daily"},
            {"id": "good", "url": GOOD, "kind": "rss", "tier": 0, "cadence": "daily"}]
    op = Opener({GOOD: Resp(fixture("rss_ok.xml"), "application/rss+xml")})
    results, notes, counts = scan.collect(srcs, {}, NOW, True, op)
    assert slept == [] and [r["id"] for r in results] == ["good"] and len(results[0]["data"]) == 2
    assert counts == {"fetched": 1, "unchanged": 0, "dropped": 1} and notes == ["dropped bad: url"]
    assert [r.full_url for r in op.requests] == [GOOD]


# (f) an item link with a bracketed host is regex text and never split
def test_a_feed_item_with_a_bracketed_link_parses_with_the_rest_of_the_feed():
    xml = fixture("rss_ok.xml").decode().replace("https://example.test/changelog#9-1-0", BAD)
    assert BAD in xml
    out = get(GOOD, opener=Opener({GOOD: Resp(xml.encode(), "application/rss+xml")}))
    assert "dropped" not in out and len(out["data"]) == 2 and BAD in out["body"]
