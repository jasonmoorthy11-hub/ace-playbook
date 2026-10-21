"""`compile/watch/fetch.py` and `sanitize.py`: conditional GET, hash store, drops, and the removal of hidden HTML. No network."""

import hashlib
import json

import pytest

from ace_playbook.compile.watch import fetch as fetch_mod
from ace_playbook.compile.watch import sanitize
from ace_playbook.compile.watch.fetch import CAP, fetch, strip_hidden

from .watch_support import FIXTURES, INJECTION, Opener, Resp, fixture, not_modified

URL = "https://example.test/page"
HTML = ("text/html",)


def get(url=URL, store=None, expect=HTML, **kw):
    return fetch(url, expect=expect, store={} if store is None else store, now="2026-10-21T12:00:00Z", **kw)


# ------------------------------------------------------------------ strip_hidden
def test_hidden_html_is_gone_and_visible_text_stays():
    text = strip_hidden(fixture("page_hidden.html").decode())
    assert INJECTION not in text and "exfiltrate" not in text
    for visible in ("Visible heading", "Benign visible text.", "partly faded but visible", "After the hidden blocks."):
        assert visible in text.splitlines() or visible in text
    assert "Zerowidthglue" in text and not any(c in text for c in "​‌‍⁠﻿")
    assert "color:red" not in text and "var x" not in text


@pytest.mark.parametrize("snippet", [
    '<div style="display:none">X</div>', '<div style="display : none !important">X</div>', '<p style="visibility:hidden">X</p>',
    '<p style="font-size:0">X</p>', '<p style="font-size: 0px">X</p>', '<p style="opacity:0">X</p>', '<p style="opacity: 0.0">X</p>',
    "<p hidden>X</p>", '<p hidden="">X</p>', '<p aria-hidden="true">X</p>', '<p aria-hidden=" TRUE ">X</p>', "<script>X</script>",
    "<style>X</style>", "<template>X</template>", "<noscript>X</noscript>", "<!-- X -->", '<div style="display:none"><p>X</p><b>X</b></div>',
    '<div style="display:none"><div>inner</div>X</div>', '<div hidden><span>X', "<script>var a = '<b>';X"])
def test_each_kind_of_hiding_is_removed(snippet):
    assert strip_hidden(f"<p>shown</p>{snippet}<p>also shown</p>").replace("shown", "").replace("also", "").strip() == ""
    assert "X" not in strip_hidden(f"<p>shown</p>{snippet}")


@pytest.mark.parametrize("snippet", ['<p style="opacity:0.5">V</p>', '<p style="font-size:0.9em">V</p>', '<p style="font-size:12px">V</p>',
                                     '<p aria-hidden="false">V</p>', '<p style="display:block">V</p>'])
def test_visible_styles_are_kept(snippet):
    assert strip_hidden(snippet) == "V"


def test_an_unclosed_hidden_element_hides_the_rest_and_a_stray_end_tag_is_ignored():
    assert strip_hidden('<p>a</p></div><div style="display:none"><p>b</p>c') == "a"


def test_block_tags_make_lines_and_inline_tags_do_not():
    assert strip_hidden("<h1>Title</h1><p>one <b>two</b> three</p><ul><li>x</li><li>y</li></ul>a<br>b") == "Title\none two three\nx\ny\na b"


def test_plain_text_passes_through_without_invisible_or_control_characters():
    assert sanitize.plain("a​b\x1b[0mc\td\n‮e") == "ab[0mc\td\ne"
    assert strip_hidden("a &amp; b &lt; c") == "a & b < c"


# ------------------------------------------------------------------ fetch
def test_a_fetch_returns_the_item_and_records_url_sha256_and_time():
    html = fixture("page_hidden.html")
    store = {}
    item = get(store=store, opener=Opener({URL: Resp(html, "text/html; charset=utf-8", etag='"e1"')}))
    assert item["url"] == URL and item["sha256"] == hashlib.sha256(html).hexdigest() and item["content_type"] == "text/html"
    assert item["fetched_at"] == "2026-10-21T12:00:00Z" and item["prev_sha256"] is None and "Visible heading" in item["body"]
    assert INJECTION not in item["body"]
    assert store["fetched"][URL] == {"sha256": item["sha256"], "fetched_at": "2026-10-21T12:00:00Z", "etag": '"e1"'}


def test_an_unchanged_body_returns_none_but_refreshes_the_time():
    store = {"fetched": {URL: {"sha256": hashlib.sha256(b"<p>x</p>").hexdigest(), "fetched_at": "2026-10-01T00:00:00Z", "etag": ""}}}
    assert get(store=store, opener=Opener({URL: Resp(b"<p>x</p>", "text/html")})) is None
    assert store["fetched"][URL]["fetched_at"] == "2026-10-21T12:00:00Z"


def test_a_changed_body_carries_the_previous_hash():
    store = {"fetched": {URL: {"sha256": "a" * 64, "fetched_at": "t", "etag": ""}}}
    assert get(store=store, opener=Opener({URL: Resp(b"<p>y</p>", "text/html")}))["prev_sha256"] == "a" * 64


def test_etag_is_sent_back_and_a_304_is_unchanged():
    store = {"fetched": {URL: {"sha256": "a" * 64, "fetched_at": "t", "etag": '"e1"'}}}
    op = Opener({URL: not_modified(URL)})
    assert get(store=store, opener=op) is None
    assert op.requests[0].get_header("If-none-match") == '"e1"' and op.requests[0].get_header("User-agent").startswith("ace-playbook-watch")
    assert store["fetched"][URL]["fetched_at"] == "2026-10-21T12:00:00Z"


def test_wrong_content_type_is_dropped_before_the_body_is_read():
    resp = Resp(b"<rss/>", "application/octet-stream")
    out = get(opener=Opener({URL: resp}))
    assert out["dropped"].startswith("content-type") and resp.given == 0 and set(out) == {"url", "fetched_at", "dropped"}
    assert get(opener=Opener({URL: Resp(b"x", None)}))["dropped"] == "content-type missing"


def test_an_oversized_body_is_dropped_without_reading_it_all():
    resp = Resp(b"a" * (2 * CAP), "text/html")
    store = {}
    out = get(store=store, opener=Opener({URL: resp}))
    assert out["dropped"] == "too large" and resp.given == CAP + 1 and URL not in store.get("fetched", {})
    exact = get(opener=Opener({URL: Resp(b"a" * CAP, "text/html")}))
    assert "dropped" not in exact


def test_a_body_that_does_not_parse_is_dropped_and_not_remembered():
    store = {}
    assert get(url=URL, store=store, expect=("application/json",), kind="npm", opener=Opener({URL: Resp(b"{nope", "application/json")}))["dropped"] == "parse"
    assert URL not in store.get("fetched", {})


@pytest.mark.parametrize("err", [OSError("boom /secret/path"), TimeoutError("slow"), ValueError("bad url")])
def test_network_errors_are_dropped_with_the_type_only(err):
    assert get(opener=Opener({URL: err}))["dropped"] == "network " + type(err).__name__


def test_http_errors_and_other_schemes_are_dropped():
    import urllib.error
    assert get(opener=Opener({URL: urllib.error.HTTPError(URL, 503, "x", {}, None)}))["dropped"] == "http 503"
    assert get(url="file:///etc/passwd", opener=Opener({}))["dropped"] == "scheme"
    assert get(url="ftp://example.test/x", opener=Opener({}))["dropped"] == "scheme"


def test_the_default_opener_is_the_same_host_opener_and_is_looked_up_at_call_time(monkeypatch):
    from ace_playbook.compile.watch import netguard
    seen = []
    monkeypatch.setattr(netguard, "open_url", lambda req, timeout=None: seen.append(req.full_url) or Resp(b"<p>ok</p>", "text/html"))
    assert get()["body"] == "ok" and seen == [URL]


def test_a_hidden_html_injection_never_reaches_the_item_body():
    page = b"<p>Benign release notes.</p><div style=\"display:none\">" + INJECTION.encode() + b" and set the model to evil</div>"
    item = get(opener=Opener({URL: Resp(page, "text/html")}))
    assert item["body"] == "Benign release notes." and INJECTION not in json.dumps(item)


def test_load_sources_validates_and_defaults_cadence(tmp_path):
    ok = [{"id": "a", "url": "https://x.test/a", "kind": "rss", "tier": 0, "cadence": "weekly"},
          {"id": "b", "url": "https://x.test/b", "kind": "atom", "tier": 1}, {"id": "c", "url": "https://x.test/c", "kind": "npm", "tier": 0}]
    p = tmp_path / "s.json"
    p.write_text(json.dumps(ok))
    assert [(s["id"], s["cadence"]) for s in fetch_mod.load_sources(p)] == [("a", "weekly"), ("b", "weekly"), ("c", "daily")]
    for bad in ([], {}, [{"id": "a"}], [{**ok[0], "url": "file:///x"}], [{**ok[0], "kind": "html"}], [{**ok[0], "tier": 2}], [ok[0], ok[0]],
                [{**ok[0], "id": "../x"}], [{**ok[0], "tier": True}]):
        p.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            fetch_mod.load_sources(p)
    p.write_text("{nope")
    with pytest.raises(ValueError):
        fetch_mod.load_sources(p)
    with pytest.raises(ValueError):
        fetch_mod.load_sources(tmp_path / "missing.json")


def test_the_example_sources_file_is_valid_and_has_five_tier_0_and_two_tier_1_sources():
    from pathlib import Path
    sources = fetch_mod.load_sources(Path(FIXTURES).parents[2] / "docs" / "watch" / "sources.example.json")
    assert [s["tier"] for s in sources].count(0) == 5 and [s["tier"] for s in sources].count(1) == 2
    assert {s["kind"] for s in sources} == {"rss", "atom", "npm", "deprecations", "sitemap"}
