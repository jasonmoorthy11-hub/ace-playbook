"""The detector functions (docs/design/compiler.md 4): pure `(event, state) -> Fire | None`.

Ported from Step 0; `ace_playbook/fixtures/parity/step0_parity.json` freezes its counts and lists
every hardening that changes one under `deviations`. No I/O, clock or environment: a detector
never mutates its state; `advance` returns the next one (catalogue.evaluate runs both). Live events
(hook payloads) carry `tier == "live"`, offline ones (transcript records) `tier == "offline"`.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from . import shell
from .events import EDIT_TOOLS, Event, hash16

MAX_PATHS = 5000
MAX_PENDING = 200
BRACKET_CAP = 512   # SH_NAME is quadratic on long [\w.-] runs, so the bracket is cut first
AUTOLOADED = {"CLAUDE.md", "MEMORY.md", "AGENTS.md"}   # loaded into context by the harness at start

HOOK_ERR = re.compile(r"^(?:PreToolUse|PostToolUse):\S+ hook error: \[([^\]]*)\]\s*(.*)", re.S)
SH_NAME = re.compile(r"([\w.-]+\.sh|[\w.-]+\.py)")
STOP_FB = re.compile(r"^Stop hook feedback:\s*(?:\[([^\]]*)\]\s*:?\s*)?(.*)", re.S)
CORRECTION = re.compile(
    r"\b(i (already )?told you|didn'?t i (just )?(say|tell|ask)|you always|you never|"
    r"you keep|why do you keep|how many times|stop (doing|adding|asking|using)|"
    r"(i|we) (already )?(said|asked|told you) (you )?(to|not|that)|as i said|like i said|"
    r"not again|do(ing)? (that|this|it) again|(asked|told) you (to|not|already))\b|"
    r"\byou (did|made|forgot|broke|missed|ignored|skipped|used|added)\b[^.?!\n]{0,40}\bagain\b", re.I)
# Scratch targets, tested after normpath(). Unlike Step 0 a temp root must end at a path boundary
# ("/tmpfoo" is not scratch) and ".." is resolved ("/tmp/../x" is "/x"): parity deviations.
SCRATCH = re.compile(r"^(?:(?:/tmp|/private/tmp|/var/folders|\$TMPDIR|\$\{TMPDIR\})(?:/|$)|\.?/?tmp/)|"
                     r"(?:^|/)(?:node_modules|\.next|dist|build|__pycache__|\.pytest_cache|\.cache|"
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


def _fire(name: str, count: int, event: Event, key: str = "") -> Fire | None:
    return Fire(name, count, event.tier, key) if count else None


def _segments(event: Event):
    """Every token segment of every Bash command in the event (cached on each ToolUse)."""
    for tu in event.tool_uses:
        yield from tu.segments()


def _rm_rf_targets(toks: list[str]) -> list[str] | None:
    if not toks or posixpath.basename(toks[0].replace("\\", "")) != "rm":
        return None
    fl = [t for t in toks[1:] if t.startswith("-")]
    rec = any(f == "--recursive" or (not f.startswith("--") and re.search(r"[rR]", f)) for f in fl)
    frc = any(f == "--force" or (not f.startswith("--") and "f" in f) for f in fl)
    targets = [t for t in toks[1:] if not t.startswith("-")]
    return targets if (rec and frc and targets) else None


def _rm_hits(event: Event, nonscratch: bool) -> tuple[int, str]:
    count, key = 0, ""
    for toks in _segments(event):
        targets = _rm_rf_targets(toks)
        if not targets:
            continue
        bad = [t for t in targets if not SCRATCH.search(posixpath.normpath(t))] if nonscratch else targets
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
    """[(tool_use, in_turn, in_session)] per Edit/Write/MultiEdit; registers paths as it goes.
    NotebookEdit registers in the live tier only (Step 0 ignores it offline; design 4)."""
    out = []
    for tu in event.tool_uses:
        path = _tool_path(tu)
        if path is None:
            continue
        h = hash16(path)
        if tu.name in EDIT_TOOLS:
            auto = _auto(path)
            out.append((tu, auto or h in turn, auto or h in seen))
        if tu.name in EDIT_TOOLS or tu.name == "Read" or (tu.name == "NotebookEdit" and event.tier == "live"):
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


def _hook_name(bracket: str | None) -> str:
    found = SH_NAME.findall((bracket or "")[:BRACKET_CAP])
    return found[-1] if found else ""


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
        n += bool(m) and _hook_name(m.group(1)) == "pretooluse-exitplan-question-check.sh"
    return _fire("exitplan_question_block", n, event)


def preclaim_audit_block(event: Event, state: dict) -> Fire | None:
    if event.tier != "offline" or not event.user_text.startswith("Stop hook feedback"):
        return None
    m = STOP_FB.match(event.user_text)
    return _fire("preclaim_audit_block", int(bool(m) and _hook_name(m.group(1)) == "stop-mid-task-audit.sh"), event)


def commit_without_push(event: Event, state: dict) -> Fire | None:
    git = state.get("git") or {}
    if event.rtype != "session_end" or git.get("commit", 0) <= 0 or git.get("push", 0) != 0:
        return None
    return _fire("commit_without_push", 1, event)


def user_correction(event: Event, state: dict) -> Fire | None:
    if event.tier != "offline" or not event.human or event.dup or event.meta:
        return None
    t = event.user_text
    return _fire("user_correction", int(len(t) <= 800 and bool(CORRECTION.search(t))), event)


def unparseable(event: Event, state: dict) -> Fire | None:
    """A length cap cut a command or path, so the detectors saw a prefix only: an evasion candidate."""
    cut = [tu for tu in event.tool_uses if tu.truncated]
    return _fire("unparseable", len(cut), event, hash16(cut[0].id) if cut else "")


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
    # turn reset: a human prompt offline, Stop live (the live hooks are PreToolUse and Stop only)
    if (event.tier == "offline" and event.human and not event.queued and not event.meta) or event.hook == "Stop":
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
    for toks in _segments(event):
        sub, _args = shell.git_subcommand(toks)
        if sub in ("commit", "push"):
            s["git"][sub] = int(s["git"].get(sub, 0)) + 1
    if len(seen) > MAX_PATHS or len(turn) > MAX_PATHS:
        s["truncated"] = True                      # stop growing; detectors go silent for this session
    else:
        s["seen_paths"], s["turn_paths"] = sorted(seen), sorted(turn)
    return s
