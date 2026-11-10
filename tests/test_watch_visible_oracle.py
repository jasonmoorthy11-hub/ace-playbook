"""The visible text of `strip_hidden` is unchanged by the F1/F2 work (a per-attribute-list memo in `treewalk.hidden`, `oa` carried to the copies `Tree.reconstruct`
makes, `fmt_del` from the end, the reconstruct budget): 3,000 generated pages (rules on classes, ids and tags, misnested formatting, shadow hosts, aria-hidden,
inline styles, repeated attributes, cells and objects, selects) gave the expected text on main (fd2e28e), recorded in `tests/data/watch_visible_main.json` and
compared here with ZERO differences. The corpus is deterministic (its own generator, no `random`); to regenerate, run `corpus()` against the code to trust."""

import json
from pathlib import Path

from ace_playbook.compile.watch.fetch import strip_hidden

FIXTURE = Path(__file__).parent / "data" / "watch_visible_main.json"
SHEETS = ["<style>.h{display:none}#i1{visibility:hidden}</style>", "<style>b{opacity:0}.x{font-size:0}</style>", "<style>[x]{display:none}.h{color:red}</style>",
          "<style>u,.z{display:none}#I2{display:none}</style>", "<style>.a{display:none}", "<style>@media x{.h{display:none}}</style>"]
BITS = ["<p>", "<p class=h>", "<p id=I1>", "<p class='x h'>", "<b>", "<i class='a h'>", "<b class=H id=i2>", "<u style='display:none'>", "<b id=i1>", "</b>", "</i>",
        "</p>", "</u>", "<div aria-hidden=true>", "<div><template shadowrootmode=open>", "<template shadowrootmode=open>", "</div>", "<span hidden>", "</span>",
        "<table><tr><td>", "<td>", "<object>", "</td>", "</table>", "<a href=x class='h h'>", "</a>", "<b class='a h' class=z>", "<b class=z class=h>", "<b a a a class=a>",
        "<select>", "<option>", "</select>", "<em style='transform:scale(0)'>", "</em>", "<font id=z>", "<div class=z>", "<s class='x'>", "<br>", "<b x>", "<i x=1>",
        "<b class=h>", "<b class=h>", "<i class=h>", "<p class=H>", "<div class='z h'>", "<i id=i1>", "<p id=i1>", "<b id=I2>", "<u class=z>", "<p class=a>"]
WORDS = ["w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8", "w9", "q"]


def corpus(n: int = 3000) -> list[str]:
    pages, state = [], 12345
    for _ in range(n):
        state = (state * 1103515245 + 12345) % 2**31
        parts = [SHEETS[(state >> 7) % len(SHEETS)]] if state % 6 else []                 # most pages carry a sheet: the id / class decision is made
        for _ in range(3 + state % 9):
            state = (state * 1103515245 + 12345) % 2**31
            pick = (state >> 8) % (len(BITS) + 6)
            parts.append(BITS[pick] if pick < len(BITS) else WORDS[(state >> 4) % len(WORDS)] if pick < len(BITS) + 4 else SHEETS[(state >> 6) % len(SHEETS)])
            state = (state * 1103515245 + 12345) % 2**31
            parts.append(WORDS[(state >> 10) % len(WORDS)] + " " if state % 3 else "")
        pages.append("".join(parts))
    return pages


def test_the_visible_text_of_3000_generated_pages_is_what_main_gave():
    pages, expected = corpus(), json.loads(FIXTURE.read_text())
    assert len(pages) == len(expected) == 3000
    got = []
    for page in pages:
        try:
            got.append(strip_hidden(page))
        except ValueError:                                                          # TooDeep
            got.append(None)
    assert [(p, g, e) for p, g, e in zip(pages, got, expected) if g != e] == []
    assert len(set(got)) > 900 and sum(1 for g in got if g) > 1500 and sum(1 for g in got if not g) > 500          # a corpus that hides AND shows
