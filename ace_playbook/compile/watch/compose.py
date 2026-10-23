"""Compose a proposal from validated Reader fields only (W2): `compose(validated, item, now) -> record`.

The record is built by the EXISTING `records.record` (so the id, the 30-day expiry and the shape are the W1 ones) as a `lead`: no inventory
hits, target `lead`, diff None, tier-1 evidence, and the per-class `verify` template for a lead (`curl -sI <url> | head -1`). `records.check`
must pass against the item text, else ValueError with its reason (the caller counts the item as rejected). A model never writes a diff, and
tier-1 evidence alone cannot carry one (`records.check`).
"""

from __future__ import annotations

from . import records


def compose(validated: dict, item: dict, now: str) -> dict:
    ev = {"url": item["url"], "sha256": item["sha256"], "quote": validated["quote"], "tier": 1}
    rec = records.record("lead", validated["title"], ev, [], "lead", None, f"curl -sI {records.sh(item['url'])} | head -1", now)
    reason = records.check(rec, item["text"])
    if reason is not None:
        raise ValueError(f"composed record does not check: {reason}")
    return rec
