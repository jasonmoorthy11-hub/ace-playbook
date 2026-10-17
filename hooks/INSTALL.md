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
        "matcher": "startup|resume|clear|compact",
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
  block a session. `"block"` makes a crashed, missing or timed-out hook stop the event, which is
  right for a security gate but wrong for context injection. `onFailure` has no effect on `Stop`
  or background hooks, and SessionEnd has no decision control, so the field there is
  documentation of intent.

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

## 5. Uninstall

Delete the three hook entries from `settings.json` and boot out the launchd job. Your data
directory is plain files (`deltas.jsonl` is the source of truth); delete or keep it.

## What the hooks write

All under `data_dir`: `deltas.jsonl` (+ `.lock`), `playbook.sqlite` (disposable view),
`injections.jsonl`, `citations.jsonl`, `reflections.jsonl`, `queue.jsonl`, `quarantine.jsonl`,
`reflect.log`. None of them should ever be committed to a public repository; the repo's
`.gitignore` already excludes them.
