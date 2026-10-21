#!/bin/bash
# Brief on wake (docs/design/compiler.md 8.x "Brief (as built)"). Usage: brief.sh [--force]
# A READ-ONLY principal: it reads calendar, mail and issues through two `claude -p` stages and writes a local HTML page. Controls: (1)
# --allowedTools from hooks/brief_allowed_tools.txt, (2) the PreToolUse deny hook hooks/brief_guard.py (via --settings), (3) the settings
# deny list. Stage 1 (tools) emits a JSON digest; CODE applies the private exclusion list; stage 2 (no tools) writes the brief JSON from
# the cleaned digest alone; CODE renders the page. Everything gathered is data, never instructions. No model writes served HTML.
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
b() { "${PYB[@]}" -m ace_playbook.compile.brief "$@"; }

BRIEF_DIR=$(b conf brief_dir) && M1=$(b conf brief_model) && M2=$(b conf brief_model_stage2) && BUDGET=$(b conf brief_max_budget_usd) || fail "config"
RUN="$BRIEF_DIR/run"
mkdir -p -m 700 "$RUN/cwd" || fail "cannot create the run directory"      # cwd: empty, no CLAUDE.md, the only directory claude sees
TOOLS=$(grep -v '^[[:space:]]*#' "$REPO/hooks/brief_allowed_tools.txt" | tr -d ' \t\r' | grep -v '^$' | paste -sd, -)
[ -n "$TOOLS" ] || fail "empty tool allowlist"

# one run at a time (wake and unlock fire together); a lock older than 30 minutes is stale
find "$BRIEF_DIR" -maxdepth 1 -name run.lock -mmin +30 -exec rmdir {} + 2>/dev/null
mkdir "$BRIEF_DIR/run.lock" 2>/dev/null || exit 0
cleanup() {   # the raw streams hold mail the exclusion list has not seen yet: they do not outlive the run
  rmdir "$BRIEF_DIR/run.lock" 2>/dev/null
  [ -n "${ACE_BRIEF_KEEP:-}" ] || rm -f "$RUN"/stage1.* "$RUN"/stage2.* "$RUN"/digest_raw.json "$RUN"/brief_raw.json "$RUN"/rl_*.json "$RUN"/res_*.json
}
trap cleanup EXIT

FORCE=""
[ "${1:-}" = "--force" ] && FORCE="--force"
b due $FORCE; rc=$?
[ "$rc" -eq 3 ] && exit 0
[ "$rc" -eq 0 ] || fail "due check failed"

ledger_stage() {   # $1 stage name, $2 its stream: one ledger row when the stream holds a result
  b extract "$2" --what rate_limit > "$RUN/rl_first.json" 2>/dev/null
  b extract "$2" --what rate_limit --last > "$RUN/rl_last.json" 2>/dev/null
  b extract "$2" --what result > "$RUN/res_$1.json" 2>/dev/null || return 0
  b ledger "$RUN/rl_first.json" "$RUN/rl_last.json" "$1" "$RUN/res_$1.json" || echo "brief: ledger row failed for $1" >&2
}

# stage 1: tools on (read-only allowlist + deny hook + settings deny), reads, emits the digest JSON
rc=0
( cd "$RUN/cwd" && env -u ANTHROPIC_API_KEY -u CLAUDECODE "$CLAUDE_BIN" -p --model "$M1" --allowedTools "$TOOLS" \
    --settings "$REPO/hooks/brief-settings.json" --output-format stream-json --verbose --max-turns 12 --max-budget-usd "$BUDGET" \
    "$(cat "$REPO/hooks/brief_prompts/stage1.txt")" ) > "$RUN/stage1.jsonl" 2> "$RUN/stage1.err" || rc=$?
ledger_stage stage1 "$RUN/stage1.jsonl"
[ "$rc" -eq 0 ] || fail "stage 1 failed (exit $rc)"
b extract "$RUN/stage1.jsonl" --what text > "$RUN/digest_raw.json" || fail "stage 1 gave no result"
b digest-apply "$RUN/digest_raw.json" --out "$RUN/digest_clean.json" || fail "digest refused"

# stage 2: no tools, no MCP servers; sees only the cleaned digest
rc=0
( cd "$RUN/cwd" && env -u ANTHROPIC_API_KEY -u CLAUDECODE "$CLAUDE_BIN" -p --model "$M2" --tools "" --strict-mcp-config \
    --output-format stream-json --verbose --max-turns 1 --max-budget-usd "$BUDGET" \
    --system-prompt "$(cat "$REPO/hooks/brief_prompts/stage2.txt")" < "$RUN/digest_clean.json" ) > "$RUN/stage2.jsonl" 2> "$RUN/stage2.err" || rc=$?
ledger_stage stage2 "$RUN/stage2.jsonl"
[ "$rc" -eq 0 ] || fail "stage 2 failed (exit $rc)"
b extract "$RUN/stage2.jsonl" --what text > "$RUN/brief_raw.json" || fail "stage 2 gave no result"
b render "$RUN/brief_raw.json" > /dev/null || fail "brief refused"
N=$(b state-mark) || fail "state file"
echo "brief: ok, $N need you"
if [ "$N" -gt 0 ] && command -v terminal-notifier > /dev/null 2>&1; then
  terminal-notifier -title "ACE brief" -message "$N need you" -open "file://$BRIEF_DIR/brief.html" > /dev/null 2>&1 || true
fi
exit 0
