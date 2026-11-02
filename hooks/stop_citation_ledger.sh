#!/bin/bash
# Stop hook: records [L-id] citations from last_assistant_message. Always exits 0.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
case "$ACE_HOME" in /*) ;; *) exit 0 ;; esac   # a relative home would put the working directory on PYTHONPATH: refuse (fail open)
PYTHONPATH="$ACE_HOME" "${ACE_PYTHON:-python3}" -P -m ace_playbook.inject cite 2>/dev/null
exit 0
