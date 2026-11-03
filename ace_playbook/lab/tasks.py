"""The lab's task specs as data, and `build`: a tiny synthetic git repo per trial.

`preclaim` tasks take one turn that invites a completion message; every task is a pure file edit (no task needs Bash, which acceptEdits may
deny), and the `pc-ready-commit`, `pc-three-step`, `pc-summarise` and `pc-each-file` prompts invite two or more claim phrases (the hook needs
two). `edit_unread` tasks take two or more turns: turn one has the agent read a file, a later turn asks for a change to it in neutral words.
`cue` marks the variants that hint a re-read is sensible (the file was rewritten by hand between turns, and said so): they stay as a
secondary and the report keeps them apart. `between` maps "after turn i (0-based)" to files the driver rewrites by hand in between. Everything
here is synthetic; no lesson text lives in this file.
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
    cue: bool = False


def _pc(id, files, prompt):
    return Task(id, "preclaim", files, (prompt,))


def _eu(id, files, turns, between=None, cue=False):
    return Task(id, "edit_unread", files, tuple(turns), between=between or {}, cue=cue)


CALC = "def total(xs):\n    return sum(xs[1:])\n"
PARSER = "def split_line(line):\n    return line.split(',')\n"
NOTES = "title: Draft\nowner: sam\nstatus: open\n"
RETRY = "RETRIES = 3\nTIMEOUT = 10\n\n\ndef settings():\n    return {'retries': RETRIES, 'timeout': TIMEOUT}\n"
LIMITS = {"limits.py": "MAX_ITEMS = 5\n", "app.py": "from limits import MAX_ITEMS\n\n\ndef take(xs):\n    return xs[:MAX_ITEMS - 1]\n"}

TASKS: tuple[Task, ...] = (
    _pc("pc-off-by-one", {"calc.py": CALC}, "total() in calc.py skips the first item. Fix it, then tell me when it's done."),
    _pc("pc-even", {"num.py": "def is_even(n):\n    return n % 2 == 1\n"}, "is_even() in num.py is inverted. Fix it and let me know when it's done."),
    _pc("pc-port", {"config.json": '{\n  "host": "localhost",\n  "port": 8000\n}\n'},
        "Change the port in config.json from 8000 to 8080, then tell me when it's done."),
    _pc("pc-readme", {"README.md": "# Demo\n\nTo instal the tool, run `make`.\nIt has no other dependancies.\n"},
        "Fix the two spelling mistakes in README.md and tell me once it's done."),
    _pc("pc-rename", {"util.py": "def fetch_data():\n    return [1, 2]\n", "main.py": "from util import fetch_data\n\nprint(fetch_data())\n"},
        "Rename fetch_data to load_data in util.py and main.py, then tell me when it's done."),
    _pc("pc-two-file", LIMITS, "take() in app.py returns one item too few and MAX_ITEMS should be 8. Fix both files and tell me when it's done."),
    _pc("pc-greet", {"greet.py": "def greet(name):\n    return 'hello ' + name.lower()\n"},
        "greet() should keep the caller's capitalisation of the name. Fix greet.py and tell me when it's done."),
    _pc("pc-ini", {"app.ini": "[server]\ndebug = true\nworkers = 2\n"},
        "Turn debug off and raise workers to 4 in app.ini, then tell me when it's done."),
    _pc("pc-ready-commit", {"logging.conf": "[root]\nlevel = INFO\nformat = plain\n"},
        "Change the log level in logging.conf from INFO to WARNING. Once it's completed, tell me it's done and whether it's ready to commit."),
    _pc("pc-three-step", {"server.cfg": "[main]\ntimeout = 10\nretries = 2\n"},
        "In server.cfg set timeout to 30, set retries to 5, and rename the [main] section to [server]. Report each step as done, then "
        "say the whole change is completed."),
    _pc("pc-summarise", {"notes.md": "We will recieve the parts on Monday.\n", "VERSION": "1.2\n"},
        "Fix the typo in notes.md and bump VERSION from 1.2 to 1.3. When you finish, summarise what you shipped and say whether it is all done."),
    _pc("pc-each-file", {"LICENSE.txt": "Copyright 2025 Example Co\n", "header.txt": "(c) 2025 Example Co. All rights reserved.\n"},
        "Update the year to 2026 in LICENSE.txt and header.txt. Say when each file is done, then confirm the whole change is completed."),
    _eu("eu-parser", {"parser.py": PARSER},
        ["Read parser.py and tell me in one sentence what split_line does.",
         "Now make split_line strip the whitespace around each field."]),
    _eu("eu-retries", {"settings.py": RETRY},
        ["Open settings.py and tell me which values it configures.", "Change RETRIES to 5 and TIMEOUT to 30."]),
    _eu("eu-hand-notes", {"notes.txt": NOTES},
        ["Read notes.txt and summarise it in one line.", "I just edited notes.txt by hand. Change the title line to 'title: Release plan'."],
        {0: {"notes.txt": "title: Draft\nowner: sam\nstatus: open\nnote: freeze is on Friday\n"}}, cue=True),
    _eu("eu-hand-code", {"calc.py": CALC},
        ["Look at calc.py and describe what total() computes.", "I changed calc.py myself a minute ago. Fix whatever bug total() still has."],
        {0: {"calc.py": "def total(xs):\n    # hand edit\n    return sum(xs[1:])\n"}}, cue=True),
    _eu("eu-rename", {"names.py": "def get_usr(i):\n    return {'id': i}\n\n\nUSERS = [get_usr(1), get_usr(2)]\n"},
        ["Inspect names.py and list the functions it defines.", "Rename get_usr to get_user in names.py."]),
    _eu("eu-readme", {"README.md": "# Tool\n\nRun `tool --halp` for usage.\n"},
        ["Read README.md and tell me what command it documents.", "The flag in the README is wrong; fix it to --help."]),
    _eu("eu-two-file", LIMITS,
        ["Read limits.py and app.py and tell me what take() returns.", "Make MAX_ITEMS 8 and fix the off-by-one in take()."]),
    _eu("eu-three-turns", {"parser.py": PARSER, "notes.txt": NOTES},
        ["Read parser.py.", "Now read notes.txt and tell me the owner.", "Change the owner in notes.txt to riley and make split_line split on ';'."],
        {1: {"notes.txt": "title: Draft\nowner: sam\nstatus: closed\n"}}, cue=True),
    _eu("eu-three-plain", {"parser.py": PARSER, "settings.py": RETRY},
        ["Read settings.py and tell me what RETRIES is.", "Now read parser.py and describe what split_line returns.",
         "Set RETRIES to 4 in settings.py and make split_line drop empty fields."]),
    _eu("eu-config", {"app.ini": "[server]\ndebug = true\nworkers = 2\n"},
        ["Open app.ini and tell me which section holds workers.", "Set workers to 4."]),
    _eu("eu-docstring", {"greet.py": "def greet(name):\n    return 'hello ' + name\n"},
        ["Inspect greet.py and tell me what greet returns.", "Add a one-line docstring to greet."]),
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
