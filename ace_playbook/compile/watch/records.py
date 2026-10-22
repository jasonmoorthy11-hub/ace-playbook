"""The proposal record of design 4.3 (exactly): its builder, its id, its validator. Stdlib only.

`record` builds `{id, created, class, elevated, title, evidence[{url, sha256, quote, tier}], inventory_hits, change{target, diff, diff_sha256},
verify, expires, status}`; `id` is `W-` plus the first 8 hex of sha256 of `class|target|evidence sha|quote`, with `|model id` appended when
the (code-made) title names a model: two ids in one table cell share a quote and must not share an id. A quote is ONE line of the fetched
body, verbatim (`quote_line`); `check(rec, body)` also requires it to be a substring of `body` when the body is given. `check` returns None
for a valid W1 record, else the reason: shape, title, evidence, quote, id, diff hash, "tier-1 evidence alone cannot carry a diff", verify
(one of three fixed command templates) or dates. `change.target` is always an inventory key, a mapped docs URL, a source id or
`claude_version`, never text taken from a page.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from datetime import datetime, timedelta

from .store import clean

DAYS = 30
KEYS = {"id", "created", "class", "elevated", "title", "evidence", "inventory_hits", "change", "verify", "expires", "status"}
CLASSES = ("model_pin", "version", "docs", "lead")
PLAIN = r"[\w./~@+:=-]+"
ARG = rf"(?:{PLAIN}|'[^']*')"
TITLE = re.compile(r"Replace (?P<mid>\S+) with (?P<succ>\S+) in |retirement with no successor named: (?P<lead>\S+) \(")
TEMPLATES = (re.compile(rf'grep -n "[A-Za-z0-9._:-]{{3,80}}" {ARG}( {ARG})*'), re.compile(r"claude --version"),
             re.compile(rf"curl -sI {ARG} \| head -1"))


def sh(text: str) -> str:
    return text if re.fullmatch(PLAIN, text) else shlex.quote(text)


def title_model(title: object) -> tuple[str, str]:
    """(model id, successor) named by a code-made model_pin or retirement-lead title, else ("", "")."""
    m = TITLE.match(title) if isinstance(title, str) else None
    return ((m.group("mid") or m.group("lead")), m.group("succ") or "") if m else ("", "")


def make_id(cls: str, target: str, sha: str, quote: str, model: str = "") -> str:
    return "W-" + hashlib.sha256("|".join((cls, target, sha, quote, *([model] if model else []))).encode()).hexdigest()[:8]


def quote_line(body: str, *prefer: str) -> str:
    """One line of `body`, stripped, at most 400 characters and unchanged by `clean`, so it is a verbatim substring of the body: the
    first line that holds one of the `prefer` texts, else the first line. "" for an empty body or when no line is clean (the record
    that would carry it fails `check` and is dropped)."""
    lines = [ln for ln in (x.strip()[:400] for x in body.split("\n")) if ln]
    ok = [ln for ln in lines if clean(ln, 400) == ln]
    pick = next((ln for ln in ok if any(p and p in ln for p in prefer)), ok[0] if ok else None)
    return pick or ""


def record(cls: str, title: str, ev: dict, hits: list[str], target: str, diff: str | None, verify: str, now: str) -> dict:
    ev = {"url": ev["url"], "sha256": ev["sha256"], "quote": clean(ev["quote"], 400), "tier": ev["tier"]}
    exp = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = clean(title)
    return {"id": make_id(cls, target, ev["sha256"], ev["quote"], title_model(title)[0]), "created": now, "class": cls, "elevated": False, "title": title,
            "evidence": [ev], "inventory_hits": hits,
            "change": {"target": target, "diff": diff, "diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None},
            "verify": verify, "expires": exp, "status": "open"}


def check(rec: dict, body: str | None = None) -> str | None:
    """None when `rec` is a valid W1 record, else the reason it is not (see the module docstring)."""
    try:
        ev, ch = rec["evidence"], rec["change"]
        if set(rec) != KEYS or set(ch) != {"target", "diff", "diff_sha256"} or rec["class"] not in CLASSES or rec["elevated"] is not False:
            return "shape"
        if not (isinstance(rec["title"], str) and 0 < len(rec["title"]) <= 120 and rec["title"] == clean(rec["title"])):
            return "title"
        if not (ev and all(set(e) == {"url", "sha256", "quote", "tier"} and isinstance(e["quote"], str) and 0 < len(e["quote"]) <= 400
                           and re.fullmatch(r"[0-9a-f]{64}", str(e["sha256"])) and e["tier"] in (0, 1) for e in ev)):
            return "evidence"
        if not all(e["quote"] == clean(e["quote"], 400) and (body is None or e["quote"] in body) for e in ev):
            return "quote"
        if rec["id"] != make_id(rec["class"], ch["target"], ev[0]["sha256"], ev[0]["quote"], title_model(rec["title"])[0]):
            return "id"
        if ch["diff"] is not None and (not isinstance(ch["diff"], str) or ch["diff_sha256"] != hashlib.sha256(ch["diff"].encode()).hexdigest()):
            return "diff hash"
        if ch["diff"] is None and ch["diff_sha256"] is not None:
            return "diff hash"
        if ch["diff"] is not None and all(e["tier"] == 1 for e in ev):
            return "tier-1 evidence alone cannot carry a diff"
        if not any(t.fullmatch(rec["verify"]) for t in TEMPLATES):
            return "verify"
        return None if rec["expires"] > rec["created"] and rec["status"] in ("open", "queued") else "dates"
    except (KeyError, TypeError):
        return "shape"
