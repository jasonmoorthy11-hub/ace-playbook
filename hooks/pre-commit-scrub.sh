#!/bin/bash
# Git pre-commit hook: scan staged files for secrets and PII; a hit blocks the commit.
# Usage (inside a repo you want to protect):
#   ln -s "$ACE_PLAYBOOK_HOME/hooks/pre-commit-scrub.sh" .git/hooks/pre-commit
# Fails closed: if the scanner cannot run, the commit is refused.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$(readlink "$0" || echo "$0")")/.." && pwd)}"
ROOT="$(git rev-parse --show-toplevel)" || exit 1
cd "$ROOT" || exit 1
PYTHONPATH="$ACE_HOME${PYTHONPATH:+:$PYTHONPATH}" "${ACE_PYTHON:-python3}" \
  -m ace_playbook.scrub check --mode pre-commit --staged --root "$ROOT" || {
  echo "pre-commit: scrub check failed or could not run; commit refused" >&2
  exit 1
}
