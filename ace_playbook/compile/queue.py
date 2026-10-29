"""`compile label --queue` (docs/design/compiler.md 7.3 "Label queue"): the blind queue of unlabelled fired shadow rows.

`pending` lists the fired rows of the private corpus that no label covers, oldest first, one per `labels.row_id`; an item
carries ids, hashes and the proposal's aggregate tally, never an existing label, a note, a command or any text (payloads are
hashes only); during a randomized arm phase its mode is `hidden` (`labels_queue`, which also draws the card). `run` shows one card per
item at a terminal and appends the human `tp` or `fp` row the operator picks; an `fp` needs a scrubbed note.
Not on the hook path: `shadow.py`, `catalogue.py`, `detectors.py` and `events.py` never import it.
Imports: stdlib, `..canon`, `..config`, `.labels`, `.labels_queue`, `.report`, `.status`; `proposal` and `shadow` load lazily.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Callable

from ..canon import now_iso
from ..config import Config
from . import labels, labels_queue, report, status

NEEDED, TRIES = 5, 3                                        # true positives a promotion needs; key attempts per row
KEYS = {"1": "tp", "2": "fp", "3": "skip", "q": "quit"}
CONTROL = "1 tp / 2 fp / 3 skip / q quit"
LABELLED = status.COVERING                                  # only a human tp or fp row (or an auto fp) covers a row; a tp_candidate covers nothing


def age(ts: str, now: str) -> str:
    """`ts` to `now` as 12m, 5h or 3d (a stamp in the future is 0m); `-` for anything that is not a stamp."""
    try:
        secs = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")).total_seconds()
    except (ValueError, TypeError):
        return "-"
    secs = max(0, int(secs))
    return f"{secs // 86400}d" if secs >= 86400 else f"{secs // 3600}h" if secs >= 3600 else f"{secs // 60}m"


def _hash8(cfg: Config, pid: str) -> str:
    """First 8 characters of the approved copy's `proposal_sha256` when the copy verifies, else `-`."""
    from . import proposal, shadow
    try:
        rec = proposal.load(shadow.approved_path(cfg, pid))
        body, _ = shadow.pending_body(rec)
    except (ValueError, OSError):
        return "-"
    return rec["proposal_sha256"][:8] if body is not None else "-"


def pending(cfg: Config, limit: int = 50, now: str | None = None, include_retired: bool = False) -> list[dict]:
    """Unlabelled fired private rows, oldest first, one per row id, at most `limit` (see the module docstring for an item). The rows of
    retired proposals (`status.retired`) are left out unless `include_retired`."""
    now = now or now_iso()
    rows, _ = report.read_corpora(cfg)
    broken = labels.read_chain(cfg)[1]
    marks, _ = report.label_map(cfg)                        # ref -> tp|fp|tp_candidate; used for the cover test and the tally, never copied
    tally: dict[str, dict] = {}
    for pid in {r["proposal_id"] for r in rows}:
        ids = {labels.row_id(r) for r in rows if r["proposal_id"] == pid}
        tp, fp = (sum(marks.get(i) == k for i in ids) for k in ("tp", "fp"))
        tally[pid] = {"n": len(ids), "tp": tp, "fp": fp, "needed": max(0, NEEDED - tp)}
    meta, chains, seen, items = {}, report._chains(cfg), set(), []
    for r in sorted((r for r in rows if status.fired(r)), key=lambda r: r["ts"]):
        rid, pid = labels.row_id(r), r["proposal_id"]
        if rid in seen or marks.get(rid) in LABELLED:
            continue
        seen.add(rid)
        if pid not in meta:
            meta[pid] = (*report._meta(cfg, pid, chains, now), _hash8(cfg, pid))
        det, ev, approved, mode, h8 = meta[pid]
        if not include_retired and status.retired({"mode": mode, "approved_mode": approved, "revoked": status.is_revoked(chains[0], pid)}):
            continue
        items.append({"id": rid, "ts": r["ts"], "age": age(r["ts"], now), "proposal_id": pid, "detector": det, "hash8": h8,
                      "mode": labels_queue.mode_shown(mode, now), "event": r["event"] if isinstance(r.get("event"), str) else ev,
                      "cause": r["cause"] if isinstance(r.get("cause"), str) else "-", "session8": r["session"][:8],
                      "payload_sha256": r["payload_sha256"], "tally": dict(tally[pid]), "chain_ok": broken is None})
    return items[:limit]


def _key(ask: Callable[[str], str]) -> str:
    """tp, fp, skip or quit; a key outside 1, 2, 3, q is asked again, and the third miss skips the row."""
    for _ in range(TRIES):
        key = KEYS.get(ask("> ").strip().lower())
        if key:
            return key
    return "skip"


def _note(cfg: Config, ask: Callable[[str], str], required: bool) -> tuple[str | None, bool]:
    """(note, accepted): the scrubbed note, asked again once after a refusal or (when required) an empty answer."""
    for _ in range(2):
        try:
            note = labels.check_note(cfg, ask("fp note (required): " if required else "note (optional): "))
        except labels.Refusal as exc:
            print(f"error: {exc}")
            continue
        if note or not required:
            return note, True
        print("error: an fp label needs a note")
    return None, False


def run(cfg: Config, limit: int, ask: Callable[[str], str], need_tty: Callable[[str], bool], include_retired: bool = False) -> int:
    """Entry of `compile label --queue`: exit 0 (also with an empty queue), 1 refused (broken chain), 2 usage or no terminal."""
    try:
        if limit < 1:
            raise labels.Refusal("--limit must be at least 1", 2)
        if not need_tty("label --queue"):
            return 2
        labels.check_chain(cfg)
        items = pending(cfg, limit, include_retired=include_retired)
        if not items:
            print("queue empty")
            return 0
        done = {"tp": 0, "fp": 0, "skip": 0}
        for n, item in enumerate(items, 1):
            labels_queue.card(item, n, len(items), CONTROL, NEEDED)
            try:
                key = _key(ask)
                note, ok = _note(cfg, ask, key == "fp") if key in ("tp", "fp") else (None, False)
            except EOFError:                                # the terminal went away: stop like q
                break
            if key == "quit":
                break
            if not ok:
                done["skip"] += 1
                continue
            labels.append_row(cfg, key, item["id"], note, reason_code="human")
            done[key] += 1
            for other in items[n:]:                         # the cards still to come show the labels given so far
                if other["proposal_id"] == item["proposal_id"]:
                    other["tally"][key] += 1
                    other["tally"]["needed"] = max(0, NEEDED - other["tally"]["tp"])
        _, broken = labels.read_chain(cfg)
        print(f"labelled {done['tp']} tp, {done['fp']} fp, skipped {done['skip']}, chain {'BROKEN' if broken else 'ok'}")
        return 0
    except labels.Refusal as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
