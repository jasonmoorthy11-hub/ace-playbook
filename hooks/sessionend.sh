#!/bin/bash
# SessionEnd hook (matcher: clear). Only enqueues the transcript path: the default
# SessionEnd budget is 1.5 s, so no cleaning, scrubbing or API work happens here.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
ACE_HOME="$(cd "$ACE_HOME" 2>/dev/null && pwd -P)" || exit 0
PYTHONPATH="$ACE_HOME" "${ACE_PYTHON:-python3}" -P -m ace_playbook.enqueue 2>/dev/null
exit 0
