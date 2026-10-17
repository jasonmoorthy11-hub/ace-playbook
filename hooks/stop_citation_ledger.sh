#!/bin/bash
# Stop hook: records [L-id] citations from last_assistant_message. Always exits 0.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHONPATH="$ACE_HOME${PYTHONPATH:+:$PYTHONPATH}" "${ACE_PYTHON:-python3}" -m ace_playbook.inject cite 2>/dev/null
exit 0
