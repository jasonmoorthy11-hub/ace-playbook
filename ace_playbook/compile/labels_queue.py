"""What the human labeller sees (split out of `labels.py` and `queue.py`, which are at their size caps): the `--rows` listing and the queue card.

During a randomized arm phase (PREREGISTRATION.md, Arm assignment) both hide the proposal's resolved mode and never show the arm, so the
label cannot depend on which arm a row came from. A phase runs from a detector's `arm_start` line to its horizon (12 weeks) plus the 7
days before the analysis; the window is wider than the horizon needs on purpose (a session cap that ends the horizon earlier still hides
the mode). Not on the hook path: `labels` loads this module lazily, and `arms` is loaded only to read the arm start lines.
"""

from __future__ import annotations

from datetime import timedelta

from ..canon import now_iso
from ..config import Config
from . import labels

HIDDEN = "hidden"


def blind(now: str | None = None) -> bool:
    """True while any detector's arm phase (arm start to horizon plus the analysis lag) is running."""
    from . import arms
    at, span = arms._dt(now or now_iso()), timedelta(weeks=arms.WEEKS, days=arms.LAG_DAYS)
    return any(t0 is not None and t0 <= at < t0 + span for t0 in map(arms._dt, arms.arm_starts().values()))


def mode_shown(mode: str, now: str | None = None) -> str:
    """`mode`, or `hidden` during an arm phase."""
    return HIDDEN if blind(now) else mode


def rows(cfg: Config, limit: int) -> int:
    """`compile label --rows`: the newest `limit` shadow rows, the mode last (a `disabled` row, kill file, shows)."""
    shown = blind()
    for r in labels.shadow_rows(cfg)[-limit:]:
        mode = HIDDEN if shown else r.get("mode") or "-"
        print(*map(labels._clean, (r["ts"], labels.row_id(r), r["proposal_id"], r.get("lesson_id", "-"), r.get("cause", "-"),
                                   str(r.get("would_fire")).lower(), mode)))
    return 0


def card(item: dict, number: int, total: int, control: str, needed: int) -> None:
    """One queue card; the `mode` part is left out when the item's mode is hidden."""
    c, t = labels._clean, item["tally"]
    mode = "" if item["mode"] == HIDDEN else f"  mode {c(item['mode'])}"
    print(f"\n[{number}/{total}] id {c(item['id'])}  {c(item['detector'])}@{c(item['hash8'])}{mode}  age {c(item['age'])}\n"
          f"  why:        detector {c(item['detector'])} fired (cause {c(item['cause'])}); payloads are hashes only, no command text\n"
          f"  context:    session {c(item['session8'])}  ts {c(item['ts'])}  proposal {c(item['proposal_id'])}  event {c(item['event'])}\n"
          f"  label:      {control}\n"
          f"  tally:      n {t['n']}  tp {t['tp']}  fp {t['fp']}  needed {t['needed']} more to reach {needed} TPs\n"
          f"  provenance: chain {'ok' if item['chain_ok'] else 'BROKEN'}  rubric = design §7.3")
