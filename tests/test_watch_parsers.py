"""`compile/watch/parsers.py` through `fetch` (so the Content-Type, size and hidden-HTML guards apply to every source kind). No network."""

import json
from pathlib import Path

import pytest

from ace_playbook.compile.watch import netguard, parsers
from ace_playbook.compile.watch.fetch import CAP, EXPECT, fetch

from .watch_support import INJECTION, Opener, Resp, fixture

URL = "https://example.test/src"
NOW = "2026-10-21T12:00:00Z"
GOOD = {"rss": ("rss_ok.xml", "application/rss+xml"), "atom": ("atom_ok.xml", "application/atom+xml; charset=utf-8"),
        "npm": ("npm_tags.json", "application/json"), "sitemap": ("sitemap.xml", "application/xml"),
        "deprecations": ("deprecations.md", "text/markdown"), "md": ("doc_page.md", "text/plain")}


def get(kind, body, ctype, store=None, cap=CAP):
    return fetch(URL, expect=EXPECT[kind], store={} if store is None else store, kind=kind, cap=cap, now=NOW, opener=Opener({URL: Resp(body, ctype)}))


ALL_FIXTURES = {                                             # every file under tests/fixtures/watch: the kind and Content-Type it is served as
    "atom_ok.xml": ("atom", "application/atom+xml"), "atom_xhtml.xml": ("atom", "application/atom+xml"),
    "deprecations.md": ("deprecations", "text/markdown"), "deprecations_reordered.md": ("deprecations", "text/markdown"),
    "doc_page.md": ("md", "text/markdown"), "npm_tags.json": ("npm", "application/json"), "page_hidden.html": ("md", "text/html"),
    "rss_hidden.xml": ("rss", "application/rss+xml"), "rss_inline.xml": ("rss", "application/rss+xml"), "rss_ok.xml": ("rss", "application/rss+xml"),
    "sitemap.xml": ("sitemap", "application/xml"), "canary.xml": ("rss", "application/rss+xml"),       # W2: the planted-injection feed
}
NOT_A_SOURCE = {"fake_reader.py"}                            # W2: the fake `claude -p` of the Reader tests is a program, not a fetched source


def test_the_fixture_table_lists_every_fixture_file():
    files = sorted(p.name for p in (Path(__file__).parent / "fixtures" / "watch").iterdir() if p.name not in NOT_A_SOURCE)
    assert sorted(ALL_FIXTURES) == files and len(ALL_FIXTURES) == 12


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_every_fixture_goes_through_fetch_with_the_default_opener_replaced_and_yields_a_body(name, monkeypatch):
    kind, ctype = ALL_FIXTURES[name]
    monkeypatch.setattr(netguard, "open_url", lambda req, timeout=None: Resp(fixture(name), ctype))
    item = fetch(URL, expect=EXPECT[kind], store={}, kind=kind, now=NOW)
    assert "dropped" not in item and item["body"].strip(), name


# ------------------------------------------------------------------ the three guards, for every kind
@pytest.mark.parametrize("kind", sorted(GOOD))
def test_every_kind_parses_its_fixture(kind):
    name, ctype = GOOD[kind]
    item = get(kind, fixture(name), ctype)
    assert "dropped" not in item and item["data"] and item["body"]


@pytest.mark.parametrize("kind", sorted(GOOD))
def test_every_kind_drops_a_wrong_content_type(kind):
    name, _ = GOOD[kind]
    assert get(kind, fixture(name), "image/png")["dropped"] == "content-type image/png"
    assert get(kind, fixture(name), "application/x-msdownload")["dropped"].startswith("content-type")


@pytest.mark.parametrize("kind", sorted(GOOD))
def test_every_kind_drops_an_oversized_body_without_reading_it(kind):
    resp = Resp(b"x" * (CAP + 5000), GOOD[kind][1])
    out = fetch(URL, expect=EXPECT[kind], store={}, kind=kind, now=NOW, opener=Opener({URL: resp}))
    assert out["dropped"] == "too large" and resp.given == CAP + 1


@pytest.mark.parametrize("kind", sorted(GOOD))
def test_every_kind_drops_a_body_that_does_not_parse_or_is_the_wrong_shape(kind):
    ctype = GOOD[kind][1]
    if kind in ("rss", "atom", "sitemap", "npm"):
        assert get(kind, b"<<< not valid", ctype)["dropped"] == "parse"
    other = {"rss": "atom_ok.xml", "atom": "rss_ok.xml", "sitemap": "rss_ok.xml", "npm": "sitemap.xml"}.get(kind)
    if other:
        assert get(kind, fixture(other), ctype)["dropped"] == "parse"


def test_hidden_html_injection_in_feeds_and_pages_never_reaches_a_body_or_a_parsed_field():
    for kind, name, ctype in (("rss", "rss_hidden.xml", "application/rss+xml"), ("atom", "atom_ok.xml", "application/atom+xml"),
                              ("md", "doc_page.md", "text/markdown"), ("deprecations", "doc_page.md", "text/markdown")):
        body = fixture(name) + (fixture("deprecations.md") if kind == "deprecations" else b"")          # a page with a table and a hidden block
        item = get(kind, body, ctype)
        assert INJECTION not in json.dumps(item).lower() and "evil-model" not in json.dumps(item) and "curl" not in item["body"], kind
    rss = get("rss", fixture("rss_hidden.xml"), "application/rss+xml")
    assert rss["data"][0]["summary"] == "Subagents are useful. Zerowidth" and rss["data"][0]["title"] == "A harmless post about subagents"
    assert get("atom", fixture("atom_ok.xml"), "application/atom+xml")["data"][0]["summary"] == "What changed in 9.1.0"


def test_a_dtd_or_entity_declaration_is_refused():
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><rss version="2.0"><channel><item><title>&a;</title></item></channel></rss>'
    assert get("rss", bomb, "application/rss+xml")["dropped"] == "xml"
    for decl in (b"<!DOCTYPE rss>", b"<!doctype rss>", b'<!ENTITY x "y">', b"<! DOCTYPE rss>"):
        for kind, name, ctype in (("rss", "rss_ok.xml", "application/rss+xml"), ("atom", "atom_ok.xml", "application/atom+xml"),
                                  ("sitemap", "sitemap.xml", "application/xml")):
            body = fixture(name).replace(b"?>", b"?>\n" + decl, 1)
            assert get(kind, body, ctype)["dropped"] == "xml", (kind, decl)


# ------------------------------------------------------------------ the parsers
def test_rss_and_atom_fields():
    rss = parsers.parse_rss(fixture("rss_ok.xml").decode())
    assert rss[0] == {"id": "example-9.1.0", "title": "Release 9.1.0: new hooks events", "link": "https://example.test/changelog#9-1-0",
                      "updated": "Mon, 19 Oct 2026 10:00:00 GMT", "summary": "Adds a hooks event and fixes a status line bug."}
    assert rss[1]["id"] == "https://example.test/changelog#9-0-4"                          # no guid: the link
    atom = parsers.parse_atom(fixture("atom_ok.xml").decode())
    assert atom[0]["id"].endswith("v9.1.0") and atom[0]["link"] == "https://example.test/releases/tag/v9.1.0" and atom[0]["updated"].startswith("2026")
    assert atom[1]["link"] == "https://example.test/releases/tag/v9.0.4" and atom[1]["summary"] == "Fixes."


def test_links_that_are_not_http_are_dropped_from_entries():
    xml = '<rss version="2.0"><channel><item><title>t</title><link>javascript:alert(1)</link><guid>g</guid></item></channel></rss>'
    assert parsers.parse_rss(xml)[0]["link"] == "" and parsers.parse_rss(xml)[0]["id"] == "g"


def test_npm_dist_tags():
    assert parsers.parse_npm_dist_tags(fixture("npm_tags.json").decode()) == {"stable": "9.0.4", "latest": "9.1.0"}
    assert parsers.parse_npm_dist_tags('{"latest": "9.1.0"}') == {"stable": None, "latest": "9.1.0"}
    assert parsers.parse_npm_dist_tags('{"latest": "9.1.0", "stable": "not a version"}') == {"stable": None, "latest": "9.1.0"}
    for bad in ("[]", "{}", '{"latest": 5}', "nope", '{"latest": "9.1.0; rm -x", "stable": "1.2.3"}', '{"stable": "1.2.3"}'):
        with pytest.raises(ValueError):
            parsers.parse_npm_dist_tags(bad)


def test_sitemap():
    assert parsers.parse_sitemap(fixture("sitemap.xml").decode()) == {
        "https://docs.example.test/en/hooks": "2026-10-20", "https://docs.example.test/en/settings": "2026-09-01",
        "https://docs.example.test/en/overview": ""}


def test_markdown_heading_or_first_400_characters():
    assert parsers.parse_md("intro\n\n## Hooks reference ##\ntext") == {"heading": "Hooks reference ##"}
    assert parsers.parse_md("x " * 400)["heading"] == ("x " * 400).strip()[:400] and len(parsers.parse_md("y" * 900)["heading"]) == 400


def rows_of(name):
    return parsers.parse_deprecations_md(fixture(name).decode())


def test_deprecations_rows_of_the_status_and_history_layout():
    rows = rows_of("deprecations.md")
    by = {}
    for r in rows:
        by.setdefault(r["model_id"], []).append(r)
    assert set(by) == {"claude-active-model-20260101", "claude-future-model-20260401", "claude-preview-model-20260101",
                       "claude-old-sonnet-20250101", "claude-old-haiku-20240101"}
    assert [by[m][0]["state"] for m in sorted(by)] == ["Active", "Active", "Retired", "Deprecated", "Deprecated"]
    future = by["claude-future-model-20260401"][0]
    assert (future["deprecated"], future["retires"]) == (None, "Not sooner than June 1, 2027")       # kept as written; match.py ignores it for Active
    assert by["claude-preview-model-20260101"][0]["retires"] is None                               # "To be announced" is not a date
    status, history = by["claude-old-sonnet-20250101"]
    assert (status["deprecated"], status["retires"], status["successor"]) == ("September 1, 2026", "March 1, 2027", None)
    assert (history["deprecated"], history["retires"], history["successor"]) == (None, "March 1, 2027", "claude-new-sonnet-20260601")
    assert by["claude-active-model-20260101"][0]["deprecated"] is None and by["claude-active-model-20260101"][0]["retires"] is None
    assert status["row"] in fixture("deprecations.md").decode().splitlines()                      # verbatim, padding included
    assert status["row"].startswith("| `claude-old-sonnet-20250101` | Deprecated ")


def test_deprecations_rows_with_reordered_columns_and_several_ids():
    rows = rows_of("deprecations_reordered.md")
    assert [r["model_id"] for r in rows] == ["claude-old-sonnet-20250101", "claude-two-ids-a-20240101", "claude-two-ids-b-20240101"]
    assert rows[0] == {"model_id": "claude-old-sonnet-20250101", "deprecated": "September 1, 2026", "retires": "March 1, 2027",
                       "successor": "claude-new-sonnet-20260601", "state": None,
                       "row": "| claude-new-sonnet-20260601 | March 1, 2027 | September 1, 2026 | **claude-old-sonnet-20250101** |"}
    assert (rows[1]["successor"], rows[1]["retires"], rows[1]["deprecated"]) == (None, None, "June 1, 2026")
    assert rows[1]["row"] == rows[2]["row"]


def test_deprecations_header_variants_and_garbage():
    t = "| Name | Deprecation date | Sunset | Migrate to |\n|--|--|--|--|\n| claude-x-1 | A | B | claude-y-2 |\n"
    row = parsers.parse_deprecations_md(t)[0]
    assert (row["model_id"], row["deprecated"], row["retires"], row["successor"]) == ("claude-x-1", "A", "B", "claude-y-2")
    for junk in ("", "| a | b |", "|---|---|\n|---|---|", "| Model |\n|---|\n", "plain text\n" * 5):
        assert parsers.parse_deprecations_md(junk) == []
    short = "| Model | Retired |\n|---|---|\n| claude-q-1 |\n"                                  # a short row does not raise
    assert parsers.parse_deprecations_md(short)[0]["retires"] is None


def test_every_deprecations_row_is_a_verbatim_substring_of_the_fetched_body():
    item = get("deprecations", fixture("deprecations.md"), "text/markdown")
    assert all(r["row"] in item["body"] for r in item["data"])
    item = get("rss", fixture("rss_ok.xml"), "application/xml")
    assert all(e["title"] in item["body"] for e in item["data"])


# ------------------------------------------------------------------ wave 4: escaped text inside XHTML children, ASCII versions
ATOM_ENTRY = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>x</id><title>T</title><updated>2026-10-01</updated>'
              '<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">%s</div></content></entry></feed>')


@pytest.mark.parametrize("inner", [
    '<span hidden="">&lt;/span&gt; SECRETTEXT</span>', '<span style="display:none">&lt;/span&gt;SECRETTEXT</span>',
    '<span hidden="">&lt;/div&gt;&lt;p&gt; SECRETTEXT &lt;/p&gt;</span>', '<div hidden=""><b>&lt;/div&gt; SECRETTEXT</b></div>',
    '<span hidden="">x<b>y</b>&lt;/span&gt;SECRETTEXT</span>'])
def test_xhtml_text_inside_a_child_element_cannot_close_its_hidden_ancestor_with_an_escaped_tag(inner):
    summary = parsers.parse_atom(ATOM_ENTRY % f"vis {inner} tail")[0]["summary"]
    assert "SECRETTEXT" not in summary and summary.startswith("vis")


def test_xhtml_child_text_is_escaped_once_and_the_roots_own_text_stays_raw():
    entry = parsers.parse_atom(ATOM_ENTRY % "a <b>x &amp; y &lt;i&gt; z</b> tail")[0]
    assert entry["summary"] == "a x & y <i> z tail"
    rss = '<rss><channel><item><title>t</title><description>&lt;div hidden&gt;SECRETTEXT&lt;/div&gt;seen</description></item></channel></rss>'
    assert parsers.parse_rss(rss)[0]["summary"] == "seen"
    rss = '<rss><channel><item><title>A &lt;script/&gt;SECRETTEXT</title></item></channel></rss>'
    assert "SECRETTEXT" not in parsers.parse_rss(rss)[0]["title"]


@pytest.mark.parametrize("latest", ["1.2.3-ignore_previous_instructions_é", "1.2.3-é", "١.٢.٣", "1.2.3-" + "a" * 41, "next",
                                    "1.2.3_x", "1.2.3-a b", "1.2.3\n", "1.2.3-Ａ"])
def test_an_npm_latest_that_is_not_an_ascii_version_is_a_parse_drop(latest):
    body = json.dumps({"latest": latest, "stable": "1.0.0"}).encode()
    with pytest.raises(ValueError):
        parsers.parse_npm_dist_tags(body.decode())
    assert get("npm", body, "application/json") == {"url": URL, "fetched_at": NOW, "dropped": "parse"}


@pytest.mark.parametrize("latest", ["1.2.3", "10.20.30-beta.1", "1.2.3+build5", "1.2.3-" + "a" * 40, "0.0.1-rc.1"])
def test_an_ascii_version_passes(latest):
    assert parsers.parse_npm_dist_tags(json.dumps({"latest": latest}))["latest"] == latest
