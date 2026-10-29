"""Per-detector source hash (docs/design/compiler.md 4 and 6), by exclusion: hash everything, minus what cannot matter.

A row's hash covers the whole text of detectors.py and catalogue.py except the BODIES of the other rows' `fire` functions
(their decorators, signatures and defaults run at import time and stay hashed; a fire that the remaining text still names
is kept whole). catalogue.py is hashed whole, registry literal included: an expression inside it runs at import time in every
process, and the row's own fields enter again through `meta` (harmless, and what keeps a runtime-built row honest). `advance`
and `blank_state` are hashed for every row, since `evaluate` runs them in the same hook call as every detector. Imports,
decorators, rebindings, constants, comments and every other statement are hashed verbatim. Excision is by exact source
span, so statements sharing a line with an excised node stay. Pure AST over on-disk text, no runtime introspection. The
hash describes the text read from disk at first use in the process (as the old whole-file scheme did), not the loaded
code: a tampered .pyc or an in-memory monkeypatch is invisible to it.
"""

from __future__ import annotations

import ast
import hashlib
from functools import lru_cache
from pathlib import Path

from ..canon import canonical

_HERE = Path(__file__).parent
_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_LEAVES = ("events", "shell", "textrules", "resolve", "resolve_pre", "resolve_chain")


@lru_cache(maxsize=None)
def _read(path: Path) -> str:
    return path.read_text()


def module_text(path: Path) -> str:
    """On-disk text of a hashed module, read once per process (patched in tests to simulate an edit)."""
    return _read(path)


@lru_cache(maxsize=None)
def _tree(text: str) -> ast.Module:
    return ast.parse(text)


def excised_text(text: str, drop=()) -> str:
    """`text` with the body of each dropped top-level function replaced by `...excised...` (decorators, signature and
    defaults kept). Only the body's exact characters are replaced (columns are UTF-8 byte offsets)."""
    lines, spans = text.split("\n"), []
    for node in _tree(text).body:
        if isinstance(node, _FUNCS) and node.name in drop:
            first = node.body[0]
            spans.append((first.lineno, first.col_offset, node.end_lineno, node.end_col_offset))
    for l1, c1, l2, c2 in sorted(spans, reverse=True):
        lines[l1 - 1:l2] = [lines[l1 - 1].encode()[:c1].decode() + "...excised..." + lines[l2 - 1].encode()[c2:].decode()]
    return "\n".join(lines)


def _ids(node) -> set[str]:
    return {n.id if isinstance(n, ast.Name) else n.attr for n in ast.walk(node) if isinstance(n, (ast.Name, ast.Attribute))}


def _remaining(node, drop) -> set[str]:
    """Names the text still mentions once the bodies of `drop` are excised: a dropped function keeps its header."""
    if isinstance(node, _FUNCS) and node.name in drop:
        return set().union(*(_ids(x) for x in [*node.decorator_list, node.args, node.returns] if x))
    return _ids(node)


def detector_drop(text: str, fire: str, others) -> set[str]:
    """Names of the other rows' fires whose bodies are excised from detectors.py for the row whose fire is `fire`: every
    one that no remaining text names (a dropped fire's decorators and signature count as remaining), to a fixed point."""
    body = _tree(text).body
    defs = {n.name for n in body if isinstance(n, _FUNCS)}
    drop = {o for o in others if o in defs and o != fire}
    while keep := drop & set().union(*(_remaining(n, drop) for n in body)):
        drop -= keep
    return drop


def digest(name: str, meta: dict, fire: str, others, leaves: tuple[str, ...]) -> str:
    """sha256 over NUL-joined, each part preceded by its label: the row name, its fire function's name, canonical
    metadata, detectors.py (the other rows' fire bodies excised) and catalogue.py (whole), then each leaf module's whole
    text. No dedupe."""
    dtext = module_text(_HERE / "detectors.py")
    parts = [("name", name), ("fire", fire), ("meta", canonical(meta)),
             ("detectors", excised_text(dtext, detector_drop(dtext, fire, others))),
             ("catalogue", module_text(_HERE / "catalogue.py")),
             *zip(_LEAVES, leaves)]
    return hashlib.sha256("\0".join(f"{label}\0{text}" for label, text in parts).encode()).hexdigest()
