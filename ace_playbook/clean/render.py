"""Signals header, head+tail sampling and rendering of cleaned turns."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .transcript import Drops, Parsed, Turn, parse_records, read_records, subagent_records

HEAD_CHARS = 25_000
TAIL_CHARS = 75_000

CORRECTION_RE = re.compile(
    r"(?i)(?:^|[\s.,;!?])(?:no[,.!]|nope|wrong|that'?s not|not what i|i (?:said|asked|told)|"
    r"don'?t|do not|stop\b|why did you|you (?:should|shouldn'?t|forgot|missed)|instead\b|"
    r"revert|undo)")
INTERRUPT_PREFIX = "[Request interrupted"
PREFIX = {"user": "U", "assistant": "A", "tool_use": "T", "tool_result": "R"}


@dataclass
class Signals:
    user_corrections: int = 0
    hook_blocks: int = 0
    repeated_failing_commands: list[tuple[str, int]] = field(default_factory=list)

    def header(self, parsed: Parsed) -> str:
        rep = "; ".join(f"{n}x {c!r}" for c, n in self.repeated_failing_commands) or "none"
        d = parsed.drops
        return (
            "OUTCOME SIGNALS (computed on the full transcript before sampling)\n"
            f"- user_corrections: {self.user_corrections}\n"
            f"- hook_blocks: {self.hook_blocks}\n"
            f"- repeated_failing_commands: {rep}\n"
            f"- dropped: sidechain={d.sidechain} noise_records={d.noise_records} "
            f"big_results={d.big_results} unparsed_bash={d.unparsed_bash}\n")


def compute_signals(parsed: Parsed) -> Signals:
    corrections = sum(
        1 for t in parsed.turns
        if t.origin == "user_message" and not t.text.startswith(INTERRUPT_PREFIX)
        and CORRECTION_RE.search(t.text))
    fails = Counter(" ".join(c.split()) for c, bad in parsed.failures if bad and c)
    repeated = [(c, n) for c, n in fails.most_common(5) if n >= 2]
    return Signals(corrections, parsed.hook_blocks, repeated)


def turn_line(t: Turn) -> str:
    """`R: first line`, every further line of the turn indented: no line of tool output (a Read, an `agent_file`, the user's `shell_output`)
    or user text starts at column 0, so none can pose as a `U:` turn (S-1 F15).
    `splitlines` also breaks at \\r, \\x0b, \\x0c, \\x1c-\\x1e, \\x85 and the Unicode separators, which a reader may take for breaks."""
    return f"{PREFIX[t.role]}: " + "\n    ".join(t.text.splitlines() or [""])


def sample_turns(turns: list[Turn], head: int = HEAD_CHARS,
                 tail: int = TAIL_CHARS) -> tuple[str, list[Turn]]:
    """First `head` + last `tail` characters of rendered turns, cut at turn boundaries."""
    lines = [turn_line(t) for t in turns]
    if sum(len(x) + 1 for x in lines) <= head + tail:
        return "\n".join(lines), list(turns)
    h = 0
    used = 0
    for i, ln in enumerate(lines):
        if used + len(ln) + 1 > head:
            break
        used, h = used + len(ln) + 1, i + 1
    used, t_start = 0, len(lines)
    for i in range(len(lines) - 1, h - 1, -1):
        if used + len(lines[i]) + 1 > tail:
            break
        used, t_start = used + len(lines[i]) + 1, i
    omitted = sum(len(x) + 1 for x in lines[h:t_start])
    kept = turns[:h] + turns[t_start:]
    text = "\n".join(lines[:h] + [f"[... {t_start - h} turns / {omitted} chars omitted ...]"]
                     + lines[t_start:])
    return text, kept


@dataclass
class Cleaned:
    session_id: str
    cwd: str
    header: str
    body: str
    signals: Signals
    kept_turns: list[Turn]
    all_turns: list[Turn]

    def prompt_text(self) -> str:
        return self.header + "\nTRANSCRIPT\n" + self.body

    @property
    def user_turn_count(self) -> int:
        return sum(1 for t in self.all_turns if t.role == "user")


def clean_file(path: Path, result_max: int = 2000,
               untrusted: tuple[str, ...] = ("upstream/", "node_modules/", ".venv/")) -> Cleaned:
    drops = Drops()
    parsed = parse_records(read_records(path, drops), result_max, untrusted, subagent_records(path, drops))
    parsed.drops.bad_lines = drops.bad_lines
    signals = compute_signals(parsed)
    body, kept = sample_turns(parsed.turns)
    return Cleaned(parsed.session_id or Path(path).stem, parsed.cwd, signals.header(parsed),
                   body, signals, kept, parsed.turns)
