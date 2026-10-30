"""One guarded `urlsplit` for the watcher: Python 3.11.4+ raises ValueError on a bracketed host that is no IPv6 literal
(`https://[REDACTED]/x`, `http://[abc`), and on other malformed netlocs. A URL that does not split is malformed, so `split` returns None
for it (and for anything that is not a str) and each caller drops the source or refuses the redirect instead of raising. Stdlib only,
network-free, and never on the hook path (tests/test_hook_conformance.py `ABSENT`).
"""

from __future__ import annotations

import urllib.parse


def split(url: str) -> urllib.parse.SplitResult | None:
    """`urllib.parse.urlsplit(url)`, or None when `url` is not a str or the standard parser rejects it."""
    if not isinstance(url, str):
        return None
    try:
        return urllib.parse.urlsplit(url)
    except ValueError:
        return None
