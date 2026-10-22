"""The HTML5 tree builder of the sanitizer (`treebuild`, `treemodes`, `treestyle`, `treewalk`): the six Opus wave-7 leaks, the wave-7 minor, scope
walks, foster parenting, `</template>`, frameset, self-closed integration points, SVG styles under CDATA and the one-place byte order mark rule.
Every world is pushed through `strip_hidden` AND through `fetch` with a patched opener, and each has a VISIBLE control: a near-identical page whose
text a browser shows must still show it."""

import pytest

from ace_playbook.compile.watch import stylepass
from ace_playbook.compile.watch.fetch import fetch, strip_hidden

from .test_watch_sanitize import URL, body_of
from .test_watch_sanitize_waves import leak_free
from .watch_support import Opener, Resp

BOM = "﻿"
RAW_BOM = b"\xef\xbb\xbf"


def shows(html: str, expected: str) -> None:
    """A browser shows exactly `expected` of this page: so do the direct call and the fetch body."""
    assert strip_hidden(html) == expected, html
    assert body_of(html) == expected, html


def bytes_body(raw: bytes) -> str:
    item = fetch(URL, expect=("text/html",), store={}, now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(raw, "text/html")}))
    return item["body"]


# ------------------------------------------------------------------ finding 1: the byte order mark is stripped in exactly one place
def test_two_byte_order_marks_make_the_page_quirks_so_the_table_stays_in_the_hidden_paragraph():
    page = "<!DOCTYPE html><p hidden><table>LEAK"
    assert bytes_body(RAW_BOM + RAW_BOM + page.encode()) == ""                      # the decoder strips one; the second is a character token
    assert strip_hidden(BOM + page) == "" and strip_hidden(BOM + BOM + page) == ""
    assert bytes_body(RAW_BOM + page.encode()) == "LEAK" and strip_hidden(page) == "LEAK"             # control: one BOM, standards mode


def test_a_remaining_byte_order_mark_is_a_character_token_not_whitespace():
    assert strip_hidden(BOM + "<!DOCTYPE html><p>VIS</p>") == "VIS"                # quirks mode shows plain text all the same
    assert bytes_body(RAW_BOM + RAW_BOM + b"<!DOCTYPE html><p>VIS</p>") == "VIS"
    assert strip_hidden(" \n" + "<!DOCTYPE html><p hidden><table>VIS") == "VIS"      # blanks before the doctype are ignored: standards


# ------------------------------------------------------------------ finding 2: an SVG style wrapped in CDATA
SVG_CDATA = "<svg><style><![CDATA[.h{display:none}]]></style></svg><p class=h>LEAK</p><p>OK</p>"


@pytest.mark.parametrize("sheet", ["<![CDATA[.h{display:none}]]>", "<![CDATA[.h{display:none}]]><!-- c -->", "<!-- c --><![CDATA[.h{display:none}]]>",
                                   ".h{display:none}<!-- c -->", ".a{x:y}<!-- c -->.h{display:none}", "<![CDATA[.a{x:y}]]><![CDATA[.h{display:none}]]>"])
def test_the_text_of_an_svg_style_is_its_character_data_cdata_included(sheet):
    page = f"<svg><style>{sheet}</style></svg><p class=h>LEAK</p><p>OK</p>"
    assert leak_free(page), sheet
    assert strip_hidden(page) == "OK" and body_of(page) == "OK"
    assert "h" in stylepass.style_rules(page)[0] and stylepass.style_sheets(page)[0].count(".h{display:none}") == 1


def test_an_svg_style_cdata_sheet_that_hides_another_class_leaves_the_text_visible():
    shows("<svg><style><![CDATA[.g{display:none}]]></style></svg><p class=h>VIS</p>", "VIS")
    assert stylepass.style_sheets(SVG_CDATA) == [".h{display:none}"]


# ------------------------------------------------------------------ finding 3: the nearest-name end tag approximation
def test_an_end_tag_that_a_scope_marker_stops_leaves_an_svg_style_with_its_character_references():
    page = "<div><object><svg><g></div><style>.&#x68;{display:none}</style></g></svg></object></div><p class=h>LEAK</p>"
    assert leak_free(page)
    shows(page.replace("<p class=h>LEAK</p>", "<p class=h>LEAK</p><p>OK</p>"), "OK")
    shows(page.replace("&#x68;", "&#x67;").replace("LEAK", "VIS"), "VIS")                               # control: the rule names another class


def test_the_comment_inside_an_svg_style_is_not_part_of_its_text():
    page = "<div><object><svg><g></div><style>.a{}<!-- x -->.h{display:none}</style></g></svg></object></div><p class=h>LEAK</p>"
    assert leak_free(page)
    shows(page.replace("LEAK", "VIS").replace(".h{", ".g{"), "VIS")


@pytest.mark.parametrize("sheet", ["<![CDATA[.h{display:none}]]>", ".&#x68;{display:none}", ".a{}<!-- x -->.h{display:none}", ".a{}<b>.h{display:none}"])
def test_the_scan_reads_an_html_style_in_the_variants_too_and_over_hides(sheet):
    page = f"<style>{sheet}</style><p class=h>LEAK</p>"                   # a browser applies none of these to an HTML style; the safety net unions them
    assert strip_hidden(page) == "" and body_of(page) == "" and "h" in stylepass.style_rules(page)[0]
    assert stylepass.style_sheets(page) == [sheet]                           # the tree's sheet is the exact text of the element


# ------------------------------------------------------------------ finding 4: annotation-xml is a special element
def test_the_end_tag_of_a_span_is_stopped_by_annotation_xml_so_the_text_stays_hidden():
    assert leak_free('<math><annotation-xml encoding="text/html"><span hidden></math>LEAK')
    assert leak_free('<math><annotation-xml><span hidden></math>LEAK') and leak_free("<math><mi><span hidden></math>LEAK")
    shows('<math><annotation-xml encoding="text/html"><span hidden></span></math>VIS', "VIS")        # closed in order: nothing stays hidden
    shows("<math><mi><span hidden></span></mi></math>VIS", "VIS")
    shows('<span hidden><math><annotation-xml encoding="text/html"></span></math>VIS', "")           # the same end tag: ignored again, to the end of input


# ------------------------------------------------------------------ finding 5: a self-closed foreign start tag is popped
SELF_CLOSED = {
    "desc": ("<svg><desc/>", "</desc></svg>"), "title": ("<svg><title/>", "</title></svg>"),
    "foreignObject": ("<svg><foreignObject/>", "</foreignObject></svg>"),
    "mi": ("<math><mi/>", "</mi></math>"), "mo": ("<math><mo/>", "</mo></math>"), "mn": ("<math><mn/>", "</mn></math>"),
    "ms": ("<math><ms/>", "</ms></math>"), "mtext": ("<math><mtext/>", "</mtext></math>"),
    "annotation-xml": ('<math><annotation-xml encoding="text/html"/>', "</annotation-xml></math>"),
}


@pytest.mark.parametrize("name", sorted(SELF_CLOSED))
@pytest.mark.parametrize("raw", ["xmp", "style", "textarea", "iframe"])
def test_a_self_closed_integration_point_hands_no_html_rules_to_the_tags_after_it(name, raw):
    head, tail = SELF_CLOSED[name]
    assert leak_free(f"{head}<{raw}><p hidden></{raw}>{tail}LEAK"), (name, raw)
    shows(f"{head.replace('/>', '>')}<{raw}><p hidden></{raw}>{tail}VIS", "VIS")                       # control: a real integration point, raw text


def test_the_wave_7_self_closed_worlds():
    for page in ("<svg><desc/><xmp><p hidden></xmp></desc></svg>LEAK", "<math><mi/><style><p hidden></style></mi></math>LEAK",
                 "<svg><foreignObject/><textarea><p hidden></textarea></foreignObject></svg>LEAK"):
        assert leak_free(page), page
    shows("<p hidden><svg/><div>VIS", "VIS")                                                         # the svg element itself is popped too
    shows("<svg><g/><desc>x</desc></svg>VIS", "VIS")


# ------------------------------------------------------------------ finding 6: frameset
def test_a_frameset_replaces_the_body_so_nothing_after_it_renders():
    assert leak_free("<frameset></frameset>LEAK") and leak_free("<frameset><frame>LEAK")
    assert leak_free("<html><head></head><frameset cols=1><frame></frameset>LEAK") and leak_free("<div></div><frameset></frameset>LEAK")
    assert leak_free("<p></p><frameset>LEAK") and leak_free("<!DOCTYPE html><frameset>LEAK") and strip_hidden("VIS<frameset>") != ""
    shows("<body><frameset></frameset>VIS", "VIS")                                                   # control: an explicit body makes the frameset ignored
    shows("<p>x</p><frameset></frameset>VIS", "x\nVIS")                                              # text before it: not ok, the frameset is ignored
    shows("<frameset></frameset>", "")


# ------------------------------------------------------------------ finding 7: </template> pops to the template whatever stands between
@pytest.mark.parametrize("inner", ["<svg><desc><span hidden>", "<div hidden><table>", "<p hidden><object>", "<span hidden><td>", "<b hidden><select>"])
def test_a_template_end_tag_pops_to_the_template_regardless_of_scope(inner):
    shows(f"<template>{inner}</template>VIS", "VIS")
    shows(f"<div><template>{inner}</template>VIS</div>", "VIS")
    assert leak_free(f"<template>{inner}LEAK")                                                       # control: no end tag, hidden to the end


# ------------------------------------------------------------------ scope walks
@pytest.mark.parametrize("opener,closer", [("<object>", "</object>"), ("<marquee>", "</marquee>"), ("<template>", "</template>"), ("<applet>", "</applet>"),
                                           ("<table><tr><td>", "</td></tr></table>"), ("<table><caption>", "</caption></table>"),
                                           ("<table><tr><th>", "</th></tr></table>")])
def test_an_end_tag_is_ignored_when_a_scope_marker_stands_between(opener, closer):
    assert leak_free(f"<div hidden>{opener}</div>LEAK"), opener
    assert leak_free(f"<p hidden>{opener}</p>LEAK") or opener.startswith("<table"), opener
    shows(f"<div hidden>{opener}{closer}</div>VIS", "VIS")                                           # control: closed in order, the div ends


def test_a_button_and_a_list_scope_stop_their_end_tags():
    assert leak_free("<p hidden><button></p>LEAK") and leak_free("<li hidden><ul></li>LEAK")
    shows("<p hidden><button></button></p>VIS", "VIS")
    shows("<ul><li hidden><ul></ul></li></ul>VIS", "VIS")


# ------------------------------------------------------------------ foster parenting
def test_text_and_elements_in_a_table_are_foster_parented_before_it():
    assert leak_free("<p hidden><table>LEAK") and leak_free("<div hidden><table><tr>LEAK</tr></table>")
    assert leak_free("<p hidden><table><tr><td>x</td></tr>LEAK</table>")
    shows("<!DOCTYPE html><p hidden><table>VIS</table>", "VIS")                                      # control: standards mode, the table closes the p
    shows("<div hidden><table>LEAK</table></div>VIS", "VIS")
    shows("<table><tr><td>A</td></tr>B</table>", "B\nA")                                             # B is placed before the table
    shows("<table><tr><td>A</td></tr><span>B</span></table>", "B\nA")
    shows("<table> <tr> <td>A</td> </tr> </table>", "A")


def test_a_table_start_tag_closes_a_paragraph_only_in_standards_mode():
    assert leak_free("<p hidden><table><tr><td>LEAK</td></tr></table>") and leak_free(
        "<!DOCTYPE html PUBLIC \"-//W3C//DTD HTML 3.2//EN\"><p hidden><table>LEAK")
    shows("<!DOCTYPE html><p hidden><table><tr><td>VIS</td></tr></table>", "VIS")
    shows("<!DOCTYPE html><!DOCTYPE html><p hidden><table>VIS", "VIS")                               # a second doctype is ignored: the first decides


# ------------------------------------------------------------------ frameset-ok, forms, formatting: the builder's own state
def test_the_first_doctype_decides_the_quirks_mode():
    shows("<!DOCTYPE html SYSTEM \"about:legacy-compat\"><p hidden><table>VIS", "VIS")
    assert leak_free("<p hidden><!DOCTYPE html><table>LEAK") and leak_free("x<!DOCTYPE html><p hidden><table>LEAK")


def test_a_form_end_tag_removes_the_form_but_not_what_is_open_inside_it():
    assert leak_free("<form hidden><div>x</form>LEAK</div>") and leak_free("<form hidden><form>LEAK</form></form>")       # kept open: over-hides, never shows
    shows("<form hidden>x</form>VIS", "VIS")


def test_a_hidden_formatting_element_is_reopened_after_an_outer_end_tag():
    assert leak_free("<p><b hidden>x<div>LEAK</div></b>") and leak_free("<b><i hidden></b>LEAK</i>")
    assert leak_free("<b hidden><div>x</b>LEAK</div>LEAK")
    shows("<b><i></b>VIS</i>", "VIS")


def test_html_and_body_tokens_after_the_end_of_the_body_still_merge_their_attributes():
    assert strip_hidden("<body>LEAK</body><body hidden>") == "" and strip_hidden("<p>LEAK</p></html><html hidden>") == ""
    shows("<body>VIS</body></html>VIS2", "VISVIS2")
    shows("<body>VIS</body><p>VIS2</p>", "VIS\nVIS2")                                               # text after </body> goes to the body


# ------------------------------------------------------------------ wave 15 (Opus wave-8 I1-I6): html and markdown, each with a visible control
def hides(*worlds: str) -> None:
    for html in worlds:
        assert strip_hidden(html) == "" and body_of(html) == "" and strip_hidden(html, markdown=True) == "", html


def shows_md(html: str, expected: str) -> None:
    shows(html, expected)
    assert strip_hidden(html, markdown=True) == expected, html


@pytest.mark.parametrize("html", [
    "<table><tr><td hidden><table></table>LEAK</td></tr></table>", "<table><tr><td hidden><table><tr><td>LEAK",
    "<table><tr><td><span hidden><table></table>LEAK", "<table><tr><td><span hidden><table><tr><td>LEAK",
    "<table><caption hidden><table></table>LEAK</caption></table>", "<table><caption hidden><table><tr><td>LEAK"])
def test_i1_a_table_start_tag_in_a_cell_or_caption_nests_and_does_not_close_the_outer_table(html):
    hides(html)


def test_i1_controls_a_visible_cell_and_the_table_end_tag_in_a_cell():
    shows_md("<table><tr><td><table></table>VIS</td></tr></table>", "VIS")
    shows_md("<table><caption><table></table>VIS</caption></table>", "VIS")
    shows_md("<table><tr><td hidden>x</table>VIS", "VIS")                                    # an end tag still closes the cell and the table
    shows_md("<table><tr>table</tr></table>VIS", "table\nVIS")                               # the characters "table" are text, not a table tag


@pytest.mark.parametrize("html", [
    "<table><object><b hidden></table>LEAK", "<table><marquee><i hidden></table>LEAK", "<table><tbody><applet><u hidden></tbody></table>LEAK",
    "<table><object><b hidden>x<tr><td>y</td></tr></table>LEAK"])
def test_i2_a_marker_that_leaves_the_stack_without_its_own_end_tag_keeps_the_formatting_list(html):
    assert leak_free(html), html                                    # the `y` of the last world is a table cell, visible in a browser too
    assert strip_hidden(html) == ("y" if "<td>y" in html else "")


def test_i2_controls_the_own_end_tag_clears_the_list_to_the_marker():
    shows_md("<table><object><b></table>VIS", "VIS")
    shows_md("<object><b hidden></object>VIS", "VIS")
    shows_md("<marquee><i hidden></marquee>VIS", "VIS")
    shows_md("<b hidden></b><table><tr><td><i hidden></i></td></tr></table>VIS", "VIS")     # the cell still clears its part of the list


@pytest.mark.parametrize("html", [
    '<math><annotation-xml encoding="text/html "><xmp><span hidden></xmp></annotation-xml></math>LEAK',
    '<math><annotation-xml encoding=" text/html"><xmp><span hidden></xmp></annotation-xml></math>LEAK',
    '<math><annotation-xml encoding="application/xhtml+xml\n"><xmp><span hidden></xmp></annotation-xml></math>LEAK'])
def test_i3_the_annotation_xml_encoding_is_compared_untrimmed(html):
    hides(html)


def test_i3_controls_an_exact_encoding_is_an_integration_point_in_any_case():
    for enc in ("text/html", "TEXT/HTML", "Application/XHTML+xml"):
        shows_md(f'<math><annotation-xml encoding="{enc}"><xmp><span hidden></xmp></annotation-xml></math>VIS', "VIS")


@pytest.mark.parametrize("html", [
    "<select><object></select>LEAK", "<select><table></select>LEAK", "<select><marquee></select>LEAK", "<select><math><mi></select>LEAK",
    "<select><svg><desc></select>LEAK"])
def test_i4_an_end_select_needs_the_select_in_scope_in_the_newer_reading(html):
    hides(html)


def test_i4_controls_a_plain_close_and_the_kept_over_hide_of_ignored_end_tags():
    shows_md("<select><option>x</select>VIS", "VIS")
    shows_md("<select><optgroup><option>x</optgroup></select>VIS", "VIS")
    hides("<select><object></object></select>VIS", "<select><table></table></select>VIS")       # kept over-hide: an open select ignores end tags


@pytest.mark.parametrize("html", ["<select><b hidden></select>LEAK", "<select><option><i hidden></select>LEAK", "<select><a hidden></select>LEAK"])
def test_i5_a_formatting_tag_in_the_newer_select_reading_is_pushed_and_reconstructed(html):
    hides(html)


def test_i5_controls_a_visible_formatting_tag_in_the_select():
    shows_md("<select><b></select>VIS", "VIS")
    shows_md("<select><option><i>x</select>VIS", "VIS")


@pytest.mark.parametrize("html", ["<div>\x00</div><frameset></frameset>LEAK", "\x00<frameset>LEAK", "<svg>\x00</svg><frameset>LEAK"])
def test_i6_a_nul_character_leaves_frameset_ok_so_the_frameset_replaces_the_body(html):
    hides(html)


def test_i6_controls_text_after_the_nul_turns_frameset_ok_off():
    shows_md("<div>\x00</div>x<frameset>VIS", "xVIS")
    shows_md("<div>\x00 </div>x<frameset>VIS", "xVIS")
    shows_md("<svg>\x00x</svg><frameset>VIS", "VIS")


# ------------------------------------------------------------------ wave 16: the formatting list is cleared ONCE per cell / caption close / template end
@pytest.mark.parametrize("html", [
    "<template><i hidden><table><tr><td></template>LEAK", "<template><i hidden><table><caption></template>LEAK",
    "<template><i hidden><table><tr><th>x</template>LEAK", "<template><i hidden><template><table><tr><td></template></template>LEAK",
    "<template><i hidden><table><tr><td><select></template>LEAK", "<head><template><i hidden><table><tr><td></template>LEAK"])
def test_w16_a_template_end_clears_the_formatting_list_once_not_once_per_popped_cell(html):
    hides(html)                                                      # the list is [M_t, i, M_td]: only M_td goes, the hidden i is rebuilt


@pytest.mark.parametrize("html,expected", [
    ("<template><i hidden><table><tr><td><object></template>OVER", "OVER"),
    ("<template><i hidden><table><caption>x</caption></table></template>VIS", "VIS"),
    ("<template><i hidden><table><tr><td>x</td></tr></table></template>VIS", "VIS")])
def test_w16_controls_a_cell_or_caption_that_closed_by_itself_clears_its_own_marker_once(html, expected):
    shows_md(html, expected)


# ------------------------------------------------------------------ wave 17: a table tag in a select closes it (opened in a table) or is ignored
@pytest.mark.parametrize("html", [
    "<template><i hidden><table><tr><td><select><td></template>LEAK", "<template><i hidden><table><tr><td><select><th></template>LEAK",
    "<template><i hidden><table><tr><td><select><caption></template>LEAK",
    "<p><i hidden><template><select><td></template></p>LEAK", "<p><i hidden><template><select><caption></template></p>LEAK",
    "<p><i hidden><template><select><th></template></p>LEAK"])
def test_w17_a_table_structure_start_tag_in_a_select_pushes_no_cell_marker_outside_the_table_modes(html):
    hides(html)                                                      # in a table the tag closes the select; elsewhere it is ignored as in body


@pytest.mark.parametrize("html", [
    "<i hidden><select><td></select>LEAK", "<select><td><i hidden></select>LEAK", "<i hidden><select><caption></select>LEAK"])
def test_w17_guards_these_worlds_were_hidden_before_and_stay_hidden(html):
    hides(html)


@pytest.mark.parametrize("html", [
    "<template><i hidden><table><tr><td><select><tr></template>LEAK", "<table><tr><td><i hidden><select><td></select></td></tr></table>LEAK"])
def test_w17_a_select_closed_by_a_cell_tag_reads_like_the_same_page_without_the_select(html):
    shows_md(html, "LEAK")                                           # the cell closes (the i goes with it): a browser shows it, like the select-free page
    shows_md(html.replace("<select>", "").replace("</select>", ""), "LEAK")


@pytest.mark.parametrize("html,expected", [
    ("<table><tr><td><select><td>VIS", "VIS"), ("<p><template><select><td></template></p>VIS", "VIS"),
    ("<table><tr><td><select><tr><td>VIS", "VIS"), ("<table><tr><td><select><th>VIS", "VIS")])
def test_w17_controls_a_visible_cell_after_a_select_start_tag_shows_its_text(html, expected):
    shows_md(html, expected)


# ------------------------------------------------------------------ wave 18: a select is "in a table" only when the table lies BELOW it, nothing above hides
@pytest.mark.parametrize("html", [
    "<select><table><td hidden></select>LEAK", "<select><table><tr><td hidden></select>LEAK", "<select><table><caption hidden></select>LEAK",
    "<p><select><table><td hidden></select></p>LEAK"])
def test_w18_a_table_inside_a_select_is_not_a_select_in_a_table_so_its_hidden_cell_hides_what_follows(html):
    hides(html)                                                      # the two readings disagree (the cell hides the rest / the select closes): fail closed


@pytest.mark.parametrize("html", [
    "<i hidden><select><table><td></select>LEAK", "<table><tr><td><select><b hidden><td>VIS", "<table><tr><td><select><b hidden><tr>VIS"])
def test_w18_guards_a_table_inside_a_select_or_a_hiding_element_above_it_keeps_the_rest_hidden(html):
    hides(html)                                                      # the cell tag is ignored (b hides itself), the select stays open: over-hide, fail closed


@pytest.mark.parametrize("html,expected", [
    ("<table><tr><td><select><td>VIS", "VIS"), ("<table><tr><td><select><option>a<td>VIS", "VIS"),
    ("<p><template><select><td></template></p>VIS", "VIS"), ("<table><tr><td><select><b>x<td>VIS", "VIS")])
def test_w18_controls_a_select_opened_in_a_table_is_still_closed_by_a_cell_tag_when_nothing_above_it_hides(html, expected):
    shows_md(html, expected)


def test_w18_a_cell_tag_in_a_select_with_a_template_between_it_and_the_table_is_ignored():
    hides("<table><tr><td hidden><template><select><td></select>LEAK")


# ------------------------------------------------------------------ wave 19: `</template>` closing a select that holds a barrier hides the rest
@pytest.mark.parametrize("html", [
    "<template><i hidden><select><table><td></template>LEAK", "<template><i hidden><select><table><th></template>LEAK",
    "<template><i hidden><select><table><caption></template>LEAK", "<head><template><b hidden><select><table><tr><td></template></head>LEAK"])
def test_w19_a_template_end_closing_a_select_that_holds_a_table_hides_the_rest(html):
    hides(html)                                                      # the newer reading pushes a cell marker the older does not: the lists diverge, fail closed


def test_w19_a_template_end_closing_a_select_that_holds_a_marquee_and_a_row_tag_hides_the_rest():
    hides("<template><i hidden><table><tr><td><select><marquee><tr></template>LEAK")


@pytest.mark.parametrize("html", [
    "<template><i hidden><table><tr><td><select><marquee><td></template>LEAK", "<template><i hidden><table><tr><td><select><object><td></template>LEAK",
    "<template><i hidden><table><tr><td><select><applet><td></template>LEAK", "<template><i hidden><table><caption><select><marquee><td></template>LEAK",
    "<template><i hidden><table><tr><td><select><object></template>LEAK", "<template><i hidden><table><tr><td><select><marquee></select><td></template>LEAK"])
def test_w19_a_template_end_closing_a_select_that_holds_a_marquee_object_or_applet_hides_the_rest(html):
    hides(html)                                                      # the older reading ignores the marquee, the newer keeps its marker: the readings disagree


def test_w19_over_hide_a_template_end_closing_a_select_that_holds_a_table_hides_the_rest_although_a_browser_shows_it():
    hides("<template><i hidden><select><table><tr></template>VIS")   # documented over-hide (LIMITATIONS.md): fail closed, D-128


@pytest.mark.parametrize("html,expected", [
    ("<template><table><tr><td><select><td></template>VIS", "VIS"), ("<template><select><option>a</select></template>VIS", "VIS"),
    ("<p><template><select><td></template></p>VIS", "VIS"), ("<template><select><option>a</template>VIS", "VIS")])
def test_w19_controls_a_template_end_closing_a_select_that_holds_no_barrier_leaves_the_rest_visible(html, expected):
    shows_md(html, expected)


# ------------------------------------------------------------------ wave 20: the template-end poison inspects the whole select region above the template
@pytest.mark.parametrize("html", [
    "<template><i hidden><select><table><tr><td><select></template>LEAK", "<template><i hidden><select><table><tr><th><select></template>LEAK",
    "<template><i hidden><select><table><caption><select></template>LEAK", "<template><i hidden><select><table><td><select></template>LEAK",
    "<template><i hidden><select><marquee><select></template>LEAK", "<template><i hidden><select><object><select></template>LEAK",
    "<template><i hidden><select><applet><select></template>LEAK", "<head><template><b hidden><select><table><tr><td><select></template></head>LEAK"])
def test_w20_a_template_end_closing_an_inner_select_with_a_barrier_between_the_two_selects_hides_the_rest(html):
    hides(html)                                                      # the newer reading's cell marker is cleared once by the template pop: the lists diverge


@pytest.mark.parametrize("html", [
    "<template><i hidden><table><tr><td><select><marquee><select><td></template>LEAK", "<template><i hidden><select><table><tr></template>LEAK",
    "<template><i hidden><select><table><td></template>LEAK"])
def test_w20_guards_the_wave_19_poisons_still_hide_the_rest(html):
    hides(html)


def test_w20_control_two_selects_with_nothing_between_them_show_what_follows_the_template():
    # correct: no marker was pushed after the template's, so both readings clear [marker, i] whole and the reconstructed `i` is gone: LEAK is visible
    shows_md("<template><i hidden><select><select></template>LEAK", "LEAK")


def test_w20_over_hide_two_selects_with_a_bare_table_between_them_hide_what_follows_the_template_although_a_browser_shows_it():
    # a browser shows LEAK (no cell marker), but our stack equals the one with `<tr><td>` between the selects (STRUCT tags are ignored in a select): fail closed
    hides("<template><i hidden><select><table><select></template>LEAK")


# ------------------------------------------------------------------ wave 21: a template in "in body" mode ignores table-structure start tags
@pytest.mark.parametrize("html", [
    "<p><i hidden></p><template><span><td></template>LEAK", "<p><i hidden></p><template><b><td></template>LEAK",
    "<p><i hidden></p><template><img><td></template>LEAK", "<p><i hidden></p><template><div><td></template>LEAK",
    "<p><i hidden></p><template><span></span><td></template>LEAK", "<p><i hidden></p><template><span><th></template>LEAK",
    "<p><i hidden></p><template><span><caption></template>LEAK",
    "<p><i hidden></p><table><tr><td><template><span><td></template></td></tr></table>LEAK",
    "<p><b class=h></p><template><div><td></template>LEAK<style>.h{display:none}</style>"])
def test_w21_a_table_structure_start_tag_in_a_template_in_body_mode_is_ignored_so_the_template_pop_clears_its_own_marker(html):
    # browser: "in template", any other start tag (span): pop the template mode, push "in body"; a td there is ignored; `</template>` clears once: [i]
    hides(html)


def test_w21_control_a_nested_template_has_its_own_mode_so_its_cell_pushes_a_marker_and_each_end_clears_once():
    # [i, M_t1, M_t2, M_td] -> inner end -> [i, M_t1, M_t2] -> outer end -> [i, M_t1]: the last entry is a marker, nothing is rebuilt, LEAK shows (checked
    # in a browser: DOMParser gives `...</template>LEAK`; the brief's expected '' was a miscount). The outer template's "body" flag must not reach the inner one
    shows_md("<p><i hidden></p><template><span><template><td></template></template>LEAK", "LEAK")
    shows_md("<p><i hidden></p><template><template><td></template></template>LEAK", "LEAK")


def test_w21_a_nested_template_in_body_mode_of_its_own_ignores_its_cell_tag_so_the_two_ends_leave_the_hidden_element_to_rebuild():
    hides("<p><i hidden></p><template><span><template><span><td></template></template>LEAK")        # [i, M_t1, M_t2] -> [i]: i is rebuilt (browser: hidden)
    hides("<p><i hidden></p><template><span><template><td></template>LEAK")                         # guard: the outer template is still open, hiding all


@pytest.mark.parametrize("html,expected", [
    ("<p><i hidden></p><template><td></template>VIS", "VIS"), ("<p><i hidden></p><template>x<td></template>VIS", "VIS"),
    ("<template><i hidden><td></template>VIS", "VIS")])
def test_w21_controls_a_cell_tag_before_any_other_start_tag_builds_a_cell_and_the_hidden_element_in_body_mode_is_cleared(html, expected):
    # a td in template mode pushes "in row" and a marker (the list ends [i, M_t]); `<i hidden>` is "any other start tag": body mode, the td is ignored
    shows_md(html, expected)


# ------------------------------------------------------------------ wave 22: each template carries its own insertion mode, set once by its first non-head tag
@pytest.mark.parametrize("html", [
    "<template><tr></tr><i hidden><td></template>LEAK", "<template><td></td><i hidden><td></template>LEAK",
    "<template><caption></caption><i hidden><td></template>LEAK", "<template><tbody></tbody><b hidden><tr><td></template>LEAK",
    "<template><thead></thead><u hidden><th></template>LEAK",
    "<table><tr><td><template><tr></tr><b hidden><td></template>LEAK</td></tr></table>", "<head><template><tr></tr><i hidden><td></template></head>LEAK",
    "<select><template><tr></tr><i hidden><td></template></select>LEAK", "<template><td></td><svg><desc><i hidden></desc></svg><td></template>LEAK",
    "<template><i hidden><template><tr></tr><b hidden><td></template></template>VIS"])
def test_w22_a_template_that_a_table_tag_put_in_a_table_mode_stays_there_so_a_later_cell_tag_still_builds_and_pushes_its_marker(html):
    # browser: the template's mode is set once, by its first start tag that is not an in-head name (`tr`: "in table body"); `i` is "anything else", read by the
    # in-table rules with the template as target (a plain insert, the mode stays); the later td clears back to the table-body context and pushes a marker
    hides(html)


@pytest.mark.parametrize("html", [
    "<p><i hidden></p><template><col><td></template>LEAK", "<p><i hidden></p><template><col><caption></template>LEAK",
    "<p><i hidden></p><template><col><tr><td></template>LEAK", "<p><i hidden></p><template><col><table><td></template>LEAK",
    "<p><i hidden></p><template><col><object></template>LEAK", "<p><i hidden></p><template><col><marquee></template>LEAK"])
def test_w22_a_template_in_column_group_mode_ignores_everything_but_col_so_nothing_pushes_a_marker(html):
    hides(html)            # "in column group", anything else: the current node is the template, not a colgroup, so the token is ignored


@pytest.mark.parametrize("html", [
    "<p><i hidden></p><template><tr></tr><caption></template>LEAK", "<p><i hidden></p><template><tr><caption></template>LEAK",
    "<p><i hidden></p><template><td></td><caption></template>LEAK", "<p><i hidden></p><template><td><caption></template>LEAK",
    "<p><i hidden></p><template><td></td><table><caption></template>LEAK"])
def test_w22_a_table_structure_tag_the_templates_mode_ignores_builds_nothing_so_the_template_pop_clears_its_own_marker(html):
    # table body: caption through thead are ignored when no tbody is in table scope; row: caption through tr when no tr is; a caption in a cell closes the
    # cell and lands in row mode (`intable`, not `intemplate`); the template is a table-scope boundary. The list stays [i, M_t], `</template>` leaves [i]
    hides(html)


@pytest.mark.parametrize("html,expected", [
    ("<p><i hidden></p><template><colgroup><td></template>VIS", "VIS"), ("<p><i hidden></p><template><tr></tr><table><td></template>VIS", "VIS"),
    ("<p><i hidden></p><template><caption>x</template>VIS", "VIS"), ("<p><i hidden></p><template><tbody></tbody><caption></template>LEAK", "LEAK"),
    ("<template><i hidden><td></template>VIS", "VIS"), ("<p><i hidden></p><template><td></td><span><td></template>LEAK", "LEAK"),
    ("<template><tr></tr><i hidden><caption></template>LEAK", "LEAK")])
def test_w22_controls_a_tag_the_mode_keeps_still_builds_and_the_second_cell_in_row_mode_pushes_a_marker(html, expected):
    # a colgroup puts the template in "in table" (a td builds); a bare table is ignored in table body; a first-tag tbody puts it in "in table", where a
    # caption builds; in row, the second td pushes a marker (the wave-21 body flag hid this although a browser shows it). The last world was listed in the
    # Opus wave-12 finding 1 as hidden, but its caption is ignored in table body: the list is [M_t, i] and `</template>` clears both, so LEAK shows
    shows_md(html, expected)
