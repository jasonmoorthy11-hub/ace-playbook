#!/bin/bash
# Brief on wake (docs/design/compiler.md 8.x "Brief (as built)"). Usage: brief.sh [--force]
# A READ-ONLY principal: it reads calendar, mail and issues through two `claude -p` stages and writes a local HTML page. Controls: (1) scope:
# --tools ToolSearch (no built-in tool), --allowedTools from hooks/brief_allowed_tools.txt (the read allowlist), --disallowedTools from
# hooks/brief_denied_tools.txt and the same list as permissions.deny in hooks/brief-settings.json (connector writes and whole other servers),
# and -p, where anything unlisted is denied. The operator's user-level permissions.allow and defaultMode still apply inside the run (D-117),
# so stage 1 passes --permission-mode default and the deny file names EVERY connector tool the brief does not need, reads included.
# There is NO hook: brief-settings.json sets disableAllHooks so the operator's own hooks (ACE inject, citation ledger, holdout draws) stay
# out of the run, which also disables a PreToolUse guard; hooks/brief_guard.py is kept for principals that run with hooks.
# Stage 1 (tools) emits a JSON digest; CODE applies the private exclusion list; stage 2 (no tools) writes
# the brief JSON from the cleaned digest alone; CODE renders the page. Everything gathered is data, never instructions. No model writes HTML.
# Stage 1 does NOT pass --strict-mcp-config: the claude.ai connectors are account-level and cannot be named in an mcp-config file (phase 0:
# strict mode showed 0 MCP servers), so strict mode would remove the only data sources. It does NOT pass --setting-sources either: that
# drops the connectors too (they attach through the user settings source; probed 2026-10-22). Both stages pass --no-session-persistence (no
# transcript of the raw mail is written where the nightly reflect sweep would find it) and --settings (disableAllHooks, the deny list).
# --tools, --allowedTools and --disallowedTools take a variadic list: a positional prompt right after one would be read as a tool name, so
# the prompt always comes last, after a flag with a single value (--max-budget-usd).
# Config: $ACE_PLAYBOOK_CONFIG (else the default file) via `python -m ace_playbook.compile.brief conf`. $ACE_BRIEF_CLAUDE names the claude
# binary (tests inject a fake); $ACE_BRIEF_KEEP keeps the raw streams. stdout/stderr carry counts and one-line reasons, never mail content.
set -u -o pipefail
umask 077
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P) || exit 1
export ACE_PLAYBOOK_REPO="$REPO" PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin"      # launchd/Hammerspoon start with a bare PATH
CLAUDE_BIN="${ACE_BRIEF_CLAUDE:-claude}"
PYB=(uv run --quiet --project "$REPO" python)
[ -n "${ACE_BRIEF_PY:-}" ] && read -r -a PYB <<<"$ACE_BRIEF_PY"
fail() { echo "brief: $1" >&2; exit 1; }
b() { "${PYB[@]}" -m ace_playbook.compile.brief "$@"; }      # only for `conf`, before the lock and the traps exist
CHILD=""
run_bg() {   # every long step runs in the background and is waited for: a foreground child would defer a signal's trap until it ended
  "$@" &
  CHILD=$!; wait "$CHILD"; local rc=$?; CHILD=""; return "$rc"
}
py() { run_bg "${PYB[@]}" -m ace_playbook.compile.brief "$@"; }
names() { grep -v '^[[:space:]]*#' "$1" | tr -d ' \t\r' | grep -v '^$' | paste -sd, -; }

BRIEF_DIR=$(b conf brief_dir) && M1=$(b conf brief_model) && M2=$(b conf brief_model_stage2) && BUDGET=$(b conf brief_max_budget_usd) || fail "config"
RUN="$BRIEF_DIR/run"
mkdir -p -m 700 "$RUN/cwd" || fail "cannot create the run directory"      # cwd: empty, no CLAUDE.md, the only directory claude sees
TOOLS=$(names "$REPO/hooks/brief_allowed_tools.txt"); DENIED=$(names "$REPO/hooks/brief_denied_tools.txt")
[ -n "$TOOLS" ] && [ -n "$DENIED" ] || fail "empty tool allowlist or deny list"

# one run at a time (wake and unlock fire together). A lock is stale when it is older than 30 minutes or the pid written in it is dead (a
# SIGKILL leaves the lock and the raw mail behind); the next run takes it over.
LOCK="$BRIEF_DIR/run.lock"
OWNER=$(cat "$LOCK/pid" 2>/dev/null)
stale() { [ -n "$(find "$BRIEF_DIR" -maxdepth 1 -name run.lock -mmin +30)" ] || { [[ "$OWNER" =~ ^[0-9]+$ ]] && ! kill -0 "$OWNER" 2>/dev/null; }; }
[ -d "$LOCK" ] && stale && { rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; }
mkdir "$LOCK" 2>/dev/null || exit 0
echo $$ > "$LOCK/pid"
purge() { rm -f "$RUN"/stage1.* "$RUN"/stage2.* "$RUN"/digest_raw.json "$RUN"/brief_raw.json "$RUN"/rl_*.json "$RUN"/res_*.json; }
purge      # raw mail that a killed run left behind goes first
cleanup() {   # the raw streams hold mail the exclusion list has not seen yet: they go first, then the lock
  [ -n "${ACE_BRIEF_KEEP:-}" ] || purge
  rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null
}
on_signal() {   # $1 the exit code; the child is stopped and reaped before the EXIT trap runs cleanup
  [ -n "$CHILD" ] && { kill "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null; }
  exit "$1"
}
trap cleanup EXIT
trap 'on_signal 143' TERM; trap 'on_signal 130' INT; trap 'on_signal 129' HUP

FORCE=""
[ "${1:-}" = "--force" ] && FORCE="--force"
py due $FORCE; rc=$?
[ "$rc" -eq 3 ] && exit 0
[ "$rc" -eq 0 ] || fail "due check failed"

ledger_stage() {   # $1 stage name, $2 its stream: one ledger row when the stream holds a result
  py extract "$2" --what rate_limit > "$RUN/rl_first.json" 2>/dev/null
  py extract "$2" --what rate_limit --last > "$RUN/rl_last.json" 2>/dev/null
  py extract "$2" --what result > "$RUN/res_$1.json" 2>/dev/null || return 0
  py ledger "$RUN/rl_first.json" "$RUN/rl_last.json" "$1" "$RUN/res_$1.json" || echo "brief: ledger row failed for $1" >&2
}

# stage 1: tools on (scope + read-only allowlist + deny lists, no hooks), reads, emits the digest JSON. In the background so a signal can
# kill it: `exec` makes the subshell pid the claude pid.
( cd "$RUN/cwd" && exec env -u ANTHROPIC_API_KEY -u CLAUDECODE "$CLAUDE_BIN" -p --model "$M1" --no-session-persistence \
    --permission-mode default --tools ToolSearch --allowedTools "$TOOLS" --disallowedTools "$DENIED" \
    --settings "$REPO/hooks/brief-settings.json" --output-format stream-json --verbose --max-turns 12 --max-budget-usd "$BUDGET" \
    "$(cat "$REPO/hooks/brief_prompts/stage1.txt")" ) < /dev/null > "$RUN/stage1.jsonl" 2> "$RUN/stage1.err" &
CHILD=$!; wait "$CHILD"; rc=$?; CHILD=""
ledger_stage stage1 "$RUN/stage1.jsonl"
[ "$rc" -eq 0 ] || fail "stage 1 failed (exit $rc)"
py extract "$RUN/stage1.jsonl" --what text > "$RUN/digest_raw.json" || fail "stage 1 gave no result"
py digest-apply "$RUN/digest_raw.json" --out "$RUN/digest_clean.json" || fail "digest refused"

# stage 2: no tools, no MCP servers; sees only the cleaned digest
( cd "$RUN/cwd" && exec env -u ANTHROPIC_API_KEY -u CLAUDECODE "$CLAUDE_BIN" -p --model "$M2" --no-session-persistence \
    --settings "$REPO/hooks/brief-settings.json" --tools "" --strict-mcp-config --output-format stream-json --verbose --max-turns 1 \
    --max-budget-usd "$BUDGET" --system-prompt "$(cat "$REPO/hooks/brief_prompts/stage2.txt")" ) \
    < "$RUN/digest_clean.json" > "$RUN/stage2.jsonl" 2> "$RUN/stage2.err" &
CHILD=$!; wait "$CHILD"; rc=$?; CHILD=""
ledger_stage stage2 "$RUN/stage2.jsonl"
[ "$rc" -eq 0 ] || fail "stage 2 failed (exit $rc)"
py extract "$RUN/stage2.jsonl" --what text > "$RUN/brief_raw.json" || fail "stage 2 gave no result"
py render "$RUN/brief_raw.json" > /dev/null || fail "brief refused"
py state-mark > "$RUN/state_n.txt" || fail "state file"
read -r N < "$RUN/state_n.txt"; rm -f "$RUN/state_n.txt"
echo "brief: ok, $N need you"
if [ "$N" -gt 0 ] && command -v terminal-notifier > /dev/null 2>&1; then
  terminal-notifier -title "ACE brief" -message "$N need you" -open "file://$BRIEF_DIR/brief.html" > /dev/null 2>&1 || true
fi
exit 0
