"""The brief's private exclusion list (docs/design/compiler.md 8.x, D-116): LITERAL substrings only, run in a child process under a timeout.

The file is `{"drop_senders": [...], "drop_subjects": [...], "drop_bodies_from": [...]}`; an entry is a string (matched anywhere in the
field) or a one-key object `{"starts": "text"}` / `{"contains": "text"}`. Every entry is `re.escape`d, so regex syntax in it is plain text
and no entry can backtrack; matching is case-insensitive. Mail comes from an attacker, so the matching still runs in a child
(`python3 -I brief_excl.py`: stdin `{"ex": <file>, "mail": [...]}`, stdout the kept mail) that is killed after TIMEOUT seconds. Any failure
(bad file, timeout, crash, odd output) raises Refused: the digest never passes through on failure. Stdlib only, importable and runnable alone.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

KEYS = ("drop_senders", "drop_subjects", "drop_bodies_from")
MAX_LEN, MAX_ENTRIES, TIMEOUT = 200, 500, 10


class Refused(ValueError):
    """The exclusions cannot be applied; the message is a short reason, never content."""


def _entry(item) -> str:
    """The regex source for one entry: an escaped literal, anchored with `^` for mode `starts`."""
    mode, text = "contains", item
    if isinstance(item, dict):
        if len(item) != 1 or next(iter(item)) not in ("starts", "contains"):
            raise Refused("entry")
        mode, text = next(iter(item.items()))
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_LEN:
        raise Refused("entry")
    return ("^" if mode == "starts" else "") + re.escape(text)


def compile_lists(raw) -> dict:
    """{key: one case-insensitive alternation of escaped literals, or None for an empty list}."""
    if not isinstance(raw, dict) or set(raw) - set(KEYS):
        raise Refused("shape")
    out = {}
    for key in KEYS:
        items = raw.get(key, [])
        if not isinstance(items, list) or len(items) > MAX_ENTRIES:
            raise Refused("list")
        out[key] = re.compile("|".join(_entry(i) for i in items), re.IGNORECASE) if items else None
    return out


def filter_mail(raw, mail: list) -> list:
    """Drop the threads whose sender or subject match; a drop_bodies_from sender keeps sender and subject only."""
    lists = compile_lists(raw)

    def hit(key, text):
        return lists[key] is not None and lists[key].search(text) is not None
    return [{**m, "summary": ""} if hit("drop_bodies_from", m["sender"]) else m for m in mail
            if not (hit("drop_senders", m["sender"]) or hit("drop_subjects", m["subject"]))]


def child_argv() -> list:
    return [sys.executable, "-I", str(Path(__file__).resolve())]


def apply(raw, mail: list) -> list:
    """filter_mail in a child process, killed after TIMEOUT seconds; Refused on a timeout, a crash or an answer that is not a list of mails."""
    try:
        p = subprocess.run(child_argv(), input=json.dumps({"ex": raw, "mail": mail}), capture_output=True, text=True, timeout=TIMEOUT, env={})
        out = json.loads(p.stdout) if p.returncode == 0 else None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        raise Refused("child") from None
    if not isinstance(out, list) or len(out) > len(mail) or not all(isinstance(m, dict) for m in out):
        raise Refused("child")
    return out


def _child() -> int:
    try:
        job = json.loads(sys.stdin.read())
        print(json.dumps(filter_mail(job["ex"], job["mail"])))
    except (ValueError, KeyError, TypeError):       # Refused is a ValueError
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_child())
