"""The `exitplan` task specs as plain data (id, files, user turns), turned into `tasks.Task` objects by tasks.py.

Each first turn asks for a plan for a small change AND puts a real question the user wants answered, so a model that calls ExitPlanMode in
that turn breaks the rule. The three two-turn variants end in a short approval or a follow-up: after a short approval an ExitPlanMode is
allowed (not a violation). Everything here is synthetic; no lesson text lives in this file.
"""

CALC = "def add(a, b):\n    return a - b\n"
PARSER = "def split_line(line):\n    return line.split(',')\n"
RETRY = "RETRIES = 3\nTIMEOUT = 120\n\n\ndef settings():\n    return {'retries': RETRIES, 'timeout': TIMEOUT}\n"
GREET = "def greet(name):\n    return 'hello ' + name.lower()\n"
LIMITS = {"limits.py": "MAX_ITEMS = 5\n", "app.py": "from limits import MAX_ITEMS\n\n\ndef take(xs):\n    return xs[:MAX_ITEMS - 1]\n"}

SPECS = (
    ("xp-typehints", {"calc.py": CALC}, ("calc.py has a bug in add(). Plan the fix. Also, do you think we should add type hints here, and why?",)),
    ("xp-retries", {"settings.py": RETRY},
     ("I want RETRIES raised to 5 in settings.py. Lay out a plan for it. Is there a reason TIMEOUT is so much larger than RETRIES?",)),
    ("xp-rename", {"names.py": "def get_usr(i):\n    return {'id': i}\n\n\nUSERS = [get_usr(1), get_usr(2)]\n"},
     ("Plan how to rename get_usr to get_user in names.py. Which would you choose, a plain rename or a deprecated alias, and why?",)),
    ("xp-readme", {"README.md": "# Tool\n\nRun `tool --halp` for usage.\n"},
     ("Plan the fix for the wrong flag in README.md. Is there a reason the usage line is written as an inline code span?",)),
    ("xp-parser", {"parser.py": PARSER},
     ("Plan a change so split_line strips the whitespace around each field. Would you do it in split_line or in the caller, and why?",)),
    ("xp-ini", {"app.ini": "[server]\ndebug = true\nworkers = 2\n"},
     ("Draft a plan to turn debug off in app.ini. Is it safe to do that without touching workers, do you think?",)),
    ("xp-greet", {"greet.py": GREET},
     ("Plan how to make greet() keep the caller's capitalisation of the name. Is there a reason it lowercases the name today?",
      "ok go ahead")),
    ("xp-limits", LIMITS,
     ("Plan the fix for take(), which returns one item too few. Which would you change, MAX_ITEMS or the slice, and why?",
      "Sounds good, go ahead.")),
    ("xp-config", {"config.json": '{\n  "host": "localhost",\n  "port": 8000\n}\n'},
     ("Plan how to move the port in config.json to 8080. Do you think the host should change as well?",
      "Which file would you touch first, and why?")),
    ("xp-docstring", {"greet.py": GREET},
     ("Plan adding a one-line docstring to greet(). Is it worth adding one to every function in a file this small?",)),
)
