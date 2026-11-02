"""The brief's connectors check (docs/design/compiler.md 8.x "Brief (as built)"). Not on the hook path; stdlib only.

The claude.ai connectors attach to `claude -p` ASYNCHRONOUSLY: at the stream's first `system/init` event a connector may be `pending` or not in
`mcp_servers` at all, the model then sees none of its tools, calls nothing and answers an empty digest. `check` reads that init event and the
tool calls of the stage 1 stream and says whether every server the read allowlist names was `connected` and at least one of its tools was
called. Only a slug of the checked-in allowlist is ever printed: a server name from the stream is never written down. No model call, no network.
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
    """{"servers": N, "pending": [slugs], "missing": [slugs], "calls": N, "malformed": N, "ready": bool}. `ready` is: every required server has an
    entry with status exactly "connected" in the FIRST init event, and at least one tool_use block names a tool of a required server. ValueError
    when nothing could be checked (a stream over ba.CAP bytes, or with no init event): nothing may pass."""
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
    prefixes = tuple(f"mcp__{s}__" for s in need)
    names = [b["name"] for b in ba.call_blocks(evs) if isinstance(b.get("name"), str)]
    calls = sum(any(n.startswith(p) and len(n) > len(p) for p in prefixes) for n in names)      # a bare `mcp__<slug>__` names no tool
    pending = sorted(s for s in need if s in state and not state[s])
    missing = sorted(s for s in need if s not in state)
    return {"servers": len(entries), "pending": pending, "missing": missing, "calls": calls, "malformed": bad,
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
