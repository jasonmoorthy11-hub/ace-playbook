"""Parsers of the tier-0 and tier-1 source formats (stdlib only: `xml.etree`, `json`, `re`). Each takes text and returns plain data or
raises ValueError (a `SyntaxError` from the XML parser also counts as "does not parse" to `fetch`).

Every free-text field is cut to visible text (`sanitize.one_line`) and to a length cap; links are kept only when they are http(s). XML
with a DOCTYPE or an ENTITY declaration is refused before parsing (entity expansion); the 1 MiB body cap bounds the rest.
"""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET

from .sanitize import one_line

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]{1,40})?", re.ASCII)       # ASCII only: it reaches titles and status.json
MODEL = re.compile(r"claude-[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
SEPARATOR = re.compile(r"\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?")
NONE_WORDS = ("", "n/a", "na", "-", "—", "none", "tbd", "tba", "null", "to be announced")
MAX_ENTRIES, MAX_LINE = 100, 2000
LINK = re.compile(r"\[([^\[\]]*)\]\((?:[^()]*(?:\([^()]*\)[^()]*)*)\)")         # the classes exclude their openers, so no `[` and no `(` is scanned
#   twice (S-1 F7, Opus I-2); one level of balanced parentheses in the URL is allowed (`.../a_(b)`, Opus N-2)


class DTDRefused(ValueError):
    """XML with a DOCTYPE or ENTITY declaration (entity expansion); `fetch` drops it with the reason `xml`."""


def _xml(text: str) -> ET.Element:
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
        raise DTDRefused("DTD refused")
    return ET.fromstring(text)


def _ln(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _kids(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _ln(c) == name]


def _markup(el: ET.Element) -> str:
    """The content of `el` as markup: the text of `el` itself as it is (an escaped `&lt;div hidden&gt;` is HTML a reader would render), child
    elements as tags with their attributes (so `strip_hidden` sees an XHTML `<span style="display:none">` instead of a flattened string).
    Text and tails inside a child element are HTML-escaped: they are literal text in XHTML, and an escaped `&lt;/span&gt;` there must not
    close a hidden ancestor. The walk keeps its own stack: a document nested thousands of levels deep must not reach the recursion limit."""
    out, walk, closers = [el.text or ""], [iter(el)], []
    esc = lambda text, depth: text if depth == 1 else html.escape(text, quote=False)  # noqa: E731 - depth 1: the tail of a child of `el`
    while walk:
        child = next(walk[-1], None)
        if child is None:
            walk.pop()
            if closers:
                out.append(closers.pop())
        elif not isinstance(child.tag, str):                     # a comment or a processing instruction
            out.append(esc(child.tail or "", len(walk)))
        else:
            attrs = "".join(f' {k.rsplit("}", 1)[-1]}="{html.escape(v, quote=True)}"' for k, v in child.attrib.items())
            out.append(f"<{_ln(child)}{attrs}>{html.escape(child.text or '', quote=False)}")
            closers.append(f"</{_ln(child)}>{esc(child.tail or '', len(walk))}")
            walk.append(iter(child))
    return "".join(out)


def _txt(el: ET.Element, *names: str, limit: int = 300) -> str:
    for name in names:
        for c in _kids(el, name):
            t = one_line(_markup(c), limit)
            if t:
                return t
    return ""


def _url(value: str) -> str:
    return value if re.fullmatch(r"https?://\S{1,480}", value) else ""


def parse_rss(xml: str) -> list[dict]:
    """RSS 2.0 `channel/item` entries as `{id, title, link, updated, summary}` (at most 100)."""
    root = _xml(xml)
    if _ln(root) != "rss":
        raise ValueError("not RSS")
    items = [i for ch in _kids(root, "channel") for i in _kids(ch, "item")][:MAX_ENTRIES]
    out = []
    for i in items:
        link = _url(_txt(i, "link", limit=500))
        out.append({"id": _txt(i, "guid", limit=500) or link or _txt(i, "title"), "title": _txt(i, "title"), "link": link,
                    "updated": _txt(i, "pubDate", "date", limit=60), "summary": _txt(i, "description", "encoded", limit=1000)})
    return out


def parse_atom(xml: str) -> list[dict]:
    """Atom `entry` elements as `{id, title, link, updated, summary}` (at most 100)."""
    root = _xml(xml)
    if _ln(root) != "feed":
        raise ValueError("not Atom")
    out = []
    for e in _kids(root, "entry")[:MAX_ENTRIES]:
        links = [c for c in _kids(e, "link") if c.get("href")]
        best = next((c for c in links if c.get("rel", "alternate") == "alternate"), links[0] if links else None)
        link = _url(best.get("href", "").strip()) if best is not None else ""
        out.append({"id": _txt(e, "id", limit=500) or link, "title": _txt(e, "title"), "link": link,
                    "updated": _txt(e, "updated", "published", limit=60), "summary": _txt(e, "summary", "content", limit=1000)})
    return out


def parse_npm_dist_tags(text: str) -> dict:
    """`{stable, latest}` from the registry's dist-tags JSON. `latest` must be an ASCII version (`VERSION`), else ValueError (a parse
    drop); a `stable` that is not a version is None."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    if not (isinstance(raw.get("latest"), str) and VERSION.fullmatch(raw["latest"])):
        raise ValueError("no version in latest")
    return {k: raw[k] if isinstance(raw.get(k), str) and VERSION.fullmatch(raw[k]) else None for k in ("stable", "latest")}


def parse_sitemap(xml: str) -> dict:
    """`{loc: lastmod}` of a sitemap or sitemap index (lastmod is "" when absent)."""
    root = _xml(xml)
    if _ln(root) not in ("urlset", "sitemapindex"):
        raise ValueError("not a sitemap")
    out = {}
    for el in root:
        loc = _url(_txt(el, "loc", limit=500))
        if loc:
            out[loc] = _txt(el, "lastmod", limit=40)
    return out


def parse_md(text: str) -> dict:
    """`{heading}`: the first Markdown heading of a page (without its `#`), else its first 400 characters."""
    for line in text.splitlines():
        if line.lstrip().startswith("#") and line.lstrip("# \t"):
            return {"heading": line.lstrip("# \t").strip()[:400]}
    return {"heading": " ".join(text.split())[:400]}


def _cells(line: str) -> list[str]:
    text = line.strip()[:MAX_LINE]
    parts = re.split(r"(?<!\\)\|", text)                         # an escaped pipe (`\|`) is cell text, not a column break
    closed = text.endswith("|") and not text.endswith("\\|")
    return [re.sub(r"\s+", " ", LINK.sub(r"\1", c.replace("\\|", "|"))).replace("`", "").replace("*", "").strip()
            for c in parts[1:-1 if closed else None]]


def _roles(header: list[str]) -> dict[str, int]:
    roles: dict[str, int] = {}
    for i, h in enumerate(header):
        low = h.lower()
        role = ("successor" if re.search(r"replace|successor|recommended|migrat", low) else "retires" if re.search(r"retire|sunset", low)
                else "model" if re.search(r"model|name", low) else "state" if re.search(r"state|status", low)
                else "deprecated" if "deprecat" in low else None)
        if role and role not in roles:
            roles[role] = i
    return roles


def parse_deprecations_md(text: str) -> list[dict]:
    """Rows `{model_id, deprecated, retires, successor, state, row}` of every Markdown table with a model column. Columns are found by
    their header words, in any order (model | state/status | deprecat* | retire* | replace*/recommended/successor); `N/A` and empty
    cells are None; `row` is the table line verbatim (at most 400 characters). A model cell yields one row per `claude-...` id in it."""
    lines, rows, i = [ln[:MAX_LINE] for ln in text.splitlines()], [], 0                  # a table line is cut at MAX_LINE before anything reads it
    while i < len(lines) - 1:
        if not (lines[i].strip().startswith("|") and SEPARATOR.fullmatch(lines[i + 1].strip())):
            i += 1
            continue
        roles, j = _roles(_cells(lines[i])), i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            if "model" in roles:
                cells = _cells(lines[j])
                get = lambda role: (cells[roles[role]] if role in roles and roles[role] < len(cells) else "")  # noqa: E731
                val = lambda role: None if get(role).lower() in NONE_WORDS else get(role)[:80]  # noqa: E731
                for mid in MODEL.findall(get("model")):                          # only a `claude-...` id is a model id; "default" is not
                    rows.append({"model_id": mid, "deprecated": val("deprecated"), "retires": val("retires"), "successor": val("successor"),
                                 "state": val("state"), "row": lines[j].strip()[:400]})
            j += 1
        i = j
    return rows


PARSERS = {"rss": parse_rss, "atom": parse_atom, "npm": parse_npm_dist_tags, "sitemap": parse_sitemap, "md": parse_md,
           "deprecations": parse_deprecations_md}
