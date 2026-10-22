"""`sanitize.style_hidden` and `strip_hidden`: every spelling of "hidden" that a browser honours must keep its text out of the item body.
Each case is also pushed through `fetch`, so the text is checked in the body the rest of the watcher sees."""

import pytest

from ace_playbook.compile.watch import cssstyle, sanitize
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
    # review wave: clip, font shorthand, modern color syntax, content-visibility, zoom, far units, far translate
    "clip:rect(0,0,0,0)", "clip:rect(0 0 0 0)", "clip: rect( 0px , 0px , 0px , 0px )", "position:absolute;clip:rect(0 0 0 0)", "font:0/0 a",
    "font:0px", "font:italic 0px/0 serif", "font-size:calc(0px)", "color:rgb(0 0 0 / 0)", "color:rgb(0 0 0/0%)", "color:rgba(1,2,3,0.0)",
    "color:rgba(1,2,3,0)", "color:hsl(0 0% 0% / 0%)", "color:hsla(0,0%,0%,0)", "color:RGB(0 0 0 / 0)", "content-visibility:hidden", "zoom:0",
    "text-indent:-100em", "text-indent:-9999px", "text-indent:-100%", "text-indent:-100vw", "position:absolute;left:-100em",
    "position:absolute;top:-100rem", "position:fixed;right:-100vw", "position:absolute;bottom:-100%", "transform:translateX(-9999px)",
    "transform:translate(-100em,0)", "dis\nplay:none", "display\x00:none",
    # wave 4: cheap hides (negative or tiny opacity, any zero scale argument, far margins, half-inset clip-path, text-fill, tiny boxes)
    "opacity:-1", "opacity:-0.5", "opacity:0.05", "opacity:0.01", "opacity:9%", "opacity:-5%", "font-size:0.5px", "font-size:.001px",
    "font-size:0.01em", "font-size:5%", "font-size:0.5pt", "transform:scale(1,0)", "transform:scale(0,1)", "transform:scaleY(0)",
    "transform:scale(1,1,0)", "transform:scale3d(1,0,1)", "scale:0", "scale:1 0", "margin-left:-9999px", "margin-top:-5000px",
    "margin:0 0 0 -9999px", "margin-inline-start:-9999px", "clip-path:inset(50%)", "clip-path:inset(10% 60%)", "clip-path:inset(0 0 99%)",
    "-webkit-text-fill-color:transparent", "-webkit-text-fill-color:rgba(0,0,0,0)", "text-indent:-501px", "text-indent:-21em",
    "text-indent:-500px", "position:absolute;left:-600px", "transform:translateX(-600px)", "transform:translate(-99em,0)",
    "position:absolute;left:-99em", "text-indent:-99em", "width:1px;height:1px;overflow:hidden", "height:1px;overflow:clip",
    "border:0;clip:rect(1px,1px,1px,1px);clip-path:inset(50%);height:1px;margin:-1px;overflow:hidden;padding:0;position:absolute;width:1px",
    "clip:rect(1px,1px,1px,1px);overflow:hidden;height:1px;width:1px;position:absolute",
]
KEEPING_STYLES = ["opacity:0.5", "opacity:1", "opacity:0.1", "opacity:10%", "margin-left:-10px", "margin:-1px", "margin-left:-499px",
                  "text-indent:-499px", "text-indent:-19em", "font-size:0.9em", "font-size:12px", "font-size:1px", "display:block", "display:inline",
                  "visibility:visible", "width:0", "overflow:hidden", "width:100px;overflow:hidden", "position:absolute;left:10px",
                  "position:absolute;left:-10px", "position:relative;left:-9999px", "left:-9999px", "clip-path:inset(10%)",
                  "transform:scale(1)", "transform:scale(0.5)", "transform:scale(0.05)", "color:red", "color:rgba(0,0,0,0.5)", "color:#000",
                  "color:#fff8", "text-indent:-10px", "text-indent:2em", "", "garbage", "display", ":none", "display:;",
                  "clip:rect(1px,1px,1px,1px)", "clip:auto", "font:12px/0 serif", "font:12px serif", "font-size:calc(1px)", "color:rgb(0 0 0 / 0.5)",
                  "color:rgba(1,2,3,0.1)", "color:hsl(0 0% 0%)", "content-visibility:auto", "zoom:1", "position:absolute;left:-19em",
                  "transform:translateX(-10px)", "transform:translate(-19em,0)", "width:2px;overflow:hidden", "height:2px;overflow:hidden"]


def body_of(html: str) -> str:
    item = fetch(URL, expect=("text/html",), store={}, now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(html.encode(), "text/html")}))
    return item["body"]


def page(opening: str, closing: str = "div") -> str:
    return f"<p>shown before</p>{opening}{SECRET}</{closing}><p>shown after</p>"


def check(html: str) -> None:
    for text in (strip_hidden(html), body_of(html)):
        assert SECRET not in text and "shown before" in text and "shown after" in text, html


def gone(html: str) -> None:
    """The secret is absent from the visible text and from the fetch body."""
    for text in (strip_hidden(html), body_of(html)):
        assert SECRET not in text, html


def test_a_style_attribute_entity_is_decoded_by_the_parser_before_the_css_is_read():
    check(page('<div style="display&#58;none">'))
    check(page('<div style="display&colon;none">'))


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


# ------------------------------------------------------------------ review wave: more elements, <style> rules, adoption agency, characters
@pytest.mark.parametrize("opening,closing", [("<div popover>", "div"), ("<div popover=auto>", "div"), ("<datalist><option>", "option></datalist"),
                                             ("<noembed>", "noembed"), ("<noframes>", "noframes"), ("<canvas>", "canvas"), ("<video>", "video"),
                                             ("<audio>", "audio"), ("<ruby>a<rp>", "rp></ruby")])
def test_more_elements_whose_content_is_fallback_or_hidden(opening, closing):
    check(page(opening, closing))


@pytest.mark.parametrize("css,html", [
    (".h{display:none}", '<p class="h">SECRETTEXT</p>'), (".h { visibility : hidden }", '<p class="a h b">SECRETTEXT</p>'),
    ("#x{opacity:0}", '<p id="x">SECRETTEXT</p>'), ("p{font-size:0}", "<p>SECRETTEXT</p>"), ("P{display:none}", "<p>SECRETTEXT</p>"),
    (".h{clip:rect(0,0,0,0)}", '<p class="h">SECRETTEXT</p>'), ("/* c */.h{display:none}", '<p class="h">SECRETTEXT</p>'),
    (".a,.h{display:none}", '<p class="h">SECRETTEXT</p>'), (".h{display:none}", '<p class="h" style="display:block">SECRETTEXT</p>'),
    ("@import url(x.css); .h{display:none}", '<p class="h">SECRETTEXT</p>')])
def test_simple_style_block_rules_hide_what_they_match_even_before_the_style_block(css, html):
    gone(f"<style>{css}</style>{html}")
    gone(f"{html}<STYLE type='text/css'>{css}</STYLE>")


@pytest.mark.parametrize("css,html", [
    (".h{display:none}", '<p class="hh">SECRETTEXT</p>'), (".h{display:none}", '<p id="h">SECRETTEXT</p>'), (".h{color:red}", '<p class="h">SECRETTEXT</p>'),
    ("div .h{display:none}", '<p class="h">SECRETTEXT</p>'), ("div>.h{display:none}", '<div><p class="h">SECRETTEXT</p></div>'),
    (".h:hover{display:none}", '<p class="h">SECRETTEXT</p>'), ("@media print{.h{display:none}}", '<p class="h">SECRETTEXT</p>'),
    (".h[x]{display:none}", '<p class="h">SECRETTEXT</p>')])
def test_style_rules_beyond_the_simple_case_are_not_applied(css, html):
    assert strip_hidden(f"<style>{css}</style>{html}") == SECRET                                  # documented in LIMITATIONS.md


def test_the_adoption_agency_case_stays_hidden():
    assert SECRET not in strip_hidden(f"<b><i hidden></b>{SECRET}</i>")
    assert SECRET not in strip_hidden(f'<p>a</p><em><u style="display:none"></em>{SECRET}</u>')
    assert strip_hidden(f"<b><i></b>{SECRET}</i>") == SECRET                                      # a visible formatting element stays visible
    assert SECRET not in strip_hidden(f"<a><b hidden><i></a>{SECRET}</i></b>")


@pytest.mark.parametrize("char", ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u200e", "\u200f", "\u202a", "\u202e", "\u2066", "\u2069",
                                  "\u2061", "\u2062", "\u2063", "\u2064", "\u00ad", "\u3164", "\u2800", "\U000e0041", "\U000e0001", "\U000e007f",
                                  "\u180e", "\ufe0f", "\ufe00", "\U000e0100", "\u115f", "\u1160", "\uffa0", "\u034f", "\u17b4", "\u061c"])
def test_every_invisible_character_is_removed(char):
    assert strip_hidden(f"S{char}E{char}C") == "SEC" and sanitize.plain(f"a{char}b") == "ab"
    assert SECRET[0] + SECRET[1:] in strip_hidden(f"{SECRET[:3]}{char}{SECRET[3:]}")


def test_format_characters_in_general_are_removed():
    import unicodedata
    cf = [chr(c) for c in range(0x110000) if unicodedata.category(chr(c)) == "Cf"]
    assert cf and all(sanitize.plain(f"a{c}b") == "ab" for c in cf)
    assert sanitize.plain("a\u2028b\u2029c") == "a\nb\nc" and sanitize.plain("x\x1b[0m\x07y") == "x[0my"


def test_markdown_mode_treats_a_raw_text_opener_in_a_code_span_as_prose():
    md = "Note: the console no longer loads a `<script>` tag for this page.\n\n| Model | State |\n|---|---|\n| claude-x-1 | Deprecated |\n"
    assert "claude-x-1" not in strip_hidden(md) and "claude-x-1" in strip_hidden(md, markdown=True)
    assert "loads a `<script>` tag" in strip_hidden(md, markdown=True)
    bare = md.replace("`<script>`", "<script>")
    assert "claude-x-1" not in strip_hidden(bare, markdown=True)                  # a bare opener is markup: hidden to the end of input
    closed = f"before <script>{SECRET}</script> after\n| claude-x-1 |"
    assert SECRET not in strip_hidden(closed, markdown=True) and "claude-x-1" in strip_hidden(closed, markdown=True)
    assert SECRET not in strip_hidden(f"<!-- {SECRET} -->\ntext", markdown=True)
    assert SECRET not in strip_hidden(f'<div style="display:none">{SECRET}</div>\ntext', markdown=True)


# ------------------------------------------------------------------ wave 3: raw end-tag names, browser-style closers, markdown code
def test_a_prefixed_end_tag_does_not_close_an_unprefixed_hidden_element():
    assert strip_hidden("<div hidden>SECRET</x:div>VISIBLE") == ""                      # a browser ignores `</x:div>`: all of it stays hidden
    assert strip_hidden("<div hidden>SECRET</x:div>VISIBLE", markdown=True) == ""
    assert strip_hidden("<x:div hidden>A</x:div>B") == "B"                              # an unknown element: `hidden` applies, its end tag closes it
    assert strip_hidden("<x:div hidden>A</div>B") == ""                                 # `</div>` does not close `x:div`
    assert strip_hidden("<x:script>kept</x:script>") == "kept"                          # an unknown element, not a script


@pytest.mark.parametrize("closer", ["</script foo>", "</script/>", "</SCRIPT >", "</script\n>", "</Script x='y'>"])
@pytest.mark.parametrize("markdown", [False, True])
def test_a_script_closer_with_attributes_or_a_slash_still_hides_the_script_text(closer, markdown):
    text = strip_hidden(f"<script>{SECRET}{closer}VISIBLE", markdown=markdown)
    assert SECRET not in text and "VISIBLE" in text


@pytest.mark.parametrize("markdown", [False, True])
@pytest.mark.parametrize("tag", ["noscript", "template", "svg", "head", "dialog", "script", "style", "iframe", "title", "noembed"])
def test_a_bare_unclosed_skip_element_hides_to_the_end(tag, markdown):
    assert strip_hidden(f"before <{tag}>{SECRET} after", markdown=markdown) == "before"


@pytest.mark.parametrize("fence", ["```", "~~~", "````"])
def test_markdown_openers_in_a_fenced_block_are_prose(fence):
    closer = fence.strip()
    shown = strip_hidden(f"intro\n{fence}html\n<script>\n{closer}\nvisible text", markdown=True)
    assert "<script>" in shown and shown.endswith("visible text")
    assert "visible text" in strip_hidden(f"{fence}\n<style>\nvisible text", markdown=True)       # an unclosed fence runs to the end


def test_markdown_openers_are_prose_only_inside_code_spans_and_fences():
    assert strip_hidden("use `<script>` here\n\nvisible text", markdown=True) == "use `<script>` here\nvisible text"
    assert "visible text" in strip_hidden("use ``<style>`` and `<title>` here\nvisible text", markdown=True)
    assert strip_hidden(f"<script>\n{SECRET}\n", markdown=True) == ""                           # a bare opener hides to the end of input
    assert strip_hidden(f"line one\n<script>var x;\nline {SECRET}", markdown=True) == "line one"
    assert strip_hidden(f"<div hidden>\n{SECRET}\n</div>\nvisible", markdown=True) == "visible"
    assert strip_hidden(f"<div hidden>{SECRET}\nvisible", markdown=True) == ""
    assert SECRET not in strip_hidden(f"`x` <script>{SECRET}", markdown=True)                  # a code span before the opener changes nothing
    assert SECRET not in strip_hidden(f"a ` b <script>{SECRET}", markdown=True)                # an unmatched backtick is not a code span
    assert SECRET not in strip_hidden(f"``a <script>{SECRET}` b", markdown=True)               # a backtick run of another length does not close
    assert SECRET not in strip_hidden(f"~~~\n<style>\n~~~\n<style>{SECRET}", markdown=True)    # the fence closed: the next opener is bare
    assert SECRET not in strip_hidden(f"```\n<style>\n~~~\n```\n<style>{SECRET}", markdown=True)  # only a fence of the same character closes


def test_a_non_raw_text_skip_opener_in_a_code_span_still_hides_to_the_end():
    assert strip_hidden(f"use `<noscript>` {SECRET}", markdown=True) == "use `"


# ------------------------------------------------------------------ wave 4: self-closing tags, scope barriers, double-escaped script, markdown
def md_body(text: str, ctype: str = "text/markdown", kind: str = "md") -> str:
    item = fetch(URL, expect=(ctype,), store={}, kind=kind, now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(text.encode(), ctype)}))
    return item["body"] if item and "body" in item else ""


@pytest.mark.parametrize("html", [
    f"<p>ok</p><div hidden/>{SECRET}", f"<span style='display:none'/>{SECRET}", f"<p>ok</p><script/>{SECRET}</script>", f"<style/>{SECRET}</style>",
    f"<p>ok</p><script />{SECRET}", f"<template/>{SECRET}", f"<noscript/>{SECRET}", f"<div aria-hidden='true'/>{SECRET}",
    f"<div hidden/>{SECRET}</div>VISIBLE"])
@pytest.mark.parametrize("markdown", [False, True])
def test_a_slash_on_a_non_void_tag_is_ignored_like_a_browser_does(html, markdown):
    assert SECRET not in strip_hidden(html, markdown=markdown)


def test_self_closing_void_tags_and_closed_self_closing_elements_stay_as_they_were():
    assert strip_hidden("a<br/>b") == "a b" and strip_hidden("a<hr/>b") == "a b" and strip_hidden("a<img src=x/>b") == "ab"
    assert strip_hidden("<p>a</p><div hidden/>H</div>VISIBLE") == "a\nVISIBLE"


@pytest.mark.parametrize("html", [
    f"<span hidden><div>A</span>{SECRET}</div>{SECRET}", f"<div hidden><table></div>{SECRET}", f"<div hidden><object></div>{SECRET}",
    f"<div hidden><marquee></div>{SECRET}", f"<div hidden><td></div>{SECRET}", f"<div hidden><caption></div>{SECRET}",
    f"<b hidden><div>X</b>{SECRET}</div>{SECRET}", f"<span><div hidden>A</span>{SECRET}</div>",
    f"<div hidden><applet></div>{SECRET}", f"<div hidden><template></div>{SECRET}", f"<i hidden><table></i>{SECRET}"])
@pytest.mark.parametrize("markdown", [False, True])
def test_an_end_tag_that_a_browser_ignores_does_not_unhide_text(html, markdown):
    assert SECRET not in strip_hidden(html, markdown=markdown)


@pytest.mark.parametrize("html,shown", [
    ("<div hidden><p>x</div>VISIBLE", "VISIBLE"), ("<div hidden><ul><li>x</ul></div>VISIBLE", "VISIBLE"),
    ("<table><tr><td hidden>x</table>VISIBLE", "VISIBLE"), ("<span hidden><b>x</span>VISIBLE", "VISIBLE"),
    ("<div hidden><span>x</span></div>VISIBLE", "VISIBLE"), ("<b hidden><i>x</b>VISIBLE", "VISIBLE")])
def test_an_end_tag_with_only_formatting_or_implied_end_elements_in_between_still_closes(html, shown):
    assert strip_hidden(html) == shown


@pytest.mark.parametrize("markdown", [False, True])
def test_a_double_escaped_script_is_hidden_to_its_next_but_one_closer(markdown):
    assert strip_hidden(f"<script><!--<script></script>{SECRET}</script>VISIBLE", markdown=markdown) == "VISIBLE"
    assert strip_hidden(f"<script><!--<script ></script>{SECRET}</SCRIPT >VISIBLE", markdown=markdown) == "VISIBLE"
    assert SECRET not in strip_hidden(f"<script><!--<script></script>{SECRET}", markdown=markdown)               # no second closer: to the end
    assert strip_hidden("<script>a</script>VISIBLE", markdown=markdown) == "VISIBLE"
    assert strip_hidden("<script><!--<script>--></script>VISIBLE", markdown=markdown) == "VISIBLE"                # `-->` leaves the escape
    assert strip_hidden("<script><!-- x --></script>VISIBLE", markdown=markdown) == "VISIBLE"
    assert strip_hidden("<script><script></script>VISIBLE", markdown=markdown) == "VISIBLE"                       # no `<!--`: an ordinary script


def test_a_double_escaped_script_does_not_swallow_the_next_script():
    assert strip_hidden(f"<script><!--<script></script></script>VISIBLE<script>{SECRET}</script>DONE") == "VISIBLEDONE"


def test_the_element_stack_depth_is_capped_and_the_document_is_a_parse_drop():
    deep = "<b>" * 600 + "x"
    with pytest.raises(ValueError):
        strip_hidden(deep)
    assert strip_hidden("<b>" * 500 + "x") == "x"
    item = fetch(URL, expect=("text/html",), store={}, kind="md", now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(deep.encode(), "text/html")}))
    assert item == {"url": URL, "fetched_at": "2026-10-21T12:00:00Z", "dropped": "parse"}


def test_a_flood_of_unmatched_end_tags_is_fast():
    import time
    t0 = time.monotonic()
    with pytest.raises(ValueError):
        strip_hidden("<b>" * 20000 + "</i>" * 20000)
    assert time.monotonic() - t0 < 1.5


def test_unmatched_end_tags_below_the_depth_cap_are_skipped_without_a_scan():
    import time
    t0 = time.monotonic()
    assert strip_hidden("<b>" * 500 + "</i>" * 250000) == ""
    assert strip_hidden("<div hidden>" * 400 + "</span>" * 100000 + "X") == ""
    assert time.monotonic() - t0 < 4


MD_LEAKS = {
    "e2": f"# Page\n\n<p>\n`<script>`\n{SECRET}\n</script>\n</p>\n",
    "e4": f'# Page\n\n<a title="`"><script>`{SECRET} </script>\n',
    "f1": f"# Page\n\n<div>\n```\n<script>\n```\n{SECRET}\n</script>\n</div>\n",
    "e1": f"<div hidden>\n`<script>`{SECRET}\n</div>\n\nmore `x` text\n</script>tail",
    "e5": f"para `<script>` x\n\n{SECRET}\n</script>\nafter",
    "tilde": f"<div>\n~~~\n<style>\n~~~\n{SECRET}\n</style>\n</div>",
    "inline-tag": f'text <a title="`"><script>`{SECRET}</script>',
    "two-spans": f"`a` and `<script>` and `b`\n\n{SECRET}\n</script>",
    "span-then-closer-in-code": f"use `<script>` {SECRET} and `</script>` end",
    "html-block-open-no-closer": f"<div>\n`<script>` {SECRET}\n",
    "html-block-list": f"- <div>\n  `<script>` {SECRET}\n",
    "quote-block": f"> <div>\n> `<script>` {SECRET}\n",
    "pre-block-blank-line": f"<pre>\n\n`<script>` {SECRET}\n</pre>",
}


@pytest.mark.parametrize("name", sorted(MD_LEAKS))
def test_markdown_never_shows_the_text_of_a_raw_text_opener_that_has_a_closer_or_sits_in_an_html_block(name):
    text = MD_LEAKS[name]
    assert SECRET not in strip_hidden(text, markdown=True), name
    for ctype in ("text/markdown", "text/plain"):
        assert SECRET not in md_body(text, ctype), (name, ctype)


@pytest.mark.parametrize("text,shown", [
    ("use `<script>` here\n\nvisible text", "use `<script>` here\nvisible text"),
    ("intro\n```html\n<script>\n```\nvisible text", "intro\n```html\n<script>\n```\nvisible text"),
    ("`<style>` then `<title>`\n\nvisible", "`<style>` then `<title>`\nvisible"),
    ("<div>fine</div>\n\nuse `<script>` tag\n\nvisible", "fine\nuse `<script>` tag\nvisible"),
    ("<div>\n\n</div>\n\nuse `<script>`\n\nvisible", "use `<script>`\nvisible"),
    ("<div>a</div>\n\n`<script>` shown", "a\n`<script>` shown")])
def test_markdown_code_with_no_closer_after_it_is_still_prose(text, shown):
    assert strip_hidden(text, markdown=True) == shown


def test_markdown_html_blocks_hide_a_code_span_opener_even_without_a_closer():
    assert "hidden" not in strip_hidden("<div>\n`<script>` hidden", markdown=True)
    assert strip_hidden("<!--\n\n`<script>` x\n-->\nafter", markdown=True) == "after"


def test_html_content_type_is_not_markdown_gated_by_these_rules():
    assert SECRET not in md_body(MD_LEAKS["e2"], "text/html")
    assert SECRET not in md_body(MD_LEAKS["f1"], "text/html")


# ------------------------------------------------------------------ wave 4: css rules, class case, default-ignorable characters
@pytest.mark.parametrize("css,html", [
    (".X{display:none}", f"<div class='x'>{SECRET}</div>"), (".x{display:none}", f"<div class='X'>{SECRET}</div>"),
    ("#Hid{display:none}", f"<div id='hid'>{SECRET}</div>"), (".a{opacity:-1}", f"<div class='a'>{SECRET}</div>"),
    (".sr{clip:rect(1px,1px,1px,1px);height:1px;overflow:hidden;position:absolute;width:1px}", f"<div class='sr'>{SECRET}</div>")])
def test_style_block_classes_and_ids_match_case_insensitively_like_quirks_mode(css, html):
    gone(f"<style>{css}</style>{html}")


def test_every_default_ignorable_code_point_is_invisible():
    from ace_playbook.compile.watch.chars import invisible
    ranges = [(0xAD, 0xAD), (0x34F, 0x34F), (0x61C, 0x61C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F),
              (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
              (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF)]
    missed = [hex(c) for a, b in ranges for c in range(a, b + 1) if not invisible(chr(c)) or sanitize.plain(f"a{chr(c)}b") != "ab"]
    assert missed == []
    assert sanitize.plain("price 1− 2") == "price 1− 2"


# ------------------------------------------------------------------ wave 5: scope barriers, document-level hiding, shadow DOM, one-line code spans
S = SECRET
SCOPE_LEAKS = [f"<p hidden><button></p>{S}", f"<li hidden><ul></li>{S}", f"<li hidden><ol><li>x</li></li>{S}", f"<div hidden><select></div>{S}",
               f"<div hidden><select><option>x</div>{S}", f"<table hidden><select></table>{S}", f"<b hidden><select></b>{S}"]


@pytest.mark.parametrize("html", SCOPE_LEAKS)
@pytest.mark.parametrize("markdown", [False, True])
def test_an_end_tag_stopped_by_button_list_or_select_scope_keeps_the_text_hidden(html, markdown):
    assert S not in strip_hidden(html, markdown=markdown)


def test_an_end_tag_that_the_scope_rules_let_through_still_closes():
    assert strip_hidden("<p hidden>x</p>VISIBLE") == "VISIBLE" and strip_hidden("<li hidden>x</li>VISIBLE") == "VISIBLE"
    assert strip_hidden("<li hidden><b>x</li>VISIBLE") == "VISIBLE" and strip_hidden("<div hidden><button>x</button></div>VISIBLE") == "VISIBLE"
    assert strip_hidden("<ul><li hidden>a</li><li>VISIBLE</li></ul>") == "VISIBLE"


DOCUMENT_HIDDEN = [f"<body>{S}<body hidden>", f"<p>{S}</p><html hidden>", f"<body>{S}</body><body style='display:none'>",
                   f"<style>body{{display:none}}</style><p>{S}", f"<style>html{{opacity:0}}</style><p>{S}</p>", f"<style>*{{display:none}}</style>{S}",
                   f"<p>{S}</p><html aria-hidden=true>", f"<p>{S}</p><body inert>", f"<style>.x{{display:none}}</style><p>{S}</p><body class=x>",
                   f"<style>BODY{{visibility:hidden}}</style>{S}", f"<html><body>{S}</body></html><html hidden>"]


@pytest.mark.parametrize("html", DOCUMENT_HIDDEN)
def test_a_hiding_html_or_body_tag_or_rule_anywhere_hides_the_whole_document(html):
    assert strip_hidden(html) == "" and strip_hidden(html, markdown=True) == ""
    assert body_of(html) == ""


def test_html_and_body_tags_that_do_not_hide_leave_the_text_visible():
    assert strip_hidden("<html lang=en><body class=x><p>VISIBLE</p></body></html>") == "VISIBLE"
    assert strip_hidden("<style>p{color:red}</style><body>VISIBLE</body><body class=y>") == "VISIBLE"
    assert strip_hidden("<style>.x{display:none}</style><body class=y>VISIBLE") == "VISIBLE"


NEVER_RENDERED = [f"<div><template shadowrootmode=open><p>ok</p></template>{S}</div>", f"<div>{S}<template shadowrootmode=closed></template></div>",
                  f"<progress>{S}</progress>", f"<meter value=1>{S}</meter>", f"<select>{S}<option>ok", f"<select><option>{S}</option></select>",
                  f"<p><span><template shadowrootmode=open></template>{S}</span></p>", f"<template shadowroot=open></template>{S}"]


@pytest.mark.parametrize("html", NEVER_RENDERED)
def test_fallback_content_select_text_and_unslotted_shadow_host_children_are_not_shown(html):
    gone(html)


def test_a_declarative_shadow_root_hides_only_its_host_and_a_plain_template_hides_only_itself():
    assert strip_hidden(f"<div>{S}<template shadowrootmode=open></template>{S}</div>VISIBLE") == "VISIBLE"
    assert strip_hidden(f"<div><template>t</template>VISIBLE</div>") == "VISIBLE"
    assert strip_hidden(f"<section>VISIBLE</section><div><template shadowrootmode=open></template>{S}</div>") == "VISIBLE"
    assert strip_hidden(f"VISIBLE<template shadowrootmode=open></template>{S}") == ""            # the host is the document: all of it


@pytest.mark.parametrize("markdown", [False, True])
def test_a_script_closer_followed_by_a_vertical_tab_or_other_non_html_space_does_not_close_the_double_escaped_script(markdown):
    for sep in ("\x0b", "\x1c", "\x85", "\xa0", " "):
        assert S not in strip_hidden(f"<script><!--<script></script{sep}</script>{S}</script>VISIBLE", markdown=markdown), repr(sep)
    assert strip_hidden(f"<script><!--<script></script\t</script>{S}</script>VISIBLE", markdown=markdown) == "VISIBLE"


MD_WORLDS = {
    "atx": f"para `\n# head <script> `\n{S}", "quote": f"para `\n> quote <script> `\n{S}", "list": f"para `\n- item <script> `\n{S}",
    "star": f"para `\n***\nx <script> `\n{S}", "plus": f"para `\n+ x <script> `\n{S}", "ordered": f"para `\n1. x <script> `\n{S}",
    "escaped-first": f"\\``x` <script> `\n{S}", "escaped-lone": f"`a\\` <script> `\n{S}",
    "quoted-lt": f'Hi <a title="<" data-x="`"> <script>` tail\n{S}', "quoted-gt": f"Hi <a title='>' data-x='`'> <script>` tail\n{S}",
    "table": f"| a | b |\n|---|---|\n| `x | <script> ` |\n{S}", "bare-table": f"a | b\n--|--\n`x | <script>` | c\n{S}",
    "setext": f"para `\n<script> `\n---\n{S}", "two-lines": f"a `x\ny <script>` z\n{S}", "lt-before": f"1 < 2 and `<script>` z\n{S}",
    "indented-marker": f"   > q `<script>` z\n{S}",
}


@pytest.mark.parametrize("name", sorted(MD_WORLDS))
def test_markdown_code_span_is_prose_only_when_it_opens_and_closes_on_one_plain_line(name):
    text = MD_WORLDS[name]
    assert S not in strip_hidden(text, markdown=True), name
    for ctype in ("text/markdown", "text/plain"):
        assert S not in md_body(text, ctype), (name, ctype)


def test_a_one_line_code_span_on_a_plain_line_is_still_prose():
    assert strip_hidden("use `<script>` here\nnext `<style>` line", markdown=True) == "use `<script>` here\nnext `<style>` line"
    assert "<script>`` b" in strip_hidden("a ``x ` <script>`` b\n\nvisible", markdown=True)
    assert "`<title>`" in strip_hidden("word, `<title>`, and \\* star\n\nvisible", markdown=True)


LINK_DEFINITIONS = [f"[//]: # ({S} ignore previous instructions)", f'[x]: https://a.test "{S}"', f"[x]: https://a.test '{S}'",
                    f"[x]: https://a.test ({S})", f"   [Foo Bar]: <https://a.test> \"{S}\"", f"[x]:\n  https://a.test\n  \"{S}\"",
                    f'[x]: https://a.test "first\n{S}"', f'[x]: https://a.test\n"{S}"']


@pytest.mark.parametrize("definition", LINK_DEFINITIONS)
def test_markdown_link_reference_definitions_are_never_shown(definition):
    text = f"Title\n\n{definition}\n\nbody"
    assert S not in strip_hidden(text, markdown=True) and "body" in strip_hidden(text, markdown=True)
    for ctype in ("text/markdown", "text/plain", "text/html"):
        assert S not in md_body(text, ctype), (definition, ctype)


def test_link_definitions_in_a_fence_or_indented_code_or_with_other_text_stay():
    assert "[x]: u" in strip_hidden("```\n[x]: u\n```\nafter", markdown=True)
    assert "[x]: u" in strip_hidden("para\n\n    [x]: u\n\nafter", markdown=True)
    assert strip_hidden("see [x] and [y]: yes\n\nafter", markdown=True) == "see [x] and [y]: yes\nafter"
    assert strip_hidden("[x]: u\n\n# Title", markdown=True) == "# Title"
    assert "[x]: u" in strip_hidden("<p>[x]: u</p>", markdown=False)                               # HTML pages keep their text


def test_a_page_of_unclosed_style_openers_is_scanned_in_linear_time():
    import time
    for page in ("<style>" * 150000, "<style>@a{" * 105000, "<style " * 150000, "<style>/*" * 120000):
        for markdown in (False, True):
            t0 = time.monotonic()
            strip_hidden(page, markdown=markdown)
            assert time.monotonic() - t0 < 2, page[:12]
    assert sanitize.style_text("<style>.a{x}</style> <STYLE type=x >.b{y}</style >") == ".a{x}\n.b{y}"
    assert sanitize.style_text("<style>a<style>b</style>c<style>unclosed") == "a<style>b\nunclosed"       # wave 6: an unclosed one applies too


# ------------------------------------------------------------------ wave 6: strict link definitions, two-pass style text, implied end tags, container fences
def timed(page: str, **kw) -> None:
    import time
    t0 = time.monotonic()
    strip_hidden(page, **kw)
    assert time.monotonic() - t0 < 2, page[:24]


DEF_LEAKS = {
    "trailing-script": "[x]: https://a.test <script>\nLEAK\n</script>\nafter", "title-then-div": '[x]: https://a.test "t" <div hidden>\nLEAK\n</div>',
    "comment-dest": "[x]: <!--\nLEAK\n-->\nafter", "style-after-dest": "[x]: /u <style>\nLEAK\n</style>",
    "next-line-comment": "[x]:\n<!--\nLEAK1\n\nLEAK2\n-->\nafter", "unclosed-title-comment": '[x]: /u "open\n<!--\nLEAK1\n\nLEAK2\n-->\nafter',
    "next-line-div": "[x]:\n<div hidden>\nLEAK1\n\nLEAK2\n</div>", "title-line-comment": "[x]: /u\n'<!--\nLEAK1\n\nLEAK2\n-->",
    "block-after-dest": "[x]: /u\n<div hidden>\nLEAK1\n\nLEAK2\n</div>", "title-spans-heading": '[x]: /u "a\n# b\n<script>\nLEAK\n"',
    "dest-with-lt": "[x]: /u<script>\nLEAK\n</script>", "in-a-paragraph": 'para\n[x]: <script> "t"\nLEAK\n</script>',
    "label-only": "[x]:\n\n<script>\nLEAK", "escaped-title-end": '[x]: /u "a\\"\n<script>\nLEAK',
}


@pytest.mark.parametrize("name", sorted(DEF_LEAKS))
def test_a_line_is_dropped_as_a_link_definition_only_when_it_is_a_strictly_valid_one(name):
    text = DEF_LEAKS[name]
    assert "LEAK" not in strip_hidden(text, markdown=True), name
    for ctype in ("text/markdown", "text/plain"):
        assert "LEAK" not in md_body(text, ctype), (name, ctype)


def test_strictly_valid_definitions_are_dropped_and_the_text_after_them_stays():
    for text in ('[x]: <script> "t"\nshown', '[x]: https://a.test "<div hidden>"\nshown', "[x]: <!-- a> 'q'\nshown", "[x]: /u (<style>)\nshown",
                 '[x]:\n  https://a.test\n  "t"\nshown', "[x]:\n  https://a.test\nshown", '[x]: /u\n"t"\nshown', '[x]: /u\n"t\nmore"\nshown',
                 '[x]: /u\r\n"t"\r\nshown', "[x]: /u(a)\nshown", "# H\n[x]: /u\nshown", "[x]: /u\n[y]: /v\nshown", '[x]: /u\n"never closed\nshown'):
        assert strip_hidden(text, markdown=True).splitlines()[-1] == "shown", text
    assert strip_hidden('[x]: /u\n"never closed\nshown', markdown=True) == '"never closed\nshown'      # a title that is not closed is not one
    assert strip_hidden('[x]:\n  https://a.test\n  "t"\nok', markdown=True) == "ok"
    assert strip_hidden("# H\n[x]: /u\nshown", markdown=True) == "# H\nshown"
    assert strip_hidden("para\n[x]: /u\nok", markdown=True) == "para\n[x]: /u\nok"                      # a definition cannot interrupt a paragraph
    assert strip_hidden("[x]: /u 'a\nb\nc\nd'\nok", markdown=True) != "ok"                               # a title spans at most three lines


def test_a_definition_scan_finds_only_whole_definitions():
    from ace_playbook.compile.watch import mdrules
    assert mdrules.scan('[x]: /u "t"\nbody')[1] == [(0, 12)]
    assert mdrules.scan("[x]:\n  /u\n  'q'\nbody")[1] == [(0, 16)]
    assert mdrules.scan("[x]: /u <script>\nbody")[1] == [] and mdrules.scan("[x]:\n<div>\n")[1] == []


def test_a_page_of_failing_definitions_is_scanned_in_linear_time():
    for page in ("[a]: b '\n" * 100000, '[a]: b "\n[a]: b (\n' * 50000, "[a]:\n" * 150000, "[a]: <" * 150000, "[a]: (" * 150000,
                 "[a]: " + "(" * 500000, "[" + "a" * 500000, "[a]: b 'c\n\n" * 90000):
        timed(page, markdown=True)


HTML_DEFS = {
    "div-hidden": "<p>i</p>\n\n[x]: /u <div hidden>\nLEAK\n</div>\n", "comment": "<p>i</p>\n\n[a]: b <!--\nLEAK\n-->\n",
    "open-tag": "<p>i</p>\n\n<div\n\n[x]: hidden\n>\nLEAK</div>\n", "script": "<p>i</p>\n\n[a]: b <script>\nLEAK\n</script>ok\n",
    "style": "<p>i</p>\n\n[a]: b <style>\n.h{display:none}</style>\n<p class=h>LEAK</p>\n",
}


@pytest.mark.parametrize("name", sorted(HTML_DEFS))
def test_definitions_are_dropped_from_the_visible_text_of_an_html_page_never_from_its_raw_markup(name):
    assert "LEAK" not in strip_hidden(HTML_DEFS[name], definitions=True), name
    assert "LEAK" not in md_body(HTML_DEFS[name], "text/html"), name


def test_an_html_page_for_a_markdown_source_loses_its_visible_definition_lines_only():
    page = '<p>i</p>\n\n<p>[x]: https://a.test "SECRETTEXT"</p>\n<p>ok</p><p>see [y]: z</p>'
    assert strip_hidden(page, definitions=True) == "i\nok\nsee [y]: z" and md_body(page, "text/html") == "i\nok\nsee [y]: z"
    assert strip_hidden(page) == 'i\n[x]: https://a.test "SECRETTEXT"\nok\nsee [y]: z'
    assert strip_hidden("<p>i</p>\n\n[x]: /u <div hidden>\nLEAK\n</div>\n", definitions=True) == "i"             # no edge newline is left behind


FAKE_STYLES = {
    "F4-script-string": '<script>x="<style>"</script><style>.h{display:none}</style><p class=h>LEAK</p>',
    "F5-comment": "<!-- <style> --><style>body{display:none}</style><p>LEAK</p>",
    "F6-attribute": '<p title="<style>">ok</p><style>#x{display:none}</style><p id=x>LEAK</p>',
    "F7-custom-element": "<style-x>a</style-x><style>.h{display:none}</style><p class=h>LEAK</p>",
    "F8-textarea": "<textarea><style</textarea><style>.h{display:none}</style><p class=h>LEAK</p>",
    "title": "<title><style></title><style>.h{display:none}</style><p class=h>LEAK</p>",
    "double-escaped": "<script><!--<script></script><style></script><style>.h{display:none}</style><p class=h>LEAK</p>",
    "unclosed-style-last": "<p class=h>LEAK</p><style>.h{display:none}", "slash-style": "<style/>.h{display:none}</style><p class=h>LEAK</p>",
    "upper-case": "<STYLE>.h{display:none}</STYLE><p class=h>LEAK</p>", "legacy-comment-tokens": "<style><!-- .h{display:none} --></style><p class=h>LEAK</p>",
}


@pytest.mark.parametrize("name", sorted(FAKE_STYLES))
@pytest.mark.parametrize("markdown", [False, True])
def test_style_rules_come_from_the_real_style_elements_of_the_tokenizer(name, markdown):
    assert "LEAK" not in strip_hidden(FAKE_STYLES[name], markdown=markdown), name
    assert "LEAK" not in body_of(FAKE_STYLES[name]), name


def test_style_text_follows_the_tokenizer():
    assert sanitize.style_text('<script>x="<style>"</script><style>.a{x}</style>') == ".a{x}"
    assert sanitize.style_text("<style-x>a</style-x><textarea><style></textarea><!-- <style>b</style> -->") == ""
    assert sanitize.style_text("<style>.a{x}</style><p><style>.b{y}</style>") == ".a{x}\n.b{y}"
    assert sanitize.style_text("<style>a<style>b</style>c<style>unclosed") == "a<style>b\nunclosed"           # a browser applies an unclosed one too


def test_both_parser_passes_are_linear_on_a_megabyte_of_markup():
    for page in ("<style>x</style>" * 65000, "<p>" * 350000, "<style>" * 150000, '<script>"<style>"</script>' * 40000, "<!--<style>-->" * 70000,
                 "<textarea><style></textarea>" * 35000, "<style>@a{" * 105000, "<b>" * 400 + "<li></li>" * 100000, "<li>a" * 200000,
                 "<dt>a<dd>b" * 100000, "<p><table><td>" + "<div></div>" * 90000):
        for markdown in (False, True):
            timed(page, markdown=markdown)


IMPLIED = {
    "F1-div": "<p>a<div hidden></p>LEAK</div>", "F2-ul": "<p>a<ul hidden></p>LEAK</ul>", "F3-dt": "<dl><dd>a<dt hidden>b</dd>LEAK</dt></dl>",
    "dd-by-dd": "<dl><dt>a<dd hidden>b</dt>LEAK</dd></dl>", "p-by-h2": "<p>a<h2 hidden></p>LEAK</h2>",
    "p-by-table": "<p>a<table hidden></p>LEAK</table>", "p-by-pre": "<p>a<pre hidden></p>LEAK</pre>",
    "li-closes-p": "<ul><li><p>a<li hidden>b</p>LEAK</li></ul>", "span-between": "<p>a<span>b<div hidden></p>LEAK</div>",
    "p-in-hidden-format": "<p><b hidden>x<div>LEAK</div></b>", "p-by-dd": "<dl><dd><p>a<dd hidden>b</p>LEAK</dd></dl>",
}


@pytest.mark.parametrize("name", sorted(IMPLIED))
@pytest.mark.parametrize("markdown", [False, True])
def test_an_end_tag_for_an_element_the_browser_closed_implicitly_pops_nothing(name, markdown):
    assert "LEAK" not in strip_hidden(IMPLIED[name], markdown=markdown), name
    assert "LEAK" not in body_of(IMPLIED[name]), name


def test_implied_end_tags_also_reach_the_markdown_heading_through_fetch():
    page = "<p>a<div hidden></p><h1>LEAK heading</h1></div>"
    item = fetch(URL, expect=("text/html",), store={}, kind="md", now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(page.encode(), "text/html")}))
    assert "LEAK" not in str(item["data"]) and item["body"] == "a"


@pytest.mark.parametrize("html,shown", [
    ("<p>a<span hidden>b</p>LEAK", "a\nLEAK"), ("<ul><li>a<li hidden>b</ul>LEAK", "a\nLEAK"), ("<p hidden>a<div>VISIBLE</div>", "VISIBLE"),
    ("<ul><li hidden>a<li>VISIBLE</ul>", "VISIBLE"), ("<dl><dt hidden>a<dd>VISIBLE</dl>", "VISIBLE"), ("<p>a<div>b</div>c", "a\nb\nc"),
    ("<p>a<td><div hidden></p>x</div>VISIBLE", "a\nVISIBLE"), ("<p>a<button><div hidden></p>x</div>VISIBLE", "a\nVISIBLE"),
    ("<li hidden><ul><li>x</li></ul>y</li>VISIBLE", "VISIBLE"), ("<ul><li>a<ul><li>b</ul></ul>", "a\nb"), ("<p>a<hr>b", "a\nb"),
    ("<dl><dd>a<li hidden>b</dd>LEAK</dl>", "a\nLEAK")])
def test_elements_the_browser_does_not_close_stay_open_and_the_ones_it_closes_end_their_hiding(html, shown):
    assert strip_hidden(html) == shown, html


FENCE_LEAKS = {"item-fence": "- item\n\n  ```\nx\n<script>\nLEAK\n", "item-fence-inline": "- a\n  ```\n\nx <script>\nLEAK\n",
               "ordered": "1. a\n\n   ~~~\n   x\ny <style>\nLEAK\n", "one-space": "- a\n ```\nx\n<script>\nLEAK\n"}


@pytest.mark.parametrize("name", sorted(FENCE_LEAKS))
def test_a_fence_inside_a_container_is_never_trusted_as_code(name):
    text = FENCE_LEAKS[name]
    assert "LEAK" not in strip_hidden(text, markdown=True), name
    for ctype in ("text/markdown", "text/plain"):
        assert "LEAK" not in md_body(text, ctype), (name, ctype)


def test_only_a_fence_that_starts_at_the_left_margin_makes_its_openers_prose():
    assert "<script>" in strip_hidden("```\n<script>\nLEAK\n", markdown=True)
    assert "<script>" in strip_hidden("~~~html\n<script>\n~~~\nafter\n", markdown=True)
    assert "after" in strip_hidden("~~~\n<script>\n~~~\nafter", markdown=True)
    assert "<script>" not in strip_hidden("  ```\n<script>\nLEAK\n  ```\n", markdown=True)
    body = "- item\n\n  ```\nx\n<script>\n# LEAK heading\n"
    item = fetch(URL, expect=("text/markdown",), store={}, kind="md", now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(body.encode(), "text/markdown")}))
    assert "LEAK" not in str(item["data"]) and "LEAK" not in item["body"]


def test_a_stylesheet_is_scanned_in_linear_time_whatever_its_closed_or_unclosed_content():
    for sheet in ("x" * 1000000, "@" * 1000000, ";" * 1000000, "a;" * 500000, "{}" * 250000, ".a{display:none}" * 60000, "@a;" * 300000,
                  "@a{}" * 200000, "} " * 400000, "/*" * 400000, "<!--" * 250000, "a{b" * 300000, "@" + "{" * 500 + "}" * 500):
        timed(f"<style>{sheet}</style><p>ok", markdown=False)
    assert strip_hidden("<style>" + "{" * 600 + "</style><p>x") == "x"                                 # blocks past the nesting cap are skipped
    assert cssstyle.css_rules("@a{" * 100000 + ".h{display:none}") == (frozenset(), frozenset(), frozenset())
    assert cssstyle.css_rules("{" * 600 + "}" * 600 + ".h{display:none}")[0] == {"h"}


@pytest.mark.parametrize("css,hidden", [
    (".h{display:none}", True), ("@media print{.h{display:none}}", False), ("@import url(x);.h{display:none}", True),
    ("@media x{.a{b:c}} .h{display:none}", True), (".h{display:none; @media print{color:red}}", True), (".h{.b{color:red}}", False),
    ("a{b{c}} .h{display:none}", True), ("@media x{ .h{display:none}", True), ("}} .h{display:none}", True), (".h{display:none", False),
    ("@a{@b{.h{display:none}}} .h2{x:y}", False), ("<!-- .h{display:none} -->", True), ("x;.h{display:none}", False)])
def test_stylesheet_rules_read_in_one_pass_match_the_old_rule_reader(css, hidden):
    assert ("h" in cssstyle.css_rules(css)[0]) is hidden, css
