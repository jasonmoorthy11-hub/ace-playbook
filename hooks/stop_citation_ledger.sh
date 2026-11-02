#!/bin/bash
# Stop hook: records [L-id] citations from last_assistant_message. Always exits 0.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
ACE_HOME="$(cd "$ACE_HOME" 2>/dev/null && pwd -P)" || exit 0
PYTHONPATH="$ACE_HOME" "${ACE_PYTHON:-python3}" -P -m ace_playbook.inject cite 2>/dev/null
exit 0
