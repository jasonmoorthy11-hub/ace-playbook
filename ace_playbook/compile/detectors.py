"""The detector catalogue (docs/design/compiler.md 4): pure `(event, state) -> Fire | None`.

Ported from Step 0 (the private rebuild-lab `ace/step0/detectors.py`); frozen counts in
`ace_playbook/fixtures/frozen/step0_parity.json` keep the port honest. No I/O, no clock, no
environment: a detector reads the state it is given and never mutates it. State changes come
from `advance(event, state)`, which returns a new dict; `evaluate` runs both in the right order.

Two tiers feed the same functions. Live events (hook payloads) carry `tier == "live"`; offline
events (transcript records) carry `tier == "offline"` and are authoritative for measurement.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Callable

from .events import EDIT_TOOLS, Event, command_segments, git_subcommand, hash16

MAX_PATHS = 5000
MAX_PENDING = 200
AUTOLOADED = {"CLAUDE.md", "MEMORY.md", "AGENTS.md"}   # loaded into context by the harness at start
_READ_TOOLS = ("Read", "NotebookEdit")

HOOK_ERR = re.compile(r"^(?:PreToolUse|PostToolUse):\S+ hook error: \[([^\]]*)\]\s*(.*)", re.S)
SH_NAME = re.compile(r"([\w.-]+\.sh|[\w.-]+\.py)")
STOP_FB = re.compile(r"^Stop hook feedback:\s*(?:\[([^\]]*)\]\s*:?\s*)?(.*)", re.S)
CORRECTION = re.compile(
    r"\b(i (already )?told you|didn'?t i (just )?(say|tell|ask)|you always|you never|"
    r"you keep|why do you keep|how many times|stop (doing|adding|asking|using)|"
    r"(i|we) (already )?(said|asked|told you) (you )?(to|not|that)|as i said|like i said|"
    r"not again|do(ing)? (that|this|it) again|(asked|told) you (to|not|already))\b|"
    r"\byou (did|made|forgot|broke|missed|ignored|skipped|used|added)\b[^.?!\n]{0,40}\bagain\b", re.I)
# Scratch targets. Known evasions, kept for parity with Step 0 (design 4, 7.3): there is no
# boundary after "/tmp", so "/tmp/../x" and "/tmpfoo" count as scratch; ".." is never resolved.
SCRATCH = re.compile(r"^(/tmp|/private/tmp|/var/folders|\$TMPDIR|\$\{TMPDIR|tmp/|\.?/?tmp/)|"
                     r"(^|/)(node_modules|\.next|dist|build|__pycache__|\.pytest_cache|\.cache|"
                     r"coverage|\.turbo|scratchpad|\.venv)/?$")


@dataclass(frozen=True)
class Fire:
    detector: str
    count: int = 1
    tier: str = "offline"
    key: str = ""          # sha256[:16] of the first offending target; never the text itself


def blank_state() -> dict:
    return {"v": 1, "seen_paths": [], "turn_paths": [], "git": {"commit": 0, "push": 0},
            "events": 0, "truncated": False, "pending": {}}


# ---------------------------------------------------------------- helpers
def _fire(name: str, count: int, event: Event, key: str = "") -> Fire | None:
    return Fire(name, count, event.tier, key) if count else None


def _bash_cmds(event: Event) -> list[str]:
    return [tu.input["command"] for tu in event.tool_uses
            if tu.name == "Bash" and isinstance(tu.input.get("command"), str)]


def _flags(toks: list[str]) -> list[str]:
    return [t for t in toks[1:] if t.startswith("-")]


def _rm_rf_targets(toks: list[str]) -> list[str] | None:
    if not toks or toks[0] != "rm":
        return None
    fl = _flags(toks)
    rec = any(f == "--recursive" or (not f.startswith("--") and re.search(r"[rR]", f)) for f in fl)
    frc = any(f == "--force" or (not f.startswith("--") and "f" in f) for f in fl)
    targets = [t for t in toks[1:] if not t.startswith("-")]
    return targets if (rec and frc and targets) else None


def _rm_hits(event: Event, nonscratch: bool) -> tuple[int, str]:
    count, key = 0, ""
    for cmd in _bash_cmds(event):
        for toks in command_segments(cmd):
            targets = _rm_rf_targets(toks)
            if not targets:
                continue
            bad = [t for t in targets if not SCRATCH.search(t)] if nonscratch else targets
            if bad:
                count += 1
                key = key or hash16(bad[0])
    return count, key


def _tool_path(tu) -> str | None:
    p = tu.input.get("file_path") or tu.input.get("notebook_path")
    return p if isinstance(p, str) and p else None


def _auto(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in AUTOLOADED or "/.claude/plans/" in path   # plan file pre-created by harness


def _walk(event: Event, turn: set, seen: set) -> list[tuple]:
    """[(tool_use, in_turn, in_session)] per Edit/Write/MultiEdit; registers each path as it goes."""
    out = []
    for tu in event.tool_uses:
        path = _tool_path(tu)
        if path is None:
            continue
        h = hash16(path)
        if tu.name in EDIT_TOOLS:
            auto = _auto(path)
            out.append((tu, auto or h in turn, auto or h in seen))
        if tu.name in EDIT_TOOLS or tu.name in _READ_TOOLS:
            turn.add(h)
            seen.add(h)
    return out


def _unread(event: Event, state: dict, col: int, name: str) -> Fire | None:
    """col 1 = not read this turn, col 2 = not read this session."""
    if state.get("truncated"):
        return None
    n, key = 0, ""
    if event.tier == "live":
        flags = _walk(event, set(state.get("turn_paths") or ()), set(state.get("seen_paths") or ()))
        for tu, *cols in flags:
            if not cols[col - 1] and tu.exists is not False:   # absent path = new file
                n, key = n + 1, key or hash16(_tool_path(tu))
    else:
        pending = state.get("pending") or {}
        for tr in event.tool_results:
            info = pending.get(tr.id)
            if not info or tr.is_error:
                continue
            if info[0] == "Write" and tr.text[:300].lower().startswith("file created"):
                continue
            if not info[col]:
                n, key = n + 1, key or hash16(tr.id)
    return _fire(name, n, event, key)


# ---------------------------------------------------------------- detectors
def rm_rf_nonscratch(event: Event, state: dict) -> Fire | None:
    n, key = _rm_hits(event, nonscratch=True)
    return _fire("rm_rf_nonscratch", n, event, key)


def rm_rf(event: Event, state: dict) -> Fire | None:
    n, key = _rm_hits(event, nonscratch=False)
    return _fire("rm_rf", n, event, key)


def edit_unread_session(event: Event, state: dict) -> Fire | None:
    return _unread(event, state, 2, "edit_unread_session")


def edit_unread_turn(event: Event, state: dict) -> Fire | None:
    return _unread(event, state, 1, "edit_unread_turn")


def exitplan_question_block(event: Event, state: dict) -> Fire | None:
    if event.tier != "offline":
        return None
    n = 0
    for tr in event.tool_results:
        m = HOOK_ERR.match(tr.text)
        found = SH_NAME.findall(m.group(1)) if m else []
        n += bool(found) and found[-1] == "pretooluse-exitplan-question-check.sh"
    return _fire("exitplan_question_block", n, event)


def preclaim_audit_block(event: Event, state: dict) -> Fire | None:
    if event.tier != "offline" or not event.user_text.startswith("Stop hook feedback"):
        return None
    m = STOP_FB.match(event.user_text)
    found = SH_NAME.findall(m.group(1) or "") if m else []
    return _fire("preclaim_audit_block", int(bool(found) and found[-1] == "stop-mid-task-audit.sh"), event)


def commit_without_push(event: Event, state: dict) -> Fire | None:
    git = state.get("git") or {}
    if event.rtype != "session_end" or git.get("commit", 0) <= 0 or git.get("push", 0) != 0:
        return None
    return _fire("commit_without_push", 1, event)


def user_correction(event: Event, state: dict) -> Fire | None:
    if event.tier != "offline" or not event.human or event.dup:
        return None
    t = event.user_text
    return _fire("user_correction", int(len(t) <= 800 and bool(CORRECTION.search(t))), event)


# ---------------------------------------------------------------- catalogue
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
    deps: tuple = ()         # regexes, sets and helpers the function reads; they join the hash

    @property
    def source_sha256(self) -> str:
        """Hash of the function body plus its declared dependencies (invalidates approvals)."""
        import inspect
        parts = [inspect.getsource(self.fire)]
        for dep in self.deps:
            parts.append(dep.pattern if hasattr(dep, "pattern") else
                         inspect.getsource(dep) if callable(dep) else repr(sorted(dep)))
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()


_EDIT_USES = tuple(f"PreToolUse:{t}" for t in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"))
_ATTACH = ("attachment:file", "attachment:edited_text_file", "attachment:compact_file_reference")
_RM = ("L-b3d1fba9",)

CATALOGUE: dict[str, Detector] = {d.name: d for d in (
    Detector("rm_rf_nonscratch", "strong", "live+offline", ("PreToolUse:Bash",), "none",
             rm_rf_nonscratch, "block", _RM, (SCRATCH, _rm_hits, _rm_rf_targets)),
    Detector("rm_rf", "weak", "live+offline", ("PreToolUse:Bash",), "none",
             rm_rf, "observe", _RM, (_rm_hits, _rm_rf_targets)),
    Detector("edit_unread_session", "strong", "live+offline", _EDIT_USES + _ATTACH, "path_hash_set",
             edit_unread_session, "warn", (), (AUTOLOADED, _walk, _unread)),
    Detector("edit_unread_turn", "weak", "live+offline", _EDIT_USES + _ATTACH + ("Stop",), "turn_path_hash_set",
             edit_unread_turn, "observe", (), (AUTOLOADED, _walk, _unread)),
    Detector("exitplan_question_block", "strong", "offline", ("transcript:tool_result",), "none",
             exitplan_question_block, "none", (), (HOOK_ERR, SH_NAME)),
    Detector("preclaim_audit_block", "strong", "offline", ("transcript:user",), "none",
             preclaim_audit_block, "none", (), (STOP_FB, SH_NAME)),
    Detector("commit_without_push", "weak", "live+offline", ("PreToolUse:Bash", "session_end"), "git_counts",
             commit_without_push, "observe", (), ()),
    Detector("user_correction", "weak", "offline", ("transcript:human_prompt",), "none",
             user_correction, "none", (), (CORRECTION,)),
)}


# ---------------------------------------------------------------- state transition + driver
def advance(event: Event, state: dict | None) -> dict:
    """Return the state after `event`. Pure: the argument is never mutated."""
    base = blank_state()
    s = {**base, **(state or {})}
    s["git"] = dict(s.get("git") or base["git"])
    s["pending"] = dict(s.get("pending") or {})
    s["events"] = int(s.get("events", 0)) + 1
    for tr in event.tool_results:
        s["pending"].pop(tr.id, None)
    seen, turn = set(s["seen_paths"]), set(s["turn_paths"])
    if (event.human and not event.queued) or event.hook == "Stop":
        turn.clear()
    for name in event.read_files:
        if isinstance(name, str):
            turn.add(hash16(name))
            seen.add(hash16(name))
    for tu, in_turn, in_sess in _walk(event, turn, seen):
        if event.tier == "offline":
            s["pending"][tu.id] = [tu.name, in_turn, in_sess]
    while len(s["pending"]) > MAX_PENDING:
        s["pending"].pop(next(iter(s["pending"])))
    for cmd in _bash_cmds(event):
        for toks in command_segments(cmd):
            sub, _args = git_subcommand(toks)
            if sub in ("commit", "push"):
                s["git"][sub] = int(s["git"].get(sub, 0)) + 1
    if len(seen) > MAX_PATHS or len(turn) > MAX_PATHS:
        s["truncated"] = True                      # stop growing; detectors go silent for this session
    else:
        s["seen_paths"], s["turn_paths"] = sorted(seen), sorted(turn)
    return s


def evaluate(event: Event, state: dict | None, names=None) -> tuple[list[Fire], dict]:
    """Run catalogue detectors on the pre-event state, then advance. A raising detector is skipped."""
    state = state if state is not None else blank_state()
    rows = CATALOGUE.values() if names is None else [CATALOGUE[n] for n in names]
    fires = []
    for row in rows:
        if row.tier == "offline" and event.tier != "offline":
            continue
        try:
            f = row.fire(event, state)
        except Exception:   # a buggy detector must never take the hook down
            f = None
        if f:
            fires.append(f)
    return fires, advance(event, state)
