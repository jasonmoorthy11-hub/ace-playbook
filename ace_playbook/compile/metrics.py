"""The measurement computations of `compile report` (docs/design/compiler.md 8, step 5c). Stdlib only; pure functions over lists of dict rows.

Per corpus and per proposal (a proposal is checked by one detector): `eligible` sessions, the session-level fire rate with a Wilson 95% interval,
fires per eligible session, repeat fires, friction, evasion candidates; per corpus: hook cost (p50 and p95 of `dur_ms`) and live-versus-offline
agreement. `-` means "not computable" (a zero denominator, or no data), never zero.

Private corpus: the sessions are the offline pass's `_session` summary rows in `detector_events.jsonl` (the newest row of a session wins; it
carries tool calls, human prompts and the detectors that consumed at least one event of the session); a fire only counts in an eligible session.
Synthetic corpus: every session of `shadow.synthetic.jsonl` is a corpus session, eligible for the detectors it fed, each distinct payload is
one tool call. Hook cost is that of firing calls, in-process (no interpreter start, imports or config load); the synthetic cost is the evaluator's.
"""

from __future__ import annotations

import hashlib
import math

Z = 1.96
SESSION = "_session"
DASH = "-"


def wilson(k: int, n: int, z: float = Z) -> list[float] | str:
    """[lo, hi] of the Wilson score interval for k of n (four decimals), `-` when n is 0."""
    if n == 0:
        return DASH
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def percentile(values: list[int], q: float) -> int | str:
    """Nearest-rank percentile (q in 0..1) of `values`; `-` for none."""
    return sorted(values)[max(0, math.ceil(q * len(values)) - 1)] if values else DASH


def ratio(num: float, den: float, scale: float = 1, digits: int = 2) -> float | str:
    return round(scale * num / den, digits) if den else DASH


def event_tags(event) -> set[str]:
    """The catalogue `consumes` tags one event carries (the end-of-session marker is left out: every session has one)."""
    tags = {f"PreToolUse:{tu.name}" for tu in event.tool_uses} | {"attachment:" + str(event.attachment.get("type", ""))}
    tags |= {"PreToolUse:*"} if event.tool_uses else set()
    tags |= {"transcript:tool_result"} if event.tool_results else set()
    tags |= {"transcript:user"} if event.rtype == "user" else set()
    tags |= {"transcript:human_prompt"} if event.human and not event.dup and not event.meta else set()
    return tags | ({"Stop"} if event.hook == "Stop" else set())


def session_row(events: list, session: str, ts: str, consumes: dict[str, tuple], stamp: str = "") -> dict:
    """The offline pass's per-session summary row: tool calls, human prompts, detectors with a consumed event. The id covers the
    values and `stamp` (the transcript's size and mtime), so an unchanged scan is not written again and every changed transcript adds
    a new row, even one that returns to an older state (the newest wins)."""
    calls = sum(len(e.tool_uses) for e in events)
    prompts = sum(1 for e in events if e.human and not e.dup and not e.meta)
    seen = set().union(set(), *(event_tags(e) for e in events))
    names = sorted(n for n, tags in consumes.items() if seen & set(tags))
    rid = hashlib.sha256(f"{session}|{SESSION}|{calls}|{prompts}|{','.join(names)}|{ts}|{stamp}".encode()).hexdigest()[:16]
    return {"ts": ts, "v": 1, "id": rid, "session": session, "detector": SESSION, "tier": "offline", "count": calls,
            "prompts": prompts, "eligible": names}


def latest_sessions(ev_rows: list[dict]) -> dict[str, dict]:
    """{session: newest `_session` row} (file order: the last one wins)."""
    return {r["session"]: r for r in ev_rows if r.get("detector") == SESSION and isinstance(r.get("session"), str)}


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _sessions(corpus: str, rows: list[dict], ev_rows: list[dict]) -> dict[str, dict]:
    """{session: {"calls", "prompts", "eligible": set of detectors}}: from the summary rows (private) or from the rows (synthetic:
    a tool call is a distinct payload of the session, however many detectors it was fed to)."""
    if corpus == "synthetic":
        out: dict[str, dict] = {}
        for r in rows:
            s = out.setdefault(r["session"], {"calls": 0, "prompts": 0, "eligible": set(), "payloads": set()})
            s["payloads"].add(r["payload_sha256"])
            s["calls"] = len(s["payloads"])
            s["eligible"].add(r.get("detector"))
        return out
    return {k: {"calls": _int(v.get("count")), "prompts": _int(v.get("prompts")),
                "eligible": {n for n in v.get("eligible", []) if isinstance(n, str)}} for k, v in latest_sessions(ev_rows).items()}


def _fire(r: dict) -> bool:
    return r.get("would_fire") is not False


def _agreement(rows: list[dict], det_of: dict[str, str], sessions: dict, ev_rows: list[dict], stop: set[str]) -> dict | str:
    """{"live_only", "offline_only"}: live fires (detector fires in offline-scanned sessions) with no offline event of the same detector,
    session and payload hash, and offline events of a deployed detector dated at or after THAT detector's first live row with no live fire.
    A revocation gap is not excluded. Left out: `cause: truncated` rows (fail-closed, not detector fires), and rows and proposals of the
    `Stop` event (their payload hash is the raw hook input, which no offline row has). `-` without live rows."""
    mine = [r for r in rows if r["proposal_id"] in det_of and r["proposal_id"] not in stop and r.get("event") != "Stop"]
    if not mine:
        return DASH
    start: dict[str, str] = {}
    for r in mine:
        d = det_of[r["proposal_id"]]
        start[d] = min(start.get(d, r["ts"]), r["ts"])
    live = {(r["session"], r["payload_sha256"], det_of[r["proposal_id"]]) for r in mine
            if _fire(r) and r.get("cause") != "truncated" and r["session"] in sessions}
    off = {(e.get("session"), e.get("payload_sha256"), e.get("detector")) for e in ev_rows
           if e.get("tier") == "offline" and e.get("detector") in start and str(e.get("ts", "")) >= start[e["detector"]]}
    return {"live_only": len(live - off), "offline_only": len(off - live)}


def compute(corpus: str, rows: list[dict], det_of: dict[str, str], ev_rows: list[dict], evasion: list[dict],
            stop: set[str] = frozenset()) -> dict:
    """{"proposals": {pid: {...}}, "corpus": {...}} for one corpus. `rows` are every row of its shadow file (a `would_fire: false` row
    is an evaluated event that did not fire), `det_of` maps proposal id to detector, `ev_rows` and `evasion` are the offline files,
    `stop` the proposals of the Stop event. `fires` are detector fires in eligible sessions; `cause: truncated` rows are counted apart
    (`truncated_fires`, fail-closed) but still count as messages in the friction (the hook emitted them)."""
    sess = _sessions(corpus, rows, ev_rows)
    fixes = [r for r in ev_rows if r.get("detector") == "user_correction" and r.get("session") in sess]
    out: dict[str, dict] = {}
    for pid, det in det_of.items():
        elig = {s for s, v in sess.items() if det in v["eligible"]}
        mine = [r for r in rows if r["proposal_id"] == pid and _fire(r) and r["session"] in elig]
        fires = [r for r in mine if r.get("cause") != "truncated"]
        per = {s: sum(r["session"] == s for r in fires) for s in {r["session"] for r in fires}}
        warned = sum(r.get("mode") in ("warn", "block") for r in mine)
        calls, prompts = sum(sess[s]["calls"] for s in elig), sum(sess[s]["prompts"] for s in elig)
        fixed = sum(_int(r.get("count")) for r in fixes if r["session"] in elig)
        out[pid] = {"detector": det, "eligible": len(elig), "sessions_with_fire": len(per),
                    "fire_rate": ratio(len(per), len(elig), digits=4), "wilson95": wilson(len(per), len(elig)),
                    "fires": len(fires), "truncated_fires": len(mine) - len(fires), "fires_per_eligible": ratio(len(fires), len(elig)),
                    "repeat_fires": sum(n - 1 for n in per.values()),
                    "friction": {"messages_per_100_calls": ratio(warned, calls, 100),
                                 "corrections_per_100_prompts": ratio(fixed, prompts, 100) if corpus == "private" else DASH},
                    "evasion": sum(e.get("proposal_id") == pid for e in evasion)}
    cost = [r["dur_ms"] for r in rows if isinstance(r.get("dur_ms"), int) and not isinstance(r["dur_ms"], bool)]
    agree = _agreement(rows, det_of, sess, ev_rows, set(stop)) if corpus == "private" else DASH
    waiting = sum(r["proposal_id"] in det_of and _fire(r) and r["session"] not in sess for r in rows)
    return {"proposals": out, "corpus": {"sessions": len(sess), "awaiting_offline_scan": waiting, "cost_rows": len(cost),
                                          "cost_p50_ms": percentile(cost, 0.5), "cost_p95_ms": percentile(cost, 0.95), "agreement": agree}}
