"""Per-class rules over the inventory snapshot (design 4.2, 4.3): plain code, no model, and no diff written by anything but a template.

A *result* is one source as `scan` hands it over: `{id, url, kind, tier, sha256, prev_sha256, data, quote, heading, body}`. `candidates` turns
results plus the inventory into proposal records of exactly design 4.3 (`records.py`): `model_pin` (`pin.py`: a deprecated or retiring model id
that the inventory pins; the diff replaces the id with the named successor, else a `lead` with no diff), `version` (npm `latest` or `stable`
newer than the installed `claude`), `docs` (a mapped page changed: its sitemap `lastmod` moved, unless it is more than a day ahead, or its
`.md` hash changed) and `lead` (a tier-1 feed entry with a configured keyword). Text from the web reaches a record only as the title
(of a lead), the quote (cut to 120 and 400),
and as a model id or successor that passed `pin.SAFE` and `pin.MODEL_ID`; `change.target` is an inventory key, a mapped docs URL, a source id
or `claude_version`; `verify` comes from a fixed template; tier-1 evidence alone never yields a diff (`records.check`). Every quote is
checked against the body of the result that carries its sha256 (a heading that is not a line of the body gives way to the result's quote).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from .pin import model_pin  # noqa: F401 - part of this module's surface
from .records import check, make_id, record, sh  # noqa: F401
from .store import RANK

MAX_LEADS = 10


def _ver(text: object) -> tuple[int, ...] | None:
    m = re.search(r"[0-9]+\.[0-9]+\.[0-9]+", text) if isinstance(text, str) else None             # ASCII digits only
    return tuple(int(x) for x in m.group().split(".")) if m else None


def version(inv: dict, results: list[dict], now: str) -> list[dict]:
    have = _ver(inv.get("claude_version"))
    out = []
    for res in (r for r in results if r["kind"] == "npm") if have else ():
        pick = next((t for t in ("latest", "stable") if (_ver(res["data"].get(t)) or ()) > have), None)
        if pick:
            ev = {"url": res["url"], "sha256": res["sha256"], "quote": res["quote"], "tier": res["tier"]}
            title = f"Claude Code {res['data'][pick]} available (installed {'.'.join(map(str, have))})"
            out.append(record("version", title, ev, ["claude_version"], "claude_version", None, "claude --version", now))
    return out


def plausible(lastmod: object, now: str) -> bool:
    """A sitemap lastmod that is a date and at most a day ahead of `now`: a lastmod of 9999-12-31 would mask every later real change."""
    try:
        day = datetime.strptime(str(lastmod)[:10], "%Y-%m-%d").date()
        return day <= datetime.strptime(now[:10], "%Y-%m-%d").date() + timedelta(days=1)
    except (ValueError, OverflowError):
        return False


def norm(url: str) -> str:
    return url.split("#")[0].rstrip("/").removesuffix(".md")


def docs(inv: dict, results: list[dict], mapping: dict, prev: dict, now: str) -> list[dict]:
    mapped = {norm(u): (u, keys) for u, keys in mapping.get("docs", {}).items()}              # normalised URL -> (the URL as mapped, keys)
    out = []

    def add(seen_url: str, ev: dict) -> None:
        page, keys = mapped[norm(seen_url)]                  # title, target and command use the operator's mapped URL, not the page's own
        out.append(record("docs", f"docs page changed: {page}", ev, list(keys), page, None, f"curl -sI {sh(page)} | head -1", now))

    for res in results:
        if res["kind"] == "sitemap":
            for loc, lm in sorted(res["data"].items()):
                if norm(loc) in mapped and plausible(lm, now) and prev.get(loc) and lm > prev[loc]:
                    add(loc, {"url": res["url"], "sha256": res["sha256"], "quote": f"{loc} lastmod {lm}", "tier": res["tier"]})
        elif res["kind"] == "md" and norm(res["url"]) in mapped and res.get("prev_sha256") and res["prev_sha256"] != res["sha256"]:
            head = res["heading"] if res["heading"] in res.get("body", res["heading"]) else ""         # a collapsed first-400 heading is not a line
            add(res["url"], {"url": res["url"], "sha256": res["sha256"], "quote": head or res["quote"], "tier": res["tier"]})
    return out


def keyword_entries(results: list[dict], mapping: dict):
    """Every (result, entry) of a tier-1 feed whose entry has a title and a configured keyword: what `leads` records (before its cap of MAX_LEADS)."""
    words = [w.lower() for w in mapping.get("lead_keywords", [])]
    for res in (r for r in results if r["kind"] in ("rss", "atom") and r["tier"] == 1):
        for e in res["data"]:
            if e["title"] and any(w in f"{e['title']} {e['summary']}".lower() for w in words):
                yield res, e


def flagged(results: list[dict], mapping: dict) -> set[tuple[str, str]]:
    """The (source id, title) of EVERY keyword entry, not only the MAX_LEADS the rules keep: the Reader never reads an entry the rules can flag."""
    return {(res["id"], e["title"]) for res, e in keyword_entries(results, mapping)}


def leads(results: list[dict], mapping: dict, now: str) -> list[dict]:
    out = []
    for res, e in keyword_entries(results, mapping):
        url = e["link"] or res["url"]
        ev = {"url": url, "sha256": res["sha256"], "quote": e["title"], "tier": 1}
        out.append(record("lead", e["title"], ev, [], res["id"], None, f"curl -sI {sh(url)} | head -1", now))
    return out[:MAX_LEADS]


def candidates(inv: dict, results: list[dict], mapping: dict, prev_lastmod: dict, now: str) -> list[dict]:
    """Every valid proposal record the rules produce, ranked (model_pin, version, docs, then leads), duplicates by id dropped."""
    found = [*model_pin(inv, results, now), *version(inv, results, now), *docs(inv, results, mapping, prev_lastmod, now),
             *leads(results, mapping, now)]
    bodies = {r["sha256"]: r["body"] for r in results if isinstance(r.get("body"), str)}
    valid = (r for r in found if check(r, bodies.get(r["evidence"][0]["sha256"])) is None)
    ranked = sorted(valid, key=lambda r: (RANK.get(r["class"], 9), r["change"]["target"], r["id"]))
    return list({r["id"]: r for r in ranked}.values())
