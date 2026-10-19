#!/bin/bash
# Shadow evaluator for approved ACE proposals. Arg is a fixed literal from settings (pre|stop).
# The payload reaches Python on stdin only; no payload field is expanded by the shell.
# Python prints a decision as JSON on stdout. This script never exec's Python and ALWAYS exits 0,
# because exit 2 means "block" to the harness and an interpreter error must never block.
# Python runs isolated, mirroring hooks/pre-commit-scrub.sh:12-18: -I (with -P where it exists)
# and a cd into $ACE_HOME, so a json.py in the project's working directory is never imported.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
[ -n "$ACE_HOME" ] || exit 0
PY="${ACE_PYTHON:-python3}"
if "$PY" -I -P -c pass 2>/dev/null; then
  ISOLATE=(-I -P)
else
  ISOLATE=(-I)  # Python < 3.11 has no -P (nor PYTHONSAFEPATH); -I already drops cwd and script dir
fi
cd "$ACE_HOME" || exit 0
"$PY" "${ISOLATE[@]}" -c 'import sys; sys.path.insert(0, sys.argv[1]); from ace_playbook.compile.shadow import main; sys.exit(main(sys.argv[2:]))' \
  "$ACE_HOME" "$1"
exit 0
