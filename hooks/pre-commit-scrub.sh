#!/bin/bash
# Git pre-commit hook: scan staged files for secrets and PII; a hit blocks the commit.
# Usage (inside a repo you want to protect):
#   ln -s "$ACE_PLAYBOOK_HOME/hooks/pre-commit-scrub.sh" .git/hooks/pre-commit
# Fails closed: if the scanner cannot run, the commit is refused.
# The scanned repo is untrusted input: Python runs in isolated mode from $ACE_HOME with no
# current-directory or script-directory import path, and the repo is passed only via --root,
# so a repo-local module (ace_playbook/, json.py, ...) can never shadow the scanner.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$(readlink "$0" || echo "$0")")/.." && pwd)}"
ROOT="$(git rev-parse --show-toplevel)" || exit 1
PY="${ACE_PYTHON:-python3}"
if "$PY" -I -P -c pass 2>/dev/null; then
  ISOLATE=(-I -P)
else
  export PYTHONSAFEPATH=1  # Python < 3.11 has no -P; -I already drops cwd and script dir
  ISOLATE=(-I)
fi
cd "$ACE_HOME" || exit 1
"$PY" "${ISOLATE[@]}" -c 'import sys; sys.path.insert(0, sys.argv[1]); from ace_playbook.scrub.__main__ import main; sys.exit(main(sys.argv[2:]))' \
  "$ACE_HOME" check --mode pre-commit --staged --root "$ROOT" || {
  echo "pre-commit: scrub check failed or could not run; commit refused" >&2
  exit 1
}
