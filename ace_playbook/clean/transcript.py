"""Parse a Claude Code JSONL transcript into a cleaned sequence of turns.

Dropped: harness noise (system-reminders and similar tag blocks, hook output,
attachments, summaries), sidechain (subagent) records, thinking blocks, and
tool_result blobs over a size limit. Kept: user text, assistant text, tool_use
names with short command strings, and short tool results.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

NOISE_TAGS = ("system-reminder", "local-command-caveat", "local-command-stdout",
              "local-command-stderr", "command-message", "ide_opened_file",
              "ide_selection", "user-prompt-submit-hook", "task-notification")
NOISE_RE = re.compile(
    "|".join(rf"<{t}[^>]*>.*?</{t}>" for t in NOISE_TAGS), re.DOTALL)
ACE_BLOCK_RE = re.compile(r"=== ACE PLAYBOOK.*?=== END ACE PLAYBOOK ===", re.DOTALL)

USER_TEXT_MAX = 6000
ASSISTANT_TEXT_MAX = 3000
COMMAND_MAX = 200
ARG_MAX = 120
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


@dataclass
class Turn:
    role: str  # user | assistant | tool_use | tool_result
    text: str
    origin: str  # user_message | user_file | tool_output | assistant
    tool: str | None = None


@dataclass
class Drops:
    sidechain: int = 0
    noise_records: int = 0
    big_results: int = 0
    bad_lines: int = 0


@dataclass
class Parsed:
    turns: list[Turn] = field(default_factory=list)
    drops: Drops = field(default_factory=Drops)
    hook_blocks: int = 0
    failures: list[tuple[str, bool]] = field(default_factory=list)  # (command, is_error)
    session_id: str = ""
    cwd: str = ""


def strip_noise(text: str) -> str:
    return ACE_BLOCK_RE.sub("", NOISE_RE.sub("", text)).strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f" [...{len(text) - limit} chars cut]"


def _blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tool_summary(name: str, inp: dict) -> str:
    if name == "Bash":
        return _clip(str(inp.get("command", "")), COMMAND_MAX)
    for key in ("file_path", "path", "pattern", "url", "query", "description"):
        if inp.get(key):
            return _clip(str(inp[key]), ARG_MAX)
    return ""


def _result_text(block: dict) -> str:
    c = block.get("content", "")
    if isinstance(c, list):
        c = "\n".join(str(b.get("text", "")) for b in c if isinstance(b, dict))
    return str(c)


def _is_hook_block(rec: dict, text: str) -> bool:
    att = rec.get("attachment")
    if isinstance(att, dict) and att.get("type") == "hook_blocking_error":
        return True
    return bool(re.search(r"hook (?:blocking )?error|blocked by (?:a )?hook|hook.{0,30}(?:denied|blocked)",
                          text[:600], re.IGNORECASE))


def _result_origin(use: dict, cwd: str, written: set[str], untrusted: tuple[str, ...]) -> str:
    """A Read of a file inside the project that the agent did not write itself is user-owned."""
    if use.get("name") != "Read":
        return "tool_output"
    path = str(use.get("input", {}).get("file_path", ""))
    if not path or path in written or any(m in path for m in untrusted):
        return "tool_output"
    if path.startswith("/") and cwd and not path.startswith(cwd):
        return "tool_output"
    return "user_file"


def parse_records(records: Iterator[dict], result_max: int,
                  untrusted: tuple[str, ...] = ("upstream/", "node_modules/", ".venv/")) -> Parsed:
    out = Parsed()
    uses: dict[str, dict] = {}
    written: set[str] = set()
    for rec in records:
        out.cwd = out.cwd or rec.get("cwd", "") or ""
        out.session_id = out.session_id or rec.get("sessionId", "") or ""
        if rec.get("isSidechain"):
            out.drops.sidechain += 1
            continue
        if rec.get("type") == "attachment" and _is_hook_block(rec, ""):
            out.hook_blocks += 1
        msg = rec.get("message")
        if rec.get("type") not in ("user", "assistant") or not isinstance(msg, dict) or rec.get("isMeta"):
            out.drops.noise_records += 1
            continue
        for b in _blocks(msg.get("content")):
            _handle_block(rec["type"], b, out, uses, written, result_max, untrusted)
    return out


def _handle_block(kind: str, b: dict, out: Parsed, uses: dict, written: set,
                  result_max: int, untrusted: tuple[str, ...]) -> None:
    t = b.get("type")
    if t == "text" and kind == "user":
        text = strip_noise(str(b.get("text", "")))
        if text:
            out.turns.append(Turn("user", _clip(text, USER_TEXT_MAX), "user_message"))
    elif t == "text" and kind == "assistant":
        text = strip_noise(str(b.get("text", "")))
        if text:
            out.turns.append(Turn("assistant", _clip(text, ASSISTANT_TEXT_MAX), "assistant"))
    elif t == "tool_use":
        name, inp = str(b.get("name", "?")), b.get("input") or {}
        uses[str(b.get("id"))] = {"name": name, "input": inp}
        if name in WRITE_TOOLS and inp.get("file_path"):
            written.add(str(inp["file_path"]))
        out.turns.append(Turn("tool_use", f"{name}: {_tool_summary(name, inp)}".rstrip(": "),
                              "assistant", name))
    elif t == "tool_result":
        _handle_result(b, out, uses, written, result_max, untrusted)


def _handle_result(b: dict, out: Parsed, uses: dict, written: set,
                   result_max: int, untrusted: tuple[str, ...]) -> None:
    text = strip_noise(_result_text(b))
    use = uses.get(str(b.get("tool_use_id")), {})
    if _is_hook_block({}, text):
        out.hook_blocks += 1
    if use.get("name") == "Bash":
        cmd = _tool_summary("Bash", use.get("input", {}))
        failed = bool(b.get("is_error")) or text.startswith("Exit code ")
        out.failures.append((cmd, failed))
    if len(text) > result_max:
        out.drops.big_results += 1
        out.turns.append(Turn("tool_result", f"[result omitted: {len(text)} chars]", "tool_output",
                              use.get("name")))
        return
    if text:
        origin = _result_origin(use, out.cwd, written, untrusted)
        out.turns.append(Turn("tool_result", text, origin, use.get("name")))


def read_records(path: Path, drops: Drops | None = None) -> Iterator[dict]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                if drops:
                    drops.bad_lines += 1
                continue
            if isinstance(rec, dict):
                yield rec
