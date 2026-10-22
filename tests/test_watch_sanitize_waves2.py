"""The sanitizer wave 14 (`strip_hidden`, `treebuild`, `treestyle`): foreign content decided by NAMESPACE, CDATA by the namespace of the current node,
`</p>` and `</br>` breaking out of a foreign run, the scan skipping quoted attribute values, code-span tags in Markdown, the byte order mark.
Shared helpers come from the first two files; each case is also pushed through `fetch`."""

from pathlib import Path

import pytest

from ace_playbook.compile.watch import stylepass
from ace_playbook.compile.watch.fetch import EXPECT, fetch, strip_hidden

from .test_watch_sanitize import URL, body_of
from .test_watch_sanitize_waves import leak_free
from .watch_support import Opener, Resp

# ------------------------------------------------------------------ wave 14: integration points per namespace
NS_LEAKS = {                                                 # the name is an ordinary foreign element in THIS namespace: `<p` breaks out of the run
    "svg-axml-style": "<svg><annotation-xml><style><p hidden></style></annotation-xml></svg>LEAK",
    "svg-axml-script": "<svg><annotation-xml><script><p hidden></script></annotation-xml></svg>LEAK",
    "math-title-xmp": "<math><title><xmp><p hidden></xmp></title></math>LEAK",
    "math-desc-noscript": "<math><desc><noscript><p hidden></noscript></desc></math>LEAK",
    "math-fo-iframe": "<math><foreignObject><iframe><p hidden></iframe></foreignObject></math>LEAK",
    "svg-mi-script": "<svg><mi><script><p hidden></script></mi></svg>LEAK", "svg-mo-style": "<svg><mo><style><p hidden></style></mo></svg>LEAK",
    "svg-mn-xmp": "<svg><mn><xmp><p hidden></xmp></mn></svg>LEAK", "svg-ms-noembed": "<svg><ms><noembed><p hidden></noembed></ms></svg>LEAK",
    "svg-mtext-title": "<svg><mtext><title><p hidden></title></mtext></svg>LEAK",
    "math-axml-bare": "<math><annotation-xml><style><p hidden></style></annotation-xml></math>LEAK",
    "math-axml-other": '<math><annotation-xml encoding="image/svg+xml"><xmp><p hidden></xmp></annotation-xml></math>LEAK',
    "glyph": "<math><mi><mglyph><style><p hidden></style></mglyph></mi></math>LEAK",
    "malignmark": "<math><mi><malignmark><xmp><p hidden></xmp></malignmark></mi></math>LEAK",
    "upper": "<SVG><MI><SCRIPT><P HIDDEN></SCRIPT></MI></SVG>LEAK",
    "axml-svg-child": "<math><annotation-xml><svg><mi><style><p hidden></style></mi></svg></annotation-xml></math>LEAK",
}


@pytest.mark.parametrize("name", sorted(NS_LEAKS))
def test_an_integration_point_name_in_the_other_namespace_is_an_ordinary_foreign_element(name):
    assert leak_free(NS_LEAKS[name]), name


NS_RAW = {                                                   # a REAL integration point: its children are HTML, so a raw-text tag is raw text there
    "svg-desc": "<svg><desc><xmp><p hidden></xmp></desc></svg>VIS", "svg-title": "<svg><title><style><p hidden></style></title></svg>VIS",
    "svg-fo": "<svg><foreignObject><iframe><p hidden></iframe></foreignObject></svg>VIS", "math-mi": "<math><mi><xmp><p hidden></xmp></mi></math>VIS",
    "math-mtext": "<math><mtext><noembed><p hidden></noembed></mtext></math>VIS", "math-ms": "<math><ms><style><p hidden></style></ms></math>VIS",
    "axml-html": '<math><annotation-xml encoding="text/html"><xmp><p hidden></xmp></annotation-xml></math>VIS',
    "axml-xhtml": '<math><annotation-xml encoding="application/xhtml+xml"><style><p hidden></style></annotation-xml></math>VIS',
    "axml-case": '<math><annotation-xml ENCODING="TEXT/HTML"><xmp><p hidden></xmp></annotation-xml></math>VIS',
}


@pytest.mark.parametrize("name", sorted(NS_RAW))
def test_the_children_of_a_real_integration_point_are_html_so_raw_text_ends_at_its_own_end_tag(name):
    assert "VIS" in strip_hidden(NS_RAW[name]) and "VIS" in body_of(NS_RAW[name]), name


def test_the_style_rules_of_an_html_integration_point_apply_and_those_of_a_foreign_style_do_not_swallow_the_page():
    page = "<svg><desc><style>.h{display:none}</style></desc></svg><p class=h>LEAK</p><p>OK</p>"
    assert "LEAK" not in strip_hidden(page) and "OK" in strip_hidden(page) and "h" in stylepass.style_rules(page)[0]
    page = "<svg><annotation-xml><style>.h{display:none}</style></annotation-xml></svg><p class=h>LEAK</p><p>OK</p>"
    assert "LEAK" not in strip_hidden(page) and "OK" in strip_hidden(page)                         # an svg style element: the tokenizer pass reads markup
    page = '<math><annotation-xml encoding="text/html"><style>.h{display:none}</style></annotation-xml></math><p class=h>LEAK</p><p>OK</p>'
    assert stylepass.style_sheets(page) == [".h{display:none}"] and "LEAK" not in strip_hidden(page)


# ------------------------------------------------------------------ CDATA: a real section whenever the current node is not an HTML element
CDATA_WORLDS = {
    "svg-fo": "<div hidden><svg><foreignObject><![CDATA[x></foreignObject></svg></div>]]>LEAK",
    "math-mi": "<div hidden><math><mi><![CDATA[x></mi></math></div>]]>LEAK",
    "svg-desc": "<div hidden><svg><desc><![CDATA[x></desc></svg></div>]]>LEAK", "svg-title": "<div hidden><svg><title><![CDATA[x></title></svg></div>]]>LEAK",
    "math-axml": '<div hidden><math><annotation-xml encoding="text/html"><![CDATA[x></annotation-xml></math></div>]]>LEAK',
    "math-mtext": "<div hidden><math><mtext><![CDATA[x></mtext></math></div>]]>LEAK", "svg": "<div hidden><svg><![CDATA[x></svg></div>]]>LEAK",
}


@pytest.mark.parametrize("name", sorted(CDATA_WORLDS))
def test_cdata_is_a_real_section_when_the_current_node_is_an_integration_point(name):
    assert leak_free(CDATA_WORLDS[name]), name


def test_cdata_under_an_html_element_inside_an_integration_point_is_still_a_bogus_comment():
    html = "<div hidden><svg><foreignObject><div><![CDATA[x></div></foreignObject></svg></div>]]>VIS"
    assert "VIS" in strip_hidden(html) and "VIS" in body_of(html)


# ------------------------------------------------------------------ `</p>` and `</br>` break out of a foreign run
BREAKOUT_WORLDS = {
    "cdata-after-p": "<svg></p><![CDATA[><span hidden>]]></svg>LEAK", "q-after-p": "<svg></p><q hidden></svg>LEAK",
    "q-after-br": "<svg></br><q hidden></svg>LEAK", "math-p": "<math></p><q hidden></math>LEAK", "nested": "<svg><g><a></p><q hidden></svg>LEAK",
    "upper": "<SVG></P><Q HIDDEN></SVG>LEAK", "axml": "<math><annotation-xml></p><q hidden></math>LEAK",
    "glyph": "<math><mi><mglyph></p><q hidden></mi></math>LEAK",
    "cdata-after-br": "<svg></br><![CDATA[><span hidden>]]></svg>LEAK", "in-div": "<div><svg></p><q hidden></svg>LEAK</div>",
}


@pytest.mark.parametrize("name", sorted(BREAKOUT_WORLDS))
def test_an_end_tag_p_or_br_pops_the_foreign_run_so_what_follows_is_html(name):
    assert leak_free(BREAKOUT_WORLDS[name]), name


def test_an_end_tag_p_in_an_integration_point_or_html_content_pops_nothing_and_a_hidden_svg_is_left_behind():
    assert strip_hidden("<div hidden><svg><desc></p></desc></svg></div>VIS") == "VIS" and strip_hidden("<svg hidden></p>VIS") == "VIS"
    assert strip_hidden("<p hidden><svg></p>VIS") == "VIS" and strip_hidden("<svg><desc></br></desc></svg>VIS") == "VIS"


# ------------------------------------------------------------------ the scan skips quoted attribute values
STYLE_WORLDS = {
    "p-cdata": '<svg></p><![CDATA[><style title="a>b">.h{display:none}</style>]]></svg><p class=h>LEAK</p>',
    "axml-script": '<svg><annotation-xml><script></annotation-xml></svg><style title="a>b">.h{display:none}</style></script></annotation-xml></svg>'
                   "<p class=h>LEAK</p>",
    "single": "<style title='a>b'>.h{display:none}</style><p class=h>LEAK</p>", "after-equals": '<style a = "x>y">.h{display:none}</style><p class=h>LEAK</p>',
    "fake-before": '<!-- <style a="> --><style title="a>b">.h{display:none}</style><p class=h>LEAK</p>',
}


@pytest.mark.parametrize("name", sorted(STYLE_WORLDS))
def test_a_style_tag_with_a_quoted_greater_than_still_applies_its_rules(name):
    page = STYLE_WORLDS[name] + "<p>OK</p>"
    assert ".h{display:none}" in stylepass.scan_sheets(page), name
    assert "h" in stylepass.style_rules(page)[0] and "LEAK" not in strip_hidden(page) and "LEAK" not in body_of(page) and "OK" in strip_hidden(page), name


def test_the_scan_of_start_tags_stays_linear_and_capped():
    from ace_playbook.compile.watch.htmlrules import TooDeep
    for page in ("<style a='" * 100000 + ">", '<style a="b>' * 80000, "<style " * 100000 + ">"):
        try:
            strip_hidden(page)
        except TooDeep:
            pass                                                # a start tag that runs far over the budget fails closed, in time
    assert stylepass.scan_sheets('<style a="1" b=\'2\' c=3 d = "4">.x{y:z}</style>') == [".x{y:z}"]


# ------------------------------------------------------------------ code spans in Markdown, the byte order mark, the 11 fixtures
@pytest.mark.parametrize("tag", ["details", "noscript", "select", "svg", "math", "template", "object", "dialog", "canvas", "video", "datalist"])
def test_a_tag_that_hides_its_content_is_prose_inside_a_trusted_code_span(tag):
    text = f"Use `<{tag}>` to fold.\n\nVIS\n"
    assert strip_hidden(text, markdown=True) == f"Use `<{tag}>` to fold.\nVIS"
    assert strip_hidden(f"```\n<{tag}>\n```\n\nVIS\n", markdown=True).endswith("VIS")
    assert strip_hidden(f"Use `<{tag}>` here.\n\n</{tag}>\n\nVIS\n", markdown=True) == "Use `\nVIS"        # a closer follows: markup, hides up to it


def test_fetch_drops_one_leading_byte_order_mark_and_strip_hidden_none():
    page = "<!DOCTYPE html><p hidden><table>X"
    item = fetch(URL, expect=("text/html",), store={}, now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(b"\xef\xbb\xbf" + page.encode(), "text/html")}))
    assert item["body"] == "X" and strip_hidden(page) == "X"                       # the decoder strips the one BOM: standards mode
    assert strip_hidden("\ufeff" + page) == ""      # a remaining U+FEFF is a character token: quirks mode


@pytest.mark.parametrize("kind", ["npm", "rss", "md"])
def test_fetch_drops_a_leading_byte_order_mark_for_every_kind(kind):
    name, ctype = {"npm": ("npm_tags.json", "application/json"), "rss": ("rss_ok.xml", "application/rss+xml"), "md": ("doc_page.md", "text/markdown")}[kind]
    raw = (Path(__file__).parent / "fixtures" / "watch" / name).read_bytes()
    plain_item = fetch(URL, expect=EXPECT[kind], store={}, kind=kind, now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(raw, ctype)}))
    bom_item = fetch(URL, expect=EXPECT[kind], store={}, kind=kind, now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(b"\xef\xbb\xbf" + raw, ctype)}))
    assert "dropped" not in bom_item and bom_item["data"] == plain_item["data"] and bom_item["body"] == plain_item["body"]
