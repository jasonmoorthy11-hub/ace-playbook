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
