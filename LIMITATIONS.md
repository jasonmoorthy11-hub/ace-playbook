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
state-versus-lesson classifier. In the stage-2 compiler: the arms (randomization of the check per session), the evaluation of
an effect (randomization inference, the spillover flag), the review packet (`packet`) and the review record
(`review-record`). The seal command exists (`compile seal`, with `--dry-run`); the pre-registration is not sealed until the
go/no-go, and the seal refuses until the `shadow_seed`, `permutation_seed` and `arm_rate` config fields are set and the text
states the session-based entry gate and the horizon; the arm analysis (`python3 -m ace_playbook.compile.arms report`) is
built and hashed in `report_sha256`, with the hook's `arm` and `agent_id_present` fields (without which `arms report`
refuses), the phase marker, the observe-arm silence, the symmetric correction rule, human-only demotion and the minting freeze built, and the
arm-start verb built (`compile.arm_start`), and the spillover comparison still open (design section 8, Arms). The arm analysis refuses as a whole
when any approved
proposal's file is missing, even one of a
detector that has not started (it fails closed; `unapprove` keeps the file, so it can be restored). The arm analysis reads the whole demotions chain,
not only the part before the `approvals=` head: a demotion stamped before the arm start but appended after the head refuses the family (it fails
closed; only an API call with an explicit `now` can write one, `compile offline` stamps at append time). A malformed first `arm_start` line of a
detector is never replaced by a corrected second one (the first line wins), so the analysis refuses for good; run `arms report` right after adding the
line. `PREREGISTRATION.md` describes the rest as plans only.

## Compiler (stage 2)

- `agent_id_present` on a shadow row records only whether the hook payload carried an `agent_id` key, never its value (False for an over-cap payload).
- The arm phase has two views that the arm-start verb (`compile.arm_start`) writes together: the `arm_start` line in PREREGISTRATION.md, which only
  the analysis reads, and `data_dir/arm_phase.json`, which only the hook, the offline pass and the minting freeze read. A marker without a
  matching line silences nothing in the analysis (the hook, the corrections and the freeze behave as in a phase, `arms report` still says
  `not started`); a line without a marker leaves the hook delivering in both arms, and `arms report` does NOT see that: deleting or corrupting
  the marker removes the silence, the rows are accepted as they are, and only the nightly check (`compile/arm_check.py`, run by
  `hooks/nightly_sweep.py`) flags it, as the notes `arm phase missing: <detector>`, `arm phase unverified: <detector>` or `arm phase stamp differs: <detector>`
  (first in the list, so `compile status`'s four shown never lose them to the backup and brain notes), until the next nightly; a check that
  itself crashes is the note `arm check failed`, and it never fails the night. The same check notes `holdout on during an arm phase`; the verb
  refuses to start with
  `holdout_enabled` true. A change of `shadow_seed` or `arm_rate` in the config, by contrast, IS caught by the analysis (`primary` refuses rows
  that differ from `assign` and a seed that does not hash to the sealed line). The verb appends the line first and writes the marker last: if
  a write fails, or the writes finish 1 s or more after the stamp, the verb itself restores PREREGISTRATION.md, deletes the one-line file and puts
  the previous marker back, exits 1 and says to fix the cause and rerun. `--at` may only be now or later, and the default stamp is the NEXT whole
  second after the `y`. Sessions starting in that stamp's second before the writes land are eligible yet ran with output in both arms:
  the window between the stamp and the writes landing is under 1 s, and zero when they land before the stamp;
  the verb refuses when they finish 1 s or more after it.
- The marker is signed with the chain key (`chains.seal`, as approvals rows): an unsigned, malformed, other-key or tampered marker is no phase
  and the hook logs `arm:phase:unverified`. That protects against a file an agent can write, not against an agent that can read the chain key
  (a Bash tool reads any file the operator can) and not on an unkeyed install, where the signature is the plain record hash and anyone can
  recompute it. The kill file `DISABLED` remains the documented unauthenticated off switch; the phase marker is not one. A present but unusable
  marker (a directory, a symlink, a fifo, oversize, bad JSON) is the same: no phase, `arm:phase:unverified`.
- The verb's tree check is a working-tree check: it refuses uncommitted changes to the thirteen hashed files (`git status --porcelain`), so the sha256
  lines of the entry match `HEAD` only while the tree is clean when the verb runs; a change made after the verb and before the commit is not
  seen, and the verb does not verify the signed tag or the OpenTimestamps proof (the operator runs them).
- Both phase log lines go through the hook's `_Once` throttle: `arm:phase:<detector>` and `arm:phase:unverified` are written at most once per run
  and not again while they are in the last 8 KiB of `shadow.log`, so their count is not a count of silenced hits (the shadow rows are).
- In a phase no auto label is written for a started detector, in either arm: every fire waits for a human label in the blind queue. The
  correction rule is symmetric by design; `auto_fp_shell_rerun` and `auto_tp_hook_block` follow a delivered block and are therefore
  arm-sensitive, which is why they are off too, and `auto_fp_prompt_target` would cover the row and hide it from the queue (condition (4) counts
  human labels only). Demotion in a phase counts only human labels; `error_rate`, `lesson_inactive`,
  `source_sha256` and the `evasion_pair` flag still act. `neutral_corrections` is in the offline summary dict, not in the CLI's printed lines.
- The minting freeze is enforced at the only session-driven writer of lessons, `reflect_transcript`: during a phase a transcript whose raw bytes
  carry `ACE P-` (or that is unreadable or over 64 MiB) mints nothing and writes NO reflections ledger row (a `status=frozen_arm_phase` line in
  `reflect.log` only), so the freeze defers: the nightly sweep (`sweep._sweep`) decides "frozen" with the same `runner.arm_frozen` before it fills
  its `max_n` slots, so frozen transcripts take no slot, and reports the ones it set aside as a final `{"status": "frozen_arm_phase", "count": n}`
  entry (counted among the candidates it examined before the cap filled); it reads each examined transcript once (bounded, 64 MiB fail-closed) and
  re-examines them every night during the phase. After the phase (marker gone) they are ordinary candidates and are minted.
  No production code path writes RETIRE, PATCH, MERGE or NARROW from a session
  (`store/promote.py` and `store/__main__.py` are operator-driven); tests/test_arm_phase.py fails if one appears elsewhere. The NOOP counters
  of a frozen session are not written either.
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
  or a chain holds a `v: 2` row; deleting the rows and the heads file as well is the stated residual). The `v: 2` marker scan is gated by
  one regex pass, so junk chain files cost well under a millisecond, but a chain file full of short `{"v": 2}` lines passes the regex and is
  fully parsed (about 190 ms for two 1 MiB files on this Mac): a slow-hook residual with no bypass, bounded by the hook timeout. The deny rules in hooks/INSTALL.md section 7 cover
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
- The shadow hook's kill file `data_dir/proposals/DISABLED` is writable by the user the agent runs as, so the gated agent can mute block and warn
  with one `touch` (S-1 F3). It is not prevented, it is visible: while it exists the hook still evaluates and writes its rows with
  `mode: "disabled"` (the real cause, `would_fire: true`), prints nothing, and logs `disabled:kill_file` (once per 8 KiB of `shadow.log`);
  `compile status` shows `kill_file` in the heartbeat notes and the status line and menu-bar plugin print `DISABLED`. Nothing is raised or
  blocked, and the agent can delete the file again, so this is detection for the operator, not enforcement. The permission deny rules
  should cover `data_dir/proposals/**` (so `proposals/approved/` too) as well as `~/.config/ace-playbook/**` (hooks/INSTALL.md section 7).
  Separately, a payload over the 256 KiB stdin cap (S-1 F1) is read only for its first 256 KiB: the tool name is found by a raw scan of those
  bytes and, when it cannot be read or appears with two different values, every proposal of the event applies; each one with
  `on_truncated: "fire"` (all block-capable ones) fires as a `truncated` row and is denied or warned like a cut command. A proposal with
  `on_truncated: "ignore"` does not fire on such a payload. A `tool_name` found only after the first `"tool_input"` key is not trusted (the tool
  is then unknown), and an over-cap `Stop` whose `stop_hook_active` cannot be read is treated as active, so it is recorded but never blocked.
- Scrub, run-start guards (S-1 F9 F12): to keep the regexes linear several rules only start where an identifier run starts. The e-mail rule still
  finds what the old one found (`scrub/patterns.py` `emails`: an unguarded attempt where the previous match ended, so two addresses glued with `_`
  give two hits). The `jwt`, `railway_token`, `vercel_token` and `url_userinfo` rules no longer start in the middle of a run: a JWT written directly after a
  `-` (`-eyJ...`) is reported by the entropy layer and the outbound profile rather than by the `jwt` rule, so that hit may not span the whole token;
  the commit is still blocked.
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

The operator surface (`compile status`, `digest`, `dashboard`, `label --queue`, `hooks/statusline.sh`) reads the private data
dir and writes only `status.json`, `digest.log` and `dashboard.html` there (0600). None of it reaches the model: the status line
is rendered by Claude Code outside the context window, and nothing is injected. A stale `status.json` shows stale numbers until
the next `compile status` or nightly; the heartbeat tile reports the staleness rather than hiding it.

- Watcher (W1, `compile watch`): proposals are rule-matched by plain code, no model reads a source. Model-pin diffs are generated from
  a template over the matched inventory lines (the page's successor id replaces the pinned id, nothing else); a docs proposal says only
  that a mapped page changed (its sitemap `lastmod` moved or its `.md` hash differs), not what changed; version and lead proposals have
  no diff. The first scan of a page or sitemap entry is a baseline and proposes nothing. Docs and feed-lead proposals are events and run
  to their 30-day expiry; model-pin and version proposals close themselves when their trigger vanishes, and are reopened (a `reopened`
  decision row with a new 30-day expiry) if the trigger comes back after they expired or closed. Nothing is applied by the watcher.
- Watcher, trust boundary: W1 fetches as the operator, in the same process that reads the matched `~/.claude` lines and runs
  `claude --version`. There is no separate `acewatch` user and no egress allowlist until W2, so a fetch that is fooled (a redirect the guard
  misjudges, a rebinding answer) runs with the operator's network and file reach. The SSRF guard checks the address before the connection,
  not on the connected socket, so a DNS-rebinding race remains: a name that resolves to a public address at the check and to a private one at
  the connect is not caught. After a sleep and wake the first lookups can fail before DNS is up: a scan waits ONCE (20 s, bounded) for the first
  source whose name did not resolve and tries that source again; the others in the same scan are not retried, so a second scan of the day still
  catches up (a dropped source keeps no stamp and stays due).
- Watcher, Reader (W2, built, not live, off by default): a tool-less single-turn model call reads one sanitized tier-1 feed item and may
  only answer a fixed JSON schema that code validates; the result is a `lead` with no diff, whatever the item says. The residual is the
  title: it is the one model-touched free text (at most 120 characters, control, invisible and line-separator characters removed, never in
  the form of a code-made title) and it is shown in the queue and the scan output (quoted; leads stay out of the digest), so every place
  that renders it must escape it as it does the lead titles of W1. The quote is a verbatim line chosen by number, never model text. A fooled
  Reader can still call an item relevant (a noise lead; at most five per scan) or stay silent about a real one; the canary verb
  (`compile watch canary`) checks refusal only for its four planted items, not in general. An outage is decided by the Reader's own health probe,
  never by feed items (`outage` in the scan line); a feed item that makes the model fail twice is retired on its own. The Reader is for the
  `acewatch` user ONLY: run
  as the operator it would load the operator's hooks and `CLAUDE.md`. The call passes `--settings` (`disableAllHooks`) and
  `--setting-sources ""` (hooks off, the settings files dropped; checked by the release probe on the installed binary, but the fake claude of the
  tests cannot tell whether a later claude version still honours them); `CLAUDE.md` and auto-memory are kept out by the separate user, which
  is the guarantee. The `acewatch` user, its group, its own claude and login and any egress limit are the operator's steps
  (`docs/watch/SANDBOX.md`): launchd cannot restrict the network, so without a `pf` rule the fetcher's own guards are the only egress control.
  Moving `acewatch`'s proposals to the operator's store is a manual copy followed by `compile watch import <dir>`, which trusts the validation
  (`records.check`, the id and name rules, the size cap, stamps, http(s) URLs, targets known to the operator's own config), not the copy's origin:
  a forged but valid record in the copy becomes a proposal. An imported record is evidence plus title: an imported record never carries a diff
  (a diff comes only from the operator's own scan, which supersedes the import) and its title shows ` (imported)`; a forged quote, URL or title can still
  mislead a reader of the queue. An own scan's diff always wins over a diff-less imported twin (same id: the file is replaced in place; another id: the
  twin is closed as superseded, also when the own id is replaced; the record with a diff survives whatever the
  `created` stamps; an expired, auto-closed or forged-key twin is replaced too). A dismissal or an accept of an imported twin is a
  decision about ITS key and is not undone: dismissing an imported twin hides the own scan's diff for that key (same model and successor) for good. A
  dismissed or accepted twin of the same id with ANOTHER key (a forged successor) is replaced by the own candidate and reopened, or waits for a free slot
  (after any queued proposal is promoted) when five are open. An expired or auto-closed own record that already has its diff is reopened even
  when a diff-less same-key import of another id exists, whatever the import's `created`; with five open an open import is swapped at once (the
  open count is unchanged), a queued one is swapped by the scan after a free slot, and a dismissed or accepted forged twin still waits. Some scans
  close a record while five are open; each such close keeps the open count. The accept row records the `diff_sha256` that
  the operator saw (`diff_sha256 none` for a record without a diff), so a queue session left open across a scan that replaced the file leaves an accept of
  a hash that no longer matches the file; nothing is applied by either.
- Watcher, sanitizer coverage: `strip_hidden` reads inline `style` attributes and simple single-selector `<style>` rules (a tag, `.class` or
  `#id` rule, no combinators; class and id names match without case, as in quirks mode). Media queries, descendant, child and attribute
  selectors, `@import` and external stylesheets are NOT handled, so text hidden that way is read as visible. The inline rules hide opacity
  below 0.1, font-size below 1px, any zero scale argument, far negative margins, offsets and text-indent (500px or 20em and beyond),
  `clip-path:inset(50%)` and wider, a transparent `color` or `-webkit-text-fill-color`, and a box of 1px or less with overflow hidden (the
  WordPress screen-reader-text). These cases are undetectable by this design or are visible by design, and are not removed: `left` or `top`
  offsets on an element without `position`, `<font size=0>`, white-on-white or any low-contrast colour pair, a size or opacity just above
  the thresholds, `var()` indirection in a style value, the content of `<textarea>`, `<plaintext>` and `<xmp>` (kept as text), and the odd
  comment spellings (`<!->` and similar) that browsers treat differently from the parser. End tags are matched by the name as written, so
  `</x:div>` does not close `<div>`, as in a browser. A style text (an inline `style` value or one rule body) over 64 KiB counts as hiding (fail
  closed). A CSS number over 64 characters is first normalised (leading integer zeros and trailing fraction zeros dropped, so
  `opacity:` plus 80 zeros and `.0` is 0); if it still does not fit it is ambiguous and the property HIDES (S-1 F2 F4, wave 1b: an over-hide on
  purpose, no real page writes a 64-character number, and a browser would read `opacity:0.000...0` of any length as 0).
  The sanitizer builds ONE HTML tree (the HTML Standard's tree construction, simplified: `treebuild`, `treemodes`) and reads it twice, for the
  visible text (`treewalk`) and for the rules of `<style>` (`treestyle`). Every simplification is fail closed: it hides MORE, never less. The
  stack of open elements keeps the scope barriers: `</p>` is stopped by `button`, `</li>` by `ol` and `ul`, an end tag by a "special"
  element in between (it pops nothing when that would unhide text), and an end tag that names nothing in scope is ignored. A slash on a
  non-void HTML tag is ignored (`<div hidden/>` hides what follows). More than 512 open elements make the page a `parse` drop (`<td>`, `<tr>`,
  `<option>`, `<h1>`..`<h6>` and `<a>` without end tags count: they nest deeper here than in a browser, which only hides more). Implied end
  tags are modelled where a missing one would unhide text: a block start tag (`div`, `ul`, `table`, `h1`, `hr`, `pre`, ... the `p`-closing
  set) closes an open `<p>` in button scope, `<li>` closes an open `<li>`, `<dd>`/`<dt>` close an open `<dd>`/`<dt>`, and the table elements
  close their open predecessors, so `<p>a<div hidden></p>X</div>` keeps X hidden. The active formatting elements (`b`, `i`, `font`, ...) are
  reconstructed after a block closes them, with Noah's Ark and a simplified adoption agency: a formatting element whose furthest block is
  open stays open (over-hiding). FOSTER PARENTING is modelled: text and elements a table cannot hold (`<table hidden>X`, `<table><div>X`)
  are placed BEFORE the table, as a browser does, so what contains the table shows or hides them, not the table itself. The insertion
  modes (initial, before html, head, after head, body, text, table, caption, table body, row, cell, select, template, after body, after
  after body) are derived from the stack; `colgroup` is read in the table mode, and each template carries its own mode (table, colgroup,
  table body, row or body), set once by its first start tag that is not an in-head name, which decides the table-structure tags it builds
  and the ones it ignores, as a browser does, a select in a table, tbody or row template included (`treewalk.KEEP`; a template closed inside
  that select splits the two readings, see the select entry). A `<table>` closes an open
  `<p>` only in standards mode, decided by the FIRST doctype alone: `<!DOCTYPE html>` (optionally with
  `SYSTEM "about:legacy-compat"`) is standards; no doctype, a legacy (HTML 4, XHTML) one, or one after
  any tag or non-blank text is quirks, where the table nests in the paragraph (fail closed: `<p hidden><table>X` keeps X hidden, also on a legacy-doctype page
  that a browser reads in standards mode); a second doctype is ignored. `</template>` closes the template whatever lies in between (no
  scope check). A `<frameset>` that replaces the body shows nothing, and so does a `<template shadowrootmode=...>` whose host is `html`,
  `head` or the document; with another host it hides every child of that host, those before it too. Document-level hiding is modelled: an
  `<html>` or `<body>` start tag (a late or repeated one too: a browser merges its attributes onto the root) that hides, or a simple
  `<style>` rule that hides `html`, `body` or `*`, makes the whole body empty; `:root`, `html > body` and other selectors are NOT read. A
  `<select>` hides ALL its option text (a browser shows the selected one), as do `<progress>` and `<meter>` fallbacks, and a closed
  `<details>` hides its whole subtree, the summary included: over-hiding. The text of `<svg>` and `<math>` is never shown.
  Inside foreign content (`<svg>`, `<math>`) NO implied end tag happens: a start tag there is not processed by the HTML rules, so
  `<p hidden><svg><section></section>X` keeps X hidden. Only a breakout tag (`b big blockquote body br center code dd div dl dt em embed
  h1`..`h6 head hr i img li listing menu meta nobr ol p pre ruby s small span strong strike sub sup table tt u ul var`, and `font` with
  `color`, `face` or `size`) pops the foreign elements first, up to the nearest HTML element or integration point, and then the HTML rules
  apply; so do the end tags `</p>` and `</br>` (`<svg></p><q hidden></svg>X` keeps X hidden: the `</svg>` is then ignored). Every element
  has a NAMESPACE (html, svg, math) and the integration points are decided by it: in svg `foreignObject`, `desc`, `title`; in math `mi`,
  `mo`, `mn`, `ms`, `mtext`, and `annotation-xml` whose `encoding` (ASCII case folded, NOT trimmed) is `text/html` or `application/xhtml+xml`
  (any other `annotation-xml` is an ordinary math element, but its `<svg>` child is svg). Their children are HTML again (`mglyph` and
  `malignmark` under a math text point stay math). The same names in the OTHER namespace (svg `mi`, math `title`, ...) are ordinary foreign
  elements: a raw-text tag there is an element, and `<p` breaks out. Nothing implied, and no end tag, reaches past an integration point
  (`<div hidden><svg><foreignObject></div>X` keeps X hidden). A foreign element written self-closed (`<svg/>`, `<desc/>`, `<mi/>`) is
  popped at once, an integration point too, as in a browser, so nothing after `<svg hidden/>` is hidden by it; the end tag of a foreign
  element is found by a walk down the stack by name and is ignored when an HTML element is above an integration point (`<svg><desc><span
  hidden><b></desc></svg>X` keeps X hidden). The tokenizer is html.parser's, not a browser's, and the builder steers it: only an HTML
  element enters raw text or RCDATA (an svg `<style>`, `<script>` or `<title>` is an element). `<![CDATA[` (and every other `<![`) is a
  bogus comment that ends at the FIRST `>` in HTML content, as in a browser, whatever the patch release of html.parser; whenever the
  current node is NOT an HTML element (an `svg` or `math` element or an integration point itself) it is a real section that runs to `]]>`
  and its text is character data (`htmlrules.ScriptedParser.parse_html_declaration`). `<noscript>` is raw text, as in a browser with
  scripting on, so `<div hidden><noscript></div>X</noscript>` keeps X hidden (a browser with scripting off would show it, the rarer
  reader). A slash on a raw-text element (`<xmp/>`, `<iframe/>`, `<noembed/>`, `<noframes/>`, `<textarea/>`, `<title/>`, `<plaintext/>`) is
  ignored, as on `<script/>`. A double-escaped `<script><!--<script></script>` closes at its next-but-one `</script`. A comment ends where
  a browser ends it, on every patch release of html.parser (`<!-->`, `<!--->`, `-->`, `--!>`): `htmlrules.ScriptedParser.parse_comment`.
  An open `<select>` is read in TWO ways because browsers differ: the older ignores start tags in it, the newer inserts them. The
  visible text uses the newer (the more hiding), and a page with a `<select` has its `<style>` sheets read in both and unioned. In it
  every end tag is ignored but `</option>`, `</optgroup>`, `</select>`, `</template>` and, when the select is in a table and the tag is in table scope,
  `</caption>`, `</table>`, `</tbody>`, `</tfoot>`, `</thead>`, `</tr>`, `</td>` and `</th>`, which end it (as a browser's "in select in table" rule does,
  past anything the select holds, a hidden option or a foreign element too; the cell's marker clears there, so a hidden element opened before a
  template is rebuilt only as in a browser; an `applet`, `marquee` or `object` above the select poisons the rest, see below); a nested
  `<select>` stays open; `<input>`,
  `<keygen>` and `<textarea>` end it only when nothing between the top and the select is a template, a table, an integration point, a
  foreign element or a hidden element (`<div hidden><select><template><input></template></div>X` keeps X hidden). `</select>` needs the select in
  scope under the newer reading, so an open select that held a scope marker or a formatting element hides the rest even after that element closed
  (`<select><object></object></select>VIS` and `<select><b hidden></b></select>VIS`: a browser shows VIS under both readings, we hide:
  over-hide). Table-structure start tags inside a select close the select when it was opened in a table or in a template whose mode is table,
  tbody or row, and are ignored otherwise. A template closed inside a select that is in a table only through its template puts the older reading
  back in "in select" (its reset meets the template before any table), which ignores the next cell tag, while the newer reading (Chrome) ends the
  select there: the readings split, so that cell tag hides the rest (`<template><tr></tr><i hidden><select><template><td></template><td></template>LEAK`:
  Chrome shows LEAK, we hide it under the two-reading rule: over-hide, kept on purpose; `…<select><template><td></template></select><td></template>LEAK`
  still shows it, as `</select>` resets to the template mode first). A table-structure tag
  (start or end, the end tag in table scope) closes it past anything it holds (`<table><tr><td><select><b hidden><td>VIS` shows VIS); only an
  `applet`, `marquee` or `object` above the select makes that tag poison the rest, as only the newer reading inserts them, each with a marker, so
  `…<marquee><td>VIS` shows nothing although a browser shows VIS (over-hide); `col` and `colgroup` in a select in a table split the
  readings (the older step ignores them, the newer ends the select through the table steps (it closes the cell or caption, or clears back to the
  table)) and hide the rest (`<table><tr><td><select><col></td>VIS` shows
  nothing, Chrome shows VIS: over-hide, kept on purpose); a table opened inside a select is no select in a table, so
  `<select><table><td hidden>x</td></table></select>VIS` shows nothing (over-hide). A `</template>` that closes a select, or a select nested in it,
  holding a scope marker, a table, a foreign or a hiding element hides the rest of the page, because the two readings' formatting lists differ from
  there on: `<template><i hidden><select><table><tr></template>VIS` shows nothing although a browser shows VIS (over-hide; so does
  `…<select><table><select></template>VIS`). A raw-text element
  opened in a select that holds a `<` poisons the rest of the document: nothing after it shows (over-hiding by design, not for `script`).
  The rules of `<style>` come from the TREE: the sheet of a page is the child text of its `style` elements in any namespace (an HTML one
  is raw text, comments included, as CSS reads them; an svg one is character data: a CDATA section counts, a comment does not, character
  references are decoded), so a `<style` inside a script string, comment, attribute, `<textarea>`, `<title>` or `<style-x>` adds no rule
  from the tree. That set is UNIONED with a plain scan for `<style ... </style` that starts a sheet at EVERY `<style` opener (fake ones
  too, and those inside another sheet) and reads each scanned sheet also with CDATA markers removed, with character references decoded and
  with tags and comments dropped, so a page that html.parser and a browser tokenize differently cannot hide a style. The scan can only
  add rules, which only hides MORE (a CSS sample in a comment can hide the elements it names; an HTML `<style><![CDATA[.h{display:none}]]>`
  is hidden although a browser shows that text). Its start tag ends at the first `>` AND at the first `>` outside a quoted attribute
  value, a sheet for each; more than 1000 openers inside another sheet or 4 MiB of overlapping sheet text make the page a `parse` drop
  (fail closed). The style pass no longer matches an end tag by the nearest name: the builder's scope rules decide what a `</style>`,
  `</svg>` or `</desc>` closes (the nearest-name approximation of the older style pass could also MISS a sheet a browser applies, not
  only over-hide). An unclosed `<style>` at the end of the page applies to the elements before it; the legacy `<!--` and `-->` tokens in
  a stylesheet are ignored as CSS does. Every `<style>` is parsed on its own, so an open block, string or comment never carries into the
  next sheet. Not modelled, and always on the side of hiding more: the start-tag rules of `a`, `nobr`, `button`, the headings, `option`,
  `ruby` and `form` (they nest deeper; `</form>` pops only a current form) and the mode of `colgroup` beyond the routing above (the
  template modes are modelled, not over-hidden: see the insertion modes above).
  The stylesheet is read in
  one linear pass over its braces (`cssscan.py`):
  braces, semicolons and comment openers inside a CSS
  string (`content:"{"`), after a backslash or in an unquoted `url(...)` (the ident is recognised after escape decoding: `\75rl(`, `u\72l(`,
  `\55RL(`) do not count; nor do braces and semicolons inside a `(` or `[` simple block (`foo(})`; nested blocks close in order, an unclosed
  one swallows the rest, comments are stripped first so `foo(/*)` opens a comment); a rule with nested blocks is read with the
  nested blocks cut out of its body and the nested rules are read on their own as well (over-hiding: a browser matches them only inside the
  parent); a rule or block still open at the end is closed there, as a browser does (`.h{display:none` applies); CSS escapes in a selector are
  decoded (`.\68` is `.h`, `#\69 ` is `#i`) and a selector list is split on unescaped commas only (`.a\,b` is the one class `a,b`).
  Blocks nested more than 512 deep are skipped; the rules inside a closed at-rule are not read, as before. The tree depends on html.parser reading
  `<textarea>` and `<title>` as RCDATA (Python 3.12.15 and later patch releases; an older 3.12 reads a `<style` in them as a tag, which
  can only add a spurious rule). A double-escaped script closes only at a `</script` followed by an HTML space, `/` or `>`.
  A leading byte order mark is dropped in ONE place, `fetch` (it decodes `utf-8-sig`, as the Encoding Standard strips one BOM before the
  tokenizer sees the page). `strip_hidden` removes none: a U+FEFF that remains (a second BOM, or one inside a Markdown page) is a
  character token in the initial insertion mode, so the page is in quirks mode, as it would be in a browser. A BOM before `<!DOCTYPE
  html>` that `fetch` did not strip therefore does NOT keep the page in standards mode.
  Markdown pages (every Content-Type but `text/html`) are read with the HTML rules, because their site renders raw HTML. A raw-text or
  content-hiding opener (`<script>`, `<style>`, `<title>`, `<textarea>`, `<xmp>`, `<noembed>`, `<noframes>`, `<iframe>`, and since wave 14 also
  `<noscript>`, `<details>`, `<select>`, `<svg>`, `<math>`, `<template>`, `<object>`, `<dialog>`, `<canvas>`, `<video>`, `<audio>`,
  `<datalist>`, `<head>`, `<rp>`, `<progress>`, `<meter>`) is prose only when ALL hold: it sits in
  a fenced code block or in an inline code span that opens and closes on ONE line (a span that crosses a line is never trusted: a
  heading, quote, list item, table cell or thematic break may cut it), that line starts with no block marker (`#`, `>`, `-`, `*`, `+`,
  `|`, `1.`), has no backslash directly before a backtick and no `<` before the opening run, and the span holds no `|`; it is not inside an HTML
  block (a line that starts with `<tag`, `</tag`, `<!` or `<?` runs to
  the next blank line, to its closing tag for `script`, `pre`, `style` and `textarea`, to `-->` for a comment, after any list or quote
  marker; a backtick inside a tag opens no span); and NO matching closer (`</script` for `<script`) follows it anywhere later in the input.
  Every other opener hides to its closer or to the end of the input, so a page that mentions `<script>` in code and later closes it, or does
  so inside a `<Note>` block, loses the text in between (hiding more is the safe direction). The detection is a CommonMark subset (no
  indented code blocks, no nested quote or list handling beyond the marker prefix); an indented `<script>` is read as markup. A fence
  that does not start at the left margin (an indent of 1 to 3 spaces, as inside a list item, which ends the fence with the item) is
  never trusted as code: its lines keep their HTML meaning, so a `<script>` in a code sample inside a list item hides to its closer or
  the end of the page (fail closed; the price is lost text on pages that show HTML samples inside lists). Link
  reference definitions (`[x]: url "title"`, `[//]: # (comment)`) are dropped ONLY when strictly valid per CommonMark: the label on one
  line, `:`, a destination (`<...>` with no `<`, `>` or newline inside, or non-space characters with no `<` and at most one level of
  balanced parentheses), an optional title fully quoted (`"..."`, `'...'` or `(...)`) over at most three lines with no blank line, and
  nothing else on the last line; the destination and the title may be on the next lines, unless such a line starts a quote, heading,
  fence, list item, thematic break or contains a `<` at its start. A definition line right after paragraph text is not a definition (a
  renderer shows it) and stays. A heading, a thematic break (`---`) or a setext underline under paragraph text ends the paragraph, but a
  `===`, `=` or `--` line with NO paragraph open is itself paragraph text, so a definition after it stays too (its tags keep their HTML
  meaning; a lone `-` is also read as text: over-hiding). The same holds INSIDE a quote or a list item: there such a line is a lazy
  continuation of the paragraph, never a setext underline (`> foo` then `===`), so the definition after it stays. The container flag is set
  by a `>` or a list-marker line (a bare marker such as `-` or `1.` too: it opens an empty item, so `-` then an indented line then `===` is lazy
  text; a lone `-` right under a paragraph is still its underline), kept over blank and indented lines and cleared by an unindented
  non-marker line after a blank line, or by an
  unindented heading or thematic break (a thematic break such as `> foo` then `---` still ends the paragraph); it is not a full CommonMark
  container parse and errs towards "still inside" (fail closed).
  Whatever fails the rule is left alone and read as HTML (`[x]: /u <script>` hides to its closer; so does
  `[x]:` followed by a `<!--` or `<div hidden>` line), so a definition never swallows the lines after it. A definition with a label over
  more than one line, a title over more than three, or parentheses nested twice in the destination stays visible (a renderer hides it).
  A `md` source served as `text/html` loses its definition lines from the VISIBLE text only, after the markup is gone. A footnote
  definition (`[^1]: text`) is dropped like a link definition (CommonMark has no footnotes; GitHub shows them).
  NOT handled: image alt text and link titles in running text (`![alt](u)`, `[a](u "title")`) stay in the text although a renderer
  shows them only on a failed image or a hover. The defence is not the sanitizer alone: web text reaches a record only as a title or a
  400-character quote, never as a diff, a target or a command, and every model id must pass a strict pattern first. A `watch_dir` that is
  itself a symlink is refused (`compile watch scan` exits 2, no path in the message) and counts as one corrupt entry, so the status tile
  reads `none  corrupt 1`.

## Security and scrubbing

Secret scanners have measured recall well below 100% (31 to 88% in one comparison, 36.6% for the
best static scanner on runtime credentials in another). The scrubber here is regex, entropy and
decoded-content checks plus lints, and it has not been measured against real credentials; the
synthetic corpus is generated to match its own patterns, so detection on it is circular.
Paraphrase leakage (a lesson that restates a token's prefix or last characters) is a hypothesis
the long-token lint partly covers and nothing measures.

## Provenance origins

- A pre-existing third-party file under the session's working directory that matches none of the untrusted markers (a cloned repo's
  `docs/`, a PR branch) is still labelled `user_file`, and so still counts as the user's. Authorship by git history is a later design (S-1 F6).
- The `agent_file` label rests on a tracker that deliberately over-approximates (`ace_playbook/clean/bashwrite.py` and `origins.py`, D-169 clauses 6-9): any
  write-capable Bash command (a write verb such as `tee cp mv install curl wget git rsync sed patch python sh touch mkdir tar`, a
  `>`/`>>`/`<<` anywhere in the text, or an `-o`/`-O`/`--output*` flag) marks every path-looking word it mentions as agent-written, plus the
  basename of every URL (under the working directory and under each marked directory) and the directory of each marked path, and every
  word of a command with a destination verb. A user-owned file the agent merely named in such a command is then untrusted for the rest
  of the session. Two consequences are accepted by design and pinned in `tests/test_clean.py`: `grep -n 'a > b' x.md` makes `x.md`
  `agent_file` (the `>` is inside quotes), and a Read of `/etc/hosts` after `cp a.md /etc/hosts2` is `agent_file` (the directory rule,
  which applies to Bash only, never to the four write tools). Comments, heredoc bodies and quoting are not parsed (shlex with
  `cmd.split()` as the fallback; `unparsed_bash` in the summary counts the fallbacks, and a command that needed it is write-capable by
  definition: every non-flag word of it is marked). The interpreters (`python perl ruby node sh bash zsh`), `sed`, `patch` and `dd` mark
  every word of the command. A command that names an extraction verb (`tar unzip ditto rsync patch gh bsdtar 7z gzip gunzip bzip2 bunzip2 xz
  unxz zstd unzstd cpio pax`), `git` at all (any subcommand, with or without a URL), or a
  server-name trigger (`curl -J` or any short-flag cluster holding `J`, `--remote-h...`, `--content-d...`, `--trust-s...`, a wgetrc `-e` spelling of
  content-disposition or trust-server-names in any case, with or without `_` and `-`) records the working directory as written, plus every
  value of `-C -d --directory -P --directory-prefix
  --output-dir -o` found anywhere in the command, separated or attached (`-Cdir`); a flag value never replaces the working directory. So
  `tar -C out`, `unzip -d dest`, `wget -P dl` and `git clone <url> vendor2` also taint the working directory, and after any git command
  (even `git status`) every later Read under the session root is `agent_file`: accepted, pinned in `tests/test_clean.py`. A destination verb
  joins every marked word with the basename of every other (`cp ../x/a.md .` writes `./a.md`; a command
  with over 200 marked words or 2000 basenames records the session root instead of pairing, so a huge command taints every later
  Read), every word after a bare `--` is a plain word, and the value of any `--flag=value` or attached short
  flag (`-tdir`, `-oout.md`, `-sLoout.md`, `-Pdl`) is a marked word. A word holding `*`, `?`, `[` or a `{...}` group records its directory
  as written (no glob or brace expansion is done); `~` and a leading `$HOME` are expanded; a write-capable command that changes directory
  or re-evaluates text (`cd`, `pushd`, `popd`, `eval`, `source`, `.`, `exec`, `env`, `nohup`, `time`, `xargs`, `find`, or any mention of
  `CDPATH`; matched on basenames, so `/usr/bin/env` counts) taints the whole session root (the first cwd, never a record's own cwd), because the
  tracker does not model the shell's working directory; the explicit `cd` and `find` targets are not recorded separately (every target under the root
  is covered by the root, every target outside it is outside the trust boundary and already `tool_output`), and a `cd` or `eval` inside a quoted word
  is still seen because the loose tokenisation splits on punctuation (`bash -c "cd x; echo a > r.md"`, pinned); and for every URL its host and first
  path segment are recorded as directories under the working directory and under each marked directory (a recursive `wget` writes
  `host/path`). A script or interpreter that writes a file it does not name on its command line is not tracked; name the file on
  the command line or read it through the Bash tool's own output. Writes by a command outside the verb list and without a redirection
  are not tracked either. Any command containing a `$` expansion (`$VAR`, `${...}`, `$(...)`, `$@`, `$$`), ANSI-C or locale quoting
  (`$'...'`, `$"..."`) or a backtick makes it write-capable and taints the whole session root, because shlex models none of them and an
  expansion can spell a verb or a path (`echo "$X" > out.md`, `cp a.md "$DIR/"` and ``cat `ls` `` all taint the root). By design, any `*`, `?`
  or `[` anywhere in a command, quoted or not, counts the same way (`c? a.md b.md` can spell `cp`): any glob in a command taints the session root.
  A `{` anywhere (brace expansion: `c{p,} a.md docs`) or a word-initial `=` (zsh: `=cp a.md z.md`) counts the same way, so a JSON literal in a command
  taints the root, by design. Verbs match case-insensitively (macOS volumes are case-insensitive); flag spellings stay case-sensitive.
  Any `~` not followed by `/`, whitespace or the end of the word (`~+`, `~-`, `~N`, `~user`, zsh `~NAME`, and `foo~bar`) counts as an unmodelled
  expansion and taints the root, wherever it appears (`file~` and `~/x` do not);
  the zsh clobber operators `>!`, `>>!` and `&>!` are redirects (both `!target` and `target` are recorded: bash writes `!target`, zsh `target`);
  and a string in a non-read-only tool's input is recorded as the raw string and, for a `file://` URL, also as its path and the percent-decoded
  path (`file:///a/b`, `file://localhost/a/b`, `sp%20ace.md` and `sp ace.md`); a string that is not a file URL is never decoded.
  A tool that percent-decodes a non-URL path argument itself (`a%20b.md` written as `a b.md`) is not tracked; the next tracker widening records
  every tool string in its percent-decoded form as well.
  Shell spellings outside the modelled set — the verb list, the redirect operators and the expansion characters —
  are not tracked; six review rounds each found a new one, so assume more exist.
- Subagent writes are read from the `isSidechain` records of the main transcript and from every `*.jsonl` under
  `<session>/subagents/` beside it (the harness's current layout); their turns are never kept. A file there that is not valid JSONL is
  skipped line by line and counted in `bad_lines`.
- Written paths are compared casefolded (and NFKC-normalised; default APFS and NTFS are case-insensitive; on a case-sensitive filesystem
  this is an over-approximation that can only make a file untrusted). A symlinked or firmlinked alias of a written file (macOS
  `/System/Volumes/Data/...`) is not recognised: reading it through another path stays `user_file`; a leading `//` is collapsed to `/`.
  Subagent writes use the session's first cwd as the root for the root taint and the clamp.
- Any tool other than `Bash`, the four write tools and a read-only allowlist (`Read`, `Grep`, `Glob`, `LS`, `TodoRead`, `TaskList`, `TaskGet`,
  `WebSearch`, `WebFetch`, the MCP resource readers, and any MCP tool whose last name segment starts with `read_`, `list_`, `search_`, `get_`,
  `find_`, `query_` or `describe_` and whose whole name holds no write verb such as `write`, `edit`, `create`, `move`, `copy`, `save`, `put`,
  `upload`, `patch`, `set`, `update`, `delete`, `clone`, `extract`) is presumed to write: every string anywhere inside its input (nested
  dicts and lists, dict keys included) that sits under a known path key (`path`, `file_path`, `destination`, `target`, `source`, `from`, `to`,
  `output`, `file`, `files`, `paths`, `uri` and similar) counts as written whatever its shape, as does any other string that looks like a path (a
  `/`, a leading `.` or `~`, or a file suffix), and such a string holding `*`, `?`, `[` or `{` also records the directory before the first of them
  (`docs/*.md` records `docs`; `*.md` the working directory); a tool that writes files it does not name at all is not tracked; a tool_use with
  over 200 such strings taints the session root. A write through a tool named
  like a reader without a write verb, or a path under an unknown key that does not look like one, is not tracked. A shell-command string
  of such a tool (under `command`, `cmd`, `script`, `commands` or `args`) is also analysed as a Bash command; a PowerShell `Set-Content` or any
  other non-POSIX write form is a residual. Paths are compared NFKC-normalised and casefolded.
- Write blocks of any tool seen before the first record with a working directory are held back and noted once it is known (or at the end of
  the transcript if none ever appears).
- A transcript in which no record carries a working directory has no user files: every Read is `tool_output`.
- The trust boundary for `user_file` is the session's first cwd. A record whose own cwd is outside it makes its Reads `tool_output`.
- `shell_output` is decided by the `<bash-input>`, `<bash-stdout>` and `<bash-stderr>` tags of a user record: a user record with any of
  them in any text block, balanced or not, has every text block turned into `shell_output`, never partly a user message. Output that reaches the transcript
  another way is not recognised.
- Shell-mode records no longer count as user turns, so the quote offsets of evidence cards minted before this change (`compile/evidence.py`
  `_offsets`, an index among user messages) can shift for sessions that held such records.

## Brief on wake

The brief's Hammerspoon trigger (`hooks/hammerspoon/ace.lua`) has no test here: no Lua interpreter is installed on the build machine, so the
file is checked as text and reviewed by eye (this covers its reading of `ace_config.lua` too: an empty or blank `config_file`, a file that does
not load or return a table, or a value that is not a string skips the run with one logged line), and the LaunchAgent template is only `plutil
-lint`ed. The brief's tool allowlist does not shrink
the prompt: every connector's tool definitions still load, about 140k cached input tokens per stage-1 run (phase-0 measurement); a probe of a
Haiku run that loaded them all showed 248k cache-creation tokens ($0.25-equivalent) on a cold cache, so the connector definitions, not the
model, dominate stage-1 cost. The brief relies on tool scoping and deny lists, not on a hook, and `-p` alone does not deny an unlisted tool because
the operator's
user-level `permissions.allow` and `defaultMode` still apply, so stage 1 passes `--permission-mode default` and the deny file names every
unneeded connector tool by name; the list is a snapshot (`hooks/brief_connector_tools.txt`, 2026-10-22, re-diffed 2026-10-29). A connector tool added later, and
any user-scope MCP server that is not in the snapshot, is NOT stopped by `--tools` (built-ins only) or `--allowedTools` (it only adds allows):
the default mode and `-p` deny it only when no user-level `permissions.allow` covers it, so with a server-wide or wildcard allow such as
`mcp__claude_ai_Gmail` or `mcp__*` a new Gmail write tool is open to stage 1 (the D-117 caveat). Operators must never keep server-wide or
wildcard `permissions.allow` entries for Gmail, Calendar or Linear (or any `mcp__*` wildcard) in user settings. The after-the-fact check
`tools-audit` refuses a run whose init tool list or calls show ANY tool outside the allowlist and `ToolSearch`, a built-in too (exit 2, digest
discarded, ledger row),
but it detects, it does not prevent: the tool may already have run. `--setting-sources` cannot be
used because it drops the claude.ai
connectors (probed 2026-10-22), so `disableAllHooks` keeps the operator's hooks out, which also disables the PreToolUse guard. Isolating
with a private `CLAUDE_CONFIG_DIR` does not work: credentials are bound to the config dir, so a fresh one is "Not logged in". The window figures in its
ledger are one reading per stream, so a single run cannot show its own cost in plan share; only the trend over consecutive rows does.
The `last refusal:` line of `brief status` names only a reason from a fixed list (a stage timeout and a tool drift are the other two kinds, each from its
own row); a run
whose config or brief directory cannot be read has no ledger to write to, so its refusal stays on stderr only, and a quiet exit (another run holding the
lock, not due) is not a refusal.

## Nightly check notes

The three optional config keys `backup_check`, `brain_check` and `acewatch_check` name local scripts that the nightly runs as
`<script> --check`. A note is text
from a local script the operator configured, not from the network; first line, 80 chars, failures are reported as `check failed` without
detail (a script that is missing or not a regular file at run time, a non-zero exit, a timeout of 20 s, a start error, an empty or unprintable first
line and a line that contains a `/` all give
`backup check failed`, `brain check failed` or `acewatch check failed`: never stderr, the script path or an exception text). The script's own line is shown
as it
wrote it, so what it prints is only as good as the script (a check line is shown as a plain note; a line whose first word carries a colon is
rejected so it cannot pose as a digest category such as `silent:`); `compile status` shows at most four notes, each cut to 80 characters;
the arm notes come first, so with two of them plus the backup and brain notes the `acewatch` note is not shown there (it stays in `health.json`).

The sandbox logs in `/var/acewatch/logs` are rotated only if the operator installs the newsyslog template (`hooks/acewatch/newsyslog.acewatch.conf`) as
root; nothing in this repository installs it, and the other launchd jobs' logs (nightly, brief) are not covered by it.

The `acewatch` note is the same kind of text: whatever the operator's `acewatch_check` script prints (the sample prints the first `open ...`, `none` or
`disabled` line of `watch status` as `acewatch`), not a count this repository verified. The sudoers line it needs is the operator's, installed by hand.

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
