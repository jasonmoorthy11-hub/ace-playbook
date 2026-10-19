"""Readers for the three migration inputs. Bad files and lines become error notes, not crashes."""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_date(value: object) -> str | None:
    """The value if it is a real YYYY-MM-DD date, else None."""
    if isinstance(value, str) and _DATE.match(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return None
        return value
    return None


@dataclass
class Legacy:
    """Legacy lessons by id. The first occurrence of an id supplies its text."""
    texts: dict[str, str] = field(default_factory=dict)
    created: dict[str, str] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def load_legacy(pattern: str) -> Legacy:
    out = Legacy()
    for name in sorted(glob.glob(os.path.expanduser(pattern))):
        path = Path(name)
        if not path.is_file():
            continue
        out.files.append(path.name)
        try:
            lessons = json.loads(path.read_text(encoding="utf-8"))["lessons"]
            if not isinstance(lessons, list):
                raise TypeError("lessons is not a list")
        except (OSError, ValueError, KeyError, TypeError) as e:
            out.unreadable.append(path.name)
            out.notes.append(f"legacy file {path.name} unreadable ({type(e).__name__})")
            continue
        for item in lessons:
            if not (isinstance(item, dict) and isinstance(item.get("id"), str)
                    and isinstance(item.get("text"), str)):
                continue
            out.texts.setdefault(item["id"], item["text"])
            day = valid_date(item.get("created"))
            if day:
                out.created.setdefault(item["id"], day)
    return out


def _jsonl(path: Path, label: str, required: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    notes: list[str] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            notes.append(f"{label} line {n}: invalid JSON, skipped")
            continue
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in required):
            notes.append(f"{label} line {n}: needs string fields {list(required)}, skipped")
            continue
        rows.append(row)
    return rows, notes


def load_classified(path: Path) -> tuple[list[dict], list[str]]:
    return _jsonl(Path(path), Path(path).name, ("id",))


def load_overrides(path: Path | None) -> tuple[dict[str, dict], list[str]]:
    """Overrides by id; a later line for the same id replaces an earlier one."""
    if path is None:
        return {}, []
    rows, notes = _jsonl(Path(path), Path(path).name, ("id", "action"))
    out: dict[str, dict] = {}
    for row in rows:
        if "text" in row and not isinstance(row["text"], str):
            notes.append(f"{Path(path).name}: override for {row['id']} has a non-string text, skipped")
            continue
        out[row["id"]] = row
    return out, notes
