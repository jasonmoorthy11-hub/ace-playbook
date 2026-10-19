# ace-playbook Stage 2: the compiler in shadow mode (design)

Status: design draft, 2026-10-19; nothing built. Package `ace_playbook/compile/` in `~/ace-playbook`
(Stage 1 at `5448d6d`). Unprefixed paths are in that repo; `step0/` = `rebuild-lab/ace/step0/`; "report" =
`rebuild-lab/reports/ACE rebuild research round two.md`.

## 1. What Stage 2 is, in one paragraph

Stage 1 stores lessons, injects pinned ones and records which were shown (README.md:33-44), and
says plainly that the compiler is not built (README.md:46-47, LIMITATIONS.md:49-53). Stage 2 adds
a compiler that turns a well-evidenced lesson into a **spec-only JSON proposal** naming one
human-written detector from a fixed catalogue, validates it TRACE-style (parse, schema, unit
regressions, replay of the originating incident; report:25), routes it through an Opus review
that sees provenance and a human approval (report:51, SECURITY.md:43-47), and then evaluates
approved proposals live in **shadow mode** on an observe, warn, block ladder (report:25, 37).
The compiler never writes hooks, settings or skills. Counters come only from external signals,
never from agent self-tags (LIMITATIONS.md:34-36, report:37).

## 2. Ground truth Stage 2 builds on

| Fact | Source |
|---|---|
| Hooks today fail open, exit 0, never block, no network | SECURITY.md:35-36; inject/__main__.py:1-5, 61-64; hooks/sessionstart.sh:4-5 |
| Ledgers are append-only JSONL, 0600, `sort_keys` | inject/ledger.py:11-17 |
| Exposure ledger (`injections.jsonl`) holds `shown_ids`, `holdout_ids` per session | inject/injector.py:125-134 |
| Citation ledger holds `[L-id]` strings the agent wrote (agent self-report) | inject/citations.py:11-25 |
| Inertness check to reuse for proposal messages | inject/sanitize.py:108-120; store/schema.py:64-78 |
| Lesson evidence = `{quote, origin, session_id}`, live status needs user origin | store/schema.py:106-121, 143-144 |
| Quote matching is whitespace-collapsed, case-folded; user origins only | reflect/provenance.py:31-32, 65-92 |
| Scrubber: `scan(text, profile, deny_terms)`, `QuarantineRequired` | scrub/engine.py:192-203; scrub/quarantine.py:20-27 |
| Transcript file can lag the live conversation; Stop gets `last_assistant_message` | hooks/INSTALL.md:94-95 |
| SessionEnd default budget 1.5 s; SessionStart output cap 10,000 chars | hooks/INSTALL.md:86-90; report:33 |
| Stage 1 test functions: 145 (`grep -c "def test" tests/*.py`) | tests/ |

Step 0 measured 651 main sessions, 93 interactive, 97 with the ACE block, over 39 days
(RESULTS.md:9-18). Four strong detectors with a mapped lesson clear 2% of eligible sessions:
`edit_unread_session`, `exitplan_question_block`, `preclaim_audit_block`, `rm_rf_nonscratch`
(RESULTS.md:33). **`commit_without_push` is classified weak**, not strong (step0/detectors.py:298;
RESULTS.md:62), alongside `edit_unread_turn` and `rm_rf` (RESULTS.md:34). The verdict was
"NOT FEASIBLE AS INJECTED TODAY" (RESULTS.md:40), which is why the compiled path matters.

## 3. Package layout

| File | Role | Budget |
|---|---|---|
| `compile/events.py` | `Event`, `ToolUse`, `ToolResult` (port of step0/scanlib.py:24-52); two adapters: hook payload to Event, transcript record to Event (port of scanlib.py:80-126); `command_segments`, `git_subcommand` (scanlib.py:231-266) | ~220 lines |
| `compile/detectors.py` | Pure detector functions and the catalogue (section 4) | ~300 |
| `compile/state.py` | Rolling per-session state file, locked, atomic, hashed name | ~90 |
| `compile/evidence.py` | Evidence cards (section 5) | ~260 |
| `compile/proposal.py` | Proposal schema, validation, replay/regression runner, writer (section 6) | ~300 |
| `compile/shadow.py` | Hook entry point: load approved proposals, evaluate, decide exit code (section 7) | ~220 |
| `compile/offline.py` | Nightly pass: run signal detectors over finished transcripts, finalize session-level detectors, label pre-fill, demotions | ~250 |
| `compile/report.py` | shadow.jsonl to results tables, effect estimates (section 8) | ~260 |
| `compile/__main__.py` | CLI: `cards`, `propose`, `packet`, `review-record`, `approve`, `label`, `report`, `replay-synthetic` | ~180 |
| `hooks/shadow.sh` | One script, two registrations (PreToolUse, Stop) | 6 |

Data lives under the existing `data_dir` (config.py:20, 69-70), never under `~/.claude`
(config.py:1): `detector_events.jsonl`, `evidence/cards.jsonl`, `proposals/`, `proposals/reviews/`,
`proposals/approved/`, `approvals.jsonl`, `demotions.jsonl`, `labels.jsonl`, `shadow.jsonl`,
`shadow.synthetic.jsonl`, `shadow_state/`.

## 4. detectors.py: the catalogue

Detectors are pure: `fire(event, state) -> (hits: dict[str, int], new_state)`. No I/O, no clock,
no environment. The same function runs in two tiers, fed by the two adapters in `events.py`:

- **Live tier** (hook path): PreToolUse/PostToolUse/Stop payloads. Only *payload detectors* run
  here, because the transcript can lag the live conversation (hooks/INSTALL.md:94-95), so hook
  blocks by other hooks are not reliably visible yet.
- **Offline tier** (nightly, `offline.py`): the finished transcript, parsed with the faithful port
  of `scanlib.parse_event`. This tier is authoritative for measurement and is the only tier for
  *signal detectors* (blocks raised by existing hooks, user corrections).

Each catalogue entry carries `name`, `strength` (copied from Step 0), `tier`, `consumes`,
`state`, `fire`, `compilable`, `lessons` (L-ids after migration), and `source_sha256` of the
function body (used to invalidate approvals when the code changes).

| Detector | Step 0 strength / eligible rate | Tier | Consumes | State | Fires when | Compilable |
|---|---|---|---|---|---|---|
| `rm_rf_nonscratch` | strong, 7/97 = 7.2%, 9 events (RESULTS.md:52) | live + offline | PreToolUse `Bash` | none | some segment of `tool_input.command` is `rm` with recursive and force flags and a target (detectors.py:52-59), and at least one target fails the `SCRATCH` regex (detectors.py:38-40, 115-119) | yes, up to block |
| `rm_rf` | weak, 8/97 = 8.2% (RESULTS.md:51) | live + offline | PreToolUse `Bash` | none | same, any target (detectors.py:115-117) | observe only |
| `edit_unread_session` | strong, 11/97 = 11.3%, 15 events (RESULTS.md:49) | live + offline | PreToolUse `Read`, `Edit`, `Write`, `MultiEdit`, `NotebookEdit`; offline also attachments `file`, `edited_text_file`, `compact_file_reference` (scanlib.py:94-96) | set of sha256[:16] path hashes read or written this session, capped at 5,000 | Edit/Write/MultiEdit on a path not in the set, basename not in `AUTOLOADED`, not under `/.claude/plans/` (detectors.py:45, 79-90); offline: tool result not an error and not "file created" (detectors.py:100-107); live: path exists (`os.path.lexists`) | yes, up to warn (block gated, see 7.3) |
| `edit_unread_turn` | weak, 13/97 = 13.4% (RESULTS.md:50) | live + offline | as above, plus Stop (turn reset) | per-turn set, cleared at each human prompt (detectors.py:74-75); live clears at Stop | as above, against the turn set | observe only |
| `exitplan_question_block` | strong, 10/97 = 10.3%, 16 events (RESULTS.md:57) | offline only | transcript tool_result text | none | `HOOK_ERR` match naming `pretooluse-exitplan-question-check.sh` (detectors.py:26-27, 146-156) | no: already a hook; outcome counter and evidence only |
| `preclaim_audit_block` | strong, 60/97 = 61.9%, 269 events (RESULTS.md:55) | offline only | transcript user record | none | text starts "Stop hook feedback" and the bracket names `stop-mid-task-audit.sh` (detectors.py:28, 173-186, 202-203) | no: already a hook; double-counts when both variants fire (detectors.py:265) |
| `commit_without_push` | weak, 3/97 = 3.1% (RESULTS.md:62) | live state + offline finalize | PreToolUse `Bash`; finalized at session end | counts of `git commit`, `git push` via `git_subcommand` (detectors.py:122-132) | session ended with commit > 0 and push == 0 (detectors.py:142-143) | observe only (session-level; no tool call to gate) |
| `user_correction` | weak (detectors.py:274-275) | offline only | human prompt, <= 800 chars | none | `CORRECTION` regex (detectors.py:29-34, 225-230) | no: an FP/TP signal (section 7.3), never a check |

The faithful port keeps every Step 0 quirk that affects comparability: quoted multi-word strings
become the token `Q` and heredoc bodies are dropped (scanlib.py:231-254), so `bash -c` bodies stay
invisible (detectors.py:255). Two live-tier deviations are deliberate and recorded per row in
`shadow.jsonl` as `tier: "live"`: the live tier cannot see harness-loaded attachments (so live
`edit_unread_session` over-fires relative to offline), and it decides at PreToolUse before knowing
whether the tool succeeded. The offline tier recomputes both faithfully; measurement uses offline
counts, and live-versus-offline disagreement is itself reported. Other Step 0 detectors
(`sleep_chain_cmd`, `python_bare`, `push_tags_cmd`, `git_force_or_branchD_cmd`) port the same way
later; `sleep_chain_cmd` is the obvious second proposal because it is frequent (28/95 block
sessions without the lesson, RESULTS.md:54).

### 4.1 Regression and replay payloads (shipped as test fixtures)

Every detector ships at least three payloads that must NOT fire and one replay payload that MUST
fire. All are invented, taken from or modelled on step0/tests/test_detectors.py.

| Detector | Must NOT fire | Must fire (replay) |
|---|---|---|
| `rm_rf_nonscratch` | `Bash {"command": "rm -rf /tmp/scratch node_modules"}` (test:99); `Bash {"command": "grep rm -rf notes.md"}` (test:105); `Bash {"command": "rm -f file"}` (test:105); heredoc `cat <<'EOF'` with the rm line in the body (test:106) | `Bash {"command": "rm -rf /w/src"}` (test:101) |
| `rm_rf` | `rm file.txt`; `rm -r dir`; `echo "rm -rf /w/src"` (test:105) | `cd /w && rm -fr build2` (test:95) |
| `edit_unread_session` | Read `/w/a.py` then Edit `/w/a.py` (test:66-68); Edit `/w/CLAUDE.md` (test:79-81); Write `/w/new.py` to a new file (test:75-77; live: path absent); Edit `<home>/.claude/plans/p.md` (detectors.py:87) | Edit `/w/a.py` with no prior Read (test:62-64) |
| `edit_unread_turn` | Read then Edit in the same turn (test:66-68); Edit `CLAUDE.md`; Write new file | Read in turn 1, Edit in turn 2 (test:70-73) |
| `exitplan_question_block` | tool_result `Exit code 2\nNo such file` (test:172); block by `some-new-gate.sh` (test:167-169); block by `guard-destructive.sh` (test:155-157) | `PreToolUse:ExitPlanMode hook error: [bash ~/.claude/hooks/pretooluse-exitplan-question-check.sh]: blocked: reason` (test:152-161) |
| `preclaim_audit_block` | human text "The stop hook feedback was odd" (test:190-191); feedback naming `stop-ack-mid-turn.sh` (test:178); context nudge feedback (test:186-188) | `Stop hook feedback:\n[bash ~/.claude/hooks/stop-mid-task-audit.sh]: stop` (test:45-46, 176) |
| `commit_without_push` | commit then push (test:146); `git status` only (test:148); `echo "git commit -m m"` (quoted, masked to `Q`, scanlib.py:232-254) | `git commit -m m`, session ends (test:145) |
| `user_correction` | "can you try that again"; "Stop hook feedback: I told you so"; task-notification text (test:227-229) | "you keep doing that" (test:222-224) |

A parity test runs the ported functions over the Step 0 synthetic session builder and asserts
identical counts to step0/detectors.py for every scenario in test_detectors.py, so the port cannot
drift from the numbers in RESULTS.md.

### 4.2 Per-session state (`state.py`)

```json
{"v": 1, "session_hash": "9c1e0a7b44d2f310", "created": "2026-10-20T09:01:12Z",
 "seen_paths": ["4be1c0f2a9d8e771", "0d93aa61c2b4f5e0"], "turn_paths": ["0d93aa61c2b4f5e0"],
 "git": {"commit": 1, "push": 0}, "events": 37, "truncated": false}
```

File name `shadow_state/<sha256(session_id)[:16]>.json`, so a hostile session id cannot traverse
paths. Paths are stored as sha256[:16], never as text (the Step 0 rule of storing no transcript
text, scanlib.py:1-5). Writes take `filelock.locked` (filelock.py:12-22) because parallel tool
calls can run hooks concurrently, then write-to-temp and `os.replace`. Over 5,000 entries sets
`truncated: true` and the detector stops firing for that session (fail toward silence). The
nightly pass deletes state files seven days after the session's last event.

## 5. evidence.py: evidence cards

A card ties one lesson to one externally observed incident. Inputs, in order of authority:

1. **Lesson store**: the lesson's evidence quotes and their `session_id` (store/schema.py:106-121);
   only `user_message`/`user_file` origins count (schema.py:18).
2. **Exposure ledger** `injections.jsonl`: whether the lesson was shown or held out in the
   incident session (injector.py:125-134). Harness-written, not agent-written.
3. **Detector events** `detector_events.jsonl` from the offline tier, plus live `shadow.jsonl` rows.
4. **Reflections ledger** `reflections.jsonl` (reflect/runner.py:50-55): which session was
   reflected and with what status, to link a lesson to its originating session.
5. **Citation ledger** `citations.jsonl`: recorded on the card as `agent_cited` and flagged
   `self_report: true`. It never admits a card, never counts toward TP/FP, never promotes.

Admission rules (all must hold, else no card): the lesson is `active` or `provisional` with user
evidence; the evidence quote is re-located, with `provenance.norm` (provenance.py:31-32), inside a
*user message* of the originating session's cleaned transcript, with compact summaries and
sidechains dropped as in clean/transcript.py:116-122; at least one detector event mapped to the
lesson exists. The incident is the first mapped detector event in the originating session, or if
there is none, the first one in a later session where the lesson was eligible (card field
`incident_kind: "originating" | "later"`).

```json
{"id": "E-71c2d9a0", "v": 1, "lesson_id": "L-b3d1fba9",
 "origin_session": "sid:5f0c9e21aa03b7d4", "incident_session": "sid:5f0c9e21aa03b7d4",
 "incident_kind": "originating",
 "quote_offsets": [{"record_uuid_hash": "a91f03c2", "start": 112, "end": 171, "origin": "user_message"}],
 "detector": "rm_rf_nonscratch", "detector_sha256": "3e0b...", "tier": "offline",
 "incident_ts": "2026-09-22T14:03:51Z",
 "payload_sha256": "c4a1...e9", "payload_ref": "evidence/payloads/c4a1...e9.json",
 "lesson_shown_in_incident_session": true, "agent_cited": false, "self_report": true,
 "scrub": {"profile": "LESSON", "hits": 0}, "created": "2026-10-20T03:10:00Z"}
```

Session ids appear only as `sid:` plus sha256[:16]. The card stores quote *offsets*, not quote
text. `payload_ref` is a minimized hook-shaped payload (`tool_name`, `tool_input` only) rebuilt
from the transcript tool_use; it is scanned with `scan(text, LESSON, cfg.deny_terms)`
(engine.py:192-203). A hit masks it and sets `replayable: false`; the proposal then must use a
synthetic replay and say so. Card ids are `E-` plus sha256[:8] of (lesson id, incident session,
payload hash), so rebuilding is idempotent.

## 6. proposal.py: spec-only proposals

The Stage 2 compiler is **deterministic code, not a model**. It selects candidates by the
catalogue's `lessons` field (the same mapping Step 0 kept in step0/detectors.py:20) plus at least
one admitted evidence card, and fills a template. No LLM writes a check, a matcher or a message.
This follows the report's rule of a fixed evaluator the proposer cannot edit (report:29).

```json
{"id": "P-3f9a0c12", "v": 1, "lesson_id": "L-b3d1fba9",
 "event": "PreToolUse", "matcher": "Bash",
 "check": {"detector": "rm_rf_nonscratch", "params": {}, "detector_sha256": "3e0b..."},
 "message": "ACE P-3f9a0c12: recursive force delete outside a scratch folder. Confirm the target with the user first.",
 "mode": "observe",
 "regression_examples": [
   {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/scratch node_modules"}, "expect": "no_fire", "source": "synthetic"},
   {"tool_name": "Bash", "tool_input": {"command": "grep rm -rf notes.md"}, "expect": "no_fire", "source": "synthetic"},
   {"tool_name": "Bash", "tool_input": {"command": "rm -f file"}, "expect": "no_fire", "source": "synthetic"}],
 "replay": {"evidence_card": "E-71c2d9a0", "payload_sha256": "c4a1...e9", "expect": "fire", "source": "private"},
 "provenance": ["E-71c2d9a0"],
 "created": "2026-10-20T03:12:00Z", "created_by": "ace_playbook.compile 0.2.0",
 "reviewed_by": null}
```

Validation, all fail-closed, before the file is written:

1. **Parse and schema**: exact key set; `id` matches `^P-[0-9a-f]{8}$`; `event` in {PreToolUse,
   Stop}; `matcher` from the detector's `consumes`; `mode` must be `observe` at creation;
   `detector` in the catalogue and `compilable`; `params` validated by the detector's own schema.
2. **Message**: 20 to 300 chars, `inert_reason(message)` (sanitize.py:108-120) must return None,
   and the message must start with the literal `ACE <id>:` so a user can always trace a warning to
   its file. A message never interpolates payload fields.
3. **Unit regressions**: at least three `no_fire` examples (catalogue fixtures plus private
   near-misses from the same session) produce no hit.
4. **Replay**: the originating incident's payload, through the same detector with a fresh state,
   must fire. No replay, no proposal (TRACE acceptance, report:25; EviSkill's 20.7-42.5% rejection
   rate is the expected baseline, report:27).
5. **Scrub**: every string in the proposal through `scan(..., LESSON, deny_terms)`; any hit raises
   `QuarantineRequired` (quarantine.py:20-27) and the proposal goes to quarantine, not to disk.
6. **Path**: the writer resolves `data_dir/proposals/<id>.json`, checks `relative_to` the resolved
   proposals dir, refuses symlinks, writes temp then `os.replace`. It has no code path to any other
   directory.

**Review and approval.** `compile packet P-...` writes a review packet (proposal, cards, lesson
text and evidence quotes from the store, detector source, regression and replay results, Step 0
FP note for the detector) for a **separate Opus 5.5 reviewer** that sees provenance (report:51).
The reviewer's verdict is stored by `compile review-record` as
`proposals/reviews/<id>.json` = `{"proposal_sha256": "...", "verdict": "approve", "reviewer":
"claude-opus-5-5", "at": "...", "notes_sha256": "..."}`, and `reviewed_by` in the pending file is
set to `{"reviewer": "claude-opus-5-5", "review_sha256": "..."}`. Promotion to approved is the
human moving the file, done through `compile approve P-3f9a0c12 --mode observe`, which refuses
unless stdin is a TTY, the review verdict is `approve` for this exact proposal hash, and checks 1-6
pass again on current code. It then moves the file into `proposals/approved/` and appends a
hash-chained record to `approvals.jsonl`:

```json
{"seq": 4, "proposal_id": "P-3f9a0c12", "proposal_sha256": "77d2...", "mode": "observe",
 "rule_check": "n/a (initial)", "at": "2026-10-21T08:40:00Z", "prev_hash": "1b9c...", "hash": "e04f..."}
```

The hook loads a proposal only if its file hash equals the latest `approvals.jsonl` record for that
id. Mode changes go through the same command (`--mode warn`), which also checks the promotion rule.

## 7. shadow.py and hooks/shadow.sh

```bash
#!/bin/bash
# Shadow evaluator for approved ACE proposals. Arg is a fixed literal from settings (pre|stop).
# The payload reaches Python on stdin only; no payload field is expanded by the shell.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
PYTHONPATH="$ACE_HOME" exec "${ACE_PYTHON:-python3}" -P -m ace_playbook.compile.shadow "$1"
```

`-P` stops Python prepending the working directory to `sys.path`. Without it, `python3 -m` run
from a project that contains a `json.py` imports that file; the Stage 1 hooks have this exposure
today (hooks/sessionstart.sh:4, hooks/stop_citation_ledger.sh:4, hooks/sessionend.sh:5), the same
class as the pre-commit shadowing finding (docs/reviews/2026-10-17-opus-stage1-review.md:24).

Registration (documented in INSTALL.md, never installed by code): PreToolUse matcher
`Bash|Read|Edit|Write|MultiEdit|NotebookEdit`, `timeout: 5`, `onFailure: "continue"`; Stop with
the same script and arg `stop`, alongside the existing citation hook.

### 7.1 Evaluation

1. Read stdin (cap 256 KiB; larger means log `skipped:oversize`, exit 0). Require a non-empty
   string `session_id`, as inject/__main__.py:28-30 does.
2. Kill switch: `data_dir/proposals/DISABLED` exists, or config `shadow_enabled` false, then exit 0.
3. Load approved proposals: at most 50 files, each at most 16 KiB, regular files only (no
   symlinks), hash must match `approvals.jsonl`, message re-checked with `inert_reason`. Any
   failure skips that proposal and logs the reason by type only (as inject/__main__.py:18-25).
4. Effective mode = min(approved mode, any cap in `demotions.jsonl`, arm). The arm comes from
   `coin(shadow_seed, session_id, proposal_id) < arm_rate` (injector.py:35-38) during a randomized
   phase; outside one the arm is always "active".
5. Build the Event, run each matching detector with the session state, update state.
6. Append one row per evaluated proposal to `shadow.jsonl` (below), then act on the strongest
   decision.

| Mode | On fire | Exit | Notes |
|---|---|---|---|
| observe | append `would_fire` row | 0 | no output at all; the agent sees nothing |
| warn | row + one stderr line: the stored message | 0 | see open question 1 on whether the model sees it |
| block (PreToolUse) | row + stored message on stderr | 2 | tool call is not run |
| block (Stop) | row + stored message on stderr | 2 | never when `stop_hook_active` is true, which prevents a loop |
| any error | type-only log line | 0 | fail open; a broken store never blocks a session |

```json
{"ts": "2026-10-21T10:12:04Z", "v": 1, "corpus": "private", "session": "sid:5f0c9e21aa03b7d4",
 "proposal_id": "P-3f9a0c12", "detector": "rm_rf_nonscratch", "tier": "live", "event": "PreToolUse",
 "mode": "observe", "arm": "active", "decision": "would_fire", "hits": 1,
 "payload_sha256": "c4a1...e9", "target_hashes": ["0d93aa61c2b4f5e0"], "agent_id_present": false,
 "dur_ms": 41}
```

Rows hold only enums, ids, hashes, integers and timestamps. There is no free-text field, so there
is nothing for the scrubber to miss on the hot path; a test asserts the row schema.

### 7.2 Cost budget

The hook adds a Python start to every matched tool call. Imports are limited to `json`, `os`,
`re`, `hashlib`, `fcntl`, `time`; no `sqlite3`, no scrub engine, no store. Target p95 below 100 ms
measured from `dur_ms`; above 200 ms over seven days disables the hook (7.3). Nothing is added to
SessionStart (the 7,000-char block and 10,000 cap are untouched, config.py:15) or SessionEnd, which
keeps only its 1.5 s enqueue (enqueue.py:1-5). Session-level finalization (`commit_without_push`)
runs in the nightly pass from the state file, not in SessionEnd.

### 7.3 True positive, false positive, promotion, demotion

**Labels.** Every live fire gets a label. Precedence: a human label in `labels.jsonl` (written by
`compile label`, TTY only) beats everything. Before that, the nightly pass pre-fills:

- **Auto-FP** (counts as FP until a human relabels it with a reason): (a) `user_correction` fires
  within the next two human prompts after a warn or block message, meaning the user objected to
  the check; (b) a target hash of the fire occurs in the most recent human prompt before it, meaning
  the user asked for exactly this (hashing is done in memory and no prompt text is stored); (c) in
  block mode, the user runs the same target through their own shell-mode command.
- **Auto-TP candidate** (counts only after a human confirms): (a) an existing hook blocked the same
  tool call (`guard_destructive_block`, detectors.py:157-158); (b) in observe mode, `user_correction`
  fires within two human prompts after the fire, meaning the user objected to the action.
- **Unlabeled** fires count toward neither side. Agent self-tags (`helpful`, `harmful`, `violated`,
  reflect/runner.py:20) and `[L-id]` citations are never inputs.

**Mode ladder.**

| Step | Allowed for | Rule to move up (all must hold) | Who acts |
|---|---|---|---|
| observe | any compilable detector | initial mode after approval | human `approve --mode observe` |
| observe to warn | strong detectors only | at least 14 days in observe; at least 5 human-confirmed TPs and 0 FPs since entering observe; replay passes on current code; lesson still `active` | human `approve --mode warn` (CLI checks the rule) |
| warn to block | strong detectors whose catalogue row allows block | the same counts again, fresh, in warn; a new Opus review of the warn-period data; evasion candidates reviewed | human `approve --mode block` |
| any mode to disabled | always | lesson leaves `active`; detector `source_sha256` changes; hook error rate above 1% or p95 above 200 ms over seven days | nightly pass (lowering only) |

**Demotion** is automatic and only ever lowers a mode. The nightly pass appends to
`demotions.jsonl` (each record names the label or metric that caused it): block to warn on one FP;
warn to observe on one FP; two FPs within 30 days in any mode to observe, with re-review required.
Counters reset after any demotion. Two evasion candidates in 14 days (after a block, a different
command touching the same target hash within the next five tool calls, which is the
unanticipated-path failure in report:45) flags the proposal for review without demoting it.

**What the numbers mean.** Five TPs with zero FPs bounds the FP rate only below about 45%
(one-sided 95%, 1 - 0.05^(1/5)); about 29 clean TPs are needed to get under 10%. At Step 0 volume
(`rm_rf_nonscratch` 9 events in 39 days, RESULTS.md:18, 52) the expected time to five fires is
about 22 days even if every fire is a true positive, so "14 days" is a floor, not a forecast.

## 8. Measurement

**Tables.** `compile report` reads `shadow.jsonl`, `shadow.synthetic.jsonl`, `labels.jsonl` and
the offline `detector_events.jsonl`, joined to an offline session index (interactive versus
automated by the Step 0 rule, step0/measure.py:79). It renders two tables, never one:

- **Synthetic (reproducible)**: rows produced by `compile replay-synthetic`, which feeds the frozen
  fixture corpus (`ace_playbook/fixtures/frozen/`) through the evaluator with `corpus:
  "synthetic"` into its own file.
- **Private corpus (aggregate, not reproducible)**: live rows, counts and rates only, no ids.

The report refuses to aggregate across `corpus` values, and each table states its source. This is
the separation the report requires for the README results table (report:59).

| Column (per proposal, per mode, per tier) | Definition |
|---|---|
| Eligible sessions | interactive sessions with at least one event the detector consumes |
| Sessions with fire / rate / Wilson 95% | session-level binary, as Step 0 (RESULTS.md:196; measure.py:28-35) |
| Fires per eligible session | event count over eligible sessions |
| Repeat fires | fires after the first in the same session |
| TP / FP / unlabeled | from 7.3 |
| False-block rate | FP blocks over all blocks (the compiled-path primary, PREREGISTRATION.md:78-81) |
| Friction | warn or block messages per 100 tool calls; correction turns per 100 human prompts |
| Evasion candidates | from 7.3 |
| Hook cost | p50/p95 `dur_ms`, error count |
| Live versus offline agreement | fires seen in one tier and not the other |

**Effect estimate.** For the compiled path, blocking prevents the action by construction, so the
primary is false-block rate and friction, with recurrence descriptive (PREREGISTRATION.md:78-84).
The descriptive effect is fires per eligible session with versus without the check, using a
per-(session, proposal) arm drawn with the seeded coin (injector.py:35-38) at `arm_rate` 0.5 during
the randomized phase: "without" sessions run in observe, "with" sessions run the approved mode. A
hook has no memory across sessions, so the first attempt in a session cannot differ between arms;
any real effect shows in **repeat fires** and evasion, and the report leads with those. The
estimate is a rate ratio with randomization inference (10,000 seeded permutations of arm labels)
and Wilson intervals per arm, matching the analysis named in PREREGISTRATION.md:33. With about 2.4
interactive sessions a day (RESULTS.md:184) and a 7.2% session rate, one week yields about one
session with a fire: enough to test plumbing, cost and labeling, not to estimate any effect.

**Pre-registration additions** (into PREREGISTRATION.md as a dated amendment section, never an
edit of sealed text, PREREGISTRATION.md:3-7): the `shadow_seed` sha256 (seed private, as
PREREGISTRATION.md:29-32); the detector list with each `source_sha256` and strength; the approved
proposal hashes and modes; `arm_rate` and the arm definition; the eligible-session rule; the
analysis code hash (`compile/report.py` plus `compile/detectors.py`); the TP/FP rules of 7.3; the
promotion and demotion rules; stopping for harm only.

**Why seal only after shadow data exists.** (a) The detectors are unvalidated until they run on
live payloads: Step 0 found one strong detector completely blind (`edit_unread_error`, 0 events
because the error text never reaches transcripts, RESULTS.md:242), and the live tier deviates from
the offline one by design. Sealing first would freeze broken instruments. (b) Power and the
eligible-session definition need measured fire and FP rates, which do not exist yet; the Step 0
baselines are one month over a nearly fixed block (RESULTS.md:189-203). (c) The observe-only week
has no arms and the agent sees nothing, so it contains no treatment contrast that could be peeked
at. To stop the forking-paths risk, two things are committed **before** observe starts: the
detector `source_sha256` list and the report code hash. Seal (`prereg-v1`) happens after the
observe week and before the first randomized warn session; anything randomized before the tag is
exploratory (PREREGISTRATION.md:6-7).

## 9. Security

| Control | Mechanism | Test |
|---|---|---|
| Fail-closed message sanitiser | `inert_reason` at propose, at approve, and again at hook load (sanitize.py:108-120); plus 300-char cap and the `ACE <id>:` prefix; failure means the proposal is skipped, never rewritten | probes from the Stage 1 review (review.md:8) as messages: URL, bare domain, backtick, "Ignore the previous guidance", settings wording |
| No shell interpolation | shadow.sh passes stdin untouched and `"$1"` is a literal from settings; Python never calls `subprocess`, `os.system` or `eval`; detectors tokenize by regex (scanlib.py:241-254) and never execute or expand | grep test over `compile/` for those names; shell test with a payload holding `$(...)` and backticks |
| Payload never echoed | stderr carries only the stored message, never the command or path | test that no payload substring reaches stderr |
| Path containment | proposal ids by regex; resolved paths must sit under `data_dir/proposals[/approved]`; symlinks refused; state file names are hashes | traversal ids, symlinked approved file |
| No network in the hook path | `compile.shadow` imports no `urllib`, `http`, `socket`, `ssl`, `subprocess` | run `python3 -I -c` importing the module and inspect `sys.modules` |
| Scrub before write | cards and proposals through `scan(..., LESSON, deny_terms)`, hit means quarantine (quarantine.py:20-27); shadow rows and state hold no free text by schema | planted FAKE token in a fixture replay payload |
| Tamper evidence | hash-chained `approvals.jsonl`, TTY-only approve and label; hook loads only hash-matching files; report flags approved files whose hash or mtime changed without a ledger record | edited approved file is ignored |
| Bounded cost | input, file count, file size and state caps; oversize means skip | oversize fixtures |

Residual risks, stated plainly: the agent runs with Jason's user permissions and can edit
`data_dir`, so it could append a demotion or delete an approved file to evade a block; the controls
above make that visible in the report, not impossible. Block mode is the first time ace-playbook
hooks can stop the agent, which contradicts SECURITY.md:35 ("Hooks never block a session") as
written; that line needs a dated amendment before step 4 can reach block.

## 10. Build order

| Step | Deliverable | New tests |
|---|---|---|
| 1 | `events.py`, `detectors.py`, `state.py`: 8 catalogue detectors, both adapters, parity with step0 | 44: 8 detectors x 4 fixtures (32), 6 adapter, 4 state, 2 parity |
| 2 | `evidence.py` + `compile cards` | 12: admission rules, quote only in tool output rejected, self-report-only refused, scrub hit masks and sets non-replayable, idempotent ids, offsets within user messages |
| 3 | `proposal.py` + first proposal for `rm_rf_nonscratch` (lesson g-010, mapped to `L-b3d1fba9` in the migration dry run, ace/migration/id_map.jsonl:96) | 16: schema, 5 sanitiser rejects, replay must fire, regressions must not, scrub, containment, approve refuses without TTY or review, hash chain, end-to-end under a temp HOME proving nothing outside `data_dir` changes |
| 4 | `shadow.py`, `hooks/shadow.sh`, INSTALL.md section; Jason installs it by hand in observe mode (settings writes are hook-guarded per his setup) | 18: three modes and exit codes, `stop_hook_active`, malformed or empty stdin, missing session id, kill switch, unapproved or hash-mismatched or symlinked proposal ignored, oversize skip, no-network imports, no payload text in rows or stderr, arm determinism, shell script inspection |
| 5 | `offline.py` + `report.py`: nightly signal detectors, label pre-fill, demotions, two tables | 12: corpus separation, Wilson, TP/FP precedence, promotion check, each demotion trigger |
| 6 | One week of observe-mode measurement | none; output is the report with every fire labeled, live versus offline agreement, p95 cost, and a written go or no-go for the seal |

Total about 102 new tests over the existing 145 test functions. Steps 1 and 2 need no lesson
migration (synthetic fixture lessons). Step 3's *real* proposal needs `L-b3d1fba9` in the private
store with user-origin evidence; until then step 3 is built and tested against a fixture lesson.

**Not built in Stage 2:** no auto-install of hooks or edits to settings, CLAUDE.md, skills or
`~/.claude`; no self-modifying hooks (the evaluator never edits proposals, approvals or its own
code; the nightly pass may only lower a mode); no LLM anywhere in the hook path or in proposal
generation (the only model is the out-of-band Opus reviewer); no CLAUDE.md-line proposals,
deletion proposals or path-scoped rules (report:69, a later stage); no judge; no `UserPromptSubmit`
reminders; no cross-session learning by the hook; no block mode during the step 6 week.

## 11. Open questions for Jason

1. **Warn visibility.** The brief's warn is "stderr line, exit 0". My reading of the hooks docs is
   that stderr on exit 0 reaches the user's verbose view but not the model. Should warn use the
   documented JSON output channel instead (verified live at step 4), or stay user-visible only?
2. **Randomized arms.** Measuring "with versus without" means the check is off (observe) in about
   half of sessions during the randomized phase. Do you accept that for `rm_rf_nonscratch`, given
   that guard-destructive.sh still covers root and home deletes? This is the same kind of decision
   as hiding lessons (report:19).
3. **Block threshold.** Five clean TPs bounds FP below about 45% only. Keep 5/0 for warn but require
   more (for example about 29) for block?
4. **TTY-only approve and label.** This keeps the agent from approving its own proposals, but it
   means running those commands in a separate terminal, not through `!`. Acceptable?
5. **Subagents.** Step 0 counted 19 `rm_rf_nonscratch` events in subagent transcripts
   (RESULTS.md:175). Enforce in subagents too, or log them and exclude them from the primary?
6. **Lesson migration.** Apply the migration for `g-010` alone so step 3 has a real lesson, or
   wait for the full migration (662 rows in id_map.jsonl)?
7. **Stage 1 `-P` fix.** Add `-P` to the three Stage 1 hook scripts now as a separate fix, before
   Stage 2 starts?

## Decisions taken while drafting

1. `commit_without_push` is treated as **weak** (Step 0's own label, detectors.py:298), not strong
   as grouped in the brief. Weak detectors are capped at observe.
2. Two tiers: live (payload detectors only) and offline (authoritative, all detectors), because the
   transcript can lag (INSTALL.md:94-95). `exitplan_question_block` and `preclaim_audit_block` are
   signal-only and not compilable, since existing hooks already enforce them (RESULTS.md:248).
3. The compiler is deterministic, with a human-maintained lesson-to-detector map; no LLM drafts checks.
4. "A human moves the file" is done through `compile approve`, which needs a TTY and a matching
   approving review, and records a hash-chained approval. A bare `mv` is not loaded by the hook.
5. Citations are recorded on cards but flagged self-report and never used; exposure comes from
   `injections.jsonl`.
6. FP is operational from user-correction and target-in-prompt signals, with human labels final;
   only human-confirmed TPs count toward promotion.
7. Promotion uses a 14-day floor, not a fixed 14-day window, because Step 0 volumes cannot reach
   5 TPs in 14 days.
8. Demotion is automatic and lowering-only, through `demotions.jsonl`; raising always needs a human.
9. Live `edit_unread_session` uses `os.path.lexists` instead of the "File created" result text
    (unavailable at PreToolUse); the offline tier keeps the original rule.
10. Session-level finalization runs nightly; SessionEnd stays enqueue-only. Stop never blocks when `stop_hook_active`.
11. Effect reporting leads with repeat fires and evasion, since a hook cannot change a session's
    first attempt.
12. Hook-path writes hold no free text, so scrub-before-write holds by schema; cards and proposals are scanned.
13. The new hook uses `python3 -P`, and the same gap in Stage 1 hooks is raised as question 7
    rather than fixed (this task is read-only).

## Files read

| File | Lines |
|---|---|
| ace-playbook/README.md, LIMITATIONS.md, PREREGISTRATION.md, SECURITY.md | 103, 75, 88, 62 |
| ace-playbook/docs/reviews/2026-10-17-opus-stage1-review.md | 24 |
| ace-playbook/ace_playbook/config.py, enqueue.py, filelock.py | 129, 39, 22 |
| ace-playbook/ace_playbook/store/schema.py (full), store/log.py (lines 20-40, def list) | 262, 130 |
| ace-playbook/ace_playbook/inject/sanitize.py, citations.py, ledger.py, injector.py, __main__.py | 120, 26, 38, 140, 68 |
| ace-playbook/ace_playbook/reflect/provenance.py (full), reflect/runner.py (lines 20-60, def list) | 92, 192 |
| ace-playbook/ace_playbook/clean/transcript.py (lines 28-60, 84-134) | 189 |
| ace-playbook/ace_playbook/scrub/engine.py (lines 192-205, def list), scrub/quarantine.py (lines 20-60) | 230, 84 |
| ace-playbook/hooks/sessionstart.sh, stop_citation_ledger.sh, sessionend.sh | 5, 5, 6 |
| ace-playbook/hooks/INSTALL.md (grep, lines 80-105) | 158 |
| rebuild-lab/reports/ACE rebuild research round two.md | 85 |
| rebuild-lab/ace/step0/measure.py, detectors.py, scanlib.py, RESULTS.md | 346, 302, 266, 253 |
| rebuild-lab/ace/step0/tests/test_detectors.py (lines 1-230, test list) | 345 |
| rebuild-lab/ace/migration/id_map.jsonl (grep for g-010, line 96) | 662 |
