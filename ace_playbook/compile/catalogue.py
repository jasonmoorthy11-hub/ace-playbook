"""The detector registry and driver (docs/design/compiler.md 4): CATALOGUE, evaluate, source hashes.

The detector functions and `advance` live in detectors.py; this module only lists them with their
metadata (strength, tier, consumes, state, compilable, lessons) and runs them.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import textrules
from . import detectors, events, shell, srchash
from .detectors import (Fire, advance, blank_state, commit_without_push, edit_unread_session, edit_unread_turn,
                        exitplan_question_block, preclaim_audit_block, rm_rf, rm_rf_nonscratch, unparseable,
                        user_correction)
from .events import Event


def _source_texts() -> tuple[str, ...]:
    """Full text of the three leaf modules every detector depends on, hashed whole (patched in tests)."""
    return tuple(srchash.module_text(Path(m.__file__)) for m in (events, shell, textrules))


@dataclass(frozen=True)
class Detector:
    name: str
    strength: str            # copied from Step 0: "strong" | "weak"
    tier: str                # "live+offline" | "offline"
    consumes: tuple
    state: str
    fire: Callable[[Event, dict], Fire | None]
    compilable: str          # highest mode a proposal may reach: block | warn | observe | none
    lessons: tuple = ()

    @property
    def source_sha256(self) -> str:
        """Row name, fire name and metadata, plus the text of detectors.py minus the other rows' fire bodies, the whole
        text of catalogue.py (registry literal included) and the three leaf modules (srchash.py). Raises ValueError
        unless the fire is the top-level function of detectors.py that carries its name (a lambda, a wrapper, a partial
        or a callable instance fails closed, for this row only: a nameless fire is left out of the other rows' list of
        fires)."""
        name = getattr(self.fire, "__name__", None)
        if not isinstance(name, str) or getattr(detectors, name, None) is not self.fire:
            raise ValueError(f"fire of {self.name} is not detectors.{name}")
        meta = {k: getattr(self, k) for k in ("strength", "tier", "consumes", "state", "compilable", "lessons")}
        fires = [n for r in CATALOGUE.values() if isinstance(n := getattr(r.fire, "__name__", None), str)]
        return srchash.digest(self.name, meta, name, fires, _source_texts())


_EDIT_USES = tuple(f"PreToolUse:{t}" for t in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"))
_ATTACH = tuple("attachment:" + t for t in ("file", "edited_text_file", "compact_file_reference"))
_RM = ("L-b3d1fba9",)

CATALOGUE: dict[str, Detector] = {d.name: d for d in (
    Detector("rm_rf_nonscratch", "strong", "live+offline", ("PreToolUse:Bash",), "none",
             rm_rf_nonscratch, "block", _RM),
    Detector("rm_rf", "weak", "live+offline", ("PreToolUse:Bash",), "none", rm_rf, "observe", _RM),
    Detector("edit_unread_session", "strong", "live+offline", _EDIT_USES + _ATTACH, "path_hash_set",
             edit_unread_session, "warn"),
    Detector("edit_unread_turn", "weak", "live+offline", _EDIT_USES + _ATTACH + ("Stop",), "turn_path_hash_set",
             edit_unread_turn, "observe"),
    Detector("exitplan_question_block", "strong", "offline", ("transcript:tool_result",), "none",
             exitplan_question_block, "none"),
    Detector("preclaim_audit_block", "strong", "offline", ("transcript:user",), "none",
             preclaim_audit_block, "none"),
    Detector("commit_without_push", "weak", "live+offline", ("PreToolUse:Bash", "session_end"), "git_counts",
             commit_without_push, "observe"),
    Detector("user_correction", "weak", "offline", ("transcript:human_prompt",), "none",
             user_correction, "none"),
    Detector("unparseable", "weak", "live+offline", ("PreToolUse:*",), "none", unparseable, "observe"),
)}


def stale(name: str, sha: str) -> bool:
    """True when `sha` is not the current hash of row `name`: unknown row, changed source, or a row that cannot be hashed."""
    try:
        return name not in CATALOGUE or CATALOGUE[name].source_sha256 != sha
    except ValueError:
        return True


def source_table() -> dict[str, str]:
    """name -> source_sha256 for every catalogue row (the seal command); raises ValueError if any row cannot be hashed."""
    return {n: row.source_sha256 for n, row in CATALOGUE.items()}


def evaluate(event: Event, state: dict | None, names=None,
             strict: bool = False) -> tuple[list[Fire], dict, list[tuple[str, str]]]:
    """Run catalogue detectors on the pre-event state, then advance: (fires, new_state, errors).

    errors = [(detector_name, exception_type_name)]. strict=True re-raises (tests, offline pass);
    the hook path leaves it False, logs `error:<name>` and fails open."""
    state = state if state is not None else blank_state()
    rows = CATALOGUE.values() if names is None else [CATALOGUE[n] for n in names]
    fires, errors = [], []
    for row in rows:
        if row.tier == "offline" and event.tier != "offline":
            continue
        try:
            f = row.fire(event, copy.deepcopy(state))
        except Exception as exc:
            if strict:
                raise
            errors.append((row.name, type(exc).__name__))
            continue
        if f:
            fires.append(f)
    try:
        new = advance(event, state)
    except Exception as exc:       # the state transition is not exempt from failing open
        if strict:
            raise
        errors.append(("advance", type(exc).__name__))
        new = state
    return fires, new, errors
