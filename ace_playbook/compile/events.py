"""Typed events and the two adapters that feed the detectors (docs/design/compiler.md 3, 4).

`event_from_hook` turns a live hook payload into an Event; `event_from_record` turns one
transcript JSONL record into an Event (a faithful port of Step 0's scanlib.parse_event).
Both ignore unknown fields, default missing ones, and treat every string as data: nothing here
is ever executed, expanded or interpolated. Stdlib only; no I/O except the injectable path probe.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from .shell import command_segments, git_subcommand  # noqa: F401  (re-exported)

MAX_CMD = 65536    # longest command kept; a longer one is cut AND marked truncated (-> `unparseable`)
MAX_TEXT = 20000   # longest prompt or tool-result text kept
MAX_PATH = 4096
EDIT_TOOLS = ("Edit", "Write", "MultiEdit")
_INPUT_KEYS = {"command": MAX_CMD, "file_path": MAX_PATH, "notebook_path": MAX_PATH}

META_KEYS = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")
SYNTHETIC_PREFIXES = (
    "<", "[", "Stop hook feedback", "Another Claude session", "Review this change for",
    "## Context Usage", "Implement the following plan", "This session is being continued",
    "Base directory for this skill", "You are ", "# ", "Read and follow", "Research task",
    "Do not use any tools", "You previously flagged", "The coordinator sent",
    "Caveat:", "Approach this as",
)


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict
    exists: bool | None = None   # live tier only: did the target path exist at PreToolUse?
    truncated: bool = False      # a length cap cut the command or path: detectors saw only a prefix
    _segs: list | None = field(default=None, init=False, repr=False, compare=False)

    def segments(self) -> list:
        """Token segments of the Bash command, computed once per tool use."""
        if self._segs is None:
            cmd = self.input.get("command")
            self._segs = command_segments(cmd) if self.name == "Bash" and isinstance(cmd, str) else []
        return self._segs


@dataclass
class ToolResult:
    id: str
    name: str
    input: dict
    text: str
    is_error: bool


@dataclass
class Event:
    rtype: str = ""
    ts: str = ""
    sidechain: bool = False
    tool_uses: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    user_text: str = ""
    human: bool = False
    meta: bool = False        # isMeta, isCompactSummary or isVisibleInTranscriptOnly record: never typed by the operator
    queued: bool = False      # human prompt delivered mid-turn as a queued_command attachment
    dup: bool = False         # queued prompt later re-delivered as a user record (count once)
    read_files: list = field(default_factory=list)
    attachment: dict = field(default_factory=dict)
    hook: str = ""            # live tier: hook_event_name
    session_id: str = ""
    tier: str = "offline"     # "live" (hook payload) or "offline" (transcript)


def end_event(tier: str = "offline") -> Event:
    """Synthetic last event; session-level detectors (commit_without_push) fire on it."""
    return Event(rtype="session_end", tier=tier)


def hash16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "backslashreplace")).hexdigest()[:16]


def _s(v: Any, cap: int = MAX_TEXT) -> str:
    return v[:cap] if isinstance(v, str) else ""


def _pick_input(raw: Any) -> tuple[dict, bool]:
    """Keep only the string fields the detectors read (everything else is ignored).

    Returns (fields, truncated): truncated is True when a length cap cut a field, so the
    detectors saw only a prefix. The caller marks the ToolUse so the cut is visible, never silent.
    """
    if not isinstance(raw, dict):
        return {}, False
    kept = {k: raw[k][:cap] for k, cap in _INPUT_KEYS.items() if isinstance(raw.get(k), str)}
    return kept, any(len(raw[k]) > len(v) for k, v in kept.items())


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content[:MAX_TEXT]
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict)
                        and isinstance(b.get("text", ""), str))[:MAX_TEXT]
    return ""


def is_human_text(rec: dict, text: str) -> bool:
    if rec.get("isMeta") or rec.get("planContent") or rec.get("isCompactSummary"):
        return False
    t = text.strip()
    return bool(t) and not t.startswith(SYNTHETIC_PREFIXES)


# ---------------------------------------------------------------- hook payload -> Event
def _probe(probe: Callable[[str], bool], path: str) -> bool | None:
    try:
        return bool(probe(path))
    except (OSError, ValueError, TypeError):
        return None


def _response(resp: Any) -> tuple[str, bool]:
    if isinstance(resp, dict):
        text = " ".join(resp[k] for k in ("content", "stdout", "stderr", "output", "text", "error")
                        if isinstance(resp.get(k), str))
        return text[:MAX_TEXT], bool(resp.get("is_error") or resp.get("error"))
    return _result_text(resp), False


def event_from_hook(payload: Any, probe: Callable[[str], bool] = os.path.lexists) -> Event:
    e = Event(rtype="hook", tier="live")
    if not isinstance(payload, dict):
        return e
    e.hook, e.session_id = _s(payload.get("hook_event_name"), 64), _s(payload.get("session_id"), 256)
    name, tid = _s(payload.get("tool_name"), 128), _s(payload.get("tool_use_id"), 128)
    inp, cut = _pick_input(payload.get("tool_input"))
    if e.hook == "PreToolUse":
        tu = ToolUse(tid, name, inp, truncated=cut)
        if name in EDIT_TOOLS and inp.get("file_path"):
            # note: lexists runs on an agent-chosen path (a stat on a slow mount counts against the
            # hook's 5 s timeout; docs/design/compiler.md step 4)
            tu.exists = _probe(probe, inp["file_path"])
        e.tool_uses.append(tu)
    elif e.hook == "PostToolUse":
        text, err = _response(payload.get("tool_response"))
        e.tool_results.append(ToolResult(tid, name, inp, text, err))
    elif e.hook == "UserPromptSubmit":
        e.user_text = _s(payload.get("prompt"))
        e.human = is_human_text({}, e.user_text)
    return e


# ---------------------------------------------------------------- transcript record -> Event
def event_from_record(rec: Any, tool_index: dict | None = None) -> Event:
    """Turn a raw transcript record into an Event; tool_index maps tool_use id -> ToolUse."""
    tool_index = tool_index if tool_index is not None else {}
    rec = rec if isinstance(rec, dict) else {}
    e = Event(rtype=_s(rec.get("type"), 32), ts=_s(rec.get("timestamp"), 64),
              sidechain=bool(rec.get("isSidechain")), meta=any(bool(rec.get(k)) for k in META_KEYS), tier="offline")
    if e.rtype == "attachment":
        a = rec.get("attachment")
        e.attachment = a if isinstance(a, dict) else {}
        origin, e.meta = e.attachment.get("origin"), e.meta or bool(e.attachment.get("isMeta"))
        if (e.attachment.get("type") == "queued_command" and e.attachment.get("commandMode") == "prompt"
                and isinstance(origin, dict) and origin.get("kind") == "human"
                and isinstance(e.attachment.get("prompt"), str)):
            e.user_text = e.attachment["prompt"][:MAX_TEXT]
            e.queued = True
            e.human = is_human_text({"isMeta": e.attachment.get("isMeta")}, e.user_text) and not e.meta
        elif e.attachment.get("type") in ("file", "edited_text_file", "compact_file_reference"):
            if isinstance(e.attachment.get("filename"), str):
                e.read_files.append(e.attachment["filename"][:MAX_PATH])
        return e
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if e.rtype == "assistant" and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                inp, cut = _pick_input(b.get("input"))
                tu = ToolUse(str(b.get("id")), str(b.get("name")), inp, truncated=cut)
                tool_index[tu.id] = tu
                e.tool_uses.append(tu)
    elif e.rtype == "user":
        texts = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_result":
                    tid = str(b.get("tool_use_id"))
                    tu = tool_index.get(tid)
                    e.tool_results.append(ToolResult(
                        tid, tu.name if tu else "", tu.input if tu else {},
                        _result_text(b.get("content")), bool(b.get("is_error"))))
                elif b.get("type") == "text" and isinstance(b.get("text"), str):
                    texts.append(b["text"])
        if not e.tool_results:
            e.user_text = "\n".join(texts)[:MAX_TEXT]
            e.human = is_human_text(rec, e.user_text) and not e.meta
    return e


def events_from_records(records: Iterable[Any], include_sidechain: bool = False) -> Iterator[Event]:
    """The scan loop of Step 0: parse each record, flag a queued prompt's later duplicate."""
    tool_index: dict = {}
    queued: set = set()
    for rec in records:
        if not isinstance(rec, dict) or (rec.get("isSidechain") and not include_sidechain):
            continue
        e = event_from_record(rec, tool_index)
        if e.human:
            h = hash16(e.user_text)
            if e.queued:
                queued.add(h)
            elif h in queued:
                queued.discard(h)
                e.dup = True
        yield e

