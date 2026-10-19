"""Spec-only proposals (docs/design/compiler.md 6): deterministic JSON naming one catalogue detector.

No model writes a check, matcher or message: `build` fills a template from the catalogue row and the admitted
cards. `validate` fails closed in this order and names the failing step: schema, message (inert_reason),
regressions (none may fire), replay (must fire), scrub, path. New proposals are ALWAYS `observe`; a block-capable
detector must carry `check.on_truncated: "fire"`. Stdlib-only imports on the hook path (scrub loads lazily).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from ..atomic import write_private
from ..canon import ID_RE, canonical, now_iso
from ..config import Config
from ..textrules import inert_reason
from . import catalogue, evidence
from .events import event_from_hook

PROPOSALS = "proposals"
CREATED_BY = "ace_playbook.compile 0.2.0"
EXAMPLES = Path(__file__).resolve().parent.parent / "fixtures" / "compile" / "examples.json"
STEPS = ("schema", "message", "regressions", "replay", "scrub", "path")
EVENTS = ("PreToolUse", "Stop")
PID = re.compile(r"P-[0-9a-f]{8}")
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
SHA256, EVIDENCE_REF = re.compile(r"[0-9a-f]{64}"), re.compile(r"sid:[0-9a-f]{16}#[0-9a-f]{16}|legacy#lab:[0-9a-f]{8}")
KEYS = {"id", "v", "lesson_id", "event", "matcher", "check", "message", "mode", "regression_examples", "replay",
        "provenance", "created", "created_by", "reviewed_by"}
CHECK_KEYS, PROV_KEYS = {"detector", "params", "on_truncated"}, {"cards", "lesson_evidence", "source_sha256"}
INPUT_KEYS = {"command", "file_path", "notebook_path"}
MIN_REGRESSIONS, MAX_BYTES = 3, 262144
MESSAGES = {
    "rm_rf_nonscratch": "recursive force delete outside a scratch folder. Confirm the target with the user first.",
    "rm_rf": "recursive force delete. Confirm the target with the user first.",
}


class ProposalError(ValueError):
    def __init__(self, step: str, detail: str, report: "Report | None" = None) -> None:
        super().__init__(f"{step}: {detail}")
        self.step, self.detail, self.report = step, detail, report


@dataclass
class Report:
    results: list = field(default_factory=list)       # (step, True|False|None for skipped, detail)

    @property
    def failed(self) -> str | None:
        return next((s for s, ok, _ in self.results if ok is False), None)

    @property
    def ok(self) -> bool:
        return all(ok is True for _, ok, _ in self.results)

    def detail(self, step: str) -> str:
        return next(d for s, _, d in self.results if s == step)


def proposal_id(p: dict) -> str:
    body = {"lesson_id": p["lesson_id"], "event": p["event"], "matcher": p["matcher"],
            "detector": p["check"]["detector"], "params": p["check"]["params"],
            "cards": sorted(p["provenance"]["cards"]), "source_sha256": p["provenance"]["source_sha256"]}
    return "P-" + hashlib.sha256(canonical(body).encode()).hexdigest()[:8]


def _matchers(det, event: str) -> set[str]:
    return {c.split(":", 1)[1] if ":" in c else "*" for c in det.consumes if c == event or c.startswith(event + ":")}


def fires(check: dict, payload: dict) -> bool:
    """Run the check's detector over one hook-shaped payload with a fresh state (paths count as existing)."""
    ev = event_from_hook({"hook_event_name": "PreToolUse", "session_id": "replay", "tool_use_id": "replay",
                          "tool_name": payload["tool_name"], "tool_input": payload["tool_input"]}, probe=lambda _p: True)
    if check.get("on_truncated") == "fire" and any(tu.truncated for tu in ev.tool_uses):
        return True
    return bool(catalogue.evaluate(ev, None, names=[check["detector"]], strict=True)[0])


def proposal_path(cfg: Config, pid: str) -> Path:
    if not isinstance(pid, str) or not PID.fullmatch(pid):
        raise ValueError("not a proposal id")
    base = Path(cfg.data_dir) / PROPOSALS
    path = base / (pid + ".json")
    if base.is_symlink() or (base.exists() and not base.is_dir()) or path.parent.resolve() != base.resolve() or path.is_symlink():
        raise ValueError("proposals directory or file is a symlink, or the path escapes the proposals directory")
    return path


def build(cfg: Config, lesson_id: str, detector: str, cards: list[dict], *, now: str | None = None) -> dict:
    """The proposal for (lesson, detector): the catalogue must map them and an admitted card must exist."""
    det = catalogue.CATALOGUE.get(detector)
    if det is None or det.compilable == "none" or detector not in MESSAGES:
        raise ProposalError("schema", "detector is not in the catalogue or not compilable")
    if lesson_id not in det.lessons:
        raise ProposalError("schema", "lesson is not mapped to this detector in the catalogue")
    mine = sorted((c for c in cards if c["lesson_id"] == lesson_id and c["detector"] == detector), key=lambda c: c["id"])
    ex = json.loads(EXAMPLES.read_text())["detectors"].get(detector)
    if not mine:
        raise ProposalError("schema", "no admitted evidence card for this lesson and detector")
    if not ex:
        raise ProposalError("regressions", "no synthetic examples for this detector")
    check = {"detector": detector, "params": {}, "on_truncated": "fire" if det.compilable == "block" else "ignore"}
    card = next((c for c in mine if c["replayable"] and fires(check, c["payload_ref"])), None)
    replay = ({"evidence_card": card["id"], "payload_sha256": card["payload_sha256"], "expect": "fire", "source": "private"}
              if card else {**ex["fire"], "expect": "fire", "source": "synthetic"})
    refs = sorted({f"{c['origin_session']}#{o['quote_sha256']}" for c in mine for o in c["quote_offsets"]}
                  | {f"legacy#{c['label_ref']}" for c in mine if c["provenance"] == "legacy"})
    p = {"id": "P-00000000", "v": 1, "lesson_id": lesson_id, "event": "PreToolUse",
         "matcher": "|".join(sorted(_matchers(det, "PreToolUse"))), "check": check, "message": "", "mode": "observe",
         "regression_examples": [{**x, "expect": "no_fire", "source": "synthetic"} for x in ex["no_fire"]],
         "replay": replay, "provenance": {"cards": [c["id"] for c in mine], "lesson_evidence": refs,
                                           "source_sha256": det.source_sha256},
         "created": now or now_iso(), "created_by": CREATED_BY, "reviewed_by": None}
    p["id"] = proposal_id(p)
    p["message"] = f"ACE {p['id']}: {MESSAGES[detector]}"
    return p


def _payload_err(x: object, keys: set[str]) -> str | None:
    if not (isinstance(x, dict) and set(x) == keys):
        return "example keys differ"
    ti = x["tool_input"]
    if not (isinstance(x["tool_name"], str) and 0 < len(x["tool_name"]) <= 128 and isinstance(ti, dict)
            and set(ti) <= INPUT_KEYS and all(isinstance(v, str) for v in ti.values())):
        return "example payload must be tool_name plus string tool_input fields"
    return None if len(canonical({"tool_name": x["tool_name"], "tool_input": ti})) <= evidence.MAX_PAYLOAD else "example too large"


def _cards(p: dict, cfg: Config, cards: dict | None) -> dict:
    return cards if cards is not None else {c["id"]: c for c in evidence.load_cards(cfg, p["lesson_id"])}


def _labelled(c: dict, cfg: Config) -> bool:
    """A legacy card is only as good as its human `incident` label: ref = card id, label_ref = hash prefix."""
    row = evidence._labels(cfg).get(c["id"])
    return bool(row) and c["label_ref"] == "lab:" + row["hash"][:8]


def _schema(p: object, cfg: Config, cards: dict | None) -> str | None:
    if not isinstance(p, dict) or set(p) != KEYS:
        return "keys differ from the proposal schema"
    chk, prov = p["check"], p["provenance"]
    if not (isinstance(chk, dict) and set(chk) == CHECK_KEYS and isinstance(prov, dict) and set(prov) == PROV_KEYS):
        return "check or provenance keys differ"
    if not (isinstance(p["id"], str) and PID.fullmatch(p["id"]) and p["v"] == 1 and type(p["v"]) is int):
        return "id or version malformed"
    if not (isinstance(p["lesson_id"], str) and ID_RE.fullmatch(p["lesson_id"])):
        return "lesson_id malformed"
    det = catalogue.CATALOGUE.get(chk["detector"]) if isinstance(chk["detector"], str) else None
    if det is None or det.compilable == "none":
        return "detector is not in the catalogue or not compilable"
    if chk["params"] != {} or chk["on_truncated"] not in ("fire", "ignore"):
        return "params must be empty and on_truncated fire|ignore"
    if det.compilable == "block" and chk["on_truncated"] != "fire":
        return "a block-capable detector needs check.on_truncated 'fire'"
    if p["lesson_id"] not in det.lessons:
        return "lesson is not mapped to this detector"
    if p["mode"] != "observe":
        return "mode must be observe at creation"
    valid = _matchers(det, p["event"]) if p["event"] in EVENTS else set()
    if not (isinstance(p["matcher"], str) and p["matcher"] and set(p["matcher"].split("|")) <= valid):
        return "event or matcher not consumed by the detector"
    if not (isinstance(p["created"], str) and STAMP.fullmatch(p["created"]) and isinstance(p["created_by"], str)
            and len(p["created_by"]) <= 64 and p["reviewed_by"] is None):
        return "created, created_by or reviewed_by malformed"
    ids = prov["cards"]
    if not (isinstance(ids, list) and ids and len(set(ids)) == len(ids) and all(isinstance(i, str) for i in ids)
            and isinstance(prov["lesson_evidence"], list)
            and all(isinstance(i, str) and EVIDENCE_REF.fullmatch(i) for i in prov["lesson_evidence"])):
        return "provenance malformed"
    if prov["source_sha256"] != det.source_sha256:
        return "catalogue source_sha256 is stale"
    if p["id"] != proposal_id(p):
        return "id does not match the proposal content"
    known = _cards(p, cfg, cards)
    for i in ids:
        c = known.get(i)
        if c is None or evidence.verify_card(c) or c["lesson_id"] != p["lesson_id"] or c["detector"] != chk["detector"]:
            return "provenance card missing or inconsistent"
        if c["provenance"] == "legacy" and not _labelled(c, cfg):
            return "legacy card has no matching incident label in the labels chain"
    rx, rp = p["regression_examples"], p["replay"]
    if not isinstance(rx, list) or not isinstance(rp, dict):
        return "regression_examples or replay malformed"
    for x in rx:
        if _payload_err(x, {"tool_name", "tool_input", "expect", "source"}) or x["expect"] != "no_fire" or x["source"] != "synthetic":
            return "regression example malformed"
    if rp.get("source") == "synthetic":
        err = _payload_err(rp, {"tool_name", "tool_input", "expect", "source"})
    else:
        ok = set(rp) == {"evidence_card", "payload_sha256", "expect", "source"} and rp["source"] == "private"
        err = None if ok and isinstance(rp["payload_sha256"], str) and SHA256.fullmatch(rp["payload_sha256"]) else "replay keys"
    return err or (None if rp["expect"] == "fire" else "replay must expect fire")


def _leaves(x: object):
    if isinstance(x, str):
        yield x
    for v in x.values() if isinstance(x, dict) else x if isinstance(x, list) else ():
        yield from _leaves(v)


def _message(p: dict) -> str | None:
    m = p["message"]
    if not (isinstance(m, str) and 20 <= len(m) <= 300 and m.startswith(f"ACE {p['id']}: ")):
        return "message must be 20 to 300 chars and start with 'ACE <id>: '"
    why = inert_reason(m)
    if why:
        return f"message is not inert ({why})"
    if any(len(s) >= 8 and s in m for s in _leaves([p["regression_examples"], p["replay"]])):
        return "message interpolates payload text"
    return None


def _regressions(p: dict) -> str | None:
    if len(p["regression_examples"]) < MIN_REGRESSIONS:
        return f"need at least {MIN_REGRESSIONS} no_fire examples"
    bad = [i for i, x in enumerate(p["regression_examples"]) if fires(p["check"], x)]
    return f"regression example(s) {bad} fire" if bad else None


def _replay(p: dict, cfg: Config, cards: dict | None) -> str | None:
    rp = p["replay"]
    if rp["source"] == "private":
        c = _cards(p, cfg, cards).get(rp["evidence_card"])
        if not (c and rp["evidence_card"] in p["provenance"]["cards"] and c["replayable"]
                and c["payload_sha256"] == rp["payload_sha256"]):
            return "private replay card missing, not replayable or hash mismatch"
        rp = c["payload_ref"]
    return None if fires(p["check"], rp) else "replay payload does not fire"


def _scrub(p: dict, cfg: Config) -> str | None:
    from . import propscrub                          # lazy: the scrub engine stays out of the hook path
    return propscrub.error(p, cfg)


def _path(p: dict, cfg: Config, given: Path | None) -> str | None:
    target = proposal_path(cfg, p["id"]).resolve()
    ok = given is None or (not Path(given).is_symlink() and Path(given).resolve() == target)
    return None if ok else "file is not at proposals/<id>.json under the data directory"


def validate(p: object, cfg: Config, cards: dict | None = None, path: Path | None = None,
             hot: bool = False) -> Report:
    """Run the six steps in order; later steps are `skipped` once one fails. `cards` overrides the disk lookup.
    `hot` (the shadow hook) does not rerun the scrub step: it needs the scrub engine, and the approved file's
    proposal_sha256 already binds the text that passed scrub when the proposal was written."""
    rep = Report()
    steps = (lambda: _schema(p, cfg, cards), lambda: _message(p), lambda: _regressions(p),
             lambda: _replay(p, cfg, cards), lambda: _scrub(p, cfg), lambda: _path(p, cfg, path))
    for name, fn in zip(STEPS, steps):
        if rep.failed or (hot and name == "scrub"):
            rep.results.append((name, None, "skipped") if rep.failed else (name, True, "hash-bound, not rerun"))
            continue
        try:
            err = fn()
        except Exception as exc:                           # fail closed, never leak the exception text
            err = f"{type(exc).__name__} (fail closed)"
        rep.results.append((name, err is None, err or "ok"))
    return rep


def load(path: Path) -> object:
    """Read a proposal file: regular file only (no symlink, FIFO or oversize), JSON only."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise ValueError
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError
        return json.loads(raw)
    except (OSError, ValueError, RecursionError):
        raise ProposalError("schema", "file unreadable, symlinked, oversize or not JSON") from None


def write(cfg: Config, p: dict, cards: dict | None = None) -> Path:
    """Validate, then write `proposals/<id>.json` at 0600. A scrub hit goes to quarantine, never to disk."""
    rep = validate(p, cfg, cards)
    if not rep.ok:
        if rep.failed == "scrub":
            from . import propscrub
            propscrub.to_quarantine(p, cfg)
        raise ProposalError(rep.failed, rep.detail(rep.failed), rep)
    path = proposal_path(cfg, p["id"])
    evidence.private_dir(path.parent)
    write_private(path, json.dumps(p, indent=1, sort_keys=True) + "\n")
    return path
