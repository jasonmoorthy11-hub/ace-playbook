"""PreToolUse deny hook for a read-only principal that runs with hooks.
NOT active in hooks/brief.sh, whose settings set disableAllHooks. Stdlib json only; run as `python3 -I -S brief_guard.py [--hard]`.

Deny by default: reads the hook JSON on stdin and allows ONLY a tool whose exact name is listed in hooks/brief_allowed_tools.txt
(override: $ACE_BRIEF_ALLOWLIST), whether it is an `mcp__*` connector tool or a built-in. Everything else is denied (prints the deny JSON,
exit 0), as is any input that cannot be parsed and any allowlist that cannot be read, is empty or holds a line that is not blank, a full-line
`#` comment or exactly one name (a `#` after a name is refused: brief.sh reads the file the same way). `--hard` blocks with exit 2 and the
reason on stderr instead (Claude Code treats exit 2 as a block). An allowed tool prints nothing. Every error path denies: a failure of
this script must never let a tool through.
"""

import json
import os
import sys

REASON = "brief is read-only"
DENY = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": REASON}}


def allowed_names() -> set:
    path = os.environ.get("ACE_BRIEF_ALLOWLIST") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "brief_allowed_tools.txt")
    out = set()
    with open(path, encoding="utf-8") as fh:
        for ln in (raw.strip() for raw in fh):
            if ln and not ln.startswith("#"):
                if "#" in ln or len(ln.split()) != 1:        # blank, a full-line comment or ONE name: brief.sh reads the file the same way
                    raise ValueError("list line")
                out.add(ln)
    return out


def verdict(raw: str) -> bool:
    """True to allow. Raises on anything unexpected (the caller denies)."""
    event = json.loads(raw)
    name = event["tool_name"]
    if not isinstance(name, str) or not name:
        return False
    listed = allowed_names()
    return bool(listed) and name in listed


def main(argv: list) -> int:
    try:
        ok = verdict(sys.stdin.read())
    except BaseException:  # noqa: BLE001 - fail closed on every error, including a broken pipe or a bad encoding
        ok = False
    if ok:
        return 0
    if "--hard" in argv:
        print(REASON, file=sys.stderr)
        return 2
    print(json.dumps(DENY))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
