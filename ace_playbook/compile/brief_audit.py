"""The brief's stream reader, tool drift audit and ledger append (docs/design/compiler.md 8.x "Brief (as built)").

Stage 1 runs with the operator's user-level permissions (D-117), so a connector tool added after the deny file was written is not stopped by
`--tools`, `--allowedTools` or the default mode: if the operator keeps a server-wide or wildcard allow, the new tool is open. `audit` is the
check that notices it after the run: ANY tool (built-ins included) in the stream's init tool list or in a tool_use block that is not on the
read allowlist or `ToolSearch` is drift. The model chooses the names of its tool_use blocks, so a name reaches a file or a stream only when
it is a NAME that is on the allowlist, in the checked-in connector snapshot, a built-in tool name or in the init tool list (which claude
prints before the model runs); every other name is only counted (`unknown`). Stream lines are split on "\\n" ONLY: `str.splitlines` also
splits on U+2028, U+2029, U+0085 and U+001C-U+001E, which a JSON writer may emit raw inside a string. No model call, no network, never
returns mail content. Stdlib only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from pathlib import Path

NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
ASCII_WS = " \t\r\n\v\f"          # the same set `[[:space:]]` matches under LC_ALL=C in hooks/brief_lib.sh
RAW_NAME = re.compile(r'"name"\s*:\s*"(mcp__[A-Za-z0-9_.-]+)"')
CAP = 50_000_000                  # bytes of a stream that `events` reads; `audit` refuses a larger file instead of cutting it
HOOKS = Path(__file__).resolve().parents[2] / "hooks"
BUILTINS = frozenset({"Bash", "BashOutput", "Edit", "ExitPlanMode", "Glob", "Grep", "KillShell", "LS", "MultiEdit", "NotebookEdit", "NotebookRead",
                      "Read", "Skill", "SlashCommand", "Task", "Agent", "TodoWrite", "ToolSearch", "WebFetch", "WebSearch", "Write"})


def jl(text: str):
    """One JSON value from a line, else None."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def events(path) -> list:
    """The JSON objects of a stream-json file (one per "\\n"-terminated line; anything else is skipped)."""
    return [e for e in map(jl, Path(path).read_bytes()[:CAP].decode("utf-8", "replace").split("\n")) if isinstance(e, dict)]


def call_blocks(evs: list) -> list:
    """Every tool call block of the assistant messages: a content block whose `type` ends with `tool_use` (tool_use, server_tool_use,
    mcp_tool_use). A single block given as a dict counts like a list of one."""
    out = []
    for e in evs:
        content = e["message"].get("content") if e.get("type") == "assistant" and isinstance(e.get("message"), dict) else None
        out += [b for b in (content if isinstance(content, list) else [content])
                if isinstance(b, dict) and isinstance(b.get("type"), str) and b["type"].endswith("tool_use")]
    return out


def tool_uses(evs: list) -> set:
    """The names of the call blocks (strings only; see `nameless_calls`)."""
    return {b["name"] for b in call_blocks(evs) if isinstance(b.get("name"), str)}


def nameless_calls(evs: list) -> int:
    """The number of call blocks whose `name` is not a string (a list, dict, number, null or absent): the audit cannot say what was called."""
    return sum(not isinstance(b.get("name"), str) for b in call_blocks(evs))


def init_tools(evs: list) -> set:
    """The tool names of the init events (claude's own list, printed before the model runs)."""
    return {t for e in evs if e.get("type") == "system" and e.get("subtype") == "init" and isinstance(e.get("tools"), list)
            for t in e["tools"] if isinstance(t, str)}


def allowed_names(path) -> set:
    """The names of a list file: a line is blank, a full-line `#` comment or ONE name of [A-Za-z0-9_][A-Za-z0-9_.-]*; anything else (a BOM, a
    `#` after a name, a space inside, a leading `-`, a non-ASCII byte, a NUL byte ANYWHERE) raises ValueError. brief.sh and hooks/brief_guard.py
    read the files the same way."""
    raw, out = Path(path).read_bytes(), set()
    if b"\0" in raw:
        raise ValueError("NUL")
    for ln in (x.strip(ASCII_WS) for x in raw.decode("utf-8").split("\n")):
        if ln and not ln.startswith("#"):
            if not NAME.fullmatch(ln):
                raise ValueError("list line")
            out.add(ln)
    return out


def known_names(allow=None, connectors=None) -> set:
    """The names that may be written down: the allowlist, the checked-in connector snapshot (hooks/brief_connector_tools.txt) and the built-ins."""
    return allowed_names(allow or HOOKS / "brief_allowed_tools.txt") | allowed_names(connectors or HOOKS / "brief_connector_tools.txt") | BUILTINS


def called_tools(evs: list, known: set) -> dict:
    """The tool_use names that may go to the ledger (a NAME of at most 100 characters that is known or in the init list), the rest counted."""
    init, used = init_tools(evs), tool_uses(evs)
    keep = sorted(t for t in used if len(t) <= 100 and NAME.fullmatch(t) and (t in known or t in init))
    return {"_tools_called": keep, "_unknown_tools": len(used) - len(keep) + nameless_calls(evs)}


def audit(stream, allow, connectors=None) -> dict:
    """{"init": N, "drift": [...], "unknown": N, "summary": {...}}. `drift` (for the ledger) lists the tools outside the allowlist and
    `ToolSearch` that may be named (see the module doc), plus the pseudo-names `unknown` (a name that may not be written down) and
    `unparseable` (the audit cannot vouch for the stream: a non-blank line that is not a JSON object, a call block whose name is not a string,
    or a file over CAP bytes). A raw scan for `"name": "mcp__..."` of the lines that did NOT parse as JSON objects adds the names they show (a
    parsed line is read by structure: the same text inside an allowed tool's input or a mail body is data). `summary` is the one line for
    stdout: the same, but it
    names only tools of the checked-in lists and the built-ins; any other is counted. ValueError when the stream is whole but has no init
    event with a `tools` list (nothing was audited, so nothing may pass)."""
    ok, known = allowed_names(allow), known_names(allow, connectors)
    raw = Path(stream).read_bytes()
    text = raw.decode("utf-8", "replace")
    parsed = [(ln, jl(ln)) for ln in text[:CAP].split("\n") if ln.strip()]
    evs = [e for _, e in parsed if isinstance(e, dict)]
    bad = len(raw) > CAP or len(evs) != len(parsed) or nameless_calls(evs) > 0
    inits = [e.get("tools") for e in evs if e.get("type") == "system" and e.get("subtype") == "init"]
    if not bad and (not inits or not all(isinstance(t, list) for t in inits)):
        raise ValueError("no init")
    init = init_tools(evs)
    seen = init | tool_uses(evs) | set(RAW_NAME.findall("\n".join(ln for ln, e in parsed if not isinstance(e, dict))))      # malformed lines only
    listable = {t for t in seen if NAME.fullmatch(t) and (t in known or (t in init and len(t) <= 100))}
    named = sorted((listable - ok) - {"ToolSearch"})
    unknown, pseudo = len(seen - listable), (["unparseable"] if bad else [])
    shown = [t for t in named if t in known]
    hidden = unknown + len(named) - len(shown)
    return {"init": len(init), "drift": sorted(named + (["unknown"] if unknown else []) + pseudo), "unknown": unknown,
            "summary": {"init": len(init), "drift": sorted(shown + (["unknown"] if hidden else []) + pseudo), "unknown": hidden}}


def append_row(path, row: dict) -> None:
    """One line to the ledger: 0600, O_APPEND, locked."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "ab") as fh:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        fh.write(json.dumps(row, sort_keys=True).encode() + b"\n")
