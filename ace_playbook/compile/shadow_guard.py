"""Pure helpers of the shadow hook (docs/design/compiler.md 7), split out of shadow.py for its 300-line budget. Stdlib only, no I/O.

`scan` reads the tool name and session id out of the first bytes of an over-cap payload (S-1 F1: such a payload is not valid JSON to
`json.loads` once cut, and must still reach the block-mode proposals), `applies` is the matcher test, `decision` turns the hits into the
stderr echo and the stdout JSON.
"""

from __future__ import annotations

import re

RAW_TOOL, RAW_SID = (re.compile(rb'"%s"\s*:\s*"([%s]{1,%d})"' % (key, chars, cap))
                     for key, chars, cap in ((b"tool_name", b"A-Za-z0-9_", 64), (b"session_id", b"A-Za-z0-9_.:-", 128)))
RAW_STOP = re.compile(rb'"stop_hook_active"\s*:\s*true')


def scan(raw: bytes) -> tuple[str | None, str, bool]:
    """(tool_name or None, session_id or "", stop_hook_active) of an over-cap payload. A key that is absent, not a plain string or found with
    two different values (a nested copy an agent put there) is unknown: None for the tool (every proposal of the event applies), "" otherwise."""
    def one(rx: re.Pattern) -> str | None:
        found = {m.decode() for m in rx.findall(raw)}
        return found.pop() if len(found) == 1 else None
    return one(RAW_TOOL), one(RAW_SID) or "", RAW_STOP.search(raw) is not None


def applies(p: dict, event: str, tool: str | None) -> bool:
    """The proposal is for this event and its matcher covers `tool`; `tool` None (unknown) is covered by every proposal of the event."""
    return p["event"] == event and (tool is None or bool({"*", tool} & set(p["matcher"].split("|"))))


def decision(event: str, hits: list, active: bool) -> tuple[list[str], dict | None]:
    """([warn messages for stderr], the JSON for stdout or None) of `hits` = [(proposal, mode, cause)]: block wins over warn, and a Stop
    with `stop_hook_active` prints nothing on stdout (a loop guard; the fires are still recorded by the caller)."""
    warns = [p["message"] for p, mode, _cause in hits if mode == "warn"]
    block = next((p["message"] for p, mode, _cause in hits if mode == "block"), None)
    if event == "Stop" and active:
        return warns, None
    if block is not None:
        return warns, ({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": block}}
                       if event == "PreToolUse" else {"decision": "block", "reason": block})
    return warns, ({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(warns)}} if warns else None)
