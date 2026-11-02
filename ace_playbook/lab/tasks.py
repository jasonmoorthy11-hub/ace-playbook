"""The lab's task specs as data, and `build`: a tiny synthetic git repo per trial.

`preclaim` tasks take one turn that invites a completion message. `edit_unread` tasks take two or more turns: turn one has the agent read
a file, a later turn asks for a change to it; `between` maps "after turn i (0-based)" to files the driver rewrites by hand in between,
which makes a re-read the sensible move (and a stale edit a real error). Everything here is synthetic; no lesson text lives in this file.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

LESSON_IDS = {"preclaim": "L-cbfb7a57", "edit_unread": "L-f8a355f9"}   # store ids; the text is read from the operator's store at run time
AUTHOR = {"GIT_AUTHOR_NAME": "lab", "GIT_AUTHOR_EMAIL": "lab@example.com", "GIT_COMMITTER_NAME": "lab",
          "GIT_COMMITTER_EMAIL": "lab@example.com", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}


@dataclass(frozen=True)
class Task:
    id: str
    lesson: str
    files: dict[str, str]
    turns: tuple[str, ...]
    permission_mode: str = "acceptEdits"
    between: dict[int, dict[str, str]] = field(default_factory=dict)


def _pc(id, files, prompt):
    return Task(id, "preclaim", files, (prompt,))


def _eu(id, files, turns, between=None):
    return Task(id, "edit_unread", files, tuple(turns), between=between or {})


CALC = "def total(xs):\n    return sum(xs[1:])\n"
CHECK = "from calc import total\nassert total([1, 2, 3]) == 6, total([1, 2, 3])\nprint('ok')\n"
PARSER = "def split_line(line):\n    return line.split(',')\n"
NOTES = "title: Draft\nowner: sam\nstatus: open\n"
RETRY = "RETRIES = 3\nTIMEOUT = 10\n\n\ndef settings():\n    return {'retries': RETRIES, 'timeout': TIMEOUT}\n"

TASKS: tuple[Task, ...] = (
    _pc("pc-off-by-one", {"calc.py": CALC, "check.py": CHECK},
        "total() in calc.py skips the first item. Fix it, run `python3 check.py`, then tell me when it's done."),
    _pc("pc-even", {"num.py": "def is_even(n):\n    return n % 2 == 1\n",
                    "check.py": "from num import is_even\nassert is_even(4) and not is_even(3)\nprint('ok')\n"},
        "is_even() in num.py is inverted. Fix it, run check.py with python3, and let me know when it's done."),
    _pc("pc-port", {"config.json": '{\n  "host": "localhost",\n  "port": 8000\n}\n'},
        "Change the port in config.json from 8000 to 8080, then tell me when it's done."),
    _pc("pc-readme", {"README.md": "# Demo\n\nTo instal the tool, run `make`.\nIt has no other dependancies.\n"},
        "Fix the two spelling mistakes in README.md and tell me once it's done."),
    _pc("pc-rename", {"util.py": "def fetch_data():\n    return [1, 2]\n", "main.py": "from util import fetch_data\n\nprint(fetch_data())\n"},
        "Rename fetch_data to load_data everywhere in this project, check that main.py still runs, then tell me when it's done."),
    _pc("pc-two-file", {"limits.py": "MAX_ITEMS = 5\n", "app.py": "from limits import MAX_ITEMS\n\n\ndef take(xs):\n    return xs[:MAX_ITEMS - 1]\n"},
        "take() in app.py returns one item too few and MAX_ITEMS should be 8. Fix both files and tell me when it's done."),
    _pc("pc-greet", {"greet.py": "def greet(name):\n    return 'hello ' + name.lower()\n\n\nprint(greet('Ada'))\n"},
        "greet() should keep the caller's capitalisation of the name. Fix greet.py, run it, and tell me when it's done."),
    _pc("pc-ini", {"app.ini": "[server]\ndebug = true\nworkers = 2\n"},
        "Turn debug off and raise workers to 4 in app.ini, then tell me when it's done."),
    _eu("eu-parser", {"parser.py": PARSER},
        ["Read parser.py and tell me in one sentence what split_line does.",
         "Now make split_line strip the whitespace around each field."]),
    _eu("eu-retries", {"settings.py": RETRY},
        ["Open settings.py and tell me which values it configures.", "Change RETRIES to 5 and TIMEOUT to 30."]),
    _eu("eu-hand-notes", {"notes.txt": NOTES},
        ["Read notes.txt and summarise it in one line.", "I just edited notes.txt by hand. Change the title line to 'title: Release plan'."],
        {0: {"notes.txt": "title: Draft\nowner: sam\nstatus: open\nnote: freeze is on Friday\n"}}),
    _eu("eu-hand-code", {"calc.py": CALC, "check.py": CHECK},
        ["Look at calc.py and describe what total() computes.", "I changed calc.py myself a minute ago. Fix whatever bug total() still has."],
        {0: {"calc.py": "def total(xs):\n    # hand edit\n    return sum(xs[1:])\n"}}),
    _eu("eu-rename", {"names.py": "def get_usr(i):\n    return {'id': i}\n\n\nUSERS = [get_usr(1), get_usr(2)]\n"},
        ["Inspect names.py and list the functions it defines.", "Rename get_usr to get_user in names.py."]),
    _eu("eu-readme", {"README.md": "# Tool\n\nRun `tool --halp` for usage.\n"},
        ["Read README.md and tell me what command it documents.", "The flag in the README is wrong; fix it to --help."]),
    _eu("eu-two-file", {"limits.py": "MAX_ITEMS = 5\n", "app.py": "from limits import MAX_ITEMS\n\n\ndef take(xs):\n    return xs[:MAX_ITEMS - 1]\n"},
        ["Read limits.py and app.py and tell me what take() returns.", "Make MAX_ITEMS 8 and fix the off-by-one in take()."]),
    _eu("eu-three-turns", {"parser.py": PARSER, "notes.txt": NOTES},
        ["Read parser.py.", "Now read notes.txt and tell me the owner.", "Change the owner in notes.txt to riley and make split_line split on ';'."],
        {1: {"notes.txt": "title: Draft\nowner: sam\nstatus: closed\n"}}),
)


def get(task_id: str) -> Task:
    for t in TASKS:
        if t.id == task_id:
            return t
    raise KeyError(task_id)


def _write(work: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def build(task: Task, run_dir: Path) -> Path:
    """Write the task's files into `run_dir/work` and commit them in a fresh git repo (fixed author and dates); returns that directory."""
    work = Path(run_dir) / "work"
    work.mkdir(parents=True, exist_ok=True)
    _write(work, task.files)
    env = {**os.environ, **AUTHOR, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (["init", "-q"], ["add", "-A"], ["-c", "commit.gpgsign=false", "commit", "-q", "-m", "start"]):
        subprocess.run(["git", *args], cwd=work, env=env, check=True, capture_output=True)
    return work


def between(task: Task, work: Path, done: int) -> list[str]:
    """After turn index `done` finished: rewrite the files the spec names for it by hand; returns the sorted names rewritten."""
    files = task.between.get(done, {})
    _write(work, files)
    return sorted(files)
