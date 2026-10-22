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
python3 hooks/nightly_sweep.py --dry-run --offline-dry-run
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

The nightly job also runs the offline compiler pass (`compile offline`: detector events, label pre-fill, demotions) before the
reflection step; it makes no API call, so `--dry-run` only affects the reflection step (use `--offline-dry-run` to make the pass
write nothing, or `--no-offline` to skip it).

**Upgrade note.** A nightly job already started with `--dry-run` now also writes offline data (`detector_events.jsonl`, auto
labels, demotions, evasion candidates, `shadow_state` cleanup) and still makes no API call. Add `--offline-dry-run` to keep
the whole job read-only. When the offline pass raises or reports errors, `nightly_sweep.py` records a health failure (see
`--health` below) even under `--dry-run`, because that pass is not a dry run unless `--offline-dry-run` is given.

The sweep reflects up to 20 transcripts per run that are idle for an hour, larger than 2 KB, not
in a subagent folder, and have no entry in `reflections.jsonl`. Each reflection is one Haiku 5.5
call; usage is written to the ledger and the log. Remove the job with
`launchctl bootout "gui/$(id -u)/com.example.ace-playbook-nightly"`.

Each run without `--dry-run`, and each `--dry-run` night whose offline pass is real, writes
`health.json` (0600) to the data directory: last run time, `ok`, `consecutive_failures` and a
scrubbed `last_error`. A night counts as failed on an exception, an API error (including a spend
or usage cap), a missing key or a delta log that fails verification. Under `--dry-run` the record
reflects the offline pass and the script itself only: a real, clean offline pass records `ok` (the
nightly ran), a failing pass or a crash records the failure, and reflection-side failures (key,
cap, API) are not seen; `--dry-run` together with `--offline-dry-run` or `--no-offline` leaves the
record untouched. Check it with
`PYTHONPATH="$ACE_PLAYBOOK_HOME" python3 -m ace_playbook.inject --health` (prints
`no health record` before the first run). After 3 failures in a row the SessionStart block adds
one line telling you to read the reflect log (`reflect.log`) in the data dir.

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

To label shadow fires, run `python3 -m ace_playbook.compile label --queue [--limit N]` at a terminal: one blind card per unlabelled fire, oldest first
(`--all` also offers the fires of retired proposals).

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
`approvals:chain_broken` in `shadow.log`; a hand edit of the copy alone never enforces. A demotion (a row in `demotions.jsonl` from the nightly pass) lowers the proposal until you approve it again at the terminal: `approve --mode observe` lifts every older row and restarts the ladder, and a revoked proposal is skipped (`skipped:P-xxxxxxxx:revoked`). Row numbers in the chain messages (`approvals.jsonl`, `demotions.jsonl`, `labels.jsonl`) count from 1. If `approve --mode warn` reports a `forged label` (a label with no fire row in `shadow.jsonl`), restore the missing shadow row or truncate `labels.jsonl` to the row before the forged one and re-label; if it reports `copy not backed by the approvals chain`, run `approve --mode observe` to record the copy.

**Chain key (optional, recommended before warn or block).** `approvals.jsonl` and `demotions.jsonl` are plain hash chains until you create a
key. At a terminal, with `ACE_PLAYBOOK_KEYED` NOT set in that shell, run `uv run python -m ace_playbook.compile keygen`. It runs on a
never-keyed install only (no key file, no `chain_heads.json`, no flag): it lists every row of `approvals.jsonl` and `demotions.jsonl` it will
re-seal, asks y/N (it holds both chain locks from the one read of the files to the last replace, so a file swapped while the prompt is open
cannot change what is sealed), then writes 32 random bytes as 64 hex characters to the file `chain_key_path` names in `config.json`. There is no default key path (a
config that names no key reads none): if the line is missing, keygen proposes `chain_key` beside the config file, says so in the prompt
and, on y, writes `"chain_key_path": "~/..."` into `config.json` itself first (atomic, 0600, every other key kept), so you add no line by
hand; an install whose config already names the key is unchanged, `--rehead` never writes the config, and an explicit `"chain_key_path": null`
is left alone (keygen refuses). The key file is mode 0600; if the directory is missing it is created 0700, an existing directory keeps its mode, so make it private yourself; it never
overwrites a key and prints only the path). Next to the key it keeps byte-for-byte copies of both chains as they were
(`approvals.jsonl.bak`, `demotions.jsonl.bak`, 0600): keep them, they are your record of the pre-keygen state. READ EVERY `warn` AND `block`
ROW in the listing against your own record of what you approved before answering y: a row you did not approve is sealed like any other (this is
the one residual of deleting both the key and the heads file, then writing rows). A line "row N onward does not verify and will be kept as is"
means that tail stays byte for byte after the sealed rows. It then re-seals both chains in place:
every row becomes a `v: 2` row hashed with HMAC-SHA256 under the key (same content and order, `prev_hash` relinked; a tail that does not verify is
kept as it is and named before the prompt), and it writes `chain_heads.json` beside the key, the signed last hash of each chain. If keygen fails it puts
the chains back and removes the key and the copies, so you can run it again. From then on only `v: 2` rows verify, every `approve`, `unapprove`,
ladder step and nightly demotion appends a `v: 2` row and moves its head, and a `v: 1` row (a chain rewritten as plain hashes) is a broken chain. An install counts as KEYED when the key file or
`chain_heads.json` exists (any file type), a chain holds a `v: 2` row, or the environment variable `ACE_PLAYBOOK_KEYED=1` is set (`keygen` refuses on all of these); a keyed install without a usable key
(deleted, mode other than owner-only, wrong owner) has both chains broken: the hook runs observe, logs `chain_key:missing` once, and `approve`,
`unapprove`, the ladder and the nightly pass refuse to write and print the restore message (restore the key file from your backup, or move
the unusable key file away, delete `approvals.jsonl`, `demotions.jsonl` and `chain_heads.json`, unset the flag in that shell and run `keygen`
again, re-making every approval); `unapprove` still removes the approved copy. A config with no `chain_key_path` line names no key, so with
the flag set, a `chain_heads.json` beside that config file, or a `v: 2` row in `approvals.jsonl` or `demotions.jsonl`, both chains are broken
(`chain_key:missing`), never a silent v1 rule. **Upgrading:** add `"chain_key_path": "~/.config/ace-playbook/chain_key"` (or wherever your key
is) to `config.json` BEFORE you take the first release that requires `chain_key_path` (installs from before 2026-10-21 relied on a default key path); a keyed install
without that line fails closed (observe, `chain_key:missing`, writers refuse) until you add it. A
cut or rolled-back chain is `approvals:head_mismatch` or `demotions:head_mismatch`; a missing or forged heads file breaks both chains
(`chain_heads:missing`). Back the key file up with your other secrets: losing it breaks both chains. On a keyed install `keygen` refuses
(there is no resume and no overwrite), because re-sealing plain v1 rows there would sign a forgery under a new key.

**The flag (recommended second marker).** Set `ACE_PLAYBOOK_KEYED=1` for the shadow hook so that removing the key file and the heads file
does not turn the install back into an unkeyed one. Claude Code runs a hook `command` through a shell (the entries above already rely on
`$HOME` expanding), so the portable form is an assignment in front of the command in your user-level `settings.json`:

```json
{ "type": "command", "command": "ACE_PLAYBOOK_KEYED=1 $HOME/ace-playbook/hooks/shadow.sh pre", "timeout": 5, "onFailure": "continue" }
```

(use the same prefix on the `stop` entry; `hooks/shadow.sh` passes its environment through unchanged, and a test checks that). Also export it
in your own shell profile (`export ACE_PLAYBOOK_KEYED=1`) once `keygen` has run, so `approve`, `unapprove` and the ladder fail safe if the key
is moved away, and the example launchd plist in section 4 carries it commented out: uncomment it after keygen has run. Set it only AFTER the first `keygen` (keygen refuses
while it is set, and until a key exists the nightly pass would report an error for its demotion stage). A config with `"chain_key_path": null`
means no key, which is only safe on a never-keyed install: with the flag set, a `chain_heads.json` beside the config or a `v: 2` row in a
chain, both chains are broken (`key_missing`, fail closed), so keep the path set. The settings file
sits behind a stronger consent gate than the data dir, but `shadow.sh` and the `ace_playbook` package live in a checkout the agent can write, so
the flag raises the bar and the permission layer below remains the real boundary.

The key is readable by your user, so by the agent too. A keyed chain proves that its writer read, replaced or redirected the key (the key file,
its directory, or `chain_key_path` in `config.json`), so put all of `~/.config/ace-playbook` out of the agent's reach with deny rules in your
user-level `settings.json`. The form is the home-relative one from the Claude Code permissions reference (`~/path`; the absolute form is a
`//` prefix followed by the full path from the filesystem root and spells out your username, the single-slash form is relative to the settings
file):

```json
{ "permissions": { "deny": [
  "Read(~/.config/ace-playbook/**)",
  "Read(~/.config/ace-playbook)",
  "Edit(~/.config/ace-playbook/**)"
] } }
```

There is no `Write(...)` rule: the permissions reference checks file rules against Read and Edit only, a `Write(...)` rule is never consulted
(and Claude Code warns about it at every start), and a Read deny already blocks Edit, Write and file creation on the path (Claude Code 2.1.228
and later). The bare-directory rule covers the directory itself (listings, `@`-mentions). These rules stop the Read, Edit and Write tools
(and, per the permissions reference, a symlink that points into the directory) and the recognised file commands in Bash (`cat`, `head`, and
`<`/`>` redirects). They do not stop every
shell command. If you run a guard hook that matches substrings of command text, it may refuse a command that names the key but it does not catch
`cd ~/.config/ace-playbook; mv chain_key x`; add Bash deny rules of your own if you want that closed, and edit `settings.json` with an editor
rather than a shell one-liner. What this setup still does not close: a same-user process outside the permission layer, deleting both the key
file and the heads file without the flag set (the install falls back to the unkeyed v1-only rule), and restoring an old chain together with its
old heads file. Heads do close a cut tail and a rollback of the chain alone. With a key, deleting `approvals.jsonl` or `demotions.jsonl`
disables rather than promotes; before `keygen`, deleting `demotions.jsonl` lifts its caps.

*Repair, interrupted keygen.* If the key is present but `chain_heads.json` is not, `keygen` refuses to run again and says which case you are in.
(1) Both chains verify under the key (the heads file was deleted or lost): run `uv run python -m ace_playbook.compile keygen --rehead`, which lists
what changed and asks. Do NOT restore from the `.bak` files then: that would silently roll back every revocation and demotion made since keygen.
(2) A chain is still plain v1 (keygen was killed between the two replaces): delete the key, restore both chains from the `.bak` files in the key
directory, delete the `.bak` files, and run keygen again.

*Repair.* A broken chain (`approvals.jsonl chain broken at row N`) is repaired by hand: truncate the file to its last complete line before the
bad row. After a truncation you authorised, after a crash between an append and its head write (`head_mismatch`), or after restoring a chain
from a backup, run `uv run python -m ace_playbook.compile keygen --rehead` at a terminal: it needs the existing key, verifies both chains with it
(it refuses while one is still broken), holds the chain locks, and for each chain prints what differs from the OLD heads: `old head not found:
rows were removed or replaced after row N` (with the rows the chain now ends with), or the rows added after the old head (normal after an
interrupted append), or that the old heads file is missing (removed rows cannot be listed: compare with a backup before answering y). Inspect the listed rows before answering y: it asks on the terminal, there is no
`--yes`, and only then does it write the heads and print their two hashes. A row that someone cut is not listed (it is gone); the message
that your old head is no longer in the chain is the signal. Do not hand-write rows.

After a detector change, re-propose and re-approve that detector's proposals only: an edit inside one detector's own `fire` body retires only that detector's proposals, while any other edit to `detectors.py` or `catalogue.py` (any row's registry fields, a shared helper, an import, a decorator or signature, `evaluate`, `advance`, a new detector) or to `events.py`, `shell.py` or `textrules.py` retires every approved proposal (see LIMITATIONS.md). One-time migration: the release that introduced the per-detector hash made every proposal written before it stale at once (`compile validate` says "catalogue source_sha256 is stale", the hook logs `skipped:P-xxxxxxxx:invalid:schema`, and the nightly pass records a `source_sha256` demotion), so re-propose and re-approve each live proposal once.

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

**Regression check.** `python3 -m ace_playbook.compile replay-synthetic` feeds the frozen invented corpus through the evaluator
(a throwaway state directory), writes `data_dir/shadow.synthetic.jsonl` (never `shadow.jsonl`; `--out` inside `data_dir` is refused unless it is that file) and exits 1 if any expected outcome
differs; `compile report` then prints the synthetic table apart from the private one.

**Seal preview.** `python3 -m ace_playbook.compile seal --dry-run` prints the pre-registration's Frozen instruments block (detector hashes, report hash, approved proposals, measurement start) and writes nothing; without `--dry-run` it needs a terminal, refuses until the `shadow_seed`/`arm_rate` config fields exist (the executed seal, next version) and until N is filled by hand in the pre-registration, and only prints the git tag commands, it never runs git.

**Status.** `python3 -m ace_playbook.compile status` prints one screen (measurement day, per proposal fires over eligible sessions, tp/fp/unlabelled,
precision, a verdict, the label queue, heartbeat ages, hook cost against 200 ms, notes) and writes `data_dir/status.json` (0600; `--out PATH` elsewhere,
`--json` prints it). It reads only; the `heartbeat` block shows when the hook, the offline scan and the nightly run last worked.
`python3 -m ace_playbook.compile digest [--since ISO]` prints the same data as six blocks. `hooks/nightly_sweep.py` runs it quietly after the
sweep (`digest` in its JSON report), refreshes `status.json` and appends one line `{ts, report_sha256, queue, notes}` to `data_dir/digest.log`
(0600); in an offline dry run it writes neither. Check `digest.log` for a line per night: a missing night means the sweep did not run.
`python3 -m ace_playbook.compile dashboard [--days N] [--out PATH]` renders the same data as one self-contained HTML page (`data_dir/dashboard.html`,
0600; inline CSS, no script, no network) that you open in a browser.

**Status line.** `hooks/statusline.sh` is a Claude Code status-line command. Add it to your user-level `settings.json` (adjust the path to your checkout):

```json
{"statusLine": {"type": "command", "command": "$HOME/ace-playbook/hooks/statusline.sh", "refreshInterval": 30}}
```

It prints one line, `<model> | ctx <pct>% | ace approved N  unlab24h N  unlabelled N  chains ok|BROKEN  heartbeat <age>`, from `status.json`
in the data dir (the config is found as for the other hooks: `ACE_PLAYBOOK_CONFIG`, else the default config file; `ACE_PYTHON` picks the interpreter).
`approved` counts proposals in observe, warn or block mode. `unlab24h` counts the unlabelled queue rows stamped within 24 hours
of `generated_at` (`N+` when the queue, capped at 50 oldest first, is shorter than `unlabelled`). `unlabelled` is `queue_total`. `chains` is `ok`
only when the approvals, demotions and labels chains all are. `heartbeat` is the age of the last hook row. Without a readable `status.json` it prints
`<model> | ctx <pct>% | ace: no status file`. It runs one `python3 -I -S` (stdlib only, about 15 ms) and exits 0 on any error.
The numbers are as fresh as `status.json`, which `compile status` and the nightly digest rewrite.

## 8. Brief on wake (optional, read-only)

`hooks/brief.sh` writes one local page, `brief.html`, from your calendar, mail and Linear issues each time the Mac wakes or unlocks (at
most once per `brief_dedupe_hours`). It is a read-only principal: two `claude -p` stages, no write tool, no send, no outbound channel.
Stage 1 has the tools of `hooks/brief_allowed_tools.txt` and emits a JSON digest; code removes the threads on your private exclusion list;
stage 2 has no tools and writes the brief JSON from the cleaned digest; code (not a model) renders the page with every field escaped.
Run it by hand first: `hooks/brief.sh --force` (it needs `uv` and a logged-in `claude` on the PATH; it never sees `ANTHROPIC_API_KEY`).

**Config keys** (all optional, in the same config file as the rest):

| Key | Default | Meaning |
|---|---|---|
| `brief_dir` | `data_dir/brief` | where the page, JSON, ledger and state live (created 0700; files 0600) |
| `brief_exclusions` | none | path of the private exclusions file below |
| `brief_model` / `brief_model_stage2` | `claude-sonnet-5-5` | the explicit model of each stage |
| `brief_dedupe_hours` | 4 | a wake inside this window of the last brief does nothing |
| `brief_max_budget_usd` | 1.0 | `--max-budget-usd` of each stage |

**Exclusions file** (keep it out of any checkout; regexes, case-insensitive, matched by code on the digest, never by the model):

```json
{"drop_senders": ["@example\\.org$"], "drop_subjects": ["^private"], "drop_bodies_from": ["billing@"]}
```

A thread whose sender matches `drop_senders` or whose subject matches `drop_subjects` is removed from the digest; one whose sender
matches `drop_bodies_from` stays as sender and subject only (its summary is emptied). A bad regex, a pattern longer than 200 characters,
a pattern with a quantified group that itself holds a quantifier (`(a+)+`, refused as a catastrophic-backtracking heuristic, not a proof)
or an unreadable file stops the run. Sender and subject are capped at 200 and 300 characters before matching. The lists apply to mail
only, not to calendar titles or issues.

**Triggers.**

```sh
brew install --cask hammerspoon        # wake and unlock
brew install terminal-notifier         # the "N need you" notification; optional
```

Grant Hammerspoon Accessibility (System Settings, Privacy and Security, Accessibility), then add one line to `~/.hammerspoon/init.lua`:

```lua
dofile(os.getenv("HOME") .. "/ace-playbook/hooks/hammerspoon/ace.lua")
```

The path defaults to `$HOME/ace-playbook/hooks/brief.sh`; a `~/.hammerspoon/ace_config.lua` returning
`{ brief_script = "/abs/path/brief.sh" }` overrides it. Login coverage (launchd has no wake event) is a LaunchAgent that runs the
script once at load:

```sh
cp hooks/launchd/com.example.ace-brief.plist "$HOME/Library/LaunchAgents/com.example.ace-brief.plist"
# edit the file: replace __ACE_PLAYBOOK_HOME__, __ACE_CONFIG_FILE__, __ACE_DATA_DIR__ and __ACE_USER_HOME__ (your home directory)
# with absolute paths and rename the Label to something you own
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.ace-brief.plist"
```

Remove it with `launchctl bootout "gui/$(id -u)/com.example.ace-brief"`, delete the `dofile` line and `brew uninstall` what you added.

**What phase 0 measured.** Your own `~/.claude` hooks fire inside every plain `claude -p` run (six SessionStart events per probe), so
`brief.sh` passes `--setting-sources project,local`: the claude.ai connectors still load (probed: 9 MCP servers, 328 tools) and no user
hook fires; `--settings` still adds the brief's own guard. `--allowedTools` does not shrink the prompt: all connector tool definitions
still load, about 140k cached input tokens per stage-1 run (about 52k created plus 91k read in the measured run); stage 2 loads none.
The model was seen calling only the allowed tools.

**Sessions.** Both stages pass `--no-session-persistence`, so no session transcript is written: without it Claude Code would store every
raw mail result under `~/.claude/projects/` where the nightly reflect sweep reads transcripts. Probed: a run creates only an empty
`~/.claude/projects/<cwd slug>/memory/` directory and no `.jsonl` (the empty directory is harmless).

**Tool scope.** Stage 1 passes `--tools ToolSearch` (no built-in file or shell tool loads), `--allowedTools` from
`hooks/brief_allowed_tools.txt`, and `--disallowedTools` from `hooks/brief_denied_tools.txt` (the connector write tools by full name and
the other connectors as whole servers; `hooks/brief-settings.json` repeats the list in `permissions.deny` and a test keeps them equal).
It cannot use `--strict-mcp-config`, because the claude.ai connectors are account-level and cannot be named in an mcp-config file.
Links on the page are limited to `mail.google.com`, `calendar.google.com` and `linear.app` (constant `LINK_HOSTS` in
`compile/brief_render.py`): a link is an outbound channel, so any other URL in the brief is dropped.

**Ledger.** Each stage appends one line to `brief_ledger.jsonl` (0600): `ts`, `stage`, `model`, `cost_usd` (list-price equivalent; the
subscription is not billed per run), the four token counts, `duration_ms`, `num_turns`, `tools_called`, and the plan windows read from
the stream's `rate_limit_event`: `five_hour_before/after` and `seven_day_before/after` (utilization, 0 to 1; both readings come from the
same stream, so compare consecutive rows for the share of a week). `uv run python -m ace_playbook.compile.brief status` prints the last
brief, the needs-you count, the ledger row count and the last cost of each stage.

The raw streams of a run hold mail the exclusion list has not seen, so `brief.sh` deletes them when it exits, also on SIGTERM, SIGINT or
SIGHUP, which also stop the `claude` child (`ACE_BRIEF_KEEP=1` keeps them, 0600, under `brief_dir/run`; the session transcript is
never written, see Sessions above). `digest_clean.json` (summaries after the exclusions) stays there until the next run.

## What the hooks write

All under `data_dir`: `deltas.jsonl` (+ `.lock`), `playbook.sqlite` (disposable view),
`injections.jsonl`, `citations.jsonl`, `reflections.jsonl`, `queue.jsonl`, `quarantine.jsonl`,
`health.json`, `reflect.log`; with the shadow hook also `shadow.jsonl`, `shadow.log` and `shadow_state/` (and `shadow.synthetic.jsonl` after `replay-synthetic`).
None of them should ever be committed to a public repository; keep `data_dir` outside any checkout (the
repo's `.gitignore` lists the stage 1 files only).
