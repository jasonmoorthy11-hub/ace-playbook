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
  the connect is not caught.
- Watcher, sanitizer coverage: `strip_hidden` reads inline `style` attributes and simple single-selector `<style>` rules (a tag, `.class` or
  `#id` rule, no combinators; class and id names match without case, as in quirks mode). Media queries, descendant, child and attribute
  selectors, `@import` and external stylesheets are NOT handled, so text hidden that way is read as visible. The inline rules hide opacity
  below 0.1, font-size below 1px, any zero scale argument, far negative margins, offsets and text-indent (500px or 20em and beyond),
  `clip-path:inset(50%)` and wider, a transparent `color` or `-webkit-text-fill-color`, and a box of 1px or less with overflow hidden (the
  WordPress screen-reader-text). These cases are undetectable by this design or are visible by design, and are not removed: `left` or `top`
  offsets on an element without `position`, `<font size=0>`, white-on-white or any low-contrast colour pair, a size or opacity just above
  the thresholds, `var()` indirection in a style value, the content of `<textarea>`, `<plaintext>` and `<xmp>` (kept as text), and the odd
  comment spellings (`<!->` and similar) that browsers treat differently from the parser. End tags are matched by the name as written, so
  `</x:div>` does not close `<div>`, as in a browser.
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
  after body) are derived from the stack; `colgroup` is read in the table mode, and a template routes its table tags to it and the rest
  to the body (over-hiding). A `<table>` closes an open `<p>` only in standards mode, decided by the FIRST doctype alone: `<!DOCTYPE html>`
  (optionally with `SYSTEM "about:legacy-compat"`) is standards; no doctype, a legacy (HTML 4, XHTML) one, or one after any tag or non-blank
  text is quirks, where the table nests in the paragraph (fail closed: `<p hidden><table>X` keeps X hidden, also on a legacy-doctype page
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
  every end tag is ignored but `</option>`, `</optgroup>`, `</select>` and `</template>`; a nested `<select>` stays open; `<input>`,
  `<keygen>` and `<textarea>` end it only when nothing between the top and the select is a template, a table, an integration point, a
  foreign element or a hidden element (`<div hidden><select><template><input></template></div>X` keeps X hidden). A raw-text element
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
  `ruby` and `form` (they nest deeper; `</form>` pops only a current form) and the modes of `colgroup` and `template` beyond the routing
  above.
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
