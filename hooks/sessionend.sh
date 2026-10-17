#!/bin/bash
# SessionEnd hook (matcher: clear). Only enqueues the transcript path: the default
# SessionEnd budget is 1.5 s, so no cleaning, scrubbing or API work happens here.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHONPATH="$ACE_HOME${PYTHONPATH:+:$PYTHONPATH}" "${ACE_PYTHON:-python3}" -m ace_playbook.enqueue 2>/dev/null
exit 0
