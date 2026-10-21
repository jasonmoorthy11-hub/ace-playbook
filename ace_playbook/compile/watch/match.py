"""Per-class rules over the inventory snapshot (design 4.2, 4.3): plain code, no model, and no diff written by anything but a template.

A *result* is one source as `scan` hands it over: `{id, url, kind, tier, sha256, prev_sha256, data, quote, heading}`. `candidates` turns
results plus the inventory into proposal records of exactly design 4.3: `model_pin` (a deprecated or retiring model id that the inventory
pins; the diff replaces the id with the named successor, else a `lead` with no diff), `version` (npm `latest` or `stable` newer than the
installed `claude`), `docs` (a mapped page changed: its sitemap `lastmod` moved or its `.md` hash changed) and `lead` (a tier-1 feed
entry with a configured keyword). Text from the web reaches a record only as the title and the quote (cut to 120 and 400) and as a model
id or successor that passed `SAFE`; `verify` comes from a fixed template; tier-1 evidence alone never yields a diff (`check`).
"""

from __future__ import annotations

import hashlib
import re
import shlex
from datetime import datetime, timedelta

from .store import RANK, clean

DAYS, MAX_LEADS, MAX_LINE = 30, 10, 300
SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,79}")
NONE_WORDS = ("", "n/a", "na", "-", "none", "tbd")
KEYS = {"id", "created", "class", "elevated", "title", "evidence", "inventory_hits", "change", "verify", "expires", "status"}
CLASSES = ("model_pin", "version", "docs", "lead")
PLAIN = r"[\w./~@+:=-]+"
ARG = rf"(?:{PLAIN}|'[^']*')"
TEMPLATES = (re.compile(rf'grep -n "[A-Za-z0-9._:-]{{3,80}}" {ARG}( {ARG})*'), re.compile(r"claude --version"),
             re.compile(rf"curl -sI {ARG} \| head -1"))


def sh(text: str) -> str:
    return text if re.fullmatch(PLAIN, text) else shlex.quote(text)


def make_id(cls: str, target: str, sha: str, quote: str) -> str:
    return "W-" + hashlib.sha256("|".join((cls, target, sha, quote)).encode()).hexdigest()[:8]


def record(cls: str, title: str, ev: dict, hits: list[str], target: str, diff: str | None, verify: str, now: str) -> dict:
    ev = {"url": ev["url"], "sha256": ev["sha256"], "quote": clean(ev["quote"], 400), "tier": ev["tier"]}
    exp = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": make_id(cls, target, ev["sha256"], ev["quote"]), "created": now, "class": cls, "elevated": False, "title": clean(title),
            "evidence": [ev], "inventory_hits": hits,
            "change": {"target": target, "diff": diff, "diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None},
            "verify": verify, "expires": exp, "status": "open"}


def check(rec: dict) -> str | None:
    """None when `rec` is a valid W1 record, else the reason it is not (shape, caps, id, diff hash, tier-1 diff, verify template)."""
    try:
        ev, ch = rec["evidence"], rec["change"]
        if set(rec) != KEYS or set(ch) != {"target", "diff", "diff_sha256"} or rec["class"] not in CLASSES or rec["elevated"] is not False:
            return "shape"
        if not (isinstance(rec["title"], str) and 0 < len(rec["title"]) <= 120 and rec["title"] == clean(rec["title"])):
            return "title"
        if not (ev and all(set(e) == {"url", "sha256", "quote", "tier"} and isinstance(e["quote"], str) and 0 < len(e["quote"]) <= 400
                           and re.fullmatch(r"[0-9a-f]{64}", str(e["sha256"])) and e["tier"] in (0, 1) for e in ev)):
            return "evidence"
        if rec["id"] != make_id(rec["class"], ch["target"], ev[0]["sha256"], ev[0]["quote"]):
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


def _boundary(token: str) -> re.Pattern:
    return re.compile(r"(?<![\w.-])" + re.escape(token) + r"(?![\w-]|\.\d)")


def _lines(inv: dict, rx: re.Pattern) -> dict[str, dict[str, list]]:
    """`{key: {path: [[n, text]]}}` of the inventory lines that contain the token."""
    out: dict = {}
    for key, t in inv.get("targets", {}).items():
        for path, f in t.get("files", {}).items():
            hit = [ln for ln in f.get("lines", []) if isinstance(ln, list) and len(ln) == 2 and rx.search(str(ln[1]))]
            if hit:
                out.setdefault(key, {})[path] = hit
    return out


def _diff(files: dict, rx: re.Pattern, new: str) -> str:
    out = []
    for path, lines in sorted(files.items()):
        out += [f"--- a/{path}", f"+++ b/{path}"]
        for n, text in lines:
            out += [f"@@ -{n},1 +{n},1 @@", f"-{text}", f"+{rx.sub(lambda m: new, text)}"]
    return "\n".join(out) + "\n"


def _real(v: object) -> bool:
    return isinstance(v, str) and v.strip().lower() not in NONE_WORDS


def model_pin(inv: dict, results: list[dict], now: str) -> list[dict]:
    merged: dict = {}
    for res in (r for r in results if r["kind"] == "deprecations"):
        for row in res["data"]:
            m = merged.setdefault(row["model_id"], {"res": res, "had": False, **{k: None for k in ("deprecated", "retires", "successor", "row")}})
            for k in ("deprecated", "retires", "successor"):
                m[k] = m[k] or row[k]
            if m["row"] is None or (row["successor"] and not m["had"]):
                m["row"], m["had"], m["res"] = row["row"], bool(row["successor"]), res
    out = []
    for mid, m in sorted(merged.items()):
        if not SAFE.fullmatch(mid) or not (_real(m["deprecated"]) or _real(m["retires"])):
            continue
        rx, new = _boundary(mid), m["successor"] if _real(m["successor"]) and SAFE.fullmatch(m["successor"] or "") else None
        ev = {"url": m["res"]["url"], "sha256": m["res"]["sha256"], "quote": m["row"], "tier": m["res"]["tier"]}
        for key, files in sorted(_lines(inv, rx).items()):
            verify = f'grep -n "{mid}" ' + " ".join(sh(p) for p in sorted(files))
            whole = new and new != mid and all(len(str(t)) < MAX_LINE for ls in files.values() for _, t in ls)
            if whole:
                out.append(record("model_pin", f"Replace {mid} with {new} in {key}", ev, [key], key, _diff(files, rx, new), verify, now))
            else:
                out.append(record("lead", f"retirement with no successor named: {mid} ({key})", ev, [key], key, None, verify, now))
    return out


def _ver(text: object) -> tuple[int, ...] | None:
    m = re.search(r"\d+\.\d+\.\d+", text) if isinstance(text, str) else None
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


def _norm(url: str) -> str:
    return url.split("#")[0].rstrip("/").removesuffix(".md")


def docs(inv: dict, results: list[dict], mapping: dict, prev: dict, now: str) -> list[dict]:
    mapped = {_norm(u): keys for u, keys in mapping.get("docs", {}).items()}
    out = []

    def add(page: str, ev: dict) -> None:
        out.append(record("docs", f"docs page changed: {page}", ev, list(mapped[_norm(page)]), page, None, f"curl -sI {sh(page)} | head -1", now))

    for res in results:
        if res["kind"] == "sitemap":
            for loc, lm in sorted(res["data"].items()):
                if _norm(loc) in mapped and lm and prev.get(loc) and lm > prev[loc]:
                    add(loc, {"url": res["url"], "sha256": res["sha256"], "quote": f"{loc} lastmod {lm}", "tier": res["tier"]})
        elif res["kind"] == "md" and _norm(res["url"]) in mapped and res.get("prev_sha256") and res["prev_sha256"] != res["sha256"]:
            add(res["url"], {"url": res["url"], "sha256": res["sha256"], "quote": res["heading"] or res["quote"], "tier": res["tier"]})
    return out


def leads(results: list[dict], mapping: dict, now: str) -> list[dict]:
    words, out = [w.lower() for w in mapping.get("lead_keywords", [])], []
    for res in (r for r in results if r["kind"] in ("rss", "atom") and r["tier"] == 1):
        for e in res["data"]:
            if e["title"] and any(w in f"{e['title']} {e['summary']}".lower() for w in words):
                url = e["link"] or res["url"]
                ev = {"url": url, "sha256": res["sha256"], "quote": e["title"], "tier": 1}
                out.append(record("lead", e["title"], ev, [], (e["id"] or url)[:200], None, f"curl -sI {sh(url)} | head -1", now))
    return out[:MAX_LEADS]


def candidates(inv: dict, results: list[dict], mapping: dict, prev_lastmod: dict, now: str) -> list[dict]:
    """Every valid proposal record the rules produce, ranked (model_pin, version, docs, then leads), duplicates by id dropped."""
    found = [*model_pin(inv, results, now), *version(inv, results, now), *docs(inv, results, mapping, prev_lastmod, now),
             *leads(results, mapping, now)]
    ranked = sorted((r for r in found if check(r) is None), key=lambda r: (RANK.get(r["class"], 9), r["change"]["target"], r["id"]))
    return list({r["id"]: r for r in ranked}.values())
