"""Inertness check for text that will be injected into a session.

The rules live in the stdlib-only leaf module `ace_playbook.textrules` (so a hook can use them
without importing subprocess, sqlite3 or the store). This module re-exports every name it used
to define, so existing imports keep working.
"""

from __future__ import annotations

from ..textrules import (  # noqa: F401
    _CHECKS, _CONFIG_STRONG, _CONFIG_WORD, _DOTTED, _IPV4, _MODIFY, _PATH_CHARS, _PUNYCODE, _SCHEME,
    KNOWN_FILES, _exempt, _host_like, _path_prefix_ok, inert_reason, invisible_chars,
    unsafe_text_reason)
