"""The brief's stream reader, tool drift audit and ledger append (docs/design/compiler.md 8.x "Brief (as built)").

Stage 1 runs with the operator's user-level permissions (D-117), so a connector tool added after the deny file was written is not stopped by
`--tools`, `--allowedTools` or the default mode: if the operator keeps a server-wide or wildcard allow, the new tool is open. `drift` is the
check that notices it after the run: any `mcp__` tool in the stream's init tool list or in a tool_use block that is not on the read allowlist.
Stream lines are split on "\\n" ONLY: `str.splitlines` also splits on U+2028, U+2029, U+0085 and U+001C-U+001E, which a JSON writer may emit
raw inside a string. No model call, no network, never returns mail content: tool names only. Stdlib only.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from pathlib import Path

NAME = re.compile(r"[A-Za-z0-9_.-]+")
ASCII_WS = " \t\r\n\v\f"          # the same set `[[:space:]]` matches under LC_ALL=C in hooks/brief_lib.sh


def jl(text: str):
    """One JSON value from a line, else None."""
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def events(path) -> list:
    """The JSON objects of a stream-json file (one per "\\n"-terminated line; anything else is skipped)."""
    return [e for e in map(jl, Path(path).read_bytes()[:50_000_000].decode("utf-8", "replace").split("\n")) if isinstance(e, dict)]


def tool_uses(evs: list) -> set:
    """The names of every tool_use block of the assistant messages."""
    return {b["name"] for e in evs if e.get("type") == "assistant" and isinstance(e.get("message"), dict)
            for b in e["message"].get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("name"), str)}


def allowed_names(path) -> set:
    """The names of a list file: a line is blank, a full-line `#` comment or ONE name of [A-Za-z0-9_.-]; anything else (a BOM, a `#` after a
    name, a space inside, a non-ASCII byte) raises ValueError. brief.sh and hooks/brief_guard.py read the files the same way."""
    out = set()
    with open(path, encoding="utf-8", newline="\n") as fh:
        for ln in (raw.strip(ASCII_WS) for raw in fh):
            if ln and not ln.startswith("#"):
                if not NAME.fullmatch(ln):
                    raise ValueError("list line")
                out.add(ln)
    return out


def drift(stream, allow) -> list:
    """Sorted `mcp__` tool names that the init tool list or a tool_use block shows and the allowlist does not name. ValueError when the stream
    has no init event with a `tools` list (nothing was audited, so nothing may pass)."""
    ok, evs = allowed_names(allow), events(stream)
    inits = [e.get("tools") for e in evs if e.get("type") == "system" and e.get("subtype") == "init"]
    if not inits or not all(isinstance(t, list) for t in inits):
        raise ValueError("no init")
    seen = {t for tools in inits for t in tools if isinstance(t, str)} | tool_uses(evs)
    return sorted(t for t in seen if t.startswith("mcp__") and t not in ok)


def append_row(path, row: dict) -> None:
    """One line to the ledger: 0600, O_APPEND, locked."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "ab") as fh:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        fh.write(json.dumps(row, sort_keys=True).encode() + b"\n")
