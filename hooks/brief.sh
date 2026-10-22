#!/bin/bash
# Brief on wake (docs/design/compiler.md 8.x "Brief (as built)"). Usage: brief.sh [--force]
# A READ-ONLY principal: it reads calendar, mail and issues through two `claude -p` stages and writes a local HTML page. Controls: (1) scope:
# --tools ToolSearch (no built-in tool), --allowedTools from hooks/brief_allowed_tools.txt (the read allowlist), --disallowedTools from
# hooks/brief_denied_tools.txt and the same list as permissions.deny in hooks/brief-settings.json (connector writes and whole other servers),
# and -p, where anything unlisted is denied. The operator's user-level permissions.allow and defaultMode still apply inside the run (D-117),
# so stage 1 passes --permission-mode default and the deny file names EVERY connector tool the brief does not need, reads included. A tool
# added after the snapshot is NOT stopped by --tools or --allowedTools (they only scope built-ins and add allows) and is open if the operator
# keeps a server-wide or wildcard allow, so (2) after stage 1 `tools-audit` refuses the run (exit 2, digest discarded) when the init tool list
# or a call shows ANY tool (a built-in too) outside the allowlist and ToolSearch; it prints a one-line summary, names of the checked-in lists
# only. A tool name the model invents is counted, never written down. There is NO hook: brief-settings.json sets disableAllHooks so the operator's own hooks
# stay out of the run, which also disables a PreToolUse guard; hooks/brief_guard.py is kept for principals that run with hooks.
# Stage 1 (tools) emits a JSON digest; CODE applies the private exclusion list; stage 2 (no tools) writes the brief JSON from the cleaned
# digest alone; CODE renders the page. Everything gathered is data, never instructions. No model writes HTML.
# Stage 1 does NOT pass --strict-mcp-config (the claude.ai connectors are account-level: strict mode shows 0 MCP servers) nor --setting-sources
# (it drops the connectors too; probed 2026-10-22). Both stages pass --no-session-persistence (no transcript of the raw mail where the nightly
# reflect sweep would find it) and --settings (disableAllHooks, the deny list). --tools, --allowedTools and --disallowedTools take a variadic
# list, so the prompt always comes last, after a flag with a single value (--max-budget-usd). One run at a time: a kernel lock (lockf) on
# brief_dir/run.lockf, released by the kernel when the run dies. Each stage has a wall-clock limit, ACE_BRIEF_STAGE_TIMEOUT seconds (600): TERM,
# then KILL after ACE_BRIEF_KILL_GRACE seconds (10); a stage also runs under its own perl alarm of both, so it dies when this script is killed.
# Config: $ACE_PLAYBOOK_CONFIG (else the default file) via `python -m ace_playbook.compile.brief conf`. $ACE_BRIEF_CLAUDE names the claude
# binary (tests inject a fake); $ACE_BRIEF_KEEP keeps the raw streams. stdout/stderr carry counts and one-line reasons, never mail content.
set -u -o pipefail
umask 077
HOOKS=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P) && REPO=$(dirname "$HOOKS") || exit 1
export ACE_PLAYBOOK_REPO="$REPO" PATH="$PATH:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin"      # launchd/Hammerspoon start with a bare PATH
CLAUDE_BIN="${ACE_BRIEF_CLAUDE:-claude}"
PYB=(uv run --quiet --project "$REPO" python)
[ -n "${ACE_BRIEF_PY:-}" ] && read -r -a PYB <<<"$ACE_BRIEF_PY"
CHILD=""
source "$HOOKS/brief_lib.sh" || exit 1

BRIEF_DIR=$(b conf brief_dir) && M1=$(b conf brief_model) && M2=$(b conf brief_model_stage2) && BUDGET=$(b conf brief_max_budget_usd) || fail "config"
take_lock "$BRIEF_DIR"      # a live run holds it: this one exits 0 without a word
[ -x /usr/bin/perl ] || fail "no /usr/bin/perl"
limits
RUN="$BRIEF_DIR/run"
mkdir -p -m 700 "$RUN/cwd" || fail "cannot create the run directory"      # cwd: empty, no CLAUDE.md, the only directory claude sees
TOOLS=$(names "$HOOKS/brief_allowed_tools.txt") && DENIED=$(names "$HOOKS/brief_denied_tools.txt") &&
  [ -n "$TOOLS" ] && [ -n "$DENIED" ] || fail "empty or malformed tool allowlist or deny list"

purge() { rm -f "$RUN"/stage1.* "$RUN"/stage2.* "$RUN"/digest_raw.json "$RUN"/brief_raw.json "$RUN"/rl_*.json "$RUN"/res_*.json; }
purge      # raw mail that a killed run left behind goes first
cleanup() {   # the raw streams hold mail the exclusion list has not seen yet; a signal must not cut this short
  trap '' TERM INT HUP
  [ -n "${ACE_BRIEF_KEEP:-}" ] || purge
}
on_signal() {   # $1 the exit code; every child of this shell is stopped (one just started may not be in CHILD yet) and reaped, then cleanup runs
  trap '' TERM INT HUP      # first: a second signal must not cut the stop or the purge short
  [ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null
  pkill -TERM -P $$ 2>/dev/null
  wait 2>/dev/null
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
# kill it: `exec` makes the subshell pid the claude pid (the alarm prefix execs too).
( cd "$RUN/cwd" && exec "${ALARM[@]}" env -u ANTHROPIC_API_KEY -u CLAUDECODE "$CLAUDE_BIN" -p --model "$M1" --no-session-persistence \
    --permission-mode default --tools ToolSearch --allowedTools "$TOOLS" --disallowedTools "$DENIED" \
    --settings "$REPO/hooks/brief-settings.json" --output-format stream-json --verbose --max-turns 12 --max-budget-usd "$BUDGET" \
    "$(cat "$REPO/hooks/brief_prompts/stage1.txt")" ) < /dev/null > "$RUN/stage1.jsonl" 2> "$RUN/stage1.err" &
CHILD=$!; wait_stage stage1; rc=$?
ledger_stage stage1 "$RUN/stage1.jsonl"
[ "$rc" -ne 124 ] || fail "stage 1 timed out"
[ "$rc" -eq 0 ] || fail "stage 1 failed (exit $rc)"
py tools-audit "$RUN/stage1.jsonl" --allow "$HOOKS/brief_allowed_tools.txt" > "$RUN/res_audit.json"; rc=$?      # a tool the allowlist does not name
if [ "$rc" -ne 0 ]; then
  [ "$rc" -eq 2 ] || fail "tool audit failed"
  notify "brief: tool drift, run refused"
  echo "brief: tool drift, run refused" >&2; cat "$RUN/res_audit.json" >&2; exit 2      # the summary: counts and names of the checked-in lists
fi
py extract "$RUN/stage1.jsonl" --what text > "$RUN/digest_raw.json" || fail "stage 1 gave no result"
py digest-apply "$RUN/digest_raw.json" --out "$RUN/digest_clean.json" || fail "digest refused"

# stage 2: no tools, no MCP servers; sees only the cleaned digest
( cd "$RUN/cwd" && exec "${ALARM[@]}" env -u ANTHROPIC_API_KEY -u CLAUDECODE "$CLAUDE_BIN" -p --model "$M2" --no-session-persistence \
    --settings "$REPO/hooks/brief-settings.json" --tools "" --strict-mcp-config --output-format stream-json --verbose --max-turns 1 \
    --max-budget-usd "$BUDGET" --system-prompt "$(cat "$REPO/hooks/brief_prompts/stage2.txt")" ) \
    < "$RUN/digest_clean.json" > "$RUN/stage2.jsonl" 2> "$RUN/stage2.err" &
CHILD=$!; wait_stage stage2; rc=$?
ledger_stage stage2 "$RUN/stage2.jsonl"
[ "$rc" -ne 124 ] || fail "stage 2 timed out"
[ "$rc" -eq 0 ] || fail "stage 2 failed (exit $rc)"
py extract "$RUN/stage2.jsonl" --what text > "$RUN/brief_raw.json" || fail "stage 2 gave no result"
py render "$RUN/brief_raw.json" > /dev/null || fail "brief refused"
py state-mark > "$RUN/state_n.txt" || fail "state file"
read -r N < "$RUN/state_n.txt"; rm -f "$RUN/state_n.txt"
echo "brief: ok, $N need you"
[ "$N" -gt 0 ] && notify "$N need you" "file://$BRIEF_DIR/brief.html"
exit 0
