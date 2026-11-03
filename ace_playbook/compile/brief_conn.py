"""The brief's connectors check (docs/design/compiler.md 8.x "Brief (as built)"). Not on the hook path; stdlib only.

The claude.ai connectors attach to `claude -p` ASYNCHRONOUSLY: at the stream's first `system/init` event a connector may be `pending` or not in
`mcp_servers` at all; the model may then see none of its tools and answer an empty digest,
or load them itself (ToolSearch). `check` reads that init event and the
tool calls of the stage 1 stream and says whether every server the read allowlist names was ready and at least one of its tools was called. A
server is ready when it was `connected` in that init event OR a tool of it was called and the result of that call came back without an error (it
attached late: `late`). Only a slug of the checked-in allowlist is ever printed: a server name from the stream is never written down. No model call, no network.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import brief_audit as ba


def slug(name: str) -> str:
    """The tool-name prefix of a server: every character outside [A-Za-z0-9] becomes `_` (`claude.ai Gmail` -> `claude_ai_Gmail`)."""
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def required_slugs(allow_path) -> set:
    """The server slugs of the read allowlist: `mcp__<slug>__<tool>` gives `<slug>`; `ToolSearch` and other names give nothing. The slug ends at the
    FIRST `__` after `mcp__`, so a name whose server slug itself held `__` would be cut there (none does)."""
    out = set()
    for name in ba.allowed_names(allow_path):
        m = re.fullmatch(r"mcp__(.+?)__.+", name)
        if m:
            out.add(m.group(1))
    return out


def check(stream_path, allow_path) -> dict:
    """{"servers": N, "pending": [slugs], "missing": [slugs], "calls": N, "malformed": N, "late": [slugs], "ready": bool}. A required server is
    ready when an entry of it has status exactly "connected" in the FIRST init event, or a tool_use block names a tool of it and a tool_result with
    that block's id has no `is_error` (absent, null or false). `late` lists the ready servers that were not connected at init; `pending` and
    `missing` keep the init meaning but list only servers that are not ready. `ready` is: every required server ready, and at least one tool_use
    block names a tool of a required server. ValueError when nothing could be checked (a stream over ba.CAP bytes, or no init event)."""
    need = required_slugs(allow_path)
    if Path(stream_path).stat().st_size > ba.CAP:
        raise ValueError("cap")
    evs = ba.events(stream_path)
    init = next((e for e in evs if e.get("type") == "system" and e.get("subtype") == "init"), None)
    if init is None:
        raise ValueError("no init")
    listed = init.get("mcp_servers")
    entries = listed if isinstance(listed, list) else []
    state, bad = {}, 0          # slug -> True once some entry of it is connected
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("name"), str) and isinstance(e.get("status"), str):
            s = slug(e["name"])
            state[s] = state.get(s, False) or e["status"] == "connected"
        else:
            bad += 1
    prefixes = {s: f"mcp__{s}__" for s in need}
    blocks = [b for b in ba.call_blocks(evs) if isinstance(b.get("name"), str)]
    calls = sum(any(b["name"].startswith(p) and len(b["name"]) > len(p) for p in prefixes.values()) for b in blocks)      # a bare `mcp__<slug>__` is no tool
    results = [b for b in ba.result_blocks(evs) if isinstance(b.get("tool_use_id"), str)]
    failed = {b["tool_use_id"] for b in results if b.get("is_error") is not None and b.get("is_error") is not False}      # anything but absent/null/false
    answered = {b["tool_use_id"] for b in results} - failed             # a call whose result (matched by tool_use_id) came back without an error
    late = {s for s in need if not state.get(s) and any(isinstance(b.get("id"), str) and b["id"] in answered and b["name"].startswith(prefixes[s])
                                                         and len(b["name"]) > len(prefixes[s]) for b in blocks)}
    pending = sorted(s for s in need if s in state and not state[s] and s not in late)
    missing = sorted(s for s in need if s not in state and s not in late)
    return {"servers": len(entries), "pending": pending, "missing": missing, "calls": calls, "malformed": bad, "late": sorted(late),
            "ready": not pending and not missing and calls >= 1}


def main(argv=None) -> int:
    ap = ba.OneLineParser(prog="python -m ace_playbook.compile.brief_conn")
    ap.add_argument("stream")
    ap.add_argument("--allow", required=True)
    a = ap.parse_args(argv)
    try:
        r = check(a.stream, a.allow)
    except (OSError, ValueError, RecursionError) as e:
        print(f"brief: {type(e).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(r))
    return 0 if r["ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
