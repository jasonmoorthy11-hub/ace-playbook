# Installing the hooks (manual, nothing is installed for you)

This repository never writes to your Claude Code configuration directory: no installer,
no symlinks, no edits. You wire the hooks in yourself, after reading this page and the
scripts they point to. All of them fail open (exit 0 on any error) and never print secrets.

Requirements: `python3` 3.12 or newer on the hook's `PATH` (override with `ACE_PYTHON`),
and a checkout of this repo. No Python packages are needed at runtime.

## 1. Choose paths

```sh
export ACE_PLAYBOOK_HOME="$HOME/src/ace-playbook"        # your checkout
mkdir -p "$HOME/.config/ace-playbook"
cat > "$HOME/.config/ace-playbook/config.json" <<'JSON'
{
  "data_dir": "~/.local/share/ace-playbook",
  "transcripts_root": "~/.claude/projects",
  "api_key_path": "~/.config/ace-playbook/anthropic_api_key",
  "engagements_path": "~/.config/ace-playbook/engagements.json"
}
JSON
```

`transcripts_root` is only read (by the nightly sweep). `api_key_path` is a file holding a
key for a dedicated, spend-capped API workspace; it is read by the reflector and never
logged. Without it the reflector skips and says so in `reflect.log`. All other config keys
are in `ace_playbook/config.py`.

Make the scripts executable if your checkout lost the bit: `chmod +x "$ACE_PLAYBOOK_HOME"/hooks/*.sh`.

## 2. Add these entries to your user-level `settings.json`

Merge into the existing `"hooks"` object; do not replace it. Put them in user settings,
not project settings: a SessionEnd hook defined by a plugin cannot raise its timeout, and
project-scoped hooks run for every clone you open.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact|fork",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/src/ace-playbook/hooks/sessionstart.sh",
            "timeout": 10,
            "onFailure": "continue"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "clear",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/src/ace-playbook/hooks/sessionend.sh",
            "timeout": 5,
            "onFailure": "continue"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/src/ace-playbook/hooks/stop_citation_ledger.sh",
            "timeout": 5,
            "onFailure": "continue"
          }
        ]
      }
    ]
  }
}
```

Why these values:

- **SessionStart** re-runs on `compact` as well as `startup`, `resume` and `clear`, because the
  injected block is part of the context that compaction rewrites. The block is at most 7,000
  characters; the hook output cap is 10,000. `timeout: 10` is generous: the hook reads a local
  SQLite file and a JSONL ledger.
- **SessionEnd** has a default budget of 1.5 seconds, which a per-hook `timeout` raises (up to
  60 s) only for hooks defined in user settings. `sessionend.sh` does no cleaning, scrubbing or
  API work: it appends one line to `queue.jsonl` and exits. The matcher `clear` is the seam
  where a rollforward-and-clear workflow ends a session; add `other` or `logout` if you also
  want those reflected. The nightly sweep catches anything the trigger missed.
- **Stop** receives `last_assistant_message`, so citations are read from the hook payload rather
  than from the transcript file, which can lag behind the live conversation.
- **onFailure.** `"continue"` is deliberate for all three: a broken lesson store should never
  block a session (stage-1 hooks never block; stage-2 shadow hooks block only a proposal the
  operator promoted to block mode after the observe/warn ladder, see docs/design/compiler.md).
  `"block"` makes a crashed, missing or timed-out hook stop the event, which is right for a
  security gate but wrong for context injection. `onFailure` has no effect on `Stop` or
  background hooks, and SessionEnd has no decision control, so the field there is documentation
  of intent.

Optional but recommended: a `ConfigChange` hook of your own that logs edits to settings, hooks
and skills, so any change to the hooks above is visible. This repo does not ship one.

## 3. Check it by hand before trusting it

```sh
cd "$ACE_PLAYBOOK_HOME"
export ACE_PLAYBOOK_CONFIG="$HOME/.config/ace-playbook/config.json"

echo '{"session_id":"test-1","cwd":"'"$PWD"'","source":"startup"}' | hooks/sessionstart.sh
echo '{"session_id":"test-1","transcript_path":"/tmp/none.jsonl","reason":"clear"}' | hooks/sessionend.sh
echo '{"session_id":"test-1","last_assistant_message":"used [L-00000000]"}' | hooks/stop_citation_ledger.sh
python3 hooks/nightly_sweep.py --dry-run
```

With an empty store, `sessionstart.sh` prints nothing. Add a lesson with a pinned status (see
`python -m ace_playbook.store --help`) and it prints a JSON object whose `additionalContext` is
the advisory block. In stage 1 only pinned lessons are injected.

## 4. Nightly sweep (launchd)

```sh
cp hooks/com.example.ace-playbook-nightly.plist "$HOME/Library/LaunchAgents/com.example.ace-playbook-nightly.plist"
# edit the file: replace __ACE_PLAYBOOK_HOME__, __ACE_CONFIG_FILE__, __ACE_DATA_DIR__ with absolute
# paths (launchd does not expand ~ or $HOME) and rename the Label to something you own
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.ace-playbook-nightly.plist"
launchctl print "gui/$(id -u)/com.example.ace-playbook-nightly" | head
```

The sweep reflects up to 20 transcripts per run that are idle for an hour, larger than 2 KB, not
in a subagent folder, and have no entry in `reflections.jsonl`. Each reflection is one Haiku 5.5
call; usage is written to the ledger and the log. Remove the job with
`launchctl bootout "gui/$(id -u)/com.example.ace-playbook-nightly"`.

Each non-dry run writes `health.json` (0600) to the data directory: last run time, `ok`,
`consecutive_failures` and a scrubbed `last_error`. A night counts as failed on an exception, an
API error (including a spend or usage cap), a missing key or a delta log that fails verification.
Check it with `PYTHONPATH="$ACE_PLAYBOOK_HOME" python3 -m ace_playbook.inject --health` (prints
`no health record` before the first run). After 3 failures in a row the SessionStart block adds
one line telling you to read the reflect log (`reflect.log`) in the data dir. If the launchd job
is set up with `--dry-run`, no health record is written and `--health` prints `no health record`;
that is expected and is not a sign of health.

## 5. Optional: git pre-commit scrub

To scan staged files in a repository before every commit (this touches only that repo's
`.git/hooks`, never your Claude Code configuration):

```sh
ln -s "$ACE_PLAYBOOK_HOME/hooks/pre-commit-scrub.sh" .git/hooks/pre-commit
```

It runs `python -m ace_playbook.scrub check --mode pre-commit --staged` and fails closed. The
same check runs in this repo's CI.

## 6. Uninstall

Delete the three hook entries from `settings.json` and boot out the launchd job. Your data
directory is plain files (`deltas.jsonl` is the source of truth); delete or keep it.

## 7. Stage 2 shadow hook (observe, warn, block)

Optional, and separate from the three hooks above. `hooks/shadow.sh` evaluates the proposals you
approved (see `docs/design/compiler.md`, section 7) against each Bash call and records what **would**
have fired. In observe mode it prints nothing, changes nothing and never blocks: the wrapper does not
`exec` Python and always exits 0 (exit 2 would mean "block" to Claude Code), and Python runs isolated
(`-I`, plus `-P` where it exists) from `$ACE_PLAYBOOK_HOME`, so a `json.py` in the project you are
working in is never imported. It works on the system Python 3.9 too (no `-P` there).

Approve a proposal first, by hand at a terminal (nothing else can do it):

```sh
cd "$ACE_PLAYBOOK_HOME"
python3 -m ace_playbook.compile propose --lesson L-xxxxxxxx --detector rm_rf_nonscratch
python3 -m ace_playbook.compile approve P-xxxxxxxx --mode observe    # lists provenance, asks y/N on /dev/tty
# (a lesson whose evidence is migrated legacy text prints a banner and asks a second y/N)
```

A lesson whose evidence is migrated legacy text first needs
`python3 -m ace_playbook.compile label --incident --lesson L-xxxxxxxx --session <id>`, which shows the incident
and asks y/N on /dev/tty; only then does `propose` have a card for it.

Then add these two entries to
the same `"hooks"` object of your user-level `settings.json` (adjust the path to your checkout):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/ace-playbook/hooks/shadow.sh pre",
            "timeout": 5,
            "onFailure": "continue"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/ace-playbook/hooks/shadow.sh stop",
            "timeout": 5,
            "onFailure": "continue"
          }
        ]
      }
    ]
  }
}
```

The `Stop` entry goes next to the existing citation hook. In this step it does nothing yet: no approvable
proposal names a `Stop` event, so the script finds nothing to evaluate and exits. Register it only if you
want the wiring in place for later.

What it writes, all under `data_dir` and mode 0600: `shadow.jsonl` (append-only, one row per fire:
`ts`, `proposal_id`, `lesson_id`, `mode`, `event`, `tool_name`, `session` (a 16-character hash),
`payload_sha256`, `would_fire`, `cause` (`detector` or `truncated`), `tier`; never the command text), `approvals.jsonl` (hash-chained, one row per approval), `shadow.log` (one type-only line per
skipped proposal or error, capped at 1 MB), `shadow_state/` (per-session detector state, hashed file
names) and `proposals/approved/` (your approved copies). Every call re-validates each approved proposal
from disk; a missing, edited, oversize or symlinked file is skipped and its id is logged.

**Warn and block.** A proposal reaches `warn` only through `python3 -m ace_playbook.compile approve P-xxxxxxxx --mode warn`, at
a terminal, after at least 14 days in observe with at least 5 true positives you labelled yourself (`compile label --tp`)
and no false positive; the command lists every counted fire and label, checks the rules again on current code and asks
`y/N`. In warn mode the hook still records the fire and prints the stored message on stderr (a debug-log echo), and it also
prints one `hookSpecificOutput.additionalContext` JSON object on stdout, which is the only channel the model sees on exit 0
(it arrives as a system reminder next to the tool result). `--mode block --opus-reviewed <sha>` repeats the rules in warn
for strong detectors that allow it and makes the hook answer `permissionDecision: "deny"`, which stops the tool call; a
block wins over any warn in the same call. Every approval is a row in the hash-chained `approvals.jsonl`; the hook runs a
`warn` or `block` copy at that mode only while the newest row for it carries the same hash and mode. A copy edited by hand
(or with no row, or with a broken `approvals.jsonl`) falls back to observe and logs `approvals:<id>:missing`, `:mismatch` or
`approvals:chain_broken` in `shadow.log`; a hand edit of the copy alone never enforces. If `approve --mode warn` reports a `forged label` (a label with no fire row in `shadow.jsonl`), restore the missing shadow row or truncate `labels.jsonl` to the row before the forged one and re-label; if it reports `copy not backed by the approvals chain`, run `approve --mode observe` to record the copy.

Two things to know. The matcher is `Bash` only, narrower than the design's list (no approvable proposal
looks at Read, Edit or Write yet, and every extra matcher is another Python start per call). And Python
writes bytecode into `__pycache__` folders inside `$ACE_PLAYBOOK_HOME` the first time it imports the
package (`-I` does not stop that); set `PYTHONDONTWRITEBYTECODE=1` in the hook's environment if you want
the checkout left untouched.

Check it by hand: `echo '{"session_id":"t1","tool_name":"Bash","tool_input":{"command":"rm -rf /w/src"}}'
| hooks/shadow.sh pre` prints nothing; `shadow.jsonl` gains a row only if an approved proposal matches.

**Remove it.** Delete the two entries from `settings.json`. To stop evaluation without touching settings,
create `data_dir/proposals/DISABLED` (any content) or run `python3 -m ace_playbook.compile unapprove
P-xxxxxxxx` at a terminal. `shadow.jsonl`, `shadow.log` and `shadow_state/` are plain files you can delete.

## What the hooks write

All under `data_dir`: `deltas.jsonl` (+ `.lock`), `playbook.sqlite` (disposable view),
`injections.jsonl`, `citations.jsonl`, `reflections.jsonl`, `queue.jsonl`, `quarantine.jsonl`,
`health.json`, `reflect.log`; with the shadow hook also `shadow.jsonl`, `shadow.log` and `shadow_state/`.
None of them should ever be committed to a public repository; keep `data_dir` outside any checkout (the
repo's `.gitignore` lists the stage 1 files only).
