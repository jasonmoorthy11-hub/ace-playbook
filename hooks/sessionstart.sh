#!/bin/bash
# SessionStart hook: prints the advisory block as additionalContext JSON. Always exits 0.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHONPATH="$ACE_HOME${PYTHONPATH:+:$PYTHONPATH}" "${ACE_PYTHON:-python3}" -P -m ace_playbook.inject start 2>/dev/null
exit 0
