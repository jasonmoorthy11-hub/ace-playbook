"""`sanitize.style_hidden` and `strip_hidden`: every spelling of "hidden" that a browser honours must keep its text out of the item body.
Each case is also pushed through `fetch`, so the text is checked in the body the rest of the watcher sees."""

import pytest

from ace_playbook.compile.watch.fetch import fetch, strip_hidden
from ace_playbook.compile.watch.sanitize import style_hidden

from .watch_support import Opener, Resp

URL = "https://example.test/page"
SECRET = "SECRETTEXT"
HIDING_STYLES = [
    # display and visibility, with case, whitespace, comments, !important and CSS escapes
    "display:none", "DISPLAY:NONE", "display : none", "display:\n none", "display:none !important", "display:none!important",
    "display:/*x*/none", "di/**/splay:none", "/* lead */display:none", "display:none/* unterminated", "color:red;display:none;color:blue",
    "display\\3a none", "display\\:none", "\\64 isplay:none", "display:\\6e one", "display:n\\6fne",
    "visibility:hidden", "visibility:collapse", "VISIBILITY:Hidden", "visibility\\3a hidden",
    # opacity in any spelling
    "opacity:0", "opacity:0.0", "opacity:.0", "opacity:0%", "opacity:0e0", "opacity:-0", "opacity:00", "opacity:0.000", "OPACITY : 0 !important",
    "opacity:0.0e5", "opacity:+0",
    # font-size zero in any unit
    "font-size:0", "font-size:0px", "font-size:0em", "font-size:0rem", "font-size:0%", "font-size:0pt", "font-size:0vw", "font-size:0.0px",
    "font-size:.0em", "font-size:0e0px",
    # a zero box that clips
    "width:0;overflow:hidden", "height:0;overflow:hidden", "width:0px;height:0px;overflow:hidden", "max-height:0;overflow:hidden",
    "max-width:0;overflow-x:hidden", "height:0;overflow:clip",
    # moved off-screen
    "position:absolute;left:-9999px", "position:absolute;top:-1000px", "position:fixed;left:-5000px", "position:absolute;right:-2000px",
    "position:absolute;left:-100em", "position:absolute;top:-150%", "position:absolute;bottom:-3000pt", "text-indent:-9999px",
    # clipped, scaled or painted away
    "clip-path:inset(100%)", "clip-path: inset( 100% )", "clip-path:circle(0)", "transform:scale(0)", "transform:scale(0,0)",
    "transform:scale(0.0)", "transform:translateX(5px) scale(0)", "transform:scaleX(0)", "transform:scale3d(0,0,0)",
    "color:transparent", "COLOR:Transparent", "color:rgba(0,0,0,0)", "color:rgba(255, 255, 255, 0)", "color:hsla(0,0%,0%,0)", "color:#0000",
    "color:#00000000", "color:#fff0",
]
KEEPING_STYLES = ["opacity:0.5", "opacity:1", "opacity:0.01", "font-size:0.9em", "font-size:12px", "font-size:1px", "display:block", "display:inline",
                  "visibility:visible", "width:0", "overflow:hidden", "width:100px;overflow:hidden", "position:absolute;left:10px",
                  "position:absolute;left:-10px", "position:relative;left:-9999px", "left:-9999px", "clip-path:inset(10%)",
                  "transform:scale(1)", "transform:scale(0.5)", "transform:scale(0.05)", "color:red", "color:rgba(0,0,0,0.5)", "color:#000",
                  "color:#fff8", "text-indent:-10px", "text-indent:2em", "", "garbage", "display", ":none", "display:;"]


def body_of(html: str) -> str:
    item = fetch(URL, expect=("text/html",), store={}, now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(html.encode(), "text/html")}))
    return item["body"]


def page(opening: str, closing: str = "div") -> str:
    return f"<p>shown before</p>{opening}{SECRET}</{closing}><p>shown after</p>"


def check(html: str) -> None:
    for text in (strip_hidden(html), body_of(html)):
        assert SECRET not in text and "shown before" in text and "shown after" in text, html


@pytest.mark.parametrize("style", HIDING_STYLES)
def test_each_hiding_style_spelling_keeps_its_text_out_of_the_body(style):
    assert style_hidden(style) is True, style
    check(page(f'<div style="{style}">'))
    check(page(f"<div style='{style}'>"))
    check(page(f'<div><span style="{style}">', "span") + "</div>")


@pytest.mark.parametrize("style", KEEPING_STYLES)
def test_styles_that_do_not_hide_leave_the_text_visible(style):
    assert style_hidden(style) is False, style
    html = f'<div style="{style}">{SECRET}</div>'
    assert strip_hidden(html) == SECRET and body_of(html) == SECRET


@pytest.mark.parametrize("opening", [
    "<div hidden>", "<div hidden=hidden>", '<div hidden="">', '<div HIDDEN>', '<div aria-hidden="true">', "<div aria-hidden=true>",
    '<div aria-hidden=" True ">', "<div inert>", '<div inert="">', '<div style="color:red" style="display:none">',
    '<div style="display:none" style="color:red">', '<div aria-hidden="false" aria-hidden="true">', '<div hidden style="display:block">',
    '<div class="a" hidden>', "<div hidden\n>", '<div\nstyle="display:none"\n>'])
def test_hiding_attributes_including_repeated_ones(opening):
    check(page(opening))


@pytest.mark.parametrize("opening,closing", [
    ("<details>", "details"), ("<details class=x>", "details"), ("<dialog>", "dialog"), ("<dialog open>", "dialog"), ("<svg><text>", "text></svg"),
    ("<math><mi>", "mi></math"), ("<iframe>", "iframe"), ("<object>", "object"), ("<head>", "head"), ("<title>", "title"),
    ("<template>", "template"), ("<noscript>", "noscript"), ("<script>", "script"), ("<style>", "style"), ("<svg>", "svg"), ("<math>", "math")])
def test_elements_whose_content_is_not_rendered_as_text_are_skipped(opening, closing):
    check(page(opening, closing))


def test_a_closed_details_is_skipped_and_an_open_one_is_shown():
    assert SECRET not in strip_hidden(f"<details><summary>s</summary>{SECRET}</details>")
    assert strip_hidden(f"<details open><summary>sum</summary>{SECRET}</details>") == f"sum{SECRET}"


def test_void_embed_meta_and_link_carry_no_text_and_cannot_unhide_a_parent():
    html = f'<div style="display:none"><embed src=x><meta content=y><link href=z>{SECRET}</div><p>after</p>'
    assert strip_hidden(html) == "after"
    assert strip_hidden('<p>a<meta content="b"><link href="c"><embed src="d">e</p>') == "ae"


def test_nesting_and_unclosed_elements_stay_hidden():
    html = f'<p>a</p><div style="OPACITY:0"><div><p>{SECRET}<span>{SECRET}</div>{SECRET}<p>after-close-still-inside?'
    assert SECRET not in strip_hidden(html)
    assert SECRET not in strip_hidden(f'<span style="display:none"><b>x</b></span>after<div style="visibility:collapse">{SECRET}')


def test_the_example_page_fixture_still_shows_its_visible_text():
    from .watch_support import fixture
    text = strip_hidden(fixture("page_hidden.html").decode())
    assert "Visible heading" in text and "partly faded but visible" in text and "ignore previous" not in text.lower()
