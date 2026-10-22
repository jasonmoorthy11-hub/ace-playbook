# Helpers of hooks/brief.sh, sourced by it (functions only; docs/design/compiler.md 8.x "Brief (as built)"). The caller sets PYB (the python
# command), RUN (brief_dir/run) and CHILD (the pid of the tracked background step, "" when none) before it calls these.
fail() { echo "brief: $1" >&2; exit 1; }
b() { "${PYB[@]}" -m ace_playbook.compile.brief "$@"; }      # only for `conf`, before the lock and the traps exist
run_bg() {   # every long step runs in the background and is waited for: a foreground child would defer a signal's trap until it ended
  "$@" &
  CHILD=$!; wait "$CHILD"; local rc=$?; CHILD=""; return "$rc"
}
py() { run_bg "${PYB[@]}" -m ace_playbook.compile.brief "$@"; }
names() {   # $1 a list file -> `a,b,c`. A line is blank, a full-line `#` comment or ONE name of [A-Za-z0-9_][A-Za-z0-9_.-]*; a BOM, a leading `-`, a
  # `#` after a name, a space inside, a NUL anywhere or any other byte fails (rc 1), exactly as hooks/brief_guard.py reads the file.
  # LC_ALL=C: only ASCII blanks are blanks.
  iconv -f UTF-8 -t UTF-8 "$1" > /dev/null 2>&1 || return 1
  LC_ALL=C tr -d '\000' < "$1" | cmp -s - "$1" || return 1      # grep and awk stop at a NUL byte: refuse it
  if LC_ALL=C grep -a -q -v -E '^[[:space:]]*(#.*)?$|^[[:space:]]*[A-Za-z0-9_][A-Za-z0-9_.-]*[[:space:]]*$' "$1"; then return 1; fi
  LC_ALL=C grep -a -v -E '^[[:space:]]*(#|$)' "$1" | LC_ALL=C tr -d ' \t\r\013\014' | paste -sd, -
}
take_lock() {   # $1 brief_dir. One run at a time: a kernel flock(2) (taken with python's stdlib fcntl on the inherited fd 9: the lockf(1) and
  # flock(1) commands are each platform-specific, the syscall is not) lives on the open file description, so it is held by every process that
  # inherits fd 9 and released by the kernel when the last one dies, a SIGKILL included: no stale lock and no takeover; a live run is always
  # respected (exit 0). Only "somebody holds it" (EWOULDBLOCK) is that quiet exit; any other error of the lock step fails the run.
  mkdir -p -m 700 "$1" || fail "cannot create the brief directory"
  exec 9>> "$1/run.lockf" || fail "cannot open the lock file"
  "${ACE_PYTHON:-python3}" -I -c 'import fcntl,sys
try: fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError: sys.exit(75)' 2> /dev/null; local rc=$?      # stderr off: a failure is ONE line, `brief: lock`, never a traceback
  [ "$rc" -eq 0 ] && return 0
  [ "$rc" -eq 75 ] && exit 0
  fail "lock"
}
limits() {   # sets STAGE_T (the wall-clock limit of a stage) and GRACE (the wait between TERM and KILL), integers >= 1 in seconds, and ALARM
  # (a prefix that makes a stage die on its own alarm even when this script is gone: the lock fd 9 is inherited by the stage)
  STAGE_T="${ACE_BRIEF_STAGE_TIMEOUT:-600}" GRACE="${ACE_BRIEF_KILL_GRACE:-10}"
  [[ "$STAGE_T" =~ ^[0-9]{1,6}$ ]] && STAGE_T=$((10#$STAGE_T)) && [ "$STAGE_T" -ge 1 ] || STAGE_T=600
  [[ "$GRACE" =~ ^[0-9]{1,6}$ ]] && GRACE=$((10#$GRACE)) && [ "$GRACE" -ge 1 ] || GRACE=10
  ALARM=(/usr/bin/perl -e '$SIG{ALRM} = "DEFAULT"; alarm shift; exec @ARGV' "$((STAGE_T + GRACE))")      # a launcher may have left SIGALRM ignored
}
watch() {   # $1 pid, $2 seconds, $3 grace: TERM the pid when the time is up (and leave $RUN/timed_out), KILL it when it outlives the grace
  local i
  for ((i = 0; i < $2; i++)); do kill -0 "$1" 2> /dev/null || return 0; sleep 1; done
  kill -TERM "$1" 2> /dev/null || return 0
  : > "$RUN/timed_out"
  for ((i = 0; i < $3; i++)); do kill -0 "$1" 2> /dev/null || return 0; sleep 1; done
  kill -KILL "$1" 2> /dev/null
}
wait_stage() {   # $1 stage name: wait for the background step in CHILD under the wall-clock watchdog; rc 124 and a ledger row on a timeout
  local rc wd
  rm -f "$RUN/timed_out"
  watch "$CHILD" "$STAGE_T" "$GRACE" 9>&- & wd=$!       # fd 9 closed: a lingering sleep must not hold the run lock
  wait "$CHILD" 2> /dev/null; rc=$?                    # bash reports a KILL or an alarm of the child on stderr: the exit code says it
  kill "$wd" 2> /dev/null; wait "$wd" 2> /dev/null
  CHILD=""
  [ -e "$RUN/timed_out" ] || return "$rc"
  rm -f "$RUN/timed_out"; py timeout-mark "$1" || true; return 124
}
notify() {   # $1 message, $2 optional page to open. $ACE_BRIEF_NOTIFIER names the notifier
  # (tests inject a fake; empty means unset, and the default terminal-notifier comes from the PATH)
  local n="${ACE_BRIEF_NOTIFIER:-terminal-notifier}"
  command -v "$n" > /dev/null 2>&1 || return 0
  "$n" -title "ACE brief" -message "$1" ${2:+-open "$2"} > /dev/null 2>&1 9>&- || true
}
