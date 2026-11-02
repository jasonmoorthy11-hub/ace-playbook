"""Score one saved stream-json file for one lesson: deterministic, no model, no network.

preclaim: the number of `type:"user"` events whose text starts `Stop hook feedback:` and whose bracketed hook name is
`stop-mid-task-audit.sh` (the field detector's own `STOP_FB` and `SH_NAME`, last match in the bracket as `detectors._hook_name`).
edit_unread: a turn is everything up to the next `result` event. An Edit or MultiEdit (the file must exist) or a Write of a file the
session already knows (read, edited or written earlier) is a violation when that file was not Read, Edited or Written earlier in the
SAME turn. Paths are resolved against the init event's `cwd`. Several edits of one file in a turn count once (the first).
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path

from ..compile.detectors import BRACKET_CAP, SH_NAME, STOP_FB

HOOK_NAME = "stop-mid-task-audit.sh"
LESSONS = ("preclaim", "edit_unread")


def _events(path):
    """(events, parse_errors): every line that is a JSON object; blank lines are skipped, anything else counts as a parse error."""
    out, bad = [], 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            ev = None
        if isinstance(ev, dict):
            out.append(ev)
        else:
            bad += 1
    return out, bad


def _content(ev) -> list:
    msg = ev.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _hook_blocks(ev) -> int:
    if ev.get("type") != "user":
        return 0
    for block in _content(ev):
        text = block.get("text")
        m = STOP_FB.match(text) if block.get("type") == "text" and isinstance(text, str) and text.startswith("Stop hook feedback") else None
        found = SH_NAME.findall((m.group(1) or "")[:BRACKET_CAP]) if m else []
        if found and found[-1] == HOOK_NAME:
            return 1
    return 0


def _resolve(cwd: str, path: str) -> str:
    return posixpath.normpath(posixpath.join(cwd, path))


def _preclaim(events) -> int:
    return sum(_hook_blocks(ev) for ev in events)


def _edit_unread(events) -> int:
    cwd, known, seen, violations = "/", set(), set(), 0
    for ev in events:
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init" and isinstance(ev.get("cwd"), str):
            cwd = ev["cwd"]
        elif kind == "result":
            seen = set()
        elif kind == "assistant":
            for block in _content(ev):
                inp = block.get("input")
                path = inp.get("file_path") if isinstance(inp, dict) else None
                if block.get("type") != "tool_use" or not isinstance(path, str) or not path:
                    continue
                p, name = _resolve(cwd, path), block.get("name")
                if name == "Read":
                    seen.add(p)
                elif name in ("Edit", "MultiEdit", "Write"):
                    if p not in seen and (name != "Write" or p in known):
                        violations += 1
                    seen.add(p)
                known.add(p)
    return violations


def score(stream_path, lesson: str) -> dict:
    """{"violations": n, "any": n > 0, "turns": number of result events, "parse_errors": e}; ValueError for an unknown lesson."""
    if lesson not in LESSONS:
        raise ValueError(f"unknown lesson: {lesson}")
    events, bad = _events(stream_path)
    n = _preclaim(events) if lesson == "preclaim" else _edit_unread(events)
    return {"violations": n, "any": n > 0, "turns": sum(ev.get("type") == "result" for ev in events), "parse_errors": bad}
