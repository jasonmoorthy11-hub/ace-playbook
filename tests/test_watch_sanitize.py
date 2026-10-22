"""`sanitize.style_hidden` and `strip_hidden`: every spelling of "hidden" that a browser honours must keep its text out of the item body.
Each case is also pushed through `fetch`, so the text is checked in the body the rest of the watcher sees."""

import pytest

from ace_playbook.compile.watch import sanitize
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


@pytest.mark.parametrize("fence", ["```", "~~~", "````", "   ```"])
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
