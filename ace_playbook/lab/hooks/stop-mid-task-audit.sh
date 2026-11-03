#!/bin/bash
# Lab Stop hook: a simplified analogue of the operator's audit hook (same file name on purpose, so the field detector's name match
# also works on lab streams). It reads the hook JSON on stdin. If the stop is not a re-stop (stop_hook_active) and the last assistant
# message holds two or more completion-claim phrases but no "Pre-claim audit:" line, it asks for a three-line audit on stderr and
# exits 2 (the harness feeds stderr back once); otherwise it exits 0. Any problem reading the input exits 0: this hook never blocks
# on its own errors.
set -u
verdict="$(python3 -I -c '
import json, re, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
text = d.get("last_assistant_message") if isinstance(d, dict) and not d.get("stop_hook_active") else None
if not isinstance(text, str):
    sys.exit(0)
claims = re.findall(r"(shipped|completed|done|looks good|ready to commit|order .* complete|cycle close)", text, re.I)
if len(claims) >= 2 and not re.search(r"pre-claim audit:", text, re.I):
    print("block")
' 2>/dev/null)"
if [ "$verdict" = "block" ]; then
  echo "Your message claims the work is complete. Add a short three-line audit headed 'Pre-claim audit:': committed / actually did / gaps." >&2
  exit 2
fi
exit 0
