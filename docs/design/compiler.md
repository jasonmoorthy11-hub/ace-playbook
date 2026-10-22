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
| `compile/catalogue.py` | `CATALOGUE` registry (`Detector` rows with strength, tier, consumes, state, compilable, lessons), `evaluate` (strict mode, error list), `source_sha256` per detector (exclusion hash, `srchash.py`) and `source_table()` | ~100 |
| `compile/state.py` | Rolling per-session state file under `data_dir/shadow_state/`, locked (`LOCK_NB`), atomic, hashed name, 0600/0700, FIFO- and symlink-safe | ~235 |
| `compile/evidence.py` | Evidence cards (section 5): admission, legacy path, scrub, `cards/` writer | 300 (built, step 2) |
| `compile/proposal.py` | Proposal schema, ordered validation, replay/regression runner, writer (section 6) | 299 (built, step 2) |
| `compile/propscrub.py` | The scrub step and quarantine path of `proposal.write`, split out and imported lazily so the hook path never loads the scrub engine (step 4) | 34 |
| `ace_playbook/canon.py` | Stdlib-only leaf: `canonical`, `now_iso`, `record_hash`, `GENESIS`, `ID_RE`; `store/schema.py` re-exports them (step 4, see 7.2); `keyed_hash` and `chain_key` for the keyed chains (7.3) | 29; 52 |
| `compile/shadow.py` | Hook entry point: load approved proposals, re-validate, check the approvals row of a warn or block copy, evaluate, append `shadow.jsonl` rows, print the decision as JSON on stdout (block deny, or warn `additionalContext`); always returns 0 (section 7); since step 5a it reads `demotions.jsonl` once per call, since the integration wave it asks `modes.resolve` for the effective mode (disabled or revoked skips the proposal, a broken chain caps every proposal at observe), repeated log lines are throttled per call (`_Once`), and since step 5c every row carries `v` and `dur_ms` | ~220; 220 (step 4), 259 (step 5b), 300 (5a, integration, 5c) |
| `compile/approvals.py` | The hash-chained `approvals.jsonl`: `read_chain`, `newest`, `append` (locked, chain verified from the fd, duplicate skip), `verify_copy`; stdlib, `canon` and `chains` only, on the hook path (step 5b; `v: 2` keyed rows, 7.3 "Keyed chains") | 117; 131 |
| `compile/srchash.py` | The per-detector hash behind `Detector.source_sha256`: `module_text` (on-disk text, read once), `excised_text` (exact-span replacement of other rows' fire bodies only), `detector_drop` (which fire bodies to excise) and `digest` (step 5, per-detector hash) | ~100 |
| `compile/ladder.py` | The promotion rules: pure `check` over rows (TP and FP re-derivation, 14 days, 5 human TPs, 0 FPs, strength, source hash, cap) and `run`, the `approve --mode warn\|block` flow with the TP listing; imported lazily by the CLI (step 5b) | 235 |
| `compile/offline.py` | Nightly pass: run signal detectors over finished transcripts, finalize session-level detectors, label pre-fill, evasion candidates, demotions, `shadow_state` cleanup (section 7.3) | ~250; 290 (built, step 5a), 300 (integration, 5c: `_session` summary rows) |
| `compile/demotions.py` | Hash-chained, append-only `demotions.jsonl` (stdlib and `canon` only, read by the hook): reader, `effective_cap`, `lower`, locked writer; plus the pure FP and evasion rules `decide` and `pair_ref` the nightly pass uses | 149 (built, step 5a); 150 |
| `compile/chains.py` | What the two chains share (7.3, "Keyed chains (as built)"): the `Keyring`, the signed heads file, the keyed-install marker `key_of`, `hash_ok` (the verifier rule), `head_state`, `seal`, `has_v2`, `notes`, `read_regular`; stdlib and `canon` only, on the hook path | 150 (cap 150) |
| `compile/chains_io.py` | The re-seal of both chains under a new key with `.bak` copies and rollback, the file helpers and `need_key` for the writers; not on the hook path | 111 (cap 150) |
| `compile/chains_cli.py` | `compile keygen` (never-keyed installs only: lists, asks, seals) and `keygen --rehead` (shows what differs from the old heads, asks y/N); not on the hook path | 126 (cap 150) |
| `compile/seal.py` | `compile seal [--dry-run]` (section 8, "Seal (as built, not executed)"): the Frozen instruments block from live values and the TTY-only rewrite of the PREREGISTRATION.md shadow-mode heading; prints the git commands, never runs git; not on the hook path | 123 (cap 150) |
| `compile/modes.py` | `resolve` and the frozen `Resolution`: the one answer to "what does this proposal run at" for the hook, the ladder, the offline pass and the report (7.3, "Mode resolution"); `in_force`, `anchor`, `lifted` (an operator approvals row stamped strictly later lifts older demotions); stdlib, `approvals`, `demotions` only, on the hook path (integration wave after 5a and 5b) | 117 |
| `compile/metrics.py` | Stdlib-only rate code behind the report: Wilson 95 % interval, nearest-rank percentile, `_session` summary rows, eligible sessions, friction, hook cost, live-versus-offline agreement (section 8); hashed into `report_sha256` together with `report.py`; never on the hook path (step 5c) | 154 |
| `compile/synth.py` | `replay-synthetic`: the frozen invented corpus and the examples through `shadow._evaluate` with a temporary state dir into `shadow.synthetic.jsonl` only (`--out` confined by file identity); exits 1 on an `expect` disagreement; never on the hook path (step 5c) | 144 |
| `compile/report.py` | shadow.jsonl to results tables: counts, Rates (per eligible session, Wilson), hook cost and agreement blocks, Demotions section (section 8) | ~260; 193 for the minimal first cut (built, step 4c); 293 (step 5c) |
| `compile/__main__.py` | CLI: `cards`, `propose`, `validate` (step 2); `approve --mode observe`, `unapprove`, `shadow pre\|stop` (step 4); `label` (step 4b); `report` (step 4c); `offline [--dry-run] [--since DAYS] [--idle-minutes N]` (step 5a); `approve --mode warn\|block` (step 5b); `replay-synthetic [--out]` (step 5c); later `packet`, `review-record` | 98, then 181 (step 4), 266 (step 5b), 300 (integration: lift prompt, chain notices; 5c) |
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
- Hook-path imports are stdlib only: after `import ace_playbook.compile.shadow`, `sys.modules` holds no `sqlite3`, `ace_playbook.store`, `.scrub`, `.clean`, `.inject`, `.keys`, `.reflect` (a subprocess test asserts it). `canonical`/`now_iso`/`record_hash`/`ID_RE`/`GENESIS` moved to `canon.py`; `evidence.py` loads the cleaner, scrub engine and `DeltaLog` inside its offline functions; the scrub step moved to `propscrub.py`. Because the scrub engine is off the hook path, the load-time `proposal.validate(..., hot=True)` runs schema, message, regressions, replay and path but reports the scrub step as `hash-bound, not rerun`: `proposal_sha256` binds the text that passed scrub when the proposal was written.
- The hook loads a record only if the hash matches, `mode` is in `shadow.ALLOWED_MODES` (`("observe",)`, widened only by tests, so a hand-edited `mode: "block"` is skipped), and `proposal.validate` passes on the body (all six steps, replay included) on every call. It measures about 1 ms in-process and 35 ms per wrapper call (p95 37 ms on 3.14, 104 ms on the system 3.9).
- A `shadow.jsonl` row is `{ts, proposal_id, lesson_id, mode, event, tool_name, session, payload_sha256, would_fire, cause, tier}`: `session` is the bare `sha256[:16]` of the session id (the section 7.1 example's `sid:` prefix, `v`, `row_id`, `corpus`, `arm`, `hits`, `dur_ms` and `target_hashes` are not written yet); `payload_sha256` is `evidence.payload_hash` of `{tool_name, tool_input}`, so it joins to an evidence card.
- With no approved proposal matching the event and tool, the hook exits before touching `shadow_state/`, so the Stop registration does not reset per-turn state yet (only `edit_unread_turn` needs it and it cannot be approved). No kill-switch config key; `proposals/DISABLED` is the switch. No arm, no `demotions.jsonl`, no `approvals.jsonl` check.
- When the state lock is skipped or the state directory is unusable, detectors with `state == "none"` still run on a blank state and stateful ones stay silent.
- `python -m ace_playbook.compile.shadow pre` and `python -m ace_playbook.compile shadow pre` both work; the wrapper imports `ace_playbook.compile.shadow.main` directly.
- `cause` is `detector` (the detector fired) or `truncated` (only the `on_truncated: "fire"` rule did).
- Replay check of the first approved proposal (2026-10-19, observe): the originating masked payload (`$VAR/patches` target) piped through `hooks/shadow.sh pre` gave exit 0, empty stdout and one `cause: detector` row; a `/tmp` target gave no row; a 70 KB command gave one `cause: truncated` row; `compile report` showed rows 2 / detector 1 / truncated 1 and `label --rows` listed both ids. The live registration then fired inside a real session on a `$VAR/x` target and stayed silent on a `$TMPDIR/...` target. Known interaction: the operator's own destructive-command guard matches the substring `rm -rf /`, so an absolute `/tmp/...` delete is blocked by that guard before this hook's verdict matters; the auto-TP rule in 7.3 ("an existing hook blocked the same call") covers that case.
- `approve` prints a banner and asks a second y/N when a cited card has `provenance: legacy`; validation accepts a legacy card only if its `label_ref` matches an `incident` row of the hash-chained labels file (ref = card id).
- The registration matcher is `Bash` only, narrower than section 7's `Bash|Read|Edit|Write|MultiEdit|NotebookEdit`: no approvable proposal consumes the other tools yet, and each extra matcher is one more Python start per call.
- Python writes bytecode (`__pycache__`) under `$ACE_PLAYBOOK_HOME` on first import; `-I` does not stop that. Set `PYTHONDONTWRITEBYTECODE=1` in the hook environment if the checkout must stay untouched.
- `evidence.private_dir` also tightens an existing real directory of ours whose mode is not 0700 (a symlink is never chmod'ed).

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
`state`, `fire`, `compilable`, `lessons` (L-ids after migration), and `source_sha256` (used to
invalidate approvals when the code that decides a detector changes).

**Source hash (as built, per detector).** The hash is an exclusion: it covers all of `detectors.py` and
`catalogue.py` as text (catalogue.py whole), minus what cannot change this row's behaviour. `Detector.source_sha256` is the sha256
of these parts, each preceded by its label and joined by NUL (nothing is deduplicated): the row name; the name
of its `fire` function; the row metadata as canonical JSON (`strength`, `tier`, `consumes`, `state`,
`compilable`, `lessons`); `detectors.py` with only the BODIES of the other rows' `fire` functions replaced by
`...excised...` (the exact characters from the first statement of the body to the end of the def; decorators,
signatures and default arguments of those fires stay, because they run at import time in every process; a statement
sharing a line with an excised body stays hashed); `catalogue.py` WHOLE, the `CATALOGUE = {...}` registry literal
included, because any expression inside that literal runs at import time in every process (the hook too), so it is
provenance like any other import-time code; and the full text of `events.py`, `shell.py` and `textrules.py`. The
row's own fields also enter through the canonical `meta` part; that is redundant now that the literal is hashed, and
harmless (it keeps a row built at runtime honest). A fire that the remaining text still names (one fire calling another, or named
in a kept signature) is kept whole. `advance` and `blank_state` are hashed for every row, stateless or not:
`evaluate` runs `advance` in the same hook call before the decision is emitted, so a `SystemExit` or a hang in it
would silence a stateless detector's deny, and an `advance` edit is rare enough that retiring everything is the
honest answer. Everything else is hashed verbatim: imports, decorators, constants and every rebinding,
helpers, `evaluate`, the `Detector` class, comments. `source_sha256` raises `ValueError` unless the row's
`fire` is the top-level function of `detectors.py` that carries its `__name__` (a lambda, a `functools.wraps`
wrapper, a `functools.partial` or a callable instance, which has no name, fails closed for that row only: `validate` reports a fail-closed schema step, the hook skips the proposal, and the nightly pass treats it as a
`source_sha256` change; `source_table()` raises for the whole registry);
`evaluate` hands each fire its own deep copy of the state (the event is shared, not copied), and a structural
test pins that `detectors.py` mutates no module-level state and no `event` or `state` argument. That test runs at merge time only, so a mutating fire in an edited installed copy would
change other rows' results in the nightly pass and in evidence building (an offline-only residual); the hook is unaffected,
because it runs only rows whose proposals are hash-valid and an edited row's proposals are stale. So an edit inside one
detector's own `fire` body (an edit inside another row's fire body leaves this row's hash alone) retires only that row's
proposals; any other edit to `detectors.py`, `catalogue.py`, `events.py`,
`shell.py` or `textrules.py` (a registry field of any row, a shared helper, an import, `evaluate`, `advance`, a decorator or signature of
any fire) retires every approved proposal. Adding a detector also retires every proposal: it adds an import line
and a registry row to `catalogue.py` and a placeholder for its fire in `detectors.py`. The check is pure AST and text, read
from disk once per process (`srchash.module_text`): it describes the on-disk text, not the loaded code, so a
tampered `.pyc` or an in-memory monkeypatch is invisible to it, as it was to the old whole-file scheme.
`catalogue.source_table()` returns name to hash for every row (a later seal command and the report read it).
The first merge of this scheme changes every existing proposal's hash once: each goes stale (`compile
validate`: "catalogue source_sha256 is stale"; the hook skips it; the nightly pass records a `source_sha256`
demotion), so re-propose and re-approve.

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

### 4.1.1 Evasion coverage matrix and hook conformance

`tests/test_detector_evasion_matrix.py` measures `rm_rf_nonscratch` against ways an agent can delete recursively outside scratch.
Each row is a class and a variant (for example `flags/split-flags`, `interpreter/python-rmtree`, `xargs_find/find-delete`) built twice
from one command template: once with an invented path outside scratch (ground truth: it should fire) and once with a scratch
path (its benign twin: it must not fire). The strings are only data: they go through the hook's own path (hook payload,
`event_from_hook`, `catalogue.evaluate`) and are never executed. 43 variants, 86 cases.

The survivors of a mutation run over the detectors, the tokenizer and `textrules` are pinned by `tests/test_mutation_survivors.py`.

Where the frozen detector disagrees with the ground truth the case is a strict `xfail` (`documented miss: <class>`), so the suite is
green today and turns red the day a detector edit fixes a case, which forces the expectation to be updated in the same change. A
second test pins the miss count per class (FN = a delete outside scratch that does not fire, FP = a scratch delete that does), so a
change in either direction shows up. As of this commit:

| Class | FN | FP | Cases |
|---|---|---|---|
| `alias_quote` | 3 | 0 | `$IFS` separators, `$(echo rm)` as the command word, `base64 -d \| sh` |
| `interpreter` | 7 | 0 | python, node, perl, `bash -c`, `sh -c`, `eval`, a `bash <<EOF` body |
| `path` | 0 | 2 | `cd <scratch> && rm -rf ./*` and `D=<scratch>; rm -rf "$D"` fire (the target cannot be resolved) |
| `tool_indirection` | 2 | 0 | a script, or a Makefile whose `clean:` recipe deletes, written by `Write` and then run (`bash script`, `make -C dir clean`) |
| `xargs_find` | 3 | 0 | `ls -d dir/* \| xargs rm -rf`, `find -delete`, `find -exec rm -rf {} +` |
| `other_tools` | 4 | 0 | `rsync --delete`, `git clean -fdx`, `trash`, an `osascript` Finder delete |
| `control`, `flags`, `shell_construct` | 0 | 0 | |

That is 19 false negatives and 2 false positives. `trash` and the Finder delete are recoverable, but they still count as should-fire:
the lesson's letter is "confirm the target first", and a recoverable delete outside scratch has not been confirmed.
The matrix is the input to the next detector edit after the measurement week
ends: fix a class, flip its cases from xfail to plain, update the pinned counts, and re-propose and re-approve (the edit moves
`source_sha256`). Nothing in the detector changes while the week runs. `tests/test_hook_conformance.py` is the other half: the real
`hooks/shadow.sh` in a child process against malformed, empty, non-UTF-8 and over-cap stdin (exactly `MAX_STDIN` is evaluated, one
byte more is skipped), a command of exactly 64 KiB (the row carries `dur_ms`), a state lock held by another process, a missing or
read-only data directory, `ACE_PLAYBOOK_KEYED=1` without a key (`chain_key:missing` once across processes), and an allow-list of
every module the hook path loads, checked twice in a child process: at import, and after one firing evaluation (the approved proposal,
`catalogue.evaluate` and the row write have all run); `seal`, `dashboard`, `report`, `synth`, `metrics` and `scrub` must not be among
them. Every hostile-input case (malformed, empty, non-UTF-8, over the cap) must exit 0 and write nothing but at most one type-only
line in `shadow.log`; the other cases must exit 0 with an empty stdout and stderr, and pin what they do write (a row, a log note).

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

As built, `provenance` is `{cards, lesson_evidence, source_sha256}`; `source_sha256` is the per-detector
hash of section 4 and the proposal id includes it, so a detector edit gives the same proposal a new id.

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
| warn | row + stdout `hookSpecificOutput.additionalContext` (model-visible), stderr echo | one JSON object on stdout; the same message on stderr | open question 1, resolved in code (step 5b) |
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

**As built (step 4b, 2026-10-19).** `python3 -m ace_playbook.compile label` (`compile/labels.py`) writes
`labels.jsonl` rows `{ref, label, reason_code: "human", at, prev_hash, hash}`, plus `lesson_id` and `session`
(`sid:` hash, never the raw id) on an `incident` and an optional `note` (at most 200 characters, scrubbed with the
LESSON profile; a hit refuses the row). `ref` is the card id of the incident (`--incident --lesson --session`) or
the shadow `row_id` = sha256(ts, session, proposal_id, payload_sha256)[:8] (`--tp ID`, `--fp ID`, ids from `--rows`).
Writing needs a TTY and a `y` on /dev/tty; the whole chain is verified before each append and a broken chain refuses
the write. `--list [--kind K]` verifies and prints, `--rows` prints shadow rows with their ids; neither needs a TTY.
The write opens the file `O_NOFOLLOW`, takes `flock`, and verifies the chain from that locked fd. `row_id` is
computed on read from the stored shadow row (the bare `session` hash as stored, `ts|session|proposal_id|payload_sha256`
joined with `|`, sha256, first 8 hex, no `r:` prefix); shadow.py writes no `row_id`, so a future hook that
does must use the same formula.

**Label queue (as built, step 2d).** `compile label --queue [--limit N]` (`compile/queue.py`, TTY only, not on the hook path) is the
labelling path; `--rows` stays for audit. `pending` lists the fired rows of the private corpus that no label covers, oldest first,
one per `row_id`, at most N (default 50). Each card has six field groups: identity (id, detector@hash8 of the approved copy, resolved mode,
age); why (the detector and the cause; payloads are hashes only, no command text is shown); context (session[:8], ts, proposal id, event);
the label control line `1 tp / 2 fp / 3 skip / q quit`; the proposal's tally (rows, tp, fp, labels still needed to reach 5 TPs);
provenance (labels chain ok|BROKEN, `rubric = design §7.3`). The queue is blind: an item never carries an existing label, a note or the
row's text, and a row that a human `tp` or `fp` row (or an auto `fp`) covers is not offered (a `tp_candidate` covers nothing). The fires of
retired proposals are left out unless `--all`. `1` appends a human `tp` (optional note); `2` appends a human `fp` and
needs a note that passes the scrubber (asked again once, then the row is skipped); `3` skips; any other key is asked again, the third
miss skips. A broken labels chain refuses before the first card.

**As built (step 5a, 2026-10-19).** `python3 -m ace_playbook.compile offline [--dry-run] [--since DAYS] [--idle-minutes N]`
(`compile/offline.py`; `hooks/nightly_sweep.py` runs it before the reflection sweep, also under that script's `--dry-run`,
because it makes no API call) prints counts only. It scans transcripts that are regular files of at most 64 MiB, not in
`subagents`, idle for the given minutes and modified within `--since` days; `offline_state.json` maps the session hash to
size and mtime, and an unchanged session is skipped. Every catalogue detector runs over each transcript (strict mode, one
failing transcript is one error) and the session end fires `commit_without_push`; rows go to `detector_events.jsonl` with an
id of sha256(session, detector, ts, payload hash)[:16], so a rerun appends nothing twice. Live `shadow.jsonl` rows join their
transcript by session hash and the fire by payload hash and time (the tool use with that hash whose timestamp is nearest at or
before the row's, else the nearest after within 60 seconds, so only the identical call that fired is judged), and get at most one
auto label in the order of the lists above.
`labels.jsonl` rows carry `reason_code` set to the rule (`auto_fp_correction`, `auto_fp_prompt_target`, `auto_fp_shell_rerun`,
`auto_tp_hook_block`, `auto_tp_correction`) and the label `fp`, or the new `tp_candidate`, which the CLI never writes and which
counts as nothing. The effective label of a ref is its newest human row (any `reason_code` that is not an auto rule), else its
newest auto row; no auto row is written over a human one or twice. `report` counts `fp` from both kinds, `tp` from human
rows only, and shows `tp_cand` apart. Targets are hashed in memory against the latest prompt (never a token under two characters
or made of `/`, `~` and `.` only; `~` is expanded in memory) and against shell-mode commands: only a user record whose whole text
is one `<bash-input>...</bash-input>` block (Claude Code does not count it as a human prompt), never such a fragment inside
other text. No prompt text is stored. Demotions go to the
hash-chained `demotions.jsonl` (`compile/demotions.py`): `{ts, v, proposal_id, from_mode, to_mode, cause, ref,
review_required, prev_hash, hash}`, `to_mode` never above `from_mode`, a repeat of the newest row skipped. The hook reads the
file once per call: `disabled` skips the proposal, any other cap lowers its mode, a broken chain caps every proposal at
`observe` (measurement continues), and so does a read error (symlink, directory, FIFO, oversize), logged by type: the hook fails
safe, not open. The "skipped" lines for a demoted or not-yet-allowed proposal, the `approvals:` lines and the demotions lines are written
once per 8 KiB of `shadow.log` (its tail, read once per hook call; `error:` lines are never throttled), so their volume is bounded to a constant factor of the unthrottled lines (measured about 2.3 times the real lines, since once-lines scroll out of the window and are rewritten) and they cannot fill it to its cap on their own. The same applies to `skipped:<id>:oversize` and `invalid:*`, `approved_dir_symlink`, `skipped:too_many_files` and `skipped:file_name`. The pass appends `label_fp` (block to
warn, warn to observe), `label_fp_pair` (two effective FPs within 30 days: observe, `review_required`) counting FPs since the
later of approval and the proposal's last demotion (an `evasion_pair` row does not restart them; two FPs more than 30 days
apart lower one rung each in the same run); and the disable causes `lesson_inactive` (store status `dormant`, `retired`,
`superseded` or `quarantined`, never `provisional`, `active` or `compiled`; skipped, and reported as `lesson_check: skipped (no
store)`, when `deltas.jsonl` does not exist), `source_sha256` (catalogue hash against `provenance.source_sha256` of the proposal) and
`error_rate` (`error:` lines of `shadow.log` above 1% of those plus shadow rows over seven days, only when that sample is at
least 100; below it the run reports `error_rate: not computable (n=k, need 100)`, because the rows count fires, not hook calls).
When the shadow corpus cannot be read the demotion stage is skipped and counted as an error. The p95 cost rule is not implemented
(rows carry `dur_ms` since step 5c, but it covers firing calls only and not the interpreter start, and there is no demotion cause for
it): every run prints `p95_rule: not implemented`, and the rule is not treated as passing. After a block, a different Bash command whose rm targets hash to the same key within the next five
tool uses is an evasion candidate (`evasion_candidates.jsonl`); two for one proposal within 14 days append an `evasion_pair`
row with `review_required` and the current mode (no lowering). Regular `shadow_state` files older than seven days are removed
(a `.json.lock` only when its `.json` is stale or missing and its flock is free); `--dry-run` computes everything and writes
nothing. A session is marked done in `offline_state.json` only when its detector rows were written and, if it has shadow rows,
the label stage succeeded.

*Demotions are lifted by an operator approval.* A demotion row caps its proposal until the operator approves it again: the
cap is the LOWEST `to_mode` over the rows stamped at or after the proposal's newest `approvals.jsonl` row (rows stamped strictly
BEFORE that approval are lifted by it; a row in the same second as the approval is NOT lifted, and the operator re-approves a
second later; with no approvals row every row counts), so `approve --mode observe` on a demoted proposal
(including a `disabled` one) writes a fresh observe row and restarts the ladder from observe, and `approve --mode warn|block`
on a proposal that is in its previous rung is refused with `demoted:<cap>` only while a row in force caps it. `approve --mode
observe` prints every row it would lift (`to_mode`, `cause`, `ts`) and says so in the `y/N` prompt, also on a demoted warn or
block copy, and it does not back-fill a pre-chain copy while any demotion row of the proposal exists (the back-fill row would
take its `ts` from the agent-writable copy): it approves afresh, with that prompt. The agent cannot
write a valid approval row without the TTY, except by reading, replacing, redirecting or deleting the chain key (the stated residual, section 9). The cap is
no longer a rule of chain validity (a later demotion from a lifted mode must stay a valid row); `append` keeps a guard
against a row whose `from_mode` is above the mode `modes.resolve` gave the proposal (the nightly pass always passes the
resolved mode, so it should never fire: a guard, not a control). A cause that is still true after a lift (the same `to_mode`,
`cause` and `ref`, e.g. every disable cause) is written again: the pass and `append` dedupe only against rows in force. `compile report` has a `Demotions` section (every row,
its cap, cause, time, `review_required` and `lifted` yes/no; a broken chain is one visible line), so a silenced proposal shows.

**Mode resolution (as built, integration).** `compile/modes.py` holds `resolve(pid, copy_rec, approvals_rows, approvals_broken,
demotion_rows, demotions_broken, now)` and the frozen `Resolution(mode, approved_mode, cap, window_start, source, problems)`;
the hook (`shadow._load_one`), the ladder (`ladder.check`/`run`), the nightly pass (`offline.current_mode`, `demote`) and the
report (`_meta`, `demotion_summary`) all call it, so they cannot disagree. Rules: `approved_mode` is the copy's mode when
`verify_copy` says `ok`; an observe copy needs no row; a warn or block copy with no row or another hash runs observe
(`approvals:missing|mismatch`); a broken or unreadable approvals chain runs observe (`approvals:chain_broken`); a newest row
that is `revoked`, or no copy, disables the proposal (`revoked|no_copy`; the hook logs `skipped:<id>:revoked`, and
`skipped:<id>:demoted` for a `disabled` cap). `cap` is the lowest `to_mode` of the rows stamped at or after the newest
approvals row stamped at or before `now` (a broken approvals chain lifts nothing); a broken or unreadable demotions chain caps at observe
(`demotions:chain_broken|unreadable`). `mode = lower(approved_mode, cap)`. `window_start` is the newest approvals row, else
(the pre-chain observe case only) the copy's `approved_at`, then the newest counter-resetting demotion (any cause but
`evasion_pair`) after it; a stamp after `now` is ignored, not clamped, for the window (it still caps, fail safe); `source` names
which of `approval|demotion|copy` set it (`none` when nothing did). The ladder counts from `window_start` and refuses with
`demoted:<cap>` when the cap is below the requested mode; `ladder.run` and `check` no longer take a `cap`. Both chains are read
at most once per hook call, and `approvals.jsonl` is now read on every call that has an approved file (a revocation must
stop a copy; the read is capped at 1 MiB, no-follow). A real nightly pass stamps each demotion row when it is appended
(`demotions.append`), not when the pass started. `Event.meta` (`isMeta`, `isCompactSummary`, `isVisibleInTranscriptOnly`)
changes nothing about `human`; its consumers check it themselves: `user_correction`, `shell_keys`, the prompt-target rule,
`span` and the offline turn reset of `edit_unread_turn` (a transcript-only record is not "the user asked for exactly this", and it does not end a span). An unreadable
`demotions.jsonl` is logged `error:demotions:<Type>` (unthrottled, counts toward the error rate like `error:approvals:<Type>`);
`demotions:chain_broken` stays throttled. `approve --mode observe` never back-fills while a demotion row exists or the
demotions file is broken or unreadable (it prints one line saying so and approves afresh), and says `capped at <cap> by <n>
row(s) not liftable now (re-run in a second)` when the only rows in force tie the clock second, `capped at <cap> until <ts>`
for a later one, and `still capped ...` after the lift lines when a row it does not lift stays. Operator messages and the
report text count `demotions.jsonl` rows from 1, like `approvals.jsonl` (the report JSON `broken_at` stays 0-based). The evasion `flagged` check of
`offline.demote` deliberately looks at ALL rows, lifted ones included: an evasion pair is one event keyed by the newer
candidate's id (the lift prompt listed it), any new candidate makes a new ref, and re-flagging the same pair after every lift
would pin the mode. `compile report` shows `approved_mode` (the copy's own)
and `mode` (resolved). A copy restored after `unapprove` is now skipped (`revoked`) instead of running in observe.

*Trust.* Every input of the offline pass can be written by a local process: the transcripts, `shadow.jsonl` (including its
`mode`), `shadow.log`, `labels.jsonl`, `demotions.jsonl` (keyed from `v: 2`; unkeyed `v: 1` rows before the boundary), `offline_state.json`, the approved copy's `approved_at` and
`deltas.jsonl`. Demotions can therefore be both caused (forged rows, labels or log lines) and suppressed (delete or truncate
the file, forge rows). The hook's own safety is that a copy above `observe` needs a matching approvals row and demotions never
raise a mode (only an operator approval lifts them); the operator's controls are the TTY listing at promotion and this report's
`Demotions` section. A window start after now (a forged `approved_at` or demotion stamp) is ignored when FPs are counted. The
1% error-rate rule is also skipped when `shadow.log` has reached its size cap (`error_rate: not computable (log full)`), and a
deltas log that fails verification gives `lesson_check: chain invalid` (an error, no disable). `tp_candidate` rows must never
feed the ladder: the agent can print a guard-looking line into its own tool output in observe and warn mode. `shell_keys`
skips `meta` records (`isMeta`, compact summary, transcript-only) and still requires the whole-record `<bash-input>`
shape. A failed offline pass (an exception or `errors > 0`) is recorded as a health failure by
`nightly_sweep.py` whatever `--dry-run` says about reflection, unless `--offline-dry-run` is given. A clean, real offline
pass under `--dry-run` records a health success (the heartbeat's last good nightly run) and a crash of the script under
`--dry-run` records a failure, because `run_sweep` writes no record on a dry run; `--dry-run` with `--offline-dry-run` or
`--no-offline` records nothing.

**Mode ladder.**

| Step | Allowed for | Rule to move up (all must hold) | Who acts |
|---|---|---|---|
| observe | any compilable detector | initial mode after approval | human `approve --mode observe` |
| observe to warn | strong detectors only; `edit_unread_session` additionally needs a labelled sample in which each TP was checked against the raw transcript for a parser-missed read | at least 14 days in observe; at least 5 human-confirmed TPs and 0 FPs since entering observe; replay passes on current code; lesson still `active` | human `approve --mode warn`: the CLI checks the rule and lists every counted TP for TTY confirmation (below) |
| warn to block | strong detectors whose catalogue row allows block, PreToolUse only | the same counts again, fresh, in warn; a new Opus review of the warn-period data; evasion candidates reviewed | human `approve --mode block`, with the same TP listing |
| any mode to disabled | always | lesson leaves `active`; detector `source_sha256` changes; hook error rate above 1% or p95 above 200 ms over seven days | nightly pass (lowering only) |

**As built (step 5b, 2026-10-19).** `compile/approvals.py` keeps `data_dir/approvals.jsonl`, a hash chain from
`canon.GENESIS` (same scheme as `labels.jsonl`) of rows `{ts, v: 1, proposal_id, proposal_sha256, mode, reviewed_by:
"operator-tty", tp_refs, fp_refs, review_sha, prev_hash, hash}`: enums, ids, hashes, timestamps and lists of row ids only.
Every `approve` appends one row (`proposal_sha256` is the approved copy's own hash, so a row binds id, content and mode;
the same hash and mode twice appends nothing) and `unapprove` appends a `revoked` row (`proposal_sha256` = the removed
copy's hash, or `-`; also when the copy is already gone but the newest row is not revoked), so putting the old bytes
back reads as `mismatch`. `approve --mode observe` on a copy that already exists and matches, but has no row for the
proposal at all (the first live approval predates the chain), prints the same provenance block and prompts as a fresh
approval (the legacy banner and second prompt included), refuses a copy whose `approved_at` is in the future, and appends
the missing row with `ts` = the copy's `approved_at`, so the observe clock is not reset. That row is the only way a
pre-chain copy starts a window: `approve --mode warn` never trusts the copy's own `approved_at`; when the newest row says
otherwise (`mismatch`: demoted, revoked, or a copy edited down) it approves afresh (new `approved_at`, full listing, row
`ts` = now). The hook honours a copy in `warn` or `block` only when the newest row for
its proposal has the same `proposal_sha256` and `mode` (`verify_copy`); otherwise it runs the proposal in observe and logs
`approvals:<id>:missing|mismatch`. A broken chain (or an unreadable file) puts every above-observe copy back to observe and
logs `approvals:chain_broken` once. An `observe` copy needs no row. The chain is tamper-evident against hand edits of the copy
(a hand-edited `mode: "block"` falls back to observe even with a recomputed `proposal_sha256`); once
`keygen` has run it is keyed (`canon.keyed_hash`, "Keyed chains (as built)" below), so a process that also reads, replaces or redirects the key
can still append a valid row and enforce: a forged approvals row is the residual risk in section 9 (step 5c: an operator approval also lifts older demotions, see "Mode resolution"). A cut last line stays a refusal (the operator truncates the file to its last complete line).
`approve --mode warn` runs `ladder.check` and prints, before asking, every counted TP and FP as one line: `fire <ts>  session
<hash16>  row <row_id>  label <at>  chain #<n>[  [after last approval]]` (`n` is the 1-based row in `labels.jsonl`). A label is
always newer than the approval row that opens its window (it labels a fire after that row), so the per-line tag is printed
only when some counted label does NOT postdate the newest approvals row; when all do, one line says `all N counted labels
postdate the last approval row (<ts>)`. Then each failed rule, and exits 1; if all hold it asks `Promote
<id> to warn mode? N TPs, 0 FPs listed above. [y/N]` and on `y` appends the row with the listed `tp_refs` and `fp_refs`
and writes the approved copy. The rules: copy in observe for at least 14 days (window start = the newest approvals row of
the proposal, which must be an `observe` row; with no row at all the ladder refuses and says to run `approve --mode observe`, which
records it; a newer row of another mode, a `revoked` row, or a copy the chain does not back, `copy not backed by the approvals chain`, refuses); at
least 5 TPs, counted only from human `tp` rows (newest human row per ref wins) whose ref is the `row_id` of a live
`shadow.jsonl` fire of this proposal run in observe, with `window start <= fire ts <= now`; a counted label dated before its
fire or after now is refused; 0 FPs, where a ref's label is its newest
human row, else its newest row of any reason code (an auto `fp` blocks; `tp_candidate` never counts); validation passes on
current code; the lesson is `active`; the detector is strong and its catalogue `compilable` reaches the mode; the
proposal's `source_sha256` equals the catalogue's; `edit_unread_session` also needs `--raw-checked`. A counted label with no
fire row (`forged label`, every offending ref listed), a broken labels chain and a broken approvals chain are refused; the repair for a forged label is to restore the missing shadow row, or to truncate `labels.jsonl` to the row before the forged one and re-label. If the approved copy cannot be written after the row was appended, the error says so and the hook keeps running observe (row before copy is the fail-safe order). `--mode block` applies the same
rules with the window starting at the newest approvals row, which must be a `warn` row (`not in warn` otherwise), counting
fires run in warn, a `block`-capable `PreToolUse`
detector, and a required `--opus-reviewed <sha>` (7 to 40 lowercase hex) stored as `review_sha` and named in the prompt.
`ladder.check` refuses with `demoted:<cap>` when the resolver's cap (`modes.resolve`: the lowest `demotions.jsonl` row after the
newest approval; a broken demotions chain or a read error counts as `observe`) is below the requested mode, so a demoted
proposal cannot be promoted past it until the operator approves it again. Warn delivery is resolved: see section 11 question 1.

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

#### Keyed chains (as built)

`approvals.jsonl` and `demotions.jsonl` carry two row versions. A `v: 1` row is hashed with `canon.record_hash` (plain SHA-256 over the
canonical row without `hash`). A `v: 2` row is hashed with `canon.keyed_hash`: HMAC-SHA256, key = the 32 bytes of the chain key, message =
the canonical row without `hash`. The key set, `prev_hash` linking (genesis for the first row) and every other check are unchanged, and
`labels.jsonl` stays unkeyed in this slice.

*Keyed install* (`chains.key_of`). An install counts as keyed when `os.lstat` finds the key file OR the heads file (any file type, a dangling
symlink included), or when the environment variable `ACE_PLAYBOOK_KEYED=1` is set (the operator carries it in the hook command in the
Claude Code settings, behind a stronger consent gate than the data dir; `hooks/shadow.sh` does not sanitise the environment and a test
asserts the flag reaches Python). Otherwise the install is unkeyed.

*Verifier rule* (`chains.hash_ok`, `head_state`). Unkeyed: ONLY `v: 1` rows verify (plain hash, so installs from before `keygen` behave
exactly as before) and any `v: 2` row breaks the chain at that row. Keyed with a readable key: ONLY `v: 2` rows verify (keyed hash), any
`v: 1` row breaks the chain at that row, and the last verified row must equal the signed head. Keyed without a usable key (deleted, wrong
mode or owner, unreadable, not 64 hex characters): both chains are broken, even empty ones, the hook logs `chain_key:missing` once and every
writer refuses. `v` must be the int 2 or 1 (`2.0`, `"2"`, `True` never verify) and `hash` an ASCII string. An earlier draft let a `v: 1`
prefix verify while no `v: 2` row had been seen, and a later one treated "no key file" as "unkeyed", so a process that never read the key
could rewrite a chain as plain v1 rows (the second time after moving the key away); the marker above closes both.

*Key file.* `Config.chain_key_path` (expanded at load) is the key the config file NAMES; there is no ambient default (it was removed in Order
3a: a default made a config that never named a key read one), so a config without the line, `null` and a bare `Config()` all have none.
`Config.source` (set by `load`, made absolute; not a config key) is the file read; an unnamed key makes `key_of` look for `chain_heads.json`
beside that file, and a shape-valid `v: 2` row in either chain file under `data_dir` (`modes.v2_rows`) also marks the install keyed wherever the
config sits, so a keyed install whose config names no key (flag, heads file or `v: 2` rows) is `chain_key:missing`, never a silent v1 rule. `compile keygen`
on a config without the line proposes `chain_key` beside the config file, names it in the prompt and, on y, re-reads the config and writes the line
(home as `~`, `atomic.write_private`, other keys kept, so an edit made while the prompt was open survives) before it makes the key; `--rehead` and a `null` never write the config. `canon.chain_key` reads 64 hex characters (one trailing newline allowed) from a regular file of the current user,
opened with `O_NOFOLLOW`, with no group or other permission bits and owner-read (0600 or 0400); a missing file, symlink, directory, FIFO, other
owner or mode, bad length or non-hex content is `None`, and it never raises.

*Heads.* Beside the key, `chain_heads.json` (0600) holds `{"approvals": <last hash>, "demotions": <last hash>, "mac": ...}`, GENESIS for an empty
chain, where `mac` is the keyed hash of the two heads (a process without the key cannot forge a head). Every keyed `append` rewrites its head
(temp file, fsync, `os.replace`) inside the chain file's lock, under a lock on the directory because the two chains share the file. When the
key is readable the last verified row must equal the head: a cut tail or a rollback is `head_mismatch` and breaks the chain (the verified rows
are returned, but the chain counts as broken, so approvals lift nothing and the demotions cap is observe); a missing, unsigned or unreadable
heads file is `heads_missing` and breaks both chains, even empty ones. With no key the heads are not consulted. `append` refuses a chain whose
head does not match, so a truncation is never extended into a valid-looking chain.

*keygen* (TTY only) runs on a NEVER-keyed install only (no key file, no heads file under `lexists`, no `ACE_PLAYBOOK_KEYED=1`). It takes the
approvals then the demotions file lock, reads both files ONCE and holds the locks while it lists the rows it will re-seal (one line each: ts,
proposal id, mode or to_mode; an unverifiable tail is announced as "row N onward does not verify and will be kept as is") and asks y/N; the
re-seal is staged from those same bytes and never re-reads the files, so a file swapped during the prompt cannot change what is sealed (a test
rewrites the chain from the ask callback). Then it writes 32 `secrets.token_bytes` as hex to
the key path (`O_CREAT|O_EXCL|O_NOFOLLOW`, 0600, fsync; a missing parent is created 0700, an existing one keeps its mode), writes byte-for-byte
backups `approvals.jsonl.bak` and `demotions.jsonl.bak` (0600, `O_EXCL`) in the KEY directory, and RE-SEALS both chains under their file locks:
every row that verifies with the plain hash is rewritten as `v: 2` (same content and order, `prev_hash` relinked, keyed hash) through a
`tempfile.mkstemp` file in the data dir, fsynced; all temp files exist before the first `os.replace`. If anything fails the replaced files are
put back from the kept bytes and the fresh key and the backups are removed again, so a failed run leaves nothing and can simply be run again.
An unverifiable tail is kept byte for byte after the re-sealed rows with a warning naming its first row. Then the signed heads are written,
the path is printed, and the `.bak` files stay (the operator's record of the pre-keygen state). There is NO resume path and keygen never
overwrites: on a keyed install it refuses (exit 1), because re-sealing v1 rows there would sign a forgery under a new key. The table:
never-keyed: lists, asks, seals. Keyed with a usable key and valid heads: "already exists". Keyed, key usable, heads missing or invalid:
refuses and branches: if both chains verify under the key (a deleted heads file) it says to run `keygen --rehead` (which lists and asks; restoring
from `.bak` would silently roll back every later revocation and demotion); if a chain is still plain v1 (killed between the replaces) it points at
Repair (delete the key, restore both chains from the `.bak` files in the key directory, delete the `.bak` files, run keygen again). Keyed, key unusable or absent (including only the flag
or only the heads file): refuses with the restore message (restore the key file from your backup, or move the unusable key file away,
delete `approvals.jsonl`, `demotions.jsonl` and `chain_heads.json`, unset the flag in that shell and run keygen again, re-making every
approval; the key file itself must go because its mere existence keeps the install keyed); the same message is the operator line of every
writer on such an install.
`compile keygen --rehead` (TTY) needs a usable key and holds both chain locks (approvals, then demotions) around the read and the update. It
verifies both chains with the key (a broken chain refuses: truncate it by hand first) and compares the new heads with the OLD, MAC-valid
ones: per chain it prints `old head not found: rows were removed or replaced after row N` plus what the chain now ends with, or the rows added
after the old head (normal after an interrupted append), or says the old heads file is missing or invalid. In every case it asks y/N on the
terminal before writing; there is no `--yes`. It then prints the two head hashes. Removed rows cannot be listed (they are gone); the message
that the old head is no longer in the chain is the signal, and the prompt says to compare with a backup first.

*Writers.* `append` (both modules) takes a `Keyring` (key plus heads path); with one it always writes `v: 2` and moves the head. Without one it
refuses with `ValueError("chain key missing")` once the file holds a shape-valid `v: 2` row (`demotions.append` takes the keyring by keyword only) (garbage that only says `"v": 2` is a broken chain
at its row, not a missing key), and on an empty or v1-only chain it keeps writing `v: 1`. `approve`, `ladder.run` and the offline pass's
demotions go through `chains_io.need_key`: on a keyed install without a usable key they print the restore message ("this install is keyed but the key is
unusable: restore the chain key file ... or delete approvals.jsonl, demotions.jsonl and chain_heads.json and run keygen again"), on an unkeyed
one whose file already holds a shape-valid `v: 2` row `error: chain key missing (run: uv run python -m ace_playbook.compile keygen; a broken
chain itself: see hooks/INSTALL.md section 7, Repair)`, and exit 1 (the offline pass counts one `errors` and still writes its other artefacts).
`unapprove` prints the same line and exits 1 but still removes the approved copy (only the revocation row is refused; the hook already runs a
chain it cannot verify at observe). `report` and `modes.read_*` take the keyring too.

*Hook.* `shadow.load_approved` reads the key once per call and passes the keyring to both chains. The resolver maps a broken approvals chain to
observe with no lift and a broken demotions chain to cap observe, so no break can promote. Log lines through `_Once`: `approvals:chain_broken`
or `demotions:chain_broken` (rows), `approvals:head_mismatch` or `demotions:head_mismatch` (heads), `chain_heads:missing`, and
`chain_key:missing` (keyed install, no usable key, a chain is broken). A chain signed with another key is a row break, not a missing key.

*Repair.* A broken chain is repaired by hand: truncate the file to its last complete line (the existing rule), then `keygen --rehead`.

*What it buys, and what it does not.* See the residual in section 9.

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
| Hook cost | p50/p95 `dur_ms` (firing calls only, in-process; limits in the 5c paragraph) |
| Live versus offline agreement | fires seen in one tier and not the other |

**As built (step 4c, 2026-10-19).** `python3 -m ace_playbook.compile report [--days N] [--json]` (`compile/report.py`) is the
minimal cut: per proposal, rows, rows by cause (detector, truncated), distinct sessions (a count, never the hashes), active days,
tp, fp, unlabelled and first/last date, then rows per day for the last N days (1 to 3660, default 14; the totals are not
windowed, and a row dated after today counts in the totals only). Eligible-session rates, Wilson intervals, friction and hook
cost are not computed (they need the offline pass). The private table reads `shadow.jsonl` and the synthetic one
`shadow.synthetic.jsonl`, each through a no-follow, regular-file-only reader (a symlink or directory exits 2); they are never
summed, and the guard checks every raw object, so a row whose `corpus` disagrees with its file refuses the whole report
(exit 2). tp/fp come from `labels.jsonl`: newest row per ref wins, `incident` rows are ignored, and a broken chain ignores
every label and says so. Rows are not split per mode or tier; `approved_mode` is the mode of the current approved copy, not
of each row, and (until step 5c) the synthetic table took detector, event and mode from the private proposals directory; it now takes detector and event from its own rows and shows no mode. Rows sharing a
`row_id` (same second, session and payload) collapse for labelling: one label counts once in tp or fp, and `unlabelled`
counts the rows whose id has no label, so tp + fp + unlabelled can differ from rows. The header prints `report_sha256`, the hash
of `report.py` only (from step 5c: `metrics.py` and `report.py`, NUL-joined; `labels.py` and `shadow.pending_body` also shape the numbers and are not covered), so the operator can
record it before observe starts.

**As built (step 5c, 2026-10-19).** Hook rows gain `v: 1` and `dur_ms` (whole milliseconds, `time.monotonic` from the start of
`shadow._run` to the row write); `labels.row_id` is unchanged. `compile replay-synthetic [--out PATH]` (`compile/synth.py`, not imported by
the hook) feeds every `fire` and `no_fire` payload of `fixtures/compile/examples.json` (one session per detector) and the frozen sessions
of `fixtures/frozen/shadow_corpus.json` (listed in `MANIFEST.json`; `fixtures/generate.py` writes it) through `shadow._evaluate` with a
throwaway state directory. It replaces `shadow.synthetic.jsonl` with one row per event (`corpus: "synthetic"`, `detector`; a row that did
not fire has `would_fire: false`, because the report needs the quiet sessions as eligible ones; the table counts only the fires), prints
counts only, exits 1 when an `expect` disagrees with the outcome or a detector errors (the corpus is a regression test), and 2 for an
unusable corpus. Detector names and tools are checked against the catalogue. Synthetic proposal ids are stand-ins derived from the
detector name, never ids from the private proposals directory. `compile/metrics.py` (stdlib only) computes, per corpus and proposal:
`eligible` (private: sessions whose newest `_session` summary row in `detector_events.jsonl` lists the proposal's detector; the offline
pass now writes one such row per transcript, `{detector: "_session", session, count: tool calls, prompts: human prompts, eligible:
[detectors with at least one consumed event]}`, and a grown transcript adds a newer row; synthetic: every corpus session that fed the
detector), the session-level `fire_rate` with a Wilson 95% interval (z 1.96, `-` for n = 0), `fires_per_eligible`, `repeat_fires`,
friction (warn and block messages per 100 tool calls; `user_correction` events per 100 human prompts, private only), `evasion` (rows of
`evasion_candidates.jsonl`), and per corpus the hook cost (nearest-rank p50 and p95 of `dur_ms`, `-` without it) and agreement: live
fires in scanned sessions with no offline event of the same detector, session and payload hash, and offline events of the deployed
detectors dated at or after the first live row with no live fire (`-` without live rows; synthetic `-`). A live fire in a session the
offline pass has not scanned yet is not an eligible-session fire and not a disagreement; the block prints their number (`live fires
awaiting offline scan`, `awaiting_offline_scan` in `--json`; a fire stamped after its session's newest summary row counts as
awaiting too, `truncated` rows do not; a proposal id with no proposal file has no rates). The agreement window starts at each detector's own first live row (a
revocation gap is not excluded yet), `cause: truncated` rows are counted apart (`truncated_fires`; fail-closed, not detector fires, but
still messages in the friction) and left out of agreement, and so are `Stop` rows and proposals (their payload hash is the raw hook
input, which no offline row has). A session that the offline pass scanned before it wrote `_session` rows is scanned again once
(its detector rows are deduplicated by id); the `_session` id covers the transcript's size and mtime, so a transcript that returns to an
older state still adds the newest row. `compile report` prints a `Rates` table and a
`Hook cost / agreement` block per corpus and carries all of it under `rates` in `--json`. **Limits of the cost figures.** (1) Private rows
exist only for fires, so the cost is that of firing calls, not of every call. (2) `dur_ms` is in-process: it starts at `_run` entry and
excludes the interpreter start, the imports and the config load (about 35 ms per wrapper call, section 3). (3) The synthetic cost is
the evaluator alone (`_evaluate` and the row), not a hook call. No per-call row is written. **Subagents.** The offline pass drops
sidechain records, while a subagent's hook call may carry its parent's session id: such calls can count as live fires against a
parent session whose tool-call count excludes them. **`--out`.** `replay-synthetic --out` is refused (exit 2, nothing written) inside
`data_dir` (decided by file identity of the parent and its ancestors, so a differently cased path cannot pass) unless it is
`shadow.synthetic.jsonl` itself, and when the target exists and is not a regular file of synthetic rows only;
synthetic rows carry `tier: "synthetic"` and the private-file guard refuses such a row. Still not computed: arms, randomization
inference, the spillover flag (they need the randomized phase).

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
analysis code hash (`compile/report.py`, `compile/metrics.py` and `compile/detectors.py`) (as built 2026-10-20: `report_sha256` covers
`compile/metrics.py` and `compile/report.py`; detector code is covered by the per-detector hashes of `srchash.py`); the TP/FP rules of 7.3; the
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

### 8.x Operator surface (read-side)

`compile status [--days N] [--json] [--out PATH]` (`compile/status.py`) is the one place the operator reads the measurement. Principles:
(1) one source of truth: `status.json` (schema 1, 0600, atomic write; default `data_dir/status.json`) is built from `report.collect` and
the two chains only, so each count and rate in it equals the `compile report --json` figure and no rate is computed outside `metrics.py`
(precision is `metrics.wilson(tp, tp + fp)`; the one extra figure is `fp_upper_95 = metrics.upper95_zero(n)` = 1 - 0.05^(1/n) at zero false positives);
(2) nothing reaches the model: the screen, the JSON and the digest are written for the terminal, never to an injector, a hook or a prompt,
and the module is not imported on the hook path; (3) rates carry denominators and intervals, never bare counts, and fewer than five
labels is the word `INSUFFICIENT`; (4) zero fires with zero eligible sessions is not evidence, it is `silent: eligible 0`.
The verdict of a proposal is the first rule that holds, and never looser than `ladder.check` (section 7.3) except for the four checks in the known gap below.
It is
rung-aware: the proposal stands on its current mode `m` (`observe`, `warn` or `block`) and the next rung `n` is `warn` from `observe` and
`block` from `warn`. The rules, in order: `retired` when the newest approvals row revokes it or its mode or approved mode is `disabled` or
`revoked`; rows still to label are `label N fires`, N being `to_label`; no eligible session is `silent: eligible 0`; a copy the verdict must
not trust is refused before any rung rule, as `ladder.check` refuses it, in this order: (1) no approved copy, `not backed: no approved
copy`; (2) the approvals chain does not verify, `not backed: approvals chain broken` (named before any row, since `ladder.run` refuses a
broken chain outright); (3) the proposal's newest approvals row is stamped after now, `not backed: newest approvals row is dated in the
future` (`check`: "newest approvals row is dated in the future"); (4) a demotion in force caps the copy below the next rung
(`ladder.RANK[cap] < ladder.RANK[n]`), `demoted:<cap> (approve --mode observe restarts)`, or `demoted:<cap> (demotions chain broken)` when
the demotions chain does not verify, because the resolver then caps at observe and a re-approval does not lift it (`cap_note` carries the
parenthesis); (5) the copy's body is no longer the proposal file's (`ladder._load`: `shadow.pending_body(rec)[0] == p`; a file that cannot
be loaded counts as different; editing even `created` is enough), `not backed: proposal file differs from the approved copy`; (6) the newest
approvals row does not back the copy (`approvals.verify_copy(copy, rows) != "ok"`, the test behind `ladder.check`'s "copy not backed by the
approvals chain"): `not backed: no approvals row (approve --mode observe records it)` if the proposal has no row at all (a copy from before
the chain, observe included), else `not backed: newest approvals row is <mode>` (the newest row names another `proposal_sha256` than the
copy, or the copy's mode differs from the row's). `measure.resolve` returns the outcome of (2), (5) and (6) as `backing` (`ok`, `chain`,
`proposal`, `row` or `none`). The hook runs observe for an unbacked copy of a higher mode; at `block` there is nothing left to promote to,
`block (top rung)`; under 14 days since `window_start` (the counting window `modes.resolve` gives the hook and the ladder: the newest
approvals row, or a later counter-resetting demotion) is `<n> earliest YYYY-MM-DD`; fewer than five human TPs in that window is `needs 5
human TPs (has k)`; five or more labels with more than half false is `noisy: add an exception or retire`; any FP in the window is `not
eligible: k FPs (<n> needs 0)`; a detector that is not strong is `<m> only: weak detector`; a catalogue `compilable` ceiling below `n` is
`<m> only: detector allows <compilable>` (`observe only: detector allows observe` for a strong detector that may not reach warn; `warn only:
detector allows warn` for one that may not reach block); a block promotion of a proposal whose event is not `PreToolUse` is `warn only:
block is for PreToolUse`; otherwise `<n> eligible` (`warn eligible` or `block eligible`). The window counts (`tp_window`, `fp_window`) are
the ladder's: human `tp` rows only, live fires in the proposal's CURRENT mode `m` since `window_start` (a warn-mode proposal counts its
warn-mode fires, not the observe-mode fires it earned before it was promoted); each `proposals[pid]` also carries `event` (the proposal's,
`-` when unknown, which is never `PreToolUse`) and `compilable` (the catalogue's) for these checks, `cap` (the lowest `to_mode` of the
demotion rows in force, null without one) and `approvals_mode` (the mode of the newest approvals row, `none` without one) for the refusals
above, and `next_rung` (`warn` from observe, `block` from warn, null at block) with `next_earliest` (`window_start` plus 14 days, null
without a window or at block), which the digest reads. `to_label` is the number of that proposal's fired rows that no human `tp`/`fp` covers
(one per row id): the label queue rows of the proposal INCLUDING `tp_candidate` rows (a candidate covers nothing) and INCLUDING the rows of
a retired proposal, which the default queue hides (`queue_retired`), so the `to_label` of all proposals sum to `queue_total +
queue_retired`. It is the N of `label N fires`. `tp`, `fp` and `unlabelled` stay the report's, so they equal `compile report --json`
(`unlabelled` is the report's count of rows with no label of any kind, so it can be lower than `to_label` by the `tp_candidate` rows).
`auto_fp` is the number of fires in the proposal's window (live, current mode, since `window_start`) whose only label is an automatic `fp`
(an offline-pass `reason_code` of `labels.AUTO`, no human row), and `auto_fp_oldest` the 8-character row id of the oldest such fire (`label
--rows` prints it), else null. The ladder counts such a fire as an FP (`ladder._counted`: `fp` from any reason code) while the label queue
does not list it (an `fp` covers the row), so it can hold a promotion back with no row to label; when `auto_fp > 0` the verdict (any but
`retired`) gains the suffix `; k auto FPs (label --tp <id> overturns)`: a human `tp` on that row, newer than the automatic one, overturns it
(`ladder.effective` prefers a human row), after which the fire is no FP and no auto FP. This is a mention only: there is no queue flag for
these rows. Known gap: one `ladder.run` check (validate) and three `ladder.check` checks are not repeated by the verdict, and the
read-only verdict does none of them: it does not re-run `proposal.validate` (the replay and validation of the proposal against the current
code, which `ladder.run` runs before `check` and refuses on; the verdict of a proposal whose evidence cards were deleted, so that validation
fails at `schema`, still reads `warn eligible`, pinned by a test), it does not re-check that the lesson is `active`, that the detector's
`source_sha256` still matches the proposal's, or the integrity of the labels (forged, dated before their fire, dated in the future),
although `ladder.check` does the last three at approval time and the nightly demotion pass covers the first two with a delay. Replaying
validation on every status call is deliberately not done: the verdict is a read-only display, so a verdict of `<n> eligible` means the
counts and the copy are right, not that `approve` will go through.
An approved copy with no shadow
row is added with zero counts and its own rates from `metrics.compute`, because the report lists proposals with rows only. The `heartbeat`
block (last hook row, last live fire, `offline_state.json` mtime, last good nightly run, notes such as `chain_key:missing`,
`approvals:broken:<where>` and `silent:<detector>`) says whether the instruments are alive; `silent:<detector>` needs a live mode,
eligible sessions, no fires AND no hook row at all (a row that is not a scanned fire is awaiting the scan, truncated or a
disagreement, never silence), and `agreement.awaiting_scan` counts the fires waiting for the offline scan. `measurement.start` is the
greatest, over proposals whose newest approvals row is not a revocation, of each proposal's newest `observe` approvals row (the ladder
restart point, section 7.3): a warn or block promotion does not move it, and neither does a demotion to disabled. `seal --dry-run`'s
`measurement_start` is the same value: both call `measure.start_of` (`compile/measure.py`, one pure function over the approvals rows), so
they are equal at all times and a promotion never moves either.
The queue (`queue`, `queue_total`) lists the fired rows
that no human `tp`/`fp` covers; the fires of retired proposals are not listed but counted in `queue_retired` (`label --queue --all` offers
them). `compile digest [--since ISO] [--days N]` (`compile/digest.py`) renders the same dict as seven
plain-text blocks (the seventh, `proposals`, is the watcher's, see "8.x Watch (as built)"): `status` (protocol `shadow-v1`, report hash,
day, keyed, chains), `needs you` (the label queue and the command to
work it), `detectors` (the status table), `health` (silent detectors, stale heartbeats over 36 hours, a spike when a proposal has at
least 3 rows today and more than 3 times its daily mean over the window, chain notes), `changes` (approvals and demotions after
`--since`, default the last digest logged, else 24 hours) and `next` (week-one date, and for each proposal still short of the 14 days
the earliest date of its next rung from `next_rung` and `next_earliest`: `warn earliest` for an observe-mode one, `block earliest` for a
warn-mode one, nothing at block). Each run appends one JSON line
`{ts, report_sha256, queue, notes}` to `data_dir/digest.log` (0600, O_APPEND). `hooks/nightly_sweep.py` runs it quietly AFTER the sweep (so the
line carries that night's health), refreshes `status.json` for the status line and writes the log line, so a missing line is a missed night.

| Surface | Entry | Shows |
|---|---|---|
| Dashboard | `compile dashboard [--days N] [--out PATH]` | one static HTML file (0600) from `status.build`: day, tiles, table, Wilson bars |
| Status line | `hooks/statusline.sh` (Claude Code `statusLine`) | one line from `status.json`: approved, unlab24h, unlabelled, chains, heartbeat, watch |
| Menu bar | `hooks/swiftbar/ace.5m.sh` (SwiftBar, 5-minute refresh) | glyph (ok / needs you / stale), counts, three proposal titles, two buttons |

### 8.x Watch (as built)

The watcher of design `2026-10-21-ace-watcher-and-cockpit` (phase 1, W1) is `compile/watch/`: a model-free configuration watcher that
PROPOSES and never applies. It is the one network-using package of ace-playbook and is never on the hook path: `watch`, `fetch`,
`inventory`, `match`, `store`, `summary` and the helper modules `chars`, `fsio`, `cssstyle`, `cssscan`, `htmlrules`, `mdrules`,
`stylepass`, `treebuild`, `treemodes`, `treestyle`, `treewalk`, `records`, `pin` and `reconcile` are in the `ABSENT` tuple of
`tests/test_hook_conformance.py`. `status.py` imports only
`watch.summary` (a pure read, no network import); `__main__` imports the light `watch.cli`, which loads `scan`, `fetch` and `inventory` only
when a scan runs (tested in `tests/test_compile_watch_cli.py`).

- `fetch.py`, `parsers.py`, `sanitize.py`: tier-0 fetchers. Conditional GET (ETag, sha256 against `seen.json`), a Content-Type allow-list
  checked before the body is read, a 1 MiB cap (`cap + 1` bytes read), http(s) only. Parsers for RSS, Atom, npm dist-tags, the
  deprecations table and sitemaps. `strip_hidden` keeps visible text only: no script, style, template, noscript, comment, `hidden`,
  `aria-hidden`, `inert`, zero-width or bidi characters, and inline styles read after normalisation (comments and CSS escapes removed):
  display none, visibility hidden or collapse, opacity below 0.1 (negative too), font-size below 1px, a box of 1px or less that clips,
  off-screen positions and far negative margins or text-indent (500px or 20em and beyond), `clip-path:inset()` at 50 % or more, any zero
  `scale` argument, a transparent `color` or `-webkit-text-fill-color`; also skipped are a closed `details`, `dialog`, `svg`, `math`, `iframe`,
  `object`, `embed`, `head`, `select` (all its option text), `progress` and `meter`. XML with a DTD or an ENTITY declaration is dropped (`xml`).
  Also: a `Content-Encoding` other than identity is dropped (`encoding`); a 304 for a URL with no stored validator is dropped (`etag`);
  a deprecations page that parses to no rows is a `parse` drop (never an empty table); the `md` kind is read in markdown mode
  (the HTML rules, except that a raw-text opener is prose when it is in a fenced block or in a code span that opens and closes on ONE plain
  line, outside every HTML block, and no closer for it follows later; a plain line has no block marker at its start, no backslash before a
  backtick, no `<` before the opening run and no `|` inside the span, and a fence counts only when it starts at the left margin: an
  indented fence may sit in a list item, which ends it, so its lines keep their HTML meaning; link reference definitions, the
  `[//]: # (...)` idiom among them, are dropped ONLY when strictly valid per CommonMark: `mdrules.DEFINITION`, a label on one line, a
  destination, an optional title of at most three lines, nothing else on the last line, no line after the first that a paragraph could
  not continue, and never right after paragraph text. Anything else stays and keeps its HTML meaning, so `[x]: /u <script>` hides to its
  closer and the HTML start is checked before any definition continuation: `mdrules.py`; for a `md` source served as `text/html` the
  strictly valid definition lines are dropped from the VISIBLE text after `strip_hidden`, never from the raw markup).
  The sanitizer builds ONE HTML tree and two consumers read it (no `_Text`/`_Styles` pair, no rule stack). `htmlrules.py` is the
  tokenizer side: html.parser as the tokenizer (`ScriptedParser`: comment endings, the double-escaped script state `ScriptState`, `<![CDATA[`,
  a slash on a start tag kept in `selfclosing`), the element sets, `OPS` (what a start tag of the simple kind does in the body) and
  `TreeParser`, which feeds the tree and lets IT decide raw text (`Tree.raw`: only an HTML element enters raw text, RCDATA or noscript; an
  svg `<style>`, `<script>` or `<title>` is an element). `treebuild.py` (`Tree`, `build`) is the HTML Standard's tree builder in a
  simplified, fail closed form: the stack of open elements with indexes (HTML positions by name, foreign name counts, scope-marker
  positions for the default, list-item, button and table scopes, so a scope check and an end tag that names nothing open are O(1)), the
  active formatting list with markers (pushed when a td, th, caption, applet, marquee, object or template opens; cleared up to the last marker ONCE:
  at the spec's close-the-cell and caption close for td, th and caption, at the pop of the template, at the own end tag for applet, marquee and
  object, never per popped cell),
  Noah's Ark, `reconstruct` and a simplified `adopt` (a formatting element with a furthest block stays open: over-hiding), FOSTER PARENTING
  in `where`/`place` (what a table cannot hold goes before it), and the insertion mode derived from the stack (`reset_mode`). `treemodes.py`
  holds the modes as functions `mode(t, kind, name, attrs, selfclosing)`: initial, before html, before head, head, after head, body, text,
  one function for the table family (table, caption, table body, row, cell, colgroup), select, template, after body, after after body, and
  `m_foreign` for foreign content. A frameset that replaces the body, or a `<template shadowrootmode>` whose host is `html`, `head` or the
  document, sets `Tree.dead` (nothing is shown); `Tree.hb` records every `html`/`body` start tag (a hiding one hides the page). Past 512
  open elements the build raises `TooDeep` (a `parse` drop). The two consumers: `treewalk.visible` (iterative; an element hides when
  `own_attrs` says so or a style rule matches its tag, class or id; `br` and `hr` print a blank, blocks a line, the nodes the builder made up
  print none) and `treestyle` (below). The rules that matter: a block start tag of the `p`-closing set (address, article, aside,
  blockquote, center, details, dialog, dir, div, dl, fieldset, figcaption, figure, footer, form, h1 to h6, header, hgroup, hr, main, menu,
  nav, ol, p, pre, search, section, summary, table, ul, xmp, li, dd, dt) closes an open `p` in button scope first, and a `table` only in
  standards mode; `li` and `dd`/`dt` close their kind; `button` stops `</p>`, `ol` and `ul` stop `</li>`, an end tag past a special
  element pops nothing, `</template>` pops to the template whatever lies between. The quirks decision is the FIRST doctype alone
  (`htmlrules.STANDARDS`: `<!DOCTYPE html>` with an optional `SYSTEM "about:legacy-compat"`; none, a late, legacy or later one is quirks,
  fail closed). Foreign content: `foreign_dispatch` decides per token whether the HTML modes or `m_foreign` read it (at an HTML integration
  point only a start tag or characters go to the HTML modes, at a MathML text point all but `mglyph`/`malignmark` and `<svg>` at
  `annotation-xml`); a breakout start tag (the spec's list, `font` with `color`, `face` or `size`), `</p>` and `</br>` pop the foreign run
  and are read again by the mode (not by `process`, which would loop); any other foreign end tag walks down the stack by name; a foreign
  element written self-closed is popped at once, an integration point too. Every node carries its NAMESPACE (0 html, 1 svg, 2 math) and its
  integration kind `ip` (0 none, 1 HTML: svg `foreignObject`, `desc`, `title` and `annotation-xml` whose `encoding`, lower cased and NOT
  trimmed, is `text/html` or `application/xhtml+xml`; 2 MathML text: `mi`, `mo`, `mn`, `ms`, `mtext`; 3 `annotation-xml` without an HTML
  encoding); `htmlrules.special(node)` asks the special category by namespace. An open select is built in two readings (`Tree.relaxed`):
  A, the older, ignores start tags and keeps the tokenizer out of raw text; B, the newer and the one the visible text uses, inserts them
  and ignores every end tag but `option`, `optgroup`, `select` and `template`, keeps a nested `select` open, lets `input`, `keygen` and
  `textarea` pop the select only when `treemodes.reach` finds no template, table, integration point, foreign or hidden element above
  it, and lets a raw text holding a `<` poison the rest of the page (`selraw`, `rawlt`, `poisoned`; `script` exempt). `</select>` needs the select
  in scope under the newer reading, so an open select that held a scope marker or a formatting element hides the rest even after that element
  closed (`<select><object></object></select>VIS`, `<select><b hidden></b></select>VIS`: a browser shows VIS under both readings, we hide:
  over-hide, by the rule that every end tag is ignored but option, optgroup, select, template). Document-level
  hiding: an `html` or `body` start tag, the first or a late one, that hides, or a `<style>` rule that hides `html`, `body` or `*`, makes
  the body empty; a `<template shadowrootmode=...>` hides every child of its host (those before it too). THE STYLE PASS (`treestyle.py`,
  re-exported by `stylepass.py`): the sheet of a page is the child text of the `style` elements of the tree in any namespace (HTML: raw
  text; svg: character data, a CDATA section counts, a comment does not, character references are decoded), `style_sheets(src, tree)`,
  with a `<select` also from the second reading; it is UNIONED with `scan_sheets`, a plain linear scan that starts a sheet at EVERY
  `<style` opener (fake openers included, so it can only add rules), ends its start tag at the first `>` and at the first `>` outside a
  quoted value (`TAG_REST`) and, through `variants`, also reads each scanned sheet with the CDATA markers removed, with the references
  decoded and with tags and comments dropped (applied to scanned sheets only). More than `MAX_OVERLAP` (1000) openers inside another
  sheet or `MAX_SCAN_CHARS` (4 MiB) of scanned text is a `TooDeep` parse drop. There is no nearest-name end-tag matching in the style
  pass any more. `cssstyle.css_rules` reads the braces in one linear pass (`cssscan.py`: braces, semicolons and
  comment openers inside CSS strings, escapes and unquoted `url(...)` (the ident matched after escape decoding: `\75rl(`, `u\72l(`) do not
  count, nor do they inside a `(` or `[` simple block (a `blocks` stack of closers in `cssscan.rules`; unclosed blocks swallow the rest); a
  rule with nested blocks is read with them cut out and the nested rules on their own; a rule open at the end applies; CSS escapes in a
  selector are decoded and the list splits on unescaped commas only). `<![CDATA[` (any `<![`) is a bogus comment that ends at the first `>`
  in HTML content, a real section to `]]>` whenever the current node is NOT an HTML element (`ScriptedParser.parse_html_declaration`,
  `foreign()`; `_support_cdata` is off so the EOF path agrees). `noscript` is raw text (`CDATA_CONTENT_ELEMENTS`, also for `<noscript/>`)
  and stays in the skipped set, but only in HTML content. `handle_startendtag` enters raw-text mode for every `CDATA_CONTENT_ELEMENTS` name
  (`<xmp/>`, `<iframe/>`, `<noembed/>`, `<noframes/>`), for the RCDATA ones (`<textarea/>`, `<title/>`) and `<plaintext/>`: a browser
  ignores the slash. Comments end where a browser ends them (`<!-->`, `<!--->`, `-->`, `--!>`) on every patch release:
  `htmlrules.ScriptedParser.parse_comment` overrides the stdlib's (which pairs an abrupt `<!-->` with a later `-->`) for the one pass;
  the page is not rewritten, because `--!>` ends nothing in script data, an attribute value or RCDATA. A leading byte order mark is
  dropped in ONE place: `fetch` decodes `utf-8-sig`, as the Encoding Standard strips one BOM before the tokenizer; `strip_hidden` removes
  none, so a U+FEFF that remains is a character token in the initial mode and the page is quirks, as in a browser. In Markdown the `<` of
  every tag that raw-texts or hides its content (`mdrules.RAW_TEXT`: the raw-text names plus noscript, details, select, svg, math,
  template, object, dialog, canvas, video, audio, datalist, head, rp, progress, meter) is escaped inside a trusted code span or fence.
  Kept over-hides, each on purpose: a nested `select` stays open, a raw text a select holds with a `<` hides the rest of the page, the
  select enders are gated by `reach`, a furthest-block formatting element stays open, `a`, `nobr`, `button`, headings, `option` and
  `ruby` have no start-tag rules and `form` has no pointer (`</form>` pops only a current form), `colgroup` is read in the table mode
  and a template routes table tags there and the rest to the body, a closed `details` hides its summary too, and the scan variants hide
  a CDATA-wrapped HTML style a browser would show as text. The cases the tree decides that the rule stack got wrong are the six Opus
  wave-7 worlds (BOM before the doctype, svg CDATA style, nearest-name end tag, `annotation-xml` encoding, self-closed integration
  points, frameset) and the older foster-parenting gap. In Markdown a `===`, `=` or `--` line with no paragraph open is paragraph text (the paragraph flag of
  `mdrules.scan` stays set); only a heading, a thematic break or a setext underline under an open paragraph ends it, and such a line inside
  a quote or a list item is a lazy continuation, not an underline (the `cont` flag: set by a `>` or list-marker line, a bare marker (`-`,
  `1.`) included unless a lone `-` underlines the paragraph above it, kept over blank and
  indented lines, cleared by an unindented non-marker line after a blank line or by an unindented heading or thematic break). The text and tails inside
  a child element of an XHTML feed field are HTML-escaped before the sanitizer sees them (only the field's own text stays raw), so an
  escaped closer there cannot close a hidden ancestor. The npm `latest` must be an ASCII version (`[0-9]+.[0-9]+.[0-9]+` with an optional
  `-` or `+` suffix of at most 40 `[0-9A-Za-z.-]`), else the fetch is a `parse` drop.
  Helpers: `chars.py` (every Default_Ignorable_Code_Point, the format characters and the control characters, shared by the sanitizer, the
  store and the record cleaner), `cssstyle.py` (the
  inline-style normaliser and the simple single-selector `<style>` rules, with the legacy `<!--` and `-->` tokens ignored; media queries,
  descendant or attribute selectors and external stylesheets are not handled, see LIMITATIONS.md), `cssscan.py` (the string-, escape- and
  url-aware brace scanner, comment stripper, nested-block reader and selector decoder that `cssstyle` uses), `fsio.py` (private atomic
  write with a unique O_EXCL temporary name, the scan lock).
- `netguard.py`: the SSRF guard. A host that is, or resolves to, a loopback, link-local, private, multicast or reserved address is dropped
  (`address`) before any request; a redirect is followed only to the same scheme, host and port, at most three hops, with no userinfo in the
  URL, and is dropped (`redirect`) otherwise. The DNS answer can change between the check and the connect (rebinding); the address is checked
  before the connection, not on the connected socket, so that race stays.
- `inventory.py`: the snapshot (`inventory.json`, 0600). Per target of the private map only the matching LINES with numbers plus the
  file sha256; `{"missing": true}` for a missing file; at most 50 files per target, a target with more is marked `truncated` and its
  `model_pin` becomes a lead with no diff; `claude --version` only when `claude` is on PATH.
- `match.py`: the rules. `model_pin` (a deprecated model id the inventory pins: a diff generated by code replacing it with the
  successor, else a `lead` with no diff titled "retirement with no successor named"), `version` (npm `latest` or `stable` newer than the
  installed `claude`), `docs` (a mapped page's sitemap `lastmod` moved, unless it is more than a day ahead of the scan, or its `.md` hash changed; a first
  sight is a baseline, and a future `lastmod` is never remembered) and
  `lead` (a tier-1 feed entry with a `lead_keywords` term). `check` enforces the record shape, the caps, the id, the diff hash, the
  verify templates and "tier-1 evidence alone never carries a diff". `match.py` is the entry (`candidates`); the pieces are `records.py`
  (`record`, `make_id`, `check`), `pin.py` (the `model_pin` rule, `state`, `pinned`, `model_of`) and `reconcile.py` (below).
  `change.target` is never page text: it is an inventory key, a mapped docs URL, a source id or `claude_version`, and a docs title is
  built from the mapped URL. `inventory.py` splits files on `\n` (keeping `\r`) and sets a `truncated` flag on a file cut at the line cap;
  a `model_pin` whose target file is `truncated` (or too large, or has a cut or U+FFFD line) is written as a `lead` with no diff.
- `reconcile.py`: what one scan does with its candidates. Dedupe keys: a `model_pin` by (class, target, model id, successor), so editing
  the row of the deprecations table (a new date, "(extended)") opens nothing; a model_pin whose target and model a candidate also names
  with another successor, and every older duplicate of the candidate's key, is closed with the note `superseded` when the candidate
  opens; any other state-based class by (class, target, quote, model id), i.e. the state it reports, so two ids in one table cell stay two
  proposals (the id of a record hashes the model id when its title names one); a `docs` proposal by (class, target, evidence sha256), so
  a page that changes again after a dismissal is new. A `version` proposal auto-closes only when `claude --version` printed a real
  version (`match._ver` finds `N.N.N`; garbage with exit 0 closes nothing). A state-based proposal that expired or auto-closed while
  its trigger is still true gets a `reopened`
  decision row (its note is the new 30-day expiry; the proposal file is immutable) and counts toward the cap of five. A scan holds the
  exclusive `scan.lock` in `watch_dir` (a second scan prints `watch: scan running`, exit 0), and the `queue` command takes the same lock
  (blocking) for its rebalance.
- `store.py`: `watch_dir` (config key, default `data_dir/watch`, 0700) and everything in it. `summary.py`: the `status.json` fields and
  the digest block. `scan.py`, `cli.py`: `compile watch scan [--tier 0|1|all] [--dry-run] [--force]`, `status`, `queue [--limit N]`.

Record (design 4.3, exactly): `{id, created, class, elevated, title, evidence[{url, sha256, quote, tier}], inventory_hits, change{target, diff,
diff_sha256}, verify, expires, status}`. `id` is `W-` plus the first 8 hex of sha256 of `class|target|evidence sha|quote` (with `|model id`
appended when the code-made title names a model), so a re-scan dedupes; `title` is at most 120 characters and `quote` 400, both cleaned
of control characters, and the quote is ONE line of the fetched body, verbatim (`records.quote_line`, empty when no line is clean, which
drops the record; `match.candidates` runs `check(rec, body)` with the body of the result that carries the sha256); `verify` comes from a fixed template per
class (`grep -n "<model_id>" <path>`, `claude --version`, `curl -sI <url> | head -1`); a model id or successor taken from a page must match
`[A-Za-z0-9][A-Za-z0-9._:-]{2,79}` before it reaches a diff or a command; `elevated` is false for every W1 class; `expires` is 30 days
after `created`. Free text from the web appears in a record only as the title and the quote.

Store files, all under `watch_dir` and nowhere else: `proposals/W-*.json` (0600, written once, never replaced), `decisions.jsonl`
(append-only, 0600, O_APPEND, flock: `{ts, id, decision: accepted|dismissed|later|closed|promoted|reopened, note}`), `seen.json` (per-URL sha256,
ETag and `fetched_at`; the parsed result of every source; per-source stamps for the daily or weekly cadence; sitemap `lastmod` baselines;
`last_scan`), `inventory.json` and the kill file `DISABLED`. The status of a proposal is its newest decision row, else the file's own
`status`; `expired` once past `expires`; `auto-closed` when a re-scan no longer produces a state-based trigger (model_pin, version, an
inventory lead) while its source was read. Docs and feed-lead proposals are events and run to their expiry. At most five proposals are open
(leads excepted); the rest are written `queued` and a `promoted` decision row opens them in ranking order (model_pin, version, docs) when a
slot frees. A reader never raises on a corrupt store: JSON too deeply nested, bad UTF-8, a decision row of the wrong type and a proposal
of the wrong shape are skipped, and the proposals skipped are counted in `status.json` as `watch_corrupt` (`corrupt N` in the digest block);
a proposals path that is a file or a link, a `decisions.jsonl` that is a directory or a link, and a `watch_dir` that is a link count as one
each (the last so that the tile says `none  corrupt 1`, not a bare `none`, while every scan exits 2).
`ensure` keeps the mode of a directory that exists and refuses a symlinked `watch_dir` or proposals folder (`compile watch scan` exits 2). With
`DISABLED` present `scan` prints `watch: disabled`, fetches nothing and writes nothing.

`status.json` gains `watch_open`, `watch_leads`, `watch_oldest`, `watch_top`, `watch_titles` (up to three, ranked), `watch_disabled` and
`watch_last_scan` and `watch_corrupt`; the digest gains the `proposals` block (`open N  leads N  oldest <age>  last scan <age>` and up to three `- <title>`
lines, `none` or `disabled`); the status line ends with `watch N`. Nothing here touches `metrics.py`, `report.py`, `seal.py`, the detector
files or the live measurement.

### Seal (as built, not executed)

`python3 -m ace_playbook.compile seal [--dry-run]` (`compile/seal.py`) is built; the pre-registration is not sealed, and the
section in PREREGISTRATION.md still says `(DRAFT until sealed)`. The sealing itself is the go/no-go decision, taken after the observe week.

- `--dry-run` needs no terminal, asks nothing and writes nothing. It prints the Frozen instruments block with live values:
  `detectors:` (one `name strength sha256` line per `catalogue.source_table()` entry, sorted; the strength is `Detector.strength` of
  the registry), `report_sha256` (the bytes of `compile/metrics.py` then `compile/report.py`, joined by a NUL: exactly those two files;
  the detector code is covered by the per-detector hashes), `proposals:` (the newest `approvals.jsonl` row of each proposal id that is
  not revoked, as `id mode proposal_sha256`, sorted; `(none)` when there are none, or a notice line when the chain is unreadable or
  broken, still exit 0), `shadow_seed_sha256`, `arm_rate`, `measurement_start` (`measure.start_of`, the same value as `status`'s
  `measurement.start`: the greatest, over the listed proposals, of each one's newest `observe` row `ts`; `-` when none qualifies) and `sealed_at -`.
- Without `--dry-run` the checks run in this order, and the first failure ends the command with nothing written: (1) no terminal: exit 2;
  (2) the file does not hold exactly one `## Shadow-mode additions` heading (counted loosely: an indented or double-spaced heading also
  counts): exit 2; (3) the heading already says SEALED (later changes go in dated amendments; this wins over every later check): exit 1;
  (4) the Frozen instruments subsection (that heading to the next `###`, inside the shadow-mode section) does not hold exactly one
  `(filled by compile seal)` line: exit 2; (5) the Timing and stopping subsection does not hold exactly one line `N per detector: <value>` (a value containing a number)
  that is not the placeholder `(filled by hand before the seal)` (a deleted line, the placeholder or a second line all refuse), or the
  placeholder is still anywhere in the shadow-mode section: exit 1; (6) the approvals chain is broken or unreadable: exit 1; (7) the
  block holds `shadow_seed_sha256 -` or `arm_rate 0.5 (planned)`: exit 1; (8) the y/N answer is anything but yes: exit 1; (9) the file
  changed while the prompt was open: exit 1; (10) the write: the matched heading is rewritten by its position (never by text search) to
  `## Shadow-mode additions (SEALED <date> prereg-v1)` and the filler line is replaced with the block in a `text` fence, through a
  fsynced temp file in the same directory and `os.replace`.
- It prints the two commands `git -C <repo> tag -a prereg-v1 -m "pre-registration sealed <date>"` and `git -C <repo> push origin prereg-v1`
  for the operator to run; it never runs git and imports no `subprocess`.
- Until the `shadow_seed` and `arm_rate` config fields land with the executed seal (the next version), `shadow_seed_sha256` is `-` and
  `arm_rate` prints `0.5 (planned)`, and the real seal refuses (exit 1, naming `shadow_seed_sha256` and `arm_rate`) before it asks anything.

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
| Tamper evidence | hash-chained `approvals.jsonl` (HMAC-keyed from `v: 2`) and `demotions.jsonl`, hash-chained `labels.jsonl`, TTY-only approve and label; hook loads only hash-matching files; report flags approved files whose hash or mtime changed without a ledger record | edited approved file is ignored |
| Label forgery | `approve --mode warn|block` lists every counted TP and FP for TTY confirmation, re-derived from `shadow.jsonl`; broken chain or missing fire row refuses | forged label without a fire row; broken chain link; label appended after the last approval |
| Bounded cost | input, file count, file size and state caps; oversize means skip | oversize fixtures |

Residual risks, stated plainly: the agent runs with the user's permissions and can edit
`data_dir`, so it could append a demotion or delete an approved file to evade a block; the controls
above make that visible in the report, not impossible. An operator approval lifts demotions (every row stamped strictly before the
newest approvals row); the agent cannot write a valid approval row without the TTY, except by reading, replacing, redirecting or deleting the chain key, the
stated residual. The same holds for **label forgery**: the
agent can append a `tp` label, so promotion rests on the human reading the TP listing at `approve`,
not on the file. The same holds for **approvals forgery**: the key is readable by the operator's user, hence by the agent. With a readable key
the chains (`approvals.jsonl`, `demotions.jsonl`) accept only keyed `v: 2` rows and a signed head file pins their last row, so a chain
cannot be downgraded to plain hashes, cut or rolled back by a process that lacks the key. What a keyed row proves is that its writer READ,
REPLACED or REDIRECTED the key: it can read the key file, swap the file or its directory, or point `chain_key_path` in `config.json` at a key of
its own and re-sign both chains and the heads. The operator's permission layer can deny that (hooks/INSTALL.md section 7: Read, Edit and Write on
`~/.config/ace-playbook/**`, plus the config file), but a deny rule covers the tools it names, not a shell command: the substring guard
does not catch `cd ~/.config/ace-playbook; mv chain_key x`. Heads close tail truncation and rollback to an earlier approved state; they stay
open to a same-user process outside the permission layer (it can replace key, heads and chains together, or restore an old chain together with
its old heads file) and to deleting BOTH the key file and the heads file, which returns the install to the unkeyed state and its v1-only rule
unless `ACE_PLAYBOOK_KEYED=1` is set in the hook command in the operator's settings (deleting only one of the two leaves the install keyed and
both chains broken). `shadow.sh` and `ace_playbook` itself live in an agent-writable checkout, so the flag raises the bar, but the permission
layer is the real boundary. Deleting the chain files themselves disables rather than promotes WITH a key (an empty approvals chain approves
nothing, a head that does not match breaks the chain, and a broken demotions chain caps at observe); before `keygen`, deleting
`demotions.jsonl` lifts its caps. Until `keygen` has run the chains are unkeyed and any local process can append a row. Block mode is the first time ace-playbook hooks can stop the agent, which
contradicted SECURITY.md:36 ("Hooks never block a session"), the "hooks fail open and stay out of
the way" wording at README.md:79 and the `onFailure` paragraph at hooks/INSTALL.md:96-97. All three
were amended on 2026-10-19 (see Amendments): stage-1 hooks never block; stage-2 shadow hooks block
only a proposal the operator promoted to block mode after the observe and warn ladder.

Tamper matrix for the chains and the signed heads (splice, cut line versus modified row, deleted files, replayed heads,
re-signed rows): `tests/test_chain_tamper.py`.

## 10. Build order

| Step | Deliverable | New tests |
|---|---|---|
| 1 | `textrules.py` move (prerequisite, 7.2); `events.py`, `detectors.py`, `state.py`: 8 catalogue detectors, both adapters, parity with the frozen step0 fixture JSON | 47: 8 detectors x 4 fixtures (32), 6 adapter, 5 state (incl. `LOCK_NB` skip), 2 parity (frozen JSON, stale-file check), 2 textrules (re-exports identical, import hygiene: no `subprocess`/`sqlite3`) |
| 2 | `evidence.py` + `compile cards` | 15: admission rules, quote only in tool output rejected, self-report-only refused, scrub hit masks and sets non-replayable, idempotent ids, offsets within user messages; legacy path: `legacy:` evidence refused without a human incident label, admitted with one and flagged `provenance: legacy`, re-minted lesson takes the standard path |
| 3 | `proposal.py` + first proposal for `rm_rf_nonscratch` (lesson g-010, mapped to `L-b3d1fba9`, ace/migration/id_map.jsonl:96, migration applied 2026-10-19); the real proposal needs the legacy card or a re-minted lesson (section 5) | 18: schema, 5 sanitiser rejects, replay must fire, regressions must not, scrub, containment, approve refuses without TTY or review, hash chain, legacy-card banner and second confirmation, `Stop` with mode above observe rejected, end-to-end under a temp HOME proving nothing outside `data_dir` changes |
| 4 | `shadow.py`, `hooks/shadow.sh`, INSTALL.md section; the operator installs it by hand in observe mode (settings writes are hook-guarded in this setup) | 26: three modes and their stdout/stderr output, deny and block JSON shape, `stop_hook_active`, malformed or empty stdin, missing session id, kill switch, unapproved or hash-mismatched or symlinked proposal ignored, oversize skip, no-network imports, no payload text in rows or stderr, arm determinism, shell script inspection (no `exec`), 7 wrapper tests (`ACE_PYTHON=/usr/bin/python3`, no argument, nonexistent interpreter, crashing module, stdout empty-or-valid-JSON, exit 0 always, and under `ACE_PYTHON=/usr/bin/python3` a planted `json.py` in the working directory is not imported), locked state file skipped within 50 ms, `edit_unread_session` capped at observe until labelled |
| 5 | `offline.py` + `report.py`: nightly signal detectors, label pre-fill, demotions, two tables. **5a landed 2026-10-19** (`offline.py`, `demotions.py`, `compile offline`, the hook honouring demotions, the `tp_cand` column; 88 new tests in `test_compile_offline*.py` and `test_compile_demotions.py`); **5b** (the promotion ladder with its TP listing) and **5c** (synthetic replay, the report with rates; no inference) **landed 2026-10-19**; arms, randomization inference and the spillover flag follow | 25: auto-FP rules (a) correction after a message, (b) target in the last prompt, (c) shell-mode rerun (3); auto-TP (a) existing hook blocked, (b) correction after an observe fire (2); unlabeled counts neither side (1); agent self-tags and citations never inputs (1); human label precedence (1); evasion candidates flag without demoting, and the `/tmp/..` shape (2); disable triggers: lesson leaves `active`, `source_sha256` change, error rate or p95 (3); demotions: block to warn on one FP, warn to observe on one FP, two FPs in 30 days to observe with re-review (3), counters reset after a demotion (1); promotion rule check and TP listing with forged-label refusal (3); randomisation inference: seeded permutations deterministic, rate ratio and Wilson per arm (2); corpus mixing refused (1); synthetic and private tables separate (1); state files deleted seven days after the last event (1) |
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
   *Answered 2026-10-19 from the hooks reference (code.claude.com/docs/en/hooks, "Exit code 0"):
   "Claude never sees stderr from a hook that exits 0", and plain stdout on PreToolUse goes to the
   debug log only. The model-visible channels are `hookSpecificOutput.additionalContext` (exit 0,
   inserted as a system reminder next to the tool result) and exit 2 / `permissionDecision: deny`
   (blocks). So warn must emit `additionalContext` JSON on stdout; the stderr write in `shadow.py`
   is kept only as the user-facing echo. This lands with the ladder (step 5) before any warn
   approval. Confirmed live on 2026-10-19: a throwaway project-local PreToolUse hook printing one marker
   through `additionalContext` and another on stderr (exit 0) was run in a headless session; the model
   reported the `additionalContext` marker and nothing from stderr.*
   *Resolved in code (step 5b): `shadow._run` prints one `additionalContext` JSON object for warn hits. The live check covered
   PreToolUse and, since v1.5, Stop: a probe hook in a scratch project under `claude -p` showed the Stop `additionalContext`
   recorded as a `hook_additional_context` attachment and the model re-prompted with it (the turn continues; it re-fires on
   every later stop unless the hook stays silent when `stop_hook_active` is true, which `shadow._run` does). No Stop detector
   is approvable today. The Stop JSON is still emitted.*
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
   *Note 2026-10-19: re-minting through `store promote` does not open the standard path as built.
   The promote PATCH records session `promote-cli`, which has no transcript, so `_lesson_cards`
   returns `transcript_missing` (evidence.py). Only a user message in a real session, attached as
   evidence with that session's id, would. As of 2026-10-19 `compile cards --lesson L-b3d1fba9`
   reports one `legacy_unlabelled` ref (a later eligible session where `rm_rf_nonscratch` fired on
   a scratchpad path reached through an unresolved `$VAR`); whether that counts as the incident is
   the operator's label decision.*

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

`report_sha256` changed from `3c94c88235c2...` (main, 198ea65) to `5c9cb343...` (branch feat/status). `report.run` was split into
`collect()` and `run()` (behaviour-neutral: for the same inputs the `--json` dict and the text are byte-identical, checked against the main
tree; `tests/test_compile_report.py` pins that `run` prints exactly the dumped `collect` dict and `render` of its parts), and
`metrics.upper95_zero` was added for the status page. The seal has not been executed, so no committed hash is broken; the operator can
object before `prereg-v1`.

The `status.json` key `warn_earliest` was renamed `next_earliest` (with the new `next_rung`) on branch feat/measure, because the verdict
became rung-aware and the date is the next rung's, not always warn's. It was never sealed or consumed outside `digest`.

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
