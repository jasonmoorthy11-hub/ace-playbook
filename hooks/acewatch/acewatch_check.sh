#!/bin/bash
# acewatch_check.sh -- the operator-side acewatch status line for the nightly (config key `acewatch_check`; docs/watch/SANDBOX.md).
# Prints ONE line: `acewatch ` + the first `open ...` / `none` / `disabled` line of `compile watch status` as the acewatch user.
# Exits 1 (printing nothing) on any failure. Copy it outside the checkout, edit the three variables, chmod 0755, name it in your config.
# It needs ONE sudoers line, added by you with visudo (nothing installs it):
#   <operator> ALL=(acewatch) NOPASSWD: <ACEWATCH_PY> -m ace_playbook.compile --config <ACEWATCH_CONFIG> watch status
# (the two variables below, written out in full). Linux: the same line works; the paths are examples, set the real ones.
set -u
ACEWATCH_USER=acewatch
ACEWATCH_PY=/opt/acewatch/copy/.venv/bin/python
ACEWATCH_CONFIG=/var/acewatch/.config/ace-playbook/config.json

[ "${1:-}" = "--check" ] || exit 1
cd / || exit 1      # python -m must not run from a working directory the caller chose
out=$(sudo -n -u "$ACEWATCH_USER" "$ACEWATCH_PY" -m ace_playbook.compile --config "$ACEWATCH_CONFIG" watch status 2>/dev/null) || exit 1
line=$(printf '%s\n' "$out" | grep -m1 -E '^(open |none|disabled)') || exit 1
printf 'acewatch %s\n' "$line"
