"""Security finding F2: `Tree.reconstruct` re-opens every closed formatting element as a NEW node at each character token after a block, so about 4 bytes
of input (`<p>a`) could allocate hundreds of nodes. The nodes it creates are counted per document against `htmlrules.MAX_RECONSTRUCT`; past it the build
raises `TooDeep`, the same fail-closed outcome as a stack past MAX_DEPTH (`strip_hidden` raises, `fetch` drops the page as `parse`). Counted by nodes, not time."""

import pytest

from ace_playbook.compile.watch import treebuild
from ace_playbook.compile.watch.fetch import fetch, strip_hidden
from ace_playbook.compile.watch.htmlrules import MAX_DEPTH, MAX_RECONSTRUCT, TooDeep

from .test_watch_sanitize import URL
from .watch_support import Opener, Resp


def count_nodes(monkeypatch) -> list:
    made = []
    real = treebuild.Node
    monkeypatch.setattr(treebuild, "Node", lambda *a, **k: made.append(1) or real(*a, **k))
    return made


def f2_page(distinct: int, paragraphs: int) -> str:
    return "<p>" + "".join(f"<b id={i}>" for i in range(distinct)) + "<p>a" * paragraphs


def test_the_f2_payload_is_cut_off_at_the_budget(monkeypatch):
    made = count_nodes(monkeypatch)
    page = f2_page(500, 1500)                                                        # 500 closed b with distinct attributes, then 1500 paragraphs: 750,000 re-opened
    assert 500 + 3 < MAX_DEPTH and len(page) < 20_000 and 500 * 1500 > MAX_RECONSTRUCT
    with pytest.raises(TooDeep):
        treebuild.build(page)
    assert len(made) <= MAX_RECONSTRUCT + 500 + 1500 + 10, len(made)                # at most the budget, the 500 first openings, a paragraph per 4 bytes
    assert len(made) > MAX_RECONSTRUCT                                               # (and the budget is what stopped it, not the depth)


def test_the_budget_ends_as_a_too_deep_page_does_strip_hidden_raises_and_fetch_drops_the_page_as_parse(monkeypatch):
    monkeypatch.setattr(treebuild, "MAX_RECONSTRUCT", 5_000)
    page = f2_page(100, 300)
    with pytest.raises(TooDeep):                                                     # a ValueError: what fetch already catches for a stack past MAX_DEPTH
        strip_hidden(page)
    deep = "<div>" * (MAX_DEPTH + 1)
    for html in (page, deep):
        item = fetch(URL, expect=("text/html",), store={}, now="2026-10-21T12:00:00Z", opener=Opener({URL: Resp(html.encode(), "text/html")}))
        assert item == {"url": URL, "fetched_at": "2026-10-21T12:00:00Z", "dropped": "parse"}


def test_a_lowered_budget_counts_the_reopened_nodes(monkeypatch):
    monkeypatch.setattr(treebuild, "MAX_RECONSTRUCT", 1_000)
    made = count_nodes(monkeypatch)
    with pytest.raises(TooDeep):
        treebuild.build(f2_page(50, 100))                                            # 50 per paragraph: the 21st paragraph passes 1,000
    assert 1_000 < len(made) <= 1_000 + 50 + 100 + 10, len(made)             # the budget, the 50 first openings, a paragraph each


# ------------------------------------------------------------------ the legitimate pages at the 1 MiB fetch cap still parse
def test_150000_sibling_elements_still_parse(monkeypatch):
    made = count_nodes(monkeypatch)
    page = "<p>x" * 150_000                                                           # 600 KB: each `<p>` closes the one before, no formatting element
    tree = treebuild.build(page)
    assert len(made) >= 150_000 and len(tree.root.kids) == 1
    assert strip_hidden("<div>" + "<i>x</i>" * 130_000 + "</div>").count("x") == 130_000       # 1 MB of closed formatting elements: never re-opened


def test_three_misnested_formatting_elements_over_20000_paragraphs_still_parse(monkeypatch):
    made = count_nodes(monkeypatch)
    page = "<p><b><i><u>x" + "<p>a" * 20_000                                          # the case `reconstruct` exists for: 60,000 re-opened nodes
    out = strip_hidden(page)
    assert out.split() == ["x"] + ["a"] * 20_000
    assert 60_000 <= len(made) < MAX_RECONSTRUCT
    assert MAX_RECONSTRUCT >= 5 * 60_000                                              # a budget with room (see htmlrules)


def test_the_budget_boundary_is_exact(monkeypatch):
    monkeypatch.setattr(treebuild, "MAX_RECONSTRUCT", 10)
    page = "<p><b id=0><b id=1>" + "<p>a" * 5                                        # two closed b re-opened per paragraph: 5 paragraphs make exactly 10
    assert treebuild.build(page).recon == 10                                         # at the budget: fine (a `>=` check raises here)
    with pytest.raises(TooDeep):
        treebuild.build(page + "<p>a")                                               # 12 > 10


# ------------------------------------------------------------------ the per-copy work of a re-opened element is bounded too
def attr_entries(monkeypatch, page: str) -> int:
    """How many attribute entries `treewalk.by_attrs` (the id / class decision of a node) was asked to read in one `strip_hidden`."""
    from ace_playbook.compile.watch import treewalk
    seen, real = [], treewalk.by_attrs
    monkeypatch.setattr(treewalk, "by_attrs", lambda attrs, classes, ids: seen.append(len(attrs)) or real(attrs, classes, ids))
    strip_hidden(page)
    return sum(seen)


def test_the_class_and_id_decision_of_an_attribute_list_is_made_once_however_often_the_element_is_reopened(monkeypatch):
    def page(n: int) -> str:
        return "<style>.z{display:none}</style><p><b" + " a" * 10_000 + ">" + "<p>a" * n
    small, large = attr_entries(monkeypatch, page(200)), attr_entries(monkeypatch, page(2_000))
    assert small == large and 10_000 <= large <= 10_010, (small, large)              # on the branch before the memo: 10,000 per re-opened copy (2,000,000)


def test_a_long_class_attribute_and_a_long_attribute_list_are_read_once(monkeypatch):
    page = '<style>.z{display:none}</style><p><b class="' + "a " * 5_000 + '">' + "<p>a" * 1_000
    assert attr_entries(monkeypatch, page) <= 10                                     # one entry (the class), not one per copy


def test_long_attribute_lists_over_many_reopened_copies_finish_in_a_generous_time():
    import time
    for head in ("<style>.z{display:none}</style><p><b" + " a" * 20_000 + ">", '<style>.z{display:none}</style><p><b class="' + "a " * 20_000 + '">'):
        t0 = time.monotonic()
        assert strip_hidden(head + "<p>a" * 20_000).count("a") >= 20_000
        assert time.monotonic() - t0 < 30.0                                          # measured ≈ 0.3 s; the branch before the memo needed 240+ s at 197,000 copies


def test_removing_a_formatting_element_does_not_scan_the_leftover_markers(monkeypatch):
    compares = []
    monkeypatch.setattr(treebuild.Node, "__eq__", lambda self, other: compares.append(1) or self is other, raising=False)       # `list.remove` compares from the front

    def page(cells: int) -> str:
        return "<table><tr>" + ("<td>" + "<object>" * 50) * cells + "</table>" + "<b></b>" * 100
    treebuild.build(page(4))
    small = len(compares)
    compares.clear()
    treebuild.build(page(40))
    assert small == len(compares) and small < 100, (small, len(compares))           # before: one comparison per leftover marker per `</b>` (2,000 x 100)
    t = treebuild.Tree()
    with pytest.raises(ValueError):                                                  # an element that is not in the list: as `list.remove` said
        t.fmt_del(treebuild.Node("b"))
