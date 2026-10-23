"""Parse a Claude Code JSONL transcript into a cleaned sequence of turns.

Dropped: harness noise (system-reminders and similar tag blocks, hook output,
attachments, summaries), sidechain (subagent) records, thinking blocks, and
tool_result blobs over a size limit. Kept: user text, assistant text, tool_use
names with short command strings, and short tool results. The user's `!` shell-mode
records become `shell_output` tool results; a subagent's writes are noted, its turns are not kept.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .origins import is_written, normalise, written_paths

NOISE_TAGS = ("system-reminder", "local-command-caveat", "local-command-stdout",
              "local-command-stderr", "command-message", "ide_opened_file",
              "ide_selection", "user-prompt-submit-hook", "task-notification")
NOISE_RE = re.compile(
    "|".join(rf"<{t}[^>]*>.*?</{t}>" for t in NOISE_TAGS), re.DOTALL)
ACE_BLOCK_RE = re.compile(r"=== ACE PLAYBOOK.*?=== END ACE PLAYBOOK ===", re.DOTALL)
SHELL_TAG_RE = re.compile(r"</?bash-(?:input|stdout|stderr)\b[^>]*>", re.IGNORECASE)

USER_TEXT_MAX = 6000
ASSISTANT_TEXT_MAX = 3000
COMMAND_MAX = 200
ARG_MAX = 120


@dataclass
class Turn:
    role: str  # user | assistant | tool_use | tool_result
    text: str
    origin: str  # user_message | user_file | agent_file | shell_output | tool_output | assistant
    tool: str | None = None


@dataclass
class Drops:
    sidechain: int = 0
    compact_summary: int = 0
    noise_records: int = 0
    big_results: int = 0
    bad_lines: int = 0
    unparsed_bash: int = 0  # Bash commands the write tracker needed the cmd.split() fallback for (it then marks every word)


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


def _under(path: str, root: str) -> bool:
    return not root or path == root or path.startswith(root.rstrip("/") + "/")


def _result_origin(use: dict, root: str, written: set[str], untrusted: tuple[str, ...], cwd: str = "") -> str:
    """A Read of a file the agent itself wrote is `agent_file`; one inside the project that it did not write is user-owned.
    `root` (the session's first cwd) is the trust boundary; the record's `cwd` only makes a relative path absolute, and a record whose
    cwd is outside the root makes its Reads `tool_output`."""
    if use.get("name") != "Read":
        return "tool_output"
    path = str(use.get("input", {}).get("file_path", ""))
    if not path or not root or not _under(normalise(cwd or root, ""), root):  # no working directory at all: there are no user files
        return "tool_output"
    path = normalise(path, cwd or root)
    if is_written(path, written):
        return "agent_file"
    return "tool_output" if any(m in path for m in untrusted) or not _under(path, root) else "user_file"


def subagent_records(path: Path, drops: Drops | None = None) -> Iterator[dict]:
    """Records of every `<session>/subagents/**/*.jsonl` beside the main transcript (where the harness writes a subagent's turns)."""
    folder = Path(path).with_suffix("") / "subagents"
    for f in sorted(folder.rglob("*.jsonl")) if folder.is_dir() else []:
        try:
            yield from read_records(f, drops)
        except OSError:
            continue


def parse_records(records: Iterator[dict], result_max: int,
                  untrusted: tuple[str, ...] = ("upstream/", "node_modules/", ".venv/"),
                  side_records: Iterable[dict] = ()) -> Parsed:
    out = Parsed()
    uses: dict[str, dict] = {}
    written: set[str] = set()
    side: Iterator[dict] | None = iter(side_records)
    for rec in records:
        out.cwd = out.cwd or rec.get("cwd", "") or ""
        if out.cwd and side is not None:  # the session root is known: scan the subagent transcripts against it, then drop them
            _scan_side(side, written, out)
            side = None
        out.session_id = out.session_id or rec.get("sessionId", "") or ""
        cwd = rec.get("cwd") or out.cwd
        if rec.get("isSidechain"):
            out.drops.sidechain += 1
            msg = rec.get("message")
            for b in _blocks(msg.get("content")) if isinstance(msg, dict) else []:
                _note_writes(b, written, cwd, out)  # a subagent's turns are dropped; the files it wrote still are not the user's
            continue
        if (rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly")
                or rec.get("origin") == "tool_output"):
            out.drops.compact_summary += 1  # summarized tool output is not the user speaking
            continue
        if rec.get("type") == "attachment" and _is_hook_block(rec, ""):
            out.hook_blocks += 1
        msg = rec.get("message")
        if rec.get("type") not in ("user", "assistant") or not isinstance(msg, dict) or rec.get("isMeta"):
            out.drops.noise_records += 1
            continue
        blocks = _blocks(msg.get("content"))
        shell = rec["type"] == "user" and any(b.get("type") == "text" and SHELL_TAG_RE.search(str(b.get("text", ""))) for b in blocks)
        for b in blocks:
            _handle_block(rec["type"], b, out, uses, written, result_max, untrusted, cwd, shell)
    if side is not None:
        _scan_side(side, written, out)
    return out


def _scan_side(side: Iterator[dict], written: set, out: Parsed) -> None:
    """Note the writes of subagent records (the ROOT marker and the clamp use the session root, a record without a cwd falls back to it)."""
    for rec in side:
        out.drops.sidechain += 1
        msg = rec.get("message")
        for b in _blocks(msg.get("content")) if isinstance(msg, dict) else []:
            _note_writes(b, written, str(rec.get("cwd") or out.cwd), out)


def _note_writes(b: dict, written: set, cwd: str, out: Parsed) -> None:
    if b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
        paths, unparsed = written_paths(str(b.get("name", "")), b["input"], cwd, out.cwd)
        written |= {p.casefold() for p in paths}
        out.drops.unparsed_bash += unparsed


def _handle_block(kind: str, b: dict, out: Parsed, uses: dict, written: set,
                  result_max: int, untrusted: tuple[str, ...], cwd: str = "", shell: bool = False) -> None:
    t = b.get("type")
    if t == "text" and kind == "user":
        raw = str(b.get("text", ""))
        if shell or SHELL_TAG_RE.search(raw):  # the user's `!` command and output: one shell tag makes every text block of the record shell_output
            if text := strip_noise(SHELL_TAG_RE.sub("\n", raw)):
                out.turns.append(Turn("tool_result", _clip(text, USER_TEXT_MAX), "shell_output", "shell"))
        elif text := strip_noise(raw):
            out.turns.append(Turn("user", _clip(text, USER_TEXT_MAX), "user_message"))
    elif t == "text" and kind == "assistant":
        text = strip_noise(str(b.get("text", "")))
        if text:
            out.turns.append(Turn("assistant", _clip(text, ASSISTANT_TEXT_MAX), "assistant"))
    elif t == "tool_use":
        name, inp = str(b.get("name", "?")), b.get("input") or {}
        uses[str(b.get("id"))] = {"name": name, "input": inp}
        _note_writes(b, written, cwd, out)
        out.turns.append(Turn("tool_use", f"{name}: {_tool_summary(name, inp)}".rstrip(": "),
                              "assistant", name))
    elif t == "tool_result":
        _handle_result(b, out, uses, written, result_max, untrusted, cwd)


def _handle_result(b: dict, out: Parsed, uses: dict, written: set,
                   result_max: int, untrusted: tuple[str, ...], cwd: str = "") -> None:
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
        origin = _result_origin(use, out.cwd, written, untrusted, cwd)
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
