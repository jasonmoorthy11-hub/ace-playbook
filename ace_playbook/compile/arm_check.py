"""The nightly's arm-phase check (PREREGISTRATION.md, Arm assignment): `check(cfg)` returns the note lines `hooks/nightly_sweep.py` puts FIRST in the
health record, which `compile status` shows (80 characters each, at most 4 shown).

The hook reads only the signed marker `arm_phase.json` (phase.py) and nothing checks it per run, so deleting or corrupting the marker would
silently end the observe-arm silence while the sealed `arm_start` line still says the phase runs and `arms report` accepts the rows. This
check is the detector for that. For every detector with an `arm_start` line in the repository's PREREGISTRATION.md whose stamp has passed it
returns one line: `arm phase missing: <detector>` when `phase.load` has no verified entry for it (`arm phase unverified: <detector>` when a
marker file is present but fails verification), or `arm phase stamp differs: <detector>` when the verified entry's stamp is not the line's.
It adds `holdout on during an arm phase` when `holdout_enabled` is true (the sealed text keeps it false; the verb refuses it at the start,
this catches a later flip). An unreadable PREREGISTRATION.md raises (the nightly turns that into `arm check failed`). Not on the hook path:
stdlib and sibling modules only; nothing imports it but the nightly.
"""

from __future__ import annotations

from ..canon import now_iso
from . import arms_window, phase


def check(cfg, now: str | None = None) -> list[str]:
    """The note lines for an arm phase that has started and is not in force; [] outside a phase or when the marker is verified."""
    stamp = now or now_iso()
    started = {d: at for d, at in sorted(arms_window.arm_starts().items()) if at <= stamp}
    if not started:
        return []
    marked, bad = phase.load(cfg)
    out = [f"arm phase {'unverified' if bad else 'missing'}: {d}" if d not in marked else f"arm phase stamp differs: {d}"
           for d, at in started.items() if d not in marked or marked[d] != at]
    return [*out, *(["holdout on during an arm phase"] if getattr(cfg, "holdout_enabled", False) else [])]
