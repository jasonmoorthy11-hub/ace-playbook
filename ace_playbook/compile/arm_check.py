"""The nightly's arm-phase check (PREREGISTRATION.md, Arm assignment): `check(cfg)` returns the note lines `hooks/nightly_sweep.py` adds to the
health record, which `compile status` shows (80 characters each, at most 4 shown).

The hook reads only the signed marker `arm_phase.json` (phase.py) and nothing checks it per run, so deleting or corrupting the marker would
silently end the observe-arm silence while the sealed `arm_start` line still says the phase runs and `arms report` accepts the rows. This
check is the detector for that: for every detector with an `arm_start` line in the repository's PREREGISTRATION.md whose stamp has passed it
returns `arm phase missing: <detector>` when `phase.load` has no verified entry for it, `arm phase unverified` (first) when a marker file is
present but fails verification, and `holdout on during an arm phase` when `holdout_enabled` is true (the sealed text keeps it false; the verb
refuses it at the start, this catches a later flip). Not on the hook path: stdlib and sibling modules only; nothing imports it but the nightly.
"""

from __future__ import annotations

from ..canon import now_iso
from . import arms_window, phase


def check(cfg, now: str | None = None) -> list[str]:
    """The note lines for an arm phase that has started and is not in force; [] outside a phase or when the marker is verified."""
    stamp = now or now_iso()
    started = sorted(d for d, at in arms_window.arm_starts().items() if at <= stamp)
    if not started:
        return []
    marked, bad = phase.load(cfg)
    return [*(["arm phase unverified"] if bad else []), *(f"arm phase missing: {d}" for d in started if d not in marked),
            *(["holdout on during an arm phase"] if getattr(cfg, "holdout_enabled", False) else [])]
