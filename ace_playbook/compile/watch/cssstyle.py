"""Does this CSS hide its text? Inline `style` attributes and the SIMPLE rules of `<style>` blocks. Stdlib only.

`style_hidden(css)` normalises first (CSS comments removed, CSS escapes such as `\\3A` decoded, NUL removed, lower-cased, `!important`
dropped, whitespace collapsed, property names stripped of all whitespace) and then reads each declaration: display:none; visibility
hidden or collapse; content-visibility:hidden; zoom:0; opacity or font-size zero in any spelling or unit (also `calc(0px)`); the `font`
shorthand with a zero size; `clip:rect(0,0,0,0)`; clip-path inset(100%) or circle(0); transform scale(0) or a far translate; a
transparent color (`transparent`, rgb/rgba/hsl/hsla with alpha 0 in the comma or the slash syntax, `#rgba` and `#rrggbbaa` with alpha 0); a
zero width or height with overflow hidden or clip; position absolute or fixed far off-screen; text-indent far negative. "Far" is -1000px
(or pt, or no unit) and -100em, rem, ch, %, vw or vh.

`css_rules(css)` reads `<style>` text: only rules whose selector is a single `.class`, `#id` or `tag` and whose body hides count; rules
inside at-rules (media queries) and every other selector (descendant, child, attribute, pseudo-class) are NOT handled, nor are external
stylesheets (docs: LIMITATIONS.md).
"""

from __future__ import annotations

import re

CSS_COMMENT, CSS_ESCAPE = re.compile(r"/\*.*?(?:\*/|$)", re.S), re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\r\n\f]?|\\(.)", re.S)
NUMBER = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)([a-z%]*)")
SCALE0 = re.compile(r"scale(?:3d|x|y)?\((?:0+(?:\.0*)?|\.0+)[,)]")
TRANSLATE = re.compile(r"translate(?:x|y|3d)?\(([^)]*)\)")
CLIP0 = re.compile(r"rect\(0(?:px)?[ ,]0(?:px)?[ ,]0(?:px)?[ ,]0(?:px)?\)")
FONT0 = re.compile(r"(?:^| )0(?:\.0*)?(?:px|pt|em|rem|%|ex|ch|vw|vh)?(?:/| |$)")
COLOR_FN = re.compile(r"(?:rgb|rgba|hsl|hsla)\((.*)\)")
HEX_CLEAR = re.compile(r"#[0-9a-f]{3}0|#[0-9a-f]{6}00")
FAR = {"px": 1000, "pt": 1000, "": 1000, "em": 100, "rem": 100, "ch": 100, "vw": 100, "vh": 100, "%": 100}
RULE, AT_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}"), re.compile(r"@[^{};]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}|@[^{};]*;")
SIMPLE = re.compile(r"\.[\w-]+|#[\w-]+|[A-Za-z][\w-]*")


def _unescape(m: re.Match) -> str:
    if m.group(1):
        n = int(m.group(1), 16)
        return chr(n) if 0 < n <= 0x10FFFF and not 0xD800 <= n <= 0xDFFF else ""
    return m.group(2)


def _num(value: str) -> tuple[float, str] | None:
    m = NUMBER.fullmatch(value)
    return (float(m.group(1)), m.group(2)) if m else None


def _zero(value: str) -> bool:
    n = _num(value)
    return n is not None and n[0] == 0


def _far(value: str) -> bool:
    n = _num(value.strip())
    return n is not None and n[0] <= -FAR.get(n[1], 1000)


def _clear(value: str) -> bool:
    m = COLOR_FN.fullmatch(value)
    if not m:
        return value == "transparent" or bool(HEX_CLEAR.fullmatch(value))
    args = m.group(1)
    alpha = args.split("/")[1] if "/" in args else args.split(",")[3] if args.count(",") == 3 else None
    return alpha is not None and _zero(alpha.strip().rstrip("%"))


def _declarations(style: str) -> list[tuple[str, str]]:
    s = CSS_ESCAPE.sub(_unescape, CSS_COMMENT.sub("", style.replace("\x00", ""))).lower()
    out = []
    for d in re.sub(r"\s*!\s*important", "", s).split(";"):
        k, _, v = d.partition(":")
        k, v = re.sub(r"\s+", "", k), re.sub(r"\s*([(),/])\s*", r"\1", " ".join(v.split()))
        v = v[5:-1] if v.startswith("calc(") and v.endswith(")") else v
        if k and v:
            out.append((k, v))
    return out


def _hides(k: str, v: str) -> bool:
    return bool(
        (k == "display" and v == "none") or (k == "visibility" and v in ("hidden", "collapse")) or (k == "content-visibility" and v == "hidden")
        or (k == "opacity" and _zero(v.rstrip("%"))) or (k in ("font-size", "zoom") and _zero(v)) or (k == "font" and FONT0.search(v))
        or (k == "clip" and CLIP0.search(v)) or (k == "clip-path" and v.startswith(("inset(100%", "circle(0")))
        or (k == "transform" and (SCALE0.search(v) or any(_far(a) for m in TRANSLATE.finditer(v) for a in m.group(1).split(","))))
        or (k == "color" and _clear(v)) or (k == "text-indent" and _far(v.split(" ")[0])))


def style_hidden(style: str) -> bool:
    """True when the declarations of `style` hide their element (see the module docstring)."""
    decls = _declarations(style)
    if any(_hides(k, v) for k, v in decls):
        return True
    clipped = any(k.startswith("overflow") and v in ("hidden", "clip") for k, v in decls)
    away = any(k == "position" and v in ("absolute", "fixed") for k, v in decls)
    return (clipped and any(k in ("width", "height", "max-width", "max-height") and _zero(v) for k, v in decls)) or (
        away and any(k in ("left", "top", "right", "bottom") and _far(v) for k, v in decls))


def css_rules(css: str) -> tuple[frozenset, frozenset, frozenset]:
    """(classes, ids, tags) named by the simple hiding rules of `css`."""
    classes, ids, tags = set(), set(), set()
    for selectors, body in RULE.findall(AT_RULE.sub("", CSS_COMMENT.sub("", css))):
        if not style_hidden(body):
            continue
        for sel in (s.strip() for s in selectors.split(",")):
            if SIMPLE.fullmatch(sel):
                (classes if sel[0] == "." else ids if sel[0] == "#" else tags).add(sel[1:] if sel[0] in ".#" else sel.lower())
    return frozenset(classes), frozenset(ids), frozenset(tags)
