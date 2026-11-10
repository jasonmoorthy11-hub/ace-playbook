"""Security finding F1: the `scale(`, `translate(` and `inset(` matchers of `cssstyle` are linear in the length of the style value, they hide EXACTLY
what the old quadratic patterns hid (the three old patterns and the old `_hides` / `_inset` are kept below as the oracle), and the hiding decision of
one attribute string is computed once, not once per re-opened formatting element (`treewalk.Node.oa`, reused by `Tree.reconstruct`)."""

import itertools
import re
import time

from ace_playbook.compile.watch import cssstyle, treewalk
from ace_playbook.compile.watch.cssstyle import CLIP0, FONT0, _clear, _faint, _far, _flat, _num, _tiny, _zero
from ace_playbook.compile.watch.fetch import strip_hidden
from ace_playbook.compile.watch.treebuild import build

# ------------------------------------------------------------------ the oracle: the three patterns and the two functions as they were on main (fd2e28e)
OLD_SCALE = re.compile(r"scale(?:3d|x|y|z)?\(([^)]*)\)")
OLD_INSET = re.compile(r"inset\(([^)]*)\)")
OLD_TRANSLATE = re.compile(r"translate(?:x|y|3d)?\(([^)]*)\)")


def old_inset(value: str) -> bool:
    for m in OLD_INSET.finditer(value):
        for arg in m.group(1).split(" round ")[0].replace(",", " ").split():
            n = _num(arg)
            if n is cssstyle.AMBIGUOUS or (n is not None and n[1] == "%" and n[0] >= 50):
                return True
    return False


def old_hides(k: str, v: str) -> bool:
    return bool(
        (k == "display" and v == "none") or (k == "visibility" and v in ("hidden", "collapse")) or (k == "content-visibility" and v == "hidden")
        or (k == "opacity" and _faint(v)) or (k == "zoom" and _zero(v)) or (k == "font-size" and _tiny(v)) or (k == "font" and FONT0.search(v))
        or (k == "clip" and CLIP0.search(v)) or (k == "clip-path" and (v.startswith("circle(0") or old_inset(v)))
        or (k == "transform" and (any(_zero(a) for m in OLD_SCALE.finditer(v) for a in m.group(1).split(","))
                                  or any(_far(a) for m in OLD_TRANSLATE.finditer(v) for a in m.group(1).split(","))))
        or (k == "scale" and any(_zero(a) for a in v.split(" "))) or (k.startswith("margin") and any(_far(a) for a in v.split(" ")))
        or (k in ("color", "-webkit-text-fill-color") and _clear(v)) or (k == "text-indent" and _far(v.split(" ")[0])))


def old_style_hidden(monkeypatch, style: str) -> bool:
    with monkeypatch.context() as m:
        m.setattr(cssstyle, "_hides", old_hides)
        return cssstyle.style_hidden(style)


# ------------------------------------------------------------------ the corpus
ALPHABET = ["scale(", "translate(", "inset(", "0", "1", ",", ")", "(", " ", "-9999px"]
ARGS = ["0", "1", "0,1", "1,0", "1,1", "0,0", "0,1,1", "1,1,0", "-9999px", "-9999px,0", "0,-9999px", "-501px", "-500px", "-499px", "50%", "49%", "50% 0", "0 50%",
        "10px 50% round 5px", "50% round 0", "-100%", "-99%", "0.0", "00", "calc(0)", "calc(1 - 1)", "calc(-9999px)", "0px", "5%,5%", "-20em", "-19em", "",
        "0,", ",0", " 0 ", "0 0", "1 1 0", "0(", "(0", "a(b)", "scale(0", "0)scale(0"]
FUNCTIONS = ["scale", "scale3d", "scalex", "scaley", "scalez", "translate", "translatex", "translatey", "translate3d", "inset", "rotate", "skew", "matrix", "scale3", "xscale"]
WRAPS = ["{}", "rotate(5deg) {}", "{} rotate(5deg)", "{}{}", "{} {}", "calc({})", "translatex(1px) {} scale(1)"]


def realistic() -> list[str]:
    out = []
    for fn, arg, wrap in itertools.product(FUNCTIONS, ARGS, WRAPS):
        call = f"{fn}({arg})"
        out.append(wrap.format(call, call))
    out += ["scale(" * 40, "scale(0" + ")" * 3, "translate(" * 30 + ")", "inset(" * 20 + "50%)", "scale(1) scale(0)", "none", "SCALE(0)", "Translate(-9999PX)"]
    return out


def tokens(max_len: int):
    for n in range(1, max_len + 1):
        for combo in itertools.product(ALPHABET, repeat=n):
            yield "".join(combo)


def test_the_matchers_hide_exactly_what_the_old_patterns_hid_on_every_short_string():
    seen, hidden = 0, 0
    for v in tokens(5):
        for k in ("transform", "clip-path"):
            new, old = cssstyle._hides(k, v), old_hides(k, v)
            assert new == old, (k, v)
            seen += 1
            hidden += new
    assert seen == 2 * sum(len(ALPHABET) ** n for n in range(1, 6)) and hidden > 500           # the corpus does hide things: it is not all False


def test_the_matchers_hide_exactly_what_the_old_patterns_hid_on_realistic_values(monkeypatch):
    values, hidden = realistic(), 0
    assert len(values) > 600
    for v in values:
        for k in ("transform", "clip-path"):
            assert cssstyle._hides(k, v) == old_hides(k, v), (k, v)
        for text in (f"transform:{v}", f"TRANSFORM: {v.upper()} !important", f"clip-path:{v};color:red", f"-webkit-transform:{v}"):
            new = cssstyle.style_hidden(text)
            assert new == old_style_hidden(monkeypatch, text), text
            hidden += new
    assert hidden > 100


def test_style_hidden_agrees_with_the_old_decision_on_short_normalised_values(monkeypatch):
    for v in tokens(4):
        for text in (f"transform:{v}", f"clip-path:{v}"):
            assert cssstyle.style_hidden(text) == old_style_hidden(monkeypatch, text), text


class Counting:
    """An opener pattern that counts its searches: the work of `_args` is the number of times it asks the opener, and `find` calls that never overlap."""

    def __init__(self, pattern) -> None:
        self.pattern, self.searches = pattern, 0

    def search(self, value, pos=0):
        self.searches += 1
        return self.pattern.search(value, pos)


def test_args_walks_the_value_once_a_count_not_a_clock():
    for opener in (cssstyle.SCALE, cssstyle.INSET, cssstyle.TRANSLATE):
        head = opener.pattern.replace("(?:3d|x|y|z)?", "").replace("(?:x|y|3d)?", "").replace("\\(", "(")
        no_close = Counting(opener)
        assert list(cssstyle._args(no_close, head * 20_000)) == [] and no_close.searches == 1           # no `)` anywhere: ONE search, ONE failed find (quadratic: 20,000)
        closed = Counting(opener)
        assert list(cssstyle._args(closed, (head + "0)") * 20_000)) == ["0"] * 20_000 and closed.searches == 20_001      # one search per call plus the last
    assert list(cssstyle._args(cssstyle.SCALE, "scale(0)scale(1,2)scalex(3")) == ["0", "1,2"]
    assert list(cssstyle._args(cssstyle.SCALE, "scale(scale(0)")) == ["scale(0"]                       # the old pattern: up to the first `)`, then on from there


def test_the_exploit_styles_are_decided_in_a_generous_time():
    t0 = time.monotonic()
    assert not cssstyle._hides("transform", "scale(" * 10900) and not cssstyle._hides("clip-path", "inset(" * 10900) and not cssstyle._hides("transform", "translate(" * 9000)
    assert not cssstyle.style_hidden("transform:" + "scale(" * 10900)
    assert time.monotonic() - t0 < 5.0                                              # measured ≈ 0.01 s; the old patterns took ≈ 1.3 s for the scale value alone


# ------------------------------------------------------------------ the hiding decision of an attribute string is computed once
def exploit(repeats: int = 10900, paragraphs: int = 2000) -> str:
    return '<p><b style="transform:' + "scale(" * repeats + '">' + "<p>a" * paragraphs


def count_style_calls(monkeypatch, page: str) -> int:
    calls = []
    real = treewalk.style_hidden
    monkeypatch.setattr(treewalk, "style_hidden", lambda s: calls.append(len(s)) or real(s))
    strip_hidden(page)
    return len(calls)


def test_the_style_of_a_formatting_element_is_evaluated_a_constant_number_of_times_however_often_it_is_reopened(monkeypatch):
    small, large = count_style_calls(monkeypatch, exploit(paragraphs=200)), count_style_calls(monkeypatch, exploit(paragraphs=2000))
    assert small == large and large <= 3, (small, large)                              # on main: one evaluation per paragraph (200 and 2000)


def test_the_full_size_f1_payload_runs_in_a_generous_time():
    page = exploit(paragraphs=245_000)
    assert len(page) < 1_048_576                                                      # under the fetch cap
    t0 = time.monotonic()
    out = strip_hidden(page)
    assert out.count("a") == 245_000                                                  # the text is what a browser shows: the style hides nothing
    assert time.monotonic() - t0 < 30.0                                               # measured 2.4 s (245,000 nodes, no regex cost); main did not finish in minutes


def test_a_node_made_by_reconstruct_has_the_decision_of_its_formatting_entry():
    t = build('<p><b hidden><i style="display:none"><u>x<p>y')
    names = [n.name for n in t.fmt if n is not None]
    assert names == ["b", "i", "u"] and all(n.oa == n.own for n in t.fmt)
    assert [n.oa for n in t.fmt] == [True, True, False]
