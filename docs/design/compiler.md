# ace-playbook Stage 2: the compiler in shadow mode (design)

Status: DRAFT, 2026-10-19; nothing built.
Reviewed 2026-10-19 (Opus design review applied).

Package `ace_playbook/compile/` in `~/ace-playbook` (Stage 1 at `5448d6d`). Unprefixed paths are in
that repo; `step0/` = `rebuild-lab/ace/step0/`; "report" =
`rebuild-lab/reports/ACE rebuild research round two.md`.

## 1. What Stage 2 is, in one paragraph

Stage 1 stores lessons, injects pinned ones and records which were shown (README.md:33-44), and
says plainly that the compiler is not built (README.md:46-47, LIMITATIONS.md:49-53). Stage 2 adds
a compiler that turns a well-evidenced lesson into a **spec-only JSON proposal** naming one
human-written detector from a fixed catalogue, validates it TRACE-style (parse, schema, unit
regressions, replay of the originating incident; report:25), routes it through an Opus review
that sees provenance and a human approval (report:51, SECURITY.md:45-49), and then evaluates
approved proposals live in **shadow mode** on an observe, warn, block ladder (report:25, 37).
The compiler never writes hooks, settings or skills. Counters come only from external signals,
never from agent self-tags (LIMITATIONS.md:34-36, report:37). Block mode is opt-in per proposal,
reachable only after the observe and warn ladder, and only on PreToolUse in Stage 2; it needed the
dated SECURITY/README/INSTALL amendments recorded under "Amendments" at the end.

## 2. Ground truth Stage 2 builds on

| Fact | Source |
|---|---|
| Stage-1 hooks fail open, exit 0, never block, no network; they do not `exec` Python and end in `exit 0`; they pass `-P` since `d22cc16` | SECURITY.md:36-37; inject/__main__.py:1-5, 61-64; hooks/sessionstart.sh:4-5, hooks/stop_citation_ledger.sh:4-5, hooks/sessionend.sh:5-6 |
| Ledgers are append-only JSONL, 0600, `sort_keys` | inject/ledger.py:11-17 |
| Exposure ledger (`injections.jsonl`) holds `shown_ids`, `holdout_ids` per session | inject/injector.py:125-134 |
| Citation ledger holds `[L-id]` strings the agent wrote (agent self-report) | inject/citations.py:11-25 |
| Inertness check to reuse for proposal messages; **not cheaply importable today** (see 7.2), so it moves to a leaf module in step 1 | inject/sanitize.py:108-120; store/schema.py:64-78 |
| Lesson evidence = `{quote, origin, session_id}`, live status needs user origin | store/schema.py:106-121, 143-144 |
| Quote matching is whitespace-collapsed, case-folded; user origins only | reflect/provenance.py:31-32, 65-92 |
| Scrubber: `scan(text, profile, deny_terms)`, `QuarantineRequired` | scrub/engine.py:192-203; scrub/quarantine.py:20-27 |
| Transcript file can lag the live conversation; Stop gets `last_assistant_message` | hooks/INSTALL.md:94-95 |
| SessionEnd default budget 1.5 s; SessionStart output cap 10,000 chars | hooks/INSTALL.md:86-90; report:33 |
| Stage 1 tests: 223 `def test` functions (`grep -c "def test" tests/*.py`, summed), 358 collected by pytest | tests/ |
| Migration applied 2026-10-19 (rebuild-lab `c0c8459`): 514 of 662 legacy lessons stored; `L-b3d1fba9` is pinned and active, but its evidence is self-quoted `legacy:` and must not count as user evidence | README.md:60-65 |

Step 0 measured 651 main sessions, 93 interactive, 97 with the ACE block, over 39 days
(RESULTS.md:9-18). Four strong detectors with a mapped lesson clear 2% of eligible sessions:
`edit_unread_session`, `exitplan_question_block`, `preclaim_audit_block`, `rm_rf_nonscratch`
(RESULTS.md:33). **`commit_without_push` is classified weak**, not strong (step0/detectors.py:298;
RESULTS.md:62), alongside `edit_unread_turn` and `rm_rf` (RESULTS.md:34). The verdict was
"NOT FEASIBLE AS INJECTED TODAY" (RESULTS.md:40), which is why the compiled path matters.

## 3. Package layout

| File | Role | Budget |
|---|---|---|
| `compile/events.py` | `Event`, `ToolUse`, `ToolResult` (port of step0/scanlib.py:24-52); two adapters: hook payload to Event, transcript record to Event (port of scanlib.py:80-126); input caps with a `truncated` mark | ~230 lines |
| `compile/shell.py` | `command_segments`, `git_subcommand` (scanlib.py:231-266), rewritten linear-time: one pass for quotes, `$'..'`, comments and heredocs (no heredoc for `<<<`, `\<<`, `<<` inside `((`, or after a comment), at most 32 heredoc openers, `&`, braces and shell keywords split or strip, leading redirections and wrappers with their options are skipped | ~180 |
| `compile/detectors.py` | Pure detector functions, `Fire`, `advance` (the state transition) (section 4) | ~230 |
| `compile/catalogue.py` | `CATALOGUE` registry (`Detector` rows with strength, tier, consumes, state, compilable, lessons), `evaluate` (strict mode, error list), `source_sha256` over the full source of events, shell, textrules, detectors and catalogue | ~100 |
| `compile/state.py` | Rolling per-session state file under `data_dir/shadow_state/`, locked (`LOCK_NB`), atomic, hashed name, 0600/0700, FIFO- and symlink-safe | ~235 |
| `compile/evidence.py` | Evidence cards (section 5): admission, legacy path, scrub, `cards/` writer | 300 (built, step 2) |
| `compile/proposal.py` | Proposal schema, ordered validation, replay/regression runner, writer (section 6) | 299 (built, step 2) |
| `compile/shadow.py` | Hook entry point: load approved proposals, re-validate, evaluate, append `shadow.jsonl` rows, print the decision as JSON on stdout (block mode only); always returns 0 (section 7) | ~220; 218 (built, step 4) |
| `compile/offline.py` | Nightly pass: run signal detectors over finished transcripts, finalize session-level detectors, label pre-fill, demotions | ~250 |
| `compile/report.py` | shadow.jsonl to results tables, effect estimates (section 8) | ~260 |
| `compile/__main__.py` | CLI: `cards`, `propose`, `validate` (step 2); `approve --mode observe`, `unapprove`, `shadow pre\|stop` (step 4); later `packet`, `review-record`, `label`, `report`, `replay-synthetic` and `approve --mode warn\|block` | 98, then 174 (step 4), ~230 when complete |
| `ace_playbook/textrules.py` | Leaf module (stdlib `re`, `string` only): `unsafe_text_reason`, `inert_reason` and their regexes, moved out of `store/schema.py` and `inject/sanitize.py`, which re-export them (step 1, see 7.2) | ~120 moved |
| `hooks/shadow.sh` | One script, two registrations (PreToolUse, Stop); never `exec`s, always `exit 0` | 19 lines (built, step 4; a test pins it to the block in section 7) |

Data lives under the existing `data_dir` (config.py:20, 69-70), never under `~/.claude`
(config.py:1): `detector_events.jsonl`, `cards/` (one `E-xxxxxxxx.json` per card), `proposals/`, `proposals/reviews/`,
`proposals/approved/`, `approvals.jsonl`, `demotions.jsonl`, `labels.jsonl`, `shadow.jsonl`,
`shadow.synthetic.jsonl`, `shadow_state/`. Frozen CI fixtures live in `ace_playbook/fixtures/frozen/` (the seed-1 corpus that
`generate.py` writes) and `ace_playbook/fixtures/parity/` (`step0_parity.json`, which the generator does not write).

**Step 2 as built (deviations, one line each).**
- Step 2 also delivers `proposal.py`, `propose` and `validate` (the build-order table puts them in step 3); approve, packet, review-record and the hash chain stay in step 3.
- Cards are one 0600 file each under `data_dir/cards/` (not `evidence/cards.jsonl`), and `payload_ref` embeds the minimized `{tool_name, tool_input}` (scrubbed, masked on a hit) instead of pointing at `evidence/payloads/`.
- `quote_offsets` items are `{user_turn, start, end, quote_sha256}`: the ordinal among user messages and offsets into the whitespace-collapsed, case-folded text (no quote text is stored).
- `evidence.py` copies `norm` and a JSONL reader instead of importing `reflect.provenance` and `inject.ledger`, whose package inits pull in the HTTP client and `keys`; a test pins `norm` equal to the original.
- The reflections ledger and `detector_events.jsonl` are not read yet: mapped fires are recomputed from the transcript (the nightly writer is step 5); a fire needs a minimizable payload under 8 KiB.
- Legacy labels (`labels.jsonl`, written by `compile label` in a later step) are verified as a `store.schema.record_hash` chain from `GENESIS`; the label `ref` is the would-be card id, which `cards` prints for every unlabelled legacy candidate.
- The proposal schema is `check {detector, params, on_truncated}` and `provenance {cards, lesson_evidence, source_sha256}` (no separate `detector_sha256`); `P-` ids hash lesson, event, matcher, detector, params, cards and `source_sha256`, not message or clock.
- Hashes in a proposal stay full length; the scrub step scans every string except fields step 1 proved to be a bare digest or timestamp, because the LESSON lint flags any hex run over 12 characters and any ISO time.
- Regression and replay examples are single payloads run on a fresh state (`ace_playbook/fixtures/compile/examples.json`); only `rm_rf_nonscratch` and `rm_rf` have examples and message templates so far.

**Step 4 as built (deviations, one line each).**
- `compile approve` exists in minimal form: `--mode observe` only (warn and block exit 2 pointing at the ladder), TTY only, no review record and no `approvals.jsonl` hash chain yet (both step 3 work). The approved copy `proposals/approved/<id>.json` is the proposal plus `reviewed_by: "operator-tty"`, `approved_at`, `mode` and `proposal_sha256`, a hash over every other field: it catches an edit made after approval, not a deliberate edit that recomputes the hash, so the file is the trust root until the chain lands.
- The hook loads a record only if the hash matches, `mode` is in `shadow.ALLOWED_MODES` (`("observe",)`, widened only by tests, so a hand-edited `mode: "block"` is skipped), and `proposal.validate` passes on the body (all six steps, replay included) on every call. It measures about 1 ms in-process and 40 ms per wrapper call (p95 42 ms on 3.14, 122 ms on the system 3.9).
- A `shadow.jsonl` row is `{ts, proposal_id, lesson_id, mode, event, tool_name, session, payload_sha256, would_fire, tier}`: `session` is the bare `sha256[:16]` of the session id (the section 7.1 example's `sid:` prefix, `v`, `row_id`, `corpus`, `arm`, `hits`, `dur_ms` and `target_hashes` are not written yet); `payload_sha256` is `evidence.payload_hash` of `{tool_name, tool_input}`, so it joins to an evidence card.
- With no approved proposal matching the event and tool, the hook exits before touching `shadow_state/`, so the Stop registration does not reset per-turn state yet (only `edit_unread_turn` needs it and it cannot be approved). No kill-switch config key; `proposals/DISABLED` is the switch. No arm, no `demotions.jsonl`, no `approvals.jsonl` check.
- When the state lock is skipped or the state directory is unusable, detectors with `state == "none"` still run on a blank state and stateful ones stay silent.
- `python -m ace_playbook.compile.shadow pre` and `python -m ace_playbook.compile shadow pre` both work; the wrapper imports `ace_playbook.compile.shadow.main` directly.

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
| `rm_rf_nonscratch` | strong, 7/97 = 7.2%, 9 events (RESULTS.md:52) | live + offline | PreToolUse `Bash` | none | some segment of `tool_input.command` is `rm` with recursive and force flags and a target (detectors.py:52-59), and at least one target fails the `SCRATCH` regex (detectors.py:38-40, 115-119); each target is `normpath`ed first and a temp root must end at `/` or end of string, so `/tmp/../x` and `/tmpfoo` are not scratch (a deliberate deviation from Step 0, which had no boundary; `/bin/rm`, `\rm` and the wrappers env, nice, xargs, doas, busybox, command, exec, sudo count too) | yes, up to block |
| `rm_rf` | weak, 8/97 = 8.2% (RESULTS.md:51) | live + offline | PreToolUse `Bash` | none | same, any target (detectors.py:115-117) | observe only |
| `edit_unread_session` | strong, 11/97 = 11.3%, 15 events (RESULTS.md:49) | live + offline | PreToolUse `Read`, `Edit`, `Write`, `MultiEdit`, `NotebookEdit`; offline also attachments `file`, `edited_text_file`, `compact_file_reference` (scanlib.py:94-96) | set of sha256[:16] path hashes read or written this session, capped at 5,000 | Edit/Write/MultiEdit on a path not in the set, basename not in `AUTOLOADED`, not under `/.claude/plans/` (detectors.py:45, 79-90); offline: tool result not an error and not "file created" (detectors.py:100-107); live: path exists (`os.path.lexists`) | observe only until a labelled sample shows true positives (below), then up to warn; block only through the 7.3 warn-to-block rule |
| `edit_unread_turn` | weak, 13/97 = 13.4% (RESULTS.md:50) | live + offline | as above, plus Stop (turn reset) | per-turn set, cleared at each human prompt (detectors.py:74-75); live clears at Stop | as above, against the turn set | observe only |
| `exitplan_question_block` | strong, 10/97 = 10.3%, 16 events (RESULTS.md:57) | offline only | transcript tool_result text | none | `HOOK_ERR` match naming `pretooluse-exitplan-question-check.sh` (detectors.py:26-27, 146-156) | no: already a hook; outcome counter and evidence only |
| `preclaim_audit_block` | strong, 60/97 = 61.9%, 269 events (RESULTS.md:55) | offline only | transcript user record | none | text starts "Stop hook feedback" and the bracket names `stop-mid-task-audit.sh` (detectors.py:28, 173-186, 202-203) | no: already a hook; double-counts when both variants fire (detectors.py:265) |
| `commit_without_push` | weak, 3/97 = 3.1% (RESULTS.md:62) | live state + offline finalize | PreToolUse `Bash`; finalized at session end | counts of `git commit`, `git push` via `git_subcommand` (detectors.py:122-132) | session ended with commit > 0 and push == 0 (detectors.py:142-143) | observe only (session-level; no tool call to gate) |
| `user_correction` | weak (detectors.py:274-275) | offline only | human prompt, <= 800 chars | none | `CORRECTION` regex (detectors.py:29-34, 225-230) | no: an FP/TP signal (section 7.3), never a check |

`NotebookEdit` registers its path as read in the live tier only; offline it is ignored, as in Step 0. The live tier resets its turn set at
Stop, never at UserPromptSubmit (only PreToolUse and Stop are registered). A ninth row, `unparseable` (weak, observe, live + offline),
fires when an input cap cut a command or path, so a long-padding evasion is visible instead of silent.

**Why `edit_unread_session` starts observe-only.** The harness itself rejects an Edit of a file the
agent has not read, so an Edit that *succeeded* offline most likely means the parser missed a read
(a harness-loaded attachment form, a path spelled differently, a read inside a sidechain), not that
the agent edited unread. Step 0 counted 15 such events (RESULTS.md:49) without labelling them. The
detector therefore stays observe-only until a human-labelled sample (7.3) shows true positives; the
labeller must check the raw transcript for a missed read before confirming a TP. If the sample
shows mostly parser gaps, the fix is the parser, and the detector is demoted to a signal. No
compilable detector reads Stop: `edit_unread_turn` uses Stop only to reset its turn set and is
observe-only, so Stop appears in the tables below as a state-reset registration, never a block.

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

`step0/detectors.py` lives in the private rebuild-lab repo, so CI cannot import it. The parity
test instead runs the ported functions over the synthetic session scenarios of test_detectors.py
(rebuilt as fixtures) and asserts the counts frozen in
`ace_playbook/fixtures/parity/step0_parity.json`: one entry per scenario and detector, a `deviations` section listing every
scenario where the port deliberately differs from Step 0 (step0 count, port count, reason; never changed silently), plus
`step0_commit` (the rebuild-lab commit the counts were generated at) and the generator's
`source_sha256`. The operator regenerates the JSON by hand from a rebuild-lab checkout when Step 0
changes; a separate local-only check recomputes it from `step0/detectors.py` and fails if the file is
stale. CI never needs rebuild-lab, and the port still cannot drift from the numbers in RESULTS.md.

### 4.2 Per-session state (`state.py`)

```json
{"v": 1, "session_hash": "9c1e0a7b44d2f310", "created": "2026-10-20T09:01:12Z",
 "seen_paths": ["4be1c0f2a9d8e771", "0d93aa61c2b4f5e0"], "turn_paths": ["0d93aa61c2b4f5e0"],
 "git": {"commit": 1, "push": 0}, "events": 37, "truncated": false}
```

File name `shadow_state/<sha256(session_id)[:16]>.json`, so a hostile session id cannot traverse
paths. Paths are stored as sha256[:16], never as text (the Step 0 rule of storing no transcript
text, scanlib.py:1-5). Parallel tool calls can run hooks concurrently, but `filelock.locked`
(filelock.py:12-22) blocks on `LOCK_EX` and a stuck holder would hang the hook until its 5 s
timeout. `state.py` therefore takes `fcntl.flock(fd, LOCK_EX | LOCK_NB)` with up to three tries
10 ms apart; if still locked it skips that update, logs `skipped:locked` (type only) and exits 0,
failing toward silence. `filelock.py` is left unchanged. Writes are write-to-temp then
`os.replace`. Over 5,000 entries sets `truncated: true` and the detector stops firing for that
session (fail toward silence). The nightly pass deletes state files seven days after the
session's last event.

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

Admission rules, standard path (all must hold, else no card): the lesson is `active` or
`provisional` with user evidence; the evidence quote is re-located, with `provenance.norm`
(provenance.py:31-32), inside a *user message* of the originating session's cleaned transcript,
with compact summaries and
sidechains dropped as in clean/transcript.py:116-122; at least one detector event mapped to the
lesson exists. The incident is the first mapped detector event in the originating session, or if
there is none, the first one in a later session where the lesson was eligible (card field
`incident_kind: "originating" | "later"`).

**Legacy path.** A migrated lesson carries evidence with origin `user_file`, a quote equal to its
own text and a `legacy:` session id (README.md:60-65), so the rule "quote found in a user message
of the originating session" can never hold for it, and legacy evidence must never count as user
evidence. That includes `L-b3d1fba9`, the lesson the first proposal needs. A legacy lesson gets a
card only if both hold: (a) a detector event mapped to it occurs in a *later* session where the
lesson was eligible (shown or held out in `injections.jsonl`), and (b) a human has labelled that
event an offline incident with `compile label --incident` (TTY only; the label row is the only
evidence). The card is `provenance: "legacy"`, `incident_kind: "later"`, `origin_session: null`,
`quote_offsets: []`, with `label_ref` naming the label row. Packets and `approve` show a banner
for any proposal citing a legacy card ("evidence is self-quoted legacy text; the human label is
the only evidence") and `approve` asks a second explicit confirmation. The other route is to
re-mint the lesson from real user evidence (the reflector or the operator writes a new lesson
from a session where the user actually said it); that lesson takes the standard path and supersedes
`L-b3d1fba9` (open question 6). Until one of the two happens, step 3 runs on a fixture lesson.

The example below is the legacy card for the first proposal (a standard card has `origin_session`
and non-empty `quote_offsets` instead of `label_ref`):

```json
{"id": "E-71c2d9a0", "v": 1, "lesson_id": "L-b3d1fba9", "provenance": "legacy",
 "origin_session": null, "incident_session": "sid:5f0c9e21aa03b7d4",
 "incident_kind": "later", "label_ref": "lab:2c70e1a4",
 "quote_offsets": [],
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
   Stop}, and `Stop` only with mode `observe` (no compilable detector may block at Stop in
   Stage 2); `matcher` from the detector's `consumes`; `mode` must be `observe` at creation;
   `detector` in the catalogue and `compilable`; `params` validated by the detector's own schema.
2. **Message**: 20 to 300 chars, `inert_reason(message)` (from `ace_playbook/textrules.py`, moved
   out of sanitize.py:108-120) must return None, and the message must start with the literal
   `ACE <id>:` so a user can always trace a warning to its file. A message never interpolates
   payload fields.
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
pass again on current code. For `--mode warn` and `--mode block` it also prints every counted TP
and every FP (time, session hash, fire row id, label time, who wrote it) and requires the human to
confirm the list at the TTY (7.3, label forgery). It then moves the file into
`proposals/approved/` and appends a hash-chained record to `approvals.jsonl`:

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
# Python prints a decision as JSON on stdout. This script never exec's Python and ALWAYS exits 0,
# because exit 2 means "block" to the harness and an interpreter error must never block.
# Python runs isolated, mirroring hooks/pre-commit-scrub.sh:12-18: -I (with -P where it exists)
# and a cd into $ACE_HOME, so a json.py in the project's working directory is never imported.
ACE_HOME="${ACE_PLAYBOOK_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
[ -n "$ACE_HOME" ] || exit 0
PY="${ACE_PYTHON:-python3}"
if "$PY" -I -P -c pass 2>/dev/null; then
  ISOLATE=(-I -P)
else
  ISOLATE=(-I)  # Python < 3.11 has no -P (nor PYTHONSAFEPATH); -I already drops cwd and script dir
fi
cd "$ACE_HOME" || exit 0
"$PY" "${ISOLATE[@]}" -c 'import sys; sys.path.insert(0, sys.argv[1]); from ace_playbook.compile.shadow import main; sys.exit(main(sys.argv[2:]))' \
  "$ACE_HOME" "$1"
exit 0
```

**Why no `exec` and no exit-code decisions.** With `exec`, the interpreter's exit status would be
the hook's, and exit 2 means "block". The system `/usr/bin/python3` (3.9.6) prints `Unknown
option: -P` and exits 2, and an argparse usage error (no argument) also exits 2: every Bash, Read,
Edit and Write call and every Stop would be blocked, and Stop could not break out through
`stop_hook_active` because no payload is ever read. So blocking is expressed only as JSON on
stdout with the Python and wrapper exit codes both 0: PreToolUse
`{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
"permissionDecisionReason": "<stored message>"}}` and, for a future Stop detector,
`{"decision": "block", "reason": "<stored message>"}`. A malformed or missing JSON line means
"allow". The isolation is copied from hooks/pre-commit-scrub.sh:12-18: probe `"$PY" -I -P -c
pass`, use `-I -P` where it works and plain `-I` otherwise, `cd "$ACE_HOME"`, and put `ACE_HOME`
on `sys.path` by hand in the `-c` string (`-I` ignores `PYTHONPATH`). `PYTHONSAFEPATH` is **not** a
fallback: it arrived in Python 3.11 with `-P`, so on 3.9 it does nothing and the working directory
would stay first on `sys.path`; `-I` is what protects old interpreters. `main(argv)` in
`compile/shadow.py` catches everything and returns 0. If `ACE_HOME` comes out empty the wrapper
exits 0 before touching Python, so no empty `PYTHONPATH` entry can mean "working directory".
`-P` stops Python prepending the working directory to `sys.path`; it was added to the three Stage 1
hooks in `d22cc16`
(hooks/sessionstart.sh:4, hooks/stop_citation_ledger.sh:4, hooks/sessionend.sh:5), closing the
exposure of the same class as the pre-commit shadowing finding
(docs/reviews/2026-10-17-opus-stage1-review.md:24). Those Stage 1 scripts also never `exec` and end
in `exit 0`; with `ACE_PYTHON=/usr/bin/python3` they lose function silently but cannot block.

Registration (documented in INSTALL.md, never installed by code): PreToolUse matcher
`Bash|Read|Edit|Write|MultiEdit|NotebookEdit`, `timeout: 5`, `onFailure: "continue"`; Stop with
the same script and arg `stop`, alongside the existing citation hook. In Stage 2 the Stop
registration only resets per-turn state and logs observe rows.

### 7.1 Evaluation

1. Read stdin (cap 256 KiB; larger means log `skipped:oversize`, exit 0). Require a non-empty
   string `session_id`, as inject/__main__.py:28-30 does.
2. Kill switch: `data_dir/proposals/DISABLED` exists, or config `shadow_enabled` false, then exit 0.
3. Load approved proposals: at most 50 files, each at most 16 KiB, regular files only (no
   symlinks), hash must match `approvals.jsonl`, message re-checked with `inert_reason`
   (`textrules`). Any failure skips that proposal and logs the reason by type only (as inject/__main__.py:18-25).
4. Effective mode = min(approved mode, any cap in `demotions.jsonl`, arm). The arm comes from
   `coin(shadow_seed, session_id, proposal_id) < arm_rate` (injector.py:35-38) during a randomized
   phase; outside one the arm is always "active".
5. Build the Event, run each matching detector with the session state, update state.
6. Append one row per evaluated proposal to `shadow.jsonl` (below), then act on the strongest
   decision.

| Mode | On fire | Output (Python and wrapper exit are 0 in every row) | Notes |
|---|---|---|---|
| observe | append `would_fire` row | none | the agent sees nothing |
| warn | row + one stderr line: the stored message | stderr only | see open question 1 on whether the model sees it |
| block (PreToolUse) | row + `permissionDecision: "deny"` JSON carrying the stored message | stdout JSON | tool call is not run |
| block (Stop) | not reachable in Stage 2 (validation rejects it) | none | the guard stays specified for a later Stop detector: emit nothing when `stop_hook_active` is true, which prevents a loop |
| any error | type-only log line | none | fail open; a broken store never blocks a session |

```json
{"ts": "2026-10-21T10:12:04Z", "v": 1, "row_id": "r:8d41c07e", "corpus": "private", "session": "sid:5f0c9e21aa03b7d4",
 "proposal_id": "P-3f9a0c12", "detector": "rm_rf_nonscratch", "tier": "live", "event": "PreToolUse",
 "mode": "observe", "arm": "active", "decision": "would_fire", "hits": 1,
 "payload_sha256": "c4a1...e9", "target_hashes": ["0d93aa61c2b4f5e0"], "agent_id_present": false,
 "dur_ms": 41}
```

`row_id` is sha256(ts, session, proposal_id, payload_sha256)[:8]; `approve` and the report list
fires by it. Rows hold only enums, ids, hashes, integers and timestamps. There is no free-text
field, so there is nothing for the scrubber to miss on the hot path; a test asserts the row schema.

### 7.2 Cost budget

The hook adds a Python start to every matched tool call. Imports are limited to `json`, `os`,
`re`, `string`, `hashlib`, `fcntl`, `time`, the stdlib-only `ace_playbook.config` and the leaf
`ace_playbook.textrules`; no `sqlite3`, no `subprocess`, no scrub engine, no store.

**The import chain today fails this budget.** `uv run python -X importtime -c "import
ace_playbook.inject.sanitize" 2>&1 | grep -E "subprocess|sqlite3"` (2026-10-19) shows `subprocess`
(via `ace_playbook.keys.resolver`, imported by `inject/injector.py`) and `sqlite3` (via
`ace_playbook.store.view`, imported by `store/__init__.py` through `store.log`), plus the scrub
engine; `ace_playbook.inject.sanitize` costs about 27 ms cumulative because importing it first runs
`inject/__init__` (injector) and `from ..store.schema import ...` first runs `store/__init__`. So
importing `inert_reason` from `inject.sanitize` or `unsafe_text_reason` from `store.schema` would
break both the budget and the "no subprocess" import test of section 9. **Step 1 therefore moves
`unsafe_text_reason` (store/schema.py:64-78, with `FENCE`) and everything `inert_reason` needs
(`KNOWN_FILES`, the regexes and helpers, sanitize.py:15-120) into the leaf module
`ace_playbook/textrules.py`** (stdlib `re`, `string` only). `store/schema.py` and
`inject/sanitize.py` re-export the same names, so Stage 1 imports and tests are unchanged;
`inject/__init__.py` is untouched. Lazy `__init__`s (PEP 562) were rejected as more fragile.

Target p95 below 100 ms measured from `dur_ms`; above 200 ms over seven days disables the hook (7.3). Nothing is added to
SessionStart (the 7,000-char block and 10,000 cap are untouched, config.py:15) or SessionEnd, which
keeps only its 1.5 s enqueue (enqueue.py:1-5). Session-level finalization (`commit_without_push`)
runs in the nightly pass from the state file, not in SessionEnd.

### 7.3 True positive, false positive, promotion, demotion

**Labels.** Every live fire gets a label. Precedence: a human label in `labels.jsonl` (written by
`compile label`, TTY only) beats everything. A label row holds `{ref, label: tp|fp|incident,
reason_code, at, prev_hash, hash}` (`ref` is a `row_id` or a detector event id), hash-chained like
`approvals.jsonl`. Before a human label exists, the nightly pass pre-fills:

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
| observe to warn | strong detectors only; `edit_unread_session` additionally needs a labelled sample in which each TP was checked against the raw transcript for a parser-missed read | at least 14 days in observe; at least 5 human-confirmed TPs and 0 FPs since entering observe; replay passes on current code; lesson still `active` | human `approve --mode warn`: the CLI checks the rule and lists every counted TP for TTY confirmation (below) |
| warn to block | strong detectors whose catalogue row allows block, PreToolUse only | the same counts again, fresh, in warn; a new Opus review of the warn-period data; evasion candidates reviewed | human `approve --mode block`, with the same TP listing |
| any mode to disabled | always | lesson leaves `active`; detector `source_sha256` changes; hook error rate above 1% or p95 above 200 ms over seven days | nightly pass (lowering only) |

**Demotion** is automatic and only ever lowers a mode. The nightly pass appends to
`demotions.jsonl` (each record names the label or metric that caused it): block to warn on one FP;
warn to observe on one FP; two FPs within 30 days in any mode to observe, with re-review required.
Counters reset after any demotion. Two evasion candidates in 14 days (after a block, a different
command touching the same target hash within the next five tool calls, which is the
unanticipated-path failure in report:45) flags the proposal for review without demoting it.
Known evasion shapes are counted as candidates offline: a deleted target that reaches a temp root only
through a symlink or an unresolved `$VAR`, a `bash -c` body (invisible to the tokenizer,
detectors.py:255), and the same target reached through a symlink. Out of scope for the step 1
tokenizer and recorded here as further candidates: `find ... -delete`, `find ... -exec rm -rf`, `xargs` fed from a pipe, `python -c` with `shutil.rmtree`, shell functions or aliases
wrapping `rm`, `xargs rm -rf` whose targets arrive on stdin (the targets are invisible; only the wrapper is stripped), `bash -c '...'`, `eval` and
`bash <<EOF` bodies (invisible to the tokenizer), and anything past the 64 KB input cap (reported as `unparseable`).
Handled since the step 1 review waves, with parity deviations recorded in `step0_parity.json`: a redirection before the command
word (`>/dev/null rm -rf x`), wrappers with options (`sudo -u root rm`, `timeout 5 rm`), backslash and `$'..'` obfuscation of the
command word, and heredoc look-alikes (`# <<EOF`, `$((1<<2))`, `\<<EOF`) that used to hide the lines after them.

The scanner is best-effort and linear, not a shell parser. Bash parsing has more corners than any tokenizer
here covers (quoting inside `$(...)`, aliases, functions, `eval`, here-document delimiters built at run time), so
differentials between what the scanner sees and what bash runs will exist beyond the ones found so far. Every
differential found in review is pinned as a parity extra in `step0_parity.json` (its Step 0 count, its port count, the
reason) and as a unit test in `tests/test_compile_shell.py`, so none can regress silently. For the ones not yet found
the control is the ladder itself: a proposal starts in observe, reaches warn only on human-confirmed true positives,
reaches block only after a fresh warn period and a human reading every counted TP, and every evasion candidate
(including `unparseable`) is reviewed before any promotion.

**Label forgery.** `labels.jsonl` drives promotion and the agent, running as the user, can append
to files in `data_dir`: a forged TP label is self-report by another route. TTY-only `compile label`
does not stop an append by the agent; it only makes the honest path human. The control is at
promotion: `compile approve --mode warn|block` prints **every counted TP** (fire time, session
hash, `row_id`, label time, label chain position) and every FP, re-derives each from the
`shadow.jsonl` row, refuses any label whose chain link is broken or that has no matching fire row,
and requires the human at the TTY to confirm the list before it records the mode change. Labels
written after the proposal's last approval record are flagged. This makes forgery visible to the one
person who decides, not impossible (residual risks, section 9).

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

*Caveat on spillover.* The reflector reads finished transcripts, including sessions that contain a
warn or block message; a lesson minted from such a session is injected in later sessions of
**both** arms (lessons are not arm-specific), so the check's effect can reach the "without" arm
across sessions and bias the contrast toward zero. The report therefore flags every lesson whose
originating session holds an `ACE P-` message and reports the arm comparison separately for
sessions where such a lesson was shown, so the bias is visible rather than absorbed. Stripping
`ACE P-` lines in `clean/` would remove the channel but is a Stage 1 change outside this design.

**Pre-registration additions.** PREREGISTRATION.md has TBD slots (lines 4, 24, 32, 34, 52); filling
a slot before the `prereg-v1` tag is completing the document, not amending it. Items with no slot go
in a new dated "Shadow-mode additions" section added before the tag; sealed text is never edited
after it (PREREGISTRATION.md:3-7). The additions: the `shadow_seed` sha256 (seed private, as
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
| Fail-closed message sanitiser | `inert_reason` (from `textrules.py`) at propose, at approve, and again at hook load; plus 300-char cap and the `ACE <id>:` prefix; failure means the proposal is skipped, never rewritten | probes from the Stage 1 review (review.md:8) as messages: URL, bare domain, backtick, "Ignore the previous guidance", settings wording |
| Wrapper cannot block by accident | shadow.sh never `exec`s, always `exit 0`; blocks are `permissionDecision: "deny"` JSON on stdout only | run the wrapper with `ACE_PYTHON=/usr/bin/python3` (3.9.6, `-P` unknown), with no argument, with a nonexistent interpreter and with a crashing module: exit 0 every time, stdout empty or valid decision JSON; with `ACE_PYTHON=/usr/bin/python3` a planted `json.py` in the working directory is not imported |
| No shell interpolation | shadow.sh passes stdin untouched and `"$1"` is a literal from settings; Python never calls `subprocess`, `os.system` or `eval`; detectors tokenize by regex (scanlib.py:241-254) and never execute or expand | grep test over `compile/` for those names; shell test with a payload holding `$(...)` and backticks |
| Payload never echoed | stderr carries only the stored message, never the command or path | test that no payload substring reaches stderr |
| Path containment | proposal ids by regex; resolved paths must sit under `data_dir/proposals[/approved]`; symlinks refused; state file names are hashes | traversal ids, symlinked approved file |
| No network in the hook path | `compile.shadow` imports no `urllib`, `http`, `socket`, `ssl`, `subprocess`, `sqlite3` | run `python3 -I -c` importing the module and assert none is in `sys.modules`; fails until the step 1 `textrules` move |
| Scrub before write | cards and proposals through `scan(..., LESSON, deny_terms)`, hit means quarantine (quarantine.py:20-27); shadow rows and state hold no free text by schema | planted FAKE token in a fixture replay payload |
| Tamper evidence | hash-chained `approvals.jsonl` and `labels.jsonl`, TTY-only approve and label; hook loads only hash-matching files; report flags approved files whose hash or mtime changed without a ledger record | edited approved file is ignored |
| Label forgery | `approve --mode warn|block` lists every counted TP and FP for TTY confirmation, re-derived from `shadow.jsonl`; broken chain or missing fire row refuses | forged label without a fire row; broken chain link; label appended after the last approval |
| Bounded cost | input, file count, file size and state caps; oversize means skip | oversize fixtures |

Residual risks, stated plainly: the agent runs with the user's permissions and can edit
`data_dir`, so it could append a demotion or delete an approved file to evade a block; the controls
above make that visible in the report, not impossible. The same holds for **label forgery**: the
agent can append a `tp` label, so promotion rests on the human reading the TP listing at `approve`,
not on the file. Block mode is the first time ace-playbook hooks can stop the agent, which
contradicted SECURITY.md:36 ("Hooks never block a session"), the "hooks fail open and stay out of
the way" wording at README.md:79 and the `onFailure` paragraph at hooks/INSTALL.md:96-97. All three
were amended on 2026-10-19 (see Amendments): stage-1 hooks never block; stage-2 shadow hooks block
only a proposal the operator promoted to block mode after the observe and warn ladder.

## 10. Build order

| Step | Deliverable | New tests |
|---|---|---|
| 1 | `textrules.py` move (prerequisite, 7.2); `events.py`, `detectors.py`, `state.py`: 8 catalogue detectors, both adapters, parity with the frozen step0 fixture JSON | 47: 8 detectors x 4 fixtures (32), 6 adapter, 5 state (incl. `LOCK_NB` skip), 2 parity (frozen JSON, stale-file check), 2 textrules (re-exports identical, import hygiene: no `subprocess`/`sqlite3`) |
| 2 | `evidence.py` + `compile cards` | 15: admission rules, quote only in tool output rejected, self-report-only refused, scrub hit masks and sets non-replayable, idempotent ids, offsets within user messages; legacy path: `legacy:` evidence refused without a human incident label, admitted with one and flagged `provenance: legacy`, re-minted lesson takes the standard path |
| 3 | `proposal.py` + first proposal for `rm_rf_nonscratch` (lesson g-010, mapped to `L-b3d1fba9`, ace/migration/id_map.jsonl:96, migration applied 2026-10-19); the real proposal needs the legacy card or a re-minted lesson (section 5) | 18: schema, 5 sanitiser rejects, replay must fire, regressions must not, scrub, containment, approve refuses without TTY or review, hash chain, legacy-card banner and second confirmation, `Stop` with mode above observe rejected, end-to-end under a temp HOME proving nothing outside `data_dir` changes |
| 4 | `shadow.py`, `hooks/shadow.sh`, INSTALL.md section; the operator installs it by hand in observe mode (settings writes are hook-guarded in this setup) | 26: three modes and their stdout/stderr output, deny and block JSON shape, `stop_hook_active`, malformed or empty stdin, missing session id, kill switch, unapproved or hash-mismatched or symlinked proposal ignored, oversize skip, no-network imports, no payload text in rows or stderr, arm determinism, shell script inspection (no `exec`), 7 wrapper tests (`ACE_PYTHON=/usr/bin/python3`, no argument, nonexistent interpreter, crashing module, stdout empty-or-valid-JSON, exit 0 always, and under `ACE_PYTHON=/usr/bin/python3` a planted `json.py` in the working directory is not imported), locked state file skipped within 50 ms, `edit_unread_session` capped at observe until labelled |
| 5 | `offline.py` + `report.py`: nightly signal detectors, label pre-fill, demotions, two tables | 25: auto-FP rules (a) correction after a message, (b) target in the last prompt, (c) shell-mode rerun (3); auto-TP (a) existing hook blocked, (b) correction after an observe fire (2); unlabeled counts neither side (1); agent self-tags and citations never inputs (1); human label precedence (1); evasion candidates flag without demoting, and the `/tmp/..` shape (2); disable triggers: lesson leaves `active`, `source_sha256` change, error rate or p95 (3); demotions: block to warn on one FP, warn to observe on one FP, two FPs in 30 days to observe with re-review (3), counters reset after a demotion (1); promotion rule check and TP listing with forged-label refusal (3); randomisation inference: seeded permutations deterministic, rate ratio and Wilson per arm (2); corpus mixing refused (1); synthetic and private tables separate (1); state files deleted seven days after the last event (1) |
| 6 | One week of observe-mode measurement | none; output is the report with every fire labeled, live versus offline agreement, p95 cost, and a written go or no-go for the seal |

Total about 131 new tests over the existing 223 test functions (358 collected by pytest). Steps 1
and 2 use synthetic fixture lessons. The migration was applied on 2026-10-19 (rebuild-lab
`c0c8459`: 514 lessons stored; `L-b3d1fba9` is pinned and active), so the lesson exists, but its
evidence is self-quoted `legacy:` text and cannot admit a standard card. Step 3's *real* proposal
therefore needs either the legacy path (a human-labelled incident in a later eligible session) or a
lesson re-minted from real user evidence (section 5, open question 6); until one of them exists,
step 3 is built and tested against a fixture lesson.

**Not built in Stage 2:** no auto-install of hooks or edits to settings, CLAUDE.md, skills or
`~/.claude`; no self-modifying hooks (the evaluator never edits proposals, approvals or its own
code; the nightly pass may only lower a mode); no LLM anywhere in the hook path or in proposal
generation (the only model is the out-of-band Opus reviewer); no CLAUDE.md-line proposals,
deletion proposals or path-scoped rules (report:69, a later stage); no judge; no `UserPromptSubmit`
reminders; no cross-session learning by the hook; no block mode during the step 6 week.

## 11. Open questions for the operator

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
6. **First lesson's evidence.** The migration was applied on 2026-10-19 (rebuild-lab `c0c8459`) and
   `L-b3d1fba9` is pinned and active, but its evidence is self-quoted `legacy:` text, which must not
   count as user evidence (README.md:60-65). Take the legacy path (a card only after you label a
   later offline incident, with the banner and double confirmation), or re-mint the lesson from a
   session where you actually said it and let the standard path apply?

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

Decisions added by the 2026-10-19 review (R1 to R7):

- R1. The wrapper never `exec`s and always exits 0; Python emits block decisions as JSON on stdout,
  because the interpreter's exit 2 (unknown option, usage error) would otherwise block every call.
  The `-P` gap in the Stage 1 hooks was fixed in `d22cc16`, so it is no longer an open question. The
  wrapper copies the isolation of hooks/pre-commit-scrub.sh:12-18 (`-I`, `-P` when supported, `cd`
  into `ACE_HOME`, `sys.path.insert` in a `-c` string); `PYTHONSAFEPATH` is 3.11+ like `-P` and is
  not used as a fallback. An empty `ACE_HOME` exits 0.
- R2. `unsafe_text_reason` and `inert_reason` move to the leaf module `ace_playbook/textrules.py` in
  step 1, with re-exports, because the current import chain pulls `subprocess` and `sqlite3`.
- R3. Legacy-evidence lessons get cards only through a human-labelled later incident
  (`provenance: legacy`), or are re-minted; legacy text is never user evidence.
- R4. `edit_unread_session` starts observe-only: a successful offline fire may be a parser miss.
- R5. `approve --mode warn|block` lists every counted TP and FP for TTY confirmation; label forgery
  is a stated residual risk.
- R6. The step 0 parity test runs against frozen fixture JSON that records the step0 commit.
- R7. No compilable detector reads Stop; Stop blocks are rejected by validation in Stage 2.

## Amendments

Block mode is opt-in per proposal, only after the observe and warn ladder. On 2026-10-19 three
one-clause edits were made outside this file so the repository's claims match that design:

1. SECURITY.md:36: "Hooks never block a session" became "Stage-1 hooks never block a session;
   stage-2 shadow hooks block only a proposal the operator promoted to block mode after the
   observe/warn ladder (see docs/design/compiler.md)".
2. README.md:79: "hooks fail open and stay out of the way" gained the same stage-1/stage-2
   qualification.
3. hooks/INSTALL.md:96-97 (onFailure paragraph): same qualification; `onFailure: "continue"` stays.

No block mode can be promoted until step 4 has shipped; these amendments change wording only.

## Files read

| File | Lines |
|---|---|
| ace-playbook/README.md (lines 55-85), LIMITATIONS.md, PREREGISTRATION.md (TBD grep), SECURITY.md | 131, 77, 88, 64 |
| ace-playbook/docs/reviews/2026-10-17-opus-stage1-review.md | 24 |
| ace-playbook/ace_playbook/config.py, enqueue.py, filelock.py | 129, 39, 22 |
| ace-playbook/ace_playbook/store/schema.py (full), store/log.py (lines 20-40, def list) | 262, 130 |
| ace-playbook/ace_playbook/inject/sanitize.py, citations.py, ledger.py, injector.py, __main__.py | 120, 26, 38, 140, 68 |
| ace-playbook/ace_playbook/reflect/provenance.py (full), reflect/runner.py (lines 20-60, def list) | 92, 192 |
| ace-playbook/ace_playbook/clean/transcript.py (lines 28-60, 84-134) | 189 |
| ace-playbook/ace_playbook/scrub/engine.py (lines 192-205, def list), scrub/quarantine.py (lines 20-60) | 230, 84 |
| ace-playbook/hooks/sessionstart.sh, stop_citation_ledger.sh, sessionend.sh, pre-commit-scrub.sh | 5, 5, 6, 23 |
| ace-playbook/hooks/INSTALL.md (grep, lines 80-105) | 158 |
| ace-playbook/ace_playbook/inject/__init__.py, store/__init__.py; `-X importtime` of `inject.sanitize` | 4, 5 |
| rebuild-lab/reports/ACE rebuild research round two.md | 85 |
| rebuild-lab/ace/step0/measure.py, detectors.py, scanlib.py, RESULTS.md | 346, 302, 266, 253 |
| rebuild-lab/ace/step0/tests/test_detectors.py (lines 1-230, test list) | 345 |
| rebuild-lab/ace/migration/id_map.jsonl (grep for g-010, line 96) | 662 |
