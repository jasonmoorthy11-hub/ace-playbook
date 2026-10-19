"""Evidence cards (docs/design/compiler.md 5): one lesson tied to one externally observed incident.

Inputs, in order of authority: the lesson's user-origin quotes (re-located in a USER message of the originating
transcript), the exposure ledger, the citation ledger (recorded, flagged `self_report`, never admits anything) and
the offline detector fires recomputed from the transcript. A card stores quote OFFSETS, never text; sessions are
`sid:` + sha256[:16]; the payload is tool_name + tool_input only, scrubbed (a hit masks it, `replayable: false`).
LEGACY lessons (self-quoted `legacy:` evidence) get a card only through a human `incident` label (R3).
No inject/keys/reflect imports: a leaf JSONL reader and `norm` live here instead.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from pathlib import Path

from ..atomic import write_private
from ..clean.transcript import parse_records, read_records
from ..config import Config
from ..scrub import LESSON, scan
from ..scrub.quarantine import mask
from ..store import DeltaLog
from ..store.schema import GENESIS, ID_RE, LIVE_STATUSES, USER_ORIGINS, canonical, now_iso, record_hash
from . import catalogue
from .events import events_from_records, hash16

CARDS = "cards"
CARD_ID = re.compile(r"E-[0-9a-f]{8}")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_TRANSCRIPT = 64 * 1024 * 1024
MAX_PAYLOAD = 8192
KEYS = {"id", "v", "lesson_id", "provenance", "origin_session", "incident_session", "incident_kind", "label_ref",
        "quote_offsets", "detector", "detector_sha256", "tier", "incident_ts", "payload_sha256", "payload_ref",
        "replayable", "lesson_shown_in_incident_session", "agent_cited", "self_report", "scrub", "created"}


def norm(s: str) -> str:
    """Quote matching of reflect/provenance.py (whitespace-collapsed, case-folded); copied so no reflect import."""
    return " ".join(s.split()).casefold()


def sid(session_id: str) -> str:
    return "sid:" + hash16(session_id)


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def card_id(lesson_id: str, incident_sid: str, phash: str) -> str:
    return "E-" + hashlib.sha256(f"{lesson_id}|{incident_sid}|{phash}".encode()).hexdigest()[:8]


def verify_card(c: object) -> str | None:
    """Why a card is not internally consistent (tampered, hand-edited), else None."""
    if not isinstance(c, dict) or set(c) != KEYS:
        return "card keys differ from the schema"
    if c["v"] != 1 or c["tier"] != "offline" or c["self_report"] is not True or not (
            isinstance(c["payload_ref"], dict) and isinstance(c["payload_sha256"], str)):
        return "version, tier, self_report or payload"
    if c["id"] != card_id(str(c["lesson_id"]), str(c["incident_session"]), c["payload_sha256"]):
        return "id does not match lesson, incident session and payload hash"
    if c["replayable"] and payload_hash(c["payload_ref"]) != c["payload_sha256"]:
        return "replayable payload does not match its hash"
    if c["provenance"] == "standard":
        ok = bool(c["origin_session"]) and bool(c["quote_offsets"]) and c["label_ref"] is None
    else:
        ok = (c["provenance"] == "legacy" and c["origin_session"] is None and c["quote_offsets"] == []
              and c["incident_kind"] == "later" and re.fullmatch(r"lab:[0-9a-f]{8}", str(c["label_ref"])) is not None)
    return None if ok else "provenance fields"


def card_path(cfg: Config, cid: str) -> Path:
    if not isinstance(cid, str) or not CARD_ID.fullmatch(cid):
        raise ValueError("not a card id")
    base = Path(cfg.data_dir) / CARDS
    path = base / (cid + ".json")
    if base.is_symlink() or (base.exists() and not base.is_dir()) or path.parent.resolve() != base.resolve() or path.is_symlink():
        raise ValueError("cards directory or card file is a symlink, or the path escapes the cards directory")
    return path


def load_cards(cfg: Config, lesson_id: str | None = None) -> list[dict]:
    base = Path(cfg.data_dir) / CARDS
    out = []
    for p in sorted(base.glob("E-*.json")) if base.is_dir() and not base.is_symlink() else []:
        try:
            c = json.loads(p.read_text()) if p.is_file() and not p.is_symlink() else None
        except (OSError, ValueError):
            continue
        if verify_card(c) is None and p.name == c["id"] + ".json" and lesson_id in (None, c["lesson_id"]):
            out.append(c)
    return out


def _jsonl(path) -> list[dict]:
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _exposure(cfg: Config) -> dict[str, dict[str, bool]]:
    """lesson -> {session: shown?}; a held-out session is eligible but not shown. Ledger order kept."""
    out: dict[str, dict[str, bool]] = {}
    for rec in _jsonl(cfg.injections):
        s = rec.get("session_id")
        for key, shown in (("shown_ids", True), ("holdout_ids", False)):
            for lid in rec.get(key) if isinstance(rec.get(key), list) else []:
                if isinstance(s, str) and isinstance(lid, str):
                    out.setdefault(lid, {})[s] = out.get(lid, {}).get(s, False) or shown
    return out


def _labels(cfg: Config) -> dict[str, dict]:
    """ref -> `incident` label row; a broken hash chain voids every label (fail closed)."""
    prev, out = GENESIS, {}
    for row in _jsonl(cfg.path("labels.jsonl")):
        if row.get("prev_hash") != prev or row.get("hash") != record_hash(row):
            return {}
        prev = row["hash"]
        if row.get("label") == "incident" and isinstance(row.get("ref"), str):
            out.setdefault(row["ref"], row)
    return out


def _transcript(cfg: Config, session_id: str) -> Path | None:
    if not cfg.transcripts_root or not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
        return None
    for p in sorted(Path(cfg.transcripts_root).rglob(session_id + ".jsonl")):
        if p.is_file() and not p.is_symlink() and "subagents" not in p.parts and p.stat().st_size <= MAX_TRANSCRIPT:
            return p
    return None


def _offsets(path: Path, quotes: list[str]) -> list[dict]:
    """Where each quote sits in the normalised text of a user message (index among user messages)."""
    turns = [norm(t.text) for t in parse_records(read_records(path), 2000).turns
             if t.role == "user" and t.origin == "user_message"]
    out = []
    for q in quotes:
        qn = norm(q)
        for i, t in enumerate(turns):
            at = t.find(qn)
            if qn and at >= 0:
                out.append({"user_turn": i, "start": at, "end": at + len(qn), "quote_sha256": hash16(qn)})
                break
    return out


def _payload(event, name: str, state) -> dict | None:
    """tool_name + tool_input of the tool use (or result) that makes `name` fire, nothing else."""
    parts = [(tu.name, tu.input, dataclasses.replace(event, tool_uses=[tu], tool_results=[])) for tu in event.tool_uses]
    parts += [(tr.name, tr.input, dataclasses.replace(event, tool_uses=[], tool_results=[tr]))
              for tr in event.tool_results if tr.name]
    for tool, inp, sub in parts:
        if catalogue.evaluate(sub, state, names=[name], strict=True)[0]:
            p = {"tool_name": tool, "tool_input": dict(inp)}
            return p if len(canonical(p)) <= MAX_PAYLOAD else None
    return None


def _first_fire(path: Path, names: list[str]) -> dict | None:
    """First fire of a mapped detector in the transcript that has a minimizable payload."""
    state = None
    for ev in events_from_records(read_records(path)):
        fires, new, _ = catalogue.evaluate(ev, state, names=names, strict=True)
        for f in fires:
            payload = _payload(ev, f.detector, state)
            if payload:
                return {"detector": f.detector, "ts": ev.ts, "payload": payload}
        state = new
    return None


def _scrubbed(cfg: Config, payload: dict) -> tuple[dict, int]:
    leaves = [payload["tool_name"], *payload["tool_input"].values()]
    hits = scan(canonical(payload), LESSON, cfg.deny_terms) + [h for s in leaves for h in scan(s, LESSON, cfg.deny_terms)]
    if not hits:
        return payload, 0
    masked = {"tool_name": mask(payload["tool_name"], hits),
              "tool_input": {k: mask(v, hits) for k, v in payload["tool_input"].items()}}
    if scan(canonical(masked), LESSON, cfg.deny_terms):       # a hit that mask() could not locate: drop the payload
        masked = {"tool_name": "[MASKED]", "tool_input": {}}
    return masked, len(hits)


def _make(cfg, lid, inc, session, kind, origin, offsets, label, shown, cited, now) -> dict:
    payload, nhits = _scrubbed(cfg, inc["payload"])
    phash, row = payload_hash(inc["payload"]), catalogue.CATALOGUE[inc["detector"]]
    return {"id": card_id(lid, sid(session), phash), "v": 1, "lesson_id": lid,
            "provenance": "standard" if label is None else "legacy",
            "origin_session": sid(origin) if origin else None, "incident_session": sid(session),
            "incident_kind": kind, "label_ref": None if label is None else "lab:" + label["hash"][:8],
            "quote_offsets": offsets, "detector": inc["detector"], "detector_sha256": row.source_sha256,
            "tier": "offline", "incident_ts": inc["ts"], "payload_sha256": phash, "payload_ref": payload,
            "replayable": nhits == 0, "lesson_shown_in_incident_session": shown, "agent_cited": cited,
            "self_report": True, "scrub": {"profile": "LESSON", "hits": nhits}, "created": now}


def _lesson_cards(cfg, lid, les, expo, cited, labels, now) -> tuple[list[dict], list[dict]]:
    no = lambda reason, **kw: ([], [{"lesson_id": lid, "reason": reason, **kw}])        # noqa: E731
    if les is None or les["status"] not in LIVE_STATUSES:
        return no("lesson_unknown" if les is None else "lesson_not_live")
    names = [n for n, d in catalogue.CATALOGUE.items() if lid in d.lessons]
    if not names:
        return no("no_mapped_detector")
    ev = [e for e in les["evidence"] if e["origin"] in USER_ORIGINS and isinstance(e.get("session_id"), str)]
    std = [e for e in ev if not e["session_id"].startswith("legacy:")]
    mk = lambda inc, s, kind, origin, offs, label: _make(                                  # noqa: E731
        cfg, lid, inc, s, kind, origin, offs, label, expo.get(lid, {}).get(s, False), (s, lid) in cited, now)
    if std:                                                                      # standard path
        origin, offs, missing = None, [], False
        for s in dict.fromkeys(e["session_id"] for e in std):
            path = _transcript(cfg, s)
            missing = missing or path is None
            offs = _offsets(path, [e["quote"] for e in std if e["session_id"] == s]) if path else []
            if offs:
                origin = s
                break
        if not origin:
            return no("transcript_missing" if missing else "quote_not_in_user_message")
        inc, kind, at = _first_fire(path, names), "originating", origin
        for s in [] if inc else [x for x in expo.get(lid, {}) if x != origin]:
            p = _transcript(cfg, s)
            inc = inc or (_first_fire(p, names) if p else None)
            if inc:
                kind, at = "later", s
                break
        return ([mk(inc, at, kind, origin, offs, None)], []) if inc else no("no_incident")
    if not ev:
        return no("no_user_evidence")
    found, refused = [], []                                                      # legacy path (R3)
    for s in expo.get(lid, {}):
        p = _transcript(cfg, s)
        inc = _first_fire(p, names) if p else None
        if inc:
            ref = card_id(lid, sid(s), payload_hash(inc["payload"]))
            if ref in labels:
                found.append(mk(inc, s, "later", None, [], labels[ref]))
            else:
                refused.append({"lesson_id": lid, "reason": "legacy_unlabelled", "ref": ref})
    return (found, refused) if found or refused else no("no_eligible_session" if not expo.get(lid) else "no_incident")


def private_dir(base: Path) -> None:
    fresh = not base.exists()
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if fresh:
        os.chmod(base, 0o700)


def _write(cfg: Config, card: dict) -> None:
    path = card_path(cfg, card["id"])
    private_dir(path.parent)
    if path.exists():                                   # idempotent: keep the first `created`
        try:
            card["created"] = json.loads(path.read_text()).get("created", card["created"])
        except (OSError, ValueError, AttributeError):
            pass
    text = json.dumps(card, indent=1, sort_keys=True) + "\n"
    if not path.exists() or path.read_text() != text:
        write_private(path, text)


def build_cards(cfg: Config, lesson_id: str | None = None, *, write: bool = True,
                now: str | None = None) -> tuple[list[dict], list[dict]]:
    """Rebuild the cards of one lesson (or of every catalogue-mapped lesson): (cards, refusals).

    ValueError for a malformed lesson id or an unsafe cards dir; SchemaError for a delta log that fails
    verification. Cards are written (0600) only when `write` is true."""
    if lesson_id is not None and not (isinstance(lesson_id, str) and ID_RE.fullmatch(lesson_id)):
        raise ValueError("lesson id must look like L-xxxxxxxx")
    log = DeltaLog(cfg.deltas, tuple(cfg.deny_terms))
    log.verify()
    lessons, expo, labels = log.state(), _exposure(cfg), _labels(cfg)
    cited = {(r.get("session_id"), lid) for r in _jsonl(cfg.citations)
             for lid in (r.get("cited_ids") if isinstance(r.get("cited_ids"), list) else [])}
    ids = [lesson_id] if lesson_id else sorted({lid for d in catalogue.CATALOGUE.values() for lid in d.lessons})
    cards, refused = [], []
    for lid in ids:
        got, why = _lesson_cards(cfg, lid, lessons.get(lid), expo, cited, labels, now or now_iso())
        cards += got
        refused += why
    if write:
        for c in cards:
            _write(cfg, c)
    return sorted(cards, key=lambda c: (c["lesson_id"], c["id"])), refused
