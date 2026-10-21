"""Tier-0 fetchers with a hash store (docs/design/compiler.md 8.x "Watch (as built)"; design 4.1): stdlib `urllib.request` only.

`fetch` returns an item `{url, sha256, fetched_at, content_type, body, prev_sha256, data}`, None when the page is unchanged (a 304 or
the same sha256 as the store's), or a dropped record `{url, fetched_at, dropped: reason}` for a scheme other than http(s), a host that is
or resolves to a loopback, link-local, private, multicast or reserved address (`address`, no request is made), a redirect to another host
or past three hops (`redirect`, see `netguard`), an XML body with a DTD or ENTITY declaration (`xml`), a Content-Type outside `expect`
(checked before the body is read), a body over `cap` bytes (at most cap + 1 bytes are read), a network
error or a body that does not parse as its `kind`. `body` is visible text only (`sanitize.strip_hidden`; feeds are rendered from their
parsed, cleaned fields). Every successful fetch records url, sha256 and fetched_at in the seen store. `compile watch` is the one
network-using module of ace-playbook and is never on the hook path (tests/test_hook_conformance.py).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ...canon import now_iso
from . import netguard
from .parsers import PARSERS, DTDRefused
from .sanitize import plain, strip_hidden

CAP, UA = 1_048_576, "ace-playbook-watch/1 (read-only; no cookies, no keys)"
XML_KINDS, TEXT_KINDS = ("rss", "atom", "sitemap"), ("deprecations", "md")
HTMLISH = ("text/html", "text/markdown")
XML = ("application/xml", "text/xml")
EXPECT = {"rss": ("application/rss+xml", *XML), "atom": ("application/atom+xml", *XML), "sitemap": XML, "npm": ("application/json",),
          "deprecations": ("text/markdown", "text/plain", "text/html"), "md": ("text/markdown", "text/plain", "text/html")}
SOURCE_ID = re.compile(r"[A-Za-z0-9_.-]{1,40}")


def load_sources(path: Path) -> list[dict]:
    """The source list of the private `watch_sources` file: `[{id, url, kind, tier, cadence}]`; ValueError (one line) when malformed."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("watch_sources is not readable JSON") from None
    if not isinstance(raw, list) or not raw:
        raise ValueError("watch_sources must be a non-empty JSON list")
    out, ids = [], set()
    for s in raw:
        ok = (isinstance(s, dict) and isinstance(s.get("id"), str) and SOURCE_ID.fullmatch(s["id"]) and s["id"] not in ids
              and isinstance(s.get("url"), str) and urllib.parse.urlsplit(s["url"]).scheme in ("http", "https")
              and s.get("kind") in EXPECT and s.get("tier") in (0, 1) and not isinstance(s.get("tier"), bool))
        if not ok:
            raise ValueError("watch_sources has a bad entry (id, url, kind, tier)")
        ids.add(s["id"])
        out.append({"id": s["id"], "url": s["url"], "kind": s["kind"], "tier": s["tier"],
                    "cadence": s.get("cadence") if s.get("cadence") in ("daily", "weekly") else "daily" if s["tier"] == 0 else "weekly"})
    return out


def _drop(url: str, reason: str, now: str) -> dict:
    return {"url": url, "fetched_at": now, "dropped": reason}


def _body(kind: str | None, data: object, text: str) -> str:
    if kind in ("rss", "atom"):
        return "\n".join(f"{e['title']} | {e['link']} | {e['updated']} | {e['summary']}" for e in data)
    return "\n".join(f"{k} lastmod {v}" for k, v in data.items()) if kind == "sitemap" else text


def fetch(url: str, *, expect: tuple[str, ...], store: dict, timeout: float = 20, cap: int = CAP, kind: str | None = None,
          opener=None, now: str | None = None) -> dict | None:
    """One conditional GET of `url` (see the module docstring). `store` is the seen dict (its `fetched` map is updated); `kind` (a key
    of `parsers.PARSERS`) makes the body parse; `opener(request, timeout=...)` replaces `urllib.request.urlopen` (tests)."""
    now = now or now_iso()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return _drop(url, "scheme", now)
    if netguard.blocked_address(parts.hostname):                              # loopback, link-local, private, metadata: no request at all
        return _drop(url, "address", now)
    known = store.setdefault("fetched", {})
    ent = known.get(url) if isinstance(known.get(url), dict) else {}
    headers = {"User-Agent": UA, "Accept": ", ".join(expect)}
    if isinstance(ent.get("etag"), str) and ent["etag"]:
        headers["If-None-Match"] = ent["etag"]
    try:
        with (opener or netguard.open_url)(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in expect:
                return _drop(url, "content-type " + (re.sub(r"[^\w/+.-]", "", ctype)[:60] or "missing"), now)
            etag = resp.headers.get("ETag")
            raw = resp.read(cap + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and ent:
            ent["fetched_at"] = now
            return None
        return _drop(url, "redirect" if 300 <= exc.code < 400 else f"http {exc.code}", now)       # a 3xx that was not followed (too many hops)
    except netguard.RedirectRefused:
        return _drop(url, "redirect", now)
    except (OSError, ValueError, http.client.HTTPException) as exc:           # the type only: a message could carry a path or a host
        return _drop(url, "network " + type(exc).__name__, now)
    if len(raw) > cap:
        return _drop(url, "too large", now)
    sha, text = hashlib.sha256(raw).hexdigest(), raw.decode("utf-8", "replace")
    if kind not in XML_KINDS:
        text = strip_hidden(text) if kind in TEXT_KINDS or ctype in HTMLISH else plain(text)
    try:
        data = PARSERS[kind](text) if kind else None
    except DTDRefused:
        return _drop(url, "xml", now)
    except (ValueError, TypeError, KeyError, SyntaxError):                    # includes json.JSONDecodeError and ElementTree.ParseError
        return _drop(url, "parse", now)
    prev = ent.get("sha256")
    known[url] = {"sha256": sha, "fetched_at": now, "etag": etag if isinstance(etag, str) and len(etag) < 200 else ""}
    if prev == sha:
        return None
    return {"url": url, "sha256": sha, "fetched_at": now, "content_type": ctype, "body": _body(kind, data, text), "prev_sha256": prev, "data": data}
