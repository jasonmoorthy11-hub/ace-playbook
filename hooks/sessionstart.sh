#!/bin/bash
# SessionStart hook: prints the advisory block as additionalContext JSON. Always exits 0.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
case "$ACE_HOME" in *:*|*$'\n'*) exit 0 ;; /*) ;; *) exit 0 ;; esac   # PYTHONPATH must be this one absolute repo path: refuse anything else
[ -f "$ACE_HOME/ace_playbook/__init__.py" ] || exit 0
PYTHONPATH="$ACE_HOME" "${ACE_PYTHON:-python3}" -P -m ace_playbook.inject start 2>/dev/null
exit 0
