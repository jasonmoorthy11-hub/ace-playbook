# Limitations

Read this before drawing any conclusion from the repository.

## Status

Stage 2 shadow mode, observe only. There are no results. The stage-1 plumbing (store, scrubber,
injector, reflector) and the stage-2 compiler (evidence cards, spec-only proposals, the shadow hook)
exist as code; as of 2026-10-19 no proposal has been approved, so the shadow hook has recorded
nothing and nothing has been measured. No measured claim about benefit is made.

## Statistical power

At roughly 2.6 sessions a day, a per-lesson hide/show design has limited power. With an assumed
3% recurrence per (session, lesson) pair and about ten eligible lessons per session, the
smallest rate ratio detectable at 80% power is roughly 0.33 at 8 weeks, 0.5 at 16 weeks and 0.57
at 24 weeks. A 30% reduction (rate ratio 0.7) has about 17 to 27% power at 8 weeks. The
pre-registered meaningful effect (0.67) is not reachable at any horizon under roughly 30 weeks,
and an informative null is not reachable at 16 weeks. The assumptions (baseline rate, number of
eligible lessons) are guesses and the real corpus will probably have fewer eligible lessons than
assumed once duplicates of always-loaded instructions are excluded. These figures come from a
simulation in the author's research notes, not from data; the notes are not part of this
repository.

## Evidence is thin

- No controlled study of automatically reflected cross-session lessons for a CLI coding agent
  was found. The ACE paper's gains depend on execution feedback and it reports a coding-agent
  setting only indirectly; one independent audit found ACE-style playbooks harmed 4 of 11
  settings, and one independent benchmark put an ACE playbook below plain in-context history.
- The only direct measurement of compiled rules against prose rules (TRACE) used simulated
  users, 29 checks from one researcher and no false-positive measure.
- Studies of LLM-written context files report null or slightly negative effects on task success
  with higher cost. This project's lessons are user- and environment-specific facts rather than
  repo knowledge, which weakens that prior without removing it.
- Agent-tagged helpful/harmful counters are known to be unreliable (a harmful rule has been
  tagged helpful 123 times). Here they are recorded as observability and used as a weak ranking
  feature only. They never retire or compile anything.

## Deviations from the ACE paper

- Deltas are validated and merged by code; there is no embedding-based deduplication and no
  lazy-refinement pass yet. A single reflector call replaces the paper's separate Generator,
  Reflector and Curator roles, and there is no LLM curator in stage 1.
- Execution feedback is replaced by observable proxies (user-correction count, hook blocks,
  repeated failing commands). These proxies are noisy and unvalidated.
- Decay is computed at read time and affects rank only. Nothing is ever deleted or rewritten.
- Stage 1 injects pinned lessons only, so it tests the plumbing and the holdout mechanism, not
  retrieval quality.

## Not built yet

The curator and refiner, the replay and task-suite evaluation harness, the judge, and the
state-versus-lesson classifier. In the stage-2 compiler: the arms (randomization of the check per session), the evaluation of an effect (randomization inference, the spillover flag), the review packet (`packet`) and the review record (`review-record`). The seal command exists (`compile seal`, with `--dry-run`); the pre-registration is not sealed until the go/no-go, and the seal refuses until the `shadow_seed` and `arm_rate` config fields exist (they land with the executed seal in the next version), so `shadow_seed_sha256` and `arm_rate` stay placeholders in its block until the arms are built. `PREREGISTRATION.md` describes the rest as plans only.

## Compiler (stage 2)

- The command scanner is a best-effort linear tokenizer, not a shell parser. Known shapes it does
  not see: `find ... -delete` and `-exec rm -rf`, `xargs` fed from a pipe, `bash -c`, `eval` and
  `bash <<EOF` bodies, shell functions and aliases, and anything past the 64 KB cap (reported as
  `unparseable`). The control is the promotion ladder: a proposal starts in observe mode and every
  counted true positive is read by a human before any mode change (`docs/design/compiler.md` 7.3).
  A strict-xfail evasion matrix (`tests/test_detector_evasion_matrix.py`, `docs/design/compiler.md` 4.1.1) pins the documented
  misses of `rm_rf_nonscratch` as of this writing: it does not see a recursive delete through an interpreter (python, node, perl,
  `bash -c`, `sh -c`, `eval`, a heredoc, a base64 pipe), through `find`, `xargs` from a pipe, `rsync --delete`, `git clean`, `trash` or
  Finder, or in a script or Makefile that was written and then run, nor through `$IFS` or `$(...)` as the command word
  (19 of 43 variants); and it flags a scratch delete written as `cd <scratch> && rm -rf ./*` or through a variable (2 false positives).
- Warn delivery, resolved: warn uses `additionalContext` (step 5b). The Claude Code hooks reference (read
  2026-10-19) says a hook's stderr on exit 0 never reaches the model, so the hook prints one
  `hookSpecificOutput.additionalContext` JSON object on stdout (and echoes the message on stderr for the debug log).
- `approvals.jsonl` and `demotions.jsonl` are hash-chained and, after `compile keygen`, HMAC-keyed (only `v: 2` rows verify while the key is
  readable) with a signed head file (`chain_heads.json`) that pins each chain's last row, so a process without the key cannot downgrade,
  cut or roll back a chain. The key is readable by the operator's user, hence by the agent: a keyed row proves its writer read, replaced or
  redirected the key (the key file, its directory, or `chain_key_path` in `config.json`; there is no default key path, so deleting that line
  also drops the key, but the install still fails closed while `ACE_PLAYBOOK_KEYED=1` is set, `chain_heads.json` sits beside the config file
  or a chain holds a `v: 2` row; deleting the rows and the heads file as well is the stated residual). The deny rules in hooks/INSTALL.md section 7 cover
  Read, Edit and Write on `~/.config/ace-playbook/**`; they do not cover a shell command (the substring guard does not catch
  `cd ~/.config/ace-playbook; mv chain_key x`), a same-user process outside the permission layer, or deleting both the key file and the heads
  file, which returns the install to the unkeyed v1-only rule unless `ACE_PLAYBOOK_KEYED=1` is set in the hook command (an install counts as
  keyed when either file exists or the flag is set; keyed without a usable key, both chains are broken). `shadow.sh` and the package live in an
  agent-writable checkout, so the permission layer is the real boundary. Deleting the chain files disables rather than promotes with a key; before
  `keygen`, deleting `demotions.jsonl` lifts its caps. Until `keygen` runs the chains are unkeyed. `keygen` itself runs on a never-keyed install only (it lists the rows it will re-seal, asks, and keeps
  `.bak` copies in the key directory); on any keyed install it refuses, so it cannot be used to seal a forged chain under a new key.
  `labels.jsonl` stays unkeyed.
  Approvals, demotions and the heads MAC share one key and the plain `keyed_hash`, with no chain or purpose label, so the separation of the
  chains rests on their disjoint row schemas (`tests/test_chain_tamper.py`); a domain-separated MAC needs a format change and a re-seal migration.
  The tamper matrix is `tests/test_chain_tamper.py`; it pins the accepted residual (an old chain restored with its old heads file verifies) and
  that a cut last line in `labels.jsonl` is skipped by its tolerant read, where in the two keyed chains it is a break at that row.
- A mutation run over `detectors.py`, `shell.py` and `textrules.py` (250 of 787 mutants, 72.0 % killed) left 68 survivors; those that were not
  equivalent are pinned by `tests/test_mutation_survivors.py`. The module-level regexes (`CORRECTION`, `SCRATCH`, the `textrules` checks) remain
  the weak spot: each alternation branch and `\b` needs its own case. Every test runs under a 60 s SIGALRM timer (`tests/conftest.py`) that
  raises a `BaseException` and re-fires every second, so a fail-open `except Exception` cannot swallow it; a swallowed firing fails the test at
  teardown, and pytest's faulthandler aborts the whole run after 125 s with every thread's stack (the per-test alarm is the normal failure
  path). It needs SIGALRM and the main thread, so it is not active on Windows. The alarm cannot cancel work after a test has already failed:
  once a timeout has surfaced it is disarmed (so it cannot fire into teardown), so a hang in the teardown that follows, or a thread or child
  process the test left running, is bounded only by the 125 s faulthandler.
- A proposal's `provenance.source_sha256` is a per-detector hash by exclusion: the text of `detectors.py` and `catalogue.py`, minus
  the bodies of the other rows' `fire` functions (`catalogue.py` is hashed whole, the `CATALOGUE` registry literal included, because
  an expression inside it runs at import time in every process), plus the row's name, fire name and catalogue fields (the `meta`
  label, redundant with the whole-file text and harmless) and the whole text of `events.py`, `shell.py` and `textrules.py`. Editing
  inside one detector's own fire body retires only that detector's proposals (re-propose, re-approve). Any other edit retires every
  approved proposal: any row's registry fields, a shared helper, an import, a constant, a decorator or signature of any fire, a rebinding, `evaluate`, the `Detector`
  class, `advance` or `blank_state` (hashed for every row: `evaluate` runs `advance` before the decision is emitted), any edit to
  the three leaf modules, and adding a detector. Dynamic lookups are moot (nothing is followed, everything is text). What the hash
  does not see is any difference between the text on disk, read once when it is first used in a process, and the code actually
  loaded (a tampered `.pyc`, an in-memory monkeypatch, a file edited while a long-lived process runs); the old whole-file hash
  had the same blind spot. A fire in the registry must be the top-level function of `detectors.py` that carries its name
  (a lambda, a wrapper, a `functools.partial` or a callable instance fails the same way): hashing then raises `ValueError` for that
  row only, the hook skips its proposals, the nightly pass treats them as stale (`source_sha256` demotion), and `source_table()`
  and building evidence cards for it raise. The event object is shared by all fires in one `evaluate` call (the state is deep-copied
  per fire); a test pins that no detector mutates either. That test runs at merge time only, so a mutating fire in an edited
  installed copy would change other rows' results in the nightly pass and in evidence building (offline only); the hook is
  unaffected, because it runs only rows whose proposals are hash-valid and an edited row's proposals are stale. The merge that introduced the hash changed every existing proposal's hash
  once, so each proposal written before it is stale and must be re-proposed and re-approved.
- `additionalContext` was confirmed live for PreToolUse and for Stop (a scratch project under `claude -p`: the transcript records a
  `hook_additional_context` attachment and the model is re-prompted with it, so a Stop warn CONTINUES the turn and fires again on every
  later stop unless the hook returns nothing when `stop_hook_active` is true, which `shadow.py` does). No Stop detector is approvable today.
- The `Stop` hook entry is wiring only: no approvable detector consumes `Stop` today.
- A lesson migrated from an older corpus carries self-quoted `legacy:` evidence and gets an evidence
  card only through a human incident label (`compile label --incident`). Re-minting such a lesson
  with `store promote` does not help here: the promote evidence carries session `promote-cli`, which
  has no transcript, so it never yields a standard card.
- `compile label` is TTY-only, which stops hooks, pipes and plain subprocesses, not a determined
  local process; promotion makes a forged label visible to the operator rather than impossible.
- The nightly `compile offline` pass pre-fills labels from transcript signals; they are pre-fill only.
  An auto `fp` counts as a false positive (and can demote a proposal) until a human relabels it, a
  `tp_candidate` is never counted as a true positive, and a human row always wins. The signals are
  heuristics (a regex for user corrections, a hash match against the latest prompt). The p95 hook-cost
  rule (disable above 200 ms over seven days) is not implemented and is reported as `not implemented` on every run (rows carry `dur_ms`, but
  it covers firing calls only and excludes the interpreter start); the report's hook cost has the same limits, and a subagent's tool calls
  may count as live fires against a parent session whose tool calls the offline pass excludes; the 1% hook-error rule needs a sample of 100 (rows count fires, not hook calls) and is
  reported `not computable` below it.
- An operator approval lifts demotions: a proposal's cap is the lowest `to_mode` among the `demotions.jsonl` rows stamped at or after its
  newest `approvals.jsonl` row, so `approve --mode observe` (TTY, which lists each row it lifts) on a demoted proposal,
  `disabled` included, lifts the strictly older rows (a same-second row stays: re-approve a second later) and restarts the
  ladder from observe; until then `approve --mode warn|block` is refused with `demoted:<cap>`. The agent
  cannot write a valid approval row without the TTY, except by reading, replacing, redirecting or deleting the chain key (the stated residual). A restored copy
  after `unapprove` is skipped (`revoked`), not run in observe.
  The `from_mode` check in `demotions.append` is a guard the nightly pass cannot trip, not a control.
- Every input of the offline pass can be written by a local process: transcripts, `shadow.jsonl` (including its `mode`),
  `shadow.log`, `labels.jsonl`, `demotions.jsonl` (hash-chained, keyed from `v: 2`), `offline_state.json`, the approved copy's
  `approved_at` and `deltas.jsonl`. Demotions can so be caused by forged inputs and suppressed by deleting or truncating the
  file. The hook's safety is that a copy above `observe` needs a matching approvals row and only an operator approval lifts a demotion; the
  operator's controls are the TTY listing at promotion and the `Demotions` section of `compile report`.
- `tp_candidate` rows must never feed the ladder (the agent can print a guard-looking line into its own tool output in observe
  and warn mode). The shell-mode rule skips meta records (`isMeta`, compact summary, transcript-only) and
  needs the whole-record `<bash-input>` shape.

## Security and scrubbing

Secret scanners have measured recall well below 100% (31 to 88% in one comparison, 36.6% for the
best static scanner on runtime credentials in another). The scrubber here is regex, entropy and
decoded-content checks plus lints, and it has not been measured against real credentials; the
synthetic corpus is generated to match its own patterns, so detection on it is circular.
Paraphrase leakage (a lesson that restates a token's prefix or last characters) is a hypothesis
the long-token lint partly covers and nothing measures.

## Platform assumptions

Behavior of Claude Code hooks (SessionStart context cap, SessionEnd budget, Stop payload,
`onFailure`) is taken from its documentation as read in October 2026 and may change. Two
behaviors were not settled by documentation; `docs/platform-notes.md` records what was observed
on 2026-10-19 (Claude Code 2.1.302, macOS): files written to the memory directory during a
session are not surfaced to the running session, and the SessionEnd hook only enqueues, so the
design never relies on a detached child surviving an interactive exit. Stage 1 depends on
neither; the nightly sweep runs under launchd instead.

## Provisional promotion

A `PATCH` that sets a live status (`active` or `provisional`) is refused unless the lesson holds
user-origin evidence that is not migrated: evidence whose session id starts with `legacy:` is
self-quoted by the migration and does not count. So a bare `PATCH {status: active}` on a migrated
lesson fails, and the `promote` CLI, which attaches the operator's typed quote, is the way through.

An `ADD` with a live status is still validated by origin only (`user_message` or `user_file`),
which is what the migration's override activations relied on. Existing `ADD active` records with
`legacy:` evidence are therefore still valid: `verify()` and a view rebuild of an existing log are
unaffected (covered by a test). The remaining gap is that nothing stops a new hand-written
`ADD` with a live status and self-quoted `user_file` evidence; the log checks origin, not who
typed the quote.

The `promote` TTY check proves a terminal is attached to stdin, not that a person typed: a pty
wrapper can pass it. It stops hooks, pipes and plain subprocesses, not a determined local process.

Migrated dormant lessons (legacy-only evidence) cannot be revived by a bare `PATCH` either, and
`promote` accepts only `provisional` lessons; reviving a dormant one is an operator decision for
later. `verify()` checks schema and the hash chain but does not fold the log, so `rebuild` is the
compatibility check after any change to a view-level rule.

## Single user

One person's workflow on one machine. Results, if any, will not generalize to teams or to other
agents without separate evidence.
