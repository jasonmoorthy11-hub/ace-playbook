"""The shape of a brief and its page (docs/design/compiler.md 8.x "Brief (as built)", D-110): the model emits JSON, this code writes HTML.

`validate_digest` / `validate_brief` check the two JSON schemas with plain code (strings capped and stripped of control characters; every
`url` becomes null, because the digest carries no url and so any url in a brief was invented by a model). `render` writes one page in the
morning skill's layout and tokens (day-date line, serif headline, terrain, three acts, Needs attention, Resolved): no script, no button, no
external resource (system fonts only), a CSP meta of `default-src 'none'; style-src 'unsafe-inline'`, every text field through `esc`, a link
only for a `url` that passes `link_ok` (https on mail.google.com, calendar.google.com or linear.app, at most 500 characters, no control or
format character): never for the null the validator leaves, and a second line of defence for a brief that did not come through it.
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from datetime import datetime

CLASSES = ("heavy", "normal", "open")
URL_MAX = 500
ITEM = {"title": ("s", 80), "sentence": ("s", 300), "source": ("s", 80), "url": ("u", URL_MAX)}   # kinds: s str, n str|null, u always null, o enum|null
DIGEST = {"generated_at": ("s", 40),
          "calendar": ("l", 60, 0, {"start": ("s", 40), "end": ("s", 40), "title": ("s", 200), "organizer_is_me": "b", "optional": "b",
                                    "attendees_n": "i"}),
          "mail": ("l", 60, 0, {"thread_id": ("s", 120), "sender": ("s", 200), "subject": ("s", 300), "received": ("s", 40), "asked_me": "b",
                                "replied": "b", "summary": ("s", 300)}),
          "issues": ("l", 60, 0, {"id": ("s", 40), "title": ("s", 200), "state": ("s", 60), "due": ("n", 40)})}
BRIEF = {"generated_at": ("s", 40), "day_class": ("e", CLASSES), "headline": ("s", 140),
         "acts": ("l", 3, 3, {"range": ("s", 60), "sentence": ("s", 200), "load": ("o", CLASSES)}, True),         # True: more than max refuses
         "needs_you": ("l", 25, 0, ITEM, True), "resolved": ("l", 25, 0, ITEM, True)}
# A link is an outbound channel (a crafted URL can carry mail text to a host), so render writes one only for these hosts: https, the exact
# host, no userinfo, no port. The validator nulls every url anyway; this allowlist is defence in depth for a brief that bypassed it.
LINK_HOSTS = ("mail.google.com", "calendar.google.com", "linear.app")
LINK = re.compile(r"https://(?:%s)(?:[/?#][^\s\x00-\x1f\x7f]*)?" % "|".join(re.escape(h) for h in LINK_HOSTS))


class SchemaError(ValueError):
    """Refused; the message is a field path or a short reason, never content."""


def link_ok(url) -> bool:
    """True for a link render may write: on the host allowlist, at most URL_MAX characters, no format (Cf, bidi) or control character."""
    return (isinstance(url, str) and len(url) <= URL_MAX and LINK.fullmatch(url) is not None
            and not any(unicodedata.category(ch) in ("Cc", "Cf") for ch in url))


def _clean(text: str, cap: int) -> str:
    text = "".join(" " if c in "\n\r\t" else c for c in text)
    return "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Zl", "Zp")).strip()[:cap].rstrip()


def _val(v, t, path: str):
    kind = t if isinstance(t, str) else t[0]
    text = isinstance(v, str)
    if kind == "b" and isinstance(v, bool):
        return v
    if kind == "i" and isinstance(v, int) and not isinstance(v, bool):
        return max(0, min(v, 10_000))
    if kind in "sn" and text:
        return _clean(v, t[1])
    if kind == "n" and v is None:
        return None
    if kind == "e" and text and v in t[1]:
        return v
    if kind == "o":
        return v if text and v in t[1] else None
    if kind == "u":
        return None          # no source of the brief holds a url, so a non-null one is a model's invention (and an outbound channel)
    if kind == "l" and isinstance(v, list) and t[2] <= len(v) <= (t[1] if len(t) > 4 else len(v)):
        return [_obj(x, t[3], f"{path}[{i}]") for i, x in enumerate(v[:t[1]])]
    raise SchemaError(path)


def _obj(raw, spec: dict, path: str) -> dict:
    if not isinstance(raw, dict):
        raise SchemaError(path)
    return {k: _val(raw.get(k), t, f"{path}.{k}") for k, t in spec.items()}


def validate_digest(raw) -> dict:
    return _obj(raw, DIGEST, "digest")


def validate_brief(raw) -> dict:
    out = _obj(raw, BRIEF, "brief")
    for act in out["acts"]:
        act["load"] = act["load"] or out["day_class"]
    return out


CSP = "default-src 'none'; style-src 'unsafe-inline'"
CALM = "Nothing needs you this morning."
LOAD_Y = {"heavy": 38.0, "normal": 88.0, "open": 138.0}      # terrain elevation (SVG y, 170 high) by the load of an act
LOAD_AMP = {"heavy": 14.0, "normal": 8.0, "open": 1.5}       # an open day is still water
CENTRES = (140, 420, 700)
CSS = """:root{--bg:#FCFCFB;--wash:#F9F9F7;--ink:#2E2C27;--ink-soft:#6B6A63;--ink-grey:#B4B3A8;--hairline:#E4E3DC;--clay:#C6613F}
@media (prefers-color-scheme: dark){:root{--bg:#151513;--wash:#1C1C19;--ink:#ECEAE2;--ink-soft:#A9A79D;--ink-grey:#6E6D66;--hairline:#2E2E2A}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink-soft);font:16px/1.55 -apple-system,"Segoe UI",sans-serif}
.top{background:var(--wash);border-bottom:1px solid var(--hairline)}
.in{max-width:860px;margin:0 auto;padding:44px 24px}
.date{margin:0 0 12px;font-size:14px}
h1{margin:0 0 10px;font:600 40px/1.15 Fraunces,Georgia,serif;color:var(--ink)}
svg{display:block;width:100%;height:auto}
.terrain{fill:none;stroke:var(--ink);stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.accent{fill:var(--clay)}
.acts{display:grid;grid-template-columns:repeat(3,1fr);margin-top:10px}
.act{padding:2px 20px;border-left:1px solid var(--hairline)}
.act:first-child{border-left:0;padding-left:0}
.act p{margin:0}
.range{font-weight:700;color:var(--ink);margin-bottom:4px!important}
h2{margin:0 0 12px;font:600 15px/1.3 -apple-system,"Segoe UI",sans-serif;color:var(--ink)}
ol{list-style:none;margin:0 0 36px;padding:0}
li{display:grid;grid-template-columns:34px 1fr;padding:10px 0}
.n{color:var(--ink-grey);font-variant-numeric:tabular-nums}
li p{margin:0}
.t{font-weight:700;color:var(--ink)}
.s{margin-top:2px!important}
.src{margin-top:2px!important;font-size:14px}
a{color:var(--ink-soft);text-decoration:underline}
.calm{margin:0}
@media (max-width: 640px){h1{font-size:30px}.acts{display:block}.act{padding:12px 0;border-left:0;border-top:1px solid var(--hairline)}
.act:first-child{border-top:0}}"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _date(stamp) -> str:
    try:
        d = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{d:%A} · {d:%B} {d.day} {d.year}"


def _ease(a: float, b: float, t: float) -> float:
    return a + (b - a) * (1 - math.cos(math.pi * t)) / 2


def _height(x: float, ys: list, amps: list) -> float:
    anchors = [(0, ys[0], amps[0]), *((c, y, a) for c, y, a in zip(CENTRES, ys, amps)), (840, ys[2], amps[2])]
    (x0, y0, a0), (x1, y1, a1) = next((p, q) for p, q in zip(anchors, anchors[1:]) if x <= q[0])
    t = (x - x0) / (x1 - x0)
    return _ease(y0, y1, t) - _ease(a0, a1, t) * math.sin(x / 29.0)


def terrain(loads: list) -> str:
    """One unbroken polyline across 840 x 170, elevation from each act's load, plus one clay dot on the highest act."""
    loads = [load if load in LOAD_Y else "normal" for load in (list(loads) + ["normal"] * 3)[:3]]
    ys, amps = [LOAD_Y[x] for x in loads], [LOAD_AMP[x] for x in loads]
    pts = " ".join(f"{x},{_height(x, ys, amps):.1f}" for x in range(0, 841, 12))
    top = ys.index(min(ys))
    dot = f'<circle class="accent" cx="{CENTRES[top]}" cy="{_height(CENTRES[top], ys, amps):.1f}" r="5"/>'
    return f'<svg viewBox="0 0 840 170" role="img" aria-label="The day drawn as terrain"><polyline class="terrain" points="{pts}"/>{dot}</svg>'


def _act(act: dict) -> str:
    return f'<div class="act"><p class="range">{esc(act.get("range", ""))}</p><p>{esc(act.get("sentence", ""))}</p></div>'


def _source(item: dict) -> str:
    source, url = esc(item.get("source", "")), item.get("url")
    if not source:
        return ""
    if link_ok(url):
        return f'<p class="src"><a href="{esc(url)}" rel="noopener noreferrer">{source}</a></p>'
    return f'<p class="src">{source}</p>'


def _list(heading: str, items: list) -> str:
    rows = "".join(f'<li><span class="n">{i:02d}</span><div><p class="t">{esc(it.get("title", ""))}</p>'
                   f'<p class="s">{esc(it.get("sentence", ""))}</p>{_source(it)}</div></li>' for i, it in enumerate(items, 1))
    return f"<section><h2>{esc(heading)}</h2><ol>{rows}</ol></section>" if items else ""


def render(brief: dict) -> str:
    """The page for a brief dict (see brief.validate_brief); safe for any str-valued fields."""
    acts = list(brief.get("acts") or [])[:3]
    cls = brief.get("day_class")
    loads = [a.get("load") or cls for a in acts] or [cls] * 3
    needs, done = list(brief.get("needs_you") or []), list(brief.get("resolved") or [])
    lists = _list("Needs attention", needs) + _list("Resolved", done) or f'<p class="calm">{CALM}</p>'
    date = f'<p class="date">{esc(_date(brief.get("generated_at")))}</p>' if _date(brief.get("generated_at")) else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{CSP}">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark">'
            f"<title>Morning brief</title><style>{CSS}</style></head><body>"
            f'<div class="top"><div class="in">{date}<h1>{esc(brief.get("headline", ""))}</h1>{terrain(loads)}'
            f'<div class="acts">{"".join(_act(a) for a in acts)}</div></div></div>'
            f'<div class="bot"><div class="in">{lists}</div></div></body></html>\n')
